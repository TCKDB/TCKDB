"""Service-layer tests for the ML-dataset export (the "living RDB7" surface).

Exercises the species/conformer-centric and reaction-centric streaming
builders in ``app.services.scientific_read.ml_dataset`` directly against a
``Session`` (HTTP-free), mirroring ``test_export.py``.
"""

from __future__ import annotations

import json

import pytest

from app.db.models.common import (
    CalculationGeometryRole,
    CalculationType,
    RecordReviewStatus,
    SubmissionRecordType,
)
from app.services.scientific_read.ml_dataset import (
    HARTREE_TO_KJ_MOL,
    MLFilters,
    iter_ml_reactions_ndjson,
    iter_ml_species_ndjson,
)
from tests.services.scientific_read._factories import (
    attach_freq_result,
    attach_geometry_atoms,
    attach_hessian,
    attach_input_geometry,
    attach_opt_result,
    attach_output_geometry,
    attach_sp_result,
    make_calculation,
    make_calculation_with_conformer,
    make_chem_reaction,
    make_conformer_group,
    make_conformer_observation,
    make_geometry,
    make_kinetics,
    make_lot,
    make_reaction_entry,
    make_species,
    make_species_entry,
    make_thermo_scalar,
    make_transition_state,
    make_transition_state_entry,
    next_inchi_key,
    set_review,
)


def _approve(session, record_type, record_id):
    set_review(
        session,
        record_type=record_type,
        record_id=record_id,
        status=RecordReviewStatus.approved,
    )


def _water_geometry(session):
    g = make_geometry(session, natoms=3)
    attach_geometry_atoms(
        session,
        geometry=g,
        symbols=["O", "H", "H"],
        coords=[[0.0, 0.0, 0.0], [0.0, 0.0, 0.96], [0.93, 0.0, -0.24]],
    )
    return g


def _species_with_structure(
    session, *, smiles="O", approve=True, lot=None
):
    """A species entry with an opt geometry, an opt+sp energy, freq, hessian.

    The opt/sp/freq calculations all resolve to the same geometry, so they
    collapse into one per-geometry ML record carrying two LOT-labelled
    energies, a frequency block, and (opt-in) a Hessian.
    """
    sp = make_species(session, smiles=smiles, inchi_key=next_inchi_key("ML"))
    entry = make_species_entry(session, sp)
    geometry = _water_geometry(session)

    opt_lot = lot or make_lot(session, method="wb97xd", basis="def2tzvp")
    sp_lot = make_lot(session, method="ccsd(t)", basis="cc-pvtz")

    # opt calc → output geometry + optimization energy
    opt_calc = make_calculation(
        session,
        type=CalculationType.opt,
        species_entry_id=entry.id,
        lot_id=opt_lot.id,
    )
    attach_output_geometry(
        session,
        calculation=opt_calc,
        geometry=geometry,
        role=CalculationGeometryRole.final,
    )
    attach_opt_result(session, calculation=opt_calc, final_energy_hartree=-76.3)

    # sp calc on that geometry → single-point energy at a higher LOT
    sp_calc = make_calculation(
        session,
        type=CalculationType.sp,
        species_entry_id=entry.id,
        lot_id=sp_lot.id,
    )
    attach_input_geometry(session, calculation=sp_calc, geometry=geometry)
    attach_sp_result(session, calculation=sp_calc, electronic_energy_hartree=-76.42)

    # freq calc on that geometry → frequencies + hessian
    freq_calc = make_calculation(
        session,
        type=CalculationType.freq,
        species_entry_id=entry.id,
        lot_id=opt_lot.id,
    )
    attach_input_geometry(session, calculation=freq_calc, geometry=geometry)
    attach_freq_result(
        session,
        calculation=freq_calc,
        frequencies_cm1=[1600.0, 3700.0, 3800.0],
        zpe_hartree=0.021,
    )
    attach_hessian(session, calculation=freq_calc, geometry=geometry, natoms=3)

    if approve:
        _approve(session, SubmissionRecordType.species_entry, entry.id)
    return entry, geometry, sp, opt_lot, sp_lot


def _parse(lines):
    return [json.loads(ln) for ln in lines]


# ---------------------------------------------------------------------------
# Species export — structure + streaming
# ---------------------------------------------------------------------------


def test_species_export_shape(db_session):
    entry, geometry, species, opt_lot, sp_lot = _species_with_structure(db_session)

    lines = list(
        iter_ml_species_ndjson(db_session, species_refs=[entry.public_ref])
    )
    assert all(ln.endswith("\n") for ln in lines)
    parsed = _parse(lines)

    assert parsed[0]["record_type"] == "manifest"
    assert parsed[0]["schema"] == "tckdb.ml.v0"
    assert parsed[0]["dataset"] == "species"
    assert parsed[-1]["record_type"] == "export_summary"
    assert parsed[-1]["counts"]["records"] == 1

    (record,) = [p for p in parsed if p["record_type"] == "ml_species"]
    # Identity (public refs only — never integer PKs).
    assert record["species_entry_ref"] == entry.public_ref
    assert record["species_ref"] == species.public_ref
    assert record["smiles"] == "O"
    assert record["charge"] == 0 and record["multiplicity"] == 1
    assert record["review_status"] == "approved"

    # Geometry.
    geo = record["geometry"]
    assert geo["geometry_ref"] == geometry.public_ref
    assert geo["natoms"] == 3
    assert geo["symbols"] == ["O", "H", "H"]
    assert geo["coords"][1] == [0.0, 0.0, 0.96]
    assert geo["coordinate_unit"] == "angstrom"

    # Two energies with explicit, machine-readable LOT labels.
    energies = record["energies"]
    by_type = {e["energy_type"]: e for e in energies}
    assert by_type["optimization"]["electronic_energy_hartree"] == -76.3
    assert by_type["single_point"]["electronic_energy_hartree"] == -76.42
    assert by_type["optimization"]["level_of_theory"]["label"] == "wb97xd/def2tzvp"
    assert by_type["single_point"]["level_of_theory"]["label"] == "ccsd(t)/cc-pvtz"
    assert by_type["single_point"]["level_of_theory"]["lot_hash"] == sp_lot.lot_hash

    # Frequencies (signed convention).
    assert record["frequencies"]["frequencies_cm1"] == [1600.0, 3700.0, 3800.0]
    assert record["frequencies"]["n_imag"] == 0
    assert record["frequencies"]["zpe_hartree"] == 0.021

    # Hessian is opt-in.
    assert record["hessian"] is None


def test_species_export_includes_hessian_when_requested(db_session):
    entry, *_ = _species_with_structure(db_session)
    lines = list(
        iter_ml_species_ndjson(
            db_session,
            species_refs=[entry.public_ref],
            filters=MLFilters(include_hessian=True),
        )
    )
    (record,) = [p for p in _parse(lines) if p["record_type"] == "ml_species"]
    hess = record["hessian"]
    assert hess is not None
    # 3N(3N+1)/2 for N=3 → 45 packed lower-triangle entries.
    assert len(hess["lower_triangle_hartree_bohr2"]) == 45
    assert hess["units"] == "hartree/bohr^2"


def test_species_export_thermo_summary(db_session):
    entry, *_ = _species_with_structure(db_session)
    thermo = make_thermo_scalar(db_session, species_entry=entry)
    thermo.h298_kj_mol = -241.8
    thermo.s298_j_mol_k = 188.8
    db_session.flush()
    _approve(db_session, SubmissionRecordType.thermo, thermo.id)

    lines = list(
        iter_ml_species_ndjson(db_session, species_refs=[entry.public_ref])
    )
    (record,) = [p for p in _parse(lines) if p["record_type"] == "ml_species"]
    assert record["thermo"]["h298_kj_mol"] == -241.8
    assert record["thermo"]["s298_j_mol_k"] == 188.8
    assert record["thermo"]["review_status"] == "approved"


# ---------------------------------------------------------------------------
# Species export — filters + trust
# ---------------------------------------------------------------------------


def test_species_export_trust_gate_skips_unapproved(db_session):
    entry, *_ = _species_with_structure(db_session, approve=False)
    lines = list(
        iter_ml_species_ndjson(db_session, species_refs=[entry.public_ref])
    )
    parsed = _parse(lines)
    assert not [p for p in parsed if p["record_type"] == "ml_species"]
    assert parsed[-1]["counts"]["skipped_below_min_review_status"] == 1

    # Dropping the floor surfaces the record.
    lines = list(
        iter_ml_species_ndjson(
            db_session,
            species_refs=[entry.public_ref],
            filters=MLFilters(min_review_status=None),
        )
    )
    assert [p for p in _parse(lines) if p["record_type"] == "ml_species"]


def test_species_export_lot_filter(db_session):
    entry, geometry, species, opt_lot, sp_lot = _species_with_structure(db_session)
    lines = list(
        iter_ml_species_ndjson(
            db_session,
            species_refs=[entry.public_ref],
            filters=MLFilters(lot_ref=sp_lot.public_ref),
        )
    )
    (record,) = [p for p in _parse(lines) if p["record_type"] == "ml_species"]
    # Only the ccsd(t) single-point energy survives the LOT filter.
    assert len(record["energies"]) == 1
    assert record["energies"][0]["level_of_theory"]["label"] == "ccsd(t)/cc-pvtz"


def test_species_export_element_filter_excludes(db_session):
    entry, *_ = _species_with_structure(db_session, smiles="O")
    # Water needs O and H; an allow-list of only C drops the geometry row.
    lines = list(
        iter_ml_species_ndjson(
            db_session,
            species_refs=[entry.public_ref],
            filters=MLFilters(elements=frozenset({"C", "H"})),
        )
    )
    assert not [p for p in _parse(lines) if p["record_type"] == "ml_species"]

    lines = list(
        iter_ml_species_ndjson(
            db_session,
            species_refs=[entry.public_ref],
            filters=MLFilters(elements=frozenset({"O", "H"})),
        )
    )
    assert [p for p in _parse(lines) if p["record_type"] == "ml_species"]


def test_species_export_conformer_refs(db_session):
    sp = make_species(db_session, smiles="C", inchi_key=next_inchi_key("CF"))
    entry = make_species_entry(db_session, sp)
    group = make_conformer_group(db_session, entry, label="g1")
    obs = make_conformer_observation(db_session, conformer_group=group)
    lot = make_lot(db_session)
    geometry = make_geometry(db_session, natoms=1)
    attach_geometry_atoms(
        db_session, geometry=geometry, symbols=["C"], coords=[[0.0, 0.0, 0.0]]
    )
    calc = make_calculation_with_conformer(
        db_session,
        species_entry=entry,
        conformer_observation=obs,
        type=CalculationType.sp,
        lot_id=lot.id,
    )
    attach_input_geometry(db_session, calculation=calc, geometry=geometry)
    attach_sp_result(db_session, calculation=calc, electronic_energy_hartree=-37.8)
    _approve(db_session, SubmissionRecordType.species_entry, entry.id)

    lines = list(
        iter_ml_species_ndjson(db_session, species_refs=[entry.public_ref])
    )
    (record,) = [p for p in _parse(lines) if p["record_type"] == "ml_species"]
    assert record["conformer_group_refs"] == [group.public_ref]
    assert record["conformer_observation_refs"] == [obs.public_ref]


def test_species_export_empty_seed_raises(db_session):
    with pytest.raises(ValueError, match="ml_export_seed_empty"):
        iter_ml_species_ndjson(db_session)


def test_species_export_unresolved_ref_raises(db_session):
    with pytest.raises(ValueError, match="ml_export_seed_unresolved"):
        iter_ml_species_ndjson(db_session, species_refs=["nope"])


def test_species_export_unknown_lot_raises(db_session):
    entry, *_ = _species_with_structure(db_session)
    with pytest.raises(ValueError, match="ml_export_lot_unresolved"):
        iter_ml_species_ndjson(
            db_session,
            species_refs=[entry.public_ref],
            filters=MLFilters(lot_ref="lot_missing"),
        )


# ---------------------------------------------------------------------------
# Reaction export (RDB7-compatible)
# ---------------------------------------------------------------------------


def _reactant_with_energy(db_session, smiles, energy_hartree, lot, *, h298=None):
    sp = make_species(db_session, smiles=smiles, inchi_key=next_inchi_key("RX"))
    entry = make_species_entry(db_session, sp)
    geometry = make_geometry(db_session, natoms=1)
    attach_geometry_atoms(
        db_session, geometry=geometry, symbols=["C"], coords=[[0.0, 0.0, 0.0]]
    )
    calc = make_calculation(
        db_session,
        type=CalculationType.sp,
        species_entry_id=entry.id,
        lot_id=lot.id,
    )
    attach_input_geometry(db_session, calculation=calc, geometry=geometry)
    attach_sp_result(
        db_session, calculation=calc, electronic_energy_hartree=energy_hartree
    )
    _approve(db_session, SubmissionRecordType.species_entry, entry.id)
    if h298 is not None:
        thermo = make_thermo_scalar(db_session, species_entry=entry)
        thermo.h298_kj_mol = h298
        db_session.flush()
        _approve(db_session, SubmissionRecordType.thermo, thermo.id)
    return entry


def test_reaction_export_rdb7_shape_and_barrier(db_session):
    lot = make_lot(db_session, method="wb97xd", basis="def2tzvp")
    e_a = _reactant_with_energy(db_session, "C", -40.0, lot, h298=-74.6)
    e_b = _reactant_with_energy(db_session, "O", -75.0, lot, h298=-241.8)
    e_c = _reactant_with_energy(db_session, "CO", -115.05, lot, h298=-201.0)

    chem = make_chem_reaction(
        db_session,
        reactants=[e_a.species, e_b.species],
        products=[e_c.species],
    )
    rxn_entry = make_reaction_entry(
        db_session,
        reaction=chem,
        reactant_entries=[e_a, e_b],
        product_entries=[e_c],
    )
    _approve(db_session, SubmissionRecordType.reaction_entry, rxn_entry.id)

    # TS with a geometry + energy at the same LOT as the reactants.
    ts = make_transition_state(db_session, reaction_entry=rxn_entry, label="ts1")
    tse = make_transition_state_entry(db_session, transition_state=ts)
    ts_geo = make_geometry(db_session, natoms=2)
    attach_geometry_atoms(
        db_session,
        geometry=ts_geo,
        symbols=["C", "O"],
        coords=[[0.0, 0.0, 0.0], [0.0, 0.0, 1.4]],
    )
    ts_calc = make_calculation(
        db_session,
        type=CalculationType.sp,
        transition_state_entry_id=tse.id,
        lot_id=lot.id,
    )
    attach_input_geometry(db_session, calculation=ts_calc, geometry=ts_geo)
    # E_TS chosen so the electronic forward barrier is +0.05 hartree.
    attach_sp_result(
        db_session, calculation=ts_calc, electronic_energy_hartree=-114.95
    )

    kin = make_kinetics(db_session, reaction_entry=rxn_entry)
    _approve(db_session, SubmissionRecordType.kinetics, kin.id)

    lines = list(
        iter_ml_reactions_ndjson(
            db_session, reaction_refs=[rxn_entry.public_ref]
        )
    )
    parsed = _parse(lines)
    assert parsed[0]["dataset"] == "reactions"
    (record,) = [p for p in parsed if p["record_type"] == "ml_reaction"]

    # RDB7 rsmi / psmi.
    assert record["reactants_smiles"] == ["C", "O"]
    assert record["products_smiles"] == ["CO"]

    # RDB7 ea analog: E_TS - (E_a + E_b) = -114.95 - (-115.0) = 0.05 hartree.
    barrier = record["barrier"]
    assert barrier is not None
    assert barrier["electronic_forward_kj_mol"] == pytest.approx(
        0.05 * HARTREE_TO_KJ_MOL
    )
    assert barrier["level_of_theory"]["label"] == "wb97xd/def2tzvp"

    # RDB7 dh analog: H298(CO) - (H298(C) + H298(O)) = -201 - (-316.4) = 115.4.
    assert record["delta_h298_kj_mol"] == pytest.approx(-201.0 - (-74.6 - 241.8))

    # TS geometry + energy.
    ts_block = record["transition_state"]
    assert ts_block["transition_state_entry_ref"] == tse.public_ref
    assert ts_block["geometry"]["symbols"] == ["C", "O"]
    assert ts_block["energy"]["electronic_energy_hartree"] == -114.95

    # Kinetics with LOT-agnostic Arrhenius params present.
    assert record["kinetics"] and record["kinetics"][0]["kinetics_ref"] == kin.public_ref


def test_reaction_export_barrier_null_without_shared_lot(db_session):
    lot_a = make_lot(db_session, method="wb97xd", basis="def2tzvp")
    lot_b = make_lot(db_session, method="b3lyp", basis="6-31g")
    e_a = _reactant_with_energy(db_session, "C", -40.0, lot_a)
    e_c = _reactant_with_energy(db_session, "CO", -115.05, lot_a)
    chem = make_chem_reaction(
        db_session, reactants=[e_a.species], products=[e_c.species]
    )
    rxn_entry = make_reaction_entry(
        db_session, reaction=chem, reactant_entries=[e_a], product_entries=[e_c]
    )
    _approve(db_session, SubmissionRecordType.reaction_entry, rxn_entry.id)
    ts = make_transition_state(db_session, reaction_entry=rxn_entry)
    tse = make_transition_state_entry(db_session, transition_state=ts)
    ts_calc = make_calculation(
        db_session,
        type=CalculationType.sp,
        transition_state_entry_id=tse.id,
        lot_id=lot_b.id,  # TS at a different LOT than the reactant
    )
    ts_geo = make_geometry(db_session, natoms=1)
    attach_geometry_atoms(
        db_session, geometry=ts_geo, symbols=["C"], coords=[[0.0, 0.0, 0.0]]
    )
    attach_input_geometry(db_session, calculation=ts_calc, geometry=ts_geo)
    attach_sp_result(
        db_session, calculation=ts_calc, electronic_energy_hartree=-114.9
    )

    lines = list(
        iter_ml_reactions_ndjson(db_session, reaction_refs=[rxn_entry.public_ref])
    )
    (record,) = [p for p in _parse(lines) if p["record_type"] == "ml_reaction"]
    # No reactant energy exists at the TS's LOT → barrier is null, not wrong.
    assert record["barrier"] is None
    # But the TS block is still emitted.
    assert record["transition_state"]["energy"]["electronic_energy_hartree"] == -114.9


def test_reaction_export_barrier_lot_fallback(db_session):
    """The barrier LOT walk skips a lower-energy TS LOT without reactant
    coverage and settles on the first LOT whose reactant set is complete,
    keeping the emitted TS energy block at that same LOT."""
    lot_cov = make_lot(db_session, method="wb97xd", basis="def2tzvp")
    lot_nocov = make_lot(db_session, method="b3lyp", basis="6-31g")
    e_a = _reactant_with_energy(db_session, "C", -115.0, lot_cov)
    e_c = _reactant_with_energy(db_session, "CO", -115.3, lot_cov)
    chem = make_chem_reaction(
        db_session, reactants=[e_a.species], products=[e_c.species]
    )
    rxn_entry = make_reaction_entry(
        db_session, reaction=chem, reactant_entries=[e_a], product_entries=[e_c]
    )
    _approve(db_session, SubmissionRecordType.reaction_entry, rxn_entry.id)

    ts = make_transition_state(db_session, reaction_entry=rxn_entry)
    tse = make_transition_state_entry(db_session, transition_state=ts)
    ts_geo = make_geometry(db_session, natoms=1)
    attach_geometry_atoms(
        db_session, geometry=ts_geo, symbols=["C"], coords=[[0.0, 0.0, 0.0]]
    )
    # Lower TS energy at a LOT with NO reactant coverage...
    calc_nocov = make_calculation(
        db_session,
        type=CalculationType.sp,
        transition_state_entry_id=tse.id,
        lot_id=lot_nocov.id,
    )
    attach_input_geometry(db_session, calculation=calc_nocov, geometry=ts_geo)
    attach_sp_result(
        db_session, calculation=calc_nocov, electronic_energy_hartree=-115.2
    )
    # ...and a higher TS energy at the LOT the reactants are covered at.
    calc_cov = make_calculation(
        db_session,
        type=CalculationType.sp,
        transition_state_entry_id=tse.id,
        lot_id=lot_cov.id,
    )
    attach_input_geometry(db_session, calculation=calc_cov, geometry=ts_geo)
    attach_sp_result(
        db_session, calculation=calc_cov, electronic_energy_hartree=-114.95
    )

    lines = list(
        iter_ml_reactions_ndjson(db_session, reaction_refs=[rxn_entry.public_ref])
    )
    (record,) = [p for p in _parse(lines) if p["record_type"] == "ml_reaction"]

    # Barrier is computed at the covered LOT, not nulled by the lower-energy
    # uncovered one: E_TS - E_reactant = -114.95 - (-115.0) = 0.05 hartree.
    barrier = record["barrier"]
    assert barrier is not None
    assert barrier["electronic_forward_kj_mol"] == pytest.approx(
        0.05 * HARTREE_TO_KJ_MOL
    )
    assert barrier["level_of_theory"]["label"] == "wb97xd/def2tzvp"
    # The TS energy block is internally consistent with the chosen LOT.
    ts_energy = record["transition_state"]["energy"]
    assert ts_energy["electronic_energy_hartree"] == -114.95
    assert ts_energy["level_of_theory"]["label"] == "wb97xd/def2tzvp"


def test_reaction_export_duplicate_reactant_counted_twice(db_session):
    """A + A -> B: the same species_entry appears as both reactants
    (participant_index 1 and 2); its energy and H298 count twice in the
    barrier and delta_h298."""
    lot = make_lot(db_session, method="wb97xd", basis="def2tzvp")
    e_a = _reactant_with_energy(db_session, "C", -40.0, lot, h298=-74.6)
    e_p = _reactant_with_energy(db_session, "CC", -80.1, lot, h298=-140.0)
    chem = make_chem_reaction(
        db_session,
        reactants=[e_a.species, e_a.species],
        products=[e_p.species],
    )
    rxn_entry = make_reaction_entry(
        db_session,
        reaction=chem,
        reactant_entries=[e_a, e_a],
        product_entries=[e_p],
    )
    _approve(db_session, SubmissionRecordType.reaction_entry, rxn_entry.id)

    ts = make_transition_state(db_session, reaction_entry=rxn_entry)
    tse = make_transition_state_entry(db_session, transition_state=ts)
    ts_geo = make_geometry(db_session, natoms=2)
    attach_geometry_atoms(
        db_session,
        geometry=ts_geo,
        symbols=["C", "C"],
        coords=[[0.0, 0.0, 0.0], [0.0, 0.0, 1.5]],
    )
    ts_calc = make_calculation(
        db_session,
        type=CalculationType.sp,
        transition_state_entry_id=tse.id,
        lot_id=lot.id,
    )
    attach_input_geometry(db_session, calculation=ts_calc, geometry=ts_geo)
    attach_sp_result(
        db_session, calculation=ts_calc, electronic_energy_hartree=-80.02
    )

    lines = list(
        iter_ml_reactions_ndjson(db_session, reaction_refs=[rxn_entry.public_ref])
    )
    (record,) = [p for p in _parse(lines) if p["record_type"] == "ml_reaction"]

    # Both reactant slots are emitted.
    assert record["reactants_smiles"] == ["C", "C"]
    assert record["reactant_refs"] == [e_a.public_ref, e_a.public_ref]

    # Barrier subtracts the duplicated reactant's energy twice:
    # E_TS - 2*E_A = -80.02 - 2*(-40.0) = -0.02 hartree.
    assert record["barrier"]["electronic_forward_kj_mol"] == pytest.approx(
        -0.02 * HARTREE_TO_KJ_MOL
    )

    # delta_h298 counts the duplicated reactant twice:
    # H298(B) - 2*H298(A) = -140.0 - 2*(-74.6) = 9.2 kJ/mol.
    assert record["delta_h298_kj_mol"] == pytest.approx(9.2)


def test_reaction_export_trust_gate(db_session):
    lot = make_lot(db_session)
    e_a = _reactant_with_energy(db_session, "C", -40.0, lot)
    e_c = _reactant_with_energy(db_session, "CO", -115.0, lot)
    chem = make_chem_reaction(
        db_session, reactants=[e_a.species], products=[e_c.species]
    )
    rxn_entry = make_reaction_entry(
        db_session, reaction=chem, reactant_entries=[e_a], product_entries=[e_c]
    )
    # reaction_entry left unreviewed → below the approved floor.
    lines = list(
        iter_ml_reactions_ndjson(db_session, reaction_refs=[rxn_entry.public_ref])
    )
    parsed = _parse(lines)
    assert not [p for p in parsed if p["record_type"] == "ml_reaction"]
    assert parsed[-1]["counts"]["skipped_below_min_review_status"] == 1
