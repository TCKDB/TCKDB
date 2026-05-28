"""Read schemas for the scientific transport read surface.

Covers:

- ``GET /api/v1/scientific/transport/{transport_ref_or_id}``
- ``GET/POST /api/v1/scientific/transport/search``

Transport rows attach at the **species_entry** level (direct FK) and
link to source calculations via ``transport_source_calculation`` by
role. Scalar parameters (``sigma_angstrom`` / ``epsilon_over_k_k`` /
``dipole_debye`` / ``polarizability_angstrom3`` /
``rotational_relaxation``) live directly on the row — there is no
nested mode/JSON payload to project. The ORM also has no
``model_kind`` column; ``scientific_origin`` (computed / experimental
/ estimated) is the closest model-class signal and is surfaced
verbatim.

See ``backend/docs/specs/scientific_transport_reads.md``.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from app.db.models.common import (
    CalculationType,
    ScientificOriginKind,
    TransportCalculationRole,
)
from app.schemas.reads.scientific_common import (
    LevelOfTheorySummary,
    LiteratureSummary,
    RecordReviewBadge,
    ReviewStatusSummary,
    SoftwareReleaseSummary,
    WorkflowToolReleaseSummary,
)
from app.services.trust.models import TrustFragment

# ---------------------------------------------------------------------------
# Request
# ---------------------------------------------------------------------------


class TransportDetailRequest(BaseModel):
    """Service-layer request for the transport detail read."""

    include: list[str] = Field(default_factory=list)


class RequestEcho(BaseModel):
    """Echo of the parsed include list, post-validation and post-policy."""

    include: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Core block
# ---------------------------------------------------------------------------


class TransportCoreBlock(BaseModel):
    """Direct transport-row metadata.

    Fixed-unit columns from the ORM are exposed verbatim:

    - ``sigma_angstrom`` — Lennard-Jones collision diameter (Å).
    - ``epsilon_over_k_k`` — LJ well depth divided by k_B (K).
    - ``dipole_debye`` — permanent dipole magnitude (Debye).
    - ``polarizability_angstrom3`` — polarizability (Å³).
    - ``rotational_relaxation`` — rotational relaxation collision
      number (dimensionless).

    The schema's ``lj_pair_both_or_neither`` constraint guarantees
    sigma and epsilon are populated together or not at all; the
    ``has_lj_parameters`` boolean on the evidence summary tracks that
    presence.
    """

    transport_id: int | None = None
    transport_ref: str
    scientific_origin: ScientificOriginKind
    sigma_angstrom: float | None = None
    epsilon_over_k_k: float | None = None
    dipole_debye: float | None = None
    polarizability_angstrom3: float | None = None
    rotational_relaxation: float | None = None
    note: str | None = None
    created_at: datetime
    review: RecordReviewBadge


# ---------------------------------------------------------------------------
# Species context
# ---------------------------------------------------------------------------


class TransportSpeciesContext(BaseModel):
    """Lightweight species/species-entry pointer for a transport record.

    Mirrors the statmech / conformer / TS species context shape.
    Integer ids are Phase D policy-gated.
    """

    species_id: int | None = None
    species_ref: str
    species_entry_id: int | None = None
    species_entry_ref: str
    canonical_smiles: str | None = None
    inchi_key: str | None = None
    charge: int | None = None
    multiplicity: int | None = None


# ---------------------------------------------------------------------------
# Source calculation + evidence + available_sections + review history
# ---------------------------------------------------------------------------


class TransportSourceCalculationSummary(BaseModel):
    """Compact source-calculation projection for
    ``include=source_calculations``.

    The ``role`` column on ``transport_source_calculation``
    distinguishes ``full_transport`` (a calc that produced the LJ
    pair directly), ``dipole``, ``polarizability``, and
    ``supporting_geometry``. Heavy include sections (results,
    parameters, geometries) remain on the calculation detail
    endpoint.
    """

    role: TransportCalculationRole
    calculation_id: int | None = None
    calculation_ref: str
    calculation_type: CalculationType
    quality: str
    created_at: datetime
    review: RecordReviewBadge
    level_of_theory: LevelOfTheorySummary | None = None
    software_release: SoftwareReleaseSummary | None = None
    workflow_tool_release: WorkflowToolReleaseSummary | None = None


class TransportEvidenceSummary(BaseModel):
    """Bounded evidence projection.

    Booleans are computed from the row's own column values and
    cheap EXISTS queries against ``transport_source_calculation``.
    """

    source_calculation_count: int
    has_source_calculations: bool
    has_lj_parameters: bool
    has_dipole_moment: bool
    has_polarizability: bool
    has_rotational_relaxation: bool
    has_literature_source: bool


class AvailableTransportSections(BaseModel):
    """Boolean map describing which heavy include sections have data."""

    has_source_calculations: bool
    has_review: bool


class TransportReviewEntry(BaseModel):
    """One ``record_review`` row projected for ``include=review``."""

    status: str
    reviewed_at: datetime | None = None
    reviewed_by: int | None = None
    note: str | None = None


# ---------------------------------------------------------------------------
# Record + response envelope
# ---------------------------------------------------------------------------


class ScientificTransportRecord(BaseModel):
    """One transport projected as a scientific record.

    Default response carries the core block + species context +
    optional software / workflow / literature provenance pointers +
    bounded evidence and available_sections summaries.
    ``source_calculations`` / ``review_history`` populate only under
    the corresponding include tokens.
    """

    transport: TransportCoreBlock
    species: TransportSpeciesContext
    software_release: SoftwareReleaseSummary | None = None
    workflow_tool_release: WorkflowToolReleaseSummary | None = None
    literature: LiteratureSummary | None = None
    evidence_summary: TransportEvidenceSummary
    available_sections: AvailableTransportSections

    # Optional include blocks
    source_calculations: list[TransportSourceCalculationSummary] | None = None
    review_history: list[TransportReviewEntry] | None = None
    trust: TrustFragment | None = None


class ScientificTransportDetailResponse(BaseModel):
    """Response envelope for ``GET /scientific/transport/{handle}``."""

    request: RequestEcho
    review_summary: ReviewStatusSummary
    record: ScientificTransportRecord


__all__ = [
    "AvailableTransportSections",
    "RequestEcho",
    "ScientificTransportDetailResponse",
    "ScientificTransportRecord",
    "TransportCoreBlock",
    "TransportDetailRequest",
    "TransportEvidenceSummary",
    "TransportReviewEntry",
    "TransportSourceCalculationSummary",
    "TransportSpeciesContext",
]
