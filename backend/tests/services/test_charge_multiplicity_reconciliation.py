"""Tests for charge / spin-multiplicity reconciliation against ESS output logs.

These exercise the check that makes ``charge_mismatch`` and
``multiplicity_mismatch`` able to fire at all. Before the log-derived source
existed, the Layer-2 deduction pass compared the upload payload against an
``ESSJobMeta`` it had just built *from that same payload*, so no input could
make the comparison unequal and no test of a genuine mismatch was possible.
"""

from __future__ import annotations

import os

import pytest

from app.services.charge_multiplicity_reconciliation import (
    W_CHARGE_MISMATCH,
    W_MULTIPLICITY_MISMATCH,
    ChargeMultiplicityAction,
    parse_charge_multiplicity_from_log,
    reconcile_charge_multiplicity,
)

_FIX = os.path.join(os.path.dirname(__file__), "..", "fixtures")


def _read(*parts: str) -> str:
    with open(os.path.join(_FIX, *parts), encoding="utf-8", errors="replace") as fh:
        return fh.read()


# A real Gaussian UB3LYP single-point log: ``Charge =  0 Multiplicity = 3``.
GAUSSIAN_TRIPLET = _read("gaussian", "sp_ub3lyp_g16.log")
# A real ORCA input echo: ``* xyz 0 2``.
ORCA_DOUBLET = _read("orca", "sp_orca_minimal.txt")
# A real Molpro CCSD(T)-F12 deck: ``wf,spin=1,charge=0`` -> charge 0, mult 2.
MOLPRO_DOUBLET = _read("molpro", "ch3_radical", "input.out")
# A real Molpro MRCI deck in positional form: ``wf,9,2,1`` -> mult 2, and a
# charge that is *derived*, never declared.
MOLPRO_MRCI_DOUBLET = _read("molpro", "mrci", "nh2_radical", "input.out")


def _codes(outcome) -> list[str]:
    return [w.code for w in outcome.warnings]


# ---------------------------------------------------------------------------
# The point of the change: a genuine contradiction is now detected
# ---------------------------------------------------------------------------


class TestGenuineMismatchIsDetected:
    def test_multiplicity_mismatch_fires(self):
        """Log says triplet, uploader declared a singlet."""
        outcome = reconcile_charge_multiplicity(
            declared_charge=0,
            declared_multiplicity=1,
            log_text=GAUSSIAN_TRIPLET,
        )
        assert outcome.action is ChargeMultiplicityAction.mismatch
        assert _codes(outcome) == [W_MULTIPLICITY_MISMATCH]
        assert outcome.log_multiplicity == 3
        assert outcome.declared_multiplicity == 1
        assert outcome.warnings[0].field == "species_entry.multiplicity"
        assert "3" in outcome.warnings[0].message

    def test_charge_mismatch_fires(self):
        """Log says neutral, uploader declared a cation."""
        outcome = reconcile_charge_multiplicity(
            declared_charge=1,
            declared_multiplicity=3,
            log_text=GAUSSIAN_TRIPLET,
        )
        assert outcome.action is ChargeMultiplicityAction.mismatch
        assert _codes(outcome) == [W_CHARGE_MISMATCH]
        assert outcome.log_charge == 0
        assert outcome.warnings[0].field == "species_entry.charge"

    def test_both_mismatch_together(self):
        outcome = reconcile_charge_multiplicity(
            declared_charge=-1,
            declared_multiplicity=1,
            log_text=GAUSSIAN_TRIPLET,
        )
        assert outcome.action is ChargeMultiplicityAction.mismatch
        assert _codes(outcome) == [W_CHARGE_MISMATCH, W_MULTIPLICITY_MISMATCH]

    def test_mismatch_on_orca(self):
        outcome = reconcile_charge_multiplicity(
            declared_charge=0, declared_multiplicity=1, log_text=ORCA_DOUBLET
        )
        assert _codes(outcome) == [W_MULTIPLICITY_MISMATCH]
        assert outcome.software == "orca"

    def test_mismatch_on_molpro(self):
        outcome = reconcile_charge_multiplicity(
            declared_charge=0, declared_multiplicity=1, log_text=MOLPRO_DOUBLET
        )
        assert _codes(outcome) == [W_MULTIPLICITY_MISMATCH]
        assert outcome.software == "molpro"

    def test_transition_state_owner_field_prefix(self):
        outcome = reconcile_charge_multiplicity(
            declared_charge=0,
            declared_multiplicity=1,
            log_text=GAUSSIAN_TRIPLET,
            field_prefix="transition_state_entry",
        )
        assert outcome.warnings[0].field == "transition_state_entry.multiplicity"


# ---------------------------------------------------------------------------
# Agreement is silent
# ---------------------------------------------------------------------------


class TestMatchingValuesEmitNothing:
    @pytest.mark.parametrize(
        ("log", "charge", "multiplicity"),
        [
            (GAUSSIAN_TRIPLET, 0, 3),
            (ORCA_DOUBLET, 0, 2),
            (MOLPRO_DOUBLET, 0, 2),
        ],
    )
    def test_agreement_is_confirmed_and_silent(self, log, charge, multiplicity):
        outcome = reconcile_charge_multiplicity(
            declared_charge=charge,
            declared_multiplicity=multiplicity,
            log_text=log,
        )
        assert outcome.action is ChargeMultiplicityAction.confirmed
        assert outcome.warnings == []


# ---------------------------------------------------------------------------
# Absence is not a contradiction (ADR 0008)
# ---------------------------------------------------------------------------


class TestUnparseableEmitsNothing:
    @pytest.mark.parametrize(
        "log_text",
        [
            pytest.param(None, id="no_artifact"),
            pytest.param("", id="empty"),
            pytest.param("   \n\n ", id="whitespace"),
            pytest.param(
                "GAMESS VERSION = 30 JUN 2020\n CHARGE OF MOLECULE = 0\n",
                id="unsupported_program",
            ),
            pytest.param(
                "\x00\x01\x02 binary garbage \xff",
                id="binary_garbage",
            ),
            pytest.param(
                " Entering Gaussian System, Link 0=g16\n",
                id="gaussian_truncated_before_charge_line",
            ),
            pytest.param(
                "***  PROGRAM SYSTEM MOLPRO  ***\n memory,100,m;\n",
                id="molpro_no_wf_directive",
            ),
        ],
    )
    def test_no_warning_when_nothing_could_be_parsed(self, log_text):
        outcome = reconcile_charge_multiplicity(
            declared_charge=1,
            declared_multiplicity=7,
            log_text=log_text,
        )
        assert outcome.action is ChargeMultiplicityAction.unverifiable
        assert outcome.warnings == []
        assert outcome.log_charge is None
        assert outcome.log_multiplicity is None

    def test_nothing_declared_is_absent_not_mismatch(self):
        outcome = reconcile_charge_multiplicity(
            declared_charge=None,
            declared_multiplicity=None,
            log_text=GAUSSIAN_TRIPLET,
        )
        assert outcome.action is ChargeMultiplicityAction.absent
        assert outcome.warnings == []


# ---------------------------------------------------------------------------
# Reject-don't-guess: ambiguous or inferred values are never compared
# ---------------------------------------------------------------------------


class TestRejectRatherThanGuess:
    def test_conflicting_gaussian_declarations_are_unknown(self):
        """A counterpoise/ONIOM log declares the pair once per fragment.

        The first match describes a fragment, not the system, so a
        disagreement between declarations must yield *unknown* rather than
        a fabricated mismatch against whichever one came first.
        """
        log = (
            " Entering Gaussian System, Link 0=g16\n"
            " Charge =  0 Multiplicity = 1\n"
            " Charge =  1 Multiplicity = 2\n"
        )
        assert parse_charge_multiplicity_from_log(log) is None
        outcome = reconcile_charge_multiplicity(
            declared_charge=0, declared_multiplicity=1, log_text=log
        )
        assert outcome.action is ChargeMultiplicityAction.unverifiable
        assert outcome.warnings == []

    def test_repeated_agreeing_declarations_are_trusted(self):
        """Multi-step (``--Link1--``) logs repeat the same pair; that is fine."""
        log = (
            " Entering Gaussian System, Link 0=g16\n"
            " Charge =  0 Multiplicity = 2\n"
            " Charge =  0 Multiplicity = 2\n"
        )
        parsed = parse_charge_multiplicity_from_log(log)
        assert parsed is not None
        assert (parsed.charge, parsed.multiplicity) == (0, 2)

    def test_molpro_positional_charge_is_never_compared(self):
        """MRCI decks derive the charge from the geometry; that is an inference.

        The multiplicity, which the deck states outright, is still checked.
        """
        parsed = parse_charge_multiplicity_from_log(MOLPRO_MRCI_DOUBLET)
        assert parsed is not None
        assert parsed.charge is None
        assert parsed.multiplicity == 2

        outcome = reconcile_charge_multiplicity(
            declared_charge=99,  # would mismatch a derived charge
            declared_multiplicity=2,
            log_text=MOLPRO_MRCI_DOUBLET,
        )
        assert outcome.action is ChargeMultiplicityAction.confirmed
        assert outcome.warnings == []

    def test_molpro_omitted_charge_keyword_is_not_assumed_neutral(self):
        """``wf,spin=0`` without ``charge=`` means unknown, not neutral."""
        log = "***  PROGRAM SYSTEM MOLPRO  ***\n basis=cc-pvtz\n wf,spin=0;\n"
        parsed = parse_charge_multiplicity_from_log(log)
        assert parsed is not None
        assert parsed.charge is None
        assert parsed.multiplicity == 1

        outcome = reconcile_charge_multiplicity(
            declared_charge=1, declared_multiplicity=1, log_text=log
        )
        assert outcome.action is ChargeMultiplicityAction.confirmed
        assert outcome.warnings == []

    def test_unphysical_multiplicity_is_discarded(self):
        log = (
            " Entering Gaussian System, Link 0=g16\n"
            " Charge =  0 Multiplicity = 0\n"
        )
        parsed = parse_charge_multiplicity_from_log(log)
        assert parsed is not None
        assert parsed.multiplicity is None
        assert parsed.charge == 0

        outcome = reconcile_charge_multiplicity(
            declared_charge=0, declared_multiplicity=1, log_text=log
        )
        assert outcome.warnings == []

    def test_declared_field_absent_is_not_compared(self):
        outcome = reconcile_charge_multiplicity(
            declared_charge=None,
            declared_multiplicity=1,
            log_text=GAUSSIAN_TRIPLET,
        )
        assert _codes(outcome) == [W_MULTIPLICITY_MISMATCH]
