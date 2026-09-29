"""Producer responses name their records by public ref; the two path routes accept it.

#578: ``POST /submissions/{id}/rights-attestations`` and
``POST /calculations/{id}/artifacts`` used to take only a row id, and no upload
response returned the ``sub_`` / ``calc_`` ref a caller would need instead.

Two halves, both of which must fail when the fix is reverted:

* every producer response that carries a ``submission_id`` or a calculation id
  carries the ref beside it, and the ref is the row's real ``public_ref``;
* both routes accept the integer or the ref and apply *identical* checks to
  either -- a ref is not a way around an ownership or freeze check.
"""

from __future__ import annotations

import base64
import importlib.util
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.routing import APIRoute
from sqlalchemy import select

from app.db.models.calculation import Calculation
from app.db.models.common import (
    RecordReviewEventKind,
    RecordReviewStatus,
    SubmissionRecordType,
)
from app.db.models.record_review import RecordReview, RecordReviewEvent
from app.db.models.submission import Submission
from tests.api.test_api_bundle_submit import ENDPOINT as BUNDLE_ENDPOINT
from tests.api.test_api_bundle_submit import _load_bundle
from tests.api.test_api_kfir_rxn import _BUNDLE as COMPUTED_REACTION_BUNDLE
from tests.api.test_api_network_reads import _pdep_payload
from tests.api.test_api_provenance_warnings import _kinetics_payload
from tests.api.test_api_statmech_upload import _statmech_payload
from tests.api.test_api_transport_upload import _transport_payload
from tests.api.test_api_upload_computed_species import _hydrogen_bundle_payload
from tests.api.test_api_uploads import _reaction_payload, _transition_state_payload

CONFORMER = {
    "species_entry": {"smiles": "[H]", "charge": 0, "multiplicity": 2},
    "geometry": {"xyz_text": "1\nH atom\nH 0.0 0.0 0.0"},
    "calculation": {
        "type": "sp",
        "software_release": {"name": "Gaussian", "version": "16"},
        "level_of_theory": {"method": "B3LYP", "basis": "6-31G(d)"},
    },
    "label": "h-conf-578",
}
THERMO = {
    "enthalpy_reference_kind": "formation_298k",
    "species_entry": {"smiles": "[H]", "charge": 0, "multiplicity": 2},
    "scientific_origin": "computed",
    "h298_kj_mol": 217.998,
}
ARTIFACT = {
    "artifacts": [
        {
            "kind": "ancillary",
            "filename": "note.txt",
            "content_base64": base64.b64encode(b"hello-578").decode("ascii"),
        }
    ]
}


@pytest.fixture(autouse=True)
def _stub_store(monkeypatch):
    monkeypatch.setattr(
        "app.services.artifact_persistence.store_artifact",
        lambda content, sha256: f"s3://test-bucket/{sha256[:2]}/{sha256}",
    )


def _sub_ref_of(db_session, submission_id: int) -> str:
    return db_session.get(Submission, submission_id).public_ref


# ---------------------------------------------------------------------------
# 1. Responses return the refs
# ---------------------------------------------------------------------------


_UPLOAD_PAYLOADS = {
    "/api/v1/uploads/conformers": lambda: CONFORMER,
    "/api/v1/uploads/thermo": lambda: THERMO,
    "/api/v1/uploads/statmech": _statmech_payload,
    "/api/v1/uploads/transport": _transport_payload,
    "/api/v1/uploads/reactions": _reaction_payload,
    "/api/v1/uploads/kinetics": _kinetics_payload,
    "/api/v1/uploads/networks": lambda: {"name": "example network"},
    "/api/v1/uploads/networks/pdep": _pdep_payload,
    "/api/v1/uploads/transition-states": _transition_state_payload,
    "/api/v1/uploads/computed-species": _hydrogen_bundle_payload,
    "/api/v1/uploads/computed-reaction": lambda: COMPUTED_REACTION_BUNDLE,
    "/api/v1/jobs/transport": _transport_payload,
}


@pytest.mark.parametrize("route", list(_UPLOAD_PAYLOADS))
def test_upload_and_job_responses_carry_the_submission_ref(
    client, db_session, route
) -> None:
    resp = client.post(route, json=_UPLOAD_PAYLOADS[route]())
    assert resp.status_code in (201, 202), resp.text
    body = resp.json()
    assert body["submission_id"] is not None
    assert body["submission_ref"] is not None and body["submission_ref"].startswith("sub_")
    assert body["submission_ref"] == _sub_ref_of(db_session, body["submission_id"])


def test_bundle_submit_response_carries_the_submission_ref(client, db_session) -> None:
    resp = client.post(BUNDLE_ENDPOINT, json=_load_bundle("thermo-bundle-v0.json"))
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["submission_ref"] == _sub_ref_of(db_session, body["submission_id"])


def test_conformer_upload_names_its_calculation_by_ref(client, db_session) -> None:
    resp = client.post("/api/v1/uploads/conformers", json=CONFORMER)
    assert resp.status_code == 201, resp.text
    ref = resp.json()["primary_calculation"]
    row = db_session.get(Calculation, ref["calculation_id"])
    assert ref["calculation_ref"].startswith("calc_")
    assert ref["calculation_ref"] == row.public_ref


def test_computed_species_names_its_calculations_by_ref(client, db_session) -> None:
    resp = client.post("/api/v1/uploads/computed-species", json=_hydrogen_bundle_payload())
    assert resp.status_code == 201, resp.text
    calc = resp.json()["conformers"][0]["primary_calculation"]
    assert calc["calculation_ref"] == db_session.get(
        Calculation, calc["calculation_id"]
    ).public_ref


def test_computed_reaction_names_every_calculation_key_by_ref(client, db_session) -> None:
    resp = client.post("/api/v1/uploads/computed-reaction", json=COMPUTED_REACTION_BUNDLE)
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["calculation_keys"], "the fixture must persist calculations"
    assert body["calculation_key_refs"].keys() == body["calculation_keys"].keys()
    for key, calc_id in body["calculation_keys"].items():
        assert body["calculation_key_refs"][key] == db_session.get(
            Calculation, calc_id
        ).public_ref


def test_artifact_upload_response_carries_the_calculation_ref(client, db_session) -> None:
    conf = client.post("/api/v1/uploads/conformers", json=CONFORMER).json()
    ref = conf["primary_calculation"]["calculation_ref"]
    resp = client.post(f"/api/v1/calculations/{ref}/artifacts", json=ARTIFACT)
    assert resp.status_code == 201, resp.text
    assert resp.json()["calculation_ref"] == ref


def test_worker_job_result_carries_the_refs(client, db_session) -> None:
    """The polled job result is a producer response too."""
    from app.db.models.upload_job import UploadJob
    from app.workers.upload_worker import run_one_job

    enq = client.post("/api/v1/jobs/conformer", json=CONFORMER)
    assert enq.status_code == 202, enq.text
    assert enq.json()["submission_ref"].startswith("sub_")
    job = db_session.get(UploadJob, enq.json()["job_id"])
    run_one_job(db_session, job)
    result = job.result
    assert result["submission_ref"] == enq.json()["submission_ref"]
    calc = result["primary_calculation"]
    assert calc["calculation_ref"] == db_session.get(
        Calculation, calc["calculation_id"]
    ).public_ref


def test_worker_computed_reaction_job_result_carries_the_refs(client, db_session) -> None:
    """The computed-reaction job's polled result names each calc key by ref."""
    from app.db.models.upload_job import UploadJob
    from app.workers.upload_worker import run_one_job

    enq = client.post("/api/v1/jobs/computed-reaction", json=COMPUTED_REACTION_BUNDLE)
    assert enq.status_code == 202, enq.text
    job = db_session.get(UploadJob, enq.json()["job_id"])
    run_one_job(db_session, job)
    result = job.result
    assert result["submission_ref"] == enq.json()["submission_ref"]
    assert "result_unavailable" not in result, result
    assert result["calculation_keys"], "the fixture must persist calculations"
    assert result["calculation_key_refs"].keys() == result["calculation_keys"].keys()
    for key, calc_id in result["calculation_keys"].items():
        assert result["calculation_key_refs"][key] == db_session.get(
            Calculation, calc_id
        ).public_ref


def test_every_producer_response_model_pairs_its_ids_with_refs() -> None:
    """A new producer response cannot return a bare submission/calculation id.

    Walks the response models of every producer route and requires a
    ``*_ref`` sibling for each ``submission_id`` and each calculation-id
    field, so the next upload route cannot reintroduce the gap #578 closed.
    """
    path = Path(__file__).resolve().parents[2] / "scripts" / "generate_producer_contract.py"
    spec = importlib.util.spec_from_file_location("generate_producer_contract", path)
    generator = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("generate_producer_contract", generator)
    spec.loader.exec_module(generator)

    def models(annotation):
        out, stack = [], [annotation]
        while stack:
            cur = stack.pop()
            if isinstance(cur, type) and hasattr(cur, "model_fields"):
                out.append(cur)
                continue
            stack.extend(getattr(cur, "__args__", ()) or ())
        return out

    seen: dict[type, None] = {}

    def walk(model):
        if model in seen:
            return
        seen[model] = None
        for field in model.model_fields.values():
            for inner in models(field.annotation):
                walk(inner)

    for info in generator.discover_routes():
        route: APIRoute = info.route
        if info.category == "producer" and route.response_model is not None:
            for model in models(route.response_model):
                walk(model)

    checked = 0
    missing: list[str] = []
    for model in seen:
        fields = model.model_fields
        for name in ("submission_id", "calculation_id"):
            if name in fields:
                checked += 1
                sibling = name.replace("_id", "_ref")
                if sibling not in fields:
                    missing.append(f"{model.__name__}.{name}")
        if "calculation_keys" in fields:
            checked += 1
            if "calculation_key_refs" not in fields:
                missing.append(f"{model.__name__}.calculation_keys")
    assert checked >= 10, "the walk found almost nothing; it is checking nothing"
    # ``CalculationArtifactRead.calculation_id`` echoes a stored row on both the
    # read and write surface, where ``public_ref`` names the artifact; it is
    # deliberately left to the read-side phase of the identifier policy.
    assert sorted(missing) == ["CalculationArtifactRead.calculation_id"]


# ---------------------------------------------------------------------------
# 2. Both routes accept the integer or the ref, with identical checks
# ---------------------------------------------------------------------------


def _attest(client, handle, basis="depositor_agreement"):
    return client.post(
        f"/api/v1/submissions/{handle}/rights-attestations",
        json={"license": "CC-BY-4.0", "basis": basis},
    )


def test_rights_attestation_accepts_integer_and_ref_alike(client, db_session) -> None:
    dep = client.post("/api/v1/uploads/thermo", json=THERMO).json()
    by_ref = _attest(client, dep["submission_ref"])
    assert by_ref.status_code == 201, by_ref.text
    by_int = _attest(client, dep["submission_id"])
    assert by_int.status_code == 201, by_int.text
    assert by_ref.json()["submission_ref"] == dep["submission_ref"]
    assert by_int.json()["submission_ref"] == dep["submission_ref"]
    # The second supersedes the first: they hit the one submission.
    assert by_int.json()["supersedes_attestation_ref"] == by_ref.json()["attestation_ref"]


@pytest.mark.parametrize("form", ["ref", "id"])
def test_rights_attestation_checks_are_the_same_for_both_forms(
    client, login_as, _api_curator_user, _api_other_user, form
) -> None:
    dep = client.post("/api/v1/uploads/thermo", json=THERMO).json()
    handle = dep["submission_ref"] if form == "ref" else dep["submission_id"]

    # A depositor may not make a curator-basis attestation.
    r = _attest(client, handle, basis="historical_review")
    assert r.status_code == 403
    assert r.json()["code"] == "rights_attestation_requires_curator"

    # A curator who did not deposit may not claim the depositor's agreement.
    login_as(_api_curator_user)
    r = _attest(client, handle)
    assert r.status_code == 403
    assert r.json()["code"] == "rights_attestation_not_depositor"

    # A stranger cannot see the submission at all.
    # (The view check answers first, in its own words: a depositor_agreement
    # would also be refused as "not the depositor", so pin which check spoke.)
    login_as(_api_other_user)
    stranger = _attest(client, handle)
    assert stranger.status_code == 403
    assert stranger.json()["detail"] == "Not authorized to view this submission."


def test_rights_attestation_rejects_unknown_and_mistyped_handles(client) -> None:
    unknown = _attest(client, "sub_" + "a" * 26)
    assert unknown.status_code == 404
    wrong = _attest(client, "calc_" + "a" * 26)
    assert wrong.status_code == 422
    assert wrong.json()["code"] == "handle_type_mismatch"
    assert _attest(client, "not a handle").status_code == 422


def _handle_for(client, form):
    conf = client.post("/api/v1/uploads/conformers", json=CONFORMER).json()
    primary = conf["primary_calculation"]
    handle = primary["calculation_ref"] if form == "ref" else primary["calculation_id"]
    return primary["calculation_id"], f"/api/v1/calculations/{handle}/artifacts"


@pytest.mark.parametrize("form", ["ref", "id"])
def test_artifact_upload_ownership_is_the_same_for_both_forms(
    client, login_as, _api_other_user, form
) -> None:
    calc_id, url = _handle_for(client, form)
    ok = client.post(url, json=ARTIFACT)
    assert ok.status_code == 201, ok.text
    assert ok.json()["calculation_id"] == calc_id

    login_as(_api_other_user)
    refused = client.post(url, json=ARTIFACT)
    assert refused.status_code == 403, refused.text


@pytest.mark.parametrize("form", ["ref", "id"])
def test_artifact_upload_freeze_is_the_same_for_both_forms(
    client, db_session, form
) -> None:
    calc_id, url = _handle_for(client, form)
    calculation = db_session.get(Calculation, calc_id)
    review = db_session.scalar(
        select(RecordReview).where(
            RecordReview.record_type == SubmissionRecordType.calculation,
            RecordReview.record_id == calc_id,
        )
    )
    review.status = RecordReviewStatus.under_review
    db_session.flush()
    db_session.add(
        RecordReviewEvent(
            record_review_id=review.id,
            event_kind=RecordReviewEventKind.status_change,
            from_status=RecordReviewStatus.under_review,
            to_status=RecordReviewStatus.approved,
            actor_user_id=calculation.created_by,
            created_at=datetime.now(timezone.utc),
        )
    )
    db_session.flush()
    frozen = client.post(url, json=ARTIFACT)
    assert frozen.status_code == 409, frozen.text
    assert "frozen after calculation approval" in frozen.json()["detail"]


def test_artifact_upload_rejects_unknown_and_mistyped_handles(client) -> None:
    unknown = client.post(f"/api/v1/calculations/calc_{'a' * 26}/artifacts", json=ARTIFACT)
    assert unknown.status_code == 404
    wrong = client.post(f"/api/v1/calculations/sub_{'a' * 26}/artifacts", json=ARTIFACT)
    assert wrong.status_code == 422
    assert wrong.json()["code"] == "handle_type_mismatch"
    missing_int = client.post("/api/v1/calculations/999999999/artifacts", json=ARTIFACT)
    assert missing_int.status_code == 404


@pytest.mark.parametrize(
    "url",
    [
        "/api/v1/submissions/sub_" + "a" * 26 + "/rights-attestations",
        "/api/v1/submissions/999999999/rights-attestations",
        "/api/v1/calculations/calc_" + "a" * 26 + "/artifacts",
        "/api/v1/calculations/999999999/artifacts",
    ],
)
def test_an_unknown_handle_is_the_same_404_code_on_both_routes_and_forms(
    client, url
) -> None:
    body = (
        {"license": "CC-BY-4.0", "basis": "depositor_agreement"}
        if "rights" in url
        else ARTIFACT
    )
    resp = client.post(url, json=body)
    assert resp.status_code == 404, resp.text
    assert resp.json()["code"] == "handle_not_found"
