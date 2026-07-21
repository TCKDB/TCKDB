from __future__ import annotations

from pathlib import Path

from sqlalchemy import text

MIGRATION = (
    Path(__file__).resolve().parents[2]
    / "alembic"
    / "versions"
    / "f2a4c6e8b0d1_add_kinetics_degeneracy_convention.py"
)


def test_degeneracy_convention_migration_follows_current_head() -> None:
    source = MIGRATION.read_text()
    assert 'revision: str = "f2a4c6e8b0d1"' in source
    assert 'down_revision: Union[str, Sequence[str], None] = "e9a3c5f7b1d2"' in source
    assert 'server_default="unknown"' in source
    assert "nullable=False" in source


def test_degeneracy_convention_database_contract(db_session) -> None:
    column = db_session.execute(
        text(
            """
            SELECT is_nullable, column_default, udt_name
            FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name = 'kinetics'
              AND column_name = 'degeneracy_convention'
            """
        )
    ).one()
    assert column.is_nullable == "NO"
    assert column.column_default == "'unknown'::kinetics_degeneracy_convention"
    assert column.udt_name == "kinetics_degeneracy_convention"

    labels = db_session.scalars(
        text(
            """
            SELECT enumlabel
            FROM pg_enum
            JOIN pg_type ON pg_type.oid = pg_enum.enumtypid
            WHERE pg_type.typname = 'kinetics_degeneracy_convention'
            ORDER BY enumsortorder
            """
        )
    ).all()
    assert labels == ["already_applied", "not_applied", "unknown"]
