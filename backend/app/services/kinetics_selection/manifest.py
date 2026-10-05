"""The replayable kinetics decision manifest.

A selection is only as trustworthy as the inputs it can be re-derived from. The manifest holds the
normalised request, the snapshot isolation it was read under, the subject reaction entry, the population
counts, every assessed record with its normalised inputs and applicability verdict, the determinations the
eligible ones form, the registry entries (rule id, version, status, evidence limits, audited manifest digest)
that were consulted, each rule's verdict per determination, the pair-compatibility checks, the accepted edges,
the overridden and unused ones, the fronts and the administrative order. It is a JSON-ready dict built from
public refs only: no database id, no internal key. ``id_rank`` is an ordinal standing in for the id as the
last tie-break and cannot be turned back into one.

:func:`replay_decision` re-runs the engine over the manifest's eligible records with no database. A digest
would only prove the manifest was not altered; replay proves the decision follows from what it records. A
policy version or a rule version, status or audited-manifest digest that the running registry does not carry
refuses the replay rather than substituting another.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from app.services.kinetics_selection.engine import KineticsDecision, build_candidates, decide
from app.services.kinetics_selection.models import (
    ASSESSMENT_SEMANTICS_VERSION,
    POLICY_NAME,
    POLICY_VERSION,
    SUPPORTED_ASSESSMENT_SEMANTICS,
    KineticsAssessmentResult,
    KineticsRequest,
    KineticsSubject,
    NormalizedKinetics,
)
from app.services.kinetics_selection.rules import KineticsRule, default_rules
from app.services.selection_kernel.assessment_semantics import (
    UnsupportedAssessmentSemantics,
    check_supported,
    describe_replay,
    semantics_block,
)
from app.services.trust.rubrics import COMPUTED_KINETICS_V2

MANIFEST_FORMAT_VERSION = 1


class ReplayError(ValueError):
    """The manifest cannot be replayed against the running registry."""


def build_manifest(result: KineticsAssessmentResult, decision: KineticsDecision) -> dict[str, Any]:
    """Assemble the manifest of one decision over one assessed population."""
    assessments = {a.kinetics_ref: a for a in result.assessments}
    ordered = sorted(result.candidates, key=lambda c: c.id_rank)
    return {
        "manifest_format_version": MANIFEST_FORMAT_VERSION,
        "policy": {"name": POLICY_NAME, "version": POLICY_VERSION},
        "assessment_semantics": semantics_block(
            version=ASSESSMENT_SEMANTICS_VERSION,
            evidence_rubric=f"{COMPUTED_KINETICS_V2.name}@{COMPUTED_KINETICS_V2.version}",
        ),
        "request": {
            **result.request.to_dict(),
            "effective_review_statuses": [s.value for s in result.effective_statuses],
        },
        "snapshot_isolation": result.snapshot_isolation,
        "subject": result.subject.to_dict(),
        "population": {
            "kinetics_rows_for_entry": result.total_rows,
            "visible_candidates": result.visible_candidates,
            "assessed": len(ordered),
            "excluded_count": result.excluded_count,
            "excluded_by_review": [dict(e) for e in result.excluded_by_review],
        },
        "candidates": [
            {
                **c.to_dict(),
                "assessment": assessments[c.kinetics_ref].to_dict(),
                "eligible": assessments[c.kinetics_ref].physically_eligible,
            }
            for c in ordered
        ],
        "determinations": [
            {**g.to_dict(), "representation_count": len(g.representation_refs)} for g in result.groups
        ],
        "decision": decision.to_dict(),
        "outcome": decision.outcome.value,
        "disclosures": {
            "unresolved_refs": list(result.unresolved_refs),
            "unsupported_refs": list(result.unsupported_refs),
            "notes": list(result.notes),
        },
    }


def _rules_for(manifest: dict[str, Any], rules: Sequence[KineticsRule] | None) -> tuple[KineticsRule, ...]:
    registry = tuple(default_rules() if rules is None else rules)
    available = {(r.rule_id, r.version): r for r in registry}
    used: list[KineticsRule] = []
    for entry in manifest["decision"]["rules"]:
        key = (entry["rule_id"], entry["version"])
        if key not in available:
            raise ReplayError(f"rule {key[0]} version {key[1]} is not in the running registry")
        rule = available[key]
        described = rule.describe()
        if described["status"] != entry["status"]:
            raise ReplayError(
                f"rule {key[0]} version {key[1]} was {entry['status']} when this decision was made and is "
                f"{described['status']} now; a rule's status change is a new version"
            )
        if described.get("manifest_sha256") != entry.get("manifest_sha256"):
            raise ReplayError(f"rule {key[0]} version {key[1]} now rests on a different audited manifest")
        used.append(rule)
    return tuple(used)


def replay_decision(manifest: dict[str, Any], *, rules: Sequence[KineticsRule] | None = None) -> dict[str, Any]:
    """Recompute the decision from the manifest's recorded inputs; no database.

    :returns: ``KineticsDecision.to_dict()``.
    :raises ReplayError: if the manifest's format or policy version, or a rule's version, status or audited
        manifest, is not what the running registry carries; or if the manifest contradicts itself (a record's
        recorded eligibility, or the determinations it says the eligible records form).
    """
    if manifest.get("manifest_format_version") != MANIFEST_FORMAT_VERSION:
        raise ReplayError(f"unknown manifest format {manifest.get('manifest_format_version')!r}")
    if manifest["policy"] != {"name": POLICY_NAME, "version": POLICY_VERSION}:
        raise ReplayError(
            f"manifest was made under policy {manifest['policy']!r}; this registry replays {POLICY_NAME} v{POLICY_VERSION}"
        )
    try:
        check_supported(manifest, supported=SUPPORTED_ASSESSMENT_SEMANTICS)
    except UnsupportedAssessmentSemantics as exc:
        raise ReplayError(str(exc)) from exc
    used = _rules_for(manifest, rules)
    for c in manifest["candidates"]:
        if c["eligible"] != c["assessment"]["physically_eligible"]:
            raise ReplayError(
                f"candidate {c['kinetics_ref']} records eligible={c['eligible']} but its assessment says "
                f"physically_eligible={c['assessment']['physically_eligible']}"
            )
    request = KineticsRequest.from_dict(
        {k: v for k, v in manifest["request"].items() if k != "effective_review_statuses"}
    )
    eligible = [NormalizedKinetics.from_dict(_candidate_fields(c)) for c in manifest["candidates"] if c["eligible"]]
    candidates = build_candidates(eligible, admin_policy=request.admin_policy)
    recorded = [{"determination_ref": g["determination_ref"], "representation_refs": g["representation_refs"],
                 "representative_ref": g["representative_ref"]} for g in manifest["determinations"]]
    rebuilt = [
        {
            "determination_ref": c.determination_ref,
            "representation_refs": [r.kinetics_ref for r in c.records],
            "representative_ref": c.records[0].kinetics_ref,
        }
        for c in candidates
    ]
    if recorded != rebuilt:
        raise ReplayError("the recorded determinations are not the ones the eligible candidates form")
    decision = decide(
        candidates,
        subject=KineticsSubject.from_dict(manifest["subject"]),
        request=request,
        rules=used,
    )
    return decision.to_dict()


def _candidate_fields(candidate: dict[str, Any]) -> dict[str, Any]:
    """The normalised-record part of a manifest candidate (without its assessment and eligibility)."""
    return {k: v for k, v in candidate.items() if k not in {"assessment", "eligible"}}


def replay_matches(manifest: dict[str, Any], *, rules: Sequence[KineticsRule] | None = None) -> bool:
    """True when replaying the manifest reproduces the decision it records and the outcome it states.

    The top-level ``outcome`` is compared too: it is a summary a reader trusts, so an edit of it that the decision
    does not support must not "match".
    """
    replayed = replay_decision(manifest, rules=rules)
    return replayed == manifest["decision"] and replayed["outcome"] == manifest["outcome"]


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
