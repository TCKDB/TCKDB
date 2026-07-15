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
    NetworkChannelKind,
    NetworkKineticsModelKind,
    NetworkSolveCalculationRole,
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
from tckdb_schemas.shared.calculation_in import (
    CalculationIn,
    GeometryIn,
    calculation_in_to_with_results_payload,
)
from tckdb_schemas.workflows.computed_species_upload import StatmechInBundle

from app.schemas.utils import normalize_optional_text
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
        Their ``geometry_key`` must point to one of this species's conformer
        geometries so the backend can anchor each calculation to the correct
        conformer observation.
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
    def validate_species_calc_geometry_belongs_to_conformer(self) -> Self:
        """Require species-side calculations to reference one of this species's conformers."""
        conformer_geometry_keys = {conf.geometry.key for conf in self.conformers}
        for calc in self.calculations:
            if calc.geometry_key is None:
                continue
            if calc.geometry_key not in conformer_geometry_keys:
                raise ValueError(
                    f"Species '{self.key}' calculation '{calc.key}' geometry_key "
                    f"must reference one of that species's conformer geometries."
                )
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
    :param kind: Channel classification.
    """

    source_state_key: str = Field(min_length=1)
    sink_state_key: str = Field(min_length=1)
    kind: NetworkChannelKind

    @model_validator(mode="after")
    def validate_source_ne_sink(self) -> Self:
        if self.source_state_key == self.sink_state_key:
            raise ValueError("source_state_key and sink_state_key must differ.")
        return self


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

    :param model: Energy transfer model name (e.g. ``single_exponential_down``).
    :param alpha0_cm_inv: Average downward energy transfer at reference temperature.
    :param t_exponent: Temperature exponent for the energy transfer model.
    :param t_ref_k: Reference temperature in K.
    :param note: Optional note.
    """

    model: str | None = None
    alpha0_cm_inv: float | None = None
    t_exponent: float | None = None
    t_ref_k: float | None = Field(default=None, gt=0)
    note: str | None = None

    @model_validator(mode="after")
    def normalize_text(self) -> Self:
        self.model = normalize_optional_text(self.model)
        self.note = normalize_optional_text(self.note)
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

    source_state_key: str = Field(min_length=1)
    sink_state_key: str = Field(min_length=1)
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
        if self.source_state_key == self.sink_state_key:
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
    :param literature: Optional literature submission payload.
    :param software_release: Optional software provenance reference.
    :param workflow_tool_release: Optional workflow-tool provenance reference.
    :param bath_gas: Bath gas composition.
    :param energy_transfer: Energy transfer model parameters.
    :param source_calculations: Calculations used in this solve, by local key and role.
    :param channel_kinetics: Fitted phenomenological k(T,P) for channels, each
        referencing its channel by ``(source_state_key, sink_state_key)``.
    :param note: Optional free-text note.
    """

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
    energy_transfer: EnergyTransferIn | None = None
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
        for state in self.states:
            for p in state.participants:
                if p.species_key not in species_keys:
                    raise ValueError(
                        f"State '{state.key}' references undefined species_key "
                        f"'{p.species_key}'."
                    )

        # Channels must reference defined states
        for ch in self.channels:
            if ch.source_state_key not in state_keys:
                raise ValueError(
                    f"Channel references undefined source_state_key "
                    f"'{ch.source_state_key}'."
                )
            if ch.sink_state_key not in state_keys:
                raise ValueError(
                    f"Channel references undefined sink_state_key "
                    f"'{ch.sink_state_key}'."
                )

        # Micro reaction participants must reference defined species
        for rxn in self.micro_reactions:
            for rp in rxn.reactants + rxn.products:
                if rp.species_key not in species_keys:
                    raise ValueError(
                        f"Micro reaction '{rxn.key}' references undefined "
                        f"species_key '{rp.species_key}'."
                    )

        # TS must reference defined micro reactions
        for ts in self.transition_states:
            if ts.micro_reaction_key not in reaction_keys:
                raise ValueError(
                    f"Transition state '{ts.key}' references undefined "
                    f"micro_reaction_key '{ts.micro_reaction_key}'."
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
                raise ValueError(
                    f"Calculation '{calc.key}' in {context} references "
                    f"undefined geometry_key '{calc.geometry_key}'."
                )

        # Species statmech references must resolve against that species's OWN
        # calculations only. A species statmech can only be sourced from that
        # species's calculations (the persistence seam enforces species-entry
        # ownership at runtime); scoping here turns what would otherwise be a
        # persist-time KeyError/ownership error into a clean 422.
        for sp in self.species:
            if sp.statmech is None:
                continue
            own_calc_types: dict[str, CalculationType] = {}
            for conf in sp.conformers:
                own_calc_types[conf.calculation.key] = conf.calculation.type
            for calc in sp.calculations:
                own_calc_types[calc.key] = calc.type

            for sc in sp.statmech.source_calculations:
                if sc.calculation_key not in own_calc_types:
                    raise ValueError(
                        f"Species '{sp.key}' statmech.source_calculations "
                        f"references calculation_key '{sc.calculation_key}', "
                        f"which is not one of that species's own calculations."
                    )

            for i, t in enumerate(sp.statmech.torsions):
                scan_key = t.source_scan_calculation_key
                if scan_key is None:
                    continue
                if scan_key not in own_calc_types:
                    raise ValueError(
                        f"Species '{sp.key}' statmech.torsions[{i}]."
                        f"source_scan_calculation_key '{scan_key}' is not one "
                        f"of that species's own calculations."
                    )
                if own_calc_types[scan_key] != CalculationType.scan:
                    raise ValueError(
                        f"Species '{sp.key}' statmech.torsions[{i}]."
                        f"source_scan_calculation_key '{scan_key}' must "
                        f"reference a scan-type calculation."
                    )

        # Bath gas species must reference defined species
        if self.solve:
            for bg in self.solve.bath_gas:
                if bg.species_key not in species_keys:
                    raise ValueError(
                        f"Bath gas references undefined species_key "
                        f"'{bg.species_key}'."
                    )

            # Solve source calculations must reference defined calculation keys
            for sc in self.solve.source_calculations:
                if sc.calculation_key not in calculation_keys:
                    raise ValueError(
                        f"Solve source_calculations references undefined "
                        f"calculation_key '{sc.calculation_key}'."
                    )

            # Channel kinetics must reference defined states and a defined
            # channel (source, sink) pair.
            channel_pairs = {
                (ch.source_state_key, ch.sink_state_key) for ch in self.channels
            }
            for nk in self.solve.channel_kinetics:
                if nk.source_state_key not in state_keys:
                    raise ValueError(
                        f"channel_kinetics references undefined source_state_key "
                        f"'{nk.source_state_key}'."
                    )
                if nk.sink_state_key not in state_keys:
                    raise ValueError(
                        f"channel_kinetics references undefined sink_state_key "
                        f"'{nk.sink_state_key}'."
                    )
                if (nk.source_state_key, nk.sink_state_key) not in channel_pairs:
                    raise ValueError(
                        f"channel_kinetics references undefined channel "
                        f"({nk.source_state_key} -> {nk.sink_state_key}); "
                        f"no matching entry in 'channels'."
                    )

        return self

    @model_validator(mode="after")
    def validate_unique_channels(self) -> Self:
        """Ensure no duplicate (source, sink) channel pairs."""
        pairs = [
            (ch.source_state_key, ch.sink_state_key) for ch in self.channels
        ]
        if len(set(pairs)) != len(pairs):
            raise ValueError("Channels must be unique by (source_state_key, sink_state_key).")
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
            (nk.source_state_key, nk.sink_state_key, nk.model_kind)
            for nk in self.solve.channel_kinetics
        ]
        if len(set(triples)) != len(triples):
            raise ValueError(
                "channel_kinetics entries must be unique by "
                "(source_state_key, sink_state_key, model_kind) within one "
                "payload; a channel may carry at most one entry per model_kind "
                "(one chebyshev and/or one plog)."
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

    @model_validator(mode="after")
    def validate_one_ts_per_reaction(self) -> Self:
        """MVP: at most one transition state per micro reaction."""
        seen_rxn_keys: set[str] = set()
        for ts in self.transition_states:
            if ts.micro_reaction_key in seen_rxn_keys:
                raise ValueError(
                    f"Multiple transition states reference micro_reaction_key "
                    f"'{ts.micro_reaction_key}'. MVP supports one TS per reaction."
                )
            seen_rxn_keys.add(ts.micro_reaction_key)
        return self
