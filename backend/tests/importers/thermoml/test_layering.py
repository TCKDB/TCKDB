"""Layering invariant: the ``thermoml`` importer package must not
import from ``app.db`` or ``app.services``, except the one sanctioned
wire-schema import (``app.schemas.entities.molecular_property_observation``,
which itself imports enums from ``app.db.models.common`` -- pure
Python constants, not ORM/session machinery).

This is the *inverse* of
``backend/tests/services/test_cccbdb_molecular_property_import.py::test_service_does_not_import_parsers_or_fetchers``:
that test keeps DB-facing services from pulling in parser internals;
this one keeps the importer package (which runs against arbitrary
third-party bytes, no DB session available) from reaching into
``app.db`` session/engine machinery or ``app.services`` business logic.
Persistence is Phase C-E3, a different package.

PINNED EXCEPTION, minimized 2026-09-20: ``mapping.py`` needs four
enums -- ``MolecularPropertyKind`` (via the wire schema import above),
``ObservedStateBasis``, ``ObservedUncertaintyAssessor`` and
``ObservedUncertaintyKind``. Of those, only ``ScientificOriginKind``
has a wire-package mirror today (``tckdb_schemas.enums``, checked
byte-for-byte against ``app.db.models.common`` by
``backend/tests/schemas/test_tckdb_schemas_enum_drift.py``), so
``mapping.py`` imports THAT one enum from ``tckdb_schemas.enums``
instead of ``app.db.models.common`` -- see ``mapping.py``'s own module
docstring. The other three enums have no wire mirror yet, so
``app.db.models.common`` stays in ``ALLOWED_APP_DB_IMPORTS`` for them.
This is the same shape of exception the CCCBDB precedent documents
(a named, minimal, tested allowlist entry, not a blanket carve-out):
when a wire mirror is added for the remaining three enums, this
allowlist should shrink to match, the same way it would grow if a
wire mirror it depends on were ever removed.
"""

from __future__ import annotations

import ast
from pathlib import Path

THERMOML_PKG = Path(__file__).parents[3] / "app" / "importers" / "thermoml"

#: The one exception: the wire schema module the mapper builds
#: payloads against, plus the three ``app.db.models.common`` enums
#: with no wire mirror yet (see the module docstring above).
ALLOWED_APP_DB_IMPORTS = {"app.db.models.common"}
ALLOWED_APP_SERVICES_IMPORTS: set[str] = set()


def _imported_module_names(source: str) -> set[str]:
    tree = ast.parse(source)
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def _all_thermoml_source_files() -> list[Path]:
    files = sorted(THERMOML_PKG.rglob("*.py"))
    assert files, "no .py files found under app/importers/thermoml"
    return files


def test_package_has_python_files():
    assert _all_thermoml_source_files()


def test_no_app_db_imports_except_the_enum_module():
    offenders: dict[str, set[str]] = {}
    for path in _all_thermoml_source_files():
        names = _imported_module_names(path.read_text(encoding="utf-8"))
        bad = {
            n
            for n in names
            if (n == "app.db" or n.startswith("app.db."))
            and n not in ALLOWED_APP_DB_IMPORTS
        }
        if bad:
            offenders[str(path.relative_to(THERMOML_PKG))] = bad
    assert offenders == {}, f"forbidden app.db imports: {offenders}"


def test_no_app_services_imports():
    offenders: dict[str, set[str]] = {}
    for path in _all_thermoml_source_files():
        names = _imported_module_names(path.read_text(encoding="utf-8"))
        bad = {
            n
            for n in names
            if (n == "app.services" or n.startswith("app.services."))
            and n not in ALLOWED_APP_SERVICES_IMPORTS
        }
        if bad:
            offenders[str(path.relative_to(THERMOML_PKG))] = bad
    assert offenders == {}, f"forbidden app.services imports: {offenders}"


def test_allowed_app_db_module_is_actually_used():
    """Guards against the allowlist silently going stale: if nothing
    under the package imports ``app.db.models.common`` any more, the
    exception should be removed, not left as dead cover."""

    found = False
    for path in _all_thermoml_source_files():
        if "app.db.models.common" in _imported_module_names(
            path.read_text(encoding="utf-8")
        ):
            found = True
            break
    assert found, (
        "ALLOWED_APP_DB_IMPORTS carries an entry nothing uses any more"
    )
