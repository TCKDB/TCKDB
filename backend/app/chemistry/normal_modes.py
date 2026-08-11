"""Normal-mode analysis recovered from a stored Cartesian Hessian.

Pure numerics. Coordinates, masses and a packed force-constant matrix in;
mass-weighted eigenpairs and their projections onto rigid-body and
torsional motion out. No database access, nothing persisted.

Why this module exists
----------------------

ADR 0012 asks for two projections on every imaginary mode -- onto the six
rigid-body vectors, and onto a dihedral rotation about each rotatable
bond -- because "a determination beats a threshold wherever one is
available". ADR 0013 deferred them on the grounds that TCKDB stores no
displacement vectors, so there was nothing to project.

That premise is false. ``calc_hessian`` stores the packed lower triangle
of the full symmetric 3N x 3N Cartesian force-constant matrix, in fixed
units of hartree/bohr^2, bound to a mandatory ``geometry_id``. Mass-weight
it with the per-atom masses that ``geometry_atom.element`` and
``geometry_atom.isotope_mass_number`` already determine, diagonalise, and
the eigenvectors are the displacements ADR 0012 wants. The frequency list
comes back with it, which is what makes the recovery checkable: a
recovered spectrum that does not reproduce the stored one is a recovery
this module refuses to project.

Nothing here is stored. ADR 0013 itself observes that "it is the
projection that is the inference, not the vector"; computing the
projection at read time and storing neither is the reading of that
sentence which adds a determination without adding an inference to the
database.

Conventions
-----------

* Coordinates are Angstrom, masses amu, force constants hartree/bohr^2 --
  the units the geometry and Hessian tables already hold. No conversion
  happens on the way in.
* Eigenvectors are returned in **mass-weighted** coordinates
  (q_i = sqrt(m_i) * x_i), unit-normalised. Every projection in this
  module is an inner product in that metric, which is the metric in which
  the rigid-body vectors are mutually orthogonal and in which "fraction of
  the mode" is a meaningful quantity.
* Overlaps are returned as a **fraction of squared norm** in [0, 1], so
  the overlap onto a subspace is the sum of the per-vector overlaps and
  the numbers add up the way a reader expects.
"""

from __future__ import annotations

import math
from collections import deque
from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum

import numpy as np
from rdkit import Chem

__all__ = [
    "BOND_PERCEPTION_TOLERANCE",
    "DEGENERACY_WINDOW_CM1",
    "FRAME_CONSISTENCY_TOLERANCE_CM1",
    "LINEARITY_SINGULAR_VALUE_TOLERANCE",
    "RIGID_BODY_OVERLAP_THRESHOLD",
    "TORSION_OVERLAP_THRESHOLD",
    "ModeDetermination",
    "ModeProjection",
    "NormalMode",
    "RigidBodySubspace",
    "SpectrumMatch",
    "TorsionAxis",
    "atomic_mass",
    "match_stored_frequency",
    "perceive_bonds",
    "project_mode",
    "rigid_body_curvature_cm1",
    "rigid_body_subspace",
    "rotatable_bonds",
    "solve_normal_modes",
    "torsion_axes",
    "unpack_lower_triangle",
]

# CODATA 2018, matching the constants the rest of the backend quotes.
_HARTREE_J = 4.3597447222071e-18
_BOHR_M = 5.29177210903e-11
_AMU_KG = 1.66053906660e-27
_C_CM_S = 2.99792458e10
_BOHR_ANGSTROM = 0.529177210903

#: hartree/bohr^2/amu -> (rad/s)^2. A mass-weighted eigenvalue times this
#: is omega^2 in SI; the wavenumber is sqrt(|omega^2|) / (2 pi c).
_EIGENVALUE_TO_OMEGA2_SI = _HARTREE_J / (_BOHR_M**2 * _AMU_KG)

#: Two atoms are bonded when their separation is within this multiple of
#: the sum of their covalent radii. Bond *orders* are deliberately not
#: determined -- see :func:`rotatable_bonds`.
#:
#: 1.25 is the usual distance-perception factor and is chosen here for a
#: specific reason: a transition state carries a partially formed or
#: partially broken bond, and RDKit's valence-based ``rdDetermineBonds``
#: (used elsewhere in :mod:`app.chemistry.torsion_fingerprint`) is known
#: in this codebase to be fragile exactly there. Distance perception has
#: no valence model to be wrong about.
BOND_PERCEPTION_TOLERANCE = 1.25

#: Relative tolerance on the singular values of the six raw rigid-body
#: vectors. A rotation generator about a molecular axis is the **exact**
#: zero vector for a collinear geometry, so the rank of that set is 5 for
#: a linear molecule and 6 otherwise. The singular values of the rotation
#: block are the square roots of the principal moments of inertia, so this
#: is the standard linearity test wearing different clothes.
LINEARITY_SINGULAR_VALUE_TOLERANCE = 1e-6

#: A recovered frequency must land within this many cm-1 of the stored one
#: -- or within :data:`SPECTRUM_MATCH_RELATIVE_TOLERANCE` of it, whichever
#: is looser -- for the recovered eigenvector to be attached to the stored
#: mode. Wider than that and the Hessian on file is not the matrix the
#: stored frequency list came out of, and projecting it would answer a
#: question about a different calculation.
#:
#: The relative figure is set by a real and unavoidable discrepancy rather
#: than by numerical noise: programs disagree about what an atom weighs.
#: TCKDB's own convention (``geometry_atom.isotope_mass_number IS NULL``
#: means the most abundant natural isotope) is what this module applies,
#: and it is the right one, but ORCA's ``.hess`` fixture in this repo
#: carries *average* atomic weights (Cl 35.453 against the isotopic
#: 34.96885). For a mode dominated by that atom the two conventions differ
#: by roughly half the mass difference -- measured at 0.30% on the C-Cl
#: stretch of ``Orca_TS_test.hess``, against 0.01-0.08% for the
#: isotopic-mass Gaussian and Molpro fixtures. 1% leaves room for the
#: heavier halogens without being wide enough to reach a neighbouring
#: fundamental.
SPECTRUM_MATCH_ABSOLUTE_TOLERANCE_CM1 = 2.0
SPECTRUM_MATCH_RELATIVE_TOLERANCE = 0.01

#: Two recovered modes closer than this are treated as degenerate, and a
#: stored frequency matching into such a cluster gets no projection.
#: Within a degenerate subspace the individual eigenvector is arbitrary --
#: any rotation of the pair diagonalises the Hessian equally well -- so a
#: per-mode overlap is a property of the diagonaliser, not of the
#: molecule. Only the subspace total is well defined, and that is not what
#: ADR 0012 asks for. Refusing is the honest answer.
#:
#: The effect is visible in ``Orca_TS_test.hess``: its 173.47/173.53 and
#: 3186.99/3187.08 pairs agree with ORCA's own eigenvectors to
#: ``|cos| = 0.96`` and ``0.26``, and ``1.00`` and ``0.00`` respectively --
#: not because either diagonalisation is wrong, but because the pair is
#: degenerate and the split between its members is not determined.
DEGENERACY_WINDOW_CM1 = 1.0

#: Largest rigid-body curvature, as a signed wavenumber, that still allows
#: the projections to be computed. Translation and rotation are null
#: directions of an exact Hessian at a stationary point, so a large
#: curvature along one means either the geometry on file is not the frame
#: the Hessian was computed in, or the geometry is far from stationary.
#: Either way the rigid-body subspace this module projects onto is not the
#: one the matrix knows about, and every overlap below would be quietly
#: wrong rather than loudly absent.
#:
#: This is not a hypothetical. Gaussian prints its force constants in one
#: orientation and its geometry in two, and taking the wrong one is
#: silent: on ``freq_g09.log`` the six rigid-body modes' overlaps fall
#: from 0.9985-1.0000 to 0.44-1.00 and the 110 cm-1 torsion's overlap
#: collapses from 0.986 to 0.044 -- a plausible-looking answer to the
#: wrong question. Measured maxima:
#:
#: ===========================================  ==============
#: case                                         max curvature
#: ===========================================  ==============
#: Gaussian ``freq_g09.log``, input orientation       12.8
#: Molpro ``molpro_TS_freq.out``, as printed           0.8
#: ORCA ``Orca_TS_test.hess``, as printed             49.4
#: Gaussian, *standard* orientation (wrong)          792.9
#: Molpro, rotated 30 degrees (wrong)                760.3
#: ORCA, rotated 30 degrees (wrong)                  419.5
#: ===========================================  ==============
#:
#: 100 sits in an empty interval with a factor of two below it and four
#: above. A correct frame at a badly unconverged geometry can also exceed
#: it, and refusing there is right for the same reason.
FRAME_CONSISTENCY_TOLERANCE_CM1 = 100.0

#: ADR 0012: "more than about 90% overlap means the mode is projection
#: residue and nothing else".
#:
#: Kept at ADR 0012's figure because the measurement says it is not a
#: tuned parameter. Across the three real ESS Hessians in
#: ``backend/tests/fixtures`` -- 51 modes, 18 of them rigid-body
#: directions -- every rigid-body direction scores 0.9985 or above and no
#: vibration scores above 0.0022. The threshold names a point inside an
#: empty interval three orders of magnitude wide, so moving it anywhere
#: between 0.01 and 0.99 would change no answer.
RIGID_BODY_OVERLAP_THRESHOLD = 0.90

#: ADR 0012: "more than about 70% identifies a torsion".
#:
#: Also kept, on thinner evidence, and the thinness is the finding. Only
#: one fixture has rotatable bonds at all (``freq_g09.log``, a 12-atom
#: N-N-C-C-C chain): its 110 cm-1 mode is 0.986 a rotation about the C-C
#: bond, the next most torsional mode is 0.472, and everything above
#: 675 cm-1 is below 0.10. 0.70 separates them by a factor of two either
#: way, which is enough to keep ADR 0012's number and not enough to
#: justify replacing it with a different one. The overlap itself is
#: reported alongside every determination so a reader can apply their own.
TORSION_OVERLAP_THRESHOLD = 0.70


class ModeDetermination(str, Enum):
    """What the projections say a mode is.

    Deliberately **not**
    :class:`~app.db.models.common.ImaginaryModeDisposition`. That enum is
    the depositor's declared vocabulary and has six values; the
    projections can positively identify two of them and can otherwise only
    say "neither of those". Reusing the declared enum would claim a
    resolution the measurement does not have.
    """

    #: Overlap with the rigid-body subspace at or above the residue
    #: threshold: the "mode" is translation and rotation leaking through
    #: an imperfect projection, and is not a vibration at all.
    rigid_body_residue = "rigid_body_residue"

    #: Overlap with a dihedral rotation about one perceived rotatable bond
    #: at or above the torsion threshold.
    torsion = "torsion"

    #: Neither. A positive statement that the mode is internal motion that
    #: is not a rotation about any acyclic bond -- which is what a genuine
    #: reaction coordinate looks like -- but *not* a claim about which
    #: internal coordinate it is. Ring puckers, intermolecular modes and
    #: symmetry-breaking modes all land here, undistinguished.
    internal_vibration = "internal_vibration"


@dataclass(frozen=True)
class NormalMode:
    """One eigenpair of the mass-weighted Cartesian Hessian.

    :param frequency_cm1: Wavenumber, **negative** for an imaginary mode,
        matching how ``calc_freq_mode.frequency_cm1`` stores them.
    :param displacement: Mass-weighted eigenvector, unit norm, length 3N.
    """

    frequency_cm1: float
    displacement: np.ndarray


@dataclass(frozen=True)
class RigidBodySubspace:
    """Orthonormal basis for rigid-body motion, in mass-weighted coordinates.

    :param basis: ``(k, 3N)`` array, ``k`` = 5 (linear) or 6.
    :param is_linear: Whether the geometry is collinear.

    A single atom is the third case and gets ``dimension == 3`` with
    ``is_linear`` false: it has three translations and no rotations at
    all, and calling that "linear" would be a stretch. ``dimension`` is
    the honest number in every case, which is why it is what the read
    surface reports.
    """

    basis: np.ndarray
    is_linear: bool

    @property
    def dimension(self) -> int:
        return int(self.basis.shape[0])


@dataclass(frozen=True)
class TorsionAxis:
    """A dihedral rotation about one rotatable bond.

    :param atom_index_a: 1-based index of the bond's first atom, matching
        ``geometry_atom.atom_index``.
    :param atom_index_b: 1-based index of the bond's second atom.
    :param vector: Mass-weighted unit vector, orthogonal to every
        rigid-body direction.
    """

    atom_index_a: int
    atom_index_b: int
    vector: np.ndarray


@dataclass(frozen=True)
class ModeProjection:
    """The ADR 0012 projections for one mode.

    :param rigid_body_overlap: Fraction of the mode's squared norm lying in
        the rigid-body subspace, in [0, 1].
    :param torsion_overlap: The largest single-bond torsional overlap, in
        [0, 1]. ``0.0`` when the geometry has no rotatable bond.
    :param torsion_subspace_overlap: Fraction lying in the span of *all*
        torsion vectors. Never smaller than ``torsion_overlap``; reported
        because a torsion delocalised over two adjacent bonds is a real
        shape a single-bond number understates.
    :param best_torsion_bond: The bond that achieved ``torsion_overlap``,
        as 1-based atom indices, or ``None``.
    :param determination: The classification the overlaps support.
    """

    rigid_body_overlap: float
    torsion_overlap: float
    torsion_subspace_overlap: float
    best_torsion_bond: tuple[int, int] | None
    determination: ModeDetermination


@dataclass(frozen=True)
class SpectrumMatch:
    """A stored frequency paired with the recovered mode that reproduces it.

    :param stored_frequency_cm1: The frequency as stored.
    :param recovered_frequency_cm1: The frequency recovered from the Hessian,
        or ``None`` when the recovered spectrum was empty.
    :param mode: The recovered eigenpair, or ``None`` when nothing in the
        recovered spectrum lands close enough to the stored value.
    :param degenerate: Whether another recovered mode sits within
        :data:`DEGENERACY_WINDOW_CM1` of the match, making the individual
        eigenvector arbitrary within its subspace.
    """

    stored_frequency_cm1: float
    recovered_frequency_cm1: float | None
    mode: NormalMode | None
    degenerate: bool = False

    @property
    def matched(self) -> bool:
        return self.mode is not None and not self.degenerate


def atomic_mass(element: str, isotope_mass_number: int | None) -> float | None:
    """Resolve one atom's mass in amu.

    Follows the two conventions :mod:`app.chemistry.isotopes` states.
    ``isotope_mass_number is None`` means the element's most abundant
    natural isotope -- not "unknown". The element symbols ``D`` and ``T``
    name a nuclide rather than an element and carry their own mass number,
    which an explicit ``isotope_mass_number`` overrides.

    :param element: Element symbol from ``geometry_atom.element``.
    :param isotope_mass_number: ``geometry_atom.isotope_mass_number``.
    :returns: Mass in amu, or ``None`` when the element or isotope is
        unknown to RDKit -- in which case the caller must refuse to
        mass-weight rather than substitute a guess.
    """

    # Imported here rather than at module scope so this module's numerics
    # stay importable without the identity layer.
    from app.chemistry.geometry import resolve_element_symbol
    from app.chemistry.isotopes import HYDROGEN_ISOTOPE_SYMBOLS

    symbol = element.strip()
    mass_number = isotope_mass_number
    if mass_number is None:
        mass_number = HYDROGEN_ISOTOPE_SYMBOLS.get(symbol)
    resolved = resolve_element_symbol(symbol)

    table = Chem.GetPeriodicTable()
    if mass_number is None:
        try:
            mass = table.GetMostCommonIsotopeMass(resolved.capitalize())
        except (RuntimeError, ValueError):
            return None
        return None if mass == 0.0 else float(mass)

    try:
        mass = table.GetMassForIsotope(resolved.capitalize(), int(mass_number))
    except (RuntimeError, ValueError):
        return None
    return None if mass == 0.0 else float(mass)


def unpack_lower_triangle(packed: Sequence[float], natoms: int) -> np.ndarray:
    """Rebuild the symmetric 3N x 3N Hessian from its packed lower triangle.

    Row-major, diagonal included -- the layout ``calc_hessian`` stores and
    :mod:`app.services.hessian_parsing` writes.

    :param packed: ``3N(3N+1)/2`` force constants in hartree/bohr^2.
    :param natoms: Atom count.
    :returns: ``(3N, 3N)`` symmetric array.
    :raises ValueError: If the packed length is not the triangular number
        implied by ``natoms``.
    """

    dim = 3 * natoms
    expected = dim * (dim + 1) // 2
    if len(packed) != expected:
        raise ValueError(f"packed lower triangle has {len(packed)} entries, expected {expected} for natoms={natoms}")
    matrix = np.zeros((dim, dim), dtype=float)
    row, col = 0, 0
    for value in packed:
        matrix[row, col] = matrix[col, row] = value
        if col == row:
            row += 1
            col = 0
        else:
            col += 1
    return matrix


def solve_normal_modes(
    hessian_hartree_bohr2: np.ndarray,
    masses_amu: Sequence[float],
) -> list[NormalMode]:
    """Diagonalise the mass-weighted Hessian.

    No rigid-body projection is applied first, deliberately. The whole
    point of :func:`rigid_body_subspace` is to *measure* how much
    rigid-body character a mode carries, and projecting it out beforehand
    would set every one of those measurements to zero by construction.

    :param hessian_hartree_bohr2: ``(3N, 3N)`` Cartesian force constants.
    :param masses_amu: ``N`` atomic masses.
    :returns: Modes in ascending frequency order, imaginary ones first as
        negative wavenumbers.
    """

    masses = np.asarray(masses_amu, dtype=float)
    if masses.ndim != 1 or hessian_hartree_bohr2.shape != (
        3 * masses.size,
        3 * masses.size,
    ):
        raise ValueError(f"hessian shape {hessian_hartree_bohr2.shape} does not match {masses.size} atoms")
    if not np.all(masses > 0.0):
        raise ValueError("every atomic mass must be positive")

    root_mass = np.repeat(np.sqrt(masses), 3)
    weighted = hessian_hartree_bohr2 / np.outer(root_mass, root_mass)
    eigenvalues, eigenvectors = np.linalg.eigh(weighted)

    modes: list[NormalMode] = []
    for value, vector in zip(eigenvalues, eigenvectors.T, strict=True):
        omega_squared = value * _EIGENVALUE_TO_OMEGA2_SI
        wavenumber = math.sqrt(abs(omega_squared)) / (2.0 * math.pi * _C_CM_S)
        modes.append(
            NormalMode(
                frequency_cm1=-wavenumber if omega_squared < 0.0 else wavenumber,
                displacement=np.asarray(vector, dtype=float),
            )
        )
    modes.sort(key=lambda mode: mode.frequency_cm1)
    return modes


def rigid_body_subspace(
    coordinates_angstrom: np.ndarray,
    masses_amu: Sequence[float],
) -> RigidBodySubspace:
    """Build an orthonormal basis for translation and rotation.

    In mass-weighted coordinates the three translations are
    ``sqrt(m_k) * e_axis`` and the three rotations about the centre of
    mass are ``sqrt(m_k) * (n x (r_k - R_com))``. Translations and
    rotations about the centre of mass are already mutually orthogonal
    there; the SVD is taken over all six anyway, because its rank is
    exactly the linearity test.

    :param coordinates_angstrom: ``(N, 3)`` atomic positions.
    :param masses_amu: ``N`` atomic masses.
    :returns: The subspace, with 5 basis vectors for a collinear geometry
        and 6 otherwise.
    """

    coordinates = np.asarray(coordinates_angstrom, dtype=float)
    masses = np.asarray(masses_amu, dtype=float)
    natoms = masses.size
    if coordinates.shape != (natoms, 3):
        raise ValueError(f"coordinate shape {coordinates.shape} does not match {natoms} atoms")

    root_mass = np.sqrt(masses)
    centre = (masses[:, None] * coordinates).sum(axis=0) / masses.sum()
    relative = coordinates - centre

    raw = np.zeros((6, 3 * natoms), dtype=float)
    for axis in range(3):
        translation = np.zeros((natoms, 3), dtype=float)
        translation[:, axis] = 1.0
        raw[axis] = (root_mass[:, None] * translation).reshape(-1)

        unit = np.zeros(3, dtype=float)
        unit[axis] = 1.0
        rotation = np.cross(unit, relative)
        raw[3 + axis] = (root_mass[:, None] * rotation).reshape(-1)

    _, singular_values, right = np.linalg.svd(raw, full_matrices=False)
    if singular_values.size == 0 or singular_values[0] <= 0.0:
        raise ValueError("degenerate geometry: no rigid-body motion")
    keep = singular_values > singular_values[0] * LINEARITY_SINGULAR_VALUE_TOLERANCE
    basis = right[keep]
    return RigidBodySubspace(basis=basis, is_linear=int(keep.sum()) == 5)


def perceive_bonds(
    elements: Sequence[str],
    coordinates_angstrom: np.ndarray,
) -> tuple[tuple[int, int], ...]:
    """Perceive connectivity from interatomic distances.

    Two atoms are bonded when they sit within
    :data:`BOND_PERCEPTION_TOLERANCE` times the sum of their covalent
    radii. Bond *order* is not determined and is not needed: see
    :func:`rotatable_bonds`.

    :param elements: ``N`` element symbols.
    :param coordinates_angstrom: ``(N, 3)`` positions.
    :returns: 1-based atom-index pairs, ``a < b``, in ascending order.
    """

    from app.chemistry.geometry import resolve_element_symbol

    coordinates = np.asarray(coordinates_angstrom, dtype=float)
    table = Chem.GetPeriodicTable()
    radii: list[float] = []
    for element in elements:
        try:
            radii.append(float(table.GetRcovalent(resolve_element_symbol(element).capitalize())))
        except (RuntimeError, ValueError):
            radii.append(0.0)

    bonds: list[tuple[int, int]] = []
    natoms = len(elements)
    for a in range(natoms):
        for b in range(a + 1, natoms):
            limit = (radii[a] + radii[b]) * BOND_PERCEPTION_TOLERANCE
            if limit <= 0.0:
                continue
            if float(np.linalg.norm(coordinates[a] - coordinates[b])) <= limit:
                bonds.append((a + 1, b + 1))
    return tuple(bonds)


def rotatable_bonds(
    natoms: int,
    bonds: Sequence[tuple[int, int]],
) -> tuple[tuple[int, int], ...]:
    """Select the bonds a dihedral rotation is defined about.

    A bond qualifies when it is a **bridge** in the connectivity graph --
    cutting it splits the molecule, so it is not in a ring -- and both
    ends carry at least one further neighbour, so the rotation moves
    something on both sides.

    Bond order is deliberately not consulted, and this is a real
    limitation stated rather than hidden: connectivity here is perceived
    from distances, so a C=C would qualify. It is tolerable because the
    projection is a *measurement* of overlap with rotation about that
    axis, and a mode that is 80% rotation about a double bond is a
    torsion-like out-of-plane motion whichever name is preferred. It also
    means a methyl rotor qualifies, which is intended: a methyl torsion is
    exactly the kind of soft mode ADR 0012 is about.

    :param natoms: Atom count.
    :param bonds: 1-based bonded pairs, as from :func:`perceive_bonds`.
    :returns: The rotatable subset, in the order given.
    """

    adjacency: dict[int, set[int]] = {i: set() for i in range(1, natoms + 1)}
    for a, b in bonds:
        adjacency[a].add(b)
        adjacency[b].add(a)

    rotatable: list[tuple[int, int]] = []
    for a, b in bonds:
        if len(adjacency[a]) < 2 or len(adjacency[b]) < 2:
            continue
        if _reaches(adjacency, start=a, target=b, without=(a, b)):
            # Still connected with the bond cut: it closes a ring.
            continue
        rotatable.append((a, b))
    return tuple(rotatable)


def _reaches(
    adjacency: dict[int, set[int]],
    *,
    start: int,
    target: int,
    without: tuple[int, int],
) -> bool:
    """Whether ``target`` is reachable from ``start`` without one edge."""

    forbidden = (without, (without[1], without[0]))
    seen = {start}
    queue = deque([start])
    while queue:
        node = queue.popleft()
        for neighbour in adjacency[node]:
            if (node, neighbour) in forbidden:
                continue
            if neighbour == target:
                return True
            if neighbour not in seen:
                seen.add(neighbour)
                queue.append(neighbour)
    return False


def _side_of_bond(
    adjacency: dict[int, set[int]],
    *,
    anchor: int,
    excluded: int,
) -> set[int]:
    """Atoms reachable from ``anchor`` without crossing to ``excluded``."""

    seen = {anchor}
    queue = deque([anchor])
    while queue:
        node = queue.popleft()
        for neighbour in adjacency[node]:
            if neighbour == excluded or neighbour in seen:
                continue
            seen.add(neighbour)
            queue.append(neighbour)
    return seen


def torsion_axes(
    coordinates_angstrom: np.ndarray,
    masses_amu: Sequence[float],
    bonds: Sequence[tuple[int, int]],
    rotatable: Sequence[tuple[int, int]],
    rigid_body: RigidBodySubspace,
) -> list[TorsionAxis]:
    """Build one mass-weighted dihedral-rotation vector per rotatable bond.

    The vector rotates the atoms on one side of the bond about the bond
    axis and leaves the other side alone. That construction carries net
    angular momentum, which is removed by orthogonalising against
    ``rigid_body`` -- the standard fix, and the reason a torsion vector
    and the rigid-body subspace can never be confused for one another
    afterwards.

    :param coordinates_angstrom: ``(N, 3)`` positions.
    :param masses_amu: ``N`` atomic masses.
    :param bonds: Full 1-based connectivity.
    :param rotatable: The subset from :func:`rotatable_bonds`.
    :param rigid_body: The subspace to orthogonalise against.
    :returns: One axis per rotatable bond whose vector survives
        orthogonalisation with non-negligible norm.
    """

    coordinates = np.asarray(coordinates_angstrom, dtype=float)
    masses = np.asarray(masses_amu, dtype=float)
    natoms = masses.size
    root_mass = np.sqrt(masses)

    adjacency: dict[int, set[int]] = {i: set() for i in range(1, natoms + 1)}
    for a, b in bonds:
        adjacency[a].add(b)
        adjacency[b].add(a)

    axes: list[TorsionAxis] = []
    for a, b in rotatable:
        origin = coordinates[b - 1]
        axis = coordinates[b - 1] - coordinates[a - 1]
        norm = float(np.linalg.norm(axis))
        if norm <= 0.0:
            continue
        axis = axis / norm

        moving = _side_of_bond(adjacency, anchor=b, excluded=a)
        displacement = np.zeros((natoms, 3), dtype=float)
        for atom in moving:
            if atom == b:
                continue
            displacement[atom - 1] = np.cross(axis, coordinates[atom - 1] - origin)

        vector = (root_mass[:, None] * displacement).reshape(-1)
        vector = vector - rigid_body.basis.T @ (rigid_body.basis @ vector)
        length = float(np.linalg.norm(vector))
        if length <= 1e-8:
            continue
        axes.append(TorsionAxis(atom_index_a=a, atom_index_b=b, vector=vector / length))
    return axes


def project_mode(
    mode: NormalMode,
    rigid_body: RigidBodySubspace,
    axes: Sequence[TorsionAxis],
    *,
    rigid_body_threshold: float = RIGID_BODY_OVERLAP_THRESHOLD,
    torsion_threshold: float = TORSION_OVERLAP_THRESHOLD,
) -> ModeProjection:
    """Project one mode onto rigid-body and torsional motion.

    :param mode: A unit-norm mass-weighted eigenvector.
    :param rigid_body: Orthonormal rigid-body basis.
    :param axes: Torsion vectors, already orthogonal to ``rigid_body``.
    :param rigid_body_threshold: Overlap at or above which the mode is
        called rigid-body residue.
    :param torsion_threshold: Single-bond overlap at or above which the
        mode is called a torsion.
    :returns: The overlaps and the determination they support.
    """

    vector = mode.displacement
    rigid_overlap = float(np.sum((rigid_body.basis @ vector) ** 2))

    best_overlap = 0.0
    best_bond: tuple[int, int] | None = None
    for axis in axes:
        overlap = float(np.dot(axis.vector, vector) ** 2)
        if overlap > best_overlap:
            best_overlap = overlap
            best_bond = (axis.atom_index_a, axis.atom_index_b)

    subspace_overlap = best_overlap
    if len(axes) > 1:
        stacked = np.vstack([axis.vector for axis in axes])
        # The per-bond vectors are not mutually orthogonal, so the span
        # needs its own orthonormal basis before the overlaps can be summed.
        _, singular_values, right = np.linalg.svd(stacked, full_matrices=False)
        keep = singular_values > singular_values[0] * 1e-8
        subspace_overlap = float(np.sum((right[keep] @ vector) ** 2))

    rigid_overlap = min(1.0, max(0.0, rigid_overlap))
    best_overlap = min(1.0, max(0.0, best_overlap))
    subspace_overlap = min(1.0, max(0.0, max(subspace_overlap, best_overlap)))

    if rigid_overlap >= rigid_body_threshold:
        determination = ModeDetermination.rigid_body_residue
    elif best_overlap >= torsion_threshold:
        determination = ModeDetermination.torsion
    else:
        determination = ModeDetermination.internal_vibration

    return ModeProjection(
        rigid_body_overlap=rigid_overlap,
        torsion_overlap=best_overlap,
        torsion_subspace_overlap=subspace_overlap,
        best_torsion_bond=best_bond,
        determination=determination,
    )


def match_stored_frequency(
    stored_frequency_cm1: float,
    modes: Sequence[NormalMode],
) -> SpectrumMatch:
    """Attach a recovered eigenvector to a stored frequency.

    The Hessian and the frequency list are separately parsed facts about
    the same job, so pairing them is a claim that has to be checked rather
    than assumed. Matching is by nearest wavenumber, and a nearest
    neighbour further away than
    :data:`SPECTRUM_MATCH_ABSOLUTE_TOLERANCE_CM1` (or
    :data:`SPECTRUM_MATCH_RELATIVE_TOLERANCE` of the stored value,
    whichever is looser) is reported as no match at all. That case is not
    a projection with a caveat; it is a refusal to project.

    :param stored_frequency_cm1: Signed wavenumber as stored.
    :param modes: Recovered modes.
    :returns: The pairing, matched or not.
    """

    if not modes:
        return SpectrumMatch(
            stored_frequency_cm1=stored_frequency_cm1,
            recovered_frequency_cm1=None,
            mode=None,
        )
    nearest = min(modes, key=lambda mode: abs(mode.frequency_cm1 - stored_frequency_cm1))
    tolerance = max(
        SPECTRUM_MATCH_ABSOLUTE_TOLERANCE_CM1,
        abs(stored_frequency_cm1) * SPECTRUM_MATCH_RELATIVE_TOLERANCE,
    )
    if abs(nearest.frequency_cm1 - stored_frequency_cm1) > tolerance:
        return SpectrumMatch(
            stored_frequency_cm1=stored_frequency_cm1,
            recovered_frequency_cm1=nearest.frequency_cm1,
            mode=None,
        )
    degenerate = any(
        other is not nearest and abs(other.frequency_cm1 - nearest.frequency_cm1) <= DEGENERACY_WINDOW_CM1
        for other in modes
    )
    return SpectrumMatch(
        stored_frequency_cm1=stored_frequency_cm1,
        recovered_frequency_cm1=nearest.frequency_cm1,
        mode=nearest,
        degenerate=degenerate,
    )


def rigid_body_curvature_cm1(
    hessian_hartree_bohr2: np.ndarray,
    masses_amu: Sequence[float],
    rigid_body: RigidBodySubspace,
) -> list[float]:
    """Curvature of the Hessian along each rigid-body direction.

    Each entry is the Rayleigh quotient ``b @ H_mw @ b`` expressed as a
    signed wavenumber, the same convention as
    :attr:`NormalMode.frequency_cm1`. Translation and rotation are exact
    null directions of a Hessian evaluated at a stationary point, so these
    numbers are the direct test of whether the geometry on file is the
    frame the matrix was computed in -- see
    :data:`FRAME_CONSISTENCY_TOLERANCE_CM1`.

    :param hessian_hartree_bohr2: ``(3N, 3N)`` Cartesian force constants.
    :param masses_amu: ``N`` atomic masses.
    :param rigid_body: Basis from :func:`rigid_body_subspace`.
    :returns: One signed wavenumber per basis vector.
    """

    masses = np.asarray(masses_amu, dtype=float)
    root_mass = np.repeat(np.sqrt(masses), 3)
    weighted = hessian_hartree_bohr2 / np.outer(root_mass, root_mass)
    curvatures: list[float] = []
    for vector in rigid_body.basis:
        omega_squared = float(vector @ weighted @ vector) * _EIGENVALUE_TO_OMEGA2_SI
        wavenumber = math.sqrt(abs(omega_squared)) / (2.0 * math.pi * _C_CM_S)
        curvatures.append(-wavenumber if omega_squared < 0.0 else wavenumber)
    return curvatures


def bohr_to_angstrom(value: float) -> float:
    """Convert a length in bohr to Angstrom."""

    return value * _BOHR_ANGSTROM
