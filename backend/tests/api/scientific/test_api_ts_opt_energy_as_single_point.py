"""API tests: a transition-state opt's final energy served as the
sp-equivalent when no same-level-of-theory ``sp`` exists on the *same*
``transition_state_entry``.

This is the transition-state side of the derivation shipped for species
in #313 (see ``test_api_opt_energy_as_single_point.py``). The guard used
to key on ``conformer_observation_id`` — a species-only concept, since a
transition state is a saddle point with no conformer basin and so never
carries one. The fix generalizes the guard to match on whichever owner a
calculation actually has: ``conformer_observation_id`` for a
species-owned calculation, ``transition_state_entry_id`` for a
TS-owned one.

Covers the read-time derivation surfaced on
``GET /scientific/transition-states/{ts_ref}?include=calculations`` and
``GET /scientific/transition-state-entries/{tse_ref}?include=calculations``
(``calculations[].energy.single_point_equivalent``), and its deliberate
non-effect on the TS-entry evidence summary (``has_sp`` /
``calculation_count``) on the same TS-entry detail endpoint.

See ``backend/docs/specs/scientific_transition_state_reads.md``.
"""

from __future__ import annotations

from app.db.models.common import CalculationType, TransitionStateEntryStatus
from tests.services.scientific_read._factories import (
    attach_opt_result,
    attach_sp_result,
    make_calculation,
    make_chem_reaction,
    make_lot,
    make_reaction_entry,
    make_species,
    make_species_entry,
    make_transition_state,
    make_transition_state_entry,
    next_inchi_key,
)


def _make_reaction_with_ts_entries(db_session, *, n_entries: int = 1, prefix: str):
    """Build a Species×2 -> ChemReaction -> ReactionEntry -> TS -> TS-entries chain."""
    sp_a = make_species(db_session, inchi_key=next_inchi_key(f"{prefix}A"))
    sp_b = make_species(db_session, inchi_key=next_inchi_key(f"{prefix}B"))
    sp_c = make_species(db_session, inchi_key=next_inchi_key(f"{prefix}C"))
    sp_d = make_species(db_session, inchi_key=next_inchi_key(f"{prefix}D"))
    se_a = make_species_entry(db_session, sp_a)
    se_b = make_species_entry(db_session, sp_b)
    se_c = make_species_entry(db_session, sp_c)
    se_d = make_species_entry(db_session, sp_d)
    chem = make_chem_reaction(db_session, reactants=[sp_a, sp_b], products=[sp_c, sp_d])
    rxe = make_reaction_entry(
        db_session,
        reaction=chem,
        reactant_entries=[se_a, se_b],
        product_entries=[se_c, se_d],
    )
    ts = make_transition_state(db_session, reaction_entry=rxe, label=f"{prefix}-ts")
    entries = [
        make_transition_state_entry(
            db_session,
            transition_state=ts,
            charge=0,
            multiplicity=2,
            status=TransitionStateEntryStatus.optimized,
        )
        for _ in range(n_entries)
    ]
    return ts, entries


def _tse_url(handle: str, **params) -> str:
    base = f"/api/v1/scientific/transition-state-entries/{handle}"
    if not params:
        return base
    qs = "&".join(f"{k}={v}" for k, v in params.items())
    return f"{base}?{qs}"


def _tse_calculations(client, tse_ref: str) -> list[dict]:
    resp = client.get(_tse_url(tse_ref, include="calculations"))
    assert resp.status_code == 200, resp.text
    return resp.json()["record"]["calculations"]


def _tse_evidence(client, tse_ref: str) -> dict:
    resp = client.get(_tse_url(tse_ref))
    assert resp.status_code == 200, resp.text
    return resp.json()["record"]["evidence_summary"]


def test_ts_opt_without_same_lot_sp_gets_marked_derived_value(client, db_session):
    """No sp at all on the TS entry -> opt's own energy served as derived sp."""
    _, entries = _make_reaction_with_ts_entries(db_session, prefix="TSD1")
    tse = entries[0]
    lot = make_lot(db_session, method="b3lyp", basis="def2tzvp")
    opt = make_calculation(
        db_session,
        type=CalculationType.opt,
        transition_state_entry_id=tse.id,
        lot_id=lot.id,
    )
    attach_opt_result(db_session, calculation=opt, final_energy_hartree=-150.246802)

    calcs = _tse_calculations(client, tse.public_ref)
    assert len(calcs) == 1
    energy = calcs[0]["energy"]
    assert energy["energy_hartree"] == -150.246802
    assert energy["energy_kind"] == "final_energy"
    sp_equiv = energy["single_point_equivalent"]
    assert sp_equiv is not None
    assert sp_equiv["energy_hartree"] == -150.246802
    assert sp_equiv["energy_kind"] == "final_energy_as_single_point"
    assert sp_equiv["derived_from_calculation_type"] == "opt"

    # Never counted as a real sp: no fabricated calculation, no evidence leak.
    ev = _tse_evidence(client, tse.public_ref)
    assert ev["has_sp"] is False
    assert ev["calculation_count"] == 1


def test_ts_real_same_lot_sp_suppresses_derivation_and_is_served_verbatim(
    client, db_session
):
    """A real sp at the same LoT, on the *same* TS entry, wins: derivation is
    off, and the served sp value is the sp's own number -- not the opt's.
    Values deliberately differ so equality can't hide which one was served.
    """
    _, entries = _make_reaction_with_ts_entries(db_session, prefix="TSD2")
    tse = entries[0]
    lot = make_lot(db_session, method="b3lyp", basis="6-31g")
    opt = make_calculation(
        db_session,
        type=CalculationType.opt,
        transition_state_entry_id=tse.id,
        lot_id=lot.id,
    )
    attach_opt_result(db_session, calculation=opt, final_energy_hartree=-329.585559237)
    sp = make_calculation(
        db_session,
        type=CalculationType.sp,
        transition_state_entry_id=tse.id,
        lot_id=lot.id,
    )
    attach_sp_result(db_session, calculation=sp, electronic_energy_hartree=-329.600000000)

    calcs = _tse_calculations(client, tse.public_ref)
    by_type = {c["type"]: c for c in calcs}
    assert by_type["opt"]["energy"]["single_point_equivalent"] is None
    assert by_type["sp"]["energy"]["energy_hartree"] == -329.600000000
    assert (
        by_type["sp"]["energy"]["energy_hartree"]
        != by_type["opt"]["energy"]["energy_hartree"]
    )

    ev = _tse_evidence(client, tse.public_ref)
    assert ev["has_sp"] is True
    assert ev["calculation_count"] == 2


def test_ts_sp_on_a_different_entry_does_not_suppress_the_derivation(
    client, db_session
):
    """Cross-owner leakage guard: an ``sp`` at the *same* level of theory but
    on a **different** ``transition_state_entry`` must not suppress the
    derivation for this entry's ``opt``."""
    _, entries_a = _make_reaction_with_ts_entries(db_session, prefix="TSD3")
    _, entries_b = _make_reaction_with_ts_entries(db_session, prefix="TSD4")
    tse_a, tse_b = entries_a[0], entries_b[0]
    lot = make_lot(db_session, method="wb97xd", basis="def2tzvp")
    opt = make_calculation(
        db_session,
        type=CalculationType.opt,
        transition_state_entry_id=tse_a.id,
        lot_id=lot.id,
    )
    attach_opt_result(db_session, calculation=opt, final_energy_hartree=-88.0)
    other_sp = make_calculation(
        db_session,
        type=CalculationType.sp,
        transition_state_entry_id=tse_b.id,
        lot_id=lot.id,
    )
    attach_sp_result(db_session, calculation=other_sp, electronic_energy_hartree=-999.0)

    calcs = _tse_calculations(client, tse_a.public_ref)
    assert len(calcs) == 1
    sp_equiv = calcs[0]["energy"]["single_point_equivalent"]
    assert sp_equiv is not None
    assert sp_equiv["energy_hartree"] == -88.0

    ev = _tse_evidence(client, tse_a.public_ref)
    assert ev["has_sp"] is False
    assert ev["calculation_count"] == 1


def test_ts_search_and_browse_include_calculations_carry_the_same_derivation(
    client, db_session
):
    """``_build_calculations_summary`` is the shared per-entry builder behind
    TS-entry detail, ``/transition-states/search``, and
    ``/transition-states/browse`` (byte-identical record shape, per
    ``transition_states_search.browse_transition_states``'s own
    docstring). One generalized derivation reaches all three -- a
    derivation offered on one and silently absent from a sibling would be
    its own inconsistency.
    """
    ts, entries = _make_reaction_with_ts_entries(db_session, prefix="TSD6")
    tse = entries[0]
    lot = make_lot(db_session, method="ccsd(t)", basis="cc-pvtz")
    opt = make_calculation(
        db_session,
        type=CalculationType.opt,
        transition_state_entry_id=tse.id,
        lot_id=lot.id,
    )
    attach_opt_result(db_session, calculation=opt, final_energy_hartree=-12.5)

    search_body = client.get(
        "/api/v1/scientific/transition-states/search"
        f"?transition_state_entry_ref={tse.public_ref}&include=calculations"
    ).json()
    search_calc = search_body["records"][0]["calculations"][0]
    assert search_calc["energy"]["single_point_equivalent"]["energy_hartree"] == -12.5

    browse_body = client.get(
        "/api/v1/scientific/transition-states/browse"
        f"?has_calculations=true&include=calculations"
    ).json()
    browse_calc_by_ref = {
        c["calculation_ref"]: c
        for rec in browse_body["records"]
        for c in (rec["calculations"] or [])
    }
    assert (
        browse_calc_by_ref[opt.public_ref]["energy"]["single_point_equivalent"][
            "energy_hartree"
        ]
        == -12.5
    )


def test_ts_opt_with_null_energy_yields_no_derivation(client, db_session):
    """No CalculationOptResult row at all -> no energy, no fabrication."""
    _, entries = _make_reaction_with_ts_entries(db_session, prefix="TSD5")
    tse = entries[0]
    lot = make_lot(db_session, method="b3lyp", basis="sto-3g")
    make_calculation(
        db_session,
        type=CalculationType.opt,
        transition_state_entry_id=tse.id,
        lot_id=lot.id,
    )
    # No attach_opt_result call: final_energy_hartree stays null.

    calcs = _tse_calculations(client, tse.public_ref)
    energy = calcs[0]["energy"]
    assert energy["energy_hartree"] is None
    assert energy["single_point_equivalent"] is None
