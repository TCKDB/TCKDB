"""One owner for tau, applied by every validation adapter (owner decision 2026-10-31).

Resolution order: a recorded ``freq.hessian_method`` -> the assumed default of
the payload's own software and level-of-theory method -> ``protocol_not_recorded``.
"""

from __future__ import annotations

import pytest
from tckdb_schemas.fragments.calculation import CalculationWithResultsPayload
from tckdb_schemas.hessian_method_assumption import (
    infer_hessian_method,
    resolve_tau_with_assumption,
)
from tckdb_schemas.shared.calculation_in import CalculationIn, tau_resolution_of
from tckdb_schemas.stationary_point import (
    W_TS_EXTRA_IMAGINARY_MODE_ABOVE_TAU,
    W_TS_EXTRA_IMAGINARY_MODES_BELOW_TAU,
    TauBasis,
)

_MODES = [
    {"mode_index": 1, "frequency_cm1": -1300.0, "is_imaginary": True},
    {
        "mode_index": 2,
        "frequency_cm1": -40.0,
        "is_imaginary": True,
        "imaginary_disposition": "torsion",
    },
    {"mode_index": 3, "frequency_cm1": 600.0, "is_imaginary": False},
]
_FD = [
    {
        "raw_key": "freq",
        "raw_value": "numer",
        "canonical_key": "freq.hessian_method",
        "canonical_value": "finite_difference_gradient",
    }
]


def _payload(software: str, method: str, parameters=None) -> CalculationWithResultsPayload:
    return CalculationWithResultsPayload(
        type="freq",
        software_release={"name": software, "version": "16"},
        level_of_theory={"method": method, "basis": "6-31G(d)"},
        freq_result={
            "n_imag": 2,
            "imag_freq_cm1": -1300.0,
            "reaction_coordinate_mode_index": 1,
            "modes": _MODES,
        },
        parameters=parameters,
    )


def _bundle(software: str, method: str, parameters=None) -> CalculationIn:
    return CalculationIn(
        key="f",
        type="freq",
        geometry_key="g",
        software_release={"name": software, "version": "16"},
        level_of_theory={"method": method, "basis": "6-31G(d)"},
        freq_n_imag=2,
        freq_imag_freq_cm1=-1300.0,
        freq_frequencies_cm1=[-1300.0, -40.0, 600.0],
        freq_reaction_coordinate_mode_index=1,
        freq_imaginary_dispositions={2: "torsion"},
        parameters=parameters,
    )


@pytest.mark.parametrize(
    ("software", "method", "basis", "tau"),
    [
        ("Gaussian", "B3LYP", TauBasis.assumed_analytic_default, 30.0),
        ("gaussian", "wB97XD", TauBasis.assumed_analytic_default, 30.0),
        ("ORCA", "B3LYP", TauBasis.assumed_analytic_default, 30.0),
        ("Gaussian", "CCSD", TauBasis.assumed_finite_difference_gradient, 50.0),
        ("Gaussian", "CCSD(T)", TauBasis.assumed_finite_difference_energy, 80.0),
        ("Molpro", "B3LYP", TauBasis.protocol_not_recorded, 50.0),
        ("Gaussian", "some-unlisted-method", TauBasis.protocol_not_recorded, 50.0),
    ],
)
def test_every_adapter_resolves_the_same_tau(software, method, basis, tau) -> None:
    payload = _payload(software, method).tau_resolution()
    bundle = tau_resolution_of(_bundle(software, method))
    for resolved in (payload, bundle):
        assert resolved.basis is basis
        assert resolved.tau_cm1 == tau


def test_a_recorded_method_beats_the_assumption() -> None:
    resolved = _payload("Gaussian", "B3LYP", _FD).tau_resolution()
    assert resolved.basis is TauBasis.finite_difference_gradient
    assert resolved.tau_cm1 == 50.0


def test_the_flag_follows_the_resolved_tau() -> None:
    """-40 cm-1 extra mode: above the assumed 30, below the conservative 50."""
    gaussian = _payload("Gaussian", "B3LYP").transition_state_frequency_findings(
        location="calc"
    )
    assert {f.code for f in gaussian} == {W_TS_EXTRA_IMAGINARY_MODE_ABOVE_TAU}
    assert all(f.tau.basis is TauBasis.assumed_analytic_default for f in gaussian)

    molpro = _payload("Molpro", "B3LYP").transition_state_frequency_findings(location="calc")
    assert {f.code for f in molpro} == {W_TS_EXTRA_IMAGINARY_MODES_BELOW_TAU}


def test_no_software_is_never_guessed() -> None:
    assert resolve_tau_with_assumption([], None, "B3LYP").basis is TauBasis.protocol_not_recorded
    assert resolve_tau_with_assumption([], "Gaussian", None).basis is TauBasis.protocol_not_recorded


def test_the_reason_never_echoes_depositor_text() -> None:
    """The reason is TCKDB prose: the table's program and the method family,
    not the depositor's raw strings."""
    resolved = infer_hessian_method("gaussian", "  WB97XD  ")
    assert resolved is not None
    assert "wb97xd" not in resolved.reason.lower()
    assert "Gaussian" in resolved.reason
    assert "dft method family" in resolved.reason
