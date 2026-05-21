"""Read schemas for the scientific energy-correction-scheme surface.

Covers:

- ``GET /api/v1/scientific/energy-correction-schemes/{energy_correction_scheme_ref_or_id}``
- ``GET/POST /api/v1/scientific/energy-correction-schemes/search``

EnergyCorrectionScheme is a content-derived reference table (prefix
``ecs_``). It is not in ``SubmissionRecordType``, so it has no per-row
review history; the envelope still carries an empty ``review_summary``
for shape parity.

See ``backend/docs/specs/scientific_correction_reads.md``.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from app.db.models.common import EnergyCorrectionSchemeKind, EnergyUnit
from app.schemas.reads.scientific_common import (
    LevelOfTheorySummary,
    LiteratureSummary,
    ReviewStatusSummary,
)


# ---------------------------------------------------------------------------
# Request
# ---------------------------------------------------------------------------


class RequestEcho(BaseModel):
    """Echo of the parsed include list, post-validation and post-policy."""

    include: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Core block
# ---------------------------------------------------------------------------


class EnergyCorrectionSchemeCoreBlock(BaseModel):
    """Direct energy_correction_scheme row metadata."""

    energy_correction_scheme_id: int | None = None
    energy_correction_scheme_ref: str
    name: str
    scheme_kind: EnergyCorrectionSchemeKind
    version: str | None = None
    units: EnergyUnit | None = None
    note: str | None = None
    created_at: datetime


# ---------------------------------------------------------------------------
# Correction terms (include=corrections)
# ---------------------------------------------------------------------------


class EnergyCorrectionTermSummary(BaseModel):
    """One correction parameter row projected for ``include=corrections``.

    Covers all three child tables uniformly:

    - ``atom`` from ``energy_correction_scheme_atom_param``
      (target = element symbol).
    - ``bond`` from ``energy_correction_scheme_bond_param``
      (target = bond key, e.g. ``C-H``).
    - ``component`` from ``energy_correction_scheme_component_param``
      (correction_kind = Melius sub-type, target = composite key).

    Child rows do not get standalone public refs — there is no use
    case for addressing them outside the parent scheme.
    """

    correction_kind: str
    target: str
    value: float
    component_kind: str | None = None


class EnergyCorrectionSchemeUsageSummary(BaseModel):
    """One inverse-link to a record that uses this scheme.

    Currently sourced from ``applied_energy_correction`` rows whose
    ``scheme_id`` matches the scheme. The pointer resolves to a
    species/reaction/transition-state-entry scientific record endpoint.
    """

    record_type: str
    record_ref: str
    record_id: int | None = None
    endpoint: str


class EnergyCorrectionSchemeEvidenceSummary(BaseModel):
    """Bounded evidence projection for an ECS row."""

    atom_param_count: int
    bond_param_count: int
    component_param_count: int
    has_corrections: bool
    applied_usage_count: int
    has_applied_usage: bool
    has_literature_source: bool


class AvailableEnergyCorrectionSchemeSections(BaseModel):
    """Boolean map describing which heavy include sections have data."""

    has_corrections: bool
    has_used_by: bool
    has_literature: bool


# ---------------------------------------------------------------------------
# Record + response envelope
# ---------------------------------------------------------------------------


class ScientificEnergyCorrectionSchemeRecord(BaseModel):
    """One ECS row projected as a scientific record."""

    energy_correction_scheme: EnergyCorrectionSchemeCoreBlock
    level_of_theory: LevelOfTheorySummary | None = None
    literature: LiteratureSummary | None = None
    evidence_summary: EnergyCorrectionSchemeEvidenceSummary
    available_sections: AvailableEnergyCorrectionSchemeSections

    # Optional include blocks
    corrections: list[EnergyCorrectionTermSummary] | None = None
    used_by: list[EnergyCorrectionSchemeUsageSummary] | None = None


class ScientificEnergyCorrectionSchemeDetailResponse(BaseModel):
    """Response envelope for ``GET /scientific/energy-correction-schemes/{handle}``."""

    request: RequestEcho
    review_summary: ReviewStatusSummary
    record: ScientificEnergyCorrectionSchemeRecord


__all__ = [
    "AvailableEnergyCorrectionSchemeSections",
    "EnergyCorrectionSchemeCoreBlock",
    "EnergyCorrectionSchemeEvidenceSummary",
    "EnergyCorrectionSchemeUsageSummary",
    "EnergyCorrectionTermSummary",
    "RequestEcho",
    "ScientificEnergyCorrectionSchemeDetailResponse",
    "ScientificEnergyCorrectionSchemeRecord",
]
