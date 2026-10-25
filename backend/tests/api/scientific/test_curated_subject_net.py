"""The subject net, pinned on REAL response models (ADR 0023, step 2).

Under ``curated`` the approved floor decides which records a list returns. A service that forgets it hands the guard an
UNLISTED record as the answer, and the guard must fail loud rather than serve it badged. These tests make a real service
forget its floor and require the request to fail closed, so they fail if either the service's floor test is lost (that is
what the mutation does) or the guard's subject net stops recognising the model's identity (the registry in
``ref_guard.SUBJECT_IDENTITY``).

The mutation is the one a reviewer used: replace the ``visible_statuses(...)`` floor of a search service with the statuses
the default profile shows plus ``under_review`` and ``not_reviewed``.
"""

from __future__ import annotations

import importlib
import typing

import pytest
from pydantic import BaseModel

from app.db.models.common import RecordReviewStatus
from app.services.scientific_read import ref_guard
from tests.api.scientific._curated_sweep_crawl import enumerate_routes, send, variants_for
from tests.api.scientific._curated_sweep_world import build_sweep_world

BASE = "/api/v1/scientific"
NO_FLOOR = {RecordReviewStatus.approved, RecordReviewStatus.under_review, RecordReviewStatus.not_reviewed}


def _forgetful(*_args, **_kwargs):
    return set(NO_FLOOR)


#: ``(service module, route)``: a read whose service applies the floor through ``visible_statuses``. The requests are the
#: sweep's own variants for the route (the filters it needs to return the world's records).
CASES = [
    ("calculations_search", ("GET", f"{BASE}/calculations/search")),
    ("species_calculations_search", ("GET", f"{BASE}/species-calculations/search")),
    ("conformers_search", ("GET", f"{BASE}/conformers/search")),
    ("statmech_search", ("GET", f"{BASE}/statmech/search")),
    ("transport_search", ("GET", f"{BASE}/transport/search")),
    ("artifacts_search", ("GET", f"{BASE}/artifacts/search")),
    ("thermo", ("GET", f"{BASE}/species-entries/{{species_entry_id}}/thermo")),
    ("kinetics", ("GET", f"{BASE}/reaction-entries/{{reaction_entry_id}}/kinetics")),
    ("transition_states_search", ("GET", f"{BASE}/transition-states/search")),
    ("networks_search", ("GET", f"{BASE}/networks/search")),
    ("network_solves_search", ("GET", f"{BASE}/network-solves/search")),
    ("network_kinetics_search", ("GET", f"{BASE}/network-kinetics/search")),
    ("observations", ("GET", f"{BASE}/species-entries/{{species_entry_id}}/observations")),
]


@pytest.fixture
def world(db_session, client, login_as):
    w = build_sweep_world(db_session)
    login_as(w.extra["curator"].id)
    return w


def _variants(client, world, route):
    spec = next(s for s in enumerate_routes(client.app) if s.key == route)
    return spec, variants_for(spec, world, with_internal_ids=False)


@pytest.mark.parametrize(("module", "route"), CASES, ids=[c[0] for c in CASES])
def test_a_service_that_forgets_its_floor_fails_closed_instead_of_serving_an_unlisted_subject(
    client, world, monkeypatch, module, route
):
    """Kills: the subject net losing a model (a new ``record`` shape with no registered identity), or a search served
    badged. Control first: every variant of the read answers 200 with its floor in place. Then the service forgets its
    floor, and at least one variant (the one that returns an unlisted record) must fail closed."""
    spec, variants = _variants(client, world, route)
    assert variants
    for variant in variants:
        assert send(client, spec, variant, profile="curated").status_code == 200
    service = importlib.import_module(f"app.services.scientific_read.{module}")
    monkeypatch.setattr(service, "visible_statuses", _forgetful)
    failed_closed = 0
    for variant in variants:
        try:
            send(client, spec, variant, profile="curated")
        except ref_guard.RefGuardUnplaceable:
            failed_closed += 1
        assert send(client, spec, variant, profile="exploratory").status_code == 200  # exploratory is not guarded
    assert failed_closed, f"{module}: serving unlisted records as the answer did not fail closed"


# ---------------------------------------------------------------------------
# The registry is complete
# ---------------------------------------------------------------------------


def _subject_models(app) -> dict[str, set[str]]:
    """Every response model that is the type of ``record`` or an element of ``records``, with the routes that return it."""

    def models(annotation):
        if isinstance(annotation, type) and issubclass(annotation, BaseModel):
            yield annotation
        for arg in typing.get_args(annotation):
            yield from models(arg)

    found: dict[str, set[str]] = {}
    for route in app.routes:
        path = getattr(route, "path", "")
        if not path.startswith(BASE):
            continue
        for envelope in models(getattr(route, "response_model", None)):
            for field_name in ("record", "records"):
                field = envelope.model_fields.get(field_name)
                if field is None:
                    continue
                for model in models(field.annotation):
                    found.setdefault(model.__name__, set()).add(path)
    return found


def test_every_subject_model_has_a_registered_identity(client):
    """Kills: a new response model for ``record`` / ``records`` that the subject net would silently not check. Each is in
    ``SUBJECT_IDENTITY`` (with the path to its identity ref) or in ``UNGATED_SUBJECTS`` (with a reason), never both, and the
    registry names no model that no route returns."""
    subjects = _subject_models(client.app)
    registered = set(ref_guard.SUBJECT_IDENTITY)
    ungated = set(ref_guard.UNGATED_SUBJECTS)
    assert not registered & ungated
    assert set(subjects) == registered | ungated, {
        "unregistered": sorted(set(subjects) - registered - ungated),
        "stale": sorted((registered | ungated) - set(subjects)),
    }
    assert all(reason for reason in ref_guard.UNGATED_SUBJECTS.values())


def test_every_registered_identity_path_leads_to_a_ref_field(client):
    """Kills: a registry path that names a field the model does not have (the net would match nothing)."""

    def models(annotation):
        if isinstance(annotation, type) and issubclass(annotation, BaseModel):
            yield annotation
        for arg in typing.get_args(annotation):
            yield from models(arg)

    by_name: dict[str, type[BaseModel]] = {}
    for route in client.app.routes:
        if not getattr(route, "path", "").startswith(BASE):
            continue
        for envelope in models(getattr(route, "response_model", None)):
            for field_name in ("record", "records"):
                field = envelope.model_fields.get(field_name)
                for model in models(field.annotation) if field is not None else ():
                    by_name[model.__name__] = model
    for name, identity in ref_guard.SUBJECT_IDENTITY.items():
        model = by_name[name]
        for key in identity[:-1]:
            (model,) = list(models(model.model_fields[key].annotation))[:1]
        assert identity[-1] in model.model_fields and identity[-1].endswith("_ref"), name
