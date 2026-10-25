"""A search filter on a linked record matches only through linked records the profile can see (D6).

Under ``profile=curated`` a calculation (or conformer observation) below the review floor is hidden == unknown ==
absent. The statmech, transport and conformer searches and the calculation dependency filter used to match an approved
record *because of* such a linked record, which told the caller that a hidden record exists and what it ran at. Each
test builds an approved record with one approved link and one never-reviewed link and asks for a fact only the hidden
link has. Each test names the mutation it kills.
"""

from __future__ import annotations

import pytest

from app.db.models.common import (
    CalculationDependencyRole,
    CalculationType,
    RecordReviewStatus,
    StatmechCalculationRole,
    SubmissionRecordType,
)
from tests.services.scientific_read._factories import (
    attach_dependency,
    attach_statmech_source_calculation,
    attach_statmech_torsion,
    attach_transport_source_calculation,
    make_calculation,
    make_conformer_group,
    make_conformer_observation,
    make_lot,
    make_software_release,
    make_species,
    make_species_entry,
    make_statmech,
    make_transport,
    next_inchi_key,
    set_review,
)

API = "/api/v1/scientific"
T = SubmissionRecordType


def _approve(db_session, record_type, record_id):
    set_review(db_session, record_type=record_type, record_id=record_id, status=RecordReviewStatus.approved)


def _entry(db_session, tag):
    return make_species_entry(db_session, make_species(db_session, inchi_key=next_inchi_key(tag)))


def _levels(db_session, tag):
    """Level A / gaussian (the shown link) and level B / orca (the hidden link)."""
    return (
        make_lot(db_session, method=f"{tag}amethod"),
        make_lot(db_session, method=f"{tag}bmethod"),
        make_software_release(db_session, name="gaussian", version="16"),
        make_software_release(db_session, name="orca", version="6"),
    )


def _records(response):
    assert response.status_code == 200, response.text
    return response.json()["records"]


def _total(response):
    return response.json()["pagination"]["total"]


# ---------------------------------------------------------------------------
# Statmech search
# ---------------------------------------------------------------------------


def _statmech_world(db_session):
    entry = _entry(db_session, "HLS")
    lot_a, lot_b, gaussian, orca = _levels(db_session, "hls")
    sm = make_statmech(db_session, species_entry=entry)
    shown = make_calculation(
        db_session,
        type=CalculationType.opt,
        species_entry_id=entry.id,
        lot_id=lot_a.id,
        software_release_id=gaussian.id,
    )
    hidden = make_calculation(
        db_session, type=CalculationType.freq, species_entry_id=entry.id, lot_id=lot_b.id, software_release_id=orca.id
    )
    attach_statmech_source_calculation(db_session, statmech=sm, calculation=shown, role=StatmechCalculationRole.opt)
    attach_statmech_source_calculation(db_session, statmech=sm, calculation=hidden, role=StatmechCalculationRole.freq)
    _approve(db_session, T.calculation, shown.id)
    _approve(db_session, T.statmech, sm.id)
    return sm, lot_a, lot_b


@pytest.mark.parametrize(
    "hidden_filter",
    [{"software": "orca"}, {"software": "orca", "software_version": "6"}, {"has_freq_calculation": "true"}],
    ids=["software", "software_version", "has_freq_calculation"],
)
def test_statmech_search_does_not_match_through_a_hidden_source_calculation(client, db_session, hidden_filter):
    """Kills: the statmech search provenance / evidence filters reading hidden source calculations."""
    sm, _, _ = _statmech_world(db_session)
    base = {"statmech_ref": sm.public_ref}
    curated = client.get(f"{API}/statmech/search", params={**base, **hidden_filter, "profile": "curated"})
    assert _records(curated) == []
    assert _total(curated) == 0
    exploratory = client.get(f"{API}/statmech/search", params={**base, **hidden_filter, "profile": "exploratory"})
    assert sm.public_ref in str(_records(exploratory))


def test_statmech_search_method_filter_ignores_a_hidden_source_calculation(client, db_session):
    """Kills: the method filter matching the level of a hidden source calculation."""
    sm, lot_a, lot_b = _statmech_world(db_session)
    base = {"statmech_ref": sm.public_ref, "profile": "curated"}
    assert _records(client.get(f"{API}/statmech/search", params={**base, "method": lot_b.method})) == []
    shown = client.get(f"{API}/statmech/search", params={**base, "method": lot_a.method})
    assert sm.public_ref in str(_records(shown))  # non-vacuous: the shown link still matches


def test_statmech_has_source_calculations_ignores_hidden_calculations(client, db_session):
    """Kills: has_source_calculations counting a hidden link (it must equal 'the shown links')."""
    entry = _entry(db_session, "HLS2")
    sm = make_statmech(db_session, species_entry=entry)
    hidden = make_calculation(db_session, type=CalculationType.freq, species_entry_id=entry.id)
    attach_statmech_source_calculation(db_session, statmech=sm, calculation=hidden, role=StatmechCalculationRole.freq)
    _approve(db_session, T.statmech, sm.id)
    base = {"statmech_ref": sm.public_ref, "profile": "curated"}
    assert _records(client.get(f"{API}/statmech/search", params={**base, "has_source_calculations": "true"})) == []
    absent = client.get(f"{API}/statmech/search", params={**base, "has_source_calculations": "false"})
    assert sm.public_ref in str(_records(absent))
    exploratory = client.get(
        f"{API}/statmech/search", params={**base, "profile": "exploratory", "has_source_calculations": "true"}
    )
    assert sm.public_ref in str(_records(exploratory))


def test_statmech_has_rotor_scans_ignores_a_hidden_scan(client, db_session):
    """Kills: has_rotor_scans counting a torsion whose scan calculation is hidden."""
    entry = _entry(db_session, "HLS3")
    sm = make_statmech(db_session, species_entry=entry)
    scan = make_calculation(db_session, type=CalculationType.scan, species_entry_id=entry.id)
    attach_statmech_torsion(db_session, statmech=sm, source_scan_calculation=scan)
    _approve(db_session, T.statmech, sm.id)
    base = {"statmech_ref": sm.public_ref, "has_rotor_scans": "true"}
    assert _records(client.get(f"{API}/statmech/search", params={**base, "profile": "curated"})) == []
    exploratory = client.get(f"{API}/statmech/search", params={**base, "profile": "exploratory"})
    assert sm.public_ref in str(_records(exploratory))


# ---------------------------------------------------------------------------
# Transport search
# ---------------------------------------------------------------------------


def _transport_world(db_session):
    entry = _entry(db_session, "HLT")
    lot_a, lot_b, gaussian, orca = _levels(db_session, "hlt")
    tr = make_transport(db_session, species_entry=entry)
    shown = make_calculation(
        db_session, type=CalculationType.sp, species_entry_id=entry.id, lot_id=lot_a.id, software_release_id=gaussian.id
    )
    hidden = make_calculation(
        db_session, type=CalculationType.sp, species_entry_id=entry.id, lot_id=lot_b.id, software_release_id=orca.id
    )
    attach_transport_source_calculation(db_session, transport=tr, calculation=shown)
    attach_transport_source_calculation(db_session, transport=tr, calculation=hidden)
    _approve(db_session, T.calculation, shown.id)
    _approve(db_session, T.transport, tr.id)
    return tr, lot_a, lot_b


def test_transport_search_does_not_match_through_a_hidden_source_calculation(client, db_session):
    """Kills: the transport search provenance filter reading hidden source calculations."""
    tr, lot_a, lot_b = _transport_world(db_session)
    base = {"transport_ref": tr.public_ref}
    for hidden_filter, shown_filter in (
        ({"software": "orca"}, {"software": "gaussian"}),
        ({"method": lot_b.method}, {"method": lot_a.method}),
    ):
        curated = client.get(f"{API}/transport/search", params={**base, **hidden_filter, "profile": "curated"})
        assert _records(curated) == [], hidden_filter
        assert _total(curated) == 0
        visible = client.get(f"{API}/transport/search", params={**base, **shown_filter, "profile": "curated"})
        assert tr.public_ref in str(_records(visible)), shown_filter
        exploratory = client.get(f"{API}/transport/search", params={**base, **hidden_filter, "profile": "exploratory"})
        assert tr.public_ref in str(_records(exploratory)), hidden_filter


def test_transport_has_source_calculations_ignores_hidden_calculations(client, db_session):
    """Kills: has_source_calculations counting a hidden link."""
    entry = _entry(db_session, "HLT2")
    tr = make_transport(db_session, species_entry=entry)
    hidden = make_calculation(db_session, type=CalculationType.sp, species_entry_id=entry.id)
    attach_transport_source_calculation(db_session, transport=tr, calculation=hidden)
    _approve(db_session, T.transport, tr.id)
    base = {"transport_ref": tr.public_ref, "profile": "curated"}
    assert _records(client.get(f"{API}/transport/search", params={**base, "has_source_calculations": "true"})) == []
    absent = client.get(f"{API}/transport/search", params={**base, "has_source_calculations": "false"})
    assert tr.public_ref in str(_records(absent))
    exploratory = client.get(
        f"{API}/transport/search", params={**base, "profile": "exploratory", "has_source_calculations": "true"}
    )
    assert tr.public_ref in str(_records(exploratory))


# ---------------------------------------------------------------------------
# Conformer search
# ---------------------------------------------------------------------------


def _conformer_world(db_session):
    """An approved group with an approved observation (opt at level A) and a not-reviewed one (freq at level B)."""
    entry = _entry(db_session, "HLC")
    lot_a, lot_b, gaussian, orca = _levels(db_session, "hlc")
    group = make_conformer_group(db_session, entry)
    obs_shown = make_conformer_observation(db_session, conformer_group=group)
    obs_hidden = make_conformer_observation(db_session, conformer_group=group)
    calc_shown = make_calculation(
        db_session,
        type=CalculationType.opt,
        species_entry_id=entry.id,
        lot_id=lot_a.id,
        software_release_id=gaussian.id,
        conformer_observation_id=obs_shown.id,
    )
    make_calculation(
        db_session,
        type=CalculationType.freq,
        species_entry_id=entry.id,
        lot_id=lot_b.id,
        software_release_id=orca.id,
        conformer_observation_id=obs_hidden.id,
    )
    _approve(db_session, T.conformer_group, group.id)
    _approve(db_session, T.conformer_observation, obs_shown.id)
    _approve(db_session, T.calculation, calc_shown.id)
    return group, obs_shown, lot_a, lot_b


def _search_conformers(client, body, profile):
    return client.post(f"{API}/conformers/search?profile={profile}", json=body)


def test_conformer_search_provenance_ignores_a_hidden_observation(client, db_session):
    """Kills: the conformer provenance filter reading calculations of hidden observations."""
    group, _, lot_a, lot_b = _conformer_world(db_session)
    base = {"conformer_group_ref": group.public_ref}
    for hidden_filter, shown_filter in (
        ({"software": "orca"}, {"software": "gaussian"}),
        ({"method": lot_b.method}, {"method": lot_a.method}),
    ):
        curated = _search_conformers(client, {**base, **hidden_filter}, "curated")
        assert _records(curated) == [], hidden_filter
        assert _total(curated) == 0
        assert group.public_ref in str(_records(_search_conformers(client, {**base, **shown_filter}, "curated")))
        assert group.public_ref in str(_records(_search_conformers(client, {**base, **hidden_filter}, "exploratory")))


def test_conformer_search_provenance_ignores_a_hidden_calculation_on_a_shown_observation(client, db_session):
    """Kills: filtering hidden observations but not hidden calculations on a SHOWN observation."""
    group, obs_shown, lot_a, lot_b = _conformer_world(db_session)
    make_calculation(
        db_session,
        type=CalculationType.sp,
        species_entry_id=group.species_entry_id,
        lot_id=make_lot(db_session, method="hlconly").id,
        conformer_observation_id=obs_shown.id,
    )
    body = {"conformer_group_ref": group.public_ref, "method": "hlconly"}
    assert _records(_search_conformers(client, body, "curated")) == []
    assert group.public_ref in str(_records(_search_conformers(client, body, "exploratory")))


@pytest.mark.parametrize("quantifier", ["any_observation", "all_observations"])
def test_conformer_search_evidence_flags_ignore_hidden_records(client, db_session, quantifier):
    """Kills: the has_* evidence filters (either quantifier) counting a hidden calculation or observation."""
    group, _, _, _ = _conformer_world(db_session)  # shown: opt. hidden: a freq on a hidden observation
    base = {"conformer_group_ref": group.public_ref, "evidence_match": quantifier}
    assert _records(_search_conformers(client, {**base, "has_freq": True}, "curated")) == []
    assert group.public_ref in str(_records(_search_conformers(client, {**base, "has_freq": False}, "curated")))
    assert group.public_ref in str(_records(_search_conformers(client, {**base, "has_opt": True}, "curated")))
    # Exploratory shows both observations: the freq exists (any), and the shown observation still lacks it (all).
    exploratory = _search_conformers(client, {**base, "has_freq": True}, "exploratory")
    assert bool(_records(exploratory)) == (quantifier == "any_observation")
    # Under 'all' the hidden observation has no opt, so only the exploratory profile sees incomplete coverage.
    if quantifier == "all_observations":
        assert _records(_search_conformers(client, {**base, "has_opt": True}, "exploratory")) == []


def test_conformer_search_observation_filters_ignore_hidden_observations(client, db_session):
    """Kills: has_observations / scientific_origin matching a hidden observation."""
    entry = _entry(db_session, "HLC2")
    group = make_conformer_group(db_session, entry)
    obs = make_conformer_observation(db_session, conformer_group=group)  # never reviewed
    _approve(db_session, T.conformer_group, group.id)
    base = {"conformer_group_ref": group.public_ref}
    origin = obs.scientific_origin.value
    assert _records(_search_conformers(client, {**base, "has_observations": True}, "curated")) == []
    assert _records(_search_conformers(client, {**base, "scientific_origin": origin}, "curated")) == []
    assert group.public_ref in str(_records(_search_conformers(client, {**base, "has_observations": False}, "curated")))
    exploratory = _search_conformers(client, {**base, "scientific_origin": origin}, "exploratory")
    assert group.public_ref in str(_records(exploratory))


# ---------------------------------------------------------------------------
# Calculation search: dependency_role alone
# ---------------------------------------------------------------------------


def test_calculation_dependency_role_ignores_an_edge_to_a_hidden_calculation(client, db_session):
    """Kills: dependency_role matching a shown calculation through an edge whose other end is hidden."""
    entry = _entry(db_session, "HLD")
    opt = make_calculation(db_session, type=CalculationType.opt, species_entry_id=entry.id)
    freq = make_calculation(db_session, type=CalculationType.freq, species_entry_id=entry.id)
    attach_dependency(db_session, parent=opt, child=freq, role=CalculationDependencyRole.freq_on)
    _approve(db_session, T.calculation, opt.id)  # the freq stays not_reviewed
    body = {"dependency_role": "freq_on", "species_entry_ref": entry.public_ref}
    curated = client.post(f"{API}/calculations/search?profile=curated", json=body)
    assert _records(curated) == []
    assert _total(curated) == 0
    exploratory = client.post(f"{API}/calculations/search?profile=exploratory", json=body)
    assert opt.public_ref in str(_records(exploratory))
    # Non-vacuous: once the far end is approved the same edge matches.
    _approve(db_session, T.calculation, freq.id)
    both = client.post(f"{API}/calculations/search?profile=curated", json=body)
    assert opt.public_ref in str(_records(both))
