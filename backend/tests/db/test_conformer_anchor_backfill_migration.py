"""Disposable-database contract for the coarse-pre-optimisation anchoring backfill.

``b8e3f1a7c250`` sets ``calculation.conformer_observation_id`` on species-owned
``opt`` rows that have none, copying the anchor from the single anchored
calculation they are the ``optimized_from`` parent of. Everything here is a way
the backfill could be wrong on a real database while looking perfect on an
empty one, which is why none of it is checked through the service layer.

The seed is built entirely out of near-misses. Exactly two of its ten
calculations should move, and every other one differs from those two by
precisely one clause of the predicate:

======================  =========================================
seed row                the clause it exists to pin
======================  =========================================
``already_anchored``    ``parent.conformer_observation_id IS NULL``
``ambiguous``           ``HAVING count(*) = 1``
``cross_owner``         ``grp.species_entry_id = parent.species_entry_id``
``wrong_role``          ``dependency_role = 'optimized_from'``
``wrong_type``          ``parent.type = 'opt'``
``unanchored_child``    ``refinement.conformer_observation_id IS NOT NULL``
======================  =========================================

Drop any one clause and a row that must not move, moves. That is the whole
design: a predicate whose narrowness nothing tests is a predicate that has
been written down, not established.

``already_anchored`` is the load-bearing one
--------------------------------------------
It is a coarse stage that was anchored correctly all along -- species-owned
``opt``, sitting on the same observation as the refinement it feeds. After the
upgrade it is **structurally identical** to a row this revision repaired, and
there are 20 such rows on the deployed database today (measured). A downgrade
that re-derived its target set from the shape of the data would null all of
them, destroying links the upgrade never made. So this row must come through
the upgrade unchanged *and* survive the downgrade still anchored, and the only
thing that can achieve the second is the repair ledger the upgrade writes.

Transition states are not seeded, deliberately
-----------------------------------------------
124 TS calculations carry a NULL anchor on the deployed database and every one
of them is correct: ``conformer_group.species_entry_id`` is NOT NULL with no TS
counterpart (DR-0004), so there is no observation a TS calculation could be
anchored to. They are excluded from this backfill by
``ck_calculation_one_owner`` -- a calculation has a species entry or a
transition-state entry and never both -- which makes ``species_entry_id IS NOT
NULL`` in the predicate sufficient by database constraint rather than by
argument. ``test_transition_state_calculations_cannot_match_the_predicate``
asserts that constraint rather than seeding a whole reaction chain to
rediscover it.
"""

from __future__ import annotations

import ast
import subprocess
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

from tests.db._migration_chain import revision_under_test

_MIGRATION = revision_under_test("b8e3f1a7c250")

_REVISION_FILE = (
    Path(__file__).resolve().parents[2]
    / "alembic"
    / "versions"
    / "b8e3f1a7c250_anchor_orphaned_coarse_optimisations.py"
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
    created = _MigrationHarness("conformer_anchor")
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


def _new_observation(conn, species_entry_id: int) -> tuple[int, int]:
    """One conformer group under *species_entry_id*, and one observation in it."""
    group_id = conn.scalar(
        text(
            "INSERT INTO conformer_group (species_entry_id) "
            "VALUES (:sid) RETURNING id"
        ),
        {"sid": species_entry_id},
    )
    observation_id = conn.scalar(
        text(
            "INSERT INTO conformer_observation (conformer_group_id) "
            "VALUES (:gid) RETURNING id"
        ),
        {"gid": group_id},
    )
    return group_id, observation_id


def _observation_in(conn, group_id: int) -> int:
    return conn.scalar(
        text(
            "INSERT INTO conformer_observation (conformer_group_id) "
            "VALUES (:gid) RETURNING id"
        ),
        {"gid": group_id},
    )


def _new_calculation(
    conn,
    *,
    species_entry_id: int,
    calc_type: str = "opt",
    observation_id: int | None = None,
) -> int:
    """Write one ``calculation`` row and return its id.

    Raw SQL at the parent revision on purpose: this reproduces rows the old
    write path produced, including the unanchored state the current workflow
    no longer creates.
    """
    return conn.scalar(
        text(
            "INSERT INTO calculation "
            "(type, species_entry_id, conformer_observation_id) "
            "VALUES (CAST(:type AS calc_type), :sid, :oid) RETURNING id"
        ),
        {"type": calc_type, "sid": species_entry_id, "oid": observation_id},
    )


def _new_ts_owned_calculation(
    conn, *, observation_id: int | None = None
) -> int:
    """A transition-state-owned ``opt``, with the reaction chain behind it.

    ``ck_calculation_one_owner`` forces ``species_entry_id`` to be NULL here,
    which is the whole point: this is what a TS calculation looks like to the
    backfill predicate.
    """
    reaction_id = conn.scalar(
        text("INSERT INTO chem_reaction (reversible) VALUES (true) RETURNING id")
    )
    entry_id = conn.scalar(
        text(
            "INSERT INTO reaction_entry (reaction_id) VALUES (:rid) RETURNING id"
        ),
        {"rid": reaction_id},
    )
    ts_id = conn.scalar(
        text(
            "INSERT INTO transition_state (reaction_entry_id) "
            "VALUES (:eid) RETURNING id"
        ),
        {"eid": entry_id},
    )
    ts_entry_id = conn.scalar(
        text(
            "INSERT INTO transition_state_entry "
            "(transition_state_id, charge, multiplicity) "
            "VALUES (:tsid, 0, 2) RETURNING id"
        ),
        {"tsid": ts_id},
    )
    return conn.scalar(
        text(
            "INSERT INTO calculation "
            "(type, transition_state_entry_id, conformer_observation_id) "
            "VALUES (CAST('opt' AS calc_type), :tsid, :oid) RETURNING id"
        ),
        {"tsid": ts_entry_id, "oid": observation_id},
    )


def _link(conn, *, parent: int, child: int, role: str = "optimized_from") -> None:
    conn.execute(
        text(
            "INSERT INTO calculation_dependency "
            "(parent_calculation_id, child_calculation_id, dependency_role) "
            "VALUES (:p, :c, CAST(:role AS calculation_dependency_role))"
        ),
        {"p": parent, "c": child, "role": role},
    )


def _seed(conn) -> dict[str, int]:
    """Two rows that must move, and six near-misses that must not."""
    ids: dict[str, int] = {}

    # --- The population the revision exists for -------------------------
    # A coarse stage with no anchor, feeding one refinement that has one.
    entry_a = _new_species_entry(conn, "CC", "AAAAAAAAAAAAAA-UHFFFAOYSA-N")
    _, obs_a = _new_observation(conn, entry_a)
    ids["target_obs_a"] = obs_a
    ids["coarse_a"] = _new_calculation(conn, species_entry_id=entry_a)
    fine_a = _new_calculation(
        conn, species_entry_id=entry_a, observation_id=obs_a
    )
    _link(conn, parent=ids["coarse_a"], child=fine_a)

    entry_b = _new_species_entry(conn, "CCC", "BBBBBBBBBBBBBB-UHFFFAOYSA-N")
    group_b, obs_b = _new_observation(conn, entry_b)
    ids["target_obs_b"] = obs_b
    ids["coarse_b"] = _new_calculation(conn, species_entry_id=entry_b)
    fine_b = _new_calculation(
        conn, species_entry_id=entry_b, observation_id=obs_b
    )
    _link(conn, parent=ids["coarse_b"], child=fine_b)

    # --- Near-miss 1: already anchored ----------------------------------
    # Correct since the day it was written, and after the upgrade it is
    # indistinguishable from a repaired row. The downgrade must not null it.
    ids["already_anchored"] = _new_calculation(
        conn, species_entry_id=entry_b, observation_id=obs_b
    )
    fine_already = _new_calculation(
        conn, species_entry_id=entry_b, observation_id=obs_b
    )
    _link(conn, parent=ids["already_anchored"], child=fine_already)

    # --- Near-miss 2: two anchored children, on two observations --------
    # No unambiguous answer, so this revision does not invent one.
    entry_c = _new_species_entry(conn, "CCCC", "CCCCCCCCCCCCCC-UHFFFAOYSA-N")
    group_c, obs_c1 = _new_observation(conn, entry_c)
    obs_c2 = _observation_in(conn, group_c)
    ids["ambiguous"] = _new_calculation(conn, species_entry_id=entry_c)
    _link(
        conn,
        parent=ids["ambiguous"],
        child=_new_calculation(
            conn, species_entry_id=entry_c, observation_id=obs_c1
        ),
    )
    _link(
        conn,
        parent=ids["ambiguous"],
        child=_new_calculation(
            conn, species_entry_id=entry_c, observation_id=obs_c2
        ),
    )

    # --- Near-miss 3: the observation belongs to another species --------
    # A malformed edge must not file this calculation under a basin its own
    # species does not own.
    entry_d = _new_species_entry(conn, "CCCCC", "DDDDDDDDDDDDDD-UHFFFAOYSA-N")
    ids["cross_owner"] = _new_calculation(conn, species_entry_id=entry_d)
    _link(
        conn,
        parent=ids["cross_owner"],
        # Child owned by entry_d, but anchored into entry_a's group.
        child=_new_calculation(
            conn, species_entry_id=entry_d, observation_id=obs_a
        ),
    )

    # --- Near-miss 4: the wrong dependency role -------------------------
    # A frequency job on an optimised geometry is different evidence, not an
    # earlier stage; an anchor must not travel along that edge.
    entry_e = _new_species_entry(conn, "CCCCCC", "EEEEEEEEEEEEEE-UHFFFAOYSA-N")
    group_e, obs_e = _new_observation(conn, entry_e)
    ids["wrong_role"] = _new_calculation(conn, species_entry_id=entry_e)
    _link(
        conn,
        parent=ids["wrong_role"],
        child=_new_calculation(
            conn,
            species_entry_id=entry_e,
            calc_type="freq",
            observation_id=obs_e,
        ),
        role="freq_on",
    )

    # --- Near-miss 5: the parent is not an opt --------------------------
    ids["wrong_type"] = _new_calculation(
        conn, species_entry_id=entry_e, calc_type="freq"
    )
    _link(
        conn,
        parent=ids["wrong_type"],
        child=_new_calculation(
            conn, species_entry_id=entry_e, observation_id=obs_e
        ),
    )

    # --- Near-miss 6: the child has no anchor either --------------------
    # Nothing to copy. The revision invents nothing.
    entry_f = _new_species_entry(conn, "CCCCCCC", "FFFFFFFFFFFFFF-UHFFFAOYSA-N")
    _new_observation(conn, entry_f)
    ids["unanchored_child"] = _new_calculation(conn, species_entry_id=entry_f)
    _link(
        conn,
        parent=ids["unanchored_child"],
        child=_new_calculation(conn, species_entry_id=entry_f),
    )

    # --- Near-miss 7: owned by a transition state, not a species --------
    # Correctly unanchored: a conformer group requires a species entry and
    # has no TS counterpart (DR-0004), so there is nothing for this to be
    # anchored to. 124 rows on the deployed database look like this. Its
    # anchored child is contrived -- nothing writes one today -- and that is
    # the point: the predicate must exclude this row even when every other
    # clause it can satisfy is satisfied.
    ids["ts_owned"] = _new_ts_owned_calculation(conn)
    _link(
        conn,
        parent=ids["ts_owned"],
        child=_new_ts_owned_calculation(conn, observation_id=obs_a),
    )

    return ids


#: What must move, and what its anchor must become.
_MOVED = ("coarse_a", "coarse_b")

#: What must not move, and the clause each one pins.
_NOT_MOVED = (
    "ambiguous",
    "cross_owner",
    "wrong_role",
    "wrong_type",
    "unanchored_child",
    "ts_owned",
)


def _anchors(conn, ids: dict[str, int]) -> dict[str, int | None]:
    """Current anchor of every seeded calculation, keyed by its seed label."""
    by_id = {
        row[0]: row[1]
        for row in conn.execute(
            text("SELECT id, conformer_observation_id FROM calculation")
        ).all()
    }
    return {
        label: by_id[calc_id]
        for label, calc_id in ids.items()
        if not label.startswith("target_obs_")
    }


def _ledger(conn, calculation_id: int) -> list[tuple]:
    """This revision's repair-change rows for one calculation, oldest first."""
    return [
        tuple(row)
        for row in conn.execute(
            text(
                "SELECT change.before_json ->> 'conformer_observation_id', "
                "       change.after_json ->> 'conformer_observation_id', "
                "       change.changed_columns, "
                "       change.row_identity ->> 'id' "
                "  FROM accepted_science_repair_change AS change "
                "  JOIN accepted_science_repair AS declaration "
                "    ON declaration.id = change.repair_id "
                " WHERE declaration.alembic_revision = 'b8e3f1a7c250' "
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


def test_only_unanchored_coarse_stages_move(harness) -> None:
    """The predicate, clause by clause.

    Every row in ``_NOT_MOVED`` is one clause away from being repaired, so
    this fails whichever clause a future edit drops. ``already_anchored`` is
    listed separately below because its anchor is unchanged rather than
    absent, which is a different assertion about a different risk.
    """
    ids = _upgraded(harness)
    with harness.engine.connect() as conn:
        anchors = _anchors(conn, ids)

    assert anchors["coarse_a"] == ids["target_obs_a"]
    assert anchors["coarse_b"] == ids["target_obs_b"]
    assert anchors["already_anchored"] == ids["target_obs_b"]
    for label in _NOT_MOVED:
        assert anchors[label] is None, (
            f"{label} was anchored by the backfill. It differs from a "
            f"repairable row by exactly one clause of the predicate, so the "
            f"predicate has been widened."
        )


def test_each_moved_row_is_recorded_once_in_the_repair_ledger(harness) -> None:
    """The ledger says what happened, once per row, and nothing else does.

    Without these rows the downgrade has no way to tell a row it repaired
    from one that was correct all along -- see the module docstring. The
    assertion that ``already_anchored`` has *no* entry is half the point: the
    ledger records what the upgrade wrote, not what merely resembles it.
    """
    ids = _upgraded(harness)
    with harness.engine.connect() as conn:
        for label in _MOVED:
            target = ids["target_obs_a" if label == "coarse_a" else "target_obs_b"]
            assert _ledger(conn, ids[label]) == [
                (
                    None,
                    str(target),
                    ["conformer_observation_id"],
                    str(ids[label]),
                )
            ], label
        for label in ("already_anchored", *_NOT_MOVED):
            assert _ledger(conn, ids[label]) == [], label


def test_downgrade_restores_exactly_the_rows_the_upgrade_anchored(
    harness,
) -> None:
    """What the downgrade can, will not, and cannot do.

    **Can**: null exactly the anchors this revision set, read back from the
    ledger by primary key.

    **Will not**: touch ``already_anchored``. After the upgrade it is
    structurally identical to a repaired row -- same type, same owner, same
    observation as its refinement -- and a downgrade that re-derived its
    targets from that shape would destroy it. This single assertion is what
    separates an exact downgrade from an approximate one.

    **Will not**, second case: touch ``coarse_b`` once a curator has moved it
    to a different observation. It still carries the marker, but the value is
    no longer this revision's to overwrite.

    **Cannot**: un-append the ledger. Both repair tables are append-only, so
    the downgrade adds its own reverse rows and the database reads as two
    recorded transitions.
    """
    ids = _upgraded(harness)

    # A curator re-anchors coarse_b somewhere else after the upgrade.
    with harness.engine.begin() as conn:
        moved_on = _observation_in(
            conn,
            conn.scalar(
                text(
                    "SELECT conformer_group_id FROM conformer_observation "
                    "WHERE id = :oid"
                ),
                {"oid": ids["target_obs_b"]},
            ),
        )
        conn.execute(
            text(
                "UPDATE calculation SET conformer_observation_id = :oid "
                "WHERE id = :id"
            ),
            {"oid": moved_on, "id": ids["coarse_b"]},
        )

    harness.run("downgrade", _MIGRATION.parent)

    with harness.engine.connect() as conn:
        anchors = _anchors(conn, ids)

        # Repaired, then un-repaired.
        assert anchors["coarse_a"] is None
        # The curator's decision stands.
        assert anchors["coarse_b"] == moved_on
        # Never repaired, so never un-repaired. The whole ballgame.
        assert anchors["already_anchored"] == ids["target_obs_b"]
        for label in _NOT_MOVED:
            assert anchors[label] is None, label

        assert _ledger(conn, ids["coarse_a"]) == [
            (
                None,
                str(ids["target_obs_a"]),
                ["conformer_observation_id"],
                str(ids["coarse_a"]),
            ),
            (
                str(ids["target_obs_a"]),
                None,
                ["conformer_observation_id"],
                str(ids["coarse_a"]),
            ),
        ]
        # Nothing was restored for coarse_b, so nothing claims it was.
        assert len(_ledger(conn, ids["coarse_b"])) == 1


def test_an_approved_row_is_repaired_and_recorded_once(harness) -> None:
    """The path the repair declaration exists for, exercised for real.

    ``trg_as_root_calculation`` refuses UPDATE on an ever-approved
    calculation. None of the 43 rows on the deployed database is approved, so
    on that database the declaration is inert and this path never runs -- which
    is exactly why it needs a test. An operator instance that *has* approved
    one of these rows is where the difference shows up, and the difference is
    between a recorded repair and a migration that dies partway through.

    Two things are asserted, and the second is the subtle one:

    * the upgrade **succeeds** and anchors the approved row, rather than
      raising the guard's refusal;
    * the row is recorded in the ledger **once**, not twice.
      ``tckdb_repair_permits`` writes a change row itself for an accepted
      record, and the revision writes one for every row the guard did *not*
      cover. Those two sets have to be disjoint. If the revision's
      ``NOT tckdb_record_is_accepted(...)`` filter were dropped, this row
      would be recorded twice and the downgrade would claim to have restored
      it twice.
    """
    harness.run("upgrade", _MIGRATION.parent)
    with harness.engine.begin() as conn:
        ids = _seed(conn)
        # Approve the calculation the backfill is about to repair.
        conn.execute(
            text(
                "INSERT INTO record_review "
                "(record_type, record_id, status, reviewed_at, "
                " first_approved_at) "
                "VALUES (CAST('calculation' AS submission_record_type), :id, "
                "        CAST('approved' AS record_review_status), now(), "
                "        now())"
            ),
            {"id": ids["coarse_a"]},
        )

    completed = harness.run("upgrade", _MIGRATION.revision, check=False)
    assert completed.returncode == 0, (
        "the migration failed against an approved calculation. The repair "
        "declaration is what is supposed to make this work:\n"
        f"{completed.stdout}\n{completed.stderr}"
    )

    with harness.engine.connect() as conn:
        anchors = _anchors(conn, ids)
        assert anchors["coarse_a"] == ids["target_obs_a"], (
            "an approved row was skipped rather than repaired"
        )
        assert _ledger(conn, ids["coarse_a"]) == [
            (
                None,
                str(ids["target_obs_a"]),
                ["conformer_observation_id"],
                str(ids["coarse_a"]),
            )
        ], (
            "the approved row is recorded a number of times other than once "
            "-- the guard's own change row and the revision's must not overlap"
        )


def test_the_repair_declaration_names_one_column(harness) -> None:
    """The guard is stood down for ``conformer_observation_id`` and nothing else.

    ``tckdb_raise_if_accepted`` compares OLD against NEW and refuses an
    UPDATE touching an undeclared column, so this is the enforced bound on
    what either direction of this revision can write to an accepted
    calculation -- not a promise in a docstring.
    """
    _upgraded(harness)
    with harness.engine.connect() as conn:
        declared = conn.execute(
            text(
                "SELECT declared_columns, target_table, target_schema "
                "  FROM accepted_science_repair "
                " WHERE alembic_revision = 'b8e3f1a7c250'"
            )
        ).all()
    assert declared, "the revision declared no repair"
    for columns, table, schema in declared:
        assert columns == ["conformer_observation_id"]
        assert (schema, table) == ("public", "calculation")


def test_transition_state_calculations_cannot_match_the_predicate(
    harness,
) -> None:
    """TS calculations are excluded by a database constraint, not by luck.

    124 of them carry a NULL anchor on the deployed database and every one is
    correct -- a conformer group requires a species entry (DR-0004), so there
    is nothing for a TS calculation to be anchored to. The predicate's
    ``species_entry_id IS NOT NULL`` is sufficient because
    ``ck_calculation_one_owner`` makes a TS-owned calculation's
    ``species_entry_id`` necessarily NULL. Asserted here so that relaxing the
    ownership constraint fails this test rather than silently widening a
    backfill that has already shipped.
    """
    ids = _upgraded(harness)
    with harness.engine.connect() as conn:
        definition = conn.scalar(
            text(
                "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                " WHERE conrelid = 'calculation'::regclass "
                "   AND conname = 'ck_calculation_one_owner'"
            )
        )
    assert definition is not None, "ck_calculation_one_owner is gone"
    normalised = " ".join(definition.split())
    assert "transition_state_entry_id IS NOT NULL" in normalised
    assert "species_entry_id IS NULL" in normalised

    # The behavioural half, which is what makes this test able to fail.
    # ``ts_owned`` is seeded with every other clause of the predicate
    # satisfied -- an unanchored ``opt`` that is the sole ``optimized_from``
    # parent of an anchored calculation -- and differs only in being owned by
    # a transition-state entry.
    #
    # An earlier draft asserted the clause's *text* appeared in the revision
    # instead. A mutation that deleted the clause and left the same words in
    # an adjacent SQL comment passed it, which is the vacuous-pass failure in
    # miniature: the check ran, verified nothing, and went green.
    with harness.engine.connect() as conn:
        anchor = conn.scalar(
            text(
                "SELECT conformer_observation_id FROM calculation "
                " WHERE id = :id"
            ),
            {"id": ids["ts_owned"]},
        )
    assert anchor is None, (
        "a transition-state-owned calculation was anchored to a conformer "
        "observation; a conformer group requires a species entry, so there "
        "is no basin this row could belong to"
    )


def _executable_source() -> str:
    """The revision file with its module docstring removed.

    ``ast`` rather than a text heuristic, so this cannot be fooled by prose
    that happens to look like the docstring's delimiters.
    """
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

    Read off the file rather than inferred from a passing upgrade: an added
    column would migrate an empty test database perfectly well and would still
    change what an operator has to plan for on the deployed one.

    Scanned over the revision's *code*, with its module docstring removed --
    that docstring discusses ``ALTER TABLE ... DISABLE TRIGGER`` in order to
    explain what the repair declaration replaces, and a guard that cannot
    tell an explanation from an instruction would force the explanation out.
    The docstring is the one region of the file that provably executes
    nothing; every string a statement could reach is still scanned.
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
            "migration; the operator runbook and the PR both say no schema "
            "changes, and one of the three is now wrong."
        )
