"""Disposable-database contract for the composite-Gaussian-release merge.

``6141f2d98e78`` repoints every ``calculation.software_release_id`` that
cites a composite Gaussian ``software_release`` row (``version`` holding a
parsed ESS banner such as ``"Gaussian 16, Revision C.02"``, ``revision``
NULL) onto the already-decomposed sibling row (``version="16",
revision="C.02"``), then deletes the composite row. Everything here is a way
the merge could be wrong on a real database while looking perfect on an
empty one, which is why the seed pins each near-miss separately rather than
relying on one uniform fixture where every calculation sits on the
composite row.

======================  =========================================
seed row                the clause it exists to pin
======================  =========================================
``on_target``           calculations already on the decomposed release
                        must not move -- a fixture with only
                        composite-pointing rows cannot distinguish a
                        correct repoint from a blanket UPDATE.
``bare_version``        ``sr.version ~* pattern`` (no ", Revision" suffix,
                        e.g. release 1's "09" -- must not match)
``null_version``        ``regexp_match`` against a NULL ``version`` (the
                        NULL-version defect tracked separately against
                        #305 -- must not crash or match)
``other_software``      ``software.name = 'Gaussian'`` scope -- a
                        different program's release, and even a release
                        whose ``version`` text happens to *start with*
                        "Gaussian" while its declared software does not,
                        must not be merged into anything.
======================  =========================================

``on_target`` is the load-bearing one: after the upgrade it is
**structurally indistinguishable** from a repointed calculation (same
``software_release_id``), and a downgrade that re-derived its target set
from that shape would move it too. Only the repair ledger lets the
downgrade tell the two apart -- see
``test_downgrade_restores_exactly_the_calculations_the_upgrade_repointed``.
"""

from __future__ import annotations

import ast
import subprocess
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

from tests.db._migration_chain import revision_under_test

_MIGRATION = revision_under_test("6141f2d98e78")

_REVISION_FILE = (
    Path(__file__).resolve().parents[2]
    / "alembic"
    / "versions"
    / "6141f2d98e78_merge_composite_gaussian_software_release.py"
)

_COMPOSITE_VERSION = "Gaussian 16, Revision C.02"


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
    created = _MigrationHarness("merge_gaussian")
    yield created
    created.close()


def _new_software(conn, name: str) -> int:
    return conn.scalar(
        text("INSERT INTO software (name) VALUES (:name) RETURNING id"),
        {"name": name},
    )


def _new_release(
    conn,
    *,
    software_id: int,
    version: str | None,
    revision: str | None,
    build: str | None = None,
) -> int:
    return conn.scalar(
        text(
            "INSERT INTO software_release (software_id, version, revision, build) "
            "VALUES (:sid, :version, :revision, :build) RETURNING id"
        ),
        {"sid": software_id, "version": version, "revision": revision, "build": build},
    )


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


def _new_calculation(
    conn, *, species_entry_id: int, software_release_id: int | None
) -> int:
    return conn.scalar(
        text(
            "INSERT INTO calculation (type, species_entry_id, software_release_id) "
            "VALUES (CAST('opt' AS calc_type), :sid, :rid) RETURNING id"
        ),
        {"sid": species_entry_id, "rid": software_release_id},
    )


def _seed(conn) -> dict[str, int]:
    """Two rows that must move, two that must not, and three near-miss releases."""
    ids: dict[str, int] = {}

    gaussian_id = _new_software(conn, "Gaussian")
    ids["composite_release"] = _new_release(
        conn, software_id=gaussian_id, version=_COMPOSITE_VERSION, revision=None
    )
    ids["target_release"] = _new_release(
        conn, software_id=gaussian_id, version="16", revision="C.02"
    )

    # --- The population this revision exists for -------------------------
    entry_a = _new_species_entry(conn, "CC", "AAAAAAAAAAAAAA-UHFFFAOYSA-N")
    ids["on_composite_a"] = _new_calculation(
        conn, species_entry_id=entry_a, software_release_id=ids["composite_release"]
    )
    entry_b = _new_species_entry(conn, "CCC", "BBBBBBBBBBBBBB-UHFFFAOYSA-N")
    ids["on_composite_b"] = _new_calculation(
        conn, species_entry_id=entry_b, software_release_id=ids["composite_release"]
    )

    # --- Near-miss 1: already on the decomposed release -------------------
    # Correct since the day it was written, and after the upgrade it is
    # indistinguishable from a repointed row. The downgrade must not move it.
    entry_c = _new_species_entry(conn, "CCCC", "CCCCCCCCCCCCCC-UHFFFAOYSA-N")
    ids["on_target_a"] = _new_calculation(
        conn, species_entry_id=entry_c, software_release_id=ids["target_release"]
    )
    entry_d = _new_species_entry(conn, "CCCCC", "DDDDDDDDDDDDDD-UHFFFAOYSA-N")
    ids["on_target_b"] = _new_calculation(
        conn, species_entry_id=entry_d, software_release_id=ids["target_release"]
    )

    # --- Near-miss 2: a bare version, no embedded revision label ----------
    # Release 1's shape ("09", revision NULL). No ", Revision" suffix, so
    # the identifying regex must not match it.
    ids["bare_version_release"] = _new_release(
        conn, software_id=gaussian_id, version="09", revision=None
    )
    entry_e = _new_species_entry(conn, "CCCCCC", "EEEEEEEEEEEEEE-UHFFFAOYSA-N")
    ids["on_bare_version"] = _new_calculation(
        conn, species_entry_id=entry_e, software_release_id=ids["bare_version_release"]
    )

    # --- Near-miss 3: NULL version ------------------------------------------
    # Releases 2 and 3's shape -- a separate, out-of-scope defect. Must not
    # crash regexp_match, and must not be touched.
    ids["null_version_release"] = _new_release(
        conn, software_id=gaussian_id, version=None, revision=None
    )
    entry_f = _new_species_entry(conn, "CCCCCCC", "FFFFFFFFFFFFFF-UHFFFAOYSA-N")
    ids["on_null_version"] = _new_calculation(
        conn, species_entry_id=entry_f, software_release_id=ids["null_version_release"]
    )

    # --- Near-miss 4: a different software, including one whose version --
    # text itself reads "Gaussian ..." -- the software.name = 'Gaussian'
    # scope must be what excludes it, not merely the fact that no sibling
    # exists for it.
    orca_id = _new_software(conn, "ORCA")
    ids["other_software_release"] = _new_release(
        conn, software_id=orca_id, version="ORCA 6.0.0", revision=None
    )
    ids["impostor_release"] = _new_release(
        conn, software_id=orca_id, version=_COMPOSITE_VERSION, revision=None
    )
    entry_g = _new_species_entry(conn, "CCCCCCCC", "GGGGGGGGGGGGGG-UHFFFAOYSA-N")
    ids["on_other_software"] = _new_calculation(
        conn, species_entry_id=entry_g, software_release_id=ids["other_software_release"]
    )
    entry_h = _new_species_entry(conn, "CCCCCCCCC", "HHHHHHHHHHHHHH-UHFFFAOYSA-N")
    ids["on_impostor"] = _new_calculation(
        conn, species_entry_id=entry_h, software_release_id=ids["impostor_release"]
    )

    # --- Release 6's shape: zero calculations, out of scope regardless ----
    arkane_id = _new_software(conn, "Arkane")
    ids["arkane_release"] = _new_release(
        conn,
        software_id=arkane_id,
        version="0.1.1",
        revision="a" * 40,
    )

    return ids


_MOVED = ("on_composite_a", "on_composite_b")
_NOT_MOVED = (
    "on_target_a",
    "on_target_b",
    "on_bare_version",
    "on_null_version",
    "on_other_software",
    "on_impostor",
)


def _releases(conn, ids: dict[str, int]) -> dict[str, int | None]:
    by_id = {
        row[0]: row[1]
        for row in conn.execute(
            text("SELECT id, software_release_id FROM calculation")
        ).all()
    }
    return {
        label: by_id[calc_id]
        for label, calc_id in ids.items()
        if label.startswith("on_")
    }


def _ledger(conn, calculation_id: int) -> list[tuple]:
    """This revision's repair-change rows for one calculation, oldest first."""
    return [
        tuple(row)
        for row in conn.execute(
            text(
                "SELECT change.before_json ->> 'software_release_id', "
                "       change.after_json ->> 'software_release_id', "
                "       change.changed_columns, "
                "       change.row_identity ->> 'id' "
                "  FROM accepted_science_repair_change AS change "
                "  JOIN accepted_science_repair AS declaration "
                "    ON declaration.id = change.repair_id "
                " WHERE declaration.alembic_revision = '6141f2d98e78' "
                "   AND change.record_id = :id "
                "   AND change.record_type = 'calculation' "
                " ORDER BY change.id"
            ),
            {"id": calculation_id},
        ).all()
    ]


def _upgraded(harness) -> dict[str, int]:
    harness.run("upgrade", _MIGRATION.parent)
    with harness.engine.begin() as conn:
        ids = _seed(conn)
    harness.run("upgrade", _MIGRATION.revision)
    return ids


def test_only_calculations_on_the_composite_release_move(harness) -> None:
    """The predicate, near-miss by near-miss.

    Every row in ``_NOT_MOVED`` is one clause away from being repointed, so
    this fails whichever clause a future edit drops. ``on_target_a``/``_b``
    are listed separately in the ledger test below because their
    ``software_release_id`` is unchanged rather than merely equal to what a
    repointed row now has, which is a different assertion about a different
    risk.
    """
    ids = _upgraded(harness)
    with harness.engine.connect() as conn:
        releases = _releases(conn, ids)

    for label in _MOVED:
        assert releases[label] == ids["target_release"], label
    assert releases["on_target_a"] == ids["target_release"]
    assert releases["on_target_b"] == ids["target_release"]
    assert releases["on_bare_version"] == ids["bare_version_release"], (
        "a bare version with no embedded revision label was merged; the "
        "', Revision' suffix requirement has been dropped"
    )
    assert releases["on_null_version"] == ids["null_version_release"]
    assert releases["on_other_software"] == ids["other_software_release"]
    assert releases["on_impostor"] == ids["impostor_release"], (
        "a non-Gaussian release whose version text merely reads "
        "'Gaussian ...' was merged; the software.name = 'Gaussian' scope "
        "has been dropped"
    )


def test_the_composite_release_row_is_deleted(harness) -> None:
    ids = _upgraded(harness)
    with harness.engine.connect() as conn:
        remaining = conn.scalar(
            text("SELECT count(*) FROM software_release WHERE id = :id"),
            {"id": ids["composite_release"]},
        )
    assert remaining == 0

    # Every near-miss release is still standing.
    with harness.engine.connect() as conn:
        for label in (
            "target_release",
            "bare_version_release",
            "null_version_release",
            "other_software_release",
            "impostor_release",
            "arkane_release",
        ):
            assert (
                conn.scalar(
                    text("SELECT count(*) FROM software_release WHERE id = :id"),
                    {"id": ids[label]},
                )
                == 1
            ), label


def test_each_repointed_calculation_is_recorded_once_in_the_repair_ledger(
    harness,
) -> None:
    """The ledger says what happened, once per row, and nothing else does.

    Without these rows the downgrade has no way to tell a calculation it
    repointed from one that was correct all along.
    """
    ids = _upgraded(harness)
    with harness.engine.connect() as conn:
        for label in _MOVED:
            assert _ledger(conn, ids[label]) == [
                (
                    str(ids["composite_release"]),
                    str(ids["target_release"]),
                    ["software_release_id"],
                    str(ids[label]),
                )
            ], label
        for label in _NOT_MOVED:
            assert _ledger(conn, ids[label]) == [], label


def test_the_repair_declaration_names_one_column(harness) -> None:
    """The guard is stood down for ``software_release_id`` and nothing else."""
    _upgraded(harness)
    with harness.engine.connect() as conn:
        declared = conn.execute(
            text(
                "SELECT declared_columns, target_table, target_schema "
                "  FROM accepted_science_repair "
                " WHERE alembic_revision = '6141f2d98e78'"
            )
        ).all()
    assert declared, "the revision declared no repair"
    for columns, table, schema in declared:
        assert columns == ["software_release_id"]
        assert (schema, table) == ("public", "calculation")


def test_an_approved_calculation_is_repointed_and_recorded_once(harness) -> None:
    """The path the repair declaration exists for, exercised for real.

    ``trg_as_root_calculation`` refuses UPDATE on an ever-approved
    calculation. None of the 408 on the deployed database is approved, so on
    that database the declaration is inert -- which is exactly why this
    needs a test. An operator instance that *has* approved one of these
    rows is where the difference shows up.
    """
    harness.run("upgrade", _MIGRATION.parent)
    with harness.engine.begin() as conn:
        ids = _seed(conn)
        curator_id = conn.scalar(
            text(
                "INSERT INTO app_user (username, role, is_active) "
                "VALUES (:username, 'curator', true) RETURNING id"
            ),
            {"username": "gaussian-merge-approver"},
        )
        conn.execute(
            text(
                "INSERT INTO record_review "
                "(record_type, record_id, status, reviewed_by, reviewed_at, "
                " first_approved_at) "
                "VALUES (CAST('calculation' AS submission_record_type), :id, "
                "        CAST('approved' AS record_review_status), :curator, "
                "        now(), now())"
            ),
            {"id": ids["on_composite_a"], "curator": curator_id},
        )

    completed = harness.run("upgrade", _MIGRATION.revision, check=False)
    assert completed.returncode == 0, (
        "the migration failed against an approved calculation. The repair "
        "declaration is what is supposed to make this work:\n"
        f"{completed.stdout}\n{completed.stderr}"
    )

    with harness.engine.connect() as conn:
        releases = _releases(conn, ids)
        assert releases["on_composite_a"] == ids["target_release"], (
            "an approved row was skipped rather than repointed"
        )
        assert _ledger(conn, ids["on_composite_a"]) == [
            (
                str(ids["composite_release"]),
                str(ids["target_release"]),
                ["software_release_id"],
                str(ids["on_composite_a"]),
            )
        ], (
            "the approved row is recorded a number of times other than "
            "once -- the guard's own change row and the revision's must "
            "not overlap"
        )


def test_downgrade_restores_exactly_the_calculations_the_upgrade_repointed(
    harness,
) -> None:
    """What the downgrade can, will not, and cannot do.

    **Can**: recreate the composite release and repoint exactly the
    calculations this revision moved, read back from the ledger by primary
    key.

    **Will not**: touch ``on_target_a``/``_b``. After the upgrade they are
    structurally identical to a repointed row -- same ``software_release_id``
    -- and a downgrade that re-derived its targets from that shape would
    move them too. This is the assertion that separates an exact downgrade
    from a shape-derived one.

    **Will not**, second case: touch ``on_composite_b`` once a curator has
    moved it to a different release.

    **Cannot**: un-append the ledger, or bring back the original composite
    row's id. The downgrade adds its own reverse rows and a freshly
    identified release row.
    """
    ids = _upgraded(harness)

    # A curator repoints on_composite_b to some third release after the
    # upgrade.
    with harness.engine.begin() as conn:
        other_software = _new_software(conn, "NWChem")
        moved_on = _new_release(
            conn, software_id=other_software, version="7.0.2", revision=None
        )
        conn.execute(
            text("UPDATE calculation SET software_release_id = :rid WHERE id = :id"),
            {"rid": moved_on, "id": ids["on_composite_b"]},
        )

    harness.run("downgrade", _MIGRATION.parent)

    with harness.engine.connect() as conn:
        releases = _releases(conn, ids)

        recreated = conn.execute(
            text(
                "SELECT sr.id FROM software_release sr "
                "  JOIN software s ON s.id = sr.software_id "
                " WHERE s.name = 'Gaussian' AND sr.version = :version "
                "   AND sr.revision IS NULL"
            ),
            {"version": _COMPOSITE_VERSION},
        ).scalars().all()
        assert len(recreated) == 1, (
            "expected exactly one recreated composite release row, got "
            f"{recreated}"
        )
        recreated_id = recreated[0]
        assert recreated_id != ids["composite_release"], (
            "the recreated row reused the original numeric id -- that id is "
            "not portable across databases and this revision does not rely "
            "on it"
        )

        # Repointed, then un-repointed onto the recreated row.
        assert releases["on_composite_a"] == recreated_id
        # The curator's decision stands.
        assert releases["on_composite_b"] == moved_on
        # Never repointed, so never un-repointed. The whole ballgame.
        assert releases["on_target_a"] == ids["target_release"]
        assert releases["on_target_b"] == ids["target_release"]

        assert _ledger(conn, ids["on_composite_a"]) == [
            (
                str(ids["composite_release"]),
                str(ids["target_release"]),
                ["software_release_id"],
                str(ids["on_composite_a"]),
            ),
            (
                str(ids["target_release"]),
                str(recreated_id),
                ["software_release_id"],
                str(ids["on_composite_a"]),
            ),
        ]
        # Nothing was restored for on_composite_b, so nothing claims it was.
        assert len(_ledger(conn, ids["on_composite_b"])) == 1


def test_downgrade_recreates_the_release_with_recoverable_fields(harness) -> None:
    """``software_id``/``version``/``revision``/``build`` come back exactly.

    ``release_date`` and ``notes`` do not -- nothing this revision records
    captures what they were, and the module docstring says so. Asserted
    here as the honest half of that claim: this seed's composite row never
    had either set, so a correct downgrade recreates it with both NULL, the
    same state it started in.
    """
    ids = _upgraded(harness)
    harness.run("downgrade", _MIGRATION.parent)

    with harness.engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT sr.software_id, sr.version, sr.revision, sr.build, "
                "       sr.release_date, sr.notes "
                "  FROM software_release sr "
                "  JOIN software s ON s.id = sr.software_id "
                " WHERE s.name = 'Gaussian' AND sr.revision IS NULL "
                "   AND sr.version = :version"
            ),
            {"version": _COMPOSITE_VERSION},
        ).one()
        software_id, version, revision_value, build, release_date, notes = row

    with harness.engine.connect() as conn:
        expected_software_id = conn.scalar(
            text("SELECT software_id FROM software_release WHERE id = :id"),
            {"id": ids["target_release"]},
        )

    assert software_id == expected_software_id
    assert version == _COMPOSITE_VERSION
    assert revision_value is None
    assert build is None
    assert release_date is None
    assert notes is None


def test_upgrade_downgrade_upgrade_round_trip_repoints_the_same_calculations(
    harness,
) -> None:
    """Real idempotency: the migration genuinely re-runs, twice.

    Alembic no-ops an already-applied revision, so the only way to execute
    ``upgrade()`` a second time against real data is to downgrade first --
    which is also exactly the operator sequence a rollback-then-reapply
    performs. The calculations this revision repoints must land on a
    release matching the same identity (Gaussian, version="16",
    revision="C.02") both times, and every calculation outside the
    predicate must be untouched by all three runs.
    """
    ids = _upgraded(harness)
    with harness.engine.connect() as conn:
        first_pass = _releases(conn, ids)
    assert first_pass["on_composite_a"] == ids["target_release"]
    assert first_pass["on_composite_b"] == ids["target_release"]

    harness.run("downgrade", _MIGRATION.parent)
    harness.run("upgrade", _MIGRATION.revision)

    with harness.engine.connect() as conn:
        second_pass = _releases(conn, ids)

    # The target release's identity is what round-trips, not its numeric id
    # (the recreated composite row, and therefore nothing else, changes id
    # between passes).
    assert second_pass["on_composite_a"] == ids["target_release"]
    assert second_pass["on_composite_b"] == ids["target_release"]
    for label in _NOT_MOVED:
        assert second_pass[label] == first_pass[label], label

    with harness.engine.connect() as conn:
        ledger = _ledger(conn, ids["on_composite_a"])
    assert len(ledger) == 3, (
        "expected 3 ledger rows for on_composite_a after upgrade, downgrade, "
        f"upgrade: got {ledger}"
    )
    assert ledger[0] == (
        str(ids["composite_release"]),
        str(ids["target_release"]),
        ["software_release_id"],
        str(ids["on_composite_a"]),
    )
    # ledger[1] is the downgrade's reversal onto whatever id the first
    # recreated composite row got; ledger[2] is the second upgrade's
    # repoint back onto the (unchanged) target release. Its "before" is
    # ledger[1]'s "after" -- the recreated row's id -- which this test does
    # not know in advance, so only the fixed half of the row is asserted.
    assert ledger[2][1:] == (
        str(ids["target_release"]),
        ["software_release_id"],
        str(ids["on_composite_a"]),
    )
    assert ledger[1][1] == ledger[2][0], (
        "the second upgrade's 'before' does not match the downgrade's "
        "'after' -- the round trip did not repoint from the row the "
        "downgrade actually recreated"
    )


def test_more_than_one_composite_candidate_is_refused(harness) -> None:
    """A repair that cannot name a single row does not choose one for itself.

    Mirrors ``b8e3f1a7c250``'s ``HAVING count(*) = 1`` refusal for an
    ambiguous anchor: this revision merges exactly one known row into its
    decomposed sibling, and two candidates is a database that does not look
    like the one this repair was measured against.
    """
    harness.run("upgrade", _MIGRATION.parent)
    with harness.engine.begin() as conn:
        gaussian_id = _new_software(conn, "Gaussian")
        _new_release(
            conn, software_id=gaussian_id, version=_COMPOSITE_VERSION, revision=None
        )
        _new_release(
            conn,
            software_id=gaussian_id,
            version="Gaussian 09, Revision D.01",
            revision=None,
        )
        _new_release(conn, software_id=gaussian_id, version="16", revision="C.02")
        _new_release(conn, software_id=gaussian_id, version="09", revision="D.01")

    completed = harness.run("upgrade", _MIGRATION.revision, check=False)
    assert completed.returncode != 0
    assert "will not guess which of several to merge" in (
        completed.stdout + completed.stderr
    )


def test_a_composite_row_with_no_decomposed_sibling_is_refused(harness) -> None:
    """This revision merges into an existing row; it does not fabricate one."""
    harness.run("upgrade", _MIGRATION.parent)
    with harness.engine.begin() as conn:
        gaussian_id = _new_software(conn, "Gaussian")
        _new_release(
            conn, software_id=gaussian_id, version=_COMPOSITE_VERSION, revision=None
        )
        # Deliberately no version="16", revision="C.02" sibling.

    completed = harness.run("upgrade", _MIGRATION.revision, check=False)
    assert completed.returncode != 0
    assert "does not create one" in (completed.stdout + completed.stderr)


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

    Deleting and inserting rows in ``software_release`` is DML, not a
    schema change -- no ``op.create_table``/``op.drop_table``/
    ``op.add_column`` or similar appears anywhere in the executable body.
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
