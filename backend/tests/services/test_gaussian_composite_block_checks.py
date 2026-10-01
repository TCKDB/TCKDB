"""The checks every Gaussian composite block must pass (ADR 0021, P3b).

Companion to ``test_gaussian_composite_parser.py``: the block identity, the
archive cross-check, route wrapping, and partial-block handling.

G4 and G4MP2: the printed labels of the Gaussian 16 Rev A.03 blocks are shifted
-------------------------------------------------------------------------------
Evidence, read from the two fixtures themselves (not through the parser):

* the archive states ``\\G4=-115.6517642`` and ``\\G4MP2=-115.571053``, while the
  ``(0 K)`` lines state -115.648433 and -115.566778;
* the manual's identity ``Energy - E0 = E(Thermal) - E(ZPE)`` holds for the CBS and
  G3 logs and fails here by 0.002387 (G4) and 0.030352 (G4MP2) hartree;
* so the number under ``(0 K)`` is the 298 K energy (G4) or the enthalpy (G4MP2).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.services import gaussian_composite_parser
from app.services.gaussian_composite_parser import (
    UNREAD_COMPOSITE_METHOD_KEYS,
    _MethodLayout,
    parse_gaussian_composite_summary,
)

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "gaussian_composite"

# fixture -> (method key, printed (0 K) label, archive key, E0)
_GOOD = {
    "cbs_qb3_ts_c2h5no2_g16.out": ("cbs-qb3", "CBS-QB3", "CBSQB3", -283.819775),
    "cbs_qb3_ts_intra_h_migration_g09.out": ("cbs-qb3", "CBS-QB3", "CBSQB3", -118.135290),
    "cbs_qb3_so2oo_g03.log": ("cbs-qb3", "CBS-QB3", "CBSQB3", -698.186333),
    "rocbs_qb3_methanol_g16.out": ("rocbs-qb3", "CBS-QB3", "CBSQB3", -115.539942),
    "cbs_4m_methanol_g16.out": ("cbs-4m", "CBS-4", "CBS4M", -115.563231),
    "g3_ethylene_g03.log": ("g3", "G3", "G3", -78.507415),
}
_G4_LOGS = {
    "g4_methanol_g16.out": ("g4", "G4", 0.002387, "G4=-115.6517642"),
    "g4mp2_methanol_g16.out": ("g4mp2", "G4MP2", 0.030352, "G4MP2=-115.571053"),
}
_QB3 = "cbs_qb3_ts_c2h5no2_g16.out"


def _text(name: str) -> str:
    return (FIXTURES / name).read_text()


def _archive(text: str) -> str:
    """All archive lines joined the way Gaussian wraps them (one leading column dropped)."""
    return "".join(line[1:] for line in text.splitlines())


def _edit_last_archive(text: str, edit) -> str:
    """Replace the log's last archive entry by ``edit(joined)`` re-emitted as one line.

    Several fixtures wrap the entry mid-key (``\\CBS`` / ``4M=``), so the entry cannot be
    edited in place; the parser joins wrapped lines, so a one-line archive reads the same.
    """
    lines = text.splitlines()
    start = max(i for i, line in enumerate(lines) if line.startswith(" 1\\1\\"))
    end = start
    while not lines[end].rstrip().endswith("\\@"):
        end += 1
    joined = "".join(line[1:] for line in lines[start : end + 1])
    return "\n".join([*lines[:start], " " + edit(joined), *lines[end + 1 :]])


def _identity_gap(text: str, label: str) -> float:
    """``(Energy - E0) - (E(Thermal) - E(ZPE))`` of the last block, read straight from the text."""
    zpe, thermal = map(
        float, re.findall(r"E\(ZPE\)=\s+(-?\d+\.\d+)\s+E\(Thermal\)=\s+(-?\d+\.\d+)", text)[-1]
    )
    e0, energy = map(
        float,
        re.findall(rf"{label}\s?\(0 K\)=\s+(-?\d+\.\d+)\s+{label}\s+Energy=\s+(-?\d+\.\d+)", text)[-1],
    )
    return (energy - e0) - (thermal - zpe)


# ---------------------------------------------------------------------------
# G4 / G4MP2 are declined, with the evidence pinned
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", sorted(_G4_LOGS))
def test_g4_and_g4mp2_are_declined_not_read(name):
    key = _G4_LOGS[name][0]
    assert key in UNREAD_COMPOSITE_METHOD_KEYS
    assert parse_gaussian_composite_summary(_text(name)) is None


@pytest.mark.parametrize("name", sorted(_G4_LOGS))
def test_the_g4_blocks_violate_the_manuals_identity_by_the_measured_amount(name):
    _, label, gap, archive = _G4_LOGS[name]
    text = _text(name)
    assert abs(_identity_gap(text, label)) == pytest.approx(gap, abs=2e-6)
    # The archive says something other than the (0 K) line, which is the shift's other witness.
    assert "\\" + archive + "\\" in _archive(text)


@pytest.mark.parametrize("name", sorted(_GOOD))
def test_every_good_block_satisfies_the_identity(name):
    _, label, _, _ = _GOOD[name]
    assert abs(_identity_gap(_text(name), label)) <= 2e-6


@pytest.mark.parametrize("name", sorted(_G4_LOGS))
def test_the_identity_check_alone_rejects_the_shifted_blocks(name, monkeypatch):
    """Were G4 a supported layout and its archive not checked, the block is still refused."""
    key, label, _, _ = _G4_LOGS[name]
    monkeypatch.setitem(gaussian_composite_parser._LAYOUTS, key, _MethodLayout(label, "NO_SUCH_KEY", None))
    assert parse_gaussian_composite_summary(_text(name)) is None


@pytest.mark.parametrize("name", sorted(_G4_LOGS))
def test_the_archive_check_alone_rejects_the_shifted_blocks(name, monkeypatch):
    """With the identity check made vacuous, the archive entry still refuses the block."""
    key, label, _, _ = _G4_LOGS[name]
    monkeypatch.setitem(gaussian_composite_parser._LAYOUTS, key, _MethodLayout(label, label, None))
    real = gaussian_composite_parser.composite_arithmetic_tolerance_hartree
    monkeypatch.setattr(
        gaussian_composite_parser,
        "composite_arithmetic_tolerance_hartree",
        lambda n: 1.0 if n == 4 else real(n),
    )
    assert parse_gaussian_composite_summary(_text(name)) is None


# ---------------------------------------------------------------------------
# The archive cross-check
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", sorted(_GOOD))
def test_every_good_fixture_has_an_archive_entry_that_matches(name):
    """The cross-check is exercised on each fixture, not skipped for lack of an entry."""
    _, _, archive_key, e0 = _GOOD[name]
    values = re.findall(rf"\\{archive_key}=(-?\d+\.\d+)\\", _archive(_text(name)))
    assert values, f"{name} has no archive entry for {archive_key}"
    assert float(values[-1]) == pytest.approx(e0, abs=1e-6)


@pytest.mark.parametrize("name", sorted(_GOOD))
def test_a_disagreeing_archive_entry_yields_nothing_on_every_fixture(name):
    _, _, archive_key, _ = _GOOD[name]
    text = _text(name)
    assert parse_gaussian_composite_summary(text) is not None
    entry = re.findall(rf"\\{archive_key}=(-?\d+\.\d+)\\", _archive(text))[-1]
    changed = _edit_last_archive(
        text, lambda joined: joined.replace(f"\\{archive_key}={entry}\\", f"\\{archive_key}={float(entry) + 0.001:.7f}\\")
    )
    assert changed != text
    assert parse_gaussian_composite_summary(changed) is None


def test_an_archive_entry_wrapped_mid_number_is_still_read_whole():
    """Gaussian wraps the archive at a fixed column, inside the number."""
    text = _text(_QB3)
    wrapped = text.replace("\\CBSQB3=-283.8197747\\", "\\CBSQB3=-283.81\n 97747\\")
    assert wrapped != text
    assert parse_gaussian_composite_summary(wrapped) is not None
    # The same wrap with a changed digit is a real disagreement, not a parse failure.
    assert parse_gaussian_composite_summary(wrapped.replace("97747", "87747")) is None


# ---------------------------------------------------------------------------
# A route wrapped in the middle of a token
# ---------------------------------------------------------------------------


def test_a_route_wrapped_inside_the_method_name_is_still_rocbs_qb3():
    text = _text("rocbs_qb3_methanol_g16.out")
    printed = "tight) rocbs-qb3 scf"
    assert printed in text
    summary = parse_gaussian_composite_summary(text.replace(printed, "tight) ro\n cbs-qb3 scf", 1))
    assert summary is not None
    assert summary.method_key == "rocbs-qb3"  # never silently cbs-qb3


def test_a_route_wrapped_after_a_trailing_space_keeps_its_token_boundary():
    """``(tight, `` then ``direct)``: the space that ends a line is part of the route."""
    text = _text("cbs_4m_methanol_g16.out")
    assert "cbs-4m scf=(tight, \n direct)" in text
    assert parse_gaussian_composite_summary(text).method_key == "cbs-4m"


# ---------------------------------------------------------------------------
# A partial block is never skipped for a later complete one
# ---------------------------------------------------------------------------


def test_a_partial_block_followed_by_a_complete_one_refuses_the_whole_log():
    text = _text(_QB3)
    start = text.index("Temperature=               298.150000 Pressure=")
    end = text.index(" CBS-QB3 Enthalpy=", start)
    block = text[start:end]
    printed = " DE(CCSD)=                   -0.034546 DE(Int)=                        0.032180"
    assert printed in block
    partial = block.replace(printed, " DE(Int)=                        0.032180")
    assert parse_gaussian_composite_summary(text) is not None
    assert parse_gaussian_composite_summary(text[:start] + partial + "\n" + text[start:]) is None
