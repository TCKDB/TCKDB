"""The replayable structure decision manifest, and its two-level replay.

A selection is only as trustworthy as the inputs it can be re-derived from. The manifest holds the normalised
request, the read profile and effective review statuses it was made under, the snapshot isolation, the subject
entry, the population counts, **every normalised unit and source calculation** (public refs and stored facts, no
database id), each unit's assessment, and the decision. ``id_rank`` is an ordinal
standing in for the id as the last tie-break; it cannot be turned back into one.

Replay has two levels and does both by default:

1. :func:`replay_structure_assessment` recomputes every unit's assessment from the normalised inputs. It does not
   trust a recorded ``eligible``, ``applicable`` or assessment flag: the recorded assessment is compared with the
   recomputed one, and any difference refuses the replay.
2. :func:`replay_structure_decision` runs the decision (cohorts, repeats, ordering, fronts) over the
   *recomputed* assessments and compares it with the recorded decision and outcome.

What replay is and is not. It reproduces reasoning from captured inputs. It does not authenticate scientific
truth, and it cannot prove that a forged population was the complete one. The digests in the manifest (one per
normalised input and one over the whole) are **checksums, not signatures**: they detect an accidental or careless
edit, and anyone who edits a manifest can recompute them. A digest therefore proves nothing about who made the
manifest; verifying a manifest against current server-held content and authorization is a separate step that no
replay performs. A historic replay keeps the review statuses captured in the manifest: a later withdrawal
or supersession changes a new live decision, never the historical artifact.

A manifest format or semantic version this release does not carry refuses the replay rather than substituting another.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from typing import Any

from app.db.models.common import RecordReviewStatus
from app.schemas.reads.scientific_common import REVIEW_FLOOR_ORDER, default_visible_statuses, status_at_or_above
from app.services.manifest_formats import manifest_header, new_format_registry, read_manifest_format
from app.services.scientific_read.common import check_read_profile_block, evidence_usable_statuses
from app.services.scientific_read.profile import CURATED_REVIEW_FLOOR
from app.services.selection_kernel.requested import (
    OFFER_FINDINGS,
    Eligibility,
    UnreadableEligibility,
    expected_candidate_statuses,
)
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
from app.services.structure_selection.service import quality_state, verdict_of, verdict_record
from app.versioning import same_value

#: A hidden ref in the captured facts is a placeholder token (`withheld:<n>`) under a profile with a review floor. Replay
#: reads exactly this format; a manifest in any other is refused. Version 2: the decision no longer holds rules, rule
#: matches, pair checks, edges, overridden edges, opposing pairs or cycles, and the request no longer holds
#: ``apply_rules``, an objective or a reference model. Version 3: the request records its filters and its eligibility
#: settings, the manifest records which review statuses a unit itself may have and each unit's verdict under those
#: settings (with the status it was judged by), and the population records the funnel. Version 4: ``read_profile`` records the review statuses whose linked evidence
#: the profile withheld (ADR 0023); under ``curated`` a not-reviewed or under-review record is shown as evidence and
#: only a rejected or deprecated one is a token. Version 5: a determination records its conformer observation's status
#: and that observation's group's status apart (``observation_review_status``, ``group_review_status``) beside the combined
#: ``review_status``, so a replay judges the group (a record) by the record floor and the observation (evidence) by the
#: evidence rule; a manifest of an earlier format is refused.
MANIFEST_FORMAT_VERSION = 5
MANIFEST_FORMAT_NAME = "structure_selection_manifest"
MANIFEST_FORMATS = new_format_registry(
    "structure_selection_manifest_formats", MANIFEST_FORMAT_NAME, MANIFEST_FORMAT_VERSION
)

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
    funnel_record = result.funnel
    return {
        "total_units": result.visible_units if withhold_excluded else result.total_units,
        "visible_units": result.visible_units,
        "nested_rows": result.nested_rows,
        "excluded_count": 0 if withhold_excluded else result.excluded_count,
        "excluded_by_review": [] if withhold_excluded else [dict(e) for e in result.excluded_by_review],
        "excluded_by_review_withheld": withhold_excluded,
        "funnel": dict(funnel_record),
    }


#: Public name for consumers outside this module (relative conformer energies). An alias, not a copy: there is one
#: redaction of the excluded listing under a floor.
population_block = _population


def build_manifest(
    result: StructureAssessmentResult,
    decision: StructureDecision,
    *,
    profile: dict[str, Any],
) -> dict[str, Any]:
    """Assemble the manifest of one decision over one assessed population.

    :raises CodedValueError: ``structure_selection_manifest_too_large`` when the canonical manifest exceeds the
        request's bound; nothing is returned without its whole manifest.
    """
    body: dict[str, Any] = {
        "format": manifest_header(MANIFEST_FORMATS, MANIFEST_FORMAT_NAME),
        "versions": {
            "assessment": ASSESSMENT_VERSION,
            "normalizer": NORMALIZER_VERSION,
            "decision": DECISION_VERSION,
            "administrative_key": ADMIN_KEY_VERSION,
            "finding_semantics": sorted(SUPPORTED_FINDING_VERSIONS),
        },
        "request": result.request.to_dict(),
        "effective_review_statuses": sorted(s.value for s in result.effective_statuses),
        "candidate_review_statuses": sorted(s.value for s in result.candidate_statuses),
        "verdicts": {ref: dict(v) for ref, v in sorted(result.verdicts.items())},
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
    read_manifest_format(
        MANIFEST_FORMATS,
        MANIFEST_FORMAT_NAME,
        manifest,
        lambda message: ReplayError("unsupported_manifest_format", message),
    )
    versions = manifest.get("versions") or {}
    running = {
        "assessment": ASSESSMENT_VERSION,
        "normalizer": NORMALIZER_VERSION,
        "decision": DECISION_VERSION,
        "administrative_key": ADMIN_KEY_VERSION,
        "finding_semantics": sorted(SUPPORTED_FINDING_VERSIONS),
    }
    for name, value in running.items():
        if not same_value(versions.get(name), value):
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
    problem = check_read_profile_block(profile)
    if problem is not None:
        raise ReplayError("inconsistent_manifest", problem)
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
    # Belt and braces: the expected-statuses comparison above already pins a curated profile's RECORD floor to approved
    # only, so this cannot fire on its own; it states the curated invariant where a reader looks for it and survives a
    # change to ``expected_effective_statuses``. (Linked evidence follows the evidence rule instead: see below.)
    if name == "curated" and recorded != frozenset({RecordReviewStatus.approved}):
        raise ReplayError("inconsistent_manifest", "a curated profile admits approved records only")
    try:
        eligibility = Eligibility.from_record(request_eligibility(manifest), offered=OFFER_FINDINGS)
    except UnreadableEligibility as exc:
        raise ReplayError("inconsistent_manifest", str(exc)) from exc
    recorded_candidate = frozenset(RecordReviewStatus(s) for s in manifest.get("candidate_review_statuses") or ())
    if recorded_candidate != expected_candidate_statuses(floor, request.min_review_status, eligibility):
        raise ReplayError(
            "inconsistent_manifest",
            "the review statuses a unit may have are not what the recorded profile, request floor and eligibility settings allow",
        )
    # Every recorded input, not only the units: a determination's source calculations carry their own status, and a
    # manifest relabelled curated (or a curated one with a source quietly flipped) must not pass on its units alone. A
    # unit is judged against what a unit may have; a source calculation, by the effective statuses. A determination's
    # verdict also records the status a request can lift (its observation's).
    #
    # ADR 0023: under a profile with a floor the floor decides the units (a record), not what a unit rests on. A source
    # calculation, a lineage parent and a basin's conformer observation are evidence, so they may be any status the evidence
    # rule uses (everything but rejected and deprecated, over the caller's own floor), and a basin's own status, the weakest
    # of its group's and its observation's, may be one of those too. A rejected or deprecated input is still refused.
    usable = evidence_usable_statuses(request.min_review_status) if floor is not None else recorded
    unit_refs = {getattr(u, "calculation_ref", None) or u.determination_ref for u in units}
    observation_allowed = usable if floor is not None else recorded_candidate
    for item in recorded_inputs:
        ref = getattr(item, "calculation_ref", None) or item.determination_ref
        allowed = recorded_candidate if ref in unit_refs else usable
        if hasattr(item, "determination_ref") and request.grain is Grain.conformer:
            check_basin_statuses(item, ref, recorded_candidate, observation_allowed)
            continue
        if hasattr(item, "determination_ref") and (
            item.observation_review_status is not None or item.group_review_status is not None
        ):
            raise ReplayError("inconsistent_manifest", f"{ref} records a conformer status, which only a conformer-grain unit has")
        if item.review_status is None and hasattr(item, "determination_ref"):
            continue  # a determination with no review of its own (see NormalizedDetermination.review_status)
        if item.review_status not in allowed:
            raise ReplayError("inconsistent_manifest", f"{ref} has a review status its own request cannot see")
    verdicts = manifest.get("verdicts")
    if not isinstance(verdicts, dict) or set(verdicts) != unit_refs:
        raise ReplayError("verdict_mismatch", "the recorded verdicts do not cover exactly the recorded units")
    for ref, record in verdicts.items():
        gate = record.get("gate_review_status") if isinstance(record, dict) else None
        if gate is not None and RecordReviewStatus(gate) not in recorded_candidate | (
            observation_allowed if request.grain is Grain.conformer else frozenset()
        ):
            raise ReplayError("inconsistent_manifest", f"{ref} was judged by a review status its own request cannot see")
    if manifest.get("population", {}).get("visible_units") != len(units):
        raise ReplayError("inconsistent_manifest", "the recorded visible-unit count is not the number of recorded units")


def check_basin_statuses(item: Any, ref: str, record_statuses: frozenset, observation_statuses: frozenset) -> None:
    """A conformer basin's recorded statuses: its group is a record, its observation is evidence, the unit status combines them.

    The group must be a status a unit may have (the record floor, with the request's lifts); the observation one the
    evidence rule admits (or the same set when the profile has no floor); and the combined ``review_status`` the weaker of the
    two, so a ``not_reviewed`` that came from the group cannot pass as one that came from the observation.
    """
    observation, group = item.observation_review_status, item.group_review_status
    if (observation is None) != (item.conformer_observation_ref is None) or (group is None) != (
        item.conformer_group_ref is None
    ):
        raise ReplayError("inconsistent_manifest", f"{ref} records a conformer status without its conformer, or the reverse")
    if group is not None and group not in record_statuses:
        raise ReplayError("inconsistent_manifest", f"{ref} has a conformer group status its own request cannot see")
    if observation is not None and observation not in observation_statuses:
        raise ReplayError("inconsistent_manifest", f"{ref} has a conformer observation status its own request cannot see")
    chain = [s for s in (observation, group) if s is not None]
    weakest = max(chain, key=lambda s: REVIEW_FLOOR_ORDER[s]) if chain else None
    if item.review_status != weakest:
        raise ReplayError("inconsistent_manifest", f"{ref} has a review status that is not the weaker of its observation's and group's")


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


def request_eligibility(manifest: dict[str, Any]) -> Any:
    request = manifest.get("request")
    return request.get("eligibility") if isinstance(request, dict) else None


def _check_verdicts(
    manifest: dict[str, Any],
    request: StructureRequest,
    units: Sequence[Any],
    assessments: Sequence[StructureAssessment],
    calculations: dict[str, NormalizedCalculation],
) -> None:
    """Each unit's recorded verdict must be what its assessment gives under the recorded eligibility settings."""
    for unit, assessment in zip(units, assessments, strict=True):
        record = manifest["verdicts"][assessment.unit_ref]
        gate_raw = record.get("gate_review_status")
        gate = RecordReviewStatus(gate_raw) if gate_raw is not None else None
        if request.grain is Grain.calculation and gate != unit.review_status:
            raise ReplayError("verdict_mismatch", f"{assessment.unit_ref} was judged by a status that is not its own")
        expected = verdict_record(verdict_of(assessment, gate, quality_state(unit, calculations, request), request), gate)
        if _canon(record) != _canon(expected):
            raise ReplayError(
                "verdict_mismatch",
                f"the recorded verdict of {assessment.unit_ref} is not what its assessment gives under the recorded eligibility settings",
            )


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
    _check_verdicts(manifest, request, units, recomputed, calcs)
    return recomputed


def replay_structure_decision(manifest: dict[str, Any]) -> dict[str, Any]:
    """Recompute the decision from the manifest: assessments first, then cohorts, fronts and ordering.

    The decision runs over the *recomputed* assessments, never the recorded ones.

    :returns: ``StructureDecision.to_dict()`` of the replayed decision.
    :raises ReplayError: as :func:`replay_structure_assessment`.
    """
    assessments = replay_structure_assessment(manifest)
    request, _, units, calcs, _ = _parse(manifest)
    decision = decide_structures(request=request, units=units, assessments=assessments, calculations=calcs)
    return decision.to_dict()


def replay_matches(manifest: dict[str, Any]) -> bool:
    """True when both levels of replay reproduce what the manifest records.

    The top-level ``outcome`` is compared too: it is a summary a reader trusts, so an edit of it that the decision
    does not support must not "match".
    """
    replayed = replay_structure_decision(manifest)
    return _canon(replayed) == _canon(manifest["decision"]) and replayed["outcome"] == manifest["outcome"]

