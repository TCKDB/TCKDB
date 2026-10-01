"""Read the summary block of a Gaussian composite-method log (ADR 0021, P3b).

A Gaussian composite job (``CBS-QB3``, ``G4``, ...) runs several internal steps
and ends with one summary block. The block is the only place the method's own
answer is printed, so this module reads it and nothing else. It is pure text in,
values out: no database, no TCKDB schema objects.

What a block holds, and what each number includes
--------------------------------------------------
Gaussian 09 manual (``k_cbs.htm``, ``k_g1.htm``) and the real logs under
``tests/fixtures/gaussian_composite``::

    Temperature=               298.150000 Pressure=                       1.000000
     E(ZPE)=                      0.072623 E(Thermal)=                     0.078657
     E(SCF)=                   -282.684490 DE(MP2)=                       -1.027708
     DE(CBS)=                    -0.099901 DE(MP34)=                      -0.030782
     DE(CCSD)=                   -0.034546 DE(Int)=                        0.032180
     DE(Empirical)=              -0.047151
     CBS-QB3 (0 K)=            -283.819775 CBS-QB3 Energy=              -283.813741

* ``E(ZPE)`` is the recipe's own zero-point energy, already multiplied by the
  recipe's scale factor (CBS-QB3 uses 0.99 on B3LYP/CBSB7). It is positive.
* ``<METHOD> (0 K)`` is the 0 K energy *including* that ZPE:
  ``E0 = Eelec + ZPE`` (the manual's own definition).
* The CBS-family terms (``E(SCF)`` and every ``DE(...)``, the empirical term
  among them) sum to the **ZPE-free** electronic energy, so
  ``sum(terms) + E(ZPE) = E0``. The printed ``E(ZPE)``/``E0`` are rounded to six
  decimals, as is every term.
* The ZPE-free energy is **not printed** by any block, so it is not returned
  here. A caller that wants it has to compute it, and TCKDB never stores a value
  it computed. :func:`implied_electronic_energy_hartree` is the comparison-only
  derivation, kept separate and named so it cannot be mistaken for a parse.

Which methods are read, and why only these
------------------------------------------
Exactly the methods for which a real Gaussian log was available to check the
layout: ``CBS-QB3``, ``ROCBS-QB3``, ``CBS-4M``, ``G3``, ``G4`` and ``G4MP2``.
``CBS-APNO``, ``G3B3``, ``G3MP2``, ``G3MP2B3``, ``W1U``, ``W1BD`` and ``W1RO``
have no real log here; they return ``None`` (reject, do not guess). ``W1`` is
also a different shape: it prints two tables (before and after the spin
correction) and which one is "the" answer is the manual's to say, not ours.

* The CBS family prints one term per line pair and the terms are read and
  cross-checked against ``E0``. A block with a term missing, renamed or
  reordered, or whose terms disagree with ``E0`` beyond printed precision, is
  refused.
* The G-family blocks mislabel their middle lines (a real G4 log prints the
  Hartree-Fock limit under ``DE(HF)`` and repeats ``E(Empiric)``), so the
  middle lines are not read as terms at all. Only ``E(ZPE)`` and ``E0`` are.

``CBS-QB3`` and ``ROCBS-QB3`` print the identical label ``CBS-QB3 (0 K)``, and
``CBS-4M`` prints ``CBS-4 (0 K)``. The summary alone cannot say which recipe
ran, so the route line (``# ... rocbs-qb3 ...``) names the method and the label
must agree with it. A log whose route names no supported method, or names two,
is refused.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

from tckdb_schemas.fragments.calculation import composite_arithmetic_tolerance_hartree

from app.chemistry.method_names import method_identity_key

_NUMBER = r"-?\d+\.\d+"

_CBS_QB3_TERMS = ("E(SCF)", "DE(MP2)", "DE(CBS)", "DE(MP34)", "DE(CCSD)", "DE(Int)", "DE(Empirical)")
_CBS_4M_TERMS = ("E(SCF)", "DE(MP2)", "DE(CBS)", "DE(MP34)", "DE(Int)", "DE(Empirical)")


@dataclass(frozen=True)
class _MethodLayout:
    """How one supported method's block looks."""

    label: str
    """Text before ``(0 K)``; several methods share one label."""
    terms: tuple[str, ...] | None
    """The ordered term labels, or ``None`` when the block's middle is not read."""


#: Identity key of the method -> its block layout. Every entry has a real log
#: in ``tests/fixtures/gaussian_composite`` (see the README there).
_LAYOUTS: dict[str, _MethodLayout] = {
    "cbs-qb3": _MethodLayout("CBS-QB3", _CBS_QB3_TERMS),
    "rocbs-qb3": _MethodLayout("CBS-QB3", _CBS_QB3_TERMS),
    "cbs-4m": _MethodLayout("CBS-4", _CBS_4M_TERMS),
    "g3": _MethodLayout("G3", None),
    "g4": _MethodLayout("G4", None),
    "g4mp2": _MethodLayout("G4MP2", None),
}

#: Methods the module recognises as composite but declines to read.
UNREAD_COMPOSITE_METHOD_KEYS = frozenset(
    {"cbs-apno", "g3b3", "g3mp2", "g3mp2b3", "w1u", "w1bd", "w1ro", "w1", "g1", "g2", "g2mp2"}
)

_TEMPERATURE_LINE = re.compile(rf"^\s*Temperature=\s+{_NUMBER}\s+Pressure=\s+{_NUMBER}\s*$")
_ZPE_LINE = re.compile(rf"^\s*E\(ZPE\)=\s+(?P<zpe>{_NUMBER})\s+E\(Thermal\)=\s+{_NUMBER}\s*$")
_PAIR = re.compile(rf"(?P<label>[A-Za-z]+\([A-Za-z0-9]+\))=\s+(?P<value>{_NUMBER})")
_MAX_BLOCK_LINES = 12


@dataclass(frozen=True)
class CompositeLogTerm:
    """One printed term of a CBS-family block, in print order."""

    label: str
    value_hartree: float


@dataclass(frozen=True)
class GaussianCompositeSummary:
    """The values a Gaussian composite summary block states.

    :param method_key: Identity key of the method the route names
        (``cbs-qb3``, ``rocbs-qb3``, ``cbs-4m``, ``g3``, ``g4``, ``g4mp2``).
    :param e0_hartree: ``<METHOD> (0 K)``: the 0 K energy including the recipe ZPE.
    :param recipe_zpe_hartree: ``E(ZPE)``: the recipe's scaled zero-point energy.
    :param terms: The printed terms in print order (CBS family); empty for the
        G family, whose middle lines are not read.
    """

    method_key: str
    e0_hartree: float
    recipe_zpe_hartree: float
    terms: tuple[CompositeLogTerm, ...]


def implied_electronic_energy_hartree(summary: GaussianCompositeSummary) -> float:
    """``E0 - E(ZPE)``: the ZPE-free energy the block implies. **Comparison only.**

    No block prints this number. It is derived from two printed ones through the
    manual's definition ``E0 = Eelec + ZPE``, so it carries the rounding of both.
    It exists so a deposited ZPE-free energy can be compared with the log; it is
    never stored.
    """
    return summary.e0_hartree - summary.recipe_zpe_hartree


_DASH_ROW = re.compile(r"^-{8,}$")


def _first_route(lines: list[str]) -> str | None:
    """The first echoed route section: a ``#`` line between two rows of dashes.

    Written here rather than reusing ``extract_gaussian_route_text`` because that
    one only recognises dash rows of 60 or more characters, so a short route such
    as ``#CBS-QB3 opt freq`` (a 17-character row) is passed over for a later
    link's route.
    """
    for index, line in enumerate(lines[:-1]):
        if not _DASH_ROW.match(line.strip()) or not lines[index + 1].strip().startswith("#"):
            continue
        parts: list[str] = []
        for follow in lines[index + 1 :]:
            if _DASH_ROW.match(follow.strip()):
                return " ".join(parts)
            parts.append(follow.strip())
        return None
    return None


def _route_method_key(lines: list[str]) -> str | None:
    """The one supported composite method key the route names, else ``None``."""
    route = _first_route(lines)
    if not route:
        return None
    found: set[str] = set()
    for token in route.split():
        key = method_identity_key(token.split("=", 1)[0].lstrip("#"))
        if key in _LAYOUTS or key in UNREAD_COMPOSITE_METHOD_KEYS:
            found.add(key)
    if len(found) != 1:
        return None
    (key,) = found
    return key if key in _LAYOUTS else None


def _parse_terms(
    lines: list[str], expected: tuple[str, ...]
) -> tuple[CompositeLogTerm, ...] | None:
    """Read ``lines`` as exactly the ``expected`` labels, in order, else ``None``."""
    terms: list[CompositeLogTerm] = []
    for line in lines:
        pairs = list(_PAIR.finditer(line))
        # The pairs must account for the whole line, so a stray token is a refusal.
        if not pairs or _PAIR.sub("", line).strip():
            return None
        terms.extend(CompositeLogTerm(p["label"], float(p["value"])) for p in pairs)
    if tuple(t.label for t in terms) != expected:
        return None
    return tuple(terms)


def parse_gaussian_composite_summary(text: str | None) -> GaussianCompositeSummary | None:
    """Read the composite summary block of a Gaussian log, or ``None``.

    ``None`` means *not read*, for every reason: not a Gaussian composite log,
    a method outside the supported set, a missing or partial block, terms that
    contradict ``E0``, two blocks that disagree, or a non-finite number. It never
    returns a partial result.

    :param text: Decoded log text.
    """
    if not text:
        return None
    lines = text.splitlines()
    method_key = _route_method_key(lines)
    if method_key is None:
        return None
    layout = _LAYOUTS[method_key]

    zero_k = re.compile(
        rf"^\s*{re.escape(layout.label)}\s?\(0 K\)=\s+(?P<e0>{_NUMBER})\s+"
        rf"{re.escape(layout.label)}\s+Energy=\s+{_NUMBER}\s*$"
    )
    found: list[GaussianCompositeSummary] = []
    for index, line in enumerate(lines):
        match = zero_k.match(line)
        if match is None:
            continue
        summary = _read_block(lines, index, float(match["e0"]), method_key, layout)
        if summary is None:
            # A (0 K) line whose block is partial: refuse the log, never skip to another.
            return None
        found.append(summary)
    if not found:
        return None
    last = found[-1]
    if any(
        s.e0_hartree != last.e0_hartree or s.recipe_zpe_hartree != last.recipe_zpe_hartree for s in found
    ):
        return None
    return last


def _read_block(
    lines: list[str],
    zero_k_index: int,
    e0: float,
    method_key: str,
    layout: _MethodLayout,
) -> GaussianCompositeSummary | None:
    start = None
    for back in range(1, _MAX_BLOCK_LINES + 1):
        probe = zero_k_index - back
        if probe < 0:
            break
        if _TEMPERATURE_LINE.match(lines[probe]):
            start = probe
            break
    if start is None:
        return None
    zpe_match = _ZPE_LINE.match(lines[start + 1]) if start + 1 < zero_k_index else None
    if zpe_match is None:
        return None
    zpe = float(zpe_match["zpe"])
    if not (math.isfinite(zpe) and math.isfinite(e0)) or zpe < 0:
        return None

    terms: tuple[CompositeLogTerm, ...] = ()
    if layout.terms is not None:
        parsed = _parse_terms(lines[start + 2 : zero_k_index], layout.terms)
        if parsed is None:
            return None
        # e0, zpe and every term are six-decimal roundings: n = terms + 2.
        tolerance = composite_arithmetic_tolerance_hartree(len(parsed) + 2)
        if abs(sum(t.value_hartree for t in parsed) + zpe - e0) > tolerance + 1e-12:
            return None
        terms = parsed
    return GaussianCompositeSummary(
        method_key=method_key,
        e0_hartree=e0,
        recipe_zpe_hartree=zpe,
        terms=terms,
    )
