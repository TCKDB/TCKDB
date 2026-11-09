"""Builders send attached files inline; only what cannot go inline needs the second phase."""

from __future__ import annotations

import base64

import pytest

from tckdb_client.builders import (
    Calculation,
    ChemReaction,
    ComputedReactionUpload,
    ComputedSpeciesUpload,
    DIAG_CODES,
    Geometry,
    LevelOfTheory,
    SoftwareRelease,
    Species,
    TCKDBBuilderValidationError,
    TransitionState,
)
from tckdb_client.builders import artifact as artifact_mod

WATER = "3\nwater\nO 0.0 0.0 0.117\nH 0.0 0.757 -0.469\nH 0.0 -0.757 -0.469"


def _opt(label="opt", **kw):
    return Calculation.opt(
        SoftwareRelease(software="Gaussian", version="16"),
        LevelOfTheory(method="b3lyp", basis="def2svp"),
        output_geometry=Geometry.from_xyz(WATER),
        final_energy_hartree=-76.3,
        converged=True,
        label=label,
        **kw,
    )


def _species_upload(calcs, **kw):
    return ComputedSpeciesUpload(
        species=Species(smiles="O", charge=0, multiplicity=1, label="water"),
        calculations=calcs,
        primary_calculation=calcs[0],
        **kw,
    )


def _calc_artifacts(payload):
    return payload["conformers"][0]["primary_calculation"].get("artifacts")


@pytest.fixture
def log(tmp_path):
    p = tmp_path / "opt.log"
    p.write_bytes(b"Entering Gaussian System\nbody\n")
    return p


def test_species_payload_embeds_the_file_and_artifact_plan_is_then_empty(log):
    opt = _opt()
    opt.add_artifact(log, kind="output_log")
    upload = _species_upload([opt])
    arts = _calc_artifacts(upload.to_payload())
    assert arts == [
        {
            "kind": "output_log",
            "filename": "opt.log",
            "content_base64": base64.b64encode(log.read_bytes()).decode(),
        }
    ]
    response = {
        "conformers": [
            {
                "key": "conformer-1",
                "primary_calculation": {"key": "opt", "calculation_id": 5, "calculation_ref": "calc_x"},
                "additional_calculations": [],
            }
        ]
    }
    assert upload.artifact_plan(response) == []
    assert not [d for d in upload.emission_diagnostics() if d.code == DIAG_CODES.ARTIFACT_UPLOAD_REQUIRES_SECOND_PHASE]


def test_declared_sha256_and_bytes_are_forwarded(log):
    import hashlib

    opt = _opt()
    opt.add_artifact(
        log, kind="output_log", sha256=hashlib.sha256(log.read_bytes()).hexdigest(), bytes=log.stat().st_size
    )
    art = _calc_artifacts(_species_upload([opt]).to_payload())[0]
    assert art["sha256"] == hashlib.sha256(log.read_bytes()).hexdigest()
    assert art["bytes"] == log.stat().st_size


def test_inline_artifacts_false_keeps_files_out_and_plan_lists_them(log):
    opt = _opt()
    opt.add_artifact(log, kind="output_log")
    upload = _species_upload([opt], inline_artifacts=False)
    assert _calc_artifacts(upload.to_payload()) is None
    assert len(upload.artifact_plan_preview()) == 1
    assert [d for d in upload.emission_diagnostics() if d.code == DIAG_CODES.ARTIFACT_UPLOAD_REQUIRES_SECOND_PHASE]


def test_preview_still_lists_every_file_after_an_inline_payload(log):
    opt = _opt()
    opt.add_artifact(log, kind="output_log")
    upload = _species_upload([opt])
    upload.to_payload()
    assert len(upload.artifact_plan_preview()) == 1
    # ...and did not disturb what the real plan skips.
    assert _calc_artifacts(upload.to_payload()) is not None


def test_a_missing_file_is_refused_with_a_clear_message(tmp_path):
    opt = _opt()
    opt.add_artifact(tmp_path / "gone.log", kind="output_log")
    with pytest.raises(TCKDBBuilderValidationError, match="gone.log"):
        _species_upload([opt]).to_payload()


def test_a_file_over_the_per_artifact_cap_is_refused_early(log, monkeypatch):
    monkeypatch.setattr(artifact_mod, "MAX_ARTIFACT_BYTES", 5)
    opt = _opt()
    opt.add_artifact(log, kind="output_log")
    with pytest.raises(TCKDBBuilderValidationError, match="per artifact"):
        _species_upload([opt]).to_payload()


def test_files_past_the_inline_budget_fall_back_to_the_second_phase(tmp_path, monkeypatch):
    big, small = tmp_path / "big.log", tmp_path / "small.log"
    big.write_bytes(b"x" * 100)
    small.write_bytes(b"y" * 10)
    monkeypatch.setattr(artifact_mod, "INLINE_TOTAL_BUDGET_BYTES", 115)
    first, second, third = _opt("a"), _opt("b"), _opt("c")
    first.add_artifact(big, kind="output_log")
    second.add_artifact(big, kind="output_log")  # does not fit after the first
    third.add_artifact(small, kind="output_log")  # a later, smaller file still fits
    upload = _species_upload([first, second, third])
    conf = upload.to_payload()["conformers"][0]
    assert conf["primary_calculation"]["artifacts"][0]["filename"] == "big.log"
    by_key = {c["key"]: c for c in conf["additional_calculations"]}
    assert "artifacts" not in by_key["b"]
    assert by_key["c"]["artifacts"][0]["filename"] == "small.log"
    response = {
        "conformers": [
            {
                "key": "conformer-1",
                "primary_calculation": {"key": "a", "calculation_id": 1},
                "additional_calculations": [
                    {"key": "b", "calculation_id": 2},
                    {"key": "c", "calculation_id": 3},
                ],
            }
        ]
    }
    plan = upload.artifact_plan(response)
    assert [(p.calculation_key, p.path) for p in plan] == [("b", big)]
    diags = [d for d in upload.emission_diagnostics() if d.code == DIAG_CODES.ARTIFACT_UPLOAD_REQUIRES_SECOND_PHASE]
    assert [d.path for d in diags] == ["calculations[b].artifacts"]


def test_reaction_payload_embeds_files_on_the_flat_calculations(log):
    ch4 = Species(smiles="C", charge=0, multiplicity=1, label="CH4")
    ch3 = Species(smiles="[CH3]", charge=0, multiplicity=2, label="CH3")
    ts_geom = Geometry.from_xyz("3\nts\nC 0 0 0\nH 0 0 0.8\nH 0 0 -1.0")
    ts_opt = _opt("ts opt")
    ts_opt.add_artifact(log, kind="output_log")
    ch4_opt = _opt("ch4 opt")
    ch4_opt.add_artifact(log, kind="output_log")
    upload = ComputedReactionUpload(
        reaction=ChemReaction(
            reactants=[ch3],
            products=[ch4],
            transition_state=TransitionState(charge=0, multiplicity=2, geometry=ts_geom),
        ),
        calculations=[ts_opt],
        species_calculations={ch4: [ch4_opt]},
    )
    payload = upload.to_payload()
    assert payload["transition_state"]["calculation"]["artifacts"][0]["filename"] == "opt.log"
    with_calcs = [s for s in payload["species"] if s["conformers"]]
    assert with_calcs[0]["conformers"][0]["calculation"]["artifacts"][0]["kind"] == "output_log"
    ts_key = payload["transition_state"]["calculation"]["key"]
    sp_key = with_calcs[0]["conformers"][0]["calculation"]["key"]
    plan = upload.artifact_plan({"calculation_keys": {ts_key: 1, sp_key: 2}})
    assert plan == []
