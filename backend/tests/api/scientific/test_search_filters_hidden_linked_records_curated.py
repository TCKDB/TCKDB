"""A search filter on a linked record matches through linked evidence unless the evidence rule withholds it.

ADR 0023, step 4 (matching). Under ``profile=curated`` the approved RECORD floor decides which records a search returns;
the records they LINK to (source calculations, conformer observations, dependency edges) are evidence. Evidence takes
part in a match with its badge, whatever its review state, except ``rejected`` and ``deprecated`` evidence, which is
withheld: it is not a candidate for the match, and no count or total moves because it exists. Under ``exploratory``
nothing is withheld.

Each world is an approved record with one approved link and one link whose review state the test sets. A fact only that
second link has is asked for. ``None`` is "never reviewed" (no review row). Each test names the mutation it kills.
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
S = RecordReviewStatus

#: Review states of a linked record, and whether the curated profile lets it take part in a match.
WITHHELD = (S.rejected, S.deprecated)
LINK_STATUSES = [
    pytest.param(None, True, id="not_reviewed"),
    pytest.param(S.under_review, True, id="under_review"),
    pytest.param(S.rejected, False, id="rejected"),
    pytest.param(S.deprecated, False, id="deprecated"),
]


def _set(db_session, record_type, record_id, status):
    if status is not None:
        set_review(db_session, record_type=record_type, record_id=record_id, status=status)


def _approve(db_session, record_type, record_id):
    _set(db_session, record_type, record_id, S.approved)


def _entry(db_session, tag):
    return make_species_entry(db_session, make_species(db_session, inchi_key=next_inchi_key(tag)))


def _levels(db_session, tag):
    """Level A / gaussian (the approved link) and level B / orca (the other link)."""
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


def _found(response, ref):
    return ref in str(_records(response))


def _assert_matches(curated, ref, matches, label=""):
    """Curated finds ``ref`` iff ``matches``; when it does not, the total is 0 (nothing counted for the withheld link)."""
    assert _found(curated, ref) is matches, label
    if not matches:
        assert _total(curated) == 0, label


# ---------------------------------------------------------------------------
# Statmech search
# ---------------------------------------------------------------------------


def _statmech_world(db_session, link_status):
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
    other = make_calculation(
        db_session, type=CalculationType.freq, species_entry_id=entry.id, lot_id=lot_b.id, software_release_id=orca.id
    )
    attach_statmech_source_calculation(db_session, statmech=sm, calculation=shown, role=StatmechCalculationRole.opt)
    attach_statmech_source_calculation(db_session, statmech=sm, calculation=other, role=StatmechCalculationRole.freq)
    _approve(db_session, T.calculation, shown.id)
    _set(db_session, T.calculation, other.id, link_status)
    _approve(db_session, T.statmech, sm.id)
    return sm, lot_a, lot_b


@pytest.mark.parametrize("link_status,matches", LINK_STATUSES)
@pytest.mark.parametrize(
    "extra",
    [{"software": "orca"}, {"software": "orca", "software_version": "6"}, {"has_freq_calculation": "true"}],
    ids=["software", "software_version", "has_freq_calculation"],
)
def test_statmech_search_matches_through_evidence_the_rule_leaves_visible(
    client, db_session, extra, link_status, matches
):
    """Kills: matching through rejected/deprecated evidence (leak), or refusing not-reviewed evidence (old rule)."""
    sm, _, _ = _statmech_world(db_session, link_status)
    base = {"statmech_ref": sm.public_ref, **extra}
    curated = client.get(f"{API}/statmech/search", params={**base, "profile": "curated"})
    _assert_matches(curated, sm.public_ref, matches)
    exploratory = client.get(f"{API}/statmech/search", params={**base, "profile": "exploratory"})
    assert _found(exploratory, sm.public_ref)  # exploratory is unchanged: nothing is withheld as evidence


@pytest.mark.parametrize("link_status,matches", LINK_STATUSES)
def test_statmech_search_method_filter(client, db_session, link_status, matches):
    """Kills: the method filter matching the level of withheld evidence / not matching visible evidence."""
    sm, lot_a, lot_b = _statmech_world(db_session, link_status)
    base = {"statmech_ref": sm.public_ref, "profile": "curated"}
    _assert_matches(
        client.get(f"{API}/statmech/search", params={**base, "method": lot_b.method}), sm.public_ref, matches
    )
    shown = client.get(f"{API}/statmech/search", params={**base, "method": lot_a.method})
    assert _found(shown, sm.public_ref)  # non-vacuous: the approved link always matches


@pytest.mark.parametrize("link_status,matches", LINK_STATUSES)
def test_statmech_has_source_calculations(client, db_session, link_status, matches):
    """Kills: has_source_calculations counting withheld evidence, or ignoring visible not-reviewed evidence."""
    entry = _entry(db_session, "HLS2")
    sm = make_statmech(db_session, species_entry=entry)
    link = make_calculation(db_session, type=CalculationType.freq, species_entry_id=entry.id)
    attach_statmech_source_calculation(db_session, statmech=sm, calculation=link, role=StatmechCalculationRole.freq)
    _set(db_session, T.calculation, link.id, link_status)
    _approve(db_session, T.statmech, sm.id)
    base = {"statmech_ref": sm.public_ref, "profile": "curated"}
    has = client.get(f"{API}/statmech/search", params={**base, "has_source_calculations": "true"})
    none = client.get(f"{API}/statmech/search", params={**base, "has_source_calculations": "false"})
    assert _found(has, sm.public_ref) is matches
    assert _found(none, sm.public_ref) is (not matches)


@pytest.mark.parametrize("link_status,matches", LINK_STATUSES)
def test_statmech_has_rotor_scans(client, db_session, link_status, matches):
    """Kills: has_rotor_scans counting a torsion whose scan calculation is withheld."""
    entry = _entry(db_session, "HLS3")
    sm = make_statmech(db_session, species_entry=entry)
    scan = make_calculation(db_session, type=CalculationType.scan, species_entry_id=entry.id)
    attach_statmech_torsion(db_session, statmech=sm, source_scan_calculation=scan)
    _set(db_session, T.calculation, scan.id, link_status)
    _approve(db_session, T.statmech, sm.id)
    base = {"statmech_ref": sm.public_ref, "has_rotor_scans": "true"}
    _assert_matches(client.get(f"{API}/statmech/search", params={**base, "profile": "curated"}), sm.public_ref, matches)
    assert _found(client.get(f"{API}/statmech/search", params={**base, "profile": "exploratory"}), sm.public_ref)


# ---------------------------------------------------------------------------
# Transport search
# ---------------------------------------------------------------------------


def _transport_world(db_session, link_status):
    entry = _entry(db_session, "HLT")
    lot_a, lot_b, gaussian, orca = _levels(db_session, "hlt")
    tr = make_transport(db_session, species_entry=entry)
    shown = make_calculation(
        db_session, type=CalculationType.sp, species_entry_id=entry.id, lot_id=lot_a.id, software_release_id=gaussian.id
    )
    other = make_calculation(
        db_session, type=CalculationType.sp, species_entry_id=entry.id, lot_id=lot_b.id, software_release_id=orca.id
    )
    attach_transport_source_calculation(db_session, transport=tr, calculation=shown)
    attach_transport_source_calculation(db_session, transport=tr, calculation=other)
    _approve(db_session, T.calculation, shown.id)
    _set(db_session, T.calculation, other.id, link_status)
    _approve(db_session, T.transport, tr.id)
    return tr, lot_a, lot_b


@pytest.mark.parametrize("link_status,matches", LINK_STATUSES)
def test_transport_search_provenance(client, db_session, link_status, matches):
    """Kills: the transport provenance filter reading withheld source calculations / refusing visible ones."""
    tr, lot_a, lot_b = _transport_world(db_session, link_status)
    base = {"transport_ref": tr.public_ref}
    for hidden_filter, shown_filter in (
        ({"software": "orca"}, {"software": "gaussian"}),
        ({"method": lot_b.method}, {"method": lot_a.method}),
    ):
        curated = client.get(f"{API}/transport/search", params={**base, **hidden_filter, "profile": "curated"})
        _assert_matches(curated, tr.public_ref, matches, hidden_filter)
        visible = client.get(f"{API}/transport/search", params={**base, **shown_filter, "profile": "curated"})
        assert _found(visible, tr.public_ref), shown_filter
        exploratory = client.get(f"{API}/transport/search", params={**base, **hidden_filter, "profile": "exploratory"})
        assert _found(exploratory, tr.public_ref), hidden_filter


@pytest.mark.parametrize("link_status,matches", LINK_STATUSES)
def test_transport_has_source_calculations(client, db_session, link_status, matches):
    """Kills: has_source_calculations counting a withheld link."""
    entry = _entry(db_session, "HLT2")
    tr = make_transport(db_session, species_entry=entry)
    link = make_calculation(db_session, type=CalculationType.sp, species_entry_id=entry.id)
    attach_transport_source_calculation(db_session, transport=tr, calculation=link)
    _set(db_session, T.calculation, link.id, link_status)
    _approve(db_session, T.transport, tr.id)
    base = {"transport_ref": tr.public_ref, "profile": "curated"}
    has = client.get(f"{API}/transport/search", params={**base, "has_source_calculations": "true"})
    none = client.get(f"{API}/transport/search", params={**base, "has_source_calculations": "false"})
    assert _found(has, tr.public_ref) is matches
    assert _found(none, tr.public_ref) is (not matches)


# ---------------------------------------------------------------------------
# Conformer search
# ---------------------------------------------------------------------------


def _conformer_world(db_session, obs_status, calc_status):
    """An approved group: an approved observation (opt at level A) and a second observation / freq at level B.

    ``obs_status`` is the second observation's review state, ``calc_status`` its calculation's.
    """
    entry = _entry(db_session, "HLC")
    lot_a, lot_b, gaussian, orca = _levels(db_session, "hlc")
    group = make_conformer_group(db_session, entry)
    obs_shown = make_conformer_observation(db_session, conformer_group=group)
    obs_other = make_conformer_observation(db_session, conformer_group=group)
    calc_shown = make_calculation(
        db_session,
        type=CalculationType.opt,
        species_entry_id=entry.id,
        lot_id=lot_a.id,
        software_release_id=gaussian.id,
        conformer_observation_id=obs_shown.id,
    )
    calc_other = make_calculation(
        db_session,
        type=CalculationType.freq,
        species_entry_id=entry.id,
        lot_id=lot_b.id,
        software_release_id=orca.id,
        conformer_observation_id=obs_other.id,
    )
    _approve(db_session, T.conformer_group, group.id)
    _approve(db_session, T.conformer_observation, obs_shown.id)
    _approve(db_session, T.calculation, calc_shown.id)
    _set(db_session, T.conformer_observation, obs_other.id, obs_status)
    _set(db_session, T.calculation, calc_other.id, calc_status)
    return group, obs_shown, lot_a, lot_b


def _search_conformers(client, body, profile):
    return client.post(f"{API}/conformers/search?profile={profile}", json=body)


#: (second observation's state, its calculation's state) and whether the pair can take part in a match.
CONFORMER_PAIRS = [
    pytest.param(None, None, True, id="neither_reviewed"),
    pytest.param(S.under_review, None, True, id="observation_under_review"),
    pytest.param(S.rejected, None, False, id="observation_rejected"),
    pytest.param(S.deprecated, None, False, id="observation_deprecated"),
    pytest.param(None, S.rejected, False, id="calculation_rejected"),
    pytest.param(None, S.deprecated, False, id="calculation_deprecated"),
]


@pytest.mark.parametrize("obs_status,calc_status,matches", CONFORMER_PAIRS)
def test_conformer_search_provenance(client, db_session, obs_status, calc_status, matches):
    """Kills: the provenance filter reading withheld observations / calculations, or refusing visible ones."""
    group, _, lot_a, lot_b = _conformer_world(db_session, obs_status, calc_status)
    base = {"conformer_group_ref": group.public_ref}
    for hidden_filter, shown_filter in (
        ({"software": "orca"}, {"software": "gaussian"}),
        ({"method": lot_b.method}, {"method": lot_a.method}),
    ):
        curated = _search_conformers(client, {**base, **hidden_filter}, "curated")
        _assert_matches(curated, group.public_ref, matches, hidden_filter)
        assert _found(_search_conformers(client, {**base, **shown_filter}, "curated"), group.public_ref)
        assert _found(_search_conformers(client, {**base, **hidden_filter}, "exploratory"), group.public_ref)


@pytest.mark.parametrize(
    "calc_status", [pytest.param(S.rejected, id="rejected"), pytest.param(S.deprecated, id="deprecated")]
)
def test_conformer_search_withheld_calculation_on_a_visible_observation(client, db_session, calc_status):
    """Kills: withholding observations but not the calculations on a visible observation."""
    group, obs_shown, _, _ = _conformer_world(db_session, None, None)
    extra = make_calculation(
        db_session,
        type=CalculationType.sp,
        species_entry_id=group.species_entry_id,
        lot_id=make_lot(db_session, method="hlconly").id,
        conformer_observation_id=obs_shown.id,
    )
    _set(db_session, T.calculation, extra.id, calc_status)
    body = {"conformer_group_ref": group.public_ref, "method": "hlconly"}
    _assert_matches(_search_conformers(client, body, "curated"), group.public_ref, False)
    assert _found(_search_conformers(client, body, "exploratory"), group.public_ref)


@pytest.mark.parametrize("quantifier", ["any_observation", "all_observations"])
@pytest.mark.parametrize("obs_status,calc_status,counts", CONFORMER_PAIRS)
def test_conformer_search_evidence_flags(client, db_session, quantifier, obs_status, calc_status, counts):
    """Kills: the has_* evidence filters (either quantifier) counting withheld observations or calculations."""
    group, _, _, _ = _conformer_world(db_session, obs_status, calc_status)  # visible: opt. other: a freq
    base = {"conformer_group_ref": group.public_ref, "evidence_match": quantifier}
    has_freq = _search_conformers(client, {**base, "has_freq": True}, "curated")
    no_freq = _search_conformers(client, {**base, "has_freq": False}, "curated")
    has_opt = _search_conformers(client, {**base, "has_opt": True}, "curated")
    if quantifier == "any_observation":
        assert _found(has_freq, group.public_ref) is counts
        assert _found(no_freq, group.public_ref) is (not counts)
        assert _found(has_opt, group.public_ref)
    else:
        # The shown observation has no freq, so 'every observation has a freq' never holds; a counted freq on the
        # other observation makes the coverage incomplete ('some lacks') only when that observation counts.
        assert not _found(has_freq, group.public_ref)
        assert _found(no_freq, group.public_ref)
        # 'every observation has an opt' holds only while the other observation (which has none) is withheld.
        assert _found(has_opt, group.public_ref) is (obs_status in WITHHELD)
    assert _found(_search_conformers(client, {**base, "has_freq": True}, "exploratory"), group.public_ref) is (
        quantifier == "any_observation"
    )


@pytest.mark.parametrize(
    "calc_status",
    [
        pytest.param(None, id="not_reviewed"),
        pytest.param(S.rejected, id="rejected"),
        pytest.param(S.deprecated, id="deprecated"),
    ],
)
def test_conformer_all_observations_calculation_on_a_visible_observation(client, db_session, calc_status):
    """Kills: dropping the calculation condition inside the all_observations clause (the observation condition alone)."""
    entry = _entry(db_session, "HLC3")
    group = make_conformer_group(db_session, entry)
    obs = make_conformer_observation(db_session, conformer_group=group)
    freq = make_calculation(
        db_session, type=CalculationType.freq, species_entry_id=entry.id, conformer_observation_id=obs.id
    )
    _approve(db_session, T.conformer_group, group.id)
    _approve(db_session, T.conformer_observation, obs.id)
    _set(db_session, T.calculation, freq.id, calc_status)
    body = {"conformer_group_ref": group.public_ref, "has_freq": True, "evidence_match": "all_observations"}
    _assert_matches(_search_conformers(client, body, "curated"), group.public_ref, calc_status is None)
    assert _found(_search_conformers(client, body, "exploratory"), group.public_ref)


@pytest.mark.parametrize(
    "obs_status,matches", [(None, True), (S.under_review, True), (S.rejected, False), (S.deprecated, False)]
)
def test_conformer_search_observation_filters(client, db_session, obs_status, matches):
    """Kills: has_observations / scientific_origin matching a withheld observation."""
    entry = _entry(db_session, "HLC2")
    group = make_conformer_group(db_session, entry)
    obs = make_conformer_observation(db_session, conformer_group=group)
    _set(db_session, T.conformer_observation, obs.id, obs_status)
    _approve(db_session, T.conformer_group, group.id)
    base = {"conformer_group_ref": group.public_ref}
    origin = obs.scientific_origin.value
    assert (
        _found(_search_conformers(client, {**base, "has_observations": True}, "curated"), group.public_ref) is matches
    )
    assert _found(_search_conformers(client, {**base, "has_observations": False}, "curated"), group.public_ref) is (
        not matches
    )
    assert (
        _found(_search_conformers(client, {**base, "scientific_origin": origin}, "curated"), group.public_ref)
        is matches
    )
    exploratory = _search_conformers(client, {**base, "scientific_origin": origin}, "exploratory")
    assert _found(exploratory, group.public_ref)


# ---------------------------------------------------------------------------
# Calculation search: dependency_role alone
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("link_status,matches", LINK_STATUSES)
def test_calculation_dependency_role(client, db_session, link_status, matches):
    """Kills: dependency_role matching through an edge whose other end is withheld / refusing a visible far end."""
    entry = _entry(db_session, "HLD")
    opt = make_calculation(db_session, type=CalculationType.opt, species_entry_id=entry.id)
    freq = make_calculation(db_session, type=CalculationType.freq, species_entry_id=entry.id)
    attach_dependency(db_session, parent=opt, child=freq, role=CalculationDependencyRole.freq_on)
    _approve(db_session, T.calculation, opt.id)
    _set(db_session, T.calculation, freq.id, link_status)
    body = {"dependency_role": "freq_on", "species_entry_ref": entry.public_ref}
    curated = client.post(f"{API}/calculations/search?profile=curated", json=body)
    if matches:
        assert opt.public_ref in str(_records(curated))
    else:
        # The far end is withheld: the approved calculation is not found through the edge, and nothing is counted.
        assert opt.public_ref not in str(_records(curated))
        assert _total(curated) == 0
    exploratory = client.post(f"{API}/calculations/search?profile=exploratory", json=body)
    assert opt.public_ref in str(_records(exploratory))
