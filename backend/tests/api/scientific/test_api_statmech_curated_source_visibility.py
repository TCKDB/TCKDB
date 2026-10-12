"""Under ``profile=curated`` a statmech read must not name calculations the curated profile hides.

``GET /scientific/calculations/{ref}`` answers 404 for a calculation below ``approved`` under the curated
profile. The statmech source-calculations block (ref, role, type, review status) and the frequency pointer
(``source_freq_calculation_refs`` / ``_ids``) used to list such calculations anyway.

Deliberate scope: only those two blocks are filtered. Record-level facts (``levels``, ``evidence_summary``
counts, ``available_sections`` flags, trust) describe the approved statmech's own provenance and do not vary by
profile. Release, archive and export copy raw provenance tables and are untouched.

Each test names the mutation it kills.
"""

from __future__ import annotations

from app.db.models.common import (
    CalculationType,
    RecordReviewStatus,
    StatmechCalculationRole,
    SubmissionRecordType,
)
from tests.services.scientific_read._factories import (
    attach_statmech_source_calculation,
    attach_statmech_torsion,
    make_calculation,
    make_species,
    make_species_entry,
    make_statmech,
    next_inchi_key,
    set_review,
)

R = StatmechCalculationRole
URL = "/api/v1/scientific/statmech/"


def _world(db_session):
    """An approved statmech linking an approved opt and a not-reviewed freq calculation."""
    entry = make_species_entry(db_session, make_species(db_session, inchi_key=next_inchi_key("CSRC")))
    sm = make_statmech(db_session, species_entry=entry)
    shown = make_calculation(db_session, type=CalculationType.opt, species_entry_id=entry.id)
    hidden = make_calculation(db_session, type=CalculationType.freq, species_entry_id=entry.id)
    attach_statmech_source_calculation(db_session, statmech=sm, calculation=shown, role=R.opt)
    attach_statmech_source_calculation(db_session, statmech=sm, calculation=hidden, role=R.freq)
    set_review(db_session, record_type=SubmissionRecordType.calculation, record_id=shown.id, status=RecordReviewStatus.approved)
    # Approve last: an approved statmech is immutable, so its links must exist first.
    set_review(db_session, record_type=SubmissionRecordType.statmech, record_id=sm.id, status=RecordReviewStatus.approved)
    return entry, sm, shown, hidden


def _record(client, sm, profile):
    r = client.get(
        URL + sm.public_ref, params={"include": "source_calculations,frequencies", "profile": profile}
    )
    assert r.status_code == 200
    return r.json()["record"]


def _src_refs(rec):
    return [c["calculation_ref"] for c in rec["source_calculations"]]


def test_curated_hides_the_unapproved_calculation_from_both_blocks(client, db_session):
    """Kills: no filtering of source_rows under a review floor (today's leak)."""
    _, sm, shown, hidden = _world(db_session)
    assert client.get(f"/api/v1/scientific/calculations/{hidden.public_ref}", params={"profile": "curated"}).status_code == 404
    rec = _record(client, sm, "curated")
    assert _src_refs(rec) == [shown.public_ref]
    assert rec["frequencies"]["source_freq_calculation_refs"] == []
    assert hidden.public_ref not in str(rec["source_calculations"]) + str(rec["frequencies"])


def test_exploratory_still_lists_every_linked_calculation(client, db_session):
    """Kills: filtering regardless of profile."""
    _, sm, shown, hidden = _world(db_session)
    rec = _record(client, sm, "exploratory")
    assert sorted(_src_refs(rec)) == sorted([shown.public_ref, hidden.public_ref])
    assert rec["frequencies"]["source_freq_calculation_refs"] == [hidden.public_ref]


def test_exploratory_still_lists_a_rejected_calculation(client, db_session):
    """Kills: applying the default rejected/deprecated hiding (or any filter) outside a review floor."""
    _, sm, shown, hidden = _world(db_session)
    set_review(db_session, record_type=SubmissionRecordType.calculation, record_id=hidden.id, status=RecordReviewStatus.rejected)
    rec = _record(client, sm, "exploratory")
    by_ref = {c["calculation_ref"]: c["review"]["status"] for c in rec["source_calculations"]}
    assert by_ref == {shown.public_ref: "approved", hidden.public_ref: "rejected"}
    assert rec["frequencies"]["source_freq_calculation_refs"] == [hidden.public_ref]


def test_default_profile_output_is_unchanged(client, db_session):
    """Kills: the no-profile default being treated as curated (default must equal exploratory byte for byte)."""
    _, sm, *_ = _world(db_session)
    url = URL + sm.public_ref
    default = client.get(url, params={"include": "source_calculations,frequencies"}).json()["record"]
    explicit = client.get(url, params={"include": "source_calculations,frequencies", "profile": "exploratory"}).json()["record"]
    assert default == explicit
    assert len(default["source_calculations"]) == 2


def test_record_level_facts_do_not_vary_by_profile(client, db_session):
    """Kills: filtering the counts/flags/levels (which would make trust vary by profile). Pins the scope decision."""
    _, sm, *_ = _world(db_session)

    def facts(profile):
        rec = client.get(URL + sm.public_ref, params={"profile": profile}).json()["record"]
        return rec["evidence_summary"], rec["available_sections"], rec["levels"]

    assert facts("curated") == facts("exploratory")
    evidence, sections, _ = facts("curated")
    assert evidence["source_calculation_count"] == 2
    assert sections["has_frequencies"] is True


def test_approved_calculations_stay_visible_under_curated(client, db_session):
    """Kills: dropping every calculation under curated."""
    entry = make_species_entry(db_session, make_species(db_session, inchi_key=next_inchi_key("CSR2")))
    sm2 = make_statmech(db_session, species_entry=entry)
    calc = make_calculation(db_session, type=CalculationType.freq, species_entry_id=entry.id)
    attach_statmech_source_calculation(db_session, statmech=sm2, calculation=calc, role=R.freq)
    set_review(db_session, record_type=SubmissionRecordType.calculation, record_id=calc.id, status=RecordReviewStatus.approved)
    set_review(db_session, record_type=SubmissionRecordType.statmech, record_id=sm2.id, status=RecordReviewStatus.approved)
    rec = _record(client, sm2, "curated")
    assert _src_refs(rec) == [calc.public_ref]
    assert rec["frequencies"]["source_freq_calculation_refs"] == [calc.public_ref]


def test_species_statmech_read_is_filtered_too(client, db_session):
    """Kills: filtering only in the detail route instead of the shared record builder."""
    entry, sm, shown, hidden = _world(db_session)
    r = client.get(
        f"/api/v1/scientific/species-entries/{entry.public_ref}/statmech",
        params={"include": "source_calculations,frequencies", "profile": "curated"},
    )
    assert r.status_code == 200
    assert r.json()["records"], "the approved statmech must be returned"
    text = str(r.json()["records"])
    assert shown.public_ref in text  # non-vacuous: the blocks were rendered, only the hidden ref is absent
    assert hidden.public_ref not in text


def test_search_read_is_filtered_too(client, db_session):
    """Kills: filtering only the detail/species routes."""
    _, sm, shown, hidden = _world(db_session)
    r = client.post(
        "/api/v1/scientific/statmech/search?profile=curated",
        json={"statmech_ref": sm.public_ref, "include": ["source_calculations", "frequencies"]},
    )
    assert r.status_code == 200
    assert r.json()["records"], "the approved statmech must be returned"
    text = str(r.json()["records"])
    assert shown.public_ref in text
    assert hidden.public_ref not in text


def _torsion_world(db_session, *, scan_status):
    entry = make_species_entry(db_session, make_species(db_session, inchi_key=next_inchi_key("CSRT")))
    sm = make_statmech(db_session, species_entry=entry)
    scan = make_calculation(db_session, type=CalculationType.scan, species_entry_id=entry.id)
    attach_statmech_torsion(db_session, statmech=sm, source_scan_calculation=scan)
    if scan_status is not None:
        set_review(db_session, record_type=SubmissionRecordType.calculation, record_id=scan.id, status=scan_status)
    set_review(db_session, record_type=SubmissionRecordType.statmech, record_id=sm.id, status=RecordReviewStatus.approved)
    return sm, scan


def _torsion_scan(client, sm, profile):
    r = client.get(URL + sm.public_ref, params={"include": "torsions", "profile": profile})
    assert r.status_code == 200
    (t,) = r.json()["record"]["torsions"]
    return t


def test_curated_does_not_name_an_unapproved_scan_calculation(client, db_session):
    """Kills: leaving ``source_scan_calculation_ref`` / ``_id`` unfiltered under a review floor."""
    sm, scan = _torsion_world(db_session, scan_status=None)
    assert client.get(f"/api/v1/scientific/calculations/{scan.public_ref}", params={"profile": "curated"}).status_code == 404
    t = _torsion_scan(client, sm, "curated")
    assert t["source_scan_calculation_ref"] is None
    assert t.get("source_scan_calculation_id") is None
    # The torsion itself is still described; only the hidden calculation is not named.
    assert t["torsion_index"] == 1
    assert _torsion_scan(client, sm, "exploratory")["source_scan_calculation_ref"] == scan.public_ref


def test_curated_names_an_approved_scan_calculation(client, db_session):
    """Kills: nulling every scan reference under curated."""
    sm, scan = _torsion_world(db_session, scan_status=RecordReviewStatus.approved)
    assert _torsion_scan(client, sm, "curated")["source_scan_calculation_ref"] == scan.public_ref


def test_no_calculation_statement_when_no_naming_block_is_requested(client, db_session):
    """Kills: running the profile filter on every record (it must run only for the blocks that name a calculation)."""
    from sqlalchemy import event

    _, sm, *_ = _world(db_session)
    seen = []

    def count(conn, cursor, statement, *a):
        if "record_review" in statement and "calculation" in str(a):
            seen.append(statement)

    bind = db_session.get_bind()
    event.listen(bind, "before_cursor_execute", count)
    try:
        r = client.get(URL + sm.public_ref, params={"include": "review", "profile": "curated"})
    finally:
        event.remove(bind, "before_cursor_execute", count)
    assert r.status_code == 200
    assert seen == []


def test_curated_does_not_expose_an_unapproved_scan_calculation_id(client, db_session, allow_internal_ids):
    """Kills: nulling the ref but leaving ``source_scan_calculation_id`` under ``internal_ids``."""
    sm, scan = _torsion_world(db_session, scan_status=None)
    r = client.get(URL + sm.public_ref, params={"include": "torsions,internal_ids", "profile": "curated"})
    (t,) = r.json()["record"]["torsions"]
    assert "source_scan_calculation_id" in t  # non-vacuous: the id field is present in this mode
    assert t["source_scan_calculation_id"] is None
    r = client.get(URL + sm.public_ref, params={"include": "torsions,internal_ids", "profile": "exploratory"})
    assert r.json()["record"]["torsions"][0]["source_scan_calculation_id"] == scan.id
