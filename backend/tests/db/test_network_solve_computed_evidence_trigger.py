"""Disposable-database contract for the ``computed`` evidence trigger.

``f9b2e6c4a1d7`` turns ``computed ⇒ master-equation inputs`` from a convention
of one Pydantic validator into a property of the record. The claim only becomes
checkable at COMMIT — a ``network_solve`` is written before the children that
evidence it — so every assertion here has to *commit*, which the ordinary
per-test rollback fixture cannot do. Hence a throwaway database, as in
``test_network_solve_kind_migration.py``.

Five things are load-bearing and each has a test:

* an unevidenced ``computed`` solve is refused, and refused *at commit* rather
  than at insert, so the legitimate write order is untouched;
* the legitimate write order — solve, flush, children, one transaction — still
  commits, because a backstop that breaks the front door is not a backstop;
* each rule is inapplicable exactly where the validator says the evidence
  should not exist — no ⟨ΔE⟩down for a network with no well, no barrier for an
  all-barrierless one — because a backstop stricter than the front door turns a
  clean 422 into an opaque 500;
* ``reported`` is exempt, since it holds none of these inputs by construction
  (ADR 0010);
* tearing a whole network down in one transaction still works, because the
  trigger asks about a solve that, by then, is gone.

What is *not* tested here, because it is not guaranteed: coverage. A computed
solve with four state energies out of five commits. That rule lives in
``validate_mechanistic_channel_evidence`` and is exercised in
``tests/workflows/test_network_pdep_upload.py``.
"""

import subprocess
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

PREVIOUS_REVISION = "c4d8f1b2a9e6"
TRIGGER_REVISION = "f9b2e6c4a1d7"

REPO_BACKEND = Path(__file__).resolve().parents[2]


def _alembic(direction: str, revision: str, env: dict[str, str], **kwargs):
    return subprocess.run(
        ["conda", "run", "-n", "tckdb_env", "alembic", direction, revision],
        cwd=REPO_BACKEND,
        env=env,
        **kwargs,
    )


def _create_database(db_name: str):
    from conftest import _database_url

    admin = create_engine(
        _database_url("postgres"), isolation_level="AUTOCOMMIT", pool_pre_ping=True
    )
    conn = admin.connect()
    conn.execute(text(f'CREATE DATABASE "{db_name}"'))
    return admin, conn


def _drop_database(admin, admin_conn, db_name: str) -> None:
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


@pytest.fixture(scope="module")
def trigger_engine():
    """One database migrated to the trigger revision, shared by the module.

    Each test seeds its own network and commits into it; nothing here rolls
    back, because a deferred constraint trigger fires at COMMIT and a rolled
    back transaction would prove nothing at all.
    """
    from conftest import _database_url, _db_env

    db_name = f"tckdb_test_computed_evidence_{uuid4().hex}"
    admin, admin_conn = _create_database(db_name)
    engine = None
    try:
        _alembic("upgrade", TRIGGER_REVISION, _db_env(db_name), check=True)
        engine = create_engine(_database_url(db_name), pool_pre_ping=True)
        yield engine
    finally:
        if engine is not None:
            engine.dispose()
        _drop_database(admin, admin_conn, db_name)


def _new_network(conn, suffix: str, *, with_well: bool = True) -> dict[str, int]:
    """Seed the topology a solve hangs off, without any solve.

    ``with_well=False`` builds the one shape in which the trigger's
    energy-transfer requirement legitimately does not apply: a network with no
    energized intermediate, for which
    ``validate_mechanistic_channel_evidence`` requires *zero* ⟨ΔE⟩down entries
    rather than at least one.
    """
    ids: dict[str, int] = {}
    ids["network_id"] = conn.scalar(
        text(
            "INSERT INTO network (name, public_ref) "
            "VALUES ('evidence trigger network', :public_ref) RETURNING id"
        ),
        {"public_ref": f"net_evid{suffix}"},
    )
    if with_well:
        ids["well_id"] = conn.scalar(
            text(
                "INSERT INTO network_state "
                "(network_id, kind, composition_hash, label) "
                "VALUES (:network_id, 'well', :composition_hash, 'N2H4') "
                "RETURNING id"
            ),
            {
                "network_id": ids["network_id"],
                "composition_hash": f"w{suffix}".ljust(64, "0"),
            },
        )
    ids["exit_id"] = conn.scalar(
        text(
            "INSERT INTO network_state (network_id, kind, composition_hash, label) "
            "VALUES (:network_id, 'bimolecular', :composition_hash, 'NH2+NH2') "
            "RETURNING id"
        ),
        {
            "network_id": ids["network_id"],
            "composition_hash": f"x{suffix}".ljust(64, "0"),
        },
    )
    ids["collider_id"] = _bath_gas_collider(conn)
    return ids


def _bath_gas_collider(conn) -> int:
    """The one N2 bath gas, shared across networks.

    ``uq_species_identity`` dedupes on (smiles, charge, multiplicity), which is
    the identity table doing its job — every network in this module colliding
    with the same nitrogen is the realistic case, not a fixture shortcut.
    """
    species_id = conn.scalar(
        text(
            "SELECT id FROM species "
            "WHERE smiles = 'N#N' AND charge = 0 AND multiplicity = 1"
        )
    )
    if species_id is None:
        species_id = conn.scalar(
            text(
                "INSERT INTO species "
                "(kind, smiles, inchi_key, charge, multiplicity, stereo_kind) "
                "VALUES ('molecule', 'N#N', 'IJGRMHOSHXDMSA-UHFFFAOYSA-N', 0, 1, "
                " 'achiral') RETURNING id"
            )
        )
    entry_id = conn.scalar(
        text("SELECT id FROM species_entry WHERE species_id = :species_id LIMIT 1"),
        {"species_id": species_id},
    )
    if entry_id is None:
        entry_id = conn.scalar(
            text(
                "INSERT INTO species_entry (species_id) VALUES (:species_id) "
                "RETURNING id"
            ),
            {"species_id": species_id},
        )
    return entry_id


def _insert_solve(conn, network_id: int, suffix: str, *, kind: str = "computed",
                  literature_id: int | None = None) -> int:
    return conn.scalar(
        text(
            "INSERT INTO network_solve "
            "(network_id, public_ref, kind, literature_id, me_method, tmin_k, "
            " tmax_k, pmin_bar, pmax_bar) "
            "VALUES (:network_id, :public_ref, :kind, :literature_id, "
            " 'reservoir_state', 300, 2000, 0.01, 100) RETURNING id"
        ),
        {
            "network_id": network_id,
            "public_ref": f"nsolve_evid{suffix}",
            "kind": kind,
            "literature_id": literature_id,
        },
    )


def _insert_state_energy(conn, solve_id: int, state_id: int) -> None:
    conn.execute(
        text(
            "INSERT INTO network_solve_state_energy "
            "(solve_id, state_id, energy_kj_mol, energy_zero_convention, "
            " correction_convention) "
            "VALUES (:solve_id, :state_id, -120.0, 'entrance_channel', "
            " 'electronic_plus_zpe')"
        ),
        {"solve_id": solve_id, "state_id": state_id},
    )


def _insert_energy_transfer(conn, solve_id: int, state_id: int, collider_id: int) -> None:
    conn.execute(
        text(
            "INSERT INTO network_solve_energy_transfer "
            "(solve_id, scope, state_id, collider_species_entry_id, model, "
            " alpha0_cm_inv) "
            "VALUES (:solve_id, 'per_well', :state_id, :collider_id, "
            " 'single_exponential_down', 175.0)"
        ),
        {"solve_id": solve_id, "state_id": state_id, "collider_id": collider_id},
    )


def _insert_channel(conn, ids: dict[str, int], key: str) -> int:
    return conn.scalar(
        text(
            "INSERT INTO network_channel "
            "(network_id, source_state_id, sink_state_id, kind, channel_key) "
            "VALUES (:network_id, :source_id, :sink_id, 'dissociation', :key) "
            "RETURNING id"
        ),
        {
            "network_id": ids["network_id"],
            "source_id": ids["well_id"],
            "sink_id": ids["exit_id"],
            "key": key,
        },
    )


def _insert_path(conn, channel_id: int, *, barrierless: bool) -> dict[str, int]:
    """A channel path, with or without a saddle point.

    The distinction is the whole content of the barrier rule's applicability
    condition: a barrierless path has no transition state, so there is no
    barrier height to deposit and none may be demanded.
    """
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
    ts_entry_id = None
    if not barrierless:
        ts_id = conn.scalar(
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
                "VALUES (:ts_id, 0, 1) RETURNING id"
            ),
            {"ts_id": ts_id},
        )
    conn.execute(
        text(
            "INSERT INTO network_channel_microreaction "
            "(channel_id, reaction_entry_id, transition_state_entry_id) "
            "VALUES (:channel_id, :reaction_entry_id, :ts_entry_id)"
        ),
        {
            "channel_id": channel_id,
            "reaction_entry_id": reaction_entry_id,
            "ts_entry_id": ts_entry_id,
        },
    )
    return {
        "channel_id": channel_id,
        "reaction_entry_id": reaction_entry_id,
        "transition_state_entry_id": ts_entry_id,
    }


def _insert_channel_barrier(conn, solve_id: int, path: dict[str, int]) -> None:
    conn.execute(
        text(
            "INSERT INTO network_solve_channel_barrier "
            "(solve_id, channel_id, reaction_entry_id, transition_state_entry_id, "
            " forward_barrier_kj_mol, reverse_barrier_kj_mol, "
            " energy_zero_convention, correction_convention) "
            "VALUES (:solve_id, :channel_id, :reaction_entry_id, :ts_entry_id, "
            " 250.0, 40.0, 'entrance_channel', 'electronic_plus_zpe')"
        ),
        {
            "solve_id": solve_id,
            "channel_id": path["channel_id"],
            "reaction_entry_id": path["reaction_entry_id"],
            "ts_entry_id": path["transition_state_entry_id"],
        },
    )


def _insert_literature(conn, suffix: str) -> int:
    return conn.scalar(
        text(
            "INSERT INTO literature (kind, title, public_ref) "
            "VALUES ('article', 'A cited paper', :public_ref) RETURNING id"
        ),
        {"public_ref": f"lit_evid{suffix}"},
    )


def test_computed_solve_without_state_energies_is_refused_at_commit(trigger_engine):
    """The central claim: no row may say ``computed`` and hold nothing.

    The rejection has to land at COMMIT and not at INSERT. That is not a
    detail of implementation but the whole reason this is a deferred
    constraint trigger: the evidence rows carry ``solve_id`` and cannot exist
    before the solve does, so an immediate check would refuse every legitimate
    write.
    """
    with trigger_engine.connect() as conn:
        trans = conn.begin()
        solve_id = _insert_solve(conn, _new_network(conn, "a1")["network_id"], "a1")
        # The insert itself is accepted — the claim is not yet false, the
        # transaction has simply not finished making it true.
        assert conn.scalar(
            text("SELECT kind::text FROM network_solve WHERE id = :id"),
            {"id": solve_id},
        ) == "computed"

        with pytest.raises(IntegrityError) as excinfo:
            trans.commit()
        message = str(excinfo.value)
        assert "computed_requires_state_energy" in message
        assert "nsolve_evida1" in message
        assert "kind='reported'" in message

    # Nothing survived: the whole transaction went, not just the solve.
    with trigger_engine.connect() as conn:
        assert conn.scalar(
            text("SELECT count(*) FROM network WHERE public_ref = 'net_evida1'")
        ) == 0


def test_computed_solve_on_a_well_network_needs_an_energy_transfer_model(trigger_engine):
    """State energies alone are not a master-equation solve.

    Collisional energy transfer is what makes a network pressure-dependent, so
    a computed solve over a network with an energized well and no ⟨ΔE⟩down is
    incomplete in the one way that changes the answer.
    """
    with trigger_engine.connect() as conn:
        trans = conn.begin()
        ids = _new_network(conn, "b2")
        solve_id = _insert_solve(conn, ids["network_id"], "b2")
        _insert_state_energy(conn, solve_id, ids["well_id"])

        with pytest.raises(IntegrityError) as excinfo:
            trans.commit()
        assert "computed_requires_energy_transfer" in str(excinfo.value)


def test_computed_solve_on_a_wellless_network_needs_no_energy_transfer(trigger_engine):
    """The backstop is never stricter than the front door.

    ``validate_mechanistic_channel_evidence`` requires
    ``energy_transfer == wells × bath_gas``, so a network declaring no well
    must supply *zero* entries. A blanket requirement here would refuse a
    payload the upload path is obliged to accept and turn a clean 422 into a
    raw database error, so the trigger's energy-transfer rule is gated on the
    topology.
    """
    with trigger_engine.begin() as conn:
        ids = _new_network(conn, "c3", with_well=False)
        solve_id = _insert_solve(conn, ids["network_id"], "c3")
        _insert_state_energy(conn, solve_id, ids["exit_id"])

    with trigger_engine.connect() as conn:
        assert conn.scalar(
            text("SELECT kind::text FROM network_solve WHERE public_ref = 'nsolve_evidc3'")
        ) == "computed"


def test_computed_solve_with_a_saddle_point_path_needs_a_barrier(trigger_engine):
    """The barrier heights are what the microcanonical rates were computed from.

    A network declaring an explicit transition state and a solve claiming to
    have used it, with no barrier anywhere, is the same over-claim as the
    missing state energy: the row asserts a derivation it holds no part of.
    """
    with trigger_engine.connect() as conn:
        trans = conn.begin()
        ids = _new_network(conn, "k1")
        channel_id = _insert_channel(conn, ids, "saddle_path")
        _insert_path(conn, channel_id, barrierless=False)
        solve_id = _insert_solve(conn, ids["network_id"], "k1")
        _insert_state_energy(conn, solve_id, ids["well_id"])
        _insert_energy_transfer(conn, solve_id, ids["well_id"], ids["collider_id"])

        with pytest.raises(IntegrityError) as excinfo:
            trans.commit()
        assert "computed_requires_channel_barrier" in str(excinfo.value)


def test_an_all_barrierless_network_needs_no_barrier(trigger_engine):
    """Barrierless association is correct science with zero barriers.

    ADR 0008 forbids the blocking tier from firing on a correct result, so the
    barrier rule is conditional on the topology declaring a saddle point at
    all. Offering a barrier here would be a fabricated number, which is why
    ``validate_mechanistic_channel_evidence`` requires exactly none.
    """
    with trigger_engine.begin() as conn:
        ids = _new_network(conn, "l2")
        channel_id = _insert_channel(conn, ids, "barrierless_path")
        _insert_path(conn, channel_id, barrierless=True)
        solve_id = _insert_solve(conn, ids["network_id"], "l2")
        _insert_state_energy(conn, solve_id, ids["well_id"])
        _insert_energy_transfer(conn, solve_id, ids["well_id"], ids["collider_id"])

    with trigger_engine.connect() as conn:
        assert conn.scalar(
            text(
                "SELECT count(*) FROM network_solve_channel_barrier "
                "WHERE solve_id = :id"
            ),
            {"id": solve_id},
        ) == 0


def test_the_legitimate_write_order_still_commits(trigger_engine):
    """Solve, then children, in one transaction — the shape the workflow writes.

    ``app/workflows/network_pdep.py`` inserts the solve, flushes for its id,
    then writes the energy transfer and state energies that reference it. If
    this ordering had stopped working the guarantee would have been bought by
    breaking the only wired write path.
    """
    with trigger_engine.begin() as conn:
        ids = _new_network(conn, "d4")
        channel_id = _insert_channel(conn, ids, "diss_path")
        path = _insert_path(conn, channel_id, barrierless=False)
        # Solve first — it has to exist before anything can carry its id.
        solve_id = _insert_solve(conn, ids["network_id"], "d4")
        conn.execute(
            text(
                "INSERT INTO network_kinetics "
                "(channel_id, solve_id, model_kind, public_ref, tmin_k, tmax_k, "
                " pmin_bar, pmax_bar) "
                "VALUES (:channel_id, :solve_id, 'chebyshev', 'nkin_evidd4', 300, "
                " 2000, 0.01, 100)"
            ),
            {"channel_id": channel_id, "solve_id": solve_id},
        )
        _insert_energy_transfer(conn, solve_id, ids["well_id"], ids["collider_id"])
        _insert_state_energy(conn, solve_id, ids["well_id"])
        _insert_channel_barrier(conn, solve_id, path)

    with trigger_engine.connect() as conn:
        assert conn.scalar(
            text("SELECT count(*) FROM network_kinetics WHERE solve_id = :id"),
            {"id": solve_id},
        ) == 1


def test_reported_solves_are_exempt(trigger_engine):
    """A reported solve holds none of these inputs, by construction.

    Demanding them is exactly the refusal ADR 0010 removed: a paper's
    supplementary table does not publish the state energies, the barriers or
    the ⟨ΔE⟩down. What it must have instead — the citation — is already held by
    ``ck_network_solve_reported_requires_literature``.
    """
    with trigger_engine.begin() as conn:
        ids = _new_network(conn, "e5")
        literature_id = _insert_literature(conn, "e5")
        _insert_solve(
            conn,
            ids["network_id"],
            "e5",
            kind="reported",
            literature_id=literature_id,
        )

    with trigger_engine.connect() as conn:
        assert conn.scalar(
            text(
                "SELECT count(*) FROM network_solve_state_energy e "
                "JOIN network_solve s ON s.id = e.solve_id "
                "WHERE s.public_ref = 'nsolve_evide5'"
            )
        ) == 0


def test_relabelling_a_reported_solve_to_computed_is_checked(trigger_engine):
    """The exemption cannot be escaped by changing the label afterwards.

    ``UPDATE`` fires the trigger too, so a row that acquires the stronger claim
    has to acquire the evidence for it in the same transaction.
    """
    with trigger_engine.begin() as conn:
        ids = _new_network(conn, "f6")
        literature_id = _insert_literature(conn, "f6")
        solve_id = _insert_solve(
            conn, ids["network_id"], "f6", kind="reported", literature_id=literature_id
        )

    with trigger_engine.connect() as conn:
        trans = conn.begin()
        conn.execute(
            text("UPDATE network_solve SET kind = 'computed' WHERE id = :id"),
            {"id": solve_id},
        )
        with pytest.raises(IntegrityError) as excinfo:
            trans.commit()
        assert "computed_requires_state_energy" in str(excinfo.value)


def test_evidence_cannot_be_deleted_out_from_under_a_committed_solve(trigger_engine):
    """A guarantee that only holds at insert time is not a guarantee.

    Without the triggers on the evidence tables, ``DELETE FROM
    network_solve_state_energy`` would quietly reduce a committed ``computed``
    solve to the unbacked row this revision exists to forbid.
    """
    with trigger_engine.begin() as conn:
        ids = _new_network(conn, "g7")
        channel_id = _insert_channel(conn, ids, "g7_path")
        path = _insert_path(conn, channel_id, barrierless=False)
        solve_id = _insert_solve(conn, ids["network_id"], "g7")
        _insert_state_energy(conn, solve_id, ids["well_id"])
        _insert_energy_transfer(conn, solve_id, ids["well_id"], ids["collider_id"])
        _insert_channel_barrier(conn, solve_id, path)

    for table, expected in (
        ("network_solve_state_energy", "computed_requires_state_energy"),
        ("network_solve_energy_transfer", "computed_requires_energy_transfer"),
        ("network_solve_channel_barrier", "computed_requires_channel_barrier"),
    ):
        with trigger_engine.connect() as conn:
            trans = conn.begin()
            conn.execute(
                text(f"DELETE FROM {table} WHERE solve_id = :id"), {"id": solve_id}
            )
            with pytest.raises(IntegrityError) as excinfo:
                trans.commit()
            assert expected in str(excinfo.value)


def test_tearing_down_a_whole_network_still_commits(trigger_engine):
    """Deleting the evidence *and* the solve is not a violation, it is cleanup.

    The trigger looks the solve up at commit time and finds it gone, so there
    is no ``computed`` claim left to evidence. Getting this wrong would make
    networks undeletable — a constraint that traps data is worse than no
    constraint.
    """
    with trigger_engine.begin() as conn:
        ids = _new_network(conn, "h8")
        channel_id = _insert_channel(conn, ids, "h8_path")
        path = _insert_path(conn, channel_id, barrierless=False)
        solve_id = _insert_solve(conn, ids["network_id"], "h8")
        _insert_state_energy(conn, solve_id, ids["well_id"])
        _insert_energy_transfer(conn, solve_id, ids["well_id"], ids["collider_id"])
        _insert_channel_barrier(conn, solve_id, path)
        literature_id = _insert_literature(conn, "h8")
        _insert_solve(
            conn, ids["network_id"], "h8b", kind="reported", literature_id=literature_id
        )

    network_id = ids["network_id"]
    with trigger_engine.begin() as conn:
        for statement in (
            "DELETE FROM network_solve_energy_transfer WHERE solve_id = :solve_id",
            "DELETE FROM network_solve_state_energy WHERE solve_id = :solve_id",
            "DELETE FROM network_solve_channel_barrier WHERE solve_id = :solve_id",
        ):
            conn.execute(text(statement), {"solve_id": solve_id})
        conn.execute(
            text("DELETE FROM network_solve WHERE network_id = :network_id"),
            {"network_id": network_id},
        )
        conn.execute(
            text(
                "DELETE FROM network_channel_microreaction WHERE channel_id IN "
                "(SELECT id FROM network_channel WHERE network_id = :network_id)"
            ),
            {"network_id": network_id},
        )
        conn.execute(
            text("DELETE FROM network_channel WHERE network_id = :network_id"),
            {"network_id": network_id},
        )
        conn.execute(
            text("DELETE FROM network_state WHERE network_id = :network_id"),
            {"network_id": network_id},
        )
        conn.execute(
            text("DELETE FROM network WHERE id = :network_id"),
            {"network_id": network_id},
        )

    with trigger_engine.connect() as conn:
        assert conn.scalar(
            text("SELECT count(*) FROM network WHERE id = :id"), {"id": network_id}
        ) == 0
        assert conn.scalar(
            text("SELECT count(*) FROM network_solve WHERE network_id = :id"),
            {"id": network_id},
        ) == 0


def test_migration_round_trips_and_downgrade_removes_the_trigger():
    """Up, down, up. Downgrade needs no guard and must leave nothing behind.

    Dropping a constraint can invalidate no stored row — unlike
    ``c4d8f1b2a9e6``'s downgrade, which had to refuse because the older schema
    could not *say* what a reported solve is. Here the rule simply reverts to
    living in ``validate_mechanistic_channel_evidence``, where it still is.
    """
    from conftest import _database_url, _db_env

    db_name = f"tckdb_test_computed_evidence_roundtrip_{uuid4().hex}"
    admin, admin_conn = _create_database(db_name)
    engine = None
    try:
        env = _db_env(db_name)
        _alembic("upgrade", TRIGGER_REVISION, env, check=True)
        engine = create_engine(_database_url(db_name), pool_pre_ping=True)
        with engine.connect() as conn:
            assert conn.scalar(
                text(
                    "SELECT count(*) FROM pg_trigger "
                    "WHERE tgname LIKE 'ct_network_solve%computed_evidence'"
                )
            ) == 4
        engine.dispose()
        engine = None

        _alembic("downgrade", PREVIOUS_REVISION, env, check=True)
        engine = create_engine(_database_url(db_name), pool_pre_ping=True)
        with engine.connect() as conn:
            assert conn.scalar(
                text(
                    "SELECT count(*) FROM pg_trigger "
                    "WHERE tgname LIKE 'ct_network_solve%computed_evidence'"
                )
            ) == 0
            assert conn.scalar(
                text(
                    "SELECT count(*) FROM pg_proc "
                    "WHERE proname = 'network_solve_computed_evidence'"
                )
            ) == 0
            # The axis it backs is still there: this revision removed a
            # backstop, not the claim.
            assert conn.scalar(
                text(
                    "SELECT 1 FROM information_schema.columns "
                    "WHERE table_name = 'network_solve' AND column_name = 'kind'"
                )
            )
        engine.dispose()
        engine = None

        _alembic("upgrade", TRIGGER_REVISION, env, check=True)
        engine = create_engine(_database_url(db_name), pool_pre_ping=True)
        with engine.connect() as conn:
            assert conn.scalar(
                text(
                    "SELECT count(*) FROM pg_trigger "
                    "WHERE tgname LIKE 'ct_network_solve%computed_evidence'"
                )
            ) == 4
    finally:
        if engine is not None:
            engine.dispose()
        _drop_database(admin, admin_conn, db_name)


def test_upgrade_refuses_computed_solves_it_could_not_have_enforced():
    """The guard checks the database rather than inheriting a claim.

    ``c4d8f1b2a9e6.upgrade()`` refuses while any solve lacks state energies,
    which is a real guarantee about rows present *at that revision* — but it
    says nothing about ``network_solve_energy_transfer``, and nothing about
    rows written afterwards. Creating the trigger over data that already
    violates it would produce a rule true only of the future, so the upgrade
    asks, names the offenders and stops.
    """
    from conftest import _database_url, _db_env

    db_name = f"tckdb_test_computed_evidence_guard_{uuid4().hex}"
    admin, admin_conn = _create_database(db_name)
    engine = None
    try:
        env = _db_env(db_name)
        _alembic("upgrade", PREVIOUS_REVISION, env, check=True)

        engine = create_engine(_database_url(db_name), pool_pre_ping=True)
        with engine.begin() as conn:
            # Written after c4d8f1b2a9e6 applied, so its guard never saw it —
            # exactly the window the ancestor's proof does not cover.
            ids = _new_network(conn, "i9")
            channel_id = _insert_channel(conn, ids, "i9_path")
            _insert_path(conn, channel_id, barrierless=False)
            _insert_solve(conn, ids["network_id"], "i9")
            # And ones that satisfy the ancestor's guard but not this one:
            # state energies present, the other two classes missing.
            half = _insert_solve(conn, ids["network_id"], "j0")
            _insert_state_energy(conn, half, ids["well_id"])
            _insert_energy_transfer(conn, half, ids["well_id"], ids["collider_id"])
        engine.dispose()
        engine = None

        completed = _alembic(
            "upgrade", TRIGGER_REVISION, env, capture_output=True, text=True
        )
        assert completed.returncode != 0
        combined = completed.stdout + completed.stderr
        assert "2 network_solve row(s) with kind='computed'" in combined
        # Named individually, and told which classes each one is short of.
        assert (
            "nsolve_evidi9 (missing state energies, energy transfer, "
            "channel barriers)" in combined
        )
        assert "nsolve_evidj0 (missing channel barriers)" in combined

        # The refusal left the schema alone rather than half-applying it.
        engine = create_engine(_database_url(db_name), pool_pre_ping=True)
        with engine.connect() as conn:
            assert conn.scalar(
                text(
                    "SELECT count(*) FROM pg_trigger "
                    "WHERE tgname LIKE 'ct_network_solve%computed_evidence'"
                )
            ) == 0
    finally:
        if engine is not None:
            engine.dispose()
        _drop_database(admin, admin_conn, db_name)
