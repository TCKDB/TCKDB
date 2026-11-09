"""Tests for the ADR 0012 2026-09-04 amendment's assumption table.

:mod:`app.services.hessian_method_inference` is a pure, table-driven
function: given a software name and a level-of-theory method, it either
returns an assumed :class:`~tckdb_schemas.stationary_point.TauResolution`
or ``None``. These tests exercise the method-family recogniser directly
(the list the task named: b3lyp, wb97xd, m062x, pbe0, cam-b3lyp, hf, mp2,
ccsd(t), dlpno-ccsd(t), and an unknown token) and the full
``infer_hessian_method`` dispatch for Gaussian, ORCA, and a
not-taught software (Molpro), which must stay conservative.
"""

from __future__ import annotations

import pytest
from tckdb_schemas.stationary_point import (
    TAU_ANALYTIC_DEFAULT_CM1,
    TAU_FINITE_DIFFERENCE_ENERGY_CM1,
    TAU_FINITE_DIFFERENCE_GRADIENT_CM1,
    TauBasis,
)

from app.services.hessian_method_inference import (
    MethodFamily,
    classify_method_family,
    infer_hessian_method,
    is_dft_functional,
)

# ---------------------------------------------------------------------------
# Method-family recognition -- the exact list named in the task brief.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "method, expected_family",
    [
        ("b3lyp", MethodFamily.DFT),
        ("wb97xd", MethodFamily.DFT),
        ("wb97x-d", MethodFamily.DFT),
        ("m062x", MethodFamily.DFT),
        ("m06-2x", MethodFamily.DFT),
        ("pbe0", MethodFamily.DFT),
        ("cam-b3lyp", MethodFamily.DFT),
        ("hf", MethodFamily.HF),
        ("mp2", MethodFamily.MP2),
        ("ccsd(t)", MethodFamily.CCSD_T),
        ("dlpno-ccsd(t)", MethodFamily.CCSD_T),
        ("some-unheard-of-method-42", None),
    ],
)
def test_classify_method_family(method, expected_family):
    assert classify_method_family(method) == expected_family


@pytest.mark.parametrize(
    "method, expected",
    [
        ("b3lyp", True),
        ("B3LYP", True),
        ("wb97xd", True),
        ("wb97x-d", True),
        ("m062x", True),
        ("pbe0", True),
        ("cam-b3lyp", True),
        ("hf", False),
        ("mp2", False),
        ("ccsd(t)", False),
        (None, False),
        ("", False),
    ],
)
def test_is_dft_functional(method, expected):
    assert is_dft_functional(method) is expected


def test_ccsd_t_is_not_shadowed_by_ccsd():
    """The longer, more specific prefix must win over the shorter one."""
    assert classify_method_family("ccsd") == MethodFamily.CCSD
    assert classify_method_family("ccsd(t)") == MethodFamily.CCSD_T
    assert classify_method_family("qcisd") == MethodFamily.QCISD
    assert classify_method_family("qcisd(t)") == MethodFamily.QCISD_T


# ---------------------------------------------------------------------------
# infer_hessian_method -- software dispatch
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "method, expected_basis, expected_tau",
    [
        ("hf", TauBasis.assumed_analytic_default, TAU_ANALYTIC_DEFAULT_CM1),
        ("b3lyp", TauBasis.assumed_analytic_default, TAU_ANALYTIC_DEFAULT_CM1),
        ("wb97xd", TauBasis.assumed_analytic_default, TAU_ANALYTIC_DEFAULT_CM1),
        ("mp2", TauBasis.assumed_analytic_default, TAU_ANALYTIC_DEFAULT_CM1),
        ("cis", TauBasis.assumed_analytic_default, TAU_ANALYTIC_DEFAULT_CM1),
        ("casscf", TauBasis.assumed_analytic_default, TAU_ANALYTIC_DEFAULT_CM1),
        (
            "mp3",
            TauBasis.assumed_finite_difference_gradient,
            TAU_FINITE_DIFFERENCE_GRADIENT_CM1,
        ),
        (
            "mp4(sdq)",
            TauBasis.assumed_finite_difference_gradient,
            TAU_FINITE_DIFFERENCE_GRADIENT_CM1,
        ),
        (
            "qcisd",
            TauBasis.assumed_finite_difference_gradient,
            TAU_FINITE_DIFFERENCE_GRADIENT_CM1,
        ),
        (
            "ccsd",
            TauBasis.assumed_finite_difference_gradient,
            TAU_FINITE_DIFFERENCE_GRADIENT_CM1,
        ),
        (
            "ccsd(t)",
            TauBasis.assumed_finite_difference_energy,
            TAU_FINITE_DIFFERENCE_ENERGY_CM1,
        ),
        (
            "qcisd(t)",
            TauBasis.assumed_finite_difference_energy,
            TAU_FINITE_DIFFERENCE_ENERGY_CM1,
        ),
    ],
)
def test_gaussian_table(method, expected_basis, expected_tau):
    resolution = infer_hessian_method("Gaussian", method)
    assert resolution is not None
    assert resolution.basis is expected_basis
    assert resolution.tau_cm1 == expected_tau
    # The reason must name what happened without reading as a recorded
    # statement -- "assumption" is the load-bearing word.
    assert "assumption" in resolution.reason
    assert "not recorded" in resolution.reason


def test_gaussian_matching_is_case_and_alias_insensitive():
    assert infer_hessian_method("gaussian", "b3lyp") is not None
    assert infer_hessian_method("GAUSSIAN", "B3LYP") is not None


@pytest.mark.parametrize(
    "method, expected_basis, expected_tau",
    [
        ("hf", TauBasis.assumed_analytic_default, TAU_ANALYTIC_DEFAULT_CM1),
        ("pbe0", TauBasis.assumed_analytic_default, TAU_ANALYTIC_DEFAULT_CM1),
        (
            "mp2",
            TauBasis.assumed_finite_difference_gradient,
            TAU_FINITE_DIFFERENCE_GRADIENT_CM1,
        ),
        (
            "ri-mp2",
            TauBasis.assumed_finite_difference_gradient,
            TAU_FINITE_DIFFERENCE_GRADIENT_CM1,
        ),
        (
            "ccsd(t)",
            TauBasis.assumed_finite_difference_gradient,
            TAU_FINITE_DIFFERENCE_GRADIENT_CM1,
        ),
        (
            "dlpno-ccsd(t)",
            TauBasis.assumed_finite_difference_gradient,
            TAU_FINITE_DIFFERENCE_GRADIENT_CM1,
        ),
    ],
)
def test_orca_table(method, expected_basis, expected_tau):
    resolution = infer_hessian_method("ORCA", method)
    assert resolution is not None
    assert resolution.basis is expected_basis
    assert resolution.tau_cm1 == expected_tau


# ---------------------------------------------------------------------------
# Staying conservative -- the software/method pairs the table refuses
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "software, method",
    [
        ("Molpro", "b3lyp"),
        ("Molpro", None),
        ("Arkane", "hf"),
        ("ARC", "b3lyp"),
        (None, "b3lyp"),
        ("", "b3lyp"),
        ("Gaussian", "some-unheard-of-method-42"),
        ("Gaussian", None),
        ("Gaussian", ""),
        ("ORCA", "casscf"),  # not in ORCA's documented table
        ("Molpro", "ccsd(t)"),
    ],
)
def test_not_taught_pairs_stay_none(software, method):
    """Unrecognised software or method must never produce a guess.

    This is the module's whole safety property: absent a documented
    default, the caller keeps ``protocol_not_recorded`` rather than this
    module inventing one.
    """
    assert infer_hessian_method(software, method) is None


def test_a_recognised_method_on_unrecognised_software_stays_none():
    """A method this module knows on a software it does not is still None.

    Guards against a dispatch bug where the family lookup runs before the
    software gate and accidentally answers for the wrong program.
    """
    assert infer_hessian_method("Molpro", "b3lyp") is None
    assert infer_hessian_method("Molpro", "hf") is None
