"""Database contracts for the ``molecular_property_observation`` public ref.

Every observation has a citable ``mpo_`` handle generated server-side (base32 function + ``server_default``). There is no
append-only trigger: this table is append-only by convention, not by trigger.
"""

from __future__ import annotations

import re

from sqlalchemy import text


def test_public_ref_database_contract(db_session):
    column = db_session.execute(
        text(
            """
            SELECT is_nullable, column_default FROM information_schema.columns
            WHERE table_name = 'molecular_property_observation' AND column_name = 'public_ref'
            """
        )
    ).one()
    assert column.is_nullable == "NO"
    assert "mpo_" in column.column_default

    index_kind = db_session.execute(
        text(
            """
            SELECT indexdef FROM pg_indexes
            WHERE tablename = 'molecular_property_observation'
              AND indexname = 'ix_molecular_property_observation_public_ref'
            """
        )
    ).scalar()
    assert index_kind is not None
    assert "UNIQUE" in index_kind


def test_raw_sql_fallback_has_standard_base32_shape(db_session):
    ref = db_session.scalar(text("SELECT public.mpo_opaque_public_ref()"))
    assert re.fullmatch(r"mpo_[a-z2-7]{26}", ref)
    ref2 = db_session.scalar(text("SELECT public.mpo_opaque_public_ref()"))
    assert ref != ref2
