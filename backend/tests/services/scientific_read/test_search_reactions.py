"""Service-layer tests for search_reactions (/scientific/reactions/search)."""

from __future__ import annotations

import pytest

from app.db.models.common import RecordReviewStatus, SubmissionRecordType
from app.schemas.reads.scientific_common import CollapseMode
from app.schemas.reads.scientific_reactions import (
    ReactionDirectionQuery,
    ReactionMatchMode,
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


def test_reactants_only_query_finds_reactions_consuming_that_species(db_session):
    """The query that justifies ``match``: "what consumes hydrazine?".

    Before ``match`` existed this returned zero records for every possible
    input, because the matcher demanded the (empty) product side match the
    stored product side exactly and no reaction has zero products.
    """
    _setup_reaction(
        db_session,
        reactants_smiles=["NN"],
        products_smiles=["[H][H]", "[N-]=[NH2+]"],
    )

    response = search_reactions(db_session, ReactionSearchRequest(reactants=["NN"]))

    assert len(response.records) == 1
    assert response.pagination.total == 1
    assert {p.smiles for p in response.records[0].reactants} == {"NN"}


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


# ---------------------------------------------------------------------------
# match=contains vs match=exact
#
# ``contains`` is set containment per role and is the default; ``exact`` is
# multiset equality on both roles and is what the endpoint used to do
# unconditionally. The two axes (``direction``, ``match``) are independent
# and every combination below is asserted rather than assumed.
# ---------------------------------------------------------------------------


def test_contains_only_products_finds_reactions_producing_that_species(db_session):
    """The mirror of the reactants-only case: "what produces OH?"."""
    _setup_reaction(
        db_session,
        reactants_smiles=["CT_A", "CT_B"],
        products_smiles=["CT_C", "CT_D"],
    )

    response = search_reactions(db_session, ReactionSearchRequest(products=["CT_C"]))

    assert len(response.records) == 1
    assert {p.smiles for p in response.records[0].products} == {"CT_C", "CT_D"}


def test_contains_both_sides_matches_a_larger_reaction(db_session):
    """One species named per side; the stored reaction has two on each."""
    _setup_reaction(
        db_session,
        reactants_smiles=["CB_A", "CB_B"],
        products_smiles=["CB_C", "CB_D"],
    )

    response = search_reactions(
        db_session,
        ReactionSearchRequest(reactants=["CB_A"], products=["CB_C"]),
    )

    assert len(response.records) == 1


def test_exact_rejects_the_partial_query_contains_accepts(db_session):
    """Same stored reaction, same query, opposite answers — that is the point."""
    _setup_reaction(
        db_session,
        reactants_smiles=["EX_A", "EX_B"],
        products_smiles=["EX_C", "EX_D"],
    )

    partial = ReactionSearchRequest(
        reactants=["EX_A"],
        products=["EX_C"],
        match=ReactionMatchMode.exact,
    )
    assert search_reactions(db_session, partial).records == []

    whole = ReactionSearchRequest(
        reactants=["EX_A", "EX_B"],
        products=["EX_C", "EX_D"],
        match=ReactionMatchMode.exact,
    )
    assert len(search_reactions(db_session, whole).records) == 1


def test_exact_reproduces_the_old_result_for_a_full_query(db_session):
    """The migration path for callers who relied on the old default.

    A full two-sided query returned exactly this reaction before ``match``
    existed. ``match=exact`` must still return exactly it, and the default
    ``contains`` must return it too — containment widens the answer set, it
    never drops a record the old semantics returned.
    """
    _setup_reaction(
        db_session,
        reactants_smiles=["OLD_A", "OLD_B"],
        products_smiles=["OLD_C"],
    )

    fields = {"reactants": ["OLD_A", "OLD_B"], "products": ["OLD_C"]}
    exact = search_reactions(
        db_session, ReactionSearchRequest(match=ReactionMatchMode.exact, **fields)
    )
    default = search_reactions(db_session, ReactionSearchRequest(**fields))

    assert len(exact.records) == 1
    assert [r.reaction_entry_id for r in default.records] == [
        r.reaction_entry_id for r in exact.records
    ]


def test_exact_still_rejects_a_reactants_only_query(db_session):
    """``exact`` keeps the old behaviour, defect included, by explicit request.

    Asking for "reactants exactly {X} and products exactly {}" is still
    unanswerable. Under ``exact`` that is the caller's stated question, not
    the endpoint silently substituting one.
    """
    _setup_reaction(
        db_session,
        reactants_smiles=["XO_A"],
        products_smiles=["XO_B"],
    )

    response = search_reactions(
        db_session,
        ReactionSearchRequest(reactants=["XO_A"], match=ReactionMatchMode.exact),
    )

    assert response.records == []


def test_contains_is_set_not_multiset_query_names_fewer(db_session):
    """A reaction consuming two of a species matches a query naming one.

    This is the stoichiometry decision: containment ignores counts. If it
    were multiset containment this query would return nothing.
    """
    _setup_reaction(
        db_session,
        reactants_smiles=["DUP_A", "DUP_A"],
        products_smiles=["DUP_B"],
    )

    response = search_reactions(db_session, ReactionSearchRequest(reactants=["DUP_A"]))

    assert len(response.records) == 1
    assert [p.smiles for p in response.records[0].reactants] == ["DUP_A", "DUP_A"]


def test_contains_is_set_not_multiset_query_names_more(db_session):
    """And the converse: naming a species twice matches a reaction with one.

    Multiset containment in the other direction would reject this. Set
    semantics accept it, which is the reading the docstring commits to.
    """
    _setup_reaction(
        db_session,
        reactants_smiles=["DUPQ_A"],
        products_smiles=["DUPQ_B"],
    )

    response = search_reactions(
        db_session, ReactionSearchRequest(reactants=["DUPQ_A", "DUPQ_A"])
    )

    assert len(response.records) == 1


def test_exact_does_count_stoichiometry(db_session):
    """``exact`` is the mode that does care: one queried NN != two stored."""
    _setup_reaction(
        db_session,
        reactants_smiles=["ES_A", "ES_A"],
        products_smiles=["ES_B"],
    )

    one = search_reactions(
        db_session,
        ReactionSearchRequest(
            reactants=["ES_A"], products=["ES_B"], match=ReactionMatchMode.exact
        ),
    )
    two = search_reactions(
        db_session,
        ReactionSearchRequest(
            reactants=["ES_A", "ES_A"],
            products=["ES_B"],
            match=ReactionMatchMode.exact,
        ),
    )

    assert one.records == []
    assert len(two.records) == 1


# ---------------------------------------------------------------------------
# direction x match composition
# ---------------------------------------------------------------------------


def test_contains_either_matches_the_reverse_orientation(db_session):
    """A species stored as a product, asked for as a reactant, under ``either``."""
    _setup_reaction(
        db_session,
        reactants_smiles=["DE_A"],
        products_smiles=["DE_B", "DE_C"],
    )

    response = search_reactions(
        db_session,
        ReactionSearchRequest(
            reactants=["DE_B"], direction=ReactionDirectionQuery.either
        ),
    )

    assert len(response.records) == 1
    assert response.records[0].matched_direction == ReactionDirectionQuery.reverse


def test_contains_either_reports_forward_for_a_one_sided_forward_match(db_session):
    """matched_direction must follow the same semantics the matcher used.

    Resolving the orientation with multiset equality while the matcher used
    containment reports ``reverse`` for every one-sided ``contains`` hit,
    including this obviously-forward one.
    """
    _setup_reaction(
        db_session,
        reactants_smiles=["DF_A", "DF_B"],
        products_smiles=["DF_C"],
    )

    response = search_reactions(
        db_session,
        ReactionSearchRequest(
            reactants=["DF_A"], direction=ReactionDirectionQuery.either
        ),
    )

    assert len(response.records) == 1
    assert response.records[0].matched_direction == ReactionDirectionQuery.forward


def test_contains_forward_does_not_match_the_reverse_orientation(db_session):
    """``direction=forward`` still pins the orientation under containment."""
    _setup_reaction(
        db_session,
        reactants_smiles=["DFW_A"],
        products_smiles=["DFW_B", "DFW_C"],
    )

    matched = search_reactions(
        db_session,
        ReactionSearchRequest(
            reactants=["DFW_A"], direction=ReactionDirectionQuery.forward
        ),
    )
    unmatched = search_reactions(
        db_session,
        ReactionSearchRequest(
            reactants=["DFW_B"], direction=ReactionDirectionQuery.forward
        ),
    )

    assert len(matched.records) == 1
    assert unmatched.records == []


def test_contains_reverse_matches_only_the_swapped_orientation(db_session):
    _setup_reaction(
        db_session,
        reactants_smiles=["DRV_A"],
        products_smiles=["DRV_B", "DRV_C"],
    )

    matched = search_reactions(
        db_session,
        ReactionSearchRequest(
            reactants=["DRV_B"], direction=ReactionDirectionQuery.reverse
        ),
    )
    unmatched = search_reactions(
        db_session,
        ReactionSearchRequest(
            reactants=["DRV_A"], direction=ReactionDirectionQuery.reverse
        ),
    )

    assert len(matched.records) == 1
    assert unmatched.records == []


def test_exact_either_still_matches_in_both_orientations(db_session):
    """``exact`` composes with ``either`` exactly as it did before ``match``."""
    _setup_reaction(
        db_session,
        reactants_smiles=["EE_A"],
        products_smiles=["EE_B"],
    )

    response = search_reactions(
        db_session,
        ReactionSearchRequest(
            reactants=["EE_B"],
            products=["EE_A"],
            direction=ReactionDirectionQuery.either,
            match=ReactionMatchMode.exact,
        ),
    )

    assert len(response.records) == 1
    assert response.records[0].matched_direction == ReactionDirectionQuery.reverse


def test_exact_forward_rejects_what_exact_either_accepts(db_session):
    _setup_reaction(
        db_session,
        reactants_smiles=["EF_A"],
        products_smiles=["EF_B"],
    )

    response = search_reactions(
        db_session,
        ReactionSearchRequest(
            reactants=["EF_B"],
            products=["EF_A"],
            direction=ReactionDirectionQuery.forward,
            match=ReactionMatchMode.exact,
        ),
    )

    assert response.records == []


# ---------------------------------------------------------------------------
# Unknown species and the echo
# ---------------------------------------------------------------------------


def test_contains_query_naming_an_unknown_species_returns_nothing(db_session):
    """An unresolvable SMILES still short-circuits to empty, under contains too.

    Documented here because it is *not* the same failure as the one this
    change fixes: the empty answer is caused by the species being unknown to
    the database, not by the query shape being unanswerable. It is still a
    silent empty rather than a "no such species" error — see the PR notes.
    """
    _setup_reaction(
        db_session,
        reactants_smiles=["UNK_A"],
        products_smiles=["UNK_B"],
    )

    response = search_reactions(
        db_session,
        ReactionSearchRequest(reactants=["UNK_A", "NOT_A_REAL_SPECIES"]),
    )

    assert response.records == []
    assert response.pagination.total == 0


def test_filter_echo_reports_the_match_mode(db_session):
    """The mode changes the meaning of the answer, so it is echoed always."""
    _setup_reaction(
        db_session,
        reactants_smiles=["ECHO_A"],
        products_smiles=["ECHO_B"],
    )

    default = search_reactions(db_session, ReactionSearchRequest(reactants=["ECHO_A"]))
    exact = search_reactions(
        db_session,
        ReactionSearchRequest(
            reactants=["ECHO_A"],
            products=["ECHO_B"],
            match=ReactionMatchMode.exact,
        ),
    )

    assert default.request.filter["match"] == "contains"
    assert exact.request.filter["match"] == "exact"
