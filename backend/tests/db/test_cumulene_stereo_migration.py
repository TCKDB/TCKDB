"""Allene ``Ra``/``Sa`` and odd-cumulene ``E``/``Z`` in the entry stereo descriptor (#827): the fill step and the constraint."""

from __future__ import annotations

from uuid import uuid4

import pytest
from rdkit import Chem
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.chemistry import stereo
from app.chemistry.species import identity_mol_from_smiles
from app.services import cumulene_stereo_fill
from tests.services import _cumulene_fixtures as fx


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


def test_a_fill_that_collides_with_a_live_entry_stops_the_migration_with_a_list(db_session, monkeypatch) -> None:
    allene = _species(db_session, "CC=C=CC")
    open_entry, open_ref = _entry(db_session, allene, "CC=C=CC", "?")
    _geometry(db_session, open_entry, fx.build("CC=C=CC", -90.0))
    # Only an entry written after the swap can already hold Ra: the key differs by descriptor, so both are live.
    _holder, holder_ref = _entry(db_session, allene, "CC=C=CC", "Ra", "resolved")
    with pytest.raises(RuntimeError) as excinfo:
        cumulene_stereo_fill.fill_cumulene_stereo(db_session.connection(), revision="test")
    message = str(excinfo.value)
    assert open_ref in message and holder_ref in message and "never merges" in message
    assert _state(db_session, open_entry)[0] == "?"  # nothing was written


def test_entries_of_one_species_that_differ_in_state_do_not_collide(db_session) -> None:
    allene = _species(db_session, "CC=C=CC")
    first, _ = _entry(db_session, allene, "CC=C=CC", "?")
    second, _ = _entry(db_session, allene, "CC=C=CC", "?", term_symbol="T")
    for entry in (first, second):
        _geometry(db_session, entry, fx.build("CC=C=CC", -90.0))
    report = cumulene_stereo_fill.fill_cumulene_stereo(db_session.connection(), revision="test")
    assert len(report.changes) == 2
    assert _state(db_session, first)[0] == _state(db_session, second)[0] == "Ra"


RING = "CC1C=C=CCCCCC=C=C1"


def _embedded(smiles: str, seed: int = 3) -> Chem.Mol:
    from rdkit.Chem import AllChem

    mol = Chem.AddHs(Chem.MolFromSmiles(smiles))
    AllChem.EmbedMolecule(mol, randomSeed=seed)
    return mol


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


SPIRO_RINGS = "C12(C=C=CCCCC=C=C1)C=C=CCCCC=C=C2"


BOND_CASE = "CC=C=CC(C=C=CC)=C(C(C)O)C(C)O"


TIED_END_ROWS = {
    "allenyl arms": ("CC=C=CC(C=C=CC)=C=C(C)Cl", "?,?"),
    "carbinol arms (letters, no ?)": ("CC(O)C(C(C)O)=C=C(C)Cl", "R,S"),
    "carbinol arms on a butatriene": ("CC(O)C(C(C)O)=C=C=C(C)Cl", "R,S"),
}
