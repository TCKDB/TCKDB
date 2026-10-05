"""Wire contract for ``POST /api/v1/uploads/transition-states``.

Supports uploading a transition state with an embedded reaction description
(reactants/products by scientific content), a required primary optimisation
calculation, and optional additional calculations (freq, sp, irc, scan,
path_search).

The backend resolves the reaction identity, creates the TS concept and entry,
resolves the geometry, and persists calculations.
"""

from typing import Any, Self

from pydantic import ConfigDict, Field, field_validator, model_validator

from tckdb_schemas.common import SchemaBase
from tckdb_schemas.energy_correction import AppliedEnergyCorrectionUploadPayload
from tckdb_schemas.enums import CalculationType, ReactionRole
from tckdb_schemas.fragments.calculation import CalculationWithResultsPayload
from tckdb_schemas.fragments.geometry import GeometryPayload
from tckdb_schemas.fragments.identity import SpeciesEntryIdentityPayload
from tckdb_schemas.fragments.reaction_atom_map import (
    AtomMapParticipantGeometry,
    ReactionAtomMapIn,
    validate_reaction_atom_map,
)
from tckdb_schemas.fragments.ts_validation_evidence import (
    TransitionStateValidationEvidenceIn,
    validate_ts_evidence_set,
)
from tckdb_schemas.reaction_family import find_canonical_reaction_family
from tckdb_schemas.rights import DepositRights
from tckdb_schemas.shared.calculation_in import GeometryIn
from tckdb_schemas.stationary_point import (
    StationaryPointFinding,
    raise_for_blocking_findings,
)
from tckdb_schemas.structure_declarations import (
    StructureDeterminationDeclaration,
    assert_structure_pin_keys_declared,
)
from tckdb_schemas.utils import normalize_optional_text

# ---------------------------------------------------------------------------
# Embedded reaction content (no FK IDs — resolved by the workflow)
# ---------------------------------------------------------------------------


class TSReactionParticipantUpload(SchemaBase):
    """One participant slot in the TS reaction description.

    :param species_entry: Species-entry identity payload to resolve or create.
    :param note: Optional note stored on the structured participant row.
    :param key: Local name for this participant, which an ``atom_map`` uses to
        say which participant a mapping is about. Required on every
        participant when the request carries an ``atom_map`` and unused
        otherwise. Unique within the request.
    :param geometry: The participant's own geometry, which an ``atom_map``
        counts its atom indices into. Optional; a participant the map does not
        map (or one with no atoms, such as a free electron) has none. Its
        ``key`` is what the map's ``geometry_key`` names, and is unique within
        the request. Stored as a geometry only; nothing is computed from it.
    """

    species_entry: SpeciesEntryIdentityPayload
    note: str | None = None
    key: str | None = Field(default=None, min_length=1)
    geometry: GeometryIn | None = None

    @model_validator(mode="after")
    def normalize_note(self) -> Self:
        self.note = normalize_optional_text(self.note)
        return self


class TSReactionUpload(SchemaBase):
    """Embedded reaction content for a transition-state upload.

    :param reversible: Whether the reaction is reversible. Omitted means
        ``true``, the same as on the computed-reaction route: a transition
        state belongs to an elementary step, and an elementary step is
        reversible by microscopic reversibility. Send ``false`` only to state
        that the step is irreversible.
    :param reaction_family: Optional reaction-family label.
    :param reaction_family_source_note: Required when the family is non-canonical.
    :param reactants: Ordered reactant participants.
    :param products: Ordered product participants.
    """

    reversible: bool = Field(
        default=True,
        description=(
            "Whether the reaction is reversible. Omitted means true: an "
            "elementary step is reversible by microscopic reversibility. "
            "Send false only to state the step is irreversible."
        ),
    )
    reaction_family: str | None = None
    reaction_family_source_note: str | None = None
    reactants: list[TSReactionParticipantUpload] = Field(min_length=1)
    products: list[TSReactionParticipantUpload] = Field(min_length=1)

    @field_validator("reaction_family", "reaction_family_source_note")
    @classmethod
    def normalize_reaction_family(cls, value: str | None) -> str | None:
        return normalize_optional_text(value)

    @model_validator(mode="after")
    def validate_reaction_family(self) -> Self:
        if self.reaction_family is None:
            if self.reaction_family_source_note is not None:
                raise ValueError(
                    "reaction_family_source_note requires reaction_family."
                )
            return self

        if find_canonical_reaction_family(self.reaction_family) is None:
            if self.reaction_family_source_note is None:
                raise ValueError(
                    "reaction_family_source_note is required when reaction_family "
                    "is not a supported canonical family."
                )
        return self


# ---------------------------------------------------------------------------
# Top-level upload request
# ---------------------------------------------------------------------------

_ALLOWED_ADDITIONAL_TYPES = frozenset(
    {
        CalculationType.freq,
        CalculationType.sp,
        CalculationType.irc,
        CalculationType.path_search,
        # A rotor scan on the saddle point, which the computed-reaction bundle
        # already accepts. Nothing on this route consumes it (there is no
        # statmech block here), so it is stored as a calculation with its
        # points and a reader can find it; the bundle is the route that also
        # ties it to a torsion.
        CalculationType.scan,
    }
)


class TransitionStateCalculationIn(CalculationWithResultsPayload):
    """A transition-state-upload calculation that a structure determination can name by a local key.

    The key exists only so a ``structure_determinations`` entry can say "the energy is *that* single point"
    using a name the depositor chose, rather than a calculation row id (DR-0029 Requirement 1). Optional: a
    payload with no determination never needs one. It is not a namespace for evidence, corrections or
    anything else on this route.

    :param key: Optional local name for this calculation, unique within the request.
    """

    key: str | None = Field(default=None, min_length=1)


class TransitionStateUploadRequest(SchemaBase):
    """Workflow-facing transition-state upload payload.

    The backend resolves the reaction from the embedded content, creates a
    ``TransitionState`` concept and ``TransitionStateEntry``, resolves the
    geometry and calculation provenance, and optionally attaches additional
    calculations.

    :param reaction: Reaction described by scientific content (reactants/products).
    :param charge: Net charge of the TS structure.
    :param multiplicity: Spin multiplicity of the TS structure.
    :param unmapped_smiles: Optional SMILES for the TS (no atom maps).
    :param geometry: Saddle-point geometry payload (XYZ text).
    :param primary_opt: Required primary optimisation calculation.
    :param additional_calculations: Optional freq / sp / irc / scan / path_search
        calculations. A ``path_search`` additional calculation models a
        TS-guess generator (NEB, GSM, ...) and is wired as the parent of
        the primary opt via ``calculation_dependency.role = optimized_from``.
    :param geometry_key: Local name for ``geometry``, which the ``atom_map``
        names as its ``ts_geometry_key``. Required when an ``atom_map`` is
        supplied, unused otherwise.
    :param applied_energy_corrections: Applied energy corrections targeting
        this transition state directly (AEC, BAC, SOC totals, with optional
        component breakdowns). This payload has no calculation-key or
        conformer-key namespace, so ``source_calculation_key`` and
        ``source_conformer_key`` are not accepted, and neither is a frequency
        scale factor, which is defined by the frequency calculation it was
        applied to.
    :param atom_map: Which atom of each reactant and product is which atom of
        the saddle point (ADR 0011), supplied and never derived. It names the
        participants by their ``key`` and the geometries by the ``key`` of the
        participant ``geometry`` it counts into; ``ts_geometry_key`` names
        ``geometry_key``. Optional: a deposit without one succeeds and returns
        a ``reaction_atom_map_absent`` upload warning.
    :param label: Optional human-readable label for the TS concept.
    :param note: Optional free-text note on the TS concept.
    """

    # A minimal valid payload. Published as the JSON Schema's ``examples``, in
    # the OpenAPI document, and in the producer contract, which validates it
    # against this model on every generation (generate_producer_contract.py).
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "reaction": {
                        "reversible": True,
                        "reactants": [
                            {
                                "species_entry": {
                                    "smiles": "[H]",
                                    "charge": 0,
                                    "multiplicity": 2
                                }
                            },
                            {
                                "species_entry": {
                                    "smiles": "[H][H]",
                                    "charge": 0,
                                    "multiplicity": 1
                                }
                            }
                        ],
                        "products": [
                            {
                                "species_entry": {
                                    "smiles": "[H][H]",
                                    "charge": 0,
                                    "multiplicity": 1
                                }
                            },
                            {
                                "species_entry": {
                                    "smiles": "[H]",
                                    "charge": 0,
                                    "multiplicity": 2
                                }
                            }
                        ]
                    },
                    "charge": 0,
                    "multiplicity": 2,
                    "geometry": {
                        "xyz_text": "3\nH3 transition state\nH 0.0 0.0 -0.93\nH 0.0 0.0 0.0\nH 0.0 0.0 0.93"
                    },
                    "primary_opt": {
                        "type": "opt",
                        "software_release": {
                            "name": "Gaussian",
                            "version": "16"
                        },
                        "level_of_theory": {
                            "method": "wb97xd",
                            "basis": "def2-tzvp"
                        }
                    }
                }
            ]
        },
    )

    reaction: TSReactionUpload
    charge: int
    multiplicity: int = Field(ge=1)
    unmapped_smiles: str | None = None

    # Deposit-time license agreement; see ``tckdb_schemas.rights``. Optional
    # so existing clients keep working -- absence bites at release time.
    rights: DepositRights | None = None

    geometry: GeometryPayload
    primary_opt: TransitionStateCalculationIn
    additional_calculations: list[TransitionStateCalculationIn] = Field(
        default_factory=list
    )
    structure_determinations: list[StructureDeterminationDeclaration] = Field(
        default_factory=list,
        max_length=16,
        description=(
            "Source-attributed claims about this saddle point's geometry, each pinning the calculations of "
            "this upload (by their 'key') that play its roles. Optional; a transition state deposited "
            "without one reads as 'not stated'. Nothing is inferred from the calculations themselves."
        ),
    )
    validation_evidence: list[TransitionStateValidationEvidenceIn] = Field(
        default_factory=list,
        description=(
            "Structured validation evidence, at most one record per kind. "
            "kind='irc' is evidence that this saddle point connects the "
            "declared reactants and products; optional but strongly "
            "recommended: a deposit without a passing one succeeds and "
            "returns a 'transition_state_missing_irc_evidence' upload "
            "warning. kind='imaginary_mode' records what the frequency "
            "calculation found. This payload has no calculation-key "
            "namespace, so evidence binds to the upload's single additional "
            "calculation of the needed type ('irc' or 'freq'); "
            "source_calculation_key must be omitted. kind='energy_ordering' "
            "compares the saddle point with the wells' own calculations, "
            "which this payload does not carry, so it is accepted only on the "
            "computed-reaction upload."
        ),
    )
    geometry_key: str | None = Field(default=None, min_length=1)
    applied_energy_corrections: list[AppliedEnergyCorrectionUploadPayload] = Field(
        default_factory=list,
    )
    atom_map: ReactionAtomMapIn | None = Field(
        default=None,
        description=(
            "Which atom of each reactant and product is which atom of the "
            "saddle point (ADR 0011). Supplied by the depositor and never "
            "derived. Requires 'geometry_key' on the request and a 'key' on "
            "every reaction participant; the participants' 'geometry' keys are "
            "what the map counts into. Optional: a deposit without one "
            "succeeds and returns a 'reaction_atom_map_absent' upload warning."
        ),
    )

    label: str | None = None
    note: str | None = None

    @model_validator(mode="after")
    def normalize_text(self) -> Self:
        self.label = normalize_optional_text(self.label)
        self.note = normalize_optional_text(self.note)
        self.unmapped_smiles = normalize_optional_text(self.unmapped_smiles)
        return self

    def declared_calculation_keys(self) -> list[str]:
        """Local keys this request put on its own calculations, in order."""
        return [
            calc.key for calc in [self.primary_opt, *self.additional_calculations] if calc.key is not None
        ]

    @field_validator("primary_opt", "additional_calculations", mode="before")
    @classmethod
    def accept_plain_calculation_payloads(cls, value: Any) -> Any:
        """A ``CalculationWithResultsPayload`` built in Python is accepted as it always was.

        The two fields are typed :class:`TransitionStateCalculationIn` so a determination can name a calculation by
        key; a producer that builds the request from the shared payload (without a key) must not be refused for
        that, so a plain payload is lifted to the keyed type here, field for field, with no key.
        """

        def lift(item: Any) -> Any:
            if isinstance(item, CalculationWithResultsPayload) and not isinstance(item, TransitionStateCalculationIn):
                return TransitionStateCalculationIn.model_validate(item, from_attributes=True)
            return item

        return [lift(item) for item in value] if isinstance(value, list) else lift(value)

    @model_validator(mode="after")
    def validate_calculation_keys(self) -> Self:
        """Calculation keys are unique, and every one a determination pins is declared here."""
        keys = self.declared_calculation_keys()
        if len(set(keys)) != len(keys):
            raise ValueError("Transition-state upload calculation keys must be unique within the request.")
        assert_structure_pin_keys_declared(self.structure_determinations, set(keys))
        return self

    @model_validator(mode="after")
    def validate_primary_opt_is_opt(self) -> Self:
        if self.primary_opt.type != CalculationType.opt:
            raise ValueError(
                f"primary_opt must have type 'opt', "
                f"got '{self.primary_opt.type.value}'."
            )
        return self

    @model_validator(mode="after")
    def validate_validation_evidence(self) -> Self:
        """Bind each evidence record to the calculation that produced it.

        There are no bundle-local calculation keys here, so the locator is the
        calculation itself: an ``irc`` record is depositable alongside exactly
        one ``irc`` additional calculation and an ``imaginary_mode`` record
        alongside exactly one ``freq`` one. That is not a limitation invented
        here -- ``transition_state_validation_evidence`` stores a single
        source calculation per record, so one record can only ever name one.

        ``energy_ordering`` is refused outright rather than half-supported.
        Its energies are the saddle point's and the wells', each from its own
        calculation, and this payload carries the saddle point's calculations
        only; the reactants and products are identities with no calculations
        at all.
        """
        if not self.validation_evidence:
            return self

        for record in self.validation_evidence:
            if record.source_calculation_key is not None:
                raise ValueError(
                    "source_calculation_key is not accepted on a standalone "
                    "transition-state upload: it has no calculation-key "
                    "namespace, and evidence binds to the upload's single "
                    "additional calculation of the needed type."
                )
            if record.kind == "energy_ordering":
                raise ValueError(
                    "kind='energy_ordering' is not accepted on a standalone "
                    "transition-state upload: it compares the saddle point "
                    "with the reactants' and products' calculations, which "
                    "this payload does not carry. Deposit it through the "
                    "computed-reaction upload."
                )

        for kind, calculation_type in (
            ("irc", CalculationType.irc),
            ("imaginary_mode", CalculationType.freq),
        ):
            if not any(record.kind == kind for record in self.validation_evidence):
                continue
            bindable = [
                calc
                for calc in self.additional_calculations
                if calc.type == calculation_type
            ]
            if len(bindable) != 1:
                raise ValueError(
                    "validation_evidence requires exactly one additional "
                    f"calculation of type '{calculation_type.value}' to bind to; found "
                    f"{len(bindable)}."
                )

        validate_ts_evidence_set(
            self.validation_evidence,
            subject_label=self.label or "transition state",
            xyz_text=self.geometry.xyz_text,
            # Kinds rather than counts: a participant that legitimately has no
            # atoms maps to an empty list, and only the declared kind says
            # which participant that is. This payload carries each
            # participant's full identity inline, so the kinds are right here.
            reactant_kinds=[
                participant.species_entry.molecule_kind
                for participant in self.reaction.reactants
            ],
            product_kinds=[
                participant.species_entry.molecule_kind
                for participant in self.reaction.products
            ],
        )
        return self

    @model_validator(mode="after")
    def validate_applied_energy_corrections(self) -> Self:
        """Refuse the links this payload has no namespace to resolve.

        A correction's ``source_calculation_key`` and ``source_conformer_key``
        resolve against a namespace the enclosing request declares, and this
        one declares neither. A key with nothing to resolve against is
        refused rather than dropped: a link the depositor believes was
        recorded and was not is worse than one they were told they cannot
        make. A frequency scale factor requires the calculation it was applied
        to, so it is refused for the same reason.
        """
        for index, correction in enumerate(self.applied_energy_corrections):
            field = f"applied_energy_corrections[{index}]"
            if correction.frequency_scale_factor is not None:
                raise ValueError(
                    f"{field}.frequency_scale_factor is not accepted on a "
                    "standalone transition-state upload: a scale factor is "
                    "defined by the frequency calculation it was applied to, "
                    "and this payload has no calculation-key namespace to name "
                    "it in."
                )
            if correction.source_calculation_key is not None:
                raise ValueError(
                    f"{field}.source_calculation_key is not accepted on a "
                    "standalone transition-state upload: it has no "
                    "calculation-key namespace to resolve it against."
                )
            if correction.source_conformer_key is not None:
                raise ValueError(
                    f"{field}.source_conformer_key is not accepted on a "
                    "transition-state upload: a transition state declares no "
                    "conformers."
                )
        return self

    def atom_map_participants(self) -> list[AtomMapParticipantGeometry]:
        """Describe every declared participant and the geometry it may use.

        Assembled here because the map needs the reaction's participant slots
        and each participant's geometry at the same time, and neither nested
        model can see both.
        """

        participants: list[AtomMapParticipantGeometry] = []
        for side, members in (
            (ReactionRole.reactant, self.reaction.reactants),
            (ReactionRole.product, self.reaction.products),
        ):
            for position, member in enumerate(members, start=1):
                if member.key is None:
                    # ``validate_atom_map`` reports the missing key with a
                    # message that names the field; do not pre-empt it.
                    continue
                xyz_by_key = (
                    {member.geometry.key: member.geometry.xyz_text}
                    if member.geometry is not None
                    else {}
                )
                participants.append(
                    AtomMapParticipantGeometry(
                        side=side,
                        species_key=member.key,
                        participant_index=position,
                        geometry_keys=frozenset(xyz_by_key),
                        xyz_by_geometry_key=xyz_by_key,
                        molecule_kind=member.species_entry.molecule_kind,
                    )
                )
        return participants

    @model_validator(mode="after")
    def validate_participant_keys_and_geometries(self) -> Self:
        """Participant keys and geometry keys are each unique in the request.

        Checked whether or not an ``atom_map`` was supplied, because a
        participant geometry is stored either way and two that share a key
        could not be told apart by anything that later names one.
        """
        members = [*self.reaction.reactants, *self.reaction.products]
        keys = [member.key for member in members if member.key is not None]
        if len(set(keys)) != len(keys):
            raise ValueError("reaction participant keys must be unique.")
        geometry_keys = [
            member.geometry.key for member in members if member.geometry is not None
        ]
        if self.geometry_key is not None:
            geometry_keys.append(self.geometry_key)
        if len(set(geometry_keys)) != len(geometry_keys):
            raise ValueError(
                "participant geometry keys and geometry_key must be unique."
            )
        return self

    @model_validator(mode="after")
    def validate_atom_map(self) -> Self:
        """Refuse a self-contradictory atom map; accept an incomplete one.

        Runs here rather than on a nested model because the map spans the
        reaction's participant slots, every participant's geometry, and the
        saddle point's geometry.
        """
        if self.atom_map is None:
            return self
        if self.geometry_key is None:
            raise ValueError(
                "atom_map requires geometry_key, the local name of the "
                "saddle-point geometry the map's ts_geometry_key refers to."
            )
        for side, members in (
            ("reactants", self.reaction.reactants),
            ("products", self.reaction.products),
        ):
            for position, member in enumerate(members):
                if member.key is None:
                    raise ValueError(
                        f"atom_map requires every reaction participant to "
                        f"carry a key; reaction.{side}[{position}] has none."
                    )
        validate_reaction_atom_map(
            self.atom_map,
            participants=self.atom_map_participants(),
            ts_geometry_key=self.geometry_key,
            ts_xyz_text=self.geometry.xyz_text,
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
        """Judge this saddle point against its own frequency evidence.

        The whole payload is one transition state, so every frequency
        result in it describes that saddle point — there is no species
        entry here whose zero-imaginary-mode expectation could be
        confused with the TS's one.
        """
        findings: list[StationaryPointFinding] = []
        for label, calc in [
            ("primary_opt", self.primary_opt),
            *(
                (f"additional_calculations[{i}]", c)
                for i, c in enumerate(self.additional_calculations)
            ),
        ]:
            findings.extend(
                calc.transition_state_frequency_findings(
                    location=f"{label}.freq_result"
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
    def validate_reaction_coordinate_contract(self) -> Self:
        """Refuse frequency evidence with no usable reaction coordinate.

        Definitional, therefore blocking (ADR 0008, narrowed by ADR
        0012: at least one imaginary mode, exactly one designated the
        reaction coordinate, and no undeclared mode stiff enough to make
        that designation meaningless).
        """
        raise_for_blocking_findings(self.stationary_point_findings())
        return self
