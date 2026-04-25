"""Entity schemas for application user identity."""

from pydantic import BaseModel, Field, field_validator

from app.db.models.common import AppUserRole
from app.schemas.common import SchemaBase, TimestampedReadSchema
from app.schemas.utils import normalize_optional_text, normalize_required_text


class AppUserBase(BaseModel):
    """Shared fields for an application user.

    :param username: Unique username.
    :param email: Optional unique email address.
    :param full_name: Optional full name.
    :param affiliation: Optional institutional affiliation.
    :param orcid: Optional 19-character ORCID identifier.
    :param role: User role (defaults to ``user``).
    """

    username: str = Field(min_length=1)
    email: str | None = None
    full_name: str | None = None
    affiliation: str | None = None
    orcid: str | None = Field(
        default=None, min_length=19, max_length=19
    )
    role: AppUserRole = AppUserRole.user

    @field_validator("username")
    @classmethod
    def normalize_username(cls, value: str) -> str:
        return normalize_required_text(value)

    @field_validator("email", "full_name", "affiliation")
    @classmethod
    def normalize_text(cls, value: str | None) -> str | None:
        return normalize_optional_text(value)


class AppUserCreate(AppUserBase, SchemaBase):
    """Create schema for an application user."""


class AppUserUpdate(SchemaBase):
    """Patch schema for an application user."""

    username: str | None = Field(default=None, min_length=1)
    email: str | None = None
    full_name: str | None = None
    affiliation: str | None = None
    orcid: str | None = Field(
        default=None, min_length=19, max_length=19
    )
    role: AppUserRole | None = None

    @field_validator("username")
    @classmethod
    def normalize_username(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return normalize_required_text(value)

    @field_validator("email", "full_name", "affiliation")
    @classmethod
    def normalize_text(cls, value: str | None) -> str | None:
        return normalize_optional_text(value)


class AppUserRead(AppUserBase, TimestampedReadSchema):
    """Read schema for an application user."""
