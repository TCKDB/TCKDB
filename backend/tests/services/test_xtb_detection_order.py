"""Detection order around xtb: g98.out, ORCA look-alikes, charge/multiplicity, and the version regex.

Fixtures are trimmed real xtb 6.7.1 outputs (see ``tests/fixtures/xtb/README.txt``).
"""

from __future__ import annotations

from pathlib import Path

from app.db.models.common import ArtifactKind
from app.services.charge_multiplicity_reconciliation import parse_charge_multiplicity_from_log
from app.services.ess_software_detection import detect_software_from_text
from app.services.input_geometry_extraction import extract_input_geometry
from app.services.software_banner_extraction import observe_software_banner
from app.services.xtb_banner import parse_software_version

_FIX = Path(__file__).resolve().parents[1] / "fixtures" / "xtb"
TEXT = (_FIX / "opt_freq_gfn2_ethanol.out").read_text()
G98 = (_FIX / "g98_gfn2_ethanol.out").read_text()


def test_g98_out_is_xtb_not_gaussian_and_yields_no_geometry() -> None:
    # Opens with Gaussian's own header, then says the xtb code wrote it.
    assert "Entering Gaussian System" in G98
    assert detect_software_from_text(G98) == "xtb"
    parsed, _program = observe_software_banner(G98)
    assert parsed is None  # g98.out has no xtb version banner: nothing to compare
    result = extract_input_geometry(software="xtb", artifact_kind=ArtifactKind.output_log, text=G98)
    assert result.action.value == "not_determinable"


def test_a_real_gaussian_log_is_still_gaussian() -> None:
    assert detect_software_from_text("Entering Gaussian System, Link 0=g16\n Gaussian 16:\n") == "gaussian"


def test_xtb_beats_a_loose_orca_word_in_its_program_path() -> None:
    text = TEXT.replace("program call               : xtb", "program call               : /opt/orca/bin/xtb")
    assert "/orca/" in text
    assert detect_software_from_text(text) == "xtb"


def test_strict_orca_banner_beats_a_later_xtb_banner() -> None:
    assert detect_software_from_text("  * O   R   C   A *\n" + TEXT) == "orca"


def test_charge_multiplicity_is_not_read_from_xtb() -> None:
    text = TEXT + "\n Charge = 0 Multiplicity = 1\n"
    assert detect_software_from_text(text) == "xtb"
    assert parse_charge_multiplicity_from_log(text) is None


def test_version_must_end_at_whitespace_or_a_parenthesis() -> None:
    assert parse_software_version("   * xtb version 6.7.1 (edcfbbe) compiled") == {
        "name": "xtb",
        "version": "6.7.1",
        "build": None,
        "release_date_raw": None,
    }
    assert parse_software_version("   * xtb version 6.7.1pre (edcfbbe)") is None
    assert parse_software_version("   * xtb version 6.7.1") is None  # no terminator: not a banner line
