"""Tests for Psi4 charge / spin-multiplicity parsing.

Exercised against real Psi4 logs truncated to their header region — see
``tests/fixtures/psi4/README.md`` for provenance. Psi4 support is
deliberately narrow: charge and multiplicity only, no single-point energy
(the choice of which ``Total Energy`` line is the answer is
method-dependent), no frequencies, no geometries.
"""

from __future__ import annotations

import os

import pytest

from app.services.psi4_parameter_parser import parse_all_charge_multiplicity

_FIX = os.path.join(os.path.dirname(__file__), "..", "fixtures", "psi4")


def _read(name: str) -> str:
    with open(os.path.join(_FIX, name), encoding="utf-8", errors="replace") as fh:
        return fh.read()


# ---------------------------------------------------------------------------
# Real logs, multiplicity 1 / 2 / 3
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("fixture", "charge", "multiplicity"),
    [
        pytest.param("opt_freq_singlet.out", 0, 1, id="singlet_opt_freq"),
        pytest.param("opt_freq_dft_ts_singlet.out", 0, 1, id="singlet_dft_ts"),
        pytest.param("sp_nh2_doublet.dat", 0, 2, id="doublet_nh2"),
        pytest.param("sp_mrcc_triplet.dat", 0, 3, id="triplet_mrcc"),
    ],
)
def test_reads_charge_and_multiplicity(fixture, charge, multiplicity):
    found = parse_all_charge_multiplicity(_read(fixture))
    assert found, "expected at least one declaration"
    # Every declaration in a single-molecule job agrees.
    assert set(found) == {(charge, multiplicity)}


def test_both_declaration_forms_are_read():
    """Psi4 states the pair twice: a geometry header and an SCF block.

    Both must be picked up — the geometry header alone would miss a log
    whose header line was clipped, and the SCF block alone would miss a
    job that never reached SCF.
    """
    text = _read("sp_mrcc_triplet.dat")
    assert "charge = 0, multiplicity = 3" in text
    assert "Charge       = 0" in text
    # One geometry header + one SCF block in this truncated fixture.
    assert parse_all_charge_multiplicity(text) == [(0, 3), (0, 3)]


# ---------------------------------------------------------------------------
# Reject-don't-guess
# ---------------------------------------------------------------------------


def test_truncated_log_yields_nothing():
    """A log cut short before any declaration emits nothing at all.

    The fixture is a real Psi4 run that later died with a PSIO error, cut
    before its first charge/multiplicity block. Absence is not a
    contradiction: nothing is inferred, so nothing can be contradicted.
    """
    assert parse_all_charge_multiplicity(_read("io_error_truncated.out")) == []


@pytest.mark.parametrize(
    "text",
    [
        pytest.param("", id="empty"),
        pytest.param("not an output log at all\n", id="not_a_log"),
        pytest.param("\x00\x01\x02 binary garbage \xff", id="binary_garbage"),
    ],
)
def test_unparseable_text_yields_nothing(text):
    assert parse_all_charge_multiplicity(text) == []


def test_disagreeing_declarations_are_all_reported():
    """A counterpoise/SAPT job prints one header per fragment.

    The parser reports every declaration rather than silently returning the
    first, which is what lets the reconciliation see the disagreement and
    report *unknown* instead of fabricating a mismatch against whichever
    fragment happened to be printed first.
    """
    log = (
        "    Psi4: An Open-Source Ab Initio Electronic Structure Package\n"
        "    Geometry (in Angstrom), charge = 0, multiplicity = 1:\n"
        "    Geometry (in Angstrom), charge = 1, multiplicity = 2:\n"
    )
    found = parse_all_charge_multiplicity(log)
    assert found == [(0, 1), (1, 2)]
    # No single agreed value exists in either column.
    assert len({c for c, _ in found}) > 1
    assert len({m for _, m in found}) > 1


def test_repeated_agreeing_declarations_are_all_reported():
    """An optimisation repeats the pair every step; agreement is expected."""
    text = _read("opt_freq_dft_ts_singlet.out")
    found = parse_all_charge_multiplicity(text)
    assert len(found) > 1
    assert set(found) == {(0, 1)}


def test_unphysical_multiplicity_is_reported_not_silently_dropped():
    """``Multiplicity = 0`` is impossible (2S+1 >= 1).

    The parser reports it verbatim, mirroring Gaussian and ORCA. Discarding
    it is the reconciliation's job, which drops the multiplicity while
    keeping the charge from the same block — a distinction this function
    could not express by dropping the pair.
    """
    log = (
        "    Psi4: An Open-Source Ab Initio Electronic Structure Package\n"
        "  Charge       = 0\n"
        "  Multiplicity = 0\n"
    )
    assert parse_all_charge_multiplicity(log) == [(0, 0)]


def test_negative_charge_is_read():
    log = (
        "    Psi4: An Open-Source Ab Initio Electronic Structure Package\n"
        "    Geometry (in Angstrom), charge = -1, multiplicity = 2:\n"
    )
    assert parse_all_charge_multiplicity(log) == [(-1, 2)]


def test_crlf_and_tab_separated_blocks_are_read():
    """Line anchoring must not depend on LF or on space-padded alignment.

    A log fetched through a Windows share arrives CRLF-terminated, and the
    SCF block's padding is alignment, not syntax.
    """
    lf = (
        "    Psi4: An Open-Source Ab Initio Electronic Structure Package\n"
        "    Geometry (in Angstrom), charge = 0, multiplicity = 3:\n"
        "  Charge       = 0\n"
        "  Multiplicity = 3\n"
    )
    assert parse_all_charge_multiplicity(lf) == [(0, 3), (0, 3)]
    assert parse_all_charge_multiplicity(lf.replace("\n", "\r\n")) == [(0, 3), (0, 3)]
    tabbed = lf.replace("  Charge       =", "\tCharge\t=").replace(
        "  Multiplicity =", "\tMultiplicity\t="
    )
    assert parse_all_charge_multiplicity(tabbed) == [(0, 3), (0, 3)]


def test_scf_block_pair_must_be_adjacent_lines():
    """A stray ``Charge =`` far from a ``Multiplicity =`` is not a pair.

    Without line anchoring the whitespace between the two would span
    arbitrary intervening output and marry unrelated values.
    """
    log = (
        "    Psi4: An Open-Source Ab Initio Electronic Structure Package\n"
        "  Charge       = 0\n"
        "  Electrons    = 9\n"
        "  Multiplicity = 2\n"
    )
    assert parse_all_charge_multiplicity(log) == []
