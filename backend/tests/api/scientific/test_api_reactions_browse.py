"""API tests for GET /api/v1/scientific/reactions/browse.

Companion to ``test_api_reaction_search.py``. Reuses that module's
reasoning: the browse surface shares its query builder and record shape
with ``/reactions/search`` (see
:func:`app.services.scientific_read.reactions.browse_reactions`); only
"no filter required" is new.
"""

from __future__ import annotations

from app.db.models.common import RecordReviewStatus, SubmissionRecordType
from tests.services.scientific_read._factories import (
    make_chem_reaction,
    make_kinetics,
    make_reaction_entry,
    make_species,
    make_species_entry,
    make_transition_state,
    next_inchi_key,
    set_review,
)


def _browse_url(**params) -> str:
    base = "/api/v1/scientific/reactions/browse"
    if not params:
        return base
    qs = "&".join(f"{k}={v}" for k, v in params.items())
    return f"{base}?{qs}"


def _entry(db_session, *, reactant_smiles="A", product_smiles="B", family=None):
    rs = make_species(db_session, smiles=reactant_smiles, inchi_key=next_inchi_key("RBRW1"))
    ps = make_species(db_session, smiles=product_smiles, inchi_key=next_inchi_key("RBRW2"))
    chem = make_chem_reaction(db_session, reactants=[rs], products=[ps])
    if family is not None:
        from app.db.models.reaction import ReactionFamily

        fam = db_session.query(ReactionFamily).filter_by(name=family).first()
        if fam is None:
            fam = ReactionFamily(name=family)
            db_session.add(fam)
            db_session.flush()
        chem.reaction_family_id = fam.id
        db_session.flush()
    return make_reaction_entry(
        db_session,
        reaction=chem,
        reactant_entries=[make_species_entry(db_session, rs)],
        product_entries=[make_species_entry(db_session, ps)],
    )


# ---------------------------------------------------------------------------
# The headline defect this route exists to fix
# ---------------------------------------------------------------------------


def test_get_with_no_query_params_returns_200_not_422(client, db_session):
    """The exact call that 422s on /search today must 200 here."""
    _entry(db_session)

    search_resp = client.get("/api/v1/scientific/reactions/search")
    assert search_resp.status_code == 422
    assert "missing_reaction_search_filter" in search_resp.text

    browse_resp = client.get(_browse_url())
    assert browse_resp.status_code == 200


def test_browse_returns_the_same_record_shape_as_search(client, db_session):
    entry = _entry(db_session)

    search_body = client.get(
        f"/api/v1/scientific/reactions/search?reaction_entry_ref={entry.public_ref}"
    ).json()
    browse_body = client.get(_browse_url()).json()

    search_record = search_body["records"][0]
    browse_record = next(
        r for r in browse_body["records"] if r["reaction_entry_ref"] == entry.public_ref
    )
    assert set(search_record.keys()) == set(browse_record.keys())
    for key in ("reaction_ref", "reaction_entry_ref", "equation", "reversible", "family"):
        assert search_record[key] == browse_record[key]
    assert search_record["reactants"] == browse_record["reactants"]
    assert search_record["products"] == browse_record["products"]


# ---------------------------------------------------------------------------
# Filters
# ---------------------------------------------------------------------------


def test_family_filter_narrows_results(client, db_session):
    matching = _entry(db_session, reactant_smiles="C", product_smiles="N", family="h_abstraction")
    _entry(db_session, reactant_smiles="O", product_smiles="F")

    body = client.get(_browse_url(family="h_abstraction")).json()
    refs = {r["reaction_entry_ref"] for r in body["records"]}
    assert refs == {matching.public_ref}


def test_unknown_family_returns_empty_not_422(client, db_session):
    _entry(db_session)
    body = client.get(_browse_url(family="not_a_real_family")).json()
    assert body["records"] == []


def test_reactant_smiles_filter_narrows_results(client, db_session):
    matching = _entry(db_session, reactant_smiles="[NH2]", product_smiles="N")
    _entry(db_session, reactant_smiles="O", product_smiles="F")

    body = client.get(_browse_url(reactant_smiles="%5BNH2%5D")).json()
    refs = {r["reaction_entry_ref"] for r in body["records"]}
    assert refs == {matching.public_ref}


def test_product_smiles_filter_narrows_results(client, db_session):
    matching = _entry(db_session, reactant_smiles="C", product_smiles="[OH]")
    _entry(db_session, reactant_smiles="O", product_smiles="F")

    body = client.get(_browse_url(product_smiles="%5BOH%5D")).json()
    refs = {r["reaction_entry_ref"] for r in body["records"]}
    assert refs == {matching.public_ref}


def test_has_kinetics_filter(client, db_session):
    with_kinetics = _entry(db_session, reactant_smiles="C", product_smiles="N")
    make_kinetics(db_session, reaction_entry=with_kinetics)
    without_kinetics = _entry(db_session, reactant_smiles="O", product_smiles="F")

    body = client.get(_browse_url(has_kinetics="true")).json()
    refs = {r["reaction_entry_ref"] for r in body["records"]}
    assert with_kinetics.public_ref in refs
    assert without_kinetics.public_ref not in refs

    body_false = client.get(_browse_url(has_kinetics="false")).json()
    refs_false = {r["reaction_entry_ref"] for r in body_false["records"]}
    assert without_kinetics.public_ref in refs_false
    assert with_kinetics.public_ref not in refs_false


def test_has_transition_state_filter(client, db_session):
    with_ts = _entry(db_session, reactant_smiles="C", product_smiles="N")
    make_transition_state(db_session, reaction_entry=with_ts)
    without_ts = _entry(db_session, reactant_smiles="O", product_smiles="F")

    body = client.get(_browse_url(has_transition_state="true")).json()
    refs = {r["reaction_entry_ref"] for r in body["records"]}
    assert with_ts.public_ref in refs
    assert without_ts.public_ref not in refs


def test_min_review_status_filters_by_entry_review(client, db_session):
    approved = _entry(db_session, reactant_smiles="C", product_smiles="N")
    set_review(
        db_session,
        record_type=SubmissionRecordType.reaction_entry,
        record_id=approved.id,
        status=RecordReviewStatus.approved,
    )
    unreviewed = _entry(db_session, reactant_smiles="O", product_smiles="F")

    body = client.get(
        _browse_url(min_review_status=RecordReviewStatus.approved.value)
    ).json()
    refs = {r["reaction_entry_ref"] for r in body["records"]}
    assert refs == {approved.public_ref}
    assert unreviewed.public_ref not in refs


def test_include_rejected_returns_rejected_entries(client, db_session):
    visible = _entry(db_session, reactant_smiles="C", product_smiles="N")
    rejected = _entry(db_session, reactant_smiles="O", product_smiles="F")
    set_review(
        db_session,
        record_type=SubmissionRecordType.reaction_entry,
        record_id=rejected.id,
        status=RecordReviewStatus.rejected,
    )

    default_refs = {
        r["reaction_entry_ref"] for r in client.get(_browse_url()).json()["records"]
    }
    assert visible.public_ref in default_refs
    assert rejected.public_ref not in default_refs

    included_refs = {
        r["reaction_entry_ref"]
        for r in client.get(_browse_url(include_rejected="true")).json()["records"]
    }
    assert rejected.public_ref in included_refs


def test_include_deprecated_returns_deprecated_entries(client, db_session):
    visible = _entry(db_session, reactant_smiles="C", product_smiles="N")
    deprecated = _entry(db_session, reactant_smiles="O", product_smiles="F")
    set_review(
        db_session,
        record_type=SubmissionRecordType.reaction_entry,
        record_id=deprecated.id,
        status=RecordReviewStatus.deprecated,
    )

    default_refs = {
        r["reaction_entry_ref"] for r in client.get(_browse_url()).json()["records"]
    }
    assert visible.public_ref in default_refs
    assert deprecated.public_ref not in default_refs

    included_refs = {
        r["reaction_entry_ref"]
        for r in client.get(_browse_url(include_deprecated="true")).json()["records"]
    }
    assert deprecated.public_ref in included_refs


def test_offset_and_limit_page_results(client, db_session):
    for i in range(2, 5):
        _entry(db_session, reactant_smiles=f"[{i}H]", product_smiles="N")
    body_full = client.get(_browse_url(limit=200)).json()
    assert len(body_full["records"]) >= 3

    body_page = client.get(_browse_url(offset=0, limit=1)).json()
    assert len(body_page["records"]) == 1
    assert body_page["pagination"]["total"] == body_full["pagination"]["total"]


# ---------------------------------------------------------------------------
# Sort / echo
# ---------------------------------------------------------------------------


def test_default_sort_orders_records_review_rank_then_has_kinetics(client, db_session):
    """Asserts both the echoed sort label and the actual row order it names.

    Three entries, distinguished on the sort key's first two axes:
    ``review_rank`` (approved beats not_reviewed) beats ``has_kinetics``
    (a not_reviewed entry with kinetics still sorts after an approved
    entry with none), and among equal review rank, ``has_kinetics``
    breaks the tie. The echoed string alone (asserted first) is a label a
    caller can display; it is not proof of the order actually served, so
    every assertion below checks real row positions.
    """
    best = _entry(db_session, reactant_smiles="[2H]", product_smiles="N")
    set_review(
        db_session,
        record_type=SubmissionRecordType.reaction_entry,
        record_id=best.id,
        status=RecordReviewStatus.approved,
    )
    mid = _entry(db_session, reactant_smiles="[3H]", product_smiles="N")
    make_kinetics(db_session, reaction_entry=mid)
    worst = _entry(db_session, reactant_smiles="[4H]", product_smiles="N")

    body = client.get(_browse_url(limit=200)).json()
    assert body["request"]["sort"] == (
        "review_rank,has_kinetics,has_transition_state,created_at,id"
    )

    refs = [r["reaction_entry_ref"] for r in body["records"]]
    positions = {ref: i for i, ref in enumerate(refs)}

    assert positions[best.public_ref] < positions[mid.public_ref]
    assert positions[mid.public_ref] < positions[worst.public_ref]
