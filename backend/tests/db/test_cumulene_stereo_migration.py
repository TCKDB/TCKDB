"""``e5b2c8d1f947``: allene ``Ra``/``Sa`` and odd-cumulene ``E``/``Z`` in the entry stereo descriptor (#827).

Driven in-process on the per-test transaction: the revision's ``downgrade()`` first puts the database back to the
vocabulary of the parent (so entries can be seeded the way the old code left them: ``?`` for every cumulene
element), then ``upgrade()`` runs the constraint swap and the data step.
"""

from __future__ import annotations

import re
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from uuid import uuid4

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from rdkit import Chem
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError

from app.chemistry import stereo
from app.chemistry.species import identity_mol_from_smiles
from app.db.base import Base
from app.services import cumulene_stereo_fill
from tests.services import _cumulene_fixtures as fx

_PATH = Path(__file__).resolve().parents[2] / "alembic/versions/e5b2c8d1f947_allene_and_cumulene_stereo_tokens.py"


def _load(db_session, monkeypatch):
    spec = spec_from_file_location("cumulene_stereo_migration", _PATH)
    migration = module_from_spec(spec)
    spec.loader.exec_module(migration)
    monkeypatch.setattr(
        migration,
        "op",
        Operations(MigrationContext.configure(db_session.connection(), opts={"target_metadata": Base.metadata})),
    )
    return migration


def _species(db_session, smiles: str) -> int:
    return db_session.scalar(
        text(
            "INSERT INTO species (kind, smiles, inchi_key, charge, multiplicity, stereo_kind) "
            "VALUES (CAST('molecule' AS molecule_kind), :s, :k, 0, 1, CAST('enantiomer' AS stereo_kind)) RETURNING id"
        ),
        {"s": smiles, "k": Chem.MolToInchiKey(Chem.MolFromSmiles(smiles))},
    )


def _entry(
    db_session, species: int, smiles: str, descriptor: str, resolution: str = "unresolved", term_symbol: str | None = None
) -> tuple[int, str]:
    row = db_session.execute(
        text(
            "INSERT INTO species_entry (species_id, mol, stereo_descriptor, stereo_resolution, stereo_label, term_symbol) "
            "VALUES (:sp, mol_from_smiles(CAST(:m AS cstring)), :d, CAST(:r AS stereo_resolution), :l, :t) "
            "RETURNING id, public_ref"
        ),
        {
            "sp": species,
            "m": smiles,
            "d": descriptor,
            "r": resolution,
            "l": None if resolution == "unresolved" else descriptor,
            "t": term_symbol,
        },
    ).one()
    return row.id, row.public_ref


def _geometry(db_session, entry: int, mol: Chem.Mol) -> None:
    xyz = fx.xyz(mol)
    geometry = db_session.scalar(
        text("INSERT INTO geometry (natoms, geom_hash, xyz_text) VALUES (:n, :h, :x) RETURNING id"),
        {"n": mol.GetNumAtoms(), "h": uuid4().hex.ljust(64, "0"), "x": xyz},
    )
    for index, line in enumerate(xyz.strip().splitlines()[2:], start=1):
        element, x, y, z = line.split()
        db_session.execute(
            text("INSERT INTO geometry_atom (geometry_id, atom_index, element, x, y, z) VALUES (:g, :i, :e, :x, :y, :z)"),
            {"g": geometry, "i": index, "e": element, "x": float(x), "y": float(y), "z": float(z)},
        )
    calculation = db_session.scalar(
        text("INSERT INTO calculation (type, species_entry_id) VALUES (CAST('opt' AS calc_type), :e) RETURNING id"),
        {"e": entry},
    )
    db_session.execute(
        text(
            "INSERT INTO calculation_output_geometry (calculation_id, geometry_id, output_order, role) "
            "VALUES (:c, :g, 1, CAST('final' AS calculation_geometry_role))"
        ),
        {"c": calculation, "g": geometry},
    )


def _state(db_session, entry: int) -> tuple[str, str, str | None, str | None]:
    row = db_session.execute(
        text(
            "SELECT stereo_descriptor, stereo_resolution::text, stereo_label, stereo_smiles FROM species_entry WHERE id = :e"
        ),
        {"e": entry},
    ).one()
    return tuple(row)


def _merge(db_session, loser: int, survivor: int) -> None:
    db_session.execute(
        text("UPDATE species_entry SET merged_into_species_entry_id = :s WHERE id = :l"), {"s": survivor, "l": loser}
    )


@pytest.fixture
def old_world(db_session, monkeypatch):
    """The database as the parent revision left it, and the migration module bound to it."""

    migration = _load(db_session, monkeypatch)
    migration.downgrade()
    return migration


def test_unanimous_geometries_fill_in_place_and_the_rest_is_listed_and_left(db_session, old_world, capsys) -> None:
    allene = _species(db_session, "CC=C=CC")
    filled, filled_ref = _entry(db_session, allene, "CC=C=CC", "?")
    _geometry(db_session, filled, fx.build("CC=C=CC", -90.0))
    _geometry(db_session, filled, fx.build("CC=C=CC", -70.0, seed=5))  # another conformer of the same sense

    trans, trans_ref = _entry(db_session, _species(db_session, "CC=C=C=CC"), "CC=C=C=CC", "?")
    _geometry(db_session, trans, fx.build("CC=C=C=CC", 180.0))

    disputed, disputed_ref = _entry(db_session, _species(db_session, "CC(Cl)=C=C(F)C"), "CC(Cl)=C=C(F)C", "?")
    pair = fx.build("CC(Cl)=C=C(F)C", -90.0)
    _geometry(db_session, disputed, pair)
    _geometry(db_session, disputed, fx.mirror(pair))

    no_geometry, _ = _entry(db_session, _species(db_session, "FC=C=CCl"), "FC=C=CCl", "?")

    small, _ = _entry(db_session, _species(db_session, "C1=C=C(C)CCC1"), "C1=C=C(C)CCC1", "?")  # never settled
    flat, _ = _entry(db_session, _species(db_session, "OC=C=CC"), "OC=C=CC", "?")
    _geometry(db_session, flat, fx.build("OC=C=CC", 0.0))

    old_world.upgrade()
    out = capsys.readouterr().out

    assert _state(db_session, filled) == ("Ra", "resolved", "Ra", None)
    assert f"{filled_ref}: ? -> Ra" in out
    assert _state(db_session, trans) == ("E", "resolved", "E", None)
    assert f"{trans_ref}: ? -> E" in out
    for entry in (disputed, no_geometry, small, flat):
        assert _state(db_session, entry)[:3] == ("?", "unresolved", None), entry
    assert disputed_ref in out and "geometries disagree" in out
    assert "0 changed" not in out
    assert re.search(r"2 filled, 1 left \(geometries do not settle the element\), 1 left \(no linked geometry\)", out), out
    assert "1 left (geometries disagree)" in out


def test_a_second_run_changes_nothing_and_says_so(db_session, old_world, capsys) -> None:
    entry, _ = _entry(db_session, _species(db_session, "CC=C=CC"), "CC=C=CC", "?")
    _geometry(db_session, entry, fx.build("CC=C=CC", 90.0))
    old_world.upgrade()
    capsys.readouterr()
    assert _state(db_session, entry)[0] == "Sa"
    report = cumulene_stereo_fill.fill_cumulene_stereo(db_session.connection(), revision="again")
    cumulene_stereo_fill.print_report(report, "again")
    out = capsys.readouterr().out
    assert report.changes == [] and "0 changed" in out and "0 filled" in out


def test_with_nothing_to_fill_it_prints_zero_changed_and_a_summary(db_session, old_world, capsys) -> None:
    old_world.upgrade()
    out = capsys.readouterr().out
    assert "0 changed" in out
    assert "cumulene stereo fill: 0 filled" in out


def test_an_entry_that_already_knows_part_of_its_descriptor_keeps_it(db_session, old_world) -> None:
    smiles = "CC=C=CC(C)O"
    order = [e.kind for e in stereo.frame_from_mol(identity_mol_from_smiles(smiles)).elements]
    assert sorted(order) == ["axial", "tetrahedral"]
    geometry = fx.build(smiles, -90.0)
    frame = stereo.frame_from_mol(identity_mol_from_smiles(smiles))
    centre = stereo.geometry_tokens(frame, fx.xyz(geometry))[order.index("tetrahedral")]
    assert centre in ("R", "S")
    entry, _ = _entry(
        db_session,
        _species(db_session, smiles),
        smiles,
        f"?,{centre}" if order[0] == "axial" else f"{centre},?",
        "partial",
    )
    _geometry(db_session, entry, geometry)
    old_world.upgrade()
    descriptor, resolution, _label, stereo_smiles = _state(db_session, entry)
    assert sorted(descriptor.split(",")) == sorted([centre, "Ra"]) and resolution == "resolved" and stereo_smiles is None
    assert descriptor.split(",")[order.index("tetrahedral")] == centre


def test_a_geometry_against_the_known_part_of_the_descriptor_leaves_the_entry_listed(db_session, old_world, capsys) -> None:
    smiles = "CC=C=CC(C)O"
    geometry = fx.build(smiles, -90.0)
    frame = stereo.frame_from_mol(identity_mol_from_smiles(smiles))
    order = [e.kind for e in frame.elements]
    centre = stereo.geometry_tokens(frame, fx.xyz(geometry))[order.index("tetrahedral")]
    other = "S" if centre == "R" else "R"  # the entry says the other centre than its geometry does
    old = f"?,{other}" if order[0] == "axial" else f"{other},?"
    entry, ref = _entry(db_session, _species(db_session, smiles), smiles, old, "partial")
    _geometry(db_session, entry, geometry)
    old_world.upgrade()
    assert _state(db_session, entry)[0] == old
    out = capsys.readouterr().out
    assert ref in out and "geometries disagree" in out


def test_a_merged_away_entry_is_frozen_listed_and_never_touched(db_session, old_world, capsys) -> None:
    allene = _species(db_session, "CC=C=CC")
    survivor, _ = _entry(db_session, allene, "CC=C=CC", "?")
    loser, loser_ref = _entry(db_session, allene, "CC=C=CC", "?", "unresolved", term_symbol="X")
    _geometry(db_session, loser, fx.build("CC=C=CC", -90.0))
    _merge(db_session, loser, survivor)
    before = _state(db_session, loser)
    old_world.upgrade()
    out = capsys.readouterr().out
    assert _state(db_session, loser) == before == ("?", "unresolved", None, None)
    assert f"merged species_entry skipped (frozen): {loser_ref}" in out
    assert "1 merged row(s) skipped" in out


def test_a_fill_that_collides_with_a_live_entry_stops_the_migration_with_a_list(db_session, monkeypatch) -> None:
    allene = _species(db_session, "CC=C=CC")
    open_entry, open_ref = _entry(db_session, allene, "CC=C=CC", "?")
    _geometry(db_session, open_entry, fx.build("CC=C=CC", -90.0))
    # Only an entry written after the swap can already hold Ra: the key differs by descriptor, so both are live.
    _holder, holder_ref = _entry(db_session, allene, "CC=C=CC", "Ra", "resolved")
    with pytest.raises(RuntimeError) as excinfo:
        cumulene_stereo_fill.fill_cumulene_stereo(db_session.connection(), revision="e5b2c8d1f947")
    message = str(excinfo.value)
    assert open_ref in message and holder_ref in message and "never merges" in message
    assert _state(db_session, open_entry)[0] == "?"  # nothing was written


def test_entries_of_one_species_that_differ_in_state_do_not_collide(db_session) -> None:
    allene = _species(db_session, "CC=C=CC")
    first, _ = _entry(db_session, allene, "CC=C=CC", "?")
    second, _ = _entry(db_session, allene, "CC=C=CC", "?", term_symbol="T")
    for entry in (first, second):
        _geometry(db_session, entry, fx.build("CC=C=CC", -90.0))
    report = cumulene_stereo_fill.fill_cumulene_stereo(db_session.connection(), revision="e5b2c8d1f947")
    assert len(report.changes) == 2
    assert _state(db_session, first)[0] == _state(db_session, second)[0] == "Ra"


def _assessment(db_session, entry: int, ref: str) -> None:
    db_session.execute(
        text(
            "INSERT INTO record_reproducibility_assessment (record_type, record_id, grade, rubric_name, rubric_version, "
            "context_hash, context_json, assessor_kind, public_ref) VALUES "
            "(CAST('species_entry' AS submission_record_type), :e, CAST('described' AS reproducibility_grade), 'r', 1, "
            "repeat('b', 64), CAST('{}' AS jsonb), CAST('system' AS reproducibility_assessor_kind), :ref)"
        ),
        {"e": entry, "ref": ref},
    )


def test_filled_entries_with_a_stored_assessment_are_reported_as_restaling(db_session, old_world, capsys) -> None:
    entry, _ = _entry(db_session, _species(db_session, "CC=C=CC"), "CC=C=CC", "?")
    _geometry(db_session, entry, fx.build("CC=C=CC", -90.0))
    _assessment(db_session, entry, "rra_cumulene_test_1")
    old_world.upgrade()
    out = capsys.readouterr().out
    assert "1 reproducibility assessment(s)" in out and "will read as stale" in out


def test_a_human_review_row_is_not_a_digest_and_is_not_counted(db_session, old_world, capsys) -> None:
    entry, _ = _entry(db_session, _species(db_session, "CC=C=CC"), "CC=C=CC", "?")
    _geometry(db_session, entry, fx.build("CC=C=CC", -90.0))
    db_session.execute(
        text(
            "INSERT INTO record_review (record_type, record_id, status) "
            "VALUES (CAST('species_entry' AS submission_record_type), :e, CAST('not_reviewed' AS record_review_status))"
        ),
        {"e": entry},
    )
    old_world.upgrade()
    assert "so none restales" in capsys.readouterr().out


def test_entries_without_a_geometry_are_listed_by_ref_and_not_double_counted(db_session, old_world, capsys) -> None:
    entry, ref = _entry(db_session, _species(db_session, "CC=C=CC"), "CC=C=CC", "?")
    old_world.upgrade()
    out = capsys.readouterr().out
    assert f"no linked geometry, entry left as it was: {ref}" in out
    assert "0 filled" in out and "1 left (no linked geometry)" in out
    assert _state(db_session, entry)[0] == "?"


RING = "CC1C=C=CCCCCC=C=C1"


def _embedded(smiles: str, seed: int = 3) -> Chem.Mol:
    from rdkit.Chem import AllChem

    mol = Chem.AddHs(Chem.MolFromSmiles(smiles))
    AllChem.EmbedMolecule(mol, randomSeed=seed)
    return mol


def test_a_stored_minus_on_an_allene_dependent_centre_is_listed_and_left(db_session, old_world, capsys) -> None:
    entry, ref = _entry(db_session, _species(db_session, RING), RING, "?,?,-")
    _geometry(db_session, entry, _embedded(RING))
    old_world.upgrade()
    out = capsys.readouterr().out
    assert _state(db_session, entry)[0] == "?,?,-"
    assert ref in out and "entry left as it was" in out


def test_the_fill_never_writes_a_state_for_an_allene_dependent_element(db_session, monkeypatch) -> None:
    from dataclasses import replace

    entry, ref = _entry(db_session, _species(db_session, RING), RING, "?,?,?")
    _geometry(db_session, entry, _embedded(RING))
    real = cumulene_stereo_fill.stereo.resolve_stereo
    order = [e.kind for e in stereo.frame_from_mol(identity_mol_from_smiles(RING)).elements]

    def with_a_dash(*args, **kwargs):
        tokens = ["Ra", "Sa", "Sa"]
        tokens[order.index("allene_dependent")] = "-"
        return replace(real(*args, **kwargs), descriptor=",".join(tokens))

    monkeypatch.setattr(cumulene_stereo_fill.stereo, "resolve_stereo", with_a_dash)
    report = cumulene_stereo_fill.fill_cumulene_stereo(db_session.connection(), revision="t")
    assert report.changes == [] and any(ref in line for line in report.unreadable)
    assert _state(db_session, entry)[0] == "?,?,?"


def test_with_no_stored_review_on_a_filled_entry_nothing_restales(db_session, old_world, capsys) -> None:
    entry, _ = _entry(db_session, _species(db_session, "CC=C=CC"), "CC=C=CC", "?")
    _geometry(db_session, entry, fx.build("CC=C=CC", -90.0))
    old_world.upgrade()
    assert "so none restales" in capsys.readouterr().out


def test_species_entry_carries_no_accepted_science_guard_and_a_guard_would_stop_the_step(db_session) -> None:
    cumulene_stereo_fill.assert_no_accepted_science_guard(db_session.connection())
    db_session.execute(
        text(
            "CREATE TRIGGER trg_test_species_entry_guard BEFORE UPDATE ON species_entry FOR EACH ROW "
            "EXECUTE FUNCTION public.tckdb_reject_mutation()"
        )
    )
    with pytest.raises(RuntimeError, match="accepted-science guard"):
        cumulene_stereo_fill.assert_no_accepted_science_guard(db_session.connection())


def test_the_new_constraint_accepts_ra_sa_as_whole_tokens(db_session) -> None:
    species = _species(db_session, "CC=C=CC")
    for number, descriptor in enumerate(("Ra", "Sa", "R,Ra,?", "Ra,Sa")):
        with db_session.begin_nested():
            _entry(db_session, species, "CC=C=CC", descriptor, "resolved", term_symbol=f"T{number}")
    for number, bad in enumerate(("Rb", "RA", "Ra,", "a", "R a", "ra")):
        with pytest.raises(IntegrityError), db_session.begin_nested():
            _entry(db_session, species, "CC=C=CC", bad, "resolved", term_symbol=f"B{number}")


def test_downgrade_stops_with_a_list_when_an_entry_holds_ra_or_sa(db_session, monkeypatch) -> None:
    migration = _load(db_session, monkeypatch)
    allene = _species(db_session, "CC=C=CC")
    live, live_ref = _entry(db_session, allene, "CC=C=CC", "Ra", "resolved")
    loser, loser_ref = _entry(db_session, allene, "CC=C=CC", "Sa", "resolved")
    _merge(db_session, loser, live)
    with pytest.raises(RuntimeError) as excinfo:
        migration.downgrade()
    message = str(excinfo.value)
    assert live_ref in message and loser_ref in message and "merged" in message and "not rewritten silently" in message
    assert _state(db_session, live)[0] == "Ra" and _state(db_session, loser)[0] == "Sa"  # nothing was rewritten


def test_downgrade_without_axis_tokens_restores_the_old_vocabulary_and_upgrade_restores_the_new(db_session, monkeypatch) -> None:
    migration = _load(db_session, monkeypatch)
    migration.downgrade()
    with pytest.raises((IntegrityError, DBAPIError)), db_session.begin_nested():
        _entry(db_session, _species(db_session, "CC=C=CC"), "CC=C=CC", "Ra", "resolved")
    entry, _ = _entry(db_session, _species(db_session, "CC=CC"), "CC=CC", "E", "resolved")
    assert _state(db_session, entry)[0] == "E"
    migration.upgrade()
    _entry(db_session, _species(db_session, "CC=C=C(C)C"), "CC=C=C(C)C", "Sa", "resolved")
