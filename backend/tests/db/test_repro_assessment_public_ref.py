"""Database contracts for the reproducibility-assessment public ref."""

from __future__ import annotations

import re

from sqlalchemy import text


def test_public_ref_database_contract_and_append_only_trigger(db_session):
    column = db_session.execute(text("""
        SELECT is_nullable, column_default FROM information_schema.columns
        WHERE table_name = 'record_reproducibility_assessment' AND column_name = 'public_ref'
    """)).one()
    assert column.is_nullable == "NO"
    assert "rpa_" in column.column_default
    trigger = db_session.scalar(text("""
        SELECT tgname FROM pg_trigger WHERE tgrelid = 'record_reproducibility_assessment'::regclass
          AND tgname = 'trg_repro_assessment_append_only' AND NOT tgisinternal
    """))
    assert trigger == "trg_repro_assessment_append_only"


def test_raw_sql_fallback_has_standard_base32_shape(db_session):
    ref = db_session.scalar(text("SELECT public.rpa_opaque_public_ref()"))
    assert re.fullmatch(r"rpa_[a-z2-7]{26}", ref)
