"""Tests for the admin-only submission machine-review inspection endpoint.

``GET /api/v1/admin/submissions/{submission_id}/machine-review-inspection`` is a
private/debugging surface: it projects a submission's existing
``llm_precheck_recorded`` audit events onto the records linked to that
submission, reusing the private machine-review stack. It is **admin-only** (the
stricter of the two existing gates) and must never imply that machine review is
public scientific trust — it does not touch the public ``TrustFragment`` or the
scientific read routes, and it mutates nothing.

These follow the existing admin-route testing pattern: the ``client`` fixture's
default actor is role=user (so it exercises the 403 path), ``login_as`` swaps
roles, and a fresh app without the auth override exercises the anonymous 401
path (mirroring ``test_api_legacy_route_auth.py``).
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.app import create_app
from app.api.deps import get_db, get_write_db
from app.db.models.common import (
    SubmissionActorKind,
    SubmissionAuditEventKind,
    SubmissionKind,
    SubmissionRecordType,
)
from app.db.models.machine_review_curator_task import MachineReviewCuratorTask
from app.db.models.submission import Submission, SubmissionAuditEvent
from app.services.machine_review import (
    MachineReviewCategory,
    MachineReviewSeverity,
    MachineReviewStatus,
)
from app.services.machine_review.schemas import (
    MACHINE_REVIEW_FORMAT,
    MachineReviewProviderFinding,
    MachineReviewProviderResult,
)
from app.services.submission import (
    create_submission,
    link_record,
    record_machine_review_audit_event,
)
from app.services.trust import build_trust_fragment
from app.services.trust.models import (
    EvidenceBadge,
    EvidenceEvaluation,
    EvidenceOutcome,
)
from tests.api import _machine_review_seed as seed
from tests.services.scientific_read._factories import (
    make_applied_energy_correction,
    make_energy_correction_scheme,
    make_species,
    make_species_entry,
)

_BASE = "/api/v1/admin/submissions"


def _url(submission_id: int) -> str:
    return f"{_BASE}/{submission_id}/machine-review-inspection"


# --------------------------------------------------------------------------- #
# Seeding helpers
# --------------------------------------------------------------------------- #


def _new_submission(db_session: Session, user_id: int) -> Submission:
    submission = create_submission(
        db_session,
        created_by=user_id,
        submission_kind=SubmissionKind.thermo,
        title="Admin MR inspection",
        summary="compact",
    )
    db_session.flush()
    return submission


def _record_finding(
    *,
    record_id: int | None,
    record_type: str | None = "calculation",
    severity: MachineReviewSeverity = MachineReviewSeverity.warning,
) -> MachineReviewProviderFinding:
    return MachineReviewProviderFinding(
        severity=severity,
        category=MachineReviewCategory.provenance,
        record_type=record_type,
        record_id=record_id,
        message="Missing source artifact summary.",
        evidence_keys=("missing_checks.source_artifact_present",),
    )


def _record_mr_event(
    db_session: Session,
    submission: Submission,
    *,
    findings: tuple[MachineReviewProviderFinding, ...] = (),
    status: MachineReviewStatus = MachineReviewStatus.machine_screened_warning,
) -> SubmissionAuditEvent:
    """Append a realistic machine-review (llm_precheck_recorded / llm) event."""
    result = MachineReviewProviderResult(
        format=MACHINE_REVIEW_FORMAT,
        status=status,
        summary="advisory",
        findings=findings,
        model="fake_test/simple-v1",
        used_rag=False,
    )
    event = record_machine_review_audit_event(
        db_session,
        submission=submission,
        result=result,
        provider="FakeLLMPrecheckProvider",
    )
    db_session.flush()
    return event


# --------------------------------------------------------------------------- #
# Access control
# --------------------------------------------------------------------------- #


@pytest.fixture
def anon_client(db_session: Session):
    """A client with DB overrides but no auth override -> real auth runs."""
    app = create_app()
    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[get_write_db] = lambda: db_session
    with TestClient(app) as c:
        yield c


def test_admin_submission_machine_review_inspection_requires_auth(
    anon_client, db_session, _api_test_user
):
    """Anonymous callers are rejected with 401 before any inspection runs."""
    submission = _new_submission(db_session, _api_test_user)

    resp = anon_client.get(_url(submission.id))

    assert resp.status_code == 401, resp.text


def test_admin_submission_machine_review_inspection_requires_admin(
    client, db_session, login_as, _api_test_user, _api_curator_user
):
    """Normal users and curators are forbidden; the gate is admin-only."""
    submission = _new_submission(db_session, _api_test_user)

    # Default actor is role=user.
    assert client.get(_url(submission.id)).status_code == 403

    # Curators are also forbidden — this debugging surface is admin-only.
    login_as(_api_curator_user)
    assert client.get(_url(submission.id)).status_code == 403


# --------------------------------------------------------------------------- #
# Lookup / empty
# --------------------------------------------------------------------------- #


def test_admin_submission_machine_review_inspection_404_for_missing_submission(
    client, login_as, _api_admin_user
):
    """A missing submission yields 404, with no internal id leaked."""
    login_as(_api_admin_user)

    resp = client.get(_url(999_999))

    assert resp.status_code == 404, resp.text


def test_admin_submission_machine_review_inspection_empty_when_no_machine_review_events(
    client, db_session, login_as, _api_admin_user
):
    """A submission with no machine-review events returns an empty inspection."""
    submission = _new_submission(db_session, _api_admin_user)
    login_as(_api_admin_user)

    resp = client.get(_url(submission.id))

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body == {
        "submission_id": submission.id,
        "record_summaries": [],
        "unmapped_findings_count": 0,
        "mapping_warnings": [],
        "parse_warnings": [],
        "source_audit_event_ids": [],
    }


def test_admin_submission_machine_review_inspection_ignores_non_machine_review_events(
    client, db_session, login_as, _api_admin_user
):
    """The submission_created (user) event is present but is not a source."""
    submission = _new_submission(db_session, _api_admin_user)
    # Sanity: the submission already carries a non-MR audit event.
    assert any(
        e.event_kind is SubmissionAuditEventKind.submission_created
        and e.actor_kind is SubmissionActorKind.user
        for e in submission.audit_events
    )
    login_as(_api_admin_user)

    body = client.get(_url(submission.id)).json()

    assert body["record_summaries"] == []
    assert body["source_audit_event_ids"] == []


# --------------------------------------------------------------------------- #
# Mapping behavior
# --------------------------------------------------------------------------- #


def test_admin_submission_machine_review_inspection_maps_record_finding(
    client, db_session, login_as, _api_admin_user
):
    """A finding naming a linked record becomes that record's latest summary."""
    submission = _new_submission(db_session, _api_admin_user)
    link_record(
        db_session,
        submission=submission,
        record_type=SubmissionRecordType.calculation,
        record_id=9001,
        role="primary",
    )
    event = _record_mr_event(
        db_session,
        submission,
        findings=(_record_finding(record_id=9001, severity=MachineReviewSeverity.warning),),
    )
    login_as(_api_admin_user)

    body = client.get(_url(submission.id)).json()

    assert len(body["record_summaries"]) == 1
    record = body["record_summaries"][0]
    assert record["record_type"] == "calculation"
    assert record["record_id"] == 9001
    # 9001 is a fabricated id with no calculation row behind it, so there is
    # nothing to name. `null`, not the row id dressed up as a handle.
    assert record["record_public_ref"] is None
    assert "record_ref" not in record
    assert record["latest_summary"]["status"] == "machine_screened_warning"
    assert record["all_record_reviews_count"] == 1
    assert body["source_audit_event_ids"] == [event.id]
    assert body["unmapped_findings_count"] == 0


def test_admin_submission_machine_review_inspection_does_not_fan_out_submission_scoped_finding(
    client, db_session, login_as, _api_admin_user
):
    """A submission-scoped finding never becomes any linked record's summary."""
    submission = _new_submission(db_session, _api_admin_user)
    for rid in (9001, 9002):
        link_record(
            db_session,
            submission=submission,
            record_type=SubmissionRecordType.calculation,
            record_id=rid,
            role=f"r{rid}",
        )
    _record_mr_event(
        db_session,
        submission,
        status=MachineReviewStatus.machine_screened_needs_attention,
        findings=(_record_finding(record_id=None, record_type=None,
                                  severity=MachineReviewSeverity.critical),),
    )
    login_as(_api_admin_user)

    body = client.get(_url(submission.id)).json()

    # No fan-out onto either linked record; the finding is a diagnostic only.
    assert body["record_summaries"] == []
    assert body["unmapped_findings_count"] == 1


def test_admin_submission_machine_review_inspection_preserves_mapping_warnings(
    client, db_session, login_as, _api_admin_user
):
    """A finding naming an unlinked record is kept as a mapping warning."""
    submission = _new_submission(db_session, _api_admin_user)
    link_record(
        db_session,
        submission=submission,
        record_type=SubmissionRecordType.calculation,
        record_id=9001,
        role="primary",
    )
    _record_mr_event(
        db_session,
        submission,
        findings=(_record_finding(record_id=7777),),  # 7777 is not linked
    )
    login_as(_api_admin_user)

    body = client.get(_url(submission.id)).json()

    assert body["record_summaries"] == []
    assert body["unmapped_findings_count"] == 1
    assert len(body["mapping_warnings"]) == 1
    assert "not linked" in body["mapping_warnings"][0]


def test_admin_submission_machine_review_inspection_preserves_parse_warnings(
    client, db_session, login_as, _api_admin_user
):
    """A malformed machine-review event degrades to a preserved parse warning."""
    submission = _new_submission(db_session, _api_admin_user)
    # A raw, malformed llm_precheck_recorded event (status not in the enum).
    db_session.add(
        SubmissionAuditEvent(
            submission_id=submission.id,
            actor_kind=SubmissionActorKind.llm,
            event_kind=SubmissionAuditEventKind.llm_precheck_recorded,
            details_json={"format": MACHINE_REVIEW_FORMAT.model_dump(), "status": "not-a-real-status", "findings": []},
        )
    )
    db_session.flush()
    login_as(_api_admin_user)

    body = client.get(_url(submission.id)).json()

    assert body["record_summaries"] == []
    assert len(body["parse_warnings"]) == 1


# --------------------------------------------------------------------------- #
# Boundary: no mutation, public trust shape unchanged
# --------------------------------------------------------------------------- #


def test_admin_submission_machine_review_inspection_does_not_mutate_submission(
    client, db_session, login_as, _api_admin_user
):
    """Inspecting a submission leaves its lifecycle/moderation fields intact."""
    submission = _new_submission(db_session, _api_admin_user)
    link_record(
        db_session,
        submission=submission,
        record_type=SubmissionRecordType.calculation,
        record_id=9001,
        role="primary",
    )
    _record_mr_event(
        db_session, submission, findings=(_record_finding(record_id=9001),)
    )
    before = (
        submission.status,
        submission.approved_at,
        submission.approved_by,
        submission.rejected_at,
        submission.rejected_by,
        submission.rejection_reason,
    )
    login_as(_api_admin_user)

    assert client.get(_url(submission.id)).status_code == 200

    db_session.refresh(submission)
    after = (
        submission.status,
        submission.approved_at,
        submission.approved_by,
        submission.rejected_at,
        submission.rejected_by,
        submission.rejection_reason,
    )
    assert after == before


def test_admin_submission_machine_review_inspection_does_not_change_public_trust_shape(
    client, db_session, login_as, _api_admin_user
):
    """The public TrustFragment still has the frozen precheck shape and no machine_review."""
    submission = _new_submission(db_session, _api_admin_user)
    link_record(
        db_session,
        submission=submission,
        record_type=SubmissionRecordType.calculation,
        record_id=9001,
        role="primary",
    )
    _record_mr_event(
        db_session, submission, findings=(_record_finding(record_id=9001),)
    )
    login_as(_api_admin_user)

    # Exercising the admin endpoint must not perturb the public trust shape.
    assert client.get(_url(submission.id)).status_code == 200

    evaluation = EvidenceEvaluation(
        record_type="calculation",
        record_id=9001,
        rubric="computed_calculation",
        rubric_version=1,
        label=EvidenceBadge.partial,
        checks={
            "opt_converged": EvidenceOutcome.passed,
            "source_artifact_present": EvidenceOutcome.missing,
        },
        passed_count=1,
        possible_count=2,
        evidence_completeness=0.5,
    )
    dumped = build_trust_fragment(evaluation).model_dump(mode="json")

    assert dumped["llm_precheck"] == {
        "enabled": False,
        "label": "not_run",
        "summary": None,
    }
    assert "machine_review" not in dumped
    assert set(dumped) == {
        "review_status",
        "trust_status",
        "evidence",
        "llm_precheck",
        "is_certified",
    }


# --------------------------------------------------------------------------- #
# record_public_ref -- the identifier a maintainer can actually follow
# --------------------------------------------------------------------------- #
#
# The two identifiers on this surface answer different questions. `record_ref`
# means the *public* ref everywhere else in this API, so the inspection page
# must show it, not a database row id, beneath that heading.


def test_inspection_resolves_the_public_ref_of_a_real_record(
    client, db_session, login_as, _api_admin_user
):
    """A finding on a record that exists is named by the ref the archive uses."""
    species = make_species(db_session)
    submission = _new_submission(db_session, _api_admin_user)
    link_record(
        db_session,
        submission=submission,
        record_type=SubmissionRecordType.species,
        record_id=species.id,
        role="primary",
    )
    _record_mr_event(
        db_session,
        submission,
        findings=(
            _record_finding(
                record_id=species.id,
                record_type="species",
                severity=MachineReviewSeverity.warning,
            ),
        ),
    )
    login_as(_api_admin_user)

    record = client.get(_url(submission.id)).json()["record_summaries"][0]

    # Equality with the row's own ref: returning some other species' ref would
    # satisfy any shape check perfectly.
    assert record["record_public_ref"] == species.public_ref
    # And it is a handle, not the row id restated -- the whole point.
    assert record["record_public_ref"] != str(record["record_id"])


def test_inspection_never_publishes_the_old_name(
    client, db_session, login_as, _api_admin_user
):
    """`record_ref` is gone from this surface, and nothing stands in for it.

    Leaving the row-id key in place alongside the public ref would keep the page's
    ambiguity alive. Renaming it would be little better: on this surface
    the matching key is always ``str(record_id)`` -- ``audit_adapter`` keys
    every link that way and drops links with no id -- so a second field would
    restate ``record_id`` under another name, on a page whose whole purpose is
    to stop presenting row ids as handles. The exact field set is asserted so
    that reintroducing one fails here.
    """
    species = make_species(db_session)
    submission = _new_submission(db_session, _api_admin_user)
    link_record(
        db_session,
        submission=submission,
        record_type=SubmissionRecordType.species,
        record_id=species.id,
        role="primary",
    )
    _record_mr_event(
        db_session,
        submission,
        findings=(
            _record_finding(
                record_id=species.id,
                record_type="species",
                severity=MachineReviewSeverity.warning,
            ),
        ),
    )
    login_as(_api_admin_user)

    record = client.get(_url(submission.id)).json()["record_summaries"][0]

    assert "record_ref" not in record
    assert "record_match_key" not in record
    assert set(record) == {
        "record_type",
        "record_public_ref",
        "record_id",
        "latest_summary",
        "all_record_reviews_count",
        "all_record_reviews",
        "container_type",
        "container_ref",
    }


# --------------------------------------------------------------------------- #
# The reviewer's words: every review, every finding, every run
# --------------------------------------------------------------------------- #

_SPECIES = SubmissionRecordType.species


def _species_submission(db_session, user_id, *, n: int = 1):
    species = [make_species(db_session) for _ in range(n)]
    submission = seed.new_submission(db_session, user_id)
    for s in species:
        seed.link(db_session, submission, _SPECIES, s.id)
    return submission, species


def test_inspection_returns_every_review_with_findings(
    client, db_session, login_as, _api_admin_user, monkeypatch
):
    """Two runs on one record: both reviews come back, with the reviewer's words."""
    submission, (species,) = _species_submission(db_session, _api_admin_user)
    login_as(_api_admin_user)
    first = seed.finding(
        "species", species.id, message="First pass concern.", evidence_keys=("a.key",),
        recommended_action="Do the first thing.",
    )
    second = seed.finding(
        "species", species.id, severity=MachineReviewSeverity.critical,
        category=MachineReviewCategory.geometry, message="Second pass concern.",
        evidence_keys=("b.key", "c.key"), recommended_action="Do the second thing.",
    )
    seed.run_review(client, monkeypatch, submission.id, seed.result((first,)))
    seed.run_review(client, monkeypatch, submission.id, seed.result((second,)))

    body = client.get(_url(submission.id)).json()

    (record,) = body["record_summaries"]
    reviews = record["all_record_reviews"]
    assert len(reviews) == 2
    assert [r["is_latest"] for r in reviews] == [True, False]
    newest, oldest = reviews
    assert newest["audit_event_id"] > oldest["audit_event_id"]
    assert newest["model"] == "fake_test/machine-review"
    assert newest["provider"] == "FakeMachineReviewProvider"
    assert newest["curator_priority"] == "medium"
    (f_new,) = newest["findings"]
    assert f_new["severity"] == "critical"
    assert f_new["category"] == "geometry"
    assert f_new["message"] == "Second pass concern."
    assert f_new["evidence_keys"] == ["b.key", "c.key"]
    assert f_new["recommended_action"] == "Do the second thing."
    (f_old,) = oldest["findings"]
    assert f_old["message"] == "First pass concern."
    # Pre-existing fields are unchanged.
    assert record["all_record_reviews_count"] == 2
    assert record["latest_summary"]["findings_count"] == 1
    assert record["latest_summary"]["highest_severity"] == "critical"
    assert body["unmapped_findings_count"] == 0


def test_inspection_returns_runs_with_reviewer_summary(
    client, db_session, login_as, _api_admin_user, monkeypatch
):
    submission, (species,) = _species_submission(db_session, _api_admin_user)
    login_as(_api_admin_user)
    ok = seed.result(
        (seed.finding("species", species.id),), summary="The reviewer's own summary."
    )
    seed.run_review(client, monkeypatch, submission.id, ok)

    class _Raising:
        def review_submission(self, context):
            raise RuntimeError("upstream endpoint unreachable")

    seed.use_provider(monkeypatch, _Raising())
    assert client.post(f"{seed.RUN_URL}/{submission.id}").status_code == 200

    runs = client.get(_url(submission.id)).json()["runs"]

    assert len(runs) == 2
    failed, good = runs
    assert failed["audit_event_id"] > good["audit_event_id"]
    assert failed["status"] == "machine_review_failed"
    assert failed["parsed"] is True
    assert "RuntimeError" in failed["summary"]
    assert failed["findings_count"] == 0
    assert good["summary"] == "The reviewer's own summary."
    assert good["status"] == "machine_screened_warning"
    assert good["curator_priority"] == "medium"
    assert good["model"] == "fake_test/machine-review"
    assert good["provider"] == "FakeMachineReviewProvider"
    assert good["findings_count"] == 1
    assert good["mapped_findings_count"] == 1
    assert good["unmapped_findings_count"] == 0
    assert good["highest_severity"] == "warning"


def test_inspection_lists_unmapped_findings_with_reason(
    client, db_session, login_as, _api_admin_user, monkeypatch
):
    submission, (s1, s2) = _species_submission(db_session, _api_admin_user, n=2)
    login_as(_api_admin_user)
    findings = (
        seed.finding(None, None, message="Whole-submission concern."),
        seed.finding("not_a_type", None, record_ref="42", message="Unknown type concern."),
        seed.finding("species", None, message="Which species?"),  # two linked -> ambiguous
        seed.finding("species", s1.id, message="Mapped concern."),
    )
    seed.run_review(client, monkeypatch, submission.id, seed.result(findings))

    body = client.get(_url(submission.id)).json()

    assert body["unmapped_findings_count"] == 3
    unmapped = body["unmapped_findings"]
    assert len(unmapped) == body["unmapped_findings_count"]
    by_message = {u["message"]: u for u in unmapped}
    assert by_message["Whole-submission concern."]["reason"] == "submission_scoped"
    assert by_message["Whole-submission concern."]["cited_record_type"] is None
    assert by_message["Unknown type concern."]["reason"] == "unknown_record_type"
    assert by_message["Unknown type concern."]["cited_record_type"] == "not_a_type"
    assert by_message["Unknown type concern."]["cited_record_ref"] == "42"
    assert by_message["Which species?"]["reason"] == "ambiguous_record_type"
    run_event = body["runs"][0]["audit_event_id"]
    assert {u["audit_event_id"] for u in unmapped} == {run_event}
    # Anti-fan-out: none of them sits under a record.
    (record,) = body["record_summaries"]
    messages = {f["message"] for r in record["all_record_reviews"] for f in r["findings"]}
    assert messages == {"Mapped concern."}
    assert body["runs"][0]["mapped_findings_count"] == 1
    assert body["runs"][0]["unmapped_findings_count"] == 3


def test_unparseable_event_appears_as_unparsed_run(
    client, db_session, login_as, _api_admin_user
):
    submission = seed.new_submission(db_session, _api_admin_user)
    db_session.add(
        SubmissionAuditEvent(
            submission_id=submission.id,
            actor_kind=SubmissionActorKind.llm,
            event_kind=SubmissionAuditEventKind.llm_precheck_recorded,
            details_json={"format": MACHINE_REVIEW_FORMAT.model_dump(), "status": "not-a-real-status", "findings": []},
        )
    )
    db_session.flush()
    login_as(_api_admin_user)

    runs = client.get(_url(submission.id)).json()["runs"]

    assert len(runs) == 1
    assert runs[0]["parsed"] is False
    assert runs[0]["status"] is None
    assert len(runs[0]["parse_warnings"]) == 1


def test_inspection_findings_link_to_their_curator_tasks(
    client, db_session, login_as, _api_admin_user, monkeypatch
):
    submission, (species,) = _species_submission(db_session, _api_admin_user)
    login_as(_api_admin_user)
    warn = seed.finding("species", species.id, message="Warn concern.")
    info = seed.finding("species", species.id, severity=MachineReviewSeverity.info, message="Info note.")
    seed.run_review(client, monkeypatch, submission.id, seed.result((warn, info)))

    def _ids():
        record = client.get(_url(submission.id)).json()["record_summaries"][0]
        return {
            f["message"]: f["curator_task_id"]
            for f in record["all_record_reviews"][0]["findings"]
        }

    assert _ids() == {"Warn concern.": None, "Info note.": None}  # before the build

    built = seed.build_tasks(client, submission.id)

    ids = _ids()
    assert ids["Info note."] is None
    assert ids["Warn concern."] == built["task_ids"][0]
    assert len(built["task_ids"]) == 1


def test_applied_energy_correction_record_names_its_container(
    client, db_session, login_as, _api_admin_user, monkeypatch
):
    entry = make_species_entry(db_session, make_species(db_session))
    correction = make_applied_energy_correction(
        db_session,
        target_species_entry=entry,
        scheme=make_energy_correction_scheme(db_session, name="inspection_container"),
    )
    submission = seed.new_submission(db_session, _api_admin_user)
    seed.link(db_session, submission, SubmissionRecordType.applied_energy_correction, correction.id)
    login_as(_api_admin_user)
    seed.run_review(
        client, monkeypatch, submission.id,
        seed.result((seed.finding("applied_energy_correction", correction.id),)),
    )

    (record,) = client.get(_url(submission.id)).json()["record_summaries"]

    assert record["record_public_ref"] is None
    assert record["container_type"] == "species_entry"
    assert record["container_ref"] == entry.public_ref


def test_inspection_reads_write_nothing(
    client, db_session, login_as, _api_admin_user, monkeypatch
):
    submission, (species,) = _species_submission(db_session, _api_admin_user)
    login_as(_api_admin_user)
    seed.run_review(client, monkeypatch, submission.id, seed.result((seed.finding("species", species.id),)))
    status_before = submission.status
    events_before = db_session.scalar(
        select(func.count()).select_from(SubmissionAuditEvent).where(SubmissionAuditEvent.submission_id == submission.id)
    )

    assert client.get(_url(submission.id)).status_code == 200

    assert submission.status is status_before
    assert db_session.scalar(
        select(func.count()).select_from(SubmissionAuditEvent).where(SubmissionAuditEvent.submission_id == submission.id)
    ) == events_before
    assert db_session.scalar(select(func.count()).select_from(MachineReviewCuratorTask)) == 0
