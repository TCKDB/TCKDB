"""Closing the ways around the curated floor on the legacy entity reads.

* B1  a path id spelled ``+5`` / ``5.0`` / `` 5`` / ``5_0`` is still the id 5 to FastAPI; the gate sees the same id.
* B2  a record the profile shows does not name a record of ANY gated kind that the profile WITHHOLDS (ADR 0023: rejected
      or deprecated linked evidence): pointer columns are blanked, embedded collections are filtered, a child of a
      withheld parent is withheld with it, and an id-typed query filter naming a withheld record answers as an unknown
      id does. A record that is merely below the floor (never reviewed) is linked evidence and is named as it always was;
      each B2 test runs over both states.
* B3  ``?solve_id=`` on a channel's kinetics answers a hidden solve exactly as a missing one.
"""

from __future__ import annotations

import json

import pytest

from app.db.models.common import (
    EnergyCorrectionApplicationRole,
    RecordReviewStatus,
    SubmissionRecordType,
    ThermoTargetKind,
)
from tests.api.test_legacy_reads_visibility import CASES, LEGACY, _seed
from tests.services.scientific_read._factories import (
    make_calculation,
    make_chem_reaction,
    make_conformer_group,
    make_conformer_observation,
    make_reaction_entry,
    make_species,
    make_species_entry,
    make_statmech,
    make_thermo_scalar,
    make_transition_state,
    make_transition_state_entry,
    next_inchi_key,
    set_review,
)

S = RecordReviewStatus
T = SubmissionRecordType

SPELLINGS = ["+{id}", "{id}.0", "%20{id}", "{id}_0", "0{id}"]


# --- B1 --------------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("case", sorted(CASES))
def test_a_respelled_id_is_gated_like_the_id(case, client, db_session):
    """Kills: a gate that compares the raw path text (``isdecimal``) while FastAPI coerces the spelling to the id."""
    seeded, url = _seed(case, db_session)
    hidden_id = seeded.rows[3].id  # not_reviewed: below the curated floor
    unknown = client.get(f"{url}/999999999", params={"profile": "curated"})
    accepted = 0
    for spelling in SPELLINGS:
        path = f"{url}/{spelling.format(id=hidden_id)}"
        if client.get(path).status_code != 200:
            continue  # FastAPI does not read this spelling as the id on this route
        accepted += 1
        curated = client.get(path, params={"profile": "curated"})
        assert curated.status_code == 404, (path, curated.status_code)
        assert curated.json() == unknown.json(), path
    assert accepted >= 1, "no respelling reached the record: the test would prove nothing"


# --- B2 --------------------------------------------------------------------------------------------------------------


def _world(db_session, withheld=False):
    """Approved records that point at hidden records of other kinds: never reviewed, or with ``withheld`` rejected."""
    species = make_species(db_session, smiles="C=O", inchi_key=next_inchi_key("HW"))
    entry = make_species_entry(db_session, species)
    set_review(db_session, record_type=T.species_entry, record_id=entry.id, status=S.approved)
    group = make_conformer_group(db_session, entry, label="hidden-group")  # unreviewed: hidden
    hidden_observation = make_conformer_observation(db_session, conformer_group=group)
    approved_group = make_conformer_group(db_session, entry, label="shown-group")
    set_review(db_session, record_type=T.conformer_group, record_id=approved_group.id, status=S.approved)
    shown_observation = make_conformer_observation(db_session, conformer_group=approved_group)
    set_review(db_session, record_type=T.conformer_observation, record_id=shown_observation.id, status=S.approved)
    orphaned_observation = make_conformer_observation(db_session, conformer_group=group)  # approved, in a hidden group
    set_review(db_session, record_type=T.conformer_observation, record_id=orphaned_observation.id, status=S.approved)

    hidden_statmech = make_statmech(db_session, species_entry=entry)
    thermo = make_thermo_scalar(db_session, species_entry=entry, statmech_id=hidden_statmech.id)
    thermo.thermodynamic_target_kind = ThermoTargetKind.single_conformer
    thermo.target_conformer_group_id = group.id
    set_review(db_session, record_type=T.thermo, record_id=thermo.id, status=S.approved)

    calc = make_calculation(db_session, species_entry_id=entry.id, conformer_observation_id=hidden_observation.id)
    set_review(db_session, record_type=T.calculation, record_id=calc.id, status=S.approved)

    rs = make_species(db_session, smiles="CC", inchi_key=next_inchi_key("HWR"))
    ps = make_species(db_session, smiles="CCC", inchi_key=next_inchi_key("HWP"))
    chem = make_chem_reaction(db_session, reactants=[rs], products=[ps])
    reaction_entry = make_reaction_entry(
        db_session,
        reaction=chem,
        reactant_entries=[make_species_entry(db_session, rs)],
        product_entries=[make_species_entry(db_session, ps)],
    )
    ts = make_transition_state(db_session, reaction_entry=reaction_entry, label="ts")
    set_review(db_session, record_type=T.transition_state, record_id=ts.id, status=S.approved)
    hidden_ts_entry = make_transition_state_entry(db_session, transition_state=ts)
    ts_calc = make_calculation(db_session, transition_state_entry_id=hidden_ts_entry.id)
    set_review(db_session, record_type=T.calculation, record_id=ts_calc.id, status=S.approved)
    # An approved child under an unreviewed parent: its own review passes, its parent's does not.
    orphan_calc = make_calculation(db_session, species_entry_id=entry.id, conformer_observation_id=orphaned_observation.id)
    set_review(db_session, record_type=T.calculation, record_id=orphan_calc.id, status=S.approved)
    hidden_ts = make_transition_state(db_session, reaction_entry=reaction_entry, label="hidden-parent-ts")  # unreviewed
    orphan_ts_entry = make_transition_state_entry(db_session, transition_state=hidden_ts)
    set_review(db_session, record_type=T.transition_state_entry, record_id=orphan_ts_entry.id, status=S.approved)
    orphan_ts_calc = make_calculation(db_session, transition_state_entry_id=orphan_ts_entry.id)
    set_review(db_session, record_type=T.calculation, record_id=orphan_ts_calc.id, status=S.approved)
    if withheld:
        for record_type, row in (
            (T.conformer_group, group),
            (T.conformer_observation, hidden_observation),
            (T.statmech, hidden_statmech),
            (T.transition_state_entry, hidden_ts_entry),
            (T.transition_state, hidden_ts),
        ):
            set_review(db_session, record_type=record_type, record_id=row.id, status=S.rejected)
    db_session.flush()
    return {
        "orphan_calc": orphan_calc,
        "orphan_ts_entry": orphan_ts_entry,
        "orphan_ts_calc": orphan_ts_calc,
        "entry": entry,
        "group": group,
        "hidden_observation": hidden_observation,
        "shown_observation": shown_observation,
        "orphaned_observation": orphaned_observation,
        "statmech": hidden_statmech,
        "thermo": thermo,
        "calc": calc,
        "ts": ts,
        "ts_entry": hidden_ts_entry,
        "ts_calc": ts_calc,
        "reaction_entry": reaction_entry,
    }


def _curated(client, path, **params):
    return client.get(f"{LEGACY}{path}", params={"profile": "curated", **params})


@pytest.mark.parametrize("withheld", [False, True], ids=["unlisted", "withheld"])
def test_an_approved_thermo_does_not_name_a_withheld_statmech_or_conformer_group(client, db_session, withheld):
    """Unlisted (never reviewed): the pointers are kept. Withheld (rejected): they are blanked."""
    w = _world(db_session, withheld)
    for body in (
        _curated(client, f"/thermo/{w['thermo'].id}").json(),
        _curated(client, "/thermo").json()["items"][0],
        _curated(client, f"/species-entries/{w['entry'].id}/thermo").json()[0],
    ):
        if withheld:
            assert body["statmech_id"] is None, body
            assert body["target_conformer_group_id"] is None, body
        else:
            assert body["statmech_id"] == w["statmech"].id, body
            assert body["target_conformer_group_id"] == w["group"].id, body
    # Exploratory still shows both: nothing is removed without a floor.
    assert client.get(f"{LEGACY}/thermo/{w['thermo'].id}").json()["statmech_id"] == w["statmech"].id


@pytest.mark.parametrize("withheld", [False, True], ids=["unlisted", "withheld"])
def test_an_approved_calculation_does_not_name_a_withheld_observation_or_ts_entry(client, db_session, withheld):
    w = _world(db_session, withheld)
    observation = _curated(client, f"/calculations/{w['calc'].id}").json()["conformer_observation_id"]
    ts_entry = _curated(client, f"/calculations/{w['ts_calc'].id}").json()["transition_state_entry_id"]
    assert observation == (None if withheld else w["hidden_observation"].id)
    assert ts_entry == (None if withheld else w["ts_entry"].id)
    assert client.get(f"{LEGACY}/calculations/{w['calc'].id}").json()["conformer_observation_id"] == w["hidden_observation"].id


@pytest.mark.parametrize("withheld", [False, True], ids=["unlisted", "withheld"])
def test_an_id_filter_naming_a_withheld_record_answers_as_an_unknown_id_does(client, db_session, withheld):
    """Kills: ``/calculations?conformer_observation_id=<withheld>`` returning the approved calculation (total 1). A filter
    is not a subject: one naming a record that is merely unreviewed is kept and finds the approved calculation."""
    w = _world(db_session, withheld)
    for param, hidden_id in [
        ("conformer_observation_id", w["hidden_observation"].id),
        ("transition_state_entry_id", w["ts_entry"].id),
    ]:
        named = _curated(client, "/calculations", **{param: hidden_id}).json()
        unknown = _curated(client, "/calculations", **{param: 999999999}).json()
        if withheld:
            assert named == unknown, param
            assert named["total"] == 0
        else:
            assert named["total"] == 1, param


@pytest.mark.parametrize("withheld", [False, True], ids=["unlisted", "withheld"])
def test_an_approved_ts_does_not_list_a_withheld_entry(client, db_session, withheld):
    """An entry embedded in its transition state is linked evidence of it: listed while unreviewed, dropped once rejected.
    Its own detail route stays on the record floor either way."""
    w = _world(db_session, withheld)
    count = 0 if withheld else 1
    detail = _curated(client, f"/transition-states/{w['ts'].id}").json()
    assert len(detail["entries"]) == count, detail
    listed = _curated(client, "/transition-states").json()["items"][0]
    assert len(listed["entries"]) == count
    via_entry = _curated(client, f"/reaction-entries/{w['reaction_entry'].id}/transition-states").json()[0]
    assert len(via_entry["entries"]) == count
    exploratory = client.get(f"{LEGACY}/transition-states/{w['ts'].id}", params={"include_rejected": "true"})
    assert len(exploratory.json()["entries"]) == 1
    assert _curated(client, f"/transition-states/entries/{w['ts_entry'].id}").status_code == 404


def test_an_observation_in_a_hidden_group_is_hidden_with_it(client, db_session):
    """Kills: listing an approved observation that names its (hidden) group, and 200 on its own detail route."""
    w = _world(db_session)
    orphan = w["orphaned_observation"]
    assert client.get(f"{LEGACY}/conformer-observations/{orphan.id}").status_code == 200
    curated = _curated(client, f"/conformer-observations/{orphan.id}")
    assert curated.status_code == 404
    assert curated.json() == _curated(client, "/conformer-observations/999999999").json()
    listed = _curated(client, "/conformer-observations").json()
    assert [i["id"] for i in listed["items"]] == [w["shown_observation"].id]
    assert listed["total"] == 1
    under_entry = _curated(client, f"/species-entries/{w['entry'].id}/conformers").json()
    assert [i["id"] for i in under_entry] == [w["shown_observation"].id]
    assert json.dumps(under_entry).count(str(w["group"].id)) == 0 or all(
        i["conformer_group_id"] != w["group"].id for i in under_entry
    )
    summary = _curated(client, f"/species-entries/{w['entry'].id}").json()["conformer_summary"]
    assert summary["conformer_observation_count"] == 1  # the count equals the curated list


# --- B3 --------------------------------------------------------------------------------------------------------------


def test_a_hidden_solve_filter_answers_as_a_missing_one(client, db_session):
    from sqlalchemy import select

    from app.db.models.network_pdep import NetworkChannel
    from tests.api.test_browse_order_echo import seed_network_kinetics

    seeded = seed_network_kinetics(db_session)
    network_id = db_session.scalar(select(NetworkChannel.network_id))
    channel_id = db_session.scalar(select(NetworkChannel.id))
    set_review(db_session, record_type=T.network, record_id=network_id, status=S.approved)
    hidden_solve = seeded.review_targets[0][1]  # a solve with no review row: below the floor
    path = f"/networks/{network_id}/channels/{channel_id}/kinetics"
    hidden = _curated(client, path, solve_id=hidden_solve)
    missing = _curated(client, path, solve_id=999999999)
    assert hidden.status_code == missing.status_code == 404
    assert hidden.json() == missing.json()


# --- entries: the visibility the scientific reads apply to species and reaction entries ---------------------------------


def test_lookup_species_lists_the_entries_the_request_shows_and_counts_the_rest(client, db_session):
    species = make_species(db_session, smiles="C=O", inchi_key="WSFSSNUMVMOOMR-UHFFFAOYSA-N")
    entries = [make_species_entry(db_session, species, stereo_label=label) for label in "abc"]
    set_review(db_session, record_type=T.species_entry, record_id=entries[0].id, status=S.rejected)
    set_review(db_session, record_type=T.species_entry, record_id=entries[1].id, status=S.approved)
    params = {"smiles": "C=O", "charge": 0, "multiplicity": 1}

    def listed(**extra):
        body = client.get("/api/v1/lookup/species", params={**params, **extra}).json()
        return [r["id"] for r in body["results"] if r["resource_type"] == "species_entry"], body

    ids, body = listed()
    assert sorted(ids) == sorted([entries[1].id, entries[2].id])
    assert body["entries_hidden_by_default"]["counts"] == {"rejected": 1, "deprecated": 0}
    assert body["entries_hidden_by_default"]["counted"] == "species_entries"
    assert body["hidden_by_default"] is None  # no records are listed: that field is about records
    assert sorted(listed(include_rejected="true")[0]) == sorted(e.id for e in entries)
    ids, body = listed(profile="curated", include_rejected="true")
    assert ids == [entries[1].id]  # approved only
    assert body["hidden_by_default"] is None and body["entries_hidden_by_default"] is None
    # The species row itself is identity, not reviewable: it is always there.
    assert any(r["resource_type"] == "species" for r in body["results"])


def test_an_unreviewed_species_entry_is_a_scoping_parent_reachable_everywhere(client, db_session):
    """Entries never gate: an approved record on an unreviewed entry shows under curated, the entry answers 200 on its
    detail and sub-routes, the entry-id filters return the record, and the record names the entry. Only the lookup's
    entry LISTING follows the scientific species-search visibility -- and it does not withhold the records."""
    from tests.services.scientific_read._factories import attach_opt_result, make_lot

    species = make_species(db_session, smiles="C=O", inchi_key="WSFSSNUMVMOOMR-UHFFFAOYSA-N")
    entry = make_species_entry(db_session, species)  # never reviewed: below the curated floor
    thermo = make_thermo_scalar(db_session, species_entry=entry)
    lot = make_lot(db_session)
    calc = make_calculation(db_session, species_entry_id=entry.id, lot_id=lot.id)
    attach_opt_result(db_session, calculation=calc, final_energy_hartree=-1.0)
    set_review(db_session, record_type=T.thermo, record_id=thermo.id, status=S.approved)
    set_review(db_session, record_type=T.calculation, record_id=calc.id, status=S.approved)
    curated = {"profile": "curated"}

    assert client.get(f"{LEGACY}/species-entries/{entry.id}", params=curated).status_code == 200
    sub_list = client.get(f"{LEGACY}/species-entries/{entry.id}/thermo", params=curated).json()
    assert [t["id"] for t in sub_list] == [thermo.id]
    assert sub_list[0]["species_entry_id"] == entry.id  # the record names the entry
    by_filter = client.get(f"{LEGACY}/thermo", params={**curated, "species_entry_id": entry.id}).json()
    assert [t["id"] for t in by_filter["items"]] == [thermo.id]
    by_filter = client.get(f"{LEGACY}/calculations", params={**curated, "species_entry_id": entry.id}).json()
    assert [c["id"] for c in by_filter["items"]] == [calc.id]
    assert [r["id"] for r in client.get(f"{LEGACY}/lookup/thermo", params={**curated, "species_entry_id": entry.id}).json()["results"]] == [thermo.id]

    # lookup by identity: the entry is not LISTED under curated, but the approved calculation on it is returned
    body = client.get(
        f"{LEGACY}/lookup/species-calculation",
        params={**curated, "smiles": "C=O", "charge": 0, "multiplicity": 1, "type": "opt", "method": lot.method, "basis": lot.basis},
    ).json()
    assert [r for r in body["results"] if r["resource_type"] == "species_entry"] == []
    assert [r["id"] for r in body["results"] if r["resource_type"] == "calculation"] == [calc.id]
    # the record says its entry is not listed, and the match says which entry (entries are ungated: naming it is allowed)
    assert [r["entry_listed"] for r in body["results"] if r["resource_type"] == "calculation"] == [False]
    assert "species_entry_unlisted" in body["match"]["detail_codes"]
    assert any(str(entry.id) in d for d, c in zip(body["match"]["details"], body["match"]["detail_codes"], strict=True) if c == "species_entry_unlisted")
    # exploratory lists the unreviewed entry: nothing to explain
    listed = client.get(
        f"{LEGACY}/lookup/species-calculation",
        params={"smiles": "C=O", "charge": 0, "multiplicity": 1, "type": "opt", "method": lot.method, "basis": lot.basis},
    ).json()
    assert [r["entry_listed"] for r in listed["results"] if r["resource_type"] == "calculation"] == [True]
    assert "species_entry_unlisted" not in listed["match"]["detail_codes"]
    # and with the selection mode: the record on the unlisted entry is still a candidate
    chosen = client.get(
        f"{LEGACY}/lookup/species-calculation",
        params={**curated, "smiles": "C=O", "charge": 0, "multiplicity": 1, "type": "opt", "method": lot.method, "basis": lot.basis, "selection": "latest"},
    ).json()
    assert [r["id"] for r in chosen["results"] if r["resource_type"] == "calculation"] == [calc.id]
    assert [g["entry_listed"] for g in chosen["selected_by"]["groups"]] == [False]


def test_an_unreviewed_reaction_entry_is_a_scoping_parent_reachable_everywhere(client, db_session):
    from tests.services.scientific_read._factories import make_kinetics

    rs = make_species(db_session, smiles="CC", inchi_key="OTMSDBZUPAUEDD-UHFFFAOYSA-N")
    ps = make_species(db_session, smiles="CCC", inchi_key="ATUOYWHBWRKTHZ-UHFFFAOYSA-N")
    chem = make_chem_reaction(db_session, reactants=[rs], products=[ps])
    reaction_entry = make_reaction_entry(
        db_session,
        reaction=chem,
        reactant_entries=[make_species_entry(db_session, rs)],
        product_entries=[make_species_entry(db_session, ps)],
    )  # never reviewed
    kinetics = make_kinetics(db_session, reaction_entry=reaction_entry, a=1e-12)
    set_review(db_session, record_type=T.kinetics, record_id=kinetics.id, status=S.approved)
    curated = {"profile": "curated"}

    assert client.get(f"{LEGACY}/reaction-entries/{reaction_entry.id}", params=curated).status_code == 200
    sub_list = client.get(f"{LEGACY}/reaction-entries/{reaction_entry.id}/kinetics", params=curated).json()
    assert [k["id"] for k in sub_list] == [kinetics.id] and sub_list[0]["reaction_entry_id"] == reaction_entry.id
    by_filter = client.get(f"{LEGACY}/kinetics", params={**curated, "reaction_entry_id": reaction_entry.id}).json()
    assert [k["id"] for k in by_filter["items"]] == [kinetics.id]
    body = client.get(
        f"{LEGACY}/lookup/reaction-kinetics", params={**curated, "reactants": ["CC"], "products": ["CCC"]}
    ).json()
    assert [r for r in body["results"] if r["resource_type"] == "reaction_entry"] == []  # the listing follows the entry rule
    assert [r["id"] for r in body["results"] if r["resource_type"] == "kinetics"] == [kinetics.id]  # the record is not withheld
    assert [r["entry_listed"] for r in body["results"] if r["resource_type"] == "kinetics"] == [False]
    assert "reaction_entry_unlisted" in body["match"]["detail_codes"]
    assert any(
        str(reaction_entry.id) in d for d, c in zip(body["match"]["details"], body["match"]["detail_codes"], strict=True) if c == "reaction_entry_unlisted"
    )


def test_lookup_reaction_lists_the_reaction_entries_the_request_shows(client, db_session):
    rs = make_species(db_session, smiles="CC", inchi_key="OTMSDBZUPAUEDD-UHFFFAOYSA-N")
    ps = make_species(db_session, smiles="CCC", inchi_key="ATUOYWHBWRKTHZ-UHFFFAOYSA-N")
    chem = make_chem_reaction(db_session, reactants=[rs], products=[ps])
    reaction_entry = make_reaction_entry(
        db_session,
        reaction=chem,
        reactant_entries=[make_species_entry(db_session, rs)],
        product_entries=[make_species_entry(db_session, ps)],
    )
    w = {"reaction_entry": reaction_entry}
    body = client.get(
        f"{LEGACY}/lookup/reaction", params={"reactants": ["CC"], "products": ["CCC"]}
    ).json()
    entries = [r for r in body["results"] if r["resource_type"] == "reaction_entry"]
    assert [e["id"] for e in entries] == [w["reaction_entry"].id]
    curated = client.get(
        f"{LEGACY}/lookup/reaction", params={"reactants": ["CC"], "products": ["CCC"], "profile": "curated"}
    ).json()
    assert [r for r in curated["results"] if r["resource_type"] == "reaction_entry"] == []
    assert curated["entries_hidden_by_default"] is None


# --- applied energy corrections are reviewable records -----------------------------------------------------------------


def test_applied_energy_corrections_obey_the_default_visibility_and_the_floor(client, db_session):
    from tests.services.scientific_read._factories import (
        make_applied_energy_correction,
        make_energy_correction_scheme,
        make_lot,
    )

    w = _world(db_session)
    scheme = make_energy_correction_scheme(db_session, name="ac", lot=make_lot(db_session, method="ac", basis="b"))
    rows = [
        make_applied_energy_correction(
            db_session, target_species_entry=w["entry"], scheme=scheme, application_role=role
        )
        for role in (
            EnergyCorrectionApplicationRole.bac_total,
            EnergyCorrectionApplicationRole.aec_total,
            EnergyCorrectionApplicationRole.soc_total,
        )
    ]
    set_review(db_session, record_type=T.applied_energy_correction, record_id=rows[0].id, status=S.rejected)
    set_review(db_session, record_type=T.applied_energy_correction, record_id=rows[1].id, status=S.approved)
    url = f"{LEGACY}/applied-energy-corrections"
    first = client.get(url)
    assert first.status_code == 200, first.text
    body = first.json()
    assert [i["id"] for i in body["items"]] == [rows[1].id, rows[2].id]
    assert body["hidden_by_default"]["counts"] == {"rejected": 1, "deprecated": 0}
    curated = client.get(url, params={"profile": "curated"}).json()
    assert [i["id"] for i in curated["items"]] == [rows[1].id] and curated["total"] == 1
    assert curated["hidden_by_default"] is None
    assert client.get(f"{url}/{rows[2].id}").status_code == 200
    assert client.get(f"{url}/{rows[2].id}", params={"profile": "curated"}).status_code == 404


# --- counts equal lists, also without a floor ---------------------------------------------------------------------------


def test_exploratory_counts_follow_the_default_visibility_like_the_lists(client, db_session):
    w = _world(db_session)
    group = w["group"]
    obs = [make_conformer_observation(db_session, conformer_group=group) for _ in range(2)]
    set_review(db_session, record_type=T.conformer_observation, record_id=obs[0].id, status=S.rejected)
    # the group's own detail: the rejected observation is not listed or counted by default
    detail = client.get(f"{LEGACY}/conformer-groups/{group.id}").json()
    listed = {o["id"] for o in detail["observations"]}
    assert obs[0].id not in listed and obs[1].id in listed
    assert detail["observation_count"] == len(detail["observations"])
    detail = client.get(f"{LEGACY}/conformer-groups/{group.id}", params={"include_rejected": "true"}).json()
    assert obs[0].id in {o["id"] for o in detail["observations"]}
    assert detail["observation_count"] == len(detail["observations"])
    # the entry summary counts what its lists show
    summary = client.get(f"{LEGACY}/species-entries/{w['entry'].id}").json()["conformer_summary"]
    shown = client.get(f"{LEGACY}/species-entries/{w['entry'].id}/conformers").json()
    assert summary["conformer_observation_count"] == len(shown)
    # a rejected transition-state entry is not embedded by default
    set_review(db_session, record_type=T.transition_state_entry, record_id=w["ts_entry"].id, status=S.rejected)
    assert client.get(f"{LEGACY}/transition-states/{w['ts'].id}").json()["entries"] == []
    assert len(client.get(f"{LEGACY}/transition-states/{w['ts'].id}", params={"include_rejected": "true"}).json()["entries"]) == 1


# --- selection and the channel header -------------------------------------------------------------------------------------


def test_lowest_energy_counts_the_candidates_it_could_not_compare(client, db_session):
    from tests.services.scientific_read._factories import attach_opt_result, make_lot

    w = _world(db_session)
    lot = make_lot(db_session)
    a = make_calculation(db_session, species_entry_id=w["entry"].id, lot_id=lot.id)
    b = make_calculation(db_session, species_entry_id=w["entry"].id, lot_id=lot.id)
    c = make_calculation(db_session, species_entry_id=w["entry"].id, lot_id=lot.id)  # no energy recorded
    attach_opt_result(db_session, calculation=a, final_energy_hartree=-10.0)
    attach_opt_result(db_session, calculation=b, final_energy_hartree=-11.0)
    body = client.get(
        f"{LEGACY}/lookup/calculations",
        params={"species_entry_id": w["entry"].id, "selection": "lowest_energy", "method": lot.method, "basis": lot.basis},
    ).json()
    group = body["selected_by"]["groups"][0]
    assert group["candidates"] == 3 and group["not_compared"] == 1 and group["chosen"] == 1
    assert [r["id"] for r in body["results"] if r["resource_type"] == "calculation"] == [b.id]
    del c


def test_merged_levels_are_one_level_for_the_lowest_energy_refusal(db_session):
    from types import SimpleNamespace

    from app.api.routes.lookup import _refuse_mixed_levels
    from app.db.models.level_of_theory import LevelOfTheoryMerge
    from tests.services.scientific_read._factories import make_lot

    kept = make_lot(db_session, method="kept", basis="b")
    merged = make_lot(db_session, method="merged-spelling", basis="b")
    db_session.add(LevelOfTheoryMerge(merged_lot_id=merged.id, into_lot_id=kept.id))
    db_session.flush()
    _refuse_mixed_levels(db_session, [SimpleNamespace(lot_id=kept.id), SimpleNamespace(lot_id=merged.id)], None)


def test_a_channels_kinetics_discloses_what_the_default_hid_in_a_header(client, db_session):
    from sqlalchemy import select

    from app.db.models.network_pdep import NetworkChannel, NetworkKinetics
    from tests.api.test_browse_order_echo import seed_network_kinetics
    from tests.services.scientific_read._factories import attach_network_kinetics_plog

    seeded = seed_network_kinetics(db_session)
    for row in db_session.scalars(select(NetworkKinetics)).all():
        attach_network_kinetics_plog(db_session, kinetics=row)
    network_id = db_session.scalar(select(NetworkChannel.network_id))
    channel_id = db_session.scalar(select(NetworkChannel.id))
    set_review(db_session, record_type=T.network_solve, record_id=seeded.review_targets[0][1], status=S.rejected)
    response = client.get(f"{LEGACY}/networks/{network_id}/channels/{channel_id}/kinetics")
    assert response.headers["X-TCKDB-Hidden-By-Default"] == "deprecated=0; rejected=1"
    curated = client.get(f"{LEGACY}/networks/{network_id}/channels/{channel_id}/kinetics", params={"profile": "curated"})
    assert "X-TCKDB-Hidden-By-Default" not in curated.headers


# --- N1: a child of a hidden parent is hidden everywhere it can be named ------------------------------------------------


@pytest.mark.parametrize("withheld", [False, True], ids=["unlisted", "withheld"])
def test_an_approved_child_of_a_withheld_parent_is_not_named_by_a_pointer(client, db_session, withheld):
    """Kills: judging a pointer by the child's own review only. The detail route of the observation / the entry answers
    404 while its parent is below the floor (step 7 changes that); a calculation must not name it once the parent is
    withheld, and does name it while the parent is merely unreviewed."""
    w = _world(db_session, withheld)
    assert _curated(client, f"/conformer-observations/{w['orphaned_observation'].id}").status_code == 404
    assert _curated(client, f"/transition-states/entries/{w['orphan_ts_entry'].id}").status_code == 404
    observation = _curated(client, f"/calculations/{w['orphan_calc'].id}").json()["conformer_observation_id"]
    ts_entry = _curated(client, f"/calculations/{w['orphan_ts_calc'].id}").json()["transition_state_entry_id"]
    assert observation == (None if withheld else w["orphaned_observation"].id)
    assert ts_entry == (None if withheld else w["orphan_ts_entry"].id)
    # exploratory: nothing removed
    assert client.get(f"{LEGACY}/calculations/{w['orphan_calc'].id}").json()["conformer_observation_id"] == w["orphaned_observation"].id


@pytest.mark.parametrize("withheld", [False, True], ids=["unlisted", "withheld"])
def test_an_id_filter_naming_a_child_of_a_withheld_parent_answers_as_an_unknown_id(client, db_session, withheld):
    w = _world(db_session, withheld)
    for param, child in [
        ("conformer_observation_id", w["orphaned_observation"].id),
        ("transition_state_entry_id", w["orphan_ts_entry"].id),
    ]:
        named = _curated(client, "/calculations", **{param: child}).json()
        if withheld:
            assert named == _curated(client, "/calculations", **{param: 999999999}).json(), param
            assert named["total"] == 0
        else:
            assert named["total"] == 1, param


def test_exploratory_hides_a_child_with_its_parent_by_default(client, db_session):
    """A rejected parent hides its observations by default, in the list and in the counts; including rejected shows them."""
    w = _world(db_session)
    set_review(db_session, record_type=T.conformer_group, record_id=w["group"].id, status=S.rejected)
    listed = client.get(f"{LEGACY}/conformer-observations").json()
    assert w["orphaned_observation"].id not in [i["id"] for i in listed["items"]]
    under_entry = client.get(f"{LEGACY}/species-entries/{w['entry'].id}/conformers").json()
    assert w["orphaned_observation"].id not in [i["id"] for i in under_entry]
    summary = client.get(f"{LEGACY}/species-entries/{w['entry'].id}").json()["conformer_summary"]
    assert summary["conformer_observation_count"] == len(under_entry)
    lifted = client.get(f"{LEGACY}/conformer-observations", params={"include_rejected": "true"}).json()
    assert w["orphaned_observation"].id in [i["id"] for i in lifted["items"]]
    assert w["orphaned_observation"].id in [
        i["id"] for i in client.get(f"{LEGACY}/species-entries/{w['entry'].id}/conformers", params={"include_rejected": "true"}).json()
    ]


# --- the disclosure counts a child hidden through its parent under the parent's status -------------------------------------


def test_the_disclosure_counts_children_hidden_through_a_rejected_parent(client, db_session):
    """Kills: counting only the observation's own status (rejected: 0) while ``include_rejected`` brings it back."""
    species = make_species(db_session, smiles="C=O", inchi_key=next_inchi_key("DC"))
    entry = make_species_entry(db_session, species)
    group = make_conformer_group(db_session, entry, label="rejected-group")
    set_review(db_session, record_type=T.conformer_group, record_id=group.id, status=S.rejected)
    child = make_conformer_observation(db_session, conformer_group=group)  # unreviewed itself
    own_rejected = make_conformer_observation(db_session, conformer_group=group)
    set_review(db_session, record_type=T.conformer_observation, record_id=own_rejected.id, status=S.rejected)

    listed = client.get(f"{LEGACY}/conformer-observations", params={"conformer_group_id": group.id}).json()
    assert listed["items"] == []
    # one child hidden through its parent (rejected), one hidden by its own status (rejected): each counted once
    assert listed["hidden_by_default"]["counts"] == {"rejected": 2, "deprecated": 0}
    restored = client.get(
        f"{LEGACY}/conformer-observations", params={"conformer_group_id": group.id, "include_rejected": "true"}
    ).json()
    assert {i["id"] for i in restored["items"]} == {child.id, own_rejected.id}  # the count matches what the switch restores

    response = client.get(f"{LEGACY}/species-entries/{entry.id}/conformers")
    assert response.json() == []
    assert response.headers["X-TCKDB-Hidden-By-Default"] == "deprecated=0; rejected=2"
    restored_under_entry = client.get(f"{LEGACY}/species-entries/{entry.id}/conformers", params={"include_rejected": "true"})
    assert len(restored_under_entry.json()) == 2
    curated = client.get(
        f"{LEGACY}/conformer-observations", params={"conformer_group_id": group.id, "profile": "curated"}
    ).json()
    assert curated["hidden_by_default"] is None


def test_a_child_hidden_by_two_exclusions_is_counted_under_both(client, db_session):
    """Itself deprecated, in a rejected group: in both counts, and restored only when both switches are on."""
    species = make_species(db_session, smiles="C=O", inchi_key=next_inchi_key("DD"))
    entry = make_species_entry(db_session, species)
    group = make_conformer_group(db_session, entry, label="rejected-group")
    set_review(db_session, record_type=T.conformer_group, record_id=group.id, status=S.rejected)
    child = make_conformer_observation(db_session, conformer_group=group)
    set_review(db_session, record_type=T.conformer_observation, record_id=child.id, status=S.deprecated)
    url = f"{LEGACY}/conformer-observations"
    base = {"conformer_group_id": group.id}
    body = client.get(url, params=base).json()
    assert body["items"] == []
    assert body["hidden_by_default"]["counts"] == {"rejected": 1, "deprecated": 1}
    only_deprecated = client.get(url, params={**base, "include_deprecated": "true"}).json()
    assert only_deprecated["items"] == []  # the rejected group still hides it
    assert only_deprecated["hidden_by_default"]["counts"] == {"rejected": 1}
    only_rejected = client.get(url, params={**base, "include_rejected": "true"}).json()
    assert only_rejected["items"] == []  # its own deprecated status still hides it
    assert only_rejected["hidden_by_default"]["counts"] == {"deprecated": 1}
    both = client.get(url, params={**base, "include_rejected": "true", "include_deprecated": "true"}).json()
    assert [i["id"] for i in both["items"]] == [child.id]
    assert both["hidden_by_default"] is None
