"""Service-layer tests for search_reactions (/scientific/reactions/search)."""

from __future__ import annotations

import pytest

from app.db.models.common import RecordReviewStatus, SubmissionRecordType
from app.schemas.reads.scientific_common import CollapseMode
from app.schemas.reads.scientific_reactions import (
    ReactionDirectionQuery,
    ReactionSearchRequest,
)
from app.services.scientific_read.reactions import search_reactions
from tests.services.scientific_read._factories import (
    make_chem_reaction,
    make_kinetics,
    make_reaction_entry,
    make_species,
    make_species_entry,
    next_inchi_key,
    set_review,
)


def _setup_reaction(
    session, *, reactants_smiles: list[str], products_smiles: list[str]
):
    """Create a complete reaction (chem reaction + entry + structure participants)."""
    reactants = [
        make_species(session, smiles=s, inchi_key=next_inchi_key("R"))
        for s in reactants_smiles
    ]
    products = [
        make_species(session, smiles=s, inchi_key=next_inchi_key("P"))
        for s in products_smiles
    ]
    reactant_entries = [make_species_entry(session, sp) for sp in reactants]
    product_entries = [make_species_entry(session, sp) for sp in products]
    chem = make_chem_reaction(
        session, reactants=reactants, products=products, reversible=True
    )
    entry = make_reaction_entry(
        session,
        reaction=chem,
        reactant_entries=reactant_entries,
        product_entries=product_entries,
    )
    return chem, entry, reactant_entries, product_entries


# ---------------------------------------------------------------------------
# Identity matching
# ---------------------------------------------------------------------------


def test_match_with_two_reactants_and_two_products(db_session):
    _setup_reaction(
        db_session,
        reactants_smiles=["[CH3]", "c1ccccc1"],
        products_smiles=["CH4", "[c]1ccccc1"],
    )

    response = search_reactions(
        db_session,
        ReactionSearchRequest(
            reactants=["[CH3]", "c1ccccc1"],
            products=["CH4", "[c]1ccccc1"],
        ),
    )

    assert len(response.records) == 1
    record = response.records[0]
    assert {p.smiles for p in record.reactants} == {"[CH3]", "c1ccccc1"}
    assert {p.smiles for p in record.products} == {"CH4", "[c]1ccccc1"}


def test_direction_forward_excludes_reverse_match(db_session):
    _setup_reaction(
        db_session,
        reactants_smiles=["A1"],
        products_smiles=["B1"],
    )

    # Query with reactants=B1 products=A1 in forward direction → should not match
    response = search_reactions(
        db_session,
        ReactionSearchRequest(
            reactants=["B1"],
            products=["A1"],
            direction=ReactionDirectionQuery.forward,
        ),
    )
    assert response.records == []


def test_direction_either_matches_in_either_orientation(db_session):
    _setup_reaction(
        db_session,
        reactants_smiles=["A2"],
        products_smiles=["B2"],
    )

    response = search_reactions(
        db_session,
        ReactionSearchRequest(
            reactants=["B2"],
            products=["A2"],
            direction=ReactionDirectionQuery.either,
        ),
    )
    assert len(response.records) == 1
    # Query was reversed against stored — matched_direction should report reverse.
    assert response.records[0].matched_direction == ReactionDirectionQuery.reverse


def test_direction_either_forward_match_reports_forward(db_session):
    _setup_reaction(
        db_session,
        reactants_smiles=["A3"],
        products_smiles=["B3"],
    )

    response = search_reactions(
        db_session,
        ReactionSearchRequest(
            reactants=["A3"],
            products=["B3"],
            direction=ReactionDirectionQuery.either,
        ),
    )
    assert len(response.records) == 1
    assert response.records[0].matched_direction == ReactionDirectionQuery.forward


def test_direction_exact_not_in_v0_enum(db_session):
    """direction=exact is not a legal enum value in v0."""
    with pytest.raises(ValueError):
        ReactionSearchRequest(
            reactants=["A"], products=["B"], direction="exact"
        )


# ---------------------------------------------------------------------------
# Default trust posture + filters
# ---------------------------------------------------------------------------


def test_default_excludes_rejected_reaction_entries(db_session):
    _, entry, _, _ = _setup_reaction(
        db_session, reactants_smiles=["X1"], products_smiles=["Y1"]
    )
    set_review(
        db_session,
        record_type=SubmissionRecordType.reaction_entry,
        record_id=entry.id,
        status=RecordReviewStatus.rejected,
    )

    response = search_reactions(
        db_session,
        ReactionSearchRequest(reactants=["X1"], products=["Y1"]),
    )
    assert response.records == []


def test_min_review_status_approved_filters_at_entry_level(db_session):
    _, e1, _, _ = _setup_reaction(
        db_session, reactants_smiles=["X2"], products_smiles=["Y2"]
    )
    _, e2, _, _ = _setup_reaction(
        db_session, reactants_smiles=["X2b"], products_smiles=["Y2b"]
    )
    set_review(
        db_session,
        record_type=SubmissionRecordType.reaction_entry,
        record_id=e1.id,
        status=RecordReviewStatus.approved,
    )
    # e2 has no review row → not_reviewed.

    response = search_reactions(
        db_session,
        ReactionSearchRequest(
            reactants=["X2"],
            products=["Y2"],
            min_review_status=RecordReviewStatus.approved,
        ),
    )
    assert len(response.records) == 1
    assert response.records[0].reaction_entry_id == e1.id


# ---------------------------------------------------------------------------
# Availability
# ---------------------------------------------------------------------------


def test_availability_reports_kinetics_count(db_session):
    _, entry, _, _ = _setup_reaction(
        db_session, reactants_smiles=["K1"], products_smiles=["K2"]
    )
    make_kinetics(db_session, reaction_entry=entry)
    make_kinetics(db_session, reaction_entry=entry, ea_kj_mol=20.0)

    response = search_reactions(
        db_session,
        ReactionSearchRequest(reactants=["K1"], products=["K2"]),
    )
    assert response.records[0].availability.has_kinetics is True
    assert response.records[0].availability.kinetics_count == 2


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def test_client_supplied_sort_rejected(db_session):
    with pytest.raises(ValueError, match="client_sort_not_supported"):
        search_reactions(
            db_session,
            ReactionSearchRequest(reactants=["A"], products=["B"], sort="anything"),
        )


def test_unknown_include_token_rejected(db_session):
    with pytest.raises(ValueError, match="unknown_include_token"):
        search_reactions(
            db_session,
            ReactionSearchRequest(
                reactants=["A"], products=["B"], include=["banana"]
            ),
        )


def test_missing_identifier_rejected(db_session):
    with pytest.raises(ValueError, match="missing_reaction_search_filter"):
        search_reactions(db_session, ReactionSearchRequest())


def test_empty_result_returns_empty_records(db_session):
    response = search_reactions(
        db_session,
        ReactionSearchRequest(
            reactants=["NONEXISTENT_R"], products=["NONEXISTENT_P"]
        ),
    )
    assert response.records == []
    assert response.pagination.total == 0


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------


def test_sort_is_deterministic(db_session):
    _setup_reaction(
        db_session, reactants_smiles=["D1"], products_smiles=["D2"]
    )

    r1 = search_reactions(
        db_session,
        ReactionSearchRequest(reactants=["D1"], products=["D2"]),
    )
    r2 = search_reactions(
        db_session,
        ReactionSearchRequest(reactants=["D1"], products=["D2"]),
    )
    assert r1.model_dump() == r2.model_dump()


def test_collapse_first_applies_before_offset(db_session):
    _setup_reaction(
        db_session, reactants_smiles=["ROFF1"], products_smiles=["ROFF2"]
    )

    response = search_reactions(
        db_session,
        ReactionSearchRequest(
            reactants=["ROFF1"],
            products=["ROFF2"],
            collapse=CollapseMode.first,
            offset=1,
        ),
    )

    assert response.records == []
    assert response.pagination.total == 1
    assert response.pagination.post_collapse_total == 1
    assert response.pagination.returned == 0
