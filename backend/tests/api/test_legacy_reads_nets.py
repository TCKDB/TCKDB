"""Nets under the legacy and lookup reads: a route, parameter or field added tomorrow is covered the day it exists.

Nothing here lists a route by hand. Routes come from the application's own route table; what is exempt is a registry
with a reason per entry; the gated path parameters come from the routers' own ``legacy_gates``; the id fields come from
the response models the routes actually return. Each net also runs against a deliberately broken target
(``test_legacy_reads_hardening.py`` and the mutations in the PR record).
"""

from __future__ import annotations

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


#: What the registries must agree with, written down separately from them. Moving a column into ``UNGATED_ID_FIELDS``,
#: dropping a gate, or dropping a ``RECORD_MODELS`` entry then fails a test instead of leaving the nets green.
INDEPENDENT_FIELD_KINDS = {
    "calculation_id": T.calculation,
    "parent_calculation_id": T.calculation,
    "child_calculation_id": T.calculation,
    "source_calculation_id": T.calculation,
    "source_scan_calculation_id": T.calculation,
    "zpe_calculation_id": T.calculation,
    "conformer_observation_id": T.conformer_observation,
    "source_conformer_observation_id": T.conformer_observation,
    "conformer_group_id": T.conformer_group,
    "target_conformer_group_id": T.conformer_group,
    "statmech_id": T.statmech,
    "thermo_id": T.thermo,
    "kinetics_id": T.kinetics,
    "transport_id": T.transport,
    "transition_state_entry_id": T.transition_state_entry,
    "transition_state_id": T.transition_state,
    "network_id": T.network,
    "solve_id": T.network_solve,
}

#: Path prefix and path parameter of every detail route that returns a gated record, and the kind it returns.
INDEPENDENT_PATH_KINDS = [
    ("/api/v1/thermo", "thermo_id", T.thermo),
    ("/api/v1/kinetics", "kinetics_id", T.kinetics),
    ("/api/v1/statmech", "statmech_id", T.statmech),
    ("/api/v1/transport", "transport_id", T.transport),
    ("/api/v1/calculations", "calculation_id", T.calculation),
    ("/api/v1/transition-states", "ts_id", T.transition_state),
    ("/api/v1/transition-states/entries", "entry_id", T.transition_state_entry),
    ("/api/v1/conformer-groups", "group_id", T.conformer_group),
    ("/api/v1/conformer-observations", "observation_id", T.conformer_observation),
    ("/api/v1/networks", "network_id", T.network),
    ("/api/v1/networks", "solve_id", T.network_solve),
    ("/api/v1/applied-energy-corrections", "correction_id", T.applied_energy_correction),
]

#: Keys under which a response embeds a list of gated records, and the kind of those records.
INDEPENDENT_EMBEDDED_KEYS = {
    "entries": T.transition_state_entry,
    "observations": T.conformer_observation,
    "groups": T.conformer_group,
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


def test_the_pointer_registry_agrees_with_the_independent_table():
    """Kills: moving a gated column (statmech_id, ...) into ``UNGATED_ID_FIELDS`` or retargeting it."""
    for name, kind in INDEPENDENT_FIELD_KINDS.items():
        assert POINTER_KINDS.get(name) == kind, (name, POINTER_KINDS.get(name))
        assert name not in UNGATED_ID_FIELDS, name


def test_every_detail_route_that_returns_a_gated_record_has_its_gate(routes):
    """Derived from record types and a name map, not from the gates: a gate that is removed is found missing.
    Kills: removing a gate (the applied-energy-correction one went unnoticed once)."""
    missing = []
    checked = 0
    for route in _legacy(routes):
        gates = _gates_of(route)
        params = {p.name for p in route.dependant.path_params}
        # the longest prefix that matches decides the kind (``/transition-states/entries`` before ``/transition-states``)
        candidates = [
            (prefix, param, kind)
            for prefix, param, kind in INDEPENDENT_PATH_KINDS
            if (route.path == prefix or route.path.startswith(prefix + "/")) and param in params
        ]
        for prefix, param, kind in candidates:
            if prefix == "/api/v1/transition-states" and route.path.startswith("/api/v1/transition-states/entries"):
                continue
            checked += 1
            gate = gates.get(param)
            if gate is None or gate.record_type != kind:
                missing.append((route.path, param, kind.value))
    assert checked >= 30, checked
    assert not missing, missing


def test_the_embedded_model_registry_covers_the_independent_keys():
    """Kills: dropping a read model (``TransitionStateEntryRead``...) from ``RECORD_MODELS``."""
    covered = set(RECORD_MODELS.values())
    assert set(INDEPENDENT_EMBEDDED_KEYS.values()) <= covered


#: Detail routes that return a record of a kind that is deliberately NOT gated, with the reason.
INDEPENDENT_UNGATED_DETAIL = {
    "/api/v1/species-entries/{entry_id}": "scoping parent (ref_guard.UNGATED_REVIEWABLE): never gated on detail",
    "/api/v1/reaction-entries/{entry_id}": "scoping parent (ref_guard.UNGATED_REVIEWABLE): never gated on detail",
}


def test_the_entry_detail_routes_are_ungated_on_purpose(routes, client, db_session):
    """Entries are scoping parents. The gate-coverage net lists them here, with the reason, and CALLS each route on an
    unreviewed entry under curated, so an ``ensure_not_below_floor`` written inside the route body is caught too."""
    from tests.services.scientific_read._factories import (
        make_chem_reaction,
        make_reaction_entry,
        make_species,
        make_species_entry,
        next_inchi_key,
    )

    species = make_species(db_session, smiles="C=O", inchi_key=next_inchi_key("UG"))
    entry = make_species_entry(db_session, species)  # never reviewed: below the curated floor
    rs = make_species(db_session, smiles="CC", inchi_key=next_inchi_key("UGR"))
    ps = make_species(db_session, smiles="CCC", inchi_key=next_inchi_key("UGP"))
    reaction_entry = make_reaction_entry(
        db_session,
        reaction=make_chem_reaction(db_session, reactants=[rs], products=[ps]),
        reactant_entries=[make_species_entry(db_session, rs)],
        product_entries=[make_species_entry(db_session, ps)],
    )
    ids = {"/api/v1/species-entries/{entry_id}": entry.id, "/api/v1/reaction-entries/{entry_id}": reaction_entry.id}
    by_path = {r.path: r for r in routes}
    for path, reason in INDEPENDENT_UNGATED_DETAIL.items():
        assert reason
        assert path in by_path, path
        assert not _gates_of(by_path[path]), path  # no gate declared on a scoping parent's route class
        for profile in ("exploratory", "curated"):
            response = client.get(path.replace("{entry_id}", str(ids[path])), params={"profile": profile})
            assert response.status_code == 200, (path, profile, response.status_code)


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


def _hidden_sets(w, hidden) -> dict:
    """Every hidden id per kind: the never-reviewed records, and the approved children of an unreviewed parent."""
    sets = {kind: {record_id} for kind, record_id in hidden.items()}
    sets[T.conformer_observation].add(w["orphaned_observation"].id)
    sets[T.transition_state_entry].add(w["orphan_ts_entry"].id)
    return sets


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
    hidden_ids = _hidden_sets(w, hidden)
    tested = 0
    for route in _legacy(routes):
        if route.path.startswith("/api/v1/lookup"):
            continue
        path_names = {p.name for p in route.dependant.path_params}
        for q in route.dependant.query_params:
            kind = INDEPENDENT_FIELD_KINDS.get(q.name)
            if kind is None or kind not in hidden_ids or path_names:
                continue  # a route with path parameters needs real parents: its filter is asserted by name elsewhere
            missing_answer = client.get(route.path, params={"profile": "curated", q.name: 999999999})
            for record_id in sorted(hidden_ids[kind]):
                hidden_answer = client.get(route.path, params={"profile": "curated", q.name: record_id})
                assert hidden_answer.status_code == missing_answer.status_code, (route.path, q.name, record_id)
                assert hidden_answer.json() == missing_answer.json(), (route.path, q.name, record_id)
                tested += 1
    assert tested >= 4, tested


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
    curated profile, and walks each JSON body against the INDEPENDENT tables: a pointer column of a gated kind never
    carries the id of a hidden record of that kind (including an approved child of a hidden parent), and no list
    embedded under ``entries`` / ``observations`` / ``groups`` holds one."""
    w, hidden, network = _hidden_world(db_session)
    hidden_ids = _hidden_sets(w, hidden)
    entry_params = {
        "/api/v1/species-entries": w["entry"].id,
        "/api/v1/reaction-entries": w["reaction_entry"].id,
    }
    calculation_ids = [w["calc"].id, w["orphan_calc"].id, w["ts_calc"].id, w["orphan_ts_calc"].id]
    called = 0
    offenders: list[str] = []
    for calculation_id in calculation_ids:
        visible = {
            "thermo_id": w["thermo"].id,
            "calculation_id": calculation_id,
            "ts_id": w["ts"].id,
            "group_id": w["group"].id,
            "observation_id": w["shown_observation"].id,
            "network_id": network.id,
        }
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
            for key, value, where in _walk(response.json()):
                kind = INDEPENDENT_FIELD_KINDS.get(key)
                if kind in hidden_ids and value in hidden_ids[kind]:
                    offenders.append(f"{route.path}: {where}.{key} = {value}")
                embedded = INDEPENDENT_EMBEDDED_KEYS.get(key)
                if embedded in hidden_ids and isinstance(value, list):
                    for item in value:
                        if isinstance(item, dict) and item.get("id") in hidden_ids[embedded]:
                            offenders.append(f"{route.path}: {where}.{key} embeds hidden {embedded.value} {item['id']}")
    assert called >= 120, called  # four calculations x the routes that can be filled
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
