"""The replayable decision manifest.

A selection is only as trustworthy as the inputs it can be re-derived from. The manifest
holds the normalised request, the effective review floor, every assessed candidate with its
normalised inputs and applicability verdict, the fronts and the administrative order. It is a JSON-ready dict,
built from public refs only: no
database id, no internal key. ``id_rank`` is an ordinal standing in for the id as the last
tie-break and cannot be turned back into one.

:func:`replay_decision` re-runs the ordering engine over the manifest's eligible candidates
with no database. A digest would only prove the manifest was not altered; replay proves the
decision follows from what it records. A manifest format, policy or assessment-semantics version that the
running registry does not carry refuses the replay rather than substituting another.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Any

from app.db.models.common import RecordReviewStatus
from app.schemas.reads.scientific_common import REVIEW_FLOOR_ORDER, SelectionPolicy
from app.services.manifest_formats import manifest_header, read_manifest_format
from app.services.selection_kernel import Applicability
from app.services.selection_kernel.requested import Eligibility, UnreadableEligibility, Verdict, judge
from app.services.selection_kernel.assessment_semantics import (
    UnsupportedAssessmentSemantics,
    check_supported,
    semantics_block,
)
from app.services.thermo_selection.engine import decide
from app.services.thermo_selection.models import (
    ASSESSMENT_SEMANTICS,
    ASSESSMENT_SEMANTICS_NAME,
    ASSESSMENT_SEMANTICS_VERSION,
    MANIFEST_FORMAT_NAME,
    MANIFEST_FORMATS,
    PHASE,
    POLICY_NAME,
    POLICY_VERSION,
    QUANTITY,
    REFERENCE_TEMPERATURE_K,
    CandidateAssessment,
    Decision,
    H298Request,
    NormalizedCandidate,
    Outcome,
    Subject,
)
from app.services.trust.rubrics import COMPUTED_THERMO
from app.versioning import same_value


class ReplayError(ValueError):
    """The manifest cannot be replayed against the running registry."""


def build_manifest(
    *,
    request: H298Request,
    conformer_group_ref: str | None,
    effective_statuses: Iterable[RecordReviewStatus],
    subject: Subject,
    total_rows: int,
    visible_candidates: int,
    cap: int,
    excluded_by_review: Sequence[dict[str, str]],
    candidates: Sequence[tuple[NormalizedCandidate, CandidateAssessment, Verdict]],
    decision: Decision | None,
    outcome: Outcome,
    funnel: dict[str, Any],
) -> dict[str, Any]:
    """Assemble the manifest. ``decision`` is ``None`` only for a refused (over-cap) search."""
    ordered = sorted(candidates, key=lambda item: item[0].id_rank)
    return {
        "format": manifest_header(MANIFEST_FORMATS, MANIFEST_FORMAT_NAME),
        "policy": {"name": POLICY_NAME, "version": POLICY_VERSION},
        "assessment_semantics": semantics_block(
            name=ASSESSMENT_SEMANTICS_NAME, version=ASSESSMENT_SEMANTICS_VERSION, evidence_rubric=COMPUTED_THERMO.ref
        ),
        "request": {
            "quantity": QUANTITY,
            "temperature_k": REFERENCE_TEMPERATURE_K,
            "phase": PHASE,
            "target": {"kind": request.target_kind.value, "conformer_group_ref": conformer_group_ref},
            "min_review_status": request.min_review_status.value if request.min_review_status else None,
            "effective_review_statuses": sorted((s.value for s in effective_statuses), key=lambda v: _rank(v)),
            "administrative_policy": request.admin_policy.value,
            "filters": request.filters.to_record(),
            "eligibility": request.eligibility.to_record(),
            "max_candidates": cap,
        },
        "subject": subject.to_dict(),
        "population": {
            "thermo_rows_for_entry": total_rows,
            "visible_candidates": visible_candidates,
            "assessed": len(ordered),
            "excluded_by_review": [dict(e) for e in excluded_by_review],
            "funnel": dict(funnel),
        },
        "candidates": [
            {
                **norm.to_dict(),
                "assessment": assessment.to_dict(),
                "eligible": verdict.competes,
                "eligibility": {
                    "blocked_by": list(verdict.blocked_by),
                    "passed_only_by_override": list(verdict.passed_only_by_override),
                },
            }
            for norm, assessment, verdict in ordered
        ],
        "decision": decision.to_dict() if decision is not None else None,
        "outcome": outcome.value,
    }


def _rank(status_value: str) -> int:
    for status, rank in REVIEW_FLOOR_ORDER.items():
        if status.value == status_value:
            return rank
    raise ValueError(status_value)  # pragma: no cover


def replay_decision(manifest: dict[str, Any]) -> dict[str, Any]:
    """Recompute the decision from the manifest's recorded inputs; no database.

    :returns: ``Decision.to_dict()`` (``{"outcome": "bounded_search_exceeded"}`` for a refused search).
    :raises ReplayError: if the manifest's format, policy version or assessment semantics is not carried by
        the running registry.
    """
    read_manifest_format(MANIFEST_FORMATS, MANIFEST_FORMAT_NAME, manifest, ReplayError)
    if not same_value(manifest["policy"], {"name": POLICY_NAME, "version": POLICY_VERSION}):
        raise ReplayError(f"manifest was made under policy {manifest['policy']!r}; this registry replays {POLICY_NAME} version {POLICY_VERSION}")
    try:
        check_supported(manifest, semantics=ASSESSMENT_SEMANTICS, name=ASSESSMENT_SEMANTICS_NAME)
    except UnsupportedAssessmentSemantics as exc:
        raise ReplayError(str(exc)) from exc
    if manifest["decision"] is None:
        return {"outcome": manifest["outcome"]}
    try:
        eligibility = Eligibility.from_record(manifest["request"].get("eligibility"))
    except UnreadableEligibility as exc:
        raise ReplayError(str(exc)) from exc
    for c in manifest["candidates"]:
        verdict = judge(
            applicable=c["assessment"]["applicability"] == Applicability.applicable.value,
            review_status=RecordReviewStatus(c["review_status"]),
            blocking=c["assessment"]["blocking"],
            eligibility=eligibility,
        )
        recorded = c.get("eligibility") or {}
        if (
            c["eligible"] != verdict.competes
            or recorded.get("blocked_by") != list(verdict.blocked_by)
            or recorded.get("passed_only_by_override") != list(verdict.passed_only_by_override)
        ):
            raise ReplayError(
                f"candidate {c['thermo_ref']} records eligible={c['eligible']} but its assessment under the recorded "
                f"eligibility settings says eligible={verdict.competes}"
            )
    eligible = [NormalizedCandidate.from_dict(c) for c in manifest["candidates"] if c["eligible"]]
    decision = decide(eligible, admin_policy=SelectionPolicy(manifest["request"]["administrative_policy"]))
    return decision.to_dict()


def replay_matches(manifest: dict[str, Any]) -> bool:
    """True when replaying the manifest reproduces the decision it records and the outcome it states.

    The top-level ``outcome`` is compared too: it is a summary a reader trusts, so an edit of it that the decision
    does not support must not "match".
    """
    replayed = replay_decision(manifest)
    recorded = manifest["decision"] if manifest["decision"] is not None else {"outcome": manifest["outcome"]}
    return replayed == recorded and replayed["outcome"] == manifest["outcome"]

