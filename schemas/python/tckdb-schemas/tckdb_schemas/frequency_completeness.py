"""Whether a deposited frequency list is the spectrum, or only part of it.

The list is compared with the atom count of the geometry the frequency job ran
on. A harmonic analysis of *N* atoms has ``3N - 6`` vibrational modes, ``3N - 5``
if the atoms are collinear, and none for a single atom.

The floor
---------
The smallest complete list is 0 for ``N <= 1``, 1 for ``N == 2`` and ``3N - 6``
for ``N >= 3``. For ``N >= 3`` this is a lower bound that a collinear geometry
clears, so linearity is never evaluated and no collinearity tolerance is chosen.
A list below the floor is reported as a **warning**: the check cannot tell a
filtered list from a genuinely shorter one (a partial-Hessian job, a frozen-atom
or ONIOM Hessian, a lumped ``pseudo`` participant), and a deposit carries no
field saying which a short list is. A deposit without a frequency list
(``modes = null``) is accepted and reports nothing.

The ceiling
-----------
The longest list is ``3N``, the total number of eigenvalues of the
mass-weighted Hessian of an ``N``-atom geometry, the six translation/rotation
eigenvalues included. The ceiling is inclusive: a list of exactly ``3N`` is
accepted. A longer list is **blocking**: no harmonic analysis of that geometry
produces it, so the list and the geometry do not describe the same molecule. The ceiling is
declared as ``CHECK_FREQ_LIST_WITHIN_GEOMETRY_DEGREES_OF_FREEDOM`` in the
scientific check register.

Pure functions only — no Pydantic, no backend imports. Callers extract
the two integers from whatever shape they hold and pass them in.
"""

from __future__ import annotations

from tckdb_schemas.fragments.reaction_atom_map import xyz_block_shape
from tckdb_schemas.stationary_point import StationaryPointFinding, ValidationTier

#: The deposited frequency list is shorter than the smallest complete
#: harmonic spectrum the geometry admits, so modes the calculation
#: produced are missing from the record. The complete, signed, unrounded
#: list is what a record should carry; this says it does not.
W_FREQ_LIST_INCOMPLETE = "freq_list_incomplete_for_geometry"

#: The deposited frequency list is longer than ``3N`` for the geometry it
#: is attached to, so it describes more degrees of freedom than that
#: geometry has. Usually a list attached to the wrong geometry rather
#: than a filtered one.
#:
#: **Blocking**, unlike its sibling above, and the ``W_`` prefix is house
#: style for "machine-readable code" rather than a tier — the same
#: spelling ``W_TS_NO_IMAGINARY_MODE`` uses in
#: :mod:`tckdb_schemas.stationary_point`. The refusal is raised by
#: :func:`~tckdb_schemas.stationary_point.raise_for_blocking_findings`,
#: which every published upload request model calls from a
#: ``model_validator``, so the code arrives as the ``code`` field of a 422
#: rather than as a warning on an accepted deposit.
W_FREQ_LIST_EXCEEDS_GEOMETRY = "freq_list_exceeds_geometry_degrees_of_freedom"


def minimum_complete_mode_count(n_atoms: int) -> int:
    """Smallest frequency-list length a complete spectrum can have.

    Exact for ``N <= 2``, where linearity is a fact rather than a
    measurement: a single atom has no vibrations at all, and two atoms
    are collinear by definition and have exactly one. For ``N >= 3`` it
    is ``3N - 6``, which is a *lower* bound — a collinear geometry has
    ``3N - 5`` and clears it — chosen so the check never asks whether a
    geometry is linear.

    :param n_atoms: Number of atoms in the geometry the frequency job
        ran on.
    :returns: The threshold below which a list is certainly incomplete.
    """
    if n_atoms <= 1:
        return 0
    if n_atoms == 2:
        return 1
    return 3 * n_atoms - 6


def maximum_mode_count(n_atoms: int) -> int:
    """Longest frequency list the geometry admits.

    ``3N``: the full eigenvalue spectrum of the mass-weighted Hessian,
    so the six translation/rotation eigenvalues
    are available to assess contamination.
    """
    return 3 * n_atoms


def atom_count_of_xyz(xyz_text: str | None) -> int | None:
    """Atom count of an XYZ block, or ``None`` when it cannot be counted.

    An unparseable geometry is refused by the atom-map fragment that owns
    that contract; this check declines to speak about it rather than
    reporting the same defect a second time in different words.

    Counts through :func:`~tckdb_schemas.fragments.reaction_atom_map.xyz_block_shape`
    rather than catching the refusal :func:`parse_xyz_elements` raises: there is
    no code path here that raises a coded error, so the producer contract's
    static trace (which cannot see a ``try``/``except``) never lists that
    refusal against a rule that only wanted a count.
    """
    if xyz_text is None:
        return None
    declared, body = xyz_block_shape(xyz_text)
    if declared is None or len(body) != declared:
        return None
    return declared


def evaluate_frequency_list_completeness(
    n_modes: int | None,
    n_atoms: int | None,
    *,
    location: str,
) -> list[StationaryPointFinding]:
    """Judge one deposited frequency list against its own geometry.

    :param n_modes: Length of the deposited frequency list. ``None``
        means no list was deposited, which is incompleteness that says
        so; nothing is reported.
    :param n_atoms: Atom count of the geometry the frequency job ran on.
        ``None`` means no geometry is in hand, and the check is not
        reachable — nothing is reported.
    :param location: Path to the payload element, used verbatim.
    :returns: Findings, possibly empty. A list past the ceiling yields a
        **blocking** finding and a list below the floor a warning; see the
        module docstring for why the two bounds differ. Never raises —
        the refusal is raised by
        :func:`~tckdb_schemas.stationary_point.raise_for_blocking_findings`,
        which the request models call once for every finding they have
        collected, so this function stays a pure judgement and one payload
        with two contradictions still reports both.
    """
    if n_modes is None or n_atoms is None:
        return []

    floor = minimum_complete_mode_count(n_atoms)
    ceiling = maximum_mode_count(n_atoms)

    if n_modes > ceiling:
        return [
            StationaryPointFinding(
                tier=ValidationTier.block,
                code=W_FREQ_LIST_EXCEEDS_GEOMETRY,
                location=location,
                # No structural flag, and not an oversight. The flag keeps
                # an *accepted* record out of default query results and
                # bulk exports; a refused payload leaves no record to keep
                # out of anything. Every other blocking finding in
                # ``stationary_point`` carries no flag for the same reason.
                message=(
                    f"{location}: the frequency list carries {n_modes} modes "
                    f"but the geometry it is attached to has {n_atoms} atom(s), "
                    f"which have only {ceiling} degrees of freedom in total "
                    f"({W_FREQ_LIST_EXCEEDS_GEOMETRY}). 3N counts every "
                    f"Cartesian degree of freedom, the six translations and "
                    f"rotations included, so no harmonic analysis of this "
                    f"geometry can produce that many modes at any level of "
                    f"theory, on any grid, in any coordinate system. The list "
                    f"and the geometry are not describing the same molecule. "
                    f"This is refused rather than flagged because there is no "
                    f"correct calculation it could be a record of: filtering "
                    f"a spectrum makes it shorter, never longer. Attach the "
                    f"calculation to the geometry it actually ran on, or "
                    f"deposit that geometry."
                ),
            )
        ]

    if n_modes < floor:
        return [
            StationaryPointFinding(
                tier=ValidationTier.warn,
                code=W_FREQ_LIST_INCOMPLETE,
                location=location,
                structural_flag=True,
                context={"n_modes": n_modes, "n_atoms": n_atoms, "minimum_modes": floor},
                message=(
                    f"{location}: the frequency list carries {n_modes} mode(s) "
                    f"but a harmonic analysis of this {n_atoms}-atom geometry "
                    f"produces at least {floor} "
                    f"({W_FREQ_LIST_INCOMPLETE}). ADR 0012 requires the "
                    f"complete signed unrounded list, never filtered: a "
                    f"partial list agrees with an independently recovered "
                    f"spectrum on every mode it contains and is silent about "
                    f"the ones it omits, which reads as agreement. The record "
                    f"is accepted and flagged. Deposit every mode the "
                    f"frequency job printed, including the reaction "
                    f"coordinate and any mode removed from the partition "
                    f"function; if the job genuinely computed a partial "
                    f"Hessian, say so in the calculation's parameters so the "
                    f"short list is explicable."
                ),
            )
        ]

    return []


def evaluate_deposited_frequency_list(
    n_modes: int | None,
    *,
    input_geometry_xyz_text: str | None,
    fallback_xyz_text: str | None,
    location: str,
) -> list[StationaryPointFinding]:
    """Judge a deposited list against whichever geometry it will be bound to.

    Every upload shape carrying a frequency list resolves its geometry
    the same way — the calculation's own first input geometry when the
    producer named one, and otherwise the enclosing conformer's or
    transition state's reference geometry, which is the fallback the
    persistence workflow itself applies for ``freq``. Written once here
    so the three payload shapes that call it cannot drift into counting
    three different sets of atoms.

    Both arguments are XYZ text, not payload objects: this module knows
    the rule, and the caller knows where its geometries live.
    """
    xyz_text = (
        input_geometry_xyz_text
        if input_geometry_xyz_text is not None
        else fallback_xyz_text
    )
    return evaluate_frequency_list_completeness(
        n_modes, atom_count_of_xyz(xyz_text), location=location
    )


__all__ = [
    "W_FREQ_LIST_EXCEEDS_GEOMETRY",
    "W_FREQ_LIST_INCOMPLETE",
    "atom_count_of_xyz",
    "evaluate_deposited_frequency_list",
    "evaluate_frequency_list_completeness",
    "maximum_mode_count",
    "minimum_complete_mode_count",
]
