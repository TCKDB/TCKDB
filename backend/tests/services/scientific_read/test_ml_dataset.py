"""Service-layer tests for the ML-dataset export (the "living RDB7" surface).

Exercises the species/conformer-centric and reaction-centric streaming
builders in ``app.services.scientific_read.ml_dataset`` directly against a
``Session`` (HTTP-free), mirroring ``test_export.py``.
"""

from __future__ import annotations

import json
from uuid import uuid4

import pytest
from sqlalchemy import text

from app.db.models.common import (
    CalculationGeometryRole,
    CalculationType,
    KineticsDegeneracyConvention,
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
    assert parsed[0]["format"] == {"name": "tckdb.ml", "version": 2}
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

    # Frequencies (signed convention), listed per calculation and level.
    (frequencies,) = record["frequency_results"]
    assert frequencies["frequencies_cm1"] == [1600.0, 3700.0, 3800.0]
    assert frequencies["n_imag"] == 0
    assert frequencies["zpe_hartree"] == 0.021
    assert frequencies["calculation_ref"] and frequencies["level_of_theory"]["label"]
    assert "frequencies" not in record and "hessian" not in record

    # Hessian is opt-in.
    assert record["hessians"] == []


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
    (hess,) = record["hessians"]
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
    (block,) = record["thermos"]
    assert block["h298_kj_mol"] == -241.8
    assert block["s298_j_mol_k"] == 188.8
    assert block["review_status"] == "approved"
    assert "thermo" not in record


def test_species_export_lists_every_thermo_record_and_picks_none(db_session):
    entry, *_ = _species_with_structure(db_session)
    first = make_thermo_scalar(db_session, species_entry=entry)
    second = make_thermo_scalar(db_session, species_entry=entry)
    _approve(db_session, SubmissionRecordType.thermo, first.id)

    lines = list(iter_ml_species_ndjson(db_session, species_refs=[entry.public_ref], filters=MLFilters(min_review_status=None)))
    (record,) = [p for p in _parse(lines) if p["record_type"] == "ml_species"]
    assert [t["thermo_ref"] for t in record["thermos"]] == [first.public_ref, second.public_ref]


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


def test_species_export_lot_filter_follows_a_merged_ref(db_session):
    """A merged level of theory's ref names the row it was merged into (#591).

    A dataset request that cites the ref of a duplicate the merge script
    retired must select the kept row's energies, not silently match nothing.
    """
    from app.db.models.level_of_theory import LevelOfTheory, LevelOfTheoryMerge

    entry, geometry, species, opt_lot, sp_lot = _species_with_structure(db_session)
    retired = LevelOfTheory(
        method="CCSD(T)", basis="cc-pVTZ", lot_hash="e" * 64
    )
    db_session.add(retired)
    db_session.flush()
    db_session.add(
        LevelOfTheoryMerge(merged_lot_id=retired.id, into_lot_id=sp_lot.id)
    )
    db_session.flush()
    assert retired.public_ref != sp_lot.public_ref

    lines = list(
        iter_ml_species_ndjson(
            db_session,
            species_refs=[entry.public_ref],
            filters=MLFilters(lot_ref=retired.public_ref),
        )
    )
    (record,) = [p for p in _parse(lines) if p["record_type"] == "ml_species"]
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


def _reactant_with_legacy_undeclared_h298(db_session, smiles, energy_hartree, lot, *, h298):
    """Like ``_reactant_with_energy``, but the thermo row predates the
    enthalpy-reference-declaration rule: ``h298_kj_mol`` is set with no
    ``enthalpy_reference_kind`` declared. Only reachable by disabling the
    guard trigger for the insert -- mirrors ``_legacy_scalar_thermo`` in
    ``tests/services/test_contribution_bundle_export_enthalpy_reference.py``.
    """
    entry = _reactant_with_energy(db_session, smiles, energy_hartree, lot, h298=None)
    db_session.execute(
        text("ALTER TABLE thermo DISABLE TRIGGER trg_guard_thermo_enthalpy_reference")
    )
    try:
        thermo_id = db_session.scalar(
            text(
                "INSERT INTO thermo "
                "(public_ref, species_entry_id, scientific_origin, h298_kj_mol) "
                "VALUES (:ref, :entry, 'computed', :h298) RETURNING id"
            ),
            {"entry": entry.id, "ref": "th_" + uuid4().hex[:26], "h298": h298},
        )
    finally:
        db_session.execute(
            text("ALTER TABLE thermo ENABLE TRIGGER trg_guard_thermo_enthalpy_reference")
        )
    db_session.flush()
    _approve(db_session, SubmissionRecordType.thermo, thermo_id)
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
    (barrier,) = record["barriers"]
    assert barrier["transition_state_entry_ref"] == tse.public_ref
    assert barrier["electronic_forward_kj_mol"] == pytest.approx(
        0.05 * HARTREE_TO_KJ_MOL
    )
    assert barrier["level_of_theory"]["label"] == "wb97xd/def2tzvp"

    # RDB7 dh analog: H298(CO) - (H298(C) + H298(O)) = -201 - (-316.4) = 115.4.
    assert record["delta_h298_kj_mol"] == pytest.approx(-201.0 - (-74.6 - 241.8))

    # TS geometry + energy.
    (ts_block,) = record["transition_states"]
    assert ts_block["transition_state_entry_ref"] == tse.public_ref
    (ts_energy,) = ts_block["energies"]
    assert ts_energy["geometry"]["symbols"] == ["C", "O"]
    assert ts_energy["electronic_energy_hartree"] == -114.95
    assert record["barriers_unavailable"] == []
    assert "barrier" not in record and "transition_state" not in record

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
    # No reactant energy exists at the TS's LOT: no barrier, and the record says why instead of a bare null.
    assert record["barriers"] == []
    assert record["barriers_unavailable"] == [
        {"transition_state_entry_ref": tse.public_ref, "reason": "no_level_with_all_reactant_energies"}
    ]
    # But the TS block is still emitted.
    (ts_block,) = record["transition_states"]
    assert ts_block["energies"][0]["electronic_energy_hartree"] == -114.9
    (summary,) = [p for p in _parse(lines) if p["record_type"] == "export_summary"]
    assert summary["counts"]["transition_state_entries_without_barrier"] == 1


def test_ml_reaction_export_preserves_degeneracy_convention(db_session):
    reactant = make_species(db_session, smiles="C", inchi_key=next_inchi_key("MD"))
    product = make_species(db_session, smiles="CO", inchi_key=next_inchi_key("MD"))
    reactant_entry = make_species_entry(db_session, reactant)
    product_entry = make_species_entry(db_session, product)
    reaction = make_chem_reaction(
        db_session, reactants=[reactant], products=[product]
    )
    reaction_entry = make_reaction_entry(
        db_session,
        reaction=reaction,
        reactant_entries=[reactant_entry],
        product_entries=[product_entry],
    )
    _approve(db_session, SubmissionRecordType.reaction_entry, reaction_entry.id)
    kinetics = make_kinetics(
        db_session, reaction_entry=reaction_entry, degeneracy=2.0
    )
    kinetics.degeneracy_convention = KineticsDegeneracyConvention.not_applied
    _approve(db_session, SubmissionRecordType.kinetics, kinetics.id)
    db_session.flush()

    lines = iter_ml_reactions_ndjson(
        db_session, reaction_refs=[reaction_entry.public_ref]
    )
    (record,) = [p for p in _parse(lines) if p["record_type"] == "ml_reaction"]

    assert record["kinetics"][0]["degeneracy"] == 2.0
    assert record["kinetics"][0]["degeneracy_convention"] == "not_applied"


def test_reaction_export_barrier_lot_fallback(db_session):
    """A level with no reactant coverage gets no barrier; the covered level does, and both TS energies are listed.

    Nothing is chosen between the levels: the lower TS energy at the uncovered level neither wins nor suppresses the
    covered level's barrier."""
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

    # One barrier, at the covered level: E_TS - E_reactant = -114.95 - (-115.0) = 0.05 hartree.
    (barrier,) = record["barriers"]
    assert barrier["electronic_forward_kj_mol"] == pytest.approx(0.05 * HARTREE_TO_KJ_MOL)
    assert barrier["level_of_theory"]["label"] == "wb97xd/def2tzvp"
    # Both TS energies are listed, each at its own level, ordered by level label and never by energy.
    (ts_block,) = record["transition_states"]
    by_label = {e["level_of_theory"]["label"]: e for e in ts_block["energies"]}
    assert by_label["wb97xd/def2tzvp"]["electronic_energy_hartree"] == -114.95
    assert by_label["b3lyp/6-31g"]["electronic_energy_hartree"] == -115.2
    assert [e["level_of_theory"]["label"] for e in ts_block["energies"]] == sorted(by_label)


def test_reaction_export_duplicate_reactant_counted_twice(db_session):
    """A + A -> B: the same species_entry appears as both reactants
    (participant_index 1 and 2); its energy and H298 count twice in the
    barrier and delta_h298."""
    lot = make_lot(db_session, method="wb97xd", basis="def2tzvp")
    e_a = _reactant_with_energy(db_session, "C", -40.0, lot, h298=-74.6)
    e_p = _reactant_with_energy(
        db_session, "[13CH3][13CH3]", -80.1, lot, h298=-140.0
    )
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
    assert record["barriers"][0]["electronic_forward_kj_mol"] == pytest.approx(
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


# ---------------------------------------------------------------------------
# Reaction export -- delta_h298 declines across an unrecorded/mixed
# enthalpy reference (issue #519, second half)
# ---------------------------------------------------------------------------


def test_reaction_export_delta_h298_null_when_reference_unrecorded(db_session):
    """One participant carries a legacy thermo row (h298 set, no declared
    ``enthalpy_reference_kind``) predating the declaration rule. The export
    must decline to sum across it rather than silently returning a number
    computed on an unknown basis -- the barrier (an independent, purely
    electronic quantity) is unaffected."""
    lot = make_lot(db_session, method="wb97xd", basis="def2tzvp")
    e_a = _reactant_with_energy(db_session, "C", -40.0, lot, h298=-74.6)
    # Legacy: h298 present, reference never recorded.
    e_b = _reactant_with_legacy_undeclared_h298(
        db_session, "O", -75.0, lot, h298=-241.8
    )
    e_c = _reactant_with_energy(db_session, "CO", -115.05, lot, h298=-201.0)

    chem = make_chem_reaction(
        db_session, reactants=[e_a.species, e_b.species], products=[e_c.species]
    )
    rxn_entry = make_reaction_entry(
        db_session,
        reaction=chem,
        reactant_entries=[e_a, e_b],
        product_entries=[e_c],
    )
    _approve(db_session, SubmissionRecordType.reaction_entry, rxn_entry.id)

    ts = make_transition_state(db_session, reaction_entry=rxn_entry)
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
    attach_sp_result(
        db_session, calculation=ts_calc, electronic_energy_hartree=-114.95
    )

    lines = list(
        iter_ml_reactions_ndjson(db_session, reaction_refs=[rxn_entry.public_ref])
    )
    parsed = _parse(lines)
    (record,) = [p for p in parsed if p["record_type"] == "ml_reaction"]

    # The naive, buggy sum would be -201.0 - (-74.6 - 241.8) == 115.4; the
    # export must decline instead.
    assert record["delta_h298_kj_mol"] is None

    # The electronic barrier does not depend on thermo at all and is
    # unaffected by the undeclared reference.
    assert record["barriers"]
    assert record["barriers"][0]["electronic_forward_kj_mol"] == pytest.approx(
        0.05 * HARTREE_TO_KJ_MOL
    )

    summary = parsed[-1]
    assert summary["record_type"] == "export_summary"
    assert summary["counts"]["delta_h298_declined_unrecorded_reference"] == 1
    assert summary["counts"]["delta_h298_declined_mixed_reference"] == 0


def test_reaction_export_delta_h298_computed_when_every_term_declares(db_session):
    """Sanity counterpart: when every contributing thermo term declares the
    same reference, delta_h298 is computed exactly as before and the new
    decline counters stay at zero."""
    lot = make_lot(db_session, method="wb97xd", basis="def2tzvp")
    e_a = _reactant_with_energy(db_session, "C", -40.0, lot, h298=-74.6)
    e_b = _reactant_with_energy(db_session, "O", -75.0, lot, h298=-241.8)
    e_c = _reactant_with_energy(db_session, "CO", -115.05, lot, h298=-201.0)

    chem = make_chem_reaction(
        db_session, reactants=[e_a.species, e_b.species], products=[e_c.species]
    )
    rxn_entry = make_reaction_entry(
        db_session,
        reaction=chem,
        reactant_entries=[e_a, e_b],
        product_entries=[e_c],
    )
    _approve(db_session, SubmissionRecordType.reaction_entry, rxn_entry.id)

    lines = list(
        iter_ml_reactions_ndjson(db_session, reaction_refs=[rxn_entry.public_ref])
    )
    parsed = _parse(lines)
    (record,) = [p for p in parsed if p["record_type"] == "ml_reaction"]

    assert record["delta_h298_kj_mol"] == pytest.approx(-201.0 - (-74.6 - 241.8))

    summary = parsed[-1]
    assert summary["counts"]["delta_h298_declined_unrecorded_reference"] == 0
    assert summary["counts"]["delta_h298_declined_mixed_reference"] == 0


# ---------------------------------------------------------------------------
# The ML export picks nothing: every TS entry, every level, every frequency result
# ---------------------------------------------------------------------------


def _reaction_with_reactant_levels(db_session, lots_and_energies):
    """A -> B with the reactant (energy per level given) and a product; returns (reaction entry, reactant entry)."""
    e_a = _reactant_with_energy(db_session, "C", lots_and_energies[0][1], lots_and_energies[0][0])
    for lot, energy in lots_and_energies[1:]:
        calc = make_calculation(db_session, type=CalculationType.sp, species_entry_id=e_a.id, lot_id=lot.id)
        attach_sp_result(db_session, calculation=calc, electronic_energy_hartree=energy)
    e_c = _reactant_with_energy(db_session, "CO", -115.3, lots_and_energies[0][0])
    chem = make_chem_reaction(db_session, reactants=[e_a.species], products=[e_c.species])
    rxn_entry = make_reaction_entry(db_session, reaction=chem, reactant_entries=[e_a], product_entries=[e_c])
    _approve(db_session, SubmissionRecordType.reaction_entry, rxn_entry.id)
    return rxn_entry, e_a


def _ts_with_energy(db_session, rxn_entry, energies_by_lot, label):
    ts = make_transition_state(db_session, reaction_entry=rxn_entry, label=label)
    tse = make_transition_state_entry(db_session, transition_state=ts)
    for lot, energy in energies_by_lot:
        calc = make_calculation(
            db_session, type=CalculationType.sp, transition_state_entry_id=tse.id, lot_id=lot.id
        )
        attach_sp_result(db_session, calculation=calc, electronic_energy_hartree=energy)
    return tse


def test_every_transition_state_entry_is_listed_with_its_own_barrier(db_session):
    """Kills: ``_select_ts_entry`` (the lowest-id entry stood for the reaction; the others vanished)."""
    lot = make_lot(db_session, method="wb97xd", basis="def2tzvp")
    rxn_entry, _ = _reaction_with_reactant_levels(db_session, [(lot, -115.0)])
    first = _ts_with_energy(db_session, rxn_entry, [(lot, -114.95)], "ts1")
    second = _ts_with_energy(db_session, rxn_entry, [(lot, -114.80)], "ts2")

    lines = list(iter_ml_reactions_ndjson(db_session, reaction_refs=[rxn_entry.public_ref]))
    (record,) = [p for p in _parse(lines) if p["record_type"] == "ml_reaction"]

    assert [t["transition_state_entry_ref"] for t in record["transition_states"]] == [
        first.public_ref,
        second.public_ref,
    ]
    by_entry = {b["transition_state_entry_ref"]: b for b in record["barriers"]}
    assert by_entry[first.public_ref]["electronic_forward_kj_mol"] == pytest.approx(0.05 * HARTREE_TO_KJ_MOL)
    assert by_entry[second.public_ref]["electronic_forward_kj_mol"] == pytest.approx(0.20 * HARTREE_TO_KJ_MOL)


def test_absolute_energies_at_different_levels_are_never_compared(db_session):
    """Kills: ordering TS levels by absolute energy (the lowest TS energy chose the level of the barrier).

    The TS is lower at level B than at level A, which on absolute energies means nothing. Both levels have reactant
    coverage, so both barriers are emitted, each inside its own level.
    """
    lot_a = make_lot(db_session, method="wb97xd", basis="def2tzvp")
    lot_b = make_lot(db_session, method="b3lyp", basis="6-31g")
    rxn_entry, _ = _reaction_with_reactant_levels(db_session, [(lot_a, -115.0), (lot_b, -116.0)])
    tse = _ts_with_energy(db_session, rxn_entry, [(lot_a, -114.95), (lot_b, -115.80)], "ts")

    lines = list(iter_ml_reactions_ndjson(db_session, reaction_refs=[rxn_entry.public_ref]))
    (record,) = [p for p in _parse(lines) if p["record_type"] == "ml_reaction"]

    barriers = {b["level_of_theory"]["label"]: b for b in record["barriers"]}
    assert set(barriers) == {"wb97xd/def2tzvp", "b3lyp/6-31g"}
    assert barriers["wb97xd/def2tzvp"]["electronic_forward_kj_mol"] == pytest.approx(0.05 * HARTREE_TO_KJ_MOL)
    assert barriers["b3lyp/6-31g"]["electronic_forward_kj_mol"] == pytest.approx(0.20 * HARTREE_TO_KJ_MOL)
    assert all(b["transition_state_entry_ref"] == tse.public_ref for b in record["barriers"])
    # Listed by level label, not by energy (b3lyp's TS energy is the lowest, and it is listed first only because
    # "b3lyp/6-31g" sorts before "wb97xd/def2tzvp").
    assert [b["level_of_theory"]["label"] for b in record["barriers"]] == sorted(barriers)


def test_a_transition_state_with_no_energy_says_so(db_session):
    lot = make_lot(db_session, method="wb97xd", basis="def2tzvp")
    rxn_entry, _ = _reaction_with_reactant_levels(db_session, [(lot, -115.0)])
    tse = _ts_with_energy(db_session, rxn_entry, [], "ts-no-energy")

    lines = list(iter_ml_reactions_ndjson(db_session, reaction_refs=[rxn_entry.public_ref]))
    (record,) = [p for p in _parse(lines) if p["record_type"] == "ml_reaction"]

    assert record["barriers"] == []
    assert record["barriers_unavailable"] == [{"transition_state_entry_ref": tse.public_ref, "reason": "no_energies"}]


def test_species_export_lists_every_frequency_result_and_hessian_by_level(db_session):
    """Kills: ``if freq_block is None`` (the first calculation found at a geometry stood for all of them)."""
    entry, geometry, _sp, opt_lot, _sp_lot = _species_with_structure(db_session)
    other_lot = make_lot(db_session, method="b3lyp", basis="6-31g")
    second = make_calculation(db_session, type=CalculationType.freq, species_entry_id=entry.id, lot_id=other_lot.id)
    attach_input_geometry(db_session, calculation=second, geometry=geometry)
    attach_freq_result(db_session, calculation=second, frequencies_cm1=[1500.0, 3600.0, 3700.0], zpe_hartree=0.020)
    attach_hessian(db_session, calculation=second, geometry=geometry, natoms=3)

    lines = list(
        iter_ml_species_ndjson(
            db_session, species_refs=[entry.public_ref], filters=MLFilters(include_hessian=True)
        )
    )
    (record,) = [p for p in _parse(lines) if p["record_type"] == "ml_species"]

    by_label = {f["level_of_theory"]["label"]: f for f in record["frequency_results"]}
    assert set(by_label) == {"wb97xd/def2tzvp", "b3lyp/6-31g"}
    assert by_label["b3lyp/6-31g"]["frequencies_cm1"] == [1500.0, 3600.0, 3700.0]
    assert by_label["wb97xd/def2tzvp"]["frequencies_cm1"] == [1600.0, 3700.0, 3800.0]
    assert {h["level_of_theory"]["label"] for h in record["hessians"]} == {"wb97xd/def2tzvp", "b3lyp/6-31g"}
    assert {h["calculation_ref"] for h in record["hessians"]} == {f["calculation_ref"] for f in record["frequency_results"]}
