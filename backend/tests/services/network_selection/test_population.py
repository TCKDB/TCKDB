"""The authorized population: review qualification, visibility, bounds, snapshot order and determinism.

A bound is a resource limit applied to the *complete* authorized population before any applicability filtering;
exceeding one refuses the decision and never selects from a prefix. What a refusal names is only what the read
profile allows the caller to know.
"""

from __future__ import annotations

import dataclasses
import json

import pytest

from app.api.error_contract import CodedValueError
from app.api.errors import NotFoundError
from app.db.models.common import RecordReviewStatus
from app.db.models.network_pdep import (
    NetworkKinetics,
    NetworkKineticsChebyshev,
    NetworkKineticsDetermination,
    NetworkKineticsPlog,
    NetworkSolve,
)
from app.services.network_selection import BOUNDS_V1, Scope, SelectionBounds, assess_network, check_bound
from app.services.network_selection import service as service_module
from app.services.read_snapshot import SnapshotNotConsistentError
from app.services.scientific_read.profile import (
    ProfileRecommendation,
    ReadProfile,
    ResolvedReadProfile,
    reset_current_read_profile,
    set_current_read_profile,
)
from app.services.selection_kernel import Applicability as A
from tests.services.network_selection._requests import bundle_request, channel_request
from tests.services.network_selection._world import add_solve, fit_spec, target, validity

S = RecordReviewStatus


def assess(session, request):
    return assess_network(session, request=request, require_snapshot=False)


def with_bounds(request, **limits):
    return dataclasses.replace(request, bounds=dataclasses.replace(BOUNDS_V1, **limits))


def refused(session, request) -> dict:
    with pytest.raises(CodedValueError) as caught:
        assess(session, request)
    assert caught.value.code == "network_selection_population_too_large"
    return caught.value.context


# -- qualification ------------------------------------------------------------------------------


def test_rejected_and_deprecated_solves_never_enter_default_selection_and_are_listed_with_why(db_session, world):
    keep = add_solve(db_session, world, fits=[fit_spec("assoc")])
    rejected = add_solve(db_session, world, fits=[fit_spec("assoc", det="d2")], review=S.rejected)
    deprecated = add_solve(db_session, world, fits=[fit_spec("assoc", det="d3")], review=S.deprecated)
    result = assess(db_session, channel_request(world))
    assert [a.solve_ref for a in result.determination_assessments] == [keep.public_ref]  # evaluation is not endorsement
    assert {(e["solve_ref"], e["reason"]) for e in result.excluded_by_review} == {
        (rejected.public_ref, "terminal_review_status"),
        (deprecated.public_ref, "terminal_review_status"),
    }
    assert result.excluded_count == 2 and result.counts["solves"] == 1


def test_the_callers_review_floor_is_applied_without_relaxation(db_session, world):
    add_solve(db_session, world, fits=[fit_spec("assoc")], review=S.not_reviewed)
    approved = add_solve(db_session, world, fits=[fit_spec("assoc", det="d2")], review=S.approved)
    result = assess(db_session, channel_request(world, min_review_status=S.approved))
    assert [a.solve_ref for a in result.determination_assessments] == [approved.public_ref]
    assert [e["reason"] for e in result.excluded_by_review] == ["below_review_floor"]
    assert S.not_reviewed not in result.effective_statuses and S.approved in result.effective_statuses


def test_a_fit_inherits_its_solves_review_and_is_never_qualified_separately(db_session, world):
    solve = add_solve(db_session, world, fits=[fit_spec("assoc")], review=S.rejected)
    result = assess(db_session, channel_request(world))
    assert result.determination_assessments == ()
    assert result.solves == () and solve.public_ref in {e["solve_ref"] for e in result.excluded_by_review}


# -- bounds ---------------------------------------------------------------------------------------


def test_the_documented_bounds_are_the_planned_ones_and_versioned():
    assert BOUNDS_V1 == SelectionBounds(
        version="1",
        solves=200,
        kinetics_parents=2000,
        channel_nodes=500,
        bundle_nodes=500,
        states=1000,
        channels=2000,
        required_outputs=200,
        evidence_entries=10000,
        numeric_cells=100000,
        snapshot_bytes=8 * 1024 * 1024,
    )
    with pytest.raises(CodedValueError) as caught:
        check_bound(BOUNDS_V1, "solves", 201)
    assert caught.value.context == {"bound": "solves", "visible": 201, "limit": 200, "bounds_version": "1"}
    check_bound(BOUNDS_V1, "solves", 200)  # the limit itself is decided


def test_the_solve_bound_is_applied_to_the_whole_population_at_and_beyond_its_limit(db_session, world):
    for index in range(2):
        add_solve(db_session, world, fits=[fit_spec("assoc", det=f"d{index}")])
    assert len(assess(db_session, with_bounds(channel_request(world), solves=2)).solves) == 2
    add_solve(db_session, world, fits=[fit_spec("assoc", det="d2")])
    context = refused(db_session, with_bounds(channel_request(world), solves=2))
    assert context == {"bound": "solves", "visible": 3, "limit": 2, "bounds_version": "1"}


def test_the_bound_counts_before_applicability_so_a_population_of_incompatible_solves_still_refuses(db_session, world):
    for index in range(3):
        add_solve(db_session, world, fits=[fit_spec("assoc", det=f"d{index}", tmax=900.0)])  # none answers the request
    assert refused(db_session, with_bounds(channel_request(world), solves=2))["visible"] == 3


def test_rejected_and_deprecated_solves_do_not_count_toward_the_solve_bound(db_session, world):
    add_solve(db_session, world, fits=[fit_spec("assoc")])
    add_solve(db_session, world, fits=[fit_spec("assoc", det="d2")])
    add_solve(db_session, world, fits=[fit_spec("assoc", det="d3")], review=S.rejected)
    assert len(assess(db_session, with_bounds(channel_request(world), solves=2)).solves) == 2


def test_nothing_is_assessed_or_chosen_from_a_prefix_when_a_bound_is_exceeded(db_session, world, monkeypatch):
    for index in range(3):
        add_solve(db_session, world, fits=[fit_spec("assoc", det=f"d{index}")])

    def boom(*args, **kwargs):  # pragma: no cover - must never run
        raise AssertionError("assessment ran on a population that exceeded a bound")

    monkeypatch.setattr(service_module, "assess_channel_scope", boom)
    monkeypatch.setattr(service_module, "assess_bundle_scope", boom)
    refused(db_session, with_bounds(channel_request(world), solves=2))
    with pytest.raises(AssertionError):  # the same population within its bound does reach the assessor
        assess(db_session, with_bounds(channel_request(world), solves=3))


@pytest.mark.parametrize(
    "bound, limit_ok, build",
    [
        ("kinetics_parents", 3, "fits"),
        ("channel_nodes", 2, "determinations"),
        ("bundle_nodes", 2, "product_sets"),
        ("states", 3, "network"),
        ("channels", 4, "network"),
        ("evidence_entries", 2, "evidence"),
        ("numeric_cells", 7, "cells"),
    ],
)
def test_every_bound_is_enforced_at_its_limit_and_refused_one_beyond(db_session, world, bound, limit_ok, build):
    bundle = bound == "bundle_nodes"
    if build == "fits":
        add_solve(db_session, world, fits=[fit_spec("assoc"), fit_spec("assoc", det="d2"), fit_spec("assoc", det="d3")])
    elif build == "determinations":
        add_solve(db_session, world, fits=[fit_spec("assoc"), fit_spec("assoc", det="d2")])
    elif build == "product_sets":
        add_solve(
            db_session,
            world,
            fits=[fit_spec("assoc"), fit_spec("elim")],
            solve_target=target(world, outputs=[{"channel_key": "assoc", "availability": "supplied", "required": True}]),
            product_sets=[
                {"key": "p1", "members": [("d_assoc", []), ("d_elim", [])]},
                {"key": "p2", "members": [("d_elim", []), ("d_assoc", [])]},
            ],
        )
    elif build == "evidence":
        entry = {"kind": "convergence", "metric": "max_relative_error", "value": 0.01, "domain": validity()}
        add_solve(db_session, world, fits=[fit_spec("assoc")], validation={"version": 1, "entries": [entry, entry]})
    elif build == "cells":  # 3 PLOG rows + a 2 x 2 Chebyshev grid
        add_solve(
            db_session,
            world,
            fits=[fit_spec("assoc"), fit_spec("assoc", det="d2", model="chebyshev", pmin=0.01, pmax=100.0)],
        )
    else:
        add_solve(db_session, world, fits=[fit_spec("assoc")])
    make = (lambda **c: with_bounds(bundle_request(world), **c)) if bundle else (lambda **c: with_bounds(channel_request(world), **c))
    assess(db_session, make(**{bound: limit_ok}))  # at the limit: decided
    context = refused(db_session, make(**{bound: limit_ok - 1}))
    assert context["bound"] == bound and context["limit"] == limit_ok - 1 and context["visible"] == limit_ok


def test_the_number_of_required_outputs_is_bounded_when_the_request_is_built(world):
    with pytest.raises(ValueError, match="at most 1 required outputs"):
        with_bounds(bundle_request(world, "assoc", "elim"), required_outputs=1)
    check_bound(dataclasses.replace(BOUNDS_V1, required_outputs=1), "required_outputs", 1)
    with pytest.raises(CodedValueError):
        check_bound(dataclasses.replace(BOUNDS_V1, required_outputs=1), "required_outputs", 2)


def test_a_snapshot_over_the_size_limit_is_its_own_coded_refusal(db_session, world):
    add_solve(db_session, world, fits=[fit_spec("assoc")])
    assert assess(db_session, with_bounds(channel_request(world), snapshot_bytes=10**6)).solves
    with pytest.raises(CodedValueError) as caught:
        assess(db_session, with_bounds(channel_request(world), snapshot_bytes=500))
    assert caught.value.code == "network_selection_snapshot_too_large"
    assert caught.value.context["limit_bytes"] == 500 and caught.value.context["size_bytes"] > 500


# -- visibility -----------------------------------------------------------------------------------


def curated():
    return set_current_read_profile(
        ResolvedReadProfile(profile=ReadProfile.curated, recommendation=ProfileRecommendation.approved_floor_only)
    )


def test_under_curated_hidden_solves_are_not_counted_listed_or_named_in_a_refusal(db_session, world):
    visible = [add_solve(db_session, world, fits=[fit_spec("assoc", det=f"v{i}")]) for i in range(2)]
    hidden = [add_solve(db_session, world, fits=[fit_spec("assoc", det=f"h{i}")], review=S.not_reviewed) for i in range(5)]
    token = curated()
    try:
        context = refused(db_session, with_bounds(channel_request(world), solves=1))
        assert context["visible"] == 2  # never 7
        result = assess(db_session, with_bounds(channel_request(world), solves=2))
        assert {s.solve_ref for s in result.solves} == {s.public_ref for s in visible}
        assert result.excluded_count == 0 and result.excluded_by_review == ()
        assert not ({h.public_ref for h in hidden} & set(json.dumps(result.solves[0].to_dict()).split('"')))
    finally:
        reset_current_read_profile(token)
    # Without the profile the same population is fully visible.
    assert len(assess(db_session, channel_request(world)).solves) == 7


def test_a_reference_model_must_be_a_visible_solve_of_this_network_and_a_hidden_one_reads_as_unknown(db_session, world):
    from tckdb_schemas.network_declarations import NetworkComparisonObjective

    from app.db.models.common import RecordReviewStatus

    add_solve(db_session, world, fits=[fit_spec("assoc")])
    reference = add_solve(db_session, world, fits=[fit_spec("assoc", det="ref")])
    hidden = add_solve(db_session, world, fits=[fit_spec("assoc", det="hid")], review=S.not_reviewed)
    objective = NetworkComparisonObjective.model_fidelity
    assert assess(db_session, channel_request(world, objective=objective, reference_model_ref=reference.public_ref)).solves
    token = curated()
    try:
        with pytest.raises(NotFoundError):
            assess(db_session, channel_request(world, objective=objective, reference_model_ref=hidden.public_ref))
    finally:
        reset_current_read_profile(token)
    with pytest.raises(NotFoundError):
        assess(db_session, channel_request(world, objective=objective, reference_model_ref="nsolve_nonesuch"))
    assert RecordReviewStatus.approved


# -- snapshot, errors, determinism ------------------------------------------------------------------


def test_the_snapshot_opens_before_the_network_ref_is_resolved(db_session, world):
    """An unknown ref must meet the snapshot refusal first: the isolation level cannot change after a statement."""
    request = channel_request(world, network_ref="net_nonesuch")
    with pytest.raises(SnapshotNotConsistentError):
        assess_network(db_session, request=request, require_snapshot=True)
    with pytest.raises(NotFoundError):  # a caller that accepts a weaker transaction gets the ordinary 404
        assess_network(db_session, request=request, require_snapshot=False)


def test_the_result_reports_the_isolation_actually_in_force(db_session, world):
    add_solve(db_session, world, fits=[fit_spec("assoc")])
    assert assess(db_session, channel_request(world)).snapshot_isolation in {"read committed", "repeatable read"}


def test_a_request_that_names_states_or_channels_the_network_lacks_is_refused_not_answered_empty(db_session, world):
    from app.services.network_selection import PartitionRequest

    add_solve(db_session, world, fits=[fit_spec("assoc")])
    unknown_state = channel_request(world, partition=PartitionRequest(retained=("f" * 64,)))
    with pytest.raises(CodedValueError) as caught:
        assess(db_session, unknown_state)
    assert caught.value.code == "network_declaration_invalid" and caught.value.context["field"] == "request.partition"
    with pytest.raises(CodedValueError) as caught:
        assess(db_session, channel_request(world, channel_key="no_such_channel"))
    assert caught.value.context["field"] == "request.channel_key"
    with pytest.raises(CodedValueError) as caught:
        assess(db_session, bundle_request(world, "assoc", "no_such_channel"))
    assert caught.value.context["field"] == "request.outputs"


def test_an_unknown_network_is_not_found(db_session, world):
    with pytest.raises(NotFoundError):
        assess(db_session, channel_request(world, network_ref="net_nonesuch"))


def test_assessment_writes_nothing(db_session, world):
    from sqlalchemy import func, select

    add_solve(db_session, world, fits=[fit_spec("assoc"), fit_spec("assoc", det="d2", model="chebyshev", pmin=0.01, pmax=100.0)])
    models = (NetworkSolve, NetworkKinetics, NetworkKineticsDetermination, NetworkKineticsPlog, NetworkKineticsChebyshev)

    def counts():
        return [db_session.scalar(select(func.count()).select_from(m)) for m in models]

    before = counts()
    assess(db_session, channel_request(world))
    assess(db_session, bundle_request(world))
    assert counts() == before and not db_session.dirty and not db_session.new


def test_normalised_facts_carry_public_refs_and_no_row_ids(db_session, world):
    add_solve(db_session, world, fits=[fit_spec("assoc")])
    result = assess(db_session, channel_request(world))

    def walk(value, path=""):
        if isinstance(value, dict):
            for key, item in value.items():
                assert key != "id" and not key.endswith("_id"), f"{path}.{key}"
                walk(item, f"{path}.{key}")
        elif isinstance(value, list):
            for index, item in enumerate(value):
                walk(item, f"{path}[{index}]")

    walk(result.network.to_dict())
    for solve in result.solves:
        walk(solve.to_dict())
    for assessment in result.determination_assessments:
        walk(assessment.to_dict())
    assert result.solves[0].solve_ref.startswith("nsolve_") and result.solves[0].fits[0].fit_ref.startswith("nkin_")


def test_the_same_population_yields_the_same_canonical_facts_and_verdicts(db_session, world):
    add_solve(db_session, world, fits=[fit_spec("assoc"), fit_spec("assoc", det="d2", rep="x")])
    first = assess(db_session, channel_request(world))
    second = assess(db_session, channel_request(world))
    dump = lambda r: json.dumps(  # noqa: E731
        [
            r.network.to_dict(),
            [s.to_dict() for s in r.solves],
            [a.to_dict() for a in r.determination_assessments],
            r.request.to_dict(),
        ],
        sort_keys=True,
    )
    assert dump(first) == dump(second)
    assert service_module.snapshot_size_bytes(first.request, first.network, first.solves) == service_module.snapshot_size_bytes(
        second.request, second.network, second.solves
    )


def test_a_request_round_trips_through_its_manifest_form(world):
    request = channel_request(world)
    assert type(request).from_dict(request.to_dict()) == request
    bundle = bundle_request(world, scope=Scope.full_network)
    assert type(bundle).from_dict(json.loads(json.dumps(bundle.to_dict()))) == bundle


def test_the_applicability_vocabulary_is_the_shared_kernels(db_session, world):
    add_solve(db_session, world, fits=[fit_spec("assoc")])
    assert assess(db_session, channel_request(world)).determination_assessments[0].applicability is A.applicable
