"""More matching sites under the evidence rule (ADR 0023, step 4): ref-named far ends, networks, correction schemes.

Same shape as ``test_search_filters_hidden_linked_records_curated``: an approved record, one approved link and one link
whose review state the test sets. A rejected or deprecated link never matches under curated; a not-reviewed or
under-review one does; exploratory withholds nothing. Each test names the mutation it kills.
"""

from __future__ import annotations

import pytest

from app.db.models.common import (
    CalculationDependencyRole,
    CalculationType,
    NetworkSolveCalculationRole,
    RecordReviewStatus,
)
from tests.api.scientific.test_search_filters_hidden_linked_records_curated import (
    API,
    LINK_STATUSES,
    T,
    _approve,
    _entry,
    _found,
    _search_conformers,
    _set,
)
from tests.services.network_selection._world import add_solve, build_world, fit_spec
from tests.services.network_selection._world import set_review as set_solve_review
from tests.services.scientific_read._factories import (
    attach_dependency,
    attach_network_solve_source_calculation,
    make_applied_energy_correction,
    make_calculation,
    make_conformer_group,
    make_conformer_observation,
    make_energy_correction_scheme,
    make_software_release,
)

S = RecordReviewStatus

#: Review states and whether the curated profile lets a link in that state take part in a match.
STATUSES = [
    pytest.param(status, matches, id=pid)
    for pid, (status, matches) in zip(
        ("not_reviewed", "under_review", "rejected", "deprecated"),
        ((None, True), (S.under_review, True), (S.rejected, False), (S.deprecated, False)),
        strict=True,
    )
]


# ---------------------------------------------------------------------------
# Calculation search: a far end named by ref, and the child-side role branch
# ---------------------------------------------------------------------------


def _edge(db_session, far_status, *, far="parent"):
    """An edge opt -> freq (role freq_on). The far end (the one the filter names) is in ``far_status``; the returned
    calculation is approved."""
    entry = _entry(db_session, "MFD")
    opt = make_calculation(db_session, type=CalculationType.opt, species_entry_id=entry.id)
    freq = make_calculation(db_session, type=CalculationType.freq, species_entry_id=entry.id)
    attach_dependency(db_session, parent=opt, child=freq, role=CalculationDependencyRole.freq_on)
    subject, other = (freq, opt) if far == "parent" else (opt, freq)
    _approve(db_session, T.calculation, subject.id)
    _set(db_session, T.calculation, other.id, far_status)
    return entry, subject, other


def _calc_search(client, body, profile):
    return client.post(f"{API}/calculations/search?profile={profile}", json=body)


@pytest.mark.parametrize("far_status,matches", STATUSES)
def test_calculation_parent_ref_names_a_far_end_the_rule_may_withhold(client, db_session, far_status, matches):
    """Kills: parent_calculation_ref matching the child through a rejected/deprecated parent (or refusing a not-reviewed one)."""
    entry, child, parent = _edge(db_session, far_status, far="parent")
    body = {"parent_calculation_ref": parent.public_ref, "species_entry_ref": entry.public_ref}
    assert _found(_calc_search(client, body, "curated"), child.public_ref) is matches
    assert _found(_calc_search(client, body, "exploratory"), child.public_ref)


@pytest.mark.parametrize("far_status,matches", STATUSES)
def test_calculation_child_ref_names_a_far_end_the_rule_may_withhold(client, db_session, far_status, matches):
    """Kills: child_calculation_ref matching the parent through a rejected/deprecated child."""
    entry, parent, child = _edge(db_session, far_status, far="child")
    body = {"child_calculation_ref": child.public_ref, "species_entry_ref": entry.public_ref}
    assert _found(_calc_search(client, body, "curated"), parent.public_ref) is matches
    assert _found(_calc_search(client, body, "exploratory"), parent.public_ref)


@pytest.mark.parametrize("far_status,matches", STATUSES)
def test_calculation_both_refs_need_both_ends(client, db_session, far_status, matches):
    """Kills: the both-refs branch ignoring a withheld end of the exact edge."""
    entry, child, parent = _edge(db_session, far_status, far="parent")
    body = {
        "parent_calculation_ref": parent.public_ref,
        "child_calculation_ref": child.public_ref,
        "species_entry_ref": entry.public_ref,
    }
    assert _found(_calc_search(client, body, "curated"), child.public_ref) is matches
    assert _found(_calc_search(client, body, "exploratory"), child.public_ref)


@pytest.mark.parametrize("far_status,matches", STATUSES)
def test_calculation_dependency_role_matches_a_child_through_its_parent(client, db_session, far_status, matches):
    """Kills: the child-side branch of dependency_role (subject is the child, the far end is the parent)."""
    entry, child, _parent = _edge(db_session, far_status, far="parent")
    body = {"dependency_role": "freq_on", "species_entry_ref": entry.public_ref}
    assert _found(_calc_search(client, body, "curated"), child.public_ref) is matches
    assert _found(_calc_search(client, body, "exploratory"), child.public_ref)


# ---------------------------------------------------------------------------
# Conformer search: an observation named by ref
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("obs_status,matches", STATUSES)
def test_conformer_observation_ref_names_evidence_the_rule_may_withhold(client, db_session, obs_status, matches):
    """Kills: conformer_observation_ref finding the approved group through a rejected/deprecated observation."""
    entry = _entry(db_session, "MFC")
    group = make_conformer_group(db_session, entry)
    obs = make_conformer_observation(db_session, conformer_group=group)
    _approve(db_session, T.conformer_group, group.id)
    _set(db_session, T.conformer_observation, obs.id, obs_status)
    body = {"conformer_observation_ref": obs.public_ref}
    assert _found(_search_conformers(client, body, "curated"), group.public_ref) is matches
    assert _found(_search_conformers(client, body, "exploratory"), group.public_ref)


# ---------------------------------------------------------------------------
# Energy correction schemes: used_by_calculation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("calc_status,matches", STATUSES)
def test_correction_scheme_used_by_calculation(client, db_session, calc_status, matches):
    """Kills: used_by_calculation counting an application whose source calculation is rejected/deprecated."""
    entry = _entry(db_session, "MFE")
    scheme = make_energy_correction_scheme(db_session, name="mfe-scheme")
    calc = make_calculation(db_session, type=CalculationType.sp, species_entry_id=entry.id)
    make_applied_energy_correction(db_session, target_species_entry=entry, scheme=scheme, source_calculation=calc)
    _set(db_session, T.calculation, calc.id, calc_status)
    for value, expect in (("true", matches), ("false", not matches)):
        curated = _found_scheme(client, scheme, "curated", value)
        assert curated is expect, value
    assert _found_scheme(client, scheme, "exploratory", "true")


def _found_scheme(client, scheme, profile, used):
    response = client.get(
        f"{API}/energy-correction-schemes/search",
        params={"profile": profile, "used_by_calculation": used, "name": scheme.name},
    )
    assert response.status_code == 200, response.text
    return scheme.public_ref in str(response.json()["records"])


# ---------------------------------------------------------------------------
# Networks: the calculation legs of the three searches
# ---------------------------------------------------------------------------


def _network_world(db_session, link_status):
    """An approved network whose approved solve has an approved gaussian calculation and one orca calculation in
    ``link_status``; and a second solve whose only source calculation is in ``link_status``."""
    world = build_world(db_session)
    shown_release = make_software_release(db_session, name="gaussian", version="16")
    orca_release = make_software_release(db_session, name="orca", version="6")

    def solve_with(calcs):
        solve = add_solve(db_session, world, fits=[fit_spec("assoc")], review=None)
        for release, status in calcs:
            calc = make_calculation(db_session, species_entry_id=world.ar.id, software_release_id=release.id)
            attach_network_solve_source_calculation(
                db_session, solve=solve, calculation=calc, role=NetworkSolveCalculationRole.master_equation_run
            )
            _set(db_session, T.calculation, calc.id, status)
        set_solve_review(db_session, world, solve, S.approved)
        return solve

    mixed = solve_with([(shown_release, S.approved), (orca_release, link_status)])
    only = solve_with([(orca_release, link_status)])
    _approve_network(db_session, world)
    return world, mixed, only


def _approve_network(db_session, world):
    from app.services.record_review import ensure_record_review, set_record_review_status

    ensure_record_review(db_session, record_type=T.network, record_id=world.network.id)
    set_record_review_status(
        db_session, record_type=T.network, record_id=world.network.id, status=S.approved, actor=world.actor
    )


def _get(client, path, profile, **params):
    response = client.get(f"{API}{path}", params={"profile": profile, **params})
    assert response.status_code == 200, response.text
    return response


@pytest.mark.parametrize("link_status,matches", LINK_STATUSES)
def test_network_solves_search_calculation_leg(client, db_session, link_status, matches):
    """Kills: the solve search matching through a rejected/deprecated source calculation, or refusing a not-reviewed one."""
    _, mixed, only = _network_world(db_session, link_status)
    for params in ({"software": "orca"}, {"has_source_calculations": "true"}):
        curated = _get(client, "/network-solves/search", "curated", **params)
        assert _found(curated, mixed.public_ref) is (matches or "has_source_calculations" in params), params
        assert _found(curated, only.public_ref) is matches, params
        exploratory = _get(client, "/network-solves/search", "exploratory", **params)
        assert _found(exploratory, only.public_ref), params
    none = _get(client, "/network-solves/search", "curated", has_source_calculations="false")
    assert _found(none, only.public_ref) is (not matches)


@pytest.mark.parametrize("link_status,matches", LINK_STATUSES)
def test_network_kinetics_search_calculation_leg(client, db_session, link_status, matches):
    """Kills: the network-kinetics search matching through a rejected/deprecated source calculation."""
    _, mixed, only = _network_world(db_session, link_status)
    for params in ({"software": "orca"}, {"has_source_calculations": "true"}):
        curated = _get(client, "/network-kinetics/search", "curated", **params)
        assert _found(curated, mixed.public_ref) is (matches or "has_source_calculations" in params), params
        assert _found(curated, only.public_ref) is matches, params
        exploratory = _get(client, "/network-kinetics/search", "exploratory", **params)
        assert _found(exploratory, only.public_ref), params
    none = _get(client, "/network-kinetics/search", "curated", has_source_calculations="false")
    assert _found(none, only.public_ref) is (not matches)


@pytest.mark.parametrize("link_status,matches", LINK_STATUSES)
def test_networks_search_calculation_leg(client, db_session, link_status, matches):
    """Kills: the network search matching through a rejected/deprecated source calculation of one of its solves."""
    world, _, _ = _network_world(db_session, link_status)
    curated = _get(client, "/networks/search", "curated", software="orca")
    assert _found(curated, world.network.public_ref) is matches
    # The approved gaussian leg always matches, so the cases above are not an empty search.
    assert _found(_get(client, "/networks/search", "curated", software="gaussian"), world.network.public_ref)
    exploratory = _get(client, "/networks/search", "exploratory", software="orca")
    assert _found(exploratory, world.network.public_ref)
