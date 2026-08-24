"""Workflow-facing upload schema for pressure-dependent reaction networks.

This is the unified "big payload" schema. A single request contains:
- Species with conformers, geometries, and calculations
- Transition states with geometries and calculations
- Micro reactions (elementary steps)
- Network states and channels (topology)
- Master-equation solve configuration with source calculation references

All nested objects use local string keys so the backend can wire FK
relationships without exposing database IDs in the user-facing API.

Key uniqueness rules:
- Calculation keys and geometry keys are globally unique across the entire request.
- Species, state, reaction, and TS keys are unique within their own collections
  but may overlap across collections (different namespaces).

This schema expects one connected network component — disconnected subnetworks
are rejected when channels are explicitly provided.

See DR-0001 for design rationale.
"""

import math
from typing import Literal, Self

from pydantic import Field, field_validator, model_validator

from app.db.models.common import (
    ArrheniusAUnits,
    CalculationType,
    EnergyCorrectionConvention,
    EnergyZeroConvention,
    NetworkChannelKind,
    NetworkChannelMechanism,
    NetworkEnergyTransferScope,
    NetworkKineticsModelKind,
    NetworkSolveCalculationRole,
    NetworkSolveKind,
    PressureUnit,
    ScientificOriginKind,
    TemperatureUnit,
)
from app.schemas.common import SchemaBase
from app.schemas.fragments.artifact import ArtifactIn
from app.schemas.fragments.identity import SpeciesEntryIdentityPayload
from app.schemas.fragments.refs import (
    SoftwareReleaseRef,
    WorkflowToolReleaseRef,
)
from app.schemas.reaction_family import find_canonical_reaction_family

# Re-exported for backwards compatibility — ArtifactIn now lives in
# app/schemas/fragments/artifact.py.
__all__ = ("ArtifactIn",)
from tckdb_schemas.enums import CalculationType as PayloadCalculationType
from tckdb_schemas.fragments.ts_validation_evidence import (
    TransitionStateValidationEvidenceIn,
    validate_ts_evidence_set,
)
from tckdb_schemas.local_key_codes import (
    W_CALCULATION_KEY_UNDECLARED,
    W_CONFORMER_KEY_UNDECLARED,
    W_GEOMETRY_KEY_UNRESOLVED,
    W_MICRO_REACTION_KEY_UNDECLARED,
    W_NETWORK_CHANNEL_KEY_UNDECLARED,
    W_NETWORK_STATE_KEY_UNDECLARED,
    W_SPECIES_KEY_UNDECLARED,
    W_TRANSITION_STATE_KEY_UNDECLARED,
    undeclared_key_error,
)
from tckdb_schemas.shared.calculation_in import (
    CalculationIn,
    GeometryIn,
    calculation_in_to_with_results_payload,
    freq_evidence,
    frequency_completeness_findings,
    transition_state_frequency_findings,
)
from tckdb_schemas.stationary_point import (
    StationaryPointFinding,
    evaluate_species_entry_frequency,
    raise_for_blocking_findings,
)
from tckdb_schemas.workflows.computed_species_upload import StatmechInBundle

from app.schemas.utils import normalize_optional_text, normalize_required_text
from app.schemas.workflows.literature_upload import LiteratureUploadRequest
from app.schemas.workflows.transport_upload import TransportUploadPayload

# ---------------------------------------------------------------------------
# Species
# ---------------------------------------------------------------------------


class ConformerIn(SchemaBase):
    """A conformer for a species, with its geometry and optimization calculation.

    :param key: Local key for this conformer.
    :param geometry: Geometry payload with a reusable key.
    :param calculation: The optimization calculation that produced this conformer.
        Must have ``type == "opt"``.
    :param scientific_origin: Scientific origin for the conformer observation.
    :param label: Optional user hint carried with the upload; basin dedupe still
        happens at the conformer-group layer.
    :param note: Optional note on the conformer observation.

    Each payload item creates one new ``conformer_observation`` row. Matching an
    existing basin reuses the ``conformer_group`` only.
    """

    key: str = Field(min_length=1)
    geometry: GeometryIn
    calculation: CalculationIn
    scientific_origin: ScientificOriginKind = ScientificOriginKind.computed
    label: str | None = None
    note: str | None = None

    @model_validator(mode="after")
    def normalize_text(self) -> Self:
        self.label = normalize_optional_text(self.label)
        self.note = normalize_optional_text(self.note)
        return self

    @model_validator(mode="after")
    def validate_primary_calc_is_opt(self) -> Self:
        if self.calculation.type != CalculationType.opt:
            raise ValueError(
                f"Conformer '{self.key}' primary calculation must be type 'opt', "
                f"got '{self.calculation.type.value}'."
            )
        return self


class NetworkSpeciesIn(SchemaBase):
    """A species defined within this network upload.

    :param key: Local key used to reference this species elsewhere in the payload.
    :param species_entry: Species-entry identity payload to resolve or create.
    :param label: Optional human-readable display label.
    :param conformers: Optional conformer uploads (geometry + opt calculation).
    :param calculations: Additional calculations on this species (sp, freq, etc.).
        Their ``geometry_key``, when given, must point to one of this
        species's conformer geometries -- it names the geometry the
        calculation ran on.

        Anchoring is ``conformer_key``'s job. This docstring used to
        attribute it to ``geometry_key``, matching a helper in
        ``app.workflows.network_pdep`` that dropped the anchor without a word
        whenever ``geometry_key`` was absent. Both said the same wrong thing,
        so neither contradicted the other. See
        ``app.services.conformer_anchoring``.
    :param statmech: Optional statistical-mechanics interpretation for this
        species (external symmetry, optical isomers, hindered rotors, etc.).
        Reuses the bundle's statmech payload; ``source_calculations`` reference
        calculation keys defined anywhere in this request.
    """

    key: str = Field(min_length=1)
    species_entry: SpeciesEntryIdentityPayload
    label: str | None = None
    conformers: list[ConformerIn] = Field(default_factory=list)
    calculations: list[CalculationIn] = Field(default_factory=list)
    transport: TransportUploadPayload | None = None
    statmech: StatmechInBundle | None = None

    @model_validator(mode="after")
    def normalize_text(self) -> Self:
        self.label = normalize_optional_text(self.label)
        return self

    @model_validator(mode="after")
    def validate_species_calc_geometry_key(self) -> Self:
        """Species-level non-opt calculations must specify geometry_key."""
        for calc in self.calculations:
            if calc.type != CalculationType.opt and calc.geometry_key is None:
                raise ValueError(
                    f"Species '{self.key}' calculation '{calc.key}' "
                    f"(type={calc.type.value}) requires geometry_key."
                )
        return self

    @model_validator(mode="after")
    def validate_species_calc_conformer_keys(self) -> Self:
        """Require a calculation's ``conformer_key`` to name one of this species's conformers.

        The same refusal, code and context as the computed-reaction bundle's
        ``validate_calc_conformer_keys``. Both routes resolve the anchor
        through one seam now, so they must also refuse the same mistake the
        same way -- the two copies of the anchoring helper diverging is the
        failure this whole change exists to close.
        """
        conformer_keys = {conf.key for conf in self.conformers}
        for calc in self.calculations:
            if calc.conformer_key is None:
                continue
            if calc.conformer_key not in conformer_keys:
                raise undeclared_key_error(
                    W_CONFORMER_KEY_UNDECLARED,
                    f"Species '{self.key}' calculation '{calc.key}' "
                    f"conformer_key must reference one of that species's "
                    f"own conformers.",
                    field=f"calculations['{calc.key}'].conformer_key",
                    key=calc.conformer_key,
                    declared=conformer_keys,
                )
        return self

    @model_validator(mode="after")
    def validate_species_calc_geometry_belongs_to_conformer(self) -> Self:
        """Require species-side calculations to reference one of this species's conformers."""
        conformer_geometry_keys = {conf.geometry.key for conf in self.conformers}
        for calc in self.calculations:
            if calc.geometry_key is None:
                continue
            if calc.geometry_key not in conformer_geometry_keys:
                raise undeclared_key_error(
                    W_GEOMETRY_KEY_UNRESOLVED,
                    f"Species '{self.key}' calculation '{calc.key}' geometry_key "
                    f"must reference one of that species's conformer geometries.",
                    field=f"calculations['{calc.key}'].geometry_key",
                    key=calc.geometry_key,
                    declared=conformer_geometry_keys,
                )
        return self

    def stationary_point_findings(self) -> list[StationaryPointFinding]:
        """Judge this well's declared kind against its own frequency evidence.

        A network well is a species entry like any other. Its transition
        states live in separate ``TransitionStateIn`` blocks and are
        judged there, which is why this sits on the species model rather
        than on the request: a request-level scan would count the TS's
        single imaginary mode against a well.
        """
        kind = self.species_entry.species_entry_kind
        xyz_by_geometry_key = {
            conformer.geometry.key: conformer.geometry.xyz_text
            for conformer in self.conformers
        }
        findings: list[StationaryPointFinding] = []
        for conformer in self.conformers:
            location = (
                f"species['{self.key}'].conformers['{conformer.key}']"
                f".calculation['{conformer.calculation.key}']"
            )
            n_imag, imag_freq_cm1 = freq_evidence(conformer.calculation)
            findings.extend(
                evaluate_species_entry_frequency(
                    kind, n_imag, imag_freq_cm1, location=location
                )
            )
            findings.extend(
                frequency_completeness_findings(
                    conformer.calculation,
                    location=f"{location}.freq_frequencies_cm1",
                    xyz_text=conformer.geometry.xyz_text,
                )
            )
        for calc in self.calculations:
            location = f"species['{self.key}'].calculations['{calc.key}']"
            n_imag, imag_freq_cm1 = freq_evidence(calc)
            findings.extend(
                evaluate_species_entry_frequency(
                    kind, n_imag, imag_freq_cm1, location=location
                )
            )
            findings.extend(
                frequency_completeness_findings(
                    calc,
                    location=f"{location}.freq_frequencies_cm1",
                    xyz_text=xyz_by_geometry_key.get(calc.geometry_key or ""),
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


# ---------------------------------------------------------------------------
# Transition states
# ---------------------------------------------------------------------------


class TransitionStateIn(SchemaBase):
    """A transition state for one micro reaction.

    :param key: Local key for this transition state.
    :param micro_reaction_key: Local key referencing a micro reaction.
    :param charge: Net charge of the TS structure.
    :param multiplicity: Spin multiplicity.
    :param geometry: Geometry of the saddle point (with a reusable key).
    :param calculation: The optimization calculation that produced this TS geometry.
    :param calculations: Additional calculations on this TS (freq, sp, irc).
    :param label: Optional human-readable label.
    :param note: Optional note.
    """

    key: str = Field(min_length=1)
    micro_reaction_key: str = Field(min_length=1)
    charge: int
    multiplicity: int = Field(ge=1)
    geometry: GeometryIn
    calculation: CalculationIn
    calculations: list[CalculationIn] = Field(default_factory=list)
    statmech: StatmechInBundle | None = None
    validation_evidence: list["TransitionStateValidationEvidenceIn"] = Field(
        default_factory=list,
        description=(
            "Structured IRC evidence. Optional but strongly recommended: a "
            "deposit without it succeeds and returns a "
            "'transition_state_missing_irc_evidence' upload warning. "
            "source_calculation_key resolves within this transition state's "
            "calculation namespace and must name an irc-type calculation."
        ),
    )
    label: str | None = None
    note: str | None = None

    @model_validator(mode="after")
    def normalize_text(self) -> Self:
        self.label = normalize_optional_text(self.label)
        self.note = normalize_optional_text(self.note)
        return self

    @model_validator(mode="after")
    def validate_primary_calc_is_opt(self) -> Self:
        if self.calculation.type != CalculationType.opt:
            raise ValueError(
                f"Transition state '{self.key}' primary calculation must be "
                f"type 'opt', got '{self.calculation.type.value}'."
            )
        return self

    def stationary_point_findings(self) -> list[StationaryPointFinding]:
        """Judge this saddle point against its own frequency evidence."""
        findings: list[StationaryPointFinding] = []
        for calc in (self.calculation, *self.calculations):
            location = (
                f"transition_states['{self.key}'].calculations['{calc.key}']"
            )
            findings.extend(
                transition_state_frequency_findings(calc, location=location)
            )
            findings.extend(
                frequency_completeness_findings(
                    calc,
                    location=f"{location}.freq_frequencies_cm1",
                    xyz_text=self.geometry.xyz_text,
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


# ---------------------------------------------------------------------------
# States
# ---------------------------------------------------------------------------


class NetworkStateParticipantIn(SchemaBase):
    """One species within a network state definition.

    :param species_key: Local key referencing a species in the ``species`` list.
    :param stoichiometry: Stoichiometric coefficient (defaults to 1).
    """

    species_key: str = Field(min_length=1)
    stoichiometry: int = Field(default=1, ge=1)


class NetworkStateIn(SchemaBase):
    """A macroscopic state in the network (well or bimolecular channel).

    :param key: Local key used to reference this state elsewhere in the payload.
    :param kind: State kind — ``well``, ``bimolecular``, or ``termolecular``.
    :param label: Optional human-readable display label.
    :param participants: Species composition of this state.
    """

    key: str = Field(min_length=1)
    kind: Literal["well", "bimolecular", "termolecular"]
    label: str | None = None
    participants: list[NetworkStateParticipantIn] = Field(min_length=1)

    @model_validator(mode="after")
    def normalize_text(self) -> Self:
        self.label = normalize_optional_text(self.label)
        return self

    @model_validator(mode="after")
    def validate_unique_participants(self) -> Self:
        keys = [p.species_key for p in self.participants]
        if len(set(keys)) != len(keys):
            raise ValueError(
                "State participants must reference distinct species_key values."
            )
        return self


# ---------------------------------------------------------------------------
# Micro reactions (elementary steps admitted into the ME model)
# ---------------------------------------------------------------------------


class MicroReactionParticipantUpload(SchemaBase):
    """An ordered participant in a micro reaction.

    :param species_key: Local key referencing a species in the ``species`` list.
    :param note: Optional note stored on the structured participant row.
    """

    species_key: str = Field(min_length=1)
    note: str | None = None

    @model_validator(mode="after")
    def normalize_text(self) -> Self:
        self.note = normalize_optional_text(self.note)
        return self


class NetworkMicroReactionIn(SchemaBase):
    """An elementary reaction step in the network (ME input).

    :param key: Local key for this micro reaction.
    :param reversible: Whether this elementary step is reversible.
    :param reaction_family: Optional reaction-family label.
    :param reaction_family_source_note: Required when ``reaction_family`` is non-canonical.
    :param reactants: Ordered reactant participants.
    :param products: Ordered product participants.
    :param label: Optional human-readable label.
    """

    key: str = Field(min_length=1)
    reversible: bool = True
    reaction_family: str | None = None
    reaction_family_source_note: str | None = None
    reactants: list[MicroReactionParticipantUpload] = Field(min_length=1)
    products: list[MicroReactionParticipantUpload] = Field(min_length=1)
    label: str | None = None

    @field_validator("reaction_family", "reaction_family_source_note")
    @classmethod
    def normalize_family_text(cls, value: str | None) -> str | None:
        return normalize_optional_text(value)

    @model_validator(mode="after")
    def normalize_text(self) -> Self:
        self.label = normalize_optional_text(self.label)
        return self

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
# Channels (phenomenological pathways — optional in upload, can be inferred)
# ---------------------------------------------------------------------------


class NetworkChannelIn(SchemaBase):
    """A directed phenomenological channel between two network states.

    :param source_state_key: Local key of the source state.
    :param sink_state_key: Local key of the sink state.
    :param kind: Macroscopic channel classification (association, ...).
    :param mechanism: Mechanistic attribution. Defaults to ``elementary``, so
        an existing payload that names its ``microreaction_paths`` is
        unaffected.
    :param microreaction_paths: The elementary step(s) this channel is
        attributed to.

    The two classification axes are orthogonal. ``kind`` says what the channel
    does macroscopically; ``mechanism`` says what evidence stands behind it:

    - ``elementary`` (the default) requires at least one ``microreaction_path``.
      Omitting the paths is an incomplete deposit, not a declaration, and is
      rejected.
    - ``well_skipping`` declares a chemically-activated channel whose flux
      passes *through* one or more energized wells before the products separate
      — ``NH2 + NH2 → H + N2H3`` proceeding via energized ``N2H4*`` is the
      canonical case. There is no single elementary step or saddle point to
      name, so ``microreaction_paths`` must be empty; the channel's backing is
      the network topology plus the master-equation solve. Declaring it is not
      taken on trust: ``NetworkPDepUploadRequest`` checks that the endpoints are
      *not* joined by a single elementary step and *are* joined by a chain of
      elementary steps whose intermediates are all wells.
    """

    key: str = Field(min_length=1)
    source_state_key: str = Field(min_length=1)
    sink_state_key: str = Field(min_length=1)
    kind: NetworkChannelKind
    mechanism: NetworkChannelMechanism = NetworkChannelMechanism.elementary
    microreaction_paths: list["NetworkChannelMicroReactionIn"] = Field(
        default_factory=list
    )

    @model_validator(mode="after")
    def validate_source_ne_sink(self) -> Self:
        if self.source_state_key == self.sink_state_key:
            raise ValueError("source_state_key and sink_state_key must differ.")
        return self

    @model_validator(mode="after")
    def validate_paths_match_mechanism(self) -> Self:
        """Path cardinality is fixed by the declared mechanism, never optional.

        This is what keeps the relaxation additive and non-silent: a channel
        that supplies no paths is only ever accepted when it has *said* it is
        well-skipping.
        """
        if self.mechanism == NetworkChannelMechanism.elementary:
            if not self.microreaction_paths:
                raise ValueError(
                    f"channel '{self.key}' supplies no microreaction_paths. An "
                    "elementary channel (the default) must name at least one "
                    "elementary step; a channel with no elementary step behind "
                    "it must declare mechanism='well_skipping' explicitly."
                )
        elif self.microreaction_paths:
            raise ValueError(
                f"channel '{self.key}' declares mechanism='well_skipping' but "
                "supplies microreaction_paths. A well-skipping channel has no "
                "single elementary step; if the endpoints are joined by one, "
                "declare mechanism='elementary' and name it."
            )
        return self


class NetworkChannelMicroReactionIn(SchemaBase):
    """One elementary reaction supporting a channel, and how it proceeds.

    :param micro_reaction_key: Local key of the elementary step.
    :param transition_state_key: Local key of the saddle point on this path.
        Omit it (or send ``null``) for a barrierless or variational path —
        radical-radical association and simple bond fission have no saddle
        point, and they are ubiquitous in multi-well PDep networks. A
        barrierless path carries no ``channel_barriers`` entry.
    """

    micro_reaction_key: str = Field(min_length=1)
    transition_state_key: str | None = Field(default=None, min_length=1)


# ---------------------------------------------------------------------------
# Solve block
# ---------------------------------------------------------------------------


class BathGasIn(SchemaBase):
    """Bath gas component for a network solve.

    :param species_key: Local key referencing a species in the ``species`` list.
    :param mole_fraction: Mole fraction of this bath gas component (0–1].
    """

    species_key: str = Field(min_length=1)
    mole_fraction: float = Field(gt=0, le=1)


class EnergyTransferIn(SchemaBase):
    """Energy transfer model parameters for a network solve.

    ``scope`` declares what the model was specified over. ⟨ΔE⟩down is a
    property of a (well, collider) pair, and ``per_well`` — the default —
    records it that way. But Arkane, RMG and MESS inputs routinely declare one
    single-exponential-down for the entire network, and demanding a per-pair
    entry for such a run would force the depositor to paste one number N
    times, fabricating specificity the calculation never had. ``network_wide``
    lets that be said honestly. See ADR 0009.

    :param scope: ``per_well`` (names a state and a collider) or
        ``network_wide`` (names neither, by declaration).
    :param state_key: Local key of the well this applies to. Required for
        ``per_well``, forbidden for ``network_wide``.
    :param collider_species_key: Local key of the collider species. Required
        for ``per_well``, forbidden for ``network_wide``.
    :param model: Energy transfer model name (e.g. ``single_exponential_down``).
    :param alpha0_cm_inv: Average downward energy transfer at reference temperature.
    :param t_exponent: Temperature exponent for the energy transfer model.
    :param t_ref_k: Reference temperature in K.
    :param note: Optional note.
    """

    scope: NetworkEnergyTransferScope = NetworkEnergyTransferScope.per_well
    state_key: str | None = None
    collider_species_key: str | None = None
    model: str = Field(min_length=1)
    alpha0_cm_inv: float = Field(gt=0)
    t_exponent: float | None = None
    t_ref_k: float | None = Field(default=None, gt=0)
    note: str | None = None

    @model_validator(mode="after")
    def normalize_text(self) -> Self:
        # `normalize_required_text`, not `normalize_optional_text`. `model`
        # is a required `str`, and the optional helper collapses a blank
        # string to None -- so a whitespace-only model name (which passes
        # `min_length=1`) left this field holding None on a non-optional
        # column-bound value. The required helper refuses the blank with a
        # 422 instead, which is what `min_length=1` was already promising.
        self.model = normalize_required_text(self.model)
        self.note = normalize_optional_text(self.note)
        return self

    @model_validator(mode="after")
    def validate_scope(self) -> Self:
        """The declared scope and the supplied keys must agree.

        This is a definitional check, not an expectation (ADR 0008): a
        ``network_wide`` entry that also names a well contradicts itself, and a
        ``per_well`` entry with no well names nothing. Neither can be produced
        by a correct calculation, so both block.
        """
        if self.scope == NetworkEnergyTransferScope.per_well:
            if self.state_key is None or self.collider_species_key is None:
                raise ValueError(
                    "a per_well energy_transfer entry requires an explicit "
                    "state_key and collider_species_key; to declare one model "
                    "for the whole network set scope='network_wide'."
                )
        elif self.state_key is not None or self.collider_species_key is not None:
            raise ValueError(
                "a network_wide energy_transfer entry must not name a "
                "state_key or a collider_species_key; it applies to every "
                "well and to the bath gas as a whole."
            )
        return self


class ConventionBlock(SchemaBase):
    """Shared declaration of the energy zero and the corrections applied.

    Both axes are machine tokens: free text made an energy unverifiable and
    the repo's own producers had already drifted onto incompatible spellings.
    ``other`` is the single escape hatch and always requires ``convention_note``.
    """

    energy_zero_convention: EnergyZeroConvention
    correction_convention: EnergyCorrectionConvention
    convention_note: str | None = None

    @model_validator(mode="after")
    def validate_other_requires_note(self) -> Self:
        self.convention_note = normalize_optional_text(self.convention_note)
        if (
            self.energy_zero_convention == EnergyZeroConvention.other
            or self.correction_convention == EnergyCorrectionConvention.other
        ) and self.convention_note is None:
            raise ValueError(
                "convention_note is required when an energy convention is 'other'."
            )
        return self


class StateEnergyIn(ConventionBlock):
    """One solve-state energy on a declared, reproducible energy zero."""

    state_key: str = Field(min_length=1)
    energy_kj_mol: float
    source_calculation_key: str | None = None

    @model_validator(mode="after")
    def validate_energy_is_finite(self) -> Self:
        if not math.isfinite(self.energy_kj_mol):
            raise ValueError("energy_kj_mol must be finite.")
        return self


class ChannelBarrierIn(ConventionBlock):
    """A solve barrier tied to one channel/reaction/TS path, never endpoints alone.

    ``forward``/``reverse`` are oriented by the *channel* (source → sink), not
    by the micro reaction's own written direction — the channel is the
    directed object this barrier is keyed on.

    Barriers are signed relative to ``energy_zero_convention``: a submerged
    entrance barrier sits *below* the entrance channel and is legitimately
    negative, so no positivity bound applies. Only non-finite values are
    rejected. A barrierless path has no barrier and must not appear here.
    """

    channel_key: str = Field(min_length=1)
    micro_reaction_key: str = Field(min_length=1)
    transition_state_key: str = Field(min_length=1)
    forward_barrier_kj_mol: float
    reverse_barrier_kj_mol: float
    source_calculation_key: str | None = None

    @model_validator(mode="after")
    def validate_barriers_are_finite(self) -> Self:
        if not math.isfinite(self.forward_barrier_kj_mol) or not math.isfinite(
            self.reverse_barrier_kj_mol
        ):
            raise ValueError("forward and reverse barriers must be finite.")
        return self


class SolveSourceCalculationIn(SchemaBase):
    """Links a calculation (by local key) to the solve with a specific role.

    :param calculation_key: Local key of a calculation defined elsewhere in the payload.
    :param role: The scientific role of this calculation in the ME solve.
    """

    calculation_key: str = Field(min_length=1)
    role: NetworkSolveCalculationRole


class ChebyshevKineticsIn(SchemaBase):
    """Chebyshev-polynomial fit of a phenomenological k(T,P).

    :param n_temperature: Number of temperature basis polynomials (rows).
    :param n_pressure: Number of pressure basis polynomials (columns).
    :param coefficients: 2D coefficient grid, ``n_temperature`` rows each of
        length ``n_pressure``. Persisted as ``{"coeffs": [[...], ...]}`` JSONB
        to match the network-kinetics read path.
    """

    n_temperature: int = Field(ge=1)
    n_pressure: int = Field(ge=1)
    coefficients: list[list[float]] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_grid_dimensions(self) -> Self:
        if len(self.coefficients) != self.n_temperature:
            raise ValueError(
                f"Chebyshev coefficients must have n_temperature="
                f"{self.n_temperature} rows, got {len(self.coefficients)}."
            )
        for i, row in enumerate(self.coefficients):
            if len(row) != self.n_pressure:
                raise ValueError(
                    f"Chebyshev coefficients row {i} must have n_pressure="
                    f"{self.n_pressure} columns, got {len(row)}."
                )
            for j, value in enumerate(row):
                if not math.isfinite(value):
                    raise ValueError(
                        f"Chebyshev coefficient at ({i}, {j}) must be finite, "
                        f"got {value!r}."
                    )
        return self


class PlogEntryIn(SchemaBase):
    """One PLOG entry: modified-Arrhenius parameters at a discrete pressure.

    Mirrors one ``network_kinetics_plog`` row. Multiple entries at the same
    pressure are distinguished by ``entry_index`` (duplicate-Arrhenius PLOG,
    as emitted by Arkane/Cantera when a single pressure carries two Arrhenius
    terms whose rates sum).

    :param pressure_bar: Discrete pressure of this entry in bar (> 0).
    :param a: Arrhenius pre-exponential factor.
    :param a_units: Units of ``a`` (dimensionality varies with molecularity;
        e.g. ``per_s`` for unimolecular, ``cm3_mol_s`` for bimolecular).
    :param n: Temperature exponent.
    :param ea_kj_mol: Activation energy in kJ/mol.
    :param entry_index: Discriminator for multiple Arrhenius terms sharing one
        pressure (defaults to 1).
    """

    pressure_bar: float = Field(gt=0)
    a: float
    a_units: ArrheniusAUnits | None = None
    n: float
    ea_kj_mol: float
    entry_index: int = Field(default=1, ge=1)


class PlogKineticsIn(SchemaBase):
    """Pressure-log (PLOG) interpolation of a phenomenological k(T,P).

    A set of pressure-indexed modified-Arrhenius entries. Persisted as one
    ``network_kinetics_plog`` row per entry under a shared ``network_kinetics``
    parent.

    :param entries: PLOG entries (at least one). No two entries may share the
        same ``(pressure_bar, entry_index)`` pair — that maps to the child
        table's composite primary key.
    """

    entries: list[PlogEntryIn] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_unique_pressure_index(self) -> Self:
        pairs = [(e.pressure_bar, e.entry_index) for e in self.entries]
        if len(set(pairs)) != len(pairs):
            raise ValueError(
                "PLOG entries must be unique by (pressure_bar, entry_index); "
                "duplicate pairs would collide on the composite primary key."
            )
        return self


class NetworkKineticsIn(SchemaBase):
    """Fitted phenomenological k(T,P) for one channel under this solve.

    Supports Chebyshev and PLOG fits. The channel is referenced by its
    ``(source_state_key, sink_state_key)`` state-key pair (channels carry no
    local key of their own).

    :param source_state_key: Local key of the referenced channel's source state.
    :param sink_state_key: Local key of the referenced channel's sink state.
    :param model_kind: Parameterization kind (``chebyshev`` or ``plog``).
    :param chebyshev: Chebyshev coefficients (required when
        ``model_kind == chebyshev``).
    :param plog: PLOG entries (required when ``model_kind == plog``).
    :param tmin_k: Minimum temperature of validity in K.
    :param tmax_k: Maximum temperature of validity in K.
    :param pmin_bar: Minimum pressure of validity in bar.
    :param pmax_bar: Maximum pressure of validity in bar.
    :param rate_units: Units of the fitted rate coefficient.
    :param pressure_units: Units the fit's pressure axis is expressed in.
    :param temperature_units: Units the fit's temperature axis is expressed in.
    :param stores_log10_k: Whether the coefficients fit ``log10(k)`` (Chebyshev
        convention) rather than ``k`` directly.
    :param note: Optional free-text note.
    """

    channel_key: str | None = Field(default=None, min_length=1)
    # Deprecated compatibility selector. It is resolved only when endpoints
    # identify exactly one channel; parallel paths must use channel_key.
    source_state_key: str | None = Field(default=None, min_length=1)
    sink_state_key: str | None = Field(default=None, min_length=1)
    model_kind: NetworkKineticsModelKind

    chebyshev: ChebyshevKineticsIn | None = None
    plog: PlogKineticsIn | None = None

    tmin_k: float | None = Field(default=None, gt=0)
    tmax_k: float | None = Field(default=None, gt=0)
    pmin_bar: float | None = Field(default=None, gt=0)
    pmax_bar: float | None = Field(default=None, gt=0)
    rate_units: ArrheniusAUnits | None = None
    pressure_units: PressureUnit | None = None
    temperature_units: TemperatureUnit | None = None
    stores_log10_k: bool | None = None
    note: str | None = None

    @model_validator(mode="after")
    def normalize_text(self) -> Self:
        self.note = normalize_optional_text(self.note)
        return self

    @model_validator(mode="after")
    def validate_source_ne_sink(self) -> Self:
        if self.channel_key is None and (self.source_state_key is None or self.sink_state_key is None):
            raise ValueError("channel_key is required (or both legacy source_state_key and sink_state_key).")
        if (self.source_state_key is None) != (self.sink_state_key is None):
            raise ValueError("legacy source_state_key and sink_state_key must be supplied together.")
        if self.source_state_key == self.sink_state_key and self.source_state_key is not None:
            raise ValueError("source_state_key and sink_state_key must differ.")
        return self

    @model_validator(mode="after")
    def validate_model_payload(self) -> Self:
        """Chebyshev and PLOG are supported; tabulated is not yet.

        Exactly one model sub-block must be present, matching ``model_kind``.
        The tabulated (``network_kinetics_point``) write path is still
        unimplemented; reject it explicitly.
        """
        if self.model_kind == NetworkKineticsModelKind.chebyshev:
            if self.chebyshev is None:
                raise ValueError(
                    "chebyshev coefficients are required when "
                    "model_kind == 'chebyshev'."
                )
            if self.plog is not None:
                raise ValueError(
                    "plog must be omitted when model_kind == 'chebyshev'."
                )
            # A Chebyshev surface is fit in REDUCED variables: the T and P
            # axes are mapped onto [-1, 1] using the fit's own bounds. Without
            # all four bounds the polynomial cannot be evaluated at any (T, P)
            # at all, so the coefficients are unusable. The standalone kinetics
            # path already enforces this in ``ChebyshevUpload.validate_grid``;
            # the network path accepted a surface that could never be read
            # back as a rate.
            missing_bounds = [
                name
                for name, value in (
                    ("tmin_k", self.tmin_k),
                    ("tmax_k", self.tmax_k),
                    ("pmin_bar", self.pmin_bar),
                    ("pmax_bar", self.pmax_bar),
                )
                if value is None
            ]
            if missing_bounds:
                raise ValueError(
                    "Chebyshev network kinetics requires finite T and P bounds "
                    "to map onto the fit's reduced variables; missing: "
                    f"{missing_bounds}."
                )
        elif self.model_kind == NetworkKineticsModelKind.plog:
            if self.plog is None:
                raise ValueError(
                    "plog entries are required when model_kind == 'plog'."
                )
            if self.chebyshev is not None:
                raise ValueError(
                    "chebyshev must be omitted when model_kind == 'plog'."
                )
            # stores_log10_k is a Chebyshev-only concept: PLOG stores a real
            # Arrhenius A, not a log10 fit. Reject it rather than persist a
            # semantically meaningless flag on the parent row.
            if self.stores_log10_k is not None:
                raise ValueError(
                    "stores_log10_k must be omitted when model_kind == 'plog' "
                    "(it is a Chebyshev-only concept)."
                )
        else:
            raise ValueError(
                "Tabulated network kinetics upload not yet supported "
                "(supported: chebyshev, plog)."
            )
        return self

    @model_validator(mode="after")
    def validate_ranges(self) -> Self:
        if (
            self.tmin_k is not None
            and self.tmax_k is not None
            and self.tmin_k > self.tmax_k
        ):
            raise ValueError("tmin_k must be less than or equal to tmax_k.")
        if (
            self.pmin_bar is not None
            and self.pmax_bar is not None
            and self.pmin_bar > self.pmax_bar
        ):
            raise ValueError("pmin_bar must be less than or equal to pmax_bar.")
        return self


class NetworkSolveIn(SchemaBase):
    """Master-equation solve configuration and provenance.

    :param me_method: ME solution method.
    :param interpolation_model: Interpolation model for the ME solution.
    :param tmin_k: Minimum temperature in K for the ME solve.
    :param tmax_k: Maximum temperature in K for the ME solve.
    :param pmin_bar: Minimum pressure in bar for the ME solve.
    :param pmax_bar: Maximum pressure in bar for the ME solve.
    :param grain_size_cm_inv: Energy grain size in cm⁻¹.
    :param grain_count: Number of energy grains.
    :param emax_kj_mol: Maximum energy in kJ/mol for the ME solve.
    :param kind: Whether the master equation was solved here (``computed``,
        the default and preferred form) or the k(T,P) were transcribed from a
        publication (``reported``). A ``reported`` solve is not held to the
        state-energy, channel-barrier or energy-transfer coverage rules — it
        holds none of those inputs — and must supply ``literature`` instead.
        See ADR 0010.
    :param literature: Optional literature submission payload. Required when
        ``kind='reported'``.
    :param software_release: Optional software provenance reference.
    :param workflow_tool_release: Optional workflow-tool provenance reference.
    :param bath_gas: Bath gas composition.
    :param energy_transfer: Energy transfer model parameters — either one
        ``per_well`` entry per (well, bath collider) pair, or a single
        ``network_wide`` entry.
    :param source_calculations: Calculations used in this solve, by local key and role.
    :param channel_kinetics: Fitted phenomenological k(T,P) for channels, each
        referencing its channel by ``(source_state_key, sink_state_key)``.
    :param note: Optional free-text note.
    """

    kind: NetworkSolveKind = NetworkSolveKind.computed

    me_method: str | None = None
    interpolation_model: str | None = None

    tmin_k: float = Field(gt=0)
    tmax_k: float = Field(gt=0)
    pmin_bar: float = Field(gt=0)
    pmax_bar: float = Field(gt=0)

    grain_size_cm_inv: float | None = None
    grain_count: int | None = Field(default=None, ge=1)
    emax_kj_mol: float | None = None

    literature: LiteratureUploadRequest | None = None
    software_release: SoftwareReleaseRef | None = None
    workflow_tool_release: WorkflowToolReleaseRef | None = None

    bath_gas: list[BathGasIn] = Field(default_factory=list)
    # Coverage of these two is enforced exactly by
    # ``validate_mechanistic_channel_evidence`` against the wells and the
    # saddle-point paths actually declared. A blanket ``min_length=1`` would
    # instead forbid the legitimate all-barrierless network.
    energy_transfer: list[EnergyTransferIn] = Field(default_factory=list)
    # ``state_energies`` and ``source_calculations`` are master-equation inputs
    # and stay mandatory for a computed solve — enforced in
    # ``validate_kind_requirements`` below rather than by ``min_length``, which
    # cannot see ``kind``. A reported solve holds neither, by construction.
    state_energies: list[StateEnergyIn] = Field(default_factory=list)
    channel_barriers: list[ChannelBarrierIn] = Field(default_factory=list)
    source_calculations: list[SolveSourceCalculationIn] = Field(default_factory=list)
    channel_kinetics: list[NetworkKineticsIn] = Field(default_factory=list)
    note: str | None = None

    @model_validator(mode="after")
    def normalize_text(self) -> Self:
        self.me_method = normalize_optional_text(self.me_method)
        self.interpolation_model = normalize_optional_text(self.interpolation_model)
        self.note = normalize_optional_text(self.note)
        return self

    @model_validator(mode="after")
    def validate_ranges(self) -> Self:
        if self.tmin_k > self.tmax_k:
            raise ValueError("tmin_k must be less than or equal to tmax_k.")
        if self.pmin_bar > self.pmax_bar:
            raise ValueError("pmin_bar must be less than or equal to pmax_bar.")
        return self

    @model_validator(mode="after")
    def validate_unique_bath_gas(self) -> Self:
        keys = [bg.species_key for bg in self.bath_gas]
        if len(set(keys)) != len(keys):
            raise ValueError("Bath gas entries must reference distinct species_key values.")
        return self

    @model_validator(mode="after")
    def validate_kind_requirements(self) -> Self:
        """Hold a computed solve to the master-equation inputs it must have.

        These are the requirements ``min_length`` used to carry. They move
        here because they apply to one member of ``kind`` and not the other: a
        computed solve is a claim that the master equation was solved in this
        database, and a solve with no state energies and no source
        calculations does not support it. A reported solve makes no such
        claim, so the same absence is the expected shape rather than a defect
        (ADR 0010).

        What a reported solve *must* have is the mirror image: the publication
        it was transcribed from, and at least one k(T,P) — a record that
        reports nothing contradicts its own kind. Both are definitional, so
        both block under ADR 0008. The completeness limitation that *is* an
        expectation rather than a definition — that nobody can re-derive these
        rates — warns instead, in
        ``collect_network_solve_kind_warnings``.
        """
        if self.kind is NetworkSolveKind.computed:
            if not self.state_energies:
                raise ValueError(
                    "a computed solve must supply state_energies: solving the "
                    "master equation requires an energy for every network "
                    "state. If these rates were transcribed from a "
                    "publication rather than solved here, declare "
                    "kind='reported'."
                )
            if not self.source_calculations:
                raise ValueError(
                    "a computed solve must supply source_calculations naming "
                    "the calculations it was built from. If these rates were "
                    "transcribed from a publication rather than solved here, "
                    "declare kind='reported'."
                )
            return self

        if self.literature is None:
            raise ValueError(
                "a reported solve must supply literature: it is credited to a "
                "publication, and without one it would assert rates carrying "
                "neither a derivation nor a source."
            )
        if not self.channel_kinetics:
            raise ValueError(
                "a reported solve must supply channel_kinetics. Reporting "
                "k(T,P) transcribed from a publication is the only thing it "
                "is for; with none, nothing has been reported."
            )
        return self

    @model_validator(mode="after")
    def validate_bath_composition_and_state_energies(self) -> Self:
        # A reported solve need not name a bath gas — a paper often gives
        # k(T,P) without stating the collider composition it was fitted in.
        # If it does name one, the composition still has to be a composition.
        if self.bath_gas or self.kind is NetworkSolveKind.computed:
            if not math.isclose(sum(bg.mole_fraction for bg in self.bath_gas), 1.0, rel_tol=0.0, abs_tol=1e-9):
                raise ValueError("bath_gas mole fractions must sum to 1.0 within 1e-9.")
        keys = [item.state_key for item in self.state_energies]
        if len(keys) != len(set(keys)):
            raise ValueError("state_energies must be unique by state_key.")
        declared_scopes = {item.scope for item in self.energy_transfer}
        if len(declared_scopes) > 1:
            # Half a network described per-well and half described globally is
            # genuinely ambiguous: nothing says which wells the global entry
            # was meant to cover, or whether it overrides the specific ones.
            raise ValueError(
                "energy_transfer entries must all share one scope: either "
                "every entry is per_well, or there is a single network_wide "
                "entry. Mixing the two leaves it undefined which wells the "
                "network-wide model applies to."
            )
        # Checked ahead of the tuple-uniqueness rule below, which would
        # otherwise catch two network-wide entries with a message about keys
        # that a network-wide entry does not have.
        if NetworkEnergyTransferScope.network_wide in declared_scopes and len(self.energy_transfer) > 1:
            raise ValueError(
                "a network_wide energy_transfer declaration is a single "
                "entry; supplying more than one says nothing about which "
                "applies where."
            )
        scopes = [(item.scope, item.state_key, item.collider_species_key) for item in self.energy_transfer]
        if len(scopes) != len(set(scopes)):
            raise ValueError("energy_transfer entries must be unique by (scope, state_key, collider_species_key).")
        return self


# ---------------------------------------------------------------------------
# Top-level upload schema
# ---------------------------------------------------------------------------


def _collect_all_calculation_keys(request: "NetworkPDepUploadRequest") -> list[str]:
    """Gather every calculation key from across the payload."""
    keys: list[str] = []
    for sp in request.species:
        for conf in sp.conformers:
            keys.append(conf.calculation.key)
        for calc in sp.calculations:
            keys.append(calc.key)
    for ts in request.transition_states:
        keys.append(ts.calculation.key)
        for calc in ts.calculations:
            keys.append(calc.key)
    return keys


def _collect_all_geometry_keys(request: "NetworkPDepUploadRequest") -> list[str]:
    """Gather every geometry key from across the payload."""
    keys: list[str] = []
    for sp in request.species:
        for conf in sp.conformers:
            keys.append(conf.geometry.key)
    for ts in request.transition_states:
        keys.append(ts.geometry.key)
    return keys


class NetworkPDepUploadRequest(SchemaBase):
    """Unified upload payload for a pressure-dependent reaction network.

    A single request contains species (with conformers and calculations),
    transition states (with geometries and calculations), micro reactions,
    network topology (states and channels), and an optional master-equation
    solve configuration.

    Key uniqueness: calculation and geometry keys are globally unique across
    the entire request. Species, state, reaction, and TS keys are unique
    within their own collections (different namespaces).

    This schema expects one connected network — disconnected subnetworks
    are rejected when channels are explicitly provided.
    """

    name: str | None = None
    description: str | None = None

    literature: LiteratureUploadRequest | None = None
    software_release: SoftwareReleaseRef | None = None
    workflow_tool_release: WorkflowToolReleaseRef | None = None

    species: list[NetworkSpeciesIn] = Field(min_length=1)
    transition_states: list[TransitionStateIn] = Field(default_factory=list)
    micro_reactions: list[NetworkMicroReactionIn] = Field(default_factory=list)
    states: list[NetworkStateIn] = Field(min_length=1)
    channels: list[NetworkChannelIn] = Field(default_factory=list)
    solve: NetworkSolveIn | None = None

    def stationary_point_findings(self) -> list[StationaryPointFinding]:
        """Collect every entity's stationary-point findings in this network.

        Each well judges its own frequency evidence against its own
        declared kind, and each transition state judges its own, so a
        saddle point's single imaginary mode is never counted against a
        well. The blocking half already fired from the nested models'
        validators; the route layer calls this to harvest the warnings.
        """
        findings: list[StationaryPointFinding] = []
        for species in self.species:
            findings.extend(species.stationary_point_findings())
        for ts in self.transition_states:
            findings.extend(ts.stationary_point_findings())
        return findings

    @model_validator(mode="after")
    def normalize_text(self) -> Self:
        self.name = normalize_optional_text(self.name)
        self.description = normalize_optional_text(self.description)
        return self

    @model_validator(mode="after")
    def validate_unique_keys(self) -> Self:
        """Ensure all local keys are unique within their respective lists,
        and that calculation/geometry keys are globally unique."""
        species_keys = [s.key for s in self.species]
        if len(set(species_keys)) != len(species_keys):
            raise ValueError("Species keys must be unique.")

        state_keys = [s.key for s in self.states]
        if len(set(state_keys)) != len(state_keys):
            raise ValueError("State keys must be unique.")

        reaction_keys = [r.key for r in self.micro_reactions]
        if len(set(reaction_keys)) != len(reaction_keys):
            raise ValueError("Micro reaction keys must be unique.")

        ts_keys = [t.key for t in self.transition_states]
        if len(set(ts_keys)) != len(ts_keys):
            raise ValueError("Transition state keys must be unique.")

        # Calculation keys must be globally unique
        calc_keys = _collect_all_calculation_keys(self)
        if len(set(calc_keys)) != len(calc_keys):
            dupes = [k for k in calc_keys if calc_keys.count(k) > 1]
            raise ValueError(
                f"Calculation keys must be globally unique. "
                f"Duplicates: {sorted(set(dupes))}."
            )

        # Geometry keys must be globally unique
        geom_keys = _collect_all_geometry_keys(self)
        if len(set(geom_keys)) != len(geom_keys):
            dupes = [k for k in geom_keys if geom_keys.count(k) > 1]
            raise ValueError(
                f"Geometry keys must be globally unique. "
                f"Duplicates: {sorted(set(dupes))}."
            )

        return self

    @model_validator(mode="after")
    def validate_key_references(self) -> Self:
        """Ensure all cross-references point to defined keys."""
        species_keys = {s.key for s in self.species}
        state_keys = {s.key for s in self.states}
        reaction_keys = {r.key for r in self.micro_reactions}
        geometry_keys = set(_collect_all_geometry_keys(self))
        calculation_keys = set(_collect_all_calculation_keys(self))

        # State participants must reference defined species
        for state_index, state in enumerate(self.states):
            for p_index, p in enumerate(state.participants):
                if p.species_key not in species_keys:
                    raise undeclared_key_error(
                        W_SPECIES_KEY_UNDECLARED,
                        f"State '{state.key}' references undefined species_key "
                        f"'{p.species_key}'.",
                        field=(
                            f"states[{state_index}].participants[{p_index}]."
                            f"species_key"
                        ),
                        key=p.species_key,
                        declared=species_keys,
                    )

        # Channels must reference defined states
        for ch_index, ch in enumerate(self.channels):
            if ch.source_state_key not in state_keys:
                raise undeclared_key_error(
                    W_NETWORK_STATE_KEY_UNDECLARED,
                    f"Channel references undefined source_state_key "
                    f"'{ch.source_state_key}'.",
                    field=f"channels[{ch_index}].source_state_key",
                    key=ch.source_state_key,
                    declared=state_keys,
                )
            if ch.sink_state_key not in state_keys:
                raise undeclared_key_error(
                    W_NETWORK_STATE_KEY_UNDECLARED,
                    f"Channel references undefined sink_state_key "
                    f"'{ch.sink_state_key}'.",
                    field=f"channels[{ch_index}].sink_state_key",
                    key=ch.sink_state_key,
                    declared=state_keys,
                )

        # Micro reaction participants must reference defined species.
        # ``reactants`` and ``products`` are walked separately rather than
        # concatenated, so ``context['field']`` names a location that
        # exists in the depositor's file -- and the same one the workflow
        # seam would have named.
        for rxn_index, rxn in enumerate(self.micro_reactions):
            for side in ("reactants", "products"):
                for rp_index, rp in enumerate(getattr(rxn, side)):
                    if rp.species_key not in species_keys:
                        raise undeclared_key_error(
                            W_SPECIES_KEY_UNDECLARED,
                            f"Micro reaction '{rxn.key}' references undefined "
                            f"species_key '{rp.species_key}'.",
                            field=(
                                f"micro_reactions[{rxn_index}].{side}"
                                f"[{rp_index}].species_key"
                            ),
                            key=rp.species_key,
                            declared=species_keys,
                        )

        # TS must reference defined micro reactions
        for ts_index, ts in enumerate(self.transition_states):
            if ts.micro_reaction_key not in reaction_keys:
                raise undeclared_key_error(
                    W_MICRO_REACTION_KEY_UNDECLARED,
                    f"Transition state '{ts.key}' references undefined "
                    f"micro_reaction_key '{ts.micro_reaction_key}'.",
                    field=f"transition_states[{ts_index}].micro_reaction_key",
                    key=ts.micro_reaction_key,
                    declared=reaction_keys,
                )
            # IRC evidence is optional but recommended; a TS deposited without
            # it succeeds and the workflow emits an upload warning. What is
            # never accepted is *incomplete* evidence dressed up as passing.
            ts_calculation_types = {
                ts.calculation.key: ts.calculation.type,
                **{calculation.key: calculation.type for calculation in ts.calculations},
            }
            for ev_index, evidence in enumerate(ts.validation_evidence):
                # This payload HAS a calculation-key namespace, so evidence
                # must name the irc calculation it came from. A *missing*
                # key is a different refusal from a *wrong* one -- there is
                # nothing to list alternatives against -- so it keeps the
                # generic code.
                if evidence.source_calculation_key is None:
                    raise ValueError(
                        f"Transition state '{ts.key}' validation evidence requires "
                        f"source_calculation_key naming its irc calculation."
                    )
                calculation_type = ts_calculation_types.get(evidence.source_calculation_key)
                if calculation_type is None:
                    raise undeclared_key_error(
                        W_CALCULATION_KEY_UNDECLARED,
                        f"Transition state '{ts.key}' validation evidence references "
                        f"undefined calculation_key '{evidence.source_calculation_key}'.",
                        field=(
                            f"transition_states['{ts.key}']."
                            f"validation_evidence[{ev_index}]."
                            f"source_calculation_key"
                        ),
                        key=evidence.source_calculation_key,
                        declared=ts_calculation_types,
                    )
                if calculation_type != CalculationType.irc:
                    raise ValueError(
                        f"Transition state '{ts.key}' irc validation evidence "
                        f"requires an irc calculation."
                    )
            micro_reaction = next(item for item in self.micro_reactions if item.key == ts.micro_reaction_key)
            # Kinds rather than counts: a participant that legitimately has no
            # atoms maps to an empty list, and only the declared kind says
            # which participant that is. Here the participants are species
            # keys, so the kind is read off the species they name; an undefined
            # key was already reported above.
            species_by_key = {species.key: species for species in self.species}
            validate_ts_evidence_set(
                ts.validation_evidence,
                subject_label=ts.key,
                xyz_text=ts.geometry.xyz_text,
                reactant_kinds=[
                    species_by_key[rp.species_key].species_entry.molecule_kind
                    for rp in micro_reaction.reactants
                ],
                product_kinds=[
                    species_by_key[rp.species_key].species_entry.molecule_kind
                    for rp in micro_reaction.products
                ],
            )

        # Calculation geometry_key references must point to defined geometries
        all_calcs: list[tuple[str, CalculationIn]] = []
        for sp in self.species:
            for calc in sp.calculations:
                all_calcs.append((f"species '{sp.key}'", calc))
            for conf in sp.conformers:
                all_calcs.append((f"conformer '{conf.key}'", conf.calculation))
        for ts in self.transition_states:
            all_calcs.append((f"TS '{ts.key}'", ts.calculation))
            for calc in ts.calculations:
                all_calcs.append((f"TS '{ts.key}'", calc))

        for context, calc in all_calcs:
            if calc.geometry_key is not None and calc.geometry_key not in geometry_keys:
                raise undeclared_key_error(
                    W_GEOMETRY_KEY_UNRESOLVED,
                    f"Calculation '{calc.key}' in {context} references "
                    f"undefined geometry_key '{calc.geometry_key}'.",
                    field=f"calculations['{calc.key}'].geometry_key",
                    key=calc.geometry_key,
                    declared=geometry_keys,
                )

        # Species statmech references must resolve against that species's OWN
        # calculations only. A species statmech can only be sourced from that
        # species's calculations (the persistence seam enforces species-entry
        # ownership at runtime); scoping here turns what would otherwise be a
        # persist-time KeyError/ownership error into a clean 422.
        for sp in self.species:
            if sp.statmech is None:
                continue
            # Annotated with the *payload* enum, not the ORM one. `calc.type`
            # comes off a wire-package `CalculationIn`, so it is a
            # `tckdb_schemas.enums.CalculationType`; this module's bare
            # `CalculationType` is `app.db.models.common.CalculationType`.
            # The two are distinct classes with identical members, and every
            # comparison between them happens to work only because both are
            # `str` subclasses -- so the mismatch was invisible until mypy
            # could see the wire package's types at all.
            own_calc_types: dict[str, PayloadCalculationType] = {}
            for conf in sp.conformers:
                own_calc_types[conf.calculation.key] = conf.calculation.type
            for calc in sp.calculations:
                own_calc_types[calc.key] = calc.type

            for sc_index, sc in enumerate(sp.statmech.source_calculations):
                if sc.calculation_key not in own_calc_types:
                    # The namespace here is deliberately *narrower* than the
                    # seam's (this species's own calculations, not the whole
                    # network), which is why ``declared_keys`` differs
                    # between the layers even though the code does not. The
                    # narrower list is the more useful one: it is what would
                    # actually have worked.
                    raise undeclared_key_error(
                        W_CALCULATION_KEY_UNDECLARED,
                        f"Species '{sp.key}' statmech.source_calculations "
                        f"references calculation_key '{sc.calculation_key}', "
                        f"which is not one of that species's own calculations.",
                        field=(
                            f"statmech.source_calculations[{sc_index}]."
                            f"calculation_key"
                        ),
                        key=sc.calculation_key,
                        declared=own_calc_types,
                    )

            for i, t in enumerate(sp.statmech.torsions):
                scan_key = t.source_scan_calculation_key
                if scan_key is None:
                    continue
                if scan_key not in own_calc_types:
                    raise undeclared_key_error(
                        W_CALCULATION_KEY_UNDECLARED,
                        f"Species '{sp.key}' statmech.torsions[{i}]."
                        f"source_scan_calculation_key '{scan_key}' is not one "
                        f"of that species's own calculations.",
                        field=(
                            f"statmech.torsions[{i}]."
                            f"source_scan_calculation_key"
                        ),
                        key=scan_key,
                        declared=own_calc_types,
                    )
                if own_calc_types[scan_key] != CalculationType.scan:
                    raise ValueError(
                        f"Species '{sp.key}' statmech.torsions[{i}]."
                        f"source_scan_calculation_key '{scan_key}' must "
                        f"reference a scan-type calculation."
                    )

        # Bath gas species must reference defined species
        if self.solve:
            for bg_index, bg in enumerate(self.solve.bath_gas):
                if bg.species_key not in species_keys:
                    raise undeclared_key_error(
                        W_SPECIES_KEY_UNDECLARED,
                        f"Bath gas references undefined species_key "
                        f"'{bg.species_key}'.",
                        field=f"solve.bath_gas[{bg_index}].species_key",
                        key=bg.species_key,
                        declared=species_keys,
                    )

            # Solve source calculations must reference defined calculation keys.
            # Named `solve_sc`, not `sc`: the statmech loop earlier in this
            # same function binds `sc` to a `StatmechSourceCalcIn`, so reusing
            # the name gave the solve entry the statmech entry's static type.
            for sc_index, solve_sc in enumerate(self.solve.source_calculations):
                if solve_sc.calculation_key not in calculation_keys:
                    raise undeclared_key_error(
                        W_CALCULATION_KEY_UNDECLARED,
                        f"Solve source_calculations references undefined "
                        f"calculation_key '{solve_sc.calculation_key}'.",
                        field=(
                            f"solve.source_calculations[{sc_index}]."
                            f"calculation_key"
                        ),
                        key=solve_sc.calculation_key,
                        declared=calculation_keys,
                    )

            # Channel kinetics must reference defined states and a defined
            # channel (source, sink) pair.
            channel_keys = {ch.key for ch in self.channels}
            for nk_index, nk in enumerate(self.solve.channel_kinetics):
                if nk.channel_key is None:
                    matches = [ch.key for ch in self.channels if ch.source_state_key == nk.source_state_key and ch.sink_state_key == nk.sink_state_key]
                    # No key was written at all, so there is nothing to
                    # report as the offending name; the legacy endpoint pair
                    # either matched nothing or matched ambiguously. A
                    # different refusal, and it keeps the generic code.
                    if len(matches) != 1:
                        raise ValueError("channel_kinetics references undefined channel or ambiguous legacy endpoints; provide channel_key.")
                    nk.channel_key = matches[0]
                if nk.channel_key not in channel_keys:
                    raise undeclared_key_error(
                        W_NETWORK_CHANNEL_KEY_UNDECLARED,
                        f"channel_kinetics references undefined channel_key "
                        f"'{nk.channel_key}'.",
                        field=f"solve.channel_kinetics[{nk_index}].channel_key",
                        key=nk.channel_key,
                        declared=channel_keys,
                    )

        return self

    @model_validator(mode="after")
    def validate_unique_channels(self) -> Self:
        """Ensure channel identities are stable while allowing parallel paths."""
        keys = [ch.key for ch in self.channels]
        if len(set(keys)) != len(keys):
            raise ValueError("Channels must be unique by key.")
        return self

    @model_validator(mode="after")
    def validate_unique_channel_kinetics(self) -> Self:
        """Ensure no duplicate channel_kinetics within one payload.

        Uniqueness is keyed by ``(source_state_key, sink_state_key,
        model_kind)``: one channel may legitimately carry *both* a Chebyshev
        and a PLOG parameterization of the same k(T,P) (multiple
        parameterizations of one network coexisting on the same channel — the
        model imposes no ``(channel, solve)`` uniqueness). What remains user
        error, and is rejected here, is two entries of the *same* model_kind on
        one channel (two Chebyshevs or two PLOGs), which would silently write
        two redundant ``NetworkKinetics`` rows for one (channel, solve, kind).
        Multiple rows per (channel, solve) across separate uploads remain
        legitimate under append-only semantics.
        """
        if self.solve is None:
            return self
        triples = [
            (nk.channel_key, nk.model_kind)
            for nk in self.solve.channel_kinetics
        ]
        if len(set(triples)) != len(triples):
            raise ValueError(
                "channel_kinetics entries must be unique by "
                "(channel_key, model_kind) within one "
                "payload; a channel may carry at most one entry per model_kind "
                "(one chebyshev and/or one plog)."
            )
        return self

    @model_validator(mode="after")
    def validate_mechanistic_channel_evidence(self) -> Self:
        reaction_keys = {item.key for item in self.micro_reactions}
        ts_by_key = {item.key: item.micro_reaction_key for item in self.transition_states}
        for ch_index, channel in enumerate(self.channels):
            path_keys = [(path.micro_reaction_key, path.transition_state_key) for path in channel.microreaction_paths]
            if len(path_keys) != len(set(path_keys)):
                raise ValueError(f"channel '{channel.key}' microreaction_paths must be unique by reaction and TS.")
            for path_index, (reaction_key, ts_key) in enumerate(path_keys):
                if reaction_key not in reaction_keys:
                    raise undeclared_key_error(
                        W_MICRO_REACTION_KEY_UNDECLARED,
                        f"channel '{channel.key}' references undefined micro_reaction_key '{reaction_key}'.",
                        field=(
                            f"channels[{ch_index}].microreaction_paths"
                            f"[{path_index}].micro_reaction_key"
                        ),
                        key=reaction_key,
                        declared=reaction_keys,
                    )
                if ts_key is None:
                    # Barrierless / variational path: no saddle point to check.
                    continue
                if ts_key not in ts_by_key:
                    raise undeclared_key_error(
                        W_TRANSITION_STATE_KEY_UNDECLARED,
                        f"channel '{channel.key}' references undefined transition_state_key '{ts_key}'.",
                        field=(
                            f"channels[{ch_index}].microreaction_paths"
                            f"[{path_index}].transition_state_key"
                        ),
                        key=ts_key,
                        declared=ts_by_key,
                    )
                # Both keys are declared; they just do not belong together.
                # A different repair, so the generic code stays.
                if ts_by_key[ts_key] != reaction_key:
                    raise ValueError(f"channel '{channel.key}' TS '{ts_key}' does not belong to micro reaction '{reaction_key}'.")
        state_keys = {state.key for state in self.states}
        calc_keys = set(_collect_all_calculation_keys(self))
        species_keys = {sp.key for sp in self.species}

        if self.solve is not None:
            # Referential integrity of whatever the payload *did* supply. This
            # applies to both kinds: relaxed means not required, never
            # unvalidated. A reported solve that volunteers a barrier still
            # has to point it at a real path.
            for energy_index, energy in enumerate(self.solve.state_energies):
                if energy.source_calculation_key is not None and energy.source_calculation_key not in calc_keys:
                    raise undeclared_key_error(
                        W_CALCULATION_KEY_UNDECLARED,
                        "state_energies references undefined source_calculation_key.",
                        field=(
                            f"solve.state_energies[{energy_index}]."
                            f"source_calculation_key"
                        ),
                        key=energy.source_calculation_key,
                        declared=calc_keys,
                    )
            for et_index, et in enumerate(self.solve.energy_transfer):
                if et.scope != NetworkEnergyTransferScope.per_well:
                    continue
                # ``NetworkEnergyTransferIn`` already refuses a per-well
                # entry missing either key, and a nested model's validators
                # run before this one, so both are set by the time the keys
                # are read as names.
                assert et.state_key is not None
                assert et.collider_species_key is not None
                if et.state_key not in state_keys:
                    raise undeclared_key_error(
                        W_NETWORK_STATE_KEY_UNDECLARED,
                        "energy_transfer references undefined state_key.",
                        field=f"solve.energy_transfer[{et_index}].state_key",
                        key=et.state_key,
                        declared=state_keys,
                    )
                if et.collider_species_key not in species_keys:
                    raise undeclared_key_error(
                        W_SPECIES_KEY_UNDECLARED,
                        "energy_transfer references undefined collider_species_key.",
                        field=(
                            f"solve.energy_transfer[{et_index}]."
                            f"collider_species_key"
                        ),
                        key=et.collider_species_key,
                        declared=species_keys,
                    )
            for barrier_index, barrier in enumerate(self.solve.channel_barriers):
                if barrier.source_calculation_key is not None and barrier.source_calculation_key not in calc_keys:
                    raise undeclared_key_error(
                        W_CALCULATION_KEY_UNDECLARED,
                        "channel_barriers references undefined source_calculation_key.",
                        field=(
                            f"solve.channel_barriers[{barrier_index}]."
                            f"source_calculation_key"
                        ),
                        key=barrier.source_calculation_key,
                        declared=calc_keys,
                    )

        # The three coverage rules below check master-equation *inputs*
        # against the network topology: an energy for every state, a ⟨ΔE⟩down
        # for every well, a barrier for every saddle-point path. Nothing about
        # them is relaxed for a solve run in this database. They are simply
        # not applicable to one whose k(T,P) were transcribed from a
        # publication: the depositor holds none of those inputs and the paper
        # usually never published them, so demanding them refused the deposit
        # outright instead of recording a weaker but honest record (ADR 0010).
        #
        # These rules are the *full* contract and this is the only place that
        # holds it. Migration ``f9b2e6c4a1d7`` adds a deferred constraint
        # trigger that refuses a computed solve carrying *zero* rows of an
        # applicable evidence class, which is belt and braces for write paths
        # that never reach this validator — not a replacement for it. The
        # trigger guarantees existence; the coverage below is guaranteed
        # nowhere else, so do not thin it out on the strength of it.
        if self.solve is not None and self.solve.kind is NetworkSolveKind.computed:
            if {item.state_key for item in self.solve.state_energies} != state_keys:
                raise ValueError("state_energies must provide exactly one energy for every network state.")
            # Collisional energy transfer is a property of a (well, collider)
            # pair, so a per-well declaration is held to that: a five-well
            # network with an argon bath needs five ⟨ΔE⟩down entries, not one.
            # Bimolecular/termolecular states are reservoirs in the master
            # equation and carry no collisional stabilisation term, so only
            # wells are required.
            #
            # A ``network_wide`` declaration is exempt by construction: it says
            # the producer determined one model for the entire network, which
            # is what an Arkane or MESS input usually contains. Demanding the
            # cross product there would only get one number pasted N times.
            # The completeness limitation is reported as an UploadWarning
            # instead (ADR 0008, ADR 0009), never refused.
            network_wide = any(
                item.scope == NetworkEnergyTransferScope.network_wide
                for item in self.solve.energy_transfer
            )
            if not network_wide:
                expected_transfer_scopes = {
                    (state.key, bath.species_key)
                    for state in self.states
                    if state.kind == "well"
                    for bath in self.solve.bath_gas
                }
                supplied_transfer_scopes = {
                    (item.state_key, item.collider_species_key)
                    for item in self.solve.energy_transfer
                }
                if supplied_transfer_scopes != expected_transfer_scopes:
                    missing = sorted(expected_transfer_scopes - supplied_transfer_scopes)
                    extra = sorted(supplied_transfer_scopes - expected_transfer_scopes)
                    raise ValueError(
                        "energy_transfer must cover exactly one entry per (well state, "
                        "bath-gas collider) pair, or declare a single "
                        "scope='network_wide' entry if the run specified one "
                        "model for the whole network. "
                        f"Missing: {missing}. Unexpected: {extra}."
                    )
            # Every saddle-point path needs a barrier; a barrierless path has
            # none, and offering one would be a fabricated number.
            expected_paths = {
                (channel.key, path.micro_reaction_key, path.transition_state_key)
                for channel in self.channels for path in channel.microreaction_paths
                if path.transition_state_key is not None
            }
            supplied_paths = {(barrier.channel_key, barrier.micro_reaction_key, barrier.transition_state_key) for barrier in self.solve.channel_barriers}
            if supplied_paths != expected_paths:
                raise ValueError(
                    "channel_barriers must provide exactly one barrier for every "
                    "saddle-point channel path, and none for a barrierless path."
                )
        return self

    @model_validator(mode="after")
    def validate_well_skipping_channels(self) -> Self:
        """A well-skipping declaration must be supported by the topology.

        The scientific content of ``mechanism='well_skipping'`` is a claim
        about the network: the endpoints are *not* directly connected by an
        elementary step, and the flux reaches the sink by traversing one or
        more energized wells. Both halves are checkable against the
        ``micro_reactions`` and ``states`` already in this payload, so the
        declaration is verified rather than trusted — the backend still
        manufactures no evidence, it only reads the evidence the producer
        supplied.

        Bimolecular and termolecular configurations are reservoirs in the
        master equation: flux that reaches one has separated into products.
        Only a *well* can be an energized intermediate, so intermediates on the
        path are required to be wells.
        """
        well_skipping = [
            channel
            for channel in self.channels
            if channel.mechanism == NetworkChannelMechanism.well_skipping
        ]
        if not well_skipping:
            return self

        state_kind = {state.key: state.kind for state in self.states}
        composition_to_state: dict[frozenset[tuple[str, int]], str] = {
            frozenset(
                (p.species_key, p.stoichiometry) for p in state.participants
            ): state.key
            for state in self.states
        }

        def _state_for(participants: list[MicroReactionParticipantUpload]) -> str | None:
            counts: dict[str, int] = {}
            for participant in participants:
                counts[participant.species_key] = (
                    counts.get(participant.species_key, 0) + 1
                )
            return composition_to_state.get(frozenset(counts.items()))

        # Undirected adjacency of the *elementary* step graph over states. A
        # micro reaction whose sides do not both name a declared state is not
        # an edge of the macroscopic network.
        elementary_edges: set[frozenset[str]] = set()
        for reaction in self.micro_reactions:
            reactant_state = _state_for(reaction.reactants)
            product_state = _state_for(reaction.products)
            if (
                reactant_state is None
                or product_state is None
                or reactant_state == product_state
            ):
                continue
            elementary_edges.add(frozenset((reactant_state, product_state)))

        neighbours: dict[str, set[str]] = {state.key: set() for state in self.states}
        for edge in elementary_edges:
            left, right = tuple(edge)
            neighbours[left].add(right)
            neighbours[right].add(left)

        for channel in well_skipping:
            source = channel.source_state_key
            sink = channel.sink_state_key
            if source not in state_kind or sink not in state_kind:
                # Undefined endpoints are reported by validate_key_references;
                # do not mask that with a topology error.
                continue
            if frozenset((source, sink)) in elementary_edges:
                raise ValueError(
                    f"channel '{channel.key}' declares mechanism='well_skipping' "
                    f"but '{source}' and '{sink}' are directly connected by an "
                    "elementary micro reaction. Declare mechanism='elementary' "
                    "and name that step."
                )
            # Breadth-first search that may only pass *through* wells; the
            # source and the sink themselves may be of any kind.
            seen = {source}
            frontier = [source]
            reached = False
            while frontier and not reached:
                nxt: list[str] = []
                for node in frontier:
                    for neighbour in neighbours[node]:
                        if neighbour == sink:
                            reached = True
                            break
                        if neighbour in seen or state_kind[neighbour] != "well":
                            continue
                        seen.add(neighbour)
                        nxt.append(neighbour)
                    if reached:
                        break
                frontier = nxt
            if not reached:
                raise ValueError(
                    f"channel '{channel.key}' declares mechanism='well_skipping' "
                    f"but no chain of elementary micro reactions runs from "
                    f"'{source}' to '{sink}' through well states. A "
                    "chemically-activated channel is backed by the network "
                    "topology; without it there is no evidence for this "
                    "channel at all."
                )
        return self

    @model_validator(mode="after")
    def validate_no_unused_species(self) -> Self:
        """Reject if a species is defined but never referenced anywhere."""
        species_keys = {s.key for s in self.species}
        used: set[str] = set()

        for state in self.states:
            for p in state.participants:
                used.add(p.species_key)
        for rxn in self.micro_reactions:
            for rp in rxn.reactants + rxn.products:
                used.add(rp.species_key)
        if self.solve:
            for bg in self.solve.bath_gas:
                used.add(bg.species_key)

        unused = species_keys - used
        if unused:
            raise ValueError(
                f"Species defined but never referenced: {sorted(unused)}. "
                "Remove them or reference them in states, micro_reactions, "
                "or bath_gas."
            )
        return self

    @model_validator(mode="after")
    def validate_states_connected(self) -> Self:
        """Ensure all states form one connected component via channels.

        A PDep network must be a single connected component — disconnected
        subnetworks are not supported. If channels are provided, checks
        that every state is reachable from every other state through the
        channel graph. If no channels exist, skips this check (channels
        may be inferred later by the backend).
        """
        if len(self.states) <= 1 or not self.channels:
            return self

        state_keys = {s.key for s in self.states}
        adjacency: dict[str, set[str]] = {k: set() for k in state_keys}
        for ch in self.channels:
            adjacency[ch.source_state_key].add(ch.sink_state_key)
            adjacency[ch.sink_state_key].add(ch.source_state_key)

        visited: set[str] = set()
        queue = [next(iter(state_keys))]
        while queue:
            current = queue.pop()
            if current in visited:
                continue
            visited.add(current)
            queue.extend(adjacency[current] - visited)

        disconnected = state_keys - visited
        if disconnected:
            raise ValueError(
                f"States not connected to the rest of the network via "
                f"channels: {sorted(disconnected)}. All states must be "
                f"reachable through the channel graph."
            )
        return self

    # NOTE: there is deliberately NO "one TS per micro reaction" rule.
    # ``network_channel_microreaction``'s identity is
    # ``(channel, reaction_entry, transition_state_entry)`` precisely so that
    # one elementary step may proceed through several distinct saddle points
    # (e.g. syn/anti conformers of one elimination TS). Forbidding that here
    # pushed producers into declaring duplicate micro reactions with identical
    # stoichiometry, which manufactures duplicate rows in an identity table.
