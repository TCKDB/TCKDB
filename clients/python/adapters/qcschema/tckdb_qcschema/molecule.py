"""QCSchema ``Molecule`` -> TCKDB geometry and identity (C-Q1).

Bohr-to-angstrom conversion, atom-resolved isotope labelling, ghost/fragment
refusal, integer charge/multiplicity, and SMILES identity resolution with no
3D perception anywhere in this module.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import qcelemental as qcel

from tckdb_schemas.fragments.geometry import GeometryPayload

from .errors import (
    E_GHOST_ATOMS_UNSUPPORTED,
    E_IDENTITY_UNAVAILABLE,
    E_MULTI_FRAGMENT_UNSUPPORTED,
    E_NON_INTEGER_IDENTITY,
    E_NONSTANDARD_MASS,
    QCSchemaAdapterError,
)

#: CODATA bohr radius in Angstrom, identical to
#: ``backend/app/services/hessian_parsing.py:69`` (the constant already
#: governing every other Hessian-adjacent geometry TCKDB stores). Measured
#: 2026-09-19 against the pinned qcelemental==0.51.2 release:
#: ``qcelemental.constants.bohr2angstroms == 0.52917721067`` -- bit-identical
#: to this literal at float64 precision (``decimal.Decimal`` of both prints
#: the same 53-significant-bit expansion). The plan anticipated a ~4.4e-10
#: relative divergence between a CODATA-2014 and a qcelemental CODATA-2018
#: value; that divergence was not reproducible against the version actually
#: pinned here, so this adapter uses the literal (not
#: ``qcelemental.constants.bohr2angstroms``) to stay byte-identical to the
#: backend constant regardless of a future qcelemental bump, and records the
#: measurement rather than the anticipated number.
BOHR_TO_ANGSTROM = 0.52917721067

#: Absolute tolerance, in amu, for comparing a declared atomic mass against
#: qcelemental's tabulated mass for the declared nuclide.
_MASS_TOLERANCE_AMU = 1e-6

_periodic_table = qcel.periodictable


@dataclass(frozen=True)
class ResolvedIdentity:
    smiles: str
    source: str  # "depositor_declared" | "identifiers_smiles"
    charge: int
    multiplicity: int


def _require_integer(value: float, *, field: str) -> int:
    rounded = round(value)
    if abs(value - rounded) > 1e-9:
        raise QCSchemaAdapterError(
            E_NON_INTEGER_IDENTITY,
            f"molecule.{field}={value!r} is not integral. QCSchema types "
            f"{field} as a float; TCKDB species identity requires an "
            f"integer and does not round a fractional value.",
            field=field,
            value=value,
        )
    return int(rounded)


def check_no_ghost_atoms(molecule) -> None:
    real = list(molecule.real)
    if not all(real):
        ghost_indices = [i for i, r in enumerate(real) if not r]
        raise QCSchemaAdapterError(
            E_GHOST_ATOMS_UNSUPPORTED,
            f"molecule has {len(ghost_indices)} ghost atom(s) "
            f"(real=false) at 0-based index(es) {ghost_indices}; TCKDB "
            f"has no ghost/dummy-atom representation.",
            ghost_indices=ghost_indices,
        )


def check_single_fragment(molecule) -> None:
    fragments = list(molecule.fragments)
    if len(fragments) > 1:
        raise QCSchemaAdapterError(
            E_MULTI_FRAGMENT_UNSUPPORTED,
            f"molecule has {len(fragments)} fragments; only a single-"
            f"fragment molecule maps onto one TCKDB species entry.",
            fragment_count=len(fragments),
        )


def build_isotope_map(molecule) -> dict[int, int] | None:
    """1-based XYZ atom index -> isotope mass number, non-standard atoms only.

    Also refuses ``nonstandard_mass`` when a declared mass does not match
    qcelemental's tabulated mass for the declared (symbol, mass_number)
    nuclide within :data:`_MASS_TOLERANCE_AMU` -- this is checked for
    *every* atom, standard or not, because a corrupted/garbage mass on an
    otherwise-standard mass number is exactly as wrong as one on a
    deliberately isotopic atom.
    """
    symbols = list(molecule.symbols)
    mass_numbers = list(molecule.mass_numbers)
    masses = list(molecule.masses)

    isotopes: dict[int, int] = {}
    for i, (symbol, mass_number, mass) in enumerate(
        zip(symbols, mass_numbers, masses)
    ):
        nuclide = f"{symbol}{int(mass_number)}"
        try:
            tabulated = float(_periodic_table.to_mass(nuclide))
        except Exception as exc:  # qcelemental raises its own NotAnElementError
            raise QCSchemaAdapterError(
                E_NONSTANDARD_MASS,
                f"atom {i} (1-based {i + 1}): {nuclide!r} is not a nuclide "
                f"qcelemental's periodic table recognises: {exc}",
                atom_index=i,
                symbol=symbol,
                mass_number=int(mass_number),
            ) from exc
        if abs(float(mass) - tabulated) > _MASS_TOLERANCE_AMU:
            raise QCSchemaAdapterError(
                E_NONSTANDARD_MASS,
                f"atom {i} (1-based {i + 1}, {symbol}): declared mass "
                f"{mass!r} does not match qcelemental's tabulated mass "
                f"{tabulated!r} for nuclide {nuclide!r} (tolerance "
                f"{_MASS_TOLERANCE_AMU} amu).",
                atom_index=i,
                symbol=symbol,
                mass_number=int(mass_number),
                declared_mass=float(mass),
                tabulated_mass=tabulated,
            )
        standard_mass_number = _periodic_table.to_A(symbol)
        if int(mass_number) != standard_mass_number:
            isotopes[i + 1] = int(mass_number)

    return isotopes or None


def _flatten(values) -> list[float]:
    """qcelemental arrays come back as numpy (often nested); flatten to floats."""
    return [float(v) for v in np.asarray(values, dtype=float).reshape(-1)]


def to_geometry_payload(molecule) -> GeometryPayload:
    """QCSchema ``Molecule`` (geometry in bohr) -> ``GeometryPayload`` (Å).

    Refuses ``ghost_atoms_unsupported`` and ``multi_fragment_unsupported``
    before doing any conversion. Coordinates are rendered to ten decimal
    places, matching the plan's stated precision.
    """
    check_no_ghost_atoms(molecule)
    check_single_fragment(molecule)

    symbols = list(molecule.symbols)
    geometry_bohr = [float(v) for v in _flatten(molecule.geometry)]
    natoms = len(symbols)

    lines = [str(natoms), ""]
    for i in range(natoms):
        x, y, z = (geometry_bohr[3 * i + k] * BOHR_TO_ANGSTROM for k in range(3))
        lines.append(f"{symbols[i]} {x:.10f} {y:.10f} {z:.10f}")
    xyz_text = "\n".join(lines) + "\n"

    isotopes = build_isotope_map(molecule)
    return GeometryPayload(xyz_text=xyz_text, isotopes=isotopes)


def resolve_identity(molecule, *, declared_smiles: str | None) -> ResolvedIdentity:
    """SMILES from ``--smiles`` else ``identifiers.smiles``; never perceived.

    :param declared_smiles: The CLI's ``--smiles`` value, if given.
    :raises QCSchemaAdapterError: ``identity_unavailable`` when neither
        source names a SMILES, or ``non_integer_identity`` when
        ``molecular_charge``/``molecular_multiplicity`` is not integral.
    """
    if declared_smiles:
        smiles, source = declared_smiles, "depositor_declared"
    else:
        identifiers_smiles = getattr(molecule.identifiers, "smiles", None) if (
            molecule.identifiers is not None
        ) else None
        if identifiers_smiles:
            smiles, source = identifiers_smiles, "identifiers_smiles"
        else:
            raise QCSchemaAdapterError(
                E_IDENTITY_UNAVAILABLE,
                "no graph identity available: --smiles was not given and "
                "molecule.identifiers.smiles is absent. TCKDB never "
                "perceives a SMILES from 3D coordinates.",
            )

    charge = _require_integer(float(molecule.molecular_charge), field="molecular_charge")
    multiplicity = _require_integer(
        float(molecule.molecular_multiplicity), field="molecular_multiplicity"
    )
    return ResolvedIdentity(
        smiles=smiles, source=source, charge=charge, multiplicity=multiplicity
    )


__all__ = [
    "BOHR_TO_ANGSTROM",
    "ResolvedIdentity",
    "check_no_ghost_atoms",
    "check_single_fragment",
    "build_isotope_map",
    "to_geometry_payload",
    "resolve_identity",
]
