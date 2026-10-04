"""The bundle-export CLI reports what a kinetics export left out, as the thermo path does.

A portable bundle cannot carry everything a kinetics record states (a resolved-channel determination, a
protocol's supporting calculations). The exporter reports each as a ``declaration_pruned`` omission through
a sink; the CLI used to drop the sink on the kinetics path, so the loss was silent. These tests drive the CLI's
own ``_export`` and ``main`` with the database and the exporter stubbed, so they test the wiring and nothing else.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.schemas.workflows.contribution_bundle import ContributionBundleV0
from app.services.contribution_bundle_export import BundleExportOmission
from tests.api.test_api_bundle_dry_run_submit_parity import _example

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "export_contribution_bundle.py"
OMISSION = BundleExportOmission(
    action="declaration_pruned", ref="kin_" + "a" * 26, detail="Its determination names a transition state."
)


@pytest.fixture
def cli():
    spec = importlib.util.spec_from_file_location("export_contribution_bundle_under_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _args(**changes):
    base = {
        "kind": "kinetics",
        "kinetics_id": [1],
        "thermo_id": [],
        "title": "t",
        "summary": "s",
        "exporter_label": "tester",
        "instance_name": "local",
        "instance_kind": "local",
        "orcid": None,
        "affiliation": None,
        "email": None,
        "exporter_notes": None,
    }
    base.update(changes)
    return SimpleNamespace(**base)


def test_the_kinetics_export_passes_the_sink_to_the_exporter(cli, monkeypatch):
    seen = {}

    def fake_export(session, **kwargs):
        seen.update(kwargs)
        kwargs["omissions"].append(OMISSION)
        return "bundle"

    monkeypatch.setattr(cli, "deposit_rights_for_records", lambda *a, **k: None)
    monkeypatch.setattr(cli, "export_kinetics_bundle", fake_export)
    sink: list = []
    assert cli._export(None, _args(), sink) == "bundle"
    assert seen["omissions"] is sink and sink == [OMISSION]


def test_main_prints_each_kinetics_omission_and_still_writes_the_bundle(cli, monkeypatch, tmp_path, capsys):
    bundle = ContributionBundleV0.model_validate(_example("kinetics-bundle-v0.json"))

    def fake_export(session, args, kinetics_omissions):
        kinetics_omissions.append(OMISSION)
        return bundle

    class _Session:
        def __init__(self, engine):
            pass

        def __enter__(self):
            return None

        def __exit__(self, *exc):
            return False

    engine = SimpleNamespace(dispose=lambda: None)
    monkeypatch.setattr(cli, "create_engine", lambda url: engine)
    monkeypatch.setattr(cli, "Session", _Session)
    monkeypatch.setattr(cli, "_export", fake_export)
    output = tmp_path / "bundle.tckdb.json"

    code = cli.main(
        ["--kind", "kinetics", "--kinetics-id", "1", "--output", str(output), "--title", "t", "--summary", "s"]
    )

    assert code == 0
    err = capsys.readouterr().err
    assert f"note: declaration_pruned {OMISSION.ref}: {OMISSION.detail}" in err
    assert json.loads(output.read_text(encoding="utf-8"))["bundle_kind"] == "kinetics"


def test_main_prints_nothing_extra_when_nothing_was_left_out(cli, monkeypatch, tmp_path, capsys):
    bundle = ContributionBundleV0.model_validate(_example("kinetics-bundle-v0.json"))
    monkeypatch.setattr(cli, "create_engine", lambda url: SimpleNamespace(dispose=lambda: None))

    class _Ctx:
        def __enter__(self):
            return None

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(cli, "Session", lambda engine: _Ctx())
    monkeypatch.setattr(cli, "_export", lambda session, args, omissions: bundle)
    output = tmp_path / "bundle.tckdb.json"
    assert cli.main(["--kind", "kinetics", "--kinetics-id", "1", "--output", str(output), "--title", "t", "--summary", "s"]) == 0
    assert "note:" not in capsys.readouterr().err
