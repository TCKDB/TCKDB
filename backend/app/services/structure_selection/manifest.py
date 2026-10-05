"""The replayable structure decision manifest, and its two-level replay.

A selection is only as trustworthy as the inputs it can be re-derived from. The manifest holds the normalised
request, the read profile and effective review statuses it was made under, the snapshot isolation, the subject
entry, the population counts, **every normalised unit and source calculation** (public refs and stored facts, no
database id), each unit's assessment, the registry entries consulted, and the decision. ``id_rank`` is an ordinal
standing in for the id as the last tie-break; it cannot be turned back into one.

Replay has two levels and does both by default:

1. :func:`replay_structure_assessment` recomputes every unit's assessment from the normalised inputs. It does not
   trust a recorded ``eligible``, ``applicable`` or assessment flag: the recorded assessment is compared with the
   recomputed one, and any difference refuses the replay.
2. :func:`replay_structure_decision` runs the decision (cohorts, repeats, ordering, rules, fronts) over the
   *recomputed* assessments and compares it with the recorded decision and outcome.

What replay is and is not. It reproduces reasoning from captured inputs. It does not authenticate scientific
truth, and it cannot prove that a forged population was the complete one. The digests in the manifest (one per
normalised input and one over the whole) are **checksums, not signatures**: they detect an accidental or careless
edit, and anyone who edits a manifest can recompute them. A digest therefore proves nothing about who made the
manifest; verifying a manifest against current server-held content and authorization is a separate step that no
replay performs. A historic replay keeps the review statuses captured in the manifest: a later withdrawal,
supersession or rule deactivation changes a new live decision, never the historical artifact.

A manifest or rule version this release does not carry refuses the replay rather than substituting another.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from typing import Any

from app.db.models.common import RecordReviewStatus
from app.schemas.reads.scientific_common import default_visible_statuses, status_at_or_above
from app.services.scientific_read.profile import CURATED_REVIEW_FLOOR
from app.services.structure_selection.assessment import assess_unit
from app.services.structure_selection.bounds import check_manifest_size
from app.services.structure_selection.decision import (
    ADMIN_KEY_VERSION,
    DECISION_VERSION,
    StructureDecision,
    decide_structures,
)
from app.services.structure_selection.models import (
    ASSESSMENT_VERSION,
    NORMALIZER_VERSION,
    SUPPORTED_FINDING_VERSIONS,
    Grain,
    NormalizedCalculation,
    NormalizedDetermination,
    StructureAssessment,
    StructureAssessmentResult,
    StructureRequest,
    StructureSubject,
)
from app.services.structure_selection.rules import RULES_VERSION, StructureRule, default_rules, validate_rules

MANIFEST_FORMAT_VERSION = 1

INTEGRITY_NOTE = (
    "sha256 checksums over the canonical JSON of each normalised input and of the whole manifest; they detect "
    "edits and are not signatures: anyone who edits a manifest can recompute them"
)


class ReplayError(ValueError):
    """The manifest cannot be replayed: an unsupported version, a failed checksum, or a self-contradiction.

    ``code`` is a stable short token; it is not an API error code (no HTTP endpoint replays a manifest).
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code


def canonical(value: Any) -> str:
    """The canonical JSON a digest is taken over: sorted keys, no whitespace, floats as Python writes them."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _canon(value: Any) -> Any:
    """``value`` as it is after a JSON round trip (tuples become lists), for comparing recorded with recomputed."""
    return json.loads(canonical(value))


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def expected_effective_statuses(
    profile_floor: RecordReviewStatus | None, request_floor: RecordReviewStatus | None
) -> frozenset[RecordReviewStatus]:
    """The review statuses a request is allowed to see, as a pure function of the two floors.

    Mirrors the live visibility rule (terminal statuses out; each floor only narrows) without reading the process's
    own read profile, so a replay judges the manifest by what the manifest recorded.
    """
    base = default_visible_statuses(include_rejected=False, include_deprecated=False)
    for floor in (profile_floor, request_floor):
        if floor is not None:
            base = base & status_at_or_above(floor)
    return frozenset(base)


def _with_digest(raw: dict[str, Any]) -> dict[str, Any]:
    return {**raw, "content_sha256": digest(raw)}


def _without_digest(raw: dict[str, Any]) -> tuple[dict[str, Any], str | None]:
    body = {k: v for k, v in raw.items() if k != "content_sha256"}
    return body, raw.get("content_sha256")


def _population(result: StructureAssessmentResult, *, withhold_excluded: bool) -> dict[str, Any]:
    """The population block. Under a read profile that imposes a floor the records below it are not listed or counted.

    Under ``curated`` the floor is ``approved``: a record below it is one the caller could not have reached through any
    other curated read, so naming or counting it would be a leak. The redaction is made here, before the manifest's
    checksum is computed, so the downloaded manifest still verifies and replays; the total is replaced by the visible
    count so the difference cannot be recovered from the arithmetic.
    """
    return {
        "total_units": result.visible_units if withhold_excluded else result.total_units,
        "visible_units": result.visible_units,
        "nested_rows": result.nested_rows,
        "excluded_count": 0 if withhold_excluded else result.excluded_count,
        "excluded_by_review": [] if withhold_excluded else [dict(e) for e in result.excluded_by_review],
        "excluded_by_review_withheld": withhold_excluded,
    }


def build_manifest(
    result: StructureAssessmentResult,
    decision: StructureDecision,
    *,
    profile: dict[str, Any],
    rules: Sequence[StructureRule],
) -> dict[str, Any]:
    """Assemble the manifest of one decision over one assessed population.

    :raises CodedValueError: ``structure_selection_manifest_too_large`` when the canonical manifest exceeds the
        request's bound; nothing is returned without its whole manifest.
    """
    body: dict[str, Any] = {
        "manifest_format_version": MANIFEST_FORMAT_VERSION,
        "versions": {
            "assessment": ASSESSMENT_VERSION,
            "normalizer": NORMALIZER_VERSION,
            "decision": DECISION_VERSION,
            "administrative_key": ADMIN_KEY_VERSION,
            "rules": RULES_VERSION,
            "finding_semantics": sorted(SUPPORTED_FINDING_VERSIONS),
        },
        "request": result.request.to_dict(),
        "effective_review_statuses": sorted(s.value for s in result.effective_statuses),
        "read_profile": dict(profile),
        "snapshot_isolation": result.snapshot_isolation,
        "subject": result.subject.to_dict(),
        "population": _population(result, withhold_excluded=profile.get("review_floor") is not None),
        "calculations": [_with_digest(c.to_dict()) for c in result.calculations],
        "determinations": [_with_digest(d.to_dict()) for d in result.determinations],
        "assessments": [a.to_dict() for a in result.assessments],
        "decision": decision.to_dict(),
        "outcome": decision.outcome.value,
        "disclosures": {
            "unresolved_refs": list(result.unresolved_refs),
            "unsupported_refs": list(result.unsupported_refs),
            "notes": list(result.notes),
        },
    }
    body = _canon(body)
    manifest = {**body, "integrity": {"algorithm": "sha256", "manifest_sha256": digest(body), "note": INTEGRITY_NOTE}}
    check_manifest_size(result.request.bounds, len(canonical(manifest).encode()))
    return manifest


# ---------------------------------------------------------------------------
# Replay
# ---------------------------------------------------------------------------


def _check_versions(manifest: dict[str, Any]) -> None:
    if manifest.get("manifest_format_version") != MANIFEST_FORMAT_VERSION:
        raise ReplayError("unsupported_manifest_format", f"unknown manifest format {manifest.get('manifest_format_version')!r}")
    versions = manifest.get("versions") or {}
    running = {
        "assessment": ASSESSMENT_VERSION,
        "normalizer": NORMALIZER_VERSION,
        "decision": DECISION_VERSION,
        "administrative_key": ADMIN_KEY_VERSION,
        "rules": RULES_VERSION,
        "finding_semantics": sorted(SUPPORTED_FINDING_VERSIONS),
    }
    for name, value in running.items():
        if versions.get(name) != value:
            raise ReplayError(
                "unsupported_semantic_version",
                f"manifest was made under {name} version {versions.get(name)!r}; this release replays {value!r}",
            )


def _check_integrity(manifest: dict[str, Any]) -> None:
    integrity = manifest.get("integrity") or {}
    body = {k: v for k, v in manifest.items() if k != "integrity"}
    if integrity.get("algorithm") != "sha256" or integrity.get("manifest_sha256") != digest(body):
        raise ReplayError("manifest_checksum_mismatch", "the manifest does not match its own checksum")
    for section in ("calculations", "determinations"):
        for raw in manifest[section]:
            inner, recorded = _without_digest(raw)
            if recorded != digest(inner):
                ref = inner.get("calculation_ref") or inner.get("determination_ref")
                raise ReplayError("input_checksum_mismatch", f"normalised input {ref} does not match its checksum")


def _check_consistency(
    manifest: dict[str, Any],
    request: StructureRequest,
    units: Sequence[Any],
    recorded_inputs: Sequence[Any],
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
    expected = expected_effective_statuses(floor, request.min_review_status)
    if recorded != expected:
        raise ReplayError(
            "inconsistent_manifest",
            "the effective review statuses are not what the recorded profile and request floor allow",
        )
    # Belt and braces: the expected-statuses comparison above already pins a curated profile to approved-only, so this
    # cannot fire on its own; it states the curated invariant where a reader looks for it and survives a change to
    # ``expected_effective_statuses``.
    if name == "curated" and recorded != frozenset({RecordReviewStatus.approved}):
        raise ReplayError("inconsistent_manifest", "a curated profile admits approved records only")
    # Every recorded input, not only the units: a determination's source calculations carry their own status, and a
    # manifest relabelled curated (or a curated one with a source quietly flipped) must not pass on its units alone.
    for item in recorded_inputs:
        if item.review_status not in recorded:
            ref = getattr(item, "calculation_ref", None) or item.determination_ref
            raise ReplayError("inconsistent_manifest", f"{ref} has a review status its own request cannot see")
    if manifest.get("population", {}).get("visible_units") != len(units):
        raise ReplayError("inconsistent_manifest", "the recorded visible-unit count is not the number of recorded units")


def _parse(
    manifest: dict[str, Any],
) -> tuple[StructureRequest, StructureSubject, list[Any], dict[str, NormalizedCalculation], list[Any]]:
    try:
        request = StructureRequest.from_dict(manifest["request"])
        subject = StructureSubject.from_dict(manifest["subject"])
        calcs = [NormalizedCalculation.from_dict(_without_digest(c)[0]) for c in manifest["calculations"]]
        dets = [NormalizedDetermination.from_dict(_without_digest(d)[0]) for d in manifest["determinations"]]
    except (KeyError, TypeError, ValueError) as exc:
        raise ReplayError("unreadable_manifest", f"the manifest cannot be read back: {exc}") from exc
    by_ref = {c.calculation_ref: c for c in calcs}
    units: list[Any] = list(calcs) if request.grain is Grain.calculation else list(dets)
    if request.grain is Grain.calculation and dets:
        raise ReplayError("inconsistent_manifest", "a calculation-grain manifest carries determinations")
    return request, subject, units, by_ref, [*calcs, *dets]


def replay_structure_assessment(manifest: dict[str, Any]) -> list[StructureAssessment]:
    """Recompute every unit's assessment from the manifest's normalised inputs and compare it with the recorded one.

    :returns: The recomputed assessments, in the manifest's unit order.
    :raises ReplayError: unsupported format or semantic version; a failed checksum; a self-inconsistent manifest
        (profile, floors, review statuses); a unit whose recorded assessment is not what its inputs give.
    """
    _check_versions(manifest)
    _check_integrity(manifest)
    request, subject, units, calcs, recorded_inputs = _parse(manifest)
    _check_consistency(manifest, request, units, recorded_inputs)
    recomputed = [assess_unit(u, request=request, subject=subject, calculations=calcs) for u in units]
    refs = [a["unit_ref"] for a in manifest["assessments"]]
    if len(set(refs)) != len(refs):
        raise ReplayError("assessment_mismatch", "a unit has more than one recorded assessment")
    recorded = {a["unit_ref"]: a for a in manifest["assessments"]}
    if set(recorded) != {a.unit_ref for a in recomputed} or len(refs) != len(recomputed):
        raise ReplayError("assessment_mismatch", "the recorded assessments do not cover exactly the recorded units")
    for a in recomputed:
        if _canon(a.to_dict()) != _canon(recorded[a.unit_ref]):
            raise ReplayError("assessment_mismatch", f"the recorded assessment of {a.unit_ref} is not what its inputs give")
    return recomputed


def _rules_for(manifest: dict[str, Any], rules: Sequence[StructureRule] | None) -> tuple[StructureRule, ...]:
    registry = tuple(default_rules() if rules is None else rules)
    validate_rules(registry)
    available = {(r.rule_id, r.version): r for r in registry}
    protocol = manifest["decision"].get("protocol") or {}
    used: list[StructureRule] = []
    for entry in protocol.get("rules", []):
        key = (entry["rule_id"], entry["version"])
        if key not in available:
            raise ReplayError("rule_unavailable", f"rule {key[0]} version {key[1]} is not in the running registry")
        rule = available[key]
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


def replay_structure_decision(manifest: dict[str, Any], *, rules: Sequence[StructureRule] | None = None) -> dict[str, Any]:
    """Recompute the decision from the manifest: assessments first, then cohorts, rules, fronts and ordering.

    The decision runs over the *recomputed* assessments, never the recorded ones.

    :returns: ``StructureDecision.to_dict()`` of the replayed decision.
    :raises ReplayError: as :func:`replay_structure_assessment`, plus an unavailable rule or one whose status or
        audited manifest is not what the decision was made under.
    """
    assessments = replay_structure_assessment(manifest)
    used = _rules_for(manifest, rules)
    request, subject, units, calcs, _ = _parse(manifest)
    decision = decide_structures(
        request=request, subject=subject, units=units, assessments=assessments, calculations=calcs, rules=used
    )
    return decision.to_dict()


def replay_matches(manifest: dict[str, Any], *, rules: Sequence[StructureRule] | None = None) -> bool:
    """True when both levels of replay reproduce what the manifest records.

    The top-level ``outcome`` is compared too: it is a summary a reader trusts, so an edit of it that the decision
    does not support must not "match".
    """
    replayed = replay_structure_decision(manifest, rules=rules)
    return _canon(replayed) == _canon(manifest["decision"]) and replayed["outcome"] == manifest["outcome"]
