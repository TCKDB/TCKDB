"""Migration coverage for ``species_name``: reversible, with the live-row unique key and the C-collated key."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

REPO_ROOT = Path(__file__).resolve().parents[2]


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


def test_the_merged_target_trigger_is_installed(db_session):
    names = set(db_session.scalars(text("SELECT tgname FROM pg_trigger WHERE tgrelid = 'species_name'::regclass AND NOT tgisinternal")))
    assert "trg_mt_species_name_species_id" in names
