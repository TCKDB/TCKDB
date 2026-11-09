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
