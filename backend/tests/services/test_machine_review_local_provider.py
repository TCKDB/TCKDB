"""The local provider: one chat call to an OpenAI-compatible server, strictly parsed.

Every test talks to a stub HTTP server on 127.0.0.1 or a closed port; none contacts a real model.
Assertions cover request shape, failure mapping and parsing, never the model's verdict.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from app.api.config import Settings
from app.db.models.common import MachineReviewJobFailure as F
from app.services.llm_precheck.schemas import LLMPrecheckContext, LLMRecordRef
from app.services.machine_review.providers import factory as factory_module
from app.services.machine_review.providers.factory import build_machine_review_provider
from app.services.machine_review.providers.interface import (
    MachineReviewContext,
    MachineReviewProviderAuthError,
    MachineReviewProviderConfigurationError,
    MachineReviewProviderTimeoutError,
    MachineReviewProviderUnavailableError,
)
from app.services.machine_review.providers.local import LocalMachineReviewProvider
from app.services.machine_review.providers.openai_transport import (
    ModelOutputTruncatedError,
    extract_turn,
)
from app.services.machine_review.run import _failure_code_for, invoke_provider
from app.services.machine_review.schemas import MachineReviewStatus, provider_output_json_schema
from tests.services._llm_stub_server import StubResponse, StubServer, refused_base_url

_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "machine_review"
LLAMA = json.loads((_FIXTURES / "llama_server_chat_json_schema_response.json").read_text())
INLINE_THINK = json.loads((_FIXTURES / "inline_think_chat_response.json").read_text())
MODEL = "gguf/stub-model"


def a_context() -> MachineReviewContext:
    return MachineReviewContext.from_llm_precheck_context(
        LLMPrecheckContext(
            submission_id=7,
            title="a deposited reaction",
            record_refs=(
                LLMRecordRef(record_type="transition_state_entry", record_id=9),
                LLMRecordRef(record_type="calculation", record_id=111),
            ),
        )
    )


def settings_for(base_url: str, **extra) -> Settings:
    return Settings(
        ai_review_assistant_mode="local",
        llm_precheck_model=MODEL,
        llm_precheck_base_url=base_url,
        llm_precheck_timeout_seconds=extra.pop("llm_precheck_timeout_seconds", 5),
        **extra,
    )


@pytest.fixture
def stub():
    servers: list[StubServer] = []

    def make(*script: StubResponse) -> StubServer:
        server = StubServer(list(script)).start()
        servers.append(server)
        return server

    yield make
    for server in servers:
        server.stop()


def ok(body=LLAMA) -> StubResponse:
    return StubResponse(body=body)


def review(server: StubServer, **extra):
    provider = build_machine_review_provider(settings_for(server.base_url, **extra))
    return provider.review_submission(a_context())


# --- factory ---------------------------------------------------------------


def test_local_mode_builds_the_local_provider_and_calls_nothing(stub):
    server = stub(ok())
    provider = build_machine_review_provider(settings_for(server.base_url))
    assert isinstance(provider, LocalMachineReviewProvider)
    assert server.requests == []


def test_a_named_key_variable_must_be_set_and_its_value_is_never_echoed(monkeypatch):
    monkeypatch.delenv("LOCAL_KEY_VAR", raising=False)
    with pytest.raises(MachineReviewProviderConfigurationError, match="LOCAL_KEY_VAR"):
        build_machine_review_provider(settings_for("http://127.0.0.1:1/v1", llm_precheck_api_key_env="LOCAL_KEY_VAR"))
    with pytest.raises(MachineReviewProviderConfigurationError) as caught:
        build_machine_review_provider(settings_for("http://127.0.0.1:1/v1", llm_precheck_api_key_env="sk-live-secret!"))
    assert "sk-live-secret" not in str(caught.value)


def test_the_settings_defaults_for_the_local_reviewer():
    defaults = Settings()
    assert defaults.llm_precheck_max_input_tokens == 16000
    assert defaults.llm_precheck_thinking_budget_tokens == 1024
    assert defaults.llm_precheck_structured_output == "json_schema"
    with pytest.raises(ValueError):
        Settings(llm_precheck_structured_output="grammar")


# --- request shape -----------------------------------------------------------


def test_the_one_request_asks_for_the_schema_with_bounded_thinking(stub):
    server = stub(ok())
    result = review(server, llm_precheck_thinking_budget_tokens=512, llm_precheck_max_output_tokens=1200)
    assert len(server.requests) == 1
    request = server.requests[0]
    assert request.path == "/v1/chat/completions"
    body = request.body
    assert body["model"] == MODEL
    fmt = body["response_format"]
    assert fmt["type"] == "json_schema"
    assert fmt["json_schema"]["strict"] is True
    assert fmt["json_schema"]["schema"] == provider_output_json_schema()
    assert body["thinking_budget_tokens"] == 512
    assert "reasoning_budget" not in body
    assert "tools" not in body
    assert body["max_tokens"] == 1200 + 2 * 512
    assert [m["role"] for m in body["messages"]] == ["system", "user"]
    assert result.status is MachineReviewStatus.machine_screened_warning


def test_json_object_mode_sends_a_bare_json_object_format(stub):
    server = stub(ok())
    review(server, llm_precheck_structured_output="json_object")
    assert server.requests[0].body["response_format"] == {"type": "json_object"}


def test_without_a_key_a_placeholder_is_sent_and_with_one_the_key_is(stub, monkeypatch):
    server = stub(ok())
    review(server)
    assert server.requests[0].headers["authorization"].startswith("Bearer ")
    assert "real-secret-value" not in server.requests[0].headers["authorization"]
    monkeypatch.setenv("LOCAL_KEY_VAR", "real-secret-value")
    keyed = stub(ok())
    review(keyed, llm_precheck_api_key_env="LOCAL_KEY_VAR")
    assert keyed.requests[0].headers["authorization"] == "Bearer real-secret-value"


# --- failures ----------------------------------------------------------------


def test_a_refused_connection_is_unavailable_quickly_and_is_not_retried(monkeypatch):
    # Some sandboxes drop a closed local port instead of refusing it, so the failure arrives as a connect timeout.
    monkeypatch.setattr(factory_module, "_LOCAL_CONNECT_TIMEOUT_SECONDS", 2.0)
    provider = build_machine_review_provider(settings_for(refused_base_url()))
    started = time.monotonic()
    with pytest.raises(MachineReviewProviderUnavailableError):
        provider.review_submission(a_context())
    assert time.monotonic() - started < 3.5
    assert _failure_code_for(MachineReviewProviderUnavailableError("x")) is F.provider_unavailable


def test_a_read_timeout_is_a_timeout_after_exactly_one_request(stub):
    server = stub(StubResponse(body=LLAMA, delay=2.5))
    with pytest.raises(MachineReviewProviderTimeoutError):
        review(server, llm_precheck_timeout_seconds=1)
    assert len(server.requests) == 1


def test_a_503_is_unavailable_and_a_401_is_an_auth_failure(stub):
    with pytest.raises(MachineReviewProviderUnavailableError):
        review(stub(StubResponse(body={"error": {"message": "loading"}}, status=503)))
    with pytest.raises(MachineReviewProviderAuthError):
        review(stub(StubResponse(body={"error": {"message": "no"}}, status=401)))


def test_invoke_provider_maps_each_failure_to_its_code(stub):
    cases = [
        (StubResponse(body={"error": {"message": "x"}}, status=503), F.provider_unavailable),
        (StubResponse(body={"error": {"message": "x"}}, status=401), F.auth_failed),
        (StubResponse(body={"choices": [{"message": {"content": "not json"}, "finish_reason": "stop"}]}), F.invalid_output),
    ]
    for step, code in cases:
        server = stub(step)
        provider = build_machine_review_provider(settings_for(server.base_url))
        result, reason, got = invoke_provider(provider, a_context(), Settings())
        assert result is None and reason and got is code
    result, reason, got = invoke_provider(
        build_machine_review_provider(settings_for(stub(ok()).base_url)), a_context(), Settings()
    )
    assert result is not None and reason is None and got is None


def test_an_unexpected_error_stays_an_internal_error():
    assert _failure_code_for(RuntimeError("boom")) is F.internal_error


# --- parsing -----------------------------------------------------------------


def test_the_recorded_llama_reply_parses_and_keeps_thinking_out_of_the_result(stub):
    result = review(stub(ok()))
    assert result.status is MachineReviewStatus.machine_screened_warning
    assert len(result.findings) == 2
    assert result.model == MODEL
    assert "REASONING-SENTINEL" not in result.model_dump_json()


def test_model_and_provider_come_from_configuration_not_the_payload(stub):
    payload = json.loads(LLAMA["choices"][0]["message"]["content"])
    payload["model"] = "claimed-by-model"
    payload["provider"] = "claimed-provider"
    body = json.loads(json.dumps(LLAMA))
    body["choices"][0]["message"]["content"] = json.dumps(payload)
    result = review(stub(ok(body)))
    assert result.model == MODEL
    assert result.provider == "LocalMachineReviewProvider"


def test_a_leading_think_block_is_stripped_and_the_plain_twin_parses_the_same(stub):
    inline = review(stub(ok(INLINE_THINK)))
    plain = review(stub(ok()))
    assert inline.model_dump() == plain.model_dump()


def test_an_unclosed_think_block_is_invalid_output(stub):
    body = json.loads(json.dumps(INLINE_THINK))
    body["choices"][0]["message"]["content"] = "<think>never closed " + LLAMA["choices"][0]["message"]["content"]
    server = stub(ok(body))
    provider = build_machine_review_provider(settings_for(server.base_url))
    result, reason, code = invoke_provider(provider, a_context(), Settings())
    assert result is None and code is F.invalid_output


def test_truncation_with_only_thinking_names_the_thinking_setting():
    payload = {
        "choices": [
            {"finish_reason": "length", "message": {"content": "", "reasoning_content": "thought " * 50}}
        ]
    }
    with pytest.raises(ModelOutputTruncatedError, match="LLM_PRECHECK_THINKING_BUDGET_TOKENS"):
        extract_turn(payload)


def test_extract_turn_returns_content_reasoning_and_usage():
    turn = extract_turn(LLAMA)
    assert turn.content == LLAMA["choices"][0]["message"]["content"]
    assert turn.reasoning_content.startswith("REASONING-SENTINEL")
    assert turn.finish_reason == "stop"
    assert turn.usage["prompt_tokens"] == 812
