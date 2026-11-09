"""Assumed Hessian methods that must agree with the program manuals.

Each case is quoted from the manual page the table cites (see the table note in
``tckdb_schemas.hessian_method_assumption``):

* Gaussian 09 MP-methods page: "MP3, MP4(DQ) and MP4(SDQ): Energies, analytic
  gradients, and numerical frequencies." / "MP4(SDTQ) and MP5: Analytic energies,
  numerical gradients, and numerical frequencies." / "Just specifying MP4
  defaults to MP4(SDTQ)." / "MP2, B2PLYP[D], mPW2PLYP[D]: Energies, analytic
  gradients, and analytic frequencies."
* ORCA 6.1 manual p. 224 (``! CCSD(T)`` with ``! Opt NumGrad`` as the example of
  an unavailable analytic gradient), p. 677 (analytic frequencies for HF, DFT);
  ORCA 5.0.4 8.1.3.6 (F12: "Gradients are not available").
"""

from __future__ import annotations

import pytest
from tckdb_schemas.enums import HessianMethod
from tckdb_schemas.hessian_method_assumption import (
    infer_hessian_method,
    resolve_tau_with_assumption,
)
from tckdb_schemas.stationary_point import TauBasis

_ANALYTIC = (TauBasis.assumed_analytic_default, 30.0)
_GRADIENT = (TauBasis.assumed_finite_difference_gradient, 50.0)
_ENERGY = (TauBasis.assumed_finite_difference_energy, 80.0)
_NOTHING = (TauBasis.protocol_not_recorded, 50.0)


def _got(software: str, method: str, version: str | None = None):
    resolved = resolve_tau_with_assumption([], software, method, version)
    return resolved.basis, resolved.tau_cm1


@pytest.mark.parametrize(
    ("method", "expected"),
    [
        ("MP3", _GRADIENT),
        ("MP4(DQ)", _GRADIENT),
        ("MP4(SDQ)", _GRADIENT),
        ("mp4sdq", _GRADIENT),
        # The bare keyword means MP4(SDTQ); both and MP5 differentiate energies.
        ("MP4", _ENERGY),
        ("mp4", _ENERGY),
        ("MP4(SDTQ)", _ENERGY),
        ("mp4sdtq", _ENERGY),
        ("MP5", _ENERGY),
        ("mp5", _ENERGY),
    ],
)
def test_gaussian_mp_family_follows_the_mp_keyword_page(method, expected) -> None:
    assert _got("Gaussian", method) == expected


@pytest.mark.parametrize(
    "method",
    ["B2PLYP", "B2PLYPD", "B2PLYPD3", "B2PLYP-D3", "b2plyp", "mPW2PLYP", "mPW2PLYPD"],
)
def test_gaussian_documented_double_hybrids_assume_analytic(method) -> None:
    assert _got("Gaussian", method) == _ANALYTIC


@pytest.mark.parametrize(
    "method",
    ["B2GPPLYP", "B2GP-PLYP", "DSD-BLYP", "DSDPBEP86", "PWPB95", "PBE-QIDH", "PBE0-DH", "wB97X-2", "B2PLYP-D4"],
)
def test_other_gaussian_double_hybrids_still_assume_nothing(method) -> None:
    assert _got("Gaussian", method) == _NOTHING


@pytest.mark.parametrize(
    "method",
    ["CCSD", "CCSD(T)", "CCSD(T)-F12", "F12-CCSD(T)", "DLPNO-CCSD", "DLPNO-CCSD(T)", "DLPNO-CCSD(T1)"],
)
@pytest.mark.parametrize("version", ["5.0.4", "6.1.0", None])
def test_orca_coupled_cluster_differentiates_energies(method, version) -> None:
    assert _got("ORCA", method, version) == _ENERGY


@pytest.mark.parametrize("method", ["MP2", "RI-MP2", "DLPNO-MP2", "SCS-MP2"])
@pytest.mark.parametrize("version", ["5.0.4", "6.1.0", None])
def test_orca_mp2_stays_at_gradients(method, version) -> None:
    assert _got("ORCA", method, version) == _GRADIENT


@pytest.mark.parametrize("method", ["DLPNO-CEPA/1", "AUTOCI-CCSD", "DLPNO-QCISD(T)"])
def test_orca_methods_without_a_documented_answer_assume_nothing(method) -> None:
    assert infer_hessian_method("ORCA", method, "6.1.0") is None


@pytest.mark.parametrize("method", ["MP4", "MP4(SDTQ)", "MP5", "CCSD(T)"])
def test_a_recorded_method_still_beats_these_assumptions(method) -> None:
    resolved = resolve_tau_with_assumption(
        [("freq.hessian_method", HessianMethod.analytic.value)], "Gaussian", method
    )
    assert resolved.basis is TauBasis.analytic_default


def test_unaffected_methods_keep_their_rows() -> None:
    assert _got("Gaussian", "B3LYP") == _ANALYTIC
    assert _got("Gaussian", "MP2") == _ANALYTIC
    assert _got("Gaussian", "CCSD") == _GRADIENT
    assert _got("Gaussian", "CCSD(T)") == _ENERGY
    assert _got("Gaussian", "QCISD(T)") == _ENERGY
    assert _got("ORCA", "B3LYP", "6.1.0") == _ANALYTIC
    assert _got("ORCA", "HF", "6.1.0") == _ANALYTIC


# ---------------------------------------------------------------------------
# Review round: option spellings, ORCA decks, hyphen-free double hybrids, F12-MP2, labels
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "method",
    [
        "MP4(SDTQ,FC)", "MP4(FC)", "MP4(SDTQ)-FC", "mp4(sdtq)/fc", "MP4=FC", "MP4 FC", "MP4(Full)",
        "MP4(SDTQ,Full)", "mp4=(sdtq,fc)", "MP4-FC",
    ],
)
def test_gaussian_mp4_options_do_not_change_the_truncation(method) -> None:
    assert _got("Gaussian", method) == _ENERGY


@pytest.mark.parametrize("method", ["MP4(SDQ,FC)", "MP4(DQ)-FC", "mp4(sdq)/fc", "MP4=(DQ,Full)", "MP4(SDQ) FC"])
def test_gaussian_mp4_sdq_options_stay_at_gradients(method) -> None:
    assert _got("Gaussian", method) == _GRADIENT


@pytest.mark.parametrize("method", ["MP4(XYZ)", "MP4(SDTQ,bogus)", "mp4=weird"])
def test_an_unknown_mp4_option_assumes_nothing(method) -> None:
    assert infer_hessian_method("Gaussian", method) is None


@pytest.mark.parametrize(
    ("method", "expected"),
    [
        ("CCSD(T,FC)", _ENERGY),
        ("CCSD(T,Full)", _ENERGY),
        ("CCSD(T,E4T)", _ENERGY),
        ("CCSD-T", _ENERGY),
        ("CCSD(FC)", _GRADIENT),
        ("CCSD(Full)", _GRADIENT),
        ("QCISD(T,E4T)", _ENERGY),
        ("QCISD(T,FC)", _ENERGY),
        ("QCISD(FC)", _GRADIENT),
        ("CCSD(T)", _ENERGY),
        ("QCISD(T)", _ENERGY),
        ("CCSD", _GRADIENT),
        ("QCISD", _GRADIENT),
        ("CCSD=(T,FC)", _ENERGY),
        ("CCSD (T)", _ENERGY),
        ("CCSD=FC", _GRADIENT),
        ("QCISD=(T,E4T)", _ENERGY),
    ],
)
def test_gaussian_cc_and_qcisd_options(method, expected) -> None:
    assert _got("Gaussian", method) == expected


@pytest.mark.parametrize("method", ["CCSD(T,bogus)", "CCSDT", "CCSD[T]", "CCSD/RI", "QCISDT", "CCSD(RO)", "MP4(RO)", "MP4(SDTQ,RO)"])
def test_unknown_tails_ro_and_ccsdt_assume_nothing(method) -> None:
    assert infer_hessian_method("Gaussian", method) is None


def test_f12_suffix_is_not_an_option() -> None:
    assert _got("ORCA", "CCSD(T)-F12", "6.1.0") == _ENERGY


@pytest.mark.parametrize("method", ["PBE0DH", "pbe0-dh", "PBEQIDH", "PBE0-QIDH", "wB97X-2", "wB97X2"])
def test_hyphen_free_double_hybrids_do_not_fall_into_plain_pbe0(method) -> None:
    for version in ("5.0.4", "6.1.0", None):
        assert _got("ORCA", method, version) == _GRADIENT, (method, version)
    assert _got("Gaussian", method) == _NOTHING


@pytest.mark.parametrize("method", ["PBE0", "PBE1PBE", "wB97XD"])
def test_plain_hybrids_stay_analytic_on_orca(method) -> None:
    assert _got("ORCA", method, "6.1.0")[0] is TauBasis.assumed_analytic_default


@pytest.mark.parametrize("method", ["B2PLYPD3BJ", "B2PLYP-D3BJ", "B2PLYP-D3(BJ)", "b2plypd3(bj)"])
def test_gaussian_b2plyp_d3bj_spellings_are_analytic(method) -> None:
    assert _got("Gaussian", method) == _ANALYTIC


@pytest.mark.parametrize("method", ["DLPNO-CCSD", "RI-MP2-F12", "F12-MP2", "F12-RI-MP2", "MP2-F12", "F12/RI-CCSD(T)"])
def test_orca_methods_with_no_analytic_gradient_differentiate_energies(method) -> None:
    assert _got("ORCA", method, "6.1.0") == _ENERGY


@pytest.mark.parametrize("method", ["MP2", "RI-MP2", "DLPNO-MP2", "SCS-MP2"])
def test_orca_mp2_without_f12_stays_at_gradients(method) -> None:
    assert _got("ORCA", method, "6.1.0") == _GRADIENT


def test_legacy_mp4_label_is_kept_and_the_new_token_is_labelled() -> None:
    from tckdb_schemas.deposit_warning_messages import ASSUMED_FAMILY
    from tckdb_schemas.hessian_method_assumption import MethodFamily, classify_method_family

    assert ASSUMED_FAMILY["mp4"] == "MP4"
    assert ASSUMED_FAMILY["mp4(sdq)"] == "MP4(DQ/SDQ)"
    assert classify_method_family("MP4(SDQ)") == MethodFamily.MP4_SDQ == "mp4(sdq)"
    assert classify_method_family("MP4") == MethodFamily.MP4_SDTQ
    assert MethodFamily.MP4 == "mp4"


_ORCA_DECK = "! {method} def2-TZVP {freq}\n* xyz 0 1\nH 0 0 0\nH 0 0 0.74\n*\n"


@pytest.mark.parametrize(
    "method",
    ["CCSD", "CCSD(T)", "CCSD(T)-F12", "DLPNO-CCSD", "DLPNO-CCSD(T)", "RI-MP2-F12", "F12-CCSD(T)"],
)
def test_orca_numfreq_deck_on_a_method_without_a_gradient_agrees_with_the_assumption(method) -> None:
    from tckdb_schemas.hessian_method_detection import detect_hessian_method

    deck = detect_hessian_method("orca", _ORCA_DECK.format(method=method, freq="NumFreq"))
    assumed = resolve_tau_with_assumption([], "ORCA", method, "6.1.0")
    assert deck is HessianMethod.finite_difference_energy
    assert assumed.basis is TauBasis.assumed_finite_difference_energy
    assert resolve_tau_with_assumption([], "ORCA", method, "6.1.0", deck_method=deck).tau_cm1 == assumed.tau_cm1


@pytest.mark.parametrize("method", ["MP2", "RI-MP2", "DLPNO-MP2", "B3LYP", "AUTOCI-CCSD"])
def test_orca_numfreq_deck_on_a_method_with_a_gradient_stays_at_gradients(method) -> None:
    from tckdb_schemas.hessian_method_detection import detect_hessian_method

    assert (
        detect_hessian_method("orca", _ORCA_DECK.format(method=method, freq="NumFreq"))
        is HessianMethod.finite_difference_gradient
    )


def test_orca_numfreq_numgrad_and_anfreq_decks_are_unchanged() -> None:
    from tckdb_schemas.hessian_method_detection import detect_hessian_method

    assert (
        detect_hessian_method("orca", _ORCA_DECK.format(method="B3LYP", freq="NumFreq NumGrad"))
        is HessianMethod.finite_difference_energy
    )
    assert detect_hessian_method("orca", _ORCA_DECK.format(method="B3LYP", freq="AnFreq")) is HessianMethod.analytic


@pytest.mark.parametrize("method", ["MP5", "UMP5", "F12-CCSD(T)", "F12/RI-CCSD(T)"])
def test_newly_recognised_methods_are_scf_based_so_they_get_the_assumed_scf_default(method) -> None:
    """Side effect of recognising them: ``uses_scf`` follows the family classifier (MPn and coupled cluster are
    SCF-based), so a Gaussian job in one of these methods now has an assumed ``scf_convergence``."""
    from tckdb_schemas.program_default_settings import assumed_default_setting, uses_scf

    assert uses_scf(method, "Gaussian") is True
    if method in ("MP5", "UMP5"):
        assert assumed_default_setting("Gaussian", "16", "scf_convergence", method) == "tight"
