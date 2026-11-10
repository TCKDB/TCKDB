"""The grid and SCF settings an output log prints as used, read from real fixture logs.

Each pattern is confirmed on a fixture. The excerpts quoted in the comments are the lines the pattern reads.
"""

from __future__ import annotations

import base64
import glob
import json
import re
from functools import cache
from pathlib import Path

import pytest

from app.services.parameter_reconciliation import orca_preset_disagreements
from app.services.reported_settings_parser import _G_SCF, gaussian_jobs, parse_gaussian_reported, parse_orca_reported

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
        # The `!OPT FREQ` job's frequency listing follows an `SCF HESSIAN` section (see test_reported_hessian_method).
        "freq.hessian_method": "analytic",
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


def test_a_gaussian_log_holding_two_jobs_reports_nothing():
    """Synthetic: two real single-job logs end to end (a log of a `--Link1--` file, which no fixture is)."""
    five = _arc_log("Entering Gaussian System", "IRadAn=           5 IRanWt")
    zero = _arc_log("Entering Gaussian System", "IRadAn=           0 IRanWt")
    assert parse_gaussian_reported(five)  # the single job reports...
    assert parse_gaussian_reported(five + zero) == []  # ...two of them bind to no one calculation
    assert parse_gaussian_reported(five + five) == []


# A Gaussian 16 water run made for these tests: three linked (`--Link1--`) jobs in one file, and a second file with
# one default job. The jobs differ in route, grid and SCF keywords.
_LINK1 = "gaussian/water_link1_g16.log"
_DEFAULT = "gaussian/water_default_g16.log"


def test_the_link1_log_splits_at_entering_link_1_and_each_job_keeps_its_own_route():
    from app.services.gaussian_parameter_parser import all_route_texts

    text = _read(_LINK1)
    assert text.count("Entering Gaussian System") == 1  # no second banner: the old split saw one job
    jobs = gaussian_jobs(text)
    assert len(jobs) == 3
    assert [all_route_texts(j) for j in jobs] == [
        ["# opt b3lyp/def2svp int=ultrafine scf=tight"],
        ["# sp wb97xd/def2svp geom=allcheck guess=read int=finegrid scf=verytight"],
        ["# sp wb97xd/def2svp geom=allcheck guess=read"],
    ]


def test_every_link1_job_reports_its_own_scf_thresholds_and_the_whole_file_reports_nothing():
    text = _read(_LINK1)
    assert parse_gaussian_reported(text) == []  # three jobs bind to no one calculation
    for job in gaussian_jobs(text):
        assert _canonical(parse_gaussian_reported(job)) == {
            "scf.tol_rms_density": "1e-08",
            "scf.tol_max_density": "1e-06",
            "scf.tol_energy_eh": "1e-06",
        }


@pytest.mark.parametrize("fixture", [_LINK1, _DEFAULT])
def test_the_harris_guess_irad_an_is_not_read_as_the_scf_grid(fixture):
    """IRadAn prints only on the Harris initial-guess lines of these G16 logs (no `IXCGrd=` line): no grid row.

    Measured on the fixture corpus: where a log has both, the Harris lines' IRadAn differs from the SCF grid line's in
    58 logs (IXCGrd 0 against Harris 1 or 4; IXCGrd 5 against Harris 4), so the guess step's code is not the grid.
    """
    text = _read(fixture)
    assert re.search(r"Harris functional with IExCor=\s*\d+ and IRadAn=\s*5", text) and "IXCGrd=" not in text
    for job in gaussian_jobs(text):
        assert "IRadAn" not in _by_key(parse_gaussian_reported(job))


def test_a_single_default_job_reports_the_scf_default_and_no_grid():
    """`# sp b3lyp/def2svp`: no int=, scf= or guess=read. G16 reports 1.00D-08, the same as an explicit scf=tight."""
    assert gaussian_jobs(_read(_DEFAULT)) == [_read(_DEFAULT)]
    rows = parse_gaussian_reported(_read(_DEFAULT))
    assert _canonical(rows)["scf.tol_rms_density"] == "1e-08" and "IRadAn" not in _by_key(rows)


def test_a_route_wrapped_inside_a_keyword_is_rejoined_in_the_echo_parse():
    """Job 2's echo wraps at 70 characters inside `verytight` (`scf=verytigh` / `t`)."""
    from app.services.calculation_parameter_extraction import _parse_gaussian_text

    job = gaussian_jobs(_read(_LINK1))[1]
    assert "scf=verytigh\n t\n" in job  # the wrap is really in the log
    parsed = _parse_gaussian_text(job)
    assert parsed["route_line"].endswith("int=finegrid scf=verytight")
    rows = {(p["raw_key"], p["canonical_key"], p["canonical_value"]) for p in parsed["parameters"]}
    assert ("verytight", "scf.convergence", "very_tight") in rows
    assert ("finegrid", "grid.quality", "fine") in rows


def test_a_short_route_is_closed_by_its_own_short_row_of_dashes():
    """Gaussian sizes the dashes round a route to the route: job 1's are 43 wide. The echo must stop there."""
    from app.services.calculation_parameter_extraction import _parse_gaussian_text

    for job, expected in zip(
        gaussian_jobs(_read(_LINK1)),
        ["# opt b3lyp/def2svp int=ultrafine scf=tight", None, "# sp wb97xd/def2svp geom=allcheck guess=read"],
        strict=True,
    ):
        parsed = _parse_gaussian_text(job)
        if expected:
            assert parsed["route_line"] == expected
            assert {p["raw_key"] for p in parsed["parameters"]} & {"NAtoms", "Charge", "Conv"} == set()  # no log text


def test_an_extra_route_section_the_program_did_not_announce_suppresses_the_report():
    """Synthetic: a second echoed route spliced into a real single-job log, with no `Proceeding to internal job step`."""
    text = _read("gaussian/freq_g09.log")
    route = " ----------------------------------------------------------------------\n #P b3lyp/6-31g(d) sp\n ----------------------------------------------------------------------\n"
    spliced = text.replace(" Leave Link  101", route + " Leave Link  101", 1)
    assert spliced != text and parse_gaussian_reported(text)
    assert parse_gaussian_reported(spliced) == []


@pytest.mark.parametrize(
    "fixture",
    ["gaussian/opt_g09.log", "gaussian_composite/g4_methanol_g16.out", "gaussian_composite/cbs_qb3_ts_c2h5no2_g16.out"],
)
def test_real_multi_step_logs_are_not_suppressed(fixture):
    """Opt-then-freq and composite logs announce their internal steps, so the guard leaves them alone."""
    assert parse_gaussian_reported(_read(fixture))


def _gaussian_corpus() -> list[tuple[str, str]]:
    """``(source, text)`` of every Gaussian log: standalone, composite, and every ARC run payload (species and reaction)."""
    logs: dict[str, str] = {}
    for payload in glob.glob(str(FIXTURES / "arc_runs" / "*" / "tckdb_payloads*" / "**" / "*.payload.json"), recursive=True):
        for text in _walk_logs(json.loads(Path(payload).read_text())):
            if "Entering Gaussian System" in text:
                logs.setdefault(text, "arc_payload")
    for path in glob.glob(str(FIXTURES / "gaussian" / "*.log")) + glob.glob(str(FIXTURES / "gaussian_composite" / "*.[lo]*")):
        if not path.endswith(".md"):
            logs[Path(path).read_text(errors="replace")] = str(Path(path).relative_to(FIXTURES))
    return [(source, text) for text, source in logs.items()]


def test_the_split_at_entering_link_1_loses_no_fixture_logs_reports():
    """Corpus: the split is safe (one `Entering Link 1` per log, opt+freq and composites included) and every job keeps
    the reports it printed."""
    logs = _gaussian_corpus()
    assert len(logs) > 100
    with_reports = 0
    for source, log in logs:
        count = len(re.findall(r"^[ \t]*Entering Link 1 =", log, re.M))
        assert count == (3 if source == _LINK1 else 1), source
        assert len(gaussian_jobs(log)) == count
    for text in [job for _, log in logs for job in gaussian_jobs(log)]:
        codes = set(re.findall(r"IXCGrd=\s*\d+ IRadAn=\s*(\d+)", text))
        scf = set(_G_SCF.findall(text))
        if (codes or scf) and len(codes) <= 1 and len(scf) <= 1:
            # One value per setting throughout: only the job split could empty the result.
            with_reports += 1
            assert parse_gaussian_reported(text), "a fixture log's reports were suppressed"
    assert with_reports > 100


def test_requested_against_reported_scf_disagreements_over_every_gaussian_fixture_job():
    """Corpus count: the one contradiction is job 2 of the Link1 fixture (`scf=verytight`, reports 1.00D-08).

    Every other job either requests `tight` (125) or nothing about SCF convergence (49), and no log states Conver=N.
    """
    from app.services.calculation_parameter_extraction import _parse_gaussian_text
    from app.services.parameter_reconciliation import gaussian_scf_disagreements

    hits = []
    for source, log in _gaussian_corpus():
        for job in gaussian_jobs(log):
            requested = conver = None
            for p in _parse_gaussian_text(job)["parameters"]:
                if p["canonical_key"] == "scf.convergence":
                    requested = p["canonical_value"]
                if p["section"] == "scf" and p["raw_key"].lower() == "conver":
                    conver = p["raw_value"]
            reported = _canonical(parse_gaussian_reported(job)).get("scf.tol_rms_density")
            hits += [(source, d) for d in gaussian_scf_disagreements(requested, conver, reported)]
    assert hits == [(_LINK1, ("scf.tol_rms_density", "very_tight", "1e-08"))]


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
