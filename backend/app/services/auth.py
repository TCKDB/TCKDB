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

Password KDF choice
-------------------

Passwords are hashed with **scrypt** (:func:`hashlib.scrypt`), a
*memory*-hard KDF. The earlier choice, PBKDF2-HMAC-SHA256, is only
*compute*-hard: an attacker with a GPU or ASIC runs thousands of cheap
SHA-256 cores in parallel, so raising the iteration count costs them
proportionally far less than it costs us. scrypt forces every guess to
allocate a large block of memory, and memory does not parallelise
cheaply — which is the property that actually blunts offline cracking.

The original PBKDF2 rationale was "stdlib-only, no new deps". That
rationale was wrong on its own terms: ``hashlib.scrypt`` is also
stdlib, so memory-hardness was available at zero dependency cost.

**argon2id was considered and rejected here.** OWASP ranks argon2id
first and scrypt second (with PBKDF2 only "if Argon2id is not
available"), but argon2id requires the ``argon2-cffi`` C extension.
scrypt delivers the property that matters — memory-hardness — from the
standard library, so the dependency buys little. Revisit if TCKDB ever
takes a compiled crypto dependency for another reason.

Old ``pbkdf2_sha256$...`` hashes still verify: the stored format is
scheme-prefixed and :func:`verify_password` dispatches on the prefix.
:func:`password_needs_rehash` plus the login route migrate each user's
stored hash the next time they authenticate successfully.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass
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

# ---------------------------------------------------------------------------
# Password hashing
# ---------------------------------------------------------------------------
#
# Stored values are scheme-prefixed so two KDFs can coexist while deployed
# hashes migrate:
#
#     scrypt$<n>$<r>$<p>$<salt_hex>$<digest_hex>          (current)
#     pbkdf2_sha256$<iterations>$<salt_hex>$<digest_hex>  (legacy, verify-only)
#
# Every parameter needed to reproduce a digest travels with the digest, so
# raising the cost below never invalidates hashes already in the database.
# Derived-key length is not a separate field — it is ``len(digest)``.

_SCHEME_SCRYPT = "scrypt"
_SCHEME_PBKDF2 = "pbkdf2_sha256"

# Current parameters. These are the only things to change when the cost
# needs raising: `password_needs_rehash` compares against them, and the
# login route migrates each user on their next successful sign-in.
#
# n=2^16, r=8, p=1 costs ~64 MiB and ~180 ms on the deployment host
# (Raspberry Pi 4, 8 GB) — the point where memory-hardness bites without
# making an honest login feel slow.
_SCRYPT_N = 1 << 16
_SCRYPT_R = 8
_SCRYPT_P = 1
_SCRYPT_DKLEN = 32

_SALT_BYTES = 16

_PBKDF2_HASH = "sha256"

# `hashlib.scrypt` refuses to run unless `maxmem` admits the working set of
# roughly ``128 * n * r`` bytes; its default is far below that and raises
# ValueError. `maxmem` is a ceiling rather than an allocation, so the
# doubling is free headroom for the implementation's own overhead.
_MAXMEM_SLACK = 2

# Upper bounds on *stored* parameters. Nothing this module writes comes
# close; these only bound the blast radius if a stored value is ever
# corrupted or tampered with, so that verification degrades to False
# instead of trying to allocate a terabyte or spin for an hour.
_MAX_SCRYPT_MEMORY_BYTES = 512 * 1024 * 1024
_MAX_PBKDF2_ITERATIONS = 10_000_000


@dataclass(frozen=True)
class _StoredHash:
    """The parsed pieces of a stored password value."""

    scheme: str
    params: tuple[int, ...]
    salt: bytes
    digest: bytes


def _scrypt_maxmem(n: int, r: int) -> int:
    return 128 * n * r * _MAXMEM_SLACK


def _parse(stored: Optional[str]) -> Optional[_StoredHash]:
    """Split a stored value into its parts, or ``None`` if unusable.

    Never raises. Missing, truncated, over-long, non-hex and
    unknown-scheme values are all simply un-parseable.
    """
    if not stored:
        return None
    fields = stored.split("$")
    scheme = fields[0]
    if scheme == _SCHEME_SCRYPT and len(fields) == 6:
        numeric, salt_hex, digest_hex = fields[1:4], fields[4], fields[5]
    elif scheme == _SCHEME_PBKDF2 and len(fields) == 4:
        numeric, salt_hex, digest_hex = fields[1:2], fields[2], fields[3]
    else:
        return None
    try:
        params = tuple(int(value) for value in numeric)
        salt = bytes.fromhex(salt_hex)
        digest = bytes.fromhex(digest_hex)
    except ValueError:
        return None
    if not salt or not digest or any(value <= 0 for value in params):
        return None
    return _StoredHash(scheme=scheme, params=params, salt=salt, digest=digest)


def _scrypt(plain: str, salt: bytes, n: int, r: int, p: int, dklen: int) -> bytes:
    return hashlib.scrypt(
        plain.encode("utf-8"),
        salt=salt,
        n=n,
        r=r,
        p=p,
        dklen=dklen,
        maxmem=_scrypt_maxmem(n, r),
    )


def _derive(parsed: _StoredHash, plain: str) -> Optional[bytes]:
    """Recompute the digest for *plain* under *parsed*'s own parameters.

    Returns ``None`` when the stored parameters are outside the sane
    bounds above, so a corrupted row cannot be turned into a resource
    exhaustion primitive.
    """
    dklen = len(parsed.digest)
    if parsed.scheme == _SCHEME_PBKDF2:
        (iterations,) = parsed.params
        if iterations > _MAX_PBKDF2_ITERATIONS:
            return None
        return hashlib.pbkdf2_hmac(
            _PBKDF2_HASH, plain.encode("utf-8"), parsed.salt, iterations, dklen
        )
    n, r, p = parsed.params
    if n < 2 or n & (n - 1):  # scrypt requires a power of two > 1
        return None
    if 128 * n * r > _MAX_SCRYPT_MEMORY_BYTES:
        return None
    return _scrypt(plain, parsed.salt, n, r, p, dklen)


# A throwaway salt for the equal-cost rejection path below. It is never
# stored and never compared against anything.
_DUMMY_SALT = secrets.token_bytes(_SALT_BYTES)


def _burn_verify_time(plain: str) -> None:
    """Spend what a real verification spends, then discard the result.

    Without this, "user has no password set" and "stored hash is in a
    format we no longer understand" both fail in microseconds while a
    merely-wrong password takes ~100 ms — a timing oracle. This keeps
    :func:`verify_password` itself from being the thing that leaks; it
    does not close the oracle in the login route, which skips the call
    entirely when no such user exists.
    """
    try:
        _scrypt(plain, _DUMMY_SALT, _SCRYPT_N, _SCRYPT_R, _SCRYPT_P, _SCRYPT_DKLEN)
    except (ValueError, MemoryError):  # pragma: no cover - defensive
        pass


def hash_password(plain: str) -> str:
    """Hash *plain* for storage under the current scheme and parameters."""
    if not plain:
        raise ValueError("Password must not be empty")
    salt = secrets.token_bytes(_SALT_BYTES)
    digest = _scrypt(plain, salt, _SCRYPT_N, _SCRYPT_R, _SCRYPT_P, _SCRYPT_DKLEN)
    return (
        f"{_SCHEME_SCRYPT}${_SCRYPT_N}${_SCRYPT_R}${_SCRYPT_P}"
        f"${salt.hex()}${digest.hex()}"
    )


def verify_password(plain: str, stored: Optional[str]) -> bool:
    """Return whether *plain* matches *stored*.

    Dispatches on the stored scheme prefix, so hashes written before the
    move to scrypt keep verifying. Never raises and never puts the
    password, the salt or the digest into an exception or a log line.
    """
    parsed = _parse(stored)
    if parsed is None:
        _burn_verify_time(plain)
        return False
    try:
        got = _derive(parsed, plain)
    except (ValueError, MemoryError, OverflowError):
        return False
    if got is None:
        return False
    return hmac.compare_digest(got, parsed.digest)


def password_needs_rehash(stored: Optional[str]) -> bool:
    """Whether a *successfully verified* ``stored`` should be re-hashed.

    True for anything that is not scrypt at exactly the parameters
    :func:`hash_password` emits today: the legacy PBKDF2 scheme, and
    scrypt hashes left behind by an earlier, cheaper parameter choice.
    Raising the constants above is therefore enough to start a
    migration — no logic here changes.

    False for a missing hash: there is nothing to migrate, and such an
    account cannot verify in the first place.
    """
    if not stored:
        return False
    parsed = _parse(stored)
    if parsed is None or parsed.scheme != _SCHEME_SCRYPT:
        return True
    return (
        parsed.params != (_SCRYPT_N, _SCRYPT_R, _SCRYPT_P)
        or len(parsed.digest) != _SCRYPT_DKLEN
    )


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
