"""Read schemas for the scientific conformer read surface.

Covers the detail endpoints:

- ``GET /api/v1/scientific/conformer-groups/{conformer_group_ref_or_id}``
- ``GET /api/v1/scientific/conformer-observations/{conformer_observation_ref_or_id}``

Conformer concepts split three ways (per
``backend/docs/specs/scientific_conformer_reads.md``):

- ``conformer_group`` — basin identity; reviewable; carries ``cg_…`` ref.
- ``conformer_observation`` — provenance / upload row; reviewable;
  carries ``co_…`` ref.
- ``conformer_selection`` — curation row keyed by selection_kind;
  **not reviewable** and **has no public ref column today** (see open
  question 13.1 in the spec). Surfaced via an integer ``conformer_selection_id``
  that is policy-gated by the Phase D internal-ID visibility helper.

Large JSON blobs (``representative_fingerprint_json``,
``representative_coords_json`` on the group;
``torsion_fingerprint_json`` on the observation) are deliberately NOT
surfaced by the default projection. A future ``include=fingerprints``
token could carry them under explicit size bounds.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from app.db.models.common import (
    ConformerAssignmentScopeKind,
    ConformerSelectionKind,
    ScientificOriginKind,
)
from app.schemas.reads.scientific_calculation import (
    CalculationGeometryLinkSummary,
)
from app.schemas.reads.scientific_common import (
    LevelOfTheorySummary,
    ProfiledRequestEcho,
    RecordReviewBadge,
    ReviewStatusSummary,
    SoftwareReleaseSummary,
    WorkflowToolReleaseSummary,
)

# ---------------------------------------------------------------------------
# Request
# ---------------------------------------------------------------------------


class ConformerGroupDetailRequest(BaseModel):
    """Service-layer request for the conformer-group detail read."""

    include: list[str] = Field(default_factory=list)


class ConformerObservationDetailRequest(BaseModel):
    """Service-layer request for the conformer-observation detail read."""

    include: list[str] = Field(default_factory=list)


class RequestEcho(ProfiledRequestEcho):
    """Echo of the parsed include list, post-validation and post-policy."""

    include: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Core blocks
# ---------------------------------------------------------------------------


class ConformerGroupCoreBlock(BaseModel):
    """Direct conformer-group-row metadata.

    Carries the public ref (``cg_…``) plus the curator-facing label /
    note pair. The two JSONB blobs on the row
    (``representative_fingerprint_json``, ``representative_coords_json``)
    are intentionally absent — they can be large and are out of the
    default surface. A future ``include=fingerprints`` token could
    surface them with explicit size bounds.
    """

    conformer_group_id: int | None = None
    conformer_group_ref: str
    label: str | None = None
    note: str | None = None
    created_at: datetime
    review: RecordReviewBadge


class ConformerObservationCoreBlock(BaseModel):
    """Direct conformer-observation-row metadata.

    ``scientific_origin`` is the producer of this observation
    (computed / experimental / estimated). The
    ``torsion_fingerprint_json`` blob is omitted by the default
    projection for the same reasons as the group-level coordinate
    blob.
    """

    conformer_observation_id: int | None = None
    conformer_observation_ref: str
    scientific_origin: ScientificOriginKind | None = None
    note: str | None = None
    created_at: datetime
    review: RecordReviewBadge


# ---------------------------------------------------------------------------
# Species context
# ---------------------------------------------------------------------------


class ConformerSpeciesContext(BaseModel):
    """Lightweight species/species-entry pointer for a conformer record.

    Mirrors :class:`SpeciesEntryOwnerSummary` shape conventions used by
    the calculation surface so a generic client can reuse one parser
    for both surfaces. Integer ids are Phase D policy-gated.

    ``species_entry_label`` is the short discriminator that says *which*
    entry of that species this is -- ``"E"``, ``"Z"``, ``"excited T1"``.
    ``canonical_smiles`` is the species' graph identity and is shared by
    every entry under it, so without the label a search that returns
    records for two entries of one species labels them identically. That
    is not hypothetical: a statmech search for the deployed ``N=N``
    species returns eight records across cis- and trans-diazene,
    molecules with different thermochemistry, and every one of them
    reads ``N=N``. ``None`` for the plain ground-state, all-standard,
    stereo-unlabelled entry -- never ``""``. Derived by
    :func:`app.services.scientific_read.species_identity.species_entry_label`,
    the one definition every surface shares.
    """

    species_id: int | None = None
    species_ref: str
    species_entry_id: int | None = None
    species_entry_ref: str
    species_entry_label: str | None = None
    canonical_smiles: str | None = None
    inchi_key: str | None = None
    charge: int | None = None
    multiplicity: int | None = None


# ---------------------------------------------------------------------------
# Assignment scheme + selection summaries
# ---------------------------------------------------------------------------


class ConformerAssignmentSchemeSummary(BaseModel):
    """Compact projection of a ``conformer_assignment_scheme`` row.

    Used by both selection rows (``conformer_selection.assignment_scheme_id``)
    and observation rows (``conformer_observation.assignment_scheme_id``).
    Nullable ref so the schema can represent observations / selections
    without an attached scheme.
    """

    assignment_scheme_id: int | None = None
    assignment_scheme_ref: str | None = None
    name: str
    version: str | None = None
    scope: ConformerAssignmentScopeKind | None = None
    is_default: bool | None = None


class ConformerSelectionSummary(BaseModel):
    """One conformer-selection row projected for the read surface.

    ``conformer_selection`` has no ``public_ref`` column today — the
    integer ``conformer_selection_id`` is the only addressable handle
    and it is stripped under the Phase D default visibility policy.
    Callers identify selections by ``(selection_kind, assignment_scheme_ref)``
    instead of by id.

    Conformer selections are NOT reviewable records (not listed in
    ``SubmissionRecordType``), so no review badge is attached.
    """

    conformer_selection_id: int | None = None
    selection_kind: ConformerSelectionKind
    note: str | None = None
    created_at: datetime | None = None
    assignment_scheme: ConformerAssignmentSchemeSummary | None = None


# ---------------------------------------------------------------------------
# Evidence summary
# ---------------------------------------------------------------------------


class ConformerEvidenceCoverage(BaseModel):
    """How many observations in scope carry each kind of evidence.

    Every value counts **observations**, never calculations. An
    observation with three ``freq`` calculations contributes ``1`` to
    ``freq``, not ``3``. The shared denominator is
    ``ConformerGroupEvidenceSummary.observation_count``, so a caller
    reads each field as "*n* of *observation_count*".

    What a full count does and does not say
    ---------------------------------------
    ``freq == observation_count`` says the coverage is **complete** —
    every observation in the basin has at least one frequency
    calculation. It does **not** say those calculations are
    *comparable*: the five may sit at five different levels of theory,
    from five different codes, at five different geometries. A count is
    honest about coverage, not about consistency, and no number in this
    block can stand in for that.

    Half of that gap is now answerable without a second request.
    ``ConformerGroupEvidenceSummary.levels_of_theory``, beside this
    block, lists the levels used per calculation type — so
    ``freq == observation_count`` with two entries under ``freq`` is
    visibly a fully covered basin whose frequencies come from two
    levels. It still does not *assert* comparability; it just stops
    charging a round trip to find out. The code and geometry halves are
    still only under ``include=calculations``.

    ``0`` is exactly as strong as the old ``has_x is False`` was:
    nothing in scope carries that evidence.
    """

    opt: int
    freq: int
    sp: int
    geometry_validation: int
    scf_stability: int


class ConformerGroupEvidenceSummary(BaseModel):
    """Bounded calculation-evidence projection for a conformer **group**.

    ``observation_count`` is the number of observation rows under the
    group. ``calculation_count`` is the number of calculations whose
    ``conformer_observation_id`` belongs to that observation set.
    ``geometry_count`` is the number of distinct
    ``calculation_output_geometry`` rows reached through that
    calculation set. ``evidence_coverage`` reports, per evidence kind,
    how many of the ``observation_count`` observations carry it.

    Why counts here and booleans on the observation surface
    -------------------------------------------------------
    This block deliberately has a **different shape** from
    :class:`ConformerObservationEvidenceSummary`. That asymmetry is the
    point, not an oversight, and it should not be smoothed over.

    A group pools several observations. The booleans this block used to
    carry (``has_opt`` / ``has_freq`` / …) were a plain OR across all of
    them, which made them asymmetrically informative: ``false`` was
    strong (nothing in the group has it) while ``true`` was nearly
    empty (one calculation out of a hundred made it ``true``). A reader
    seeing ``has_freq: true`` on a five-observation basin could not tell
    whether five observations had frequencies or one did. Counts answer
    that question — ``freq: 2`` with ``observation_count: 5`` shows an
    unevenly covered basin at a glance — and ``count > 0`` reproduces
    the old boolean exactly, so nothing that the boolean expressed was
    lost.

    An observation is a single provenance row, so on that surface the
    booleans are unambiguous and are kept as they were.
    """

    observation_count: int | None = None
    calculation_count: int
    evidence_coverage: ConformerEvidenceCoverage
    geometry_count: int
    #: Levels of theory used in this basin, per calculation type, pooled
    #: across every observation. This is the block
    #: :class:`ConformerEvidenceCoverage`'s docstring names and then says no
    #: number in it can stand in for: ``freq == observation_count`` with two
    #: entries under ``freq`` here is a completely covered basin whose
    #: frequencies come from two different levels. It states that; it does
    #: not rule on whether they are comparable. See
    #: ``app/services/scientific_read/levels_of_theory.py``.
    levels_of_theory: dict[str, list[LevelOfTheorySummary]]


class ConformerObservationEvidenceSummary(BaseModel):
    """Bounded calculation-evidence projection for one conformer
    **observation**.

    Scope is this single observation: ``observation_count`` is always
    ``1`` and every ``has_*`` boolean describes that one provenance row,
    where a boolean is unambiguous — it cannot pool a covered
    observation with an uncovered one, which is what made the same
    booleans misleading at group scope (see
    :class:`ConformerGroupEvidenceSummary`).

    ``calculation_count`` and ``geometry_count`` carry the same meaning
    as on the group block, restricted to this observation's own
    calculations.

    As with the group block's counts, ``has_freq: true`` says frequency
    evidence exists — not that it is comparable with the frequency
    evidence on any sibling observation.
    """

    observation_count: int | None = None
    calculation_count: int
    has_opt: bool
    has_freq: bool
    has_sp: bool
    has_geometry_validation: bool
    has_scf_stability: bool
    geometry_count: int
    #: Levels of theory used on this observation, per calculation type.
    #: Keys mirror the ``has_*`` booleans above. See
    #: ``app/services/scientific_read/levels_of_theory.py``.
    levels_of_theory: dict[str, list[LevelOfTheorySummary]]


# ---------------------------------------------------------------------------
# Calculation summary (compact)
# ---------------------------------------------------------------------------


class ConformerCalculationSummary(BaseModel):
    """Compact calculation projection embedded under conformer records.

    Same shape conventions as the per-calc summary on the TS surface
    (``calculation_ref`` + type + quality + review + LoT / software /
    workflow). Heavy include sections (results, parameters, geometry
    validation, scan/IRC/path-search points) remain available on the
    calculation detail endpoint and are not surfaced here.
    """

    calculation_id: int | None = None
    calculation_ref: str
    type: str
    quality: str
    created_at: datetime
    review: RecordReviewBadge
    level_of_theory: LevelOfTheorySummary | None = None
    software_release: SoftwareReleaseSummary | None = None
    workflow_tool_release: WorkflowToolReleaseSummary | None = None


# ---------------------------------------------------------------------------
# Geometry link (reuse existing CalculationGeometryLinkSummary)
# ---------------------------------------------------------------------------


class ConformerGeometryLink(BaseModel):
    """One output-geometry link reached through a conformer's
    supporting calculations.

    Wraps the existing :class:`CalculationGeometryLinkSummary` shape
    with the additional ``calculation_ref`` pointer so a caller can
    tell *which* supporting calculation produced this geometry without
    a second round-trip. Full XYZ / atom / coordinate payloads remain
    only behind ``GET /scientific/geometries/{geometry_ref}``.
    """

    calculation_id: int | None = None
    calculation_ref: str
    geometry: CalculationGeometryLinkSummary


# ---------------------------------------------------------------------------
# Available sections + review history
# ---------------------------------------------------------------------------


class AvailableConformerSections(BaseModel):
    """Boolean map describing which heavy include sections have data."""

    has_observations: bool
    has_selections: bool
    has_calculations: bool
    has_geometries: bool
    has_review: bool


class ConformerReviewEntry(BaseModel):
    """One ``record_review`` row projected for ``include=review``.

    The associated record is implicit (conformer_group or
    conformer_observation depending on which detail surface returned
    the block). Reviewer / reviewed_by id is policy-gated by the
    Phase D internal-ID visibility helper.
    """

    status: str
    reviewed_at: datetime | None = None
    reviewed_by: int | None = None
    note: str | None = None


# ---------------------------------------------------------------------------
# Records
# ---------------------------------------------------------------------------


class ConformerObservationsSummary(BaseModel):
    """Counts of observation rows under one conformer group, broken
    down by scientific_origin (computed / experimental / estimated)."""

    total: int
    by_scientific_origin: dict[str, int] = Field(default_factory=dict)


class ScientificConformerObservationRecord(BaseModel):
    """One conformer-observation projected as a scientific record.

    Shared between the observation detail endpoint (one record per
    request) and the conformer-group detail surface under
    ``include=observations`` (one record per observation under the
    group). Reusing the shape lets a generic client parse both
    surfaces with one set of code.
    """

    conformer_observation: ConformerObservationCoreBlock
    conformer_group: ConformerGroupCoreBlock
    species: ConformerSpeciesContext
    assignment_scheme: ConformerAssignmentSchemeSummary | None = None
    evidence_summary: ConformerObservationEvidenceSummary
    available_sections: AvailableConformerSections

    # Optional include blocks
    #: Every observation in this record's conformer group, this one
    #: included — the same list the group surface returns under the same
    #: token. Populated under ``include=observations``, which on an
    #: observation-grained record is the one question the record cannot
    #: answer from itself: *what else is in this basin*. Never populated on
    #: a record nested inside another record's ``observations`` block.
    observations: list[ScientificConformerObservationRecord] | None = None
    selections: list[ConformerSelectionSummary] | None = None
    calculations: list[ConformerCalculationSummary] | None = None
    geometries: list[ConformerGeometryLink] | None = None
    review_history: list[ConformerReviewEntry] | None = None


class ScientificConformerGroupRecord(BaseModel):
    """One conformer-group projected as a scientific record.

    Carries the group core block + parent species context + bounded
    summaries (observations breakdown, evidence summary,
    available_sections). Heavy include blocks
    (``observations`` / ``selections`` / ``calculations`` /
    ``geometries`` / ``review_history``) are populated only when the
    caller opts in.
    """

    conformer_group: ConformerGroupCoreBlock
    species: ConformerSpeciesContext
    observations_summary: ConformerObservationsSummary
    selection_summary: list[ConformerSelectionSummary] = Field(default_factory=list)
    evidence_summary: ConformerGroupEvidenceSummary
    available_sections: AvailableConformerSections

    # Optional include blocks
    observations: list[ScientificConformerObservationRecord] | None = None
    selections: list[ConformerSelectionSummary] | None = None
    calculations: list[ConformerCalculationSummary] | None = None
    geometries: list[ConformerGeometryLink] | None = None
    review_history: list[ConformerReviewEntry] | None = None


# ---------------------------------------------------------------------------
# Response envelopes
# ---------------------------------------------------------------------------


class ScientificConformerGroupDetailResponse(BaseModel):
    """Response envelope for
    ``GET /scientific/conformer-groups/{handle}``."""

    request: RequestEcho
    review_summary: ReviewStatusSummary
    record: ScientificConformerGroupRecord


class ScientificConformerObservationDetailResponse(BaseModel):
    """Response envelope for
    ``GET /scientific/conformer-observations/{handle}``."""

    request: RequestEcho
    review_summary: ReviewStatusSummary
    record: ScientificConformerObservationRecord


__all__ = [
    "AvailableConformerSections",
    "ConformerAssignmentSchemeSummary",
    "ConformerCalculationSummary",
    "ConformerEvidenceCoverage",
    "ConformerGeometryLink",
    "ConformerGroupCoreBlock",
    "ConformerGroupDetailRequest",
    "ConformerGroupEvidenceSummary",
    "ConformerObservationCoreBlock",
    "ConformerObservationDetailRequest",
    "ConformerObservationEvidenceSummary",
    "ConformerObservationsSummary",
    "ConformerReviewEntry",
    "ConformerSelectionSummary",
    "ConformerSpeciesContext",
    "RequestEcho",
    "ScientificConformerGroupDetailResponse",
    "ScientificConformerGroupRecord",
    "ScientificConformerObservationDetailResponse",
    "ScientificConformerObservationRecord",
]
