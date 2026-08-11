"""Disposable-database contract for the atom-resolved isotope migration.

Proves three things a reviewer must be able to check independently of the
service layer:

1. an all-standard species entry that predates the migration keeps its
   identity across upgrade → downgrade → upgrade (no backfill needed, no
   silent re-keying);
2. isotopically resolved entries and per-atom nuclides survive the round
   trip in the schema shape the ORM expects;
3. the collision guard fires — with an actionable message naming the
   affected species — on a deployment where ``isotopologue_label`` is the
   only thing separating two entries.
"""

import subprocess
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

BASE_REVISION = "c1d2e3f4a5b6"
ISOTOPE_REVISION = "d2e3f4a5b6c7"

_UQ_DEF = text(
    "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
    "WHERE conname = 'uq_species_entry_species_id'"
)


def _seed_species(conn, smiles: str, inchi_key: str) -> int:
    return conn.scalar(
        text(
            "INSERT INTO species "
            "(kind, smiles, inchi_key, charge, multiplicity, stereo_kind) "
            "VALUES ('molecule', :smiles, :inchi_key, 0, 1, 'achiral') RETURNING id"
        ),
        {"smiles": smiles, "inchi_key": inchi_key},
    )


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
def harness(request):
    created = _MigrationHarness("iso_migration")
    yield created
    created.close()


def test_isotope_identity_upgrade_downgrade_contract(harness) -> None:
    harness.run("upgrade", BASE_REVISION)
    with harness.engine.begin() as conn:
        species_id = _seed_species(conn, "CO", "ISOMIGRATION00000000000000A")
        # A pre-existing, all-standard entry. Nothing about it mentions
        # isotopes, which is true of every row on the deployed database.
        legacy_entry_id = conn.scalar(
            text(
                "INSERT INTO species_entry (species_id, unmapped_smiles) "
                "VALUES (:species_id, 'CO') RETURNING id"
            ),
            {"species_id": species_id},
        )
        geometry_id = conn.scalar(
            text(
                "INSERT INTO geometry (natoms, geom_hash, xyz_text) "
                "VALUES (2, :geom_hash, '2\n\nC 0 0 0\nO 0 0 1.4') RETURNING id"
            ),
            {"geom_hash": "0" * 64},
        )
        conn.execute(
            text(
                "INSERT INTO geometry_atom (geometry_id, atom_index, element, x, y, z) "
                "VALUES (:geometry_id, 1, 'C', 0, 0, 0), (:geometry_id, 2, 'O', 0, 0, 1.4)"
            ),
            {"geometry_id": geometry_id},
        )
        assert "isotopologue_label" in conn.scalar(_UQ_DEF)

    harness.run("upgrade", ISOTOPE_REVISION)
    with harness.engine.begin() as conn:
        constraint = conn.scalar(_UQ_DEF)
        assert "isotope_key" in constraint
        assert "isotopologue_label" not in constraint
        assert "NULLS NOT DISTINCT" in constraint

        # The pre-existing entry is untouched: same row, and the new key is
        # NULL, which *is* the all-standard key. No backfill, no re-keying.
        legacy = conn.execute(
            text(
                "SELECT id, isotope_key, isotopologue_label FROM species_entry "
                "WHERE id = :id"
            ),
            {"id": legacy_entry_id},
        ).one()
        assert legacy.id == legacy_entry_id
        assert legacy.isotope_key is None
        assert legacy.isotopologue_label is None

        # Existing atoms mean "standard isotope" without a backfill.
        assert conn.scalar(
            text(
                "SELECT count(*) FROM geometry_atom "
                "WHERE geometry_id = :id AND isotope_mass_number IS NULL"
            ),
            {"id": geometry_id},
        ) == 2

        # The deprecated label column survives so no deployment loses text.
        assert conn.scalar(
            text(
                "SELECT 1 FROM information_schema.columns WHERE "
                "table_name = 'species_entry' AND column_name = 'isotopologue_label'"
            )
        )

        # A deuterated sibling now coexists under the same species.
        heavy_entry_id = conn.scalar(
            text(
                "INSERT INTO species_entry (species_id, unmapped_smiles, isotope_key) "
                "VALUES (:species_id, 'CO', '[2H]CO') RETURNING id"
            ),
            {"species_id": species_id},
        )
        assert heavy_entry_id != legacy_entry_id
        conn.execute(
            text(
                "UPDATE geometry_atom SET isotope_mass_number = 13 "
                "WHERE geometry_id = :id AND atom_index = 1"
            ),
            {"id": geometry_id},
        )

    # Downgrading with isotopically resolved data present would silently
    # merge distinct isotopologues, so the revision refuses.
    refused = harness.run("downgrade", BASE_REVISION, check=False)
    assert refused.returncode != 0
    assert "isotopically resolved species entries exist" in refused.stderr

    with harness.engine.begin() as conn:
        conn.execute(text("DELETE FROM species_entry WHERE id = :id"), {"id": heavy_entry_id})
        conn.execute(text("UPDATE geometry_atom SET isotope_mass_number = NULL"))

    harness.run("downgrade", BASE_REVISION)
    with harness.engine.begin() as conn:
        constraint = conn.scalar(_UQ_DEF)
        assert "isotopologue_label" in constraint
        assert "isotope_key" not in constraint
        assert conn.scalar(
            text(
                "SELECT 1 FROM information_schema.columns WHERE "
                "table_name = 'geometry_atom' AND column_name = 'isotope_mass_number'"
            )
        ) is None
        # The all-standard entry is still exactly the row it always was.
        assert conn.scalar(
            text("SELECT id FROM species_entry WHERE id = :id"), {"id": legacy_entry_id}
        ) == legacy_entry_id

    harness.run("upgrade", ISOTOPE_REVISION)
    with harness.engine.begin() as conn:
        assert "isotope_key" in conn.scalar(_UQ_DEF)
        assert conn.scalar(
            text("SELECT isotope_key FROM species_entry WHERE id = :id"),
            {"id": legacy_entry_id},
        ) is None


def test_collision_guard_refuses_to_merge_label_only_identities(harness) -> None:
    """Other deployments may have non-NULL labels; they must not be merged."""

    harness.run("upgrade", BASE_REVISION)
    with harness.engine.begin() as conn:
        species_id = _seed_species(conn, "CO", "ISOMIGRATION00000000000000B")
        for label in ("d1", "d3"):
            conn.execute(
                text(
                    "INSERT INTO species_entry (species_id, isotopologue_label) "
                    "VALUES (:species_id, :label)"
                ),
                {"species_id": species_id, "label": label},
            )

    refused = harness.run("upgrade", ISOTOPE_REVISION, check=False)
    assert refused.returncode != 0
    combined = refused.stderr + refused.stdout
    assert "would merge species entries that are currently distinct" in combined
    # Actionable: names the species and the offending labels.
    assert f"species_id={species_id}" in combined
    assert "'CO'" in combined
    assert "d1" in combined and "d3" in combined

    # And it is a guard, not a partial apply: the schema is untouched.
    with harness.engine.begin() as conn:
        assert "isotopologue_label" in conn.scalar(_UQ_DEF)
        assert conn.scalar(
            text(
                "SELECT 1 FROM information_schema.columns WHERE "
                "table_name = 'species_entry' AND column_name = 'isotope_key'"
            )
        ) is None


def test_collision_guard_allows_a_single_labelled_entry_per_identity(harness) -> None:
    """A lone legacy label is not a collision — it must upgrade cleanly."""

    harness.run("upgrade", BASE_REVISION)
    with harness.engine.begin() as conn:
        species_id = _seed_species(conn, "CO", "ISOMIGRATION00000000000000C")
        entry_id = conn.scalar(
            text(
                "INSERT INTO species_entry (species_id, isotopologue_label) "
                "VALUES (:species_id, 'd3') RETURNING id"
            ),
            {"species_id": species_id},
        )

    harness.run("upgrade", ISOTOPE_REVISION)
    with harness.engine.begin() as conn:
        row = conn.execute(
            text(
                "SELECT isotope_key, isotopologue_label FROM species_entry WHERE id = :id"
            ),
            {"id": entry_id},
        ).one()
        # The annotation is preserved verbatim; it simply no longer keys
        # identity, and the derived key stays NULL because no atom-resolved
        # isotope content exists to derive it from.
        assert row.isotopologue_label == "d3"
        assert row.isotope_key is None
