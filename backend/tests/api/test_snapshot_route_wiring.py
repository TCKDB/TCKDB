"""Which routes read in the snapshot session, checked over every route the application mounts.

A selection decides over one consistent state, so its route must take ``get_snapshot_db`` (a read-only REPEATABLE READ
session opened before anything else touches it) and must never take ``get_db``'s shared session as its own, which may
already have run a statement and so cannot become a snapshot. These assertions walk every ``APIRoute`` rather than
counting a hard-coded handful, so the next router that adds a selection route is held to the same rule and no count has
to be updated.

There is no exception: the H298 routes (``/thermo/select``) take the snapshot like the rest, and the last test fails if
any route that selects (its path ends ``/select`` or ``/select/manifest``) is wired to ``get_db`` instead.
"""

from __future__ import annotations

import re

import pytest
from fastapi.routing import APIRoute

from app.api.app import create_app
from app.api.deps import get_db, get_snapshot_db

#: Paths of the routes that decide over a population (or serialise such a decision) and so need the snapshot.
SNAPSHOT_SUFFIXES = (
    "/kinetics/select",
    "/kinetics/select/manifest",
    "/kinetics/export-selected",
    "/thermo/select",
    "/thermo/select/manifest",
    "/calculations/select",
    "/calculations/select/manifest",
    "/conformers/select",
    "/conformers/select/manifest",
    "/evidence/select",
    "/evidence/select/manifest",
)


@pytest.fixture(scope="module")
def api_routes() -> list[APIRoute]:
    return [r for r in create_app().routes if isinstance(r, APIRoute)]


def _direct(route: APIRoute) -> list:
    return [dep.call for dep in route.dependant.dependencies]


def test_no_route_takes_both_the_snapshot_session_and_the_shared_one(api_routes):
    both = [r.path for r in api_routes if get_snapshot_db in _direct(r) and get_db in _direct(r)]
    assert both == []


def test_every_snapshot_route_names_its_session_parameter_session(api_routes):
    users = [r for r in api_routes if get_snapshot_db in _direct(r)]
    assert users, "no route uses the snapshot dependency: the walk found nothing to check"
    for route in users:
        assert next(p for p in route.dependant.dependencies if p.call is get_snapshot_db).name == "session", route.path


def test_every_selection_and_export_route_uses_the_snapshot_dependency(api_routes):
    selection = [r for r in api_routes if r.path.endswith(SNAPSHOT_SUFFIXES)]
    paths = {r.path for r in selection}
    # The reaction-entry and network kinetics selections, each with its manifest, are all present.
    assert {
        "/api/v1/scientific/reaction-entries/{reaction_entry_ref}/kinetics/select",
        "/api/v1/scientific/reaction-entries/{reaction_entry_ref}/kinetics/select/manifest",
        "/api/v1/scientific/networks/{network_ref}/kinetics/select",
        "/api/v1/scientific/networks/{network_ref}/kinetics/select/manifest",
    } <= paths
    # The structure selections (calculations, conformer basins, transition-state evidence), each with its manifest.
    assert {
        "/api/v1/scientific/species-entries/{species_entry_ref}/calculations/select",
        "/api/v1/scientific/species-entries/{species_entry_ref}/calculations/select/manifest",
        "/api/v1/scientific/species-entries/{species_entry_ref}/conformers/select",
        "/api/v1/scientific/species-entries/{species_entry_ref}/conformers/select/manifest",
        "/api/v1/scientific/transition-state-entries/{transition_state_entry_ref}/evidence/select",
        "/api/v1/scientific/transition-state-entries/{transition_state_entry_ref}/evidence/select/manifest",
    } <= paths
    # The H298 thermo selection and its manifest.
    assert {
        "/api/v1/scientific/species-entries/{species_entry_ref}/thermo/select",
        "/api/v1/scientific/species-entries/{species_entry_ref}/thermo/select/manifest",
    } <= paths
    missing = [r.path for r in selection if get_snapshot_db not in _direct(r)]
    assert missing == []
    assert all(get_db not in _direct(r) for r in selection)


def test_no_selection_route_reads_through_the_shared_session(api_routes):
    """A route that selects is never on ``get_db``, whatever its path ends with, so a new one cannot slip past the suffix list."""
    selecting = [r for r in api_routes if re.search(r"/select(/manifest)?$", r.path)]
    assert len(selecting) >= 12, "the walk found too few selection routes to be checking anything"
    assert [r.path for r in selecting if get_db in _direct(r)] == []
    assert [r.path for r in selecting if get_snapshot_db not in _direct(r)] == []
