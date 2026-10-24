"""Closing the ways around the curated floor on the legacy entity reads.

* B1  a path id spelled ``+5`` / ``5.0`` / `` 5`` / ``5_0`` is still the id 5 to FastAPI; the gate sees the same id.
* B2  a record the profile shows does not name a record of ANY gated kind that the profile hides: pointer columns are
      blanked, embedded collections are filtered, a child of a hidden parent is hidden with it, and an id-typed query
      filter naming a hidden record answers as an unknown id does.
* B3  ``?solve_id=`` on a channel's kinetics answers a hidden solve exactly as a missing one.
"""

from __future__ import annotations

import json

import pytest

from app.db.models.common import RecordReviewStatus, SubmissionRecordType, ThermoTargetKind
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


def _world(db_session):
    """Approved records that point at hidden (never reviewed) records of other kinds."""
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
    db_session.flush()
    return {
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


def test_an_approved_thermo_does_not_name_a_hidden_statmech_or_conformer_group(client, db_session):
    w = _world(db_session)
    for body in (
        _curated(client, f"/thermo/{w['thermo'].id}").json(),
        _curated(client, "/thermo").json()["items"][0],
        _curated(client, f"/species-entries/{w['entry'].id}/thermo").json()[0],
    ):
        assert body["statmech_id"] is None, body
        assert body["target_conformer_group_id"] is None, body
    # Exploratory still shows both: nothing is removed without a floor.
    assert client.get(f"{LEGACY}/thermo/{w['thermo'].id}").json()["statmech_id"] == w["statmech"].id


def test_an_approved_calculation_does_not_name_a_hidden_observation_or_ts_entry(client, db_session):
    w = _world(db_session)
    assert _curated(client, f"/calculations/{w['calc'].id}").json()["conformer_observation_id"] is None
    assert _curated(client, f"/calculations/{w['ts_calc'].id}").json()["transition_state_entry_id"] is None
    assert client.get(f"{LEGACY}/calculations/{w['calc'].id}").json()["conformer_observation_id"] == w["hidden_observation"].id


def test_an_id_filter_naming_a_hidden_record_answers_as_an_unknown_id_does(client, db_session):
    """Kills: ``/calculations?conformer_observation_id=<hidden>`` returning the approved calculation (total 1)."""
    w = _world(db_session)
    for param, hidden_id in [
        ("conformer_observation_id", w["hidden_observation"].id),
        ("transition_state_entry_id", w["ts_entry"].id),
    ]:
        hidden = _curated(client, "/calculations", **{param: hidden_id}).json()
        unknown = _curated(client, "/calculations", **{param: 999999999}).json()
        assert hidden == unknown, param
        assert hidden["total"] == 0


def test_an_approved_ts_does_not_list_a_hidden_entry(client, db_session):
    w = _world(db_session)
    detail = _curated(client, f"/transition-states/{w['ts'].id}").json()
    assert detail["entries"] == [], detail
    listed = _curated(client, "/transition-states").json()["items"][0]
    assert listed["entries"] == []
    via_entry = _curated(client, f"/reaction-entries/{w['reaction_entry'].id}/transition-states").json()[0]
    assert via_entry["entries"] == []
    assert len(client.get(f"{LEGACY}/transition-states/{w['ts'].id}").json()["entries"]) == 1
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
