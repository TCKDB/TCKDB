"""Reconciling a deposited composite energy with the Gaussian log (ADR 0021, P3b).

The log is a real CBS-QB3 run (``cbs_qb3_ts_c2h5no2_g16.out``) that prints
``E(ZPE) = 0.072623`` and ``CBS-QB3 (0 K) = -283.819775``. The deposited values
below are those printed numbers, then moved by chosen amounts around the
printed-precision tolerance.

Non-vacuity: the "no warning" tests would pass if the comparison never ran, so
each is paired with a test that moves one number across the boundary and sees
the warning, on the same log.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.services.composite_energy_reconciliation import (
    UNVERIFIABLE_NO_SUPPORTED_BLOCK,
    UNVERIFIABLE_NOT_GAUSSIAN,
    W_COMPOSITE_ENERGY_LOG_AVAILABLE,
    W_COMPOSITE_ENERGY_LOG_MISMATCH,
    W_COMPOSITE_LOG_METHOD_MISMATCH,
    CompositeEnergyAction,
    reconcile_composite_energy,
)
from app.services.sp_energy_reconciliation import (
    UNVERIFIABLE_GAUSSIAN_COMPOSITE_JOB,
    SpEnergyAction,
    parse_sp_energy_from_log,
    reconcile_sp_energy,
)

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"
_QB3_LOG = (FIXTURES / "gaussian_composite" / "cbs_qb3_ts_c2h5no2_g16.out").read_text()
_E0 = -283.819775
_ZPE = 0.072623
_ELECTRONIC = -283.892398  # E0 - ZPE, the ZPE-free energy


def _reconcile(**overrides):
    args = {
        "level_method": "CBS-QB3",
        "e0_hartree": _E0,
        "electronic_energy_hartree": _ELECTRONIC,
        "recipe_zpe_hartree": _ZPE,
        "log_text": _QB3_LOG,
    }
    args.update(overrides)
    return reconcile_composite_energy(**args)


def test_the_printed_numbers_confirm():
    outcome = _reconcile()
    assert outcome.action is CompositeEnergyAction.confirmed
    assert outcome.warning is None


def test_the_unrounded_archive_value_still_confirms():
    """The archive line prints ``CBSQB3=-283.8197747``; copying it is within printed precision."""
    assert _reconcile(e0_hartree=-283.8197747).action is CompositeEnergyAction.confirmed


@pytest.mark.parametrize(
    ("field", "printed", "inside", "outside", "tolerance"),
    [
        ("e0_hartree", _E0, 0.9e-6, 1.1e-6, "1e-6 (n=2)"),
        ("recipe_zpe_hartree", _ZPE, 0.9e-6, 1.1e-6, "1e-6 (n=2)"),
        ("electronic_energy_hartree", _ELECTRONIC, 1.4e-6, 1.6e-6, "1.5e-6 (n=3)"),
    ],
)
def test_the_tolerance_is_the_printed_precision_one(field, printed, inside, outside, tolerance):
    assert _reconcile(**{field: printed + inside}).action is CompositeEnergyAction.confirmed, tolerance
    # Only the field under test moves, so a mismatch is attributable to it.
    over = _reconcile(**{field: printed + outside})
    assert over.action is CompositeEnergyAction.mismatch, tolerance
    assert over.warning.code == W_COMPOSITE_ENERGY_LOG_MISMATCH
    assert field in over.warning.message


def test_a_mismatch_names_every_disagreeing_number_and_says_nothing_is_filled():
    outcome = _reconcile(e0_hartree=_E0 + 0.01, recipe_zpe_hartree=_ZPE + 0.01)
    assert outcome.action is CompositeEnergyAction.mismatch
    message = outcome.warning.message
    assert "e0_hartree" in message and "recipe_zpe_hartree" in message
    assert "electronic_energy_hartree" not in message  # that one agrees
    assert "nothing is filled" in message
    assert outcome.warning.field == "composite_result"


def test_only_the_stated_numbers_are_compared():
    """A NULL deposited number is not compared and not filled."""
    assert _reconcile(e0_hartree=None, recipe_zpe_hartree=None).action is CompositeEnergyAction.confirmed
    only_e0_wrong = _reconcile(electronic_energy_hartree=None, recipe_zpe_hartree=None, e0_hartree=_E0 + 1e-3)
    assert only_e0_wrong.action is CompositeEnergyAction.mismatch


_NOTHING = {"e0_hartree": None, "electronic_energy_hartree": None, "recipe_zpe_hartree": None}


def test_nothing_deposited_with_a_readable_log_informs_and_fills_nothing():
    outcome = _reconcile(**_NOTHING)
    assert outcome.action is CompositeEnergyAction.available
    assert outcome.warning.code == W_COMPOSITE_ENERGY_LOG_AVAILABLE
    assert "-283.819775" in outcome.warning.message and "Nothing was filled" in outcome.warning.message
    assert not hasattr(outcome, "resolved_energy_hartree")


def test_nothing_deposited_with_an_unreadable_log_is_absent_and_silent():
    garbled = _QB3_LOG[: _QB3_LOG.index(" CBS-QB3 (0 K)=")]
    for text in (garbled, None, "not a log"):
        outcome = _reconcile(log_text=text, **_NOTHING)
        assert outcome.action is CompositeEnergyAction.absent
        assert outcome.warning is None


def test_nothing_deposited_and_a_log_of_another_method_warns_about_the_method_not_availability():
    outcome = _reconcile(level_method="CBS-4M", **_NOTHING)
    assert outcome.action is CompositeEnergyAction.method_mismatch


def test_the_outcome_carries_no_value_to_store():
    """The dataclass has no resolved/filled energy: TCKDB stores nothing it read from the log."""
    outcome = _reconcile(e0_hartree=None)
    assert outcome.action is CompositeEnergyAction.confirmed
    assert not hasattr(outcome, "resolved_energy_hartree")
    assert not hasattr(outcome, "filled")


# ---------------------------------------------------------------------------
# Method key
# ---------------------------------------------------------------------------


def test_a_log_of_another_method_than_the_level_warns_and_compares_nothing():
    # CBS-4M level, CBS-QB3 log, and an energy that would also mismatch.
    outcome = _reconcile(level_method="CBS-4M", e0_hartree=_E0 + 1.0)
    assert outcome.action is CompositeEnergyAction.method_mismatch
    assert outcome.warning.code == W_COMPOSITE_LOG_METHOD_MISMATCH
    assert "cbs-qb3" in outcome.warning.message and "'CBS-4M'" in outcome.warning.message


def test_rocbs_qb3_is_not_silently_re_keyed_to_cbs_qb3():
    outcome = _reconcile(level_method="ROCBS-QB3")
    assert outcome.action is CompositeEnergyAction.method_mismatch


def test_an_alias_spelling_of_the_same_method_is_not_a_mismatch():
    for spelling in ("cbs-qb3", "CBS-QB3", "cbsqb3"):
        assert _reconcile(level_method=spelling).action is CompositeEnergyAction.confirmed, spelling


def test_no_level_method_skips_the_method_check():
    assert _reconcile(level_method=None).action is CompositeEnergyAction.confirmed


# ---------------------------------------------------------------------------
# Unreadable logs
# ---------------------------------------------------------------------------


def test_an_unreadable_log_is_unverifiable_with_a_reason_and_no_warning():
    garbled = _QB3_LOG[: _QB3_LOG.index(" CBS-QB3 (0 K)=")]
    outcome = _reconcile(log_text=garbled)
    assert outcome.action is CompositeEnergyAction.unverifiable
    assert outcome.unverifiable_reason == UNVERIFIABLE_NO_SUPPORTED_BLOCK
    assert outcome.warning is None


@pytest.mark.parametrize("text", [None, "", "just some text"])
def test_a_non_gaussian_log_is_unverifiable(text):
    outcome = _reconcile(log_text=text)
    assert outcome.action is CompositeEnergyAction.unverifiable
    assert outcome.unverifiable_reason == UNVERIFIABLE_NOT_GAUSSIAN


# ---------------------------------------------------------------------------
# The legacy shape: a composite route on an sp keeps its refusal, with the reason
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name",
    [
        "cbs_qb3_ts_c2h5no2_g16.out",
        "rocbs_qb3_methanol_g16.out",
        "cbs_4m_methanol_g16.out",
        "g4_methanol_g16.out",
        "g4mp2_methanol_g16.out",
        "g3_ethylene_g03.log",
    ],
)
def test_a_composite_log_still_yields_no_single_point_energy(name):
    text = (FIXTURES / "gaussian_composite" / name).read_text()
    assert parse_sp_energy_from_log(text) is None


def test_the_refusal_records_its_reason_whether_or_not_the_payload_had_an_energy():
    with_payload = reconcile_sp_energy(payload_energy_hartree=_E0, log_text=_QB3_LOG)
    assert with_payload.action is SpEnergyAction.unverifiable
    assert with_payload.resolved_energy_hartree == _E0  # the producer's value stands
    assert with_payload.unverifiable_reason == UNVERIFIABLE_GAUSSIAN_COMPOSITE_JOB

    without_payload = reconcile_sp_energy(payload_energy_hartree=None, log_text=_QB3_LOG)
    assert without_payload.action is SpEnergyAction.absent  # nothing is filled from a sub-step
    assert without_payload.resolved_energy_hartree is None
    assert without_payload.unverifiable_reason == UNVERIFIABLE_GAUSSIAN_COMPOSITE_JOB


def test_an_ordinary_gaussian_sp_log_records_no_such_reason():
    plain = (FIXTURES / "gaussian" / "sp_ub3lyp_g16.log").read_text()
    outcome = reconcile_sp_energy(payload_energy_hartree=None, log_text=plain)
    assert outcome.action is SpEnergyAction.filled
    assert outcome.unverifiable_reason is None
