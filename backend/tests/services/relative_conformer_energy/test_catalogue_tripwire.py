"""PR-2's flip is enforced, not remembered.

The two relative-conformer-energy codes are catalogued ``Reach.guard`` because no route calls the service yet. The
moment a route module imports the service, that claim is false and the codes must be request-reachable.
"""

from __future__ import annotations

import ast
from pathlib import Path

from app.api.code_catalogue import CATALOGUE, Reach

ROUTES = Path(__file__).resolve().parents[3] / "app" / "api" / "routes"
PACKAGE = "app.services.relative_conformer_energy"
CODES = {"relative_conformer_energy_unsupported", "relative_conformer_energy_population_too_large"}


def _imports_the_service(path: Path) -> bool:
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.ImportFrom) and node.module and (node.module == PACKAGE or node.module.startswith(PACKAGE + ".")):
            return True
        if isinstance(node, ast.Import) and any(a.name == PACKAGE or a.name.startswith(PACKAGE + ".") for a in node.names):
            return True
    return False


def test_a_route_that_imports_the_service_needs_the_codes_to_be_request_reachable():
    files = sorted(ROUTES.rglob("*.py"))
    assert len(files) > 20  # the scan covers the routes, not an empty directory
    importers = [str(f.relative_to(ROUTES)) for f in files if _imports_the_service(f)]
    entries = [e for e in CATALOGUE if e.code in CODES]
    assert {e.code for e in entries} == CODES
    guarded = [e.code for e in entries if e.reach is Reach.guard]
    assert not (importers and guarded), (
        f"{importers} import {PACKAGE} while {guarded} are still Reach.guard in app/api/code_catalogue.py: "
        "flip them to Reach.request and regenerate the api vocabulary and producer contract."
    )
