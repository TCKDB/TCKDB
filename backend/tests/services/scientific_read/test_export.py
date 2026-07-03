"""Service-layer tests for the bulk-export selection/closure core and the
CHEMKIN serializer (docs/specs/bulk_export_design.md §4, §5, §8)."""

from __future__ import annotations

import json

import pytest

from app.db.models.common import (
    ArrheniusAUnits,
    KineticsModelKind,
    RecordReviewStatus,
    SubmissionRecordType,
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
    attach_thermo_nasa,
    attach_thermo_points,
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
