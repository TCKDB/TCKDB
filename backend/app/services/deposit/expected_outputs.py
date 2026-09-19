"""Deterministic rendering of manuscript-number generators.

A *generator* is a callable ``(Session) -> dict`` registered by name in
``backend/scripts/paper/registry.py``. Each one answers one ``[DATA]`` claim
in the manuscript by reading the (restored) database through the ORM. This
module turns a generator's dict into the two files the deposit ships per
generator -- ``<name>.json`` in canonical JSON and ``<name>.md`` as tables --
and enforces the determinism rules every generator must obey:

* collections are ordered by public ref (the generator's job; the renderer
  never sorts rows for it, so a generator that iterates a set is caught by
  the byte comparison, not hidden);
* no wall-clock values: a generator that stamps ``now()`` produces different
  bytes on the source instance and on the reproducer's restored database,
  which is exactly what the deposit's byte comparison refuses;
* ``Decimal`` is rendered as a string, ``datetime``/``date`` as ISO 8601,
  ``Enum`` as its value. Any other non-JSON type is an error, never
  ``str()``-ed silently.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Mapping
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.services.release.artifacts import canonical_json

Generator = Callable[[Session], dict[str, Any]]


def normalize(value: Any, *, path: str = "$") -> Any:
    """Return ``value`` with only JSON-representable, deterministic leaves."""
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        return value
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        return value.isoformat(timespec="microseconds")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Mapping):
        out: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError(f"{path}: mapping keys must be strings, got {type(key).__name__}")
            out[key] = normalize(item, path=f"{path}.{key}")
        return out
    if isinstance(value, (list, tuple)):
        return [normalize(item, path=f"{path}[{index}]") for index, item in enumerate(value)]
    raise TypeError(f"{path}: {type(value).__name__} is not renderable; convert it in the generator")


def render_json(payload: Mapping[str, Any]) -> bytes:
    """Canonical JSON bytes (sorted keys, compact, UTF-8, trailing newline)."""
    return canonical_json(normalize(dict(payload))).encode("utf-8") + b"\n"


def _cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (list, dict)):
        return canonical_json(value)
    return str(value).replace("|", "\\|").replace("\n", " ")


def _table(rows: list[dict[str, Any]]) -> list[str]:
    columns = sorted({key for row in rows for key in row})
    lines = ["| " + " | ".join(columns) + " |", "|" + "|".join(" --- " for _ in columns) + "|"]
    for row in rows:
        lines.append("| " + " | ".join(_cell(row.get(column)) for column in columns) + " |")
    return lines


def render_markdown(name: str, payload: Mapping[str, Any]) -> bytes:
    """A Markdown rendering of the same normalized payload, for reading."""
    data = normalize(dict(payload))
    lines = [f"# {name}", ""]
    for key in sorted(data):
        value = data[key]
        if isinstance(value, list) and value and all(isinstance(item, dict) for item in value):
            lines.append(f"## {key}")
            lines.append("")
            lines.extend(_table(value))
            lines.append("")
        elif isinstance(value, dict):
            lines.append(f"## {key}")
            lines.append("")
            lines.append("| key | value |")
            lines.append("| --- | --- |")
            for sub_key in sorted(value):
                lines.append(f"| {sub_key} | {_cell(value[sub_key])} |")
            lines.append("")
        else:
            lines.append(f"- {key}: {_cell(value)}")
    if lines[-1] != "":
        lines.append("")
    return "\n".join(lines).encode("utf-8")


def write_expected_outputs(
    session: Session,
    output_dir: Path,
    generators: Mapping[str, Generator],
) -> dict[str, str]:
    """Run every generator and write ``<name>.json`` and ``<name>.md``.

    Returns ``{relative path: sha256}`` for what was written, in path order.
    Refuses an empty registry: a deposit with no generators would bind no
    number to anything and pass every comparison vacuously.
    """
    if not generators:
        raise ValueError("no generators registered; refusing to write an empty expected_outputs/")
    output_dir.mkdir(parents=True, exist_ok=True)
    digests: dict[str, str] = {}
    for name in sorted(generators):
        payload = generators[name](session)
        if not isinstance(payload, Mapping):
            raise TypeError(f"generator {name!r} returned {type(payload).__name__}, expected a mapping")
        for suffix, content in (
            (".json", render_json(payload)),
            (".md", render_markdown(name, payload)),
        ):
            target = output_dir / f"{name}{suffix}"
            target.write_bytes(content)
            digests[target.name] = hashlib.sha256(content).hexdigest()
    return digests


__all__ = [
    "Generator",
    "normalize",
    "render_json",
    "render_markdown",
    "write_expected_outputs",
]
