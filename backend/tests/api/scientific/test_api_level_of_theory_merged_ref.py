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


# ---------------------------------------------------------------------------
# Integer ids agree with refs on every route (#591)
# ---------------------------------------------------------------------------


def test_the_legacy_detail_route_resolves_a_merged_id_to_the_kept_row(client, merged):
    resp = client.get(f"/api/v1/levels-of-theory/{merged['duplicate'].id}")
    assert resp.status_code == 200, resp.text
    assert resp.json()["id"] == merged["holder"].id


def test_the_scientific_detail_route_resolves_a_merged_id_to_the_kept_row(client, merged):
    resp = client.get(
        f"/api/v1/scientific/level-of-theories/{merged['duplicate'].id}"
    )
    assert resp.status_code == 200, resp.text
    lot = resp.json()["record"]["level_of_theory"]
    assert lot["level_of_theory_ref"] == merged["holder"].public_ref


def test_the_legacy_calculation_list_resolves_a_merged_lot_id(client, merged):
    resp = client.get(f"/api/v1/calculations?lot_id={merged['duplicate'].id}")
    assert resp.status_code == 200, resp.text
    assert {c["id"] for c in resp.json()["items"]} == {c.id for c in merged["calcs"]}


def test_the_three_routes_answer_a_merged_id_alike(client, merged):
    """Detail (legacy), detail (scientific) and the calculation filter agree."""
    dup = merged["duplicate"].id
    legacy = client.get(f"/api/v1/levels-of-theory/{dup}").json()["id"]
    scientific = client.get(f"/api/v1/scientific/level-of-theories/{dup}").json()[
        "record"
    ]["level_of_theory"]["level_of_theory_ref"]
    filtered = client.get(f"/api/v1/calculations?lot_id={dup}").json()["items"]
    holder = merged["holder"]
    assert (legacy, scientific) == (holder.id, holder.public_ref)
    assert len(filtered) == len(merged["calcs"]) > 0


def test_the_frequency_scale_factor_list_resolves_a_merged_lot_id(
    client, db_session, merged
):
    from tests.services.scientific_read._factories import make_frequency_scale_factor

    fsf = make_frequency_scale_factor(db_session, lot=merged["holder"])
    resp = client.get(
        f"/api/v1/frequency-scale-factors?level_of_theory_id={merged['duplicate'].id}"
    )
    assert resp.status_code == 200, resp.text
    assert [i["id"] for i in resp.json()["items"]] == [fsf.id]


def test_the_energy_correction_scheme_list_resolves_a_merged_lot_id(
    client, db_session, merged
):
    from tests.services.scientific_read._factories import make_energy_correction_scheme

    scheme = make_energy_correction_scheme(db_session, lot=merged["holder"])
    resp = client.get(
        f"/api/v1/energy-correction-schemes?level_of_theory_id={merged['duplicate'].id}"
    )
    assert resp.status_code == 200, resp.text
    assert [i["id"] for i in resp.json()["items"]] == [scheme.id]


def test_lowest_sp_resolves_a_merged_lot_id(client, db_session, merged):
    """The comparison context is the kept row: that is where the SPs moved."""
    from tests.services.scientific_read._factories import make_species, make_species_entry

    entry = make_species_entry(
        db_session,
        make_species(db_session, smiles=unique_smiles(), inchi_key=next_inchi_key("MREF")),
    )
    resp = client.get(
        f"/api/v1/species-entries/{entry.id}/conformer-observations/lowest-sp",
        params={"lot_id": merged["duplicate"].id},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["lot_id"] == merged["holder"].id


def test_an_upload_that_hashes_to_a_merged_row_lands_on_the_kept_row(db_session, merged):
    """The database refuses a calculation on a merged row, so the upload path must not try."""
    from tckdb_schemas.fragments.refs import LevelOfTheoryRef

    from app.services.calculation_resolution import (
        _level_of_theory_hash,
        resolve_level_of_theory_ref,
    )

    # The retired row carries the hash this spelling produces (as a row
    # re-keyed and then merged would); the kept row holds another.
    ref = LevelOfTheoryRef(method="b3lyp-merged-ref", basis="def2tzvp")
    merged["holder"].lot_hash = "1" * 64
    merged["duplicate"].lot_hash = _level_of_theory_hash(ref)
    db_session.flush()

    resolved = resolve_level_of_theory_ref(db_session, ref)
    assert resolved.id == merged["holder"].id
