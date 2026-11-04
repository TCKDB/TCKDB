"""The display-only default table and its version reading, and the pin that tau's own reading did not move."""

from __future__ import annotations

import pytest
from tckdb_schemas.hessian_method_assumption import infer_hessian_method
from tckdb_schemas.program_default_settings import assumed_default_setting, display_major_version


@pytest.mark.parametrize(
    ("version", "expected"),
    [
        # Strings the stricter tau reading does not accept stay unknown to tau (50 cm-1), exactly as before.
        ("orca5", None),
        ("release-6", None),
        ("ORCA 5.0.4", None),
        ("ORCA_4.2.1", None),
        ("x86_64", None),
        ("ORCA 6.0.0", None),
        (None, None),
        # ...and the ones it does accept keep their rows.
        ("5.0.4", ("assumed_analytic_default", 30.0)),
        ("6.0.0", ("assumed_analytic_default", 30.0)),
        ("v6", ("assumed_analytic_default", 30.0)),
        ("4.2.1", ("assumed_finite_difference_gradient", 50.0)),
    ],
)
def test_tau_assumption_reads_versions_exactly_as_before(version, expected):
    result = infer_hessian_method("ORCA", "TPSS", version)
    assert (None if result is None else (result.basis.value, result.tau_cm1)) == expected


def test_a_version_naming_another_program_is_still_read_by_tau_as_before():
    result = infer_hessian_method("Gaussian", "TPSS", "ORCA 6.0.0")
    assert result is not None and result.tau_cm1 == 30.0  # unchanged; only the display reading is stricter


@pytest.mark.parametrize(
    ("software", "version", "expected"),
    [
        ("Gaussian", "Gaussian 16", 16),
        ("Gaussian", "G16 Rev C.01", 16),
        ("Gaussian", "16 C.02", 16),
        ("Gaussian", "09", 9),
        ("ORCA", "ORCA 6.0.0", 6),
        ("ORCA", "6.0.0", 6),
        ("ORCA", "v6.0.0", 6),
        ("ORCA", "x86_64", None),
        ("ORCA", "release-6", None),
        ("Gaussian", "ORCA 6.0.0", None),
        ("ORCA", "Gaussian 16", None),
        ("ORCA", "45", None),
        ("Molpro", "2024.1", None),
        (None, "6.0.0", None),
        ("ORCA", None, None),
    ],
)
def test_display_version_reading(software, version, expected):
    assert display_major_version(software, version) == expected


@pytest.mark.parametrize("method", ["B3LYP", "UB3LYP", "RI-B2PLYP", "ROB3LYP"])
def test_a_dft_method_gets_the_default_also_with_a_spin_or_ri_prefix(method):
    assert assumed_default_setting("ORCA", "6.0.0", "grid", method) == "defgrid2"


@pytest.mark.parametrize("method", ["HF", "UHF", "ROHF", "RI-MP2", "DLPNO-CCSD(T)", "APFD", None])
def test_other_methods_get_none(method):
    assert assumed_default_setting("ORCA", "6.0.0", "grid", method) is None


@pytest.mark.parametrize(
    ("software", "version", "method", "job", "expected"),
    [
        ("Gaussian", "09", "HF", "sp", "tight"),
        ("Gaussian", "09", "CCSD(T)", None, "tight"),
        ("Gaussian", "09", "B3LYP", "freq", "tight"),
        ("ORCA", "6.0.0", "MP2", "opt", "tight"),
        ("ORCA", "6.0.0", "DLPNO-CCSD(T)", "opt", "tight"),
        ("ORCA", "5.0.4", "B3LYP", "opt", "tight"),
        ("ORCA", "6.0.0", "HF", "sp", None),
        ("ORCA", "6.0.0", "HF", None, None),
        ("Gaussian", "16", "B3LYP", "opt", "tight"),
        ("Gaussian", "03", "HF", "sp", None),
        ("Gaussian", None, "HF", "sp", None),
        ("Gaussian", "09", None, "sp", None),
        ("Molpro", "2024.1", "HF", "sp", None),
        (None, "09", "HF", "sp", None),
    ],
)
def test_assumed_scf_convergence(software, version, method, job, expected):
    assert assumed_default_setting(software, version, "scf_convergence", method, job) == expected


def test_the_scf_default_is_not_gated_on_dft_but_the_grid_still_is():
    assert assumed_default_setting("Gaussian", "09", "scf_convergence", "HF", "sp") == "tight"
    assert assumed_default_setting("Gaussian", "09", "grid", "HF", "sp") is None
