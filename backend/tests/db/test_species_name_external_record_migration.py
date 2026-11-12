"""Migration coverage for ``species_name.external_record_key`` / ``retrieved_at``."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

REPO_ROOT = Path(__file__).resolve().parents[2]


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
