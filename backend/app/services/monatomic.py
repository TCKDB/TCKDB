"""Decide whether an uploaded subject is a single atom, from real evidence.

One question, asked in several places: does this statmech describe a species
that has vibrational modes? A single atom does not, so a missing ``freq``
source is expected for it and noise for everyone else.

The answer used to be "no rotational constants and no torsions, so an
atom". That is a statement about what the depositor chose to *send*, not
about the species: on the real ARC run fixtures 26 of 45 polyatomic statmech
blocks carry neither, and every one of them was treated as an atom (#608).

Evidence, strongest first
-------------------------
1. **Geometry atom count.** A bundle carries the xyz it deposited. One atom
   is a single atom; more than one is not. This cannot be wrong on a
   well-formed upload.
2. **Species identity.** The SMILES with hydrogens made explicit has one
   atom (``[O]``, ``[Cl]``, ``[H]``) or several (``[OH]``, ``O``, ``C``).
   Identity is evidence; it outranks a declaration.
3. **``rigid_rotor_kind``.** A depositor's claim, used only when neither a
   geometry nor an identity is available: ``atom`` says monatomic, any other
   kind says a rotor, which needs at least two atoms.
4. **The old heuristic**, only when nothing above is available: rotational
   constants or torsions present means polyatomic; their absence is *not*
   evidence of anything and yields "unknown", which callers treat as
   "do not warn".

``None`` from :func:`subject_atom_count` means unknown, and is never
collapsed to "monatomic" or "polyatomic" by this module.
"""

from __future__ import annotations

from collections.abc import Iterable

from tckdb_schemas.fragments.geometry import GeometryPayload
from tckdb_schemas.frequency_completeness import atom_count_of_xyz

from app.chemistry.geometry import parse_xyz
from app.chemistry.species import element_counts_from_smiles
from app.db.models.common import RigidRotorKind

__all__ = [
    "single_atom_element",
    "statmech_subject_is_polyatomic",
    "subject_atom_count",
]


def _xyz_element(xyz_text: str) -> str | None:
    try:
        atoms = parse_xyz(GeometryPayload(xyz_text=xyz_text)).atoms
    except Exception:
        return None
    return atoms[0][0] if len(atoms) == 1 else None


def _smiles_counts(smiles: str | None) -> dict[str, int] | None:
    if not smiles:
        return None
    try:
        return dict(element_counts_from_smiles(smiles))
    except ValueError:
        return None


def subject_atom_count(
    *,
    xyz_texts: Iterable[str] = (),
    smiles: str | None = None,
) -> int | None:
    """Atoms in the subject, from a geometry first and the identity second."""
    for xyz in xyz_texts:
        n = atom_count_of_xyz(xyz)
        if n is not None:
            return n
    counts = _smiles_counts(smiles)
    if counts is not None:
        return sum(counts.values())
    return None


def single_atom_element(
    *,
    xyz_texts: Iterable[str] = (),
    smiles: str | None = None,
) -> str | None:
    """The element symbol when the subject is one atom, else ``None``."""
    for xyz in xyz_texts:
        el = _xyz_element(xyz)
        if el is not None:
            return el
        if atom_count_of_xyz(xyz) is not None:
            return None
    counts = _smiles_counts(smiles)
    if counts is not None and sum(counts.values()) == 1:
        return next(iter(counts))
    return None


def statmech_subject_is_polyatomic(
    statmech,
    *,
    xyz_texts: Iterable[str] = (),
    smiles: str | None = None,
) -> bool:
    """True when the statmech's subject is known to have internal structure.

    Returns ``False`` both for a single atom and for "cannot tell", which is
    the right bias for a warning that must not fire on every atom in every
    deposit. The fallback heuristic (constants or torsions present) applies
    only when neither a geometry, ``rigid_rotor_kind`` nor an identity
    SMILES answers; that fallback can say "polyatomic" but its silence is
    not a claim that the subject is an atom.
    """
    n = subject_atom_count(xyz_texts=xyz_texts)
    if n is not None:
        return n > 1
    n = subject_atom_count(smiles=smiles)
    if n is not None:
        return n > 1
    kind = getattr(statmech, "rigid_rotor_kind", None)
    if kind is not None:
        return RigidRotorKind(kind) != RigidRotorKind.atom
    return (
        getattr(statmech, "rotational_constant_a_cm1", None) is not None
        or getattr(statmech, "rotational_constant_b_cm1", None) is not None
        or getattr(statmech, "rotational_constant_c_cm1", None) is not None
        or bool(getattr(statmech, "torsions", ()))
    )
