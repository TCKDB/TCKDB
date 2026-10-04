"""The producer contract: what TCKDB accepts today, shipped with the wire models.

A producer adapter (a program that turns some other tool's output into TCKDB
deposits) should read this before changing any mapping::

    python -m tckdb_schemas.contract --print           # the whole contract
    python -m tckdb_schemas.contract --since 0.49.0    # what changed since a version
    python -m tckdb_schemas.contract --schemas         # the JSON Schema files

and validate every payload it builds against the JSON Schemas in its tests::

    from tckdb_schemas import contract
    schema = contract.json_schema("ThermoUploadRequest")

Every file in this directory except this module and ``__main__`` is
**generated** by ``backend/scripts/generate_producer_contract.py`` from the
live API routes, upload models and refusal-code catalogue, and CI refuses a
commit whose copy is stale. Do not edit them by hand.
"""

from __future__ import annotations

import json
import re
from importlib import resources
from pathlib import Path
from typing import Any

__all__ = [
    "CHANGELOG_FILENAME",
    "CONTRACT_FILENAME",
    "changelog_path",
    "changes_since",
    "json_schema",
    "markdown",
    "path",
    "schema_names",
    "schema_path",
]

#: The markdown contract's file name inside this package.
CONTRACT_FILENAME = "PRODUCER_CONTRACT.md"
#: The full package changelog, shipped beside the contract.
CHANGELOG_FILENAME = "CHANGELOG.md"
_SCHEMA_DIR = "schemas"
_SCHEMA_SUFFIX = ".schema.json"


def _root() -> Path:
    # Wheels are installed unpacked, so the package directory is a real
    # directory and a filesystem path is what a reader (or an agent told to
    # open the file) can use.
    return Path(str(resources.files(__name__)))


def path() -> Path:
    """Filesystem path of ``PRODUCER_CONTRACT.md`` in the installed package."""
    return _root() / CONTRACT_FILENAME


def markdown() -> str:
    """The contract's markdown text."""
    return path().read_text(encoding="utf-8")


def schema_names() -> list[str]:
    """Names of the shipped JSON Schemas, one per upload payload model."""
    directory = _root() / _SCHEMA_DIR
    return sorted(p.name[: -len(_SCHEMA_SUFFIX)] for p in directory.glob(f"*{_SCHEMA_SUFFIX}"))


def schema_path(name: str) -> Path:
    """Path of the JSON Schema for the payload model ``name``.

    ``name`` is the model's class name as the contract prints it, e.g.
    ``"ThermoUploadRequest"``; a trailing ``.schema.json`` is accepted.
    """
    stem = name[: -len(_SCHEMA_SUFFIX)] if name.endswith(_SCHEMA_SUFFIX) else name
    candidate = _root() / _SCHEMA_DIR / f"{stem}{_SCHEMA_SUFFIX}"
    if not candidate.is_file():
        raise KeyError(f"no shipped JSON Schema named {name!r}; known: {', '.join(schema_names())}")
    return candidate


def json_schema(name: str) -> dict[str, Any]:
    """The JSON Schema (draft 2020-12) for the payload model ``name``."""
    loaded: dict[str, Any] = json.loads(schema_path(name).read_text(encoding="utf-8"))
    return loaded


def _version_key(version: str) -> tuple[int, ...]:
    parts = re.findall(r"\d+", version)
    if not parts:
        raise ValueError(f"not a version: {version!r}")
    return tuple(int(part) for part in parts)


def changelog_path() -> Path:
    """Filesystem path of the package changelog shipped beside the contract (every entry)."""
    return _root() / CHANGELOG_FILENAME


def changes_since(version: str, text: str | None = None) -> str:
    """The changelog entries strictly newer than ``version``, newest first.

    Read from the changelog shipped in the package (the contract itself prints only the newest
    entries), so this works from an installed wheel with no source tree. ``text`` is changelog
    markdown: one ``## <version> - <date>`` heading per entry.
    """
    text = changelog_path().read_text(encoding="utf-8") if text is None else text
    floor = _version_key(version)
    kept: list[str] = []
    for block in re.split(r"(?m)^## ", text)[1:]:
        heading = block.split("\n", 1)[0].strip()
        if _version_key(heading.split()[0]) > floor:
            kept.append("### " + block.rstrip() + "\n")
    return "\n".join(kept)
