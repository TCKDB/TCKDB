"""Network select: the caller's filters, and the named eligibility defaults a request can lift.

Review and provenance belong to a network solve, so the unit the filters and the defaults act on is the solve (the
answer says ``unit: solves``). A solve has no evidence rubric and no source findings of its own, so only its review
status is a default here; a request naming the other two is refused rather than silently ignored.
"""

from __future__ import annotations

import copy

import pytest

from app.db.models.common import NetworkSolveCalculationRole
from app.db.models.common import RecordReviewStatus as S
from app.services.network_selection import ReplayError, replay_network
from tests.api.scientific.test_api_network_select import _approve_network, post, question
from tests.services.network_selection._protocol import protocol
from tests.services.network_selection._world import add_solve, build_world, fit_spec, set_review
from tests.services.scientific_read._factories import (
    attach_network_solve_source_calculation,
    make_calculation,
    make_lot,
    make_software_release,
)

A_, B_, C_ = "chemically_significant_eigenvalues", "modified_strong_collision", "reservoir_state"
DEFAULTS = ["rejected_review_status", "deprecated_review_status"]


@pytest.fixture
def world(db_session):
    return build_world(db_session)


def solve(session, world, proto, *, review=S.approved, software=None, lot=None, model="plog"):
    # the review is set last: an approved solve is frozen
    s = add_solve(session, world, fits=[fit_spec("assoc", model=model)], protocol=protocol(proto), review=None)
    if software or lot:
        release = make_software_release(session, name=software) if software else None
        lot_row = make_lot(session, method=lot[0], basis=lot[1]) if lot else None
        calc = make_calculation(
            session, species_entry_id=world.ar.id,
            lot_id=lot_row.id if lot_row else None, software_release_id=release.id if release else None,
        )
        attach_network_solve_source_calculation(
            session, solve=s, calculation=calc, role=NetworkSolveCalculationRole.master_equation_run
        )
    if review is not None:
        set_review(session, world, s, review)
    return s


def entry_of(body, name):
    return next(e for e in body["eligibility"] if e["name"] == name)


def refs(body):
    return {d["solve_ref"] for d in body["determinations"]}


# -- step 1: filters ---------------------------------------------------------------------------------------------


def test_a_software_filter_keeps_the_solves_whose_source_calculations_ran_there(client, db_session, world):
    kept = solve(db_session, world, A_, software="orca")
    solve(db_session, world, B_, software="gaussian")
    solve(db_session, world, C_)
    assert len(refs(post(client, world).json())) == 3
    body = post(client, world, {**question(world), "filters": {"software": "orca"}}).json()
    assert refs(body) == {kept.public_ref}
    assert body["outcome"] == "sole_eligible_candidate"
    assert body["filters"]["applied"] == {"software": "orca"} and body["filters"]["removed"] == 2
    assert body["funnel"] == {
        "unit": "solves", "considered": 3, "removed_by_filters": 2, "removed_by_eligibility": 0, "remaining": 1
    }


def test_method_and_basis_match_one_calculation_together(client, db_session, world):
    kept = solve(db_session, world, A_, lot=("wb97xd", "def2tzvp"))
    solve(db_session, world, B_, lot=("wb97xd", "6-31g"))
    body = post(client, world, {**question(world), "filters": {"method": "wb97xd", "basis": "def2tzvp"}}).json()
    assert refs(body) == {kept.public_ref}


def test_a_model_kind_filter_keeps_only_fits_of_that_form(client, db_session, world):
    plog = solve(db_session, world, A_, model="plog")
    solve(db_session, world, B_, model="chebyshev")
    body = post(client, world, {**question(world), "filters": {"model_kind": "plog"}}).json()
    assert refs(body) == {plog.public_ref}
    assert body["filters"]["removed"] == 1


def test_a_filter_the_search_does_not_offer_is_refused(client, db_session, world):
    solve(db_session, world, A_)
    assert post(client, world, {**question(world), "filters": {"scientific_origin": "computed"}}).status_code == 422
    assert post(client, world, {**question(world), "filters": {"level_of_theory_ref": "lot_x"}}).status_code == 422


# -- step 2: eligibility -------------------------------------------------------------------------------------------


def test_every_default_is_echoed_and_only_the_review_ones_are_offered(client, db_session, world):
    solve(db_session, world, A_)
    body = post(client, world).json()
    assert [e["name"] for e in body["eligibility"]] == DEFAULTS
    assert all(e["applied"] and e["source"] == "default" for e in body["eligibility"])
    refused = post(client, world, {**question(world), "eligibility": {"include_hard_failed_evidence": True}})
    assert refused.status_code == 422


def test_include_rejected_admits_exactly_the_rejected_solve_and_says_so(client, db_session, world):
    approved = solve(db_session, world, A_, review=S.approved)
    rejected = solve(db_session, world, B_, review=S.rejected)
    deprecated = solve(db_session, world, C_, review=S.deprecated)

    default = post(client, world).json()
    assert refs(default) == {approved.public_ref}
    assert entry_of(default, "rejected_review_status")["excluded"] == 1
    assert entry_of(default, "deprecated_review_status")["excluded"] == 1
    assert default["funnel"]["removed_by_eligibility"] == 2

    lifted = post(client, world, {**question(world), "eligibility": {"include_rejected": True}}).json()
    assert refs(lifted) == {approved.public_ref, rejected.public_ref}
    by_solve = {s["solve_ref"]: s for s in lifted["solves"]}
    assert by_solve[rejected.public_ref]["passed_only_by_override"] == ["rejected_review_status"]
    assert by_solve[approved.public_ref]["passed_only_by_override"] == []
    echo = entry_of(lifted, "rejected_review_status")
    assert (echo["applied"], echo["source"], echo["admitted_by_override"]) == (False, "request", 1)
    assert entry_of(lifted, "deprecated_review_status")["applied"] is True
    assert deprecated.public_ref not in {s['solve_ref'] for s in lifted['solves']}


def test_curated_cannot_be_overridden(client, db_session, world):
    _approve_network(db_session, world)
    approved = solve(db_session, world, A_, review=S.approved)
    for proto, status in ((B_, S.rejected), (C_, S.deprecated)):
        solve(db_session, world, proto, review=status)
    body = post(
        client, world, {**question(world), "eligibility": {"include_rejected": True, "include_deprecated": True}},
        profile="curated",
    ).json()
    assert refs(body) == {approved.public_ref}
    for name in DEFAULTS:
        e = entry_of(body, name)
        assert (e["applied"], e["source"], e["excluded"], e["admitted_by_override"]) == (True, "read_profile", 0, 0)


# -- replay -------------------------------------------------------------------------------------------------------


def test_a_decision_made_with_filters_and_overrides_replays(client, db_session, world):
    solve(db_session, world, A_, review=S.rejected, software="orca")
    solve(db_session, world, B_, review=S.approved, software="orca")
    solve(db_session, world, C_, review=S.approved, software="gaussian")
    request = {**question(world), "filters": {"software": "orca"}, "eligibility": {"include_rejected": True}}
    response = post(client, world, request).json()
    manifest = post(client, world, request, manifest=True).json()
    assert manifest["request"]["filters"] == {"software": "orca"}
    assert manifest["request"]["eligibility"]["include_rejected"] is True
    assert len(manifest["solves"]) == 2
    assert replay_network(manifest)["fronts"] == response["fronts"]


def test_replay_refuses_a_manifest_whose_overrides_were_removed_or_edited(client, db_session, world):
    solve(db_session, world, A_, review=S.rejected)
    solve(db_session, world, B_, review=S.approved)
    manifest = post(client, world, {**question(world), "eligibility": {"include_rejected": True}}, manifest=True).json()
    assert replay_network(manifest)
    tampered = copy.deepcopy(manifest)
    tampered["request"]["eligibility"]["include_rejected"] = False
    with pytest.raises(ReplayError, match="keep out"):
        replay_network(tampered, check_digest=False)
    stripped = copy.deepcopy(manifest)
    del stripped["request"]["eligibility"]
    with pytest.raises(Exception):
        replay_network(stripped, check_digest=False)
