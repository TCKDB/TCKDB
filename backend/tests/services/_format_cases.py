"""Format blocks every manifest reader must refuse.

A manifest records the document shape it is in a root ``format`` block, ``{"name": ..., "version": ...}``. The readers
look the recorded pair up in a ``VersionRegistry`` that matches the name and an exact ``int``. The old readers tested
membership of a frozenset (or ``!=``), so ``2.0`` equalled ``2`` and ``True`` equalled ``1``; those, text, a missing
version, another engine's name, extra keys, the old bare-integer shape and a missing block are all refused. A
deliberate tightening, pinned here.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any


def bad_format_versions(current: int) -> list:
    return [None, str(current), float(current), True, False, current + 1000]


def bad_format_manifests(manifest: dict[str, Any], current: int) -> Iterator[dict[str, Any]]:
    """Copies of *manifest* whose ``format`` block is wrong in every way a reader must refuse."""
    fmt = manifest["format"]
    for bad in bad_format_versions(current):
        yield {**manifest, "format": {**fmt, "version": bad}}
    yield {**manifest, "format": {**fmt, "name": "some_other_manifest"}}
    yield {**manifest, "format": {"name": fmt["name"]}}
    yield {**manifest, "format": {**fmt, "extra": 1}}
    yield {**manifest, "format": fmt["version"]}
    yield {k: v for k, v in manifest.items() if k != "format"}
