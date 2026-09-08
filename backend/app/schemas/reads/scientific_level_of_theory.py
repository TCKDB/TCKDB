"""Read schemas for the scientific level-of-theory surface.

Covers:

- ``GET /api/v1/scientific/level-of-theories/{level_of_theory_ref_or_id}``
- ``GET/POST /api/v1/scientific/level-of-theories/search``

``level_of_theory`` is a first-class, deduplicated provenance identity
(DR-0034), keyed by ``lot_hash`` over ``method, basis, aux_basis,
cabs_basis, dispersion, solvent, solvent_model, keywords, spin_treatment``.
It is not in ``SubmissionRecordType``, so it has no per-row review
history; the envelope still carries an empty ``review_summary`` for shape
parity with the rest of the scientific surface.

This is the record the methods-surface plan describes: "what does a
computational chemist look up before trusting a number computed at this
level" -- the recipe (identity + observed software), the correction
parameters actually deposited against it, its frequency scale factor(s),
and how many calculations use it. See ``docs/plans/methods-surface.md``
sections 5.1-5.2 (fetched from ``plan-methods-surface-v2`` at
implementation time; not committed to this repo).

See ``app/schemas/reads/scientific_energy_correction_scheme.py`` and
``scientific_frequency_scale_factor.py`` -- this module is deliberately
shaped like both.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from app.db.models.common import CalculationType, FrequencyScaleKind, SpinTreatment
from app.schemas.reads.scientific_common import (
    ProfiledRequestEcho,
    ReviewStatusSummary,
    SoftwareReleaseSummary,
    WorkflowToolReleaseSummary,
)
from app.schemas.reads.scientific_energy_correction_scheme import (
    ScientificEnergyCorrectionSchemeRecord,
)

# ---------------------------------------------------------------------------
# Request
# ---------------------------------------------------------------------------


class RequestEcho(ProfiledRequestEcho):
    """Echo of the parsed include list, post-validation and post-policy."""

    include: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Core block
# ---------------------------------------------------------------------------


class LevelOfTheoryCoreBlock(BaseModel):
    """Direct ``level_of_theory`` row metadata.

    ``lot_hash`` is served plainly rather than gated behind
    ``include=internal_ids``: it is already a public, documented filter on
    ``/scientific/calculations/search`` (``lot_hash=``), so hiding it here
    while accepting it there would gate nothing real. Unlike
    ``level_of_theory_id`` it names no database row, so it carries none of
    the enumeration risk internal ids exist to withhold.

    ``aux_basis``/``cabs_basis``/``keywords`` are part of ``lot_hash``'s
    identity inputs (DR-0034) alongside the fields already served
    elsewhere; a page whose whole point is showing what distinguishes one
    level of theory from another should not silently drop three of the
    nine. There is no ``label`` field: the ORM row carries no such column
    (unlike ``LevelOfTheorySummary.label``, which is a cross-record
    presentation field every existing builder sets to ``None`` for lack of
    a stored value) -- inventing one here would fabricate a field the API
    cannot back.
    """

    level_of_theory_id: int | None = None
    level_of_theory_ref: str
    method: str
    basis: str | None = None
    aux_basis: str | None = None
    cabs_basis: str | None = None
    dispersion: str | None = None
    solvent: str | None = None
    solvent_model: str | None = None
    keywords: str | None = None
    spin_treatment: SpinTreatment | None = None
    lot_hash: str
    created_at: datetime


# ---------------------------------------------------------------------------
# Frequency scale factors (include=frequency_scale_factors)
# ---------------------------------------------------------------------------


class LevelOfTheoryFrequencyScaleFactorProvenance(BaseModel):
    """One underlying ``frequency_scale_factor`` row inside a dedup group.

    A level of theory can carry several FSF rows that all evaluate to the
    same ``(scale_kind, value)`` pair -- ten of them for b3lyp/def2tzvp on
    the live archive today, one per distinct producing workflow-tool
    release. Each one is still a separate depositor action and keeps its
    own ref and provenance here; only the *grouping* collapses, never the
    rows themselves (methods-surface plan §4.2 item 4).
    """

    frequency_scale_factor_ref: str
    frequency_scale_factor_id: int | None = None
    software_release: SoftwareReleaseSummary | None = None
    workflow_tool_release: WorkflowToolReleaseSummary | None = None
    source_literature_ref: str | None = None


class LevelOfTheoryFrequencyScaleFactorGroup(BaseModel):
    """One distinct ``(scale_kind, value)`` pair observed for this LOT."""

    scale_kind: FrequencyScaleKind
    value: float
    frequency_scale_factor_count: int
    frequency_scale_factors: list[LevelOfTheoryFrequencyScaleFactorProvenance]


# ---------------------------------------------------------------------------
# Usage (include=used_by)
# ---------------------------------------------------------------------------


class LevelOfTheoryCalculationUsageSummary(BaseModel):
    """One calculation attributing this level of theory.

    Bounded, ordered-by-id projection -- the same shape and limit
    discipline as ``EnergyCorrectionSchemeUsageSummary`` /
    ``FrequencyScaleFactorUsageSummary``. A caller wanting the full,
    paginated set should use
    ``/scientific/calculations/search?lot_ref=...`` instead; this section
    exists so the LOT detail page does not need a second round trip for a
    first look.
    """

    calculation_ref: str
    calculation_id: int | None = None
    endpoint: str
    type: CalculationType
    record_type: str | None = None
    record_ref: str | None = None
    record_endpoint: str | None = None


# ---------------------------------------------------------------------------
# Software / workflow-tool breakdown (include=software)
# ---------------------------------------------------------------------------


class LevelOfTheorySoftwareUsage(BaseModel):
    """One ``(software, version)`` pair observed running >=1 calculation
    at this level of theory.

    ``version`` is ``None`` -- never ``""`` -- when the ``software_release``
    row(s) behind this pair carry no recorded version. Absence is stated,
    not implied: on the live archive, 2 of 3 software packages have no
    version recorded at all, and rendering that as a blank string would
    read as "we asked and it was blank" rather than "no one recorded it".
    """

    software: str
    version: str | None = None
    calculation_count: int


class LevelOfTheoryWorkflowToolUsage(BaseModel):
    """One ``(workflow_tool, version)`` pair observed running >=1
    calculation at this level of theory. Same absent-version rule as
    :class:`LevelOfTheorySoftwareUsage` -- on the live archive only 10 of
    ARC's 416 calculations carry a recorded workflow-tool-release version.
    """

    workflow_tool: str
    version: str | None = None
    calculation_count: int


class LevelOfTheorySoftwareBreakdown(BaseModel):
    """``include=software`` -- which software (and workflow tool) actually
    ran calculations at this level of theory, and how many.

    Usage-derived, same ``INNER JOIN`` discipline as ``list_software`` /
    ``list_workflow_tools`` in ``app/services/scientific_read/meta.py``
    (2026-08): a software package or workflow tool with zero calculations
    at *this* level of theory is absent from the corresponding list, never
    present with a zero count. This is the closed gap named in the
    methods-surface plan §5.3 -- establishing this mapping by hand cost 12
    ``calculations/search?lot_ref=&software=`` calls before this endpoint
    existed.
    """

    software: list[LevelOfTheorySoftwareUsage]
    workflow_tools: list[LevelOfTheoryWorkflowToolUsage]


# ---------------------------------------------------------------------------
# Evidence + available sections
# ---------------------------------------------------------------------------


class LevelOfTheoryEvidenceSummary(BaseModel):
    """Bounded evidence projection for a level-of-theory row.

    ``calculation_usage_count`` is usage-derived: an ``INNER JOIN`` from
    ``calculation.lot_id``, the same discipline ``list_software`` /
    ``list_workflow_tools`` already apply (2026-08). A level of theory
    with zero attributing calculations does not appear in
    ``/level-of-theories/search`` at all -- see that endpoint's own
    docstring.
    """

    calculation_usage_count: int
    has_correction_schemes: bool
    has_frequency_scale_factors: bool
    #: Count of distinct ``software`` packages observed running a
    #: calculation at this level of theory. Derived from the exact same
    #: query that backs ``include=software``'s ``software`` list (see
    #: ``_software_usage_rows`` in
    #: ``app/services/scientific_read/level_of_theory.py``) -- counting
    #: the distinct package names in those rows, not a separate
    #: ``COUNT(DISTINCT ...)`` query. This is a deliberate choice: PR 1
    #: computed this scalar with its own query, and a second, independent
    #: query computing the ``include=software`` breakdown could in
    #: principle disagree with it (e.g. after an unrelated edit changes
    #: one join but not the other). Sharing one query makes that
    #: disagreement structurally impossible instead of merely unlikely.
    distinct_software_count: int


class AvailableLevelOfTheorySections(BaseModel):
    """Boolean map describing which heavy include sections have data."""

    has_correction_schemes: bool
    has_frequency_scale_factors: bool
    has_used_by: bool
    #: True when at least one software package (by name) ran a
    #: calculation at this level of theory -- i.e. ``distinct_software_count
    #: > 0``. Deliberately silent on workflow-tool-only usage (a
    #: calculation citing a ``workflow_tool_release`` but no
    #: ``software_release``): that is a real but rare shape on today's
    #: archive (1 workflow tool, ARC, always alongside a software release
    #: in the measured data) and is not worth a second always-on query
    #: per record to detect. ``include=software``'s own
    #: ``workflow_tools`` list is unaffected by this simplification --
    #: it is still correct when requested, this flag just does not
    #: promise to predict it.
    has_software: bool


# ---------------------------------------------------------------------------
# Record + response envelope
# ---------------------------------------------------------------------------


class ScientificLevelOfTheoryRecord(BaseModel):
    """One ``level_of_theory`` row projected as a scientific record."""

    level_of_theory: LevelOfTheoryCoreBlock
    evidence_summary: LevelOfTheoryEvidenceSummary
    available_sections: AvailableLevelOfTheorySections

    # Optional include blocks
    correction_schemes: list[ScientificEnergyCorrectionSchemeRecord] | None = None
    frequency_scale_factors: list[LevelOfTheoryFrequencyScaleFactorGroup] | None = None
    used_by: list[LevelOfTheoryCalculationUsageSummary] | None = None
    software: LevelOfTheorySoftwareBreakdown | None = None


class ScientificLevelOfTheoryDetailResponse(BaseModel):
    """Response envelope for ``GET /scientific/level-of-theories/{handle}``."""

    request: RequestEcho
    review_summary: ReviewStatusSummary
    record: ScientificLevelOfTheoryRecord


__all__ = [
    "AvailableLevelOfTheorySections",
    "LevelOfTheoryCalculationUsageSummary",
    "LevelOfTheoryCoreBlock",
    "LevelOfTheoryEvidenceSummary",
    "LevelOfTheoryFrequencyScaleFactorGroup",
    "LevelOfTheoryFrequencyScaleFactorProvenance",
    "LevelOfTheorySoftwareBreakdown",
    "LevelOfTheorySoftwareUsage",
    "LevelOfTheoryWorkflowToolUsage",
    "RequestEcho",
    "ScientificLevelOfTheoryDetailResponse",
    "ScientificLevelOfTheoryRecord",
]
