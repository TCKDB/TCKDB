"""PR-2's flip is enforced, not remembered.

The two relative-conformer-energy codes are catalogued ``Reach.guard`` because no route calls the service yet. The
moment a route module imports the service, that claim is false and the codes must be request-reachable.

Every import form is recognised, including the house style ``from app.services import relative_conformer_energy``
and relative imports (resolved against the importing module's package).
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from app.api.code_catalogue import CATALOGUE, Reach

APP = Path(__file__).resolve().parents[3] / "app"
ROUTES = APP / "api" / "routes"
PARENT = "app.services"
NAME = "relative_conformer_energy"
PACKAGE = f"{PARENT}.{NAME}"
CODES = {"relative_conformer_energy_unsupported", "relative_conformer_energy_population_too_large"}


def _is_package(dotted: str) -> bool:
    return dotted == PACKAGE or dotted.startswith(PACKAGE + ".")


def imports_the_service(source: str, module_package: str) -> bool:
    """Whether ``source`` (a module in ``module_package``) imports the service package in any form."""
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            if any(_is_package(a.name) for a in node.names):
                return True
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                parts = module_package.split(".")
                if node.level - 1 > len(parts) - 1:
                    raise AssertionError(f"relative import climbs above the top package in {module_package}")
                base = parts[: len(parts) - (node.level - 1)]
                module = ".".join([*base, *([node.module] if node.module else [])])
            else:
                module = node.module or ""
            if _is_package(module) or (module == PARENT and any(a.name == NAME for a in node.names)):
                return True
    return False


@pytest.mark.parametrize(
    "line",
    [
        "import app.services.relative_conformer_energy",
        "import app.services.relative_conformer_energy.service as s",
        "from app.services.relative_conformer_energy import run_relative_energies",
        "from app.services.relative_conformer_energy.service import run_relative_energies",
        "from app.services import relative_conformer_energy",
        "from app.services import health, relative_conformer_energy as rce",
        "from ....services import relative_conformer_energy",
        "from ....services.relative_conformer_energy import service",
    ],
)
def test_every_import_form_is_recognised(line):
    assert imports_the_service(line, "app.api.routes.scientific") is True


@pytest.mark.parametrize(
    "line",
    [
        "from app.services import structure_selection",
        "from app.services.structure_selection import service",
        "import app.services.relative_conformer_energy_other",
        "from ..scientific import x",
    ],
)
def test_unrelated_imports_are_not_flagged(line):
    assert imports_the_service(line, "app.api.routes.scientific") is False


def _module_package(path: Path) -> str:
    rel = path.relative_to(APP.parent).with_suffix("")
    return ".".join(rel.parts[:-1])


def test_a_route_that_imports_the_service_needs_the_codes_to_be_request_reachable():
    files = sorted(ROUTES.rglob("*.py"))
    assert len(files) > 20  # the scan covers the routes, not an empty directory
    importers = [str(f.relative_to(ROUTES)) for f in files if imports_the_service(f.read_text(), _module_package(f))]
    entries = [e for e in CATALOGUE if e.code in CODES]
    assert {e.code for e in entries} == CODES
    guarded = [e.code for e in entries if e.reach is Reach.guard]
    assert not (importers and guarded), (
        f"{importers} import {PACKAGE} while {guarded} are still Reach.guard in app/api/code_catalogue.py: "
        "flip them to Reach.request and regenerate the api vocabulary and producer contract."
    )
