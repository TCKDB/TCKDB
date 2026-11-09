"""Inline artifacts: local refusals, stateless artifact_plan, budget priority, batch splitting."""

from __future__ import annotations

import copy

import httpx
import json
import pytest

from tckdb_client.builders import (
    Calculation,
    ComputedSpeciesUpload,
    Geometry,
    LevelOfTheory,
    PlannedArtifactUpload,
    SoftwareRelease,
    Species,
    TCKDBBuilderValidationError,
)
from tckdb_client.builders import artifact as artifact_mod

from conftest import make_client  # type: ignore[import-not-found]

WATER = "3\nwater\nO 0.0 0.0 0.117\nH 0.0 0.757 -0.469\nH 0.0 -0.757 -0.469"


def _opt(label="opt"):
    return Calculation.opt(
        SoftwareRelease(software="Gaussian", version="16"),
        LevelOfTheory(method="b3lyp", basis="def2svp"),
        output_geometry=Geometry.from_xyz(WATER),
        final_energy_hartree=-76.3,
        converged=True,
        label=label,
    )


def _species_upload(calcs, **kw):
    return ComputedSpeciesUpload(
        species=Species(smiles="O", charge=0, multiplicity=1, label="water"),
        calculations=calcs,
        primary_calculation=calcs[0],
        **kw,
    )


def _upload_with(path, kind="output_log", **art_kw):
    opt = _opt()
    opt.add_artifact(path, kind=kind, **art_kw)
    return _species_upload([opt])


def _calc_artifacts(payload):
    return payload["conformers"][0]["primary_calculation"].get("artifacts")


@pytest.fixture
def log(tmp_path):
    p = tmp_path / "opt.log"
    p.write_bytes(b"Entering Gaussian System\nbody\n")
    return p


# --- refused locally, before any request ------------------------------


def test_a_zero_byte_file_is_refused_naming_it(tmp_path):
    empty = tmp_path / "empty.log"
    empty.write_bytes(b"")
    with pytest.raises(TCKDBBuilderValidationError, match="empty.log"):
        _upload_with(empty).to_payload()


def test_a_kind_and_extension_the_schema_disallows_is_refused_naming_the_file(tmp_path):
    bad = tmp_path / "notes.txt"
    bad.write_bytes(b"hello")
    with pytest.raises(TCKDBBuilderValidationError, match="notes.txt"):
        _upload_with(bad, kind="output_log").to_payload()


def test_a_declared_size_or_hash_that_does_not_match_the_bytes_is_refused(log):
    with pytest.raises(TCKDBBuilderValidationError, match="bytes"):
        _upload_with(log, bytes=log.stat().st_size + 1).to_payload()
    with pytest.raises(TCKDBBuilderValidationError, match="sha256"):
        _upload_with(log, sha256="0" * 64).to_payload()


def test_inline_artifacts_false_does_not_check_files_at_build_time(tmp_path):
    empty = tmp_path / "empty.log"
    empty.write_bytes(b"")
    upload = _upload_with(empty)
    upload.inline_artifacts = False
    upload.to_payload()  # the old split behaviour: the server judges the file later


# --- artifact_plan does not depend on to_payload() state ---------------


def _response(*keys):
    return {
        "conformers": [
            {
                "key": "conformer-1",
                "primary_calculation": {"key": keys[0], "calculation_id": 1},
                "additional_calculations": [{"key": k, "calculation_id": i + 2} for i, k in enumerate(keys[1:])],
            }
        ]
    }


def test_artifact_plan_is_the_same_for_a_copy_a_rebuilt_builder_and_the_posted_payload(tmp_path, monkeypatch):
    monkeypatch.setattr(artifact_mod, "INLINE_TOTAL_BUDGET_BYTES", 105)  # the log fits, the checkpoint does not
    big = tmp_path / "big.log"
    big.write_bytes(b"x" * 100)
    chk = tmp_path / "step.chk"
    chk.write_bytes(b"y" * 10)

    def build():
        a, b = _opt("a"), _opt("b")
        a.add_artifact(big, kind="output_log")
        b.add_artifact(chk, kind="checkpoint")
        return _species_upload([a, b])

    def view(plan):
        return [(p.calculation_key, p.path) for p in plan]

    upload = build()
    payload = upload.to_payload()
    response = _response("a", "b")
    expected = [("b", chk)]
    assert view(upload.artifact_plan(response)) == expected
    assert view(copy.deepcopy(upload).artifact_plan(response)) == expected
    assert view(build().artifact_plan(response)) == expected
    assert view(build().artifact_plan(response, payload)) == expected
    # Nothing posted, nothing skipped.
    assert len(build().artifact_plan(response, {})) == 2


# --- budget priority ----------------------------------------------------


def test_logs_and_inputs_go_inline_before_checkpoints_and_ancillary(tmp_path, monkeypatch):
    monkeypatch.setattr(artifact_mod, "INLINE_TOTAL_BUDGET_BYTES", 120)
    chk, note, log_, deck = (tmp_path / n for n in ("a.chk", "b.txt", "c.log", "d.in"))
    chk.write_bytes(b"c" * 60)
    note.write_bytes(b"n" * 60)
    log_.write_bytes(b"l" * 70)
    deck.write_bytes(b"d" * 40)
    opt = _opt()
    # Walk order puts the low-priority files first.
    opt.add_artifact(chk, kind="checkpoint")
    opt.add_artifact(note, kind="ancillary")
    opt.add_artifact(log_, kind="output_log")
    opt.add_artifact(deck, kind="input")
    arts = _calc_artifacts(_species_upload([opt]).to_payload())
    assert sorted(a["filename"] for a in arts) == ["c.log", "d.in"]


# --- batch mode splits one calculation's files by size -------------------


def test_batch_mode_splits_a_calculation_by_size_and_numbers_later_keys(tmp_path, monkeypatch):
    monkeypatch.setattr(artifact_mod, "INLINE_TOTAL_BUDGET_BYTES", 45)  # two ~20-byte files per request
    paths = {}
    for name in ("opt.log", "freq.log", "sp.log", "scan.log"):
        paths[name] = tmp_path / name
        paths[name].write_bytes(f"contents of {name}\n".encode())

    captured: list = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode())
        captured.append((request.headers.get("idempotency-key", ""), [a["filename"] for a in body["artifacts"]]))
        return httpx.Response(201, json={"artifacts": [], "warnings": []})

    client, _ = make_client(handler)

    def item(key, calc_id, name):
        return PlannedArtifactUpload(
            calculation_key=key,
            calculation_id=calc_id,
            path=paths[name],
            kind="output_log",
            label=None,
            sha256=None,
            bytes=None,
            calculation_ref=f"calc_c{calc_id}",
        )

    plan = [item("a", 10, "opt.log"), item("b", 10, "freq.log"), item("c", 10, "sp.log"), item("d", 20, "scan.log")]
    results = client.upload_artifacts(plan, batch_by_calculation=True, idempotency_key_prefix="run")
    assert captured == [
        ("run:a:artifact-batch", ["opt.log", "freq.log"]),
        ("run:c:artifact-batch-2", ["sp.log"]),
        ("run:d:artifact-batch", ["scan.log"]),
    ]
    assert len(results) == 3
