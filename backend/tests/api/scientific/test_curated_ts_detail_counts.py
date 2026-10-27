"""B1 (review of #826): the transition-state detail counts the entries it lists.

Under ``curated`` a served transition state lists every entry that is not rejected or deprecated, badged. Its
``entries_summary``, ``evidence_summary`` and ``available_sections`` used to count by the approved record floor, so a page
listing one badged entry said ``total=0`` and ``has_entries=False``; an approved TS with an unreviewed entry listed 2 and
counted 1; and that entry's own detail said ``has_entries=False`` while listing two siblings.
"""

from __future__ import annotations

from sqlalchemy import delete

from app.db.models.common import RecordReviewStatus, SubmissionRecordType
from app.db.models.transition_state import TransitionStateEntry
from tests.services.scientific_read._factories import (
    make_calculation,
    make_chem_reaction,
    make_reaction_entry,
    make_species,
    make_species_entry,
    make_transition_state,
    make_transition_state_entry,
    next_inchi_key,
    set_review,
)

BASE = "/api/v1/scientific"


def _ts_scene(db_session):
    rs = make_species(db_session, smiles="CC", inchi_key=next_inchi_key("B1R"))
    ps = make_species(db_session, smiles="CCC", inchi_key=next_inchi_key("B1P"))
    chem = make_chem_reaction(db_session, reactants=[rs], products=[ps])
    rxe = make_reaction_entry(
        db_session,
        reaction=chem,
        reactant_entries=[make_species_entry(db_session, rs)],
        product_entries=[make_species_entry(db_session, ps)],
    )
    ts = make_transition_state(db_session, reaction_entry=rxe, label="b1")
    first = make_transition_state_entry(db_session, transition_state=ts)
    second = make_transition_state_entry(db_session, transition_state=ts)
    make_calculation(db_session, transition_state_entry_id=first.id)
    return ts, first, second


def test_an_unreviewed_ts_with_one_unreviewed_entry_counts_it(client, db_session):
    ts, first, second = _ts_scene(db_session)
    db_session.execute(delete(TransitionStateEntry).where(TransitionStateEntry.id == second.id))
    db_session.flush()
    response = client.get(
        f"{BASE}/transition-states/{ts.public_ref}",
        params={"profile": "curated", "include": ["entries", "calculations"]},
    )
    assert response.status_code == 200, response.text
    record = response.json()["record"]
    assert len(record["entries"]) == 1
    assert record["entries_summary"]["total"] == 1
    assert record["evidence_summary"]["entry_count"] == 1
    assert record["available_sections"]["has_entries"] and record["available_sections"]["has_calculations"]
    assert response.json()["review_status_by_ref"][first.public_ref] == "not_reviewed"


def test_an_approved_ts_counts_its_unreviewed_entry_and_the_entry_detail_sees_the_sibling(client, db_session):
    ts, first, second = _ts_scene(db_session)
    set_review(db_session, record_type=SubmissionRecordType.transition_state, record_id=ts.id, status=RecordReviewStatus.approved)
    set_review(
        db_session, record_type=SubmissionRecordType.transition_state_entry, record_id=first.id, status=RecordReviewStatus.approved
    )
    params = {"profile": "curated", "include": ["entries"]}  # the second entry has no review row: not_reviewed
    record = client.get(f"{BASE}/transition-states/{ts.public_ref}", params=params).json()["record"]
    assert len(record["entries"]) == 2 and record["entries_summary"]["total"] == 2
    entry = client.get(f"{BASE}/transition-state-entries/{first.public_ref}", params=params).json()["record"]
    assert len(entry["entries"]) == 2
    assert entry["available_sections"]["has_entries"] is True

    # A withheld sibling is neither listed nor counted.
    set_review(
        db_session, record_type=SubmissionRecordType.transition_state_entry, record_id=second.id, status=RecordReviewStatus.rejected
    )
    entry = client.get(f"{BASE}/transition-state-entries/{first.public_ref}", params=params).json()["record"]
    assert len(entry["entries"]) == 1 and entry["available_sections"]["has_entries"] is False
    record = client.get(f"{BASE}/transition-states/{ts.public_ref}", params=params).json()["record"]
    assert len(record["entries"]) == 1 and record["entries_summary"]["total"] == 1
