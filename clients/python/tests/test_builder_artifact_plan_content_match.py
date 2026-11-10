"""artifact_plan(result, payload) tells same-name files apart by content."""

from __future__ import annotations

import pytest

from tckdb_client.builders import (
    Calculation,
    ComputedSpeciesUpload,
    Geometry,
    LevelOfTheory,
    SoftwareRelease,
    Species,
)
from tckdb_client.builders import artifact as artifact_mod

WATER = "3\nwater\nO 0.0 0.0 0.117\nH 0.0 0.757 -0.469\nH 0.0 -0.757 -0.469"
RESPONSE = {
    "conformers": [
        {"key": "conformer-1", "primary_calculation": {"key": "opt", "calculation_id": 1}, "additional_calculations": []}
    ]
}


def _upload(*paths):
    opt = Calculation.opt(
        SoftwareRelease(software="Gaussian", version="16"),
        LevelOfTheory(method="b3lyp", basis="def2svp"),
        output_geometry=Geometry.from_xyz(WATER),
        final_energy_hartree=-76.3,
        converged=True,
        label="opt",
    )
    for p in paths:
        opt.add_artifact(p, kind="output_log")
    return ComputedSpeciesUpload(
        species=Species(smiles="O", charge=0, multiplicity=1, label="water"),
        calculations=[opt],
        primary_calculation=opt,
    )


def test_two_same_name_files_one_deferred_the_deferred_one_is_planned(tmp_path, monkeypatch):
    monkeypatch.setattr(artifact_mod, "INLINE_TOTAL_BUDGET_BYTES", 150)  # one 100-byte file fits, not two
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    first, second = tmp_path / "a" / "opt.log", tmp_path / "b" / "opt.log"
    first.write_bytes(b"a" * 100)
    second.write_bytes(b"b" * 100)

    upload = _upload(first, second)
    payload = upload.to_payload()
    posted = payload["conformers"][0]["primary_calculation"]["artifacts"]
    assert len(posted) == 1

    plan = upload.artifact_plan(RESPONSE, payload)
    assert [p.path for p in plan] == [second]
    # Recomputed from disk it agrees while the files are unchanged.
    assert [p.path for p in upload.artifact_plan(RESPONSE)] == [second]


def test_a_file_edited_after_posting_is_planned_when_the_payload_is_given(tmp_path):
    log = tmp_path / "opt.log"
    log.write_bytes(b"before")
    upload = _upload(log)
    payload = upload.to_payload()
    log.write_bytes(b"after: a different file now")
    assert [p.path for p in upload.artifact_plan(RESPONSE, payload)] == [log]


@pytest.mark.parametrize("payload", [None, {}])
def test_without_a_match_nothing_is_skipped_when_inline_is_off(tmp_path, payload):
    log = tmp_path / "opt.log"
    log.write_bytes(b"x")
    upload = _upload(log)
    upload.inline_artifacts = False
    assert len(upload.artifact_plan(RESPONSE, payload)) == 1
