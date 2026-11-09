"""xtb output files are recognised as an ``output_log``: recognised, never parsed.

Fixture: ``tests/fixtures/xtb/opt_freq_gfn2_ethanol.out`` is a trimmed real xtb 6.7.1 GFN2-xTB ``--ohess vtight`` run
(ethanol; banner, setup block and final thermodynamics kept; hostname and install path redacted).
"""

from __future__ import annotations

from pathlib import Path

from app.db.models.common import ArtifactKind
from app.services.charge_multiplicity_reconciliation import parse_charge_multiplicity_from_log
from app.services.ess_software_detection import detect_software_from_text
from app.services.input_geometry_extraction import extract_input_geometry
from app.services.software_banner_extraction import observe_software_banner
from app.services.sp_energy_reconciliation import parse_sp_energy_from_log

TEXT = (Path(__file__).resolve().parents[1] / "fixtures" / "xtb" / "opt_freq_gfn2_ethanol.out").read_text()


def test_detected_as_xtb() -> None:
    assert detect_software_from_text(TEXT) == "xtb"


def test_banner_names_xtb_and_its_version() -> None:
    parsed, program = observe_software_banner(TEXT)
    assert program == "xtb"
    assert parsed is not None and parsed["version"] == "6.7.1" and parsed["build"] is None


def test_no_values_are_extracted() -> None:
    assert parse_sp_energy_from_log(TEXT) is None
    assert parse_charge_multiplicity_from_log(TEXT) is None
    assert extract_input_geometry(
        software="xtb", artifact_kind=ArtifactKind.output_log, text=TEXT
    ).action.value == "not_determinable"


def test_other_programs_unaffected() -> None:
    assert detect_software_from_text("Program Version 6.1.0 - RELEASE -\n * O   R   C   A *") == "orca"
