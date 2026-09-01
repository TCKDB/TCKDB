"""Disposable-database contract for the ``thermo.model_kind`` backfill.

``53b1e7dece9d`` sets ``model_kind = 'nasa7'`` on every ``thermo`` row that
carries a complete NASA-7 fit (a ``thermo_nasa`` row) but never had the
label written. Measured on the deployed archive: 44 such rows, structurally
identical to the 21 that already say ``nasa7``. Everything here is a way the
backfill could be wrong on a real database while looking perfect on an empty
one, which is why the seed pins each clause of the predicate separately
rather than relying on one uniform fixture where every row has a fit.

======================  =========================================
seed row                the clause it exists to pin
======================  =========================================
``already_labelled``    ``thermo.model_kind IS NULL``
``no_fit``              ``EXISTS (... FROM thermo_nasa ...)``
``other_kind``          ``thermo.model_kind IS NULL`` (non-nasa7 value)
======================  =========================================

``already_labelled`` is the load-bearing one: after the upgrade it is
**structurally indistinguishable** from a row this revision repaired (same
``thermo_nasa`` row, same ``model_kind``), and a downgrade that re-derived
its target set from that shape would null it too. Only the repair ledger
lets the downgrade tell the two apart -- see
``test_downgrade_restores_exactly_the_rows_the_upgrade_labelled``.
"""

from __future__ import annotations

import ast
import subprocess
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

from tests.db._migration_chain import revision_under_test

_MIGRATION = revision_under_test("53b1e7dece9d")

_REVISION_FILE = (
    Path(__file__).resolve().parents[2]
    / "alembic"
    / "versions"
    / "53b1e7dece9d_backfill_thermo_model_kind_nasa7.py"
)


class _MigrationHarness:
    """Create a throwaway database and drive alembic against it."""

    def __init__(self, name_prefix: str):
        from conftest import _database_url, _db_env, scratch_database_name

        self.db_name = scratch_database_name(name_prefix)
        self.env = _db_env(self.db_name)
        self._database_url = _database_url
        self._admin = create_engine(
            _database_url("postgres"),
            isolation_level="AUTOCOMMIT",
            pool_pre_ping=True,
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
        self.engine = create_engine(
            self._database_url(self.db_name), pool_pre_ping=True
        )
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
            self._admin_conn.execute(
                text(f'DROP DATABASE IF EXISTS "{self.db_name}"')
            )
        finally:
            self._admin_conn.close()
            self._admin.dispose()


@pytest.fixture
def harness():
    created = _MigrationHarness("thermo_model_kind")
    yield created
    created.close()


def _new_species_entry(conn, smiles: str, inchi_key: str) -> int:
    species_id = conn.scalar(
        text(
            "INSERT INTO species "
            "(kind, smiles, inchi_key, charge, multiplicity, stereo_kind) "
            "VALUES (CAST('molecule' AS molecule_kind), :smiles, "
            "        :inchi_key, 0, 1, "
            "        CAST('unspecified' AS stereo_kind)) "
            "RETURNING id"
        ),
        {"smiles": smiles, "inchi_key": inchi_key},
    )
    return conn.scalar(
        text(
            "INSERT INTO species_entry (species_id) VALUES (:sid) RETURNING id"
        ),
        {"sid": species_id},
    )


def _new_thermo(
    conn, *, species_entry_id: int, model_kind: str | None
) -> int:
    return conn.scalar(
        text(
            "INSERT INTO thermo "
            "(species_entry_id, scientific_origin, model_kind) "
            "VALUES (:sid, CAST('computed' AS scientific_origin_kind), "
            "        CAST(:kind AS thermo_model_kind)) "
            "RETURNING id"
        ),
        {"sid": species_entry_id, "kind": model_kind},
    )


def _add_nasa_fit(conn, thermo_id: int) -> None:
    conn.execute(
        text("INSERT INTO thermo_nasa (thermo_id) VALUES (:tid)"),
        {"tid": thermo_id},
    )


def _seed(conn) -> dict[str, int]:
    """One row that must move, and near-misses that must not."""
    ids: dict[str, int] = {}

    # --- The population the revision exists for -------------------------
    # A NASA-7 fit, never labelled.
    entry_a = _new_species_entry(conn, "CC", "AAAAAAAAAAAAAA-UHFFFAOYSA-N")
    ids["backfillable"] = _new_thermo(
        conn, species_entry_id=entry_a, model_kind=None
    )
    _add_nasa_fit(conn, ids["backfillable"])

    # --- Near-miss 1: already labelled -----------------------------------
    # Correct since the day it was written, and after the upgrade it is
    # indistinguishable from a repaired row. The downgrade must not null it.
    entry_b = _new_species_entry(conn, "CCC", "BBBBBBBBBBBBBB-UHFFFAOYSA-N")
    ids["already_labelled"] = _new_thermo(
        conn, species_entry_id=entry_b, model_kind="nasa7"
    )
    _add_nasa_fit(conn, ids["already_labelled"])

    # --- Near-miss 2: no fit to justify the label -------------------------
    # No thermo_nasa row: nothing supports writing 'nasa7', so this must
    # stay NULL rather than being guessed at.
    entry_c = _new_species_entry(conn, "CCCC", "CCCCCCCCCCCCCC-UHFFFAOYSA-N")
    ids["no_fit"] = _new_thermo(conn, species_entry_id=entry_c, model_kind=None)

    # --- Near-miss 3: a fit, but already labelled something else ---------
    # model_kind IS NULL is a real clause, not incidental: a row that
    # already carries a (non-nasa7) label must be left alone even though it
    # has a thermo_nasa row.
    entry_d = _new_species_entry(conn, "CCCCC", "DDDDDDDDDDDDDD-UHFFFAOYSA-N")
    ids["other_kind"] = _new_thermo(
        conn, species_entry_id=entry_d, model_kind="wilhoit"
    )
    _add_nasa_fit(conn, ids["other_kind"])

    return ids


_MOVED = ("backfillable",)
_NOT_MOVED = ("no_fit",)  # stays NULL
_UNCHANGED_LABELLED = ("already_labelled", "other_kind")  # stays as seeded


def _model_kinds(conn, ids: dict[str, int]) -> dict[str, str | None]:
    by_id = {
        row[0]: row[1]
        for row in conn.execute(
            text("SELECT id, model_kind FROM thermo")
        ).all()
    }
    return {label: by_id[tid] for label, tid in ids.items()}


def _ledger(conn, thermo_id: int) -> list[tuple]:
    """This revision's repair-change rows for one thermo row, oldest first."""
    return [
        tuple(row)
        for row in conn.execute(
            text(
                "SELECT change.before_json ->> 'model_kind', "
                "       change.after_json ->> 'model_kind', "
                "       change.changed_columns, "
                "       change.row_identity ->> 'id' "
                "  FROM accepted_science_repair_change AS change "
                "  JOIN accepted_science_repair AS declaration "
                "    ON declaration.id = change.repair_id "
                " WHERE declaration.alembic_revision = '53b1e7dece9d' "
                "   AND change.record_id = :id "
                "   AND change.record_type = 'thermo' "
                " ORDER BY change.id"
            ),
            {"id": thermo_id},
        ).all()
    ]


def _upgraded(harness) -> dict[str, int]:
    harness.run("upgrade", _MIGRATION.parent)
    with harness.engine.begin() as conn:
        ids = _seed(conn)
    harness.run("upgrade", _MIGRATION.revision)
    return ids


def test_a_fitted_unlabelled_row_becomes_nasa7_and_an_unfitted_one_stays_null(
    harness,
) -> None:
    """The predicate's two clauses, in one fixture that can distinguish them.

    A fixture where every seeded row has a fit cannot tell a correct
    predicate from a blanket UPDATE. This seeds one row with a fit and one
    without, both starting NULL, and asserts they diverge.
    """
    ids = _upgraded(harness)
    with harness.engine.connect() as conn:
        kinds = _model_kinds(conn, ids)

    assert kinds["backfillable"] == "nasa7"
    assert kinds["no_fit"] is None
    assert kinds["already_labelled"] == "nasa7"
    assert kinds["other_kind"] == "wilhoit", (
        "a row that already carried a non-null model_kind was overwritten; "
        "the model_kind IS NULL clause of the predicate has been dropped"
    )


def test_each_backfilled_row_is_recorded_once_in_the_repair_ledger(
    harness,
) -> None:
    """The ledger says what happened, once per row, and nothing else does.

    Without these rows the downgrade has no way to tell a row it repaired
    from one that was correct all along.
    """
    ids = _upgraded(harness)
    with harness.engine.connect() as conn:
        assert _ledger(conn, ids["backfillable"]) == [
            (
                None,
                "nasa7",
                ["model_kind"],
                str(ids["backfillable"]),
            )
        ]
        for label in ("no_fit", *_UNCHANGED_LABELLED):
            assert _ledger(conn, ids[label]) == [], label


def test_downgrade_restores_exactly_the_rows_the_upgrade_labelled(
    harness,
) -> None:
    """What the downgrade can, will not, and cannot do.

    **Can**: null exactly the ``model_kind`` this revision set, read back
    from the ledger by primary key.

    **Will not**: touch ``already_labelled``. After the upgrade it is
    structurally identical to a repaired row -- same fit, same
    ``model_kind`` -- and a downgrade that re-derived its targets from that
    shape would destroy it. This assertion is what separates an exact
    downgrade from a shape-derived one.

    **Will not**, second case: touch a row a curator has since relabelled
    away from ``'nasa7'``.

    **Cannot**: un-append the ledger. Both repair tables are append-only, so
    the downgrade adds its own reverse rows.
    """
    ids = _upgraded(harness)

    # A curator relabels the repaired row after the upgrade.
    with harness.engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE thermo SET model_kind = CAST('nasa9' AS thermo_model_kind) "
                "WHERE id = :id"
            ),
            {"id": ids["backfillable"]},
        )

    harness.run("downgrade", _MIGRATION.parent)

    with harness.engine.connect() as conn:
        kinds = _model_kinds(conn, ids)

        # The curator's relabel stands -- the downgrade only reverses a row
        # still carrying exactly what the upgrade wrote.
        assert kinds["backfillable"] == "nasa9"
        # Never repaired, so never un-repaired. The whole ballgame.
        assert kinds["already_labelled"] == "nasa7"
        assert kinds["no_fit"] is None
        assert kinds["other_kind"] == "wilhoit"

        # The ledger records both the upgrade's write and the downgrade's
        # no-op-because-moved-on: only one entry, the upgrade's.
        assert _ledger(conn, ids["backfillable"]) == [
            (None, "nasa7", ["model_kind"], str(ids["backfillable"]))
        ]
        assert _ledger(conn, ids["already_labelled"]) == []


def test_downgrade_restores_a_row_the_curator_left_alone(harness) -> None:
    """The direct case: nothing moved it, so the downgrade reaches it."""
    ids = _upgraded(harness)

    harness.run("downgrade", _MIGRATION.parent)

    with harness.engine.connect() as conn:
        kinds = _model_kinds(conn, ids)
        assert kinds["backfillable"] is None
        assert kinds["already_labelled"] == "nasa7"
        assert kinds["no_fit"] is None
        assert kinds["other_kind"] == "wilhoit"

        assert _ledger(conn, ids["backfillable"]) == [
            (None, "nasa7", ["model_kind"], str(ids["backfillable"])),
            (
                "nasa7",
                None,
                ["model_kind"],
                str(ids["backfillable"]),
            ),
        ]
        assert _ledger(conn, ids["already_labelled"]) == []


def test_the_repair_declaration_names_one_column(harness) -> None:
    """The guard is stood down for ``model_kind`` and nothing else.

    ``tckdb_raise_if_accepted`` compares OLD against NEW and refuses an
    UPDATE touching an undeclared column, so this is the enforced bound on
    what either direction of this revision can write to an accepted thermo
    row -- not a promise in a docstring.
    """
    _upgraded(harness)
    with harness.engine.connect() as conn:
        declared = conn.execute(
            text(
                "SELECT declared_columns, target_table, target_schema "
                "  FROM accepted_science_repair "
                " WHERE alembic_revision = '53b1e7dece9d'"
            )
        ).all()
    assert declared, "the revision declared no repair"
    for columns, table, schema in declared:
        assert columns == ["model_kind"]
        assert (schema, table) == ("public", "thermo")


def test_an_approved_row_is_backfilled_and_recorded_once(harness) -> None:
    """The path the repair declaration exists for, exercised for real.

    ``trg_as_root_thermo`` refuses UPDATE on an ever-approved thermo row.
    None of the 44 rows on the deployed database is approved, so on that
    database the declaration is inert and this path never runs -- which is
    exactly why it needs a test here. An operator instance that *has*
    approved one of these rows is where the difference shows up, and the
    difference is between a recorded repair and a migration that dies
    partway through.

    Two things are asserted, and the second is the subtle one: the upgrade
    succeeds and labels the approved row, and the row is recorded in the
    ledger **once**, not twice (``tckdb_repair_permits`` writes a change row
    itself for an accepted record; the revision writes one for every row the
    guard did *not* cover; those two sets have to be disjoint).
    """
    harness.run("upgrade", _MIGRATION.parent)
    with harness.engine.begin() as conn:
        ids = _seed(conn)
        curator_id = conn.scalar(
            text(
                "INSERT INTO app_user (username, role, is_active) "
                "VALUES (:username, 'curator', true) RETURNING id"
            ),
            {"username": "thermo-model-kind-approver"},
        )
        conn.execute(
            text(
                "INSERT INTO record_review "
                "(record_type, record_id, status, reviewed_by, reviewed_at, "
                " first_approved_at) "
                "VALUES (CAST('thermo' AS submission_record_type), :id, "
                "        CAST('approved' AS record_review_status), :curator, "
                "        now(), now())"
            ),
            {"id": ids["backfillable"], "curator": curator_id},
        )

    completed = harness.run("upgrade", _MIGRATION.revision, check=False)
    assert completed.returncode == 0, (
        "the migration failed against an approved thermo row. The repair "
        "declaration is what is supposed to make this work:\n"
        f"{completed.stdout}\n{completed.stderr}"
    )

    with harness.engine.connect() as conn:
        kinds = _model_kinds(conn, ids)
        assert kinds["backfillable"] == "nasa7", (
            "an approved row was skipped rather than repaired"
        )
        assert _ledger(conn, ids["backfillable"]) == [
            (None, "nasa7", ["model_kind"], str(ids["backfillable"]))
        ], (
            "the approved row is recorded a number of times other than "
            "once -- the guard's own change row and the revision's must "
            "not overlap"
        )


def test_upgrade_downgrade_upgrade_round_trip_reaches_the_same_state(
    harness,
) -> None:
    """Real idempotency: the migration genuinely re-runs, twice.

    Alembic no-ops an already-applied revision, so the only way to execute
    ``upgrade()`` a second time against real data is to downgrade first --
    which is also exactly the operator sequence a rollback-then-reapply
    performs. Each run declares its own repair in its own transaction (the
    "one declaration per table per transaction" rule scopes to a single
    transaction, not to the table forever), so nothing about running twice
    conflicts with itself. The row this revision backfills, un-backfills,
    then backfills again must land on the same label both times, and every
    row outside the predicate must be untouched by all three runs.
    """
    ids = _upgraded(harness)
    with harness.engine.connect() as conn:
        first_pass = _model_kinds(conn, ids)
    assert first_pass["backfillable"] == "nasa7"

    harness.run("downgrade", _MIGRATION.parent)
    with harness.engine.connect() as conn:
        after_downgrade = _model_kinds(conn, ids)
    assert after_downgrade["backfillable"] is None

    harness.run("upgrade", _MIGRATION.revision)
    with harness.engine.connect() as conn:
        second_pass = _model_kinds(conn, ids)

    assert second_pass == first_pass, (
        "a second real run of this migration reached a different state "
        "than the first"
    )
    # The row backfilled a second time carries a fresh, distinct ledger
    # entry rather than reusing or duplicating the first one -- two
    # complete forward/back cycles, four rows total.
    with harness.engine.connect() as conn:
        ledger = _ledger(conn, ids["backfillable"])
    assert len(ledger) == 3, (
        "expected 3 ledger rows for backfillable after upgrade, downgrade, "
        f"upgrade: got {ledger}"
    )
    assert ledger[0] == (None, "nasa7", ["model_kind"], str(ids["backfillable"]))
    assert ledger[1] == ("nasa7", None, ["model_kind"], str(ids["backfillable"]))
    assert ledger[2] == (None, "nasa7", ["model_kind"], str(ids["backfillable"]))


def _executable_source() -> str:
    """The revision file with its module docstring removed."""
    source = _REVISION_FILE.read_text()
    module = ast.parse(source)
    body = module.body
    if (
        body
        and isinstance(body[0], ast.Expr)
        and isinstance(body[0].value, ast.Constant)
        and isinstance(body[0].value.value, str)
    ):
        lines = source.splitlines(keepends=True)
        return "".join(lines[body[0].end_lineno :])
    return source


def test_the_revision_touches_no_schema() -> None:
    """A data migration stays a data migration.

    Read off the file rather than inferred from a passing upgrade: no
    schema-altering call is used anywhere in the executable body.
    """
    source = _executable_source()
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
            "migration."
        )
