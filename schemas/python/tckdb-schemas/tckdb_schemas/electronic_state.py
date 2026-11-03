"""The accepted shapes of an electronic-state label and of a term symbol, and their one canonical form.

``species_entry.electronic_state_label`` and ``species_entry.term_symbol`` are
text a depositor types, and the server joins them into the labels the website
shows (``species_entry_label``, a network ``state_label``). Free text there lets
a typed ``R`` on a ground-state entry read as "R enantiomer", and lets anything
at all appear on a public page. So both fields accept only the strict shapes
below, refuse everything else with a coded 422 (``electronic_state_label_invalid``
or ``term_symbol_invalid``), and **store one canonical spelling** of what they
accept, so ``2Pi``, ``^2Pi``, ``^{2}Pi`` and ``2Π`` are one value and one
species-entry identity.

This module is the one place the grammar and the canonical form live.

Matching
--------
* Trim surrounding whitespace, then match the **NFD** form of the text (a letter
  with a tilde is a letter plus U+0303; ``Ã`` is read as ``A`` + U+0303), and
  check the length of the trimmed NFC text against the cap.
* ``ELECTRONIC_STATE_LABEL_SOURCE`` and ``TERM_SYMBOL_SOURCE`` are written in the
  subset of regular-expression syntax that Python's ``re`` and JavaScript's
  ``RegExp`` (``u`` flag) read identically (non-capturing groups, ``[0-9]``
  rather than ``\\d``, no inline flags, no named groups, literal characters
  except the combining tilde, which is ``\\u0303``), to be anchored as
  ``^(?:SOURCE)$``.

Canonical form (what is stored)
-------------------------------
A **term symbol** is ``[X ]{mult}{core}``. Everything is Unicode:

* the multiplicity is a superscript digit (``¹ ² ³ ⁴ ⁵ ⁶ ⁷ ⁸ ⁹``); ``^``, ``{`` and ``}`` are dropped;
* a state letter in front is written as in a label (below) followed by one space (``X ²Π``);
* an atomic term is ``L`` + optional parity ``°`` (``o``, ``*`` and ``°`` all become ``°``) +
  J in subscript digits with the fraction slash U+2044 (``³P₂``, ``²P₃⁄₂``, ``¹S₀``);
* a linear term is the Greek capital (``Σ Π Δ Φ Γ``; ``Sigma``, ``Pi``... are read as them) +
  parity ``g``/``u`` + sign ``⁺``/``⁻`` in that order whichever order was typed
  (``³Σg⁻`` for ``3Sigma-g`` and ``3Sigma_g-``) + optional Omega in subscripts
  (``²Π₃⁄₂``, ``³Π₀⁺``);
* a polyatomic term is the Mulliken species ``A B E T`` + subscript index + ``g``/``u`` + prime
  ``′`` or ``″`` (``¹A₁``, ``²A′``, ``¹A₁g``, ``²E″``; ``'`` is ``′``, ``''``, ``"`` and ``″`` are ``″``).

A **state label** is a letter (+ combining tilde U+0303, composed to ``Ã`` where a composed
form exists, i.e. NFC; + primes ``′``/``″``) or an ordinal ``S0 S1 T1 D0 Q0``.
``electronic_state_examples.json`` carries every accepted example with its canonical form
(``canonical``), so a frontend can mirror both acceptance and display.

The depositor's original spelling is kept in ``term_symbol_raw`` when it differs from the
canonical form and the depositor gave no ``term_symbol_raw`` of their own (see
``SpeciesEntryIdentityWriteValidatorMixin``). There is no raw field for a state label, so a
label is only canonicalised.

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
TCKDB's own computed facet words are ``R S r s E Z e z Ra Sa M P ? -``. The letters
``R S Z M P`` and ``r s z m p`` are refused as state letters, so a typed state label
cannot be read as a descriptor. ``E``/``e`` are accepted (CO E¹Π, N₂ E³Σg⁺, NO E²Σ⁺): a
bare ``E`` is also a stereo descriptor, so the server writes the state part of a joined label
with the word ``state`` (see
``app.services.scientific_read.species_identity.species_entry_label``), and that marker is
what keeps ``state E`` and ``E`` apart. A bare ``S`` is not a state letter in any case: the
singlet is ``S0``/``S1``, which carry an index.

Nothing here ever echoes the refused value: a refusal names the field and the
accepted shapes only.
"""

from __future__ import annotations

import json
import re
import unicodedata
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
    "canonical_electronic_state_label",
    "canonical_term_symbol",
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

# --- building blocks (strings, so the final sources are plain text; matched against NFD text) ---

# A bare state letter: every Latin letter except the stereo-descriptor letters R S Z (and r s z)
# and the helicity letters M P (and m p). E/e are state letters.
_LETTER = r"(?:[A-LNOQT-Y]|[a-lnoqt-y])"
# Tilde (polyatomic state): ASCII "~" or the combining tilde U+0303.
_TILDE = r"(?:~|̃)"
# Primes: ' '' " , U+2032 (prime), U+2033 (double prime).
_PRIMES = r"""(?:''?|"|′′?|″)"""
_LETTER_LABEL = rf"(?:{_LETTER}{_TILDE}?{_PRIMES}?)"
# Ordinal: singlet/doublet/triplet/quartet + energy-order index.
_ORDINAL = r"[SDTQ](?:0|[1-9][0-9]?)"

# Multiplicity 2S+1: 1-9, optionally ^2 or ^{2}, or a superscript digit.
_MULT = r"(?:\^\{[1-9]\}|\^?[1-9]|[¹²³⁴-⁹])"
# Greek capital or spelled-out name.
_LAMBDA = r"(?:Sigma|Pi|Delta|Phi|Gamma|Σ|Π|Δ|Φ|Γ)"
_PARITY = r"(?:_?[gu])"
_SIGN = r"(?:\^?[+\-−⁺⁻])"
# Omega (the total projection), 3/2 or 0+ or 1 ..., with "_", "_{...}" or bare, in ASCII or subscript digits.
_OMEGA_BODY = r"(?:[0-9]{1,2}(?:/2)?[+\-⁺⁻]?|[₀-₉]{1,2}(?:[/⁄]₂)?[+\-⁺⁻]?)"
_OMEGA = rf"(?:_\{{{_OMEGA_BODY}\}}|_?{_OMEGA_BODY})"
_LINEAR = rf"{_LAMBDA}(?:{_PARITY}{_SIGN}?|{_SIGN}{_PARITY}?)?{_OMEGA}?"
# Mulliken symmetry species A, B, E, T with an index, parity and primes.
_IRREP = r"[ABET][1-3₁-₃]?[gu]?" + _PRIMES + r"?"
# Atomic L (the letters the repo's atomic-term parser reads), parity, and J.
_J = r"(?:[0-9]{1,2}(?:/2)?|[₀-₉]{1,2}(?:[/⁄]₂)?)"
_ATOMIC = rf"[SPDFGHIK](?:o|°|\*)?(?:_\{{{_J}\}}|_?{_J})?"

# --- the two grammars ---------------------------------------------------------------------------

#: electronic_state_label: a letter label (letter, tilde, primes) or an ordinal.
ELECTRONIC_STATE_LABEL_SOURCE = rf"(?:{_LETTER_LABEL}|{_ORDINAL})"

#: term_symbol: optional state letter (one optional space), then multiplicity + atomic/molecular/polyatomic term.
TERM_SYMBOL_SOURCE = rf"(?:{_LETTER_LABEL} ?)?{_MULT}(?:{_ATOMIC}|{_LINEAR}|{_IRREP})"

ELECTRONIC_STATE_LABEL_PATTERN = re.compile(ELECTRONIC_STATE_LABEL_SOURCE)
TERM_SYMBOL_PATTERN = re.compile(TERM_SYMBOL_SOURCE)

ELECTRONIC_STATE_LABEL_ACCEPTED_SHAPES: tuple[str, ...] = (
    "a spectroscopic letter, with an optional tilde and primes: X, A, B, E, a, X~, A', A''",
    "a state ordinal: S0, S1, T1, D0, Q0",
)
TERM_SYMBOL_ACCEPTED_SHAPES: tuple[str, ...] = (
    "an atomic term: 3P, 3P2, 2P_{3/2}, 1S0",
    "a linear-molecule term: 2Pi, 3Sigma_g-, 1Sigma_g+, 2Pi_{3/2}",
    "a polyatomic term: 1A1, 2A', 3B2, 1A1g",
    "any of these with a state letter in front: X2Pi, X 1A1, X^2Pi",
    "the multiplicity may be a digit, ^2, ^{2} or a superscript digit, and Greek letters may be spelled out or written "
    "as Greek; every accepted spelling is stored as one canonical form with a superscript multiplicity, Greek capitals "
    "and subscript indices (for example 2Pi is stored as ²Π)",
)

# --- canonical form ----------------------------------------------------------------------------

_SUPERSCRIPT = dict(zip("0123456789", "⁰¹²³⁴⁵⁶⁷⁸⁹"))
_SUBSCRIPT = dict(zip("0123456789", "₀₁₂₃₄₅₆₇₈₉"))
_SUB_TO_ASCII = {v: k for k, v in _SUBSCRIPT.items()}
_SUPER_TO_ASCII = {v: k for k, v in _SUPERSCRIPT.items()}
_GREEK = {
    "Sigma": "Σ", "Pi": "Π", "Delta": "Δ", "Phi": "Φ", "Gamma": "Γ",
    "Σ": "Σ", "Π": "Π", "Δ": "Δ", "Φ": "Φ", "Γ": "Γ",
}  # fmt: skip
_SIGN_CANONICAL = {"+": "⁺", "-": "⁻", "−": "⁻", "⁺": "⁺", "⁻": "⁻"}

# Python-only parse of an already validated term symbol, to canonicalise it part by part.
_TERM_PARTS = re.compile(
    rf"(?:({_LETTER_LABEL}) ?)?({_MULT})(?:({_ATOMIC})|({_LINEAR})|({_IRREP}))"
)
_LABEL_PARTS = re.compile(rf"({_LETTER})({_TILDE})?({_PRIMES})?")
_ATOMIC_PARTS = re.compile(r"([SPDFGHIK])(o|°|\*)?(.*)")
_LINEAR_PARTS = re.compile(
    rf"({_LAMBDA})(?:_?([gu])(\^?[+\-−⁺⁻])?|(\^?[+\-−⁺⁻])(?:_?([gu]))?)?({_OMEGA})?"
)
_IRREP_PARTS = re.compile(r"([ABET])([1-3₁-₃])?([gu])?(.*)")


def _primes(text: str | None) -> str:
    if not text:
        return ""
    return "′" if text in {"'", "′"} else "″"


def _digits_sub(text: str) -> str:
    """Subscript digits and the fraction slash, dropping the ``_``/braces grouping."""

    out = []
    for ch in text:
        if ch in "_{}":
            continue
        if ch in _SUBSCRIPT:
            out.append(_SUBSCRIPT[ch])
        elif ch in "/⁄":
            out.append("⁄")
        else:
            out.append(ch)
    return "".join(out)


def _canonical_letter_label(text: str) -> str:
    m = _LABEL_PARTS.fullmatch(text)
    assert m is not None  # validated before this is called
    letter, tilde, primes = m.groups()
    return letter + ("̃" if tilde else "") + _primes(primes)


def _canonical_term(text: str) -> str:
    m = _TERM_PARTS.fullmatch(text)
    assert m is not None  # validated before this is called
    prefix, mult, atomic, linear, irrep = m.groups()
    digit = next(ch for ch in mult if ch in "123456789" or ch in _SUPER_TO_ASCII)
    digit = _SUPER_TO_ASCII.get(digit, digit)
    head = (_canonical_letter_label(prefix) + " " if prefix else "") + _SUPERSCRIPT[digit]
    if atomic is not None:
        letter, parity, tail = _ATOMIC_PARTS.fullmatch(atomic).groups()
        return head + letter + ("°" if parity else "") + _digits_sub(tail)
    if linear is not None:
        lam, p1, s1, s2, p2, omega = _LINEAR_PARTS.fullmatch(linear).groups()
        parity = p1 or p2 or ""
        sign = (s1 or s2 or "").lstrip("^")
        out = _GREEK[lam] + parity + (_SIGN_CANONICAL[sign] if sign else "")
        if omega:
            body = omega.replace("_", "").strip("{}")
            if body and body[-1] in "+-⁺⁻":
                body = body[:-1] + _SIGN_CANONICAL[body[-1]]
            out += _digits_sub(body)
        return head + out
    base, index, parity, primes = _IRREP_PARTS.fullmatch(irrep).groups()
    return head + base + (_SUBSCRIPT[_SUB_TO_ASCII.get(index, index)] if index else "") + (parity or "") + _primes(primes)


def _nfd(text: str) -> str:
    return unicodedata.normalize("NFD", text)


def _within(text: str, cap: int) -> bool:
    return 0 < len(unicodedata.normalize("NFC", text)) <= cap


def is_valid_electronic_state_label(value: str) -> bool:
    """Say whether ``value`` (already trimmed) is an accepted electronic-state label."""

    return (
        isinstance(value, str)
        and _within(value, ELECTRONIC_STATE_LABEL_MAX_LENGTH)
        and ELECTRONIC_STATE_LABEL_PATTERN.fullmatch(_nfd(value)) is not None
    )


def is_valid_term_symbol(value: str) -> bool:
    """Say whether ``value`` (already trimmed) is an accepted term symbol."""

    return (
        isinstance(value, str)
        and _within(value, TERM_SYMBOL_MAX_LENGTH)
        and TERM_SYMBOL_PATTERN.fullmatch(_nfd(value)) is not None
    )


def canonical_electronic_state_label(value: str) -> str:
    """The stored spelling of an accepted label (``A~`` and ``A`` + U+0303 are ``Ã``; ``"`` is ``″``).

    :raises ValueError: ``value`` is not an accepted label.
    """

    text = value.strip()
    if not is_valid_electronic_state_label(text):
        raise ValueError("not an accepted electronic-state label")
    nfd = _nfd(text)
    if _ORDINAL_FULL.fullmatch(nfd):
        return nfd
    return unicodedata.normalize("NFC", _canonical_letter_label(nfd))


_ORDINAL_FULL = re.compile(_ORDINAL)


def canonical_term_symbol(value: str) -> str:
    """The stored spelling of an accepted term symbol (``2Pi``, ``^2Pi`` and ``2Π`` are ``²Π``).

    :raises ValueError: ``value`` is not an accepted term symbol.
    """

    text = value.strip()
    if not is_valid_term_symbol(text):
        raise ValueError("not an accepted term symbol")
    return unicodedata.normalize("NFC", _canonical_term(_nfd(text)))


def _refusal(code: str, field: str, shapes: tuple[str, ...], limit: int) -> CodedValidationError:
    # Never echoes the refused value: the detail names the field and the accepted shapes.
    return CodedValidationError(
        code,
        f"{field} is not an accepted shape (at most {limit} characters). Accepted: " + "; ".join(shapes) + ".",
        context={"field": field, "accepted_shapes": list(shapes), "max_length": limit},
        message_prefix=False,
    )


def check_electronic_state_label(value: Any) -> Any:
    """Return the canonical label for an accepted one, ``value`` untouched if ``None``/blank/non-string.

    A non-string is returned as is so Pydantic's own type error reports it.

    :raises CodedValidationError: ``electronic_state_label_invalid``.
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
    return canonical_electronic_state_label(value)


def check_term_symbol(value: Any) -> Any:
    """Return the canonical term symbol for an accepted one, ``value`` untouched if ``None``/blank/non-string.

    :raises CodedValidationError: ``term_symbol_invalid``.
    """

    if not isinstance(value, str) or not value.strip():
        return value
    if not is_valid_term_symbol(value.strip()):
        raise _refusal(
            TERM_SYMBOL_INVALID,
            "term_symbol",
            TERM_SYMBOL_ACCEPTED_SHAPES,
            TERM_SYMBOL_MAX_LENGTH,
        )
    return canonical_term_symbol(value)


@lru_cache(maxsize=1)
def load_examples() -> dict[str, Any]:
    """Load the shipped examples (``electronic_state_examples.json``): accepted, rejected, and canonical forms."""

    text = resources.files("tckdb_schemas").joinpath("electronic_state_examples.json").read_text(encoding="utf-8")
    return json.loads(text)
