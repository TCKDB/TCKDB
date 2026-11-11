"""Migration coverage for ``species_name`` (``c3a9e5d17b24``): reversible, with the live-row unique key and the C-collated key."""

from __future__ import annotations

from pathlib import Path

import pytest
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError

from alembic import command
from tests.db._migration_chain import revision_under_test

REPO_ROOT = Path(__file__).resolve().parents[2]
_MIGRATION = revision_under_test("c3a9e5d17b24")


def _configure(db_engine, monkeypatch) -> Config:
    db_name = db_engine.url.database
    db_engine.dispose()
    monkeypatch.setenv("DB_NAME", db_name)
    monkeypatch.setenv("DB_USER", "tckdb")
    monkeypatch.setenv("DB_PASSWORD", "tckdb")
    monkeypatch.setenv("DB_HOST", "127.0.0.1")
    monkeypatch.setenv("DB_PORT", "5432")
    return Config(str(REPO_ROOT / "alembic.ini"))


def _tables(connection) -> set[str]:
    return set(connection.scalars(text("SELECT tablename FROM pg_tables WHERE schemaname = current_schema()")))


def _enums(connection) -> set[str]:
    return set(connection.scalars(text("SELECT typname FROM pg_type WHERE typname LIKE 'species_name_%'")))


def test_downgrade_then_upgrade_round_trips(db_engine, monkeypatch):
    config = _configure(db_engine, monkeypatch)
    engine = create_engine(db_engine.url.render_as_string(hide_password=False))
    try:
        with engine.connect() as connection:
            assert "species_name" in _tables(connection)
            assert _enums(connection) == {"species_name_kind", "species_name_source_kind"}
        command.downgrade(config, _MIGRATION.parent)
        with engine.connect() as connection:
            assert "species_name" not in _tables(connection)
            assert _enums(connection) == set()
        command.upgrade(config, _MIGRATION.revision)
        with engine.connect() as connection:
            assert "species_name" in _tables(connection)
    finally:
        engine.dispose()
        command.upgrade(config, "head")


def test_the_name_key_index_is_c_collated_and_the_unique_key_covers_live_rows_only(db_session):
    indexes = {
        row.indexname: row.indexdef
        for row in db_session.execute(text("SELECT indexname, indexdef FROM pg_indexes WHERE tablename = 'species_name'"))
    }
    collation = db_session.scalar(
        text("SELECT collation_name FROM information_schema.columns WHERE table_name = 'species_name' AND column_name = 'name_key'")
    )
    assert collation == "C"
    assert "(name_key)" in indexes["ix_species_name_name_key"]
    live = indexes["uq_species_name_species_id_name_key_live"]
    assert "UNIQUE" in live and "retired_at IS NULL" in live
    assert "UNIQUE" in indexes["ix_species_name_public_ref"]


def test_length_checks_refuse_an_empty_or_overlong_name(db_session):
    species_id = db_session.scalar(
        text(
            "INSERT INTO species (kind, smiles, inchi_key, charge, multiplicity, stereo_kind, public_ref) "
            "VALUES ('molecule', 'C', 'AAAAAAAAAAAAAA-AAAAAAAAAA-A', 0, 1, 'achiral', 'spc_snmmigrationtest') RETURNING id"
        )
    )
    insert = (
        "INSERT INTO species_name (species_id, name, name_key, kind, source_kind, public_ref) "
        "VALUES (:s, :n, :k, 'common', 'curated', :r)"
    )
    db_session.execute(text(insert), {"s": species_id, "n": "ok", "k": "ok", "r": "snm_ok"})
    for name, key, ref in (("", "x", "snm_a"), ("x" * 129, "x", "snm_b"), ("x", "", "snm_c")):
        with pytest.raises(DBAPIError), db_session.begin_nested():
            db_session.execute(text(insert), {"s": species_id, "n": name, "k": key, "r": ref})
