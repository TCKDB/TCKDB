"""Tests for content-based ESS software detection.

The program is identified from its banner in the log content, so detection
is independent of the filename/extension. Exercised against real fixtures.
"""

from __future__ import annotations

import os

import pytest

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


@pytest.mark.parametrize(
    "fixture",
    [
        "sp_mrcc_triplet.dat",
        "sp_nh2_doublet.dat",
        "opt_freq_singlet.out",
        "opt_freq_dft_ts_singlet.out",
        # A development build: its banner reads ``Psi4 1.4a1.dev75`` with no
        # ``release`` suffix, so a version-anchored marker would miss it.
        "io_error_truncated.out",
    ],
)
def test_detects_psi4(fixture: str) -> None:
    assert detect_software_from_text(_read("psi4", fixture)) == "psi4"


def test_psi4_passes_the_upload_signature_gate_it_is_detected_by() -> None:
    """The artifact gate already admitted Psi4 logs; now they are named too.

    ``OUTPUT_LOG_SIGNATURES`` accepts an output log if ``b"Psi4"`` appears
    in the first 4 KB, while detection reads the first 8000 characters. The
    two windows differ, so a log could in principle be storable yet
    unidentifiable; pin that it is not.
    """
    from app.services.artifact_storage import (
        _SIGNATURE_WINDOW,
        OUTPUT_LOG_SIGNATURES,
    )

    for fixture in (
        "sp_mrcc_triplet.dat",
        "sp_nh2_doublet.dat",
        "opt_freq_singlet.out",
        "opt_freq_dft_ts_singlet.out",
        "io_error_truncated.out",
    ):
        raw = _read("psi4", fixture).encode()
        assert OUTPUT_LOG_SIGNATURES["psi4"] in raw[:_SIGNATURE_WINDOW], fixture
        assert detect_software_from_text(raw.decode()) == "psi4", fixture


def test_psi4_marker_does_not_claim_other_programs() -> None:
    """Adding Psi4 must not reclassify any previously-supported log.

    ``detect_software_from_text`` is shared with the single-point-energy
    path, whose contract is that the two never disagree on the same bytes,
    so a marker that over-matched would silently reroute real Gaussian,
    ORCA or Molpro logs to the wrong parser.
    """
    assert detect_software_from_text(
        _read("gaussian", "sp_ub3lyp_g16.log")
    ) == "gaussian"
    assert detect_software_from_text(
        _read("orca", "sp_dlpno_ccsdt_orca.out")
    ) == "orca"
    assert detect_software_from_text(
        _read("orca", "sp_orca_minimal.txt")
    ) == "orca"
    assert detect_software_from_text(
        _read("molpro", "ch4_closed_shell", "input.out")
    ) == "molpro"
    assert detect_software_from_text(
        _read("molpro", "ch3_radical", "input.out")
    ) == "molpro"


def test_psi4_is_checked_before_the_generic_orca_fallback() -> None:
    """ORCA is matched partly by a generic ``Program Version X.Y.Z`` line.

    A Psi4 log that happened to contain that string must still be called
    Psi4, because Psi4's own marker is anchored on the program name.
    """
    log = (
        "    Psi4: An Open-Source Ab Initio Electronic Structure Package\n"
        "    Program Version 1.9.1\n"
    )
    assert detect_software_from_text(log) == "psi4"


def test_unknown_returns_none() -> None:
    assert detect_software_from_text("just some text, no ESS banner") is None
    assert detect_software_from_text("") is None


def test_detection_is_extension_independent() -> None:
    # Same ORCA bytes; the (ignored) filename does not affect the result.
    orca_text = _read("orca", "sp_dlpno_ccsdt_orca.out")
    assert detect_software_from_text(orca_text) == "orca"
    # A Psi4 single point named ``.dat`` and one named ``.out`` classify
    # identically; only the banner matters.
    assert detect_software_from_text(_read("psi4", "sp_mrcc_triplet.dat")) == "psi4"
    assert detect_software_from_text(_read("psi4", "opt_freq_singlet.out")) == "psi4"


class TestOrcaMarkerIsAnchoredOnTheName:
    """Every ORCA alternative must name ORCA; none may be a generic phrase.

    ``Program Version X.Y.Z`` was previously an accepted ORCA marker. It is not
    an ORCA fingerprint -- many programs print it -- and because ORCA is the
    last branch, any non-Gaussian, non-Molpro log containing that phrase in its
    first 8 kB was dispatched to the ORCA parser, which would then read charge,
    multiplicity and energies out of a foreign format.

    Misattribution is worse than silence: ``None`` makes the caller record
    nothing, while the wrong program yields a confident wrong answer.
    """

    @pytest.mark.parametrize(
        "text",
        [
            "                 * O   R   C   A *",
            # Version-independent by construction: the marker is the name, not
            # the number, so any release matches and future ones will too.
            "Program Version 4.2.1 -  RELEASE  -\nORCA",
            "Program Version 5.0.3 -  RELEASE  -\nORCA",
            "Program Version 6.1.0 -  RELEASE  -\nORCA",
            "ORCA 4.0.1",
            "ORCA 5.0.4",
            "ORCA 6.0.0",
            "****ORCA TERMINATED NORMALLY****",
        ],
    )
    def test_genuine_orca_logs_are_still_detected(self, text: str) -> None:
        assert detect_software_from_text(text) == "orca"

    @pytest.mark.parametrize(
        "text",
        [
            "Some Code\nProgram Version 1.2.3\nnothing else",
            "Northwest Computational Chemistry\nProgram Version 7.0.2",
        ],
    )
    def test_a_bare_version_line_is_not_orca(self, text: str) -> None:
        assert detect_software_from_text(text) is None

    def test_a_psi4_banner_is_psi4_not_orca(self) -> None:
        """Psi4 is now recognised in its own right.

        Before Psi4 support this asserted ``None`` -- the point being only that
        a foreign log must not be claimed by ORCA. That intent is preserved and
        strengthened: it is now positively identified rather than merely not
        misattributed.
        """
        text = "Psi4: An Open-Source Ab Initio Electronic Structure Package\nProgram Version 1.9.1"
        assert detect_software_from_text(text) == "psi4"
