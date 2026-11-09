"""Re-run the deposit checks whose outcome depends on a calculation's artifacts, when an artifact arrives.

A deposit is judged once, with the artifacts it carried. Clients may attach the output log afterwards
(``POST /calculations/{ref}/artifacts``), after which a warning raised for the missing file no longer describes the
record. TCKDB then re-evaluates those checks from stored state and:

* **retires** a stored warning the re-run no longer raises (``deposit_warning_retirement``, reason
  ``artifact_attached``): it stays in the table as history, and reads stop showing it as current;
* **supersedes** a stored warning of the same code whose stated facts changed (``superseded_by_recheck``): the
  re-run still warns, but about the new state (the file is there, the energy is not), and the old statement is no
  longer true;
* **adds** a warning the re-run raises that is not stored yet, by the same rules as at deposit.

Which checks: only those whose outcome reads ``calculation_artifact`` presence. Today that is exactly one,
``converged_opt_no_usable_energy`` (the rule in ``calculation_resolution``: no same-owner single point, so the
optimisation needs its own energy *and* a file). Every other deposit check was audited and reads parsed values or
payload structure, never whether a file exists: those are not re-run, because an artifact's arrival cannot change
them (the parsers that *do* read the new file raise their own warnings on the artifact route).

Atomic with the attach: the retirement rows are written in the artifact request's own transaction, outside any
swallowing savepoint, so a failure fails the request and the attach rolls back with it.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session
from tckdb_schemas.upload_warning import UploadWarning

from app.db.models.common import SubmissionRecordType as RT
from app.db.models.deposit_warning import DepositWarning, DepositWarningRetirement
from app.services.calculation_resolution import (
    W_CONVERGED_OPT_NO_USABLE_ENERGY,
    collect_converged_opt_energy_warnings,
)
from app.services.deposit_warnings import _current, _json_safe, reset_deposit_notes, store_deposit_warnings
from app.services.record_review import RecordRef

#: Codes whose outcome depends on artifact presence and that the re-run can decide from stored state alone.
ARTIFACT_DEPENDENT_CODES = frozenset({W_CONVERGED_OPT_NO_USABLE_ENERGY})


def _key(code: str, field: str, context: Any) -> tuple[str, str, str]:
    """A warning's identity for comparison: code, field and the facts it states (not its wording)."""
    facts = _json_safe(context) or {}
    return (code, field, repr(sorted(facts.items())))


def recheck_artifact_dependent_warnings(
    session: Session, calculation_id: int, *, submission_id: int | None, written_by: int | None
) -> list[UploadWarning]:
    """Re-run the artifact-dependent checks for ``calculation_id``; return the warnings newly raised (if any).

    Call after the artifacts (and anything extracted from them) are flushed.
    """
    fresh = [
        w
        for w in collect_converged_opt_energy_warnings(session, [calculation_id])
        if w.code in ARTIFACT_DEPENDENT_CODES
    ]
    reset_deposit_notes(session)  # the collector leaves producer notes on the session; this path places explicitly
    stored = session.scalars(
        _current(
            select(DepositWarning).where(
                DepositWarning.record_type == RT.calculation,
                DepositWarning.record_id == calculation_id,
                DepositWarning.code.in_(ARTIFACT_DEPENDENT_CODES),
            )
        ).order_by(DepositWarning.id)
    ).all()

    fresh_by_key = {_key(w.code, w.field, getattr(w, "context", None)): w for w in fresh}
    stored_keys = {_key(r.code, r.field, r.context) for r in stored}

    for row in stored:
        if _key(row.code, row.field, row.context) in fresh_by_key:
            continue  # still true as stored
        still_warns = any(w.code == row.code for w in fresh)
        session.add(
            DepositWarningRetirement(
                deposit_warning_id=row.id,
                retired_reason_code="superseded_by_recheck" if still_warns else "artifact_attached",
                retired_by_user_id=written_by,
            )
        )
    session.flush()

    added = [w for k, w in fresh_by_key.items() if k not in stored_keys]
    if added:
        store_deposit_warnings(
            session,
            submission_id,
            added,
            targets=[RecordRef(RT.calculation, calculation_id)],
            mark_checked=False,
            written_by=written_by,
        )
    return added
