"""Tests for the opportunistic calculation_parameter extraction hook
fired by the artifact upload route and inline-bundle workflows.

Hook contract:

- Fires on ``kind='input'`` artifacts (the deck) and on ``kind='output_log'`` artifacts (the input the
  log echoes); every other kind is ignored. Rows are tagged with the kind they were read from.
- Failure of the parser (or anything else inside the hook) must NEVER
  abort the artifact upload — artifacts are canonical, parameters are
  derived metadata.
- Re-uploading an input artifact replaces parser-derived parameter rows
  but preserves rows whose ``source`` is ``upload`` or ``curated``.
"""

from __future__ import annotations

import base64
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models.calculation import (
    CalculationArtifact,
    CalculationParameter,
)
from app.db.models.common import ArtifactKind, ParameterSource
from app.schemas.fragments.calculation import CalculationParameterObservation
from app.services.calculation_resolution import persist_calculation_parameters
from tests.api.artifact_urls import artifacts_url

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"

# A minimal Gaussian input file. The parser scans for a Link0 block and a
# route line; both are present here.
GAUSSIAN_GJF_TEXT = (
    "%mem=1GB\n"
    "%nprocshared=4\n"
    "# B3LYP/6-31G(d) opt freq\n"
    "\n"
    "Hydrogen atom test input\n"
    "\n"
    "0 2\n"
    "H 0.0 0.0 0.0\n"
    "\n"
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def stub_store_artifact(monkeypatch) -> list[tuple[str, str]]:
    written: list[tuple[str, str]] = []

    def _fake_store(content: bytes, sha256: str) -> str:
        uri = f"s3://test-bucket/{sha256[:2]}/{sha256}"
        written.append((uri, sha256))
        return uri

    monkeypatch.setattr(
        "app.services.artifact_persistence.store_artifact", _fake_store
    )
    return written


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _b64(content: bytes) -> str:
    return base64.b64encode(content).decode("ascii")


def _gaussian_input_artifact(filename: str = "input.gjf") -> dict:
    return {
        "kind": "input",
        "filename": filename,
        "content_base64": _b64(GAUSSIAN_GJF_TEXT.encode()),
    }


def _gaussian_output_log_artifact() -> dict:
    content = (FIXTURES / "gaussian" / "opt_g09.log").read_bytes()
    return {
        "kind": "output_log",
        "filename": "opt.log",
        "content_base64": _b64(content),
    }


CONFORMER_PAYLOAD: dict = {
    "species_entry": {"smiles": "[H]", "charge": 0, "multiplicity": 2},
    "geometry": {"xyz_text": "1\nH atom\nH 0.0 0.0 0.0"},
    "calculation": {
        "type": "sp",
        "software_release": {"name": "Gaussian", "version": "16"},
        "level_of_theory": {"method": "B3LYP", "basis": "6-31G(d)"},
    },
    "label": "h-conf-extract-hook",
}


def _create_calc_id(client) -> int:
    resp = client.post("/api/v1/uploads/conformers", json=CONFORMER_PAYLOAD)
    assert resp.status_code == 201, resp.text
    return resp.json()["primary_calculation"]["calculation_id"]


def _parameter_count(
    db_session: Session, calculation_id: int, *, source: ParameterSource | None = None
) -> int:
    stmt = select(func.count()).select_from(CalculationParameter).where(
        CalculationParameter.calculation_id == calculation_id
    )
    if source is not None:
        stmt = stmt.where(CalculationParameter.source == source)
    return db_session.scalar(stmt) or 0


# ---------------------------------------------------------------------------
# /calculations/{id}/artifacts hook
# ---------------------------------------------------------------------------


class TestUploadHook:
    def test_input_upload_populates_parser_parameters(
        self, client, db_session, stub_store_artifact
    ):
        calc_id = _create_calc_id(client)

        resp = client.post(
            artifacts_url(client, calc_id),
            json={"artifacts": [_gaussian_input_artifact()]},
        )
        assert resp.status_code == 201, resp.text
        assert _parameter_count(
            db_session, calc_id, source=ParameterSource.parser
        ) > 0

    def test_output_log_upload_extracts_the_echoed_input_tagged_output_log(
        self, client, db_session, stub_store_artifact
    ):
        calc_id = _create_calc_id(client)

        resp = client.post(
            artifacts_url(client, calc_id),
            json={"artifacts": [_gaussian_output_log_artifact()]},
        )
        assert resp.status_code == 201, resp.text
        # The log echoes the input; its parameters are read like a deck's, and tagged as the log's.
        assert _parameter_count(
            db_session, calc_id, source=ParameterSource.parser
        ) > 0
        kinds = set(
            db_session.scalars(
                select(CalculationParameter.artifact_kind).where(
                    CalculationParameter.calculation_id == calc_id,
                    CalculationParameter.source == ParameterSource.parser,
                )
            )
        )
        assert kinds == {ArtifactKind.output_log}

    def test_non_deck_non_log_artifact_does_not_trigger_extraction(
        self, client, db_session, stub_store_artifact
    ):
        calc_id = _create_calc_id(client)

        resp = client.post(
            artifacts_url(client, calc_id),
            json={"artifacts": [{**_gaussian_input_artifact("notes.txt"), "kind": "ancillary"}]},
        )
        assert resp.status_code == 201, resp.text
        assert _parameter_count(
            db_session, calc_id, source=ParameterSource.parser
        ) == 0

    def test_parser_failure_does_not_fail_upload(
        self, client, db_session, stub_store_artifact, monkeypatch, caplog
    ):
        calc_id = _create_calc_id(client)

        # Force the bridge service to raise mid-extraction so we exercise
        # the hook's safety net rather than just an "unrecognised
        # software" path that the helper handles trivially.
        from app.services import calculation_parameter_extraction as cpe

        def _boom(*args, **kwargs):
            raise cpe.ParameterExtractionError("simulated parser explosion")

        monkeypatch.setattr(cpe, "extract_and_store_calculation_parameters", _boom)

        resp = client.post(
            artifacts_url(client, calc_id),
            json={"artifacts": [_gaussian_input_artifact()]},
        )
        assert resp.status_code == 201, resp.text

        # Artifact row landed.
        assert db_session.scalar(
            select(func.count())
            .select_from(CalculationArtifact)
            .where(CalculationArtifact.calculation_id == calc_id)
        ) == 1
        # No parameter rows.
        assert _parameter_count(db_session, calc_id) == 0

    def test_curated_and_upload_rows_preserved_on_input_upload(
        self, client, db_session, stub_store_artifact
    ):
        calc_id = _create_calc_id(client)

        # Pre-seed an upload-supplied row and a curator-overridden row.
        from app.db.models.calculation import Calculation

        calc = db_session.get(Calculation, calc_id)
        persist_calculation_parameters(
            session=db_session,
            calculation=calc,
            observations=[
                CalculationParameterObservation(
                    raw_key="manual_upload_key", raw_value="x", section="custom"
                )
            ],
            source=ParameterSource.upload,
        )
        persist_calculation_parameters(
            session=db_session,
            calculation=calc,
            observations=[
                CalculationParameterObservation(
                    raw_key="curator_override_key", raw_value="y", section="custom"
                )
            ],
            source=ParameterSource.curated,
        )
        db_session.flush()

        resp = client.post(
            artifacts_url(client, calc_id),
            json={"artifacts": [_gaussian_input_artifact()]},
        )
        assert resp.status_code == 201, resp.text

        # Upload + curated rows preserved; parser rows added.
        assert _parameter_count(
            db_session, calc_id, source=ParameterSource.upload
        ) == 1
        assert _parameter_count(
            db_session, calc_id, source=ParameterSource.curated
        ) == 1
        assert _parameter_count(
            db_session, calc_id, source=ParameterSource.parser
        ) > 0

    def test_re_upload_replaces_parser_rows(
        self, client, db_session, stub_store_artifact
    ):
        calc_id = _create_calc_id(client)

        r1 = client.post(
            artifacts_url(client, calc_id),
            json={"artifacts": [_gaussian_input_artifact("input1.gjf")]},
        )
        assert r1.status_code == 201
        first_count = _parameter_count(
            db_session, calc_id, source=ParameterSource.parser
        )
        assert first_count > 0

        r2 = client.post(
            artifacts_url(client, calc_id),
            json={"artifacts": [_gaussian_input_artifact("input2.gjf")]},
        )
        assert r2.status_code == 201
        second_count = _parameter_count(
            db_session, calc_id, source=ParameterSource.parser
        )
        # Re-parse runs replace-all on parser rows: identical content
        # yields identical row count, no doubling.
        assert second_count == first_count


# ---------------------------------------------------------------------------
# Inline bundle hook (computed-species)
# ---------------------------------------------------------------------------


def _bundle_payload_with_input_artifact() -> dict:
    return {
        "species_entry": {"smiles": "[H]", "charge": 0, "multiplicity": 2},
        "conformers": [
            {
                "key": "c0",
                "geometry": {"xyz_text": "1\nH atom\nH 0.0 0.0 0.0"},
                "primary_calculation": {
                    "key": "opt0",
                    "type": "opt",
                    "level_of_theory": {"method": "B3LYP", "basis": "6-31G(d)"},
                    "software_release": {"name": "Gaussian", "version": "16"},
                    "opt_result": {"converged": True},
                    "artifacts": [_gaussian_input_artifact("opt0.gjf")],
                },
            }
        ],
    }


# ---------------------------------------------------------------------------
# One request: the order the artifacts are listed in must not decide what a Link1 log binds to
# ---------------------------------------------------------------------------

_LINK1_JOB2_DECK = "%mem=2GB\n# sp wb97xd/def2svp geom=allcheck guess=read int=finegrid scf=verytight\n\nwater\n\n0 1\n"


def _link1_artifacts(log_first: bool) -> list[dict]:
    log = {
        "kind": "output_log",
        "filename": "water_link1.log",
        "content_base64": _b64((FIXTURES / "gaussian" / "water_link1_g16.log").read_bytes()),
    }
    deck = {"kind": "input", "filename": "job2.gjf", "content_base64": _b64(_LINK1_JOB2_DECK.encode())}
    return [log, deck] if log_first else [deck, log]


def _grid_and_scf_rows(db_session: Session, calculation_id: int) -> set[tuple[str, str]]:
    return {
        (r.canonical_key, r.canonical_value)
        for r in db_session.scalars(
            select(CalculationParameter).where(
                CalculationParameter.calculation_id == calculation_id,
                CalculationParameter.source == ParameterSource.parser,
                CalculationParameter.artifact_kind == ArtifactKind.output_log,
            )
        )
        if r.canonical_key in ("grid.quality", "scf.convergence")
    }


@pytest.mark.parametrize("log_first", [False, True])
def test_a_link1_log_binds_to_its_job_whichever_order_the_request_lists_it(
    client, db_session, stub_store_artifact, log_first
):
    payload = {
        **CONFORMER_PAYLOAD,
        "calculation": {**CONFORMER_PAYLOAD["calculation"], "level_of_theory": {"method": "wB97XD", "basis": "def2-SVP"}},
    }
    resp = client.post("/api/v1/uploads/conformers", json=payload)
    assert resp.status_code == 201, resp.text
    calc_id = resp.json()["primary_calculation"]["calculation_id"]
    resp = client.post(artifacts_url(client, calc_id), json={"artifacts": _link1_artifacts(log_first)})
    assert resp.status_code == 201, resp.text
    assert _grid_and_scf_rows(db_session, calc_id) == {("grid.quality", "fine"), ("scf.convergence", "very_tight")}
    assert "reported_setting_differs_from_requested" in [w["code"] for w in resp.json().get("warnings", [])]


@pytest.mark.parametrize("log_first", [False, True])
def test_a_link1_log_in_an_inline_bundle_binds_whichever_order_it_is_listed(
    client, db_session, stub_store_artifact, log_first
):
    payload = _bundle_payload_with_input_artifact()
    calc = payload["conformers"][0]["primary_calculation"]
    calc.update(type="sp", level_of_theory={"method": "wB97XD", "basis": "def2-SVP"}, artifacts=_link1_artifacts(log_first))
    calc.pop("opt_result")
    calc["sp_result"] = {"electronic_energy_hartree": -76.3}
    resp = client.post("/api/v1/uploads/computed-species", json=payload)
    assert resp.status_code == 201, resp.text
    calc_id = resp.json()["conformers"][0]["primary_calculation"]["calculation_id"]
    assert _grid_and_scf_rows(db_session, calc_id) == {("grid.quality", "fine"), ("scf.convergence", "very_tight")}


def test_the_three_extraction_loops_read_input_decks_first():
    import inspect

    from app.api.routes import calculations
    from app.services.calculation_parameter_extraction import inputs_first
    from app.workflows import computed_reaction, computed_species

    for module in (calculations, computed_reaction, computed_species):
        assert "inputs_first(" in inspect.getsource(module)
    from types import SimpleNamespace

    listed = [SimpleNamespace(kind=k, n=n) for n, k in enumerate([ArtifactKind.output_log, ArtifactKind.input, ArtifactKind.output_log])]
    assert [(a.kind, a.n) for a in inputs_first(listed)] == [
        (ArtifactKind.input, 1),
        (ArtifactKind.output_log, 0),
        (ArtifactKind.output_log, 2),
    ]


class TestComputedSpeciesInlineHook:
    def test_inline_input_artifact_triggers_extraction(
        self, client, db_session, stub_store_artifact
    ):
        resp = client.post(
            "/api/v1/uploads/computed-species",
            json=_bundle_payload_with_input_artifact(),
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        calc_id = body["conformers"][0]["primary_calculation"]["calculation_id"]
        assert _parameter_count(
            db_session, calc_id, source=ParameterSource.parser
        ) > 0
