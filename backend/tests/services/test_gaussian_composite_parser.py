"""The Gaussian composite summary-block parser (ADR 0021, P3b).

Every expected value below was read off the printed block of a real Gaussian log
under ``tests/fixtures/gaussian_composite`` (see the README there), to the digits
Gaussian prints. Nothing is computed from the log to make a test pass.

What would make each test vacuous, and what stops it
----------------------------------------------------
* A parser returning ``None`` for everything passes every "yields nothing" test,
  so each of those tests starts from a fixture the parser reads (the table below
  is asserted first) and removes exactly one thing.
* ``test_every_real_fixture_is_read`` asserts the exact values, so a parser that
  reads the wrong line cannot pass it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.services.gaussian_composite_parser import (
    UNREAD_COMPOSITE_METHOD_KEYS,
    implied_electronic_energy_hartree,
    parse_gaussian_composite_summary,
)

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "gaussian_composite"

_QB3_TERMS = ("E(SCF)", "DE(MP2)", "DE(CBS)", "DE(MP34)", "DE(CCSD)", "DE(Int)", "DE(Empirical)")
_CBS4M_TERMS = ("E(SCF)", "DE(MP2)", "DE(CBS)", "DE(MP34)", "DE(Int)", "DE(Empirical)")

# fixture -> (method key, E0, E(ZPE), the printed terms in print order)
_EXPECTED = {
    "cbs_qb3_ts_c2h5no2_g16.out": (
        "cbs-qb3",
        -283.819775,
        0.072623,
        (-282.684490, -1.027708, -0.099901, -0.030782, -0.034546, 0.032180, -0.047151),
    ),
    "cbs_qb3_ts_intra_h_migration_g09.out": (
        "cbs-qb3",
        -118.135290,
        0.083112,
        (-117.589191, -0.510069, -0.050397, -0.044213, -0.015260, 0.017923, -0.027196),
    ),
    "cbs_qb3_so2oo_g03.log": (
        "cbs-qb3",
        -698.186333,
        0.014845,
        (-696.895407, -1.143299, -0.117570, 0.002121, -0.032333, 0.035256, -0.049946),
    ),
    "rocbs_qb3_methanol_g16.out": (
        "rocbs-qb3",
        -115.539942,
        0.050598,
        (-115.086732, -0.421288, -0.043124, -0.022739, -0.007971, 0.013957, -0.022642),
    ),
    "cbs_4m_methanol_g16.out": (
        "cbs-4m",
        -115.563231,
        0.049949,
        (-115.082011, -0.351256, -0.096132, -0.018059, 0.024489, -0.090212),
    ),
    "g3_ethylene_g03.log": ("g3", -78.507415, 0.048905, ()),
}


def _text(name: str) -> str:
    return (FIXTURES / name).read_text()


_QB3 = "cbs_qb3_ts_c2h5no2_g16.out"
_QB3_E0_LINE = " CBS-QB3 (0 K)=            -283.819775 CBS-QB3 Energy=              -283.813741"


@pytest.mark.parametrize("name", sorted(_EXPECTED))
def test_every_real_fixture_is_read(name):
    method, e0, zpe, terms = _EXPECTED[name]
    summary = parse_gaussian_composite_summary(_text(name))
    assert summary is not None
    assert summary.method_key == method
    assert summary.e0_hartree == e0
    assert summary.recipe_zpe_hartree == zpe
    assert tuple(t.value_hartree for t in summary.terms) == terms


def test_the_cbs_term_labels_are_the_printed_ones_in_print_order():
    qb3 = parse_gaussian_composite_summary(_text(_QB3))
    assert tuple(t.label for t in qb3.terms) == _QB3_TERMS
    cbs4 = parse_gaussian_composite_summary(_text("cbs_4m_methanol_g16.out"))
    assert tuple(t.label for t in cbs4.terms) == _CBS4M_TERMS


def test_the_zpe_is_in_e0_and_not_in_the_terms():
    """The manual's definition: E0 = Eelec + ZPE, and the terms are the ZPE-free energy."""
    summary = parse_gaussian_composite_summary(_text(_QB3))
    term_sum = sum(t.value_hartree for t in summary.terms)
    # The terms alone land on E0 only after the ZPE is added...
    assert term_sum == pytest.approx(summary.e0_hartree - summary.recipe_zpe_hartree, abs=4e-6)
    assert abs(term_sum - summary.e0_hartree) == pytest.approx(summary.recipe_zpe_hartree, abs=4e-6)
    # ...so the electronic energy implied by the block is below E0 by exactly the ZPE.
    assert implied_electronic_energy_hartree(summary) == pytest.approx(-283.892398, abs=1e-9)
    assert implied_electronic_energy_hartree(summary) != summary.e0_hartree


@pytest.mark.parametrize("name", ["cbs_qb3_ts_c2h5no2_g16.out", "rocbs_qb3_methanol_g16.out"])
def test_the_printed_label_is_not_what_names_the_method(name):
    """CBS-QB3 and ROCBS-QB3 print the same ``CBS-QB3 (0 K)`` label; the route decides."""
    text = _text(name)
    assert "CBS-QB3 (0 K)" in text
    method = _EXPECTED[name][0]
    assert parse_gaussian_composite_summary(text).method_key == method


# ---------------------------------------------------------------------------
# Reject, don't guess
# ---------------------------------------------------------------------------


def test_a_log_without_a_summary_block_yields_nothing():
    text = _text(_QB3)
    cut = text.index("Complete Basis Set (CBS) Extrapolation:")
    assert parse_gaussian_composite_summary(text[:cut]) is None


def test_a_block_truncated_before_the_zero_k_line_yields_nothing():
    text = _text(_QB3)
    assert parse_gaussian_composite_summary(text) is not None
    truncated = text[: text.index(_QB3_E0_LINE)]
    assert parse_gaussian_composite_summary(truncated) is None


def test_a_block_missing_its_zpe_line_yields_nothing():
    text = _text(_QB3)
    zpe_line = next(line for line in text.splitlines() if line.lstrip().startswith("E(ZPE)=      "))
    assert parse_gaussian_composite_summary(text.replace(zpe_line + "\n", "")) is None


_PRINTED_QB3_LINES = {
    "DE(CCSD)": (
        " DE(CCSD)=                   -0.034546 DE(Int)=                        0.032180",
        " DE(Int)=                        0.032180",
    ),
    "DE(MP34)": (
        " DE(CBS)=                    -0.099901 DE(MP34)=                      -0.030782",
        " DE(CBS)=                    -0.099901",
    ),
    "DE(Empirical)": (" DE(Empirical)=              -0.047151\n", ""),
}


@pytest.mark.parametrize("removed", sorted(_PRINTED_QB3_LINES))
def test_a_cbs_block_missing_a_term_yields_nothing(removed):
    text = _text(_QB3)
    printed, without = _PRINTED_QB3_LINES[removed]
    assert printed in text, "the fixture no longer prints this line; update the test"
    assert parse_gaussian_composite_summary(text) is not None
    assert parse_gaussian_composite_summary(text.replace(printed, without, 1)) is None


def test_a_cbs_block_whose_terms_contradict_e0_yields_nothing():
    """A term that was mangled (a dropped digit) is caught by the sum, not read as-is."""
    text = _text(_QB3)
    assert parse_gaussian_composite_summary(text) is not None
    mangled = text.replace("DE(Empirical)=              -0.047151", "DE(Empirical)=              -0.047", 1)
    assert mangled != text
    assert parse_gaussian_composite_summary(mangled) is None


def test_a_cbs_block_with_a_stray_token_yields_nothing():
    text = _text(_QB3)
    garbled = text.replace("DE(Empirical)=", "DE(Empirical)= ??? ", 1)
    assert garbled != text
    assert parse_gaussian_composite_summary(garbled) is None


def test_two_blocks_that_disagree_yield_nothing():
    text = _text(_QB3)
    block_start = text.index("Temperature=               298.150000 Pressure=")
    block_end = text.index(" CBS-QB3 Enthalpy=", block_start)
    block = text[block_start:block_end]
    assert parse_gaussian_composite_summary(text + "\n " + block) is not None  # a repeat is fine
    other = block.replace("-283.819775", "-283.819000").replace("0.072623", "0.073400")
    assert parse_gaussian_composite_summary(text + "\n " + other + "\n") is None


def test_a_route_that_names_no_supported_method_yields_nothing():
    text = _text(_QB3)
    assert "cbs-qb3" in text
    assert parse_gaussian_composite_summary(text.replace("cbs-qb3", "b3lyp/6-31g", 1)) is None


def test_a_route_that_names_two_methods_yields_nothing():
    text = _text(_QB3)
    assert parse_gaussian_composite_summary(text.replace("cbs-qb3", "cbs-qb3 g4", 1)) is None


@pytest.mark.parametrize("key", sorted(UNREAD_COMPOSITE_METHOD_KEYS))
def test_methods_without_a_real_log_are_declined_not_guessed(key):
    """CBS-APNO, G3B3, G3MP2, W1*, ... print a block we have not seen; we read none."""
    text = _text(_QB3).replace("cbs-qb3", key, 1)
    assert parse_gaussian_composite_summary(text) is None


def test_the_label_must_agree_with_the_route():
    """A G3 route over a CBS-QB3 summary block is not a G3 result (G3 is a read method)."""
    text = _text(_QB3).replace("cbs-qb3", "g3", 1)
    assert parse_gaussian_composite_summary(text) is None


@pytest.mark.parametrize("text", [None, "", "not a log at all", "\n" * 50])
def test_non_logs_yield_nothing(text):
    assert parse_gaussian_composite_summary(text) is None


def test_a_plain_gaussian_log_yields_nothing():
    plain = (Path(__file__).resolve().parent.parent / "fixtures" / "gaussian" / "sp_ub3lyp_g16.log").read_text()
    assert parse_gaussian_composite_summary(plain) is None
