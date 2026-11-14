"""deposit_position on reaction-entry reads is global to the reaction and survives filters."""

from __future__ import annotations

from app.db.models.common import RecordReviewStatus, SubmissionRecordType
from app.schemas.reads.scientific_reactions import ReactionsBrowseRequest
from app.services.scientific_read.reactions import browse_reactions
from tests.services.scientific_read._factories import (
    make_chem_reaction,
    make_reaction_entry,
    make_species,
    make_species_entry,
    next_inchi_key,
    set_review,
)


def _reaction_with_entries(session, n):
    reactants = [make_species(session, smiles="[CH3]", inchi_key=next_inchi_key("R"))]
    products = [make_species(session, smiles="C", inchi_key=next_inchi_key("P"))]
    r_entries = [make_species_entry(session, sp) for sp in reactants]
    p_entries = [make_species_entry(session, sp) for sp in products]
    chem = make_chem_reaction(session, reactants=reactants, products=products)
    entries = [
        make_reaction_entry(session, reaction=chem, reactant_entries=r_entries, product_entries=p_entries)
        for _ in range(n)
    ]
    return chem, entries


def _positions(session, **kwargs):
    resp = browse_reactions(session, ReactionsBrowseRequest(reactant_smiles=["[CH3]"], **kwargs))
    return {rec.reaction_entry_id: rec.deposit_position for rec in resp.records}


def test_positions_are_one_based_in_deposit_order(db_session):
    _, entries = _reaction_with_entries(db_session, 3)
    assert _positions(db_session) == {e.id: i for i, e in enumerate(entries, start=1)}


def test_positions_stable_when_review_status_changes(db_session):
    _, entries = _reaction_with_entries(db_session, 3)
    before = _positions(db_session)
    set_review(
        db_session,
        record_type=SubmissionRecordType.reaction_entry,
        record_id=entries[0].id,
        status=RecordReviewStatus.approved,
    )
    assert _positions(db_session) == before


def test_positions_are_global_across_pages(db_session):
    _, entries = _reaction_with_entries(db_session, 4)
    expected = {e.id: i for i, e in enumerate(entries, start=1)}
    seen: dict[int, int] = {}
    for offset in (0, 2):
        page = _positions(db_session, limit=2, offset=offset)
        assert len(page) == 2
        seen.update(page)
    assert seen == expected


def test_hidden_entry_leaves_a_gap(db_session):
    _, entries = _reaction_with_entries(db_session, 3)
    set_review(
        db_session,
        record_type=SubmissionRecordType.reaction_entry,
        record_id=entries[1].id,
        status=RecordReviewStatus.rejected,
    )
    positions = _positions(db_session)
    assert entries[1].id not in positions
    assert positions == {entries[0].id: 1, entries[2].id: 3}
