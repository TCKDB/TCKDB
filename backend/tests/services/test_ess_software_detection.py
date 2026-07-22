"""Tests for content-based ESS software detection.

The program is identified from its banner in the log content, so detection
is independent of the filename/extension. Exercised against real fixtures.
"""

from __future__ import annotations

import os

from app.services.ess_software_detection import detect_software_from_text

_FIX = os.path.join(os.path.dirname(__file__), "..", "fixtures")


def _read(*parts: str) -> str:
    with open(os.path.join(_FIX, *parts)) as f:
        return f.read()


def test_detects_gaussian() -> None:
    assert detect_software_from_text(_read("gaussian", "sp_ub3lyp_g16.log")) == (
        "gaussian"
    )


def test_detects_orca() -> None:
    assert detect_software_from_text(
        _read("orca", "sp_dlpno_ccsdt_orca.out")
    ) == "orca"


def test_detects_molpro() -> None:
    assert detect_software_from_text(
        _read("molpro", "ch4_closed_shell", "input.out")
    ) == "molpro"


def test_unknown_returns_none() -> None:
    assert detect_software_from_text("just some text, no ESS banner") is None
    assert detect_software_from_text("") is None


def test_detection_is_extension_independent() -> None:
    # Same ORCA bytes; the (ignored) filename does not affect the result.
    orca_text = _read("orca", "sp_dlpno_ccsdt_orca.out")
    assert detect_software_from_text(orca_text) == "orca"
