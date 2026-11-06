"""Guard: a route builds read models with ``build_read``, never ``<X>Read.model_validate``.

A direct ``model_validate`` on a database row raises pydantic's
``ValidationError`` (a ``ValueError``), which the API returns as a 422 that
blames the caller and echoes an object repr. ``app.api.read_models.build_read``
reports it as a 500 ``data_integrity_error`` instead.

A call that validates *caller* input and should stay a 422 goes in
``ALLOWED`` as ``(file name, line text) -> reason``. It is empty today: every
route call validated a row the server had read.
"""

from __future__ import annotations

import ast
from pathlib import Path

ROUTES = Path(__file__).resolve().parents[2] / "app" / "api" / "routes"

ALLOWED: dict[tuple[str, str], str] = {}


def _offenders() -> list[tuple[str, int, str]]:
    found = []
    for path in sorted(ROUTES.glob("*.py")):
        src = path.read_text()
        for node in ast.walk(ast.parse(src)):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "model_validate"
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id.endswith("Read")
            ):
                line = src.splitlines()[node.lineno - 1].strip()
                if (path.name, line) not in ALLOWED:
                    found.append((path.name, node.lineno, line))
    return found


def test_routes_do_not_call_read_model_validate_directly() -> None:
    assert ROUTES.is_dir()
    assert not _offenders(), (
        "use app.api.read_models.build_read for server data (or add a reasoned "
        f"ALLOWED entry for caller input): {_offenders()}"
    )


def test_the_guard_sees_the_pattern(tmp_path, monkeypatch) -> None:
    """Mutation pin: the scan finds a direct call when one exists."""
    (tmp_path / "x.py").write_text("def f(r):\n    return FooRead.model_validate(r)\n")
    monkeypatch.setattr(__import__(__name__, fromlist=["x"]), "ROUTES", tmp_path)
    assert _offenders() == [("x.py", 2, "return FooRead.model_validate(r)")]
