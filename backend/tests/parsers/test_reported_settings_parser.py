"""The grid and SCF settings an output log prints as used, read from real fixture logs.

Each pattern is confirmed on a fixture. The excerpts quoted in the comments are the lines the pattern reads.
"""

from __future__ import annotations

import base64
import glob
import json
from functools import cache
from pathlib import Path

import pytest

from app.services.parameter_reconciliation import orca_preset_disagreements
from app.services.reported_settings_parser import parse_gaussian_reported, parse_orca_reported

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
_ARC_PAYLOAD = next((FIXTURES / "arc_runs" / "rotor_scan_8").glob("tckdb_payloads/computed_reaction/*.payload.json"))


def _read(relative: str) -> str:
    return (FIXTURES / relative).read_text(errors="replace")


def _by_key(rows):
    return {r["raw_key"]: r for r in rows}


def _canonical(rows):
    return {r["canonical_key"]: r["canonical_value"] for r in rows if r["canonical_key"]}


@cache
def _arc_logs() -> tuple[str, ...]:
    """Every output log embedded in one ARC run payload (Gaussian 09 Rev D.01 and ORCA 6.0.0)."""
    found: list[str] = []

    def walk(node):
        if isinstance(node, dict):
            if node.get("kind") == "output_log" and "content_base64" in node:
                found.append(base64.b64decode(node["content_base64"]).decode(errors="replace"))
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(json.loads(_ARC_PAYLOAD.read_text()))
    return tuple(dict.fromkeys(found))


def _arc_log(*needles: str) -> str:
    return next(text for text in _arc_logs() if all(n in text for n in needles))


# ---------------------------------------------------------------------------
# ORCA
# ---------------------------------------------------------------------------


def test_orca_610_dft_grid_and_scf_block():
    """opt_orca.out (ORCA 6.1.0, `!B3LYP DEF2-TZVP D3BJ`, `defgrid2`, `TightSCF`).

        General Integration Accuracy     IntAcc      ... 4.388
        Angular Grid (max. ang.)         AngularGrid ... 4 (Lebedev-302)
        Angular grid pruning method      GridPruning ... 4 (adaptive)
        ...
        Convergence Tolerance:
         Energy Change          TolE            ....  1.000e-08 Eh
    """
    rows = _by_key(parse_orca_reported(_read("orca/opt_orca.out")))
    assert _canonical(rows.values()) == {
        "grid.integration_accuracy": "4.388",
        "grid.angular_max": "302",
        "scf.tol_energy_eh": "1e-08",
        "scf.tol_orbital_gradient": "1e-05",
        "scf.tol_orbital_rotation": "1e-05",
        "scf.tol_diis_error": "5e-07",
    }
    assert rows["AngularGrid"]["raw_value"] == "4 (Lebedev-302)"
    assert rows["GridPruning"]["raw_value"] == "4 (adaptive)" and rows["GridPruning"]["canonical_key"] is None
    assert {r["statement_kind"] for r in rows.values()} == {"reported"}


def test_orca_610_cosx_grids_are_not_the_dft_grid():
    """The same log prints `COSX GRID GENERATION` with IntAcc 3.816 / Lebedev-50 first; neither is the DFT grid."""
    text = _read("orca/opt_orca.out")
    assert "COSX GRID GENERATION" in text and "3.816" in text
    rows = _canonical(parse_orca_reported(text))
    assert rows["grid.integration_accuracy"] == "4.388" and rows["grid.angular_max"] == "302"


def test_orca_610_optimiser_tolerances_are_not_the_scf_tolerances():
    """`Convergence Tolerances:` (plural, geometry) prints TolE 1.0000e-06 before the SCF block's 1.000e-08."""
    text = _read("orca/opt_orca.out")
    assert "Convergence Tolerances:\nEnergy Change            TolE     ....  1.0000e-06 Eh" in text
    assert _canonical(parse_orca_reported(text))["scf.tol_energy_eh"] == "1e-08"


def test_orca_600_defgrid3_log():
    """An ORCA 6.0.0 DLPNO-CCSD(T)-F12 log with `defgrid3` and `tightscf` in the echoed input."""
    text = _arc_log("Program Version 6.0.0", "IntAcc      ... 4.959")
    rows = _canonical(parse_orca_reported(text))
    assert rows["grid.integration_accuracy"] == "4.959" and rows["grid.angular_max"] == "590"
    assert rows["scf.tol_energy_eh"] == "1e-08" and rows["scf.tol_diis_error"] == "5e-07"


def test_orca_600_default_grid_log_reports_the_grid_the_input_never_names():
    """No DEFGRID keyword in the echoed input; the log still prints IntAcc 4.388 / Lebedev-302."""
    text = _arc_log("Program Version 6.0.0", "IntAcc      ... 4.388")
    assert "defgrid" not in text.split("****END OF INPUT****")[0].lower()
    assert _canonical(parse_orca_reported(text))["grid.angular_max"] == "302"


def test_orca_504_hf_log_has_no_grid_and_only_the_thresholds_it_prints():
    """sp_dlpno_ccsdt_orca.out (ORCA 5.0.4, HF reference): no DFT grid, and the SCF block has only TolE and TolErr."""
    text = _read("orca/sp_dlpno_ccsdt_orca.out")
    assert "DFT GRID GENERATION" not in text
    assert _canonical(parse_orca_reported(text)) == {"scf.tol_energy_eh": "1e-08", "scf.tol_diis_error": "5e-07"}


# ---------------------------------------------------------------------------
# Gaussian
# ---------------------------------------------------------------------------


def test_gaussian_09_grid_is_the_raw_irad_an_code_and_scf_thresholds_are_numbers():
    """freq_g09.log (G09 Rev D.01, `integral=(grid=ultrafine`):

        IXCGrd= 4 IRadAn=           5 IRanWt=          -1 IRanGd=           0 AccXCQ= 0.00D+00.
        Requested convergence on RMS density matrix=1.00D-08 within 128 cycles.
        Requested convergence on MAX density matrix=1.00D-06.
        Requested convergence on             energy=1.00D-06.
    """
    rows = _by_key(parse_gaussian_reported(_read("gaussian/freq_g09.log")))
    # The grid is an observed raw row: no canonical key, no value like "ultrafine".
    assert rows["IRadAn"]["raw_value"] == "5"
    assert rows["IRadAn"]["canonical_key"] is None and rows["IRadAn"]["canonical_value"] is None
    assert _canonical(rows.values()) == {
        "scf.tol_rms_density": "1e-08",
        "scf.tol_max_density": "1e-06",
        "scf.tol_energy_eh": "1e-06",
    }


@pytest.mark.parametrize(
    ("fixture", "expect_grid"),
    [
        ("gaussian/sp_ub3lyp_g16.log", True),  # G16 Rev C.02
        ("gaussian_composite/g3_ethylene_g03.log", False),  # G03 Rev B.05: no IXCGrd line
        ("gaussian_composite/cbs_qb3_so2oo_g03.log", False),  # G03 Rev D.01
    ],
)
def test_gaussian_other_versions_use_the_same_lines(fixture, expect_grid):
    rows = _by_key(parse_gaussian_reported(_read(fixture)))
    assert ("IRadAn" in rows) is expect_grid
    assert _canonical(rows.values())["scf.tol_rms_density"] == "1e-08"


def test_gaussian_default_grid_log_prints_a_different_code_without_a_name():
    """A G09 log whose route names no grid prints `IRadAn= 0`: stored raw, never labelled."""
    text = _arc_log("Entering Gaussian System", "IRadAn=           0 IRanWt")
    row = _by_key(parse_gaussian_reported(text))["IRadAn"]
    assert row["raw_value"] == "0" and row["canonical_value"] is None


def test_gaussian_composite_with_several_internal_steps_reports_only_what_every_step_agrees_on():
    """g4_methanol_g16.out runs several SCF steps with different thresholds: no single SCF value, so none."""
    rows = _by_key(parse_gaussian_reported(_read("gaussian_composite/g4_methanol_g16.out")))
    assert rows["IRadAn"]["raw_value"] == "5"
    assert not [r for r in rows.values() if r["canonical_key"]]


# ---------------------------------------------------------------------------
# jobs of one file
# ---------------------------------------------------------------------------


def test_jobs_that_agree_report_and_jobs_that_differ_report_nothing_for_that_setting():
    """Two real logs end to end: each report is bound to its own job, and no job lends a value to the other."""
    five, zero = _arc_log("Entering Gaussian System", "IRadAn=           5 IRanWt"), _arc_log(
        "Entering Gaussian System", "IRadAn=           0 IRanWt"
    )
    assert _by_key(parse_gaussian_reported(five + five))["IRadAn"]["raw_value"] == "5"
    both = _by_key(parse_gaussian_reported(five + zero))
    assert "IRadAn" not in both  # the jobs print different codes
    assert "scf.tol_rms_density" in _canonical(both.values())  # the SCF thresholds they share are still reported


def test_a_job_that_prints_no_value_does_not_borrow_one_from_the_other_job():
    """opt_orca.out (grid + all four tolerances) followed by the HF log (no grid, TolE and TolErr only)."""
    both = _canonical(parse_orca_reported(_read("orca/opt_orca.out") + _read("orca/sp_dlpno_ccsdt_orca.out")))
    assert both == {"scf.tol_energy_eh": "1e-08", "scf.tol_diis_error": "5e-07"}
    assert not any(k.startswith("grid.") for k in both)


# ---------------------------------------------------------------------------
# the requested-versus-reported presets, over every fixture log
# ---------------------------------------------------------------------------


def test_documented_presets_agree_with_every_orca_fixture_log():
    """Corpus check: no ORCA fixture log (ARC run payloads plus standalone logs) contradicts its own echoed preset."""
    from app.services import orca_parameter_parser as orca

    checked = 0
    texts = [t for t in _arc_logs() if "O   R   C   A" in t] + [
        Path(p).read_text(errors="replace") for p in glob.glob(str(FIXTURES / "orca" / "*.out"))
    ]
    for payload in glob.glob(str(FIXTURES / "arc_runs" / "*" / "tckdb_payloads" / "computed_reaction" / "*.payload.json")):
        texts += [t for t in _walk_logs(json.loads(Path(payload).read_text())) if "O   R   C   A" in t]
    for text in dict.fromkeys(texts):
        requested = {}
        for p in orca.parse_orca_log(text=text)["parameters"]:
            if p.get("canonical_key") in ("grid.quality", "scf.convergence"):
                requested[p["canonical_key"]] = p["canonical_value"]
        reported = _canonical(parse_orca_reported(text))
        assert orca_preset_disagreements(requested.get("grid.quality"), requested.get("scf.convergence"), reported) == []
        checked += bool(requested)
    assert checked > 50


def _walk_logs(node):
    if isinstance(node, dict):
        if node.get("kind") == "output_log" and "content_base64" in node:
            yield base64.b64decode(node["content_base64"]).decode(errors="replace")
        for value in node.values():
            yield from _walk_logs(value)
    elif isinstance(node, list):
        for value in node:
            yield from _walk_logs(value)


def test_the_presets_fire_on_a_contradiction():
    """The twin of the corpus check: DefGrid3 against Lebedev-302, and VeryTight against Tight thresholds."""
    assert orca_preset_disagreements("defgrid3", None, {"grid.angular_max": "302"}) == [
        ("grid.angular_max", "defgrid3", "302")
    ]
    assert orca_preset_disagreements("defgrid2", None, {"grid.angular_max": "302"}) == []
    assert orca_preset_disagreements(None, "very_tight", {"scf.tol_energy_eh": "1e-08"}) == [
        ("scf.tol_energy_eh", "very_tight", "1e-08")
    ]
    assert orca_preset_disagreements(None, "tight", {"scf.tol_energy_eh": "1e-08"}) == []
    assert orca_preset_disagreements("grid5", "normal", {"grid.angular_max": "302"}) == []  # no documented preset
