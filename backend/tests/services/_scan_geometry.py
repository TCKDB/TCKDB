"""Shared, sign-independent geometry construction for scan-conformance tests.

Used by both ``tests/services/test_scan_coordinate_conformance.py`` and
``tests/scripts/test_scan_coordinate_conformance_report.py`` so there is
exactly one NeRF placement formula in the test tree, not two copies that
can drift -- which is how the sign bug this module exists to prevent a
repeat of was absorbed the first time: a second, independent-looking copy
of the same helper, in the second test file, silently carried the same
"fix".

``place_next_atom`` implements the textbook NeRF ("natural extension
reference frame") placement (Parsons et al., *J. Comput. Chem.* 2005) with
**no adjustable sign of its own** -- no parameter here is chosen to agree
with ``app.services.scan_coordinate_conformance.dihedral_deg``. Its
correctness is checked directly against RDKit
(``TestPlacementHelperAgreesWithRDKit`` in
``test_scan_coordinate_conformance.py``), never against the module under
test. That is the fix for the actual defect a review found here: an
earlier version of this file negated the dihedral it asked NeRF to place
at, commented "measured empirically against dihedral_deg below, not
assumed" -- which described exactly the failure mode the module's own
class docstring warned against (the ground-truth builder validating the
module against itself) while doing exactly that. ``dihedral_deg`` had the
sign backwards; this helper was quietly edited to agree with it instead of
being checked against anything external. Both are now pinned to RDKit
independently, so a future sign flip in either one is caught by the
consistency test between them, and a sign flip in *both* -- the actual
prior failure -- is caught because neither one's correctness is asserted
by the other any more.
"""

from __future__ import annotations

import hashlib
import itertools
import math

import numpy as np


def place_next_atom(
    a: np.ndarray,
    b: np.ndarray,
    c: np.ndarray,
    *,
    bond_length: float,
    bond_angle_deg: float,
    dihedral_deg: float,
) -> np.ndarray:
    """Place atom D such that the IUPAC/RDKit dihedral(a, b, c, d) == dihedral_deg.

    Standard NeRF placement, unmodified from the published formula. See the
    module docstring for why this carries no sign adjustment of its own.
    """
    theta = math.radians(bond_angle_deg)
    phi = math.radians(dihedral_deg)
    bc = c - b
    bc_hat = bc / np.linalg.norm(bc)
    ab = b - a
    n = np.cross(ab, bc_hat)
    n_hat = n / np.linalg.norm(n)
    m = np.cross(n_hat, bc_hat)
    local = np.array(
        [
            -bond_length * math.cos(theta),
            bond_length * math.sin(theta) * math.cos(phi),
            bond_length * math.sin(theta) * math.sin(phi),
        ]
    )
    basis = np.column_stack([bc_hat, m, n_hat])
    return c + basis @ local


def rdkit_dihedral_deg(a: np.ndarray, b: np.ndarray, c: np.ndarray, d: np.ndarray) -> float:
    """The dihedral a-b-c-d, measured by RDKit -- the external reference.

    Builds a throwaway 4-carbon chain at the given coordinates and asks
    RDKit for its own answer. Used only to check
    :func:`app.services.scan_coordinate_conformance.dihedral_deg` and
    :func:`place_next_atom` against an authority outside this repository;
    production code never imports this.
    """
    from rdkit import Chem
    from rdkit.Chem import rdMolTransforms

    mol = Chem.RWMol()
    for _ in range(4):
        mol.AddAtom(Chem.Atom(6))
    conformer = Chem.Conformer(4)
    for i, point in enumerate((a, b, c, d)):
        conformer.SetAtomPosition(i, [float(x) for x in point])
    mol.AddConformer(conformer)
    mol.AddBond(0, 1, Chem.BondType.SINGLE)
    mol.AddBond(1, 2, Chem.BondType.SINGLE)
    mol.AddBond(2, 3, Chem.BondType.SINGLE)
    rdmol = mol.GetMol()
    Chem.SanitizeMol(rdmol)
    return rdMolTransforms.GetDihedralDeg(rdmol.GetConformer(), 0, 1, 2, 3)


_geom_hash_counter = itertools.count()


def next_geom_hash(label: str = "scan-conformance-test") -> str:
    """A fresh, deterministic 64-hex-char ``geom_hash`` for a throwaway ``Geometry`` row."""
    return hashlib.sha256(f"{label}-{next(_geom_hash_counter)}".encode()).hexdigest()
