"""GET /api/v1/scientific/species-entries/{ref}/conformer-usage: declared thermo targets and kinetics selections."""

from __future__ import annotations

import pytest
from sqlalchemy import event
from starlette.requests import Request

from app.api.rate_limit import _classify
from app.db.models.common import (
    ConformerSelectionKind,
    RecordReviewStatus,
    SubmissionRecordType,
    ThermoTargetKind,
)
from app.db.models.kinetics import KineticsInterpretationAssignment
from tests.services.scientific_read._factories import (
    attach_conformer_selection,
    make_chem_reaction,
    make_conformer_group,
    make_kinetics,
    make_reaction_entry,
    make_species,
    make_species_entry,
    make_statmech,
    make_thermo_scalar,
    next_inchi_key,
    set_review,
)

API = "/api/v1/scientific"


def url(entry) -> str:
    return f"{API}/species-entries/{entry.public_ref}/conformer-usage"


def _entry(db_session, tag):
    return make_species_entry(db_session, make_species(db_session, inchi_key=next_inchi_key(tag)))


def _target(thermo, kind, group=None):
    thermo.thermodynamic_target_kind = kind
    thermo.target_conformer_group_id = group.id if group is not None else None


def _assign(db_session, kinetics, statmech, selection, *, subject_key="reactant:1", role="reactant"):
    db_session.add(
        KineticsInterpretationAssignment(
            kinetics_id=kinetics.id,
            subject_key=subject_key,
            role=role,
            statmech_id=statmech.id,
            conformer_selection_id=selection.id,
            ensemble_policy="lowest_energy_conformer",
            standard_state_convention="ideal_gas_1_bar",
            degeneracy_interpretation="reaction_path_degeneracy",
        )
    )
    db_session.flush()


def _review(db_session, record_type, row, status):
    set_review(db_session, record_type=record_type, record_id=row.id, status=status)


@pytest.fixture
def world(db_session):
    entry, other, partner, product = (_entry(db_session, t) for t in ("CUE", "CUO", "CUP", "CUQ"))
    group_a = make_conformer_group(db_session, entry, label="a")
    group_b = make_conformer_group(db_session, entry, label="b")
    group_other = make_conformer_group(db_session, other, label="o")

    thermo_a = make_thermo_scalar(db_session, species_entry=entry)
    _target(thermo_a, ThermoTargetKind.single_conformer, group_a)
    thermo_ensemble = make_thermo_scalar(db_session, species_entry=entry)
    _target(thermo_ensemble, ThermoTargetKind.equilibrium_ensemble)
    thermo_untargeted = make_thermo_scalar(db_session, species_entry=entry)
    thermo_rejected = make_thermo_scalar(db_session, species_entry=entry)
    _target(thermo_rejected, ThermoTargetKind.single_conformer, group_a)
    thermo_other_entry = make_thermo_scalar(db_session, species_entry=other)
    _target(thermo_other_entry, ThermoTargetKind.single_conformer, group_other)
    _review(db_session, SubmissionRecordType.thermo, thermo_rejected, RecordReviewStatus.rejected)

    reaction = make_chem_reaction(db_session, reactants=[entry.species, partner.species], products=[product.species])
    rxn_entry = make_reaction_entry(
        db_session, reaction=reaction, reactant_entries=[entry, partner], product_entries=[product]
    )
    selection_b = attach_conformer_selection(
        db_session, conformer_group=group_b, selection_kind=ConformerSelectionKind.preferred_for_kinetics
    )
    selection_other = attach_conformer_selection(db_session, conformer_group=group_other)
    statmech = make_statmech(db_session, species_entry=entry)
    other_statmech = make_statmech(db_session, species_entry=other)

    kinetics_b = make_kinetics(db_session, reaction_entry=rxn_entry)
    _assign(db_session, kinetics_b, statmech, selection_b)
    kinetics_other = make_kinetics(db_session, reaction_entry=rxn_entry)
    _assign(db_session, kinetics_other, other_statmech, selection_other)
    kinetics_rejected = make_kinetics(db_session, reaction_entry=rxn_entry)
    _assign(db_session, kinetics_rejected, statmech, selection_b)
    _review(db_session, SubmissionRecordType.kinetics, kinetics_rejected, RecordReviewStatus.rejected)
    db_session.flush()
    return {
        "entry": entry,
        "group_a": group_a,
        "group_b": group_b,
        "thermo_a": thermo_a,
        "thermo_ensemble": thermo_ensemble,
        "thermo_untargeted": thermo_untargeted,
        "thermo_rejected": thermo_rejected,
        "thermo_other_entry": thermo_other_entry,
        "kinetics_b": kinetics_b,
        "kinetics_other": kinetics_other,
        "kinetics_rejected": kinetics_rejected,
        "rxn_entry": rxn_entry,
        "other": other,
        "group_other": group_other,
    }


def test_route_exists_for_a_real_entry(client, db_session):
    assert client.get(url(_entry(db_session, "CUX"))).status_code == 200


def test_lists_declared_thermo_and_kinetics_per_group(client, world):
    response = client.get(url(world["entry"]))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["species_entry_ref"] == world["entry"].public_ref
    by_ref = {g["conformer_group_ref"]: g for g in body["groups"]}
    assert set(by_ref) == {world["group_a"].public_ref, world["group_b"].public_ref}

    group_a = by_ref[world["group_a"].public_ref]
    assert group_a["thermo"] == [{"thermo_ref": world["thermo_a"].public_ref, "declared_by": "depositor"}]
    assert group_a["kinetics"] == []

    group_b = by_ref[world["group_b"].public_ref]
    assert group_b["thermo"] == []
    assert group_b["kinetics"] == [
        {
            "kinetics_ref": world["kinetics_b"].public_ref,
            "reaction_entry_ref": world["rxn_entry"].public_ref,
            "role": "reactant",
            "subject_key": "reactant:1",
            "conformer_selection_kind": "preferred_for_kinetics",
            "ensemble_policy": "lowest_energy_conformer",
        }
    ]
    assert body["ensemble_thermo_refs"] == [world["thermo_ensemble"].public_ref]


def test_undeclared_other_entry_and_hidden_records_are_not_listed(client, world):
    text = client.get(url(world["entry"])).text
    for key in ("thermo_untargeted", "thermo_rejected", "thermo_other_entry", "kinetics_other", "kinetics_rejected"):
        assert world[key].public_ref not in text, key
    assert world["group_other"].public_ref not in text


def test_rejected_records_are_included_nowhere_but_curated_floor_also_applies(client, world, db_session):
    # Under curated only approved records are listed: nothing here is approved.
    body = client.get(url(world["entry"]), params={"profile": "curated"}).json()
    assert body["groups"] == []
    assert body["ensemble_thermo_refs"] == []
    _review(db_session, SubmissionRecordType.thermo, world["thermo_a"], RecordReviewStatus.approved)
    body = client.get(url(world["entry"]), params={"profile": "curated"}).json()
    assert [t["thermo_ref"] for g in body["groups"] for t in g["thermo"]] == [world["thermo_a"].public_ref]


def test_a_rejected_group_is_not_named_under_curated(client, world, db_session):
    _review(db_session, SubmissionRecordType.thermo, world["thermo_a"], RecordReviewStatus.approved)
    _review(db_session, SubmissionRecordType.conformer_group, world["group_a"], RecordReviewStatus.rejected)
    response = client.get(url(world["entry"]), params={"profile": "curated"})
    assert response.status_code == 200
    assert world["group_a"].public_ref not in response.text
    assert world["thermo_a"].public_ref not in response.text


def test_rejected_records_are_counted_and_include_rejected_lists_them(client, world, db_session):
    # A second assignment on the rejected kinetics must not count it twice.
    statmech = make_statmech(db_session, species_entry=world["entry"])
    selection = attach_conformer_selection(db_session, conformer_group=world["group_b"])
    _assign(db_session, world["kinetics_rejected"], statmech, selection, subject_key="reactant:2")

    default = client.get(url(world["entry"])).json()
    assert default["request"]["hidden_by_default"]["counts"] == {"rejected": 2, "deprecated": 0}
    assert default["request"]["hidden_by_default"]["include_with"]["rejected"] == "include_rejected=true"

    shown = client.get(url(world["entry"]), params={"include_rejected": "true"})
    body = shown.json()
    assert body["request"]["hidden_by_default"]["counts"] == {"deprecated": 0}
    assert world["thermo_rejected"].public_ref in shown.text
    assert world["kinetics_rejected"].public_ref in shown.text
    assert client.get(url(world["entry"]), params={"min_review_status": "approved"}).json()["groups"] == []


def test_a_thermo_pointed_at_another_entrys_group_is_not_listed(client, world, db_session):
    stray = make_thermo_scalar(db_session, species_entry=world["entry"])
    _target(stray, ThermoTargetKind.single_conformer, world["group_other"])
    db_session.flush()
    text = client.get(url(world["entry"])).text
    assert stray.public_ref not in text and world["group_other"].public_ref not in text
    assert client.get(url(world["other"])).status_code == 200
    assert stray.public_ref not in client.get(url(world["other"])).text


def test_an_emptied_group_lists_its_own_usage_and_names_its_survivor(client, world, db_session):
    world["group_a"].merged_into_conformer_group_id = world["group_b"].id
    db_session.flush()
    by_ref = {g["conformer_group_ref"]: g for g in client.get(url(world["entry"])).json()["groups"]}
    emptied = by_ref[world["group_a"].public_ref]
    assert emptied["merged_into_conformer_group_ref"] == world["group_b"].public_ref
    assert [t["thermo_ref"] for t in emptied["thermo"]] == [world["thermo_a"].public_ref]
    survivor = by_ref[world["group_b"].public_ref]
    assert survivor["merged_into_conformer_group_ref"] is None
    assert world["thermo_a"].public_ref not in [t["thermo_ref"] for t in survivor["thermo"]]


def test_a_merged_away_entry_ref_is_answered_for_the_survivor(client, db_session):
    from tests.api.test_api_species_merge_forwarding import _merged_pair

    survivor, loser, _ = _merged_pair(db_session)
    group = make_conformer_group(db_session, survivor)
    thermo = make_thermo_scalar(db_session, species_entry=survivor)
    _target(thermo, ThermoTargetKind.single_conformer, group)
    db_session.flush()

    body = client.get(url(loser)).json()
    assert body["species_entry_ref"] == survivor.public_ref
    assert body["request"]["species_entry_ref"] == loser.public_ref
    assert body["request"]["merged_refs"] == {loser.public_ref: survivor.public_ref}
    assert [t["thermo_ref"] for g in body["groups"] for t in g["thermo"]] == [thermo.public_ref]
    assert client.get(url(survivor)).json()["request"]["merged_refs"] == {}


def test_lists_are_ordered_by_ref(client, db_session):
    entry = _entry(db_session, "CUR")
    group = make_conformer_group(db_session, entry)
    refs = []
    for _ in range(4):
        thermo = make_thermo_scalar(db_session, species_entry=entry)
        _target(thermo, ThermoTargetKind.single_conformer, group)
        refs.append(thermo.public_ref)
    db_session.flush()
    body = client.get(url(entry)).json()
    assert [t["thermo_ref"] for t in body["groups"][0]["thermo"]] == sorted(refs)


def test_an_entry_with_nothing_declared_has_no_groups(client, db_session):
    entry = _entry(db_session, "CUN")
    make_conformer_group(db_session, entry)
    make_thermo_scalar(db_session, species_entry=entry)
    body = client.get(url(entry)).json()
    assert body["groups"] == [] and body["ensemble_thermo_refs"] == []


def test_unknown_ref_is_a_404_without_database_ids(client, db_session):
    entry = _entry(db_session, "CUU")
    response = client.get(f"{API}/species-entries/spe_{'0' * 26}/conformer-usage")
    assert response.status_code == 404
    assert str(entry.id) not in response.text.replace(entry.public_ref, "")
    assert client.get(f"{API}/species-entries/{entry.id}/conformer-usage").status_code == 422


def test_no_database_id_in_the_response(client, world):
    def keys(value):
        if isinstance(value, dict):
            for k, v in value.items():
                yield k
                yield from keys(v)
        elif isinstance(value, list):
            for v in value:
                yield from keys(v)

    assert not [k for k in keys(client.get(url(world["entry"])).json()) if k == "id" or k.endswith("_id")]


def test_statement_count_is_flat_in_the_number_of_groups(client, db_session):
    def build(n_groups):
        entry = _entry(db_session, "CUC")
        partner, product = _entry(db_session, "CUCP"), _entry(db_session, "CUCQ")
        reaction = make_chem_reaction(db_session, reactants=[entry.species, partner.species], products=[product.species])
        rxn = make_reaction_entry(db_session, reaction=reaction, reactant_entries=[entry, partner], product_entries=[product])
        statmech = make_statmech(db_session, species_entry=entry)
        for _ in range(n_groups):
            group = make_conformer_group(db_session, entry)
            thermo = make_thermo_scalar(db_session, species_entry=entry)
            _target(thermo, ThermoTargetKind.single_conformer, group)
            kinetics = make_kinetics(db_session, reaction_entry=rxn)
            _assign(db_session, kinetics, statmech, attach_conformer_selection(db_session, conformer_group=group))
        db_session.flush()
        return entry

    def count(entry) -> int:
        engine = db_session.connection().engine
        seen = []

        def _before(conn, cursor, statement, parameters, context, executemany):
            seen.append(statement)

        event.listen(engine, "before_cursor_execute", _before)
        try:
            response = client.get(url(entry))
        finally:
            event.remove(engine, "before_cursor_execute", _before)
        assert response.status_code == 200
        return len(seen), len(response.json()["groups"])

    small, small_groups = count(build(1))
    large, large_groups = count(build(6))
    assert (small_groups, large_groups) == (1, 6)
    assert small == large
    assert large == 3, f"expected 3 statements (ref lookup + 2 bulk reads), got {large}"


def test_get_under_scientific_is_anon_read():
    scope = {
        "type": "http",
        "method": "GET",
        "path": f"{API}/species-entries/spe_{'0' * 26}/conformer-usage",
        "headers": [],
        "query_string": b"",
        "client": ("203.0.113.9", 1234),
    }
    assert _classify(Request(scope))[0] == "anon_read"
