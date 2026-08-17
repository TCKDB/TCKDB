"""Upload payloads for species-level statmech records.

``StatmechUploadRequest`` is the standalone upload payload accepted by
``POST /api/v1/uploads/statmech``. Supporting calculations are declared
inline and referenced by local string keys, and provenance refs use the
existing upload fragments, so the upload boundary stays FK-free.

The nested statmech payload (``ConformerUploadStatmechPayload``) now uses
the same key-based reference components from
``tckdb_schemas.statmech_bits``; it resolves those keys against the
``key`` fields the conformer upload puts on its own calculations.
"""

from __future__ import annotations

from typing import Self

from pydantic import Field, model_validator
from tckdb_schemas.local_key_codes import (
    W_STATMECH_CALCULATION_KEY_UNDECLARED,
    undeclared_key_error,
)
from tckdb_schemas.stationary_point import (
    StationaryPointFinding,
    raise_for_blocking_findings,
)
from tckdb_schemas.statmech_bits import (
    StatmechSourceCalcIn,
    StatmechTorsionIn,
)

from app.db.models.common import (
    RigidRotorKind,
    ScientificOriginKind,
    StatmechTreatmentKind,
)
from app.schemas.common import SchemaBase
from app.schemas.fragments.calculation import CalculationWithResultsPayload
from app.schemas.fragments.identity import SpeciesEntryIdentityPayload
from app.schemas.fragments.refs import (
    FreqScaleFactorRef,
    SoftwareReleaseRef,
    WorkflowToolReleaseRef,
)
from app.schemas.utils import normalize_optional_text
from app.schemas.workflows.conformer_upload import ElectronicLevelIn
from app.schemas.workflows.literature_upload import LiteratureUploadRequest
from app.schemas.workflows.stationary_point_seam import inline_calculation_findings


class StatmechCalculationIn(SchemaBase):
    """An inline supporting calculation declared within a statmech upload.

    :param key: Local string key used to reference this calculation from
        ``source_calculations`` and torsion ``source_scan_calculation_key``
        fields. Must be unique within the upload.
    :param calculation: Scientific content for the calculation. Resolved
        and persisted by the workflow, scoped to the same species entry
        as the statmech target.
    """

    key: str = Field(min_length=1)
    calculation: CalculationWithResultsPayload


class StatmechSourceCalculationIn(StatmechSourceCalcIn):
    """Statmech → calculation link, for the *standalone* statmech upload.

    Exactly one of ``calculation_key`` (a calculation declared inline in
    this same request) or ``existing_calculation_id`` (a calculation row
    a *previous* request already deposited) must be given. It widens the
    shared wire component :class:`~tckdb_schemas.statmech_bits.StatmechSourceCalcIn`,
    which stays key-only, and the widening is deliberately scoped to this
    one contract — see "Why only here" below.

    Why chaining exists at all
    --------------------------
    Local keys resolve only within one payload. Without a way to name a
    calculation deposited earlier, a statmech deposit has to re-send the
    opt/freq/sp jobs it already stored. Calculations are append-only and
    are **never deduplicated**, so a re-send mints a second row for the
    same job. That is not merely wasted space: it destroys the meaning of
    counting candidates. "Seven independent depositions, twenty-one
    distinct calculations" is evidence of reproducibility only while a
    calculation row means *a job someone ran*; once re-deposits mint
    duplicates the count silently becomes "how many times someone
    re-uploaded", and the store cannot tell the two apart.

    The counter-argument — that a self-contained deposit is stronger for
    provenance, because a record can then never cite something that was
    not reviewed alongside it — was weighed and accepted as the smaller
    loss.

    Audience guidance
    -----------------
    * ``calculation_key`` is the **contributor-facing** path. Web uploads,
      community contributors and general workflow tools use local string
      keys so a depositor never needs to know a database id.
    * ``existing_calculation_id`` is **programmatic chaining**: a client
      threading ids back out of a prior TCKDB upload response (ARC's
      adapter chaining from its conformer upload, or replay/admin/repair
      tooling). It is *not* the primary public upload UX.

    The ``existing_*_id`` convention is deliberately id-based, and it is
    **not** a violation of the "no FK IDs in upload schemas" rule. That
    rule governs contributor-facing *scientific content* — a depositor
    describes a molecule, not a row. This field describes neither; it is
    a client quoting back an id TCKDB itself just issued to it. Please do
    not "fix" it into a key: there is no key namespace spanning two
    requests, which is the entire problem it solves.

    Nothing is skipped on this path. A chained citation is loaded, checked
    for owner-consistency against the statmech target's species entry, and
    put through the identical role/type contract
    (:func:`app.services.statmech_resolution.assert_statmech_role_compatible`)
    that a locally-keyed citation passes. If it were cheaper to validate,
    it would become the route depositors use to get around validation.

    Why only here (the bundle answer)
    ---------------------------------
    The contribution-bundle routes (``/uploads/computed-species``,
    ``/uploads/computed-reaction``, and the PDep network path) do **not**
    get this field, and their omission is a decision rather than an
    oversight. A bundle is self-contained by construction: it carries one
    global calc-key namespace covering every calculation the bundle
    deposits, so every citation a bundle needs to make is expressible as a
    key within the request it arrives in. There is nothing for chaining to
    reach for. Those paths therefore keep the key-only
    ``StatmechSourceCalcIn``, and because ``SchemaBase`` sets
    ``extra="forbid"`` a bundle that sends ``existing_calculation_id``
    is refused with ``extra_forbidden`` rather than silently ignored.

    The mirror of this field is
    :class:`app.schemas.workflows.thermo_upload.ThermoSourceCalculationIn`
    ``.existing_calculation_id``, which does the same job for thermo. The
    two are intended to stay symmetric; an asymmetry between them is a
    bug, not a signal. (Statmech reached this contract later only because
    PR #148 moved it to local keys without a chaining mechanism, and the
    gap read as deliberate because nothing recorded that it was not.)

    :param calculation_key: Local key of a calculation declared in this
        request's ``calculations`` list.
    :param existing_calculation_id: Database id of a calculation row a
        previous request already deposited. Programmatic chaining only;
        contributor-facing uploads should prefer ``calculation_key``. The
        workflow validates existence, owner-consistency and role/type
        compatibility before linking.
    :param role: Scientific role the calculation plays for this statmech.
    """

    # Widening a base class's mutable attribute is a Liskov violation, and
    # mypy is right to say so. It is nonetheless the declared design here,
    # and the suppression is narrowed to this one line and this one error
    # code rather than the module.
    #
    # The subclass relationship is load-bearing at *runtime*, not just
    # cosmetic: ``ConformerUploadStatmechPayload.source_calculations`` is
    # annotated with the key-only base, and Pydantic v2's
    # ``revalidate_instances="never"`` is what lets an instance of this
    # subclass pass through that field with ``existing_calculation_id``
    # intact. Break the inheritance and Pydantic revalidates into the base,
    # silently dropping the chained id -- the exact failure
    # ``test_statmech_upload_chained_id_survives_payload_handover`` exists
    # to pin. So the fix mypy would want is the one thing that must not
    # happen.
    #
    # The reason it is safe is a runtime invariant, not a type: the
    # ``validate_exactly_one_reference`` validator below guarantees that
    # whenever ``calculation_key`` is None, ``existing_calculation_id`` is
    # not -- and the only consumer that reads ``calculation_key`` as a
    # non-optional ``str``
    # (``app.services.statmech_resolution._resolve_calculation_key``) is
    # reached only on the branch where the chained id was absent.
    calculation_key: str | None = Field(  # type: ignore[assignment]
        default=None, min_length=1
    )
    existing_calculation_id: int | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def validate_exactly_one_reference(self) -> Self:
        """Require exactly one of calculation_key or existing_calculation_id."""
        if (self.calculation_key is None) == (self.existing_calculation_id is None):
            raise ValueError(
                "source_calculations entry must specify exactly one of "
                "calculation_key or existing_calculation_id."
            )
        return self


class StatmechUploadRequest(SchemaBase):
    """Workflow-facing standalone statmech upload payload.

    The backend resolves the target species entry, persists any inline
    supporting calculations, resolves provenance references, and routes
    the resulting scientific payload through the canonical statmech
    resolution service. Statmech is append-only — repeated uploads
    against the same species entry create independent rows.

    :param species_entry: Identity payload used to resolve the owning
        species entry.
    :param scientific_origin: Scientific origin category for this record.
    :param literature: Optional literature submission payload.
    :param workflow_tool_release: Optional workflow-tool provenance.
    :param software_release: Optional software provenance.
    :param external_symmetry: Optional external symmetry number.
    :param rotational_constant_a_cm1: Optional A rotational constant (cm⁻¹).
    :param rotational_constant_b_cm1: Optional B rotational constant (cm⁻¹).
    :param rotational_constant_c_cm1: Optional C rotational constant (cm⁻¹).
    :param point_group: Optional point-group label.
    :param is_linear: Optional linearity flag.
    :param rigid_rotor_kind: Optional rigid-rotor classification.
    :param statmech_treatment: Optional treatment classification.
    :param freq_scale_factor: Optional frequency scale factor ref.
    :param uses_projected_frequencies: Optional projected-frequency flag.
    :param note: Optional free-text note.
    :param calculations: Inline supporting calculations declared by key.
    :param source_calculations: Statmech → calculation links by key/role.
    :param torsions: Torsion definitions (source scans addressed by key).
    """

    species_entry: SpeciesEntryIdentityPayload

    scientific_origin: ScientificOriginKind = ScientificOriginKind.computed

    literature: LiteratureUploadRequest | None = None
    workflow_tool_release: WorkflowToolReleaseRef | None = None
    software_release: SoftwareReleaseRef | None = None

    external_symmetry: int | None = Field(default=None, ge=1)
    rotational_constant_a_cm1: float | None = Field(default=None, gt=0)
    rotational_constant_b_cm1: float | None = Field(default=None, gt=0)
    rotational_constant_c_cm1: float | None = Field(default=None, gt=0)
    point_group: str | None = None

    is_linear: bool | None = None
    rigid_rotor_kind: RigidRotorKind | None = None
    statmech_treatment: StatmechTreatmentKind | None = None

    freq_scale_factor: FreqScaleFactorRef | None = None
    uses_projected_frequencies: bool | None = None
    optical_isomers: int | None = Field(default=None, ge=1)
    note: str | None = None

    calculations: list[StatmechCalculationIn] = Field(default_factory=list)

    source_calculations: list[StatmechSourceCalculationIn] = Field(
        default_factory=list
    )

    torsions: list[StatmechTorsionIn] = Field(default_factory=list)
    electronic_levels: list[ElectronicLevelIn] = Field(default_factory=list)

    @model_validator(mode="after")
    def normalize_text_fields(self) -> Self:
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
    def validate_unique_calculation_keys(self) -> Self:
        keys = [c.key for c in self.calculations]
        if len(set(keys)) != len(keys):
            raise ValueError("Statmech calculations must have unique keys.")
        return self

    def stationary_point_findings(self) -> list[StationaryPointFinding]:
        """Judge the declared kind against this upload's inline frequency evidence."""
        return inline_calculation_findings(
            self.species_entry.species_entry_kind, list(self.calculations)
        )

    @model_validator(mode="after")
    def validate_n_imag_matches_species_entry_kind(self) -> Self:
        """Refuse inline frequency evidence that contradicts the declared kind.

        Definitional, therefore blocking (ADR 0008). The inline
        calculations are scoped to this upload's species entry, so the
        declared kind and the parsed ``n_imag`` are both present here.
        """
        raise_for_blocking_findings(self.stationary_point_findings())
        return self

    @model_validator(mode="after")
    def validate_source_calculation_keys_exist(self) -> Self:
        """Every *local* citation must name a calculation this request declares.

        Chained citations (``existing_calculation_id``) carry no key and
        are skipped here on purpose: the row they name was deposited by an
        earlier request, so this payload cannot possibly declare it. They
        are not thereby unchecked — existence, owner-consistency and
        role/type compatibility are all enforced against the database in
        :func:`app.services.statmech_resolution.resolve_or_create_statmech`,
        which is the only place that can check them.
        """
        defined = {c.key for c in self.calculations}
        for index, sc in enumerate(self.source_calculations):
            if sc.calculation_key is None:
                continue
            if sc.calculation_key not in defined:
                raise undeclared_key_error(
                    W_STATMECH_CALCULATION_KEY_UNDECLARED,
                    f"source_calculations references undefined "
                    f"calculation_key '{sc.calculation_key}'.",
                    field=f"statmech.source_calculations[{index}]",
                    key=sc.calculation_key,
                    declared=defined,
                )
        return self

    @model_validator(mode="after")
    def validate_unique_source_calculation_pairs(self) -> Self:
        """Refuse a repeated link, whichever way the calculation is named.

        ``statmech_source_calculation`` is keyed on ``(statmech_id,
        calculation_id, role)``, so a duplicate is a primary-key violation
        surfacing as a 500. The reference is now two-valued, so the
        uniqueness tuple carries both halves — the same shape thermo's
        ``ThermoSourceCalculationIn`` uniqueness check uses.

        One duplicate this cannot see: the same calculation cited once by
        inline key and once by id. That is impossible in practice, because
        an inline calculation is minted by this request and so has no id an
        earlier request could have handed out; the database's own primary
        key remains the backstop either way.
        """
        pairs = [
            (sc.calculation_key, sc.existing_calculation_id, sc.role)
            for sc in self.source_calculations
        ]
        if len(set(pairs)) != len(pairs):
            raise ValueError(
                "source_calculations must be unique by (calculation_key, "
                "existing_calculation_id, role)."
            )
        return self

    @model_validator(mode="after")
    def validate_unique_torsion_indices(self) -> Self:
        indices = [t.torsion_index for t in self.torsions]
        if len(set(indices)) != len(indices):
            raise ValueError(
                "Torsion indices must be unique within a statmech upload."
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

    @model_validator(mode="after")
    def validate_torsion_scan_calculation_keys(self) -> Self:
        defined = {c.key for c in self.calculations}
        for i, torsion in enumerate(self.torsions):
            key = torsion.source_scan_calculation_key
            if key is not None and key not in defined:
                raise undeclared_key_error(
                    W_STATMECH_CALCULATION_KEY_UNDECLARED,
                    f"torsions[{i}].source_scan_calculation_key '{key}' "
                    f"does not reference a declared calculation.",
                    field=(
                        f"statmech.torsions[{i}].source_scan_calculation_key"
                    ),
                    key=key,
                    declared=defined,
                )
        return self
