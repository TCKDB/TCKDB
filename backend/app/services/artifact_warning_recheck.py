"""Re-run deposit checks whose outcome depends on state that arrives after the deposit, and retire what no longer holds.

A deposit is judged once, with what it carried. Later events can make a stored warning untrue:

* **a file is attached** (``POST /calculations/{ref}/artifacts``): ``converged_opt_no_usable_energy`` needs the opt's
  energy *and* a file when no single point exists (reason ``artifact_attached``);
* **a single point is deposited** for the same owner: it makes an opt's energy obtainable, so that same warning on
  the owner's opts no longer applies (``single_point_deposited``);
* **a Hessian is stored** for a calculation: every ``hessian_not_stored_*`` warning on it no longer applies
  (``hessian_stored``).

A retired warning is kept: ``deposit_warning_retirement`` points at it, reads stop showing it as current (curators and
admins still see it, marked retired), and nothing is edited or deleted. Where a check is re-run and still warns but
about different facts (the file is there, the energy is not), the replacement is stored first and the old row is
retired as ``superseded_by_recheck`` only once the replacement exists; a newly raised warning is added by the same
rules as at deposit.

Only checks whose verdict is decided by stored state alone are included. Every other deposit check was audited:
they read parsed values or payload structure, which none of these events change.

Atomic with the event: every write here happens in the caller's transaction, outside the swallowing savepoint that
``store_deposit_warnings`` uses, so a failure fails the request and the event rolls back with it.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session
from tckdb_schemas.upload_warning import UploadWarning

from app.db.models.calculation import Calculation
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
    """Retire ``rows`` (a retirement already present is left as it is)."""
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
    """Store ``warnings`` on the calculation. Unlike ``store_deposit_warnings`` this does not swallow a failure."""
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
    """Re-run the converged-opt energy check for ``calculation_id`` against stored state; return newly raised warnings.

    ``cause`` is the retirement reason when the warning no longer applies. Call after the event is flushed.
    """
    fresh = [
        w
        for w in collect_converged_opt_energy_warnings(session, [calculation_id])
        if w.code in RECHECKED_CODES
    ]
    reset_deposit_notes(session)  # the collector leaves producer notes on the session; this path places explicitly
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

    # The replacement goes in first; the old statement is retired only once the replacement is current. A failure to
    # store raises (the event rolls back with it); a replacement that cannot become current (an identical row was
    # retired earlier) leaves the old warning in place rather than leaving the record with none.
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
    """A single point was deposited: retire ``converged_opt_no_usable_energy`` on same-owner opts it now resolves.

    Only opts that already carry a current stored warning are visited, so nothing is added to a record that was never
    warned.
    """
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
    """A Hessian is now stored for ``calculation_id``: its ``hessian_not_stored_*`` warnings no longer apply."""
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
