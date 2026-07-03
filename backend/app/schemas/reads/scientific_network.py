"""Read schemas for the scientific Network / PDep read surface.

Covers the detail endpoint and is reused by the search response:

- ``GET /api/v1/scientific/networks/{network_ref_or_id}``
- ``GET/POST /api/v1/scientific/networks/search``

The Network surface ships at the **network grain** (one record per
``network`` row). Channels, states, solves, kinetics are exposed as
bounded **embedded summaries** under explicit include tokens — they
do not have public_ref columns today, so they're not standalone
addressable surfaces. ``NetworkSolve`` carries a ``nsolve_…`` public
ref (added by the same PR that ships this surface); a future PR can
ship a `/scientific/network-solves/{ref}` standalone detail endpoint
without changing this schema.

Kinetics coefficient payloads (Chebyshev coefficient matrix, PLOG
rows, point-table triplets) are deliberately **not** inlined under
``include=kinetics`` — the summary surfaces shape metadata
(`chebyshev_shape`, `plog_count`, `point_count`) only. Full
coefficient arrays are deferred to a future
``/scientific/network-kinetics/{ref}`` standalone surface that
requires `network_kinetics.public_ref` (open question §11.2 of the
spec doc).

See ``backend/docs/specs/scientific_network_reads.md``.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from app.db.models.common import (
    CalculationType,
    NetworkChannelKind,
    NetworkKineticsModelKind,
    NetworkSolveCalculationRole,
    NetworkSpeciesRole,
    NetworkStateKind,
)
from app.schemas.reads.scientific_common import (
    LevelOfTheorySummary,
    LiteratureSummary,
    RecordReviewBadge,
    ReviewStatusSummary,
    SoftwareReleaseSummary,
    WorkflowToolReleaseSummary,
)

# ---------------------------------------------------------------------------
# Request
# ---------------------------------------------------------------------------


class NetworkDetailRequest(BaseModel):
    """Service-layer request for the network detail read."""

    include: list[str] = Field(default_factory=list)


class RequestEcho(BaseModel):
    """Echo of the parsed include list, post-validation and post-policy."""

    include: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Core block
# ---------------------------------------------------------------------------


class NetworkCoreBlock(BaseModel):
    """Direct network-row metadata.

    The Network ORM row carries ``name`` / ``description`` / optional
    literature / software_release / workflow_tool_release pointers but
    no temperature/pressure ranges of its own — those live on
    ``network_solve``. The detail endpoint surfaces the union of
    solve-level T/P ranges as an aggregate (``solve_temperature_min`` /
    ``solve_temperature_max`` etc.) so a caller browsing networks can
    see the covered envelope at a glance.
    """

    network_id: int | None = None
    network_ref: str
    name: str | None = None
    description: str | None = None
    solve_temperature_min_k: float | None = None
    solve_temperature_max_k: float | None = None
    solve_pressure_min_bar: float | None = None
    solve_pressure_max_bar: float | None = None
    created_at: datetime
    review: RecordReviewBadge


# ---------------------------------------------------------------------------
# Embedded summaries
# ---------------------------------------------------------------------------


class NetworkSpeciesSummary(BaseModel):
    """One ``network_species`` row projected for ``include=species``.

    Composite PK is ``(network_id, species_entry_id, role)`` so there's
    no standalone id to expose; participants are addressed by
    ``species_entry_ref`` + ``role`` pair.
    """

    species_entry_id: int | None = None
    species_entry_ref: str
    species_ref: str
    role: NetworkSpeciesRole
    canonical_smiles: str | None = None
    inchi_key: str | None = None


class NetworkReactionSummary(BaseModel):
    """One ``network_reaction`` row projected for ``include=reactions``."""

    reaction_entry_id: int | None = None
    reaction_entry_ref: str
    reaction_id: int | None = None
    reaction_ref: str
    reversible: bool | None = None


class NetworkStateSummary(BaseModel):
    """One ``network_state`` row projected for ``include=states``.

    No public_ref on ``network_state``; states are identified by
    ``network_state_id`` (policy-gated) + ``composition_hash`` (always
    present — it's a deduplication key over species participants, not
    an internal id).
    """

    network_state_id: int | None = None
    composition_hash: str
    kind: NetworkStateKind
    label: str | None = None
    participant_count: int


class NetworkChannelSummary(BaseModel):
    """One ``network_channel`` row projected for ``include=channels``.

    Source/sink state pointers use the composition_hash because
    ``network_state`` has no public_ref. Composition_hash is unique
    per ``(network_id, composition_hash)`` so it's a stable address
    within a network.
    """

    network_channel_id: int | None = None
    kind: NetworkChannelKind
    source_state_composition_hash: str
    sink_state_composition_hash: str
    source_state_id: int | None = None
    sink_state_id: int | None = None
    has_kinetics: bool


class NetworkSolveBathGasSummary(BaseModel):
    """One ``network_solve_bath_gas`` row.

    Composite PK is ``(solve_id, species_entry_id)`` with no standalone
    id; surfaces species_entry_ref + mole_fraction.
    """

    species_entry_id: int | None = None
    species_entry_ref: str
    mole_fraction: float


class NetworkSolveSummary(BaseModel):
    """One ``network_solve`` row projected for ``include=solves``.

    NetworkSolve has its own public_ref (``nsolve_…``) so a future PR
    can ship a `/scientific/network-solves/{ref}` standalone detail
    endpoint without changing this schema.
    """

    network_solve_id: int | None = None
    network_solve_ref: str
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
    created_at: datetime
    review: RecordReviewBadge
    bath_gases: list[NetworkSolveBathGasSummary] = Field(default_factory=list)
    bath_gas_count: int
    energy_transfer_count: int
    source_calculation_count: int


class NetworkKineticsSummary(BaseModel):
    """One ``network_kinetics`` row projected for ``include=kinetics``.

    No public_ref on ``network_kinetics`` today. The id is
    policy-gated and the row is addressed within the parent network by
    the (channel_id, solve_id, model_kind) triple. Coefficient
    payloads (Chebyshev matrix, PLOG rows, point triplets) are
    intentionally **not** inlined here — only shape metadata travels.
    A future ``/scientific/network-kinetics/{ref}`` detail endpoint
    would surface them.
    """

    network_kinetics_id: int | None = None
    network_channel_id: int | None = None
    network_solve_id: int | None = None
    network_solve_ref: str | None = None
    channel_source_composition_hash: str
    channel_sink_composition_hash: str
    model_kind: NetworkKineticsModelKind
    tmin_k: float | None = None
    tmax_k: float | None = None
    pmin_bar: float | None = None
    pmax_bar: float | None = None
    plog_entry_count: int | None = None
    point_count: int | None = None
    chebyshev_shape: str | None = None  # e.g. "6x4"


class NetworkSourceCalculationSummary(BaseModel):
    """One ``network_solve_source_calculation`` row projected for
    ``include=source_calculations``.

    Compact calculation projection — full calc detail remains behind
    ``/scientific/calculations/{ref}``.
    """

    role: NetworkSolveCalculationRole
    network_solve_ref: str
    network_solve_id: int | None = None
    calculation_id: int | None = None
    calculation_ref: str
    calculation_type: CalculationType
    level_of_theory: LevelOfTheorySummary | None = None
    software_release: SoftwareReleaseSummary | None = None
    workflow_tool_release: WorkflowToolReleaseSummary | None = None


# ---------------------------------------------------------------------------
# Evidence + available sections + review history
# ---------------------------------------------------------------------------


class NetworkEvidenceSummary(BaseModel):
    """Bounded evidence projection. Counts come from cheap aggregates
    over the child tables; the ``has_*`` booleans report which kinetics
    model kinds are present anywhere in the network."""

    species_count: int
    reaction_count: int
    state_count: int
    channel_count: int
    solve_count: int
    kinetics_count: int
    source_calculation_count: int
    has_chebyshev: bool
    has_plog: bool
    has_point_kinetics: bool


class AvailableNetworkSections(BaseModel):
    """Boolean map describing which include sections have data."""

    has_species: bool
    has_reactions: bool
    has_states: bool
    has_channels: bool
    has_solves: bool
    has_kinetics: bool
    has_source_calculations: bool
    has_review: bool


class NetworkReviewEntry(BaseModel):
    """One ``record_review`` row projected for ``include=review``."""

    status: str
    reviewed_at: datetime | None = None
    reviewed_by: int | None = None
    note: str | None = None


# ---------------------------------------------------------------------------
# Record + response envelope
# ---------------------------------------------------------------------------


class ScientificNetworkRecord(BaseModel):
    """One Network projected as a scientific record.

    Default response carries the core block + bounded evidence and
    available_sections summaries + optional provenance pointers
    (software_release / workflow_tool_release / literature when the
    network row carries them). Heavy include blocks populate only
    when their tokens are present.
    """

    network: NetworkCoreBlock
    software_release: SoftwareReleaseSummary | None = None
    workflow_tool_release: WorkflowToolReleaseSummary | None = None
    literature: LiteratureSummary | None = None
    evidence_summary: NetworkEvidenceSummary
    available_sections: AvailableNetworkSections

    species: list[NetworkSpeciesSummary] | None = None
    reactions: list[NetworkReactionSummary] | None = None
    states: list[NetworkStateSummary] | None = None
    channels: list[NetworkChannelSummary] | None = None
    solves: list[NetworkSolveSummary] | None = None
    kinetics: list[NetworkKineticsSummary] | None = None
    source_calculations: list[NetworkSourceCalculationSummary] | None = None
    review_history: list[NetworkReviewEntry] | None = None


class ScientificNetworkDetailResponse(BaseModel):
    """Response envelope for ``GET /scientific/networks/{handle}``."""

    request: RequestEcho
    review_summary: ReviewStatusSummary
    record: ScientificNetworkRecord


# ---------------------------------------------------------------------------
# Network-solve standalone detail surface
# ---------------------------------------------------------------------------


class NetworkSolveCoreBlock(BaseModel):
    """Direct ``network_solve`` row metadata for the standalone solve
    detail surface.

    Larger than :class:`NetworkSolveSummary` (which is the embedded
    projection on the network detail surface): the core block carries
    only the row-level scalars and review badge; the bath-gas list /
    child counts that the embedded summary inlines move to per-include
    blocks on the solve surface.
    """

    network_solve_id: int | None = None
    network_solve_ref: str
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
    created_at: datetime
    review: RecordReviewBadge


class NetworkContextSummary(BaseModel):
    """Lightweight parent-network pointer for a solve record.

    The full network detail remains at
    ``GET /scientific/networks/{network_ref}``.
    """

    network_id: int | None = None
    network_ref: str
    name: str | None = None
    description: str | None = None


class NetworkSolveEnergyTransferSummary(BaseModel):
    """One ``network_solve_energy_transfer`` row projected for
    ``include=energy_transfer`` on the solve detail.

    ORM fields: ``model`` (free text), ``alpha0_cm_inv`` (Å — actually
    cm⁻¹ for an exponential-down model's α₀), ``t_exponent``
    (dimensionless power-law exponent on T/T_ref),
    ``t_ref_k`` (reference temperature in K), ``note``.
    """

    energy_transfer_id: int | None = None
    model: str | None = None
    alpha0_cm_inv: float | None = None
    t_exponent: float | None = None
    t_ref_k: float | None = None
    note: str | None = None


class NetworkSolveEvidenceSummary(BaseModel):
    """Bounded evidence projection for one network solve."""

    bath_gas_count: int
    energy_transfer_count: int
    source_calculation_count: int
    kinetics_count: int
    has_chebyshev: bool
    has_plog: bool
    has_point_kinetics: bool


class AvailableNetworkSolveSections(BaseModel):
    """Boolean map describing which include sections have data."""

    has_bath_gas: bool
    has_energy_transfer: bool
    has_source_calculations: bool
    has_kinetics: bool
    has_review: bool


class ScientificNetworkSolveRecord(BaseModel):
    """One ``network_solve`` projected as a scientific record.

    Default response: solve core block + parent-network context +
    bounded evidence and available_sections summaries. Include tokens
    expand to bath-gas list, energy-transfer rows, source calculation
    summaries, kinetics summaries (model_kind + shape metadata only —
    coefficient payloads are deferred to a future
    ``/scientific/network-kinetics/{ref}`` endpoint).
    """

    network_solve: NetworkSolveCoreBlock
    network: NetworkContextSummary
    software_release: SoftwareReleaseSummary | None = None
    workflow_tool_release: WorkflowToolReleaseSummary | None = None
    literature: LiteratureSummary | None = None
    evidence_summary: NetworkSolveEvidenceSummary
    available_sections: AvailableNetworkSolveSections

    bath_gas: list[NetworkSolveBathGasSummary] | None = None
    energy_transfer: list[NetworkSolveEnergyTransferSummary] | None = None
    source_calculations: list[NetworkSourceCalculationSummary] | None = None
    kinetics: list[NetworkKineticsSummary] | None = None
    review_history: list[NetworkReviewEntry] | None = None


class ScientificNetworkSolveDetailResponse(BaseModel):
    """Response envelope for
    ``GET /scientific/network-solves/{handle}``."""

    request: RequestEcho
    review_summary: ReviewStatusSummary
    record: ScientificNetworkSolveRecord


__all__ = [
    "AvailableNetworkSections",
    "AvailableNetworkSolveSections",
    "NetworkChannelSummary",
    "NetworkContextSummary",
    "NetworkCoreBlock",
    "NetworkDetailRequest",
    "NetworkEvidenceSummary",
    "NetworkKineticsSummary",
    "NetworkReactionSummary",
    "NetworkReviewEntry",
    "NetworkSolveBathGasSummary",
    "NetworkSolveCoreBlock",
    "NetworkSolveEnergyTransferSummary",
    "NetworkSolveEvidenceSummary",
    "NetworkSolveSummary",
    "NetworkSourceCalculationSummary",
    "NetworkSpeciesSummary",
    "NetworkStateSummary",
    "RequestEcho",
    "ScientificNetworkDetailResponse",
    "ScientificNetworkRecord",
    "ScientificNetworkSolveDetailResponse",
    "ScientificNetworkSolveRecord",
]
