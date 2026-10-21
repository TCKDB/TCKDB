"""The replayable statmech decision manifest, and its three explicit replay operations.

A selection is only as trustworthy as the inputs it can be re-derived from. The manifest holds the normalised request,
the read profile and effective review statuses it was made under, the snapshot isolation, the subject entry, the
population counts, **every candidate's facts and every normalised source calculation** (public refs and stored facts,
no database id), each candidate's assessment, the registry entries consulted, and the decision. ``id_rank`` is an
ordinal standing in for the id as the last tie-break; it cannot be turned back into one. Hashes and recorded
eligibility flags alone would be inadequate, so the actual assessment inputs are carried.

Three offline operations, none touching a database:

* :func:`reproduce_statmech_ordering` runs the decision over the **recorded** assessments. It is labelled
  ``reassessed=false`` and says in words that it reproduces an ordering from recorded eligibility, not an endorsement
  under today's rules (the shared assessment-semantics contract, unchanged).
* :func:`reassess_statmech_captured_inputs` recomputes every candidate's assessment from the captured facts under the
  captured supported versions and refuses on any difference from the recorded one. It never trusts a recorded
  ``eligible``, applicability, reason or advisory, even when coordinated edits make them agree.
* :func:`replay_statmech_decision` does the reassessment, then runs the scoped rules, fronts and decision over the
  *recomputed* assessments and compares with the recorded decision and top-level outcome.

What replay is and is not. It reproduces reasoning from captured inputs. It does not authenticate scientific truth,
provenance, or that a forged population was complete. The digests are **checksums, not signatures**: they detect an
accidental or careless edit, and anyone who edits a manifest can recompute them, so a digest says nothing about who
made it. Using an external manifest operationally needs server or release provenance and authorization, which no
replay performs. A historic replay keeps the review statuses captured in the manifest: a later withdrawal, supersession,
finding or rule deactivation changes a new live decision, never the historical artifact. A fresh endorsement is a new
authorized live selection.

A manifest, semantics or rule version this release does not carry refuses the replay: nothing substitutes the current
rubric for a historical one. A manifest made under a *supported older* assessment semantics can have its recorded ordering reproduced
(labelled ``historical``) but is never reassessed under today's rubric; a reassessment or full replay runs only under
the running semantics and is labelled ``current``.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from typing import Any

from tckdb_schemas.fragments.version import VersionRef

from app.db.models.common import RecordReviewStatus
from app.schemas.reads.scientific_common import default_visible_statuses, status_at_or_above
from app.services.manifest_formats import (
    manifest_header,
    new_current_only_registry,
    new_format_registry,
    read_manifest_format,
)
from app.services.rule_registry import rule_recorded_in, rules_registry
from app.services.scientific_read.profile import CURATED_REVIEW_FLOOR
from app.services.selection_kernel import assessment_semantics as semantics
from app.services.statmech_selection.assessment import StatmechAssessment, assess_candidate
from app.services.statmech_selection.bounds import check_manifest_size
from app.services.statmech_selection.decision import (
    ADMIN_KEY_VERSION,
    DECISION_VERSION,
    StatmechDecision,
    decide_statmech,
)
from app.services.statmech_selection.facts import CandidateFacts, SubjectFacts
from app.services.statmech_selection.models import (
    ASSESSMENT_VERSION,
    NORMALIZER_VERSION,
    SUPPORTED_FINDING_VERSIONS,
    StatmechRequest,
)
from app.services.statmech_selection.rules import RULES_VERSION, StatmechRule, default_rules, validate_rules
from app.services.statmech_selection.service import StatmechQualification
from app.services.statmech_selection.vocabulary import CONTRACT_VERSION
from app.services.structure_selection.models import NormalizedCalculation
from app.versioning import same_value

#: A hidden ref in the captured facts is a placeholder token (`withheld:<n>`) under a profile with a review floor. Replay
#: reads exactly this format; a manifest in any other is refused.
MANIFEST_FORMAT_VERSION = 1
MANIFEST_FORMAT_NAME = "statmech_selection_manifest"
MANIFEST_FORMATS = new_format_registry(
    "statmech_selection_manifest_formats", MANIFEST_FORMAT_NAME, MANIFEST_FORMAT_VERSION
)

#: Assessment semantics versions this release can reassess. A manifest made under another is refused, not run under today's.
ASSESSMENT_SEMANTICS_NAME = "statmech_selection_assessment"
ASSESSMENT_SEMANTICS = new_current_only_registry(
    "statmech_selection_assessment_semantics", ASSESSMENT_SEMANTICS_NAME, ASSESSMENT_VERSION
)
EVIDENCE_RUBRIC = VersionRef(name="statmech_qualification", version=ASSESSMENT_VERSION)

INTEGRITY_NOTE = (
    "sha256 checksums over the canonical JSON of each captured input and of the whole manifest; they detect edits and "
    "are not signatures: anyone who edits a manifest can recompute them"
)


class ReplayError(ValueError):
    """The manifest cannot be replayed: an unsupported version, a failed checksum, or a self-contradiction.

    ``code`` is a stable short token; it is not an API error code (no HTTP endpoint replays a manifest).
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code


def canonical(value: Any) -> str:
    """The canonical JSON a digest is taken over: sorted keys, no whitespace, finite floats as Python writes them."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _canon(value: Any) -> Any:
    """``value`` as it is after a JSON round trip (tuples become lists), for comparing recorded with recomputed."""
    return json.loads(canonical(value))


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def expected_effective_statuses(
    profile_floor: RecordReviewStatus | None, request_floor: RecordReviewStatus | None
) -> frozenset[RecordReviewStatus]:
    """The review statuses a request may see, as a pure function of the two floors (terminal statuses out; floors only narrow)."""
    base = default_visible_statuses(include_rejected=False, include_deprecated=False)
    for floor in (profile_floor, request_floor):
        if floor is not None:
            base = base & status_at_or_above(floor)
    return frozenset(base)


def _with_digest(raw: dict[str, Any]) -> dict[str, Any]:
    return {**raw, "content_sha256": digest(raw)}


def _without_digest(raw: dict[str, Any]) -> tuple[dict[str, Any], str | None]:
    return {k: v for k, v in raw.items() if k != "content_sha256"}, raw.get("content_sha256")


def _versions() -> dict[str, Any]:
    return {
        "contract": CONTRACT_VERSION,
        "assessment": ASSESSMENT_VERSION,
        "normalizer": NORMALIZER_VERSION,
        "decision": DECISION_VERSION,
        "administrative_key": ADMIN_KEY_VERSION,
        "rules": RULES_VERSION,
        "finding_semantics": sorted(SUPPORTED_FINDING_VERSIONS),
    }


def _population(result: StatmechQualification, *, withhold_excluded: bool) -> dict[str, Any]:
    """The population block. Under a profile that imposes a floor, records below it are neither listed nor counted.

    The redaction is made before the checksum so the downloaded manifest still verifies; the total is replaced by the
    visible count so the difference cannot be recovered from the arithmetic.
    """
    return {
        "total_candidates": result.visible_candidates if withhold_excluded else result.total_candidates,
        "visible_candidates": result.visible_candidates,
        "nested_rows": result.nested_rows,
        "excluded_count": 0 if withhold_excluded else result.excluded_count,
        "excluded_by_review": [] if withhold_excluded else [dict(e) for e in result.excluded_by_review],
        "excluded_by_review_withheld": withhold_excluded,
    }


def build_manifest(
    result: StatmechQualification,
    decision: StatmechDecision,
    *,
    profile: dict[str, Any],
) -> dict[str, Any]:
    """Assemble the manifest of one decision over one assessed population.

    :raises CodedValueError: ``statmech_selection_bound_exceeded`` (``manifest_bytes``) when the canonical manifest
        exceeds the request's bound; nothing is returned without its whole manifest.
    """
    body: dict[str, Any] = {
        "format": manifest_header(MANIFEST_FORMATS, MANIFEST_FORMAT_NAME),
        "versions": _versions(),
        "assessment_semantics": semantics.semantics_block(
            name=ASSESSMENT_SEMANTICS_NAME, version=ASSESSMENT_VERSION, evidence_rubric=EVIDENCE_RUBRIC
        ),
        "request": result.request.to_dict(),
        "effective_review_statuses": sorted(s.value for s in result.effective_statuses),
        "read_profile": dict(profile),
        "snapshot_isolation": result.snapshot_isolation,
        "subject": result.subject.to_dict(),
        "population": _population(result, withhold_excluded=profile.get("review_floor") is not None),
        "candidates": [_with_digest(c.to_dict()) for c in result.candidates],
        "calculations": [_with_digest(c.to_dict()) for c in result.calculations],
        "assessments": [a.to_dict() for a in result.assessments],
        "decision": decision.to_dict(),
        "outcome": decision.outcome.value,
    }
    body = _canon(body)
    manifest = {**body, "integrity": {"algorithm": "sha256", "manifest_sha256": digest(body), "note": INTEGRITY_NOTE}}
    check_manifest_size(result.request.bounds, len(canonical(manifest).encode()))
    return manifest


# ---------------------------------------------------------------------------
# Reading a manifest back
# ---------------------------------------------------------------------------


def _check_format(manifest: dict[str, Any]) -> None:
    read_manifest_format(
        MANIFEST_FORMATS,
        MANIFEST_FORMAT_NAME,
        manifest,
        lambda message: ReplayError("unsupported_manifest_format", message),
    )


def _check_versions(manifest: dict[str, Any], *, reassessing: bool) -> int:
    """Refuse a manifest made under a version this release does not carry. Returns the assessment semantics version."""
    versions = manifest.get("versions") or {}
    running = _versions()
    for name in ("contract", "decision", "administrative_key", "rules"):
        if not same_value(versions.get(name), running[name]):
            raise ReplayError(
                "unsupported_semantic_version",
                f"manifest was made under {name} version {versions.get(name)!r}; this release replays {running[name]!r}",
            )
    try:
        recorded = semantics.check_supported(manifest, semantics=ASSESSMENT_SEMANTICS, name=ASSESSMENT_SEMANTICS_NAME)
    except semantics.UnsupportedAssessmentSemantics as exc:
        raise ReplayError("unsupported_assessment_semantics", str(exc)) from exc
    if reassessing:
        # Reassessment runs the code that is here, so it can only be honest about the versions that code is. A manifest
        # made under an older (still readable) assessment semantics keeps its recorded ordering, reproducible with
        # reproduce_statmech_ordering, but is never silently reassessed under today's rubric.
        if recorded != ASSESSMENT_VERSION:
            raise ReplayError(
                "historical_manifest_cannot_be_reassessed",
                f"the manifest was made under assessment semantics {recorded!r}; this release reassesses {ASSESSMENT_VERSION!r} "
                "only, so its recorded ordering can be reproduced but its candidates cannot be reassessed",
            )
        for name in ("assessment", "normalizer", "finding_semantics"):
            if not same_value(versions.get(name), running[name]):
                raise ReplayError(
                    "unsupported_semantic_version",
                    f"manifest was made under {name} version {versions.get(name)!r}; this release reassesses {running[name]!r}",
                )
    return recorded


def _check_integrity(manifest: dict[str, Any]) -> None:
    integrity = manifest.get("integrity") or {}
    body = {k: v for k, v in manifest.items() if k != "integrity"}
    if integrity.get("algorithm") != "sha256" or integrity.get("manifest_sha256") != digest(body):
        raise ReplayError("manifest_checksum_mismatch", "the manifest does not match its own checksum")
    for section in ("candidates", "calculations"):
        for raw in manifest[section]:
            inner, recorded = _without_digest(raw)
            if recorded != digest(inner):
                ref = inner.get("candidate_ref") or inner.get("calculation_ref")
                raise ReplayError("input_checksum_mismatch", f"captured input {ref} does not match its checksum")


def _parse(
    manifest: dict[str, Any],
) -> tuple[StatmechRequest, SubjectFacts, list[CandidateFacts], dict[str, NormalizedCalculation]]:
    try:
        request = StatmechRequest.from_dict(manifest["request"])
        subject = SubjectFacts.from_dict(manifest["subject"])
        candidates = [CandidateFacts.from_dict(_without_digest(c)[0]) for c in manifest["candidates"]]
        calcs = [NormalizedCalculation.from_dict(_without_digest(c)[0]) for c in manifest["calculations"]]
    except (KeyError, TypeError, ValueError) as exc:
        raise ReplayError("unreadable_manifest", f"the manifest cannot be read back: {exc}") from exc
    refs = [c.candidate_ref for c in candidates]
    ranks = [c.id_rank for c in candidates]
    if len(set(refs)) != len(refs) or len(set(ranks)) != len(ranks):
        raise ReplayError("inconsistent_manifest", "two captured candidates share a reference or an ordinal")
    by_ref = {c.calculation_ref: c for c in calcs}
    if len(by_ref) != len(calcs):
        raise ReplayError("inconsistent_manifest", "two captured calculations share a reference")
    return request, subject, candidates, by_ref


def _check_consistency(
    manifest: dict[str, Any], request: StatmechRequest, candidates: Sequence[CandidateFacts], calcs: dict[str, NormalizedCalculation]
) -> None:
    profile = manifest.get("read_profile") or {}
    name = profile.get("profile")
    floor_raw = profile.get("review_floor")
    if name not in ("exploratory", "curated"):
        raise ReplayError("inconsistent_manifest", f"unknown read profile {name!r}")
    floor = RecordReviewStatus(floor_raw) if floor_raw is not None else None
    if (name == "curated") != (floor == CURATED_REVIEW_FLOOR) or (name == "exploratory" and floor is not None):
        raise ReplayError("inconsistent_manifest", "the recorded read profile and its review floor disagree")
    recorded = frozenset(RecordReviewStatus(s) for s in manifest["effective_review_statuses"])
    if recorded != expected_effective_statuses(floor, request.min_review_status):
        raise ReplayError(
            "inconsistent_manifest", "the effective review statuses are not what the recorded profile and request floor allow"
        )
    # Every captured input, not only the candidates: a declaration's members and each source calculation carry their own
    # status, and a manifest relabelled curated (or one with an input quietly flipped) must not pass on its candidates alone.
    for candidate in candidates:
        if candidate.review_status not in recorded:
            raise ReplayError("inconsistent_manifest", f"{candidate.candidate_ref} has a review status its own request cannot see")
        if candidate.declaration is not None:
            for member in candidate.declaration.members:
                if member.review_status is not None and member.review_status not in recorded:
                    raise ReplayError("inconsistent_manifest", f"a member of {candidate.candidate_ref} has a status its request cannot see")
    for calc in calcs.values():
        if calc.review_status not in recorded:
            raise ReplayError("inconsistent_manifest", f"{calc.calculation_ref} has a review status its own request cannot see")
    if manifest.get("population", {}).get("visible_candidates") != len(candidates):
        raise ReplayError("inconsistent_manifest", "the recorded visible-candidate count is not the number of captured candidates")
    if manifest.get("outcome") != (manifest.get("decision") or {}).get("outcome"):
        raise ReplayError("inconsistent_manifest", "the top-level outcome is not the decision's")


def _prepare(manifest: dict[str, Any], *, reassessing: bool) -> tuple[int, StatmechRequest, SubjectFacts, list[CandidateFacts], dict[str, NormalizedCalculation]]:
    _check_format(manifest)
    recorded = _check_versions(manifest, reassessing=reassessing)
    _check_integrity(manifest)
    request, subject, candidates, calcs = _parse(manifest)
    _check_consistency(manifest, request, candidates, calcs)
    return recorded, request, subject, candidates, calcs


def _recorded_assessments(manifest: dict[str, Any], candidates: Sequence[CandidateFacts]) -> list[StatmechAssessment]:
    """The assessments the manifest records, read back, in the captured candidate order; each must be self-consistent."""
    try:
        recorded = [StatmechAssessment.from_dict(a) for a in manifest["assessments"]]
    except (KeyError, TypeError, ValueError) as exc:
        raise ReplayError("assessment_mismatch", f"a recorded assessment contradicts itself: {exc}") from exc
    refs = [a.candidate_ref for a in recorded]
    if len(set(refs)) != len(refs):
        raise ReplayError("assessment_mismatch", "a candidate has more than one recorded assessment")
    if set(refs) != {c.candidate_ref for c in candidates}:
        raise ReplayError("assessment_mismatch", "the recorded assessments do not cover exactly the captured candidates")
    by_ref = {a.candidate_ref: a for a in recorded}
    return [by_ref[c.candidate_ref] for c in candidates]


def _rules_for(manifest: dict[str, Any], rules: Sequence[StatmechRule] | None) -> tuple[StatmechRule, ...]:
    candidates = tuple(default_rules() if rules is None else rules)
    validate_rules(candidates)
    registry = rules_registry("statmech_selection_rules", candidates)
    used: list[StatmechRule] = []
    for entry in (manifest["decision"].get("protocol") or {}).get("rules", []):
        key = (entry["rule_id"], entry["version"])
        rule = rule_recorded_in(registry, entry, lambda message: ReplayError("rule_unavailable", message))
        described = rule.describe()
        if described["status"] != entry["status"]:
            raise ReplayError(
                "rule_status_changed",
                f"rule {key[0]} version {key[1]} was {entry['status']} when this decision was made and is "
                f"{described['status']} now; a rule's status change is a new version",
            )
        if described.get("manifest_sha256") != entry.get("manifest_sha256"):
            raise ReplayError("rule_manifest_changed", f"rule {key[0]} version {key[1]} now rests on a different audited manifest")
        used.append(rule)
    return tuple(used)


# ---------------------------------------------------------------------------
# The three operations
# ---------------------------------------------------------------------------


def describe_reassessment(manifest: dict[str, Any], *, recorded: int) -> dict[str, Any]:
    """Label a reassessment or full replay. It runs only under the running semantics, so it is always ``current``."""
    return {
        "recorded_assessment_semantics": recorded,
        "current_assessment_semantics": ASSESSMENT_VERSION,
        "kind": "current",
        "reassessed": True,
        "statement": (
            "Candidates were reassessed from the captured inputs under the running assessment semantics, which are the "
            "ones the manifest was made under. This verifies the consequences of those inputs; it does not authenticate "
            "scientific truth, provenance, or that the captured population was complete, and a fresh selection is a new "
            "authorized live decision."
        ),
    }


def _compare_with_recorded(manifest: dict[str, Any], decision: StatmechDecision) -> None:
    """Refuse a replay whose recomputed decision is not the one the manifest records (the top-level outcome is tied to it)."""
    if _canon(decision.to_dict()) != _canon(manifest["decision"]):
        raise ReplayError(
            "decision_mismatch", "the decision recomputed from the captured inputs is not the decision the manifest records"
        )


def reproduce_statmech_ordering(manifest: dict[str, Any], *, rules: Sequence[StatmechRule] | None = None) -> dict[str, Any]:
    """Re-run the decision over the manifest's **recorded** assessments. ``reassessed`` is false and says so.

    :returns: ``{"label": ..., "decision": StatmechDecision.to_dict()}``.
    :raises ReplayError: unsupported format or version; a failed checksum; a self-inconsistent manifest or recorded
        assessment (an eligible flag its applicability does not give, an applicability its reasons do not give); a
        decision that is not what the recorded assessments give (``decision_mismatch``).
    """
    _, request, subject, candidates, _ = _prepare(manifest, reassessing=False)
    recorded = _recorded_assessments(manifest, candidates)
    used = _rules_for(manifest, rules)
    decision = decide_statmech(request=request, subject=subject, candidates=candidates, assessments=recorded, rules=used)
    _compare_with_recorded(manifest, decision)
    label = semantics.describe_replay(manifest, semantics=ASSESSMENT_SEMANTICS, name=ASSESSMENT_SEMANTICS_NAME)
    return {"label": label, "decision": decision.to_dict()}


def reassess_statmech_captured_inputs(manifest: dict[str, Any]) -> list[StatmechAssessment]:
    """Recompute every candidate's assessment from the captured inputs and compare it with the recorded one.

    :returns: The recomputed assessments, in the captured candidate order.
    :raises ReplayError: as :func:`reproduce_statmech_ordering` for format, version, checksum and consistency, plus a
        candidate whose recorded assessment is not what its captured inputs give.
    """
    _, request, subject, candidates, calcs = _prepare(manifest, reassessing=True)
    recomputed = [
        assess_candidate(
            c, request=request, subject=subject, calculations=calcs,
            effective_statuses=frozenset(RecordReviewStatus(s) for s in manifest["effective_review_statuses"]),
        )
        for c in candidates
    ]
    refs = [a["candidate_ref"] for a in manifest["assessments"]]
    if len(set(refs)) != len(refs):
        raise ReplayError("assessment_mismatch", "a candidate has more than one recorded assessment")
    recorded = {a["candidate_ref"]: a for a in manifest["assessments"]}
    if set(recorded) != {a.candidate_ref for a in recomputed}:
        raise ReplayError("assessment_mismatch", "the recorded assessments do not cover exactly the captured candidates")
    for a in recomputed:
        if _canon(a.to_dict()) != _canon(recorded[a.candidate_ref]):
            raise ReplayError("assessment_mismatch", f"the recorded assessment of {a.candidate_ref} is not what its inputs give")
    return recomputed


def replay_statmech_decision(manifest: dict[str, Any], *, rules: Sequence[StatmechRule] | None = None) -> dict[str, Any]:
    """Recompute the decision from the manifest: reassessment first, then scoped rules, fronts and the outcome.

    The decision runs over the *recomputed* assessments, never the recorded ones.

    :returns: ``{"label": ..., "decision": StatmechDecision.to_dict()}``.
    :raises ReplayError: as :func:`reassess_statmech_captured_inputs`, plus an unavailable rule or one whose status or
        audited manifest is not what the decision was made under, and ``decision_mismatch`` when the recomputed decision
        is not the recorded one.
    """
    assessments = reassess_statmech_captured_inputs(manifest)
    used = _rules_for(manifest, rules)
    _, request, subject, candidates, _ = _prepare(manifest, reassessing=True)
    decision = decide_statmech(request=request, subject=subject, candidates=candidates, assessments=assessments, rules=used)
    _compare_with_recorded(manifest, decision)
    recorded = semantics.recorded_version(manifest)
    return {"label": describe_reassessment(manifest, recorded=recorded), "decision": decision.to_dict()}


def replay_matches(manifest: dict[str, Any], *, rules: Sequence[StatmechRule] | None = None) -> bool:
    """True when full replay reproduces what the manifest records; False when only the recorded decision disagrees.

    The top-level ``outcome`` is a summary a reader trusts; it is tied to the recorded decision's by the consistency
    check every replay runs first, so an edit of it that the decision does not support is refused, not "matched". Every
    other failure (checksum, version, assessment, rule) still raises :class:`ReplayError`.
    """
    try:
        replay_statmech_decision(manifest, rules=rules)
    except ReplayError as error:
        if error.code == "decision_mismatch":
            return False
        raise
    return True


__all__ = [
    "ASSESSMENT_SEMANTICS",
    "ASSESSMENT_SEMANTICS_NAME",
    "EVIDENCE_RUBRIC",
    "INTEGRITY_NOTE",
    "MANIFEST_FORMAT_VERSION",
    "ReplayError",
    "build_manifest",
    "canonical",
    "describe_reassessment",
    "digest",
    "expected_effective_statuses",
    "reassess_statmech_captured_inputs",
    "replay_matches",
    "replay_statmech_decision",
    "reproduce_statmech_ordering",
]
