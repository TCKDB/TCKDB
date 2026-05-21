"""I/O helpers: load CCCBDB ``MolecularPropertyObservationCreate``
payloads from both dry-run output directories.

The flat property-table lane writes ``payloads_dryrun/*.json`` (one
file per ``property_kind``); the form-result lane writes
``form_payloads_dryrun/*.json`` (one file per supported
``target_kind``). Both files share the same per-target shape:

    {
      "property_kind" | "target_kind": "...",
      ...,
      "payloads": [{...}, ...]
    }

This module returns the raw payload dicts paired with their source
path so the import service can validate, resolve identity, and
record disposition without re-coupling to the lane-specific scripts.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator


_LANE_FLAT = "flat_property_table"
_LANE_FORM = "form_result"


@dataclass(frozen=True)
class LoadedPayload:
    """One payload-dict + source provenance.

    ``payload`` is the raw JSON dict the dry-run wrote — validation
    is the caller's job.
    """

    lane: str
    target_kind: str
    source_path: Path
    payload: dict


def _iter_target_files(directory: Path) -> Iterator[Path]:
    """Yield every ``*.json`` file under ``directory`` except
    ``summary.json``. ``directory`` is allowed to not exist (returns
    nothing)."""

    if not directory.is_dir():
        return
    for path in sorted(directory.iterdir()):
        if not path.is_file() or path.suffix != ".json":
            continue
        if path.name == "summary.json":
            continue
        yield path


def _load_target_file(path: Path, lane: str) -> list[LoadedPayload]:
    """Read one per-target JSON file and yield :class:`LoadedPayload`
    entries. Robust to missing ``payloads`` lists (returns ``[]``)."""

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(
            f"failed to read payload file {path}: {exc}"
        ) from exc
    payloads = data.get("payloads") or []
    if not isinstance(payloads, list):
        raise ValueError(
            f"payload file {path} has non-list 'payloads' field"
        )
    target_kind = (
        data.get("property_kind")
        or data.get("target_kind")
        or path.stem
    )
    return [
        LoadedPayload(
            lane=lane,
            target_kind=target_kind,
            source_path=path,
            payload=p,
        )
        for p in payloads
        if isinstance(p, dict)
    ]


def load_payloads(
    *,
    flat_payload_dir: Path | None = None,
    form_payload_dir: Path | None = None,
) -> list[LoadedPayload]:
    """Load every payload from the two CCCBDB dry-run lanes.

    :param flat_payload_dir: Directory written by
        ``scripts.cccbdb_property_payload_dryrun`` — one ``*.json`` per
        ``property_kind``. ``None`` skips this lane.
    :param form_payload_dir: Directory written by
        ``scripts.cccbdb_form_payload_dryrun`` — one ``*.json`` per
        ``target_kind``. ``None`` skips this lane.
    :returns: All :class:`LoadedPayload` entries in lexicographic
        per-file order, flat lane first then form lane. The two lanes
        share the same payload shape (validated against
        :class:`MolecularPropertyObservationCreate` by the import
        service) so the caller can iterate them uniformly.
    """

    out: list[LoadedPayload] = []
    if flat_payload_dir is not None:
        for path in _iter_target_files(flat_payload_dir):
            out.extend(_load_target_file(path, _LANE_FLAT))
    if form_payload_dir is not None:
        for path in _iter_target_files(form_payload_dir):
            out.extend(_load_target_file(path, _LANE_FORM))
    return out


def filter_payloads_by_property_kind(
    payloads: Iterable[LoadedPayload],
    property_kinds: Iterable[str] | None,
) -> list[LoadedPayload]:
    """Restrict ``payloads`` to entries whose payload reports one of
    ``property_kinds``. ``None`` is a no-op (returns everything)."""

    if not property_kinds:
        return list(payloads)
    allowed = {k for k in property_kinds}
    return [
        p for p in payloads
        if p.payload.get("property_kind") in allowed
    ]


__all__ = [
    "LoadedPayload",
    "filter_payloads_by_property_kind",
    "load_payloads",
]
