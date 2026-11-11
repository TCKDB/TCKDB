"""Migration coverage for ``species_name.external_record_key`` / ``retrieved_at`` (``d5b8e2f4a916``)."""

from __future__ import annotations

from pathlib import Path

import pytest
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError

from alembic import command
from tests.db._migration_chain import revision_under_test

REPO_ROOT = Path(__file__).resolve().parents[2]
_MIGRATION = revision_under_test("d5b8e2f4a916")
_CHECKS = {"ck_species_name_offline_import_has_source", "ck_species_name_curated_has_no_record"}


def _configure(db_engine, monkeypatch) -> Config:
    db_name = db_engine.url.database
    db_engine.dispose()
    monkeypatch.setenv("DB_NAME", db_name)
    monkeypatch.setenv("DB_USER", "tckdb")
    monkeypatch.setenv("DB_PASSWORD", "tckdb")
    monkeypatch.setenv("DB_HOST", "127.0.0.1")
    monkeypatch.setenv("DB_PORT", "5432")
    return Config(str(REPO_ROOT / "alembic.ini"))


def _columns(connection) -> set[str]:
    return set(
        connection.scalars(
            text(
                "SELECT column_name FROM information_schema.columns WHERE table_name = 'species_name' AND table_schema = current_schema()"
            )
        )
    )


def _checks(connection) -> set[str]:
    return set(
        connection.scalars(
            text("SELECT conname FROM pg_constraint WHERE conrelid = 'species_name'::regclass AND contype = 'c'")
        )
    )


def test_downgrade_then_upgrade_round_trips(db_engine, monkeypatch):
    config = _configure(db_engine, monkeypatch)
    engine = create_engine(db_engine.url.render_as_string(hide_password=False))
    try:
        with engine.connect() as connection:
            assert {"external_record_key", "retrieved_at"} <= _columns(connection)
            assert _CHECKS <= _checks(connection)
        command.downgrade(config, _MIGRATION.parent)
        with engine.connect() as connection:
            assert not {"external_record_key", "retrieved_at"} & _columns(connection)
            assert not _CHECKS & _checks(connection)
        command.upgrade(config, _MIGRATION.revision)
        with engine.connect() as connection:
            assert {"external_record_key", "retrieved_at"} <= _columns(connection)
    finally:
        engine.dispose()
        command.upgrade(config, "head")


def test_downgrade_is_refused_while_imported_names_exist(db_engine, monkeypatch):
    config = _configure(db_engine, monkeypatch)
    engine = create_engine(db_engine.url.render_as_string(hide_password=False))
    try:
        with engine.begin() as connection:
            species_id = connection.scalar(
                text(
                    "INSERT INTO species (kind, smiles, inchi_key, charge, multiplicity, stereo_kind, public_ref) "
                    "VALUES ('molecule', 'C', 'AAAAAAAAAAAAAA-AAAAAAAAAC-A', 0, 1, 'achiral', 'spc_snmdowngrade') RETURNING id"
                )
            )
            source_id = connection.scalar(
                text(
                    "INSERT INTO external_source (source_name, source_release) VALUES ('PubChem Compound', 'dg') RETURNING id"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO species_name (species_id, name, name_key, kind, source_kind, external_source_id, "
                    "external_record_key, retrieved_at, public_ref) VALUES (:s, 'n', 'n', 'common', 'offline_import', "
                    ":src, '297', now(), 'snm_dg')"
                ),
                {"s": species_id, "src": source_id},
            )
        with pytest.raises(RuntimeError, match=r"1 offline_import row"):
            command.downgrade(config, _MIGRATION.parent)
        with engine.connect() as connection:
            assert {"external_record_key", "retrieved_at"} <= _columns(connection)
    finally:
        with engine.begin() as connection:
            connection.execute(text("DELETE FROM species_name WHERE public_ref = 'snm_dg'"))
            connection.execute(text("DELETE FROM species WHERE public_ref = 'spc_snmdowngrade'"))
            connection.execute(text("DELETE FROM external_source WHERE source_release = 'dg'"))
        engine.dispose()
        command.upgrade(config, "head")


def test_the_checks_tie_the_source_columns_to_the_source_kind(db_session):
    species_id = db_session.scalar(
        text(
            "INSERT INTO species (kind, smiles, inchi_key, charge, multiplicity, stereo_kind, public_ref) "
            "VALUES ('molecule', 'C', 'AAAAAAAAAAAAAA-AAAAAAAAAB-A', 0, 1, 'achiral', 'spc_snmrecordtest') RETURNING id"
        )
    )
    source_id = db_session.scalar(
        text(
            "INSERT INTO external_source (source_name, source_release) VALUES ('PubChem Compound', 'check') RETURNING id"
        )
    )
    insert = text(
        "INSERT INTO species_name (species_id, name, name_key, kind, source_kind, external_source_id, "
        "external_record_key, retrieved_at, public_ref) "
        "VALUES (:s, 'n', :k, 'common', CAST(:sk AS species_name_source_kind), :src, :key, :at, :r)"
    )
    base = {"s": species_id, "src": source_id, "key": "297", "at": "2026-11-11 10:00", "r": "snm_x"}

    def row(n, **over):
        return {**base, "k": f"k{n}", "r": f"snm_{n}", **over}

    db_session.execute(insert, row(1, sk="offline_import"))
    db_session.execute(insert, row(2, sk="curated", src=None, key=None, at=None))
    refused = [
        row(3, sk="offline_import", src=None),
        row(4, sk="offline_import", key=None),
        row(5, sk="offline_import", at=None),
        row(6, sk="curated", src=None, at=None),
        row(7, sk="curated", src=None, key=None),
    ]
    for params in refused:
        with pytest.raises(DBAPIError), db_session.begin_nested():
            db_session.execute(insert, params)
