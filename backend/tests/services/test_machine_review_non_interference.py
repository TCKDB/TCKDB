"""Non-interference tests for the advisory machine-review / precheck path.

This slice proves the *safety contract* that must hold before any public
read-API exposure or machine-review database schema work: running the
advisory machine-review path (the fake/failing precheck provider) must not
mutate any deterministic, scientific, or moderation state.

The deterministic trust/evidence evaluator (``app.services.trust``) is a pure
function over ORM rows — it has no timestamps or runtime-generated fields — so
its serialized :class:`EvidenceEvaluation` is the oracle for the strongest
invariant here: **byte-identical evaluator output before and after a
machine-review run.**

The advisory executor in this slice is the fake AI-Review-Assistant precheck
provider routed through :func:`run_llm_precheck_for_submission`; the
machine-review module is still contracts-only, so its
:func:`derive_machine_review_status` is exercised only to pin the advisory
``failed -> machine_review_failed`` / ``not_performed -> not_run`` semantics.

No real LLM provider, no RAG, no migration, and no public ``trust.machine_review``
exposure is involved. See ``backend/docs/specs/provisional_machine_review.md``.
"""

from __future__ import annotations

import hashlib
import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.config import Settings
from app.db.models.calculation import (
    Calculation,
    CalculationGeometryValidation,
    CalculationInputGeometry,
    CalculationOptResult,
    CalculationOutputGeometry,
)
from app.db.models.common import (
    CalculationGeometryRole,
    CalculationQuality,
    CalculationType,
    MoleculeKind,
    StereoKind,
    SubmissionActorKind,
    SubmissionAuditEventKind,
    SubmissionKind,
    SubmissionRecordType,
    SubmissionStatus,
    ValidationStatus,
)
from app.db.models.geometry import Geometry
from app.db.models.level_of_theory import LevelOfTheory
from app.db.models.software import Software, SoftwareRelease
from app.db.models.species import Species, SpeciesEntry
from app.db.models.submission import SubmissionAuditEvent
from app.services.llm_precheck.providers import FakeLLMPrecheckProvider
from app.services.llm_precheck.schemas import (
    LLMFinding,
    LLMFindingCategory,
    LLMFindingSeverity,
    LLMPrecheckLabel,
    LLMPrecheckResult,
)
from app.services.llm_precheck.service import run_llm_precheck_for_submission
from app.services.machine_review import (
    MachineReviewOutcome,
    MachineReviewStatus,
    derive_machine_review_status,
)
from app.services.submission import create_submission, link_record
from app.services.trust import build_trust_fragment, evaluate_computed_calculation

# ---------------------------------------------------------------------------
# Minimal ORM record with a deterministic trust rubric (computed_calculation_v1)
# ---------------------------------------------------------------------------

_UNIQUE_COUNTER = iter(range(100_000))


def _next_inchi_key() -> str:
    """Return a synthetic 27-char InChI key unique within the test session."""
    return f"MR-NONINTERF-INCHI-{next(_UNIQUE_COUNTER):04d}A"[:27].ljust(27, "X")


def _next_hash(prefix: str) -> str:
    """Return a synthetic 64-char hash unique within the test session."""
    return hashlib.sha256(f"{prefix}-{next(_UNIQUE_COUNTER)}".encode()).hexdigest()


def _make_opt_calc_with_rubric(db_session: Session) -> Calculation:
    """Build a partially-provisioned opt calc that maps to ``computed_calculation_v1``.

    Intentionally omits some provenance (artifact, parameters) so the
    evaluation has both passed and missing checks — a non-trivial, mid-band
    evaluation makes the byte-identical assertion meaningful rather than a
    degenerate all-pass/all-fail snapshot.
    """
    species = Species(
        kind=MoleculeKind.molecule,
        # Unique per call: species identity is (smiles, charge, multiplicity)
        # (DR-0031), so a fixed "CCO" collides with cross-test species.
        smiles=_next_inchi_key(),
        inchi_key=_next_inchi_key(),
        charge=0,
        multiplicity=1,
        stereo_kind=StereoKind.achiral,
    )
    db_session.add(species)
    db_session.flush()

    entry = SpeciesEntry(species_id=species.id, unmapped_smiles=species.smiles)
    db_session.add(entry)
    db_session.flush()

    lot = LevelOfTheory(
        method="wb97xd",
        basis="def2tzvp",
        lot_hash=_next_hash("mr-noninterf-lot"),
    )
    software = Software(name=f"mr-noninterf-sw-{next(_UNIQUE_COUNTER)}")
    db_session.add_all([lot, software])
    db_session.flush()
    release = SoftwareRelease(software_id=software.id, version="1.0")
    db_session.add(release)
    db_session.flush()

    calc = Calculation(
        type=CalculationType.opt,
        quality=CalculationQuality.raw,
        species_entry_id=entry.id,
        lot_id=lot.id,
        software_release_id=release.id,
    )
    db_session.add(calc)
    db_session.flush()

    input_geom = Geometry(natoms=3, geom_hash=_next_hash("mr-in"), xyz_text="dummy")
    output_geom = Geometry(natoms=3, geom_hash=_next_hash("mr-out"), xyz_text="dummy")
    db_session.add_all([input_geom, output_geom])
    db_session.flush()
    db_session.add_all(
        [
            CalculationInputGeometry(
                calculation_id=calc.id, geometry_id=input_geom.id, input_order=1
            ),
            CalculationOutputGeometry(
                calculation_id=calc.id,
                geometry_id=output_geom.id,
                output_order=1,
                role=CalculationGeometryRole.final,
            ),
            CalculationOptResult(
                calculation_id=calc.id,
                final_energy_hartree=-100.0,
                converged=True,
            ),
            CalculationGeometryValidation(
                calculation_id=calc.id,
                validation_status=ValidationStatus.passed,
                species_smiles="CCO",
                is_isomorphic=True,
            ),
        ]
    )
    db_session.flush()
    db_session.refresh(calc)
    return calc


def _seed_submission(db_session: Session, user_id: int):
    """Create a pending submission for the advisory machine-review path."""
    submission = create_submission(
        db_session,
        created_by=user_id,
        submission_kind=SubmissionKind.thermo,
        title="Machine-review non-interference",
        summary="Compact submission summary",
    )
    db_session.flush()
    return submission


def _precheck_events(db_session: Session, submission_id: int) -> list[SubmissionAuditEvent]:
    """Return the advisory precheck audit events for a submission, in order."""
    return list(
        db_session.scalars(
            select(SubmissionAuditEvent)
            .where(
                SubmissionAuditEvent.submission_id == submission_id,
                SubmissionAuditEvent.event_kind
                == SubmissionAuditEventKind.llm_precheck_recorded,
            )
            .order_by(SubmissionAuditEvent.id.asc())
        )
    )


# The full set of deterministic trust/evidence fields the spec requires to be
# unchanged by an advisory machine-review run. ``EvidenceEvaluation`` carries
# no timestamps or runtime-generated fields, so nothing is excluded.
_EVIDENCE_FIELDS = (
    "rubric",
    "rubric_version",
    "label",
    "passed_checks",
    "missing_checks",
    "warning_checks",
    "not_applicable_checks",
    "passed_count",
    "possible_count",
    "evidence_completeness",
    "hard_fail_reason",
    "is_certified",
)


def _deterministic_snapshot(db_session: Session, calculation_id: int) -> str:
    """Return a stable JSON snapshot of the deterministic trust state.

    Captures the full :class:`EvidenceEvaluation` (minus the per-check
    ``check_results`` detail, which is already summarized in the bucketed
    name tuples) plus the composed public ``trust_status``/``is_certified``.
    Serialized with ``sort_keys`` so the comparison is byte-for-byte.
    """
    evaluation = evaluate_computed_calculation(db_session, calculation_id)
    fragment = build_trust_fragment(evaluation)
    payload = {
        "evidence": evaluation.model_dump(mode="json", exclude={"check_results"}),
        "trust_status": fragment.trust_status,
        "is_certified": fragment.is_certified,
    }
    return json.dumps(payload, sort_keys=True)


def _scientific_record_snapshot(db_session: Session, calculation_id: int) -> tuple:
    """Return a tuple of the scientific calc fields that must not be mutated."""
    db_session.expire_all()
    calc = db_session.get(Calculation, calculation_id)
    assert calc is not None
    opt = calc.opt_result
    gv = calc.geometry_validation
    return (
        calc.type,
        calc.quality,
        calc.species_entry_id,
        calc.lot_id,
        calc.software_release_id,
        opt.final_energy_hartree if opt is not None else None,
        opt.converged if opt is not None else None,
        gv.validation_status if gv is not None else None,
        gv.is_isomorphic if gv is not None else None,
    )


class _RaisingProvider:
    """Advisory provider stub that always fails to review."""

    def review_submission(self, context):
        """Raise a deterministic provider error (a reviewer failure, not a record failure)."""
        raise RuntimeError("provider unavailable")


# ---------------------------------------------------------------------------
# Submission status / moderation is never touched
# ---------------------------------------------------------------------------


def test_fake_machine_review_does_not_change_submission_status(db_session, _api_test_user):
    """A fake advisory run leaves submission lifecycle/moderation state intact."""
    submission = _seed_submission(db_session, _api_test_user)
    before = (
        submission.status,
        submission.approved_at,
        submission.approved_by,
        submission.rejected_at,
        submission.rejected_by,
        submission.rejection_reason,
    )

    result = run_llm_precheck_for_submission(
        db_session,
        submission.id,
        provider=FakeLLMPrecheckProvider(),
    )

    after = (
        submission.status,
        submission.approved_at,
        submission.approved_by,
        submission.rejected_at,
        submission.rejected_by,
        submission.rejection_reason,
    )
    assert result.label is LLMPrecheckLabel.warning  # no linked records -> advisory warning
    assert submission.status is SubmissionStatus.pending
    assert after == before
    # An advisory event may be written, but it is neutral: no status transition.
    events = _precheck_events(db_session, submission.id)
    assert len(events) == 1
    assert events[0].from_status is None
    assert events[0].to_status is None


# ---------------------------------------------------------------------------
# Scientific records are never mutated
# ---------------------------------------------------------------------------


def test_fake_machine_review_does_not_mutate_scientific_records(db_session, _api_test_user):
    """A fake advisory run does not write to the linked scientific record."""
    calc = _make_opt_calc_with_rubric(db_session)
    submission = _seed_submission(db_session, _api_test_user)
    link_record(
        db_session,
        submission=submission,
        record_type=SubmissionRecordType.calculation,
        record_id=calc.id,
        role="primary",
    )
    before = _scientific_record_snapshot(db_session, calc.id)

    run_llm_precheck_for_submission(
        db_session,
        submission.id,
        provider=FakeLLMPrecheckProvider(),
    )

    assert _scientific_record_snapshot(db_session, calc.id) == before


# ---------------------------------------------------------------------------
# The most important invariant: deterministic evidence is byte-identical
# ---------------------------------------------------------------------------


def test_fake_machine_review_does_not_change_deterministic_evidence(db_session, _api_test_user):
    """The deterministic trust evaluator output is byte-identical across a fake run."""
    calc = _make_opt_calc_with_rubric(db_session)
    submission = _seed_submission(db_session, _api_test_user)
    link_record(
        db_session,
        submission=submission,
        record_type=SubmissionRecordType.calculation,
        record_id=calc.id,
        role="primary",
    )

    before = _deterministic_snapshot(db_session, calc.id)

    result = run_llm_precheck_for_submission(
        db_session,
        submission.id,
        provider=FakeLLMPrecheckProvider(),
    )

    after = _deterministic_snapshot(db_session, calc.id)
    assert result.label is LLMPrecheckLabel.pass_  # one linked record inspected
    assert after == before, "machine-review run perturbed deterministic evidence"
    # Spot-check every required field individually so a regression names itself.
    before_obj = json.loads(before)["evidence"]
    after_obj = json.loads(after)["evidence"]
    for field in _EVIDENCE_FIELDS:
        assert after_obj[field] == before_obj[field], f"{field} changed"


# ---------------------------------------------------------------------------
# Provider failure is advisory, not a caller/upload failure
# ---------------------------------------------------------------------------


def test_failed_machine_review_does_not_fail_upload_or_mutate_status(db_session, _api_test_user):
    """A reviewer failure is an advisory result that never raises or mutates state."""
    calc = _make_opt_calc_with_rubric(db_session)
    submission = _seed_submission(db_session, _api_test_user)
    link_record(
        db_session,
        submission=submission,
        record_type=SubmissionRecordType.calculation,
        record_id=calc.id,
        role="primary",
    )
    evidence_before = _deterministic_snapshot(db_session, calc.id)
    status_before = submission.status

    # Does not raise — the provider failure is swallowed into an advisory result.
    result = run_llm_precheck_for_submission(
        db_session,
        submission.id,
        provider=_RaisingProvider(),
    )

    assert result.label is LLMPrecheckLabel.failed_to_review
    assert submission.status is status_before is SubmissionStatus.pending
    assert _deterministic_snapshot(db_session, calc.id) == evidence_before
    # The advisory failure is recorded only as a neutral audit event.
    events = _precheck_events(db_session, submission.id)
    assert len(events) == 1
    assert events[0].from_status is None
    assert events[0].to_status is None
    assert events[0].details_json["error_kind"] == "RuntimeError"
    # And the contract-layer derivation agrees a reviewer failure is its own axis.
    assert (
        derive_machine_review_status((), MachineReviewOutcome.failed)
        is MachineReviewStatus.machine_review_failed
    )


# ---------------------------------------------------------------------------
# "Not performed" writes no public trust state
# ---------------------------------------------------------------------------


def test_not_performed_machine_review_writes_no_public_trust_state(db_session, _api_test_user):
    """Off mode is a no-op: no audit event, no public trust mutation, no record state."""
    calc = _make_opt_calc_with_rubric(db_session)
    submission = _seed_submission(db_session, _api_test_user)
    evidence_before = _deterministic_snapshot(db_session, calc.id)
    settings = Settings(ai_review_assistant_mode="off")

    result = run_llm_precheck_for_submission(
        db_session,
        submission.id,
        settings_obj=settings,
    )

    assert result.label is LLMPrecheckLabel.not_run
    # No advisory event is persisted when the reviewer does not run.
    assert _precheck_events(db_session, submission.id) == []
    # Deterministic evidence is untouched...
    assert _deterministic_snapshot(db_session, calc.id) == evidence_before
    # ...and the public trust fragment still keeps llm_precheck disabled/not_run
    # with no record-level machine_review block exposed in this slice.
    fragment = build_trust_fragment(evaluate_computed_calculation(db_session, calc.id))
    dumped = fragment.model_dump(mode="json")
    assert dumped["llm_precheck"] == {
        "enabled": False,
        "label": "not_run",
        "summary": None,
    }
    assert "machine_review" not in dumped
    # The contract-layer derivation agrees "not performed" is the not_run axis.
    assert (
        derive_machine_review_status((), MachineReviewOutcome.not_performed)
        is MachineReviewStatus.not_run
    )


# ---------------------------------------------------------------------------
# A finding-bearing result is advisory only
# ---------------------------------------------------------------------------


def test_machine_review_result_is_advisory_only(db_session, _api_test_user):
    """A warning result with findings stays advisory — no moderation, no evidence change."""
    calc = _make_opt_calc_with_rubric(db_session)
    submission = _seed_submission(db_session, _api_test_user)
    link_record(
        db_session,
        submission=submission,
        record_type=SubmissionRecordType.calculation,
        record_id=calc.id,
        role="primary",
    )
    evidence_before = _deterministic_snapshot(db_session, calc.id)

    fixed = LLMPrecheckResult(
        label=LLMPrecheckLabel.warning,
        summary="Advisory warning with a finding",
        findings=(
            LLMFinding(
                severity=LLMFindingSeverity.warning,
                category=LLMFindingCategory.provenance,
                record_type="calculation",
                record_id=calc.id,
                message="Missing source artifact summary.",
                evidence_keys=("missing_checks.source_artifact_present",),
            ),
        ),
        model="fake_test/fixed",
        used_rag=False,
    )

    result = run_llm_precheck_for_submission(
        db_session,
        submission.id,
        provider=FakeLLMPrecheckProvider(fixed_result=fixed),
    )

    assert result.label is LLMPrecheckLabel.warning
    assert result.findings  # the advisory finding is carried through
    # The advisory finding cites evidence (pointer) but changes nothing.
    assert submission.status is SubmissionStatus.pending
    assert _deterministic_snapshot(db_session, calc.id) == evidence_before
    # Persisted only as a neutral, append-only event authored by the llm actor.
    events = _precheck_events(db_session, submission.id)
    assert len(events) == 1
    assert events[0].actor_kind is SubmissionActorKind.llm
    assert events[0].from_status is None
    assert events[0].to_status is None
    # Advisory machine-review statuses are a separate vocabulary from moderation.
    machine_values = {m.value for m in MachineReviewStatus}
    submission_values = {s.value for s in SubmissionStatus}
    assert machine_values.isdisjoint(submission_values)


def test_repeated_machine_review_runs_keep_evidence_byte_identical(db_session, _api_test_user):
    """Repeated advisory runs append events but never drift deterministic evidence."""
    calc = _make_opt_calc_with_rubric(db_session)
    submission = _seed_submission(db_session, _api_test_user)
    link_record(
        db_session,
        submission=submission,
        record_type=SubmissionRecordType.calculation,
        record_id=calc.id,
        role="primary",
    )
    evidence_before = _deterministic_snapshot(db_session, calc.id)

    for _ in range(3):
        run_llm_precheck_for_submission(
            db_session,
            submission.id,
            provider=FakeLLMPrecheckProvider(),
        )

    assert _deterministic_snapshot(db_session, calc.id) == evidence_before
    # Advisory persistence is append-only: one event per run.
    assert len(_precheck_events(db_session, submission.id)) == 3
