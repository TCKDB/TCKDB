"""Tests for optional AI Review Assistant plumbing."""

from __future__ import annotations

from typing import Any

import pytest

from app.api.config import Settings
from app.db.models.common import SubmissionKind, SubmissionRecordType, SubmissionStatus
from app.services.llm_precheck.context_builder import build_llm_precheck_context
from app.services.llm_precheck.providers import (
    DisabledLLMPrecheckProvider,
    FakeLLMPrecheckProvider,
    LLMPrecheckConfigurationError,
    build_llm_precheck_provider,
)
from app.services.llm_precheck.schemas import (
    LLMFinding,
    LLMFindingCategory,
    LLMFindingSeverity,
    LLMPrecheckContext,
    LLMPrecheckLabel,
    LLMPrecheckResult,
)
from app.services.llm_precheck.service import run_llm_precheck_for_submission
from app.services.submission import create_submission, link_record


class RaisingProvider:
    """Provider fixture that simulates a provider failure."""

    def review_submission(
        self,
        context: LLMPrecheckContext,
    ) -> LLMPrecheckResult:
        """Raise a deterministic provider error."""
        raise RuntimeError("provider unavailable")


class MalformedProvider:
    """Provider fixture that returns a malformed response object."""

    def review_submission(
        self,
        context: LLMPrecheckContext,
    ) -> Any:
        """Return a dict that cannot validate as an LLMPrecheckResult."""
        return {"label": "not-a-real-label", "findings": []}


def _seed_submission(db_session, user_id: int):
    """Create a pending submission for LLM precheck tests."""
    submission = create_submission(
        db_session,
        created_by=user_id,
        submission_kind=SubmissionKind.thermo,
        title="LLM precheck test",
        summary="Compact submission summary",
    )
    db_session.flush()
    return submission


def test_off_mode_returns_not_run(db_session, _api_test_user):
    """Off mode returns a local not-run result."""
    submission = _seed_submission(db_session, _api_test_user)
    settings = Settings(ai_review_assistant_mode="off")

    result = run_llm_precheck_for_submission(
        db_session,
        submission.id,
        settings_obj=settings,
    )

    assert result.label is LLMPrecheckLabel.not_run
    assert result.summary == "AI Review Assistant is off"
    assert result.findings == ()
    assert result.model is None
    assert result.used_rag is False


def test_off_mode_requires_no_api_key(monkeypatch):
    """Off mode settings instantiate without any API-key environment variable."""
    monkeypatch.delenv("LLM_PRECHECK_API_KEY", raising=False)
    monkeypatch.delenv("LLM_PRECHECK_API_KEY_ENV", raising=False)

    settings = Settings(ai_review_assistant_mode="off")
    provider = build_llm_precheck_provider(settings)

    assert settings.ai_review_assistant_mode == "off"
    assert isinstance(provider, DisabledLLMPrecheckProvider)


def test_disabled_provider_returns_not_run():
    """The disabled provider returns the expected local not-run result."""
    provider = DisabledLLMPrecheckProvider()
    context = LLMPrecheckContext(submission_id=1)

    result = provider.review_submission(context)

    assert result == LLMPrecheckResult(
        label=LLMPrecheckLabel.not_run,
        summary="AI Review Assistant is off",
        findings=(),
        model=None,
        used_rag=False,
    )


def test_fake_provider_returns_deterministic_structured_result():
    """The fake provider derives a deterministic pass result from record refs."""
    provider = FakeLLMPrecheckProvider()
    context = LLMPrecheckContext(
        submission_id=1,
        record_refs=(
            {"record_type": "calculation", "record_id": 10, "role": "primary"},
        ),
    )

    result = provider.review_submission(context)

    assert result.label is LLMPrecheckLabel.pass_
    assert result.summary == "Fake precheck inspected 1 linked record(s)."
    assert result.findings == ()
    assert result.model == "fake_test/simple-v1"
    assert result.used_rag is False


def test_fake_provider_can_return_configured_fixed_result():
    """The fake provider can return a fixed structured result for tests."""
    fixed = LLMPrecheckResult(
        label=LLMPrecheckLabel.warning,
        summary="Configured warning",
        findings=(
            LLMFinding(
                severity=LLMFindingSeverity.warning,
                category=LLMFindingCategory.provenance,
                record_type="calculation",
                record_id=42,
                message="Missing source artifact summary.",
                evidence_keys=("missing_checks.source_artifact_present",),
            ),
        ),
        model="fake_test/fixed",
        used_rag=False,
    )
    provider = FakeLLMPrecheckProvider(fixed_result=fixed)

    result = provider.review_submission(LLMPrecheckContext(submission_id=1))

    assert result == fixed


def test_provider_exception_becomes_failed_to_review(db_session, _api_test_user):
    """Provider exceptions are advisory results, not caller failures."""
    submission = _seed_submission(db_session, _api_test_user)

    result = run_llm_precheck_for_submission(
        db_session,
        submission.id,
        provider=RaisingProvider(),
    )

    assert result.label is LLMPrecheckLabel.failed_to_review
    assert result.findings == ()
    assert "provider unavailable" in (result.summary or "")


def test_malformed_provider_output_becomes_failed_to_review(db_session, _api_test_user):
    """Malformed provider output validates into failed_to_review."""
    submission = _seed_submission(db_session, _api_test_user)

    result = run_llm_precheck_for_submission(
        db_session,
        submission.id,
        provider=MalformedProvider(),
    )

    assert result.label is LLMPrecheckLabel.failed_to_review
    assert result.summary == "AI Review Assistant returned malformed output."


def test_context_builder_excludes_artifacts_logs_and_coordinates_by_default(
    db_session,
    _api_test_user,
):
    """Context builder includes compact metadata and excludes raw payload flags."""
    submission = _seed_submission(db_session, _api_test_user)
    link_record(
        db_session,
        submission=submission,
        record_type=SubmissionRecordType.calculation,
        record_id=123,
        role="primary",
    )

    context = build_llm_precheck_context(db_session, submission.id)
    dumped = context.model_dump()

    assert context.submission_id == submission.id
    assert context.submission_status == SubmissionStatus.pending.value
    assert context.record_refs[0].record_type == SubmissionRecordType.calculation.value
    assert context.record_refs[0].record_id == 123
    assert context.included_artifact_text is False
    assert context.included_coordinates is False
    assert context.included_private_notes is False
    assert "artifact_text" not in dumped
    assert "coordinate_blocks" not in dumped
    assert "logs" not in dumped


def test_service_does_not_mutate_submission_or_record_links(db_session, _api_test_user):
    """Running fake precheck does not change moderation status or record links."""
    submission = _seed_submission(db_session, _api_test_user)
    link = link_record(
        db_session,
        submission=submission,
        record_type=SubmissionRecordType.calculation,
        record_id=321,
        role="primary",
    )
    before = (
        submission.status,
        submission.llm_precheck_label,
        submission.llm_precheck_summary,
        submission.llm_precheck_model,
        submission.llm_precheck_at,
        link.record_type,
        link.record_id,
        link.role,
    )

    result = run_llm_precheck_for_submission(
        db_session,
        submission.id,
        provider=FakeLLMPrecheckProvider(),
    )

    after = (
        submission.status,
        submission.llm_precheck_label,
        submission.llm_precheck_summary,
        submission.llm_precheck_model,
        submission.llm_precheck_at,
        link.record_type,
        link.record_id,
        link.role,
    )
    assert result.label is LLMPrecheckLabel.pass_
    assert after == before


def test_service_does_not_compute_or_change_evidence_completeness(
    db_session,
    _api_test_user,
):
    """Service result has no evidence completeness field and imports no trust evaluator."""
    submission = _seed_submission(db_session, _api_test_user)

    result = run_llm_precheck_for_submission(
        db_session,
        submission.id,
        provider=FakeLLMPrecheckProvider(),
    )

    assert "evidence_completeness" not in result.model_dump()
    assert result.used_rag is False


def test_service_can_run_without_deterministic_trust_evaluator(
    db_session,
    _api_test_user,
):
    """The context scaffold can run without importing deterministic trust services."""
    submission = _seed_submission(db_session, _api_test_user)

    result = run_llm_precheck_for_submission(
        db_session,
        submission.id,
        provider=FakeLLMPrecheckProvider(),
    )

    assert result.label is LLMPrecheckLabel.warning
    assert result.summary == "Fake precheck found no linked records to inspect."


@pytest.mark.parametrize(
    ("mode", "message"),
    [
        ("cloud", "Cloud mode is specified but no online provider is implemented yet."),
        ("local", "Local mode is specified but no local provider is implemented yet."),
    ],
)
def test_cloud_and_local_modes_are_not_usable_as_real_providers(mode, message):
    """Cloud/local mode selection raises until real providers are implemented."""
    settings = Settings(ai_review_assistant_mode=mode)

    with pytest.raises(LLMPrecheckConfigurationError, match=message):
        build_llm_precheck_provider(settings)
