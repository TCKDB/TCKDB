"""FastAPI dependency callables: DB session, auth, pagination."""

from __future__ import annotations

from typing import Iterator

from fastapi import Cookie, Depends, Header, HTTPException, Query
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.api.config import settings
from app.db.models.app_user import AppUser
from app.db.models.calculation import Calculation
from app.db.models.common import (
    AppUserRole,
    SubmissionRecordType,
    SubmissionStatus,
)
from app.db.models.submission import Submission, SubmissionRecordLink
from app.services.auth import (
    API_KEY_HEADER,
    SESSION_COOKIE_NAME,
    authenticate_api_key,
    resolve_session,
)

engine = create_engine(settings.database_url, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


def get_db() -> Iterator[Session]:
    """Yield a read-only database session.

    Does not commit — just closes the session when done.  Write endpoints
    should use :func:`get_write_db` instead.
    """
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def get_write_db() -> Iterator[Session]:
    """Yield a database session that commits on success, rolls back on error.

    Use this for endpoints that mutate data (uploads, creates).
    """
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_current_user(
    x_api_key: str | None = Header(None, alias=API_KEY_HEADER),
    tckdb_session: str | None = Cookie(None, alias=SESSION_COOKIE_NAME),
    session: Session = Depends(get_db),
) -> AppUser:
    """Resolve the request actor from an API key header or session cookie.

    Machines authenticate with ``X-API-Key``; humans authenticate with the
    session cookie set by ``POST /auth/login``.  Missing/invalid/revoked
    credentials return 401 — anonymous callers are rejected before any
    upload-side logic runs.
    """
    if x_api_key:
        user = authenticate_api_key(session, x_api_key)
        if user is None:
            raise HTTPException(status_code=401, detail="Invalid API key")
        return user

    if tckdb_session:
        user = resolve_session(session, tckdb_session)
        if user is None:
            raise HTTPException(status_code=401, detail="Invalid or expired session")
        return user

    raise HTTPException(status_code=401, detail="Authentication required")


def require_session_user(
    tckdb_session: str | None = Cookie(None, alias=SESSION_COOKIE_NAME),
    session: Session = Depends(get_db),
) -> AppUser:
    """Require a logged-in human user (session cookie only, no API keys).

    Used for endpoints that issue/revoke credentials — we never want an
    API-key bearer to spawn more keys for its owner.
    """
    if not tckdb_session:
        raise HTTPException(status_code=401, detail="Session authentication required")
    user = resolve_session(session, tckdb_session)
    if user is None:
        raise HTTPException(status_code=401, detail="Invalid or expired session")
    return user


_CURATION_ROLES = frozenset({AppUserRole.curator, AppUserRole.admin})

#: Submission lifecycle states that count as live ownership for authorization.
#: Rejected and superseded submissions explicitly do *not* grant the
#: contributor permission to attach artifacts to the calculations they once
#: produced — those calculations are no longer in that submission's lineage.
_ARTIFACT_AUTHORIZING_SUBMISSION_STATUSES = frozenset(
    {
        SubmissionStatus.pending,
        SubmissionStatus.precheck_passed,
        SubmissionStatus.auto_flagged,
        SubmissionStatus.approved,
    }
)


def can_modify_calculation_artifacts(
    session: Session,
    calculation: Calculation,
    user: AppUser,
) -> bool:
    """Return True if *user* may attach or modify artifacts on *calculation*.

    Three accept paths, evaluated in order; first match wins:

    1. Direct creation — ``calculation.created_by == user.id``.
    2. Submission ownership — there exists a ``submission_record_link``
       with ``record_type='calculation'`` and ``record_id=calculation.id``,
       joined to a :class:`Submission` whose ``created_by == user.id`` and
       whose ``status`` is in
       :data:`_ARTIFACT_AUTHORIZING_SUBMISSION_STATUSES` (pending,
       precheck_passed, auto_flagged, approved). Rejected and superseded
       submissions intentionally do not authorize uploads.
    3. Curator/admin override — ``user.role`` in :data:`_CURATION_ROLES`.

    Caller is responsible for raising HTTP 403 on False; this function
    does not raise. The 403 detail must not leak any internal id.
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
            Submission.status.in_(_ARTIFACT_AUTHORIZING_SUBMISSION_STATUSES),
        )
        .limit(1)
    )
    if submission_owner is not None:
        return True

    if user.role in _CURATION_ROLES:
        return True

    return False


def require_curator_or_admin(
    current_user: AppUser = Depends(get_current_user),
) -> AppUser:
    """Gate an endpoint behind curator/admin roles."""
    if current_user.role not in _CURATION_ROLES:
        raise HTTPException(
            status_code=403,
            detail="Curator or admin role required.",
        )
    return current_user


def require_admin(
    current_user: AppUser = Depends(get_current_user),
) -> AppUser:
    """Gate an endpoint behind the admin role."""
    if current_user.role is not AppUserRole.admin:
        raise HTTPException(status_code=403, detail="Admin role required.")
    return current_user


class PaginationParams:
    """Dependency that extracts ``skip`` / ``limit`` query params."""

    def __init__(
        self,
        skip: int = Query(0, ge=0),
        limit: int = Query(50, ge=1, le=200),
    ):
        self.skip = skip
        self.limit = limit
