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
    "isotope_mass",
    "most_common_isotope",
    "normalize_isotope",
    "validate_isotope",
]

#: Element symbols that name a *nuclide* rather than an element, mapped to the
#: mass number they stand for. Only hydrogen has them, and only these two:
#: ``D`` is deuterium and ``T`` is tritium.
#:
#: They are legal, common XYZ tokens — Gaussian, ORCA, Molpro and CFOUR all
#: emit or accept them — and ``geometry_atom.element`` keeps them. Ingestion
#: canonicalises the *case* of every symbol it stores
#: (:func:`app.chemistry.geometry.normalize_element_symbol`, applied in
#: :func:`app.chemistry.geometry.parse_xyz`) and deliberately stops there: a
#: ``D`` collapsed to ``H`` at deposit time would destroy the depositor's own
#: isotope labelling, which is a fact about the deposit rather than a spelling
#: of one. Anything that *counts elements* must therefore resolve them to ``H``
#: (:func:`app.chemistry.geometry.resolve_element_symbol`), or it refuses a
#: correctly deposited isotopologue for spelling its hydrogens the way its ESS
#: did — which ADR 0008 puts out of bounds for a blocking check.
#:
#: This mapping is deliberately *not* wired into :func:`validate_isotope` or
#: :func:`parse_xyz`. Isotope **identity** is carried atom-resolved by
#: ``geometry.isotopes`` and by SMILES isotope notation, never by the element
#: column; resolving ``D`` to ``H`` here answers "which element is this atom",
#: which is the only question composition counting asks.
HYDROGEN_ISOTOPE_SYMBOLS: dict[str, int] = {"D": 2, "T": 3}


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

    :param element: Element symbol from the uploaded geometry or SMILES.
    :param mass_number: Uploaded isotope mass number.
    :param context: Human-readable location for the error message.
    :raises ValueError: If the element or the isotope is unknown to RDKit.
    """

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
