"""Auth service — password hashing, session + API-key token helpers.

Two auth flows live here:

- **Password + session** for humans: ``hash_password``/``verify_password``
  and ``create_session`` / ``revoke_session``.
- **API keys** for machines: ``create_api_key`` (returns plain key once)
  and ``authenticate_api_key`` which resolves a raw key to its owner.

Security rules followed:

- plain passwords and plain API keys are never stored
- revocation is effective immediately (filtered at lookup time)
- tokens are generated from :mod:`secrets` and compared via ``hmac``
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.api_key import ApiKey
from app.db.models.app_user import AppUser
from app.db.models.common import AppUserRole
from app.db.models.user_session import UserSession

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SESSION_COOKIE_NAME = "tckdb_session"
API_KEY_HEADER = "X-API-Key"
API_KEY_PREFIX = "tck_"

# Role-based fixed (non-sliding) session TTLs. Lower-privilege accounts get
# longer windows; admin sessions are short on purpose so a stolen admin
# cookie has a small blast radius. Sessions never auto-extend on activity.
SESSION_TTL_BY_ROLE: dict[AppUserRole, timedelta] = {
    AppUserRole.user: timedelta(days=7),
    AppUserRole.curator: timedelta(days=3),
    AppUserRole.admin: timedelta(hours=12),
}

# Default TTL when a caller does not supply one — matches the user-role TTL
# and is also the longest of the three, so it is a safe upper bound for
# cookie ``max_age`` when the role is not yet known at cookie-setting time.
SESSION_TTL = SESSION_TTL_BY_ROLE[AppUserRole.user]


def session_ttl_for_role(role: AppUserRole) -> timedelta:
    """Return the fixed session TTL for *role*.

    Centralising the mapping here keeps the policy in one place: callers
    pass the resolved TTL into :func:`create_session` rather than picking
    a duration each time.
    """
    return SESSION_TTL_BY_ROLE[role]

# PBKDF2-HMAC-SHA256 with 200k iterations — stdlib-only, no new deps.
_PBKDF2_ITERATIONS = 200_000
_PBKDF2_HASH = "sha256"


# ---------------------------------------------------------------------------
# Password hashing (PBKDF2-HMAC-SHA256, stored as pbkdf2_sha256$iter$salt$hash)
# ---------------------------------------------------------------------------


def hash_password(plain: str) -> str:
    if not plain:
        raise ValueError("Password must not be empty")
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac(
        _PBKDF2_HASH, plain.encode("utf-8"), salt, _PBKDF2_ITERATIONS
    )
    return f"pbkdf2_sha256${_PBKDF2_ITERATIONS}${salt.hex()}${digest.hex()}"


def verify_password(plain: str, stored: Optional[str]) -> bool:
    if not stored:
        return False
    try:
        scheme, iter_str, salt_hex, digest_hex = stored.split("$")
    except ValueError:
        return False
    if scheme != "pbkdf2_sha256":
        return False
    try:
        iterations = int(iter_str)
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(digest_hex)
    except ValueError:
        return False
    got = hashlib.pbkdf2_hmac(
        _PBKDF2_HASH, plain.encode("utf-8"), salt, iterations
    )
    return hmac.compare_digest(got, expected)


# ---------------------------------------------------------------------------
# Token helpers
# ---------------------------------------------------------------------------


def _sha256(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _generate_token(prefix: str = "") -> str:
    return f"{prefix}{secrets.token_urlsafe(32)}"


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------


def create_session(
    session: Session,
    user: AppUser,
    ttl: Optional[timedelta] = None,
) -> tuple[UserSession, str]:
    """Create a session for *user* and return ``(row, plain_token)``.

    The plain token is the value to set as the session cookie — it is
    not stored on the row.

    When *ttl* is omitted the role-based fixed TTL from
    :data:`SESSION_TTL_BY_ROLE` is used; sessions are never refreshed
    afterwards (see :func:`resolve_session`).
    """
    if ttl is None:
        ttl = session_ttl_for_role(user.role)
    raw = _generate_token()
    record = UserSession(
        user_id=user.id,
        token_hash=_sha256(raw),
        expires_at=datetime.now(timezone.utc).replace(tzinfo=None) + ttl,
    )
    session.add(record)
    session.flush()
    return record, raw


def resolve_session(session: Session, raw_token: str) -> Optional[AppUser]:
    """Resolve a session token to its owner, or ``None`` if invalid.

    Sessions are fixed-expiry: this function intentionally never bumps
    ``expires_at``. Long-lived activity must re-authenticate when the
    role-based TTL elapses (see :data:`SESSION_TTL_BY_ROLE`).
    """
    if not raw_token:
        return None
    row = session.scalar(
        select(UserSession).where(UserSession.token_hash == _sha256(raw_token))
    )
    if row is None or row.revoked_at is not None:
        return None
    if row.expires_at <= datetime.now(timezone.utc).replace(tzinfo=None):
        return None
    user = session.get(AppUser, row.user_id)
    if user is None or not user.is_active:
        return None
    return user


def revoke_session(session: Session, raw_token: str) -> bool:
    row = session.scalar(
        select(UserSession).where(UserSession.token_hash == _sha256(raw_token))
    )
    if row is None or row.revoked_at is not None:
        return False
    row.revoked_at = datetime.now(timezone.utc).replace(tzinfo=None)
    session.flush()
    return True


# ---------------------------------------------------------------------------
# API keys
# ---------------------------------------------------------------------------


def create_api_key(
    session: Session, user: AppUser, label: Optional[str] = None
) -> tuple[ApiKey, str]:
    """Issue a new API key for *user*; return ``(row, plain_key)``.

    The plain key is only returned here — it cannot be recovered later.
    Callers must echo it to the user exactly once.
    """
    raw = _generate_token(API_KEY_PREFIX)
    record = ApiKey(
        user_id=user.id,
        key_hash=_sha256(raw),
        label=label,
    )
    session.add(record)
    session.flush()
    return record, raw


def authenticate_api_key(session: Session, raw_key: str) -> Optional[AppUser]:
    """Resolve a raw API key to its owning user, or ``None`` if invalid."""
    if not raw_key:
        return None
    row = session.scalar(
        select(ApiKey).where(ApiKey.key_hash == _sha256(raw_key))
    )
    if row is None or row.revoked_at is not None:
        return None
    user = session.get(AppUser, row.user_id)
    if user is None or not user.is_active:
        return None
    row.last_used_at = datetime.now(timezone.utc).replace(tzinfo=None)
    return user


def revoke_api_key(session: Session, key: ApiKey) -> None:
    if key.revoked_at is None:
        key.revoked_at = datetime.now(timezone.utc).replace(tzinfo=None)
        session.flush()


# ---------------------------------------------------------------------------
# First-admin bootstrap
# ---------------------------------------------------------------------------


class BootstrapResult(str):
    """Outcome marker for :func:`bootstrap_user`.

    Subclasses ``str`` so the CLI can print it directly while callers can
    still ``==``-compare against the class constants.
    """

    CREATED = "created"
    PROMOTED = "promoted"
    UNCHANGED = "unchanged"


class RoleChangeRefused(ValueError):
    """Raised when bootstrap would change an existing user's role without ``force_role_change``."""


def bootstrap_user(
    session: Session,
    *,
    username: str,
    role: AppUserRole = AppUserRole.admin,
    password: Optional[str] = None,
    email: Optional[str] = None,
    full_name: Optional[str] = None,
    affiliation: Optional[str] = None,
    force_role_change: bool = False,
) -> tuple[AppUser, str]:
    """Create or update a user at the requested role, idempotently.

    Lookup is by ``username`` first, then ``email`` (when provided). If a
    matching account exists with the same role it is left as-is (only
    reactivated if disabled). When the existing role differs, the change
    is refused unless ``force_role_change=True``. When neither lookup
    matches, a new user is created at ``role`` and *password* is required.

    Returns ``(user, outcome)`` where outcome is one of
    ``BootstrapResult.{CREATED, PROMOTED, UNCHANGED}``. ``PROMOTED`` is
    used for any role change or reactivation, regardless of direction.
    Repeated calls with the same inputs settle on ``UNCHANGED``.
    """
    username = username.strip()
    if not username:
        raise ValueError("username is required")

    user = session.scalar(select(AppUser).where(AppUser.username == username))
    if user is None and email:
        user = session.scalar(select(AppUser).where(AppUser.email == email))

    if user is None:
        if not password:
            raise ValueError(
                "password is required when creating a new user"
            )
        user = AppUser(
            username=username,
            email=email,
            full_name=full_name,
            affiliation=affiliation,
            password_hash=hash_password(password),
            role=role,
            is_active=True,
        )
        session.add(user)
        session.flush()
        return user, BootstrapResult.CREATED

    outcome = BootstrapResult.UNCHANGED
    if user.role is not role:
        if not force_role_change:
            raise RoleChangeRefused(
                f"user {user.username!r} has role {user.role.value!r}; "
                f"refusing to change to {role.value!r} without force_role_change=True"
            )
        user.role = role
        outcome = BootstrapResult.PROMOTED
    if not user.is_active:
        user.is_active = True
        outcome = BootstrapResult.PROMOTED
    session.flush()
    return user, outcome
