"""Service-layer tests for search_kinetics (chemistry-first kinetics search)."""

from __future__ import annotations

import pytest

from app.db.models.common import (
    RecordReviewStatus,
    ScientificOriginKind,
    SubmissionRecordType,
)
from app.schemas.reads.scientific_common import CollapseMode
from app.schemas.reads.scientific_kinetics_search import KineticsSearchRequest
from app.schemas.reads.scientific_reactions import ReactionDirectionQuery
from app.services.scientific_read.kinetics_search import search_kinetics
from tests.services.scientific_read._factories import (
    make_chem_reaction,
    make_kinetics,
    make_reaction_entry,
    make_species,
    make_species_entry,
    next_inchi_key,
    set_review,
)


def _setup_reaction_with_kinetics(
    db_session,
    *,
    reactant_smiles: str = "A",
    product_smiles: str = "B",
    scientific_origin: ScientificOriginKind = ScientificOriginKind.computed,
):
    rs = make_species(db_session, smiles=reactant_smiles, inchi_key=next_inchi_key("KSR"))
    ps = make_species(db_session, smiles=product_smiles, inchi_key=next_inchi_key("KSP"))
    chem = make_chem_reaction(db_session, reactants=[rs], products=[ps])
    entry = make_reaction_entry(
        db_session,
        reaction=chem,
        reactant_entries=[make_species_entry(db_session, rs)],
        product_entries=[make_species_entry(db_session, ps)],
    )
    k = make_kinetics(
        db_session, reaction_entry=entry, scientific_origin=scientific_origin
    )
    return chem, entry, k


# ---------------------------------------------------------------------------
# Identity resolution + composition
# ---------------------------------------------------------------------------


def test_search_returns_kinetics_with_reaction_context(db_session):
    chem, entry, k = _setup_reaction_with_kinetics(
        db_session, reactant_smiles="X1", product_smiles="Y1"
    )

    response = search_kinetics(
        db_session,
        KineticsSearchRequest(reactants=["X1"], products=["Y1"]),
    )

    assert len(response.records) == 1
    rec = response.records[0]
    assert rec.reaction.reaction_id == chem.id
    assert rec.reaction.reaction_entry_id == entry.id
    assert rec.kinetics.kinetics_id == k.id


def test_search_records_carry_matched_direction(db_session):
    _setup_reaction_with_kinetics(
        db_session, reactant_smiles="X2", product_smiles="Y2"
    )

    response = search_kinetics(
        db_session,
        KineticsSearchRequest(
            reactants=["Y2"],
            products=["X2"],
            direction=ReactionDirectionQuery.either,
        ),
    )

    assert len(response.records) == 1
    assert response.records[0].reaction.matched_direction == ReactionDirectionQuery.reverse


def test_search_returns_empty_when_no_matching_reaction(db_session):
    response = search_kinetics(
        db_session,
        KineticsSearchRequest(reactants=["NEVER_X"], products=["NEVER_Y"]),
    )
    assert response.records == []
    assert response.pagination.total == 0


# ---------------------------------------------------------------------------
# Non-TS-backed kinetics
# ---------------------------------------------------------------------------


def test_non_ts_backed_kinetics_provenance_is_null(db_session):
    _setup_reaction_with_kinetics(
        db_session,
        reactant_smiles="EXP1",
        product_smiles="EXP2",
        scientific_origin=ScientificOriginKind.experimental,
    )

    response = search_kinetics(
        db_session,
        KineticsSearchRequest(reactants=["EXP1"], products=["EXP2"]),
    )

    rec = response.records[0]
    assert rec.kinetics.scientific_origin == ScientificOriginKind.experimental
    p = rec.kinetics.provenance
    assert p.transition_state_entry_id is None
    assert p.ts_opt_calculation_id is None
    assert p.path_search is None


# ---------------------------------------------------------------------------
# Collapse + pagination
# ---------------------------------------------------------------------------


def test_collapse_first_preserves_plural_records_with_pre_collapse_total(db_session):
    chem, entry, _ = _setup_reaction_with_kinetics(
        db_session, reactant_smiles="C1", product_smiles="C2"
    )
    make_kinetics(db_session, reaction_entry=entry, ea_kj_mol=20.0)

    response = search_kinetics(
        db_session,
        KineticsSearchRequest(
            reactants=["C1"], products=["C2"], collapse=CollapseMode.first
        ),
    )

    assert len(response.records) == 1
    assert response.pagination.total == 2
    assert response.pagination.post_collapse_total == 1
    assert response.pagination.returned == 1


def test_collapse_first_applies_offset_after_collapse(db_session):
    _, entry, _ = _setup_reaction_with_kinetics(
        db_session,
        reactant_smiles="[SiH3]",
        product_smiles="[SiH2]",
    )
    make_kinetics(db_session, reaction_entry=entry)

    response = search_kinetics(
        db_session,
        KineticsSearchRequest(
            reactants=["[SiH3]"],
            products=["[SiH2]"],
            collapse=CollapseMode.first,
            offset=1,
        ),
    )

    assert response.records == []
    assert response.pagination.total == 2
    assert response.pagination.returned == 0


# ---------------------------------------------------------------------------
# Review (shallow on the kinetics record)
# ---------------------------------------------------------------------------


def test_min_review_status_filters_kinetics_record_only(db_session):
    chem, entry, k = _setup_reaction_with_kinetics(
        db_session, reactant_smiles="REV1", product_smiles="REV2"
    )
    set_review(
        db_session,
        record_type=SubmissionRecordType.kinetics,
        record_id=k.id,
        status=RecordReviewStatus.approved,
    )
    k2 = make_kinetics(db_session, reaction_entry=entry, ea_kj_mol=20.0)
    set_review(
        db_session,
        record_type=SubmissionRecordType.kinetics,
        record_id=k2.id,
        status=RecordReviewStatus.under_review,
    )

    response = search_kinetics(
        db_session,
        KineticsSearchRequest(
            reactants=["REV1"],
            products=["REV2"],
            min_review_status=RecordReviewStatus.approved,
        ),
    )

    assert [r.kinetics.kinetics_id for r in response.records] == [k.id]


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def test_direction_exact_not_in_v0_enum(db_session):
    """direction=exact is not a legal enum value in v0."""
    with pytest.raises(ValueError):
        KineticsSearchRequest(
            reactants=["A"], products=["B"], direction="exact"
        )


def test_client_supplied_sort_rejected(db_session):
    with pytest.raises(ValueError, match="client_sort_not_supported"):
        search_kinetics(
            db_session,
            KineticsSearchRequest(
                reactants=["A"], products=["B"], sort="anything"
            ),
        )


def test_unknown_include_token_rejected(db_session):
    _setup_reaction_with_kinetics(
        db_session, reactant_smiles="IK1", product_smiles="IK2"
    )
    with pytest.raises(ValueError, match="unknown_include_token"):
        search_kinetics(
            db_session,
            KineticsSearchRequest(
                reactants=["IK1"], products=["IK2"], include=["banana"]
            ),
        )


def test_temperature_min_greater_than_max_rejected(db_session):
    with pytest.raises(ValueError, match="invalid_temperature_range"):
        search_kinetics(
            db_session,
            KineticsSearchRequest(
                reactants=["A"],
                products=["B"],
                temperature_min=2000.0,
                temperature_max=300.0,
            ),
        )


def test_sort_is_deterministic_across_calls(db_session):
    _setup_reaction_with_kinetics(
        db_session, reactant_smiles="DET1", product_smiles="DET2"
    )

    r1 = search_kinetics(
        db_session, KineticsSearchRequest(reactants=["DET1"], products=["DET2"])
    )
    r2 = search_kinetics(
        db_session, KineticsSearchRequest(reactants=["DET1"], products=["DET2"])
    )
    assert r1.model_dump() == r2.model_dump()
