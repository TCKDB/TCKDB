"""Tests for the first-admin bootstrap helper.

The bootstrap path is operational, not user-facing — operators run it
from a shell to seed the very first admin (or to promote someone else
into the role) without needing to hand-edit the database.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.db.models.app_user import AppUser
from app.db.models.common import AppUserRole
from app.services.auth import (
    BootstrapResult,
    RoleChangeRefused,
    bootstrap_user,
    verify_password,
)


class TestBootstrapAdmin:
    def test_creates_first_admin_when_none_exists(self, db_session):
        before = db_session.scalar(
            select(AppUser).where(AppUser.role == AppUserRole.admin)
        )
        assert before is None

        user, outcome = bootstrap_user(
            db_session,
            username="root",
            email="root@example.com",
            password="hunter22-correct",
            full_name="Root Admin",
        )

        assert outcome == BootstrapResult.CREATED
        assert user.role is AppUserRole.admin
        assert user.is_active is True
        assert user.full_name == "Root Admin"
        assert verify_password("hunter22-correct", user.password_hash)

    def test_promotes_existing_user_by_username(self, db_session):
        existing = AppUser(
            username="moe",
            email="moe@example.com",
            role=AppUserRole.user,
            is_active=True,
        )
        db_session.add(existing)
        db_session.flush()

        user, outcome = bootstrap_user(
            db_session, username="moe", force_role_change=True
        )

        assert outcome == BootstrapResult.PROMOTED
        assert user.id == existing.id
        assert user.role is AppUserRole.admin

    def test_promotes_existing_user_by_email_when_username_misses(
        self, db_session
    ):
        db_session.add(
            AppUser(
                username="curly",
                email="curly@example.com",
                role=AppUserRole.curator,
            )
        )
        db_session.flush()

        user, outcome = bootstrap_user(
            db_session,
            username="brand-new-handle",  # does not match
            email="curly@example.com",
            force_role_change=True,
        )

        assert outcome == BootstrapResult.PROMOTED
        assert user.username == "curly"
        assert user.role is AppUserRole.admin

    def test_idempotent_repeat_runs_settle_on_unchanged(self, db_session):
        bootstrap_user(
            db_session,
            username="larry",
            email="larry@example.com",
            password="password-123-abc",
        )
        # Second invocation: same inputs, no mutation.
        user, outcome = bootstrap_user(
            db_session,
            username="larry",
            email="larry@example.com",
            password="password-123-abc",
        )
        assert outcome == BootstrapResult.UNCHANGED
        assert user.role is AppUserRole.admin

        # Repeated runs do not duplicate the user row.
        rows = db_session.scalars(
            select(AppUser).where(AppUser.username == "larry")
        ).all()
        assert len(rows) == 1

    def test_reactivates_disabled_existing_admin(self, db_session):
        db_session.add(
            AppUser(
                username="dormant",
                role=AppUserRole.admin,
                is_active=False,
            )
        )
        db_session.flush()

        user, outcome = bootstrap_user(db_session, username="dormant")

        assert outcome == BootstrapResult.PROMOTED
        assert user.is_active is True

    def test_creating_new_admin_requires_password(self, db_session):
        with pytest.raises(ValueError):
            bootstrap_user(db_session, username="passwordless")

    def test_username_required(self, db_session):
        with pytest.raises(ValueError):
            bootstrap_user(db_session, username="   ")

    def test_creates_user_at_explicit_non_admin_role(self, db_session):
        user, outcome = bootstrap_user(
            db_session,
            username="cure",
            email="cure@example.com",
            password="curator-pw-123",
            role=AppUserRole.curator,
        )

        assert outcome == BootstrapResult.CREATED
        assert user.role is AppUserRole.curator

    def test_refuses_role_change_without_force(self, db_session):
        db_session.add(
            AppUser(
                username="picky",
                role=AppUserRole.user,
                is_active=True,
            )
        )
        db_session.flush()

        with pytest.raises(RoleChangeRefused):
            bootstrap_user(db_session, username="picky", role=AppUserRole.admin)

    def test_force_role_change_can_demote(self, db_session):
        db_session.add(
            AppUser(
                username="overpowered",
                role=AppUserRole.admin,
                is_active=True,
            )
        )
        db_session.flush()

        user, outcome = bootstrap_user(
            db_session,
            username="overpowered",
            role=AppUserRole.curator,
            force_role_change=True,
        )

        assert outcome == BootstrapResult.PROMOTED
        assert user.role is AppUserRole.curator
