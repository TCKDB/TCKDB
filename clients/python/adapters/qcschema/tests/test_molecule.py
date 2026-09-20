"""Unit tests for molecule.py: bohr->angstrom, isotopes, identity (C-Q1)."""

from __future__ import annotations

import pytest
import qcelemental as qcel
import qcelemental.models.v1 as qcel_v1

from tckdb_qcschema.errors import QCSchemaAdapterError
from tckdb_qcschema.molecule import (
    BOHR_TO_ANGSTROM,
    build_isotope_map,
    check_no_ghost_atoms,
    check_single_fragment,
    resolve_identity,
    to_geometry_payload,
)


def _single_atom_molecule(bohr_xyz=(1.0, 2.0, 3.0), symbol="He"):
    x, y, z = bohr_xyz
    return qcel_v1.Molecule(
        symbols=[symbol],
        geometry=[x, y, z],
        molecular_charge=0,
        molecular_multiplicity=1,
        fix_com=True,
        fix_orientation=True,
    )


def test_bohr_to_angstrom_constant_matches_hessian_parsing_literal():
    # Same literal as backend/app/services/hessian_parsing.py:69 -- pinned
    # exactly, not derived from qcelemental.constants at import time, so a
    # future qcelemental bump cannot silently move TCKDB's own stored
    # geometries.
    assert BOHR_TO_ANGSTROM == 0.52917721067


def test_geometry_conversion_uses_the_exact_bohr_constant():
    mol = _single_atom_molecule(bohr_xyz=(1.0, 2.0, 3.0))
    payload = to_geometry_payload(mol)
    lines = payload.xyz_text.strip().splitlines()
    assert lines[0] == "1"
    _symbol, x, y, z = lines[2].split()
    assert float(x) == pytest.approx(1.0 * BOHR_TO_ANGSTROM, abs=1e-10)
    assert float(y) == pytest.approx(2.0 * BOHR_TO_ANGSTROM, abs=1e-10)
    assert float(z) == pytest.approx(3.0 * BOHR_TO_ANGSTROM, abs=1e-10)
    # Ten decimal places, as the plan specifies.
    assert len(x.split(".")[-1]) == 10


def test_ghost_atom_refuses_ghost_atoms_unsupported():
    mol = qcel_v1.Molecule(
        symbols=["He", "He"],
        geometry=[0, 0, 0, 0, 0, 5],
        molecular_charge=0,
        molecular_multiplicity=1,
        real=[False, True],
        fix_com=True,
        fix_orientation=True,
    )
    with pytest.raises(QCSchemaAdapterError) as excinfo:
        check_no_ghost_atoms(mol)
    assert excinfo.value.code == "ghost_atoms_unsupported"


def test_all_real_atoms_pass():
    mol = _single_atom_molecule()
    check_no_ghost_atoms(mol)  # must not raise


def test_single_fragment_passes_multi_fragment_check():
    mol = _single_atom_molecule()
    check_single_fragment(mol)  # must not raise


def test_isotope_map_only_records_non_standard_mass_numbers():
    # Deuterium (mass_number=2) alongside ordinary H (mass_number=1).
    mol = qcel_v1.Molecule(
        symbols=["H", "H"],
        geometry=[0, 0, 0, 0, 0, 1.5],
        molecular_charge=0,
        molecular_multiplicity=1,
        mass_numbers=[2, 1],
        masses=[float(qcel.periodictable.to_mass("H2")), float(qcel.periodictable.to_mass("H1"))],
        fix_com=True,
        fix_orientation=True,
    )
    isotopes = build_isotope_map(mol)
    assert isotopes == {1: 2}  # 1-based index 1 (the deuterium) only


def test_nonstandard_declared_mass_refuses():
    # qcelemental's own direct-construction path (Molecule(**kwargs))
    # cross-checks A/Z/E/mass consistency itself and would refuse this
    # before build_isotope_map ever saw it -- which is not the path a
    # real uploaded document takes. A real document round-trips through
    # JSON (json.loads then Molecule.parse_obj), which does not re-run
    # that physical-consistency reconciliation; this constructs the same
    # way so the corrupted mass survives to reach build_isotope_map,
    # exactly like the corpus's `nonstandard_mass` fixture.
    good = qcel_v1.Molecule(
        symbols=["O"],
        geometry=[0, 0, 0],
        molecular_charge=0,
        molecular_multiplicity=1,
        fix_com=True,
        fix_orientation=True,
    )
    tampered = good.dict()
    tampered["masses"] = [15.0]  # not O-16's tabulated mass
    mol = qcel_v1.Molecule.parse_obj(tampered)

    with pytest.raises(QCSchemaAdapterError) as excinfo:
        build_isotope_map(mol)
    assert excinfo.value.code == "nonstandard_mass"


def test_resolve_identity_prefers_declared_smiles():
    mol = _single_atom_molecule()
    identity = resolve_identity(mol, declared_smiles="[He]")
    assert identity.smiles == "[He]"
    assert identity.source == "depositor_declared"
    assert identity.charge == 0
    assert identity.multiplicity == 1


def test_resolve_identity_falls_back_to_identifiers_smiles():
    mol = qcel_v1.Molecule(
        symbols=["He"],
        geometry=[0, 0, 0],
        molecular_charge=0,
        molecular_multiplicity=1,
        identifiers={"smiles": "[He]"},
        fix_com=True,
        fix_orientation=True,
    )
    identity = resolve_identity(mol, declared_smiles=None)
    assert identity.smiles == "[He]"
    assert identity.source == "identifiers_smiles"


def test_resolve_identity_refuses_identity_unavailable_never_perceives():
    mol = _single_atom_molecule()  # no identifiers.smiles
    with pytest.raises(QCSchemaAdapterError) as excinfo:
        resolve_identity(mol, declared_smiles=None)
    assert excinfo.value.code == "identity_unavailable"


def test_resolve_identity_refuses_non_integer_multiplicity():
    # QCSchema types molecular_multiplicity as a float (measured against
    # the pinned Molecule model), so a genuinely fractional value is a
    # value the schema itself permits -- TCKDB's own integer requirement
    # is what refuses it here, not qcelemental.
    mol = qcel_v1.Molecule(
        symbols=["He"],
        geometry=[0, 0, 0],
        molecular_charge=0,
        molecular_multiplicity=2.5,
        fix_com=True,
        fix_orientation=True,
    )
    with pytest.raises(QCSchemaAdapterError) as excinfo:
        resolve_identity(mol, declared_smiles="[He]")
    assert excinfo.value.code == "non_integer_identity"
