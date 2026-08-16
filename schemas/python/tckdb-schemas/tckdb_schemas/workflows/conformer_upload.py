"""Wire contract for ``POST /api/v1/uploads/conformers``."""

from typing import Self

from pydantic import Field, model_validator

from tckdb_schemas.common import SchemaBase
from tckdb_schemas.energy_correction import AppliedEnergyCorrectionUploadPayload
from tckdb_schemas.enums import (
    CalculationType,
    RigidRotorKind,
    ScientificOriginKind,
    StatmechCalculationRole,
    StatmechTreatmentKind,
)
from tckdb_schemas.fragments.calculation import CalculationWithResultsPayload
from tckdb_schemas.fragments.geometry import GeometryPayload
from tckdb_schemas.fragments.identity import SpeciesEntryIdentityPayload
from tckdb_schemas.fragments.refs import (
    FreqScaleFactorRef,
    SoftwareReleaseRef,
    WorkflowToolReleaseRef,
)
from tckdb_schemas.literature import LiteratureUploadRequest
from tckdb_schemas.local_key_codes import (
    W_STATMECH_CALCULATION_KEY_UNDECLARED,
    undeclared_key_error,
)
from tckdb_schemas.statmech_bits import (
    StatmechSourceCalcIn,
    StatmechTorsionIn,
)
from tckdb_schemas.stationary_point import (
    StationaryPointFinding,
    evaluate_species_entry_frequency,
    raise_for_blocking_findings,
)
from tckdb_schemas.utils import normalize_optional_text
from tckdb_schemas.workflows.transport_upload import TransportUploadPayload


class ElectronicLevelIn(SchemaBase):
    """One electronic energy level for the electronic partition function.

    Ordered (energy, degeneracy) pairs relative to the ground state
    (DR-0033). E.g. OH X²Π: level 1 (0 cm⁻¹, g=2), level 2 (~139 cm⁻¹,
    g=2). ``level_index`` is 1-based and unique within a statmech record.
    """

    level_index: int = Field(ge=1)
    energy_cm1: float = Field(ge=0)
    degeneracy: int = Field(ge=1)


class ConformerUploadStatmechPayload(SchemaBase):
    """Workflow-facing statmech payload nested under conformer upload.

    The backend resolves referenced software/workflow provenance, creates or
    reuses the owning ``Statmech`` row for the resolved species entry, and links
    the newly created upload calculation as a source calculation when requested.

    ``source_calculations`` and ``torsions[*].source_scan_calculation_key``
    address calculations by the local ``key`` the enclosing
    ``ConformerUploadRequest`` put on its own primary/additional
    calculations — never by row id. ``ConformerUploadRequest`` validates
    that every such key was declared, so an unresolvable reference is a
    422 rather than a silently dropped provenance link.
    """

    scientific_origin: ScientificOriginKind = ScientificOriginKind.computed

    literature: LiteratureUploadRequest | None = None
    workflow_tool_release: WorkflowToolReleaseRef | None = None
    software_release: SoftwareReleaseRef | None = None

    external_symmetry: int | None = Field(default=None, ge=1)
    point_group: str | None = None

    is_linear: bool | None = None
    rigid_rotor_kind: RigidRotorKind | None = None
    statmech_treatment: StatmechTreatmentKind | None = None

    freq_scale_factor: FreqScaleFactorRef | None = None
    uses_projected_frequencies: bool | None = None
    optical_isomers: int | None = Field(default=None, ge=1)
    note: str | None = None

    uploaded_calculation_role: StatmechCalculationRole | None = None
    source_calculations: list[StatmechSourceCalcIn] = Field(default_factory=list)
    torsions: list[StatmechTorsionIn] = Field(default_factory=list)
    electronic_levels: list[ElectronicLevelIn] = Field(default_factory=list)

    @model_validator(mode="after")
    def normalize_optional_text_fields(self) -> Self:
        self.point_group = normalize_optional_text(self.point_group)
        self.note = normalize_optional_text(self.note)
        return self

    @model_validator(mode="after")
    def validate_electronic_levels(self) -> Self:
        indices = [lvl.level_index for lvl in self.electronic_levels]
        if len(set(indices)) != len(indices):
            raise ValueError(
                "electronic_levels level_index values must be unique."
            )
        return self

    @model_validator(mode="after")
    def validate_unique_source_calculation_pairs(self) -> Self:
        """Reject duplicate links the way the sibling paths already do.

        ``statmech_source_calculation`` is keyed on
        ``(statmech_id, calculation_id, role)``, so a repeated pair was a
        primary-key violation surfacing as a 500. The bundle and
        standalone statmech uploads validate this; the conformer path
        did not.

        The tuple carries a second discriminator alongside the key, read
        reflectively because this class is also the payload the *backend*
        hands the statmech resolution service — and the standalone
        statmech upload's entries are a subclass that may name a
        calculation by ``existing_calculation_id`` instead of by key.
        Without it, every such entry collapses to ``(None, role)`` and two
        genuinely different calculations sharing a role — one rotor scan
        per torsion, all of them ``role='scan'`` — are refused as a
        duplicate. Nothing changes for a payload built from this package:
        ``StatmechSourceCalcIn`` has no such attribute, so the second
        element is always ``None`` and the rule is the pair it always was.
        """
        pairs = [
            (
                sc.calculation_key,
                getattr(sc, "existing_calculation_id", None),
                sc.role,
            )
            for sc in self.source_calculations
        ]
        if len(set(pairs)) != len(pairs):
            raise ValueError(
                "statmech.source_calculations must be unique by "
                "(calculation_key, role)."
            )
        return self

    @model_validator(mode="after")
    def validate_unique_torsion_indices(self) -> Self:
        """One rotor per index, as the sibling upload paths already require."""
        indices = [torsion.torsion_index for torsion in self.torsions]
        if len(set(indices)) != len(indices):
            raise ValueError(
                "statmech.torsions torsion_index values must be unique."
            )
        return self

    @model_validator(mode="after")
    def validate_scientific_interpretation(self) -> Self:
        """Scope each requirement to the claim this record actually makes.

        ``statmech_treatment`` is deliberately NOT required. Real producers
        (ARC among them) deposit symmetry, rotational constants and a
        frequency scale factor without naming a treatment; an absent field is
        honest where an invented one would not be.

        Source calculations are likewise not required here. A monatomic
        species has no vibrational modes to point at, and an experimental,
        literature or imported statmech has no calculation at all — forcing
        either to fabricate one buys nothing. Their absence on a *computed*
        record is reported as a structured upload warning at the workflow
        seam, and is enforced as an error only where a rate coefficient
        actually depends on it (the kinetics interpretation seam).

        What IS enforced is the one claim that is self-contradictory when
        unsupported: a rotor-aware treatment is *defined* by the internal
        rotors it treats, so it must list them. With no torsions the correct
        treatment is plain ``rrho``.
        """
        rotor_aware = {"rrho_1d", "rrho_nd", "rrho_1d_nd"}
        if (
            self.statmech_treatment is not None
            and self.statmech_treatment.value in rotor_aware
            and not self.torsions
        ):
            raise ValueError(
                f"statmech_treatment='{self.statmech_treatment.value}' claims a "
                "rotor-aware treatment and must list the torsions it treated; "
                "use 'rrho' when the species has none."
            )
        return self


_ALLOWED_ADDITIONAL_TYPES = frozenset(
    {CalculationType.freq, CalculationType.sp}
)


class ConformerCalculationIn(CalculationWithResultsPayload):
    """A conformer-upload calculation that can be named by a local key.

    The key is what the nested statmech block points at. It exists so a
    depositor can say "the statmech's vibrational basis is *that* freq
    job" using a name they chose, rather than a calculation row id they
    would have to query for (DR-0029 Requirement 1).

    Optional by design: a payload with no statmech cross-references never
    needs one, and every payload written before keys existed stays valid.

    :param key: Optional local name for this calculation, unique within
        the request. Referenced from
        ``statmech.source_calculations[*].calculation_key`` and
        ``statmech.torsions[*].source_scan_calculation_key``.
    """

    key: str | None = Field(default=None, min_length=1)


class ConformerUploadRequest(SchemaBase):
    """Workflow-facing conformer upload payload.

    The backend resolves the species, species entry, geometry, and calculation
    provenance, then assigns or creates a conformer group and creates one new
    provenance-bearing observation row for this upload. If the geometry matches
    an existing basin, the group is reused but the observation is not silently
    deduplicated. Optionally, additional calculations (freq, sp) can be
    attached alongside the primary calculation, and they anchor to that same
    observation.
    """

    species_entry: SpeciesEntryIdentityPayload
    geometry: GeometryPayload
    calculation: ConformerCalculationIn
    additional_calculations: list[ConformerCalculationIn] = Field(
        default_factory=list
    )
    statmech: ConformerUploadStatmechPayload | None = None
    transport: TransportUploadPayload | None = None
    applied_energy_corrections: list[AppliedEnergyCorrectionUploadPayload] = Field(
        default_factory=list
    )

    scientific_origin: ScientificOriginKind = ScientificOriginKind.computed
    note: str | None = None
    label: str | None = Field(default=None, max_length=64)
    conformer_key: str | None = Field(
        default=None,
        min_length=1,
        description=(
            "Optional local name for the single conformer observation this "
            "upload creates, referenced from "
            "``applied_energy_corrections[*].source_conformer_key``. It is "
            "the conformer namespace's counterpart to "
            "``ConformerCalculationIn.key``, and it is deliberately not "
            "``label``: a label is a human tag that also participates in "
            "conformer-group matching, so renaming it for grouping reasons "
            "would silently break a correction reference, and a depositor "
            "who has no label has no way to point at their own conformer. "
            "Optional by design — a payload with no conformer "
            "cross-reference never needs one."
        ),
    )

    @model_validator(mode="after")
    def normalize_optional_text_fields(self) -> Self:
        self.note = normalize_optional_text(self.note)
        self.label = normalize_optional_text(self.label)
        self.conformer_key = normalize_optional_text(self.conformer_key)
        return self

    def declared_calculation_keys(self) -> list[str]:
        """Local keys this request put on its own calculations, in order."""
        return [
            calc.key
            for calc in [self.calculation, *self.additional_calculations]
            if calc.key is not None
        ]

    @model_validator(mode="after")
    def validate_unique_calculation_keys(self) -> Self:
        keys = self.declared_calculation_keys()
        if len(set(keys)) != len(keys):
            raise ValueError(
                "Conformer upload calculation keys must be unique within "
                "the request."
            )
        return self

    @model_validator(mode="after")
    def validate_statmech_calculation_keys_resolve(self) -> Self:
        """Every statmech calc reference must name a calculation declared here.

        The conformer upload has no other calc-key namespace to fall back
        on, so an unrecognised key can only be a mistake. Refusing it is
        the difference between a 422 the depositor can fix and a
        provenance link that silently never existed.

        The code is ``statmech_calculation_key_undeclared`` because that is
        what ``app.services.statmech_resolution`` answers when the same
        typo survives to the write path — this route reaches that seam, and
        ADR 0017 says the depositor must not be able to tell which of the
        two caught them.
        """
        if self.statmech is None:
            return self
        declared = set(self.declared_calculation_keys())
        for index, source in enumerate(self.statmech.source_calculations):
            if source.calculation_key not in declared:
                raise undeclared_key_error(
                    W_STATMECH_CALCULATION_KEY_UNDECLARED,
                    f"statmech.source_calculations[{index}].calculation_key "
                    f"'{source.calculation_key}' does not name a calculation "
                    f"declared in this upload. Put a matching 'key' on "
                    f"'calculation' or on one of 'additional_calculations'.",
                    field=f"statmech.source_calculations[{index}]",
                    key=source.calculation_key,
                    declared=declared,
                )
        for index, torsion in enumerate(self.statmech.torsions):
            key = torsion.source_scan_calculation_key
            if key is not None and key not in declared:
                raise undeclared_key_error(
                    W_STATMECH_CALCULATION_KEY_UNDECLARED,
                    f"statmech.torsions[{index}].source_scan_calculation_key "
                    f"'{key}' does not name a calculation declared in this "
                    f"upload.",
                    field=(
                        f"statmech.torsions[{index}]"
                        f".source_scan_calculation_key"
                    ),
                    key=key,
                    declared=declared,
                )
        return self

    @model_validator(mode="after")
    def validate_primary_calculation_not_linked_twice(self) -> Self:
        """One link per (calculation, role), counting the implicit one.

        ``uploaded_calculation_role`` already links the primary
        calculation. Naming that same calculation again with the same role
        in ``source_calculations`` asks for two rows with one primary key,
        which the database refuses — better said here, where the message
        can name the field, than as a 500 mid-transaction.
        """
        if self.statmech is None:
            return self
        role = self.statmech.uploaded_calculation_role
        primary_key = self.calculation.key
        if role is None or primary_key is None:
            return self
        for index, source in enumerate(self.statmech.source_calculations):
            if source.calculation_key == primary_key and source.role == role:
                raise ValueError(
                    f"statmech.source_calculations[{index}] links the primary "
                    f"calculation '{primary_key}' with role '{role.value}', "
                    f"which uploaded_calculation_role already links. Drop one."
                )
        return self

    @model_validator(mode="after")
    def validate_additional_calculation_types(self) -> Self:
        for calc in self.additional_calculations:
            if calc.type not in _ALLOWED_ADDITIONAL_TYPES:
                raise ValueError(
                    f"Additional calculation type '{calc.type.value}' is not "
                    f"allowed. Expected one of: "
                    f"{', '.join(t.value for t in sorted(_ALLOWED_ADDITIONAL_TYPES, key=lambda t: t.value))}."
                )
        return self

    def stationary_point_findings(self) -> list[StationaryPointFinding]:
        """Judge the declared kind against this upload's frequency evidence.

        Every calculation here is scoped to the one species entry named
        by ``species_entry``, so this payload is the earliest point at
        which the declaration and the evidence are both in hand.
        """
        kind = self.species_entry.species_entry_kind
        findings: list[StationaryPointFinding] = []
        for label, calc in [
            ("calculation", self.calculation),
            *(
                (f"additional_calculations[{i}]", c)
                for i, c in enumerate(self.additional_calculations)
            ),
        ]:
            if calc.freq_result is None:
                continue
            findings.extend(
                evaluate_species_entry_frequency(
                    kind,
                    calc.freq_result.n_imag,
                    calc.freq_result.imag_freq_cm1,
                    location=f"{label}.freq_result",
                )
            )
            findings.extend(
                calc.frequency_completeness_findings(
                    location=f"{label}.freq_result.modes",
                    fallback_xyz_text=self.geometry.xyz_text,
                )
            )
        return findings

    @model_validator(mode="after")
    def validate_n_imag_matches_species_entry_kind(self) -> Self:
        """Refuse frequency evidence that contradicts the declared kind.

        Definitional, therefore blocking (ADR 0008).
        """
        raise_for_blocking_findings(self.stationary_point_findings())
        return self
