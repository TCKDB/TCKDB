"""The E2 source of the snapshot build (``scripts/paper_snapshot/source_e2.py``): declarations and job files."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from tckdb_schemas.structure_declarations import ActualProtocolDeclaration

SNAPSHOT_DIR = Path(__file__).resolve().parents[3] / "scripts" / "paper_snapshot"


@pytest.fixture(scope="module")
def source_e2():
    sys.path.insert(0, str(SNAPSHOT_DIR))
    try:
        import source_e2 as module

        yield module
    finally:
        sys.path.remove(str(SNAPSHOT_DIR))


def _task(source_e2, tmp_path: Path, level: str, route: str | None):
    directory = tmp_path / level / "ethanol" / "c00"
    directory.mkdir(parents=True)
    if route is not None:
        (directory / "job.gjf").write_text(f"%nprocshared=8\n%mem=7GB\n{route}\n\ntitle\n\n0 1\nO 0 0 0\n\n")
    return source_e2.Task(
        task=f"{level}/ethanol/c00", level=level, molecule="ethanol", start="c00", smiles="CCO", charge=0,
        multiplicity=1, elements=["O"], directory=directory, base_directory=directory,
    )


def test_route_line_declares_grid_scf_threshold_and_dispersion_with_their_line(source_e2, tmp_path):
    route = "#P B3LYP/def2TZVP EmpiricalDispersion=GD3BJ Opt=Tight Freq Int=UltraFine SCF=Tight NoSymm"
    declaration, sources = source_e2.declare_protocol(_task(source_e2, tmp_path, "b3lyp_d3bj", route))

    parsed = ActualProtocolDeclaration.model_validate(declaration)
    assert [(a.kind.value, a.setting) for a in parsed.numerical_approximations] == [
        ("integration_grid", "ultrafine"),
        ("scf_convergence_threshold", "tight"),
    ]
    assert parsed.dispersion.value == "d3bj"
    assert parsed.spin_treatment is None and parsed.solvation is None and parsed.constraints is None
    assert {s["line"] for s in sources} == {"job.gjf:3"}
    assert {s["token"] for s in sources} == {"EmpiricalDispersion=GD3BJ", "Int=UltraFine", "SCF=Tight"}


def test_a_route_line_without_a_dispersion_keyword_declares_no_dispersion(source_e2, tmp_path):
    route = "#P wB97XD/def2TZVP Opt=Tight Freq Int=UltraFine SCF=Tight NoSymm"
    declaration, _ = source_e2.declare_protocol(_task(source_e2, tmp_path, "wb97xd", route))
    assert "dispersion" not in declaration
    assert len(declaration["numerical_approximations"]) == 2


def test_a_compound_option_is_not_read(source_e2, tmp_path):
    route = "#P B3LYP/def2TZVP Opt Freq SCF=(Tight,XQC)"
    declaration, sources = source_e2.declare_protocol(_task(source_e2, tmp_path, "b3lyp_d3bj", route))
    assert declaration is None and sources == []


def test_an_xtb_task_states_no_declaration_and_sends_xtb_out_and_g98_out(source_e2, tmp_path):
    task = _task(source_e2, tmp_path, "gfn2", None)
    assert source_e2.declare_protocol(task) == (None, [])
    assert source_e2.job_files(task) == (
        ("xtb.out", "output_log", ("opt", "freq")),
        ("g98.out", "output_log", ("freq",)),
    )


def test_gfn2_is_an_available_level(source_e2, tmp_path):
    (tmp_path / "raw_outputs" / "tasks").mkdir(parents=True)
    for name in ("raw_outputs/manifest_union.json", "raw_outputs/tasks.txt"):
        (tmp_path / name).write_text("[]" if name.endswith(".json") else "")
    assert source_e2.validate({"root": str(tmp_path), "levels": ["b3lyp_d3bj", "wb97xd", "gfn2"]}) == []
