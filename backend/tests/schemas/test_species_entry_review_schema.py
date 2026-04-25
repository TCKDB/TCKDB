"""Schema tests for species-entry review payloads."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.db.models.common import SpeciesEntryReviewRole
from app.schemas.entities.species_entry_review import (
    SpeciesEntryReviewCreate,
    SpeciesEntryReviewRead,
)


def test_create_accepts_valid_role_and_optional_note() -> None:
    schema = SpeciesEntryReviewCreate(role="curator", note="looks good")
    assert schema.role is SpeciesEntryReviewRole.curator
    assert schema.note == "looks good"


def test_create_allows_missing_note() -> None:
    schema = SpeciesEntryReviewCreate(role="reviewer")
    assert schema.note is None


def test_create_rejects_unknown_role() -> None:
    with pytest.raises(ValidationError):
        SpeciesEntryReviewCreate(role="approved")


def test_create_rejects_extra_fields() -> None:
    """Reviewer identity must come from auth, not the request body."""
    with pytest.raises(ValidationError):
        SpeciesEntryReviewCreate(
            role="curator",
            user_id=1,
        )
    with pytest.raises(ValidationError):
        SpeciesEntryReviewCreate(
            role="curator",
            species_entry_id=1,
        )


def test_read_round_trips_all_fields() -> None:
    from datetime import datetime

    payload = {
        "id": 42,
        "species_entry_id": 7,
        "user_id": 3,
        "role": "validator",
        "note": "verified against reference",
        "created_at": datetime(2026, 4, 22, 10, 0, 0),
    }
    read = SpeciesEntryReviewRead.model_validate(payload)
    assert read.id == 42
    assert read.species_entry_id == 7
    assert read.user_id == 3
    assert read.role is SpeciesEntryReviewRole.validator
    assert read.note == "verified against reference"
