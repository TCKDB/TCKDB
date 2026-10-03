"""Shared read fragments and request models for the /api/v1/scientific/* layer.

Defined once here, imported by the per-endpoint scientific read modules so the
fragment shapes (review summary, provenance summary, evidence breakdown,
temperature coverage, pagination) stay consistent across endpoints.

See docs/specs/read_api_mvp.md for the canonical contract.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, computed_field

from app.db.models.common import (
    CompositeAssembly,
    CompositeEnergyVerificationState,
    CompositeSchemeKind,
    CoreTreatment,
    LegacyCompositeShape,
    ProfileRecommendation,
    ReadProfile,
    RecordReviewStatus,
    SpinTreatment,
)

# ---------------------------------------------------------------------------
# Request-side enums and shared knobs
# ---------------------------------------------------------------------------


class ProfiledRequestEcho(BaseModel):
    """Base class for every ``/scientific/*`` response's ``request`` echo.

    Every scientific response envelope carries a ``request`` block, and every
    such block subclasses this, so the resolved read profile is part of the
    published OpenAPI contract on **all** endpoints rather than something a
    consumer has to hope was applied.

    The values are stamped at the response boundary from the profile resolved
    once per request (``app/api/routes/scientific/_response.py``), not copied
    field-by-field at ~63 service construction sites — the defaults below are
    the correct answer for any caller who did not opt in, and they are also
    what unit tests that build a response object directly will see.

    ``profile``                 which contract the response answers under.
    ``profile_recommendation``  whether TCKDB endorses these records. Never
                                inferred from ``profile`` alone: a curated
                                read of a database with no published release
                                still reports ``none``.
    ``profile_release_ref``     the dataset release backing a curated read,
                                when one exists.

    All three are machine tokens. The prose explanation lives in
    ``backend/docs/specs/dataset_release_and_profiles.md``, never in the value.
    """

    profile: ReadProfile = ReadProfile.exploratory
    profile_recommendation: ProfileRecommendation = ProfileRecommendation.none
    profile_release_ref: str | None = None


class CollapseMode(str, Enum):
    """Collapse axis values per spec D4 / Phase 2.1.

    ``all``    return every eligible record after filter/sort/pagination.
    ``first``  return at most one record (zero or one) after filter and sort.
    """

    all = "all"
    first = "first"


class SelectionPolicy(str, Enum):
    """Named, read-time selection policy used when ``collapse=first``.

    A selection policy makes "show me one product for this species form" an
    *explicit, named* choice rather than an implicit one. Policies rank
    candidate products at read time only — none persists a curator decision.

    Policies that would require a stored choice (``benchmark_reference``,
    ``curator_pick``) are intentionally absent: they need the deferred
    product-selection persistence layer, not a read knob, and cannot be
    honestly evaluated from the record data alone. See
    ``backend/docs/specs/scientific_product_candidacy.md``.

    ``default``        the endpoint's standard ranking.
    ``latest``         most recently created first.
    ``most_reviewed``  best review status first, then most recent.
    """

    default = "default"
    latest = "latest"
    most_reviewed = "most_reviewed"


# Map review status to ranking key per L2. Lower wins.
#
# ``under_review`` ranks above ``not_reviewed`` and stays there. Before
# revision ``c1d8f4a25b30`` that looked wrong, because every deposited record
# was stamped ``under_review`` and the rank was sorting the whole corpus above
# an empty set. The label was the defect, not the order: ``under_review`` now
# means a curator has the record open, and a record someone is looking at is a
# better bet than one nobody has touched. Do not reorder these two.
REVIEW_RANK: dict[RecordReviewStatus, int] = {
    RecordReviewStatus.approved: 0,
    RecordReviewStatus.under_review: 1,
    RecordReviewStatus.not_reviewed: 2,
    RecordReviewStatus.deprecated: 3,
    RecordReviewStatus.rejected: 4,
}


# ---------------------------------------------------------------------------
# Pagination
# ---------------------------------------------------------------------------


DEFAULT_LIMIT = 50
MAX_LIMIT = 200


class Pagination(BaseModel):
    """Echoed pagination block per L5.

    ``total``                pre-collapse, post-filter match count.
    ``post_collapse_total``  count after collapse, before page slicing.
    ``returned``             actual length of ``records``.
    """

    offset: int = Field(ge=0)
    limit: int = Field(ge=1, le=MAX_LIMIT)
    returned: int = Field(ge=0)
    total: int = Field(ge=0)
    post_collapse_total: int = Field(ge=0)


# ---------------------------------------------------------------------------
# Review fragments
# ---------------------------------------------------------------------------


class ReviewStatusSummary(BaseModel):
    """Counts per review status across a candidate record set (pre-collapse)."""

    approved: int = 0
    under_review: int = 0
    not_reviewed: int = 0
    deprecated: int = 0
    rejected: int = 0
    total: int = 0


class RecordReviewBadge(BaseModel):
    """Single record's direct review state — no chain traversal (D7).

    ``note`` is the curator's stated reason, and it is **public**. A status
    on its own tells a reader that somebody formed a view without saying
    what it was: a record reading ``under_review`` with no reason is a
    warning a reader cannot act on. The note is what makes the status
    usable, so it is projected alongside it rather than held back.

    Consequence for whoever writes one: a review note is reader-facing
    prose, not an internal remark. Say what was checked, what was found,
    and what a consumer of this record should do differently. Do not put
    anything in it you would not publish.
    """

    status: RecordReviewStatus
    reviewed_at: datetime | None = None
    reviewer_kind: Literal["human", "automated", "system"] | None = None
    note: str | None = None


# ---------------------------------------------------------------------------
# Supersession notice
# ---------------------------------------------------------------------------


class SupersessionNotice(BaseModel):
    """Correction notice attached to a record that has been replaced.

    Why this exists
    ---------------
    TCKDB never rewrites an accepted scientific record. A correction is a
    *new* record plus an immutable
    ``scientific_record_supersession`` edge saying "this was replaced by
    that, here is why, by whom, when". Keeping the old row byte-identical is
    what makes an existing citation keep resolving.

    Resolving is not enough. A citation that 404s announces its own problem;
    a citation that resolves cleanly to a *superseded* number looks healthy,
    so nobody investigates. Findable-and-unmarked is precisely the failure
    the append-only design was meant to prevent, so every read of a
    superseded record carries this block — unconditionally, not behind an
    ``include=`` token. See ADR 0003 (frozen rows) and ADR 0007 (no stored
    ``is_current``).

    Two pointers, deliberately
    --------------------------
    ``superseded_by``  the **immediate** successor. Truthful about the one
                       edge that was recorded, and preserves the history: a
                       reader walking A → B → C sees the real steps.
    ``current``        the **head** of the chain — what a reader actually
                       wants to follow. For a one-link chain the two are
                       equal; for A → B → C a read of A reports
                       ``superseded_by=B``, ``current=C``.

    Neither pointer is stored. The database holds only the chain of edges;
    the head is computed per read. Storing the head would mean ``UPDATE``-ing
    every earlier record whenever a new correction lands — forbidden by the
    accepted-science immutability triggers, and the same second-source-of-
    truth defect ADR 0007 rejected as ``is_current``. Appending a correction
    is one ``INSERT``, and every read of every earlier record reports the new
    head immediately because it was never written down.

    ``reason`` and ``superseded_at`` describe the **immediate** edge, matching
    ``superseded_by``. Whoever recorded the edge is deliberately not named
    here: the read API does not attribute curation actions to a person.

    Both pointers are public refs of the *records* (``thm_…``, ``kin_…``),
    never of the supersession edge, and never a database row id
    (DR-0028 Req 2).
    """

    superseded_by: str
    current: str
    reason: str
    superseded_at: datetime
    #: Number of recorded edges between this record and ``current``. ``1``
    #: means ``superseded_by`` *is* the head; ``>1`` means this record has
    #: been corrected more than once since. A client that only wants "is
    #: there anything newer than my immediate successor" can read this
    #: instead of walking the chain itself.
    chain_length: int = Field(ge=1)


# ---------------------------------------------------------------------------
# Level of theory / software / workflow tool / literature summaries
# ---------------------------------------------------------------------------


class CompositeSchemeSummary(BaseModel):
    """The composite recipe a level of theory is bound to (ADR 0021).

    ``composite_scheme_ref`` is the handle for
    ``GET /scientific/composite-schemes/{ref}``, which returns the recipe's
    terms and inputs. Refs only: no database id.
    """

    composite_scheme_ref: str
    kind: CompositeSchemeKind
    name: str
    #: The ref of the level of theory the recipe runs its geometry at (for CBS-QB3,
    #: B3LYP/CBSB7), or ``None`` when the recipe does not state one -- never "the
    #: energy level". Lets a reader tell whether a record's geometry level is the
    #: recipe's own, which is what ``ScientificLevelsSummary.notation`` needs.
    geometry_level_of_theory_ref: str | None = None


class CompositeEnergyVerification(BaseModel):
    """How far a composite energy has been checked (ADR 0021, P7a). Derived at read time, never stored.

    ``state``
        * ``recomputed`` -- an ``assembled`` composite whose stored inputs, run
          through its scheme *at read time*, give its stated total within the
          weighted tolerance. Recomputed on every read, so an input energy
          deposited later is picked up and nothing about the check is stale.
        * ``recompute_mismatch`` -- the same recomputation disagrees. Possible when
          an input changed after the composite was accepted. Surfaced, never hidden;
          ``difference_hartree`` (stated minus recomputed) and ``tolerance_hartree``
          say by how much.
        * ``log_reconciled`` -- a ``program_run`` whose attached output log was
          compared at upload and confirmed every number the deposit stated.
        * ``program_reported`` -- a ``program_run`` with no confirming log: no log was
          attached, or one was attached that could not confirm it (see ``reason``).
        * ``unverifiable`` -- the check cannot be made: an input or component is
          missing, the triples convention of an input cannot be determined, or no
          energy was stated at all (see ``reason``).

    ``reason`` is a stable machine token, ``None`` when there is nothing to add.
    The recomputed total itself is never returned or stored; only its distance from
    the stated one is.
    """

    state: CompositeEnergyVerificationState
    assembly: CompositeAssembly
    reason: str | None = None
    difference_hartree: float | None = None
    tolerance_hartree: float | None = None


class LevelOfTheorySummary(BaseModel):
    """Lightweight LoT shape used in scientific provenance summaries.

    Phase B: ``level_of_theory_ref`` is the public stable handle; the
    existing ``level_of_theory_id`` integer stays for the compatibility
    window. See ``docs/specs/public_identifier_policy.md``.
    """

    level_of_theory_id: int
    level_of_theory_ref: str
    method: str
    basis: str | None = None
    dispersion: str | None = None
    solvent: str | None = None
    #: Restricted / unrestricted / restricted-open (DR-0034). ``None`` means
    #: unspecified by the producer, distinct from the enum's own explicit
    #: ``unknown`` member -- see ``LevelOfTheory.spin_treatment``. Part of
    #: LOT identity and folded into ``lot_hash``; not builders' to omit.
    spin_treatment: SpinTreatment | None = None
    #: Frozen-core or all-electron (ADR 0021). ``None`` means the producer did
    #: not state it, never "frozen core by default". Part of LOT identity only
    #: when set. Every builder passes it explicitly
    #: (``tests/invariants/test_level_summary_carries_composite_scheme.py``).
    core_treatment: CoreTreatment | None = None
    label: str | None = None
    #: The composite recipe this level names, or ``None`` when the level is an
    #: ordinary one. Bound by TCKDB for a catalogued named composite method
    #: (CBS-QB3, G4, ...). ``None`` never means "composite but unknown": an
    #: unbound level is simply not composite.
    composite_scheme: CompositeSchemeSummary | None = None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def display(self) -> str:
        """``method/basis`` — the way a chemist writes a level of theory.

        Derived, never stored and never accepted on input: it is exactly
        ``method`` and ``basis`` rendered the conventional way, and
        ``method`` alone when there is no basis (a semi-empirical or
        composite method has none, and ``"AM1/"`` would be a worse answer
        than ``"AM1"``).

        It exists because the fields it is built from are already here and
        a human reading the JSON should not have to assemble them. It is
        deliberately *not* an identity: two different
        ``level_of_theory`` rows can render the same string when they
        differ only in dispersion, solvent or spin treatment.
        ``level_of_theory_ref`` is the handle to compare on; ``display``
        is for reading. Nothing should key, group or deduplicate on it.
        """
        if self.basis:
            return f"{self.method}/{self.basis}"
        return self.method


class ScientificLevelsSummary(BaseModel):
    """Geometry / frequency / energy levels of theory, derived at read time.

    ``geometry``, ``frequency``, ``energy`` and ``energy_source`` are never
    stored, and never behind an ``include=`` token: recomputed on
    every read from whichever ``opt``/``freq``/``sp``/``composite``/
    ``imported`` source calculations the record links right now, per
    ``app.services.calculation_levels.derive_levels`` (R1):

    * ``geometry`` — the ``opt`` role's level of theory; when no ``opt``
      is linked, the internal geometry level of a linked program-run
      ``composite`` that has an output geometry of its own (its scheme's
      recipe level), ``geometry_source="composite_recipe"``.
    * ``frequency`` — the ``freq`` role's level, or the ``opt``'s own
      level when no separate ``freq`` is linked but the optimisation
      calculation itself carries frequency results; failing both, the
      internal frequency level of that composite's scheme
      (``frequency_source="composite_recipe"``).
    * ``energy`` — a linked ``composite``'s level when exactly one
      composite level is linked; otherwise the linked ``sp``s' shared
      level when they agree; otherwise the ``opt``'s own level (an
      optimisation's final energy *is* the single-point value at its own
      level of theory); otherwise a linked ``imported`` calculation's
      level; ``null`` when linked ``sp``s (or ``composite``s) disagree on
      level of theory (see ``energy_source="ambiguous"`` below).
    * ``energy_source`` names which role answered ``energy`` -- ``'composite'``,
      ``'sp'``, ``'opt'``, or ``'imported'`` -- or ``'ambiguous'``
      when two or more linked ``sp`` calculations (a multi-conformer
      ensemble's per-conformer single points, say) or two or more linked
      ``composite`` calculations run at different
      levels of theory, so no single energy level of theory can be
      reported for the record as a whole; or ``null`` when nothing
      linked can answer it.
    * ``geometry_source`` is ``'opt'`` or ``'composite_recipe'`` (or ``null``
      when ``geometry`` is); ``frequency_source`` is ``'freq'``, ``'opt'``
      or ``'composite_recipe'`` (or ``null`` when ``frequency`` is). A
      ``'composite_recipe'`` level is what the named method runs
      internally, stated by the method's catalogue entry, not a
      calculation anybody deposited.

    Any field may be ``null`` independently of the others: a record with
    only a ``freq`` link, for instance, reports a ``frequency`` level and
    ``geometry``/``energy`` both ``null``.

    ``notation`` is the chemist's shorthand for the two levels that matter to a
    number, derived from ``energy`` and ``geometry`` on every read and never
    stored (see :func:`levels_notation` for the exact rules).

    ``composite_energy_verification`` is set when the record's energy comes from
    a ``composite`` calculation (``energy_source="composite"``): how far that
    energy has been checked. ``null`` for every other source, and for a record
    whose composite energy is ``"ambiguous"``.

    ``legacy_composite_shape`` annotates a record built the way depositors did
    before the ``composite`` calculation type existed. It is an annotation only:
    the levels above are derived exactly as they always were.

    ``declared_energy`` is the one exception to "derived at read time": see
    its field comment.
    """

    geometry: LevelOfTheorySummary | None = None
    frequency: LevelOfTheorySummary | None = None
    energy: LevelOfTheorySummary | None = None
    energy_source: (
        Literal["sp", "opt", "composite", "imported", "ambiguous"] | None
    ) = None
    geometry_source: Literal["opt", "composite_recipe"] | None = None
    frequency_source: Literal["freq", "opt", "composite_recipe"] | None = None
    #: The level of theory the depositor *declared* for this record's
    #: energy (``energy_level_of_theory`` on the upload). Unlike the four
    #: fields above this one is **stored**, not derived: it is a claim made
    #: at upload time and checked then against the calculations linked at
    #: that time. ``null`` when nothing was declared -- including every
    #: record written before it was stored -- and never back-filled from
    #: ``energy``. A thermo record derived from a statmech record reports
    #: its own declaration when it has one, else that statmech record's.
    declared_energy: LevelOfTheorySummary | None = None
    #: See the class docstring. Every builder passes this explicitly
    #: (``tests/invariants/test_levels_summary_notation_and_verification.py``): the
    #: default ``None`` reads as "not a composite energy", so a builder that forgot it
    #: would silently report an unchecked composite as ordinary.
    composite_energy_verification: CompositeEnergyVerification | None = None
    #: See the class docstring. Explicit in every builder, like the field above.
    legacy_composite_shape: LegacyCompositeShape | None = None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def notation(self) -> str | None:
        """``energy//geometry`` written the way a chemist writes it; ``null`` when it cannot be.

        Derived from ``energy`` and ``geometry`` on every read; never stored,
        never accepted on input, and never passed by a builder (an AST invariant
        forbids it). See :func:`levels_notation`.
        """
        return levels_notation(energy=self.energy, geometry=self.geometry)


def levels_notation(*, energy: LevelOfTheorySummary | None, geometry: LevelOfTheorySummary | None) -> str | None:
    """The notation of a record's energy and geometry levels, e.g. ``CCSD(T)-F12/cc-pVTZ-F12//wB97X-D/def2-TZVP``.

    The rules, exactly (ADR 0021, plan section 2.6), applied in this order:

    * **Either level absent: no notation** (``None``). Never a partial one: ``x``
      alone would claim the geometry is at ``x`` too, which is a fact nobody
      stated. A record whose energy is ambiguous across levels has no energy level
      and so no notation.
    * **The same level for both** (compared by ref, not by text: two rows can render
      alike and differ in dispersion or solvent): that level written once. A level
      bound to a recipe is written as the recipe's label (``composite_scheme.name``),
      any other as ``method/basis`` (``method`` alone when it has no basis), the way
      :attr:`LevelOfTheorySummary.display` renders it.
    * **A composite energy level** (one bound to a recipe, ``energy.composite_scheme``
      set; the role that supplied it does not matter) on another level: the composite's
      label followed by ``//`` and the geometry level, **unless the geometry is the
      recipe's own**, when the label stands alone. The geometry is the recipe's own
      when it is the level the recipe runs internally
      (``composite_scheme.geometry_level_of_theory_ref``), compared by ref. When the
      recipe states no geometry level, no geometry is its own, so the geometry is
      written.
    * **Otherwise** ``energy//geometry``.

    The frequency level is not part of the notation.

    :param energy: The record's energy level.
    :param geometry: The record's geometry level.
    :returns: The notation, or ``None`` when either level is absent.
    """
    if energy is None or geometry is None:
        return None
    scheme = energy.composite_scheme
    if energy.level_of_theory_ref == geometry.level_of_theory_ref:
        return scheme.name if scheme is not None else energy.display
    if scheme is not None:
        own_geometry = scheme.geometry_level_of_theory_ref
        if own_geometry is not None and own_geometry == geometry.level_of_theory_ref:
            return scheme.name
        return f"{scheme.name}//{geometry.display}"
    return f"{energy.display}//{geometry.display}"


class SoftwareReleaseSummary(BaseModel):
    """Software release pointer used in provenance summaries."""

    software_release_id: int
    software_release_ref: str
    software: str
    version: str | None = None


class WorkflowToolReleaseSummary(BaseModel):
    """Workflow tool release pointer used in provenance summaries."""

    workflow_tool_release_id: int
    workflow_tool_release_ref: str
    workflow_tool: str
    version: str | None = None


class LiteratureSummary(BaseModel):
    """Minimal literature reference used in provenance summaries.

    A canonical literature read model lives in
    ``app/schemas/entities/literature.py`` (LiteratureRead). This summary is a
    deliberately smaller shape sufficient for scientific-read provenance,
    avoiding a heavier include for what is usually a sidebar fact.

    Phase B: ``literature_ref`` is the public stable handle; ``id`` stays
    for the compatibility window.
    """

    id: int
    literature_ref: str
    title: str | None = None
    year: int | None = None
    doi: str | None = None


# ---------------------------------------------------------------------------
# Calculation / validation fragments
# ---------------------------------------------------------------------------


# Geometry validation values per Phase 2.3 spec patch.
GeometryValidationStatus = Literal["passed", "warning", "fail", "not_present"]

# SCF stability values per Phase 2.3 spec patch.
SCFStabilityStatusValue = Literal[
    "stable", "unstable", "stabilized", "inconclusive", "not_present"
]


class ValidationSummary(BaseModel):
    """Geometry validation outcome for a single calculation.

    Phase B: ``calculation_ref`` is the public stable handle for the
    associated calculation; ``calculation_id`` stays for compatibility.
    """

    status: GeometryValidationStatus
    calculation_id: int
    calculation_ref: str | None = None


class SCFStabilitySummary(BaseModel):
    """SCF wavefunction stability outcome for a single calculation."""

    status: SCFStabilityStatusValue
    calculation_id: int
    calculation_ref: str | None = None


class CalculationEvidenceSummary(BaseModel):
    """Lightweight per-calculation summary embedded in provenance blocks."""

    calculation_id: int
    calculation_ref: str | None = None
    calculation_type: str
    converged: bool | None = None
    geometry_validation_status: GeometryValidationStatus
    scf_stability_status: SCFStabilityStatusValue
    level_of_theory: LevelOfTheorySummary | None = None
    software: SoftwareReleaseSummary | None = None


class PathSearchSummary(BaseModel):
    """Path-search calculation summary used in TS-backed kinetics provenance."""

    calculation_id: int
    calculation_ref: str | None = None
    method: str | None = None
    converged: bool | None = None


# ---------------------------------------------------------------------------
# Temperature coverage and evidence completeness
# ---------------------------------------------------------------------------


class TemperatureCoverage(BaseModel):
    """D8 verbatim — full-range coverage gate plus extrapolation distance.

    ``overlap_fraction`` is diagnostic only; per D8 it is never the primary
    sort score.
    """

    requested_min_k: float | None = None
    requested_max_k: float | None = None
    record_min_k: float | None = None
    record_max_k: float | None = None
    covers_requested_range: bool
    overlap_fraction: float | None = None
    extrapolation_distance_k: float


class EvidenceCompletenessBreakdown(BaseModel):
    """L1 — score plus auditable per-predicate checklist.

    The outer shape is stable across endpoints; the ``checklist`` keys are
    endpoint-specific. Use ``model_config(extra="allow")`` so each endpoint
    can attach its own checklist keys without redefining the model.
    """

    model_config = ConfigDict(extra="allow")

    score: int = Field(ge=0)
    max: int = Field(ge=0)
    checklist: dict[str, bool]


# ---------------------------------------------------------------------------
# Default-trust filter knobs
# ---------------------------------------------------------------------------


def default_visible_statuses(
    *, include_rejected: bool = False, include_deprecated: bool = False
) -> set[RecordReviewStatus]:
    """Set of statuses that should be visible by default per D5.

    Approved / under_review / not_reviewed are always visible. Rejected and
    deprecated are excluded unless explicitly opted in.
    """
    statuses = {
        RecordReviewStatus.approved,
        RecordReviewStatus.under_review,
        RecordReviewStatus.not_reviewed,
    }
    if include_rejected:
        statuses.add(RecordReviewStatus.rejected)
    if include_deprecated:
        statuses.add(RecordReviewStatus.deprecated)
    return statuses


def status_at_or_above(threshold: RecordReviewStatus) -> set[RecordReviewStatus]:
    """Set of statuses with ``review_rank <= threshold's rank`` (better or equal)."""
    threshold_rank = REVIEW_RANK[threshold]
    return {s for s, rank in REVIEW_RANK.items() if rank <= threshold_rank}


def simple_selection_sort_key(
    record_id: int,
    *,
    policy: SelectionPolicy,
    review_status_by_id: dict[int, RecordReviewStatus],
    created_at_by_id: dict[int, datetime],
) -> tuple:
    """Ranking key for review/recency-only candidate sets.

    Used by the per-species thermo, statmech and transport reads and by the
    export, so the read and the export pick the same record. Temperature
    coverage and evidence scores are displayed fields, not part of the key
    (#648). ``latest`` ranks purely by recency;
    ``default`` and ``most_reviewed`` rank by review status first (the
    historical per-species order). All policies break ties by created_at DESC
    then id DESC so the order is total and deterministic.
    """
    ts = created_at_by_id[record_id].timestamp()
    if policy is SelectionPolicy.latest:
        return (-ts, -record_id)
    rank = REVIEW_RANK[review_status_by_id[record_id]]
    return (rank, -ts, -record_id)
