"""Database shape of ``energy_correction_scheme``: no ``version`` column, ``units`` outside the identity key."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import create_engine, text

REPO_ROOT = Path(__file__).resolve().parents[2]


def _set_db_env(monkeypatch, db_name: str) -> None:
    monkeypatch.setenv("DB_NAME", db_name)
    monkeypatch.setenv("DB_USER", "tckdb")
    monkeypatch.setenv("DB_PASSWORD", "tckdb")
    monkeypatch.setenv("DB_HOST", "127.0.0.1")
    monkeypatch.setenv("DB_PORT", "5432")


def test_narrowing_drops_version_and_keeps_units_as_a_column(
    db_engine, monkeypatch
):
    """The asymmetry is the point: ``version`` is dropped entirely,
    ``units`` survives as a column and only leaves the key.

    ``units`` records what the depositor sent and the scheme page renders
    that table verbatim, so canonicalising it away on write would break a
    promise the page makes. ``version`` had no such job: nullable free
    text, null on every live row, versioning nothing.

    """
    db_name = db_engine.url.database
    db_engine.dispose()
    _set_db_env(monkeypatch, db_name)

    engine = create_engine(db_engine.url.render_as_string(hide_password=False))
    try:
        with engine.connect() as connection:
            columns = set(
                connection.scalars(
                    text(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_name = 'energy_correction_scheme'"
                    )
                )
            )
            assert "version" not in columns
            assert "units" in columns

            indexed = set(
                connection.scalars(
                    text("""
                        SELECT a.attname
                        FROM pg_index i
                        JOIN pg_class c ON c.oid = i.indexrelid
                        JOIN pg_attribute a
                          ON a.attrelid = i.indrelid
                         AND a.attnum = ANY(i.indkey)
                        WHERE c.relname = 'uq_energy_correction_scheme_identity'
                    """)
                )
            )
            assert "units" not in indexed
            assert indexed == {
                "kind",
                "name",
                "level_of_theory_id",
                # The frequency half of an energy//frequency key. Units not in it.
                "frequency_level_of_theory_id",
                "source_literature_id",
                "software_release_id",
                "workflow_tool_release_id",
            }
    finally:
        engine.dispose()
