"""GET /api/v1/scientific/reaction-entries/{ref}/energy-profile: honest reactant, transition-state, product profiles."""

from __future__ import annotations

import pytest
from sqlalchemy import event
from starlette.requests import Request

from app.api.rate_limit import _classify
from app.chemistry.units import HARTREE_TO_KJ_MOL
from app.db.models.common import CalculationType, KineticsCalculationRole, RecordReviewStatus, SubmissionRecordType
from app.db.models.kinetics import KineticsSourceCalculation
from app.db.models.record_review import RecordReview
from tests.services.scientific_read._factories import (
    attach_opt_result,
    attach_sp_result,
    make_calculation,
    make_chem_reaction,
    make_kinetics,
    make_lot,
    make_reaction_entry,
    make_software_release,
    make_species,
    make_species_entry,
    make_transition_state,
    make_transition_state_entry,
    next_inchi_key,
    set_review,
)

API = "/api/v1/scientific"


def url(entry) -> str:
    return f"{API}/reaction-entries/{entry.public_ref}/energy-profile"


def _entry(db_session, tag):
    return make_species_entry(db_session, make_species(db_session, inchi_key=next_inchi_key(tag)))


def _sp(db_session, *, owner, lot, release, energy):
    kwargs = {"species_entry_id": owner.id} if hasattr(owner, "species_id") else {"transition_state_entry_id": owner.id}
    calc = make_calculation(db_session, type=CalculationType.sp, lot_id=lot.id, software_release_id=release.id, **kwargs)
    attach_sp_result(db_session, calculation=calc, electronic_energy_hartree=energy)
    return calc


def _reaction(db_session, tag, *, reactants, products):
    """A reaction entry over species entries given as lists (an entry listed twice is a coefficient of 2)."""
    chem = make_chem_reaction(
        db_session,
        reactants=[e.species for e in dict.fromkeys(reactants)],
        products=[e.species for e in dict.fromkeys(products)],
    )
    return make_reaction_entry(db_session, reaction=chem, reactant_entries=reactants, product_entries=products)


def _ts(db_session, rxn):
    return make_transition_state_entry(db_session, transition_state=make_transition_state(db_session, reaction_entry=rxn))


@pytest.fixture
def world(db_session):
    lot = make_lot(db_session, method="wb97xd", basis="def2tzvp")
    orca = make_software_release(db_session, name="orca", version="6.0")
    gaussian = make_software_release(db_session, name="gaussian", version="16")
    a, b, c = (_entry(db_session, t) for t in ("EPA", "EPB", "EPC"))
    rxn = _reaction(db_session, "EP", reactants=[a, b], products=[c])
    tse = _ts(db_session, rxn)
    return {"lot": lot, "orca": orca, "gaussian": gaussian, "a": a, "b": b, "c": c, "rxn": rxn, "tse": tse}


def _complete(db_session, w, release=None, shift=0.0):
    release = release or w["orca"]
    _sp(db_session, owner=w["a"], lot=w["lot"], release=release, energy=-40.0 + shift)
    _sp(db_session, owner=w["b"], lot=w["lot"], release=release, energy=-75.0 + shift)
    _sp(db_session, owner=w["c"], lot=w["lot"], release=release, energy=-115.1 + shift)
    return _sp(db_session, owner=w["tse"], lot=w["lot"], release=release, energy=-114.9 + shift)


def test_single_level_profile(client, db_session, world):
    ts_calc = _complete(db_session, world)
    body = client.get(url(world["rxn"])).json()
    assert body["reaction_entry_ref"] == world["rxn"].public_ref
    (profile,) = body["profiles"]
    assert body["unavailable"] == [] and body["cited_by_kinetics"] == []
    assert profile["transition_state_entry_ref"] == world["tse"].public_ref
    assert profile["program"] == "orca"
    assert profile["energy_kind"] == "electronic"
    assert "lowest single-point energy" in profile["definition"]
    assert profile["level_of_theory"]["level_of_theory_ref"] == world["lot"].public_ref
    assert profile["transition_state"]["calculation_ref"] == ts_calc.public_ref
    assert profile["transition_state"]["software"]["version"] == "6.0"
    assert {m["species_entry_ref"] for m in profile["reactants"]} == {world["a"].public_ref, world["b"].public_ref}
    assert all(m["energy_type"] == "single_point" and m["count"] == 1 for m in profile["reactants"] + profile["products"])
    assert profile["energy_hartree"] == pytest.approx({"reactants": -115.0, "transition_state": -114.9, "products": -115.1})
    assert profile["relative_kj_mol"]["reactants"] == 0.0
    assert profile["relative_kj_mol"]["transition_state"] == pytest.approx(0.1 * HARTREE_TO_KJ_MOL)
    assert profile["relative_kj_mol"]["products"] == pytest.approx(-0.1 * HARTREE_TO_KJ_MOL)


def test_two_levels_give_two_profiles(client, db_session, world):
    _complete(db_session, world)
    other = make_lot(db_session, method="b3lyp", basis="6-31g")
    for owner, energy in ((world["a"], -41.0), (world["b"], -76.0), (world["c"], -117.5), (world["tse"], -116.0)):
        _sp(db_session, owner=owner, lot=other, release=world["orca"], energy=energy)
    profiles = client.get(url(world["rxn"])).json()["profiles"]
    assert [p["level_of_theory"]["method"] for p in profiles] == ["b3lyp", "wb97xd"]
    assert profiles[0]["relative_kj_mol"]["transition_state"] == pytest.approx(1.0 * HARTREE_TO_KJ_MOL)


def test_mixed_programs_are_refused_and_never_summed(client, db_session, world):
    lot, orca, gaussian = world["lot"], world["orca"], world["gaussian"]
    _sp(db_session, owner=world["tse"], lot=lot, release=orca, energy=-114.9)
    _sp(db_session, owner=world["a"], lot=lot, release=orca, energy=-40.0)
    _sp(db_session, owner=world["b"], lot=lot, release=gaussian, energy=-75.0)
    _sp(db_session, owner=world["c"], lot=lot, release=orca, energy=-115.1)
    body = client.get(url(world["rxn"])).json()
    assert body["profiles"] == []
    (item,) = body["unavailable"]
    assert item["code"] == "points_from_different_programs"
    assert item["transition_state_entry_ref"] == world["tse"].public_ref and item["program"] == "orca"
    # A program that covers every point gets its own profile beside the refusal.
    _sp(db_session, owner=world["b"], lot=lot, release=orca, energy=-75.0)
    body = client.get(url(world["rxn"])).json()
    assert [p["program"] for p in body["profiles"]] == ["orca"] and body["unavailable"] == []


def test_missing_product_and_missing_reactant(client, db_session, world):
    lot, orca = world["lot"], world["orca"]
    _sp(db_session, owner=world["tse"], lot=lot, release=orca, energy=-114.9)
    _sp(db_session, owner=world["a"], lot=lot, release=orca, energy=-40.0)
    _sp(db_session, owner=world["b"], lot=lot, release=orca, energy=-75.0)
    (item,) = client.get(url(world["rxn"])).json()["unavailable"]
    assert item["code"] == "no_product_energy_at_level"
    other = make_lot(db_session, method="b3lyp", basis="6-31g")
    _sp(db_session, owner=world["tse"], lot=other, release=orca, energy=-1.0)
    codes = {u["level_of_theory"]["method"]: u["code"] for u in client.get(url(world["rxn"])).json()["unavailable"]}
    assert codes == {"wb97xd": "no_product_energy_at_level", "b3lyp": "no_reactant_energy_at_level"}


def test_missing_ts_energy(client, db_session, world):
    (item,) = client.get(url(world["rxn"])).json()["unavailable"]
    assert item == {
        "transition_state_entry_ref": world["tse"].public_ref,
        "transition_state_entry_review_status": "not_reviewed",
        "code": "no_ts_energy",
        "level_of_theory": None,
        "program": None,
    }


def _bare_sp(db_session, owner, lot, energy):
    kwargs = {"species_entry_id": owner.id} if hasattr(owner, "species_id") else {"transition_state_entry_id": owner.id}
    calc = make_calculation(db_session, type=CalculationType.sp, lot_id=lot.id, **kwargs)
    attach_sp_result(db_session, calculation=calc, electronic_energy_hartree=energy)
    return calc


def test_a_ts_energy_with_no_program_is_not_reported_as_no_energy(client, db_session, world):
    _bare_sp(db_session, world["tse"], world["lot"], -114.9)
    (item,) = client.get(url(world["rxn"])).json()["unavailable"]
    assert item["code"] == "no_program_recorded" and item["program"] is None
    assert item["level_of_theory"]["level_of_theory_ref"] == world["lot"].public_ref


def test_a_reactant_energy_with_no_program_is_not_reported_as_no_energy(client, db_session, world):
    lot, orca = world["lot"], world["orca"]
    _sp(db_session, owner=world["tse"], lot=lot, release=orca, energy=-114.9)
    _bare_sp(db_session, world["a"], lot, -40.0)
    _sp(db_session, owner=world["b"], lot=lot, release=orca, energy=-75.0)
    _sp(db_session, owner=world["c"], lot=lot, release=orca, energy=-115.1)
    body = client.get(url(world["rxn"])).json()
    assert body["profiles"] == []
    assert [u["code"] for u in body["unavailable"]] == ["no_program_recorded"]


def test_a_repeated_participant_is_counted_by_its_coefficient(client, db_session):
    lot = make_lot(db_session, method="wb97xd", basis="def2tzvp")
    orca = make_software_release(db_session, name="orca", version="6.0")
    a, c = _entry(db_session, "EQA"), _entry(db_session, "EQC")
    rxn = _reaction(db_session, "EQ", reactants=[a, a], products=[c])
    tse = _ts(db_session, rxn)
    _sp(db_session, owner=a, lot=lot, release=orca, energy=-40.0)
    _sp(db_session, owner=c, lot=lot, release=orca, energy=-80.5)
    _sp(db_session, owner=tse, lot=lot, release=orca, energy=-79.9)
    (profile,) = client.get(url(rxn)).json()["profiles"]
    (molecule,) = profile["reactants"]
    assert molecule["count"] == 2 and molecule["electronic_energy_hartree"] == -40.0
    assert profile["energy_hartree"]["reactants"] == pytest.approx(-80.0)
    assert profile["relative_kj_mol"]["transition_state"] == pytest.approx(0.1 * HARTREE_TO_KJ_MOL)


def test_lowest_single_point_wins_and_optimisation_is_the_fallback(client, db_session, world):
    lot, orca = world["lot"], world["orca"]
    for energy in (-40.0, -40.2):
        _sp(db_session, owner=world["a"], lot=lot, release=orca, energy=energy)
    opt = make_calculation(db_session, type=CalculationType.opt, species_entry_id=world["b"].id, lot_id=lot.id, software_release_id=orca.id)
    attach_opt_result(db_session, calculation=opt, final_energy_hartree=-75.0)
    _sp(db_session, owner=world["c"], lot=lot, release=orca, energy=-115.1)
    _sp(db_session, owner=world["tse"], lot=lot, release=orca, energy=-114.9)
    (profile,) = client.get(url(world["rxn"])).json()["profiles"]
    by_ref = {m["species_entry_ref"]: m for m in profile["reactants"]}
    assert by_ref[world["a"].public_ref]["electronic_energy_hartree"] == -40.2
    assert by_ref[world["b"].public_ref]["energy_type"] == "optimization"
    assert by_ref[world["b"].public_ref]["calculation_ref"] == opt.public_ref


def test_a_sibling_reaction_entry_never_lends_a_point(client, db_session, world):
    lot, orca = world["lot"], world["orca"]
    _sp(db_session, owner=world["a"], lot=lot, release=orca, energy=-40.0)
    _sp(db_session, owner=world["b"], lot=lot, release=orca, energy=-75.0)
    _sp(db_session, owner=world["tse"], lot=lot, release=orca, energy=-114.9)
    # A sibling entry of the same reaction has the product energy and its own transition state; this entry has neither.
    sibling = make_reaction_entry(
        db_session,
        reaction=world["rxn"].reaction,
        reactant_entries=[world["a"], world["b"]],
        product_entries=[_entry(db_session, "EPS")],
    )
    sibling_product = sibling.structure_participants[-1].species_entry
    sibling_ts = _ts(db_session, sibling)
    sib_calc = _sp(db_session, owner=sibling_product, lot=lot, release=orca, energy=-115.1)
    _sp(db_session, owner=sibling_ts, lot=lot, release=orca, energy=-1.0)
    body = client.get(url(world["rxn"])).json()
    assert body["profiles"] == []
    assert [u["code"] for u in body["unavailable"]] == ["no_product_energy_at_level"]
    assert sibling_ts.public_ref not in str(body) and sib_calc.public_ref not in str(body)


def test_kinetics_citations_are_their_own_block(client, db_session, world):
    _complete(db_session, world)
    cited = _sp(db_session, owner=world["a"], lot=world["lot"], release=world["gaussian"], energy=-39.0)
    kinetics = make_kinetics(db_session, reaction_entry=world["rxn"])
    other_kinetics = make_kinetics(db_session, reaction_entry=world["rxn"])
    db_session.add(
        KineticsSourceCalculation(kinetics_id=kinetics.id, calculation_id=cited.id, role=KineticsCalculationRole.reactant_energy)
    )
    freq = make_calculation(db_session, type=CalculationType.freq, species_entry_id=world["a"].id)
    db_session.add(KineticsSourceCalculation(kinetics_id=kinetics.id, calculation_id=freq.id, role=KineticsCalculationRole.freq))
    db_session.flush()
    body = client.get(url(world["rxn"])).json()
    (block,) = body["cited_by_kinetics"]
    assert block["kinetics_ref"] == kinetics.public_ref and other_kinetics.public_ref not in str(body["cited_by_kinetics"])
    (calc,) = block["calculations"]
    assert calc["role"] == "reactant_energy" and calc["calculation_ref"] == cited.public_ref
    assert calc["electronic_energy_hartree"] == -39.0 and calc["software"]["program"] == "gaussian"
    assert calc["species_entry_ref"] == world["a"].public_ref
    # The profiles stay as they were: the cited energy is not merged in.
    assert body["profiles"][0]["energy_hartree"]["reactants"] == pytest.approx(-115.0)


def test_rejected_kinetics_are_hidden_by_default_and_counted(client, db_session, world):
    rejected = make_kinetics(db_session, reaction_entry=world["rxn"])
    cited = _sp(db_session, owner=world["a"], lot=world["lot"], release=world["orca"], energy=-39.0)
    db_session.add(
        KineticsSourceCalculation(kinetics_id=rejected.id, calculation_id=cited.id, role=KineticsCalculationRole.reactant_energy)
    )
    set_review(db_session, record_type=SubmissionRecordType.kinetics, record_id=rejected.id, status=RecordReviewStatus.rejected)
    body = client.get(url(world["rxn"])).json()
    assert body["cited_by_kinetics"] == []
    assert body["request"]["hidden_by_default"]["counts"]["rejected"] == 1
    shown = client.get(url(world["rxn"]), params={"include_rejected": "true"}).json()
    assert [b["kinetics_ref"] for b in shown["cited_by_kinetics"]] == [rejected.public_ref]


def test_curated_floors_the_ts_entry_and_badges_its_evidence(client, db_session, world):
    ts_calc = _complete(db_session, world)
    curated = {"profile": "curated"}
    hidden = client.get(url(world["rxn"]), params=curated)
    assert hidden.status_code == 200
    assert hidden.json()["profiles"] == [] and hidden.json()["unavailable"] == []
    assert world["tse"].public_ref not in hidden.text
    # Approving the record shows the profile; its calculations are evidence, shown with their (unreviewed) badge.
    set_review(db_session, record_type=SubmissionRecordType.transition_state_entry, record_id=world["tse"].id, status=RecordReviewStatus.approved)
    (profile,) = client.get(url(world["rxn"]), params=curated).json()["profiles"]
    assert profile["transition_state_entry_review_status"] == "approved"
    assert profile["transition_state"]["calculation_ref"] == ts_calc.public_ref
    assert profile["transition_state"]["review_status"] == "not_reviewed"
    assert {m["review_status"] for m in profile["reactants"] + profile["products"]} == {"not_reviewed"}


def test_a_rejected_calculation_is_never_used(client, db_session, world):
    lot, orca = world["lot"], world["orca"]
    _complete(db_session, world)
    better = _sp(db_session, owner=world["a"], lot=lot, release=orca, energy=-41.0)
    set_review(db_session, record_type=SubmissionRecordType.calculation, record_id=better.id, status=RecordReviewStatus.rejected)
    for params in ({}, {"include_rejected": "true"}, {"min_review_status": "not_reviewed"}):
        body = client.get(url(world["rxn"]), params=params).json()
        assert body["profiles"] and better.public_ref not in str(body["profiles"])
        for profile in body["profiles"]:
            by_ref = {m["species_entry_ref"]: m for m in profile["reactants"]}
            assert by_ref[world["a"].public_ref]["electronic_energy_hartree"] == -40.0
    # A usable lower one is used, and badged.
    db_session.query(RecordReview).filter_by(record_type=SubmissionRecordType.calculation, record_id=better.id).one().status = (
        RecordReviewStatus.under_review
    )
    db_session.flush()
    (profile,) = client.get(url(world["rxn"])).json()["profiles"]
    used = {m["species_entry_ref"]: m for m in profile["reactants"]}[world["a"].public_ref]
    assert used["calculation_ref"] == better.public_ref and used["review_status"] == "under_review"


def test_a_rejected_ts_entry_is_hidden_by_default_and_counted(client, db_session, world):
    _complete(db_session, world)
    set_review(db_session, record_type=SubmissionRecordType.transition_state_entry, record_id=world["tse"].id, status=RecordReviewStatus.rejected)
    body = client.get(url(world["rxn"])).json()
    assert body["profiles"] == [] and body["unavailable"] == []
    assert body["request"]["hidden_by_default"]["counts"]["rejected"] == 1
    shown = client.get(url(world["rxn"]), params={"include_rejected": "true"}).json()
    assert shown["profiles"][0]["transition_state_entry_review_status"] == "rejected"
    assert client.get(url(world["rxn"]), params={"profile": "curated"}).json()["profiles"] == []


def _opt(db_session, world, owner, energy, converged):
    kwargs = {"species_entry_id": owner.id} if hasattr(owner, "species_id") else {"transition_state_entry_id": owner.id}
    calc = make_calculation(
        db_session, type=CalculationType.opt, lot_id=world["lot"].id, software_release_id=world["orca"].id, **kwargs
    )
    attach_opt_result(db_session, calculation=calc, final_energy_hartree=energy, converged=converged)
    return calc


def test_unconverged_optimisations_are_excluded_and_unrecorded_ones_marked(client, db_session, world):
    _opt(db_session, world, world["a"], -41.0, False)
    kept = _opt(db_session, world, world["a"], -40.0, True)
    _opt(db_session, world, world["b"], -75.0, None)
    _sp(db_session, owner=world["c"], lot=world["lot"], release=world["orca"], energy=-115.1)
    _opt(db_session, world, world["tse"], -114.9, False)
    body = client.get(url(world["rxn"])).json()
    assert body["profiles"] == [] and [u["code"] for u in body["unavailable"]] == ["no_ts_energy"]
    ts_ok = _opt(db_session, world, world["tse"], -114.9, None)
    (profile,) = client.get(url(world["rxn"])).json()["profiles"]
    by_ref = {m["species_entry_ref"]: m for m in profile["reactants"]}
    a, b = by_ref[world["a"].public_ref], by_ref[world["b"].public_ref]
    assert a["calculation_ref"] == kept.public_ref and a["convergence_unrecorded"] is False
    assert b["convergence_unrecorded"] is True
    assert profile["transition_state"]["calculation_ref"] == ts_ok.public_ref
    assert profile["transition_state"]["convergence_unrecorded"] is True
    assert profile["energy_types"] == ["optimization", "single_point"]


def test_a_single_point_wins_over_a_lower_optimisation_energy(client, db_session, world):
    _complete(db_session, world)
    _opt(db_session, world, world["a"], -45.0, True)
    (profile,) = client.get(url(world["rxn"])).json()["profiles"]
    used = {m["species_entry_ref"]: m for m in profile["reactants"]}[world["a"].public_ref]
    assert used["energy_type"] == "single_point" and used["electronic_energy_hartree"] == -40.0


def test_a_profile_states_the_energy_kinds_and_versions_it_uses(client, db_session, world):
    lot, orca = world["lot"], world["orca"]
    orca5 = make_software_release(db_session, name="orca", version="5.0")
    _sp(db_session, owner=world["a"], lot=lot, release=orca, energy=-40.0)
    _sp(db_session, owner=world["b"], lot=lot, release=orca5, energy=-75.0)
    _sp(db_session, owner=world["c"], lot=lot, release=orca, energy=-115.1)
    _sp(db_session, owner=world["tse"], lot=lot, release=orca, energy=-114.9)
    (profile,) = client.get(url(world["rxn"])).json()["profiles"]
    assert profile["energy_types"] == ["single_point"]
    assert profile["versions"] == ["5.0", "6.0"]


def test_the_headline_names_the_real_rule(client, db_session, world):
    _complete(db_session, world)
    definition = client.get(url(world["rxn"])).json()["profiles"][0]["definition"]
    assert "lowest single-point energy" in definition and "only when it has no single point" in definition


def test_a_cited_non_participant_energy_never_enters_a_profile(client, db_session, world):
    _complete(db_session, world)
    stranger = _entry(db_session, "EPX")
    lower = _sp(db_session, owner=stranger, lot=world["lot"], release=world["orca"], energy=-200.0)
    irc = _sp(db_session, owner=stranger, lot=world["lot"], release=world["orca"], energy=-300.0)
    kinetics = make_kinetics(db_session, reaction_entry=world["rxn"])
    for calc, role in ((lower, KineticsCalculationRole.reactant_energy), (irc, KineticsCalculationRole.irc)):
        db_session.add(KineticsSourceCalculation(kinetics_id=kinetics.id, calculation_id=calc.id, role=role))
    db_session.flush()
    before = client.get(url(world["rxn"])).json()
    (block,) = before["cited_by_kinetics"]
    assert [c["calculation_ref"] for c in block["calculations"]] == [lower.public_ref]
    assert {c["role"] for c in block["calculations"]} <= {"reactant_energy", "product_energy", "ts_energy"}
    assert irc.public_ref not in str(before) and lower.public_ref not in str(before["profiles"])
    assert before["profiles"][0]["energy_hartree"]["reactants"] == pytest.approx(-115.0)
    assert block["calculations"][0]["review_status"] == "not_reviewed"


def test_no_internal_ids_in_the_response(client, db_session, world):
    _complete(db_session, world)
    kinetics = make_kinetics(db_session, reaction_entry=world["rxn"])
    cited = _sp(db_session, owner=world["a"], lot=world["lot"], release=world["orca"], energy=-39.0)
    db_session.add(
        KineticsSourceCalculation(kinetics_id=kinetics.id, calculation_id=cited.id, role=KineticsCalculationRole.reactant_energy)
    )
    db_session.flush()

    def keys(value):
        if isinstance(value, dict):
            for k, v in value.items():
                yield k
                yield from keys(v)
        elif isinstance(value, list):
            for v in value:
                yield from keys(v)

    body = client.get(url(world["rxn"])).json()
    assert body["profiles"] and body["cited_by_kinetics"]
    assert not [k for k in keys(body) if k == "id" or k.endswith("_id")]


def test_unknown_ref_is_the_existing_404_and_an_integer_is_refused(client, db_session, world):
    response = client.get(f"{API}/reaction-entries/rxe_{'0' * 26}/energy-profile")
    assert response.status_code == 404
    assert response.json()["code"] == "handle_not_found"
    assert client.get(f"{API}/reaction-entries/{world['rxn'].id}/energy-profile").status_code == 422


def test_statement_count_is_flat_in_participants_and_transition_states(client, db_session):
    lot = make_lot(db_session, method="wb97xd", basis="def2tzvp")
    orca = make_software_release(db_session, name="orca", version="6.0")

    def build(n):
        reactants = [_entry(db_session, "EC") for _ in range(n)]
        product = _entry(db_session, "ECP")
        rxn = _reaction(db_session, "EC", reactants=reactants, products=[product])
        for e in (*reactants, product):
            _sp(db_session, owner=e, lot=lot, release=orca, energy=-10.0)
        for _ in range(n):
            _sp(db_session, owner=_ts(db_session, rxn), lot=lot, release=orca, energy=-9.0)
        return rxn

    def count(rxn):
        engine = db_session.connection().engine
        seen = []

        def _before(conn, cursor, statement, parameters, context, executemany):
            seen.append(statement)

        event.listen(engine, "before_cursor_execute", _before)
        try:
            response = client.get(url(rxn))
        finally:
            event.remove(engine, "before_cursor_execute", _before)
        assert response.status_code == 200
        return len(seen), len(response.json()["profiles"])

    small, small_profiles = count(build(1))
    large, large_profiles = count(build(5))
    assert (small_profiles, large_profiles) == (1, 5)
    # Participants and transition-state entries add no statements.
    assert large - small <= 0, f"{small} statements for 1 participant, {large} for 5"


def test_statement_count_is_flat_in_the_number_of_levels(client, db_session):
    orca = make_software_release(db_session, name="orca", version="6.0")

    def build(n_levels):
        a, c = _entry(db_session, "EL"), _entry(db_session, "ELP")
        rxn = _reaction(db_session, "EL", reactants=[a], products=[c])
        tse = _ts(db_session, rxn)
        for i in range(n_levels):
            lot = make_lot(db_session, method=f"m{i}", basis="b")
            for owner in (a, c, tse):
                _sp(db_session, owner=owner, lot=lot, release=orca, energy=-10.0 - i)
        return rxn

    def count(rxn):
        engine = db_session.connection().engine
        seen = []

        def _before(conn, cursor, statement, parameters, context, executemany):
            seen.append(statement)

        event.listen(engine, "before_cursor_execute", _before)
        try:
            response = client.get(url(rxn))
        finally:
            event.remove(engine, "before_cursor_execute", _before)
        assert response.status_code == 200
        return len(seen), len(response.json()["profiles"])

    small, small_profiles = count(build(1))
    large, large_profiles = count(build(6))
    assert (small_profiles, large_profiles) == (1, 6)
    assert large <= small, f"{small} statements for 1 level, {large} for 6"


def test_get_under_scientific_is_anon_read():
    scope = {
        "type": "http",
        "method": "GET",
        "path": f"{API}/reaction-entries/rxe_{'0' * 26}/energy-profile",
        "headers": [],
        "query_string": b"",
        "client": ("203.0.113.9", 1234),
    }
    assert _classify(Request(scope))[0] == "anon_read"
