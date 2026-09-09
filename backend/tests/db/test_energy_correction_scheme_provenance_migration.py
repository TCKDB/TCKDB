"""Round-trip coverage for the energy-correction-scheme provenance
widening migration (``b6d80e36dcec``).

``energy_correction_scheme`` is an already-deployed table, so the
migration rules require both ``upgrade()`` and ``downgrade()`` to be
implemented and correct. This asserts the concrete claim the revision's
docstring makes: a two-row fixture in the shape of the two live rows
(same kind/lot/version-adjacent identity, both distinct only by
``kind``, both uncited and software-less) survives an
upgrade -> downgrade -> upgrade round trip byte-for-byte on every
column the narrower schema still has, and the new columns come back
``NULL`` (never guessed) after the second upgrade.
"""

from __future__ import annotations

from pathlib import Path

from alembic.config import Config
from sqlalchemy import create_engine, text

from alembic import command
from tests.db._migration_chain import revision_under_test

REPO_ROOT = Path(__file__).resolve().parents[2]

_MIGRATION = revision_under_test("b6d80e36dcec")


def test_two_row_fixture_survives_downgrade_and_reupgrade(db_engine, monkeypatch):
    """Upgrade -> downgrade -> upgrade: the two-row fixture is intact
    throughout, refs never change, and the reintroduced columns land
    NULL, never a guess."""
    db_name = db_engine.url.database
    db_engine.dispose()
    monkeypatch.setenv("DB_NAME", db_name)
    monkeypatch.setenv("DB_USER", "tckdb")
    monkeypatch.setenv("DB_PASSWORD", "tckdb")
    monkeypatch.setenv("DB_HOST", "127.0.0.1")
    monkeypatch.setenv("DB_PORT", "5432")
    config = Config(str(REPO_ROOT / "alembic.ini"))

    engine = create_engine(db_engine.url.render_as_string(hide_password=False))
    inserted_ids: list[int] = []
    try:
        # Seed the fixture at head (columns present, both NULL -- exactly
        # the two live rows' shape).
        with engine.begin() as connection:
            inserted_ids = list(
                connection.scalars(
                    text("""
                INSERT INTO energy_correction_scheme (kind, name, version, units, note)
                VALUES ('atom_energy', 'atom_energy', NULL, 'hartree', 'migration test row 1'),
                       ('bac_petersson', 'bac_petersson', NULL, 'hartree', 'migration test row 2')
                RETURNING id
            """)
                )
            )
            refs_before = {
                row.id: row.public_ref
                for row in connection.execute(
                    text(
                        "SELECT id, public_ref FROM energy_correction_scheme "
                        "WHERE id = ANY(:ids)"
                    ),
                    {"ids": inserted_ids},
                )
            }

        # Downgrade past this revision: the new columns/index disappear,
        # the fixture's other columns and refs must not.
        command.downgrade(config, _MIGRATION.parent)
        with engine.connect() as connection:
            rows = connection.execute(
                text(
                    "SELECT id, kind, name, public_ref, note FROM "
                    "energy_correction_scheme WHERE id = ANY(:ids) ORDER BY id"
                ),
                {"ids": inserted_ids},
            ).all()
            assert len(rows) == 2
            for row in rows:
                assert row.public_ref == refs_before[row.id]
            assert {r.note for r in rows} == {
                "migration test row 1",
                "migration test row 2",
            }
            has_software_column = connection.scalar(
                text(
                    "SELECT count(*) FROM information_schema.columns "
                    "WHERE table_name = 'energy_correction_scheme' "
                    "AND column_name = 'software_id'"
                )
            )
            assert has_software_column == 0

        # Re-upgrade: the fixture is still there, with the same refs, and
        # the reintroduced columns are NULL -- not backfilled, not guessed.
        command.upgrade(config, _MIGRATION.revision)
        with engine.connect() as connection:
            rows = connection.execute(
                text(
                    "SELECT id, public_ref, software_id, source_literature_id, "
                    "workflow_tool_release_id, note FROM energy_correction_scheme "
                    "WHERE id = ANY(:ids) ORDER BY id"
                ),
                {"ids": inserted_ids},
            ).all()
            assert len(rows) == 2
            for row in rows:
                assert row.public_ref == refs_before[row.id]
                assert row.software_id is None
                assert row.source_literature_id is None
                assert row.workflow_tool_release_id is None
    finally:
        if inserted_ids:
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "DELETE FROM energy_correction_scheme WHERE id = ANY(:ids)"
                    ),
                    {"ids": inserted_ids},
                )
        engine.dispose()
        # Put the schema back where this test found it -- see
        # test_upload_job_lease_migration.py's identical finally block for
        # why this must be "head", not ``_MIGRATION.revision``.
        command.upgrade(config, "head")
