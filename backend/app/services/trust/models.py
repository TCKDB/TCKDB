"""Pydantic models and constants used by the deterministic trust layer.

The model layer here mirrors the concepts defined in
``backend/docs/specs/automated_trust_layer.md`` §5:

* :class:`EvidenceCheckSpec` is the *declaration* of a single check
  (name, kind, weight, explanation) inside a rubric.
* :class:`EvidenceCheckResult` is the *runtime outcome* of evaluating
  one such check against one record.
* :class:`EvidenceEvaluation` is the aggregated result of running every
  check in a rubric against one record.
* :class:`EvidenceRubric` is the versioned, code-defined bundle of
  checks (per §5.2).
* :class:`TrustFragment` is the JSON shape returned under ``trust:`` on
  scientific reads (per §10.1).

The module also centralises the deterministic label thresholds
(:func:`label_from_completeness`) so any future rubric inherits the
same mapping unless it explicitly overrides it.

None of the names in this module are allowed to drift toward
"quality score" framing. The metric is *evidence completeness*; the
label is the *evidence badge*. A rubric never produces
``is_certified=True``; that flag is reserved for curator action and is
always emitted as ``False`` by the evaluator.
"""

from __future__ import annotations

from enum import Enum
from typing import Callable, Optional

from pydantic import BaseModel, ConfigDict, Field


class EvidenceBadge(str, Enum):
    """Human-facing evidence-completeness label.

    Mirrors §5.4 / §6.1 of the spec. ``hard_failed`` is the only value
    that may be produced independent of the completeness ratio — it is
    triggered by a discrete structural failure signal (see
    :class:`HardFailReason`).
    """

    well_supported = "well_supported"
    mostly_supported = "mostly_supported"
    partial = "partial"
    sparse = "sparse"
    unsupported = "unsupported"
    hard_failed = "hard_failed"


class EvidenceOutcome(str, Enum):
    """Outcome of running a single :class:`EvidenceCheckSpec` against a record.

    * ``passed`` — the check's condition held; positive evidence.
    * ``missing`` — the check applies and did not pass; the explanation
      is surfaced as ``checks[<name>] == "missing"``.
    * ``warning`` — the check applies, the underlying signal is tri-state
      (typically a :class:`ValidationStatus.warning`), and the result
      is informational only. Warnings never reduce the completeness
      ratio (warning-kind checks contribute zero weight).
    * ``not_applicable`` — the check's ``applies_when`` predicate is
      false; the check is excluded from both numerator and denominator.
    """

    passed = "passed"
    missing = "missing"
    warning = "warning"
    not_applicable = "not_applicable"


class EvidenceCheckKind(str, Enum):
    """Classification of a check inside a rubric (per §5.1).

    * ``required`` — failure prevents reaching ``well_supported``.
    * ``optional`` — contributes to completeness but absence does not
      block any label.
    * ``warning`` — informational only; contributes zero weight.
    """

    required = "required"
    optional = "optional"
    warning = "warning"


class HardFailReason(str, Enum):
    """Discrete structural failure signals for deterministic evidence rubrics.

    Hard fails override the completeness ratio (§8) and force the
    rubric output into the ``hard_failed`` family. Names are stable
    identifiers; ``explain`` strings in
    :class:`EvidenceEvaluation.hard_fail_reason` may be richer.

    **Backstop reasons — do not delete these as "duplication."**

    Six of these reasons re-derive a rule the upload tier already refuses:

    =============================================  ==========================================================
    Reason                                          Already refused at upload by
    =============================================  ==========================================================
    ``invalid_lj_pair``                             ``transport_upload.validate_lj_pair``
    ``no_transport_property_present``               ``transport_upload.validate_has_scientific_content``
    ``no_thermo_representation_present``            ``thermo_upload.validate_has_scientific_content``
    ``invalid_external_symmetry``                   ``statmech_upload``: ``external_symmetry: Field(ge=1)``
    ``invalid_torsion_dimension``                   ``statmech_upload``: ``dimension: Field(ge=1)``
    ``multiplicity_invalid``                        ``transition_state_upload``: ``multiplicity: Field(ge=1)``
    =============================================  ==========================================================

    That looks like pointless duplication, and reading it that way invites
    someone to delete the read-time half. It is not duplication. Under ADR 0008
    the upload tier *owns* each of these rules; these reasons exist to catch
    records that never went through the upload tier at all — archive restore,
    data migrations, bulk importers, and direct SQL. That path is real: an
    archive-restore defect was found in this repository recently, and a record
    that entered that way has been validated by nothing.

    The practical consequence for whoever reads a report: when one of these six
    fires, it is **not a routine grading outcome**. Every path that can create
    such a record through the API already rejects it, so the record is evidence
    of a data-integrity problem — a restore, migration, or importer that wrote
    a row the upload schema would have refused. Treat it as an incident to
    trace back to its ingestion path, not as a record that merely scored badly.
    (Contrast ``sparse``/``unsupported``, which mean exactly "incomplete but
    true" and are expected in normal operation.)

    Do not "reconcile" these away by deleting either half. If a rule here ever
    diverges from its upload-tier owner, the upload tier is authoritative and
    this one is the copy that must be corrected.
    """

    calculation_missing = "calculation_missing"
    calculation_rejected = "calculation_rejected"
    kinetics_missing = "kinetics_missing"
    statmech_missing = "statmech_missing"
    thermo_missing = "thermo_missing"
    transport_missing = "transport_missing"
    species_entry_missing = "species_entry_missing"
    no_thermo_representation_present = "no_thermo_representation_present"  # backstop
    no_transport_property_present = "no_transport_property_present"  # backstop
    invalid_lj_pair = "invalid_lj_pair"  # backstop
    invalid_temperature_range = "invalid_temperature_range"
    invalid_external_symmetry = "invalid_external_symmetry"  # backstop
    invalid_torsion_dimension = "invalid_torsion_dimension"  # backstop
    geometry_validation_failed = "geometry_validation_failed"
    #: TCKDB has recorded that the bytes behind one of this calculation's
    #: artifacts do not match their content-addressed digest.
    #:
    #: Not a backstop and not a grading outcome — it is a statement about
    #: *TCKDB's* custody of the evidence, not about the depositor's
    #: science. Every other reason on this list says the record is
    #: internally wrong; this one says the record may be fine and we can
    #: no longer show you what it rests on. It is a hard fail because the
    #: alternative is to keep serving a completeness score computed over
    #: evidence we cannot produce.
    #:
    #: Driven by ``artifact_integrity_event`` rows, so it asserts only
    #: what has actually been *detected*. Absence of this reason is not a
    #: verification claim: an artifact nobody has read has been checked
    #: by nothing. See ADR 0014 and
    #: ``backend/scripts/ops/verify_artifact_integrity.py``.
    artifact_integrity_failed = "artifact_integrity_failed"
    missing_required_identity = "missing_required_identity"
    source_calculation_hard_failed_for_required_role = (
        "source_calculation_hard_failed_for_required_role"
    )
    transition_state_entry_missing = "transition_state_entry_missing"
    transition_state_parent_missing = "transition_state_parent_missing"
    reaction_entry_missing = "reaction_entry_missing"
    ts_entry_status_rejected = "ts_entry_status_rejected"
    multiplicity_invalid = "multiplicity_invalid"  # backstop
    all_source_calculations_hard_failed = "all_source_calculations_hard_failed"
    geometry_validation_failed_for_source_calculation = (
        "geometry_validation_failed_for_source_calculation"
    )
    frequency_source_has_zero_imaginary_modes_for_validated_ts = (
        "frequency_source_has_zero_imaginary_modes_for_validated_ts"
    )
    #: The stored record reports more than one imaginary mode and does
    #: not say which one is the reaction coordinate.
    #:
    #: This replaced ``frequency_source_has_multiple_imaginary_modes_for_validated_ts``,
    #: which ADR 0008 §9 named as a duplicate of the upload-time rule and
    #: ADR 0012 turned into a live contradiction: a record accepted with
    #: a warning at upload was hard-failed at read time by the surviving
    #: copy of the retired count-based gate. The question asked here is
    #: no longer a question about physics — "is n_imag one?" — but about
    #: persisted state: does the record carry the designation the
    #: blocking tier requires? A record that passed upload validation
    #: always does, so the two tiers can no longer disagree.
    frequency_source_reaction_coordinate_not_designated_for_validated_ts = (
        "frequency_source_reaction_coordinate_not_designated_for_validated_ts"
    )


class EvidenceCheckSpec(BaseModel):
    """Declaration of a single deterministic check inside a rubric.

    A rubric's check set is built from a list of these. Each spec is
    paired with a pure ``runner`` callable that takes the loaded record
    graph and returns an :class:`EvidenceOutcome`. Runners must not
    issue their own database queries — that constraint is what keeps
    the evaluator deterministic and free of N+1 surprises (see §12 of
    the spec).
    """

    model_config = ConfigDict(arbitrary_types_allowed=True, frozen=True)

    name: str = Field(..., description="Stable identifier for the check.")
    kind: EvidenceCheckKind = Field(
        ...,
        description="required / optional / warning — controls weight and label gating.",
    )
    weight: int = Field(
        default=1,
        ge=0,
        description=(
            "Numerator/denominator weight when the check is required or optional. "
            "Warning-kind checks always contribute zero weight regardless of value."
        ),
    )
    explain: str = Field(
        default="",
        description="Short human string explaining a missing or warning outcome.",
    )
    runner: Callable[..., EvidenceOutcome] = Field(
        ...,
        description="Pure callable: (record, *, context) -> EvidenceOutcome.",
    )


class EvidenceCheckResult(BaseModel):
    """Runtime outcome of evaluating one :class:`EvidenceCheckSpec`.

    Carries both the outcome and the originating spec metadata so the
    aggregator can build the deterministic check map and the weighted
    counts without re-deriving anything.
    """

    model_config = ConfigDict(frozen=True)

    name: str
    outcome: EvidenceOutcome
    kind: EvidenceCheckKind
    weight: int
    explain: Optional[str] = None

    @property
    def contributes_weight(self) -> bool:
        """Return True when this check is part of the completeness ratio.

        Warning-kind checks and ``not_applicable`` outcomes never
        contribute weight, matching §5.5 of the spec.
        """
        if self.kind is EvidenceCheckKind.warning:
            return False
        return self.outcome is not EvidenceOutcome.not_applicable


class EvidenceRubric(BaseModel):
    """Versioned bundle of checks tied to a record type (§5.2).

    A record can match at most one rubric per evaluator call (selection
    happens in :mod:`app.services.trust.rubrics`); the evaluator must
    raise rather than guess on ambiguity.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True, frozen=True)

    name: str = Field(..., description="Rubric name, e.g. computed_calculation.")
    version: int = Field(..., ge=1, description="Integer version of this rubric.")
    record_type: str = Field(
        ...,
        description="Discriminator for the kind of record this rubric applies to.",
    )
    checks: tuple[EvidenceCheckSpec, ...] = Field(
        ...,
        description="Ordered tuple of check specs. Order controls report stability.",
    )

    @property
    def qualified_name(self) -> str:
        """Return ``<name>@v<version>`` for telemetry and logs."""
        return f"{self.name}@v{self.version}"


class EvidenceEvaluation(BaseModel):
    """Aggregated result of running a rubric against one record.

    Field semantics:

    * ``checks`` — an ordered map from check name to the outcome that
      check produced. See the note below on why this is a map and not
      four name lists.
    * ``passed_count`` / ``possible_count`` — the numerator and
      denominator of :attr:`evidence_completeness`. ``possible_count``
      excludes ``not_applicable`` and warning-kind checks.
    * ``evidence_completeness`` — ``passed_weight / possible_weight``,
      rounded to four decimals. Never exposed as a percentage by the
      evaluator (see §6 of the spec).
    * ``is_certified`` — always ``False`` for automated evaluations.
      Reserved for curator action (§6.2).
    * ``hard_fail_reason`` — populated only when a structural hard-fail
      signal forces ``label = hard_failed``.

    **Why ``checks`` is a map, not four name lists.** Until this shape
    landed, the outcomes were four parallel tuples —
    ``passed_checks`` / ``missing_checks`` / ``warning_checks`` /
    ``not_applicable_checks`` — and a reader had to combine the bucket
    name with the check name to get the meaning. Because most check
    names are assertions ending in ``_present``, the common case read
    as a double negative: ``missing_checks: ["irc_evidence_present"]``
    means *there is no IRC evidence*. The name said "present", the
    bucket said "missing", and the reader had to negate one against the
    other every single time. This fragment is read by humans at least
    as often as by machines, so that cost was being paid constantly.

    Moving the bucket name into the value removes the negation:
    ``"irc_evidence_present": "missing"`` reads as written. It also
    turns *"did check X pass?"* into one lookup instead of a scan
    across up to four arrays. Nothing else moved: this is a
    serialisation shape change, and every check produces exactly the
    outcome it produced before.

    **Four outcomes, never a boolean.** The value is an
    :class:`EvidenceOutcome`, and collapsing it to true/false would be
    a correctness bug, not a simplification. ``missing`` means the
    check applied and did not pass — it counts against the record and
    is inside ``possible_count``. ``not_applicable`` means the check's
    ``applies_when`` predicate was false — the question could not be
    asked, and the check is excluded from *both* the numerator and the
    denominator. A live example: when
    ``geometry_validation_present_for_source_calculations`` is
    ``missing`` there is no geometry validation to inspect, so
    ``geometry_validation_not_failed_for_source_calculations`` is
    ``not_applicable`` — "did it fail?" has no answer. Rendering that
    as ``false`` would penalise a record for a question nobody could
    ask.

    **Ordering is the rubric's declared check order, and is stable.**
    Insertion order follows :attr:`EvidenceRubric.checks`, so two
    evaluations of the same rubric serialise their keys in the same
    order and a diff between two records lines up check-for-check. It
    is not arbitrary and consumers may rely on it.

    **Membership matches the old buckets exactly.** A check appears in
    the map with the outcome whose bucket it used to occupy, and only
    then. The one class of check that is absent is a warning-kind check
    that did not fire, which was equally absent from all four arrays:
    warning-kind checks carry zero weight, so counting a non-fired one
    as ``passed`` would make the number of ``passed`` values disagree
    with :attr:`passed_count`. Consequently
    ``passed_count == len([n for n, o in checks.items() if o is passed])``
    still holds, as it did for ``len(passed_checks)``.
    """

    model_config = ConfigDict(frozen=True)

    record_type: str
    record_id: Optional[int]
    rubric: str
    rubric_version: int
    label: EvidenceBadge
    checks: dict[str, EvidenceOutcome] = Field(
        ...,
        description=(
            "Ordered map of check name -> outcome, in the rubric's declared "
            "check order. Replaces the former passed/missing/warning/"
            "not_applicable name lists."
        ),
    )
    passed_count: int
    possible_count: int
    evidence_completeness: float
    is_certified: bool = False
    hard_fail_reason: Optional[HardFailReason] = None
    check_results: tuple[EvidenceCheckResult, ...] = Field(default_factory=tuple)


class TrustLLMPrecheck(BaseModel):
    """Advisory LLM precheck metadata surfaced under ``trust.llm_precheck``.

    Default in this MVP is ``enabled=False`` and ``label='not_run'``
    because no LLM is wired in; if/when the precheck event is
    available, the read serializer can populate this without altering
    the rubric output.
    """

    model_config = ConfigDict(frozen=True)

    enabled: bool = False
    label: str = "not_run"
    summary: Optional[str] = None


class TrustFragment(BaseModel):
    """Read-fragment shape per §10.1 of the spec.

    Built from an :class:`EvidenceEvaluation` plus the record's
    :class:`~app.db.models.record_review.RecordReview` row (when
    present). The evaluator itself returns only the evaluation; the
    fragment is composed in the read serializer where the review row
    and any LLM-precheck audit event are already in scope.
    """

    model_config = ConfigDict(frozen=True)

    review_status: str = "not_reviewed"
    trust_status: str
    evidence: dict
    llm_precheck: TrustLLMPrecheck = Field(default_factory=TrustLLMPrecheck)
    is_certified: bool = False


COMPLETENESS_THRESHOLDS: tuple[tuple[float, EvidenceBadge], ...] = (
    (0.90, EvidenceBadge.well_supported),
    (0.75, EvidenceBadge.mostly_supported),
    (0.50, EvidenceBadge.partial),
    (0.25, EvidenceBadge.sparse),
)
"""Default thresholds per §6.1 of the spec, in descending order.

``well_supported`` additionally requires every ``required`` check to
have passed. Sub-``sparse`` ratios collapse to ``unsupported``.
"""


def label_from_completeness(
    completeness: float,
    *,
    all_required_passed: bool,
) -> EvidenceBadge:
    """Map a completeness ratio plus the required-checks signal to a badge.

    The mapping is deterministic and centralised so every rubric
    inherits the same thresholds unless it deliberately overrides
    them. ``all_required_passed`` is the gate documented in §6.1: a
    record cannot reach ``well_supported`` while any required check
    still fails, regardless of how strong the ratio is.
    """
    if completeness >= 0.90 and all_required_passed:
        return EvidenceBadge.well_supported
    for threshold, badge in COMPLETENESS_THRESHOLDS:
        if completeness >= threshold and badge is not EvidenceBadge.well_supported:
            return badge
    return EvidenceBadge.unsupported
