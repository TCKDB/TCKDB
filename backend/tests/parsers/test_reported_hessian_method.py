"""The Hessian method an ORCA output log reports, read from real logs.

Each pattern is confirmed on a log; the excerpts for ORCA 4.1.2 and 5.0.4 are verbatim lines from the logs they name
(copied here, not referenced), and ORCA 6.1.0 is read from the fixtures in the repository. Every "None" case has a twin
that reports, so a reader that always answers ``None`` cannot pass.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from tckdb_schemas.enums import HessianMethod
from tckdb_schemas.hessian_method_reported import orca_reported_hessian, reported_hessian_method

from app.services.reported_settings_parser import parse_orca_reported

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
ORCA = FIXTURES / "orca"

_VIB = "-----------------------\nVIBRATIONAL FREQUENCIES\n-----------------------\n\n   0:         0.00 cm**-1\n"

# ORCA 4.1.2 and 5.0.4 (ARC's freq test logs orca_example_freq.log and orca_neg_freq_ts.out, B3LYP and wB97X-D3 Freq jobs).
_ANALYTIC_ORCA_4_5 = (
    "-------------------------------------------------------------------------------\n"
    "                               ORCA SCF HESSIAN\n"
    "-------------------------------------------------------------------------------\n\n"
    "                   --------- SHARK INITIALIZATION DONE ---------\n\n"
    "Hessian of the Kohn-Sham DFT energy:\nKohn-Sham wavefunction type                      ... UKS\n"
    "Maximum memory used throughout the entire SCFHESS-calculation: 171.4 MB\n\n"
)

# ORCA 6.1.0 numerical block (ts_opt_orca.out: OptTS NumFreq).
_NUMERICAL_ORCA_6 = (
    "----------------------------------------------------------------------------\n"
    "                           ORCA NUMERICAL FREQUENCIES\n"
    "                              (32-process run)\n"
    "----------------------------------------------------------------------------\n\n"
    "Number of atoms                ... 18\nCentral differences            ... NOT used\n\n"
    "\t<< Calculating gradient on displaced geometry  10 (of  52) >>\n"
)

# ORCA 5.0.4 numerical block with a numerical gradient (rotor_scans/orca/cc.txt: uHF CCSD(T) NumGrad): it says
# "Calculating on displaced geometry", which does not say what was differenced.
_NUMERICAL_ORCA_5_NUMGRAD = (
    "----------------------------------------------------------------------------\n"
    "                           ORCA NUMERICAL FREQUENCIES\n"
    "                              (30-process run)\n"
    "----------------------------------------------------------------------------\n\n"
    "Number of displacements        ... 12\n\n"
    "\t<< Calculating on displaced geometry   8 (of  18) >>\n"
)


def _read(name: str) -> str:
    return (ORCA / name).read_text(errors="replace")


# ---------------------------------------------------------------------------
# each version
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", ["opt_orca.out", "opt_forward_orca.out", "neb_ts_orca.out", "opt_constrained_orca.out"])
def test_orca_6_1_0_scf_hessian_is_reported_analytic(name) -> None:
    found = orca_reported_hessian(_read(name))
    assert found is not None
    assert (found.method, found.header) == (HessianMethod.analytic, "SCF HESSIAN")


def test_orca_6_1_0_numerical_frequencies_with_displaced_gradients_is_reported_finite_difference_of_gradients() -> None:
    # OptTS NumFreq prints five numerical listings (one per optimiser step); all agree.
    found = orca_reported_hessian(_read("ts_opt_orca.out"))
    assert found is not None
    assert (found.method, found.header) == (HessianMethod.finite_difference_gradient, "ORCA NUMERICAL FREQUENCIES")


def test_orca_4_1_2_and_5_0_4_orca_scf_hessian_is_reported_analytic() -> None:
    found = orca_reported_hessian(_ANALYTIC_ORCA_4_5 + _VIB)
    assert found is not None
    assert (found.method, found.header) == (HessianMethod.analytic, "ORCA SCF HESSIAN")


def test_orca_5_0_4_numerical_frequencies_on_a_numerical_gradient_do_not_say_what_was_differenced() -> None:
    assert orca_reported_hessian(_NUMERICAL_ORCA_5_NUMGRAD + _VIB) is None
    # The same block with the gradient line ORCA 6.1.0 prints reports.
    found = orca_reported_hessian(_NUMERICAL_ORCA_6 + _VIB)
    assert found is not None and found.method is HessianMethod.finite_difference_gradient


# ---------------------------------------------------------------------------
# what is not a report
# ---------------------------------------------------------------------------


def test_a_numerical_hessian_that_fed_no_frequency_listing_is_not_reported() -> None:
    # irc_orca.out computes a numerical Hessian as the IRC's initial displacement and prints no listing.
    text = _read("irc_orca.out")
    assert "ORCA NUMERICAL FREQUENCIES" in text and "VIBRATIONAL FREQUENCIES" not in text
    assert orca_reported_hessian(text) is None


def test_logs_with_no_frequency_job_report_nothing() -> None:
    for name in ("scan_bond_orca.out", "sp_dlpno_ccsdt_orca.out", "neb_ci_orca.out"):
        assert orca_reported_hessian(_read(name)) is None, name


def test_an_optimiser_step_between_the_hessian_and_the_listing_breaks_the_binding() -> None:
    assert orca_reported_hessian(_ANALYTIC_ORCA_4_5 + _VIB) is not None
    broken = _ANALYTIC_ORCA_4_5 + "                         ORCA GEOMETRY RELAXATION STEP\n" + _VIB
    assert orca_reported_hessian(broken) is None


def test_listings_that_disagree_report_nothing_and_each_listing_needs_its_own_header() -> None:
    agree = orca_reported_hessian(_ANALYTIC_ORCA_4_5 + _VIB + _ANALYTIC_ORCA_4_5 + _VIB)
    assert agree is not None and agree.method is HessianMethod.analytic
    mixed = _ANALYTIC_ORCA_4_5 + _VIB + _NUMERICAL_ORCA_6 + _VIB
    assert orca_reported_hessian(mixed) is None
    # A second listing that follows no Hessian header of its own is not taken from the first.
    assert orca_reported_hessian(_ANALYTIC_ORCA_4_5 + _VIB + _VIB) is None


def test_the_last_hessian_before_the_listing_is_the_one_that_fed_it() -> None:
    # An optimiser's analytic Hessian (no listing), then the frequency job's numerical one.
    found = orca_reported_hessian(_ANALYTIC_ORCA_4_5 + "some optimiser output\n" + _NUMERICAL_ORCA_6 + _VIB)
    assert found is not None and found.method is HessianMethod.finite_difference_gradient


def test_a_file_with_several_jobs_reports_nothing() -> None:
    multi = "|  1> ! B3LYP Freq\n|  2> $new_job\n" + _ANALYTIC_ORCA_4_5 + _VIB
    assert orca_reported_hessian(multi) is None
    assert orca_reported_hessian(_ANALYTIC_ORCA_4_5 + _VIB) is not None


def test_several_banners_must_each_report_the_same_method() -> None:
    banner = "\n                                 *****************\n                                 * O   R   C   A *\n"
    one = banner + _ANALYTIC_ORCA_4_5 + _VIB
    assert orca_reported_hessian(one + one) is not None
    assert orca_reported_hessian(one + banner + _NUMERICAL_ORCA_6 + _VIB) is None
    assert orca_reported_hessian(one + banner + "nothing to see\n") is None


def test_gaussian_reports_nothing() -> None:
    # No Gaussian log in the measured set prints a Hessian-method statement that holds across jobs.
    text = (FIXTURES / "gaussian" / "freq_g09.log").read_text(errors="replace")
    assert reported_hessian_method("gaussian", text) is None
    assert reported_hessian_method("orca", _ANALYTIC_ORCA_4_5 + _VIB) is HessianMethod.analytic
    assert reported_hessian_method(None, _ANALYTIC_ORCA_4_5 + _VIB) is None


# ---------------------------------------------------------------------------
# the stored row
# ---------------------------------------------------------------------------


def test_the_reported_row_carries_the_header_and_the_canonical_method() -> None:
    [row] = [r for r in parse_orca_reported(_read("ts_opt_orca.out")) if r["canonical_key"] == "freq.hessian_method"]
    assert row["statement_kind"] == "reported"
    assert (row["raw_key"], row["raw_value"]) == ("hessian_method", "ORCA NUMERICAL FREQUENCIES")
    assert (row["canonical_value"], row["section"], row["value_type"]) == ("finite_difference_gradient", "freq", "enum")
    assert not [r for r in parse_orca_reported(_read("scan_bond_orca.out")) if r["canonical_key"] == "freq.hessian_method"]
