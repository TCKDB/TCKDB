"""Tests for password hashing: the scrypt move and the PBKDF2 legacy path.

TCKDB hashed passwords with PBKDF2-HMAC-SHA256 before switching to
scrypt. Real accounts on the deployed instance still carry PBKDF2
hashes, so the legacy path is not a historical curiosity — breaking it
locks people out. The PBKDF2 fixtures below are therefore built from
first principles with :func:`hashlib.pbkdf2_hmac` rather than by calling
anything in ``app.services.auth``: if this module ever stopped emitting
that format entirely, these strings would still be exactly what is
sitting in the deployed ``app_user.password_hash`` column.
"""

from __future__ import annotations

import hashlib

import pytest

from app.services import auth as auth_service
from app.services.auth import (
    DUMMY_PASSWORD_HASH,
    hash_password,
    password_needs_rehash,
    verify_password,
)

PASSWORD = "correct-horse-battery-staple"
_LEGACY_SALT = bytes.fromhex("0f1e2d3c4b5a69788796a5b4c3d2e1f0")


def _legacy_pbkdf2_hash(plain: str, *, iterations: int = 200_000) -> str:
    """Rebuild the pre-scrypt stored format, without using the module."""
    digest = hashlib.pbkdf2_hmac("sha256", plain.encode("utf-8"), _LEGACY_SALT, iterations)
    return f"pbkdf2_sha256${iterations}${_LEGACY_SALT.hex()}${digest.hex()}"


class TestScryptRoundTrip:
    def test_hash_password_emits_scrypt_with_its_parameters(self):
        stored = hash_password(PASSWORD)
        scheme, n, r, p, salt_hex, digest_hex = stored.split("$")
        assert scheme == "scrypt"
        # Parameters travel with the hash so a future bump stays verifiable.
        assert (int(n), int(r), int(p)) == (
            auth_service._SCRYPT_N,
            auth_service._SCRYPT_R,
            auth_service._SCRYPT_P,
        )
        assert len(bytes.fromhex(salt_hex)) >= 16
        assert len(bytes.fromhex(digest_hex)) == auth_service._SCRYPT_DKLEN

    def test_round_trip(self):
        assert verify_password(PASSWORD, hash_password(PASSWORD)) is True

    def test_wrong_password_fails(self):
        stored = hash_password(PASSWORD)
        assert verify_password(PASSWORD + "!", stored) is False
        assert verify_password("", stored) is False

    def test_salt_is_per_hash(self):
        """Two hashes of the same password must not collide."""
        first, second = hash_password(PASSWORD), hash_password(PASSWORD)
        assert first != second
        assert verify_password(PASSWORD, first)
        assert verify_password(PASSWORD, second)

    def test_empty_password_still_rejected(self):
        with pytest.raises(ValueError):
            hash_password("")


class TestLegacyPbkdf2:
    """Hashes written before the scrypt switch must keep working."""

    def test_pre_existing_pbkdf2_hash_still_verifies(self):
        assert verify_password(PASSWORD, _legacy_pbkdf2_hash(PASSWORD)) is True

    def test_pre_existing_pbkdf2_hash_rejects_wrong_password(self):
        assert verify_password("not-it", _legacy_pbkdf2_hash(PASSWORD)) is False

    def test_pbkdf2_iterations_are_read_from_the_hash(self):
        """A hash written at a different cost verifies at *its* cost."""
        stored = _legacy_pbkdf2_hash(PASSWORD, iterations=1_000)
        assert verify_password(PASSWORD, stored) is True


class TestNeedsRehash:
    def test_true_for_legacy_scheme(self):
        assert password_needs_rehash(_legacy_pbkdf2_hash(PASSWORD)) is True

    def test_true_for_stale_scrypt_parameters(self):
        """Scheme matches, parameters have moved on — still needs upgrading."""
        stale_n = auth_service._SCRYPT_N >> 2
        salt = _LEGACY_SALT
        digest = hashlib.scrypt(
            PASSWORD.encode("utf-8"),
            salt=salt,
            n=stale_n,
            r=auth_service._SCRYPT_R,
            p=auth_service._SCRYPT_P,
            dklen=auth_service._SCRYPT_DKLEN,
            maxmem=128 * stale_n * auth_service._SCRYPT_R * 2,
        )
        stored = (
            f"scrypt${stale_n}${auth_service._SCRYPT_R}${auth_service._SCRYPT_P}"
            f"${salt.hex()}${digest.hex()}"
        )
        # Still a valid hash — it verifies — but it is below current cost.
        assert verify_password(PASSWORD, stored) is True
        assert password_needs_rehash(stored) is True

    def test_false_for_current_parameters(self):
        assert password_needs_rehash(hash_password(PASSWORD)) is False

    def test_parameter_bump_is_constants_only(self, monkeypatch):
        """Raising the cost must not require touching any logic."""
        stored = hash_password(PASSWORD)
        assert password_needs_rehash(stored) is False
        monkeypatch.setattr(auth_service, "_SCRYPT_N", auth_service._SCRYPT_N << 1)
        assert password_needs_rehash(stored) is True
        # The old hash keeps verifying across the bump.
        assert verify_password(PASSWORD, stored) is True

    def test_false_for_missing_hash(self):
        assert password_needs_rehash(None) is False
        assert password_needs_rehash("") is False


class TestDummyPasswordHash:
    """The decoy callers verify against when there is nothing to verify."""

    def test_is_a_real_hash_at_current_parameters(self):
        assert DUMMY_PASSWORD_HASH.startswith("scrypt$")
        # Derived at import from `hash_password`, so it cannot drift
        # cheaper than a genuine hash when the parameters are raised.
        assert password_needs_rehash(DUMMY_PASSWORD_HASH) is False
        assert len(DUMMY_PASSWORD_HASH.split("$")) == 6

    def test_no_password_matches_it(self):
        for attempt in ("", "password", PASSWORD, DUMMY_PASSWORD_HASH):
            assert verify_password(attempt, DUMMY_PASSWORD_HASH) is False


class TestMalformedStoredValues:
    """Anything unusable must return False, never raise."""

    @pytest.mark.parametrize(
        "stored",
        [
            None,
            "",
            "$",
            "$$$$$",
            "scrypt",
            "not-a-hash",
            # right scheme, wrong field count
            "scrypt$65536$8$1$aabb",
            "scrypt$65536$8$1$aabb$ccdd$extra",
            "pbkdf2_sha256$200000$aabb",
            # non-hex salt / digest
            "scrypt$65536$8$1$zzzz$aabb",
            "scrypt$65536$8$1$aabb$zzzz",
            "pbkdf2_sha256$200000$nothex$aabb",
            # non-numeric or nonsensical parameters
            "scrypt$lots$8$1$aabb$ccdd",
            "scrypt$0$8$1$aabb$ccdd",
            "scrypt$-1$8$1$aabb$ccdd",
            "pbkdf2_sha256$0$aabb$ccdd",
            # n is not a power of two
            "scrypt$65535$8$1$aabb$ccdd",
            # unknown schemes
            "argon2id$19456$2$1$aabb$ccdd",
            "md5$aabb$ccdd",
            "bcrypt$2b$12$aabbccdd",
            # empty salt / digest
            "scrypt$65536$8$1$$ccdd",
            "scrypt$65536$8$1$aabb$",
        ],
    )
    def test_returns_false_without_raising(self, stored):
        assert verify_password(PASSWORD, stored) is False

    def test_absurd_parameters_do_not_allocate(self):
        """A corrupted row must not become a resource-exhaustion primitive."""
        huge_n = 1 << 40
        assert verify_password(PASSWORD, f"scrypt${huge_n}$8$1$aabb$ccdd") is False
        assert verify_password(PASSWORD, "pbkdf2_sha256$999999999999$aabb$ccdd") is False
