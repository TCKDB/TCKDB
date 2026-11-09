"""Re-run deposit checks that depend on later events and retire warnings that no longer hold."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session
from tckdb_schemas.upload_warning import UploadWarning

from app.db.models.calculation import Calculation, CalculationHessian
from app.db.models.common import CalculationType
from app.db.models.common import SubmissionRecordType as RT
from app.db.models.deposit_warning import RETIREMENT_REASONS, DepositWarning, DepositWarningRetirement
from app.services.calculation_resolution import (
    W_CONVERGED_OPT_NO_USABLE_ENERGY,
    collect_converged_opt_energy_warnings,
)
from app.services.deposit_warnings import _json_safe, _row, _Target, current_warnings, reset_deposit_notes

#: Codes the file-presence / single-point re-run decides from stored state alone.
RECHECKED_CODES = frozenset({W_CONVERGED_OPT_NO_USABLE_ENERGY})
HESSIAN_NOT_STORED_PREFIX = "hessian_not_stored_"


def _key(code: str, field: str, context: Any) -> tuple[str, str, str]:
    """A warning's identity for comparison: code, field and the facts it states (not its wording)."""
    facts = _json_safe(context) or {}
    return (code, field, repr(sorted(facts.items())))


def _retire(session: Session, rows: Iterable[DepositWarning], reason: str, by_user_id: int | None) -> None:
    """Insert retirement rows for ``rows``, leaving existing retirements alone."""
    assert reason in RETIREMENT_REASONS, reason
    values = [
        {"deposit_warning_id": r.id, "retired_reason_code": reason, "retired_by_user_id": by_user_id} for r in rows
    ]
    if values:
        session.execute(
            pg_insert(DepositWarningRetirement)
            .values(values)
            .on_conflict_do_nothing(constraint="uq_deposit_warning_retirement_warning")
        )
        session.flush()


def _insert_replacements(
    session: Session, submission_id: int | None, calculation_id: int, warnings: list[UploadWarning], written_by: int | None
) -> None:
    """Insert ``warnings`` on the calculation; a failure raises."""
    rows = [_row(submission_id, _Target(RT.calculation, calculation_id), w, written_by) for w in warnings]
    session.execute(
        pg_insert(DepositWarning).values(rows).on_conflict_do_nothing(constraint="uq_deposit_warning_identity")
    )
    session.flush()


def _is_stored_current(session: Session, calculation_id: int, warning_row: dict[str, Any]) -> bool:
    return (
        session.scalar(
            current_warnings(
                select(DepositWarning.id).where(
                    DepositWarning.record_type == RT.calculation,
                    DepositWarning.record_id == calculation_id,
                    DepositWarning.content_hash == warning_row["content_hash"],
                )
            ).limit(1)
        )
        is not None
    )


def recheck_calculation_warnings(
    session: Session, calculation_id: int, *, cause: str, submission_id: int | None, written_by: int | None
) -> list[UploadWarning]:
    """Return the warnings newly raised for ``calculation_id``, retiring stored ones that no longer hold."""
    fresh = [
        w
        for w in collect_converged_opt_energy_warnings(session, [calculation_id])
        if w.code in RECHECKED_CODES
    ]
    reset_deposit_notes(session)  # drop the producer notes the collector leaves
    stored = list(
        session.scalars(
            current_warnings(
                select(DepositWarning).where(
                    DepositWarning.record_type == RT.calculation,
                    DepositWarning.record_id == calculation_id,
                    DepositWarning.code.in_(RECHECKED_CODES),
                )
            ).order_by(DepositWarning.id)
        )
    )
    fresh_by_key = {_key(w.code, w.field, getattr(w, "context", None)): w for w in fresh}
    stored_keys = {_key(r.code, r.field, r.context) for r in stored}

    stale = [r for r in stored if _key(r.code, r.field, r.context) not in fresh_by_key]
    added = [w for k, w in fresh_by_key.items() if k not in stored_keys]

    # Store the replacement first; retire the old row only once the replacement is current.
    if added:
        _insert_replacements(session, submission_id, calculation_id, added, written_by)
    retire_as_superseded: list[DepositWarning] = []
    retire_as_cause: list[DepositWarning] = []
    for row in stale:
        still_warns = any(w.code == row.code for w in fresh)
        if not still_warns:
            retire_as_cause.append(row)
        elif all(
            _is_stored_current(session, calculation_id, _row(None, None, w, None))
            for w in fresh
            if w.code == row.code
        ):
            retire_as_superseded.append(row)
    _retire(session, retire_as_cause, cause, written_by)
    _retire(session, retire_as_superseded, "superseded_by_recheck", written_by)
    return added


def recheck_owner_opts_after_single_points(
    session: Session, single_point_ids: Iterable[int], *, written_by: int | None
) -> None:
    """Retire ``converged_opt_no_usable_energy`` on opts of the same owner as the given single points."""
    ids = list(dict.fromkeys(single_point_ids))
    if not ids:
        return
    owners = session.execute(
        select(Calculation.species_entry_id, Calculation.transition_state_entry_id).where(
            Calculation.id.in_(ids), Calculation.type == CalculationType.sp
        )
    ).all()
    species_ids = {o.species_entry_id for o in owners if o.species_entry_id is not None}
    ts_ids = {o.transition_state_entry_id for o in owners if o.transition_state_entry_id is not None}
    if not species_ids and not ts_ids:
        return
    owner_filter = []
    if species_ids:
        owner_filter.append(Calculation.species_entry_id.in_(species_ids))
    if ts_ids:
        owner_filter.append(Calculation.transition_state_entry_id.in_(ts_ids))
    opt_ids = select(Calculation.id).where(Calculation.type == CalculationType.opt, or_(*owner_filter))
    warned = session.scalars(
        current_warnings(
            select(DepositWarning).where(
                DepositWarning.record_type == RT.calculation,
                DepositWarning.record_id.in_(opt_ids),
                DepositWarning.code.in_(RECHECKED_CODES),
            )
        ).order_by(DepositWarning.id)
    ).all()
    submissions: dict[int, int | None] = {}
    for row in warned:
        submissions.setdefault(row.record_id, row.submission_id)
    for calc_id, submission_id in submissions.items():
        recheck_calculation_warnings(
            session, calc_id, cause="single_point_deposited", submission_id=submission_id, written_by=written_by
        )


def retire_hessian_not_stored_warnings(session: Session, calculation_id: int, *, written_by: int | None = None) -> None:
    """Retire the calculation's current ``hessian_not_stored_*`` warnings."""
    rows = session.scalars(
        current_warnings(
            select(DepositWarning).where(
                DepositWarning.record_type == RT.calculation,
                DepositWarning.record_id == calculation_id,
                DepositWarning.code.startswith(HESSIAN_NOT_STORED_PREFIX, autoescape=True),
            )
        )
    ).all()
    _retire(session, rows, "hessian_stored", written_by)


def retire_hessian_not_stored_where_stored(
    session: Session, calculation_ids: Iterable[int], *, written_by: int | None
) -> None:
    """Retire ``hessian_not_stored_*`` warnings on each given calculation that has a Hessian."""
    ids = list(dict.fromkeys(calculation_ids))
    if not ids:
        return
    for calculation_id in session.scalars(select(CalculationHessian.calculation_id).where(CalculationHessian.calculation_id.in_(ids))):
        retire_hessian_not_stored_warnings(session, calculation_id, written_by=written_by)
