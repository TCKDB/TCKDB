"""Disposable-database contract for the deposit-status correction.

``c1d8f4a25b30`` rewrites ``record_review.status`` from ``under_review`` to
``not_reviewed`` for rows that no human has touched, and records each move as
an append-only ``record_review_event``. Four things about that have to be
checkable without going near the service layer, because each is a way the
backfill could be wrong on a real database and look fine on an empty one:

1. **Only untouched ``under_review`` rows move.** The predicate carries three
   null tests that select nothing extra on today's deployed data -- all 1153
   rows satisfy them -- so on that database ``WHERE status = 'under_review'``
   alone would be indistinguishable. It is *not* indistinguishable on a
   database where somebody is genuinely reviewing something, and the whole
   value of the predicate is what it does there. So the seed contains an
   ``under_review`` row with a ``reviewed_by``, one with a ``reviewed_at``,
   and one with a ``first_approved_at``, and each must come through the
   upgrade unchanged. Without them the predicate is unproven and a widened
   one passes.

2. **Every moved row gets exactly one ``status_change`` event, and no other
   row gets one.** ``record_review_event`` is the append-only record of who
   changed what when; a migration that moved a thousand rows silently would
   leave that log asserting something false.

3. **The downgrade restores what the upgrade moved, and only that.** It reads
   the set back from its own marker events rather than from a status many
   rows share, so a row that was ``not_reviewed`` before the upgrade stays
   ``not_reviewed`` after the downgrade. A row a curator has since moved off
   ``not_reviewed`` is left alone -- asserted here rather than assumed,
   because "the downgrade declines to overwrite a human decision" is the
   limitation the revision docstring claims and a claim nobody tests is a
   claim nobody has.

4. **The revision changes no schema.** Read straight off the file. This is a
   data migration; a column or constraint appearing in it later would change
   what an operator has to plan for, and the brief for it said to stop and
   report rather than do that quietly.

Record types are chosen to keep the accepted-science guard out of the way:
``tckdb_guard_record_review`` calls ``tckdb_lock_scientific_record`` for an
accepted-science type once ``status = 'approved'`` or ``first_approved_at`` is
set, and that raises for a ``record_id`` with no domain row behind it. Rows
that need those columns are therefore seeded as ``species_entry`` (not an
accepted-science root); rows that do not are seeded as ``thermo`` (one that
is), so the moved rows still cross the guard for real.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

from tests.db._migration_chain import revision_under_test

_MIGRATION = revision_under_test("c1d8f4a25b30")

#: Marker this revision writes into ``record_review_event.details_json``.
_MARKER = {"migration": "c1d8f4a25b30", "direction": "upgrade"}


class _MigrationHarness:
    """Create a throwaway database and drive alembic against it."""

    def __init__(self, name_prefix: str):
        from conftest import _database_url, _db_env, scratch_database_name

        self.db_name = scratch_database_name(name_prefix)
        self.env = _db_env(self.db_name)
        self._database_url = _database_url
        self._admin = create_engine(
            _database_url("postgres"), isolation_level="AUTOCOMMIT", pool_pre_ping=True
        )
        self._admin_conn = self._admin.connect()
        self._admin_conn.execute(text(f'CREATE DATABASE "{self.db_name}"'))
        self.engine = None
        self.root = Path(__file__).resolve().parents[2]

    def run(self, direction: str, revision: str, *, check: bool = True):
        if self.engine is not None:
            self.engine.dispose()
            self.engine = None
        completed = subprocess.run(
            ["conda", "run", "-n", "tckdb_env", "alembic", direction, revision],
            cwd=self.root,
            env=self.env,
            check=check,
            capture_output=True,
            text=True,
        )
        self.engine = create_engine(self._database_url(self.db_name), pool_pre_ping=True)
        return completed

    def close(self) -> None:
        if self.engine is not None:
            self.engine.dispose()
        try:
            self._admin_conn.execute(
                text(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = :name"
                ),
                {"name": self.db_name},
            )
            self._admin_conn.execute(text(f'DROP DATABASE IF EXISTS "{self.db_name}"'))
        finally:
            self._admin_conn.close()
            self._admin.dispose()


@pytest.fixture
def harness():
    created = _MigrationHarness("deposit_review_status")
    yield created
    created.close()


def _insert_review(
    conn,
    *,
    record_type: str,
    record_id: int,
    status: str,
    reviewed_by: int | None = None,
    reviewed_at: bool = False,
    first_approved_at: bool = False,
) -> int:
    """Write one ``record_review`` row and return its id.

    Deliberately raw SQL at the parent revision: the point is to reproduce
    rows the *old* ingest path wrote, including combinations the service
    layer would now refuse to create.
    """
    return conn.scalar(
        text(
            "INSERT INTO record_review "
            "(record_type, record_id, status, reviewed_by, reviewed_at, "
            " first_approved_at) "
            f"VALUES (CAST(:record_type AS submission_record_type), :record_id, "
            f"CAST(:status AS record_review_status), :reviewed_by, "
            f"{'now()' if reviewed_at else 'NULL'}, "
            f"{'now()' if first_approved_at else 'NULL'}) "
            "RETURNING id"
        ),
        {
            "record_type": record_type,
            "record_id": record_id,
            "status": status,
            "reviewed_by": reviewed_by,
        },
    )


def _seed(conn) -> dict[str, int]:
    """All five statuses, plus the three ``under_review`` rows that must not move."""
    curator_id = conn.scalar(
        text(
            "INSERT INTO app_user (username, role, is_active) "
            "VALUES (:username, 'curator', true) RETURNING id"
        ),
        {"username": "backfill-test-curator"},
    )

    ids = {
        # The population the revision exists for: deposited, never touched.
        "untouched_a": _insert_review(
            conn, record_type="thermo", record_id=9_000_001, status="under_review"
        ),
        "untouched_b": _insert_review(
            conn, record_type="thermo", record_id=9_000_002, status="under_review"
        ),
        # Genuinely under review. One row per clause of the predicate, so a
        # widened predicate is caught whichever clause it drops.
        "reviewer_bearing": _insert_review(
            conn,
            record_type="thermo",
            record_id=9_000_003,
            status="under_review",
            reviewed_by=curator_id,
        ),
        "reviewed_at_bearing": _insert_review(
            conn,
            record_type="thermo",
            record_id=9_000_004,
            status="under_review",
            reviewed_at=True,
        ),
        "first_approved_bearing": _insert_review(
            conn,
            record_type="species_entry",
            record_id=9_000_005,
            status="under_review",
            first_approved_at=True,
        ),
        # The other four statuses, none of which the predicate names.
        "already_not_reviewed": _insert_review(
            conn, record_type="thermo", record_id=9_000_006, status="not_reviewed"
        ),
        "approved": _insert_review(
            conn,
            record_type="species_entry",
            record_id=9_000_007,
            status="approved",
            reviewed_by=curator_id,
            reviewed_at=True,
            first_approved_at=True,
        ),
        "rejected": _insert_review(
            conn,
            record_type="species_entry",
            record_id=9_000_008,
            status="rejected",
            reviewed_by=curator_id,
            reviewed_at=True,
        ),
        "deprecated": _insert_review(
            conn,
            record_type="species_entry",
            record_id=9_000_009,
            status="deprecated",
            reviewed_by=curator_id,
            reviewed_at=True,
        ),
        # Moved by the upgrade, then picked up by a curator. The downgrade
        # must decline to touch it.
        "moved_then_curated": _insert_review(
            conn,
            record_type="species_entry",
            record_id=9_000_010,
            status="under_review",
        ),
    }
    ids["curator"] = curator_id
    return ids


def _statuses(conn, ids: dict[str, int]) -> dict[str, str]:
    """Current status of every seeded row, keyed by its seed label.

    Reads the whole table rather than a filtered set: the scratch database
    holds nothing but the seed, so a row appearing that no label claims is
    itself a finding, and ``KeyError`` here would name it.
    """
    by_id = {
        row[0]: row[1]
        for row in conn.execute(text("SELECT id, status::text FROM record_review")).all()
    }
    return {k: by_id[v] for k, v in ids.items() if k != "curator"}


def _marker_events(conn, review_id: int) -> list[tuple[str, str, str, int | None]]:
    return [
        tuple(row)
        for row in conn.execute(
            text(
                "SELECT details_json->>'direction', from_status::text, "
                "       to_status::text, actor_user_id "
                "  FROM record_review_event "
                " WHERE record_review_id = :id "
                "   AND event_kind = 'status_change' "
                "   AND details_json->>'migration' = 'c1d8f4a25b30' "
                " ORDER BY id"
            ),
            {"id": review_id},
        ).all()
    ]


_EXPECTED_AFTER_UPGRADE = {
    "untouched_a": "not_reviewed",
    "untouched_b": "not_reviewed",
    "moved_then_curated": "not_reviewed",
    "reviewer_bearing": "under_review",
    "reviewed_at_bearing": "under_review",
    "first_approved_bearing": "under_review",
    "already_not_reviewed": "not_reviewed",
    "approved": "approved",
    "rejected": "rejected",
    "deprecated": "deprecated",
}

_MOVED = ("untouched_a", "untouched_b", "moved_then_curated")
_NOT_MOVED = tuple(k for k in _EXPECTED_AFTER_UPGRADE if k not in _MOVED)


def _upgraded(harness) -> dict[str, int]:
    harness.run("upgrade", _MIGRATION.parent)
    with harness.engine.begin() as conn:
        ids = _seed(conn)
    harness.run("upgrade", _MIGRATION.revision)
    return ids


def test_only_untouched_under_review_rows_move(harness) -> None:
    """The predicate, clause by clause.

    ``reviewer_bearing`` is the assertion that matters: it is the row a
    curator has open, and it is the only thing standing between this
    revision and one that reads ``WHERE status = 'under_review'``. The other
    two guarded rows pin the remaining clauses, and the four statuses the
    predicate never names pin that it is not simply rewriting the table.
    """
    ids = _upgraded(harness)
    with harness.engine.connect() as conn:
        assert _statuses(conn, ids) == _EXPECTED_AFTER_UPGRADE


def test_each_moved_row_gets_exactly_one_status_change_event(harness) -> None:
    """The audit log says what happened, and says it once per row.

    ``actor_user_id`` is NULL on purpose: no human made this change, and
    naming one would be the same species of lie the revision is fixing.
    """
    ids = _upgraded(harness)
    with harness.engine.connect() as conn:
        for label in _MOVED:
            assert _marker_events(conn, ids[label]) == [
                ("upgrade", "under_review", "not_reviewed", None)
            ], label
        for label in _NOT_MOVED:
            assert _marker_events(conn, ids[label]) == [], label


def test_downgrade_restores_the_moved_rows_and_nothing_else(harness) -> None:
    """What the downgrade can, will not, and cannot do.

    **Can**: put back the rows this revision moved -- read from the marker
    events, not guessed from a status thousands of rows share.

    **Will not**: touch ``moved_then_curated``. It was moved by the upgrade
    and still carries the marker, but a curator has since deprecated it, and
    a downgrade that overwrote that would destroy a human decision to undo a
    machine one.

    **Will not**, second case: touch ``already_not_reviewed``, which was
    ``not_reviewed`` before the upgrade ever ran and has no marker. This is
    the pair that proves the marker is doing the work -- a downgrade keyed on
    status alone would sweep this row up into ``under_review`` and invent a
    review of it.

    **Cannot**: erase the forward event. ``record_review_event`` is
    append-only, so both transitions are on the record afterwards.
    """
    ids = _upgraded(harness)

    with harness.engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE record_review SET status = 'deprecated', "
                "       reviewed_by = :curator, reviewed_at = now() "
                " WHERE id = :id"
            ),
            {"curator": ids["curator"], "id": ids["moved_then_curated"]},
        )

    harness.run("downgrade", _MIGRATION.parent)

    with harness.engine.connect() as conn:
        assert _statuses(conn, ids) == {
            **_EXPECTED_AFTER_UPGRADE,
            "untouched_a": "under_review",
            "untouched_b": "under_review",
            # The curator's decision stands.
            "moved_then_curated": "deprecated",
            # Never moved, never restored.
            "already_not_reviewed": "not_reviewed",
        }

        for label in ("untouched_a", "untouched_b"):
            assert _marker_events(conn, ids[label]) == [
                ("upgrade", "under_review", "not_reviewed", None),
                ("downgrade", "not_reviewed", "under_review", None),
            ], label

        # The curated row kept its forward event and gained no reverse one:
        # nothing was restored, so nothing is claimed to have been.
        assert _marker_events(conn, ids["moved_then_curated"]) == [
            ("upgrade", "under_review", "not_reviewed", None)
        ]


def test_the_revision_touches_no_schema() -> None:
    """A data migration stays a data migration.

    Read off the file rather than inferred from a passing upgrade, because
    an added column would upgrade an empty test database perfectly well and
    would still change what an operator has to plan for on the deployed one.
    """
    source = (
        Path(__file__).resolve().parents[2]
        / "alembic"
        / "versions"
        / "c1d8f4a25b30_deposits_are_not_reviewed_not_under_review.py"
    ).read_text()
    for forbidden in (
        "op.create_table",
        "op.drop_table",
        "op.add_column",
        "op.drop_column",
        "op.alter_column",
        "op.create_index",
        "op.drop_index",
        "op.create_check_constraint",
        "op.create_unique_constraint",
        "op.drop_constraint",
        "DISABLE TRIGGER",
        "ALTER TYPE",
    ):
        assert forbidden not in source, (
            f"{forbidden} appeared in a revision documented as a pure data "
            "migration; the operator runbook and the PR both say no schema "
            "changes, and one of the three is now wrong."
        )
