"""The replayable network decision manifest, and the two levels of replay.

A selection is only as trustworthy as the inputs it can be re-derived from. The manifest holds the normalised
request, the snapshot isolation it was read under, the visibility scope (the read profile and the review states
observed), the network's states with the exact content behind each locator, every authorized solve with its
declarations, determinations and fit summaries, every assessment with its reasons and evidence states, the
fronts and the administrative order. It is built from public references and captured content only: no database id,
no internal key. Hashes alone are never the record; the canonical inputs are.

Two replay levels, because a decision rests on two things and a forged document could change either:

* :func:`replay_network_assessment` recomputes every applicability verdict (grouping, applicability, coverage,
  qualification-dependent reasons, the pinned product-set content) from the captured normalised inputs under the
  pinned semantic versions, and refuses if any recomputed verdict differs from the recorded one.
* :func:`replay_network_decision` recomputes the outcome and the administrative order from the *recomputed*
  assessments, never from recorded eligibility flags, so a mirrored edit of ``eligible`` together with
  its recorded assessment cannot bypass re-assessment.

:func:`replay_network` does both, and also compares the recorded ``outcome`` as well as the decision. A manifest
format, policy, assessment, bounds or declaration version that the running code does not carry refuses the replay
rather than substituting another.

What replay is, and is not. It reproduces the *reasoning* from captured inputs. It does not authenticate the
scientific truth of those inputs, and it cannot prove that no authorized candidate was left out of a forged
document: a document that omits a candidate replays cleanly. The record carries a canonical digest and its
provenance, and a release carries its own signature and checksum facilities, so the manifest says where it came
from; trust in that origin is not something replay supplies.

A historic replay uses the review states it captured. A withdrawal or a new review since then changes a fresh
decision, not the historical artifact, and the manifest says which review states it observed so its validity is
never mistaken for a current recommendation.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from tckdb_schemas.network_declarations import NETWORK_DECLARATION_VERSIONS

from app.db.models.common import RecordReviewStatus
from app.services.manifest_formats import manifest_header, new_format_registry, read_manifest_format
from app.services.network_selection.assessment import (
    ASSESSMENT_VERSION,
    assess_bundle_scope,
    assess_channel_scope,
    ungrouped_fits,
)
from app.services.network_selection.engine import NetworkDecision, build_candidates, decide
from app.services.network_selection.models import (
    DEFAULT_BOUNDS,
    POLICY_NAME,
    POLICY_VERSION,
    BundleAssessment,
    DeterminationAssessment,
    NetworkAssessmentResult,
    NetworkFacts,
    NetworkRequest,
    Scope,
    SolveFacts,
)
from app.services.scientific_read.common import linked_evidence_withheld_values
from app.services.scientific_read.profile import current_read_profile
from app.services.selection_kernel.requested import OFFER_REVIEW, Eligibility, UnreadableEligibility, review_default
from app.versioning import same_value

#: A hidden ref in the captured facts is a placeholder token (`withheld:<n>`) under a profile with a review floor. The
#: network's one such position, a validation entry's reference to a solve the reader cannot open, is evidence and reads
#: as unavailable (``reference_solve_ref`` null, ``reference_withheld`` true), as on a reference to nothing; no token
#: stands there today. Replay reads exactly this format; a manifest in any other is refused. Version 2: the decision no
#: longer holds rules, rule matches, pair checks, edges, overridden or unused edges, opposing pairs or cycles, and the
#: request no longer holds an objective, a reference model or reference outputs. Version 3: the request records its
#: filters and its eligibility settings, and the population records the funnel (counted in solves). Version 4: ``visibility`` records the review statuses
#: whose linked evidence the profile withheld (ADR 0023).
MANIFEST_FORMAT_VERSION = 4
MANIFEST_FORMAT_NAME = "network_selection_manifest"
MANIFEST_FORMATS = new_format_registry(
    "network_selection_manifest_formats", MANIFEST_FORMAT_NAME, MANIFEST_FORMAT_VERSION
)

REPLAY_BOUNDARY = (
    "Replay reproduces the reasoning from the captured inputs. It does not authenticate their scientific truth and "
    "cannot show that no authorized candidate was omitted from a forged document; the digest and provenance say where "
    "this manifest came from."
)


class ReplayError(ValueError):
    """The manifest cannot be replayed against the running code, or contradicts what it was recomputed to."""


def canonical_bytes(manifest: dict[str, Any]) -> bytes:
    """The canonical form the digest and the size bound are taken over: the manifest without its digest."""
    body = {k: v for k, v in manifest.items() if k != "digest"}
    return json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")


def manifest_digest(manifest: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_bytes(manifest)).hexdigest()


def build_manifest(result: NetworkAssessmentResult, decision: NetworkDecision) -> dict[str, Any]:
    """Assemble the manifest of one decision over one assessed population."""
    profile = current_read_profile()
    manifest: dict[str, Any] = {
        "format": manifest_header(MANIFEST_FORMATS, MANIFEST_FORMAT_NAME),
        "policy": {
            "name": POLICY_NAME,
            "version": POLICY_VERSION,
            "assessment_version": ASSESSMENT_VERSION,
            "bounds_version": result.request.bounds.version,
        },
        "declaration_versions": sorted(NETWORK_DECLARATION_VERSIONS),
        "request": {**result.request.to_dict(), "effective_review_statuses": [s.value for s in result.effective_statuses]},
        "visibility": {
            "read_profile": profile.profile.value,
            "linked_evidence_withheld": linked_evidence_withheld_values(profile=profile),
            "recommendation": profile.recommendation.value,
            "review_statuses_observed": sorted({s.review_status.value for s in result.solves}),
        },
        "snapshot_isolation": result.snapshot_isolation,
        "network": result.network.to_dict(),
        "population": {
            "counts": dict(sorted(result.counts.items())),
            "excluded_count": result.excluded_count,
            "excluded_by_review": [dict(e) for e in result.excluded_by_review],
            "funnel": dict(result.funnel),
        },
        "solves": [s.to_dict() for s in sorted(result.solves, key=lambda s: s.id_rank)],
        "assessments": {
            "determinations": [a.to_dict() for a in result.determination_assessments],
            "bundles": [b.to_dict() for b in result.bundle_assessments],
            "ungrouped_fit_refs": list(result.ungrouped_fit_refs),
        },
        "decision": decision.to_dict(),
        "outcome": decision.outcome.value,
        "disclosures": {
            "unresolved_refs": list(result.unresolved_refs),
            "unsupported_refs": list(result.unsupported_refs),
            "notes": list(result.notes),
        },
        "replay_boundary": REPLAY_BOUNDARY,
    }
    manifest["digest"] = {"algorithm": "sha256", "value": manifest_digest(manifest)}
    return manifest


def _check_versions(manifest: dict[str, Any]) -> None:
    read_manifest_format(MANIFEST_FORMATS, MANIFEST_FORMAT_NAME, manifest, ReplayError)
    policy = manifest["policy"]
    expected = {
        "name": POLICY_NAME,
        "version": POLICY_VERSION,
        "assessment_version": ASSESSMENT_VERSION,
        "bounds_version": DEFAULT_BOUNDS.version,
    }
    if not same_value(policy, expected):
        raise ReplayError(
            f"manifest was made under {policy!r}; this code replays {POLICY_NAME} version {POLICY_VERSION}, "
            f"assessment version {ASSESSMENT_VERSION}, bounds version {DEFAULT_BOUNDS.version}"
        )
    for solve in manifest["solves"]:
        for block in ("target", "protocol", "validation"):
            declaration = solve.get(block)
            version = declaration.get("version") if isinstance(declaration, dict) else None
            if declaration is not None and (type(version) is not int or version not in NETWORK_DECLARATION_VERSIONS):
                raise ReplayError(
                    f"solve {solve['solve_ref']} carries a {block} declaration of version {version!r}, which this "
                    f"code does not read (supported: {sorted(NETWORK_DECLARATION_VERSIONS)})"
                )


def _inputs(manifest: dict[str, Any]) -> tuple[NetworkRequest, NetworkFacts, tuple[SolveFacts, ...]]:
    request = NetworkRequest.from_dict({k: v for k, v in manifest["request"].items() if k != "effective_review_statuses"})
    network = NetworkFacts.from_dict(manifest["network"])
    solves = tuple(SolveFacts.from_dict(s) for s in manifest["solves"])
    return request, network, solves


def _recompute_assessments(
    request: NetworkRequest, network: NetworkFacts, solves: tuple[SolveFacts, ...]
) -> tuple[list[DeterminationAssessment], list[BundleAssessment], list[str]]:
    if request.scope is Scope.single_channel:
        channel_dets, channel_ungrouped = assess_channel_scope(solves, network, request)
        return channel_dets, [], channel_ungrouped
    bundles, dets = assess_bundle_scope(solves, network, request)
    wanted = {o.channel_key for o in request.required_outputs()}
    ungrouped: list[str] = []
    for solve in solves:
        ungrouped.extend(ungrouped_fits(solve, wanted))
    return dets, bundles, ungrouped


def _check_consistency(manifest: dict[str, Any]) -> None:
    """Facts the manifest states twice must agree: the review statuses it says it observed are the solves' own."""
    observed = sorted({str(s["review_status"]) for s in manifest["solves"]})
    if manifest["visibility"]["review_statuses_observed"] != observed:
        raise ReplayError("the recorded observed review statuses are not the review statuses of the captured solves")
    # The digest is an unkeyed checksum anyone can re-seal, so the review basis is checked against itself: a document
    # relabelled to a stricter profile while it still holds a solve that profile would never have shown is refused.
    effective = {str(v) for v in manifest["request"]["effective_review_statuses"]}
    outside = sorted(set(observed) - effective)
    if outside:
        raise ReplayError(
            f"a captured solve has a review status ({outside}) outside the recorded effective review statuses"
        )
    try:
        eligibility = Eligibility.from_record(manifest["request"].get("eligibility"), offered=OFFER_REVIEW)
    except UnreadableEligibility as exc:
        raise ReplayError(str(exc)) from exc
    for solve in manifest["solves"]:
        default = review_default(RecordReviewStatus(solve["review_status"]))
        if default is not None and not eligibility.lifts(default):
            raise ReplayError(
                f"solve {solve['solve_ref']} has review status {solve['review_status']}, which the recorded eligibility "
                f"settings keep out ({default}); they do not lift it"
            )
    profile = manifest["visibility"]["read_profile"]
    expected_withheld = ["deprecated", "rejected"] if profile == "curated" else []
    if manifest["visibility"].get("linked_evidence_withheld") != expected_withheld:
        raise ReplayError("the manifest's recorded linked-evidence rule is not the one its read profile applies")
    if profile == "curated" and effective != {"approved"}:
        raise ReplayError("a curated manifest's effective review statuses are approved only")
    stamped = manifest["request"].get("profile")
    if stamped is not None and stamped != profile:
        raise ReplayError("the manifest's visibility read profile is not the profile its request was made under")


_Verified = tuple[
    NetworkRequest, NetworkFacts, tuple[SolveFacts, ...], list[DeterminationAssessment], list[BundleAssessment], list[str]
]


def _verified_assessments(manifest: dict[str, Any]) -> _Verified:
    """Recompute every verdict from the captured inputs and refuse any that differs from the recorded one."""
    _check_versions(manifest)
    _check_consistency(manifest)
    request, network, solves = _inputs(manifest)
    dets, bundles, ungrouped = _recompute_assessments(request, network, solves)
    recomputed: dict[str, Any] = {
        "determinations": [a.to_dict() for a in dets],
        "bundles": [b.to_dict() for b in bundles],
        "ungrouped_fit_refs": list(ungrouped),
    }
    recorded = manifest["assessments"]
    for key in ("determinations", "bundles"):
        if recomputed[key] != recorded[key]:
            pairs: list[tuple[dict[str, Any], dict[str, Any]]] = list(
                zip(recorded[key], recomputed[key], strict=False)
            )
            differing = [r.get("determination_ref") or r.get("node_ref") for r, c in pairs if r != c] or [
                "the set of candidates"
            ]
            raise ReplayError(
                f"the recorded {key} assessments are not what the captured inputs give under assessment "
                f"v{ASSESSMENT_VERSION}: {differing}"
            )
    if recomputed["ungrouped_fit_refs"] != recorded["ungrouped_fit_refs"]:
        raise ReplayError("the recorded ungrouped fits are not the ones the captured inputs leave ungrouped")
    return request, network, solves, dets, bundles, ungrouped


def replay_network_assessment(manifest: dict[str, Any]) -> dict[str, Any]:
    """Recompute every applicability verdict from the captured normalised inputs; no database.

    :returns: ``{"determinations": [...], "bundles": [...], "ungrouped_fit_refs": [...]}`` as recomputed.
    :raises ReplayError: unknown format or version, or any recomputed verdict (or the grouping it rests on) that
        differs from the recorded one: a candidate recorded as eligible that the inputs do not make eligible, or the
        reverse, is a forged or stale document.
    """
    _, _, _, dets, bundles, ungrouped = _verified_assessments(manifest)
    return {
        "determinations": [a.to_dict() for a in dets],
        "bundles": [b.to_dict() for b in bundles],
        "ungrouped_fit_refs": list(ungrouped),
    }


def _decide_from(
    request: NetworkRequest,
    solves: tuple[SolveFacts, ...],
    dets: list[DeterminationAssessment],
    bundles: list[BundleAssessment],
) -> dict[str, Any]:
    candidates = build_candidates(request, solves, dets, bundles)
    return decide(candidates, request=request).to_dict()


def replay_network_decision(manifest: dict[str, Any]) -> dict[str, Any]:
    """Recompute the outcome, the fronts and the administrative order from *recomputed* assessments; no database.

    The candidates are built from freshly recomputed verdicts, never from recorded eligibility flags, and a caller
    cannot hand in assessments of its own: a decision replay that trusted them would reproduce any winner.

    :returns: ``NetworkDecision.to_dict()``.
    :raises ReplayError: for an unknown manifest format or version.
    """
    _check_versions(manifest)
    _check_consistency(manifest)
    request, network, solves = _inputs(manifest)
    dets, bundles, _ = _recompute_assessments(request, network, solves)
    return _decide_from(request, solves, dets, bundles)


def replay_network(manifest: dict[str, Any], *, check_digest: bool = True) -> dict[str, Any]:
    """Both replay levels, then a comparison with what the manifest records: decision *and* outcome.

    :raises ReplayError: on any refusal of either level, a digest that does not match the manifest body (when the
        manifest carries one and ``check_digest``), or a recomputed decision or outcome that differs from the
        recorded one.
    """
    if check_digest and "digest" in manifest and manifest["digest"]["value"] != manifest_digest(manifest):
        raise ReplayError("the manifest does not match its recorded digest")
    request, _, solves, dets, bundles, _ = _verified_assessments(manifest)
    decision = _decide_from(request, solves, dets, bundles)
    if decision["outcome"] != manifest["outcome"]:
        raise ReplayError(
            f"the recomputed outcome {decision['outcome']!r} differs from the recorded {manifest['outcome']!r}"
        )
    if decision != manifest["decision"]:
        raise ReplayError("the recomputed decision differs from the recorded decision")
    return decision


def replay_matches(manifest: dict[str, Any]) -> bool:
    """True when both levels reproduce what the manifest records; any refusal propagates."""
    return replay_network(manifest) == manifest["decision"]
