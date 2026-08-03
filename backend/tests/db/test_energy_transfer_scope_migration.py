"""Disposable-database contract for the energy-transfer scope migration.

``b6e1d3a9c740`` adds ``network_solve_energy_transfer.scope`` and relaxes the
two scope columns to nullable so a network-wide ⟨ΔE⟩down declaration is
representable. The deployed Pi database holds real per-well rows (network 4,
``net_o6bt63kjeyvhvxx26w6kdi433a``), so the load-bearing claim is that those
rows come out the other side still meaning *per well* — same state, same
collider, and now a token that says so.
"""

import subprocess
from pathlib import Path
from uuid import uuid4

from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

BASE_REVISION = "a7c2e4f8b6d9"
SCOPE_REVISION = "b6e1d3a9c740"


def _seed_per_well_row(conn, suffix: str) -> dict[str, int]:
    """Write one real-shaped per-well energy-transfer row at BASE_REVISION."""
    network_id = conn.scalar(
        text(
            "INSERT INTO network (name, public_ref) "
            "VALUES ('scope migration network', :public_ref) RETURNING id"
        ),
        {"public_ref": f"net_scopemig{suffix}"},
    )
    solve_id = conn.scalar(
        text(
            "INSERT INTO network_solve (network_id, public_ref) "
            "VALUES (:network_id, :public_ref) RETURNING id"
        ),
        {"network_id": network_id, "public_ref": f"nsolve_scopemig{suffix}"},
    )
    state_id = conn.scalar(
        text(
            "INSERT INTO network_state (network_id, kind, composition_hash, label) "
            "VALUES (:network_id, 'well', :composition_hash, 'N2H4') RETURNING id"
        ),
        {"network_id": network_id, "composition_hash": suffix.ljust(64, "0")},
    )
    species_id = conn.scalar(
        text(
            "INSERT INTO species "
            "(kind, smiles, inchi_key, charge, multiplicity, stereo_kind) "
            "VALUES ('molecule', 'N#N', :inchi_key, 0, 1, 'achiral') RETURNING id"
        ),
        {"inchi_key": suffix[:27].ljust(27, "A").upper()},
    )
    collider_id = conn.scalar(
        text("INSERT INTO species_entry (species_id) VALUES (:species_id) RETURNING id"),
        {"species_id": species_id},
    )
    energy_transfer_id = conn.scalar(
        text(
            "INSERT INTO network_solve_energy_transfer "
            "(solve_id, state_id, collider_species_entry_id, model, alpha0_cm_inv, "
            " t_exponent, t_ref_k) "
            "VALUES (:solve_id, :state_id, :collider_id, 'single_exponential_down', "
            " 175.0, 0.52, 298.0) RETURNING id"
        ),
        {"solve_id": solve_id, "state_id": state_id, "collider_id": collider_id},
    )
    return {
        "network_id": network_id,
        "solve_id": solve_id,
        "state_id": state_id,
        "collider_id": collider_id,
        "energy_transfer_id": energy_transfer_id,
    }


def test_existing_energy_transfer_rows_still_read_as_per_well():
    """Deployed per-well rows migrate to ``scope='per_well'`` unchanged.

    Both scope columns were NOT NULL before this revision, so every stored row
    already resolves a (state, collider) pair. ``per_well`` is a statement of
    fact about them, not a guess — and nothing about the row's meaning may
    change on the way through.
    """
    from conftest import _database_url, _db_env

    db_name = f"tckdb_et_scope_migration_{uuid4().hex}"
    admin = create_engine(
        _database_url("postgres"), isolation_level="AUTOCOMMIT", pool_pre_ping=True
    )
    admin_conn = None
    engine = None
    root = Path(__file__).resolve().parents[2]

    def run_revision(direction: str, revision: str, current_engine):
        if current_engine is not None:
            current_engine.dispose()
        subprocess.run(
            ["conda", "run", "-n", "tckdb_env", "alembic", direction, revision],
            cwd=root,
            env=env,
            check=True,
        )
        return create_engine(_database_url(db_name), pool_pre_ping=True)

    try:
        admin_conn = admin.connect()
        admin_conn.execute(text(f'CREATE DATABASE "{db_name}"'))
        env = _db_env(db_name)

        engine = run_revision("upgrade", BASE_REVISION, engine)
        with engine.begin() as conn:
            seeded = _seed_per_well_row(conn, "a1")
            # Pre-migration the columns really are mandatory: this is what
            # makes 'per_well' derivable rather than assumed.
            assert conn.scalar(
                text(
                    "SELECT is_nullable FROM information_schema.columns "
                    "WHERE table_name = 'network_solve_energy_transfer' "
                    "AND column_name = 'state_id'"
                )
            ) == "NO"

        engine = run_revision("upgrade", SCOPE_REVISION, engine)
        with engine.begin() as conn:
            row = conn.execute(
                text(
                    "SELECT scope, state_id, collider_species_entry_id, "
                    "       model, alpha0_cm_inv, t_exponent, t_ref_k "
                    "FROM network_solve_energy_transfer WHERE id = :id"
                ),
                {"id": seeded["energy_transfer_id"]},
            ).one()
            assert row.scope == "per_well"
            assert row.state_id == seeded["state_id"]
            assert row.collider_species_entry_id == seeded["collider_id"]
            assert row.alpha0_cm_inv == 175.0
            assert row.t_exponent == 0.52
            assert row.t_ref_k == 298.0

            # The relaxed columns are nullable now, but only under the token.
            for column_name in ("state_id", "collider_species_entry_id"):
                assert conn.scalar(
                    text(
                        "SELECT is_nullable FROM information_schema.columns "
                        "WHERE table_name = 'network_solve_energy_transfer' "
                        "AND column_name = :column_name"
                    ),
                    {"column_name": column_name},
                ) == "YES"

            # A client that does not know about the axis still writes the more
            # informative value rather than a NULL.
            assert conn.scalar(
                text(
                    "SELECT column_default FROM information_schema.columns "
                    "WHERE table_name = 'network_solve_energy_transfer' "
                    "AND column_name = 'scope'"
                )
            ).startswith("'per_well'")

        # A per_well row may not go scope-less, and a network_wide row may not
        # smuggle a state in: the two shapes stay mutually exclusive, so a NULL
        # state_id always has an explanation attached.
        with engine.connect() as conn:
            for scope, state_id, collider_id in (
                ("per_well", None, None),
                ("network_wide", seeded["state_id"], seeded["collider_id"]),
                ("network_wide", seeded["state_id"], None),
            ):
                trans = conn.begin()
                try:
                    conn.execute(
                        text(
                            "INSERT INTO network_solve_energy_transfer "
                            "(solve_id, scope, state_id, collider_species_entry_id, "
                            " model, alpha0_cm_inv) "
                            "VALUES (:solve_id, :scope, :state_id, :collider_id, "
                            " 'single_exponential_down', 175.0)"
                        ),
                        {
                            "solve_id": seeded["solve_id"],
                            "scope": scope,
                            "state_id": state_id,
                            "collider_id": collider_id,
                        },
                    )
                except IntegrityError as exc:
                    assert "scope_columns_agree" in str(exc)
                else:
                    raise AssertionError(
                        f"scope={scope} state_id={state_id} "
                        f"collider={collider_id} should have been rejected"
                    )
                finally:
                    trans.rollback()

            # At most one network-wide declaration per solve: the tuple unique
            # constraint cannot police it, because NULLs are distinct.
            trans = conn.begin()
            try:
                for _ in range(2):
                    conn.execute(
                        text(
                            "INSERT INTO network_solve_energy_transfer "
                            "(solve_id, scope, model, alpha0_cm_inv) "
                            "VALUES (:solve_id, 'network_wide', "
                            " 'single_exponential_down', 175.0)"
                        ),
                        {"solve_id": seeded["solve_id"]},
                    )
            except IntegrityError as exc:
                assert "uq_network_solve_energy_transfer_network_wide" in str(exc)
            else:
                raise AssertionError(
                    "two network-wide declarations on one solve should be rejected"
                )
            finally:
                trans.rollback()

        # Downgrade is clean while no network-wide row exists, and the per-well
        # row survives it intact.
        engine = run_revision("downgrade", BASE_REVISION, engine)
        with engine.begin() as conn:
            assert conn.scalar(
                text(
                    "SELECT 1 FROM information_schema.columns "
                    "WHERE table_name = 'network_solve_energy_transfer' "
                    "AND column_name = 'scope'"
                )
            ) is None
            row = conn.execute(
                text(
                    "SELECT state_id, collider_species_entry_id, alpha0_cm_inv "
                    "FROM network_solve_energy_transfer WHERE id = :id"
                ),
                {"id": seeded["energy_transfer_id"]},
            ).one()
            assert row.state_id == seeded["state_id"]
            assert row.collider_species_entry_id == seeded["collider_id"]
            assert row.alpha0_cm_inv == 175.0
    finally:
        if engine is not None:
            engine.dispose()
        if admin_conn is not None:
            try:
                admin_conn.execute(
                    text(
                        "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                        "WHERE datname = :name"
                    ),
                    {"name": db_name},
                )
                admin_conn.execute(text(f'DROP DATABASE IF EXISTS "{db_name}"'))
            finally:
                admin_conn.close()
        admin.dispose()


def test_downgrade_refuses_to_destroy_a_network_wide_declaration():
    """A network-wide row has no (state, collider) to restore, so say so.

    Dropping the column under such a row would either delete a real scientific
    declaration or silently re-present it as per-well data it never was. The
    refusal has to name what is in the way and what the operator should do.
    """
    from conftest import _database_url, _db_env

    db_name = f"tckdb_et_scope_downgrade_{uuid4().hex}"
    admin = create_engine(
        _database_url("postgres"), isolation_level="AUTOCOMMIT", pool_pre_ping=True
    )
    admin_conn = None
    engine = None
    root = Path(__file__).resolve().parents[2]

    try:
        admin_conn = admin.connect()
        admin_conn.execute(text(f'CREATE DATABASE "{db_name}"'))
        env = _db_env(db_name)

        subprocess.run(
            ["conda", "run", "-n", "tckdb_env", "alembic", "upgrade", SCOPE_REVISION],
            cwd=root,
            env=env,
            check=True,
        )

        engine = create_engine(_database_url(db_name), pool_pre_ping=True)
        with engine.begin() as conn:
            seeded = _seed_per_well_row(conn, "b2")
            conn.execute(
                text(
                    "INSERT INTO network_solve_energy_transfer "
                    "(solve_id, scope, model, alpha0_cm_inv) "
                    "VALUES (:solve_id, 'network_wide', 'single_exponential_down', "
                    " 175.0)"
                ),
                {"solve_id": seeded["solve_id"]},
            )
        engine.dispose()
        engine = None

        completed = subprocess.run(
            ["conda", "run", "-n", "tckdb_env", "alembic", "downgrade", BASE_REVISION],
            cwd=root,
            env=env,
            capture_output=True,
            text=True,
        )
        assert completed.returncode != 0
        combined = completed.stdout + completed.stderr
        assert "Cannot downgrade from b6e1d3a9c740" in combined
        assert "network-wide energy-transfer declaration(s)" in combined
        assert "/scientific/network-solves" in combined

        # The refusal left the schema intact, not half-downgraded.
        engine = create_engine(_database_url(db_name), pool_pre_ping=True)
        with engine.begin() as conn:
            assert conn.scalar(
                text(
                    "SELECT 1 FROM information_schema.columns "
                    "WHERE table_name = 'network_solve_energy_transfer' "
                    "AND column_name = 'scope'"
                )
            )
            assert conn.scalar(
                text(
                    "SELECT count(*) FROM network_solve_energy_transfer "
                    "WHERE scope = 'network_wide'"
                )
            ) == 1
    finally:
        if engine is not None:
            engine.dispose()
        if admin_conn is not None:
            try:
                admin_conn.execute(
                    text(
                        "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                        "WHERE datname = :name"
                    ),
                    {"name": db_name},
                )
                admin_conn.execute(text(f'DROP DATABASE IF EXISTS "{db_name}"'))
            finally:
                admin_conn.close()
        admin.dispose()
