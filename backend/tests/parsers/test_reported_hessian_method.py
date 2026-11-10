"""The Hessian method an ORCA output log reports, read from real logs.

Each pattern is confirmed on a log. The excerpts for ORCA 4.1.2 and 5.0.4 are spliced from lines of the logs of the
jobs named beside them (copied here, not referenced), not contiguous text; ORCA 6.1.0 is read from the fixtures in the
repository. Every "None" case has a twin
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

# Lines from an ORCA 4.1.2 `! RKS B3LYP/G SV(P) TightSCF AnFreq` job and an ORCA 5.0.4 `!uKS wb97x-d3 def2-tzvp Freq` job.
_ANALYTIC_ORCA_4_5 = (
    "-------------------------------------------------------------------------------\n"
    "                               ORCA SCF HESSIAN\n"
    "-------------------------------------------------------------------------------\n\n"
    "                   --------- SHARK INITIALIZATION DONE ---------\n\n"
    "Hessian of the Kohn-Sham DFT energy:\nKohn-Sham wavefunction type                      ... UKS\n"
    "Maximum memory used throughout the entire SCFHESS-calculation: 171.4 MB\n\n"
)

# Lines from the ORCA 6.1.0 numerical block of an `OptTS NumFreq` job (ts_opt_orca.out).
_NUMERICAL_ORCA_6 = (
    "----------------------------------------------------------------------------\n"
    "                           ORCA NUMERICAL FREQUENCIES\n"
    "                              (32-process run)\n"
    "----------------------------------------------------------------------------\n\n"
    "Number of atoms                ... 18\nCentral differences            ... NOT used\n\n"
    "\t<< Calculating gradient on displaced geometry  10 (of  52) >>\n"
)

# Lines from an ORCA 5.0.4 `!uHF ccsd(t) numgrad` optimiser scan: its numerical Hessian is an optimiser Hessian inside
# the scan, not a NumFreq job. It says "Calculating on displaced geometry", which does not say what was differenced.
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


# ---------------------------------------------------------------------------
# the final frequency job, and numerical gradients
# ---------------------------------------------------------------------------


def test_a_log_cut_off_during_an_optimisation_reports_nothing_and_its_full_twin_reports() -> None:
    full = _read("ts_opt_orca.out")
    cut = "\n".join(full.split("\n")[:7700])
    # ORCA prints a VIBRATIONAL FREQUENCIES listing after every optimiser Hessian of this OptTS NumFreq job; the cut
    # log ends after the converged optimisation, before the final frequency job.
    assert cut.count("VIBRATIONAL FREQUENCIES") == 4 and "THE OPTIMIZATION HAS CONVERGED" in cut
    assert orca_reported_hessian(cut) is None
    twin = orca_reported_hessian(full)
    assert twin is not None and twin.method is HessianMethod.finite_difference_gradient


def test_a_listing_followed_by_an_optimiser_step_is_not_the_final_job_but_a_closing_listing_is() -> None:
    step = "                         ORCA GEOMETRY RELAXATION STEP\n"
    assert orca_reported_hessian(_ANALYTIC_ORCA_4_5 + _VIB + step) is None
    assert orca_reported_hessian(_ANALYTIC_ORCA_4_5 + _VIB + step + _ANALYTIC_ORCA_4_5 + _VIB) is not None


def test_a_numerical_hessian_on_a_numerical_gradient_is_not_reported() -> None:
    block = _NUMERICAL_ORCA_6 + _VIB
    assert orca_reported_hessian(block) is not None
    echoed = "INPUT FILE\n|  1> ! B3LYP def2-SVP NumFreq NumGrad\n****END OF INPUT****\n" + block
    assert orca_reported_hessian(echoed) is None
    header = block + "                           ORCA NUMERICAL GRADIENT\n"
    assert orca_reported_hessian(header) is None
    # An analytic Hessian is not affected by a numerical gradient elsewhere in the job.
    prefix = "INPUT FILE\n|  1> ! B3LYP def2-SVP AnFreq NumGrad\n****END OF INPUT****\n"
    assert orca_reported_hessian(prefix + _ANALYTIC_ORCA_4_5 + _VIB) is not None
