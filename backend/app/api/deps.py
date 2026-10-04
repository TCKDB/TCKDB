"""FastAPI dependency callables: DB session, auth, pagination."""

from __future__ import annotations

import logging
from typing import Any, Iterator

from fastapi import Cookie, Depends, Header, HTTPException, Query
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from app.api.config import settings
from app.db.models.app_user import AppUser
from app.db.models.calculation import Calculation
from app.db.models.common import AppUserRole
from app.services.auth import (
    API_KEY_HEADER,
    SESSION_COOKIE_NAME,
    record_api_key_use,
    resolve_session,
)
from app.services.auth import (
    authenticate_api_key as _authenticate_api_key,
)
from app.services.deposit_ownership import (
    ARTIFACT_AUTHORIZING_SUBMISSION_STATUSES,
    user_owns_calculation_deposit,
)


def statement_timeout_connect_args(timeout_ms: int | None) -> dict[str, str]:
    """``connect_args`` that give every connection a ``statement_timeout``.

    The timeout travels as a libpq *startup option* (``-c statement_timeout=N``),
    so the server applies it while the session is being created, before any
    transaction exists. That is what makes it last: a ``SET`` issued on a
    fresh connection runs inside psycopg's implicit transaction, and the
    pool's rollback-on-return undoes it, so only the connection's *first*
    checkout kept the timeout and every later one ran with none (#604,
    measured on a deployed instance as ``['30s', '0', '0']``). A startup
    option is not part of any transaction and a rollback cannot reach it.

    ``None``, ``0`` or a negative value means "no app-level timeout" and
    returns ``{}``: the role or server default then applies, as it always
    did. Nothing else in this module sets ``statement_timeout``.

    Not pgbouncer-safe: a pooler in transaction mode rejects unknown startup
    parameters unless ``ignore_startup_parameters = options`` is set, and
    even then the timeout would not follow a client across server
    connections. Set it at the role instead
    (``scripts/configure_database_roles.py``) if a pooler is ever put in
    front of this API.
    """
    if not timeout_ms or timeout_ms <= 0:
        return {}
    return {"options": f"-c statement_timeout={int(timeout_ms)}"}


#: "Use ``settings.db_statement_timeout_ms``" -- distinct from ``None``/``0``,
#: which mean "no timeout" when passed explicitly.
_FROM_SETTINGS: Any = object()


def create_app_engine(url: str, *, statement_timeout_ms: Any = _FROM_SETTINGS, **kwargs: Any) -> Engine:
    """The one way this application builds an engine that serves requests.

    Applies ``settings.db_statement_timeout_ms`` (or *statement_timeout_ms*
    when given, ``0``/``None`` meaning none) through
    :func:`statement_timeout_connect_args`. The API's own engine, the test
    harness's engine and anything derived from either therefore share one
    mechanism (see :func:`_derive_stamp_engine`).

    Deliberately *not* used by ``alembic/env.py``, which builds its own
    engine and gets no timeout: a migration can legitimately run one
    statement (an index build, a backfill ``UPDATE``) for far longer than a
    request should.
    """
    if statement_timeout_ms is _FROM_SETTINGS:
        statement_timeout_ms = settings.db_statement_timeout_ms
    connect_args = dict(kwargs.pop("connect_args", None) or {})
    for key, value in statement_timeout_connect_args(statement_timeout_ms).items():
        # An ``options`` string the caller already passed is extended, not lost.
        connect_args[key] = f"{connect_args[key]} {value}" if key in connect_args else value
    kwargs.setdefault("pool_pre_ping", True)
    return create_engine(url, connect_args=connect_args, **kwargs)


engine = create_app_engine(settings.database_url)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


logger = logging.getLogger(__name__)


def _stamp_api_key_use(key_id: int) -> None:
    """Record that an API key was just used, without ever failing the request.

    Every route that authenticates by API key goes through
    :func:`authenticate_api_key` below, so read, write, legacy-read and
    optional-auth routes all stamp ``last_used_at`` the same way: in a
    transaction of their own, on a dedicated pool (``StampSessionLocal``), not
    the request's session and never waiting on the request's pool.
    Session-cookie auth has no ``last_used_at`` column and is unaffected.
    """
    try:
        record_api_key_use(StampSessionLocal, key_id)
    except (SQLAlchemyError, OSError):
        # Database and connection failures only: a bug elsewhere (and any
        # coded refusal) must stay loud rather than be logged and forgotten.
        logger.warning("could not record api_key.last_used_at", exc_info=True)


def authenticate_api_key(session: Session, raw_key: str) -> AppUser | None:
    """Authenticate *raw_key* and stamp its ``last_used_at`` out of band."""
    return _authenticate_api_key(
        session, raw_key, on_authenticated=_stamp_api_key_use
    )


#: Pool shape of the engine that records ``api_key.last_used_at``. Two
#: connections, no overflow, and a quarter-second wait: the stamp is a
#: best-effort audit write made while the request still holds its own
#: connection from ``SessionLocal``'s pool, so it must never queue for one.
#: Drawing on that shared pool stalled every stamping request for the full
#: ``pool_timeout`` once it was saturated, lost the stamp, and left the key
#: due again on the next request (#597 review). A separate small pool cannot
#: be starved by requests and adds at most two connections per process --
#: unlike ``NullPool``, which would open one per stamp and let a burst of
#: distinct keys exceed Postgres ``max_connections``.
STAMP_POOL_SIZE = 2
STAMP_POOL_TIMEOUT_S = 0.25


def _derive_stamp_engine(source: Engine) -> Engine:
    """A small dedicated engine that connects exactly as *source* does.

    Reuses the source pool's connection factory rather than the URL, so an
    engine that refuses to connect (see ``tests/conftest.py``) still refuses.
    The same factory carries the source's startup options, so the stamp engine
    has the source's ``statement_timeout`` by the same mechanism and no
    separate one: a source built by :func:`create_app_engine` gives it the
    configured timeout on every checkout, and a source built any other way
    gives it whatever that source has.
    """
    return create_engine(
        source.url,
        creator=source.pool._creator,  # type: ignore[attr-defined]
        pool_size=STAMP_POOL_SIZE,
        max_overflow=0,
        pool_timeout=STAMP_POOL_TIMEOUT_S,
        pool_pre_ping=True,
    )


stamp_engine = _derive_stamp_engine(engine)
StampSessionLocal = sessionmaker(bind=stamp_engine, expire_on_commit=False)


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

    The dedicated stamp engine (:data:`stamp_engine`, behind
    :data:`StampSessionLocal`) is rebound with it: a fresh small engine is
    derived from *new_engine* (:func:`_derive_stamp_engine`, which reuses its
    connection factory, so it reaches the same database, or refuses in the
    same way), :data:`StampSessionLocal` is pointed at it, and the previous
    stamp engine is disposed.

    Returns the previous engine so a caller can restore it. The caller
    owns *new_engine* entirely, including its pooling and any statement
    timeout: nothing is applied to it here, because a rebinder that
    silently imposed ``settings.db_statement_timeout_ms`` on someone
    else's engine would be changing behaviour behind their back. (Build it
    with :func:`create_app_engine` to get the configured timeout.) The
    derived stamp engine reuses *new_engine*'s connection factory, so it has
    exactly the timeout *new_engine* has.

    Called by ``backend/tests/conftest.py``; not used in deployment.
    """
    global engine, stamp_engine
    previous = engine
    engine = new_engine
    SessionLocal.configure(bind=new_engine)
    old_stamp_engine = stamp_engine
    stamp_engine = _derive_stamp_engine(new_engine)
    StampSessionLocal.configure(bind=stamp_engine)
    old_stamp_engine.dispose()
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


def get_snapshot_db() -> Iterator[Session]:
    """Yield a read-only REPEATABLE READ session, the snapshot opened before anything else touches it.

    For a read that must decide over one consistent state (a replayable selection). The isolation level can only be
    chosen before a transaction's first statement, so this is its own session, not :func:`get_db`'s: that session
    is shared with every other dependency of the request (authentication runs a statement on it) and would already
    be READ COMMITTED by the time the route body ran. The session carries ``info["tckdb_read_snapshot"]`` so the
    service insists on the guarantee it was given.

    Does not commit; closes the session when done.
    """
    from app.services.read_snapshot import begin_read_snapshot

    session = SessionLocal()
    try:
        begin_read_snapshot(session, require=True)
        session.info["tckdb_read_snapshot"] = True
        yield session
    finally:
        session.close()


def get_write_db() -> Iterator[Session]:
    """Yield a database session that commits on success, rolls back on error.

    Use this for endpoints that mutate data (uploads, creates), and **always
    declare it as** ``Depends(get_write_db, scope="function")``.

    The scope is what makes a ``201`` mean *committed*. A yield dependency's
    teardown normally runs in the request scope, which FastAPI exits only
    after the response has been sent. Committing there let a client receive
    its ``201`` and read straight back before the rows existed, let a failed
    commit surface after the ``201`` had gone out, and let ``/auth/login`` be
    followed by a ``401`` on ``/auth/api-keys`` because the session row was
    not yet committed (#616). With ``scope="function"`` the teardown runs as
    soon as the route function has returned, before the response is sent, so
    a commit-time failure is an ordinary exception that reaches the
    application's exception handlers and becomes a coded error response.
    ``tests/api/test_write_commit_before_response.py`` fails if any route
    declares it without the scope.

    Every declaration must agree: FastAPI caches a dependency per scope, so
    one ``Depends(get_write_db)`` without it would hand that request a second
    write session next to the idempotency dependency's.

    The ``commit`` here runs in dependency *teardown* -- after the route
    function has returned and after any decorator wrapping it has finished.
    That makes this the only place in the request that can observe a
    commit-time failure, which is the failure class where the work is
    nonetheless gone. So the error path gives
    ``app.services.upload_submission`` the chance to write the durable
    failed-upload audit its route decorator structurally cannot reach; the
    call no-ops for every session that is not a decorated synchronous
    upload, and never raises.

    A streaming route must not use this dependency: the session would be
    committed and closed before its body is produced. None does; export
    streams read through ``get_db``.
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
