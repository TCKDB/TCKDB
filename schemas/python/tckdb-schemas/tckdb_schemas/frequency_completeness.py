"""Whether a deposited frequency list is the spectrum, or only part of it.

`ADR 0012 <../../../../docs/adr/0012-imaginary-modes-are-judged-by-magnitude-not-counted.md>`_
§"What a record must carry" says the frequency list "must be complete,
signed and unrounded, never filtered". Nothing enforced the *complete*.

A depositor could upload three modes, all imaginary, with ``n_imag = 3``
and pass every check TCKDB had — including
``freq_n_imag_disagrees_with_modes``, which compares the imaginary count
against ``n_imag`` and is satisfied exactly. The real modes were simply
absent, and absence is silent: a consumer recomputing a partition
function from that list gets a number, not an error, and a comparison
against an independently recovered spectrum agrees on every mode present
and says nothing about the ones missing. An unenforced completeness
requirement therefore does not merely fail to help — it makes a partial
record read as a whole one.

What is checkable, and what is not
----------------------------------
Completeness is not a property of the payload's frequency list alone. It
is a relation between that list and the geometry the frequency job ran
on: a harmonic analysis of *N* atoms has ``3N − 6`` vibrational modes,
or ``3N − 5`` if the atoms are collinear, and none at all for a single
atom. So the check needs the geometry, which is why it lives here and is
called from the request models that hold both rather than from
``FreqResultPayload``, which holds only one.

**The bound is deliberately the weakest one that is certainly true.**
Linearity is not determined here. A collinear molecule has ``3N − 5``
modes, one *more* than ``3N − 6``, so comparing against ``3N − 6``
accepts every linear molecule without ever asking whether it is linear —
and asking would mean choosing a collinearity tolerance, which is a
numerical judgement this check has no business making at validation
time. For ``N ≤ 2`` linearity is a fact rather than a measurement (two
atoms are collinear by definition), so those cases take their exact
counts.

The upper bound is ``3N``, not ``3N − 6``, because ADR 0012 asks for
*both*: the spectrum, and "the six translation/rotation eigenvalues …
so contamination is directly assessable". A record carrying all ``3N``
eigenvalues of the mass-weighted Hessian is the most complete record the
ADR describes, and refusing it as an over-count would refuse exactly
what the ADR asks for. Only a list longer than ``3N`` describes more
degrees of freedom than the geometry has.

Why this warns rather than blocks
---------------------------------
Under `ADR 0008 <0008-validation-tiers-definitions-block-expectations-warn.md>`_
a check may block only when it asserts a definition or a contract.
"A frequency list is the spectrum" reads like a definition, and the
argument that it is not is this: **the check cannot tell a filtered list
from a genuinely shorter one.** A partial-Hessian frequency job, a
frozen-atom or ONIOM Hessian, or a lumped ``pseudo`` participant all
produce fewer than ``3N − 6`` modes and are correct records of what was
computed. TCKDB carries no field saying which of those a short list is,
so refusing would assert a determination the deposit does not contain
the information to support — the same shape of error ADR 0012 identified
in the ``n_imag == 1`` gate it retired.

The second argument is structural and decides it. ``modes = null`` is
accepted and must stay accepted: absence is incompleteness, not
contradiction, and the read surface already reports
``n_imag_at_or_above_tau = null`` rather than ``0`` for a record with no
frequency list. So the cheapest way past a completeness *block* is to
delete the frequency list entirely, turning a partial list — some
information, honestly labelled — into no information at all. ADR 0012
§"Why not refuse, when refusing is cheaper" names that failure exactly:
"A rule that converts visible ambiguity into invisible falsehood is
worse than no rule."

One sub-case genuinely is definitional: a list *longer* than ``3N`` is
arithmetically impossible for a harmonic analysis of that geometry, and
no correct deposit produces it. It is reported here at the warning tier
with its own code so that the two facts stay distinguishable; promoting
it to a refusal wants a scientific-check register entry, which is a
decision to take in the register rather than during an implementation.

Pure functions only — no Pydantic, no backend imports. Callers extract
the two integers from whatever shape they hold and pass them in.
"""

from __future__ import annotations

from tckdb_schemas.fragments.reaction_atom_map import parse_xyz_elements
from tckdb_schemas.stationary_point import StationaryPointFinding, ValidationTier

#: The deposited frequency list is shorter than the smallest complete
#: harmonic spectrum the geometry admits, so modes the calculation
#: produced are missing from the record. ADR 0012 requires the complete
#: signed unrounded list; this says the record does not carry one.
W_FREQ_LIST_INCOMPLETE = "freq_list_incomplete_for_geometry"

#: The deposited frequency list is longer than ``3N`` for the geometry it
#: is attached to, so it describes more degrees of freedom than that
#: geometry has. Usually a list attached to the wrong geometry rather
#: than a filtered one.
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
    which ADR 0012 asks for so the six translation/rotation eigenvalues
    are available to assess contamination.
    """
    return 3 * n_atoms


def atom_count_of_xyz(xyz_text: str | None) -> int | None:
    """Atom count of an XYZ block, or ``None`` when it cannot be counted.

    An unparseable geometry is refused by the atom-map fragment that owns
    that contract; this check declines to speak about it rather than
    reporting the same defect a second time in different words.
    """
    if xyz_text is None:
        return None
    try:
        return len(parse_xyz_elements(xyz_text))
    except ValueError:
        return None


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
    :returns: Findings, possibly empty. Never raises.
    """
    if n_modes is None or n_atoms is None:
        return []

    floor = minimum_complete_mode_count(n_atoms)
    ceiling = maximum_mode_count(n_atoms)

    if n_modes > ceiling:
        return [
            StationaryPointFinding(
                tier=ValidationTier.warn,
                code=W_FREQ_LIST_EXCEEDS_GEOMETRY,
                location=location,
                structural_flag=True,
                message=(
                    f"{location}: the frequency list carries {n_modes} modes "
                    f"but the geometry it is attached to has {n_atoms} atom(s), "
                    f"which have only {ceiling} degrees of freedom in total "
                    f"({W_FREQ_LIST_EXCEEDS_GEOMETRY}). A harmonic analysis "
                    f"of this geometry cannot produce that many modes, so the "
                    f"list and the geometry are not describing the same "
                    f"molecule — check that the calculation is attached to "
                    f"the geometry it actually ran on."
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
