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
    assert body["hidden_by_default"]["counts"] == {"rejected": 1, "deprecated": 0}
    assert body["hidden_by_default"]["counted"] == "species_entries"
    assert sorted(listed(include_rejected="true")[0]) == sorted(e.id for e in entries)
    ids, body = listed(profile="curated", include_rejected="true")
    assert ids == [entries[1].id]  # approved only
    assert body["hidden_by_default"] is None and body["entries_hidden_by_default"] is None
    # The species row itself is identity, not reviewable: it is always there.
    assert any(r["resource_type"] == "species" for r in body["results"])


def test_the_entry_detail_routes_answer_a_hidden_entry_as_an_unknown_id(client, db_session):
    w = _world(db_session)  # its species entry is approved; make a second, unreviewed one and a reaction entry
    other = make_species_entry(db_session, w["entry"].species, stereo_label="other")
    assert client.get(f"{LEGACY}/species-entries/{other.id}").status_code == 200
    curated = client.get(f"{LEGACY}/species-entries/{other.id}", params={"profile": "curated"})
    assert curated.status_code == 404
    assert curated.json() == client.get(f"{LEGACY}/species-entries/999999999", params={"profile": "curated"}).json()
    assert client.get(f"{LEGACY}/species-entries/{w['entry'].id}", params={"profile": "curated"}).status_code == 200
    # the reaction entry of the world is unreviewed
    assert client.get(f"{LEGACY}/reaction-entries/{w['reaction_entry'].id}").status_code == 200
    hidden = client.get(f"{LEGACY}/reaction-entries/{w['reaction_entry'].id}", params={"profile": "curated"})
    assert hidden.status_code == 404
    assert hidden.json() == client.get(f"{LEGACY}/reaction-entries/999999999", params={"profile": "curated"}).json()


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
    assert curated["hidden_by_default"] is None


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
