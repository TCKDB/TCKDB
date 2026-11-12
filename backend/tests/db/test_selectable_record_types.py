"""``SELECTABLE_RECORD_TYPES`` equals the record types the database lets a release selection name."""

from __future__ import annotations

import re

from sqlalchemy import text

from app.db.models.dataset_release import SELECTABLE_RECORD_TYPES


def test_the_selectable_record_types_equal_the_check_constraint(db_session) -> None:
    definition = db_session.scalar(
        text(
            "SELECT pg_get_constraintdef(c.oid) FROM pg_constraint c JOIN pg_class t ON t.oid = c.conrelid "
            "WHERE t.relname = 'release_selection' AND c.conname LIKE '%selectable_record_type'"
        )
    )
    assert definition, "no selectable_record_type CHECK on release_selection"
    in_database = set(re.findall(r"'([a-z_]+)'::submission_record_type", definition))
    assert len(in_database) >= 3, definition
    assert in_database == {t.value for t in SELECTABLE_RECORD_TYPES}
