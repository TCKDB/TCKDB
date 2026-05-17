"""Tests for the Phase-7 two-phase artifact upload path.

The artifact subsystem deliberately splits scientific upload from
file transport: the bundle endpoint creates calculation rows, then a
second-phase POST per calculation moves the bytes. These tests
cover:

- Local validation on ``Calculation.add_artifact``.
- ``to_payload()`` must not mention artifacts.
- ``emission_diagnostics()`` reports ``artifact_upload_requires_second_phase``.
- ``upload.artifact_plan(result)`` resolves bundle-local keys against
  the server response (both endpoints).
- ``client.upload_artifact`` / ``client.upload_artifacts`` POST to the
  expected route in the expected shape.
"""

from __future__ import annotations

import base64
import json
from pathlib import Path

import httpx
import pytest

from tckdb_client.builders import (
    ARTIFACT_KINDS,
    Artifact,
    Calculation,
    ChemReaction,
    ComputedReactionUpload,
    ComputedSpeciesUpload,
    DIAG_CODES,
    Geometry,
    Kinetics,
    LevelOfTheory,
    PlannedArtifactUpload,
    Species,
    SoftwareRelease,
    TCKDBBuilderValidationError,
    TransitionState,
)

from conftest import make_client


def _sr() -> SoftwareRelease:
    return SoftwareRelease(software="Gaussian", version="16")


def _lot() -> LevelOfTheory:
    return LevelOfTheory(method="wb97xd", basis="def2tzvp")


@pytest.fixture
def water_geom() -> Geometry:
    return Geometry.from_xyz(
        "3\nwater\n"
        "O 0.0 0.0 0.117\n"
        "H 0.0 0.757 -0.469\n"
        "H 0.0 -0.757 -0.469"
    )


@pytest.fixture
def water_species() -> Species:
    return Species(smiles="O", charge=0, multiplicity=1, label="water")


@pytest.fixture
def calc_with_artifacts(water_geom):
    opt = Calculation.opt(
        _sr(), _lot(), output_geometry=water_geom,
        final_energy_hartree=-76.4, converged=True, label="opt",
    )
    opt.add_artifact("ethanol_opt.gjf", kind="input")
    opt.add_artifact("ethanol_opt.log", kind="output_log")
    return opt


@pytest.fixture
def real_artifact_file(tmp_path: Path) -> Path:
    p = tmp_path / "input.gjf"
    p.write_text("# fake gaussian input\n")
    return p


# --- Calculation.add_artifact validation -----------------------------


class TestAddArtifactValidation:
    def test_kind_must_be_supported(self, water_geom):
        opt = Calculation.opt(_sr(), _lot(), output_geometry=water_geom)
        with pytest.raises(TCKDBBuilderValidationError):
            opt.add_artifact("foo.log", kind="weird_kind")

    def test_kind_supported_set_matches_backend_enum(self):
        # Backend ``ArtifactKind`` values, mirrored.
        assert ARTIFACT_KINDS == {
            "input", "output_log", "checkpoint",
            "formatted_checkpoint", "ancillary",
        }

    def test_path_must_be_non_empty(self, water_geom):
        opt = Calculation.opt(_sr(), _lot(), output_geometry=water_geom)
        with pytest.raises(TCKDBBuilderValidationError):
            opt.add_artifact("   ", kind="input")
        with pytest.raises(TCKDBBuilderValidationError):
            opt.add_artifact("", kind="input")

    def test_path_must_be_str_or_path(self, water_geom):
        opt = Calculation.opt(_sr(), _lot(), output_geometry=water_geom)
        with pytest.raises(TCKDBBuilderValidationError):
            opt.add_artifact(42, kind="input")  # type: ignore[arg-type]

    def test_sha256_must_be_64_hex(self, water_geom):
        opt = Calculation.opt(_sr(), _lot(), output_geometry=water_geom)
        with pytest.raises(TCKDBBuilderValidationError):
            opt.add_artifact(
                "foo.log", kind="output_log", sha256="not hex",
            )
        with pytest.raises(TCKDBBuilderValidationError):
            opt.add_artifact(
                "foo.log", kind="output_log", sha256="A" * 64,  # uppercase not allowed
            )
        # Valid: 64 lowercase hex.
        opt.add_artifact(
            "foo.log", kind="output_log", sha256="a" * 64,
        )

    def test_bytes_must_be_non_negative_int(self, water_geom):
        opt = Calculation.opt(_sr(), _lot(), output_geometry=water_geom)
        with pytest.raises(TCKDBBuilderValidationError):
            opt.add_artifact("foo.log", kind="output_log", bytes=-1)
        with pytest.raises(TCKDBBuilderValidationError):
            opt.add_artifact("foo.log", kind="output_log", bytes=True)  # type: ignore[arg-type]
        with pytest.raises(TCKDBBuilderValidationError):
            opt.add_artifact(
                "foo.log", kind="output_log", bytes="123",  # type: ignore[arg-type]
            )

    def test_label_must_be_non_empty_when_supplied(self, water_geom):
        opt = Calculation.opt(_sr(), _lot(), output_geometry=water_geom)
        with pytest.raises(TCKDBBuilderValidationError):
            opt.add_artifact("foo.log", kind="output_log", label="")

    def test_add_artifact_does_not_check_file_existence(self, water_geom):
        opt = Calculation.opt(_sr(), _lot(), output_geometry=water_geom)
        # Path doesn't exist — should still attach OK.
        opt.add_artifact("/does/not/exist.log", kind="output_log")
        assert len(opt.artifacts) == 1

    def test_attaches_in_caller_order(self, water_geom):
        opt = Calculation.opt(_sr(), _lot(), output_geometry=water_geom)
        opt.add_artifact("a.gjf", kind="input")
        opt.add_artifact("b.log", kind="output_log")
        opt.add_artifact("c.chk", kind="checkpoint")
        assert [a.kind for a in opt.artifacts] == [
            "input", "output_log", "checkpoint",
        ]


# --- to_payload must not emit artifacts -----------------------------


def test_artifacts_not_emitted_in_computed_species_payload(
    water_species, calc_with_artifacts,
):
    upload = ComputedSpeciesUpload(
        species=water_species,
        calculations=[calc_with_artifacts],
        primary_calculation=calc_with_artifacts,
    )
    payload = upload.to_payload()
    # Walk the whole payload; "artifacts" must not appear anywhere.
    blob = json.dumps(payload)
    assert "artifacts" not in blob
    assert "content_base64" not in blob


def test_artifacts_not_emitted_in_computed_reaction_payload(water_geom):
    sr = _sr()
    lot = _lot()
    ch4 = Species(smiles="C", charge=0, multiplicity=1, label="CH4")
    ch3 = Species(smiles="[CH3]", charge=0, multiplicity=2, label="CH3")
    ts_geom = Geometry.from_xyz("3\nts\nC 0 0 0\nH 0 0 0.8\nH 0 0 -1.0")
    ch4_opt = Calculation.opt(
        sr, lot, output_geometry=water_geom, converged=True, label="ch4 opt",
    )
    ch4_opt.add_artifact("ch4.log", kind="output_log")
    ts_opt = Calculation.opt(
        sr, lot, output_geometry=ts_geom, converged=True, label="ts opt",
    )
    ts_opt.add_artifact("ts.log", kind="output_log")

    rxn = ChemReaction(
        reactants=[ch3], products=[ch4],
        transition_state=TransitionState(charge=0, multiplicity=2, geometry=ts_geom),
    )
    upload = ComputedReactionUpload(
        reaction=rxn,
        calculations=[ts_opt],
        species_calculations={ch4: [ch4_opt]},
    )
    blob = json.dumps(upload.to_payload())
    assert "artifacts" not in blob
    assert "content_base64" not in blob


# --- emission_diagnostics reports second-phase requirement -----------


def test_computed_species_emission_diag_reports_artifacts(
    water_species, calc_with_artifacts,
):
    upload = ComputedSpeciesUpload(
        species=water_species,
        calculations=[calc_with_artifacts],
        primary_calculation=calc_with_artifacts,
    )
    diags = upload.emission_diagnostics()
    matches = [
        d for d in diags
        if d.code == DIAG_CODES.ARTIFACT_UPLOAD_REQUIRES_SECOND_PHASE
    ]
    assert len(matches) == 1
    assert matches[0].level == "warning"
    assert "calculations[opt].artifacts" == matches[0].path


def test_computed_reaction_emission_diag_reports_artifacts(water_geom):
    sr = _sr()
    lot = _lot()
    ch4 = Species(smiles="C", charge=0, multiplicity=1, label="CH4")
    ch3 = Species(smiles="[CH3]", charge=0, multiplicity=2, label="CH3")
    ts_geom = Geometry.from_xyz("3\nts\nC 0 0 0\nH 0 0 0.8\nH 0 0 -1.0")
    ch4_opt = Calculation.opt(
        sr, lot, output_geometry=water_geom, converged=True, label="ch4 opt",
    )
    ch4_opt.add_artifact("ch4.log", kind="output_log")
    ts_opt = Calculation.opt(
        sr, lot, output_geometry=ts_geom, converged=True, label="ts opt",
    )
    ts_opt.add_artifact("ts.log", kind="output_log")
    ts_opt.add_artifact("ts.fchk", kind="formatted_checkpoint")

    rxn = ChemReaction(
        reactants=[ch3], products=[ch4],
        transition_state=TransitionState(charge=0, multiplicity=2, geometry=ts_geom),
    )
    upload = ComputedReactionUpload(
        reaction=rxn,
        calculations=[ts_opt],
        species_calculations={ch4: [ch4_opt]},
    )
    diags = upload.emission_diagnostics()
    matches = [
        d for d in diags
        if d.code == DIAG_CODES.ARTIFACT_UPLOAD_REQUIRES_SECOND_PHASE
    ]
    # One diagnostic per calculation with attached artifacts (TS opt + ch4 opt).
    assert len(matches) == 2
    paths = sorted(d.path for d in matches)
    assert paths == [
        "calculations[ch4 opt].artifacts",
        "calculations[ts opt].artifacts",
    ]


def test_no_artifact_diagnostic_when_no_artifacts(water_species, water_geom):
    opt = Calculation.opt(
        _sr(), _lot(), output_geometry=water_geom, converged=True,
    )
    upload = ComputedSpeciesUpload(
        species=water_species,
        calculations=[opt],
        primary_calculation=opt,
    )
    diags = upload.emission_diagnostics()
    assert not [
        d for d in diags
        if d.code == DIAG_CODES.ARTIFACT_UPLOAD_REQUIRES_SECOND_PHASE
    ]


# --- artifact_plan resolution ----------------------------------------


def test_computed_species_artifact_plan_resolves_keys(
    water_species, calc_with_artifacts,
):
    upload = ComputedSpeciesUpload(
        species=water_species,
        calculations=[calc_with_artifacts],
        primary_calculation=calc_with_artifacts,
    )
    # The opt's label is "opt" which slugifies to "opt" — same key as
    # what the bundle assembler mints.
    fake_response = {
        "species_entry_id": 7,
        "type": "computed_species",
        "conformers": [
            {
                "key": "conformer_1",
                "primary_calculation": {
                    "key": "opt",
                    "calculation_id": 42,
                    "type": "opt",
                    "role": "primary",
                },
                "additional_calculations": [],
            },
        ],
    }
    plan = upload.artifact_plan(fake_response)
    assert len(plan) == 2
    assert all(isinstance(p, PlannedArtifactUpload) for p in plan)
    assert all(p.calculation_id == 42 for p in plan)
    assert [p.kind for p in plan] == ["input", "output_log"]
    assert all(p.calculation_key == "opt" for p in plan)


def test_artifact_plan_errors_when_response_missing_conformers(
    water_species, calc_with_artifacts,
):
    upload = ComputedSpeciesUpload(
        species=water_species,
        calculations=[calc_with_artifacts],
        primary_calculation=calc_with_artifacts,
    )
    with pytest.raises(TCKDBBuilderValidationError, match="conformers"):
        upload.artifact_plan({"species_entry_id": 1})


def test_artifact_plan_errors_when_response_is_not_dict(
    water_species, calc_with_artifacts,
):
    upload = ComputedSpeciesUpload(
        species=water_species,
        calculations=[calc_with_artifacts],
        primary_calculation=calc_with_artifacts,
    )
    with pytest.raises(TCKDBBuilderValidationError):
        upload.artifact_plan("not a dict")


def test_artifact_plan_errors_when_key_not_in_response(
    water_species, calc_with_artifacts,
):
    upload = ComputedSpeciesUpload(
        species=water_species,
        calculations=[calc_with_artifacts],
        primary_calculation=calc_with_artifacts,
    )
    fake_response = {
        "species_entry_id": 7,
        "conformers": [
            {
                "key": "conformer_1",
                "primary_calculation": {
                    "key": "DIFFERENT_KEY",
                    "calculation_id": 99,
                    "type": "opt",
                    "role": "primary",
                },
                "additional_calculations": [],
            },
        ],
    }
    with pytest.raises(TCKDBBuilderValidationError, match="opt"):
        upload.artifact_plan(fake_response)


def test_computed_reaction_artifact_plan_uses_calculation_keys(water_geom):
    sr = _sr()
    lot = _lot()
    ch4 = Species(smiles="C", charge=0, multiplicity=1, label="CH4")
    ch3 = Species(smiles="[CH3]", charge=0, multiplicity=2, label="CH3")
    ts_geom = Geometry.from_xyz("3\nts\nC 0 0 0\nH 0 0 0.8\nH 0 0 -1.0")
    ch4_opt = Calculation.opt(
        sr, lot, output_geometry=water_geom, converged=True, label="ch4 opt",
    )
    ch4_opt.add_artifact("ch4.log", kind="output_log")
    ts_opt = Calculation.opt(
        sr, lot, output_geometry=ts_geom, converged=True, label="ts opt",
    )
    ts_opt.add_artifact("ts.log", kind="output_log")

    rxn = ChemReaction(
        reactants=[ch3], products=[ch4],
        transition_state=TransitionState(charge=0, multiplicity=2, geometry=ts_geom),
    )
    upload = ComputedReactionUpload(
        reaction=rxn,
        calculations=[ts_opt],
        species_calculations={ch4: [ch4_opt]},
    )
    response = {
        "type": "computed_reaction",
        "reaction_entry_id": 1,
        "reaction_id": 1,
        "calculation_keys": {
            "ts_opt": 11,
            "ch4_opt": 22,
        },
    }
    plan = upload.artifact_plan(response)
    assert len(plan) == 2
    by_key = {p.calculation_key: p for p in plan}
    assert by_key["ts_opt"].calculation_id == 11
    assert by_key["ch4_opt"].calculation_id == 22


def test_computed_reaction_artifact_plan_errors_without_calculation_keys(
    water_geom,
):
    sr = _sr()
    lot = _lot()
    ch4 = Species(smiles="C", charge=0, multiplicity=1, label="CH4")
    ch3 = Species(smiles="[CH3]", charge=0, multiplicity=2, label="CH3")
    ts_geom = Geometry.from_xyz("3\nts\nC 0 0 0\nH 0 0 0.8\nH 0 0 -1.0")
    ts_opt = Calculation.opt(
        sr, lot, output_geometry=ts_geom, converged=True, label="ts opt",
    )
    ts_opt.add_artifact("ts.log", kind="output_log")
    rxn = ChemReaction(
        reactants=[ch3], products=[ch4],
        transition_state=TransitionState(charge=0, multiplicity=2, geometry=ts_geom),
    )
    upload = ComputedReactionUpload(reaction=rxn, calculations=[ts_opt])
    with pytest.raises(TCKDBBuilderValidationError, match="calculation_keys"):
        upload.artifact_plan({"type": "computed_reaction"})


# --- client.upload_artifact ------------------------------------------


def _capture_artifact_request_handler(captured: dict):
    def handler(request: httpx.Request) -> httpx.Response:
        captured["method"] = request.method
        captured["url"] = str(request.url)
        captured["body"] = json.loads(request.content.decode())
        captured["headers"] = dict(request.headers)
        return httpx.Response(
            201,
            json={
                "calculation_id": int(str(request.url).rsplit("/", 2)[-2]),
                "artifacts": [],
                "warnings": [],
            },
        )

    return handler


def test_client_upload_artifact_hits_calculation_artifacts_endpoint(
    real_artifact_file,
):
    captured: dict = {}
    client, _recorder = make_client(_capture_artifact_request_handler(captured))
    client.upload_artifact(
        42, real_artifact_file, kind="input",
    )
    assert captured["method"] == "POST"
    assert captured["url"].endswith("/calculations/42/artifacts")
    body = captured["body"]
    assert "artifacts" in body and len(body["artifacts"]) == 1
    item = body["artifacts"][0]
    assert item["kind"] == "input"
    assert item["filename"] == real_artifact_file.name
    # base64 round-trips to the source bytes.
    assert (
        base64.b64decode(item["content_base64"])
        == real_artifact_file.read_bytes()
    )


def test_client_upload_artifact_passes_optional_hashes_and_sizes(
    real_artifact_file,
):
    captured: dict = {}
    client, _recorder = make_client(_capture_artifact_request_handler(captured))
    client.upload_artifact(
        7, real_artifact_file, kind="input",
        sha256="a" * 64, bytes=23,
    )
    item = captured["body"]["artifacts"][0]
    assert item["sha256"] == "a" * 64
    assert item["bytes"] == 23


def test_client_upload_artifact_rejects_missing_file(tmp_path):
    client, _recorder = make_client(_capture_artifact_request_handler({}))
    with pytest.raises(ValueError, match="does not exist"):
        client.upload_artifact(
            1, tmp_path / "no-such-file.log", kind="output_log",
        )


def test_client_upload_artifact_rejects_directory(tmp_path):
    client, _recorder = make_client(_capture_artifact_request_handler({}))
    with pytest.raises(ValueError, match="not a file"):
        client.upload_artifact(1, tmp_path, kind="output_log")


# --- client.upload_artifacts -----------------------------------------


def test_client_upload_artifacts_uploads_sequentially(tmp_path):
    paths = []
    for name in ("a.log", "b.log", "c.log"):
        p = tmp_path / name
        p.write_text(f"contents of {name}\n")
        paths.append(p)

    seen_urls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_urls.append(str(request.url))
        calc_id = int(str(request.url).rsplit("/", 2)[-2])
        return httpx.Response(
            201,
            json={
                "calculation_id": calc_id,
                "artifacts": [],
                "warnings": [],
            },
        )

    client, _recorder = make_client(handler)
    plan = [
        PlannedArtifactUpload(
            calculation_key="a_key", calculation_id=10, path=paths[0],
            kind="output_log", label=None, sha256=None, bytes=None,
        ),
        PlannedArtifactUpload(
            calculation_key="b_key", calculation_id=20, path=paths[1],
            kind="output_log", label=None, sha256=None, bytes=None,
        ),
        PlannedArtifactUpload(
            calculation_key="c_key", calculation_id=30, path=paths[2],
            kind="output_log", label=None, sha256=None, bytes=None,
        ),
    ]
    results = client.upload_artifacts(plan)
    assert len(results) == 3
    assert [r["calculation_id"] for r in results] == [10, 20, 30]
    assert [u.rsplit("/", 2)[-2] for u in seen_urls] == ["10", "20", "30"]


def test_client_upload_artifacts_idempotency_key_prefix(tmp_path):
    p = tmp_path / "a.log"
    p.write_text("hello")
    seen_idem: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_idem.append(request.headers.get("idempotency-key", ""))
        return httpx.Response(
            201,
            json={"calculation_id": 1, "artifacts": [], "warnings": []},
        )

    client, _recorder = make_client(handler)
    plan = [
        PlannedArtifactUpload(
            calculation_key="my_opt", calculation_id=1, path=p,
            kind="output_log", label=None, sha256=None, bytes=None,
        ),
    ]
    client.upload_artifacts(plan, idempotency_key_prefix="run-2026-05-16")
    assert seen_idem == ["run-2026-05-16:my_opt:output_log"]


def test_client_upload_artifacts_raises_on_bad_plan_item(tmp_path):
    client, _recorder = make_client(_capture_artifact_request_handler({}))

    class BadItem:
        path = tmp_path / "x.log"
        kind = "output_log"
        # No calculation_id at all.

    with pytest.raises(TypeError, match="calculation_id"):
        client.upload_artifacts([BadItem()])
