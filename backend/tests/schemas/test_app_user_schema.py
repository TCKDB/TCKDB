"""Tests for app/schemas/entities/app_user.py."""

from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.db.models.common import AppUserRole
from app.schemas.entities.app_user import AppUserCreate, AppUserRead, AppUserUpdate


class TestAppUserCreate:
    def test_valid(self) -> None:
        u = AppUserCreate(
            username="calvin",
            email="calvin@example.com",
            full_name="Calvin R.",
            affiliation="MIT",
            orcid="0000-0002-1825-0097",
        )
        assert u.username == "calvin"
        assert u.role == AppUserRole.user

    def test_normalizes_whitespace(self) -> None:
        u = AppUserCreate(
            username="  calvin  ",
            full_name="  Calvin R.  ",
        )
        assert u.username == "calvin"
        assert u.full_name == "Calvin R."

    def test_rejects_empty_username(self) -> None:
        with pytest.raises(ValidationError):
            AppUserCreate(username="")

    def test_rejects_bad_orcid_length(self) -> None:
        with pytest.raises(ValidationError):
            AppUserCreate(username="calvin", orcid="0000-0002")

    def test_defaults_to_user_role(self) -> None:
        u = AppUserCreate(username="test")
        assert u.role == AppUserRole.user

    def test_explicit_admin_role(self) -> None:
        u = AppUserCreate(username="admin", role=AppUserRole.admin)
        assert u.role == AppUserRole.admin


class TestAppUserUpdate:
    def test_all_optional(self) -> None:
        update = AppUserUpdate()
        assert update.username is None
        assert update.role is None

    def test_normalizes_fields(self) -> None:
        update = AppUserUpdate(
            username="  newname  ",
            affiliation="  Harvard  ",
        )
        assert update.username == "newname"
        assert update.affiliation == "Harvard"


class TestAppUserRead:
    def test_from_orm(self) -> None:
        user = SimpleNamespace(
            id=1, username="calvin", email="c@example.com",
            full_name="Calvin R.", affiliation="MIT",
            orcid="0000-0002-1825-0097",
            role=AppUserRole.user,
            created_at="2024-01-01T00:00:00",
        )
        read = AppUserRead.model_validate(user)
        assert read.id == 1
        assert read.username == "calvin"
        assert read.role == AppUserRole.user
