"""Human authentication and API-key management endpoints.

Humans register/login/logout with sessions; once logged in they can mint
API keys for automated uploaders such as ARC.  The plain API-key value
is returned exactly once at creation time.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Cookie, Depends, HTTPException, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.config import settings
from app.api.deps import get_current_user, get_db, get_write_db, require_session_user
from app.db.models.api_key import ApiKey
from app.db.models.app_user import AppUser
from app.db.models.common import AppUserRole
from app.services.auth import (
    DUMMY_PASSWORD_HASH,
    SESSION_COOKIE_NAME,
    create_api_key,
    create_session,
    hash_password,
    password_needs_rehash,
    revoke_api_key,
    revoke_session,
    session_ttl_for_role,
    verify_password,
)

logger = logging.getLogger(__name__)

router = APIRouter()

#: The ``app_user`` uniqueness rules registration can trip, mapped to the
#: refusal each one produces, keyed on the name PostgreSQL reports for the
#: constraint. ``NAMING_CONVENTION`` spells these
#: ``uq_%(table_name)s_%(column_0_name)s``; the model declares both in
#: :class:`app.db.models.app_user.AppUser.__table_args__`.
#:
#: Two codes rather than one is the whole point. A taken username and a
#: taken email address raise the same SQLSTATE (23505), so anything
#: derived from the SQLSTATE alone can only say ``unique_conflict`` --
#: which is *less* than the English sentence this replaced, because a
#: form has two fields and no way to tell which one to highlight. The
#: constraint name is the only thing in the error that distinguishes
#: them.
#:
#: Read from ``exc.orig.diag.constraint_name`` -- psycopg's structured
#: diagnostics -- and never from the driver's message text, matching
#: :func:`app.services.idempotency._is_idempotency_unique_violation` and
#: :func:`app.api.errors._integrity_error_handler`. The message text is
#: prose PostgreSQL is free to reword between versions and locales; the
#: diagnostic field is part of the wire protocol.
#:
#: ``backend/tests/db/test_auth_registration_constraint_names.py`` checks
#: both names against live ``pg_constraint``, so renaming one in a
#: migration fails there rather than silently demoting a registration
#: refusal back to the generic sentence.
REGISTRATION_CONFLICTS: dict[str, str] = {
    "uq_app_user_username": "username_taken: That username is already in use.",
    "uq_app_user_email": "email_taken: That email address is already in use.",
}

#: What registration says when the write was refused by something other
#: than the two rules above. Deliberately the sentence that was there
#: before: an unrecognised constraint is exactly the case where naming a
#: field would be a guess.
REGISTRATION_CONFLICT_FALLBACK = "Username or email already in use."


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class RegisterRequest(BaseModel):
    username: str = Field(min_length=1)
    password: str = Field(min_length=8)
    email: str | None = None
    full_name: str | None = None


class LoginRequest(BaseModel):
    username: str = Field(min_length=1)
    password: str = Field(min_length=1)


class MeResponse(BaseModel):
    id: int
    username: str
    email: str | None
    full_name: str | None
    role: AppUserRole
    is_active: bool


class ApiKeyCreateRequest(BaseModel):
    label: str | None = Field(default=None, max_length=200)


class ApiKeyMetadata(BaseModel):
    id: int
    label: str | None
    created_at: datetime
    last_used_at: datetime | None
    revoked_at: datetime | None


class ApiKeyCreateResponse(ApiKeyMetadata):
    key: str  # plain text — shown only once


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _set_session_cookie(response: Response, token: str, ttl: timedelta) -> None:
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=token,
        max_age=int(ttl.total_seconds()),
        httponly=settings.session_cookie_httponly,
        samesite=settings.session_cookie_samesite,
        secure=settings.session_cookie_secure,
    )


def _clear_session_cookie(response: Response) -> None:
    response.delete_cookie(key=SESSION_COOKIE_NAME)


def _to_metadata(row: ApiKey) -> ApiKeyMetadata:
    return ApiKeyMetadata(
        id=row.id,
        label=row.label,
        created_at=row.created_at,
        last_used_at=row.last_used_at,
        revoked_at=row.revoked_at,
    )


def _migrate_password_hash(session: Session, user: AppUser, plain: str) -> None:
    """Re-hash *user*'s password under the current KDF, best-effort.

    Login is the only moment the plaintext is available, so it is the
    only moment a stored hash can be upgraded — users migrate off the
    legacy PBKDF2 scheme (or off stale scrypt parameters) as they come
    back, with no password reset and no bulk job.

    Call this **only after a successful verification**. A failure here
    must never turn a correct password into a failed login: the user
    authenticated, and a storage problem is not their problem. The write
    is wrapped in a SAVEPOINT so a failed upgrade rolls back on its own
    without poisoning the session that is about to create their session
    row; they keep their old, still-valid hash and the next login tries
    again.

    The log line deliberately carries only the exception type — a
    SQLAlchemy error renders its bound parameters, which would put the
    digest in the logs.
    """
    if not password_needs_rehash(user.password_hash):
        return
    try:
        with session.begin_nested():
            user.password_hash = hash_password(plain)
    except Exception as exc:  # deliberately broad: never fail a valid login
        logger.warning(
            "password rehash skipped for user id=%s (%s)",
            user.id,
            type(exc).__name__,
        )


# ---------------------------------------------------------------------------
# Registration / login / logout
# ---------------------------------------------------------------------------


def _registration_conflict_detail(exc: IntegrityError) -> str:
    """Name the field registration refused, when the driver named it.

    Returns a ``"code: message"`` detail, which
    :func:`app.api.error_contract.error_envelope` promotes into the
    ``code`` field of the body. Before this, both refusals arrived as
    ``http_409`` with one sentence covering two fields, so a sign-up form
    could not highlight the offending input without string-matching
    English.

    Call this *before* ``session.rollback()``. Rollback does not clear
    ``exc.orig``, but reading the classification off the exception while
    it is still the live failure keeps the two from drifting apart if the
    rollback ever grows a retry.

    Discloses nothing registration did not already disclose. Refusing a
    taken username is inherent to letting anyone choose one, and the same
    is true of an address the deployment has agreed to hold at most once.
    That is an argument about *this* endpoint only: no other route may
    grow a "does this account exist" answer on the strength of it.
    """
    constraint = getattr(getattr(exc.orig, "diag", None), "constraint_name", None)
    detail = REGISTRATION_CONFLICTS.get(constraint or "")
    if detail is not None:
        return detail
    # A 23505 on some other rule, or a driver that reported no constraint
    # at all. The old sentence is the honest answer: something unique
    # collided and this function cannot say which. Logged with the
    # constraint name -- not returned with it -- so the next such refusal
    # can be classified instead of guessed at.
    logger.warning(
        "registration refused by an unmapped integrity rule: constraint=%r "
        "sqlstate=%r",
        constraint,
        getattr(exc.orig, "sqlstate", None),
    )
    return REGISTRATION_CONFLICT_FALLBACK


@router.post("/register", response_model=MeResponse, status_code=201)
def register(
    request: RegisterRequest,
    response: Response,
    session: Session = Depends(get_write_db),
) -> MeResponse:
    """Create a ``user``-role account and start a session for it.

    Public registration is gated by ``AUTH_ALLOW_OPEN_REGISTRATION``: in
    local/dev it defaults to open so the API stays self-serve; hosted
    deployments flip it off and admins seed accounts directly.
    """
    if not settings.auth_allow_open_registration:
        raise HTTPException(
            status_code=403,
            detail="Public registration is disabled on this deployment.",
        )
    user = AppUser(
        username=request.username.strip(),
        email=request.email,
        full_name=request.full_name,
        password_hash=hash_password(request.password),
        role=AppUserRole.user,
        is_active=True,
    )
    session.add(user)
    try:
        session.flush()
    except IntegrityError as exc:
        detail = _registration_conflict_detail(exc)
        session.rollback()
        raise HTTPException(status_code=409, detail=detail) from None

    ttl = session_ttl_for_role(user.role)
    _, token = create_session(session, user, ttl=ttl)
    _set_session_cookie(response, token, ttl)
    return MeResponse(
        id=user.id,
        username=user.username,
        email=user.email,
        full_name=user.full_name,
        role=user.role,
        is_active=user.is_active,
    )


@router.post("/login", response_model=MeResponse)
def login(
    request: LoginRequest,
    response: Response,
    session: Session = Depends(get_write_db),
) -> MeResponse:
    user = session.scalar(
        select(AppUser).where(AppUser.username == request.username.strip())
    )
    # Verify unconditionally, against a decoy when the username is
    # unknown. The obvious `user is None or not verify_password(...)`
    # short-circuits, so an unknown username skips the KDF entirely and
    # answers in ~3 ms where a wrong password takes ~150 ms — the
    # response time becomes a username oracle that anyone can read
    # remotely. Binding the result to a name first is what keeps the
    # work from being short-circuited away.
    stored = user.password_hash if user is not None else DUMMY_PASSWORD_HASH
    password_ok = verify_password(request.password, stored)
    if user is None or not password_ok:
        raise HTTPException(status_code=401, detail="Invalid username or password.")
    # Reached only by a caller who proved they hold this account's
    # password, so telling them the account is disabled reveals nothing
    # they could not already establish. Not an enumeration vector.
    if not user.is_active:
        raise HTTPException(status_code=403, detail="Account is inactive.")

    _migrate_password_hash(session, user, request.password)

    ttl = session_ttl_for_role(user.role)
    _, token = create_session(session, user, ttl=ttl)
    _set_session_cookie(response, token, ttl)
    return MeResponse(
        id=user.id,
        username=user.username,
        email=user.email,
        full_name=user.full_name,
        role=user.role,
        is_active=user.is_active,
    )


@router.post("/logout", status_code=204)
def logout(
    response: Response,
    tckdb_session: Optional[str] = Cookie(None, alias=SESSION_COOKIE_NAME),
    session: Session = Depends(get_write_db),
) -> Response:
    if tckdb_session:
        revoke_session(session, tckdb_session)
    _clear_session_cookie(response)
    return Response(status_code=204)


@router.get("/me", response_model=MeResponse)
def me(current_user: AppUser = Depends(get_current_user)) -> MeResponse:
    return MeResponse(
        id=current_user.id,
        username=current_user.username,
        email=current_user.email,
        full_name=current_user.full_name,
        role=current_user.role,
        is_active=current_user.is_active,
    )


# ---------------------------------------------------------------------------
# API-key management (session auth only)
# ---------------------------------------------------------------------------


@router.post("/api-keys", response_model=ApiKeyCreateResponse, status_code=201)
def create_key(
    request: ApiKeyCreateRequest,
    current_user: AppUser = Depends(require_session_user),
    session: Session = Depends(get_write_db),
) -> ApiKeyCreateResponse:
    row, plain_key = create_api_key(session, current_user, label=request.label)
    return ApiKeyCreateResponse(
        id=row.id,
        label=row.label,
        created_at=row.created_at,
        last_used_at=row.last_used_at,
        revoked_at=row.revoked_at,
        key=plain_key,
    )


@router.get("/api-keys", response_model=list[ApiKeyMetadata])
def list_keys(
    current_user: AppUser = Depends(require_session_user),
    session: Session = Depends(get_db),
) -> list[ApiKeyMetadata]:
    rows = session.scalars(
        select(ApiKey).where(ApiKey.user_id == current_user.id).order_by(ApiKey.id)
    ).all()
    return [_to_metadata(r) for r in rows]


@router.delete("/api-keys/{key_id}", status_code=204)
def revoke_key(
    key_id: int,
    current_user: AppUser = Depends(require_session_user),
    session: Session = Depends(get_write_db),
) -> Response:
    row = session.get(ApiKey, key_id)
    if row is None or row.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="API key not found.")
    revoke_api_key(session, row)
    return Response(status_code=204)
