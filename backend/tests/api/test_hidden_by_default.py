"""A list that hides rejected and deprecated records by default says how many it hid (exploratory), and says nothing
about records below the curated floor (curated, decision D6: hidden == unknown == absent).

Every scientific list endpoint that applies the default visibility is driven from the echo test's seeds
(``CASES``), re-reviewed so that one row is rejected and one deprecated.
"""

from __future__ import annotations

import json

import pytest
from sqlalchemy import select

from app.db.models.common import RecordReviewStatus, SubmissionRecordType
from app.db.models.network import Network
from app.db.models.reaction import ReactionEntry
from app.db.models.record_review import RecordReview
from app.db.models.species import ConformerGroup, SpeciesEntry
from app.db.models.transition_state import TransitionState
from tests.api.test_browse_order_echo import CASES, review
from tests.services.scientific_read._factories import set_review

#: Cases that list reference data with no review state: the default visibility hides nothing there. The literature
#: records list is mixed-kind and has its own test below.
NO_REVIEW_STATE = {
    "levels-of-theory-browse",
    "levels-of-theory-search",
    "frequency-scale-factors-search",
    "energy-correction-schemes-search",
    "literature-records",
}

S = RecordReviewStatus
#: row 0 rejected, row 1 deprecated, the rest visible (one approved).
HIDING_SPECS = [(S.rejected, 1), (S.deprecated, 2), (S.approved, 3), (S.not_reviewed, 4), (S.under_review, 5)]

#: The lookup envelope has its own shape and its own tests (test_lookup_visibility_and_selection.py).
HIDING_CASES = sorted(c for c in set(CASES) - NO_REVIEW_STATE if not c.startswith("lookup-"))

#: A species list always lists its species (a species has no review state of its own); what the default hides there is
#: species entries, so the visible rows are counted among the entries.
LISTS_PARENTS = {"species-search"}

#: The one visible row of this seed has no energy, so a curated lowest-energy read refuses (it names no hidden row).
CURATED_REFUSES = {"species-calculations-lowest-energy"}

#: Scoping parents the curated floor is applied to when a search walks through them.
_PARENTS = {
    SubmissionRecordType.species_entry: SpeciesEntry,
    SubmissionRecordType.reaction_entry: ReactionEntry,
    SubmissionRecordType.transition_state: TransitionState,
    SubmissionRecordType.network: Network,
    SubmissionRecordType.conformer_group: ConformerGroup,
}


def _seed(case, db_session):
    seeded = CASES[case](db_session)
    seeded.specs = list(HIDING_SPECS)
    review(db_session, seeded)
    return seeded


def _approve_parents(db_session, seeded):
    """The curated floor applies to the scoping parents a search walks through; approve the ones that are not the
    records under test, so that the only thing separating the rows is their own review."""
    subjects = set(seeded.review_targets)
    for record_type, model in _PARENTS.items():
        for row_id in db_session.scalars(select(model.id)):
            if (record_type, row_id) in subjects:
                continue
            exists = db_session.scalar(
                select(RecordReview.id).where(RecordReview.record_type == record_type, RecordReview.record_id == row_id)
            )
            if exists is None:
                set_review(db_session, record_type=record_type, record_id=row_id, status=S.approved)
    db_session.flush()


def _get(client, seeded, **params):
    sep = "&" if "?" in seeded.url else "?"
    query = "&".join(f"{k}={v}" for k, v in params.items())
    url = seeded.url + (sep + query if query else "")
    if seeded.post_body is not None:
        return client.post(url, json=seeded.post_body).json()
    return client.get(url).json()


def _served(body, seeded, case):
    """Indexes of the seeded rows the response shows (for a species list: of the entries it shows)."""
    row_refs = seeded.row_refs or [row.public_ref for row in seeded.rows]
    refs = {ref: i for i, ref in enumerate(row_refs)}
    if case in LISTS_PARENTS:
        entry_ids = [e["species_entry_ref"] for r in body["records"] for e in r["entries"]]
        return sorted(i for i, (_t, rid) in enumerate(seeded.review_targets) if _entry_ref(rid) in entry_ids)
    return sorted(refs[seeded.ref_of(r)] for r in body["records"] if seeded.ref_of(r) in refs)


_ENTRY_REFS: dict[int, str] = {}


def _entry_ref(entry_id):
    return _ENTRY_REFS[entry_id]


@pytest.fixture(autouse=True)
def _entry_refs(db_session):
    _ENTRY_REFS.clear()
    yield


def _remember_entry_refs(db_session):
    for ref, row_id in db_session.execute(select(SpeciesEntry.public_ref, SpeciesEntry.id)):
        _ENTRY_REFS[row_id] = ref


def _seeded(case, db_session):
    seeded = _seed(case, db_session)
    _remember_entry_refs(db_session)
    return seeded


@pytest.mark.parametrize("case", HIDING_CASES)
def test_the_default_says_how_many_it_hid_and_how_to_include_them(case, client, db_session):
    seeded = _seeded(case, db_session)
    body = _get(client, seeded)
    assert _served(body, seeded, case) == [2, 3, 4]
    hidden = body["request"]["hidden_by_default"]
    assert hidden["counts"] == {"rejected": 1, "deprecated": 1}
    assert hidden["include_with"] == {"rejected": "include_rejected=true", "deprecated": "include_deprecated=true"}


@pytest.mark.parametrize("case", HIDING_CASES)
def test_an_exclusion_the_request_lifted_is_not_reported_as_hidden(case, client, db_session):
    seeded = _seeded(case, db_session)
    body = _get(client, seeded, include_rejected="true")
    assert _served(body, seeded, case) == [0, 2, 3, 4]
    assert body["request"]["hidden_by_default"]["counts"] == {"deprecated": 1}
    body = _get(client, seeded, include_rejected="true", include_deprecated="true")
    assert _served(body, seeded, case) == [0, 1, 2, 3, 4]
    assert body["request"]["hidden_by_default"] is None


@pytest.mark.parametrize("case", HIDING_CASES)
def test_the_curated_profile_withholds_below_floor_records_without_counting_them(case, client, db_session):
    """Kills: a count (or any trace) of the records the floor withheld. Every opt-in is also refused to lift the floor."""
    if case in CURATED_REFUSES:
        pytest.skip("covered by test_a_curated_lowest_energy_read_refuses_without_naming_hidden_rows")
    seeded = _seeded(case, db_session)
    _approve_parents(db_session, seeded)
    body = _get(client, seeded, profile="curated", include_rejected="true", include_deprecated="true")
    assert _served(body, seeded, case) == [2]  # only the approved row
    assert body["request"]["hidden_by_default"] is None
    assert body["request"]["profile"] == "curated"
    # Nothing in the document says how many records exist below the floor.
    text = json.dumps(body)
    assert '"counts"' not in text and "include_rejected=true" not in text


@pytest.mark.parametrize("case", HIDING_CASES)
def test_the_curated_page_counts_only_what_it_shows(case, client, db_session):
    """Every count a curated reader sees (total, summary) is a count of the visible rows: no record below the floor is
    in any of them, so curated(full world) is indistinguishable from curated(world minus the hidden records)."""
    if case in CURATED_REFUSES:
        pytest.skip("see above")
    seeded = _seeded(case, db_session)
    _approve_parents(db_session, seeded)
    body = _get(client, seeded, profile="curated")
    pagination = body.get("pagination")
    if pagination is not None and case not in LISTS_PARENTS:
        assert pagination["total"] == 1
        assert pagination["returned"] == 1
    summary = body.get("review_summary")
    if summary is not None:
        assert summary["rejected"] == 0 and summary["deprecated"] == 0
        assert summary["under_review"] == 0 and summary["not_reviewed"] == 0
        assert summary["total"] in (1, 0 if case in LISTS_PARENTS else 1)


def test_the_literature_records_list_reports_what_the_default_hid(client, db_session):
    from tests.services.scientific_read._factories import make_literature, make_species, make_species_entry
    from tests.services.scientific_read._factories import make_statmech, make_transport, next_inchi_key

    literature = make_literature(db_session)
    species = make_species(db_session, smiles="C=O", inchi_key=next_inchi_key("HL"))
    entry = make_species_entry(db_session, species)
    rows = [
        (make_statmech(db_session, species_entry=entry, literature_id=literature.id), SubmissionRecordType.statmech, S.rejected),
        (make_transport(db_session, species_entry=entry, literature_id=literature.id), SubmissionRecordType.transport, S.deprecated),
        (make_statmech(db_session, species_entry=entry, literature_id=literature.id), SubmissionRecordType.statmech, S.approved),
        (make_transport(db_session, species_entry=entry, literature_id=literature.id), SubmissionRecordType.transport, S.not_reviewed),
    ]
    for row, record_type, status in rows:
        if status is not S.not_reviewed:
            set_review(db_session, record_type=record_type, record_id=row.id, status=status)
    url = f"/api/v1/scientific/literature/{literature.public_ref}/records"

    body = client.get(url).json()
    assert len(body["records"]) == 2
    assert body["request"]["hidden_by_default"]["counts"] == {"rejected": 1, "deprecated": 1}
    curated = client.get(url, params={"profile": "curated"}).json()
    assert len(curated["records"]) == 1
    assert curated["request"]["hidden_by_default"] is None


def test_a_curated_lowest_energy_read_refuses_without_naming_hidden_rows(client, db_session):
    """Only the hidden rows have energies; the curated read behaves as if they were absent (nothing to rank)."""
    seeded = _seeded("species-calculations-lowest-energy", db_session)
    _approve_parents(db_session, seeded)
    response = client.get(seeded.url + "&profile=curated")
    assert response.status_code == 422
    body = response.json()
    context = body.get("context") or body.get("error", {}).get("context") or body["detail"]["context"]
    assert context["candidate_count"] == 1  # the approved row alone: hidden rows are not in the count


def test_a_review_of_another_kind_of_record_with_the_same_id_is_not_counted(client, db_session):
    """Kills: counting review rows without checking which kind of record they review (ids repeat across kinds)."""
    from tests.services.scientific_read._factories import (
        make_calculation,
        make_species,
        make_species_entry,
        next_inchi_key,
    )

    species = make_species(db_session, smiles="C=O", inchi_key=next_inchi_key("HK"))
    entry = make_species_entry(db_session, species)
    calc = make_calculation(db_session, species_entry_id=entry.id)
    # A statmech with the same numeric id is rejected; the calculation itself is not.
    set_review(db_session, record_type=SubmissionRecordType.statmech, record_id=calc.id, status=S.rejected)
    body = client.get(f"/api/v1/scientific/calculations/search?species_entry_ref={entry.public_ref}").json()
    assert body["request"]["hidden_by_default"]["counts"] == {"rejected": 0, "deprecated": 0}
    assert len(body["records"]) == 1
