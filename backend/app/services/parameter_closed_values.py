"""Closed value sets for the canonical parameter keys whose values are an enumeration.

A parser that maps a deck keyword to one of these keys may only emit one of the listed values. The set is
the vocabulary's own statement of what the key means (``calculation_parameter_vocab.note``), so a value
outside it is a parser bug, and a deck spelling the parser does not recognise is stored as a raw row with
no canonical key instead of being forced into the nearest value.

:func:`canonical_or_none` is the one gate every parser row passes through: it keeps a canonical key only
when the value is in the key's closed set.
"""

from __future__ import annotations

CLOSED_VALUES: dict[str, frozenset[str]] = {
    "dispersion.correction": frozenset({"none", "d2", "d3_zero", "d3_bj", "d4"}),
    "solvation.model": frozenset({"smd", "cpcm", "iefpcm", "cosmo", "alpb"}),
    "ri.approximation": frozenset({"none", "rij", "rijcosx", "rijk", "rijonx"}),
    "core.frozen": frozenset({"frozen_core", "all_electron", "window"}),
    "relativistic.hamiltonian": frozenset({"none", "dkh0", "dkh2", "dkhso", "resc", "x2c", "zora", "iora"}),
    "basis.d_function_type": frozenset({"spherical", "cartesian"}),
    "basis.f_function_type": frozenset({"spherical", "cartesian"}),
    "scf.stability_analysis": frozenset({"check_only", "optimize"}),
}


def canonical_or_none(
    canonical_key: str | None, canonical_value: str | None
) -> tuple[str | None, str | None]:
    """Keep ``(key, value)`` only when the value belongs to the key's closed set.

    Keys without a closed set pass through unchanged. A closed-set key with a value outside its set (or
    no value) is dropped to ``(None, None)``: the raw row is kept, and nothing claims it is normalised.
    """
    if canonical_key is None:
        return None, None
    closed = CLOSED_VALUES.get(canonical_key)
    if closed is None:
        return canonical_key, canonical_value
    if canonical_value in closed:
        return canonical_key, canonical_value
    return None, None


def normalise_solvent(value: str) -> str | None:
    """Lower-case a solvent name as the program spells it; ``None`` when nothing is left."""
    cleaned = value.strip().strip("\"'").strip().lower()
    return cleaned or None
