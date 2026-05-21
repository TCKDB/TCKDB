"""CLI: generate a CCCBDB form-resolver queue from the parsed
``inchix.asp`` molecule catalog.

The catalog is an IDENTITY source — formula / name / InChI / InChIKey /
SMILES / CAS only. It is NOT a data-page-URL source: the
``raw_href`` field on each catalog entry is preserved verbatim by
the parser but never trusted as a fetchable data URL (see the
``CCCBDBCatalogEntry`` docstring).

This script converts a subset of catalog entries into a
``form_queue.json`` file shaped for the existing form resolver::

    python -m scripts.cccbdb_resolve_form_page \\
      --queue-file data/external/cccbdb/form_queue.json \\
      --output-dir data/external/cccbdb \\
      --selection-policy exact-match

It NEVER fetches CCCBDB. It NEVER writes to the database.

Exit codes:
    0 — queue written. Inspect the printed next-command line.
    2 — argument / catalog-load error.
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


_logger = logging.getLogger(__name__)


_NON_FILESYSTEM_SAFE_RE = re.compile(r"[^a-z0-9._-]+")


# ---------------------------------------------------------------------------
# Catalog loading
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CatalogEntry:
    """Loose, JSON-driven view of one ``inchix.asp`` row.

    The parsed-catalog JSON shape (from
    :class:`CCCBDBMoleculeCatalog`) uses ``entries`` as its row list,
    but maintainers occasionally hand-author a queue input under
    ``records`` (the shape the form resolver consumes). The loader
    accepts either key so a maintainer can pipe a stripped-down list
    of ``{formula, name, inchikey}`` triples in without going through
    the full parser.
    """

    formula: str | None = None
    name: str | None = None
    inchi: str | None = None
    inchikey: str | None = None
    smiles: str | None = None
    cas_number: str | None = None
    raw_href: str | None = None

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "CatalogEntry":
        return cls(
            formula=(d.get("formula") or None),
            name=(d.get("name") or None),
            inchi=(d.get("inchi") or None),
            inchikey=(d.get("inchikey") or None),
            smiles=(d.get("smiles") or None),
            cas_number=(d.get("cas_number") or d.get("casno") or None),
            raw_href=(d.get("raw_href") or None),
        )


def load_catalog_entries(path: Path) -> list[CatalogEntry]:
    """Parse a catalog JSON file into :class:`CatalogEntry` rows.

    Accepts either ``{"entries": [...]}`` (the parser's native shape)
    or ``{"records": [...]}`` (the queue-input shape). Raises
    :class:`ValueError` for any other top-level layout."""

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(
            f"failed to read catalog file {path}: {exc}"
        ) from exc
    if not isinstance(data, dict):
        raise ValueError(
            f"catalog file root must be an object, got {type(data).__name__}"
        )
    rows = data.get("entries")
    if rows is None:
        rows = data.get("records")
    if rows is None:
        raise ValueError(
            "catalog file must carry 'entries' or 'records' at the top level"
        )
    if not isinstance(rows, list):
        raise ValueError(
            "catalog 'entries'/'records' must be a list of objects"
        )
    return [CatalogEntry.from_dict(r) for r in rows if isinstance(r, dict)]


# ---------------------------------------------------------------------------
# species_key generation
# ---------------------------------------------------------------------------


def _slugify(text: str) -> str:
    """Return a filesystem-safe ASCII slug of ``text``.

    Lowercases, replaces runs of non-``[a-z0-9._-]`` with ``_``, and
    trims leading/trailing underscores. Empty results map to ``"x"``
    so callers always get a non-empty token.
    """

    if not text:
        return "x"
    folded = text.casefold().strip()
    cleaned = _NON_FILESYSTEM_SAFE_RE.sub("_", folded).strip("_")
    return cleaned or "x"


def _inchikey_prefix(inchikey: str | None) -> str | None:
    """Return the 14-char InChIKey prefix (the connectivity hash) or
    ``None`` when the input isn't a well-formed key."""

    if not inchikey:
        return None
    head = inchikey.split("-", 1)[0]
    head = head.upper().strip()
    if len(head) == 14 and head.isalpha():
        return head.lower()
    return None


def generate_species_key(
    entry: CatalogEntry, *, used: set[str]
) -> str:
    """Pick a stable, filesystem-safe ``species_key`` for ``entry``.

    Strategy (deterministic): use the name when present, otherwise
    the formula. If the resulting key collides with one already in
    ``used``, append the 14-char InChIKey prefix (when available)
    to break the tie. As a last resort, append a numeric suffix.
    The chosen key is added to ``used`` in-place.
    """

    base = _slugify(entry.name or entry.formula or entry.inchikey or "x")
    candidate = base
    if candidate not in used:
        used.add(candidate)
        return candidate
    prefix = _inchikey_prefix(entry.inchikey)
    if prefix is not None:
        candidate = f"{base}_{prefix}"
        if candidate not in used:
            used.add(candidate)
            return candidate
    i = 2
    while True:
        candidate = f"{base}_{i}"
        if candidate not in used:
            used.add(candidate)
            return candidate
        i += 1


# ---------------------------------------------------------------------------
# Filters
# ---------------------------------------------------------------------------


@dataclass
class QueueGenFilters:
    """Filter knobs applied to the catalog before queue generation."""

    formulas: tuple[str, ...] = ()
    name_contains: str | None = None
    require_inchikey: bool = False
    limit: int | None = None
    offset: int = 0

    def keep(self, entry: CatalogEntry) -> bool:
        """Return ``True`` if ``entry`` passes every active filter."""

        if not entry.formula and not entry.name:
            return False
        if self.formulas:
            if entry.formula not in self.formulas:
                return False
        if self.name_contains:
            haystack = (entry.name or "").casefold()
            if self.name_contains.casefold() not in haystack:
                return False
        if self.require_inchikey and not entry.inchikey:
            return False
        return True


# ---------------------------------------------------------------------------
# Queue generation
# ---------------------------------------------------------------------------


@dataclass
class QueueRecord:
    """One row of ``form_queue.json``."""

    species_key: str
    formula: str
    name: str | None
    inchi: str | None
    inchikey: str | None
    smiles: str | None
    cas_number: str | None
    target_kind: str
    entry_url: str

    def to_json(self) -> dict[str, Any]:
        out = {
            "species_key": self.species_key,
            "formula": self.formula,
            "target_kind": self.target_kind,
            "entry_url": self.entry_url,
        }
        if self.name is not None:
            out["name"] = self.name
        if self.inchi is not None:
            out["inchi"] = self.inchi
        if self.inchikey is not None:
            out["inchikey"] = self.inchikey
        if self.smiles is not None:
            out["smiles"] = self.smiles
        if self.cas_number is not None:
            out["cas_number"] = self.cas_number
        return out


@dataclass
class QueueGenResult:
    """Aggregate report from one queue generation."""

    catalog_path: str | None = None
    total_catalog_entries: int = 0
    skipped_filtered: int = 0
    skipped_no_formula: int = 0
    skipped_duplicate: int = 0
    written: int = 0
    output_path: str | None = None
    records: list[QueueRecord] = field(default_factory=list)


def generate_queue(
    entries: list[CatalogEntry],
    *,
    target_kind: str,
    entry_url: str,
    filters: QueueGenFilters,
) -> QueueGenResult:
    """Apply ``filters`` to ``entries`` and produce queue records.

    Dedup is keyed on ``(formula, name, inchikey)`` so the same
    catalog row appearing twice yields one queue record. ``offset``
    is applied AFTER filtering but BEFORE dedup-and-key generation;
    ``limit`` is applied AFTER dedup so the caller always gets at
    most ``limit`` distinct queue records.
    """

    result = QueueGenResult()
    result.total_catalog_entries = len(entries)

    kept: list[CatalogEntry] = []
    for entry in entries:
        if not filters.keep(entry):
            result.skipped_filtered += 1
            continue
        if not entry.formula:
            # FormQueueRecord requires formula; nothing else is
            # workable for the resolver.
            result.skipped_no_formula += 1
            continue
        kept.append(entry)

    if filters.offset > 0:
        kept = kept[filters.offset :]

    seen_dedup: set[tuple[str, str | None, str | None]] = set()
    used_keys: set[str] = set()
    for entry in kept:
        dedup_key = (entry.formula or "", entry.name, entry.inchikey)
        if dedup_key in seen_dedup:
            result.skipped_duplicate += 1
            continue
        seen_dedup.add(dedup_key)

        species_key = generate_species_key(entry, used=used_keys)
        record = QueueRecord(
            species_key=species_key,
            formula=entry.formula or "",
            name=entry.name,
            inchi=entry.inchi,
            inchikey=entry.inchikey,
            smiles=entry.smiles,
            cas_number=entry.cas_number,
            target_kind=target_kind,
            entry_url=entry_url,
        )
        result.records.append(record)
        result.written += 1
        if filters.limit is not None and result.written >= filters.limit:
            break

    return result


def write_queue_file(result: QueueGenResult, path: Path) -> None:
    """Serialize ``result.records`` to ``path`` in the shape the form
    resolver consumes (``{"records": [...]}``)."""

    payload = {
        "records": [r.to_json() for r in result.records],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=False) + "\n",
        encoding="utf-8",
    )
    result.output_path = str(path)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="cccbdb_generate_form_queue",
        description=(
            "Generate a form_queue.json for the CCCBDB form resolver "
            "from a parsed inchix.asp catalog. Does NOT crawl CCCBDB "
            "and NEVER uses inchix raw_href values as data-page URLs."
        ),
    )
    p.add_argument(
        "--catalog-json", type=Path, required=True,
        help=(
            "Path to a parsed-catalog JSON file (``{'entries': [...]}`` "
            "from CCCBDBMoleculeCatalog.model_dump_json) or a "
            "hand-authored ``{'records': [...]}`` file with the same "
            "identifier fields."
        ),
    )
    p.add_argument(
        "--output", type=Path, required=True,
        help="Where to write form_queue.json (parent dir auto-created).",
    )
    p.add_argument(
        "--target-kind", required=True,
        help=(
            "Form target_kind to set on every queue record "
            "(e.g. ``atomization_energy``)."
        ),
    )
    p.add_argument(
        "--entry-url", required=True,
        help=(
            "Form entry URL to set on every queue record "
            "(e.g. ``https://cccbdb.nist.gov/ea1x.asp``)."
        ),
    )
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--offset", type=int, default=0)
    p.add_argument(
        "--formula", action="append", default=None,
        help="Restrict to one or more formulas (repeatable).",
    )
    p.add_argument(
        "--name-contains", default=None,
        help="Keep only entries whose name contains this substring (case-insensitive).",
    )
    p.add_argument(
        "--require-inchikey", action="store_true",
        help="Skip entries that don't carry an InChIKey.",
    )
    return p


def _next_command(output_path: Path) -> str:
    """Return the suggested form-resolver invocation string the CLI
    prints after a successful generation."""

    return (
        "conda run -n tckdb_env python -m scripts.cccbdb_resolve_form_page \\\n"
        f"  --queue-file {output_path} \\\n"
        "  --output-dir data/external/cccbdb \\\n"
        "  --max-pages 3 \\\n"
        "  --sleep-seconds 15 \\\n"
        "  --selection-policy exact-match \\\n"
        "  --save-rejected-html"
    )


def main(argv: list[str] | None = None) -> int:
    parser = _build_arg_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    if not args.catalog_json.exists():
        _logger.error("catalog file does not exist: %s", args.catalog_json)
        return 2

    try:
        entries = load_catalog_entries(args.catalog_json)
    except ValueError as exc:
        _logger.error("%s", exc)
        return 2

    filters = QueueGenFilters(
        formulas=tuple(args.formula) if args.formula else (),
        name_contains=args.name_contains,
        require_inchikey=args.require_inchikey,
        limit=args.limit,
        offset=max(0, args.offset),
    )

    result = generate_queue(
        entries,
        target_kind=args.target_kind,
        entry_url=args.entry_url,
        filters=filters,
    )
    result.catalog_path = str(args.catalog_json)
    write_queue_file(result, args.output)

    _logger.info(
        "Wrote %d queue record(s) to %s "
        "(filtered=%d, no-formula=%d, duplicate=%d, catalog=%d total).",
        result.written,
        result.output_path,
        result.skipped_filtered,
        result.skipped_no_formula,
        result.skipped_duplicate,
        result.total_catalog_entries,
    )
    _logger.info("Next command:\n%s", _next_command(args.output))
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
