"""Tests for the native v2 machine-review provider plumbing foundation.

These cover the producer side only — provider interface, disabled/off provider,
the test-only fake v2 provider, the factory, config validation, and the
strict-parse / serialization boundary. No real online/local model calls exist;
cloud/local modes validate config and then raise ``NotImplementedError``.

Config namespace note: ``AI_REVIEW_ASSISTANT_MODE`` + ``LLM_PRECHECK_*`` is the
implementation/config namespace; ``MachineReviewProviderResultV2`` is the output
contract; ``machine_review`` is the future *public* concept. No parallel
``MACHINE_REVIEW_*`` env vars are introduced.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.api.config import Settings
from app.services.llm_precheck.providers import LLMPrecheckConfigurationError
from app.services.llm_precheck.schemas import LLMPrecheckContext, LLMRecordRef
from app.services.machine_review.audit_adapter import (
    machine_review_result_from_audit_event,
)
from app.services.machine_review.providers import (
    DisabledMachineReviewProvider,
    FakeMachineReviewProvider,
    MachineReviewContext,
    MachineReviewProvider,
    MachineReviewProviderConfigurationError,
    build_fake_machine_review_provider,
    build_machine_review_provider,
    machine_review_v2_result_to_details_json,
    make_critical_result,
    make_failed_result,
    make_pass_result,
    make_warning_result,
    parse_machine_review_v2_payload,
)
from app.services.machine_review.schemas import (
    MACHINE_REVIEW_V2_SCHEMA_VERSION,
    MachineReviewCategory,
    MachineReviewProviderResultV2,
    MachineReviewSeverity,
    MachineReviewStatus,
)


def _context_with_records(count: int = 1) -> MachineReviewContext:
    """Build a wrapping context referencing an LLMPrecheckContext with records."""
    refs = tuple(
        LLMRecordRef(record_type="calculation", record_id=i + 1, role="primary")
        for i in range(count)
    )
    return MachineReviewContext.from_llm_precheck_context(
        LLMPrecheckContext(submission_id=1, record_refs=refs)
    )


# --------------------------------------------------------------------------- #
# Off mode / disabled provider
# --------------------------------------------------------------------------- #


def test_off_mode_returns_disabled_provider_or_no_write_behavior():
    """Off mode builds the disabled provider and needs no external config."""
    settings = Settings(ai_review_assistant_mode="off")

    provider = build_machine_review_provider(settings)

    assert isinstance(provider, DisabledMachineReviewProvider)
    # Disabled providers satisfy the structural provider protocol.
    assert isinstance(provider, MachineReviewProvider)


def test_off_mode_requires_no_api_key_model_or_base_url(monkeypatch):
    """Off mode builds without any API key, model, or base URL configured."""
    monkeypatch.delenv("LLM_PRECHECK_API_KEY", raising=False)
    settings = Settings(
        ai_review_assistant_mode="off",
        llm_precheck_model=None,
        llm_precheck_api_key_env=None,
        llm_precheck_base_url=None,
    )

    provider = build_machine_review_provider(settings)

    assert isinstance(provider, DisabledMachineReviewProvider)


def test_disabled_provider_returns_not_run_v2_result():
    """The disabled provider returns a valid v2 not-run result."""
    provider = DisabledMachineReviewProvider()

    result = provider.review_submission(MachineReviewContext(submission_id=1))

    assert isinstance(result, MachineReviewProviderResultV2)
    assert result.schema_version == MACHINE_REVIEW_V2_SCHEMA_VERSION
    assert result.status is MachineReviewStatus.not_run
    assert result.findings == ()
    assert result.used_rag is False
    assert result.summary == "AI Review Assistant is disabled / not run"


# --------------------------------------------------------------------------- #
# Fake v2 provider
# --------------------------------------------------------------------------- #


def test_fake_v2_provider_returns_valid_machine_review_v2_payload():
    """The fake provider returns a schema-valid v2 payload derived from context."""
    provider = FakeMachineReviewProvider()

    result = provider.review_submission(_context_with_records(2))

    assert isinstance(result, MachineReviewProviderResultV2)
    assert result.schema_version == MACHINE_REVIEW_V2_SCHEMA_VERSION
    assert result.status is MachineReviewStatus.machine_screened_pass
    assert "2 linked record(s)" in (result.summary or "")
    assert result.used_rag is False


def test_fake_v2_provider_returns_configured_fixed_result():
    """The fake provider returns a fixed v2 result verbatim when configured."""
    fixed = make_warning_result()
    provider = build_fake_machine_review_provider(fixed_result=fixed)

    result = provider.review_submission(MachineReviewContext(submission_id=1))

    assert result is fixed
    assert result.status is MachineReviewStatus.machine_screened_warning


def test_fake_v2_provider_can_emit_transition_state_validation():
    """The fake provider can emit the v2-only transition_state_validation category."""
    result = make_critical_result()

    assert result.status is MachineReviewStatus.machine_screened_needs_attention
    assert len(result.findings) == 1
    finding = result.findings[0]
    assert finding.category is MachineReviewCategory.transition_state_validation
    assert finding.severity is MachineReviewSeverity.critical
    assert finding.recommended_action is not None


def test_fake_v2_provider_can_emit_schema_gap():
    """The fake provider can emit the v2-only schema_gap category."""
    result = make_warning_result()

    assert result.status is MachineReviewStatus.machine_screened_warning
    assert result.findings[0].category is MachineReviewCategory.schema_gap


def test_fake_v2_provider_supports_pass_warning_critical_failed():
    """All four fake result shapes are valid v2 payloads with the right status."""
    assert make_pass_result().status is MachineReviewStatus.machine_screened_pass
    assert (
        make_warning_result().status is MachineReviewStatus.machine_screened_warning
    )
    assert (
        make_critical_result().status
        is MachineReviewStatus.machine_screened_needs_attention
    )
    assert make_failed_result().status is MachineReviewStatus.machine_review_failed


# --------------------------------------------------------------------------- #
# Factory + mode resolution
# --------------------------------------------------------------------------- #


def test_factory_does_not_expose_fake_as_deployer_mode():
    """No AI_REVIEW_ASSISTANT_MODE value yields the fake provider."""
    for mode in ("off", "cloud", "local", "test"):
        settings = Settings(
            ai_review_assistant_mode=mode,
            llm_precheck_model="vendor/model",
            llm_precheck_base_url="http://localhost:11434",
        )
        try:
            provider = build_machine_review_provider(settings)
        except (NotImplementedError, LLMPrecheckConfigurationError):
            # cloud/local (not implemented) and test (refused) never build a fake.
            continue
        assert not isinstance(provider, FakeMachineReviewProvider)


def test_test_mode_is_refused_by_deployer_factory():
    """``mode=test`` is refused: the fake is reachable only via the test helper."""
    settings = Settings(ai_review_assistant_mode="test")

    with pytest.raises(MachineReviewProviderConfigurationError, match="test-only"):
        build_machine_review_provider(settings)


# --------------------------------------------------------------------------- #
# Cloud / local: config validation + not-implemented
# --------------------------------------------------------------------------- #


def test_cloud_mode_requires_model_and_api_key_env_config(monkeypatch):
    """Cloud mode without model/API-key-env config raises a configuration error."""
    monkeypatch.delenv("LLM_PRECHECK_API_KEY", raising=False)
    settings = Settings(
        ai_review_assistant_mode="cloud",
        llm_precheck_model=None,
        llm_precheck_api_key_env=None,
    )

    with pytest.raises(MachineReviewProviderConfigurationError):
        build_machine_review_provider(settings)


def test_cloud_mode_requires_api_key_env_var_present(monkeypatch):
    """Cloud mode requires the named API-key env var to actually be set."""
    monkeypatch.delenv("MR_TEST_KEY", raising=False)
    settings = Settings(
        ai_review_assistant_mode="cloud",
        llm_precheck_model="vendor/model",
        llm_precheck_api_key_env="MR_TEST_KEY",
    )

    with pytest.raises(
        MachineReviewProviderConfigurationError, match="MR_TEST_KEY"
    ):
        build_machine_review_provider(settings)


def test_cloud_mode_real_call_not_implemented_yet(monkeypatch):
    """With valid cloud config, the factory raises NotImplementedError, no API call."""
    monkeypatch.setenv("MR_TEST_KEY", "secret-value")
    settings = Settings(
        ai_review_assistant_mode="cloud",
        llm_precheck_model="vendor/model",
        llm_precheck_api_key_env="MR_TEST_KEY",
    )

    with pytest.raises(NotImplementedError, match="no external model call"):
        build_machine_review_provider(settings)


def test_local_mode_requires_base_url_and_model_config():
    """Local mode without base URL/model config raises a configuration error."""
    settings = Settings(
        ai_review_assistant_mode="local",
        llm_precheck_model=None,
        llm_precheck_base_url=None,
    )

    with pytest.raises(MachineReviewProviderConfigurationError):
        build_machine_review_provider(settings)


def test_local_mode_real_call_not_implemented_yet():
    """With valid local config, the factory raises NotImplementedError, no call."""
    settings = Settings(
        ai_review_assistant_mode="local",
        llm_precheck_model="vendor/model",
        llm_precheck_base_url="http://localhost:11434",
    )

    with pytest.raises(NotImplementedError, match="no local model call"):
        build_machine_review_provider(settings)


# --------------------------------------------------------------------------- #
# Strict-parse / trust boundary
# --------------------------------------------------------------------------- #


def _valid_v2_payload() -> dict:
    """A minimal valid v2 payload dict."""
    return {
        "schema_version": "machine_review_v2",
        "status": "machine_screened_warning",
        "summary": "boundary test",
        "findings": [],
        "model": "vendor/model",
        "provider": "VendorProvider",
        "used_rag": False,
    }


def test_parse_helper_accepts_dict_and_json_string():
    """The parse helper validates both dict and JSON-string raw output."""
    import json

    payload = _valid_v2_payload()

    from_dict = parse_machine_review_v2_payload(payload)
    from_str = parse_machine_review_v2_payload(json.dumps(payload))

    assert from_dict == from_str
    assert from_dict.status is MachineReviewStatus.machine_screened_warning


def test_provider_result_rejects_used_rag_true():
    """A payload claiming RAG fails validation (used_rag is Literal[False])."""
    payload = _valid_v2_payload()
    payload["used_rag"] = True

    with pytest.raises(ValidationError):
        parse_machine_review_v2_payload(payload)


def test_provider_result_rejects_extra_mutation_payload():
    """An extra/mutation field is rejected (extra='forbid')."""
    payload = _valid_v2_payload()
    payload["set_record_review_status"] = "approved"

    with pytest.raises(ValidationError):
        parse_machine_review_v2_payload(payload)


def test_parse_helper_rejects_non_object_payload():
    """A non-object payload raises TypeError, not a silent pass."""
    with pytest.raises(TypeError):
        parse_machine_review_v2_payload("[1, 2, 3]")


# --------------------------------------------------------------------------- #
# Flow-through to the audit adapter
# --------------------------------------------------------------------------- #


def test_v2_provider_result_flows_through_audit_adapter():
    """A fake v2 result, serialized, is consumed by the adapter's native v2 path."""
    provider = build_fake_machine_review_provider(fixed_result=make_critical_result())

    result = provider.review_submission(MachineReviewContext(submission_id=1))
    details_json = machine_review_v2_result_to_details_json(result)

    assert details_json["schema_version"] == MACHINE_REVIEW_V2_SCHEMA_VERSION

    parsed = machine_review_result_from_audit_event(details_json)

    assert parsed.parse_warnings == ()
    assert parsed.result is not None
    assert parsed.result.status is MachineReviewStatus.machine_screened_needs_attention
    assert parsed.provider == "FakeMachineReviewProvider"
    assert (
        parsed.result.findings[0].category
        is MachineReviewCategory.transition_state_validation
    )
    assert parsed.result.findings[0].recommended_action is not None
