"""Human authentication and API-key management endpoints.

Humans register/login/logout with sessions; once logged in they can mint
API keys for automated uploaders such as ARC.  The plain API-key value
is returned exactly once at creation time.
"""

from __future__ import annotations

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
    SESSION_COOKIE_NAME,
    create_api_key,
    create_session,
    hash_password,
    revoke_api_key,
    revoke_session,
    session_ttl_for_role,
    verify_password,
)

router = APIRouter()


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
        httponly=True,
        samesite="lax",
        secure=False,  # the deployment can flip this at the reverse-proxy layer
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


# ---------------------------------------------------------------------------
# Registration / login / logout
# ---------------------------------------------------------------------------


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
    except IntegrityError:
        session.rollback()
        raise HTTPException(status_code=409, detail="Username or email already in use.")

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
    if user is None or not verify_password(request.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid username or password.")
    if not user.is_active:
        raise HTTPException(status_code=403, detail="Account is inactive.")

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
