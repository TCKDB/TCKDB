"""FastAPI dependency callables: DB session, auth, pagination."""

from __future__ import annotations

from typing import Iterator

from fastapi import Cookie, Depends, Header, HTTPException, Query
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.api.config import settings
from app.db.models.app_user import AppUser
from app.db.models.calculation import Calculation
from app.db.models.common import AppUserRole
from app.services.auth import (
    API_KEY_HEADER,
    SESSION_COOKIE_NAME,
    authenticate_api_key,
    resolve_session,
)
from app.services.deposit_ownership import (
    ARTIFACT_AUTHORIZING_SUBMISSION_STATUSES,
    user_owns_calculation_deposit,
)

engine = create_engine(settings.database_url, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


def _install_statement_timeout_listener(target_engine) -> None:
    """Apply ``settings.db_statement_timeout_ms`` on every new DBAPI connection.

    Registered as a ``connect`` event so the timeout follows pooled
    connections without needing to wrap each session in a context
    manager. When the setting is ``None``/``0`` no listener is
    attached and the role-level value (or PostgreSQL default) wins.

    Production deployments should also pin the timeout at the role
    level (``ALTER ROLE tckdb SET statement_timeout = '30s'``) so it
    survives even when the API process forgets to set it. The app-
    level listener is a belt-and-braces safety net, not the
    authoritative configuration. See F13 in
    ``docs/specs/public_read_abuse_controls.md``.
    """
    timeout_ms = settings.db_statement_timeout_ms
    if not timeout_ms or timeout_ms <= 0:
        return

    @event.listens_for(target_engine, "connect")
    def _set_statement_timeout(dbapi_connection, _connection_record):
        cursor = dbapi_connection.cursor()
        try:
            # Parameterized SET is not accepted by PostgreSQL, so we
            # inline the integer literal. ``timeout_ms`` is bound
            # from settings (operator-controlled), not user input.
            cursor.execute(f"SET statement_timeout = {int(timeout_ms)}")
        finally:
            cursor.close()


_install_statement_timeout_listener(engine)


def bind_ambient_session_factory(new_engine) -> Engine:
    """Re-point the module-level ``engine``/``SessionLocal`` at *new_engine*.

    ``engine`` and :data:`SessionLocal` are created at import time from
    ``settings.database_url``, i.e. from the ambient ``DB_NAME``. That is
    right in a deployment, where the ambient database *is* the database,
    and wrong under pytest, where the fixtures create and migrate a
    per-worker database that ``DB_NAME`` does not name.

    Several call sites cannot be handed a request-scoped session and so
    cannot avoid this factory:

    * ``app.services.upload_submission.record_failed_upload`` and
      ``app.services.artifact_integrity.record_artifact_integrity_event``
      must write in a transaction *independent* of the request's, which
      has already rolled back by the time they run;
    * ``app.workers.upload_worker`` and ``app.api.idempotency`` run
      outside any request;
    * ``app.api.startup_checks.check_server_encoding`` runs at boot;
    * ``/health``, ``/readyz`` and ``/status`` deliberately probe the
      process's *own* engine — routing them through an overridable
      request dependency would let a test declare a deployment healthy
      while the deployment's engine is broken.

    For all of them the fix is to make this binding tell the truth rather
    than to remove the binding. :func:`sessionmaker.configure` mutates
    :data:`SessionLocal` in place, so modules that did
    ``from app.api.deps import SessionLocal`` at import time follow the
    rebind — rebinding only the module attribute would not reach them.

    Returns the previous engine so a caller can restore it. The caller
    owns *new_engine* entirely, including its pooling and any statement
    timeout: no listener is installed here, because a rebinder that
    silently imposed ``settings.db_statement_timeout_ms`` on someone
    else's engine would be changing behaviour behind their back.

    Called by ``backend/tests/conftest.py``; not used in deployment.
    """
    global engine
    previous = engine
    engine = new_engine
    SessionLocal.configure(bind=new_engine)
    return previous


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

    The ``commit`` here runs in dependency *teardown* — after the route
    function has returned and after any decorator wrapping it has finished.
    That makes this the only place in the request that can observe a
    commit-time failure, which is the failure class where the response has
    already been determined and the work is nonetheless gone. So the error
    path gives ``app.services.upload_submission`` the chance to write the
    durable failed-upload audit its route decorator structurally cannot
    reach; the call no-ops for every session that is not a decorated
    synchronous upload, and never raises.
    """
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception as exc:
        session.rollback()
        # Imported lazily: ``app.services.upload_submission`` reaches back
        # into this module for ``SessionLocal``, and a module-level import
        # would close that loop.
        from app.services.upload_submission import audit_upload_failure_at_commit

        audit_upload_failure_at_commit(session, exc)
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


def get_optional_current_user(
    x_api_key: str | None = Header(None, alias=API_KEY_HEADER),
    tckdb_session: str | None = Cookie(None, alias=SESSION_COOKIE_NAME),
    session: Session = Depends(get_db),
) -> AppUser | None:
    """Resolve the request actor when a credential is present; ``None`` when absent.

    For the public scientific read surface, which stays reachable by
    anonymous callers but reveals a handful of fields — a submission
    reference, so far — only to a caller who is logged in (see
    ``app.services.scientific_read.auth_visibility``). This is
    deliberately not :func:`get_current_user` with the final ``raise``
    swallowed: the two credential branches are identical, including the
    401s.

    The one rule that matters: **missing** credentials resolve to
    ``None``, but a credential that is *present and invalid or revoked*
    still 401s, exactly as :func:`get_current_user` does. Treating an
    invalid or revoked key as "anonymous" would let a caller whose access
    was just revoked keep reading whatever the public already sees under
    the cover of a key that was supposed to stop working — turning a
    revocation into a silent downgrade instead of a refusal. So the
    branch condition is "was a credential supplied", never "did it
    resolve".
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

    return None


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

#: Re-exported for the call sites and tests that already import it from
#: here. The list itself lives with the ownership rule it qualifies, in
#: ``app.services.deposit_ownership``, so the read path and the write path
#: cannot drift into two different ideas of a "live" submission.
_ARTIFACT_AUTHORIZING_SUBMISSION_STATUSES = ARTIFACT_AUTHORIZING_SUBMISSION_STATUSES


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

    Paths 1 and 2 are :func:`~app.services.deposit_ownership.user_owns_calculation_deposit`
    — the single ownership rule, shared with the artifact download route so
    that upload and download cannot disagree about whose file it is. Path 3
    is this function's own addition and stays here: writing to someone
    else's deposit is a curator act, being its owner is not.

    Caller is responsible for raising HTTP 403 on False; this function
    does not raise. The 403 detail must not leak any internal id.
    """
    if user_owns_calculation_deposit(session, calculation, user):
        return True

    return user.role in _CURATION_ROLES


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


def require_auth_for_legacy_reads(
    x_api_key: str | None = Header(None, alias=API_KEY_HEADER),
    tckdb_session: str | None = Cookie(None, alias=SESSION_COOKIE_NAME),
    session: Session = Depends(get_db),
) -> AppUser | None:
    """Optionally require authentication on the legacy entity-read routes.

    The public scientific surface lives under ``/api/v1/scientific/*``;
    the legacy ``/api/v1/{thermo,kinetics,...}`` routes pre-date the
    visibility policy and bypass it. When
    ``settings.legacy_reads_require_auth`` is true (the hosted
    default), this dependency requires any credential to proceed —
    routes that already require auth (uploads, reviews) are
    unaffected because they install their own stricter dependency.

    When the setting is false (local/dev), the dependency is a no-op
    and returns ``None`` so anonymous callers see the legacy shape
    unchanged. See F14 in the audit and
    ``docs/specs/public_read_abuse_controls.md``.
    """
    if not settings.legacy_reads_require_auth:
        return None
    if x_api_key:
        user = authenticate_api_key(session, x_api_key)
        if user is None:
            raise HTTPException(status_code=401, detail="Invalid API key")
        return user
    if tckdb_session:
        user = resolve_session(session, tckdb_session)
        if user is None:
            raise HTTPException(
                status_code=401, detail="Invalid or expired session"
            )
        return user
    raise HTTPException(
        status_code=401,
        detail=(
            "Authentication required for legacy entity-read endpoints; "
            "use /api/v1/scientific/* for the public read surface."
        ),
    )


class PaginationParams:
    """Dependency that extracts ``skip`` / ``limit`` query params."""

    def __init__(
        self,
        skip: int = Query(0, ge=0),
        limit: int = Query(50, ge=1, le=200),
    ):
        self.skip = skip
        self.limit = limit
