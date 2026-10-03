"""Element-level isotope utilities shared by geometry and species identity.

TCKDB stores isotopic substitution *atom-resolved*: every atom of a
geometry may carry an explicit isotope mass number, and a species entry's
identity carries a canonical key derived from the isotope-labelled
molecular graph.

Two conventions are load-bearing throughout the codebase:

* ``None`` means "standard isotope" — the most abundant natural isotope
  of that element. It is **not** "unknown". A deposit that says nothing
  about isotopes is a deposit of the ordinary isotopologue, which is what
  every pre-existing TCKDB row is.
* An *explicit* mass number equal to the element's most abundant isotope
  is normalized back to ``None``. ``[1H]C([1H])([1H])[1H]`` and ``C`` are
  the same molecule and must resolve to the same identity, so the two
  spellings may never fork a species entry.
"""

from __future__ import annotations

from rdkit import Chem

__all__ = [
    "HYDROGEN_ISOTOPE_SYMBOLS",
    "implied_isotope_mass_number",
    "isotope_mass",
    "most_common_isotope",
    "normalize_isotope",
    "validate_isotope",
]

#: Element symbols that name a *nuclide* rather than an element, mapped to the
#: mass number they stand for. Only hydrogen has them, and only these two:
#: ``D`` is deuterium (mass number 2) and ``T`` is tritium (mass number 3).
#:
#: A ``D``/``T`` element token is an isotope declaration (decided 2026-10-03,
#: ``docs/adr/0022``, issue #672). :func:`app.chemistry.geometry.parse_xyz`
#: reads it as ``H`` plus the mass number below, so a new ``geometry_atom`` row
#: holds ``H`` and an ``isotope_mass_number``, the isotope identity check sees
#: ``[2H]``/``[3H]``, and the geometry hash includes the implied isotope
#: exactly as an explicit ``geometry.isotopes`` entry would. ``xyz_text`` keeps
#: the ``D`` the depositor wrote. :func:`validate_isotope` resolves the symbol
#: to ``H`` before it asks RDKit, whose periodic table has no ``D``.
#:
#: Wherever ``D`` has a meaning in chemistry it means deuterium (IUPAC Red Book
#: IR-3.3.2 permits ``D`` and ``T`` as symbols for 2H and 3H; RDKit's molfile
#: reader turns a ``D`` atom into ``[2H]``). The council that settled #672
#: found no manual saying Gaussian, ORCA, Molpro, Psi4 or Q-Chem emit ``D`` as
#: an element: each documents an isotope as a mass attached to an element
#: (``H(Iso=2)``, ``M = ...``, a ``MASS`` card, ``H@2.014101779``, a
#: ``$isotopes`` section). A ``D`` reaches TCKDB from a hand-written xyz or
#: input deck, not from an ESS's own output.
#:
#: Rows deposited *before* that decision hold ``D``/``T`` in the element column
#: with a NULL mass number and cannot be rewritten
#: (``trg_as_geometry_atom``). :func:`implied_isotope_mass_number` is how a
#: reader recovers their meaning from the row's own symbol, and
#: :func:`app.chemistry.geometry.resolve_element_symbol` is how anything that
#: only *counts elements* still resolves them to ``H``.
HYDROGEN_ISOTOPE_SYMBOLS: dict[str, int] = {"D": 2, "T": 3}


def implied_isotope_mass_number(element: str) -> int | None:
    """Return the mass number a ``D``/``T`` element symbol names, else ``None``.

    Reads the symbol only (case-insensitive, blank-padded ``character(2)``
    values accepted), so it can interpret a legacy ``geometry_atom`` row whose
    ``isotope_mass_number`` is NULL without borrowing anything from a sibling
    record.

    :param element: Element symbol as stored or deposited.
    :returns: ``2`` for ``D``, ``3`` for ``T``, ``None`` for every element
        symbol.
    """

    return HYDROGEN_ISOTOPE_SYMBOLS.get(element.strip().capitalize())


def _periodic_table() -> Chem.PeriodicTable:
    return Chem.GetPeriodicTable()


# The `.capitalize()` calls below survive the move of case canonicalisation into
# `parse_xyz`. They are not comparison-time patches over a verbatim column: they
# adapt an arbitrary caller-supplied symbol to RDKit's periodic table, which is
# case-sensitive and raises on `CL`. These are exported helpers that take
# `element: str` from SMILES, from geometry payloads and from callers that have
# not been through `parse_xyz`, so RDKit is the "other side" that
# `normalize_element_symbol` exists to line up with.


def most_common_isotope(element: str) -> int | None:
    """Return the mass number of an element's most abundant natural isotope.

    :param element: Element symbol, e.g. ``"H"`` or ``"Cl"``.
    :returns: Mass number, or ``None`` when RDKit does not know the element.
    """

    try:
        return int(_periodic_table().GetMostCommonIsotope(element.capitalize()))
    except (RuntimeError, ValueError):
        return None


def isotope_mass(element: str, mass_number: int) -> float | None:
    """Return the atomic mass of a specific isotope.

    :param element: Element symbol.
    :param mass_number: Isotope mass number (nucleon count).
    :returns: Isotope mass in amu, or ``None`` when the isotope is unknown.
    """

    try:
        mass = _periodic_table().GetMassForIsotope(element.capitalize(), mass_number)
    except (RuntimeError, ValueError):
        return None
    # RDKit signals "no such isotope" with a zero mass rather than raising.
    return None if mass == 0.0 else mass


def validate_isotope(element: str, mass_number: int, *, context: str) -> None:
    """Reject isotope mass numbers that do not exist for the given element.

    Guessing here would be scientifically indefensible: a wrong mass number
    silently corrupts every mass-weighted quantity derived from the geometry
    (frequencies, rotational constants, ZPE, kinetic isotope effects). We
    reject rather than guess.

    ``D`` and ``T`` are resolved to ``H`` first: RDKit's periodic table has no
    such element, so without that a ``D`` atom plus a correct mass number was
    refused as "unknown element symbol". Whether the mass number *agrees with*
    the spelling is a different question, answered where the spelling is read
    (:func:`app.chemistry.geometry.parse_xyz`).

    :param element: Element symbol from the uploaded geometry or SMILES.
    :param mass_number: Uploaded isotope mass number.
    :param context: Human-readable location for the error message.
    :raises ValueError: If the element or the isotope is unknown to RDKit.
    """

    if element.strip().capitalize() in HYDROGEN_ISOTOPE_SYMBOLS:
        element = "H"
    if most_common_isotope(element) is None:
        raise ValueError(f"{context}: unknown element symbol {element!r}")
    if mass_number < 1:
        raise ValueError(
            f"{context}: isotope mass number must be >= 1, got {mass_number}"
        )
    if isotope_mass(element, mass_number) is None:
        raise ValueError(
            f"{context}: {mass_number} is not a known isotope of {element!r}"
        )


def normalize_isotope(element: str, mass_number: int | None) -> int | None:
    """Collapse an explicitly stated standard isotope to ``None``.

    :param element: Element symbol.
    :param mass_number: Uploaded isotope mass number, or ``None``.
    :returns: ``None`` when the atom is at its most abundant isotope,
        otherwise the mass number unchanged.
    """

    if mass_number is None:
        return None
    if mass_number == most_common_isotope(element):
        return None
    return mass_number
