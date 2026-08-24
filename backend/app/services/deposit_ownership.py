"""One answer to "is this deposit mine?", for every path that asks.

The unit of ownership here is the **deposit** -- a calculation and the
evidence attached to it -- not the individual artifact row. Three columns
could plausibly have carried the answer, and the choice matters:

``calculation_artifact.created_by``
    Who performed one upload event. Narrowest, and wrong as *the* rule: a
    second file attached to the same calculation by a colleague would be
    owned by neither of them together, and the principal accountable for
    the deposit would lose access to half of it.

``submission.created_by``
    Where :doc:`ADR 0018 </adr/0018-an-update-names-what-a-submission-owns-and-proves-it-unchanged>`
    puts it: "what a depositor owns is not the reaction, it is the entry
    and the results they deposited", and every deposit is
    submission-scoped. This is the rule when a submission exists, and it
    is the one that stays correct when a submission is made *on someone's
    behalf* -- an agent or a group account uploading for a lab. The
    submission owner is the accountable principal; the row-level
    ``created_by`` of whatever process wrote the bytes is not.

``calculation.created_by``
    The pre-submission path. Measured on the hosted instance 2026-08-24:
    78 of 572 calculations carry no ``submission_record_link`` at all, so
    a submission-only rule would leave those deposits owned by nobody.

So the rule is both, in that order, and it is deliberately the rule the
**write** path already applies (``can_modify_calculation_artifacts`` in
``app.api.deps`` is this function plus a curator/admin override). Upload
and download must not disagree about whose file it is; two functions that
decide ownership and can disagree is a failure this project has hit
before.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.app_user import AppUser
from app.db.models.calculation import Calculation
from app.db.models.common import SubmissionRecordType, SubmissionStatus
from app.db.models.submission import Submission, SubmissionRecordLink

#: Submission lifecycle states that count as live ownership for
#: authorization. Rejected and superseded submissions explicitly do *not*
#: grant the contributor permission over the calculations they once
#: produced -- those calculations are no longer in that submission's
#: lineage. A depositor whose submission was rejected keeps access through
#: ``calculation.created_by`` when they created the calculation, which is
#: the ordinary case; what they lose is authority over a deposit that was
#: only ever theirs by way of a submission that has been retired.
ARTIFACT_AUTHORIZING_SUBMISSION_STATUSES = frozenset(
    {
        SubmissionStatus.pending,
        SubmissionStatus.precheck_passed,
        SubmissionStatus.auto_flagged,
        SubmissionStatus.approved,
    }
)


def user_owns_calculation_deposit(
    session: Session,
    calculation: Calculation,
    user: AppUser,
) -> bool:
    """Return True if *user* is the depositing principal for *calculation*.

    Two accept paths, evaluated in order; first match wins:

    1. Direct creation -- ``calculation.created_by == user.id``.
    2. Submission ownership -- there exists a ``submission_record_link``
       with ``record_type='calculation'`` and ``record_id=calculation.id``,
       joined to a :class:`~app.db.models.submission.Submission` whose
       ``created_by == user.id`` and whose ``status`` is in
       :data:`ARTIFACT_AUTHORIZING_SUBMISSION_STATUSES`.

    Role is deliberately *not* consulted. A curator is not an owner, and
    a caller that wants to grant curators something extra must say so at
    its own call site rather than have this function quietly mean two
    things. ``can_modify_calculation_artifacts`` does exactly that.

    Does not raise; the caller decides what a False means on its route.
    """
    if calculation.created_by is not None and calculation.created_by == user.id:
        return True

    submission_owner = session.scalar(
        select(Submission.id)
        .join(
            SubmissionRecordLink,
            SubmissionRecordLink.submission_id == Submission.id,
        )
        .where(
            SubmissionRecordLink.record_type == SubmissionRecordType.calculation,
            SubmissionRecordLink.record_id == calculation.id,
            Submission.created_by == user.id,
            Submission.status.in_(ARTIFACT_AUTHORIZING_SUBMISSION_STATUSES),
        )
        .limit(1)
    )
    return submission_owner is not None


__all__ = [
    "ARTIFACT_AUTHORIZING_SUBMISSION_STATUSES",
    "user_owns_calculation_deposit",
]
