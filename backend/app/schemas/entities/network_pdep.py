"""Entity schemas for pressure-dependent network models.

Covers: NetworkState, NetworkChannel, NetworkSolve (with bath gas,
energy transfer, source-calculation links), and NetworkKinetics
(with Chebyshev, PLOG, and tabulated-point parameterizations).

Note: ``composition_hash`` is a derived canonicalization field computed
by the service layer.  It appears only in Read schemas, never in
Create or Update.
"""

from typing import Self

from pydantic import BaseModel, Field, model_validator

from app.db.models.common import (
    ArrheniusAUnits,
    NetworkChannelKind,
    NetworkKineticsModelKind,
    NetworkSolveCalculationRole,
    NetworkStateKind,
    PressureUnit,
    TemperatureUnit,
)
from app.schemas.common import (
    ORMBaseSchema,
    SchemaBase,
    TimestampedCreatedByReadSchema,
    TimestampedReadSchema,
)


# ---------------------------------------------------------------------------
# Network state participants
# ---------------------------------------------------------------------------


class NetworkStateParticipantBase(BaseModel):
    """Shared fields for a species within a network state.

    :param species_entry_id: Referenced species-entry row.
    :param stoichiometry: Stoichiometric coefficient (defaults to 1).
    """

    species_entry_id: int
    stoichiometry: int = Field(default=1, ge=1)


class NetworkStateParticipantCreate(NetworkStateParticipantBase, SchemaBase):
    """Nested create payload for a network-state participant."""


class NetworkStateParticipantUpdate(SchemaBase):
    """Patch schema for a network-state participant."""

    stoichiometry: int | None = Field(default=None, ge=1)


class NetworkStateParticipantRead(NetworkStateParticipantBase, ORMBaseSchema):
    """Read schema for a network-state participant."""

    state_id: int


# ---------------------------------------------------------------------------
# Network state
# ---------------------------------------------------------------------------


class NetworkStateBase(BaseModel):
    """Shared scalar fields for a network state.

    :param network_id: Owning network id.
    :param kind: State kind (well, bimolecular, termolecular).
    :param label: Optional human-readable label.
    """

    network_id: int
    kind: NetworkStateKind
    label: str | None = None


class NetworkStateCreate(NetworkStateBase, SchemaBase):
    """Create schema for a network state.

    ``composition_hash`` is computed by the service layer from the
    normalized sorted participants — it is not part of the create payload.
    """

    participants: list[NetworkStateParticipantCreate] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_unique_participants(self) -> Self:
        ids = [p.species_entry_id for p in self.participants]
        if len(set(ids)) != len(ids):
            raise ValueError(
                "State participants must be unique by species_entry_id."
            )
        return self


class NetworkStateUpdate(SchemaBase):
    """Patch schema for a network state."""

    kind: NetworkStateKind | None = None
    label: str | None = None


class NetworkStateRead(NetworkStateBase, ORMBaseSchema):
    """Read schema for a network state."""

    id: int
    composition_hash: str
    participants: list[NetworkStateParticipantRead] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Network channel
# ---------------------------------------------------------------------------


class NetworkChannelBase(BaseModel):
    """Shared fields for a directed phenomenological channel.

    :param network_id: Owning network id.
    :param source_state_id: Source network-state id.
    :param sink_state_id: Sink network-state id.
    :param kind: Channel classification.
    """

    network_id: int
    source_state_id: int
    sink_state_id: int
    kind: NetworkChannelKind

    @model_validator(mode="after")
    def validate_source_ne_sink(self) -> Self:
        if self.source_state_id == self.sink_state_id:
            raise ValueError("source_state_id and sink_state_id must differ.")
        return self


class NetworkChannelCreate(NetworkChannelBase, SchemaBase):
    """Create schema for a network channel."""


class NetworkChannelUpdate(SchemaBase):
    """Patch schema for a network channel."""

    kind: NetworkChannelKind | None = None


class NetworkChannelRead(NetworkChannelBase, ORMBaseSchema):
    """Read schema for a network channel."""

    id: int


# ---------------------------------------------------------------------------
# Network solve — bath gas
# ---------------------------------------------------------------------------


class NetworkSolveBathGasBase(BaseModel):
    """Shared fields for a bath gas component.

    :param species_entry_id: Referenced species-entry row.
    :param mole_fraction: Mole fraction (0, 1].
    """

    species_entry_id: int
    mole_fraction: float = Field(gt=0, le=1)


class NetworkSolveBathGasCreate(NetworkSolveBathGasBase, SchemaBase):
    """Nested create payload for a bath gas component."""


class NetworkSolveBathGasUpdate(SchemaBase):
    """Patch schema for a bath gas component."""

    mole_fraction: float | None = Field(default=None, gt=0, le=1)


class NetworkSolveBathGasRead(NetworkSolveBathGasBase, ORMBaseSchema):
    """Read schema for a bath gas component."""

    solve_id: int


# ---------------------------------------------------------------------------
# Network solve — energy transfer
# ---------------------------------------------------------------------------


class NetworkSolveEnergyTransferBase(BaseModel):
    """Shared fields for energy transfer model parameters.

    :param model: Energy transfer model name.
    :param alpha0_cm_inv: Average downward energy transfer at reference T.
    :param t_exponent: Temperature exponent.
    :param t_ref_k: Reference temperature in K.
    :param note: Optional note.
    """

    model: str | None = None
    alpha0_cm_inv: float | None = None
    t_exponent: float | None = None
    t_ref_k: float | None = Field(default=None, gt=0)
    note: str | None = None


class NetworkSolveEnergyTransferCreate(NetworkSolveEnergyTransferBase, SchemaBase):
    """Nested create payload for energy transfer parameters."""


class NetworkSolveEnergyTransferUpdate(SchemaBase):
    """Patch schema for energy transfer parameters."""

    model: str | None = None
    alpha0_cm_inv: float | None = None
    t_exponent: float | None = None
    t_ref_k: float | None = Field(default=None, gt=0)
    note: str | None = None


class NetworkSolveEnergyTransferRead(NetworkSolveEnergyTransferBase, ORMBaseSchema):
    """Read schema for energy transfer parameters."""

    id: int
    solve_id: int


# ---------------------------------------------------------------------------
# Network solve — source calculation link
# ---------------------------------------------------------------------------


class NetworkSolveSourceCalculationBase(BaseModel):
    """Shared fields for a solve → calculation link.

    :param calculation_id: Referenced calculation row.
    :param role: Scientific role of this calculation in the ME solve.
    """

    calculation_id: int
    role: NetworkSolveCalculationRole


class NetworkSolveSourceCalculationCreate(
    NetworkSolveSourceCalculationBase, SchemaBase
):
    """Nested create payload for a solve source-calculation link."""


class NetworkSolveSourceCalculationUpdate(SchemaBase):
    """Patch schema for a solve source-calculation link."""

    role: NetworkSolveCalculationRole | None = None


class NetworkSolveSourceCalculationRead(
    NetworkSolveSourceCalculationBase, ORMBaseSchema
):
    """Read schema for a solve source-calculation link."""

    solve_id: int


# ---------------------------------------------------------------------------
# Network solve
# ---------------------------------------------------------------------------


class NetworkSolveBase(BaseModel):
    """Shared scalar fields for a master-equation solve.

    :param network_id: Owning network id.
    :param literature_id: Optional linked literature row.
    :param software_release_id: Optional software provenance.
    :param workflow_tool_release_id: Optional workflow provenance.
    :param me_method: ME solution method.
    :param interpolation_model: Interpolation model.
    :param grain_size_cm_inv: Energy grain size in cm^-1.
    :param grain_count: Number of energy grains.
    :param emax_kj_mol: Maximum energy in kJ/mol.
    :param tmin_k: Minimum temperature in K.
    :param tmax_k: Maximum temperature in K.
    :param pmin_bar: Minimum pressure in bar.
    :param pmax_bar: Maximum pressure in bar.
    :param note: Optional free-text note.
    """

    network_id: int

    literature_id: int | None = None
    software_release_id: int | None = None
    workflow_tool_release_id: int | None = None

    me_method: str | None = None
    interpolation_model: str | None = None

    grain_size_cm_inv: float | None = None
    grain_count: int | None = Field(default=None, ge=1)
    emax_kj_mol: float | None = None

    tmin_k: float | None = Field(default=None, gt=0)
    tmax_k: float | None = Field(default=None, gt=0)
    pmin_bar: float | None = Field(default=None, gt=0)
    pmax_bar: float | None = Field(default=None, gt=0)

    note: str | None = None

    @model_validator(mode="after")
    def validate_temperature_range(self) -> Self:
        if (
            self.tmin_k is not None
            and self.tmax_k is not None
            and self.tmin_k > self.tmax_k
        ):
            raise ValueError("tmin_k must be less than or equal to tmax_k.")
        return self

    @model_validator(mode="after")
    def validate_pressure_range(self) -> Self:
        if (
            self.pmin_bar is not None
            and self.pmax_bar is not None
            and self.pmin_bar > self.pmax_bar
        ):
            raise ValueError("pmin_bar must be less than or equal to pmax_bar.")
        return self


class NetworkSolveCreate(NetworkSolveBase, SchemaBase):
    """Create schema for a master-equation solve.

    Nested creation is supported for bath gases, energy transfer
    parameters, and source-calculation links.
    """

    bath_gases: list[NetworkSolveBathGasCreate] = Field(default_factory=list)
    energy_transfers: list[NetworkSolveEnergyTransferCreate] = Field(
        default_factory=list
    )
    source_calculations: list[NetworkSolveSourceCalculationCreate] = Field(
        default_factory=list
    )

    @model_validator(mode="after")
    def validate_unique_bath_gases(self) -> Self:
        ids = [bg.species_entry_id for bg in self.bath_gases]
        if len(set(ids)) != len(ids):
            raise ValueError(
                "Bath gas entries must be unique by species_entry_id."
            )
        return self

    @model_validator(mode="after")
    def validate_unique_source_calculations(self) -> Self:
        keys = [
            (sc.calculation_id, sc.role) for sc in self.source_calculations
        ]
        if len(set(keys)) != len(keys):
            raise ValueError(
                "Source calculations must be unique by (calculation_id, role)."
            )
        return self


class NetworkSolveUpdate(SchemaBase):
    """Patch schema for a master-equation solve."""

    literature_id: int | None = None
    software_release_id: int | None = None
    workflow_tool_release_id: int | None = None

    me_method: str | None = None
    interpolation_model: str | None = None

    grain_size_cm_inv: float | None = None
    grain_count: int | None = Field(default=None, ge=1)
    emax_kj_mol: float | None = None

    tmin_k: float | None = Field(default=None, gt=0)
    tmax_k: float | None = Field(default=None, gt=0)
    pmin_bar: float | None = Field(default=None, gt=0)
    pmax_bar: float | None = Field(default=None, gt=0)

    note: str | None = None

    @model_validator(mode="after")
    def validate_temperature_range(self) -> Self:
        if (
            self.tmin_k is not None
            and self.tmax_k is not None
            and self.tmin_k > self.tmax_k
        ):
            raise ValueError("tmin_k must be less than or equal to tmax_k.")
        return self

    @model_validator(mode="after")
    def validate_pressure_range(self) -> Self:
        if (
            self.pmin_bar is not None
            and self.pmax_bar is not None
            and self.pmin_bar > self.pmax_bar
        ):
            raise ValueError("pmin_bar must be less than or equal to pmax_bar.")
        return self


class NetworkSolveRead(NetworkSolveBase, TimestampedCreatedByReadSchema):
    """Read schema for a master-equation solve."""

    bath_gases: list[NetworkSolveBathGasRead] = Field(default_factory=list)
    energy_transfers: list[NetworkSolveEnergyTransferRead] = Field(
        default_factory=list
    )
    source_calculations: list[NetworkSolveSourceCalculationRead] = Field(
        default_factory=list
    )


# ---------------------------------------------------------------------------
# Network kinetics — Chebyshev parameterization
# ---------------------------------------------------------------------------


class NetworkKineticsChebyshevBase(BaseModel):
    """Shared fields for Chebyshev polynomial coefficients.

    :param n_temperature: Number of temperature basis functions.
    :param n_pressure: Number of pressure basis functions.
    :param coefficients: Chebyshev coefficient matrix (JSONB).
    """

    n_temperature: int = Field(ge=1)
    n_pressure: int = Field(ge=1)
    coefficients: dict


class NetworkKineticsChebyshevCreate(NetworkKineticsChebyshevBase, SchemaBase):
    """Nested create payload for Chebyshev coefficients."""


class NetworkKineticsChebyshevUpdate(SchemaBase):
    """Patch schema for Chebyshev coefficients."""

    n_temperature: int | None = Field(default=None, ge=1)
    n_pressure: int | None = Field(default=None, ge=1)
    coefficients: dict | None = None


class NetworkKineticsChebyshevRead(NetworkKineticsChebyshevBase, ORMBaseSchema):
    """Read schema for Chebyshev coefficients."""

    network_kinetics_id: int


# ---------------------------------------------------------------------------
# Network kinetics — PLOG entries
# ---------------------------------------------------------------------------


class NetworkKineticsPlogBase(BaseModel):
    """Shared fields for a PLOG entry (Arrhenius at a discrete pressure).

    :param pressure_bar: Pressure in bar.
    :param entry_index: Index for duplicate pressures (defaults to 1).
    :param a: Pre-exponential factor.
    :param a_units: Units for the pre-exponential factor.
    :param n: Temperature exponent.
    :param ea_kj_mol: Activation energy in kJ/mol.
    """

    pressure_bar: float = Field(gt=0)
    entry_index: int = Field(default=1, ge=1)
    a: float
    a_units: ArrheniusAUnits | None = None
    n: float
    ea_kj_mol: float


class NetworkKineticsPlogCreate(NetworkKineticsPlogBase, SchemaBase):
    """Nested create payload for a PLOG entry."""


class NetworkKineticsPlogUpdate(SchemaBase):
    """Patch schema for a PLOG entry."""

    a: float | None = None
    a_units: ArrheniusAUnits | None = None
    n: float | None = None
    ea_kj_mol: float | None = None


class NetworkKineticsPlogRead(NetworkKineticsPlogBase, ORMBaseSchema):
    """Read schema for a PLOG entry."""

    network_kinetics_id: int


# ---------------------------------------------------------------------------
# Network kinetics — tabulated data points
# ---------------------------------------------------------------------------


class NetworkKineticsPointBase(BaseModel):
    """Shared fields for a tabulated k(T,P) data point.

    :param temperature_k: Temperature in K.
    :param pressure_bar: Pressure in bar.
    :param rate_value: Rate constant value.
    """

    temperature_k: float = Field(gt=0)
    pressure_bar: float = Field(gt=0)
    rate_value: float


class NetworkKineticsPointCreate(NetworkKineticsPointBase, SchemaBase):
    """Nested create payload for a tabulated data point."""


class NetworkKineticsPointUpdate(SchemaBase):
    """Patch schema for a tabulated data point."""

    rate_value: float | None = None


class NetworkKineticsPointRead(NetworkKineticsPointBase, ORMBaseSchema):
    """Read schema for a tabulated data point."""

    network_kinetics_id: int


# ---------------------------------------------------------------------------
# Network kinetics
# ---------------------------------------------------------------------------


class NetworkKineticsBase(BaseModel):
    """Shared scalar fields for a phenomenological k(T,P) record.

    :param channel_id: Owning network-channel id.
    :param solve_id: Owning network-solve id.
    :param model_kind: Kinetics functional form (chebyshev, plog, tabulated).
    :param tmin_k: Optional minimum valid temperature in K.
    :param tmax_k: Optional maximum valid temperature in K.
    :param pmin_bar: Optional minimum valid pressure in bar.
    :param pmax_bar: Optional maximum valid pressure in bar.
    :param rate_units: Optional rate constant units.
    :param pressure_units: Optional pressure units.
    :param temperature_units: Optional temperature units.
    :param stores_log10_k: Whether tabulated values are log10(k).
    :param note: Optional free-text note.
    """

    channel_id: int
    solve_id: int
    model_kind: NetworkKineticsModelKind

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
    def validate_temperature_range(self) -> Self:
        if (
            self.tmin_k is not None
            and self.tmax_k is not None
            and self.tmin_k > self.tmax_k
        ):
            raise ValueError("tmin_k must be less than or equal to tmax_k.")
        return self

    @model_validator(mode="after")
    def validate_pressure_range(self) -> Self:
        if (
            self.pmin_bar is not None
            and self.pmax_bar is not None
            and self.pmin_bar > self.pmax_bar
        ):
            raise ValueError("pmin_bar must be less than or equal to pmax_bar.")
        return self


class NetworkKineticsCreate(NetworkKineticsBase, SchemaBase):
    """Create schema for a phenomenological k(T,P) record.

    Exactly one parameterization should be provided, matching ``model_kind``:
    chebyshev for ``chebyshev``, plog_entries for ``plog``, points for
    ``tabulated``.
    """

    chebyshev: NetworkKineticsChebyshevCreate | None = None
    plog_entries: list[NetworkKineticsPlogCreate] = Field(default_factory=list)
    points: list[NetworkKineticsPointCreate] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_parameterization_matches_model(self) -> Self:
        has_cheb = self.chebyshev is not None
        has_plog = len(self.plog_entries) > 0
        has_points = len(self.points) > 0

        if self.model_kind == NetworkKineticsModelKind.chebyshev and not has_cheb:
            raise ValueError(
                "model_kind='chebyshev' requires chebyshev coefficients."
            )
        if self.model_kind == NetworkKineticsModelKind.plog and not has_plog:
            raise ValueError(
                "model_kind='plog' requires at least one plog_entries entry."
            )
        if self.model_kind == NetworkKineticsModelKind.tabulated and not has_points:
            raise ValueError(
                "model_kind='tabulated' requires at least one data point."
            )

        return self

    @model_validator(mode="after")
    def validate_unique_plog_entries(self) -> Self:
        keys = [
            (p.pressure_bar, p.entry_index) for p in self.plog_entries
        ]
        if len(set(keys)) != len(keys):
            raise ValueError(
                "PLOG entries must be unique by (pressure_bar, entry_index)."
            )
        return self

    @model_validator(mode="after")
    def validate_unique_points(self) -> Self:
        keys = [
            (p.temperature_k, p.pressure_bar) for p in self.points
        ]
        if len(set(keys)) != len(keys):
            raise ValueError(
                "Tabulated points must be unique by (temperature_k, pressure_bar)."
            )
        return self


class NetworkKineticsUpdate(SchemaBase):
    """Patch schema for a phenomenological k(T,P) record."""

    model_kind: NetworkKineticsModelKind | None = None

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
    def validate_temperature_range(self) -> Self:
        if (
            self.tmin_k is not None
            and self.tmax_k is not None
            and self.tmin_k > self.tmax_k
        ):
            raise ValueError("tmin_k must be less than or equal to tmax_k.")
        return self

    @model_validator(mode="after")
    def validate_pressure_range(self) -> Self:
        if (
            self.pmin_bar is not None
            and self.pmax_bar is not None
            and self.pmin_bar > self.pmax_bar
        ):
            raise ValueError("pmin_bar must be less than or equal to pmax_bar.")
        return self


class NetworkKineticsRead(NetworkKineticsBase, TimestampedReadSchema):
    """Read schema for a phenomenological k(T,P) record."""

    chebyshev: NetworkKineticsChebyshevRead | None = None
    plog_entries: list[NetworkKineticsPlogRead] = Field(default_factory=list)
    points: list[NetworkKineticsPointRead] = Field(default_factory=list)
