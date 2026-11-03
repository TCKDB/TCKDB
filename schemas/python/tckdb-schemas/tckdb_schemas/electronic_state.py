"""The accepted shapes of an electronic-state label and of a term symbol.

``species_entry.electronic_state_label`` and ``species_entry.term_symbol`` are
text a depositor types, and the server joins them into the labels the website
shows (``species_entry_label``, a network ``state_label``). Free text there lets
a typed ``R`` on a ground-state entry read as "R enantiomer", and lets anything
at all appear on a public page. So both fields accept only the strict shapes
below, and refuse everything else with a coded 422 (``electronic_state_label_invalid``
or ``term_symbol_invalid``).

This module is the one place the grammar lives. A frontend mirrors it by
compiling the same pattern source: ``ELECTRONIC_STATE_LABEL_SOURCE`` and
``TERM_SYMBOL_SOURCE`` are written in the subset of regular-expression syntax
that Python's ``re`` and JavaScript's ``RegExp`` (``u`` flag) read identically
(non-capturing groups, ``[0-9]`` rather than ``\\d``, literal characters (never ``\\uXXXX`` escapes), no
inline flags, no named groups), to be anchored as ``^(?:SOURCE)$``. The accepted
and rejected examples are shipped in ``electronic_state_examples.json`` beside
this file so a parity test on either side drives the same table.

Sources
-------
* Spectroscopic letter labels: ``X`` for the ground state, ``A, B, C...`` for
  excited states of the same multiplicity in order of increasing energy, lower
  case ``a, b, c...`` for states of a different multiplicity; a tilde marks a
  polyatomic state (``X~``, ``A~``) and primes mark symmetry (``A'``, ``A''``).
  G. Herzberg, *Molecular Spectra and Molecular Structure I* (1950), ch. I and
  *III* (1966), ch. I; IUPAC Green Book (*Quantities, Units and Symbols in
  Physical Chemistry*, 3rd ed., 2007), section 2.5 (Spectroscopy).
* State ordinals ``S0, S1, T1, D0, Q0``: spin-multiplicity letter (singlet,
  doublet, triplet, quartet) and an energy-order index, the photochemistry
  convention (IUPAC *Glossary of Terms Used in Photochemistry*, 3rd ed., 2007).
* Atomic term symbols ``2S+1 L J`` (``3P``, ``3P2``, ``2P_{3/2}``, ``2Po``):
  Condon and Shortley; NIST Atomic Spectra Database level notation. The J and
  parity spellings are those ``app.chemistry.atomic_ground_terms.parse_atomic_term``
  already reads.
* Linear and diatomic molecular terms ``2S+1 Lambda (g|u) (+|-)``
  (``2Pi``, ``3Sigma_g-``, ``2Pi_{3/2}``): Herzberg I, ch. V; Green Book 2.5.
* Polyatomic terms ``2S+1 (irrep)`` with a Mulliken symmetry species
  (``1A1``, ``2A'``, ``3B2``, ``1A1g``, ``2E''``): Mulliken, *J. Chem. Phys.* 23,
  1997 (1955); Herzberg III. ``app.chemistry.species.derive_term_symbol``
  emits ``1A``, ``1A'``, ``1A1``, ``1A1'``, ``1A1g``, ``1Sigma`` and
  ``1Sigma_g+`` from this family.
* A state letter in front of a term symbol (``X2Pi``, ``X 1A1``, ``X^2Pi``) is
  the usual way a producer writes "ground state, doublet Pi".

Why letters that look like stereo descriptors are refused
---------------------------------------------------------
TCKDB's own computed facet words are ``R S r s E Z e z Ra Sa M P ? -``. A bare
state letter equal to one of them (``R``, ``S``, ``E``, ``Z``, ``M``, ``P``, the
lower-case ``r s e z m p``) is refused, so a typed state label can never be read
as a stereo descriptor. A bare ``S`` is not a state letter in any case: the
singlet is ``S0``/``S1``, which carry an index. The server also marks the state
part of a joined label with the word ``state`` (see
``app.services.scientific_read.species_identity.species_entry_label``), so that
data stored before this rule cannot be misread either.

Nothing here ever echoes the refused value: a refusal names the field and the
accepted shapes only.
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from importlib import resources
from typing import Any

from tckdb_schemas.coded_error import CodedValidationError

__all__ = [
    "ELECTRONIC_STATE_LABEL_ACCEPTED_SHAPES",
    "ELECTRONIC_STATE_LABEL_INVALID",
    "ELECTRONIC_STATE_LABEL_MAX_LENGTH",
    "ELECTRONIC_STATE_LABEL_PATTERN",
    "ELECTRONIC_STATE_LABEL_SOURCE",
    "TERM_SYMBOL_ACCEPTED_SHAPES",
    "TERM_SYMBOL_INVALID",
    "TERM_SYMBOL_MAX_LENGTH",
    "TERM_SYMBOL_PATTERN",
    "TERM_SYMBOL_SOURCE",
    "check_electronic_state_label",
    "check_term_symbol",
    "is_valid_electronic_state_label",
    "is_valid_term_symbol",
    "load_examples",
]

#: Refusal codes (422).
ELECTRONIC_STATE_LABEL_INVALID = "electronic_state_label_invalid"
TERM_SYMBOL_INVALID = "term_symbol_invalid"

#: ``species_entry.electronic_state_label`` is ``String(8)``; ``term_symbol`` is ``String(64)``.
ELECTRONIC_STATE_LABEL_MAX_LENGTH = 8
TERM_SYMBOL_MAX_LENGTH = 64

# --- building blocks (strings, so the final sources are plain text) -----------------------------

# A bare state letter: every Latin letter except TCKDB's stereo-descriptor letters
# (R S E Z and r s e z) and the helicity letters (M P and m p).
_LETTER = r"(?:[A-DF-LNOQT-Y]|[a-df-lnoqt-y])"
# Tilde (polyatomic state): ASCII "~", the combining tilde U+0303, or the precomposed A/a with tilde.
_TILDE = r"(?:~|̃)"
# Primes: ' '' " , U+2032 (prime), U+2033 (double prime).
_PRIMES = r"""(?:''?|"|′′?|″)"""
_LETTER_LABEL = rf"(?:(?:{_LETTER}{_TILDE}?|[Ãã]){_PRIMES}?)"
# Ordinal: singlet/doublet/triplet/quartet + energy-order index.
_ORDINAL = r"[SDTQ](?:0|[1-9][0-9]?)"

# Multiplicity 2S+1: 1-9, optionally ^2 or ^{2}, or a superscript digit.
_MULT = r"(?:\^\{[1-9]\}|\^?[1-9]|[¹²³⁴-⁹])"
# Greek capital or spelled-out name.
_LAMBDA = r"(?:Sigma|Pi|Delta|Phi|Gamma|Σ|Π|Δ|Φ|Γ)"
_PARITY = r"(?:_?[gu])"
_SIGN = r"(?:\^?[+\-−⁺⁻])"
# Omega (the total projection), 3/2 or 0+ or 1 ..., with "_", "_{...}" or bare.
_OMEGA = r"(?:_\{[0-9]{1,2}(?:/2)?[+\-]?\}|_?[0-9]{1,2}(?:/2)?[+\-]?)"
_LINEAR = rf"{_LAMBDA}(?:{_PARITY}{_SIGN}?|{_SIGN}{_PARITY}?)?{_OMEGA}?"
# Mulliken symmetry species A, B, E, T with an index, parity and primes.
_IRREP = r"[ABET][1-3₁-₃]?[gu]?" + _PRIMES + r"?"
# Atomic L (the letters the repo's atomic-term parser reads), parity, and J.
_J = r"(?:[0-9]{1,2}(?:/2)?|[₀-₉]{1,2}(?:[/⁄]₂)?)"
_ATOMIC = rf"[SPDFGHIK](?:o|°|\*)?(?:_\{{{_J}\}}|_?{_J})?"

# --- the two grammars ---------------------------------------------------------------------------

#: electronic_state_label: a letter label, an ordinal, or multiplicity + molecular/polyatomic species.
ELECTRONIC_STATE_LABEL_SOURCE = rf"(?:{_LETTER_LABEL}|{_ORDINAL}|{_MULT}(?:{_LINEAR}|{_IRREP}))"

#: term_symbol: optional state letter (one optional space), then multiplicity + atomic/molecular/polyatomic term.
TERM_SYMBOL_SOURCE = rf"(?:{_LETTER_LABEL} ?)?{_MULT}(?:{_ATOMIC}|{_LINEAR}|{_IRREP})"

ELECTRONIC_STATE_LABEL_PATTERN = re.compile(ELECTRONIC_STATE_LABEL_SOURCE)
TERM_SYMBOL_PATTERN = re.compile(TERM_SYMBOL_SOURCE)

ELECTRONIC_STATE_LABEL_ACCEPTED_SHAPES: tuple[str, ...] = (
    "a spectroscopic letter, with an optional tilde and primes: X, A, B, a, X~, A', A''",
    "a state ordinal: S0, S1, T1, D0, Q0",
    "a multiplicity and symmetry species: 1A1, 2A', 3B2, 1A'', 2Sigma+",
)
TERM_SYMBOL_ACCEPTED_SHAPES: tuple[str, ...] = (
    "an atomic term: 3P, 3P2, 2P_{3/2}, 1S0",
    "a linear-molecule term: 2Pi, 3Sigma_g-, 1Sigma_g+, 2Pi_{3/2}",
    "a polyatomic term: 1A1, 2A', 3B2, 1A1g",
    "any of these with a state letter in front: X2Pi, X 1A1, X^2Pi",
    "superscript digits and Greek letters are read as their ASCII spellings",
)


# --- the public functions -----------------------------------------------------------------------


def is_valid_electronic_state_label(value: str) -> bool:
    """Say whether ``value`` (already trimmed) is an accepted electronic-state label."""

    return (
        isinstance(value, str)
        and 0 < len(value) <= ELECTRONIC_STATE_LABEL_MAX_LENGTH
        and ELECTRONIC_STATE_LABEL_PATTERN.fullmatch(value) is not None
    )


def is_valid_term_symbol(value: str) -> bool:
    """Say whether ``value`` (already trimmed) is an accepted term symbol."""

    return (
        isinstance(value, str)
        and 0 < len(value) <= TERM_SYMBOL_MAX_LENGTH
        and TERM_SYMBOL_PATTERN.fullmatch(value) is not None
    )


def _refusal(code: str, field: str, shapes: tuple[str, ...], limit: int) -> CodedValidationError:
    # Never echoes the refused value: the detail names the field and the accepted shapes.
    return CodedValidationError(
        code,
        f"{field} is not an accepted shape (at most {limit} characters). Accepted: " + "; ".join(shapes) + ".",
        context={"field": field, "accepted_shapes": list(shapes), "max_length": limit},
        message_prefix=False,
    )


def check_electronic_state_label(value: Any) -> Any:
    """Return ``value`` if it is ``None``/blank or an accepted label; otherwise raise the coded refusal.

    A non-string is returned untouched so Pydantic's own type error reports it.
    """

    if not isinstance(value, str) or not value.strip():
        return value
    if not is_valid_electronic_state_label(value.strip()):
        raise _refusal(
            ELECTRONIC_STATE_LABEL_INVALID,
            "electronic_state_label",
            ELECTRONIC_STATE_LABEL_ACCEPTED_SHAPES,
            ELECTRONIC_STATE_LABEL_MAX_LENGTH,
        )
    return value


def check_term_symbol(value: Any) -> Any:
    """Return ``value`` if it is ``None``/blank or an accepted term symbol; otherwise raise the coded refusal."""

    if not isinstance(value, str) or not value.strip():
        return value
    if not is_valid_term_symbol(value.strip()):
        raise _refusal(
            TERM_SYMBOL_INVALID,
            "term_symbol",
            TERM_SYMBOL_ACCEPTED_SHAPES,
            TERM_SYMBOL_MAX_LENGTH,
        )
    return value


@lru_cache(maxsize=1)
def load_examples() -> dict[str, Any]:
    """Load the shipped accepted/rejected examples (``electronic_state_examples.json``)."""

    text = resources.files("tckdb_schemas").joinpath("electronic_state_examples.json").read_text(encoding="utf-8")
    return json.loads(text)
