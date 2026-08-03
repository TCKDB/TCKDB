"""Disposable-database contract for the network-solve kind migration.

``c4d8f1b2a9e6`` adds ``network_solve.kind`` so that k(T,P) transcribed from a
publication can be deposited at all. The deployed Pi database holds one real
master-equation solve (network 4, ``net_o6bt63kjeyvhvxx26w6kdi433a``: 21
channels, 42 kinetics rows, 2 energy-transfer rows), so the load-bearing claim
is that it comes out the other side still meaning *computed* — every rate it
carries still attributed to a derivation this database holds, and not one of
them silently reclassified as something a paper merely asserts.
"""

import subprocess
from pathlib import Path
from uuid import uuid4

from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

BASE_REVISION = "b6e1d3a9c740"
KIND_REVISION = "c4d8f1b2a9e6"


def _seed_computed_solve(conn, suffix: str) -> dict[str, int]:
    """Write a real-shaped master-equation solve at BASE_REVISION.

    Mirrors the deployed hydrazine network in miniature: a solve carrying the
    master-equation inputs the old contract demanded (a state energy, an
    energy-transfer model) and the fitted k(T,P) it produced. Those inputs are
    what makes ``computed`` a derivable fact about the row rather than a
    guess.
    """
    network_id = conn.scalar(
        text(
            "INSERT INTO network (name, public_ref) "
            "VALUES ('kind migration network', :public_ref) RETURNING id"
        ),
        {"public_ref": f"net_kindmig{suffix}"},
    )
    solve_id = conn.scalar(
        text(
            "INSERT INTO network_solve "
            "(network_id, public_ref, me_method, tmin_k, tmax_k, pmin_bar, "
            " pmax_bar) "
            "VALUES (:network_id, :public_ref, 'reservoir_state', 300, 2000, "
            " 0.01, 100) RETURNING id"
        ),
        {"network_id": network_id, "public_ref": f"nsolve_kindmig{suffix}"},
    )
    well_id = conn.scalar(
        text(
            "INSERT INTO network_state (network_id, kind, composition_hash, label) "
            "VALUES (:network_id, 'well', :composition_hash, 'N2H4') RETURNING id"
        ),
        {"network_id": network_id, "composition_hash": f"w{suffix}".ljust(64, "0")},
    )
    exit_id = conn.scalar(
        text(
            "INSERT INTO network_state (network_id, kind, composition_hash, label) "
            "VALUES (:network_id, 'bimolecular', :composition_hash, 'NH2+NH2') "
            "RETURNING id"
        ),
        {"network_id": network_id, "composition_hash": f"x{suffix}".ljust(64, "0")},
    )
    channel_id = conn.scalar(
        text(
            "INSERT INTO network_channel "
            "(network_id, source_state_id, sink_state_id, kind, channel_key) "
            "VALUES (:network_id, :source_id, :sink_id, 'dissociation', "
            " 'diss_path') RETURNING id"
        ),
        {"network_id": network_id, "source_id": well_id, "sink_id": exit_id},
    )
    kinetics_id = conn.scalar(
        text(
            "INSERT INTO network_kinetics "
            "(channel_id, solve_id, model_kind, public_ref, tmin_k, tmax_k, "
            " pmin_bar, pmax_bar) "
            "VALUES (:channel_id, :solve_id, 'chebyshev', :public_ref, 300, "
            " 2000, 0.01, 100) RETURNING id"
        ),
        {
            "channel_id": channel_id,
            "solve_id": solve_id,
            "public_ref": f"nkin_kindmig{suffix}",
        },
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
    conn.execute(
        text(
            "INSERT INTO network_solve_energy_transfer "
            "(solve_id, scope, state_id, collider_species_entry_id, model, "
            " alpha0_cm_inv) "
            "VALUES (:solve_id, 'per_well', :state_id, :collider_id, "
            " 'single_exponential_down', 175.0)"
        ),
        {"solve_id": solve_id, "state_id": well_id, "collider_id": collider_id},
    )
    conn.execute(
        text(
            "INSERT INTO network_solve_state_energy "
            "(solve_id, state_id, energy_kj_mol, energy_zero_convention, "
            " correction_convention) "
            "VALUES (:solve_id, :state_id, -120.0, 'entrance_channel', "
            " 'electronic_plus_zpe')"
        ),
        {"solve_id": solve_id, "state_id": well_id},
    )
    literature_id = conn.scalar(
        text(
            "INSERT INTO literature (kind, title, public_ref) "
            "VALUES ('article', 'A cited paper', :public_ref) RETURNING id"
        ),
        {"public_ref": f"lit_kindmig{suffix}"},
    )
    return {
        "network_id": network_id,
        "solve_id": solve_id,
        "channel_id": channel_id,
        "kinetics_id": kinetics_id,
        "literature_id": literature_id,
    }


def test_existing_solves_still_read_as_computed():
    """Deployed solves migrate to ``kind='computed'`` unchanged.

    Before this revision the schema admitted nothing else: a solve could not
    be written without a state energy for every network state, a barrier for
    every saddle-point path and an energy-transfer model, and there was no way
    to express a transcribed one. ``computed`` is therefore a statement of
    fact about every stored row, not an assumption — and nothing about the
    row's meaning, or about the k(T,P) hanging off it, may change on the way
    through.
    """
    from conftest import _database_url, _db_env

    db_name = f"tckdb_test_solve_kind_migration_{uuid4().hex}"
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
            seeded = _seed_computed_solve(conn, "a1")
            # Pre-migration there is no axis at all: that absence is what
            # makes 'computed' derivable from the old contract rather than
            # inferred from the data.
            assert conn.scalar(
                text(
                    "SELECT 1 FROM information_schema.columns "
                    "WHERE table_name = 'network_solve' AND column_name = 'kind'"
                )
            ) is None

        engine = run_revision("upgrade", KIND_REVISION, engine)
        with engine.begin() as conn:
            row = conn.execute(
                text(
                    "SELECT kind, network_id, me_method, tmin_k, tmax_k, "
                    "       pmin_bar, pmax_bar, literature_id "
                    "FROM network_solve WHERE id = :id"
                ),
                {"id": seeded["solve_id"]},
            ).one()
            assert row.kind == "computed"
            assert row.network_id == seeded["network_id"]
            assert row.me_method == "reservoir_state"
            assert row.tmin_k == 300
            assert row.pmax_bar == 100
            # The backfill cannot trip the new constraint, because the
            # constraint only bites on 'reported' and the backfill makes none.
            assert row.literature_id is None

            # The rates the solve exists to carry still hang off it, still
            # attributed to a derivation this database holds.
            kinetics = conn.execute(
                text(
                    "SELECT nk.id, nk.model_kind, ns.kind "
                    "FROM network_kinetics nk "
                    "JOIN network_solve ns ON ns.id = nk.solve_id "
                    "WHERE nk.id = :id"
                ),
                {"id": seeded["kinetics_id"]},
            ).one()
            assert kinetics.model_kind == "chebyshev"
            assert kinetics.kind == "computed"

            # The master-equation inputs that made it computed are untouched.
            assert conn.scalar(
                text(
                    "SELECT count(*) FROM network_solve_energy_transfer "
                    "WHERE solve_id = :id"
                ),
                {"id": seeded["solve_id"]},
            ) == 1
            assert conn.scalar(
                text(
                    "SELECT count(*) FROM network_solve_state_energy "
                    "WHERE solve_id = :id"
                ),
                {"id": seeded["solve_id"]},
            ) == 1

            # A client that does not know about this axis writes the stronger,
            # more informative claim rather than silently producing a record
            # nobody can re-derive.
            assert conn.scalar(
                text(
                    "SELECT column_default FROM information_schema.columns "
                    "WHERE table_name = 'network_solve' AND column_name = 'kind'"
                )
            ).startswith("'computed'")

        # The relaxation is paid for by the citation: a reported solve with no
        # literature is refused by the database, not merely by the API.
        with engine.connect() as conn:
            trans = conn.begin()
            try:
                conn.execute(
                    text(
                        "INSERT INTO network_solve (network_id, public_ref, kind) "
                        "VALUES (:network_id, 'nsolve_kindmig_bad', 'reported')"
                    ),
                    {"network_id": seeded["network_id"]},
                )
            except IntegrityError as exc:
                assert "reported_requires_literature" in str(exc)
            else:
                raise AssertionError(
                    "a reported solve with no literature should have been rejected"
                )
            finally:
                trans.rollback()

            # With one, it is accepted — and a computed solve may still cite a
            # paper, since the constraint bites on one member only.
            trans = conn.begin()
            try:
                conn.execute(
                    text(
                        "INSERT INTO network_solve "
                        "(network_id, public_ref, kind, literature_id) "
                        "VALUES (:network_id, 'nsolve_kindmig_ok', 'reported', "
                        " :literature_id)"
                    ),
                    {
                        "network_id": seeded["network_id"],
                        "literature_id": seeded["literature_id"],
                    },
                )
                conn.execute(
                    text(
                        "INSERT INTO network_solve "
                        "(network_id, public_ref, kind, literature_id) "
                        "VALUES (:network_id, 'nsolve_kindmig_lit', 'computed', "
                        " :literature_id)"
                    ),
                    {
                        "network_id": seeded["network_id"],
                        "literature_id": seeded["literature_id"],
                    },
                )
            finally:
                trans.rollback()

        # Downgrade is clean while no reported solve exists, and the computed
        # solve survives it intact.
        engine = run_revision("downgrade", BASE_REVISION, engine)
        with engine.begin() as conn:
            assert conn.scalar(
                text(
                    "SELECT 1 FROM information_schema.columns "
                    "WHERE table_name = 'network_solve' AND column_name = 'kind'"
                )
            ) is None
            row = conn.execute(
                text(
                    "SELECT me_method, tmin_k, pmax_bar FROM network_solve "
                    "WHERE id = :id"
                ),
                {"id": seeded["solve_id"]},
            ).one()
            assert row.me_method == "reservoir_state"
            assert row.tmin_k == 300
            assert row.pmax_bar == 100
            assert conn.scalar(
                text("SELECT count(*) FROM network_kinetics WHERE solve_id = :id"),
                {"id": seeded["solve_id"]},
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


def test_downgrade_refuses_to_relabel_reported_rates_as_derived():
    """A reported solve has no derivation to fall back on, so say so.

    Dropping the column under one would present rates transcribed from a paper
    as this database's own master-equation output — a stronger claim than the
    record supports, since it holds none of the inputs, and precisely the
    confusion the revision exists to prevent. The refusal has to name what is
    in the way and what the operator should do.
    """
    from conftest import _database_url, _db_env

    db_name = f"tckdb_test_solve_kind_downgrade_{uuid4().hex}"
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
            ["conda", "run", "-n", "tckdb_env", "alembic", "upgrade", KIND_REVISION],
            cwd=root,
            env=env,
            check=True,
        )

        engine = create_engine(_database_url(db_name), pool_pre_ping=True)
        with engine.begin() as conn:
            seeded = _seed_computed_solve(conn, "b2")
            conn.execute(
                text(
                    "INSERT INTO network_solve "
                    "(network_id, public_ref, kind, literature_id) "
                    "VALUES (:network_id, 'nsolve_kindmig_rep', 'reported', "
                    " :literature_id)"
                ),
                {
                    "network_id": seeded["network_id"],
                    "literature_id": seeded["literature_id"],
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
        combined = completed.stdout + completed.stderr
        assert "Cannot downgrade from c4d8f1b2a9e6" in combined
        assert "transcribed from a publication" in combined
        assert "/scientific/network-solves" in combined

        # The refusal left the schema intact, not half-downgraded.
        engine = create_engine(_database_url(db_name), pool_pre_ping=True)
        with engine.begin() as conn:
            assert conn.scalar(
                text(
                    "SELECT 1 FROM information_schema.columns "
                    "WHERE table_name = 'network_solve' AND column_name = 'kind'"
                )
            )
            assert conn.scalar(
                text("SELECT count(*) FROM network_solve WHERE kind = 'reported'")
            ) == 1
            assert conn.scalar(
                text("SELECT count(*) FROM network_solve WHERE kind = 'computed'")
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
