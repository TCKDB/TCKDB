"""API tests: an opt's final energy served as the sp-equivalent when no
same-level-of-theory ``sp`` calculation exists.

Covers the read-time derivation on
``GET /scientific/species-calculations/search`` (``energy.single_point_equivalent``)
and its deliberate non-effect on the conformer-observation evidence
summary (``has_sp`` / ``calculation_count``) on
``GET /scientific/conformer-observations/{ref}`` -- a derived value must
never be countable as a real ``sp`` calculation.

See docs/specs/species_calculation_search_api.md
§"energy.single_point_equivalent".
"""

from __future__ import annotations

from app.db.models.common import CalculationType
from tests.services.scientific_read._factories import (
    attach_opt_result,
    attach_sp_result,
    make_calculation_with_conformer,
    make_conformer_group,
    make_conformer_observation,
    make_lot,
    make_species,
    make_species_entry,
    next_inchi_key,
)


def _seed(db_session, *, smiles: str):
    species = make_species(db_session, smiles=smiles, inchi_key=next_inchi_key("OES"))
    entry = make_species_entry(db_session, species)
    cg = make_conformer_group(db_session, entry)
    obs = make_conformer_observation(db_session, conformer_group=cg)
    return species, entry, obs


def _search(client, entry_ref: str) -> list[dict]:
    resp = client.get(
        "/api/v1/scientific/species-calculations/search"
        f"?species_entry_ref={entry_ref}"
    )
    assert resp.status_code == 200
    return resp.json()["records"]


def _evidence_summary(client, obs_ref: str) -> dict:
    resp = client.get(f"/api/v1/scientific/conformer-observations/{obs_ref}")
    assert resp.status_code == 200
    return resp.json()["record"]["evidence_summary"]


def test_opt_without_same_lot_sp_gets_marked_derived_value(client, db_session):
    """No sp at all on the observation -> opt's own energy served as derived sp."""
    _, entry, obs = _seed(db_session, smiles="C[CH2]OES1")
    lot = make_lot(db_session, method="b3lyp", basis="def2tzvp")
    opt = make_calculation_with_conformer(
        db_session,
        species_entry=entry,
        conformer_observation=obs,
        type=CalculationType.opt,
        lot_id=lot.id,
    )
    attach_opt_result(db_session, calculation=opt, final_energy_hartree=-100.123456)

    records = _search(client, entry.public_ref)
    assert len(records) == 1
    energy = records[0]["energy"]
    assert energy["energy_hartree"] == -100.123456
    assert energy["energy_kind"] == "final_energy"
    sp_equiv = energy["single_point_equivalent"]
    assert sp_equiv is not None
    assert sp_equiv["energy_hartree"] == -100.123456
    assert sp_equiv["energy_kind"] == "final_energy_as_single_point"
    assert sp_equiv["derived_from_calculation_type"] == "opt"

    # Never counted as a real sp: no fabricated calculation, no evidence leak.
    ev = _evidence_summary(client, obs.public_ref)
    assert ev["has_sp"] is False
    assert ev["calculation_count"] == 1


def test_real_same_lot_sp_suppresses_derivation_and_is_served_verbatim(
    client, db_session
):
    """A real sp at the same LoT wins: derivation is off, and the value served
    for the sp record is the sp's own number -- not the opt's. The fixture
    picks numbers that differ so equality can't hide which one was served.
    """
    _, entry, obs = _seed(db_session, smiles="C[CH2]OES2")
    lot = make_lot(db_session, method="b3lyp", basis="6-31g")
    opt = make_calculation_with_conformer(
        db_session,
        species_entry=entry,
        conformer_observation=obs,
        type=CalculationType.opt,
        lot_id=lot.id,
    )
    attach_opt_result(db_session, calculation=opt, final_energy_hartree=-229.585559237)
    sp = make_calculation_with_conformer(
        db_session,
        species_entry=entry,
        conformer_observation=obs,
        type=CalculationType.sp,
        lot_id=lot.id,
    )
    attach_sp_result(db_session, calculation=sp, electronic_energy_hartree=-229.600000000)

    records = _search(client, entry.public_ref)
    by_type = {r["calculation"]["calculation_type"]: r for r in records}
    assert by_type["opt"]["energy"]["single_point_equivalent"] is None
    assert by_type["sp"]["energy"]["energy_hartree"] == -229.600000000
    assert (
        by_type["sp"]["energy"]["energy_hartree"]
        != by_type["opt"]["energy"]["energy_hartree"]
    )

    ev = _evidence_summary(client, obs.public_ref)
    assert ev["has_sp"] is True
    assert ev["calculation_count"] == 2


def test_sp_at_a_different_lot_does_not_suppress_the_derivation(client, db_session):
    """An sp exists on the observation, but at a different level of theory --
    a different number that does not answer the opt's own-LoT question, so
    the derivation still fires for the opt.
    """
    _, entry, obs = _seed(db_session, smiles="C[CH2]OES3")
    lot_a = make_lot(db_session, method="b3lyp", basis="def2tzvp")
    lot_b = make_lot(db_session, method="wb97xd", basis="def2tzvp")
    opt = make_calculation_with_conformer(
        db_session,
        species_entry=entry,
        conformer_observation=obs,
        type=CalculationType.opt,
        lot_id=lot_a.id,
    )
    attach_opt_result(db_session, calculation=opt, final_energy_hartree=-50.0)
    sp = make_calculation_with_conformer(
        db_session,
        species_entry=entry,
        conformer_observation=obs,
        type=CalculationType.sp,
        lot_id=lot_b.id,
    )
    attach_sp_result(db_session, calculation=sp, electronic_energy_hartree=-51.0)

    records = _search(client, entry.public_ref)
    opt_rec = next(
        r for r in records if r["calculation"]["calculation_type"] == "opt"
    )
    sp_equiv = opt_rec["energy"]["single_point_equivalent"]
    assert sp_equiv is not None
    assert sp_equiv["energy_hartree"] == -50.0

    # The real sp -- at its own, different LoT -- still counts as real evidence.
    ev = _evidence_summary(client, obs.public_ref)
    assert ev["has_sp"] is True
    assert ev["calculation_count"] == 2


def test_opt_with_null_energy_yields_no_derivation(client, db_session):
    """No CalculationOptResult row at all -> no energy, no fabrication."""
    _, entry, obs = _seed(db_session, smiles="C[CH2]OES4")
    lot = make_lot(db_session, method="b3lyp", basis="sto-3g")
    make_calculation_with_conformer(
        db_session,
        species_entry=entry,
        conformer_observation=obs,
        type=CalculationType.opt,
        lot_id=lot.id,
    )
    # No attach_opt_result call: final_energy_hartree stays null.

    records = _search(client, entry.public_ref)
    energy = records[0]["energy"]
    assert energy["energy_hartree"] is None
    assert energy["single_point_equivalent"] is None
