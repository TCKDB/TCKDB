"""The client addresses a calculation by its ``calc_`` ref when it has one (#578).

Upload responses now return ``calculation_ref`` beside ``calculation_id``. The
plan carries it through, and ``upload_artifacts`` / ``upload_artifact`` put it
in the path, falling back to the integer against a server that predates it.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from tckdb_client.builders import (
    Calculation,
    ChemReaction,
    ComputedReactionUpload,
    ComputedSpeciesUpload,
    Geometry,
    LevelOfTheory,
    PlannedArtifactUpload,
    SoftwareRelease,
    Species,
    TransitionState,
)

from conftest import make_client

REF = "calc_" + "a" * 26


@pytest.fixture
def log(tmp_path: Path) -> Path:
    p = tmp_path / "opt.log"
    p.write_bytes(b"log\n")
    return p


def _handler(captured: list):
    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request.url.path)
        return httpx.Response(
            201, json={"calculation_id": 42, "artifacts": [], "warnings": []}
        )

    return handler


def _item(log: Path, *, ref: str | None) -> PlannedArtifactUpload:
    return PlannedArtifactUpload(
        calculation_key="opt", calculation_id=42, path=log, kind="output_log",
        label=None, sha256=None, bytes=None, calculation_ref=ref,
    )


@pytest.mark.parametrize("batch", [True, False])
def test_upload_artifacts_sends_the_ref_when_the_plan_has_one(log, batch) -> None:
    captured: list[str] = []
    client, _ = make_client(_handler(captured))
    client.upload_artifacts([_item(log, ref=REF)], batch_by_calculation=batch)
    assert captured == [f"/api/v1/calculations/{REF}/artifacts"]


@pytest.mark.parametrize("batch", [True, False])
def test_upload_artifacts_falls_back_to_the_integer_without_a_ref(log, batch) -> None:
    captured: list[str] = []
    client, _ = make_client(_handler(captured))
    client.upload_artifacts([_item(log, ref=None)], batch_by_calculation=batch)
    assert captured == ["/api/v1/calculations/42/artifacts"]


def test_upload_artifact_accepts_a_ref(log) -> None:
    captured: list[str] = []
    client, _ = make_client(_handler(captured))
    client.upload_artifact(REF, log, "output_log")
    assert captured == [f"/api/v1/calculations/{REF}/artifacts"]


def test_the_plan_is_filled_from_the_upload_response() -> None:
    geom = Geometry.from_xyz("1\nH atom\nH 0.0 0.0 0.0")
    opt = Calculation.opt(
        SoftwareRelease(software="Gaussian", version="16"),
        LevelOfTheory(method="wb97xd", basis="def2tzvp"),
        output_geometry=geom, final_energy_hartree=-0.5, converged=True, label="opt",
    )
    opt.add_artifact("opt.log", kind="output_log")
    upload = ComputedSpeciesUpload(
        species=Species(smiles="[H]", charge=0, multiplicity=2, label="h"),
        calculations=[opt], primary_calculation=opt,
    )

    def response(with_ref: bool) -> dict:
        primary = {"key": "opt", "calculation_id": 42, "type": "opt", "role": "primary"}
        if with_ref:
            primary["calculation_ref"] = REF
        return {
            "species_entry_id": 1,
            "conformers": [
                {"key": "c", "primary_calculation": primary, "additional_calculations": []}
            ],
        }

    assert [p.calculation_ref for p in upload.artifact_plan(response(True))] == [REF]
    # A server that predates the ref: the plan still resolves, on the integer.
    older = upload.artifact_plan(response(False))
    assert [p.calculation_ref for p in older] == [None]
    assert [p.calculation_id for p in older] == [42]
    assert json.dumps(REF)  # the ref is plain JSON-safe text


def test_computed_reaction_plan_is_filled_from_calculation_key_refs() -> None:
    sr = SoftwareRelease(software="Gaussian", version="16")
    lot = LevelOfTheory(method="wb97xd", basis="def2tzvp")
    ch4 = Species(smiles="C", charge=0, multiplicity=1, label="CH4")
    ch3 = Species(smiles="[CH3]", charge=0, multiplicity=2, label="CH3")
    ts_geom = Geometry.from_xyz("3\nts\nC 0 0 0\nH 0 0 0.8\nH 0 0 -1.0")
    water = Geometry.from_xyz("3\nw\nO 0 0 0.117\nH 0 0.757 -0.469\nH 0 -0.757 -0.469")
    ch4_opt = Calculation.opt(sr, lot, output_geometry=water, converged=True, label="ch4 opt")
    ch4_opt.add_artifact("ch4.log", kind="output_log")
    ts_opt = Calculation.opt(sr, lot, output_geometry=ts_geom, converged=True, label="ts opt")
    ts_opt.add_artifact("ts.log", kind="output_log")
    upload = ComputedReactionUpload(
        reaction=ChemReaction(
            reactants=[ch3], products=[ch4],
            transition_state=TransitionState(charge=0, multiplicity=2, geometry=ts_geom),
        ),
        calculations=[ts_opt],
        species_calculations={ch4: [ch4_opt]},
    )
    ts_ref, ch4_ref = "calc_" + "b" * 26, "calc_" + "c" * 26
    response = {
        "calculation_keys": {"ts_opt": 11, "ch4_opt": 22},
        "calculation_key_refs": {"ts_opt": ts_ref, "ch4_opt": ch4_ref},
    }
    by_key = {p.calculation_key: p for p in upload.artifact_plan(response)}
    assert by_key["ts_opt"].calculation_ref == ts_ref
    assert by_key["ch4_opt"].calculation_ref == ch4_ref
    assert by_key["ts_opt"].calculation_id == 11

    # A server without calculation_key_refs: the plan resolves on integers.
    older = {p.calculation_key: p for p in upload.artifact_plan({"calculation_keys": {"ts_opt": 11, "ch4_opt": 22}})}
    assert {p.calculation_ref for p in older.values()} == {None}
