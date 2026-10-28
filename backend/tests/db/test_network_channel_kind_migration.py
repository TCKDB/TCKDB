"""``d3f8a1c5b9e2`` recomputes every channel's kind from its states and drops 'stabilization'."""

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from uuid import uuid4

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.db.base import Base
from app.db.models.common import SubmissionRecordType
from tests.db.test_accepted_science_immutability import _actor, _approve

_PATH = Path(__file__).resolve().parents[2] / "alembic/versions/d3f8a1c5b9e2_derive_network_channel_kind.py"


def _load(db_session, monkeypatch):
    spec = spec_from_file_location("channel_kind_migration", _PATH)
    migration = module_from_spec(spec)
    spec.loader.exec_module(migration)
    monkeypatch.setattr(
        migration,
        "op",
        Operations(MigrationContext.configure(db_session.connection(), opts={"target_metadata": Base.metadata})),
    )
    return migration


def _network(db_session) -> int:
    return db_session.scalar(
        text("INSERT INTO network (name, public_ref) VALUES ('kind migration', :ref) RETURNING id"),
        {"ref": "net_" + uuid4().hex[:26]},
    )


def _state(db_session, network_id: int, kind: str) -> int:
    return db_session.scalar(
        text(
            "INSERT INTO network_state (network_id, kind, composition_hash, label) "
            "VALUES (:n, :k, :h, :lbl) RETURNING id"
        ),
        {"n": network_id, "k": kind, "h": uuid4().hex.ljust(64, "0"), "lbl": kind},
    )


def _channel(db_session, network_id: int, source: int, sink: int, kind: str, key: str) -> int:
    return db_session.scalar(
        text(
            "INSERT INTO network_channel (network_id, source_state_id, sink_state_id, kind, channel_key) "
            "VALUES (:n, :s, :t, :k, :key) RETURNING id"
        ),
        {"n": network_id, "s": source, "t": sink, "k": kind, "key": key},
    )


def _enum_values(db_session) -> list[str]:
    return list(
        db_session.scalars(
            text(
                "SELECT e.enumlabel FROM pg_enum e JOIN pg_type t ON t.oid = e.enumtypid "
                "WHERE t.typname = 'network_channel_kind' ORDER BY e.enumsortorder"
            )
        )
    )


def _repair_count(db_session) -> int:
    return db_session.scalar(
        text(
            "SELECT count(*) FROM accepted_science_repair WHERE alembic_revision = 'd3f8a1c5b9e2' "
            "AND target_table = 'network_channel' AND declared_columns = ARRAY['kind']"
        )
    )


def _seed_mislabelled(db_session) -> tuple[int, dict[str, int]]:
    network = _network(db_session)
    well, well2 = _state(db_session, network, "well"), _state(db_session, network, "well")
    bimol, termol = _state(db_session, network, "bimolecular"), _state(db_session, network, "termolecular")
    channels = {
        # (stored kind, derived kind) -- every stored kind below is wrong except 'ok'
        "ok": _channel(db_session, network, well, well2, "isomerization", "ok"),
        "wrong_iso": _channel(db_session, network, bimol, well, "isomerization", "wrong_iso"),
        "stab": _channel(db_session, network, termol, well, "stabilization", "stab"),
        "wrong_diss": _channel(db_session, network, well, termol, "association", "wrong_diss"),
        "wrong_exch": _channel(db_session, network, bimol, termol, "dissociation", "wrong_exch"),
    }
    return network, channels


_EXPECTED = {
    "ok": "isomerization",
    "wrong_iso": "association",
    "stab": "association",
    "wrong_diss": "dissociation",
    "wrong_exch": "exchange",
}


def _kinds(db_session, channels: dict[str, int]) -> dict[str, str]:
    return {
        key: db_session.scalar(text("SELECT kind::text FROM network_channel WHERE id = :id"), {"id": cid})
        for key, cid in channels.items()
    }


@pytest.mark.parametrize("approved", [False, True])
def test_upgrade_recomputes_kinds_maps_stabilization_and_drops_the_value(db_session, monkeypatch, capsys, approved):
    migration = _load(db_session, monkeypatch)
    migration.downgrade()  # back to the enum that still has 'stabilization'
    assert "stabilization" in _enum_values(db_session)
    network, channels = _seed_mislabelled(db_session)
    if approved:
        _approve(db_session, SubmissionRecordType.network, network, _actor(db_session))
    assert _kinds(db_session, channels)["stab"] == "stabilization"

    repairs_before = _repair_count(db_session)
    if approved:
        # Frozen for real: without a declaration the guard refuses the very same UPDATE.
        with pytest.raises(DBAPIError), db_session.begin_nested():
            db_session.execute(
                text("UPDATE network_channel SET kind = 'exchange' WHERE id = :id"), {"id": channels["ok"]}
            )
    capsys.readouterr()

    migration.upgrade()
    out = capsys.readouterr().out

    assert _kinds(db_session, channels) == _EXPECTED
    assert _enum_values(db_session) == ["isomerization", "association", "dissociation", "exchange"]
    # every changed channel is listed as (network ref, channel key, old kind, new kind); the clean one is not
    ref = db_session.scalar(text("SELECT public_ref FROM network WHERE id = :id"), {"id": network})
    for key, old in (("wrong_iso", "isomerization"), ("stab", "stabilization"), ("wrong_diss", "association"),
                     ("wrong_exch", "dissociation")):
        assert f"network_channel.kind changed: {ref}/{key}: {old} -> {_EXPECTED[key]}" in out
    assert f"{ref}/ok" not in out
    assert "4 changed (1 stabilization)" in out
    # the guard was never disabled, and every change is in the repair ledger, old -> new
    assert db_session.scalar(
        text("SELECT tgenabled FROM pg_trigger WHERE tgrelid = 'public.network_channel'::regclass "
             "AND tgname LIKE 'trg_as_%' LIMIT 1")
    ) == "O"
    ledger = {
        (row.record_id and row.row_identity["id"]): (row.before_json["kind"], row.after_json["kind"])
        for row in db_session.execute(
            text("SELECT c.record_id, c.row_identity, c.before_json, c.after_json "
                 "FROM accepted_science_repair_change c JOIN accepted_science_repair r ON r.id = c.repair_id "
                 "WHERE r.alembic_revision = 'd3f8a1c5b9e2' AND c.target_table = 'network_channel'")
        )
    }
    assert ledger == {
        channels[key]: (old, _EXPECTED[key])
        for key, old in (("wrong_iso", "isomerization"), ("stab", "stabilization"), ("wrong_diss", "association"),
                         ("wrong_exch", "dissociation"))
    }
    # one declared repair on the single column, in this transaction
    assert _repair_count(db_session) == repairs_before + 1


def test_downgrade_restores_the_old_enum_without_touching_data(db_session, monkeypatch):
    migration = _load(db_session, monkeypatch)
    network = _network(db_session)
    well, bimol = _state(db_session, network, "well"), _state(db_session, network, "bimolecular")
    cid = _channel(db_session, network, bimol, well, "association", "a")
    migration.downgrade()
    assert _enum_values(db_session) == ["isomerization", "association", "dissociation", "stabilization", "exchange"]
    assert db_session.scalar(text("SELECT kind::text FROM network_channel WHERE id = :id"), {"id": cid}) == "association"
    migration.upgrade()
    assert "stabilization" not in _enum_values(db_session)


def test_upgrade_stops_with_a_list_when_a_state_kind_cannot_be_classified(db_session, monkeypatch):
    migration = _load(db_session, monkeypatch)
    network = _network(db_session)
    well, other = _state(db_session, network, "well"), _state(db_session, network, "bimolecular")
    _channel(db_session, network, well, other, "dissociation", "odd_channel")
    migration.downgrade()
    # Simulate a state kind outside the vocabulary (transactional DDL, rolled back with the test).
    db_session.execute(text("ALTER TABLE network_state ALTER COLUMN kind TYPE text USING kind::text"))
    db_session.execute(text("UPDATE network_state SET kind = 'unimolecular' WHERE id = :id"), {"id": other})
    with pytest.raises(RuntimeError, match="odd_channel"):
        migration.upgrade()


def test_a_clean_database_prints_zero_changed(db_session, monkeypatch, capsys):
    migration = _load(db_session, monkeypatch)
    db_session.execute(text("DELETE FROM network_channel"))
    network = _network(db_session)
    well, bimol = _state(db_session, network, "well"), _state(db_session, network, "bimolecular")
    _channel(db_session, network, bimol, well, "association", "fine")
    capsys.readouterr()
    migration.upgrade()
    out = capsys.readouterr().out
    assert "0 changed\n" in out
    assert "network_channel.kind changed" not in out
    assert "0 changed (0 stabilization), 1 unchanged, 1 channels" in out
