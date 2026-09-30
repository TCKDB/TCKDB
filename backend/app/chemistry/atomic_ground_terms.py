"""Ground terms and fine-structure levels of the neutral atoms TCKDB checks.

This is the one place TCKDB writes down what an isolated atom's electronic
ground state is. It exists so an upload can be told, precisely, that a
one-atom species carries an electronic partition function it did not state:

* oxygen is ``3P`` with three J-levels (``J = 2, 1, 0`` at 0, 158.265 and
  226.977 cm^-1, ``g = 5, 3, 1``), so ``q_el(298 K) ~ 6.7`` and not the
  ``3`` that the spin multiplicity alone gives;
* chlorine is ``2P`` with ``J = 3/2, 1/2`` at 0 and 882.3515 cm^-1
  (``g = 4, 2``), so ``q_el(298 K) ~ 4.03`` and not ``2``.

Source
------
Every ``J`` and level energy below is from the NIST Atomic Spectra Database
(Kramida, Ralchenko, Reader and NIST ASD Team, *NIST Atomic Spectra
Database*, ver. 5.12, https://physics.nist.gov/asd, doi:10.18434/T4W30F),
"Levels" query for each neutral atom ("X I"), retrieved 2026-09-30, lowest-configuration ground term only.
``g`` is always ``2J + 1``, computed here rather than copied: the ASD ``g``
column for S I lists 4, 2, 1 for the ``3P`` levels, which is not ``2J + 1``
(5, 3, 1) and is not used.

Scope
-----
Covered: H through Ar and Br and I, neutral, gas-phase ground term. Closed
shells (He, Be, Ne, Mg, Ar) and S states (H, Li, N, Na, P) are included so
that "this atom's ground term is an S term, so spin multiplicity alone is
the whole electronic partition function" is a table lookup and not an
absence of one.

Left out, deliberately:

* ions (a cation's term is not its neutral parent's) and anything above Ar
  other than Br and I;
* excited terms. ``O(1D)`` sits at 15 867.862 cm^-1, where
  ``exp(-hc*E/kT)`` at 298 K is ~1e-33; no atom listed here has a term
  that matters below ~2000 K other than the ground term's own fine
  structure. The table is a *ground-term* table, not a spectrum;
* transition metals and lanthanides, whose ground terms and low-lying
  configurations need real per-element care;
* isotopic and hyperfine structure, which does not change ``q_el`` at any
  temperature TCKDB stores.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

__all__ = [
    "ATOMIC_GROUND_TERMS",
    "AtomicGroundTerm",
    "ParsedAtomicTerm",
    "declared_term_multiplicity",
    "ground_term_for",
    "parse_atomic_term",
]

_L_LETTERS = "SPDFGHIK"


@dataclass(frozen=True)
class AtomicGroundTerm:
    """A neutral atom's ground term and its fine-structure levels.

    :param element: Element symbol.
    :param two_s: ``2S``, so the multiplicity is ``two_s + 1``.
    :param l: Orbital angular momentum quantum number ``L`` (0 for S, 1 for P).
    :param levels: ``(2J, energy_cm1)`` for each J-level of the ground term,
        ascending in energy; the first is the ground level.
    """

    element: str
    two_s: int
    l: int  # noqa: E741 - the quantum number is spelled L
    levels: tuple[tuple[int, float], ...]

    @property
    def multiplicity(self) -> int:
        return self.two_s + 1

    @property
    def term_symbol(self) -> str:
        return f"{self.multiplicity}{_L_LETTERS[self.l]}"

    @property
    def is_s_term(self) -> bool:
        """True when spin multiplicity is the whole electronic degeneracy."""
        return self.l == 0

    @property
    def total_degeneracy(self) -> int:
        """``(2S+1)(2L+1)``, the degeneracy of the term without spin-orbit."""
        return self.multiplicity * (2 * self.l + 1)

    @property
    def level_degeneracies(self) -> tuple[int, ...]:
        """``2J + 1`` for each J-level, in the order of :attr:`levels`."""
        return tuple(two_j + 1 for two_j, _ in self.levels)

    @property
    def ground_degeneracy(self) -> int:
        return self.levels[0][0] + 1

    @property
    def cumulative_degeneracies(self) -> tuple[int, ...]:
        """Ground-level degeneracies a depositor can legitimately state.

        Energy order matters: a J-resolved ground state is the first level
        (O: 5), and lumping the lowest levels together gives the running sums
        (O: 5, 8, 9; C: 1, 4, 9; Cl: 4, 6). The last entry is the unsplit
        term, ``(2S+1)(2L+1)``. A degeneracy that is only reachable by
        skipping a lower level (O with g=3 lowest, C with g=5 lowest) is not
        in this set.
        """
        out: list[int] = []
        total = 0
        for g in self.level_degeneracies:
            total += g
            out.append(total)
        return tuple(out)

    def electronic_partition_function(self, temperature_k: float = 298.15) -> float:
        """``q_el = sum g_i exp(-E_i / kT)`` over the ground term's J-levels."""
        c2 = 1.438776877  # hc/k, cm K
        return sum((two_j + 1) * math.exp(-c2 * e / temperature_k) for two_j, e in self.levels)

    def spin_only_free_energy_error_kj_mol(self, temperature_k: float = 298.15) -> float:
        """``-RT ln(q_el / g_spin)``: how far G(true) sits from G(spin-only).

        Negative: the spin-only partition function is too small, so a
        spin-only G is too *high* by this magnitude (O: -2.00, Cl: -1.74 kJ/mol
        at 298.15 K).
        """
        r = 8.314462618e-3
        return -r * temperature_k * math.log(self.electronic_partition_function(temperature_k) / self.multiplicity)

    @property
    def spin_orbit_shift_kj_mol(self) -> float:
        """Ground J-level minus the (2J+1)-weighted mean of the term, in kJ/mol.

        The size of the ``soc_total`` correction a depositor is expected to
        apply (O: -0.93, Cl: -3.52).
        """
        weights = self.level_degeneracies
        mean = sum(g * e for g, (_, e) in zip(weights, self.levels, strict=True)) / sum(weights)
        return -mean * 0.011962656


def _t(element: str, two_s: int, orbital_l: int, *levels: tuple[int, float]) -> AtomicGroundTerm:
    return AtomicGroundTerm(element=element, two_s=two_s, l=orbital_l, levels=tuple(levels))


#: NIST ASD, retrieved 2026-09-30. ``(2J, E / cm^-1)``.
ATOMIC_GROUND_TERMS: dict[str, AtomicGroundTerm] = {
    t.element: t
    for t in (
        _t("H", 1, 0, (1, 0.0)),
        _t("He", 0, 0, (0, 0.0)),
        _t("Li", 1, 0, (1, 0.0)),
        _t("Be", 0, 0, (0, 0.0)),
        _t("B", 1, 1, (1, 0.0), (3, 15.287)),
        _t("C", 2, 1, (0, 0.0), (2, 16.4167130), (4, 43.4134567)),
        _t("N", 3, 0, (3, 0.0)),
        _t("O", 2, 1, (4, 0.0), (2, 158.265), (0, 226.977)),
        _t("F", 1, 1, (3, 0.0), (1, 404.141)),
        _t("Ne", 0, 0, (0, 0.0)),
        _t("Na", 1, 0, (1, 0.0)),
        _t("Mg", 0, 0, (0, 0.0)),
        _t("Al", 1, 1, (1, 0.0), (3, 112.061)),
        _t("Si", 2, 1, (0, 0.0), (2, 77.115), (4, 223.157)),
        _t("P", 3, 0, (3, 0.0)),
        _t("S", 2, 1, (4, 0.0), (2, 396.05648), (0, 573.59573)),
        _t("Cl", 1, 1, (3, 0.0), (1, 882.3515)),
        _t("Ar", 0, 0, (0, 0.0)),
        _t("Br", 1, 1, (3, 0.0), (1, 3685.24)),
        _t("I", 1, 1, (3, 0.0), (1, 7602.9762)),
    )
}


def ground_term_for(element: str) -> AtomicGroundTerm | None:
    """The neutral atom's ground term, or ``None`` when TCKDB has no entry."""
    return ATOMIC_GROUND_TERMS.get(element)


# ---------------------------------------------------------------------------
# Term-symbol parsing
# ---------------------------------------------------------------------------

_SUPERSCRIPTS = str.maketrans(
    "\u2070\u00b9\u00b2\u00b3\u2074\u2075\u2076\u2077\u2078\u2079"
    "\u2080\u2081\u2082\u2083\u2084\u2085\u2086\u2087\u2088\u2089\u2044",
    "01234567890123456789/",
)

# ``3P``, ``3P2``, ``3P_2``, ``2P3/2``, and the parity forms ``3Po``, ``2P\u00b0``,
# ``2P*`` (NIST ASCII), each optionally with J.
_ATOMIC_TERM = re.compile(rf"^(?P<mult>\d+)(?P<l>[{_L_LETTERS}])(?:[o\u00b0*])?(?:_?(?P<j>\d+)(?:/(?P<half>2))?)?$")
_LEADING_MULTIPLICITY = re.compile(r"^(?P<mult>\d+)[A-Za-z]")


@dataclass(frozen=True)
class ParsedAtomicTerm:
    """An atomic term symbol read as ``(2S+1, L, 2J or None)``."""

    multiplicity: int
    l: int  # noqa: E741 - the quantum number is spelled L
    two_j: int | None


def _normalise(text: str) -> str:
    """Drop whitespace, a caret prefix and braces: ``^3P_2``, ``2P_{3/2}``."""
    text = text.translate(_SUPERSCRIPTS)
    return re.sub(r"[\s^{}]+", "", text)


def parse_atomic_term(text: str | None) -> ParsedAtomicTerm | None:
    """Read ``3P``, ``3P2``, ``3P_2``, ``2P3/2`` (and superscript forms).

    Returns ``None`` for anything else, including every molecular term
    (``2Pi``, ``1A1``), so a caller can validate what it understands and stay
    silent about what it does not.
    """
    if not text:
        return None
    m = _ATOMIC_TERM.match(_normalise(text))
    if m is None:
        return None
    two_j: int | None = None
    if m["j"] is not None:
        two_j = int(m["j"]) if m["half"] else 2 * int(m["j"])
    return ParsedAtomicTerm(multiplicity=int(m["mult"]), l=_L_LETTERS.index(m["l"]), two_j=two_j)


def declared_term_multiplicity(text: str | None) -> int | None:
    """The spin multiplicity a term symbol states, when it states one.

    Only a symbol that *starts* with the multiplicity digit is read
    (``3P``, ``1A1``, ``2Pi``, ``3Sigma-g``). A state-label prefix (``X2Pi``)
    or a bare irrep (``A1``, ``B1u``) is ambiguous between a multiplicity and
    a label, so it is not read: silence beats a false contradiction.
    """
    if not text:
        return None
    m = _LEADING_MULTIPLICITY.match(_normalise(text))
    return int(m["mult"]) if m else None
