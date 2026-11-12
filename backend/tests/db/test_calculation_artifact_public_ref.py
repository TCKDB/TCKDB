"""Database contracts for the ``calculation_artifact`` public ref.

Every uploaded artifact has a citable ``art_`` handle generated server-side (base32 function + ``server_default``). There
is no append-only trigger: this table is append-only by convention, same as ``artifact_integrity_event``.
"""

from __future__ import annotations

import re

from sqlalchemy import text


def test_public_ref_database_contract(db_session):
    column = db_session.execute(
        text(
            """
            SELECT is_nullable, column_default FROM information_schema.columns
            WHERE table_name = 'calculation_artifact' AND column_name = 'public_ref'
            """
        )
    ).one()
    assert column.is_nullable == "NO"
    assert "art_" in column.column_default

    index_kind = db_session.execute(
        text(
            """
            SELECT indexdef FROM pg_indexes
            WHERE tablename = 'calculation_artifact'
              AND indexname = 'ix_calculation_artifact_public_ref'
            """
        )
    ).scalar()
    assert index_kind is not None
    assert "UNIQUE" in index_kind


def test_raw_sql_fallback_has_standard_base32_shape(db_session):
    ref = db_session.scalar(text("SELECT public.art_opaque_public_ref()"))
    assert re.fullmatch(r"art_[a-z2-7]{26}", ref)
    # Two calls must not collide.
    ref2 = db_session.scalar(text("SELECT public.art_opaque_public_ref()"))
    assert ref != ref2
