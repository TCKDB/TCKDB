"""Read-oriented composition schemas for the network read API.

These schemas join the pure table CRUD schemas in
``app/schemas/entities/network.py`` and ``app/schemas/entities/network_pdep.py``
into graph-shaped responses suitable for frontend consumption.

They are strictly read-only: they embed nested entity payloads where the
consumer expects a stitched object, while still carrying the raw IDs so
clients can follow links if needed.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.db.models.common import (
    ArrheniusAUnits,
    NetworkChannelKind,
    NetworkKineticsModelKind,
    NetworkSolveCalculationRole,
    NetworkSpeciesRole,
    NetworkStateKind,
    PressureUnit,
    TemperatureUnit,
)
from app.schemas.common import ORMBaseSchema
from app.schemas.entities.calculation import CalculationRead
from app.schemas.entities.literature import LiteratureRead
from app.schemas.entities.network_pdep import (
    NetworkKineticsChebyshevRead,
    NetworkKineticsPlogRead,
    NetworkKineticsPointRead,
)
from app.schemas.entities.reaction import ReactionEntryRead
from app.schemas.entities.software import SoftwareReleaseRead
from app.schemas.entities.species_entry import SpeciesEntryRead
from app.schemas.entities.workflow import WorkflowToolReleaseRead

# ---------------------------------------------------------------------------
# Network species / reaction link reads (embed the linked entity)
# ---------------------------------------------------------------------------


class NetworkSpeciesLinkRead(ORMBaseSchema):
    """A species link on a network, with the linked species entry embedded."""

    network_id: int
    species_entry_id: int
    role: NetworkSpeciesRole
    species_entry: SpeciesEntryRead | None = None


class NetworkReactionLinkRead(ORMBaseSchema):
    """A reaction link on a network, with the linked reaction entry embedded."""

    network_id: int
    reaction_entry_id: int
    reaction_entry: ReactionEntryRead | None = None


# ---------------------------------------------------------------------------
# Network states, state participants, and channels
# ---------------------------------------------------------------------------


class NetworkStateParticipantRead(ORMBaseSchema):
    """A species composition entry within a network state."""

    state_id: int
    species_entry_id: int
    stoichiometry: int
    species_entry: SpeciesEntryRead | None = None


class NetworkStateRead(ORMBaseSchema):
    """A macroscopic state within a reaction network."""

    id: int
    network_id: int
    kind: NetworkStateKind
    composition_hash: str
    label: str | None = None
    participants: list[NetworkStateParticipantRead] = Field(default_factory=list)


class NetworkChannelRead(ORMBaseSchema):
    """A directed phenomenological channel between two network states."""

    id: int
    network_id: int
    source_state_id: int
    sink_state_id: int
    kind: NetworkChannelKind


# ---------------------------------------------------------------------------
# Solve sub-entities (bath gas, energy transfer, source calcs)
# ---------------------------------------------------------------------------


class NetworkSolveBathGasRead(ORMBaseSchema):
    """Bath gas entry for one solve, with the linked species entry embedded."""

    solve_id: int
    species_entry_id: int
    mole_fraction: float
    species_entry: SpeciesEntryRead | None = None


class NetworkSolveEnergyTransferRead(ORMBaseSchema):
    """Energy transfer model parameters for one solve."""

    id: int
    solve_id: int
    model: str | None = None
    alpha0_cm_inv: float | None = None
    t_exponent: float | None = None
    t_ref_k: float | None = None
    note: str | None = None


class NetworkSolveSourceCalculationRead(ORMBaseSchema):
    """A solve → calculation link, with the linked calculation embedded."""

    solve_id: int
    calculation_id: int
    role: NetworkSolveCalculationRole
    calculation: CalculationRead | None = None


# ---------------------------------------------------------------------------
# Network kinetics (polymorphic: exactly one of chebyshev / plog / points)
# ---------------------------------------------------------------------------


class NetworkKineticsRead(BaseModel):
    """Read schema for one fitted k(T,P) record on a channel for a solve.

    Exactly one of ``chebyshev``, ``plog``, or ``points`` will be populated
    according to ``model_kind``. The others are absent (``None`` or empty
    list).
    """

    model_config = ConfigDict(from_attributes=True)

    id: int
    channel_id: int
    solve_id: int
    model_kind: NetworkKineticsModelKind

    tmin_k: float | None = None
    tmax_k: float | None = None
    pmin_bar: float | None = None
    pmax_bar: float | None = None

    rate_units: ArrheniusAUnits | None = None
    pressure_units: PressureUnit | None = None
    temperature_units: TemperatureUnit | None = None
    stores_log10_k: bool | None = None

    note: str | None = None
    created_at: datetime

    chebyshev: NetworkKineticsChebyshevRead | None = None
    plog_entries: list[NetworkKineticsPlogRead] = Field(default_factory=list)
    points: list[NetworkKineticsPointRead] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Network solve list / detail
# ---------------------------------------------------------------------------


class _NetworkSolveBase(ORMBaseSchema):
    id: int
    network_id: int
    created_at: datetime
    created_by: int | None = None

    literature_id: int | None = None
    software_release_id: int | None = None
    workflow_tool_release_id: int | None = None

    me_method: str | None = None
    interpolation_model: str | None = None

    grain_size_cm_inv: float | None = None
    grain_count: int | None = None
    emax_kj_mol: float | None = None

    tmin_k: float | None = None
    tmax_k: float | None = None
    pmin_bar: float | None = None
    pmax_bar: float | None = None

    note: str | None = None


class NetworkSolveListItemRead(_NetworkSolveBase):
    """Lightweight summary for the per-network solve listing."""

    literature: LiteratureRead | None = None
    software_release: SoftwareReleaseRead | None = None
    workflow_tool_release: WorkflowToolReleaseRead | None = None

    bath_gas_count: int = 0
    source_calculation_count: int = 0
    kinetics_count: int = 0


class NetworkSolveDetailRead(_NetworkSolveBase):
    """Full detail for one master-equation solve."""

    literature: LiteratureRead | None = None
    software_release: SoftwareReleaseRead | None = None
    workflow_tool_release: WorkflowToolReleaseRead | None = None

    bath_gases: list[NetworkSolveBathGasRead] = Field(default_factory=list)
    energy_transfer: NetworkSolveEnergyTransferRead | None = None
    source_calculations: list[NetworkSolveSourceCalculationRead] = Field(
        default_factory=list
    )
    kinetics: list[NetworkKineticsRead] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Network list / detail
# ---------------------------------------------------------------------------


class _NetworkBase(ORMBaseSchema):
    id: int
    name: str | None = None
    description: str | None = None
    created_at: datetime
    created_by: int | None = None

    literature_id: int | None = None
    software_release_id: int | None = None
    workflow_tool_release_id: int | None = None


class NetworkListItemRead(_NetworkBase):
    """Lightweight network summary for the paginated /networks list."""

    literature: LiteratureRead | None = None
    software_release: SoftwareReleaseRead | None = None
    workflow_tool_release: WorkflowToolReleaseRead | None = None

    species_count: int = 0
    reaction_count: int = 0
    state_count: int = 0
    channel_count: int = 0
    solve_count: int = 0


class NetworkDetailRead(_NetworkBase):
    """Detailed structural view of a network.

    Does not inline the heavy per-solve kinetics payload — only a
    ``solve_count`` is returned; fetch the solve detail endpoint for
    kinetics data.
    """

    literature: LiteratureRead | None = None
    software_release: SoftwareReleaseRead | None = None
    workflow_tool_release: WorkflowToolReleaseRead | None = None

    species: list[NetworkSpeciesLinkRead] = Field(default_factory=list)
    reactions: list[NetworkReactionLinkRead] = Field(default_factory=list)
    states: list[NetworkStateRead] = Field(default_factory=list)
    channels: list[NetworkChannelRead] = Field(default_factory=list)
    solve_count: int = 0
