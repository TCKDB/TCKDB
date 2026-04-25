"""Pydantic schemas for species-entry review curation records."""

from pydantic import BaseModel, Field

from app.db.models.common import SpeciesEntryReviewRole
from app.schemas.common import SchemaBase, TimestampedReadSchema


class SpeciesEntryReviewBase(BaseModel):
    """Shared fields for species-entry review create/read.

    The ``role`` field is the ORM's classification of the review action (who
    reviewed, in what capacity). An outcome/decision axis (approved, rejected,
    etc.) is not modelled yet; see the backend follow-ups.
    """

    role: SpeciesEntryReviewRole
    note: str | None = Field(default=None, max_length=None)


class SpeciesEntryReviewCreate(SpeciesEntryReviewBase, SchemaBase):
    """Client-supplied payload for creating a species-entry review.

    ``species_entry_id`` is carried on the URL and ``user_id`` is derived from
    the authenticated caller — neither is accepted in the request body.
    """


class SpeciesEntryReviewRead(SpeciesEntryReviewBase, TimestampedReadSchema):
    """Read schema for a persisted species-entry review."""

    species_entry_id: int
    user_id: int
