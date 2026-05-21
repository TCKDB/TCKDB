"""CLI: build ``MolecularPropertyObservationCreate`` payloads from the
form resolver's parsed/form_*.json archive.

Reads every ``parsed/form_<target_kind>_<species_key>_<sha>.json`` file
under ``--archive-dir`` and emits, per supported target_kind, a JSON
file with workflow-ready payloads plus an aggregate ``summary.json``.

Never writes to the database. Never fetches the network.

Output layout::

    form_payloads_dryrun/
      summary.json
      atomization_energy.json

Each per-target file::

    {
      "target_kind": "atomization_energy",
      "parsed_file_count": <int>,
      "payload_count": <int>,
      "invalid_payload_count": <int>,
      "warnings": [...],
      "payloads": [...]
    }

Health gate:

* ``healthy`` — every supported target had at least one workflow-ready
  payload, or had no parsed files at all (nothing to gate on).
* ``unhealthy`` — at least one supported target had ``parsed_file_count
  > 0`` but ``payload_count == 0``. This mirrors the flat
  property-table dry-run's gate semantics.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from app.importers.cccbdb.form_payload_builder import (
    build_atomization_energy_payloads_from_form_result,
    load_parsed_form_result,
)
from app.schemas.entities.molecular_property_observation import (
    MolecularPropertyObservationCreate,
)


_logger = logging.getLogger(__name__)


# Supported target_kinds that have a per-target builder. Adding new
# kinds requires adding a corresponding ``build_*_from_form_result``
# function and wiring it into ``_TARGET_BUILDERS``.
_TARGET_BUILDERS = {
    "atomization_energy": build_atomization_energy_payloads_from_form_result,
}


@dataclass
class TargetDryRunResult:
    """One ``target_kind``'s dry-run outcome."""

    target_kind: str
    parsed_file_count: int = 0
    payload_count: int = 0
    invalid_payload_count: int = 0
    warnings: list[str] = field(default_factory=list)
    payloads: list[dict[str, Any]] = field(default_factory=list)

    def to_json(self) -> dict[str, Any]:
        return {
            "target_kind": self.target_kind,
            "parsed_file_count": self.parsed_file_count,
            "payload_count": self.payload_count,
            "invalid_payload_count": self.invalid_payload_count,
            "warnings": list(self.warnings),
            "payloads": list(self.payloads),
        }

    def is_healthy(self) -> bool:
        """True when the target either had no parsed files at all, or
        produced at least one workflow-ready payload."""

        if self.parsed_file_count == 0:
            return True
        return self.payload_count > 0

    def health_reason(self) -> str | None:
        if self.is_healthy():
            return None
        return (
            f"{self.parsed_file_count} parsed file(s) but 0 payloads emitted"
        )


@dataclass
class FormPayloadDryRunSummary:
    """Aggregate dry-run report."""

    target_count: int = 0
    total_parsed_files: int = 0
    total_payload_count: int = 0
    total_invalid_payload_count: int = 0
    total_warning_count: int = 0
    health: str = "healthy"
    created_at: str = ""
    per_target: list[dict[str, Any]] = field(default_factory=list)
    health_summary: dict[str, str] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        return {
            "created_at": self.created_at,
            "target_count": self.target_count,
            "total_parsed_files": self.total_parsed_files,
            "total_payload_count": self.total_payload_count,
            "total_invalid_payload_count": self.total_invalid_payload_count,
            "total_warning_count": self.total_warning_count,
            "health": self.health,
            "per_target": list(self.per_target),
            "health_summary": dict(self.health_summary),
        }


def _discover_parsed_files(
    archive_dir: Path, target_kind: str
) -> list[Path]:
    """Find every ``parsed/form_<target_kind>_*.json`` file under
    ``archive_dir``."""

    parsed_dir = archive_dir / "parsed"
    if not parsed_dir.is_dir():
        return []
    return sorted(parsed_dir.glob(f"form_{target_kind}_*.json"))


def run_form_payload_dryrun(
    *,
    archive_dir: Path,
    output_dir: Path,
    target_kinds: tuple[str, ...] | None = None,
) -> FormPayloadDryRunSummary:
    """Execute the form-payload dry-run.

    :param archive_dir: CCCBDB archive root (containing ``parsed/``).
    :param output_dir: Where to write per-target JSON + summary.json.
    :param target_kinds: Restrict to a subset of supported target_kinds.
        ``None`` runs every supported kind.
    """

    if target_kinds is None:
        target_kinds = tuple(_TARGET_BUILDERS.keys())

    output_dir.mkdir(parents=True, exist_ok=True)
    summary = FormPayloadDryRunSummary(
        created_at=datetime.now(timezone.utc).isoformat(),
    )

    for target_kind in target_kinds:
        if target_kind not in _TARGET_BUILDERS:
            _logger.warning(
                "unsupported target_kind %r; skipping", target_kind
            )
            continue
        result = _run_one_target(
            target_kind=target_kind,
            archive_dir=archive_dir,
            output_dir=output_dir,
        )
        summary.per_target.append(result.to_json())
        summary.target_count += 1
        summary.total_parsed_files += result.parsed_file_count
        summary.total_payload_count += result.payload_count
        summary.total_invalid_payload_count += result.invalid_payload_count
        summary.total_warning_count += len(result.warnings)
        health = "healthy" if result.is_healthy() else "unhealthy"
        summary.health_summary[target_kind] = health

    summary.health = (
        "unhealthy"
        if "unhealthy" in summary.health_summary.values()
        else "healthy"
    )

    summary_path = output_dir / "summary.json"
    summary_path.write_text(
        json.dumps(summary.to_json(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return summary


def _run_one_target(
    *,
    target_kind: str,
    archive_dir: Path,
    output_dir: Path,
) -> TargetDryRunResult:
    result = TargetDryRunResult(target_kind=target_kind)
    parsed_files = _discover_parsed_files(archive_dir, target_kind)
    result.parsed_file_count = len(parsed_files)
    builder = _TARGET_BUILDERS[target_kind]

    for path in parsed_files:
        try:
            parsed_file = load_parsed_form_result(path)
        except (ValueError, json.JSONDecodeError) as exc:
            result.warnings.append(
                f"{path.name}: failed to load ({type(exc).__name__}: {exc})"
            )
            continue

        try:
            build_results = builder(
                parsed_file.table,
                selection_metadata=parsed_file.selection_metadata,
                resolver_strategy=parsed_file.resolver_strategy,
                species_key=parsed_file.species_key,
            )
        except Exception as exc:  # noqa: BLE001
            result.warnings.append(
                f"{path.name}: builder raised "
                f"{type(exc).__name__}: {exc}"
            )
            continue

        for br in build_results:
            if br.payload is None:
                for w in br.warnings:
                    result.warnings.append(
                        f"{path.name} row {br.row_index}: {w}"
                    )
                continue
            try:
                payload_json = br.payload.model_dump(mode="json")
                MolecularPropertyObservationCreate.model_validate(payload_json)
            except ValidationError as exc:
                result.invalid_payload_count += 1
                result.warnings.append(
                    f"{path.name} row {br.row_index}: invalid payload "
                    f"({type(exc).__name__}: "
                    f"{exc.errors()[0].get('msg', '?')!r})"
                )
                continue
            result.payloads.append(payload_json)
            result.payload_count += 1
            for w in br.warnings:
                result.warnings.append(
                    f"{path.name} row {br.row_index}: {w}"
                )

    target_path = output_dir / f"{target_kind}.json"
    target_path.write_text(
        json.dumps(result.to_json(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return result


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="cccbdb_form_payload_dryrun",
        description=(
            "Build MolecularPropertyObservationCreate payloads from the "
            "CCCBDB form-resolver's parsed/form_*.json archive. Never "
            "writes to the database."
        ),
    )
    p.add_argument(
        "--archive-dir", type=Path, required=True,
        help="CCCBDB archive root (the form-resolver --output-dir).",
    )
    p.add_argument(
        "--output-dir", type=Path, required=True,
        help="Where to write per-target JSON + summary.json.",
    )
    p.add_argument(
        "--target-kind", action="append", default=None,
        help=(
            "Restrict to a supported target_kind. May be repeated. "
            "Defaults to every supported kind."
        ),
    )
    return p


def main(argv: list[str] | None = None) -> int:
    """CLI entry point.

    Exit codes:
        0 — dry-run finished. Per-row builder warnings and invalid
            payloads do NOT cause a nonzero exit; inspect summary.json.
        2 — argument / configuration error.
    """

    parser = _build_arg_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    if not args.archive_dir.exists():
        _logger.error(
            "archive directory does not exist: %s", args.archive_dir
        )
        return 2

    target_kinds = (
        tuple(args.target_kind) if args.target_kind else None
    )

    summary = run_form_payload_dryrun(
        archive_dir=args.archive_dir,
        output_dir=args.output_dir,
        target_kinds=target_kinds,
    )
    _logger.info(
        "Form-payload dry-run: %d target(s), %d parsed files, "
        "%d payloads, %d invalid, health=%s",
        summary.target_count,
        summary.total_parsed_files,
        summary.total_payload_count,
        summary.total_invalid_payload_count,
        summary.health,
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
