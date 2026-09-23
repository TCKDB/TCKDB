"""The ``rights`` fragment on an upload becomes the depositor's attestation.

Three things over the wire, and one static guard:

* a direct upload carrying ``rights`` records a ``depositor_agreement``
  attested by the depositor, on the submission the upload opened;
* ``depositor_attests_right_to_license: false`` is refused at the wire --
  the schema types it ``Literal[True]``, so there is no stored "no";
* an async enqueue attests at enqueue time, before any worker runs, because
  the agreement was made when the deposit was accepted;
* every upload request model the routers accept, and the bundle metadata,
  expose ``rights`` -- with the model list derived from the routers, so a
  new upload route cannot be added without joining the contract.
"""

from __future__ import annotations

import inspect

import pytest
from pydantic import BaseModel
from sqlalchemy import select

from app.api.routes import jobs as jobs_routes
from app.api.routes import uploads as upload_routes
from app.db.models.common import RightsBasisKind, SubmissionActorKind
from app.db.models.submission import Submission
from app.db.models.submission_rights import SubmissionRightsAttestation
from app.schemas.workflows.contribution_bundle import BundleSubmissionMetadata
from tests.api.test_api_transport_upload import _transport_payload

_THERMO = {"enthalpy_reference_kind": "formation_298k",
    "species_entry": {"smiles": "[H]", "charge": 0, "multiplicity": 2},
    "scientific_origin": "computed",
    "h298_kj_mol": 217.998,
}
_RIGHTS = {"license": "CC-BY-4.0", "depositor_attests_right_to_license": True}


def _attestations_for(db_session, submission_id: int) -> list[SubmissionRightsAttestation]:
    return list(
        db_session.scalars(
            select(SubmissionRightsAttestation)
            .where(SubmissionRightsAttestation.submission_id == submission_id)
            .order_by(SubmissionRightsAttestation.id)
        )
    )


def test_a_thermo_upload_with_rights_is_attested_by_the_depositor(
    client, db_session, _api_test_user
):
    resp = client.post("/api/v1/uploads/thermo", json={**_THERMO, "rights": _RIGHTS})
    assert resp.status_code == 201, resp.text
    submission_id = resp.json()["submission_id"]

    rows = _attestations_for(db_session, submission_id)
    assert len(rows) == 1
    row = rows[0]
    assert row.basis is RightsBasisKind.depositor_agreement
    assert row.license_id == "CC-BY-4.0"
    assert row.source_terms is None
    assert row.actor_kind is SubmissionActorKind.user
    submission = db_session.get(Submission, submission_id)
    assert row.attested_by == submission.created_by == _api_test_user


def test_an_upload_without_rights_records_no_attestation(client, db_session):
    resp = client.post("/api/v1/uploads/thermo", json=_THERMO)
    assert resp.status_code == 201, resp.text
    assert _attestations_for(db_session, resp.json()["submission_id"]) == []


def test_a_false_attestation_is_refused_at_the_wire(client, db_session):
    before = db_session.scalar(select(Submission.id).order_by(Submission.id.desc()))
    resp = client.post(
        "/api/v1/uploads/thermo",
        json={
            **_THERMO,
            "rights": {"license": "CC-BY-4.0", "depositor_attests_right_to_license": False},
        },
    )
    assert resp.status_code == 422, resp.text
    # Rejected before the route body ran: no submission was opened for it.
    after = db_session.scalar(select(Submission.id).order_by(Submission.id.desc()))
    assert after == before


def test_a_blank_license_is_refused_at_the_wire(client):
    resp = client.post(
        "/api/v1/uploads/thermo",
        json={**_THERMO, "rights": {"license": "   ", "depositor_attests_right_to_license": True}},
    )
    assert resp.status_code == 422, resp.text


def test_an_async_enqueue_attests_at_enqueue_time(client, db_session, _api_test_user):
    """The agreement is recorded with the submission, whether or not a worker ever runs."""
    payload = {**_transport_payload(), "rights": {**_RIGHTS, "source_terms": "Own runs."}}
    resp = client.post("/api/v1/jobs/transport", json=payload)
    assert resp.status_code == 202, resp.text
    submission_id = resp.json()["submission_id"]

    rows = _attestations_for(db_session, submission_id)
    assert len(rows) == 1
    assert rows[0].basis is RightsBasisKind.depositor_agreement
    assert rows[0].attested_by == _api_test_user
    assert rows[0].source_terms == "Own runs."
    # Nothing has been ingested yet: the job is still queued.
    assert resp.json()["status"] == "queued"


# ---------------------------------------------------------------------------
# Every upload request model exposes ``rights``
# ---------------------------------------------------------------------------


def _request_models() -> list[type[BaseModel]]:
    """Every Pydantic request model a POST on the upload or jobs router takes.

    Read off the routers' endpoint signatures rather than typed by hand, so
    the list grows with the routers and a new upload route is covered the
    day it lands -- or fails this guard the day it forgets the field.
    """
    found: dict[str, type[BaseModel]] = {}
    for router in (upload_routes.router, jobs_routes.router):
        for route in router.routes:
            if "POST" not in getattr(route, "methods", set()):
                continue
            # ``eval_str``: the route modules use ``from __future__ import
            # annotations``, so without it every annotation is a string.
            signature = inspect.signature(route.endpoint, eval_str=True)
            for parameter in signature.parameters.values():
                annotation = parameter.annotation
                if inspect.isclass(annotation) and issubclass(annotation, BaseModel):
                    found[annotation.__name__] = annotation
    return [found[name] for name in sorted(found)]


_MODELS = _request_models()


def test_the_model_list_is_derived_and_not_empty():
    """The guard on the guard: an empty parametrize would pass vacuously."""
    names = {model.__name__ for model in _MODELS}
    assert len(names) >= 11, sorted(names)
    assert {"ThermoUploadRequest", "ConformerUploadRequest", "ComputedReactionUploadRequest"} <= names


#: Phase C-E6's one deliberate exception: ``rights`` is *required* on
#: ``ThermoMLUploadRequest``, not optional. Every other upload route can
#: fall back on "attested later, at release time" because the underlying
#: science already exists in TCKDB under someone's authority; this route's
#: whole payload *is* a third-party document (a ThermoML XML file) the
#: depositor is asserting the right to submit, with no NIST/TRC
#: ``source_terms`` fallback the way the archive-sourced CLI path has. Its
#: own required-ness is exercised by
#: ``test_missing_rights_is_refused_by_ordinary_field_requiredness`` in
#: ``backend/tests/api/test_api_thermoml_upload.py``, not here.
_OPTIONAL_RIGHTS_EXEMPT = {"ThermoMLUploadRequest"}


@pytest.mark.parametrize("model", [*_MODELS, BundleSubmissionMetadata], ids=lambda m: m.__name__)
def test_every_upload_contract_exposes_rights(model):
    field = model.model_fields.get("rights")
    assert field is not None, f"{model.__name__} has no `rights` field"
    assert "DepositRights" in repr(field.annotation)
    if model.__name__ in _OPTIONAL_RIGHTS_EXEMPT:
        assert field.is_required(), (
            f"{model.__name__}.rights is listed as a deliberate "
            "required-rights exception but is not actually required"
        )
        return
    assert field.default is None, f"{model.__name__}.rights must default to None (optional in v1)"
