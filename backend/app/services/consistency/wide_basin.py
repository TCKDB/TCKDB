"""D8: a conformer group whose optimised structures are not all within tolerance of each other (a wide basin).

A conformer basin is the set of converged optimised structures connected by "same basin" matches
(``docs``/ADR 0005). A chain of such structures can be linked end to end while its two ends are further apart than
the matching tolerance: each pair of neighbours is the same minimum, the ends may not be. TCKDB reports that and never
splits it, because any automatic cut would put back either an arbitrary threshold or a dependence on the order of
deposition.

This check is how the report reaches a curator. It is advisory in ADR 0008's sense -- one append-only
``record_machine_review`` row a curator may read, no status, selection, trust or approval effect, nothing rewritten.
It reads only what the group stores about itself (``basin_is_clique``, ``basin_span_deg``), which the upload path and
the recompute derive from the group's defining members; it does not compare structures again.

What is judged: a group with at least one defining member (``basin_is_clique`` not NULL). A group of partly
optimised structures only (a provisional basin) has no clique question to ask and is reported as not judged, never as
agreeing.
"""
from app.services.consistency.core import AdvisoryResult, encoded, finding, snapshot
from app.services.trust.rubrics import WIDE_BASIN_CONSISTENCY

RUNNER = "wide_basin_consistency"

WIDE_BASIN = "wide_basin"
NO_DEFINING_MEMBER_NOT_JUDGED = "no_defining_member_not_judged"
ALL_DEFINING_MEMBERS_WITHIN_TOLERANCE = "all_defining_members_within_tolerance"


def compare_wide_basin(group):
    """Pure comparison of one resolved conformer group against what it stores about its own shape."""
    refs = [group.public_ref]
    context = {"basin_span_deg": group.basin_span_deg}
    if group.basin_is_clique is None:
        findings = [finding(group, {**context, "reason": NO_DEFINING_MEMBER_NOT_JUDGED}, refs)]
    elif group.basin_is_clique:
        findings = [finding(group, {**context, "reason": ALL_DEFINING_MEMBERS_WITHIN_TOLERANCE}, refs)]
    else:
        findings = [finding(group, {**context, "reason": WIDE_BASIN}, refs)]
    return AdvisoryResult(group, RUNNER, WIDE_BASIN_CONSISTENCY, tuple(findings), encoded({
        "target": snapshot(group, exclude=frozenset({"representative_fingerprint_json", "representative_coords_json"})),
        "policy": "defining-members-only;stored-shape;a-chain-is-reported-never-split",
    }))
