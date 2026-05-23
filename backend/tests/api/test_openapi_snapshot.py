"""OpenAPI golden snapshot contract test.

Freezes the generated ``/openapi.json`` schema in a reviewable
golden file at ``backend/tests/api/golden/openapi.json``. Any
intentional change to a path, request/response schema, parameter,
enum, or operation id surfaces as a diff against the golden file —
unintended drift fails the test.

To regenerate the golden after an *intentional* schema change::

    UPDATE_OPENAPI_GOLDEN=1 conda run -n tckdb_env pytest \\
        tests/api/test_openapi_snapshot.py

Review the resulting diff carefully before committing.

Why path-presence tests are not enough: the existing
``tests/api/scientific/test_api_openapi.py`` checks only that a few
expected paths exist. It cannot catch field renames, response-shape
changes, parameter changes, or enum value drift on routes that
*are* present. The golden snapshot closes that gap.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

GOLDEN_PATH = Path(__file__).parent / "golden" / "openapi.json"
_UPDATE_ENV = "UPDATE_OPENAPI_GOLDEN"


def _normalize(node: Any) -> Any:
    """Recursively sort dict keys for deterministic output.

    Arrays are intentionally left in generation order. Several OpenAPI
    arrays are semantically ordered — ``required`` field lists, path
    parameter lists, ``allOf`` / ``oneOf`` / ``anyOf`` member order,
    and ``enum`` value order (for ``IntEnum``-style ordinals). Sorting
    them would hide real changes and produce a misleadingly stable
    snapshot.
    """
    if isinstance(node, dict):
        return {key: _normalize(node[key]) for key in sorted(node.keys())}
    if isinstance(node, list):
        return [_normalize(item) for item in node]
    return node


def _serialize(schema: dict) -> str:
    """Render the normalized schema as a deterministic JSON string."""
    normalized = _normalize(schema)
    text = json.dumps(normalized, indent=2, sort_keys=True, ensure_ascii=False)
    return text + "\n"


def _fetch_openapi(client) -> dict:
    resp = client.get("/openapi.json")
    assert resp.status_code == 200, f"/openapi.json returned {resp.status_code}"
    return resp.json()


def test_openapi_matches_golden(client) -> None:
    generated = _serialize(_fetch_openapi(client))

    if os.environ.get(_UPDATE_ENV) == "1":
        GOLDEN_PATH.parent.mkdir(parents=True, exist_ok=True)
        GOLDEN_PATH.write_text(generated, encoding="utf-8")
        return

    assert GOLDEN_PATH.exists(), (
        f"Golden file missing: {GOLDEN_PATH}. "
        f"Create it with: {_UPDATE_ENV}=1 pytest "
        f"tests/api/test_openapi_snapshot.py"
    )

    expected = GOLDEN_PATH.read_text(encoding="utf-8")
    if expected != generated:
        # Surface the first ~40 differing lines so a CI log is
        # actionable without exploding into the entire schema.
        import difflib

        diff = "".join(
            difflib.unified_diff(
                expected.splitlines(keepends=True),
                generated.splitlines(keepends=True),
                fromfile="golden",
                tofile="generated",
                n=2,
            )
        )
        truncated = "\n".join(diff.splitlines()[:80])
        raise AssertionError(
            "OpenAPI schema drifted from "
            f"{GOLDEN_PATH.relative_to(Path(__file__).resolve().parents[2])}.\n"
            f"If the change is intentional, regenerate with:\n"
            f"    {_UPDATE_ENV}=1 pytest tests/api/test_openapi_snapshot.py\n"
            f"and review the resulting diff before committing.\n\n"
            f"First difference (truncated):\n{truncated}"
        )


def test_openapi_snapshot_is_normalized() -> None:
    """The committed golden file must already be in normalized form.

    Catches the case where a developer hand-edits the file or writes
    it via a non-canonical serializer; the next CI run would otherwise
    fail mysteriously even with no code change.
    """
    if not GOLDEN_PATH.exists():
        return  # the matches-golden test reports the missing file

    raw = GOLDEN_PATH.read_text(encoding="utf-8")
    reserialized = _serialize(json.loads(raw))
    assert raw == reserialized, (
        f"Golden file at {GOLDEN_PATH} is not in canonical form. "
        f"Regenerate with: {_UPDATE_ENV}=1 pytest "
        f"tests/api/test_openapi_snapshot.py"
    )
