"""Read schemas for the scientific Network-Kinetics standalone detail surface.

Covers:

- ``GET /api/v1/scientific/network-kinetics/{network_kinetics_ref_or_id}``

This is the next slice on top of the
``/scientific/networks/{ref}`` / ``/scientific/network-solves/{ref}``
surfaces. `NetworkKinetics` now has a ``nkin_…`` public ref, so the
model-specific payloads (Chebyshev coefficient matrix, PLOG rows,
point-tabulated triples) can be surfaced explicitly on their own
endpoint — the network and solve surfaces still keep these out of
their embedded summaries (shape metadata only).

See ``backend/docs/specs/scientific_network_reads.md``.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from app.db.models.common import (
    ArrheniusAUnits,
    NetworkChannelKind,
    NetworkKineticsModelKind,
    PressureUnit,
    TemperatureUnit,
)
from app.schemas.reads.scientific_common import (
    RecordReviewBadge,
    ReviewStatusSummary,
)
from app.schemas.reads.scientific_network import (
    NetworkReviewEntry,
    NetworkSourceCalculationSummary,
    RequestEcho,
)


# ---------------------------------------------------------------------------
# Core block
# ---------------------------------------------------------------------------


class NetworkKineticsCoreBlock(BaseModel):
    """Direct ``network_kinetics`` row metadata for the standalone surface.

    The shape metadata (``chebyshev_shape``, ``plog_entry_count``,
    ``point_count``) is computed from the per-parameterization child
    tables and is the same projection the embedded summary on the
    network and network-solve surfaces uses — anti-drift tests assert
    parity for the shared subset.
    """

    network_kinetics_id: int | None = None
    network_kinetics_ref: str

    model_kind: NetworkKineticsModelKind

    tmin_k: float | None = None
    tmax_k: float | None = None
    pmin_bar: float | None = None
    pmax_bar: float | None = None

    rate_units: ArrheniusAUnits | None = None
    pressure_units: PressureUnit | None = None
    temperature_units: TemperatureUnit | None = None
    stores_log10_k: bool | None = None

    chebyshev_shape: str | None = None
    plog_entry_count: int | None = None
    point_count: int | None = None

    note: str | None = None
    created_at: datetime
    review: RecordReviewBadge


# ---------------------------------------------------------------------------
# Parent context blocks
# ---------------------------------------------------------------------------


class NetworkKineticsNetworkContext(BaseModel):
    """Lightweight parent-network pointer for a network-kinetics record."""

    network_id: int | None = None
    network_ref: str
    name: str | None = None
    description: str | None = None


class NetworkKineticsSolveContext(BaseModel):
    """Lightweight parent-solve pointer for a network-kinetics record."""

    network_solve_id: int | None = None
    network_solve_ref: str
    me_method: str | None = None


class NetworkKineticsChannelContext(BaseModel):
    """Lightweight parent-channel pointer for a network-kinetics record.

    ``NetworkChannel`` has no public_ref today, so ``network_channel_ref``
    is always ``None``. The composition_hash pair acts as the stable
    per-network address.
    """

    network_channel_id: int | None = None
    network_channel_ref: str | None = None
    channel_kind: NetworkChannelKind
    source_state_composition_hash: str
    sink_state_composition_hash: str


# ---------------------------------------------------------------------------
# Per-include payloads
# ---------------------------------------------------------------------------


class NetworkKineticsChebyshevCoefficient(BaseModel):
    """One Chebyshev coefficient row projected from the JSONB matrix.

    Order keys are zero-based polynomial indices on temperature and
    pressure respectively.
    """

    temperature_order: int
    pressure_order: int
    coefficient: float


class NetworkKineticsChebyshevPayload(BaseModel):
    """Chebyshev coefficient payload for ``include=coefficients`` on a
    Chebyshev kinetics record. Empty / ``None`` for non-Chebyshev kinds.

    The flattened coefficient list is capped at ``settings.public_max_limit``
    rows to bound response size on pathological matrices. When the
    coefficient count exceeds the cap, ``coefficients_truncated`` is
    ``True`` and ``coefficient_count_total`` reports the full flattened
    count so callers can paginate or escalate.
    """

    n_temperature: int
    n_pressure: int
    coefficients: list[NetworkKineticsChebyshevCoefficient] = Field(
        default_factory=list
    )
    coefficient_count_total: int = 0
    coefficients_truncated: bool = False


class NetworkKineticsPLOGEntry(BaseModel):
    """One ``network_kinetics_plog`` row projected for ``include=plog``."""

    pressure_bar: float
    entry_index: int
    a: float
    a_units: ArrheniusAUnits | None = None
    n: float
    ea_kj_mol: float


class NetworkKineticsPLOGPayload(BaseModel):
    """PLOG-entry payload for ``include=plog`` on a kinetics record.

    Entries are capped at ``settings.public_max_limit`` rows. When the
    underlying table holds more entries than the cap,
    ``plog_entries_truncated`` is ``True`` and ``plog_entry_count_total``
    reports the full row count. Empty entries / counts for non-PLOG
    kinds so the shape stays stable across kinds.
    """

    entries: list[NetworkKineticsPLOGEntry] = Field(default_factory=list)
    plog_entry_count_total: int = 0
    plog_entries_truncated: bool = False


class NetworkKineticsPointEntry(BaseModel):
    """One ``network_kinetics_point`` row projected for ``include=points``."""

    temperature_k: float
    pressure_bar: float
    rate_value: float


# ---------------------------------------------------------------------------
# Evidence + available sections
# ---------------------------------------------------------------------------


class NetworkKineticsEvidenceSummary(BaseModel):
    """Bounded evidence projection for one network-kinetics record."""

    has_chebyshev_coefficients: bool
    chebyshev_coefficient_count: int
    has_plog_entries: bool
    plog_entry_count: int
    has_point_entries: bool
    point_count: int
    source_calculation_count: int


class AvailableNetworkKineticsSections(BaseModel):
    """Boolean map describing which include sections have data."""

    has_coefficients: bool
    has_plog: bool
    has_points: bool
    has_source_calculations: bool
    has_review: bool


# ---------------------------------------------------------------------------
# Record + response envelope
# ---------------------------------------------------------------------------


class ScientificNetworkKineticsRecord(BaseModel):
    """One ``network_kinetics`` projected as a scientific record.

    Default response: kinetics core block + parent network / solve /
    channel context + bounded evidence and available_sections
    summaries. Include tokens expand the response:

    - ``coefficients`` — Chebyshev coefficient rows (None for
      non-Chebyshev kinds). The payload is capped at
      ``public_max_limit`` rows and exposes
      ``coefficient_count_total`` + ``coefficients_truncated``.
    - ``plog`` — pressure-specific Arrhenius rows. The payload is
      capped at ``public_max_limit`` rows and exposes
      ``plog_entry_count_total`` + ``plog_entries_truncated``. Empty
      entries / zero counts for non-PLOG kinds.
    - ``points`` — tabulated (T, P, k) entries, capped at
      ``public_max_limit`` rows. When the count exceeds the cap,
      ``points_truncated`` is True and ``point_count_total`` reports
      the full count.
    - ``source_calculations`` — compact source-calc summaries from the
      parent solve.
    - ``review`` — review history rows (only when the parent solve is
      reviewable; ``NetworkKinetics`` itself is not in
      ``SubmissionRecordType``).
    """

    network_kinetics: NetworkKineticsCoreBlock
    network: NetworkKineticsNetworkContext
    network_solve: NetworkKineticsSolveContext
    network_channel: NetworkKineticsChannelContext

    evidence_summary: NetworkKineticsEvidenceSummary
    available_sections: AvailableNetworkKineticsSections

    coefficients: NetworkKineticsChebyshevPayload | None = None
    plog: NetworkKineticsPLOGPayload | None = None
    points: list[NetworkKineticsPointEntry] | None = None
    point_count_total: int | None = None
    points_truncated: bool | None = None
    source_calculations: list[NetworkSourceCalculationSummary] | None = None
    review_history: list[NetworkReviewEntry] | None = None


class ScientificNetworkKineticsDetailResponse(BaseModel):
    """Response envelope for
    ``GET /scientific/network-kinetics/{handle}``."""

    request: RequestEcho
    review_summary: ReviewStatusSummary
    record: ScientificNetworkKineticsRecord


__all__ = [
    "AvailableNetworkKineticsSections",
    "NetworkKineticsChannelContext",
    "NetworkKineticsChebyshevCoefficient",
    "NetworkKineticsChebyshevPayload",
    "NetworkKineticsCoreBlock",
    "NetworkKineticsEvidenceSummary",
    "NetworkKineticsNetworkContext",
    "NetworkKineticsPLOGEntry",
    "NetworkKineticsPLOGPayload",
    "NetworkKineticsPointEntry",
    "NetworkKineticsSolveContext",
    "ScientificNetworkKineticsDetailResponse",
    "ScientificNetworkKineticsRecord",
]
