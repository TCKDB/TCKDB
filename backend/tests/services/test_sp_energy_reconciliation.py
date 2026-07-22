"""Tests for tool-vs-log single-point energy reconciliation.

Exercised against the same real Molpro fixtures as the parser tests:

* ``ch4_closed_shell`` — CCSD(T)-F12, E = -40.457885930635 Ha
* ``mrci/nh2_radical`` — plain MRCI, E = -55.79346542 Ha

The reconciliation service is DB-free: these tests import and call it
directly, no session.
"""

from __future__ import annotations

import os

from app.services.sp_energy_reconciliation import (
    SP_ENERGY_ABS_TOL_HARTREE,
    W_SP_ENERGY_FILLED_FROM_LOG,
    W_SP_ENERGY_MISMATCH,
    SpEnergyAction,
    parse_sp_energy_from_log,
    reconcile_sp_energy,
)

_FIX = os.path.join(os.path.dirname(__file__), "..", "fixtures")
FIXTURES_DIR = os.path.join(_FIX, "molpro")

# Known SP energies re-derived from the real fixtures (Hartree).
CH4_ENERGY = -40.457885930635
NH2_ENERGY = -55.79346542
ORCA_ENERGY = -155.014748716014  # tests/fixtures/orca — DLPNO-CCSD(T)
GAUSSIAN_ENERGY = -75.096275352  # tests/fixtures/gaussian — UB3LYP DFT


def _read(name: str) -> str:
    with open(os.path.join(FIXTURES_DIR, name, "input.out")) as f:
        return f.read()


def _read_orca() -> str:
    with open(os.path.join(_FIX, "orca", "sp_dlpno_ccsdt_orca.out")) as f:
        return f.read()


def _read_gaussian() -> str:
    with open(os.path.join(_FIX, "gaussian", "sp_ub3lyp_g16.log")) as f:
        return f.read()


def test_confirmed_when_payload_matches_log() -> None:
    log = _read("ch4_closed_shell")
    result = reconcile_sp_energy(payload_energy_hartree=CH4_ENERGY, log_text=log)

    assert result.action is SpEnergyAction.confirmed
    assert result.resolved_energy_hartree == CH4_ENERGY
    assert result.warning is None


def test_confirmed_within_tolerance() -> None:
    """A payload rounded to just inside the tolerance still confirms."""
    log = _read("ch4_closed_shell")
    nudged = CH4_ENERGY + SP_ENERGY_ABS_TOL_HARTREE / 2
    result = reconcile_sp_energy(payload_energy_hartree=nudged, log_text=log)

    assert result.action is SpEnergyAction.confirmed
    # The reported (payload) value is what is kept, not the log's.
    assert result.resolved_energy_hartree == nudged
    assert result.warning is None


def test_mismatch_emits_warning_and_keeps_payload_value() -> None:
    log = _read("ch4_closed_shell")
    wrong = CH4_ENERGY + 0.01  # 10 mHa off — far outside tolerance
    result = reconcile_sp_energy(payload_energy_hartree=wrong, log_text=log)

    assert result.action is SpEnergyAction.mismatch
    # TCKDB flags but does NOT overwrite the submitter's value.
    assert result.resolved_energy_hartree == wrong
    assert result.log_energy_hartree == CH4_ENERGY
    assert result.warning is not None
    assert result.warning.code == W_SP_ENERGY_MISMATCH
    assert result.warning.field == "sp_result.electronic_energy_hartree"


def test_mismatch_just_outside_tolerance() -> None:
    log = _read("ch4_closed_shell")
    just_off = CH4_ENERGY + SP_ENERGY_ABS_TOL_HARTREE * 2
    result = reconcile_sp_energy(payload_energy_hartree=just_off, log_text=log)

    assert result.action is SpEnergyAction.mismatch
    assert result.warning is not None


def test_filled_when_payload_missing_but_log_has_energy() -> None:
    log = _read("mrci/nh2_radical")
    result = reconcile_sp_energy(payload_energy_hartree=None, log_text=log)

    assert result.action is SpEnergyAction.filled
    assert result.resolved_energy_hartree == NH2_ENERGY
    assert result.warning is not None
    assert result.warning.code == W_SP_ENERGY_FILLED_FROM_LOG


def test_unverifiable_when_log_has_no_parseable_energy() -> None:
    """A reported energy with a log we cannot re-parse is trusted as-is."""
    # A non-Molpro log (no banner) yields no re-derived energy.
    result = reconcile_sp_energy(
        payload_energy_hartree=-100.5,
        log_text="SCF Done:  E(RB3LYP) =  -100.5  A.U. after 8 cycles",
    )

    assert result.action is SpEnergyAction.unverifiable
    assert result.resolved_energy_hartree == -100.5
    assert result.log_energy_hartree is None
    assert result.warning is None


def test_unverifiable_when_no_log_provided() -> None:
    result = reconcile_sp_energy(payload_energy_hartree=-40.5, log_text=None)

    assert result.action is SpEnergyAction.unverifiable
    assert result.resolved_energy_hartree == -40.5
    assert result.warning is None


def test_absent_when_neither_present() -> None:
    result = reconcile_sp_energy(payload_energy_hartree=None, log_text=None)

    assert result.action is SpEnergyAction.absent
    assert result.resolved_energy_hartree is None
    assert result.warning is None


def test_dispatcher_returns_none_for_non_molpro_log() -> None:
    assert parse_sp_energy_from_log("some ORCA or Gaussian text") is None
    assert parse_sp_energy_from_log(None) is None
    assert parse_sp_energy_from_log("") is None


def test_dispatcher_extracts_real_molpro_energy() -> None:
    assert parse_sp_energy_from_log(_read("ch4_closed_shell")) == CH4_ENERGY
    assert parse_sp_energy_from_log(_read("mrci/nh2_radical")) == NH2_ENERGY


def test_dispatcher_gate_is_case_insensitive() -> None:
    # A lowercased banner still routes to the Molpro parser.
    log = _read("ch4_closed_shell").lower()
    assert parse_sp_energy_from_log(log) == CH4_ENERGY


def test_dispatcher_rejects_non_finite_energy(monkeypatch) -> None:
    # A parser that returns NaN/inf must be treated as "no energy" so a
    # non-finite value can never be filled or compared.
    banner = "***  PROGRAM SYSTEM MOLPRO  ***\n!RHF energy nonsense\n"
    for bad in (float("nan"), float("inf"), float("-inf")):
        monkeypatch.setattr(
            "app.services.molpro_parameter_parser.parse_sp_energy",
            lambda _text, _v=bad: _v,
        )
        assert parse_sp_energy_from_log(banner) is None


# ---------------------------------------------------------------------------
# ORCA and Gaussian — the log picks its own parser by banner content
# ---------------------------------------------------------------------------


def test_dispatcher_extracts_real_orca_energy() -> None:
    assert parse_sp_energy_from_log(_read_orca()) == ORCA_ENERGY


def test_dispatcher_extracts_real_gaussian_energy() -> None:
    assert parse_sp_energy_from_log(_read_gaussian()) == GAUSSIAN_ENERGY


def test_orca_confirms_matching_payload() -> None:
    result = reconcile_sp_energy(
        payload_energy_hartree=ORCA_ENERGY, log_text=_read_orca()
    )
    assert result.action is SpEnergyAction.confirmed
    assert result.warning is None


def test_orca_fills_when_payload_missing() -> None:
    result = reconcile_sp_energy(payload_energy_hartree=None, log_text=_read_orca())
    assert result.action is SpEnergyAction.filled
    assert result.resolved_energy_hartree == ORCA_ENERGY


def test_gaussian_mismatch_warns_and_keeps_payload() -> None:
    wrong = GAUSSIAN_ENERGY + 0.01
    result = reconcile_sp_energy(
        payload_energy_hartree=wrong, log_text=_read_gaussian()
    )
    assert result.action is SpEnergyAction.mismatch
    assert result.resolved_energy_hartree == wrong
    assert result.log_energy_hartree == GAUSSIAN_ENERGY
    assert result.warning is not None
    assert result.warning.code == W_SP_ENERGY_MISMATCH


def test_gaussian_composite_method_is_unverifiable() -> None:
    """A composite (CBS/Gn) run interleaves intermediate SCF Done lines;
    the value is not cross-checkable, so the payload stands unchanged."""
    composite_log = (
        "Entering Gaussian System, Link 0=g16\n"
        "# CBS-QB3\n"
        "SCF Done:  E(RHF) =  -76.000000  A.U. after 8 cycles\n"
        "CBS-QB3 (0 K)=  -76.500000\n"
        "E(CBS-QB3)=  -76.400000\n"
    )
    result = reconcile_sp_energy(
        payload_energy_hartree=-76.4, log_text=composite_log
    )
    assert result.action is SpEnergyAction.unverifiable
    assert result.log_energy_hartree is None
    assert result.resolved_energy_hartree == -76.4


def test_gaussian_g3mp2_and_truncated_composite_unverifiable() -> None:
    # G3MP2 is a family the old substring gate missed; its intermediate
    # EUMP2 must NOT leak as the SP energy.
    g3mp2 = (
        "Entering Gaussian System, Link 0=g16\n"
        "#p g3mp2\n\ntitle\n\n"
        " SCF Done:  E(UHF) =  -75.58  A.U. after 8 cycles\n"
        " E2 = -0.18D+00 EUMP2 = -0.75773189207D+02\n"
        " G3MP2(0 K)=  -75.912418      G3MP2 Energy=  -75.909529\n"
    )
    assert parse_sp_energy_from_log(g3mp2) is None
    # A composite job killed before its result section (lowercase route,
    # no result markers) is caught by the route echo.
    truncated = (
        "Entering Gaussian System, Link 0=g16\n"
        "#p g3\n\ntitle\n\n"
        " SCF Done:  E(UHF) =  -75.58  A.U. after 8 cycles\n"
    )
    assert parse_sp_energy_from_log(truncated) is None


def test_gaussian_dft_mentioning_composite_in_title_still_extracts() -> None:
    # A plain DFT job whose title merely mentions a composite method must
    # still have its SCF Done energy extracted (route-based gate, not a
    # whole-text substring scan).
    log = (
        "Entering Gaussian System, Link 0=g16\n"
        "#p ub3lyp/def2tzvp\n\nbenchmark vs G4MP2 reference set\n\n"
        " SCF Done:  E(UB3LYP) =  -76.123400  A.U. after 10 cycles\n"
    )
    assert parse_sp_energy_from_log(log) == -76.1234
