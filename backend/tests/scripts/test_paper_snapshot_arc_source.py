"""The ARC source of the snapshot build (``scripts/paper_snapshot/source_arc.py``): config checks and folder digests."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

SNAPSHOT_DIR = Path(__file__).resolve().parents[3] / "scripts" / "paper_snapshot"


@pytest.fixture(scope="module")
def source_arc():
    sys.path.insert(0, str(SNAPSHOT_DIR))
    try:
        import source_arc as module

        yield module
    finally:
        sys.path.remove(str(SNAPSHOT_DIR))


def _run(root: Path, name: str, *, host: str, in_upload_file: bool = True) -> Path:
    run = root / name
    (run / "output").mkdir(parents=True)
    (run / "output" / "output.yml").write_text("schema_version: '1.3'\n")
    block = f"tckdb:\n  enabled: true\n  base_url: https://{host}/api/v1\n  api_key_file: /elsewhere/key\n"
    (run / "input.yml").write_text("project: p\n" + ("" if in_upload_file else block))
    if in_upload_file:
        (run / "tckdb_upload.yml").write_text("project: p\n" + block)
    return run


def _config(tmp_path: Path, runs: list[str]) -> dict:
    adapter = tmp_path / "adapters"
    (adapter / "tckdb_arc" / "tckdb_arc").mkdir(parents=True)
    return {
        "adapter_command": ["python"],
        "adapter_path": str(adapter),
        "runs_root": str(tmp_path / "runs"),
        "recorded_hosts": ["recorded.example.org"],
        "runs": runs,
    }


def test_both_config_files_are_read_and_a_recorded_host_passes(source_arc, tmp_path):
    _run(tmp_path / "runs", "set/a", host="recorded.example.org")
    _run(tmp_path / "runs", "set/b", host="recorded.example.org", in_upload_file=False)
    assert source_arc.validate(_config(tmp_path, ["set/a", "set/b"])) == []
    assert source_arc.config_file(tmp_path / "runs" / "set/a").name == "tckdb_upload.yml"
    assert source_arc.config_file(tmp_path / "runs" / "set/b").name == "input.yml"


def test_a_run_file_naming_another_host_stops_the_build(source_arc, tmp_path):
    _run(tmp_path / "runs", "set/a", host="other.example.org")
    problems = source_arc.validate(_config(tmp_path, ["set/a"]))
    assert len(problems) == 1
    assert "names host 'other.example.org'" in problems[0]


def test_a_missing_output_and_a_repeated_run_are_problems(source_arc, tmp_path):
    run = _run(tmp_path / "runs", "set/a", host="recorded.example.org")
    (run / "output" / "output.yml").unlink()
    problems = source_arc.validate(_config(tmp_path, ["set/a", "set/a"]))
    assert any("lists a folder twice" in p for p in problems)
    assert any("output/output.yml not found" in p for p in problems)


def test_the_folder_list_digest_sees_a_written_file(source_arc, tmp_path):
    run = _run(tmp_path / "runs", "set/a", host="recorded.example.org")
    before = source_arc.list_digest(run)
    assert before == source_arc.list_digest(run)
    (run / "output" / "new.json").write_text("{}")
    assert source_arc.list_digest(run) != before


def test_the_adapter_environment_carries_the_key_and_no_build_variable(source_arc, tmp_path, monkeypatch):
    monkeypatch.setenv("TCKDB_API_KEY", "from-the-shell")
    env = source_arc._environment(_config(tmp_path, []), "instance-key")
    assert env["TCKDB_SNAPSHOT_API_KEY"] == "instance-key"
    assert "TCKDB_API_KEY" not in env
    assert str(Path(sys.executable).parent) not in env["PATH"].split(os.pathsep)
