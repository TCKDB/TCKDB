"""Disposable-database contract for the Stage 2 scientific-integrity migration."""

import subprocess
from pathlib import Path

from sqlalchemy import create_engine, text

BASE_REVISION = "b1c2d3e4f5a6"
STAGE2_REVISION = "c1d2e3f4a5b6"


def test_stage2_scientific_integrity_upgrade_downgrade_contract():
    """Exercise c1's additive science graph and its reversible legacy shape."""
    from conftest import _database_url, _db_env, scratch_database_name

    db_name = scratch_database_name("stage2_migration")
    admin_url = _database_url("postgres")
    admin = create_engine(admin_url, isolation_level="AUTOCOMMIT", pool_pre_ping=True)
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
            species_id = conn.scalar(
                text(
                    "INSERT INTO species "
                    "(kind, smiles, inchi_key, charge, multiplicity, stereo_kind) "
                    "VALUES ('molecule', 'C', 'MIGRATION000000000000000000', 0, 1, 'achiral') "
                    "RETURNING id"
                )
            )
            species_entry_id = conn.scalar(
                text("INSERT INTO species_entry (species_id) VALUES (:species_id) RETURNING id"),
                {"species_id": species_id},
            )
            statmech_id = conn.scalar(
                text(
                    "INSERT INTO statmech (species_entry_id, scientific_origin) "
                    "VALUES (:species_entry_id, 'computed') RETURNING id"
                ),
                {"species_entry_id": species_entry_id},
            )

        engine = run_revision("upgrade", STAGE2_REVISION, engine)
        with engine.begin() as conn:
            for table_name in (
                "network_channel_microreaction",
                "network_solve_state_energy",
                "network_solve_channel_barrier",
                "kinetics_interpretation_assignment",
                "kinetics_tunneling_application",
                "transition_state_validation_evidence",
            ):
                assert conn.scalar(text("SELECT to_regclass(:table_name)"), {"table_name": f"public.{table_name}"})

            for table_name, column_name in (
                ("statmech", "transition_state_entry_id"),
                ("network_channel", "channel_key"),
                ("network_solve_energy_transfer", "state_id"),
                ("network_solve_energy_transfer", "collider_species_entry_id"),
            ):
                assert conn.scalar(
                    text(
                        "SELECT 1 FROM information_schema.columns "
                        "WHERE table_name = :table_name AND column_name = :column_name"
                    ),
                    {"table_name": table_name, "column_name": column_name},
                )

            for definition_fragment in (
                "species_entry_id IS NULL) <> (transition_state_entry_id IS NULL",
                "UNIQUE (network_id, channel_key)",
                "UNIQUE (solve_id, state_id, collider_species_entry_id)",
                "UNIQUE (transition_state_entry_id, kind)",
                # The subject-key CHECK matches the whole shape by regex; the
                # old trailing-digit substring form was NULL (and therefore
                # accepted) for a key with no digits at all.
                "subject_key ~ ((\'^\'::text || role) || \':[0-9]+$\'::text)",
            ):
                assert conn.scalar(
                    text("SELECT 1 FROM pg_constraint WHERE pg_get_constraintdef(oid) LIKE :pattern"),
                    {"pattern": f"%{definition_fragment}%"},
                )

            # Barriers are signed relative to the declared zero (a submerged
            # entrance barrier is negative), so only NaN is rejected.
            barrier_checks = conn.scalars(
                text(
                    "SELECT pg_get_constraintdef(constraint_row.oid) "
                    "FROM pg_constraint AS constraint_row "
                    "JOIN pg_class AS table_row ON table_row.oid = constraint_row.conrelid "
                    "WHERE table_row.relname = 'network_solve_channel_barrier' "
                    "AND constraint_row.contype = 'c'"
                )
            ).all()
            finite_check = next(
                check for check in barrier_checks if "NaN" in check
            )
            assert "forward_barrier_kj_mol" in finite_check
            assert "reverse_barrier_kj_mol" in finite_check
            # No positivity bound: a submerged barrier is negative.
            assert ">=" not in finite_check
            assert "> 0" not in finite_check

            # Every channel carries a producer-visible key. NULLs are distinct
            # in PostgreSQL, so a nullable column would defeat the unique
            # constraint that gives parallel paths their identity.
            assert conn.scalar(
                text(
                    "SELECT is_nullable FROM information_schema.columns "
                    "WHERE table_name = 'network_channel' "
                    "AND column_name = 'channel_key'"
                )
            ) == "NO"

            # A barrierless channel path stores a NULL transition state, so
            # the link table cannot key on it.
            assert conn.scalar(
                text(
                    "SELECT is_nullable FROM information_schema.columns "
                    "WHERE table_name = 'network_channel_microreaction' "
                    "AND column_name = 'transition_state_entry_id'"
                )
            ) == "YES"
            assert conn.scalar(
                text(
                    "SELECT 1 FROM pg_indexes WHERE indexname = "
                    "'uq_network_channel_microreaction_barrierless'"
                )
            )

            # NMD is not a TCKDB concept: no column, no enum value survives.
            assert conn.scalar(
                text(
                    "SELECT 1 FROM information_schema.columns "
                    "WHERE table_name = 'transition_state_validation_evidence' "
                    "AND column_name IN ('mode_index', 'displacement_artifact_id')"
                )
            ) is None
            ts_kind_check = conn.scalar(
                text(
                    "SELECT pg_get_constraintdef(constraint_row.oid) "
                    "FROM pg_constraint AS constraint_row "
                    "JOIN pg_class AS table_row ON table_row.oid = constraint_row.conrelid "
                    "WHERE table_row.relname = 'transition_state_validation_evidence' "
                    "AND constraint_row.contype = 'c' "
                    "AND pg_get_constraintdef(constraint_row.oid) LIKE '%kind%'"
                )
            )
            assert ts_kind_check is not None
            assert "irc" in ts_kind_check
            assert "nmd" not in ts_kind_check

            statmech = conn.execute(
                text(
                    "SELECT species_entry_id, transition_state_entry_id FROM statmech WHERE id = :id"
                ),
                {"id": statmech_id},
            ).one()
            assert statmech.species_entry_id == species_entry_id
            assert statmech.transition_state_entry_id is None

        engine = run_revision("downgrade", BASE_REVISION, engine)
        with engine.begin() as conn:
            for table_name in (
                "network_channel_microreaction",
                "network_solve_state_energy",
                "network_solve_channel_barrier",
                "kinetics_interpretation_assignment",
                "kinetics_tunneling_application",
                "transition_state_validation_evidence",
            ):
                assert conn.scalar(text("SELECT to_regclass(:table_name)"), {"table_name": f"public.{table_name}"}) is None
            assert conn.scalar(
                text(
                    "SELECT 1 FROM information_schema.columns "
                    "WHERE table_name = 'statmech' AND column_name = 'transition_state_entry_id'"
                )
            ) is None
            assert conn.scalar(
                text(
                    "SELECT is_nullable FROM information_schema.columns "
                    "WHERE table_name = 'statmech' AND column_name = 'species_entry_id'"
                )
            ) == "NO"
            assert conn.scalar(
                text("SELECT species_entry_id FROM statmech WHERE id = :id"),
                {"id": statmech_id},
            ) == species_entry_id

        engine = run_revision("upgrade", STAGE2_REVISION, engine)
        with engine.begin() as conn:
            assert conn.scalar(text("SELECT to_regclass('public.network_solve_channel_barrier')"))
            assert conn.scalar(
                text(
                    "SELECT 1 FROM pg_constraint "
                    "WHERE pg_get_constraintdef(oid) LIKE "
                    "'%(species_entry_id IS NULL) <> (transition_state_entry_id IS NULL)%'"
                )
            )
    finally:
        if engine is not None:
            engine.dispose()
        if admin_conn is not None:
            try:
                admin_conn.execute(
                    text("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = :name"),
                    {"name": db_name},
                )
                admin_conn.execute(text(f'DROP DATABASE IF EXISTS "{db_name}"'))
            finally:
                admin_conn.close()
        admin.dispose()


def test_stage2_upgrade_refuses_pre_v2_pdep_rows():
    """Legacy PDep rows must stop the upgrade with an actionable message.

    ``network_solve_energy_transfer`` gains two NOT NULL scope columns and
    ``network_channel`` gains a producer-visible key. Neither can be derived
    from what v1 stored, so upgrading a database that holds those rows must
    refuse loudly rather than die on a NotNullViolation part-way through the
    DDL. The deployed database really does hold such rows — this is the
    reproduction, not a hypothetical.
    """
    from conftest import _database_url, _db_env, scratch_database_name

    db_name = scratch_database_name("stage2_legacy")
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
            ["conda", "run", "-n", "tckdb_env", "alembic", "upgrade", BASE_REVISION],
            cwd=root,
            env=env,
            check=True,
        )

        engine = create_engine(_database_url(db_name), pool_pre_ping=True)
        with engine.begin() as conn:
            network_id = conn.scalar(
                text(
                    "INSERT INTO network (name, public_ref) "
                    "VALUES ('legacy pdep network', 'net_legacyrow00000000000001') "
                    "RETURNING id"
                )
            )
            solve_id = conn.scalar(
                text(
                    "INSERT INTO network_solve (network_id, public_ref) "
                    "VALUES (:network_id, 'nsolve_legacyrow0000000000001') RETURNING id"
                ),
                {"network_id": network_id},
            )
            conn.execute(
                text(
                    "INSERT INTO network_solve_energy_transfer "
                    "(solve_id, model, alpha0_cm_inv) "
                    "VALUES (:solve_id, 'single_exponential_down', 175.0)"
                ),
                {"solve_id": solve_id},
            )
        engine.dispose()
        engine = None

        completed = subprocess.run(
            ["conda", "run", "-n", "tckdb_env", "alembic", "upgrade", STAGE2_REVISION],
            cwd=root,
            env=env,
            capture_output=True,
            text=True,
        )
        assert completed.returncode != 0
        combined = completed.stdout + completed.stderr
        assert "Cannot upgrade to c1d2e3f4a5b6" in combined
        assert "network_solve_energy_transfer row(s)" in combined
        # The message must tell the operator what to actually do.
        assert "POST /uploads/network-pdep" in combined
        assert "backend/docs/deployment/migrations.md" in combined

        # The refusal left the schema untouched, not half-migrated.
        engine = create_engine(_database_url(db_name), pool_pre_ping=True)
        with engine.begin() as conn:
            assert conn.scalar(
                text("SELECT to_regclass('public.network_solve_channel_barrier')")
            ) is None
            assert conn.scalar(
                text(
                    "SELECT 1 FROM information_schema.columns "
                    "WHERE table_name = 'network_solve_energy_transfer' "
                    "AND column_name = 'state_id'"
                )
            ) is None
            assert conn.scalar(
                text("SELECT count(*) FROM network_solve_energy_transfer")
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


def test_stage2_downgrade_refuses_parallel_channels():
    """Parallel channels cannot be re-expressed under the old unique triple."""
    from conftest import _database_url, _db_env, scratch_database_name

    db_name = scratch_database_name("stage2_parallel")
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
            ["conda", "run", "-n", "tckdb_env", "alembic", "upgrade", STAGE2_REVISION],
            cwd=root,
            env=env,
            check=True,
        )

        engine = create_engine(_database_url(db_name), pool_pre_ping=True)
        with engine.begin() as conn:
            network_id = conn.scalar(
                text(
                    "INSERT INTO network (name, public_ref) "
                    "VALUES ('parallel network', 'net_parallelrow0000000000001') "
                    "RETURNING id"
                )
            )
            state_ids = [
                conn.scalar(
                    text(
                        "INSERT INTO network_state (network_id, kind, composition_hash) "
                        "VALUES (:network_id, 'well', :composition_hash) RETURNING id"
                    ),
                    {"network_id": network_id, "composition_hash": str(index) * 64},
                )
                for index in range(2)
            ]
            for channel_key in ("path_a", "path_b"):
                conn.execute(
                    text(
                        "INSERT INTO network_channel "
                        "(network_id, source_state_id, sink_state_id, kind, channel_key) "
                        "VALUES (:network_id, :source, :sink, 'isomerization', :channel_key)"
                    ),
                    {
                        "network_id": network_id,
                        "source": state_ids[0],
                        "sink": state_ids[1],
                        "channel_key": channel_key,
                    },
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
        assert "parallel network channels" in completed.stdout + completed.stderr
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
