"""The replayable decision manifest.

A selection is only as trustworthy as the inputs it can be re-derived from. The manifest
holds the normalised request, the effective review floor, every assessed candidate with its
normalised inputs and applicability verdict, the registry entries (rule id, version, evidence
limits) that were applied, each rule's verdict per candidate, the accepted edges, the fronts,
and the administrative order. It is a JSON-ready dict, built from public refs only: no
database id, no internal key. ``id_rank`` is an ordinal standing in for the id as the last
tie-break and cannot be turned back into one.

:func:`replay_decision` re-runs the ordering engine over the manifest's eligible candidates
with no database. A digest would only prove the manifest was not altered; replay proves the
decision follows from what it records. A rule version the running registry does not carry
refuses the replay rather than substituting another.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Any

from app.db.models.common import RecordReviewStatus
from app.schemas.reads.scientific_common import REVIEW_RANK, SelectionPolicy
from app.services.selection_kernel.assessment_semantics import (
    UnsupportedAssessmentSemantics,
    check_supported,
    describe_replay,
    semantics_block,
)
from app.services.thermo_selection.engine import decide
from app.services.thermo_selection.models import (
    ASSESSMENT_SEMANTICS_VERSION,
    MANIFEST_FORMAT_VERSION,
    PHASE,
    POLICY_NAME,
    POLICY_VERSION,
    QUANTITY,
    REFERENCE_TEMPERATURE_K,
    SUPPORTED_ASSESSMENT_SEMANTICS,
    CandidateAssessment,
    Decision,
    H298Request,
    NormalizedCandidate,
    Outcome,
    Subject,
)
from app.services.thermo_selection.rules import PreferenceRule, default_rules
from app.services.trust.rubrics import COMPUTED_THERMO_V2


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
    candidates: Sequence[tuple[NormalizedCandidate, CandidateAssessment, bool]],
    decision: Decision | None,
    outcome: Outcome,
) -> dict[str, Any]:
    """Assemble the manifest. ``decision`` is ``None`` only for a refused (over-cap) search."""
    ordered = sorted(candidates, key=lambda item: item[0].id_rank)
    return {
        "manifest_format_version": MANIFEST_FORMAT_VERSION,
        "policy": {"name": POLICY_NAME, "version": POLICY_VERSION},
        "assessment_semantics": semantics_block(
            version=ASSESSMENT_SEMANTICS_VERSION, evidence_rubric=f"{COMPUTED_THERMO_V2.name}@{COMPUTED_THERMO_V2.version}"
        ),
        "request": {
            "quantity": QUANTITY,
            "temperature_k": REFERENCE_TEMPERATURE_K,
            "phase": PHASE,
            "target": {"kind": request.target_kind.value, "conformer_group_ref": conformer_group_ref},
            "min_review_status": request.min_review_status.value if request.min_review_status else None,
            "effective_review_statuses": sorted((s.value for s in effective_statuses), key=lambda v: _rank(v)),
            "administrative_policy": request.admin_policy.value,
            "max_candidates": cap,
        },
        "subject": subject.to_dict(),
        "population": {
            "thermo_rows_for_entry": total_rows,
            "visible_candidates": visible_candidates,
            "assessed": len(ordered),
            "excluded_by_review": [dict(e) for e in excluded_by_review],
        },
        "candidates": [
            {**norm.to_dict(), "assessment": assessment.to_dict(), "eligible": eligible}
            for norm, assessment, eligible in ordered
        ],
        "decision": decision.to_dict() if decision is not None else None,
        "outcome": outcome.value,
    }


def _rank(status_value: str) -> int:
    for status, rank in REVIEW_RANK.items():
        if status.value == status_value:
            return rank
    raise ValueError(status_value)  # pragma: no cover


def replay_decision(manifest: dict[str, Any], *, rules: Sequence[PreferenceRule] | None = None) -> dict[str, Any]:
    """Recompute the decision from the manifest's recorded inputs; no database.

    :returns: ``Decision.to_dict()`` (``{"outcome": "bounded_search_exceeded"}`` for a refused search).
    :raises ReplayError: if the manifest's format, policy version or a rule version is not carried by
        the running registry.
    """
    if manifest.get("manifest_format_version") != MANIFEST_FORMAT_VERSION:
        raise ReplayError(f"unknown manifest format {manifest.get('manifest_format_version')!r}")
    if manifest["policy"] != {"name": POLICY_NAME, "version": POLICY_VERSION}:
        raise ReplayError(f"manifest was made under policy {manifest['policy']!r}; this registry replays {POLICY_NAME} v{POLICY_VERSION}")
    try:
        check_supported(manifest, supported=SUPPORTED_ASSESSMENT_SEMANTICS)
    except UnsupportedAssessmentSemantics as exc:
        raise ReplayError(str(exc)) from exc
    if manifest["decision"] is None:
        return {"outcome": manifest["outcome"]}
    registry = tuple(default_rules() if rules is None else rules)
    available = {(r.rule_id, r.version): r for r in registry}
    used = []
    for entry in manifest["decision"]["rules"]:
        key = (entry["rule_id"], entry["version"])
        if key not in available:
            raise ReplayError(f"rule {key[0]} version {key[1]} is not in the running registry")
        used.append(available[key])
    for c in manifest["candidates"]:
        if c["eligible"] != c["assessment"]["physically_eligible"]:
            raise ReplayError(
                f"candidate {c['thermo_ref']} records eligible={c['eligible']} but its assessment says "
                f"physically_eligible={c['assessment']['physically_eligible']}"
            )
    eligible = [NormalizedCandidate.from_dict(c) for c in manifest["candidates"] if c["eligible"]]
    decision = decide(
        eligible,
        subject=Subject.from_dict(manifest["subject"]),
        admin_policy=SelectionPolicy(manifest["request"]["administrative_policy"]),
        rules=tuple(used),
    )
    return decision.to_dict()


def replay_matches(manifest: dict[str, Any], *, rules: Sequence[PreferenceRule] | None = None) -> bool:
    """True when replaying the manifest reproduces the decision it records and the outcome it states.

    The top-level ``outcome`` is compared too: it is a summary a reader trusts, so an edit of it that the decision
    does not support must not "match".
    """
    replayed = replay_decision(manifest, rules=rules)
    recorded = manifest["decision"] if manifest["decision"] is not None else {"outcome": manifest["outcome"]}
    return replayed == recorded and replayed["outcome"] == manifest["outcome"]


def replay_provenance(manifest: dict[str, Any]) -> dict[str, Any]:
    """Label a replay as historical or current: the assessment semantics the manifest was made under versus today's.

    :raises ReplayError: when the manifest records assessment semantics this release does not carry.
    """
    try:
        return describe_replay(
            manifest, current=ASSESSMENT_SEMANTICS_VERSION, supported=SUPPORTED_ASSESSMENT_SEMANTICS
        )
    except UnsupportedAssessmentSemantics as exc:
        raise ReplayError(str(exc)) from exc
