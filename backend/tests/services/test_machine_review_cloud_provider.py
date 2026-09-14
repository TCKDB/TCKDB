"""Contract tests for the cloud machine-review provider.

No network and no API key. The provider takes an injected client (plan
7.1: "CI never calls the API"), so these drive the **real** prompt, the
**real** Anthropic response-shape extraction and the **real** parse boundary
against a committed response fixture.

What is being pinned here is the provider's half of the bargain, not the
model's: that untrusted output cannot smuggle a mutation or a RAG claim past
the schema, that identity is stamped rather than believed, and that every
failure mode reaches the service layer as an exception rather than as a
plausible-looking pass.
"""

from __future__ import annotations

import json
import pathlib

import pytest
from pydantic import ValidationError

from app.services.llm_precheck.schemas import LLMPrecheckContext, LLMRecordRef
from app.services.machine_review.providers.anthropic_transport import extract_text
from app.services.machine_review.providers.cloud import (
    CloudMachineReviewProvider,
    MachineReviewModelClient,
)
from app.services.machine_review.providers.interface import (
    MachineReviewContext,
    MachineReviewProvider,
)
from app.services.machine_review.providers.prompt import (
    MACHINE_REVIEW_PROMPT_VERSION,
    MACHINE_REVIEW_SYSTEM_PROMPT,
    build_user_message,
)
from app.services.machine_review.recipe import ACTIVE_MACHINE_REVIEW_PROMPT_VERSION
from app.services.machine_review.schemas import (
    MachineReviewCategory,
    MachineReviewSeverity,
    MachineReviewStatus,
)

_FIXTURES = pathlib.Path(__file__).resolve().parents[1] / "fixtures" / "machine_review"
_RECORDED_RESPONSE = _FIXTURES / "anthropic_messages_warning_response.json"


class RecordedClient:
    """Replays one recorded response and captures what it was asked.

    Capturing the request is the point: without it a test can assert the
    provider returned something sensible while never checking it sent the
    prompt, which is most of what the provider does.
    """

    def __init__(self, reply: str) -> None:
        self.reply = reply
        self.calls: list[dict[str, object]] = []

    def complete(
        self,
        *,
        model: str,
        system: str,
        user: str,
        max_output_tokens: int,
        timeout_seconds: float,
    ) -> str:
        self.calls.append(
            {
                "model": model,
                "system": system,
                "user": user,
                "max_output_tokens": max_output_tokens,
                "timeout_seconds": timeout_seconds,
            }
        )
        return self.reply


class RaisingClient:
    """A transport that fails the way a real one does."""

    def __init__(self, exc: Exception) -> None:
        self.exc = exc

    def complete(self, **_: object) -> str:
        raise self.exc


def recorded_reply() -> str:
    """The model text out of the committed Anthropic response fixture."""
    return extract_text(json.loads(_RECORDED_RESPONSE.read_text()))


def a_context() -> MachineReviewContext:
    return MachineReviewContext.from_llm_precheck_context(
        LLMPrecheckContext(
            submission_id=7,
            submission_kind="thermo",
            title="a deposited reaction",
            record_refs=(
                LLMRecordRef(record_type="transition_state_entry", record_id=9),
                LLMRecordRef(record_type="calculation", record_id=111),
            ),
        )
    )


def build(client: object, **kwargs: object) -> CloudMachineReviewProvider:
    return CloudMachineReviewProvider(
        client=client,  # type: ignore[arg-type]
        model=kwargs.pop("model", "claude-sonnet-5"),  # type: ignore[arg-type]
        **kwargs,  # type: ignore[arg-type]
    )


# --------------------------------------------------------------------------- #
# The happy path, through the real fixture
# --------------------------------------------------------------------------- #


def test_it_satisfies_the_provider_protocol():
    assert isinstance(build(RecordedClient("{}")), MachineReviewProvider)


def test_the_test_double_satisfies_the_transport_protocol():
    """Pins that the protocol is what these tests think it is.

    If ``MachineReviewModelClient`` gains an argument, a stub that no longer
    matches it would otherwise keep passing -- ``Protocol`` is structural, and
    the provider calls ``complete`` by keyword either way.
    """
    assert isinstance(RecordedClient("{}"), MachineReviewModelClient)


def test_a_recorded_response_parses_into_a_validated_v2_result():
    provider = build(RecordedClient(recorded_reply()))

    result = provider.review_submission(a_context())

    assert result.schema_version == "machine_review_v2"
    assert result.status is MachineReviewStatus.machine_screened_warning
    assert len(result.findings) == 2
    first = result.findings[0]
    assert first.severity is MachineReviewSeverity.warning
    assert first.category is MachineReviewCategory.transition_state_validation
    assert first.record_type == "transition_state_entry"
    assert first.record_id == 9
    assert first.recommended_action is not None
    # used_rag is Literal[False]; asserting it here documents that the value
    # survives the round trip rather than being defaulted back in.
    assert result.used_rag is False


def test_the_prompt_and_the_context_actually_reach_the_client():
    client = RecordedClient(recorded_reply())
    context = a_context()

    build(client).review_submission(context)

    assert len(client.calls) == 1
    call = client.calls[0]
    assert call["system"] == MACHINE_REVIEW_SYSTEM_PROMPT
    assert call["user"] == build_user_message(context)
    # The context's records must be in what was sent -- a provider that posted
    # an empty context would still get a valid answer out of a recorded reply.
    assert "transition_state_entry" in str(call["user"])
    assert call["model"] == "claude-sonnet-5"


def test_limits_are_passed_through_not_silently_defaulted():
    client = RecordedClient(recorded_reply())

    build(client, max_output_tokens=99, timeout_seconds=1.5).review_submission(
        a_context()
    )

    assert client.calls[0]["max_output_tokens"] == 99
    assert client.calls[0]["timeout_seconds"] == 1.5


# --------------------------------------------------------------------------- #
# Identity is stamped, not believed
# --------------------------------------------------------------------------- #


def test_the_model_cannot_name_itself():
    """A payload claiming another model is overwritten with the configured one.

    A stored review that misattributes its author cannot be re-run, compared
    against a later one, or trusted about which prompt produced it. The model
    is not the authority on its own identity.
    """
    lying = json.dumps(
        {
            "schema_version": "machine_review_v2",
            "status": "machine_screened_pass",
            "model": "some-other-model",
            "provider": "SomeoneElse",
            "used_rag": False,
        }
    )

    result = build(
        RecordedClient(lying), model="claude-sonnet-5"
    ).review_submission(a_context())

    assert result.model == "claude-sonnet-5"
    assert result.provider == "CloudMachineReviewProvider"


# --------------------------------------------------------------------------- #
# The parse boundary refuses what the contract forbids
# --------------------------------------------------------------------------- #


def test_a_mutation_field_is_refused():
    """The reviewer advises; it never instructs.

    ``extra="forbid"`` is what stops a model returning "set field X". Without
    this test the guarantee rests on a schema flag nobody exercises.
    """
    smuggled = json.dumps(
        {
            "schema_version": "machine_review_v2",
            "status": "machine_screened_pass",
            "used_rag": False,
            "set_review_status": "approved",
        }
    )

    with pytest.raises(ValidationError):
        build(RecordedClient(smuggled)).review_submission(a_context())


def test_a_rag_claim_is_refused():
    claimed = json.dumps(
        {
            "schema_version": "machine_review_v2",
            "status": "machine_screened_pass",
            "used_rag": True,
        }
    )

    with pytest.raises(ValidationError):
        build(RecordedClient(claimed)).review_submission(a_context())


def test_an_out_of_vocabulary_category_is_refused():
    invented = json.dumps(
        {
            "schema_version": "machine_review_v2",
            "status": "machine_screened_warning",
            "used_rag": False,
            "findings": [
                {
                    "severity": "warning",
                    "category": "vibes",
                    "message": "something feels off",
                }
            ],
        }
    )

    with pytest.raises(ValidationError):
        build(RecordedClient(invented)).review_submission(a_context())


def test_prose_around_the_json_is_refused_rather_than_repaired():
    """The prompt asks for bare JSON; a preamble is malformed output.

    Stripping it here would be a silent repair, and the next thing it hid
    would not be a preamble.
    """
    chatty = "Here is my review:\n```json\n{\"schema_version\":\"machine_review_v2\"}\n```"

    with pytest.raises(json.JSONDecodeError):
        build(RecordedClient(chatty)).review_submission(a_context())


# --------------------------------------------------------------------------- #
# Failures reach the service layer as failures
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "exc",
    [
        TimeoutError("read timed out"),
        ConnectionError("connection reset"),
        RuntimeError("502 from the gateway"),
    ],
    ids=["timeout", "network", "http_error"],
)
def test_a_transport_failure_propagates(exc: Exception):
    """Never a plausible-looking pass.

    The service layer owns the conversion to an advisory failed review. A
    provider that swallowed this and returned ``machine_screened_pass`` would
    record that a review happened and found nothing, which is a lie about a
    call that never completed.
    """
    with pytest.raises(type(exc)):
        build(RaisingClient(exc)).review_submission(a_context())


# --------------------------------------------------------------------------- #
# The recorded response shape itself
# --------------------------------------------------------------------------- #


def test_text_extraction_concatenates_text_blocks_and_ignores_others():
    payload = {
        "content": [
            {"type": "thinking", "thinking": "ignored"},
            {"type": "text", "text": '{"a":'},
            {"type": "text", "text": "1}"},
        ]
    }

    assert extract_text(payload) == '{"a":1}'


@pytest.mark.parametrize(
    "payload",
    [
        {"content": []},
        {"content": [{"type": "thinking", "thinking": "no text at all"}]},
        {"content": "not a list"},
        ["not a dict"],
    ],
    ids=["empty", "no_text_block", "content_not_a_list", "not_an_object"],
)
def test_a_response_with_no_usable_text_is_an_error_not_an_empty_review(payload):
    """An empty string would parse as malformed JSON and misreport the fault.

    "The model returned nothing" and "the model returned something we could not
    parse" are different failures, and only one of them is worth re-running.
    """
    with pytest.raises(ValueError):
        extract_text(payload)


# --------------------------------------------------------------------------- #
# Prompt versioning
# --------------------------------------------------------------------------- #


def test_the_prompt_version_is_the_recipe_version():
    """The two cannot drift.

    A stored review's currency is judged against the recipe, which folds the
    prompt version in. If the text is versioned separately from the recipe,
    a review can claim a prompt version whose words it never saw.
    """
    assert MACHINE_REVIEW_PROMPT_VERSION == ACTIVE_MACHINE_REVIEW_PROMPT_VERSION


def test_the_prompt_states_the_rules_the_schema_enforces():
    """The contract is enforced at the parse boundary either way.

    Saying it in the prompt is not what makes it true -- it is what makes a
    refused payload rare instead of routine, since a rejected response is a
    wasted call and a failed review. These assertions are deliberately about
    subject matter, not wording.
    """
    prompt = MACHINE_REVIEW_SYSTEM_PROMPT
    assert "machine_review_v2" in prompt
    assert "advisory" in prompt.lower()
    assert "used_rag" in prompt
    # Every status and category the model may return is named.
    for status in ("machine_screened_pass", "machine_screened_warning"):
        assert status in prompt
    for category in MachineReviewCategory:
        assert category.value in prompt


def test_the_prompt_is_ascii():
    """It is a runtime string under app/, and the ASCII gate scans it."""
    MACHINE_REVIEW_SYSTEM_PROMPT.encode("ascii")


def test_the_user_message_carries_the_context_and_nothing_else():
    context = a_context()

    payload = json.loads(build_user_message(context))

    assert payload["submission_id"] == 7
    assert payload["submission"]["record_refs"][0]["record_type"] == (
        "transition_state_entry"
    )
    # The context carries *flags* saying artifact text and coordinates were not
    # assembled, and the renderer must pass those through rather than drop
    # them: "we did not send coordinates" is itself part of what a stored
    # context hash attests. Note these are `included_*` booleans -- asserting
    # on the bare substring "artifact_text" would match the flag name and read
    # as the opposite of what it means.
    submission = payload["submission"]
    assert submission["included_artifact_text"] is False
    assert submission["included_coordinates"] is False
    assert submission["included_private_notes"] is False
