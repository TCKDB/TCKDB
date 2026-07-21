"""Service-layer tests for the bulk-export selection/closure core and the
CHEMKIN serializer (docs/specs/bulk_export_design.md §4, §5, §8)."""

from __future__ import annotations

import json

import pytest

from app.db.models.common import (
    ArrheniusAUnits,
    KineticsModelKind,
    KineticsUncertaintyKind,
    PressureContext,
    RecordReviewStatus,
    SubmissionRecordType,
    ThermoModelKind,
    TunnelingModel,
)
from app.schemas.reads.scientific_common import CollapseMode, SelectionPolicy
from app.services.scientific_read.chemkin_serialize import (
    ChemkinOptions,
    serialize_chemkin,
)
from app.services.scientific_read.export import (
    SeedSelection,
    build_export_record_set,
    iter_export_ndjson,
)
from tests.services.scientific_read._factories import (
    attach_kinetics_arrhenius_entry,
    attach_kinetics_chebyshev,
    attach_kinetics_falloff,
    attach_kinetics_plog_entry,
    attach_kinetics_third_body_efficiency,
    attach_thermo_nasa,
    attach_thermo_nasa9,
    attach_thermo_points,
    attach_thermo_wilhoit,
    make_chem_reaction,
    make_kinetics,
    make_reaction_entry,
    make_species,
    make_species_entry,
    make_thermo_scalar,
    make_transport,
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


def _species_with_nasa(session, *, smiles=None):
    sp = make_species(session, smiles=smiles, inchi_key=next_inchi_key("EX"))
    entry = make_species_entry(session, sp)
    thermo = make_thermo_scalar(session, species_entry=entry)
    attach_thermo_nasa(session, thermo=thermo)
    _approve(session, SubmissionRecordType.thermo, thermo.id)
    return sp, entry, thermo


def _build_one_reaction(session):
    """A + B <=> C, all species with approved NASA thermo, approved kinetics."""
    _, e_a, _ = _species_with_nasa(session, smiles="C")
    _, e_b, _ = _species_with_nasa(session, smiles="O")
    _, e_c, _ = _species_with_nasa(session, smiles="CO")
    chem = make_chem_reaction(
        session,
        reactants=[e_a.species, e_b.species],
        products=[e_c.species],
    )
    entry = make_reaction_entry(
        session,
        reaction=chem,
        reactant_entries=[e_a, e_b],
        product_entries=[e_c],
    )
    kin = make_kinetics(session, reaction_entry=entry)
    _approve(session, SubmissionRecordType.kinetics, kin.id)
    return entry, (e_a, e_b, e_c), kin


# ---------------------------------------------------------------------------
# Closure
# ---------------------------------------------------------------------------


def test_closure_pulls_all_participant_species(db_session):
    entry, (e_a, e_b, e_c), kin = _build_one_reaction(db_session)

    rs = build_export_record_set(
        db_session,
        seed=SeedSelection(reaction_refs=[entry.public_ref]),
    )

    got = {sr.species_entry.id for sr in rs.species_records}
    assert got == {e_a.id, e_b.id, e_c.id}
    # Every closure species carries its selected thermo.
    assert all(sr.thermos for sr in rs.species_records)
    # The reaction is present with its selected kinetics and directional refs.
    assert len(rs.reaction_records) == 1
    rr = rs.reaction_records[0]
    assert rr.reactant_refs == [e_a.public_ref, e_b.public_ref]
    assert rr.product_refs == [e_c.public_ref]
    assert [k.kinetics.id for k in rr.kinetics] == [kin.id]
    assert rs.gaps == []


def test_seed_by_chem_reaction_ref_expands_to_entries(db_session):
    entry, species, _ = _build_one_reaction(db_session)
    chem_ref = entry.reaction.public_ref

    rs = build_export_record_set(
        db_session, seed=SeedSelection(reaction_refs=[chem_ref])
    )
    assert {r.reaction_entry.id for r in rs.reaction_records} == {entry.id}


def test_empty_seed_raises(db_session):
    with pytest.raises(ValueError, match="export_seed_empty"):
        build_export_record_set(db_session, seed=SeedSelection())


def test_unresolvable_ref_raises(db_session):
    with pytest.raises(ValueError, match="export_seed_unresolved"):
        build_export_record_set(
            db_session, seed=SeedSelection(reaction_refs=["nope_missing"])
        )


# ---------------------------------------------------------------------------
# Selection (policy + trust filter)
# ---------------------------------------------------------------------------


def test_selection_picks_policy_preferred_thermo(db_session):
    sp = make_species(db_session, smiles="C", inchi_key=next_inchi_key("SE"))
    entry = make_species_entry(db_session, sp)

    approved = make_thermo_scalar(db_session, species_entry=entry)
    attach_thermo_nasa(db_session, thermo=approved)
    _approve(db_session, SubmissionRecordType.thermo, approved.id)

    other = make_thermo_scalar(db_session, species_entry=entry)
    attach_thermo_nasa(db_session, thermo=other)
    # `other` stays not_reviewed.

    # min_review_status=None (default posture) keeps both candidates, and the
    # default policy ranks the approved one first.
    rs = build_export_record_set(
        db_session,
        seed=SeedSelection(species_refs=[entry.public_ref]),
        min_review_status=None,
        collapse=CollapseMode.first,
        selection_policy=SelectionPolicy.default,
    )
    (sr,) = rs.species_records
    assert len(sr.thermos) == 1
    assert sr.thermos[0].thermo.id == approved.id
    # The choice is visible in the emitted record (manifest/traceability).
    ndjson = sr.to_ndjson()
    assert ndjson["thermos"][0]["thermo_ref"] == approved.public_ref
    assert ndjson["thermos"][0]["review_status"] == "approved"


def test_trust_filter_drops_below_threshold_and_reports_gap(db_session):
    sp = make_species(db_session, smiles="C", inchi_key=next_inchi_key("TF"))
    entry = make_species_entry(db_session, sp)
    thermo = make_thermo_scalar(db_session, species_entry=entry)
    attach_thermo_nasa(db_session, thermo=thermo)
    # not reviewed → below default approved threshold.

    rs = build_export_record_set(
        db_session, seed=SeedSelection(species_refs=[entry.public_ref])
    )
    (sr,) = rs.species_records
    assert sr.thermos == []
    thermo_gaps = [g for g in rs.gaps if g.kind == "thermo"]
    assert len(thermo_gaps) == 1
    assert thermo_gaps[0].ref == entry.public_ref


def test_collapse_all_returns_all_candidates(db_session):
    sp = make_species(db_session, smiles="C", inchi_key=next_inchi_key("CA"))
    entry = make_species_entry(db_session, sp)
    for _ in range(2):
        t = make_thermo_scalar(db_session, species_entry=entry)
        attach_thermo_nasa(db_session, thermo=t)
        _approve(db_session, SubmissionRecordType.thermo, t.id)

    rs = build_export_record_set(
        db_session,
        seed=SeedSelection(species_refs=[entry.public_ref]),
        collapse=CollapseMode.all,
    )
    (sr,) = rs.species_records
    assert len(sr.thermos) == 2


# ---------------------------------------------------------------------------
# NDJSON structure / streaming
# ---------------------------------------------------------------------------


def test_ndjson_stream_structure(db_session):
    entry, species, _ = _build_one_reaction(db_session)

    lines = list(
        iter_export_ndjson(
            db_session, seed=SeedSelection(reaction_refs=[entry.public_ref])
        )
    )
    # Each yielded chunk is exactly one newline-terminated JSON object.
    parsed = [json.loads(line) for line in lines]
    assert all(line.endswith("\n") for line in lines)

    assert parsed[0]["record_type"] == "manifest"
    assert parsed[0]["schema"] == "tckdb.export.v0"
    assert parsed[0]["contract"]["kind"] == "selected_scientific_projection"
    assert parsed[0]["contract"]["lossless"] is False
    assert parsed[0]["contract"]["reingestible"] is False
    assert "raw_artifact_bytes" in parsed[0]["contract"]["omits"]
    assert parsed[-1]["record_type"] == "export_summary"

    kinds = [p["record_type"] for p in parsed]
    assert kinds.count("species") == 3
    assert kinds.count("reaction") == 1
    # Summary reports zero gaps for a fully-populated mechanism.
    assert parsed[-1]["counts"]["gaps"] == 0


def test_ndjson_seed_resolved_eagerly_but_records_streamed(db_session):
    # The seed is resolved eagerly (so an invalid seed 422s before the
    # stream starts), but per-record JSON is produced lazily from the
    # returned iterator — the streaming contract (spec §3).
    import collections.abc

    with pytest.raises(ValueError, match="export_seed_unresolved"):
        iter_export_ndjson(
            db_session, seed=SeedSelection(species_refs=["whatever"])
        )

    entry, _species, _ = _build_one_reaction(db_session)
    it = iter_export_ndjson(
        db_session, seed=SeedSelection(reaction_refs=[entry.public_ref])
    )
    assert isinstance(it, collections.abc.Iterator)
    first = json.loads(next(it))
    assert first["record_type"] == "manifest"


# ---------------------------------------------------------------------------
# Thermo representation kinds in the NDJSON export (regression: the loader
# must not be blind to NASA-9 / Wilhoit fits, dropping them as "scalar").
# ---------------------------------------------------------------------------


def _select_thermo(session, entry):
    rs = build_export_record_set(
        session, seed=SeedSelection(species_refs=[entry.public_ref])
    )
    (sr,) = rs.species_records
    assert len(sr.thermos) == 1
    return sr.thermos[0]


def test_nasa9_only_thermo_exports_nasa9_block_not_scalar(db_session):
    sp = make_species(db_session, smiles="C", inchi_key=next_inchi_key("N9"))
    entry = make_species_entry(db_session, sp)
    thermo = make_thermo_scalar(db_session, species_entry=entry)
    thermo.model_kind = ThermoModelKind.nasa9
    intervals = attach_thermo_nasa9(db_session, thermo=thermo)
    db_session.flush()
    _approve(db_session, SubmissionRecordType.thermo, thermo.id)

    sel = _select_thermo(db_session, entry)
    assert sel.model_kind == "nasa9"

    d = sel.to_dict()
    assert d["model_kind"] == "nasa9"
    # The fit is present, not dropped.
    assert d["nasa9"] is not None
    assert len(d["nasa9"]) == len(intervals) == 2
    assert [blk["interval_index"] for blk in d["nasa9"]] == [1, 2]
    first = d["nasa9"][0]
    assert first["t_min_k"] == 200.0
    assert first["t_max_k"] == 1000.0
    assert first["a1"] == 1.0 and first["a9"] == 9.0
    # Other representation blocks stay absent.
    assert d["nasa"] is None
    assert d["wilhoit"] is None
    assert d["points"] is None


def test_wilhoit_only_thermo_exports_wilhoit_block_not_scalar(db_session):
    sp = make_species(db_session, smiles="C", inchi_key=next_inchi_key("WH"))
    entry = make_species_entry(db_session, sp)
    thermo = make_thermo_scalar(db_session, species_entry=entry)
    thermo.model_kind = ThermoModelKind.wilhoit
    attach_thermo_wilhoit(db_session, thermo=thermo)
    db_session.flush()
    _approve(db_session, SubmissionRecordType.thermo, thermo.id)

    sel = _select_thermo(db_session, entry)
    assert sel.model_kind == "wilhoit"

    d = sel.to_dict()
    assert d["model_kind"] == "wilhoit"
    assert d["wilhoit"] is not None
    assert d["wilhoit"]["cp0_j_mol_k"] == 33.0
    assert d["wilhoit"]["cp_inf_j_mol_k"] == 120.0
    assert d["wilhoit"]["b_k"] == 500.0
    assert d["wilhoit"]["a0"] == 1.0
    assert d["wilhoit"]["a3"] == 0.125
    assert d["wilhoit"]["h0_kj_mol"] == -45.0
    assert d["wilhoit"]["s0_j_mol_k"] == 210.0
    assert d["nasa"] is None
    assert d["nasa9"] is None
    assert d["points"] is None


def test_nasa9_only_derived_when_model_kind_null(db_session):
    # Legacy row with NULL model_kind but NASA-9 children still classifies as
    # nasa9 via fit-precedence, not scalar.
    sp = make_species(db_session, smiles="C", inchi_key=next_inchi_key("N9L"))
    entry = make_species_entry(db_session, sp)
    thermo = make_thermo_scalar(db_session, species_entry=entry)
    assert thermo.model_kind is None
    attach_thermo_nasa9(db_session, thermo=thermo)
    _approve(db_session, SubmissionRecordType.thermo, thermo.id)

    sel = _select_thermo(db_session, entry)
    assert sel.model_kind == "nasa9"
    assert sel.to_dict()["nasa9"] is not None


def test_nasa7_record_still_exports_nasa_block(db_session):
    sp = make_species(db_session, smiles="C", inchi_key=next_inchi_key("N7"))
    entry = make_species_entry(db_session, sp)
    thermo = make_thermo_scalar(db_session, species_entry=entry)
    attach_thermo_nasa(db_session, thermo=thermo)
    _approve(db_session, SubmissionRecordType.thermo, thermo.id)

    sel = _select_thermo(db_session, entry)
    assert sel.model_kind == "nasa"
    d = sel.to_dict()
    assert d["nasa"] is not None
    assert d["nasa"]["t_low"] == 200.0
    assert d["nasa9"] is None
    assert d["wilhoit"] is None
    assert d["points"] is None


def test_points_only_record_still_exports_points_block(db_session):
    sp = make_species(db_session, smiles="C", inchi_key=next_inchi_key("PO"))
    entry = make_species_entry(db_session, sp)
    thermo = make_thermo_scalar(db_session, species_entry=entry)
    attach_thermo_points(db_session, thermo=thermo, temperatures_k=[300.0, 1000.0])
    _approve(db_session, SubmissionRecordType.thermo, thermo.id)

    sel = _select_thermo(db_session, entry)
    assert sel.model_kind == "points"
    d = sel.to_dict()
    assert d["points"] is not None and len(d["points"]) == 2
    assert d["nasa"] is None
    assert d["nasa9"] is None
    assert d["wilhoit"] is None


def test_scalar_only_record_still_exports_scalar(db_session):
    sp = make_species(db_session, smiles="C", inchi_key=next_inchi_key("SC"))
    entry = make_species_entry(db_session, sp)
    thermo = make_thermo_scalar(db_session, species_entry=entry)
    _approve(db_session, SubmissionRecordType.thermo, thermo.id)

    sel = _select_thermo(db_session, entry)
    assert sel.model_kind == "scalar"
    d = sel.to_dict()
    assert d["nasa"] is None
    assert d["nasa9"] is None
    assert d["wilhoit"] is None
    assert d["points"] is None


# ---------------------------------------------------------------------------
# CHEMKIN serialization + gaps
# ---------------------------------------------------------------------------


def test_chemkin_serialize_produces_files(db_session):
    entry, (e_a, e_b, e_c), kin = _build_one_reaction(db_session)
    # Give one species transport so tran.dat has content.
    tr = make_transport(db_session, species_entry=e_a)
    _approve(db_session, SubmissionRecordType.transport, tr.id)

    rs = build_export_record_set(
        db_session, seed=SeedSelection(reaction_refs=[entry.public_ref])
    )
    result = serialize_chemkin(rs, options=ChemkinOptions())

    assert set(result.files) == {"chem.inp", "therm.dat", "tran.dat"}
    chem = result.files["chem.inp"]
    assert "ELEMENTS" in chem and "SPECIES" in chem and "REACTIONS" in chem
    assert "CAL/MOLE MOLES" in chem
    # NASA cards: the 1/2/3/4 continuation markers in column 80.
    therm = result.files["therm.dat"]
    assert therm.rstrip().endswith("END")
    assert any(line.endswith("1") for line in therm.splitlines())
    assert any(line.endswith("4") for line in therm.splitlines())
    # Traceability comments carry SMILES + public ref.
    assert "SMILES=" in chem and "ref=" in chem
    # No NASA gaps for a fully-fitted mechanism.
    assert not [g for g in result.gaps if g.kind == "thermo_nasa"]


def test_points_only_thermo_is_a_chemkin_gap_not_a_broken_block(db_session):
    sp = make_species(db_session, smiles="C", inchi_key=next_inchi_key("PT"))
    entry = make_species_entry(db_session, sp)
    thermo = make_thermo_scalar(db_session, species_entry=entry)
    attach_thermo_points(db_session, thermo=thermo, temperatures_k=[300.0, 1000.0])
    _approve(db_session, SubmissionRecordType.thermo, thermo.id)

    rs = build_export_record_set(
        db_session, seed=SeedSelection(species_refs=[entry.public_ref])
    )
    # The record IS selected (points thermo is a qualifying value)...
    (sr,) = rs.species_records
    assert sr.thermos and sr.thermos[0].model_kind == "points"

    result = serialize_chemkin(rs, options=ChemkinOptions())
    # ...but CHEMKIN needs NASA-7, so it is reported as a gap, and no broken
    # thermo card is emitted for it.
    nasa_gaps = [g for g in result.gaps if g.kind == "thermo_nasa"]
    assert [g.ref for g in nasa_gaps] == [entry.public_ref]


def test_chemkin_falloff_emits_low_and_troe(db_session):
    from app.db.models.kinetics import KineticsFalloff

    _, e_a, _ = _species_with_nasa(db_session, smiles="C")
    _, e_c, _ = _species_with_nasa(db_session, smiles="CO")
    chem = make_chem_reaction(
        db_session, reactants=[e_a.species], products=[e_c.species]
    )
    entry = make_reaction_entry(
        db_session, reaction=chem, reactant_entries=[e_a], product_entries=[e_c]
    )
    kin = make_kinetics(
        db_session,
        reaction_entry=entry,
        model_kind=KineticsModelKind.troe,
        a=1.0e13,
        a_units=ArrheniusAUnits.cm3_mol_s,
    )
    db_session.add(
        KineticsFalloff(
            kinetics_id=kin.id,
            low_a=1.0e18,
            low_a_units=ArrheniusAUnits.cm6_mol2_s,
            low_n=-1.0,
            low_ea_kj_mol=0.0,
            troe_alpha=0.5,
            troe_t3=100.0,
            troe_t1=1000.0,
        )
    )
    db_session.flush()
    _approve(db_session, SubmissionRecordType.kinetics, kin.id)

    rs = build_export_record_set(
        db_session, seed=SeedSelection(reaction_refs=[entry.public_ref])
    )
    chem_inp = serialize_chemkin(rs).files["chem.inp"]
    assert "(+M)" in chem_inp
    assert "LOW /" in chem_inp
    assert "TROE /" in chem_inp


# ---------------------------------------------------------------------------
# NDJSON export: pressure-dependent / third-body kinetics forms.
#
# Regression: SelectedKinetics.to_dict used to emit only scalar
# modified-Arrhenius fields, silently dropping every non-Arrhenius form
# (multi_arrhenius / plog / chebyshev / falloff / third-body). These assert
# the export dict mirrors the PR #41 reaction-kinetics read surface.
# ---------------------------------------------------------------------------


def _reaction_with_kinetics(session, **kin_kwargs):
    """A -> C reaction with a single approved kinetics record."""
    sp_a = make_species(session, inchi_key=next_inchi_key("KRA"))
    e_a = make_species_entry(session, sp_a)
    sp_c = make_species(session, inchi_key=next_inchi_key("KRC"))
    e_c = make_species_entry(session, sp_c)
    chem = make_chem_reaction(session, reactants=[sp_a], products=[sp_c])
    entry = make_reaction_entry(
        session, reaction=chem, reactant_entries=[e_a], product_entries=[e_c]
    )
    kin = make_kinetics(session, reaction_entry=entry, **kin_kwargs)
    _approve(session, SubmissionRecordType.kinetics, kin.id)
    return entry, kin


def _export_kinetics_dict(session, entry):
    rs = build_export_record_set(
        session, seed=SeedSelection(reaction_refs=[entry.public_ref])
    )
    (rr,) = rs.reaction_records
    assert len(rr.kinetics) == 1
    return rr.kinetics[0].to_dict()


def test_scalar_arrhenius_export_keeps_legacy_keys(db_session):
    # Regression guard: a plain modified-Arrhenius record still emits the
    # original scalar keys unchanged, with the new child blocks all null.
    entry, kin = _reaction_with_kinetics(db_session)
    d = _export_kinetics_dict(db_session, entry)

    assert d["kinetics_ref"] == kin.public_ref
    assert d["model_kind"] == "modified_arrhenius"
    assert d["a"] == kin.a
    assert d["a_units"] == ArrheniusAUnits.cm3_molecule_s.value
    assert d["n"] == kin.n
    assert d["ea_kj_mol"] == kin.ea_kj_mol
    assert d["tmin_k"] == kin.tmin_k
    assert d["tmax_k"] == kin.tmax_k
    assert d["degeneracy"] == kin.degeneracy
    # New scalars default cleanly.
    assert d["direction"] is None
    assert d["is_third_body"] is False
    assert d["pressure_context"] is None
    assert d["tunneling_model"] is None
    # No child forms present.
    assert d["multi_arrhenius"] is None
    assert d["plog_entries"] is None
    assert d["chebyshev"] is None
    assert d["falloff"] is None
    assert d["third_body_efficiencies"] is None


def test_multi_arrhenius_export_emits_terms_and_null_scalars(db_session):
    entry, kin = _reaction_with_kinetics(
        db_session,
        model_kind=KineticsModelKind.multi_arrhenius,
        a=None,
        a_units=None,
        n=None,
        ea_kj_mol=None,
    )
    # Insert terms out of order to prove ORM entry_index ordering is honored.
    attach_kinetics_arrhenius_entry(
        db_session, kinetics=kin, entry_index=2, a=2.0e13, n=0.5, ea_kj_mol=42.0
    )
    attach_kinetics_arrhenius_entry(
        db_session, kinetics=kin, entry_index=1, a=1.0e12, n=0.0, ea_kj_mol=10.0
    )

    d = _export_kinetics_dict(db_session, entry)
    assert d["model_kind"] == "multi_arrhenius"
    # Scalar Arrhenius columns are null for a sum-of-Arrhenius record.
    assert d["a"] is None and d["n"] is None and d["ea_kj_mol"] is None
    terms = d["multi_arrhenius"]
    assert [t["entry_index"] for t in terms] == [1, 2]
    assert terms[0]["A"] == 1.0e12
    assert terms[0]["A_units"] == ArrheniusAUnits.cm3_molecule_s.value
    assert terms[0]["Ea_kj_mol"] == 10.0
    assert terms[1]["A"] == 2.0e13
    assert terms[1]["n"] == 0.5
    # Other child forms stay absent.
    assert d["plog_entries"] is None
    assert d["chebyshev"] is None
    assert d["falloff"] is None


def test_plog_export_emits_ordered_pressure_entries(db_session):
    entry, kin = _reaction_with_kinetics(
        db_session, model_kind=KineticsModelKind.plog
    )
    attach_kinetics_plog_entry(
        db_session, kinetics=kin, entry_index=2, pressure_bar=10.0, a=2.0e13,
        a_units=ArrheniusAUnits.cm3_mol_s, n=0.0, ea_kj_mol=20.0,
    )
    attach_kinetics_plog_entry(
        db_session, kinetics=kin, entry_index=1, pressure_bar=1.0, a=1.0e12,
        a_units=ArrheniusAUnits.cm3_mol_s, n=0.0, ea_kj_mol=10.0,
    )

    d = _export_kinetics_dict(db_session, entry)
    assert d["model_kind"] == "plog"
    plog = d["plog_entries"]
    assert [e["entry_index"] for e in plog] == [1, 2]
    assert [e["pressure_bar"] for e in plog] == [1.0, 10.0]
    assert plog[0]["A"] == 1.0e12
    assert plog[0]["A_units"] == ArrheniusAUnits.cm3_mol_s.value
    assert plog[1]["Ea_kj_mol"] == 20.0
    assert d["chebyshev"] is None
    assert d["falloff"] is None


def test_chebyshev_export_emits_matrix_and_domain(db_session):
    entry, kin = _reaction_with_kinetics(
        db_session,
        model_kind=KineticsModelKind.chebyshev,
        a=None,
        a_units=None,
        n=None,
        ea_kj_mol=None,
    )
    matrix = [[1.0, 2.0, 3.0], [4.0, 5.0, 6.0]]
    attach_kinetics_chebyshev(
        db_session, kinetics=kin, n_temperature=2, n_pressure=3,
        coefficients=matrix, tmin_k=300.0, tmax_k=2000.0,
        pmin_bar=0.01, pmax_bar=100.0,
    )

    d = _export_kinetics_dict(db_session, entry)
    assert d["model_kind"] == "chebyshev"
    assert d["a"] is None
    cheb = d["chebyshev"]
    assert cheb["n_temperature"] == 2
    assert cheb["n_pressure"] == 3
    assert cheb["coefficients"] == matrix
    assert cheb["tmin_k"] == 300.0 and cheb["tmax_k"] == 2000.0
    assert cheb["pmin_bar"] == 0.01 and cheb["pmax_bar"] == 100.0
    assert d["plog_entries"] is None
    assert d["falloff"] is None


def test_falloff_export_emits_block_and_sorted_third_body(db_session):
    entry, kin = _reaction_with_kinetics(
        db_session,
        model_kind=KineticsModelKind.troe,
        a=1.0e13,
        a_units=ArrheniusAUnits.cm3_mol_s,
        pressure_context=PressureContext.pressure_dependent,
        tunneling_model=TunnelingModel.eckart,
        a_uncertainty=2.0,
        a_uncertainty_kind=KineticsUncertaintyKind.multiplicative,
        n_uncertainty=0.1,
        ea_uncertainty_kj_mol=1.5,
    )
    attach_kinetics_falloff(
        db_session, kinetics=kin, low_a=1.0e18,
        low_a_units=ArrheniusAUnits.cm6_mol2_s, low_n=-1.0, low_ea_kj_mol=0.0,
        troe_alpha=0.5, troe_t3=100.0, troe_t1=1000.0,
    )
    # Two distinct collider species; assert they surface as public refs,
    # sorted deterministically by collider_ref.
    col_a = make_species(db_session, smiles="O", inchi_key=next_inchi_key("COLA"))
    col_b = make_species(db_session, smiles="N#N", inchi_key=next_inchi_key("COLB"))
    attach_kinetics_third_body_efficiency(
        db_session, kinetics=kin, collider_species=col_a, efficiency=6.0
    )
    attach_kinetics_third_body_efficiency(
        db_session, kinetics=kin, collider_species=col_b, efficiency=0.7
    )

    d = _export_kinetics_dict(db_session, entry)
    assert d["model_kind"] == "troe"
    # Falloff block mirrors the read FalloffBlock.
    fo = d["falloff"]
    assert fo["kind"] == "troe"
    assert fo["low_A"] == 1.0e18
    assert fo["low_A_units"] == ArrheniusAUnits.cm6_mol2_s.value
    assert fo["low_n"] == -1.0
    assert fo["troe_alpha"] == 0.5
    assert fo["troe_t3"] == 100.0
    assert fo["sri_a"] is None
    # Third-body efficiencies: public refs only, deterministic sorted order.
    tbes = d["third_body_efficiencies"]
    refs = [b["collider_ref"] for b in tbes]
    assert refs == sorted(refs)
    assert set(refs) == {col_a.public_ref, col_b.public_ref}
    # Never the raw PK.
    assert col_a.id not in refs and col_b.id not in refs
    eff_by_ref = {b["collider_ref"]: b["efficiency"] for b in tbes}
    assert eff_by_ref[col_a.public_ref] == 6.0
    assert eff_by_ref[col_b.public_ref] == 0.7
    # Pressure-dependent / third-body / uncertainty scalars surface.
    assert d["is_third_body"] is False  # falloff k∞ is not a simple +M rate
    assert d["pressure_context"] == PressureContext.pressure_dependent.value
    assert d["tunneling_model"] == TunnelingModel.eckart.value
    assert d["a_uncertainty"] == 2.0
    assert d["a_uncertainty_kind"] == KineticsUncertaintyKind.multiplicative.value
    assert d["n_uncertainty"] == 0.1
    assert d["ea_uncertainty_kj_mol"] == 1.5
    # No PLOG/Chebyshev on a falloff record.
    assert d["plog_entries"] is None
    assert d["chebyshev"] is None
