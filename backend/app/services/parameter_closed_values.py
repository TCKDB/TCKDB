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
    "pno.truncation": frozenset({"loose", "normal", "tight"}),
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


#: Spellings a depositor types for a closed-set value, by key. Compared after case-folding and with spaces,
#: hyphens and underscores removed. A spelling that is not here and not a member of the closed set is not judged.
_TYPED_ALIASES: dict[str, dict[str, str]] = {
    "dispersion.correction": {
        "gd2": "d2", "d2": "d2", "gd3bj": "d3_bj", "d3bj": "d3_bj", "d3zero": "d3_zero", "d30": "d3_zero",
        "gd3zero": "d3_zero", "d4": "d4", "none": "none", "nodisp": "none", "novdw": "none",
    },
    "solvation.model": {"pcm": "iefpcm", "iefpcm": "iefpcm", "cpcm": "cpcm", "smd": "smd", "cosmo": "cosmo", "alpb": "alpb"},
    "ri.approximation": {"rijcosx": "rijcosx", "rijk": "rijk", "rij": "rij", "ri": "rij", "rijonx": "rijonx", "nori": "none", "none": "none"},
    "core.frozen": {
        "frozencore": "frozen_core", "fc": "frozen_core", "frozen": "frozen_core", "allelectron": "all_electron",
        "full": "all_electron", "nofrozencore": "all_electron", "window": "window",
    },
    "scf.stability_analysis": {"checkonly": "check_only", "check": "check_only", "optimize": "optimize", "opt": "optimize"},
}

#: Convergence tokens the parsers emit as ``tight`` / ``very_tight``; a depositor may type the keyword.
_CONVERGENCE_ALIASES = {
    "tight": "tight", "tightscf": "tight", "tightopt": "tight", "verytight": "very_tight", "verytightscf": "very_tight",
    "verytightopt": "very_tight", "loose": "loose", "loosescf": "loose", "looseopt": "loose", "normal": "normal",
}


def _squash(value: str) -> str:
    return "".join(ch for ch in value.casefold() if ch not in " -_")


def normalise_typed_value(canonical_key: str, value: str, software: str | None = None) -> str | None:
    """A depositor-typed canonical value in the form the parsers emit, or ``None`` when it cannot be judged.

    Case, spacing and the alias spellings of a closed set are folded (``UltraFine`` = ``ultrafine``,
    ``GD3BJ`` = ``d3_bj``, ``PCM`` = ``iefpcm``). A value that is not in the key's closed set after that is a
    non-token, and a non-token is never compared. A bare ``D3`` means zero damping to Gaussian and Becke-Johnson
    to ORCA, so it is judged only when the program is known.
    """
    squashed = _squash(value)
    if not squashed:
        return None
    if canonical_key == "dispersion.correction" and squashed in ("d3", "gd3"):
        return {"orca": "d3_bj", "gaussian": "d3_zero"}.get((software or "").lower())
    if canonical_key in ("scf.convergence", "opt.convergence"):
        return _CONVERGENCE_ALIASES.get(squashed)
    if canonical_key == "grid.quality":
        if squashed == "pass0grid":
            return squashed
        return squashed[: -len("grid")] if squashed.endswith("grid") and len(squashed) > 4 else squashed
    aliases = _TYPED_ALIASES.get(canonical_key)
    closed = CLOSED_VALUES.get(canonical_key)
    if aliases is not None:
        mapped = aliases.get(squashed)
        if mapped is None and closed is not None and value.strip().casefold() in closed:
            mapped = value.strip().casefold()
        return mapped
    if closed is not None:
        return value.strip().casefold() if value.strip().casefold() in closed else None
    return value.strip().strip("\"'").strip().casefold()
