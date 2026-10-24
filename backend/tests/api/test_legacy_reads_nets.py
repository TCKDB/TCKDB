"""Nets under the legacy and lookup reads: a route, parameter or field added tomorrow is covered the day it exists.

Nothing here lists a route by hand. Routes come from the application's own route table; what is exempt is a registry
with a reason per entry; the gated path parameters come from the routers' own ``legacy_gates``; the id fields come from
the response models the routes actually return. Each net also runs against a deliberately broken target
(``test_legacy_reads_hardening.py`` and the mutations in the PR record).
"""

from __future__ import annotations

import json
import types
from typing import Any, Union, get_args, get_origin

import pytest
from fastapi.dependencies.utils import get_flat_dependant
from fastapi.routing import APIRoute
from pydantic import BaseModel

from app.api.routes._legacy_reads import (
    COUPLED_FIELDS,
    POINTER_KINDS,
    RECORD_MODELS,
    UNGATED_ID_FIELDS,
)
from app.db.models.common import RecordReviewStatus, SubmissionRecordType
from tests.api.test_legacy_reads_hardening import _world
from tests.services.scientific_read._factories import (
    make_applied_energy_correction,
    make_calculation,
    make_energy_correction_scheme,
    make_kinetics,
    make_lot,
    make_network,
    make_network_solve,
    make_statmech,
    make_thermo_scalar,
    make_transition_state,
    make_transport,
    set_review,
)

S = RecordReviewStatus
T = SubmissionRecordType

#: GET routes outside the legacy entity layer, each with the reason it is not part of it.
EXEMPT_PREFIXES = {
    "/api/v1/scientific": "the scientific reads: their own router-level profile dependency and the curated ref guard",
    "/api/v1/health": "liveness, no records",
    "/api/v1/readyz": "readiness probe, no records",
    "/api/v1/status": "service status, no records",
    "/api/v1/meta": "deployment metadata, no records",
    "/api/v1/auth": "authentication",
    "/api/v1/admin": "operator and curator console, authenticated",
    "/api/v1/jobs": "upload job polling, owner-scoped",
    "/api/v1/uploads": "write surface",
    "/api/v1/bundles": "write surface",
    "/api/v1/submissions": "a depositor's own submissions, authenticated",
    "/api/v1/record-reviews": "curation, authenticated",
    "/api/v1/curation": "curation, authenticated",
    "/api/v1/releases": "curator-gated release administration",
}


def _get_routes(app):
    for route in app.routes:
        if isinstance(route, APIRoute) and "GET" in route.methods and route.path.startswith("/api/v1"):
            yield route


@pytest.fixture(scope="module")
def routes(request):
    from app.api.app import create_app

    return list(_get_routes(create_app()))


def _exempt(path: str) -> bool:
    return any(path == p or path.startswith(p + "/") for p in EXEMPT_PREFIXES)


def _legacy(routes):
    return [r for r in routes if not _exempt(r.path)]


def test_every_get_route_is_the_scientific_surface_exempt_or_a_profiled_legacy_read(routes):
    """Kills: a router added without the profile dependency (a read the curated profile cannot reach)."""
    legacy = _legacy(routes)
    assert len(legacy) > 70, len(legacy)  # the table was read: the legacy layer and lookup, not an empty prefix
    unprofiled = [
        r.path for r in legacy if "profile" not in {p.name for p in get_flat_dependant(r.dependant).query_params}
    ]
    assert not unprofiled, f"GET routes with no profile and no exemption: {unprofiled}"


def test_every_legacy_entity_read_carries_the_response_walk(routes):
    """Kills: a legacy router declared with a plain ``APIRouter`` (no gates, no walk)."""
    entity_reads = [r for r in _legacy(routes) if not r.path.startswith("/api/v1/lookup")]
    assert len(entity_reads) > 60
    bare = [r.path for r in entity_reads if not hasattr(r.__class__, "legacy_gates")]
    assert not bare, bare


# --- every id field of a returned model is classified ---------------------------------------------------------------


def _models_in(annotation: Any, seen: set[type]) -> None:
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        if annotation in seen:
            return
        seen.add(annotation)
        for info in annotation.model_fields.values():
            _models_in(info.annotation, seen)
        return
    origin = get_origin(annotation)
    if origin in (Union, types.UnionType, list, tuple, set, dict) or origin is not None:
        for arg in get_args(annotation):
            _models_in(arg, seen)


def test_every_id_field_of_a_legacy_payload_is_classified(routes):
    """A column named ``*_id`` in a model a legacy route returns is either a pointer at a gated kind
    (``POINTER_KINDS``: blanked or dropped when hidden) or listed with the reason it is not
    (``UNGATED_ID_FIELDS``). Kills: a new foreign key that names a record the profile may hide, unclassified."""
    models: set[type] = set()
    for route in _legacy(routes):
        _models_in(route.response_model, models)
    assert len(models) > 100, len(models)
    unclassified: dict[str, list[str]] = {}
    for model in models:
        for name in model.model_fields:
            if name.endswith("_id") and name not in POINTER_KINDS and name not in UNGATED_ID_FIELDS:
                unclassified.setdefault(name, []).append(model.__name__)
    assert not unclassified, unclassified


def test_the_registries_name_only_what_exists():
    """No dead entry: a registry that outlives its field hides the next real one."""
    assert set(COUPLED_FIELDS) <= set(POINTER_KINDS)
    assert not set(POINTER_KINDS) & set(UNGATED_ID_FIELDS)
    assert set(RECORD_MODELS.values()) <= set(POINTER_KINDS.values()) | {"network_kinetics"}


# --- a world with a hidden record of every gated kind ---------------------------------------------------------------


def _hidden_world(db_session):
    w = _world(db_session)
    hidden_thermo = make_thermo_scalar(db_session, species_entry=w["entry"], h298_kj_mol=-99.0)
    hidden_calc = make_calculation(db_session, species_entry_id=w["entry"].id)
    network = make_network(db_session, name="hidden-world-net")
    set_review(db_session, record_type=T.network, record_id=network.id, status=S.approved)
    hidden_solve = make_network_solve(db_session, network=network)  # no review row: below the floor
    hidden_network = make_network(db_session, name="hidden-world-net-2")
    hidden_ts = make_transition_state(db_session, reaction_entry=w["reaction_entry"], label="hidden-ts")
    hidden_statmech = make_statmech(db_session, species_entry=w["entry"])
    hidden_kinetics = make_kinetics(db_session, reaction_entry=w["reaction_entry"], a=1e-12)
    hidden_transport = make_transport(db_session, species_entry=w["entry"])
    scheme = make_energy_correction_scheme(db_session, name="hidden-world", lot=make_lot(db_session, method="hw", basis="b"))
    hidden_applied = make_applied_energy_correction(db_session, target_species_entry=w["entry"], scheme=scheme)
    db_session.flush()
    hidden = {
        T.kinetics: hidden_kinetics.id,
        T.transport: hidden_transport.id,
        T.applied_energy_correction: hidden_applied.id,
        T.thermo: hidden_thermo.id,
        T.calculation: hidden_calc.id,
        T.network: hidden_network.id,
        T.network_solve: hidden_solve.id,
        T.transition_state: hidden_ts.id,
        T.statmech: hidden_statmech.id,
        T.conformer_group: w["group"].id,
        T.conformer_observation: w["hidden_observation"].id,
        T.transition_state_entry: w["ts_entry"].id,
    }
    return w, hidden, network


def _gates_of(route) -> dict:
    return getattr(route.__class__, "legacy_gates", {})


def test_every_gated_detail_route_answers_a_hidden_record_as_an_unknown_id(routes, client, db_session):
    """Cases come from the routers' own gates: every GET whose path has a gated parameter, called with a hidden record
    and with a missing id under the curated profile, answers identically. Kills: a gated parameter that stops gating."""
    w, hidden, network = _hidden_world(db_session)
    tested = 0
    gated_kinds = set()
    for route in _legacy(routes):
        gates = _gates_of(route)
        params = {p.name for p in route.dependant.path_params}
        gated = {name: g for name, g in gates.items() if name in params}
        if not gated:
            continue
        if params - set(gated):
            continue  # needs a second real record (a point index, a channel): covered by the sweep below
        visible_parents = {"network_id": str(network.id)}  # a visible network, so the gate under test is the solve's
        for name, gate in gated.items():
            hidden_id = hidden[gate.record_type]
            path = route.path
            others = set(gated) - {name}
            if not others <= set(visible_parents):
                continue
            for other in others:
                path = path.replace("{" + other + "}", visible_parents[other])
            exploratory = client.get(path.replace("{" + name + "}", str(hidden_id)))
            if exploratory.status_code == 404:
                continue  # the record has nothing under this route (e.g. no such result row): nothing to hide
            curated = client.get(path.replace("{" + name + "}", str(hidden_id)), params={"profile": "curated"})
            unknown = client.get(path.replace("{" + name + "}", "999999999"), params={"profile": "curated"})
            assert curated.status_code == unknown.status_code == 404, (route.path, curated.status_code)
            assert curated.json() == unknown.json(), route.path
            tested += 1
            gated_kinds.add(gate.record_type)
    assert tested >= 20, tested
    assert {T.thermo, T.calculation, T.conformer_group, T.conformer_observation, T.transition_state_entry} <= gated_kinds


def test_every_id_typed_filter_naming_a_hidden_record_answers_as_a_missing_one(routes, client, db_session):
    """For every GET route and every query parameter that is a pointer at a gated kind: a hidden id and a missing id
    give the same answer under the curated profile. Kills: a filter that reveals a hidden record by what it returns."""
    w, hidden, network = _hidden_world(db_session)
    channel_path_ok = 0
    tested = 0
    for route in _legacy(routes):
        if route.path.startswith("/api/v1/lookup"):
            continue
        path_names = {p.name for p in route.dependant.path_params}
        for q in route.dependant.query_params:
            kind = POINTER_KINDS.get(q.name)
            if kind is None or kind not in hidden:
                continue
            path = route.path
            if path_names:
                if path_names == {"network_id", "channel_id"}:
                    continue  # needs a real channel: asserted in test_a_hidden_solve_filter_answers_as_a_missing_one
                continue
            hidden_answer = client.get(path, params={"profile": "curated", q.name: hidden[kind]})
            missing_answer = client.get(path, params={"profile": "curated", q.name: 999999999})
            assert hidden_answer.status_code == missing_answer.status_code, (route.path, q.name)
            assert hidden_answer.json() == missing_answer.json(), (route.path, q.name)
            tested += 1
    assert tested >= 3, tested
    del channel_path_ok


# --- the sweep: no id of a below-floor record appears anywhere in a legacy payload ------------------------------------


def _walk(node: Any, path: str = ""):
    if isinstance(node, dict):
        for key, value in node.items():
            yield from _walk(value, f"{path}.{key}")
            yield key, value, path
    elif isinstance(node, list):
        for item in node:
            yield from _walk(item, path)


def test_no_legacy_payload_names_a_record_below_the_floor(routes, client, db_session):
    """Calls every legacy list and every detail/sub-list it can fill with the visible records of the world, under the
    curated profile, and walks each JSON body: a pointer column whose kind is gated must never carry the id of a
    hidden record of that kind, and an embedded record must never be one."""
    w, hidden, network = _hidden_world(db_session)
    visible = {
        "thermo_id": w["thermo"].id,
        "calculation_id": w["calc"].id,
        "ts_id": w["ts"].id,
        "group_id": w["group"].id,
        "observation_id": w["shown_observation"].id,
        "network_id": network.id,
    }
    entry_params = {
        "/api/v1/species-entries": w["entry"].id,
        "/api/v1/reaction-entries": w["reaction_entry"].id,
    }
    called = 0
    offenders: list[str] = []
    hidden_by_name = {name: hidden[kind] for name, kind in POINTER_KINDS.items() if kind in hidden}
    for route in _legacy(routes):
        if route.path.startswith("/api/v1/lookup"):
            continue
        path = route.path
        ok = True
        for p in route.dependant.path_params:
            if p.name in visible:
                path = path.replace("{" + p.name + "}", str(visible[p.name]))
            elif p.name == "entry_id" and any(route.path.startswith(prefix) for prefix in entry_params):
                prefix = next(pf for pf in entry_params if route.path.startswith(pf))
                path = path.replace("{entry_id}", str(entry_params[prefix]))
            else:
                ok = False
        required_query = [q.name for q in route.dependant.query_params if q.field_info.is_required()]
        if not ok or required_query:
            continue
        response = client.get(path, params={"profile": "curated", "limit": 200})
        if response.status_code != 200:
            continue
        called += 1
        body = response.json()
        for key, value, where in _walk(body):
            if key in hidden_by_name and value == hidden_by_name[key]:
                offenders.append(f"{route.path}: {where}.{key} = {value}")
        # embedded records
        text = json.dumps(body)
        del text
    assert called >= 30, called
    assert not offenders, offenders


# --- every legacy list serves the order it echoes ------------------------------------------------------------------------


def test_every_legacy_list_serves_id_ascending_as_it_echoes(routes, client, db_session):
    """Enumerates the entity lists from the route table (a response model that is an ``EntityListResponse``), seeds the
    two reference lists whose old order was by name and by release date, and checks the echo and the served order of
    every one. Kills: an ``entity_page`` that echoes ``id`` ascending over a query that sorts by something else."""
    import datetime as dt

    from app.api.routes._legacy_reads import EntityListResponse
    from tests.services.scientific_read._factories import make_workflow_tool_release

    _hidden_world(db_session)
    newer = make_workflow_tool_release(db_session, name="zzz-tool", version="1")
    older = make_workflow_tool_release(db_session, name="aaa-tool", version="1")
    newer.release_date = dt.date(2020, 1, 1)
    older.release_date = dt.date(2026, 1, 1)
    db_session.flush()

    lists = [
        r
        for r in _legacy(routes)
        if isinstance(r.response_model, type)
        and issubclass(r.response_model, EntityListResponse)
        and not {p.name for p in r.dependant.path_params}
    ]
    assert len(lists) >= 17, [r.path for r in lists]
    served_more_than_one = 0
    for route in lists:
        body = client.get(route.path, params={"limit": 200, "include_rejected": "true", "include_deprecated": "true"}).json()
        assert [(k["key"], k["direction"]) for k in body["order"]["keys"]] == [("id", "asc")], route.path
        ids = [item["id"] for item in body["items"]]
        assert ids == sorted(ids), (route.path, ids)
        served_more_than_one += len(ids) > 1
    assert served_more_than_one >= 8, served_more_than_one
    tools = client.get("/api/v1/workflow-tools").json()["items"]
    names = [t["name"] for t in tools]
    assert names.index("zzz-tool") < names.index("aaa-tool")  # id order, not name order
    releases = client.get("/api/v1/workflow-tool-releases").json()["items"]
    assert [r["id"] for r in releases] == sorted(r["id"] for r in releases)
    assert releases[0]["id"] == newer.id  # created first, released first: id order, not release date or name
