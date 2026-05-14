"""Read schemas for /api/v1/scientific/calculations/{calculation_ref_or_id}.

Default-shape detail response only — heavy include payloads (results,
dependencies, parameters, constraints, artifacts, geometries,
geometry_validation, scf_stability, scan, irc, path_search) are wired
through include validation but not yet expanded; see
``backend/docs/specs/scientific_calculation_reads.md``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.db.models.common import (
    CalculationQuality,
    CalculationType,
    SpeciesEntryStateKind,
    StationaryPointKind,
    TransitionStateEntryStatus,
)
from app.schemas.reads.scientific_common import (
    GeometryValidationStatus,
    LevelOfTheorySummary,
    LiteratureSummary,
    RecordReviewBadge,
    ReviewStatusSummary,
    SCFStabilityStatusValue,
    SoftwareReleaseSummary,
    WorkflowToolReleaseSummary,
)


# ---------------------------------------------------------------------------
# Request
# ---------------------------------------------------------------------------


class CalculationDetailRequest(BaseModel):
    """Service-layer request for the calculation detail read."""

    include: list[str] = Field(default_factory=list)


class RequestEcho(BaseModel):
    """Echo of the parsed include list, post-validation and post-policy."""

    include: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Owner summaries
# ---------------------------------------------------------------------------


class SpeciesEntryOwnerSummary(BaseModel):
    """Compact species/species-entry owner shape for a calculation.

    Mirrors ``SpeciesCalculationsSpeciesContext`` but only carries the
    fields the calculation-detail endpoint reliably has on hand without
    additional heavy joins. ``species_id`` / ``species_entry_id`` are
    stripped by the Phase D internal-ids policy when not allowed.
    """

    species_id: int
    species_ref: str
    species_entry_id: int
    species_entry_ref: str
    canonical_smiles: str
    inchi_key: str
    charge: int
    multiplicity: int
    species_entry_kind: StationaryPointKind
    electronic_state_kind: SpeciesEntryStateKind


class TransitionStateEntryOwnerSummary(BaseModel):
    """Compact TS / TS-entry owner shape for a calculation."""

    transition_state_id: int
    transition_state_ref: str
    transition_state_entry_id: int
    transition_state_entry_ref: str
    label: str | None = None
    charge: int
    multiplicity: int
    status: TransitionStateEntryStatus
    reaction_entry_id: int | None = None
    reaction_entry_ref: str | None = None


class CalculationOwnerSummary(BaseModel):
    """Discriminated owner block.

    Exactly one of ``species_entry`` / ``transition_state_entry`` is
    non-null; ``kind`` mirrors that for cheap client-side branching.
    The schema invariant ``one_owner`` on the calculation table
    guarantees this.
    """

    kind: Literal["species_entry", "transition_state_entry"]
    species_entry: SpeciesEntryOwnerSummary | None = None
    transition_state_entry: TransitionStateEntryOwnerSummary | None = None


# ---------------------------------------------------------------------------
# Calculation core + provenance
# ---------------------------------------------------------------------------


class CalculationCoreBlock(BaseModel):
    """Direct calculation-row metadata.

    Phase B/D: ``calculation_ref`` is the public stable handle alongside
    ``calculation_id`` (the integer id is stripped when the deployment
    forbids exposing internal ids).
    """

    calculation_id: int
    calculation_ref: str
    type: CalculationType
    quality: CalculationQuality
    created_at: datetime
    review: RecordReviewBadge


class CalculationEvidenceProvenanceSummary(BaseModel):
    """Lightweight provenance/evidence summary for the detail endpoint.

    Cheap projection that surfaces:

    - whether the calculation has a primary result row (``has_result``),
    - the matching geometry-validation outcome (or ``not_present``),
    - the matching SCF-stability outcome (or ``not_present``),
    - convergence flag for opt/scan/irc/path-search calculations,
    - optional ``submission_ref``/``submission_id`` for traceability
      back to the submission that created the calculation. The fields
      are always present (possibly null) so callers can detect
      "no submission link" without an extra include.
    """

    has_result: bool
    converged: bool | None = None
    geometry_validation_status: GeometryValidationStatus
    scf_stability_status: SCFStabilityStatusValue
    submission_id: int | None = None
    submission_ref: str | None = None


class AvailableCalculationSections(BaseModel):
    """Boolean map describing which heavy include sections have data.

    Computed from cheap EXISTS-style queries in the service layer so
    callers can avoid issuing follow-up requests for empty sections.
    All fields are always present; values reflect what an
    ``include=<token>`` would expand to.
    """

    has_results: bool
    has_dependencies: bool
    has_parameters: bool
    has_constraints: bool
    has_artifacts: bool
    has_input_geometries: bool
    has_output_geometries: bool
    has_geometry_validation: bool
    has_scf_stability: bool
    has_scan: bool
    has_irc: bool
    has_path_search: bool


# ---------------------------------------------------------------------------
# Top-level record + response envelope
# ---------------------------------------------------------------------------


class ScientificCalculationRecord(BaseModel):
    """One calculation projected as a scientific/provenance record."""

    calculation: CalculationCoreBlock
    owner: CalculationOwnerSummary
    level_of_theory: LevelOfTheorySummary | None = None
    software_release: SoftwareReleaseSummary | None = None
    workflow_tool_release: WorkflowToolReleaseSummary | None = None
    literature: LiteratureSummary | None = None
    provenance: CalculationEvidenceProvenanceSummary
    available_sections: AvailableCalculationSections


class ScientificCalculationDetailResponse(BaseModel):
    """Response envelope for /api/v1/scientific/calculations/{handle}."""

    request: RequestEcho
    review_summary: ReviewStatusSummary
    record: ScientificCalculationRecord


__all__ = [
    "AvailableCalculationSections",
    "CalculationCoreBlock",
    "CalculationDetailRequest",
    "CalculationEvidenceProvenanceSummary",
    "CalculationOwnerSummary",
    "RequestEcho",
    "ScientificCalculationDetailResponse",
    "ScientificCalculationRecord",
    "SpeciesEntryOwnerSummary",
    "TransitionStateEntryOwnerSummary",
]
