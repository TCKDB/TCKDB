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

FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "..", "fixtures", "molpro")

# Known SP energies re-derived from the fixtures (Hartree).
CH4_ENERGY = -40.457885930635
NH2_ENERGY = -55.79346542


def _read(name: str) -> str:
    with open(os.path.join(FIXTURES_DIR, name, "input.out")) as f:
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
