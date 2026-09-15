"""Migration coverage for ``a55cc983501a`` (task #264 backfill).

Seeds the exact shape measured on the live deployment -- a componentless
``bac_total`` of zero targeting a transition state -- plus the survivors
that must not be touched: a componentless zero on a monatomic species
(honest), a genuine componented TS-side BAC (nonzero and zero-valued
variants), and a frozen row matching the false shape (the migration must
refuse to run rather than delete it).

The deleted rows' ``record_review`` is never deleted -- see the
migration's own module docstring for why an earlier version that did
delete it was withdrawn. It is moved to ``deprecated`` instead, through
the same field-for-field effect as
``app.services.record_review.set_record_review_status``, and this file
asserts that transition and the ``record_review_event`` it appends,
never a row count going to zero.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection

from alembic import command
from tests.db._migration_chain import revision_under_test

REPO_ROOT = Path(__file__).resolve().parents[2]
_MIGRATION = revision_under_test("a55cc983501a")


def _set_db_env(monkeypatch, db_name: str) -> None:
    monkeypatch.setenv("DB_NAME", db_name)
    monkeypatch.setenv("DB_USER", "tckdb")
    monkeypatch.setenv("DB_PASSWORD", "tckdb")
    monkeypatch.setenv("DB_HOST", "127.0.0.1")
    monkeypatch.setenv("DB_PORT", "5432")


def _seed_bac_scheme(connection: Connection, tag: str) -> int:
    return connection.scalar(
        text(
            """
            INSERT INTO energy_correction_scheme (kind, name, units, public_ref)
            VALUES ('bac_petersson', :name, 'hartree', :ref)
            RETURNING id
            """
        ),
        {"name": f"migration test scheme {tag}", "ref": f"ecs_test_{tag}"},
    )


def _seed_transition_state_entry(connection: Connection, tag: str) -> int:
    reaction_id = connection.scalar(
        text(
            "INSERT INTO chem_reaction (reversible, public_ref) "
            "VALUES (true, :ref) RETURNING id"
        ),
        {"ref": f"rxn_test_{tag}"},
    )
    reaction_entry_id = connection.scalar(
        text(
            "INSERT INTO reaction_entry (reaction_id, public_ref) "
            "VALUES (:reaction_id, :ref) RETURNING id"
        ),
        {"reaction_id": reaction_id, "ref": f"rxne_test_{tag}"},
    )
    transition_state_id = connection.scalar(
        text(
            "INSERT INTO transition_state (reaction_entry_id, public_ref) "
            "VALUES (:reaction_entry_id, :ref) RETURNING id"
        ),
        {"reaction_entry_id": reaction_entry_id, "ref": f"ts_test_{tag}"},
    )
    return connection.scalar(
        text(
            """
            INSERT INTO transition_state_entry
                (transition_state_id, charge, multiplicity, unmapped_smiles,
                 status, public_ref)
            VALUES (:transition_state_id, 0, 2, '[CH2]', 'optimized', :ref)
            RETURNING id
            """
        ),
        {"transition_state_id": transition_state_id, "ref": f"tse_test_{tag}"},
    )


def _seed_species_entry(
    connection: Connection, tag: str, *, smiles: str, charge: int, multiplicity: int
) -> int:
    inchi_key = (tag.upper() + "X" * 27)[:27]
    species_id = connection.scalar(
        text(
            """
            INSERT INTO species
                (kind, smiles, inchi_key, charge, multiplicity, stereo_kind, public_ref)
            VALUES ('molecule', :smiles, :inchi_key, :charge, :multiplicity,
                    'unspecified', :ref)
            RETURNING id
            """
        ),
        {
            "smiles": smiles,
            "inchi_key": inchi_key,
            "charge": charge,
            "multiplicity": multiplicity,
            "ref": f"sp_test_{tag}",
        },
    )
    return connection.scalar(
        text(
            "INSERT INTO species_entry (species_id, public_ref) "
            "VALUES (:species_id, :ref) RETURNING id"
        ),
        {"species_id": species_id, "ref": f"spe_test_{tag}"},
    )


def _seed_applied_correction(
    connection: Connection,
    *,
    scheme_id: int,
    value: float,
    target_species_entry_id: int | None = None,
    target_transition_state_entry_id: int | None = None,
) -> int:
    # ``applied_energy_correction`` carries no ``public_ref`` (it cannot,
    # until it exists as its own citable thing -- see
    # ``docs/specs/accepted_science_immutability.md``), so fixture rows
    # here are identified for cleanup via their scheme's ``public_ref``
    # instead.
    return connection.scalar(
        text(
            """
            INSERT INTO applied_energy_correction
                (target_species_entry_id, target_transition_state_entry_id,
                 scheme_id, application_role, value, value_unit)
            VALUES (:species_entry_id, :ts_entry_id, :scheme_id, 'bac_total',
                    :value, 'hartree')
            RETURNING id
            """
        ),
        {
            "species_entry_id": target_species_entry_id,
            "ts_entry_id": target_transition_state_entry_id,
            "scheme_id": scheme_id,
            "value": value,
        },
    )


def _seed_component(connection: Connection, applied_correction_id: int) -> None:
    connection.execute(
        text(
            """
            INSERT INTO applied_energy_correction_component
                (applied_correction_id, component_kind, key, multiplicity,
                 parameter_value, contribution_value)
            VALUES (:id, 'bond', 'C-H', 1, -0.11, -0.11)
            """
        ),
        {"id": applied_correction_id},
    )


def _seed_reviewer(connection: Connection, tag: str) -> int:
    return connection.scalar(
        text("INSERT INTO app_user (username) VALUES (:username) RETURNING id"),
        {"username": f"migration_test_reviewer_{tag}"},
    )


def _seed_record_review(
    connection: Connection,
    record_id: int,
    *,
    status: str = "not_reviewed",
    first_approved_at: str | None = None,
    reviewer_id: int | None = None,
) -> int:
    # ``record_review_terminal_requires_reviewer`` requires reviewed_by
    # and reviewed_at whenever status is a terminal one ('approved'
    # included), so an 'approved' fixture row needs both -- ``reviewer_id``
    # must be given whenever ``status`` is terminal.
    review_id = connection.scalar(
        text(
            """
            INSERT INTO record_review
                (record_type, record_id, status, reviewed_by, reviewed_at,
                 first_approved_at)
            VALUES ('applied_energy_correction', :record_id,
                    CAST(:status AS record_review_status),
                    :reviewer_id,
                    CASE WHEN CAST(:reviewer_id AS bigint) IS NOT NULL THEN now() ELSE NULL END,
                    CAST(:first_approved_at AS timestamp))
            RETURNING id
            """
        ),
        {
            "record_id": record_id,
            "status": status,
            "first_approved_at": first_approved_at,
            "reviewer_id": reviewer_id,
        },
    )
    connection.execute(
        text(
            """
            INSERT INTO record_review_event
                (record_review_id, event_kind, to_status, reason)
            VALUES (:review_id, 'created', CAST(:status AS record_review_status),
                    'migration test seed')
            """
        ),
        {"review_id": review_id, "status": status},
    )
    return review_id


def _admin_url(db_engine_url) -> str:
    return (
        f"postgresql+psycopg://{db_engine_url.username}:{db_engine_url.password}"
        f"@{db_engine_url.host}:{db_engine_url.port}/postgres?client_encoding=utf8"
    )


def _create_scratch_database(admin_url: str, db_name: str) -> None:
    engine = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as connection:
            connection.execute(text(f'DROP DATABASE IF EXISTS "{db_name}"'))
            connection.execute(text(f'CREATE DATABASE "{db_name}"'))
    finally:
        engine.dispose()


def _drop_scratch_database(admin_url: str, db_name: str) -> None:
    engine = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as connection:
            connection.execute(
                text(
                    """
                    SELECT pg_terminate_backend(pid)
                      FROM pg_stat_activity
                     WHERE datname = :db_name AND pid <> pg_backend_pid()
                    """
                ),
                {"db_name": db_name},
            )
            connection.execute(text(f'DROP DATABASE IF EXISTS "{db_name}"'))
    finally:
        engine.dispose()


def test_false_ts_zero_bac_total_is_deleted_and_its_review_deprecated(
    db_engine, monkeypatch
):
    """The 17-row shape is deleted; its review is deprecated, not deleted.

    The monatomic species zero-BAC row -- the honest shape -- survives
    untouched, proving the predicate does not overreach, as do a genuine
    nonzero componented TS-side BAC and a zero-valued but componented one
    (the exact edge the ``NOT EXISTS`` clause exists for).

    A second false-shaped row, seeded ``under_review``, proves the
    two-step transition: ``under_review`` cannot go straight to
    ``deprecated`` (``app.services.record_review._ALLOWED_TRANSITIONS``),
    so this one must pick up *two* events -- to ``not_reviewed``, then to
    ``deprecated`` -- while the plain ``not_reviewed`` row picks up one.

    Runs against its own throwaway database rather than the shared
    session ``db_engine``, for the same reason the frozen-row test below
    does: a ``deprecated`` ``record_review`` row and the
    ``record_review_event`` rows this migration appends are permanent by
    design (neither table's guard permits deleting them, and this
    revision no longer asks them to), so committing them into the
    database every other test in this process shares would leave them
    there forever. The suite's own hygiene check
    (``tests/conftest.py::_refuse_committed_rows``) catches exactly this.

    *Mutation*: revert the predicate to match on ``target_species_entry_id
    IS NOT NULL`` too (or drop the ``NOT EXISTS`` component clause) and
    this must fail: either the monatomic row disappears, or a componented
    row does.
    """
    admin_url = _admin_url(db_engine.url)
    db_name = f"{db_engine.url.database[:50]}_repair264"

    _set_db_env(monkeypatch, db_name)
    config = Config(str(REPO_ROOT / "alembic.ini"))

    _create_scratch_database(admin_url, db_name)
    engine = create_engine(
        f"postgresql+psycopg://{db_engine.url.username}:{db_engine.url.password}"
        f"@{db_engine.url.host}:{db_engine.url.port}/{db_name}?client_encoding=utf8"
    )
    try:
        # A brand-new database has no alembic_version row at all, so this
        # runs the full chain up to this revision's parent, not a single
        # step -- the one-time cost of isolation.
        command.upgrade(config, _MIGRATION.parent)

        with engine.begin() as connection:
            scheme_id = _seed_bac_scheme(connection, "false_ts")

            ts_entry_id = _seed_transition_state_entry(connection, "false_ts")
            false_ts_aec_id = _seed_applied_correction(
                connection,
                scheme_id=scheme_id,
                value=0.0,
                target_transition_state_entry_id=ts_entry_id,
            )
            false_ts_review_id = _seed_record_review(connection, false_ts_aec_id)

            under_review_ts_entry_id = _seed_transition_state_entry(
                connection, "under_review_ts"
            )
            under_review_aec_id = _seed_applied_correction(
                connection,
                scheme_id=scheme_id,
                value=0.0,
                target_transition_state_entry_id=under_review_ts_entry_id,
            )
            under_review_review_id = _seed_record_review(
                connection, under_review_aec_id, status="under_review"
            )

            monatomic_species_entry_id = _seed_species_entry(
                connection, "monatomic_h", smiles="[H]", charge=0, multiplicity=2
            )
            monatomic_aec_id = _seed_applied_correction(
                connection,
                scheme_id=scheme_id,
                value=0.0,
                target_species_entry_id=monatomic_species_entry_id,
            )
            monatomic_review_id = _seed_record_review(connection, monatomic_aec_id)

            # A genuine TS-side BAC that states its bonds: same shape
            # otherwise (bac_total, zero-adjacent scheme), but not
            # componentless, so the predicate must not touch it.
            componented_ts_entry_id = _seed_transition_state_entry(
                connection, "componented_ts"
            )
            componented_aec_id = _seed_applied_correction(
                connection,
                scheme_id=scheme_id,
                value=-0.66,
                target_transition_state_entry_id=componented_ts_entry_id,
            )
            _seed_component(connection, componented_aec_id)

            # The exact edge the ``NOT EXISTS`` clause exists for: a
            # TS-side total that happens to be zero *and* states its
            # (net-cancelling) components -- a real, if unusual, BAC, not
            # the false shape.
            zero_componented_ts_entry_id = _seed_transition_state_entry(
                connection, "zero_componented_ts"
            )
            zero_componented_aec_id = _seed_applied_correction(
                connection,
                scheme_id=scheme_id,
                value=0.0,
                target_transition_state_entry_id=zero_componented_ts_entry_id,
            )
            _seed_component(connection, zero_componented_aec_id)

        command.upgrade(config, _MIGRATION.revision)

        with engine.connect() as connection:
            # The false rows are gone.
            for aec_id in (false_ts_aec_id, under_review_aec_id):
                assert (
                    connection.execute(
                        text(
                            "SELECT count(*) FROM applied_energy_correction "
                            "WHERE id = :id"
                        ),
                        {"id": aec_id},
                    ).scalar()
                    == 0
                ), f"the false TS-zero-BAC row {aec_id} must be deleted"

            # Their record_review rows survive, deprecated -- never deleted.
            row = connection.execute(
                text(
                    "SELECT status, reviewed_by, reviewed_at FROM record_review "
                    "WHERE id = :id"
                ),
                {"id": false_ts_review_id},
            ).one()
            assert row.status == "deprecated"
            assert row.reviewed_by is not None
            assert row.reviewed_at is not None

            events = connection.execute(
                text(
                    "SELECT from_status, to_status FROM record_review_event "
                    "WHERE record_review_id = :id AND event_kind = 'status_change' "
                    "ORDER BY id"
                ),
                {"id": false_ts_review_id},
            ).all()
            assert [(e.from_status, e.to_status) for e in events] == [
                ("not_reviewed", "deprecated")
            ]

            # The under_review row took the two-step path.
            under_review_row = connection.execute(
                text("SELECT status FROM record_review WHERE id = :id"),
                {"id": under_review_review_id},
            ).one()
            assert under_review_row.status == "deprecated"
            under_review_events = connection.execute(
                text(
                    "SELECT from_status, to_status FROM record_review_event "
                    "WHERE record_review_id = :id AND event_kind = 'status_change' "
                    "ORDER BY id"
                ),
                {"id": under_review_review_id},
            ).all()
            assert [(e.from_status, e.to_status) for e in under_review_events] == [
                ("under_review", "not_reviewed"),
                ("not_reviewed", "deprecated"),
            ]

            # Survivors: rows, and their review status, both untouched.
            for aec_id in (
                monatomic_aec_id,
                componented_aec_id,
                zero_componented_aec_id,
            ):
                assert (
                    connection.execute(
                        text(
                            "SELECT count(*) FROM applied_energy_correction "
                            "WHERE id = :id"
                        ),
                        {"id": aec_id},
                    ).scalar()
                    == 1
                ), f"survivor {aec_id} must not be deleted"
            assert (
                connection.execute(
                    text("SELECT status FROM record_review WHERE id = :id"),
                    {"id": monatomic_review_id},
                ).scalar()
                == "not_reviewed"
            ), "the honest monatomic-species row's review must be untouched"
    finally:
        engine.dispose()
        _drop_scratch_database(admin_url, db_name)


def test_a_frozen_matching_row_refuses_the_upgrade(db_engine, monkeypatch):
    """A matched row a human has already approved aborts the whole upgrade.

    Runs against its own throwaway database, created and dropped by this
    test alone, rather than the shared session ``db_engine``. A frozen
    row is permanent by construction (that is the entire point being
    tested): once seeded, nothing -- not this revision, not an UPDATE,
    not a DELETE -- can remove it or its ``record_review`` again, so
    leaving it in the database every other test in this process shares
    would either poison every later migration test with a database stuck
    below head, or require exactly the trigger-disabling bypass this
    revision was rewritten to stop using. A one-off database sidesteps
    the dilemma: it is dropped whole at the end, frozen row included.

    *Mutation*: delete ``_refuse_if_any_frozen`` from ``upgrade()`` and this
    must fail -- the frozen row would be silently deleted instead of
    raising, which is exactly the "explicit human decision" the guard
    exists to force.
    """
    admin_url = _admin_url(db_engine.url)
    db_name = f"{db_engine.url.database[:50]}_frozen264"

    _set_db_env(monkeypatch, db_name)
    config = Config(str(REPO_ROOT / "alembic.ini"))

    _create_scratch_database(admin_url, db_name)
    engine = create_engine(
        f"postgresql+psycopg://{db_engine.url.username}:{db_engine.url.password}"
        f"@{db_engine.url.host}:{db_engine.url.port}/{db_name}?client_encoding=utf8"
    )
    try:
        # A brand-new database has no alembic_version row at all, so this
        # runs the full chain up to this revision's parent, not a single
        # step -- the one-time cost of isolation.
        command.upgrade(config, _MIGRATION.parent)

        with engine.begin() as connection:
            reviewer_id = _seed_reviewer(connection, "frozen_ts")
            scheme_id = _seed_bac_scheme(connection, "frozen_ts")
            ts_entry_id = _seed_transition_state_entry(connection, "frozen_ts")
            frozen_aec_id = _seed_applied_correction(
                connection,
                scheme_id=scheme_id,
                value=0.0,
                target_transition_state_entry_id=ts_entry_id,
            )
            _seed_record_review(
                connection,
                frozen_aec_id,
                status="approved",
                first_approved_at="2026-01-01 00:00:00",
                reviewer_id=reviewer_id,
            )

        with pytest.raises(RuntimeError) as excinfo:
            command.upgrade(config, _MIGRATION.revision)
        message = str(excinfo.value)
        assert str(frozen_aec_id) in message, message

        # The abort must be loud, not merely silent-and-stalled: the row
        # is still there, at the parent revision, and its review is
        # untouched (never deleted, never transitioned).
        with engine.connect() as connection:
            assert (
                connection.execute(
                    text(
                        "SELECT count(*) FROM applied_energy_correction WHERE id = :id"
                    ),
                    {"id": frozen_aec_id},
                ).scalar()
                == 1
            )
            assert (
                connection.execute(
                    text("SELECT status FROM record_review WHERE record_id = :id"),
                    {"id": frozen_aec_id},
                ).scalar()
                == "approved"
            )
    finally:
        engine.dispose()
        _drop_scratch_database(admin_url, db_name)
