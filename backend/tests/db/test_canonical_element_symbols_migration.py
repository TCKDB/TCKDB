"""Disposable-database contract for the element-symbol canonicalisation.

``b4e7c1d20f83`` rewrites ``geometry_atom.element`` (and the two element
columns of ``reaction_atom_map_pair`` that mirror it into composite foreign
keys) into one spelling per element. Four things about that have to be
checkable without going anywhere near the service layer, because each one is
a way the backfill could go wrong on a real database and look fine on an empty
one:

1. **The rows actually change**, on both sides, in one transaction. The
   element column is part of the key ``reaction_atom_map_pair`` references, so
   neither table can be updated first without breaking referential integrity
   mid-migration. If the deferral is wrong, this fails.
2. **``geom_hash`` does not change, and neither does ``xyz_text``.**
   ``geom_hash`` is the dedupe key *and* a citable public ref
   (``geometry:geom_hash=...``); re-keying it would dangle published
   references and stop a re-upload of the same file deduping onto its own row.
   The deposited spelling survives in ``xyz_text``, which is what makes the
   divergence between the two columns a design decision rather than drift.
3. **A non-canonical row inside an accepted calculation stops the
   migration, loudly.** ``trg_as_geometry_atom`` refuses updates to a geometry
   attached to an accepted calculation, and this revision leaves that guard
   **standing**. Failing is the designed outcome: rewriting a value inside
   approved science is the operator's decision, not a migration's, and nothing
   downstream needs the rewrite because comparisons still normalise. The
   earlier draft of this revision disabled the guard, so the test asserts the
   failure happens rather than merely that the data is fine afterwards.
4. **The revision contains no ``DISABLE TRIGGER``.** Read straight off the
   file, so that standing the guard down cannot come back by accident in a
   later edit and pass on the strength of an empty test database.

``D`` is seeded alongside the shouted symbols to pin the other half of the
policy: case is canonicalised, isotope labelling is not.
"""

import subprocess
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError

BASE_REVISION = "d7f1a3c5e948"
CANONICAL_REVISION = "b4e7c1d20f83"

#: What an ESS that shouts its elements actually deposits. The reactant is
#: written ``CL``/``c``, the saddle point ``Cl``/``C``: one element, two
#: programs, and before this revision two different ``character(2)`` values.
_REACTANT_XYZ = "3\n\nCL 0 0 0\nc 0 0 1.8\nD 0 0 2.9"
_TS_XYZ = "3\n\nCl 0 0 0\nC 0 0 2.1\nD 0 0 3.2"
_REACTANT_HASH = "e1" * 32
_TS_HASH = "e2" * 32


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
    created = _MigrationHarness("element_migration")
    yield created
    created.close()


def _seed_geometry(conn, *, geom_hash: str, xyz_text: str, elements) -> int:
    geometry_id = conn.scalar(
        text(
            "INSERT INTO geometry (natoms, geom_hash, xyz_text) "
            "VALUES (:natoms, :geom_hash, :xyz_text) RETURNING id"
        ),
        {"natoms": len(elements), "geom_hash": geom_hash, "xyz_text": xyz_text},
    )
    for index, element in enumerate(elements, start=1):
        conn.execute(
            text(
                "INSERT INTO geometry_atom "
                "(geometry_id, atom_index, element, x, y, z) "
                "VALUES (:geometry_id, :atom_index, :element, 0, 0, :z)"
            ),
            {
                "geometry_id": geometry_id,
                "atom_index": index,
                "element": element,
                "z": float(index),
            },
        )
    return geometry_id


def _seed(conn, *, accepted: bool = False) -> dict[str, int]:
    """Write a mapped reaction whose geometries shout their elements.

    Deliberately built with raw SQL at ``BASE_REVISION``: the point is to
    reproduce rows that the *old* ingestion path could write, which the new one
    can no longer produce.

    :param accepted: Approve the calculation that owns the reactant geometry,
        arming ``trg_as_geometry_atom`` over its atoms. The backfill must then
        fail rather than rewrite them.
    """
    suffix = uuid4().hex[:8]

    species_id = conn.scalar(
        text(
            "INSERT INTO species "
            "(kind, smiles, inchi_key, charge, multiplicity, stereo_kind) "
            "VALUES ('molecule', 'CCl', :inchi_key, 0, 1, 'achiral') RETURNING id"
        ),
        {"inchi_key": f"ELEMENTMIG{suffix}AAAAAAAAAAA"[:27]},
    )
    species_entry_id = conn.scalar(
        text(
            "INSERT INTO species_entry (species_id, unmapped_smiles) "
            "VALUES (:species_id, 'CCl') RETURNING id"
        ),
        {"species_id": species_id},
    )
    reaction_id = conn.scalar(
        text("INSERT INTO chem_reaction (reversible) VALUES (true) RETURNING id")
    )
    reaction_entry_id = conn.scalar(
        text(
            "INSERT INTO reaction_entry (reaction_id) VALUES (:reaction_id) "
            "RETURNING id"
        ),
        {"reaction_id": reaction_id},
    )
    participant_id = conn.scalar(
        text(
            "INSERT INTO reaction_entry_structure_participant "
            "(reaction_entry_id, species_entry_id, role, participant_index) "
            "VALUES (:reaction_entry_id, :species_entry_id, 'reactant', 1) "
            "RETURNING id"
        ),
        {
            "reaction_entry_id": reaction_entry_id,
            "species_entry_id": species_entry_id,
        },
    )
    transition_state_id = conn.scalar(
        text(
            "INSERT INTO transition_state (reaction_entry_id) "
            "VALUES (:reaction_entry_id) RETURNING id"
        ),
        {"reaction_entry_id": reaction_entry_id},
    )
    ts_entry_id = conn.scalar(
        text(
            "INSERT INTO transition_state_entry "
            "(transition_state_id, charge, multiplicity) "
            "VALUES (:transition_state_id, 0, 1) RETURNING id"
        ),
        {"transition_state_id": transition_state_id},
    )

    reactant_geometry_id = _seed_geometry(
        conn,
        geom_hash=_REACTANT_HASH,
        xyz_text=_REACTANT_XYZ,
        elements=["CL", "c", "D"],
    )
    ts_geometry_id = _seed_geometry(
        conn,
        geom_hash=_TS_HASH,
        xyz_text=_TS_XYZ,
        elements=["Cl", "C", "D"],
    )

    atom_map_id = conn.scalar(
        text(
            "INSERT INTO reaction_atom_map "
            "(reaction_entry_id, transition_state_entry_id, "
            " transition_state_geometry_id, source) "
            "VALUES (:reaction_entry_id, :ts_entry_id, :ts_geometry_id, "
            " 'declared') RETURNING id"
        ),
        {
            "reaction_entry_id": reaction_entry_id,
            "ts_entry_id": ts_entry_id,
            "ts_geometry_id": ts_geometry_id,
        },
    )
    for atom_index, (element, ts_element) in enumerate(
        (("CL", "Cl"), ("c", "C"), ("D", "D")), start=1
    ):
        conn.execute(
            text(
                "INSERT INTO reaction_atom_map_pair "
                "(atom_map_id, side, structure_participant_id, geometry_id, "
                " atom_index, transition_state_geometry_id, ts_atom_index, "
                " element, ts_element) "
                "VALUES (:atom_map_id, 'reactant', :participant_id, "
                " :geometry_id, :atom_index, :ts_geometry_id, :atom_index, "
                " :element, :ts_element)"
            ),
            {
                "atom_map_id": atom_map_id,
                "participant_id": participant_id,
                "geometry_id": reactant_geometry_id,
                "atom_index": atom_index,
                "ts_geometry_id": ts_geometry_id,
                "element": element,
                "ts_element": ts_element,
            },
        )

    # The reactant geometry is the output of a calculation. Whether anybody
    # approved it decides whether the backfill is allowed to touch its atoms.
    # Everything above had to exist before the approval, because the guard
    # refuses writes to `geometry_atom` from the moment it lands.
    calculation_id = conn.scalar(
        text(
            "INSERT INTO calculation (type, species_entry_id) "
            "VALUES ('opt', :species_entry_id) RETURNING id"
        ),
        {"species_entry_id": species_entry_id},
    )
    conn.execute(
        text(
            "INSERT INTO calculation_output_geometry "
            "(calculation_id, geometry_id, output_order) "
            "VALUES (:calculation_id, :geometry_id, 1)"
        ),
        {"calculation_id": calculation_id, "geometry_id": reactant_geometry_id},
    )
    if accepted:
        reviewer_id = conn.scalar(
            text(
                "INSERT INTO app_user (username, role) "
                "VALUES (:username, 'curator') RETURNING id"
            ),
            {"username": f"element-migration-reviewer-{suffix}"},
        )
        conn.execute(
            text(
                "INSERT INTO record_review "
                "(record_type, record_id, status, reviewed_by, reviewed_at, "
                " first_approved_at) "
                "VALUES ('calculation', :calculation_id, 'approved', "
                " :reviewer_id, now(), now())"
            ),
            {"calculation_id": calculation_id, "reviewer_id": reviewer_id},
        )

    return {
        "reactant_geometry_id": reactant_geometry_id,
        "ts_geometry_id": ts_geometry_id,
        "atom_map_id": atom_map_id,
        "calculation_id": calculation_id,
    }


def _elements(conn, geometry_id: int) -> list[str]:
    return [
        row[0].strip()
        for row in conn.execute(
            text(
                "SELECT element FROM geometry_atom WHERE geometry_id = :id "
                "ORDER BY atom_index"
            ),
            {"id": geometry_id},
        )
    ]


def _assert_guard_refuses(harness, geometry_id: int, element: str) -> None:
    """The accepted-science guard stops an ordinary UPDATE.

    Rolled back explicitly rather than through ``engine.begin()``: the
    statement aborts the transaction, and committing an aborted one is not
    what this is checking.
    """
    with harness.engine.connect() as conn:
        transaction = conn.begin()
        try:
            with pytest.raises(DBAPIError) as excinfo:
                conn.execute(
                    text(
                        "UPDATE geometry_atom SET element = :element "
                        "WHERE geometry_id = :id AND atom_index = 1"
                    ),
                    {"id": geometry_id, "element": element},
                )
            assert "is immutable" in str(excinfo.value)
        finally:
            transaction.rollback()


def test_the_revision_leaves_the_accepted_science_guard_alone() -> None:
    """No ``DISABLE TRIGGER``, asserted off the file rather than the database.

    An earlier draft of this revision disabled ``trg_as_geometry_atom`` to push
    the backfill through accepted rows. On a database with no non-canonical
    accepted rows -- which is every one we can see, including the deployed
    Pi -- that is indistinguishable from not disabling it, so no behavioural
    test can tell the two apart. Reading the source can.
    """
    revision = (
        Path(__file__).resolve().parents[2]
        / "alembic"
        / "versions"
        / "b4e7c1d20f83_canonical_element_symbols.py"
    ).read_text()
    body = revision.split('"""', 2)[2]
    assert "DISABLE TRIGGER" not in body.upper()
    assert "ENABLE TRIGGER" not in body.upper()
    assert "SESSION_REPLICATION_ROLE" not in body.upper()


def test_element_symbols_are_canonicalised_without_re_keying_a_geometry(
    harness,
) -> None:
    harness.run("upgrade", BASE_REVISION)
    with harness.engine.begin() as conn:
        seeded = _seed(conn)

        # The pre-migration state this revision exists to remove: one element,
        # two spellings.
        assert _elements(conn, seeded["reactant_geometry_id"]) == ["CL", "c", "D"]
        assert _elements(conn, seeded["ts_geometry_id"]) == ["Cl", "C", "D"]

    harness.run("upgrade", CANONICAL_REVISION)

    with harness.engine.begin() as conn:
        # 1. Both tables moved, and the composite foreign keys still resolve --
        #    which they could not if only one side had been rewritten.
        assert _elements(conn, seeded["reactant_geometry_id"]) == ["Cl", "C", "D"]
        assert _elements(conn, seeded["ts_geometry_id"]) == ["Cl", "C", "D"]

        pairs = conn.execute(
            text(
                "SELECT atom_index, btrim(element), btrim(ts_element) "
                "FROM reaction_atom_map_pair WHERE atom_map_id = :id "
                "ORDER BY atom_index"
            ),
            {"id": seeded["atom_map_id"]},
        ).all()
        assert pairs == [(1, "Cl", "Cl"), (2, "C", "C"), (3, "D", "D")]

        assert conn.scalar(
            text(
                "SELECT count(*) FROM reaction_atom_map_pair p "
                "JOIN geometry_atom a ON a.geometry_id = p.geometry_id "
                " AND a.atom_index = p.atom_index AND a.element = p.element "
                "WHERE p.atom_map_id = :id"
            ),
            {"id": seeded["atom_map_id"]},
        ) == 3

        # 2. The geometry is the same geometry: same hash, same public ref,
        #    same deposited text -- ``CL`` and ``c`` still readable in it.
        stored = conn.execute(
            text("SELECT geom_hash, xyz_text FROM geometry WHERE id = :id"),
            {"id": seeded["reactant_geometry_id"]},
        ).one()
        assert stored.geom_hash.strip() == _REACTANT_HASH
        assert stored.xyz_text == _REACTANT_XYZ

        # 3/4. The guard is enabled, and was never otherwise: the revision does
        #      not touch it, so a migration that ran to completion proves the
        #      rows it rewrote were ones it was permitted to rewrite.
        assert conn.scalar(
            text(
                "SELECT tgenabled = 'O' FROM pg_trigger "
                "WHERE tgname = 'trg_as_geometry_atom'"
            )
        )


def test_a_non_canonical_row_inside_accepted_science_stops_the_migration(
    harness,
) -> None:
    """Failing here is the feature, not the shortcoming.

    Same seed, one difference: the calculation that owns the reactant geometry
    has been approved, so ``trg_as_geometry_atom`` stands over its atoms. The
    revision does not stand the guard down, so the backfill trips it and the
    whole migration rolls back.

    That is the correct outcome. Rewriting a value inside an approved record is
    a decision for the operator holding that database, not for a migration
    executing unattended, and nothing downstream depends on the rewrite: the
    code shipping with this revision still normalises at comparison time, so a
    row left spelled ``CL`` reads correctly everywhere it is compared.

    Both halves are asserted -- that it fails, *and* that the data is untouched
    -- because a migration that failed after a partial rewrite would be worse
    than either outcome.
    """
    harness.run("upgrade", BASE_REVISION)
    with harness.engine.begin() as conn:
        seeded = _seed(conn, accepted=True)

    _assert_guard_refuses(harness, seeded["reactant_geometry_id"], "Cl")

    refused = harness.run("upgrade", CANONICAL_REVISION, check=False)
    assert refused.returncode != 0
    assert "is immutable" in refused.stderr

    with harness.engine.begin() as conn:
        # Untouched, both tables, and still at the previous revision.
        assert _elements(conn, seeded["reactant_geometry_id"]) == ["CL", "c", "D"]
        pairs = conn.execute(
            text(
                "SELECT btrim(element), btrim(ts_element) "
                "FROM reaction_atom_map_pair WHERE atom_map_id = :id "
                "ORDER BY atom_index"
            ),
            {"id": seeded["atom_map_id"]},
        ).all()
        assert pairs == [("CL", "Cl"), ("c", "C"), ("D", "D")]
        assert conn.scalar(text("SELECT version_num FROM alembic_version")) == (
            BASE_REVISION
        )

    # And the guard is exactly as it was: never disabled, never left down.
    with harness.engine.begin() as conn:
        assert conn.scalar(
            text(
                "SELECT tgenabled = 'O' FROM pg_trigger "
                "WHERE tgname = 'trg_as_geometry_atom'"
            )
        )
    _assert_guard_refuses(harness, seeded["reactant_geometry_id"], "Br")


def test_downgrade_is_a_documented_no_op_and_upgrade_is_idempotent(harness) -> None:
    """The casing cannot be restored, so the revision does not pretend to.

    What must hold is that the round trip is *safe*: downgrading leaves a
    schema and a dataset the previous revision's code handles correctly (it
    normalised at comparison time, so canonical rows are what it already
    coped with), and upgrading again over already-canonical rows is a no-op
    rather than a second rewrite.
    """
    harness.run("upgrade", BASE_REVISION)
    with harness.engine.begin() as conn:
        seeded = _seed(conn)

    harness.run("upgrade", CANONICAL_REVISION)
    with harness.engine.begin() as conn:
        canonical = _elements(conn, seeded["reactant_geometry_id"])
    assert canonical == ["Cl", "C", "D"]

    harness.run("downgrade", BASE_REVISION)
    with harness.engine.begin() as conn:
        assert _elements(conn, seeded["reactant_geometry_id"]) == canonical
        assert conn.scalar(
            text("SELECT geom_hash FROM geometry WHERE id = :id"),
            {"id": seeded["reactant_geometry_id"]},
        ).strip() == _REACTANT_HASH

    harness.run("upgrade", CANONICAL_REVISION)
    with harness.engine.begin() as conn:
        assert _elements(conn, seeded["reactant_geometry_id"]) == canonical
