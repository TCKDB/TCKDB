"""A requested bundle: declared product sets of one solve, certified as a whole or not at all.

A bundle node is a declared solve and product set, never a combination of whatever fits exist, and no output
is filled from another solve. Missing is not zero; a declared zero is a separate, supported claim.
"""

from __future__ import annotations

from app.services.network_selection import Scope, assess_network
from app.services.selection_kernel import Applicability as A
from tests.services.network_selection._requests import bundle_request
from tests.services.network_selection._world import add_solve, fit_spec, target, validity


def assess(session, request):
    return assess_network(session, request=request, require_snapshot=False)


def out(channel: str, availability: str = "supplied", **extra):
    return {"channel_key": channel, "availability": availability, "required": True, **extra}


def reasons(node) -> set[str]:
    return {r.code for r in node.reasons}


def coverage(node) -> dict[str, str]:
    return {c.channel_key: c.status for c in node.coverage}


def two_channel_solve(db_session, world, **changes):
    """A solve answering ``assoc`` and ``elim`` whose declared product set holds both."""
    options = {
        "fits": [fit_spec("assoc"), fit_spec("elim")],
        "solve_target": target(world, outputs=[out("assoc"), out("elim")]),
        "product_sets": [{"key": "both", "members": [("d_assoc", []), ("d_elim", [])]}],
    }
    options.update(changes)
    return add_solve(db_session, world, **options)


def test_a_complete_declared_product_set_is_applicable_with_every_output_covered(db_session, world):
    solve = two_channel_solve(db_session, world)
    result = assess(db_session, bundle_request(world))
    (node,) = result.bundle_assessments
    assert node.applicability is A.applicable and node.physically_eligible
    assert node.node_ref == f"{solve.public_ref}/both" and node.solve_ref == solve.public_ref
    assert coverage(node) == {"assoc": "determination", "elim": "determination"}
    assert set(node.member_refs) == {d.public_ref for d in solve._dets.values()}  # one generating solve
    assert {a.determination_ref for a in result.determination_assessments} == set(node.member_refs)
    assert all(a.physically_eligible for a in result.determination_assessments)


def test_each_solve_wins_one_channel_and_no_network_winner_is_formed(db_session, world):
    """S carries assoc, T carries elim: neither product set answers a request for both, and none is spliced."""
    add_solve(
        db_session,
        world,
        fits=[fit_spec("assoc"), fit_spec("elim")],
        solve_target=target(world, outputs=[out("assoc"), out("elim")]),
        product_sets=[{"key": "only_assoc", "members": [("d_assoc", [])]}],
    )
    add_solve(
        db_session,
        world,
        fits=[fit_spec("assoc"), fit_spec("elim")],
        solve_target=target(world, outputs=[out("assoc"), out("elim")]),
        product_sets=[{"key": "only_elim", "members": [("d_elim", [])]}],
    )
    result = assess(db_session, bundle_request(world))
    first, second = result.bundle_assessments
    assert [n.applicability for n in result.bundle_assessments] == [A.incompatible, A.incompatible]
    assert "required_output_not_in_product_set:elim" in reasons(first)
    assert "required_output_not_in_product_set:assoc" in reasons(second)
    assert not any(n.physically_eligible for n in result.bundle_assessments)


def test_a_missing_output_is_not_zero_and_a_declared_zero_is_a_separate_supported_claim(db_session, world):
    # Absent from the catalog: the producer said nothing, so certification is unresolved.
    two_channel_solve(
        db_session,
        world,
        fits=[fit_spec("assoc")],
        solve_target=target(world, outputs=[out("assoc")]),
        product_sets=[{"key": "p", "members": [("d_assoc", [])]}],
    )
    (absent,) = assess(db_session, bundle_request(world)).bundle_assessments
    assert absent.applicability is A.unresolved and "required_output_not_in_catalog:elim" in reasons(absent)
    assert coverage(absent)["elim"] == "not_in_catalog"
    # Declared unavailable: known not to be given.
    two_channel_solve(
        db_session,
        world,
        fits=[fit_spec("assoc")],
        solve_target=target(world, outputs=[out("assoc"), out("elim", "unavailable")]),
        product_sets=[{"key": "p", "members": [("d_assoc", [])]}],
    )
    unavailable = assess(db_session, bundle_request(world)).bundle_assessments[1]
    assert unavailable.applicability is A.incompatible and coverage(unavailable)["elim"] == "unavailable"
    # Declared zero with a basis: covered, and said to be a declared zero, not a determination.
    two_channel_solve(
        db_session,
        world,
        fits=[fit_spec("assoc")],
        solve_target=target(world, outputs=[out("assoc"), out("elim", "declared_zero", zero_basis="source_statement")]),
        product_sets=[{"key": "p", "members": [("d_assoc", [])]}],
    )
    zero = assess(db_session, bundle_request(world)).bundle_assessments[2]
    assert zero.applicability is A.applicable and coverage(zero) == {"assoc": "determination", "elim": "declared_zero"}


def test_an_output_in_the_catalog_but_not_in_the_declared_set_is_incompatible(db_session, world):
    two_channel_solve(db_session, world, product_sets=[{"key": "p", "members": [("d_assoc", [])]}])
    (node,) = assess(db_session, bundle_request(world)).bundle_assessments
    assert node.applicability is A.incompatible and coverage(node)["elim"] == "missing"


def test_one_member_with_unknown_validity_leaves_the_whole_bundle_unresolved(db_session, world):
    two_channel_solve(
        db_session,
        world,
        solve_target=target(
            world,
            validity=None,
            outputs=[out("assoc", validity=validity()), out("elim")],  # elim has no validity of its own
        ),
    )
    (node,) = assess(db_session, bundle_request(world)).bundle_assessments
    assert node.applicability is A.unresolved
    assert "member:d_elim:physical_validity_not_declared" in reasons(node)
    assert not any(r.startswith("member:d_assoc") for r in reasons(node))  # the other member is fine
    assert not node.physically_eligible


def test_a_member_that_cannot_answer_the_domain_makes_the_bundle_incompatible_with_its_reason(db_session, world):
    two_channel_solve(
        db_session,
        world,
        fits=[fit_spec("assoc"), fit_spec("elim", tmax=900.0)],
    )
    (node,) = assess(db_session, bundle_request(world)).bundle_assessments
    assert node.applicability is A.incompatible and "member:d_elim:request_outside_fit_support" in reasons(node)


def test_several_declared_product_sets_are_distinct_competing_nodes(db_session, world):
    solve = two_channel_solve(
        db_session,
        world,
        product_sets=[
            {"key": "both", "members": [("d_assoc", []), ("d_elim", [])]},
            {"key": "also_both", "members": [("d_elim", []), ("d_assoc", [])]},
        ],
    )
    nodes = assess(db_session, bundle_request(world)).bundle_assessments
    assert [n.product_set_key for n in nodes] == ["both", "also_both"]
    assert [n.node_ref for n in nodes] == [f"{solve.public_ref}/both", f"{solve.public_ref}/also_both"]
    assert nodes[0].content_hash == nodes[1].content_hash  # same membership, still two declared nodes
    assert len({n.id_rank for n in nodes}) == 2


def test_a_product_set_whose_pinned_content_no_longer_matches_is_not_certified(db_session, world):
    solve = two_channel_solve(db_session, world, review=None)
    body = dict(solve.target_declaration)
    sets = [dict(s) for s in body["product_sets"]]
    sets[0]["members"] = sets[0]["members"][:1]  # membership edited, hash left as pinned
    body["product_sets"] = sets
    solve.target_declaration = body
    db_session.flush()
    from app.db.models.common import RecordReviewStatus
    from tests.services.network_selection._world import set_review

    set_review(db_session, world, solve, RecordReviewStatus.approved)
    (node,) = assess(db_session, bundle_request(world)).bundle_assessments
    assert node.applicability is A.incompatible and "product_set_content_mismatch" in reasons(node)


def test_a_member_may_pin_which_representations_the_set_contains(db_session, world):
    solve = add_solve(
        db_session,
        world,
        fits=[
            fit_spec("assoc", rep="plog1"),
            fit_spec("assoc", rep="cheb1", model="chebyshev", pmin=0.01, pmax=100.0),
            fit_spec("elim"),
        ],
        solve_target=target(world, outputs=[out("assoc"), out("elim")]),
        product_sets=[
            {"key": "plog_only", "members": [("d_assoc", ["plog1"]), ("d_elim", [])]},
            {"key": "cheb_only", "members": [("d_assoc", ["cheb1"]), ("d_elim", [])]},
        ],
    )
    result = assess(db_session, bundle_request(world))
    assert [n.applicability for n in result.bundle_assessments] == [A.applicable, A.applicable]
    assert solve is not None


def test_a_pinned_representation_that_is_not_eligible_leaves_the_set_incompatible(db_session, world):
    add_solve(
        db_session,
        world,
        fits=[
            fit_spec("assoc", rep="plog1"),
            fit_spec("assoc", rep="cheb1", model="chebyshev", pmin=2.0, pmax=3.0),  # does not cover the window
            fit_spec("elim"),
        ],
        solve_target=target(world, outputs=[out("assoc"), out("elim")]),
        product_sets=[{"key": "cheb_only", "members": [("d_assoc", ["cheb1"]), ("d_elim", [])]}],
    )
    (node,) = assess(db_session, bundle_request(world)).bundle_assessments
    assert node.applicability is A.incompatible
    assert "member:d_assoc:no_chosen_representation_is_eligible" in reasons(node)


# -- full network ------------------------------------------------------------------------------


def full_solve(db_session, world, **changes):
    channels = ("assoc", "diss", "elim", "elim_alt")
    options = {
        "fits": [fit_spec(c) for c in channels],
        "solve_target": target(world, outputs=[out(c) for c in channels]),
        "product_sets": [{"key": "all", "members": [(f"d_{c}", []) for c in channels]}],
    }
    options.update(changes)
    return add_solve(db_session, world, **options)


def full_request(world, *channels, **changes):
    return bundle_request(world, *(channels or ("assoc", "diss", "elim", "elim_alt")), scope=Scope.full_network, **changes)


def test_a_full_network_certification_needs_catalog_boundaries_and_every_required_output(db_session, world):
    full_solve(db_session, world)
    (node,) = assess(db_session, full_request(world)).bundle_assessments
    assert node.applicability is A.applicable


def test_a_full_network_request_cannot_silently_omit_a_required_output(db_session, world):
    full_solve(db_session, world)
    (node,) = assess(db_session, full_request(world, "assoc", "elim", "elim_alt")).bundle_assessments
    assert node.applicability is A.incompatible and "request_omits_required_output:diss" in reasons(node)
    # The same request as a projection names a subset and is certified for that subset only.
    (projected,) = assess(db_session, bundle_request(world, "assoc", "elim", "elim_alt")).bundle_assessments
    assert projected.applicability is A.applicable


def test_a_full_network_without_a_catalog_or_boundary_semantics_is_unresolved(db_session, world):
    full_solve(db_session, world, solve_target=target(world, outputs=[]))
    (no_catalog,) = assess(db_session, full_request(world)).bundle_assessments
    assert no_catalog.applicability is A.unresolved and "output_catalog_not_declared" in reasons(no_catalog)
    full_solve(db_session, world, solve_target=target(world, boundaries=None, outputs=[out(c) for c in ("assoc", "diss", "elim", "elim_alt")]))
    no_boundaries = assess(db_session, full_request(world)).bundle_assessments[1]
    assert no_boundaries.applicability is A.unresolved and "boundary_semantics_not_declared" in reasons(no_boundaries)


def test_a_boundary_loss_missing_from_the_catalog_leaves_closure_unresolved(db_session, world):
    full_solve(
        db_session,
        world,
        solve_target=target(world, outputs=[out(c) for c in ("assoc", "diss", "elim")]),  # elim_alt ends in the absorbing exit
    )
    (node,) = assess(db_session, full_request(world, "assoc", "diss", "elim")).bundle_assessments
    assert node.applicability is A.unresolved and "boundary_loss_not_in_catalog:elim_alt" in reasons(node)


def test_a_solve_without_product_sets_cannot_answer_a_bundle_and_is_noted(db_session, world):
    add_solve(db_session, world, fits=[fit_spec("assoc"), fit_spec("elim")])
    result = assess(db_session, bundle_request(world))
    assert result.bundle_assessments == ()
    assert any("declare no product set" in n for n in result.notes)
