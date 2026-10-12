"""A statmech record is a release candidate for its own owner: a species entry or a TS entry.

PR 6.5. The owner is a discriminated pair (``statmech_exactly_one_subject``),
so a release derives the subject from whichever key is set. Supporting a TS
candidate never selects, embeds or endorses its sibling statmech records.
"""

from __future__ import annotations

import json

import pytest
from sqlalchemy import select

from app.db.models.common import SubmissionRecordType
from app.db.models.statmech import Statmech
from app.services.release import artifacts as artifacts_module
from app.services.release.artifacts import (
    ReleaseRecordUniverse,
    load_selection_state,
    render_artifacts,
)
from app.services.release.curation import (
    ReleaseCurationError,
    _assert_subject_matches,
    add_selection,
    owner_of,
    resolve_selectable_record,
)
from app.services.release.records import (
    CANDIDATE_SOURCES,
    CandidateOwner,
    serialize_records,
    subject_identities,
)
from tests.services.release._ts_statmech import approve_and_license, make_ts_entry, make_ts_statmech
from tests.services.scientific_read._factories import make_statmech

SM = SubmissionRecordType.statmech
TSE = SubmissionRecordType.transition_state_entry


@pytest.fixture
def ts_entry(db_session):
    return make_ts_entry(db_session)


@pytest.fixture
def ts_statmechs(db_session, curator, attested_submission, ts_entry):
    rows = [
        make_ts_statmech(db_session, ts_entry, external_symmetry=1),
        make_ts_statmech(db_session, ts_entry, external_symmetry=2),
    ]
    approve_and_license(db_session, curator, attested_submission, rows)
    return rows


def _select_ts(db_session, release, curator, row, ts_entry):
    return add_selection(
        db_session,
        release=release,
        record_type=SM,
        record_id=row.id,
        subject_type=TSE,
        subject_id=ts_entry.id,
        rationale="RRHO treatment at the reported level.",
        selected_by=curator.id,
    )


def _lines(db_session, release, path):
    artifact = next(a for a in render_artifacts(db_session, release) if a.path == path)
    return [json.loads(line) for line in artifact.content.decode().splitlines() if line]


def test_a_ts_owned_statmech_is_selectable_for_its_ts_entry(db_session, draft_release, curator, ts_statmechs, ts_entry):
    chosen = ts_statmechs[0]
    resolved = resolve_selectable_record(db_session, chosen.public_ref)
    assert (resolved.record_type, resolved.subject_type, resolved.subject_id) == (SM, TSE, ts_entry.id)

    row = _select_ts(db_session, draft_release, curator, chosen, ts_entry)
    assert (row.subject_type, row.subject_id) == (TSE, ts_entry.id)


def test_one_selected_ts_statmech_does_not_select_its_siblings(db_session, draft_release, curator, ts_statmechs, ts_entry):
    chosen, sibling = ts_statmechs
    _select_ts(db_session, draft_release, curator, chosen, ts_entry)

    selected = _lines(db_session, draft_release, "selected_records.ndjson")
    candidates = _lines(db_session, draft_release, "candidate_records.ndjson")

    assert [line["record_ref"] for line in selected] == [chosen.public_ref]
    ts_candidates = {c["record_ref"]: c for c in candidates if c["record_type"] == "statmech"}
    assert set(ts_candidates) == {chosen.public_ref, sibling.public_ref}
    for line in ts_candidates.values():
        assert line["subject_type"] == "transition_state_entry"
        assert line["subject_ref"] == ts_entry.public_ref
    assert ts_candidates[chosen.public_ref]["selected_in_release"] is True
    assert ts_candidates[sibling.public_ref]["selected_in_release"] is False


def test_releasing_the_ts_entry_itself_embeds_no_statmech(db_session, ts_statmechs, ts_entry):
    # The TS entry owns two statmech records, so a child would have something to ship.
    assert len(db_session.scalars(select(Statmech).where(Statmech.transition_state_entry_id == ts_entry.id)).all()) == 2
    payload = serialize_records(db_session, record_type=TSE, record_ids=[ts_entry.id])[ts_entry.id]
    assert "statmech" not in payload
    text = json.dumps(payload, default=str)
    assert not any(row.public_ref in text for row in ts_statmechs)


def test_a_ts_statmech_refuses_a_concept_level_or_species_subject(db_session, ts_statmechs, ts_entry):
    chosen = ts_statmechs[0]
    with pytest.raises(ReleaseCurationError, match="subject_type_mismatch") as excinfo:
        _assert_subject_matches(
            db_session,
            record_type=SM,
            record_id=chosen.id,
            subject_type=SubmissionRecordType.transition_state,
            subject_id=ts_entry.transition_state_id,
        )
    # The message lists what a statmech selection may be about.
    assert "species_entry" in str(excinfo.value) and "transition_state_entry" in str(excinfo.value)

    # An allowed subject type that is not this record's owner is a different refusal.
    with pytest.raises(ReleaseCurationError, match="record_subject_mismatch"):
        _assert_subject_matches(
            db_session,
            record_type=SM,
            record_id=chosen.id,
            subject_type=SubmissionRecordType.species_entry,
            subject_id=ts_entry.id,
        )
    # The true owner is accepted.
    _assert_subject_matches(db_session, record_type=SM, record_id=chosen.id, subject_type=TSE, subject_id=ts_entry.id)


def test_a_ts_statmechs_subject_identity_carries_the_reaction_smiles(db_session, draft_release, curator, ts_statmechs, ts_entry):
    identity = subject_identities(db_session, subject_type=TSE, subject_ids=[ts_entry.id])[ts_entry.id]
    assert identity["transition_state_entry_ref"] == ts_entry.public_ref
    assert identity["label"] == "TS0"
    assert (identity["charge"], identity["multiplicity"]) == (0, 2)
    assert identity["reaction"]["reactants"] == ["CC"]
    assert identity["reaction"]["products"] == ["C=C"]

    # And it reaches the released line, not only the helper.
    _select_ts(db_session, draft_release, curator, ts_statmechs[0], ts_entry)
    line = _lines(db_session, draft_release, "selected_records.ndjson")[0]
    assert line["subject"]["reaction"]["reactants"] == ["CC"]
    assert line["subject"]["reaction"]["products"] == ["C=C"]


def test_the_no_subject_backstop_refuses_a_row_with_no_owner():
    transient = Statmech(species_entry_id=None, transition_state_entry_id=None)
    assert owner_of(transient, CANDIDATE_SOURCES[SM]) is None
    # A row with an owner resolves to it.
    owned = Statmech(species_entry_id=None, transition_state_entry_id=7)
    found = owner_of(owned, CANDIDATE_SOURCES[SM])
    assert found is not None
    assert (found[0].subject_type, found[1]) == (TSE, 7)


def test_statmech_has_exactly_the_two_owners_in_species_first_order():
    owners = CANDIDATE_SOURCES[SM]
    assert [(o.fk_attr, o.subject_type) for o in owners] == [
        ("species_entry_id", SubmissionRecordType.species_entry),
        ("transition_state_entry_id", TSE),
    ]
    assert all(isinstance(o, CandidateOwner) for o in owners)


# --- species-only releases render byte-for-byte as before -------------------


def _legacy_release_record_universe(session, release):
    """The enumeration as it was before statmech had a second owner (single owner per type)."""
    legacy_sources = {
        rt: (owners[0].model, owners[0].fk_attr, owners[0].subject_type) for rt, owners in CANDIDATE_SOURCES.items()
    }
    state = load_selection_state(session, release)
    selected_pairs = {(row.record_type, row.record_id) for row in state.active}
    subject_pairs = {(row.subject_type, row.subject_id) for row in state.all_selections}
    ledger_pairs = {(row.record_type, row.record_id) for row in state.all_selections}
    candidate_pairs: set = set()
    candidates_by_subject: dict = {}
    for record_type in sorted({rt for rt, _ in ledger_pairs}, key=lambda t: t.value):
        model, fk_attr, subject_type = legacy_sources[record_type]
        subject_ids = sorted(sid for stype, sid in subject_pairs if stype == subject_type)
        if not subject_ids:
            continue
        fk = getattr(model, fk_attr)
        grouped: dict = {}
        for record_id, subject_id in session.execute(select(model.id, fk).where(fk.in_(subject_ids)).order_by(model.id)):
            grouped.setdefault(subject_id, []).append(record_id)
        for subject_id, ids in grouped.items():
            for record_id in ids:
                candidates_by_subject[(record_type, record_id)] = (subject_type, subject_id)
            candidate_pairs |= {(record_type, rid) for rid in ids}
    candidate_pairs |= selected_pairs | ledger_pairs
    return ReleaseRecordUniverse(
        state=state,
        selected_pairs=selected_pairs,
        subject_pairs=subject_pairs,
        ledger_pairs=ledger_pairs,
        candidate_pairs=candidate_pairs,
        candidates_by_subject=candidates_by_subject,
    )


def test_a_species_only_release_renders_byte_identically(
    db_session, draft_release, curator, attested_submission, thermo_candidates, species_entry, monkeypatch
):
    chosen_thermo, _ = thermo_candidates
    statmechs = [make_statmech(db_session, species_entry=species_entry) for _ in range(3)]
    approve_and_license(db_session, curator, attested_submission, statmechs)
    add_selection(
        db_session,
        release=draft_release,
        record_type=SubmissionRecordType.thermo,
        record_id=chosen_thermo.id,
        subject_type=SubmissionRecordType.species_entry,
        subject_id=species_entry.id,
        rationale="Composite single point.",
        selected_by=curator.id,
    )
    add_selection(
        db_session,
        release=draft_release,
        record_type=SM,
        record_id=statmechs[1].id,
        subject_type=SubmissionRecordType.species_entry,
        subject_id=species_entry.id,
        rationale="RRHO treatment at the reported level.",
        selected_by=curator.id,
    )

    now = {a.path: a.content for a in render_artifacts(db_session, draft_release)}
    monkeypatch.setattr(artifacts_module, "release_record_universe", _legacy_release_record_universe)
    before = {a.path: a.content for a in render_artifacts(db_session, draft_release)}

    # Non-vacuous: the release carries both statmech candidates and thermo ones.
    candidate_lines = [json.loads(x) for x in now["candidate_records.ndjson"].decode().splitlines()]
    assert sum(1 for c in candidate_lines if c["record_type"] == "statmech") == 3
    assert sum(1 for c in candidate_lines if c["record_type"] == "thermo") == 2
    assert len(now) == 4 and all(now[p] for p in now)
    assert now == before
