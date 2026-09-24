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
``representative_coords_json`` on the group; ``torsion_fingerprint_json``
on the observation) are deliberately NOT surfaced by the default
projection. ``include=fingerprints`` now carries the group's
``representative_fingerprint_json`` (as the typed
:class:`ConformerGroupFingerprint`, on ``ConformerGroupCoreBlock``);
``representative_coords_json`` and ``torsion_fingerprint_json`` remain
unsurfaced -- no reader-facing need for either has been requested yet.
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


class ConformerRotorTorsion(BaseModel):
    """One rotor's torsion angle within a group's representative fingerprint.

    Pairs a rotor (``rotor_key``, e.g. ``"R_8_10"`` — a canonical
    ``R_<atom index>_<atom index>`` label for the rotatable bond) with the
    quantized bin it falls in and the representative conformer's measured
    angle at that rotor. ``quantized_bin`` — read together with the parent
    :class:`ConformerGroupFingerprint`'s ``bin_width_deg`` — is what
    *defines* basin membership for this rotor; ``raw_torsion_deg`` /
    ``folded_torsion_deg`` are what the *representative* conformer happens
    to measure there, which is a member of the basin, not the basin
    itself. Never render one as a stand-in for the other.

    Built by zipping the row's parallel ``canonical_rotor_keys`` /
    ``quantized_bins`` / ``raw_torsions_deg`` / ``folded_torsions_deg``
    arrays positionally (see
    ``app.services.scientific_read.conformers._build_group_fingerprint``);
    this type exists so that positional pairing is fixed once, at the
    service boundary, rather than re-done — and re-riskable — by every
    caller that wants to know which angle belongs to which rotor.
    """

    rotor_key: str
    quantized_bin: int
    raw_torsion_deg: float
    folded_torsion_deg: float


class ConformerGroupFingerprint(BaseModel):
    """Numeric basin identity for one conformer group.

    Surfaced only under ``include=fingerprints`` — see
    ``ConformerGroupCoreBlock.fingerprint``. ``bin_width_deg`` plus each
    entry's ``quantized_bin`` together define the torsional-basin
    membership test that groups conformers into this basin; that is the
    group's actual *definition*. The per-rotor ``raw_torsion_deg`` /
    ``folded_torsion_deg`` values on each :class:`ConformerRotorTorsion`
    are the *representative* conformer's own measured angles — a single
    member of the basin, not the basin's boundary. Two representatives can
    sit tens of degrees apart while sharing the same bin; that is normal
    and expected, not a data error.

    ``fingerprint_hash`` from the underlying
    ``representative_fingerprint_json`` blob is deliberately never
    reproduced here. It identifies the basin bin internally
    (deduplication key) and carries no meaning for a reader.
    """

    rotor_count: int
    bin_width_deg: float
    torsions: list[ConformerRotorTorsion]


class ConformerGroupCoreBlock(BaseModel):
    """Direct conformer-group-row metadata.

    Carries the public ref (``cg_…``) plus the curator-facing label /
    note pair. Of the two JSONB blobs on the row,
    ``representative_coords_json`` is intentionally absent — no surface
    need for it has been requested. ``representative_fingerprint_json``
    is surfaced as ``fingerprint``, populated only under
    ``include=fingerprints`` (``None`` otherwise, and ``None`` when the
    row itself carries no fingerprint blob or an internally inconsistent
    one — see ``_build_group_fingerprint``).
    """

    conformer_group_id: int | None = None
    conformer_group_ref: str
    label: str | None = None
    note: str | None = None
    created_at: datetime
    review: RecordReviewBadge
    fingerprint: ConformerGroupFingerprint | None = None


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


class ConformerObservationSiblingCore(BaseModel):
    """Identity + review slice of a sibling observation, list-shaped.

    Deliberately thinner than :class:`ConformerObservationCoreBlock`: no
    ``scientific_origin`` / ``note`` / ``created_at``, because nothing
    that reads a sibling list reads those either — see
    :class:`ConformerObservationSiblingSummary`.
    """

    conformer_observation_id: int | None = None
    conformer_observation_ref: str
    review: RecordReviewBadge


class ConformerObservationSiblingSummary(BaseModel):
    """One sibling in a conformer-observation's basin, list-shaped.

    Populated under ``include=observations`` on the
    **conformer-observation** detail surface only — i.e. it is the type
    of ``ScientificConformerObservationRecord.observations``, which
    exists to answer the one question an observation-grained record
    cannot answer about itself: what else is in this basin. See
    :class:`ConformerObservationGroupSummary` for the analogous, but
    slightly richer, projection the **conformer-group** surface embeds
    under its own ``observations`` include by default.

    Before this type existed, a sibling was built as a full
    :class:`ScientificConformerObservationRecord` with every include
    token minus ``observations`` — its own calculations, geometries,
    selections, review history and evidence summary, and a *duplicate*
    copy of the parent group's core block and species context. For a
    basin the UI renders as three ``<li>`` elements (ref + review pill),
    that scaled the response linearly with basin size: one measured
    request came back at 22.8 KB, 90%+ of it the sibling block (issue
    #269). A sibling in a list carries what the list needs; the full
    record for any one sibling is one hop away, at that sibling's own
    ``GET /scientific/conformer-observations/{ref}``.
    """

    conformer_observation: ConformerObservationSiblingCore


class ConformerObservationGroupSummary(BaseModel):
    """One embedded observation under a conformer **group**'s
    ``observations`` block, by default.

    Populated under ``include=observations`` on the
    **conformer-group** detail (and search) surface whenever
    ``observation_details`` is *not* also requested — see
    :func:`app.services.scientific_read.conformers.build_group_record`.
    Carries what a *list* of a basin's observations needs: identity
    (``conformer_observation_ref``), review status, ``scientific_origin``
    (computed / experimental / estimated) and ``note`` — the four facts
    the group page's own observation cards render regardless of whether
    an observation has any calculations at all. Deliberately richer than
    :class:`ConformerObservationSiblingCore` (which drops
    ``scientific_origin``/``note`` because nothing that reads a sibling
    reads those either): this projection has a different reader with a
    different need, not the same one duplicated.

    What it does **not** carry is calculations, geometries, selections,
    review history or its own evidence summary — before ``observation_
    details`` existed as an explicit opt-in, every embedded observation
    was a full :class:`ScientificConformerObservationRecord` (every
    include token minus ``observations``), so a caller requesting
    ``include=observations,selections,review`` on the group surface got
    every one of those sections duplicated onto every observation, never
    requested them there, and paid a cost that multiplied with basin
    size without ever asking for it (issue #537, following the same
    shape #269/PR #535 fixed on the observation-sibling case above). A
    caller that genuinely needs an observation's own calculations and
    geometries inline — the group page's own use — asks for both
    explicitly: ``include=observations,observation_details,calculations,
    geometries``, which builds :class:`ScientificConformerObservationRecord`
    entries instead, still gated by whichever of ``calculations`` /
    ``geometries`` / ``selections`` / ``review`` the caller also named.
    """

    conformer_observation: ConformerObservationCoreBlock


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

    ``formula`` is derived server-side (RDKit cartridge, Hill notation)
    from ``canonical_smiles`` and is ``null`` only if that SMILES fails
    to parse — see ``app.services.scientific_read.species._formula_expr``
    for the same derivation used elsewhere on the read surface.
    """

    species_id: int | None = None
    species_ref: str
    species_entry_id: int | None = None
    species_entry_ref: str
    species_entry_label: str | None = None
    formula: str | None = None
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
    #: Every calculation row anchored to this basin's observations, the
    #: coarse stage of a two-stage optimisation included. Deliberately a
    #: **row** count and not an evidence count: it is the length of the
    #: list ``include=calculations`` returns, and a count that disagreed
    #: with the list it counts would be a worse defect than the one
    #: ``optimization_chain_count`` exists to fix. Read it as inventory —
    #: "how many jobs are on file" — never as "how much independent
    #: evidence".
    calculation_count: int
    evidence_coverage: ConformerEvidenceCoverage
    #: Optimisations behind this basin, counted as **chains** rather than
    #: rows: a coarse pre-optimisation joined to its refinement by
    #: ``calculation_dependency.dependency_role = 'optimized_from'``
    #: contributes ``1``, not ``2``. This is the block's answer to "how
    #: many independent geometry optimisations back this basin", which
    #: neither ``calculation_count`` (inventory, counts both stages) nor
    #: ``evidence_coverage.opt`` (observations covered, so a basin with
    #: nine optimisations on one observation still reads ``1``) can give.
    #:
    #: Only ``optimized_from`` collapses. A frequency job on an optimised
    #: geometry, or a single point at it, is *different* evidence from the
    #: optimisation and is never folded into it.
    optimization_chain_count: int
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
    #: included, projected as a lean ref+review summary rather than a
    #: full nested record — see :class:`ConformerObservationSiblingSummary`
    #: for why (issue #269). Populated under ``include=observations``,
    #: which on an observation-grained record is the one question the
    #: record cannot answer from itself: *what else is in this basin*.
    #: This field only exists on the top-level record returned by the
    #: observation detail endpoint; nothing ever nests a
    #: ``ScientificConformerObservationRecord`` inside this list (unlike
    #: ``ScientificConformerGroupRecord.observations``, which does, and
    #: keeps the full shape because its consumer needs each observation's
    #: own calculations/geometries).
    observations: list[ConformerObservationSiblingSummary] | None = None
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

    ``observations`` is one of two shapes depending on whether
    ``observation_details`` is also requested alongside ``observations``
    (see :func:`app.services.scientific_read.conformers.build_group_record`
    and :class:`ConformerObservationGroupSummary`): a lean per-observation
    summary by default, or the full :class:`ScientificConformerObservationRecord`
    — with its own calculations/geometries/selections/review history — only
    when a caller deliberately opts into the heavier shape. This is the
    fix for issue #537: previously ``observations`` was unconditionally
    the full shape, so every include token requested at the group level
    cascaded onto every embedded observation as well, multiplying with
    basin size.
    """

    conformer_group: ConformerGroupCoreBlock
    species: ConformerSpeciesContext
    observations_summary: ConformerObservationsSummary
    selection_summary: list[ConformerSelectionSummary] = Field(default_factory=list)
    evidence_summary: ConformerGroupEvidenceSummary
    available_sections: AvailableConformerSections

    # Optional include blocks
    observations: (
        list[ConformerObservationGroupSummary]
        | list[ScientificConformerObservationRecord]
        | None
    ) = None
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
    "ConformerGroupFingerprint",
    "ConformerObservationCoreBlock",
    "ConformerObservationDetailRequest",
    "ConformerObservationEvidenceSummary",
    "ConformerObservationGroupSummary",
    "ConformerObservationsSummary",
    "ConformerReviewEntry",
    "ConformerRotorTorsion",
    "ConformerSelectionSummary",
    "ConformerSpeciesContext",
    "RequestEcho",
    "ScientificConformerGroupDetailResponse",
    "ScientificConformerGroupRecord",
    "ScientificConformerObservationDetailResponse",
    "ScientificConformerObservationRecord",
]
