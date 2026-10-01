"""The neutral-atom ground-term table, pinned against NIST ASD, and the term parser.

The expected values here are typed in independently of the module: NIST
Atomic Spectra Database ver. 5.12 (doi:10.18434/T4W30F), "Levels" query,
neutral "X I", lowest configuration, retrieved 2026-09-30. Rows are
``(element, 2S+1, L, [(2J, E/cm^-1), ...])`` in energy order.

Without this a wrong J order, a wrong energy or a wrong L in the table is
invisible to every behavioural test that happens to use a different atom.
"""

from __future__ import annotations

import pytest

from app.chemistry.atomic_ground_terms import (
    ATOMIC_GROUND_TERMS,
    declared_term_multiplicity,
    parse_atomic_term,
)

NIST = [
    ("H", 2, 0, [(1, 0.0)]),
    ("He", 1, 0, [(0, 0.0)]),
    ("Li", 2, 0, [(1, 0.0)]),
    ("Be", 1, 0, [(0, 0.0)]),
    ("B", 2, 1, [(1, 0.0), (3, 15.287)]),
    ("C", 3, 1, [(0, 0.0), (2, 16.4167130), (4, 43.4134567)]),
    ("N", 4, 0, [(3, 0.0)]),
    ("O", 3, 1, [(4, 0.0), (2, 158.265), (0, 226.977)]),
    ("F", 2, 1, [(3, 0.0), (1, 404.141)]),
    ("Ne", 1, 0, [(0, 0.0)]),
    ("Na", 2, 0, [(1, 0.0)]),
    ("Mg", 1, 0, [(0, 0.0)]),
    ("Al", 2, 1, [(1, 0.0), (3, 112.061)]),
    ("Si", 3, 1, [(0, 0.0), (2, 77.115), (4, 223.157)]),
    ("P", 4, 0, [(3, 0.0)]),
    ("S", 3, 1, [(4, 0.0), (2, 396.05648), (0, 573.59573)]),
    ("Cl", 2, 1, [(3, 0.0), (1, 882.3515)]),
    ("Ar", 1, 0, [(0, 0.0)]),
    ("Br", 2, 1, [(3, 0.0), (1, 3685.24)]),
    ("I", 2, 1, [(3, 0.0), (1, 7602.9762)]),
]


def test_the_table_covers_exactly_the_documented_elements():
    assert set(ATOMIC_GROUND_TERMS) == {row[0] for row in NIST}


@pytest.mark.parametrize("element,mult,orbital_l,levels", NIST, ids=[r[0] for r in NIST])
def test_table_matches_nist(element, mult, orbital_l, levels):
    term = ATOMIC_GROUND_TERMS[element]
    assert term.multiplicity == mult
    assert term.l == orbital_l
    assert [j for j, _ in term.levels] == [j for j, _ in levels], "J order"
    for (_, got), (_, want) in zip(term.levels, levels, strict=True):
        assert got == pytest.approx(want, abs=1e-6)
    # The ground level is the lowest and sits at zero.
    assert term.levels[0][1] == 0.0
    # Every level of the term is present: sum of 2J+1 is (2S+1)(2L+1)
    # (H/N/P-like S terms and closed shells have their single level).
    if len(levels) == len(range(abs(2 * orbital_l - (mult - 1)), 2 * orbital_l + mult, 2)):
        assert sum(term.level_degeneracies) == term.total_degeneracy


@pytest.mark.parametrize(
    "element,expected",
    [("O", (5, 8, 9)), ("C", (1, 4, 9)), ("Cl", (4, 6)), ("B", (2, 6)), ("H", (2,))],
)
def test_cumulative_degeneracies_run_in_energy_order(element, expected):
    assert ATOMIC_GROUND_TERMS[element].cumulative_degeneracies == expected


def test_electronic_partition_function_and_free_energy_error_for_o_and_cl():
    """q_el(298.15 K): O 6.73, Cl 4.03. G(true) - G(spin-only) = -RT ln(q/g)."""
    o, cl = ATOMIC_GROUND_TERMS["O"], ATOMIC_GROUND_TERMS["Cl"]
    assert o.electronic_partition_function() == pytest.approx(6.73, abs=0.01)
    assert cl.electronic_partition_function() == pytest.approx(4.03, abs=0.01)
    assert o.spin_only_free_energy_error_kj_mol() == pytest.approx(-2.00, abs=0.01)
    assert cl.spin_only_free_energy_error_kj_mol() == pytest.approx(-1.74, abs=0.01)


@pytest.mark.parametrize(
    "element,kj",
    [("O", -0.93), ("C", -0.35), ("F", -1.61), ("S", -2.34), ("Cl", -3.52), ("Br", -14.7), ("I", -30.3)],
)
def test_spin_orbit_shift_matches_the_reference_values(element, kj):
    assert ATOMIC_GROUND_TERMS[element].spin_orbit_shift_kj_mol == pytest.approx(kj, abs=0.06)


@pytest.mark.parametrize(
    "text,expected",
    [
        ("3P", (3, 1, None)),
        ("3P2", (3, 1, 4)),
        ("3P_2", (3, 1, 4)),
        ("2P3/2", (2, 1, 3)),
        ("2P_{3/2}", (2, 1, 3)),
        ("^3P_2", (3, 1, 4)),
        ("^3P", (3, 1, None)),
        ("3Po", (3, 1, None)),
        ("2Po3/2", (2, 1, 3)),
        ("2P°3/2", (2, 1, 3)),
        ("4S°3/2", (4, 0, 3)),
        ("2P*", (2, 1, None)),
        ("²P₃⁄₂", (2, 1, 3)),  # noqa: RUF001
        (" 3 P ", (3, 1, None)),
    ],
)
def test_atomic_terms_parse(text, expected):
    got = parse_atomic_term(text)
    assert got is not None, text
    assert (got.multiplicity, got.l, got.two_j) == expected


@pytest.mark.parametrize("text", ["X2Pi", "3A1", "1A1", "2Π", "3Sigma-g", "A2Sigma+", "", None, "P"])
def test_molecular_or_malformed_terms_are_not_atomic(text):
    assert parse_atomic_term(text) is None


@pytest.mark.parametrize(
    "text,expected",
    [("3P", 3), ("^3P_2", 3), ("2P_{3/2}", 2), ("3Po", 3), ("1A1", 1), ("2Pi", 2), ("3Sigma-g", 3)],
)
def test_declared_multiplicity_is_read_from_a_leading_digit(text, expected):
    assert declared_term_multiplicity(text) == expected


@pytest.mark.parametrize("text", ["X2Pi", "A1", "B1u", "2Π", "", None])
def test_ambiguous_terms_state_no_multiplicity(text):
    assert declared_term_multiplicity(text) is None
