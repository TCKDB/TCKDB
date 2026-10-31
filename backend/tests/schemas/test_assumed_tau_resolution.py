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


def _species_bundle_calc(software: str, method: str, parameters=None):
    from tckdb_schemas.workflows.computed_species_upload import CalculationInBundle

    return CalculationInBundle(
        key="f",
        type="freq",
        software_release={"name": software, "version": "16"},
        level_of_theory={"method": method, "basis": "6-31G(d)"},
        freq_result={"n_imag": 1, "imag_freq_cm1": -40.0},
        parameters=parameters,
    )


@pytest.mark.parametrize(
    ("software", "method", "codes"),
    [
        ("Gaussian", "B3LYP", {"n_imag_contradicts_minimum"}),
        ("Gaussian", "CCSD(T)", {"minimum_imaginary_modes_below_tau"}),
        ("Molpro", "B3LYP", {"minimum_imaginary_modes_below_tau"}),
    ],
)
def test_the_computed_species_adapter_resolves_the_same_tau(software, method, codes) -> None:
    from tckdb_schemas.stationary_point import StationaryPointKind

    findings = _species_bundle_calc(software, method).species_entry_frequency_findings(
        StationaryPointKind.minimum, location="calc"
    )
    assert {f.code for f in findings} == codes


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
    assert "DFT calculations" in resolved.reason


@pytest.mark.parametrize(
    ("method", "basis"),
    [
        ("CIS", TauBasis.assumed_analytic_default),
        ("UCIS", TauBasis.assumed_analytic_default),
        ("CISD", TauBasis.assumed_finite_difference_gradient),
        ("CID", TauBasis.assumed_finite_difference_gradient),
        ("CIS(D)", TauBasis.protocol_not_recorded),
    ],
)
def test_cis_is_matched_exactly_and_cisd_is_numerical(method, basis) -> None:
    assert resolve_tau_with_assumption([], "Gaussian", method).basis is basis


@pytest.mark.parametrize("value", ["numerical", "numer", "Freq=Numer"])
def test_an_unrecognised_recorded_method_is_never_replaced_by_an_assumption(value) -> None:
    resolved = resolve_tau_with_assumption(
        [("freq.hessian_method", value)], "Gaussian", "B3LYP"
    )
    assert resolved.basis is TauBasis.protocol_not_recorded
    assert resolved.tau_cm1 == 50.0
    # An absent or blank value is not a statement.
    blank = resolve_tau_with_assumption([("freq.hessian_method", " ")], "Gaussian", "B3LYP")
    assert blank.basis is TauBasis.assumed_analytic_default


def test_the_reason_uses_human_family_labels() -> None:
    assert "DFT calculations" in infer_hessian_method("Gaussian", "B3LYP").reason
    assert "CCSD(T) calculations" in infer_hessian_method("Gaussian", "CCSD(T)").reason
    assert "dlpno_other" not in infer_hessian_method("ORCA", "DLPNO-MP2").reason
    assert "method family" not in infer_hessian_method("ORCA", "DLPNO-MP2").reason


# ---------------------------------------------------------------------------
# ORCA release gate (owner decision 2026-10-31)
# Sources: see the table note in ``hessian_method_assumption.py``.
# ---------------------------------------------------------------------------


def _orca(method: str, version: str | None):
    return resolve_tau_with_assumption([], "ORCA", method, version)


@pytest.mark.parametrize(
    ("method", "version", "basis", "tau"),
    [
        ("M06-2X", "4.2.1", TauBasis.assumed_finite_difference_gradient, 50.0),
        ("M06-2X", "5.0.4", TauBasis.assumed_analytic_default, 30.0),
        ("M06-2X", "6.0.1", TauBasis.assumed_analytic_default, 30.0),
        ("TPSS", "4.2.1", TauBasis.assumed_finite_difference_gradient, 50.0),
        ("r2SCAN", "4.2.1", TauBasis.assumed_finite_difference_gradient, 50.0),
        ("B3LYP", "4.2.1", TauBasis.assumed_analytic_default, 30.0),
        ("PBE0", "4.0.0", TauBasis.assumed_analytic_default, 30.0),
        ("B3LYP", None, TauBasis.assumed_analytic_default, 30.0),
        ("B3LYP", "not-a-version", TauBasis.assumed_analytic_default, 30.0),
        # version-dependent family with no usable version: no assumption
        ("M06-2X", None, TauBasis.protocol_not_recorded, 50.0),
        ("M06-2X", "unparseable", TauBasis.protocol_not_recorded, 50.0),
        # VV10 and double hybrids: the manuals document numerical frequencies
        ("wB97X-V", "5.0.4", TauBasis.assumed_finite_difference_gradient, 50.0),
        ("wB97M-V", "6.0.1", TauBasis.assumed_finite_difference_gradient, 50.0),
        ("B3LYP-NL", "5.0.4", TauBasis.assumed_finite_difference_gradient, 50.0),
        ("B2PLYP", "5.0.4", TauBasis.assumed_finite_difference_gradient, 50.0),
        ("B2PLYP-D3", "4.2.1", TauBasis.assumed_finite_difference_gradient, 50.0),
        ("wB97X-2", "6.0.1", TauBasis.assumed_finite_difference_gradient, 50.0),
        ("wB97X-V", None, TauBasis.assumed_finite_difference_gradient, 50.0),
        ("B2PLYP", "unparseable", TauBasis.assumed_finite_difference_gradient, 50.0),
        # a name in no list is a plain functional only if the classifier calls it DFT
        ("mystery-functional", "4.2.1", TauBasis.protocol_not_recorded, 50.0),
    ],
)
def test_the_orca_table_is_gated_by_release_and_functional_class(method, version, basis, tau) -> None:
    resolved = _orca(method, version)
    assert resolved.basis is basis
    assert resolved.tau_cm1 == tau


def test_gaussian_is_not_version_gated() -> None:
    assert resolve_tau_with_assumption([], "Gaussian", "M06-2X", "09").basis is TauBasis.assumed_analytic_default


def test_the_orca_version_rule_is_named_in_the_reason() -> None:
    old = _orca("M06-2X", "4.2.1").reason
    assert "before version 5" in old and "meta-GGA" in old
    assert "M06-2X" not in old
    assert "meta-GGA" in _orca("M06-2X", "5.0.4").reason


def test_the_version_comes_from_the_payload_software_release() -> None:
    old = CalculationWithResultsPayload(
        type="freq",
        software_release={"name": "ORCA", "version": "4.2.1"},
        level_of_theory={"method": "M06-2X", "basis": "def2-SVP"},
        freq_result={"n_imag": 0},
    )
    new = old.model_copy(update={"software_release": old.software_release.model_copy(update={"version": "5.0.4"})})
    assert old.tau_resolution().basis is TauBasis.assumed_finite_difference_gradient
    assert new.tau_resolution().basis is TauBasis.assumed_analytic_default


def test_vv10_and_double_hybrid_reasons_cite_the_documented_numerical_outcome() -> None:
    for method, label in (("wB97X-V", "VV10"), ("B2PLYP", "double-hybrid")):
        reason = _orca(method, "5.0.4").reason
        assert label in reason and "numerical" in reason
        assert method.lower() not in reason.lower()


@pytest.mark.parametrize("version", ["45", "2024.1", "0.9", "100", "21.0"])
def test_a_leading_integer_outside_the_orca_major_range_is_an_unknown_version(version) -> None:
    # Not read as ">= 5": a meta-GGA gets no assumption, a plain hybrid keeps the analytic one.
    assert _orca("M06-2X", version).basis is TauBasis.protocol_not_recorded
    assert _orca("B3LYP", version).basis is TauBasis.assumed_analytic_default


@pytest.mark.parametrize("version", ["5", "5.0.4", "6.1.0", "ORCA 6.0.1"])
def test_a_plausible_orca_major_version_is_read(version) -> None:
    expected = TauBasis.assumed_analytic_default if not version.startswith("ORCA") else TauBasis.protocol_not_recorded
    assert _orca("M06-2X", version).basis is expected


@pytest.mark.parametrize("method", ["mPWB1K", "mPW1B95", "BB1K", "mPW1KCIS", "TPSS0", "TPSSh", "PW6B95", "BMK"])
def test_meta_hybrids_never_get_the_analytic_assumption_on_old_or_unknown_orca(method) -> None:
    assert _orca(method, "4.2.1").basis is not TauBasis.assumed_analytic_default
    assert _orca(method, None).basis is not TauBasis.assumed_analytic_default


# ---------------------------------------------------------------------------
# Gaussian double hybrids: not settled by the Gaussian documentation we can read,
# so nothing is assumed (ARC's table agrees for the names below).
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "method",
    ["DSD-BLYP", "DSDPBEP86", "revDSD-PBEP86-D4", "PWPB95", "PBE-QIDH", "PBE0-DH", "B2GPPLYP", "wB97X-2", "B2PLYP", "B2PLYP-D3", "mPW2PLYP"],
)
def test_gaussian_double_hybrids_get_no_assumption(method) -> None:
    resolved = resolve_tau_with_assumption([], "Gaussian", method)
    assert resolved.basis is TauBasis.protocol_not_recorded
    assert resolved.tau_cm1 == 50.0


@pytest.mark.parametrize("method", ["B3LYP", "PBE0", "wB97XD", "M06-2X", "HF", "MP2"])
def test_plain_gaussian_methods_keep_the_analytic_assumption(method) -> None:
    assert resolve_tau_with_assumption([], "Gaussian", method).basis is TauBasis.assumed_analytic_default


def test_a_recorded_method_still_wins_on_a_gaussian_double_hybrid() -> None:
    resolved = resolve_tau_with_assumption([("freq.hessian_method", "analytic")], "Gaussian", "B2PLYP")
    assert resolved.basis is TauBasis.analytic_default


# ---------------------------------------------------------------------------
# Names the family classifier must recognise as DFT so they reach their rows
# ---------------------------------------------------------------------------

_DOUBLE_HYBRIDS = [
    "DSD-BLYP", "DSD-PBEP86", "DSDPBEP86", "revDSD-PBEP86-D4", "PWPB95", "mPW2PLYP",
    "B2GP-PLYP", "B2GPPLYP", "pr2SCAN50", "B2PLYP", "B2PLYP-D3", "PBE-QIDH", "PBE0-DH", "wB97X-2",
]


@pytest.mark.parametrize("method", _DOUBLE_HYBRIDS)
def test_orca_double_hybrids_reach_the_numerical_row_on_every_version(method) -> None:
    for version in ("4.2.1", "5.0.4", "6.0.1", None):
        resolved = _orca(method, version)
        assert resolved.basis is TauBasis.assumed_finite_difference_gradient, (method, version)


@pytest.mark.parametrize("method", _DOUBLE_HYBRIDS)
def test_gaussian_double_hybrids_get_no_assumption_by_name(method) -> None:
    assert resolve_tau_with_assumption([], "Gaussian", method).basis is TauBasis.protocol_not_recorded


@pytest.mark.parametrize("method", ["mPW1B95", "BB1K", "mPWB1K", "mPW1KCIS"])
def test_orca_meta_hybrids_follow_the_release_gate(method) -> None:
    assert _orca(method, "4.2.1").basis is TauBasis.assumed_finite_difference_gradient
    assert _orca(method, None).basis is TauBasis.protocol_not_recorded
    assert _orca(method, "5.0.4").basis is TauBasis.assumed_analytic_default
