"""Backfill calculation_parameter rows from already-stored input artifacts.

Operational entry point for parsing input artifacts (Gaussian ``.gjf`` /
ORCA ``.in``) that pre-date the artifact-upload extraction hook, or for
reparsing after a parser-version bump. Mirrors the upload-side hook by
calling the same
:func:`app.services.calculation_parameter_extraction.extract_and_store_calculation_parameters`
service, so behavior stays consistent across normal uploads and bulk
backfill.

Usage::

    # Single calculation by id.
    python backend/scripts/extract_calculation_parameters.py --calculation-id 123

    # Every calc that has an input artifact and zero parser-derived rows.
    python backend/scripts/extract_calculation_parameters.py --all-missing

    # Force reparse even if parser rows already exist (replace-all on
    # source='parser' is performed by the underlying service).
    python backend/scripts/extract_calculation_parameters.py --all-missing --force

    # Bound work by --limit.
    python backend/scripts/extract_calculation_parameters.py --all-missing --limit 50

    # Inspect the candidate set and the artifact picks without writing.
    python backend/scripts/extract_calculation_parameters.py --all-missing --dry-run

    # Stop on the first failure (default is continue-on-error).
    python backend/scripts/extract_calculation_parameters.py --all-missing --fail-fast

    # Operator override: force a specific artifact onto a single calc.
    python backend/scripts/extract_calculation_parameters.py \\
        --calculation-id 123 --artifact-id 456

Artifact-selection policy
-------------------------

When multiple input artifacts exist for one calculation, the backfill
parser uses the **earliest input artifact by ``calculation_artifact.id``**
(``ORDER BY id ASC LIMIT 1``).

Rationale: the first input artifact is the one most likely to represent
the file that actually drove the calculation in normal ARC/TCKDB uploads.
Later input artifacts are ambiguous without an explicit
supersession/replacement model — they may be corrections, debug files,
annotations, or accidental duplicates. Until artifact replacement is
modeled, deterministic earliest-upload wins is safer than
last-write-wins.

If an operator needs to point the parser at a different artifact, use
``--artifact-id`` (single-calculation mode only) to override the
selection explicitly. Do NOT change the ordering rule in the absence of
a real artifact-supersession model.

Exit codes
----------

* ``0`` — every selected calculation was processed (success or
  documented skip).
* ``1`` — at least one calculation failed and ``--fail-fast`` was set,
  or argument validation failed.
"""

from __future__ import annotations

import argparse
import logging
import sys
from dataclasses import dataclass, field
from typing import Optional

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.api.config import settings
from app.db.models.calculation import (
    Calculation,
    CalculationArtifact,
    CalculationParameter,
)
from app.db.models.common import ArtifactKind, ParameterSource
from app.services.calculation_parameter_extraction import (
    ParameterExtractionError,
    try_extract_parameters_from_input_artifact_row,
)

logger = logging.getLogger("extract_calculation_parameters")


# ---------------------------------------------------------------------------
# Selection
# ---------------------------------------------------------------------------


def _select_input_artifact(
    session: Session,
    calculation_id: int,
    *,
    artifact_id_override: int | None = None,
) -> CalculationArtifact | None:
    """Pick the input artifact to feed the parser for one calculation.

    Returns ``None`` if no input artifact exists. With
    ``artifact_id_override`` set, returns that specific row only if it
    is an input artifact attached to ``calculation_id`` — otherwise
    ``None``.

    Selection rule (no override): ``ORDER BY id ASC LIMIT 1`` over
    ``CalculationArtifact`` rows where ``kind = 'input'`` and
    ``calculation_id = calculation_id``. See module docstring for
    rationale.
    """

    if artifact_id_override is not None:
        row = session.get(CalculationArtifact, artifact_id_override)
        if row is None:
            return None
        if row.calculation_id != calculation_id:
            return None
        if row.kind is not ArtifactKind.input:
            return None
        return row

    return session.scalar(
        select(CalculationArtifact)
        .where(
            CalculationArtifact.calculation_id == calculation_id,
            CalculationArtifact.kind == ArtifactKind.input,
        )
        .order_by(CalculationArtifact.id.asc())
        .limit(1)
    )


def _calculations_with_input_and_no_parser_rows(
    session: Session, *, force: bool, limit: int | None
):
    """Yield calculation IDs eligible for backfill.

    Without ``--force``: a calc qualifies when it has at least one input
    artifact AND zero ``calculation_parameter`` rows with
    ``source='parser'``.

    With ``--force``: the parser-row exclusion is dropped — every calc
    with an input artifact qualifies, and the underlying replace-all
    semantics on the service wipe stale parser rows on reparse.
    """
    has_input = (
        select(CalculationArtifact.id)
        .where(
            CalculationArtifact.calculation_id == Calculation.id,
            CalculationArtifact.kind == ArtifactKind.input,
        )
        .exists()
    )
    has_parser_row = (
        select(CalculationParameter.id)
        .where(
            CalculationParameter.calculation_id == Calculation.id,
            CalculationParameter.source == ParameterSource.parser,
        )
        .exists()
    )

    stmt = select(Calculation.id).where(has_input)
    if not force:
        stmt = stmt.where(~has_parser_row)
    stmt = stmt.order_by(Calculation.id.asc())
    if limit is not None:
        stmt = stmt.limit(limit)

    return list(session.scalars(stmt).all())


# ---------------------------------------------------------------------------
# Per-calc processing
# ---------------------------------------------------------------------------


@dataclass
class _RunStats:
    candidates: int = 0
    succeeded: int = 0
    skipped_no_artifact: int = 0
    skipped_no_rows: int = 0
    failed: int = 0
    failures: list[tuple[int, str]] = field(default_factory=list)

    def report(self) -> str:
        lines = [
            f"candidates  : {self.candidates}",
            f"succeeded   : {self.succeeded}",
            f"no-artifact : {self.skipped_no_artifact}",
            f"empty-parse : {self.skipped_no_rows}",
            f"failed      : {self.failed}",
        ]
        if self.failures:
            lines.append("")
            lines.append("failures:")
            for cid, msg in self.failures:
                lines.append(f"  calculation_id={cid}: {msg}")
        return "\n".join(lines)


def _process_one(
    session: Session,
    calculation_id: int,
    *,
    artifact_id_override: int | None,
    dry_run: bool,
    stats: _RunStats,
) -> bool:
    """Process a single calculation. Returns True on success/skip.

    Returns False only when extraction raised — caller decides whether
    to keep going or short-circuit on ``--fail-fast``.
    """

    calculation = session.get(Calculation, calculation_id)
    if calculation is None:
        stats.failed += 1
        stats.failures.append((calculation_id, "calculation not found"))
        logger.error("calc id=%s not found", calculation_id)
        return False

    artifact = _select_input_artifact(
        session, calculation_id, artifact_id_override=artifact_id_override
    )
    if artifact is None:
        stats.skipped_no_artifact += 1
        if artifact_id_override is not None:
            logger.warning(
                "calc id=%s: --artifact-id %s did not match an input "
                "artifact attached to this calc",
                calculation_id,
                artifact_id_override,
            )
        else:
            logger.info(
                "calc id=%s: no input artifact to backfill from",
                calculation_id,
            )
        return True

    if dry_run:
        logger.info(
            "DRY-RUN calc id=%s: would parse artifact id=%s "
            "(kind=%s, filename=%s, sha256=%s)",
            calculation_id,
            artifact.id,
            artifact.kind.value,
            artifact.filename,
            artifact.sha256,
        )
        # Dry-run did no writes, so no commit/rollback needed; pending
        # transaction (if any) holds only SELECTs.
        return True

    try:
        rows = try_extract_parameters_from_input_artifact_row(
            session, calculation, artifact
        )
    except ParameterExtractionError as exc:
        # Defense-in-depth: the helper currently catches this, but if
        # that ever changes we still must not crash the loop unless
        # --fail-fast is set. Rollback discards any partial writes.
        session.rollback()
        stats.failed += 1
        stats.failures.append((calculation_id, str(exc)))
        logger.warning(
            "calc id=%s: extraction failed: %s", calculation_id, exc
        )
        return False
    except Exception as exc:
        session.rollback()
        stats.failed += 1
        stats.failures.append(
            (calculation_id, f"{type(exc).__name__}: {exc}")
        )
        logger.exception(
            "calc id=%s: unexpected failure during extraction",
            calculation_id,
        )
        return False

    if rows is None:
        # Helper failed cleanly without writing (unrecognised software,
        # storage read failure, etc.); nothing to commit or roll back.
        stats.skipped_no_artifact += 1
        return True

    if not rows:
        # Replace-all ran (deleted stale parser rows) but the parser
        # found nothing new — commit so the deletion sticks.
        session.commit()
        stats.skipped_no_rows += 1
        logger.info(
            "calc id=%s: parser returned no parameters from artifact id=%s",
            calculation_id,
            artifact.id,
        )
        return True

    session.commit()
    stats.succeeded += 1
    logger.info(
        "calc id=%s: persisted %d parameter rows from artifact id=%s",
        calculation_id,
        len(rows),
        artifact.id,
    )
    return True


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument(
        "--calculation-id",
        type=int,
        help="Backfill exactly this calculation id.",
    )
    target.add_argument(
        "--all-missing",
        action="store_true",
        help=(
            "Backfill every calculation that has an input artifact but "
            "no calculation_parameter rows with source='parser'."
        ),
    )
    parser.add_argument(
        "--artifact-id",
        type=int,
        default=None,
        help=(
            "Operator override: force the specified artifact id as the "
            "parser input. Only valid with --calculation-id and only "
            "applies if that artifact is an input attached to the calc."
        ),
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help=(
            "Reparse calculations that already have parser-derived "
            "parameter rows. The underlying service performs replace-all "
            "on source='parser' rows."
        ),
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Cap the number of calculations processed (with --all-missing).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "List candidate (calc_id, artifact_id) pairs without "
            "writing calculation_parameter rows or updating "
            "calculation.parameters_parser_version / "
            "parameters_extracted_at."
        ),
    )
    parser.add_argument(
        "--fail-fast",
        action="store_true",
        help="Stop on the first per-calculation failure.",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="DEBUG-level logging.",
    )
    return parser.parse_args(argv)


def _validate_args(args: argparse.Namespace) -> None:
    if args.artifact_id is not None and args.calculation_id is None:
        raise SystemExit(
            "--artifact-id is only valid together with --calculation-id "
            "(operator override for a single calc)."
        )
    if args.limit is not None and args.limit <= 0:
        raise SystemExit("--limit must be > 0")


def _configure_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )


def run_backfill(
    session: Session,
    *,
    calculation_id: int | None = None,
    all_missing: bool = False,
    artifact_id: int | None = None,
    force: bool = False,
    limit: int | None = None,
    dry_run: bool = False,
    fail_fast: bool = False,
) -> _RunStats:
    """Test-friendly entry point. ``main`` wraps this with engine + argparse.

    Caller owns the session; commits/rollbacks happen per calculation
    inside :func:`_process_one`.
    """

    if calculation_id is not None:
        calc_ids = [calculation_id]
    elif all_missing:
        calc_ids = _calculations_with_input_and_no_parser_rows(
            session, force=force, limit=limit
        )
    else:
        raise ValueError("Either calculation_id or all_missing must be set.")

    stats = _RunStats()
    stats.candidates = len(calc_ids)
    logger.info("selected %d candidate calculation(s)", stats.candidates)

    for cid in calc_ids:
        ok = _process_one(
            session,
            cid,
            artifact_id_override=artifact_id,
            dry_run=dry_run,
            stats=stats,
        )
        if not ok and fail_fast:
            logger.error("--fail-fast set; stopping after first failure")
            break

    return stats


def main(argv: Optional[list[str]] = None) -> int:
    args = _parse_args(argv)
    _validate_args(args)
    _configure_logging(args.verbose)

    if args.dry_run:
        logger.info("DRY-RUN mode: no calculation_parameter rows will be written.")

    engine = create_engine(settings.database_url)
    try:
        with Session(engine) as session:
            stats = run_backfill(
                session,
                calculation_id=args.calculation_id,
                all_missing=args.all_missing,
                artifact_id=args.artifact_id,
                force=args.force,
                limit=args.limit,
                dry_run=args.dry_run,
                fail_fast=args.fail_fast,
            )
    finally:
        engine.dispose()

    sys.stderr.write("\n" + stats.report() + "\n")
    if args.fail_fast and stats.failed > 0:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
