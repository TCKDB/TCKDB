"""FastAPI dependency callables: DB session, auth, pagination."""

from __future__ import annotations

from typing import Iterator

from fastapi import Cookie, Depends, Header, HTTPException, Query
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.api.config import settings
from app.db.models.app_user import AppUser
from app.db.models.common import AppUserRole
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
