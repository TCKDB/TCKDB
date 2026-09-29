"""A merged level of theory's ref still resolves, to the row it was merged into (#574).

``merge_duplicate_levels_of_theory.py`` keeps a duplicate row, with its
``public_ref``, and records it in ``level_of_theory_merge``. A published release froze that
ref into its provenance, so every read that accepts a level-of-theory ref
must treat it as the kept row: the detail route, the calculation search and
analytics ``lot_ref`` filters, the id+ref pair filters and the LOT search.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.db.models.level_of_theory import LevelOfTheory, LevelOfTheoryMerge
from app.services.scientific_read.handles import (
    NO_MATCH,
    canonical_level_of_theory_id,
    reconcile_level_of_theory_pair,
)
from tests.services.scientific_read._factories import (
    make_calculation,
    make_lot,
    make_species,
    make_species_entry,
    next_inchi_key,
    unique_smiles,
)


@pytest.fixture
def merged(db_session):
    holder = make_lot(db_session, method="b3lyp-merged-ref", basis="def2-tzvp")
    duplicate = make_lot(db_session, method="b3lyp-merged-ref", basis="def2tzvp")
    calcs = []
    for _ in range(2):
        entry = make_species_entry(
            db_session,
            make_species(db_session, smiles=unique_smiles(), inchi_key=next_inchi_key("MREF")),
        )
        calcs.append(make_calculation(db_session, species_entry_id=entry.id, lot_id=holder.id))
    # What the merge script leaves: calculations on the holder, the
    # duplicate kept and pointing at it.
    db_session.add(LevelOfTheoryMerge(merged_lot_id=duplicate.id, into_lot_id=holder.id))
    db_session.flush()
    return {"holder": holder, "duplicate": duplicate, "calcs": calcs}


def test_detail_by_a_merged_ref_returns_the_kept_row(client, merged):
    resp = client.get(
        f"/api/v1/scientific/level-of-theories/{merged['duplicate'].public_ref}"
    )
    assert resp.status_code == 200, resp.text
    lot = resp.json()["record"]["level_of_theory"]
    assert lot["level_of_theory_ref"] == merged["holder"].public_ref


def test_calculation_search_by_a_merged_ref_finds_the_moved_calculations(client, merged):
    resp = client.get(
        "/api/v1/scientific/calculations/search"
        f"?lot_ref={merged['duplicate'].public_ref}"
    )
    assert resp.status_code == 200, resp.text
    refs = {r["calculation"]["calculation_ref"] for r in resp.json()["records"]}
    assert refs == {c.public_ref for c in merged["calcs"]}


def test_analytics_by_a_merged_ref_finds_the_moved_calculations(client, merged):
    resp = client.get(
        "/api/v1/scientific/analytics/calculations"
        f"?lot_ref={merged['duplicate'].public_ref}"
    )
    assert resp.status_code == 200, resp.text
    assert len(resp.json()["records"]) == 2


def test_lot_search_by_a_merged_ref_returns_the_kept_row(client, merged):
    resp = client.get(
        "/api/v1/scientific/level-of-theories/search"
        f"?level_of_theory_ref={merged['duplicate'].public_ref}"
    )
    assert resp.status_code == 200, resp.text
    refs = [r["level_of_theory"]["level_of_theory_ref"] for r in resp.json()["records"]]
    assert refs == [merged["holder"].public_ref]


def test_pair_filters_treat_the_merged_ref_as_the_kept_row(db_session, merged):
    holder, duplicate = merged["holder"], merged["duplicate"]
    assert reconcile_level_of_theory_pair(
        db_session, id_value=None, ref_value=duplicate.public_ref
    ) == holder.id
    # Merged ref beside the kept row's id: the same level, not a conflict.
    assert reconcile_level_of_theory_pair(
        db_session, id_value=holder.id, ref_value=duplicate.public_ref
    ) == holder.id
    assert reconcile_level_of_theory_pair(
        db_session, id_value=duplicate.id, ref_value=None
    ) == holder.id
    assert reconcile_level_of_theory_pair(
        db_session, id_value=None, ref_value="lot_doesnotexist0000000000"
    ) is NO_MATCH


def test_an_unmerged_row_stands_for_itself(db_session, merged):
    holder = merged["holder"]
    assert canonical_level_of_theory_id(db_session, holder.id) == holder.id


def test_the_legacy_listing_omits_merged_rows(client, db_session, merged):
    resp = client.get("/api/v1/levels-of-theory?method=b3lyp-merged-ref")
    assert resp.status_code == 200, resp.text
    ids = [item["id"] for item in resp.json()["items"]]
    assert ids == [merged["holder"].id]
    # The row itself is still there.
    assert db_session.scalar(
        select(LevelOfTheory.id).where(LevelOfTheory.id == merged["duplicate"].id)
    )
