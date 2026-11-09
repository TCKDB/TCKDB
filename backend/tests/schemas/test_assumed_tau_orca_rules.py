"""ORCA assumed-Hessian rules (coupled cluster, F12, AUTOCI, open-shell MP2) and the refinement of a recorded gradient method."""

from __future__ import annotations

import pytest
from tckdb_schemas.coded_error import CodedValidationError
from tckdb_schemas.deposit_warning_messages import ASSUMPTION_RULES, render_message
from tckdb_schemas.enums import CoreTreatment, HessianMethod, RiApproximation, SpinTreatment
from tckdb_schemas.hessian_method_assumption import infer_hessian_method, resolve_tau_with_assumption
from tckdb_schemas.hessian_method_detection import check_recorded_method_agrees_with_deck
from tckdb_schemas.stationary_point import (
    W_HESSIAN_METHOD_REFINED,
    StationaryPointKind,
    TauBasis,
    evaluate_species_entry_frequency,
)

_ENERGY = (TauBasis.assumed_finite_difference_energy, 80.0)
_GRADIENT = (TauBasis.assumed_finite_difference_gradient, 50.0)


def _orca(method, version="6.1.0", **qualifiers):
    resolved = infer_hessian_method("ORCA", method, version, **qualifiers)
    return None if resolved is None else (resolved.basis, resolved.tau_cm1)


@pytest.mark.parametrize(
    "method",
    ["CCSD", "CCSD(T)", "DLPNO-CCSD", "DLPNO-CCSD(T)", "DLPNO-CCSD(T1)", "CCSD(T)-F12", "DLPNO-CCSD(T)-F12", "F12-CCSD(T)"],
)
@pytest.mark.parametrize("version", ["5.0.4", "6.1.0", None])
def test_orca_cc_dlpno_cc_and_f12_are_energies(method, version) -> None:
    assert _orca(method, version) == _ENERGY


@pytest.mark.parametrize("method", ["RI-MP2-F12", "F12-MP2", "F12-RI-MP2", "DLPNO-MP2-F12"])
def test_orca_f12_mp2_is_energies(method) -> None:
    assert _orca(method) == _ENERGY


_ALL_ELECTRON_NO_RI = {"core_treatment": CoreTreatment.all_electron, "ri_approximation": RiApproximation.none}


@pytest.mark.parametrize(
    "method", ["AUTOCI-CCSD", "AUTOCI-CCSD(T)", "autoci-ccsd", "AUTOCI-MP2", "AUTOCI-MP3", "AUTOCI-MP4"]
)
def test_autoci_cc_is_gradients_from_orca_6_without_frozen_core_or_ri(method) -> None:
    assert _orca(method, "6.0.1", **_ALL_ELECTRON_NO_RI) == _GRADIENT
    assert _orca(method, "6.1.0", core_treatment="all_electron", ri_approximation="none") == _GRADIENT


@pytest.mark.parametrize(
    ("version", "qualifiers"),
    [
        ("5.0.4", _ALL_ELECTRON_NO_RI),
        (None, _ALL_ELECTRON_NO_RI),
        ("6.1.0", {}),
        ("6.1.0", {"core_treatment": CoreTreatment.all_electron}),
        ("6.1.0", {"ri_approximation": RiApproximation.none}),
        ("6.1.0", {"core_treatment": CoreTreatment.frozen_core, "ri_approximation": RiApproximation.none}),
        ("6.1.0", {"core_treatment": CoreTreatment.all_electron, "ri_approximation": RiApproximation.rijcosx}),
    ],
)
def test_autoci_assumes_nothing_when_a_condition_is_unknown_or_unmet(version, qualifiers) -> None:
    assert all(_orca(m, version, **qualifiers) is None for m in ("AUTOCI-CCSD(T)", "AUTOCI-MP2", "AUTOCI-MP4"))


def test_other_autoci_methods_and_gaussian_assume_nothing() -> None:
    for other in ("AUTOCI-CCSDT", "AUTOCI-CEPA(0)", "AUTOCI-QCISD", "AUTOCI-CCSD[T]", "AUTOCI-MP5"):
        assert _orca(other, "6.1.0", **_ALL_ELECTRON_NO_RI) is None, other
    assert infer_hessian_method("Gaussian", "AUTOCI-CCSD", "16", **_ALL_ELECTRON_NO_RI) is None


@pytest.mark.parametrize(
    ("method", "qualifiers"),
    [
        ("DLPNO-MP2", {"spin_treatment": SpinTreatment.unrestricted}),
        ("DLPNO-MP2", {"spin_treatment": "restricted_open"}),
        ("UHF-DLPNO-MP2", {}),
        ("ROHF-DLPNO-MP2", {}),
        ("ROHF-MP2", {}),
        ("MP2", {"spin_treatment": SpinTreatment.restricted_open}),
        ("RI-MP2", {"spin_treatment": SpinTreatment.restricted_open}),
    ],
)
def test_orca_open_shell_dlpno_mp2_and_rohf_mp2_are_energies(method, qualifiers) -> None:
    assert _orca(method, **qualifiers) == _ENERGY


@pytest.mark.parametrize(
    ("method", "qualifiers"),
    [
        ("DLPNO-MP2", {}),
        ("DLPNO-MP2", {"spin_treatment": SpinTreatment.restricted}),
        ("DLPNO-MP2", {"spin_treatment": SpinTreatment.unknown}),
        ("MP2", {"spin_treatment": SpinTreatment.unrestricted}),
        ("RI-MP2", {}),
        ("UHF-MP2", {}),
        ("SCS-MP2", {}),
    ],
)
def test_orca_mp2_keeps_the_gradient_row_unless_open_shell_dlpno_or_rohf(method, qualifiers) -> None:
    assert _orca(method, **qualifiers) == _GRADIENT


def test_gaussian_rohf_mp2_assumes_nothing() -> None:
    assert infer_hessian_method("Gaussian", "ROHF-MP2", "16") is None


def test_the_new_rules_are_in_the_closed_set_and_render() -> None:
    for rule in ("orca_no_analytic_gradient", "orca_open_shell_no_gradient", "orca_autoci_gradient_from_6"):
        assert rule in ASSUMPTION_RULES
    for resolution, needle in (
        (infer_hessian_method("ORCA", "CCSD(T)", "6.1.0"), "no analytic gradient"),
        (infer_hessian_method("ORCA", "DLPNO-MP2", "6.1.0", spin_treatment="unrestricted"), "open-shell DLPNO-MP2"),
        (infer_hessian_method("ORCA", "AUTOCI-CCSD", "6.1.0", **_ALL_ELECTRON_NO_RI), "AUTOCI"),
    ):
        text = render_message(
            "transition_state_imaginary_frequency_too_small",
            {
                "tau_cm1": resolution.tau_cm1,
                "tau_basis": resolution.basis.value,
                "tau_assumption_rule": resolution.assumption_rule,
                "tau_software": resolution.assumed_software,
                "tau_method_family": resolution.assumed_family,
                "reaction_coordinate_cm1": 20.0,
                "ts_minimum_cm1": 50.0,
            },
        )
        assert needle in text, text


# ---------------------------------------------------------------------------
# Refinement
# ---------------------------------------------------------------------------

_REC_GRAD = [("freq.hessian_method", "finite_difference_gradient")]
_STATED_ENERGY = HessianMethod.finite_difference_energy


@pytest.mark.parametrize("stated_in", ["deck", "echo"])
@pytest.mark.parametrize("method", ["CCSD(T)", "DLPNO-CCSD(T)", "RI-MP2-F12"])
def test_recorded_gradient_plus_numfreq_on_a_method_without_a_gradient_is_refined(stated_in, method) -> None:
    stated = {"deck_method": _STATED_ENERGY} if stated_in == "deck" else {"echo_method": _STATED_ENERGY}
    resolved = resolve_tau_with_assumption(_REC_GRAD, "ORCA", method, "6.1.0", **stated)
    assert (resolved.basis, resolved.tau_cm1) == (TauBasis.finite_difference_energy, 80.0)
    assert resolved.refined_from == "finite_difference_gradient"


def test_open_shell_dlpno_mp2_is_refined_by_its_stated_spin() -> None:
    kwargs = {"deck_method": _STATED_ENERGY}
    open_shell = resolve_tau_with_assumption(_REC_GRAD, "ORCA", "DLPNO-MP2", "6.1.0", spin_treatment="unrestricted", **kwargs)
    assert open_shell.refined_from == "finite_difference_gradient"
    closed = resolve_tau_with_assumption(_REC_GRAD, "ORCA", "DLPNO-MP2", "6.1.0", **kwargs)
    assert closed.refined_from is None


def test_no_refinement_without_a_recorded_gradient_or_for_a_method_with_a_gradient() -> None:
    kwargs = {"deck_method": _STATED_ENERGY}
    assert resolve_tau_with_assumption([], "ORCA", "CCSD(T)", "6.1.0", **kwargs).refined_from is None
    assert resolve_tau_with_assumption(_REC_GRAD, "ORCA", "B3LYP", "6.1.0", **kwargs).refined_from is None
    assert resolve_tau_with_assumption(_REC_GRAD, "Gaussian", "CCSD(T)", None, **kwargs).refined_from is None
    reported = resolve_tau_with_assumption(
        _REC_GRAD, "ORCA", "CCSD(T)", "6.1.0", reported_method=HessianMethod.finite_difference_gradient, **kwargs
    )
    assert reported.refined_from is None and reported.basis is TauBasis.finite_difference_gradient


def test_the_deck_check_accepts_the_refinement_and_refuses_real_contradictions() -> None:
    check_recorded_method_agrees_with_deck(_REC_GRAD, _STATED_ENERGY, software="ORCA", lot_method="CCSD(T)")
    check_recorded_method_agrees_with_deck(
        _REC_GRAD, _STATED_ENERGY, software="ORCA", lot_method="DLPNO-MP2", lacks_gradient=True
    )
    for params, software, lot in (
        ([("freq.hessian_method", "analytic")], "ORCA", "CCSD(T)"),
        (_REC_GRAD, "ORCA", "B3LYP"),
        (_REC_GRAD, "Gaussian", "CCSD(T)"),
        (_REC_GRAD, None, None),
    ):
        with pytest.raises(CodedValidationError):
            check_recorded_method_agrees_with_deck(params, _STATED_ENERGY, software=software, lot_method=lot)


def test_the_refinement_warning_is_rendered_from_its_code_and_values() -> None:
    tau = resolve_tau_with_assumption(_REC_GRAD, "ORCA", "CCSD(T)", "6.1.0", deck_method=_STATED_ENERGY)
    findings = evaluate_species_entry_frequency(StationaryPointKind.minimum, 0, location="calc", tau=tau)
    (finding,) = [f for f in findings if f.code == W_HESSIAN_METHOD_REFINED]
    assert finding.context == {
        "recorded": "finite_difference_gradient",
        "stored_method": "finite_difference_energy",
        "stored_basis": "finite_difference_energy",
        "refined_because": "no_analytic_gradient",
    }
    text = render_message(finding.code, finding.context)
    assert "finite_difference_gradient" in text and "finite_difference_energy" in text and "numerical" in text
