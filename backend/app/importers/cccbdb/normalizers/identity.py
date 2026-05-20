"""Identity-field normalization for CCCBDB-parsed species pages.

The helpers here are intentionally conservative: they clean up
whitespace and known string prefixes, but they do **not** invoke RDKit
or perform structural canonicalization. Phase 1 preserves CCCBDB's raw
identity strings verbatim wherever round-tripping is in doubt.
"""

from __future__ import annotations

import re


def collapse_whitespace(value: str | None) -> str | None:
    """Strip leading/trailing whitespace and collapse internal runs to single spaces.

    :param value: Raw string or ``None``.
    :returns: Cleaned string, or ``None`` if input is ``None`` / empty
        after stripping.
    """

    if value is None:
        return None
    cleaned = re.sub(r"\s+", " ", value).strip()
    return cleaned or None


def normalize_formula(value: str | None) -> str | None:
    """Conservative formula normalization.

    Strips whitespace and removes spaces between element symbols and
    counts (``"C 6 H 6"`` -> ``"C6H6"``). Does **not** reorder
    elements or impose Hill order.
    """

    cleaned = collapse_whitespace(value)
    if cleaned is None:
        return None
    return cleaned.replace(" ", "")


def normalize_inchi(value: str | None) -> str | None:
    """Normalize an InChI string.

    Strips whitespace. Does not validate layers or recompute hashes;
    if the input does not start with ``InChI=`` the value is still
    returned (the caller can emit a warning).
    """

    return collapse_whitespace(value)


def normalize_inchikey(value: str | None) -> str | None:
    """Normalize an InChIKey to uppercase, whitespace-stripped form.

    InChIKeys are 27 characters with two hyphens. The normalizer does
    not enforce length; malformed keys are uppercased and returned for
    inspection upstream.
    """

    cleaned = collapse_whitespace(value)
    if cleaned is None:
        return None
    return cleaned.upper()


def normalize_smiles(value: str | None) -> str | None:
    """Whitespace-strip a SMILES string.

    Does **not** call RDKit. Canonicalization (if needed) is a
    Phase-2 concern that lives in the builder layer.
    """

    return collapse_whitespace(value)


def normalize_cas(value: str | None) -> str | None:
    """Trim a CAS number string. No structural validation."""

    return collapse_whitespace(value)


def parse_int_or_none(value: str | None) -> int | None:
    """Parse an integer from a raw cell; return ``None`` for blank /
    non-numeric inputs rather than raising."""

    cleaned = collapse_whitespace(value)
    if cleaned is None:
        return None
    try:
        return int(cleaned)
    except ValueError:
        return None


_STATE_LABEL_RE = re.compile(
    r"""
    ^\s*
    (?P<term_symbol>[A-Z]\s*\d*[A-Za-z+\-']*)?  # e.g. X 1A1, X 2Pi
    .*$
    """,
    re.VERBOSE,
)


def parse_state_label(value: str | None) -> str | None:
    """Return the cleaned raw electronic-state / conformation label.

    We deliberately preserve CCCBDB's original wording (e.g. ``"X 1A1"``,
    ``"X^2 Pi"``) and leave term-symbol parsing to downstream curation.
    """

    cleaned = collapse_whitespace(value)
    if cleaned is None:
        return None
    return cleaned


_MULT_FROM_TERM_RE = re.compile(r"\b([1-9])(?=[A-Z])")


def infer_multiplicity_from_state(state_label: str | None) -> int | None:
    """Best-effort multiplicity from a term-symbol-shaped label.

    Examples:

        ``"X 1A1"``  -> 1
        ``"X 2Pi"``  -> 2
        ``"a 3B1"``  -> 3

    Returns ``None`` if no leading digit is found. The parser does
    **not** call this automatically — multiplicity is left null unless
    CCCBDB states it explicitly. The helper exists so a downstream
    curator can opt in.
    """

    if state_label is None:
        return None
    match = _MULT_FROM_TERM_RE.search(state_label)
    if not match:
        return None
    return int(match.group(1))
