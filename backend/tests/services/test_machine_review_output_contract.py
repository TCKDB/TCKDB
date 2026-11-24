"""Format 2 of the provider payload: caps, addressing, offered-ref check, exported schema."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from jsonschema import Draft7Validator
from pydantic import ValidationError

from app.services.machine_review.offer import offer_violation
from app.services.machine_review.providers.interface import (
    ReservedMachineReviewStatusError,
    parse_machine_review_payload,
)
from app.services.machine_review.schemas import provider_output_json_schema

_FIXTURE = Path(__file__).parent.parent / "fixtures" / "machine_review" / "format_v2_full_review.json"


def _payload() -> dict:
    return {
        "format": {"name": "machine_review", "version": 2},
        "status": "machine_screened_warning",
        "curator_priority": "low",
        "summary": "One concern.",
        "findings": [
            {
                "severity": "warning",
                "category": "provenance",
                "record_type": "kinetics",
                "record_ref": "9001",
                "message": "Provenance is thin.",
                "evidence_keys": ["prov.missing"],
                "recommended_action": None,
            }
        ],
        "retrieval_sources": [],
    }


def _with(**changes):
    payload = _payload()
    payload.update(changes)
    return payload


def _finding_with(**changes):
    payload = _payload()
    payload["findings"][0].update(changes)
    return payload


_OVER_AND_AT_CAP = [
    ("summary", lambda n: _with(summary="s" * n), 281, 280),
    ("findings", lambda n: _with(findings=[_payload()["findings"][0]] * n), 11, 10),
    ("message", lambda n: _finding_with(message="m" * n), 241, 240),
    ("evidence_keys", lambda n: _finding_with(evidence_keys=["k"] * n), 9, 8),
    ("evidence_key_length", lambda n: _finding_with(evidence_keys=["k" * n]), 65, 64),
    ("recommended_action", lambda n: _finding_with(recommended_action="a" * n), 201, 200),
    ("retrieval_sources", lambda n: _with(retrieval_sources=["s"] * n), 11, 10),
    ("retrieval_source_length", lambda n: _with(retrieval_sources=["s" * n]), 65, 64),
]


@pytest.mark.parametrize(("name", "build", "over", "at"), _OVER_AND_AT_CAP, ids=[c[0] for c in _OVER_AND_AT_CAP])
def test_cap_refuses_one_over_and_accepts_the_cap(name, build, over, at):
    with pytest.raises(ValidationError):
        parse_machine_review_payload(build(over))
    assert parse_machine_review_payload(build(at)).format.version == 2


def test_summary_is_required():
    payload = _payload()
    del payload["summary"]
    with pytest.raises(ValidationError):
        parse_machine_review_payload(payload)


def test_finding_with_record_id_is_refused_and_without_it_parses():
    with pytest.raises(ValidationError):
        parse_machine_review_payload(_finding_with(record_id=9001))
    assert parse_machine_review_payload(_payload()).findings[0].record_ref == "9001"


def test_used_rag_is_refused_and_absent_parses():
    with pytest.raises(ValidationError):
        parse_machine_review_payload(_with(used_rag=False))
    assert parse_machine_review_payload(_payload()).retrieval_sources == ()


def test_format_one_is_refused():
    with pytest.raises(ValidationError):
        parse_machine_review_payload(_with(format={"name": "machine_review", "version": 1}))


@pytest.mark.parametrize("status", ["not_run", "machine_review_failed"])
def test_reserved_statuses_stay_refused_and_a_provider_status_parses(status):
    with pytest.raises(ReservedMachineReviewStatusError):
        parse_machine_review_payload(_with(status=status))
    assert parse_machine_review_payload(_with(status="machine_screened_pass")).status.value == "machine_screened_pass"


# ---------------------------------------------------------------------------
# offered refs and sources
# ---------------------------------------------------------------------------

_OFFERED = frozenset({("kinetics", "9001")})


def _result(**finding_changes):
    return parse_machine_review_payload(_finding_with(**finding_changes))


def test_offer_violation_unoffered_pair_vs_offered_pair():
    assert offer_violation(_result(record_ref="1"), offered_refs=_OFFERED, returned_sources=frozenset())
    assert offer_violation(_result(), offered_refs=_OFFERED, returned_sources=frozenset()) is None


def test_offer_violation_names_the_field_and_does_not_echo_the_model_values():
    result = _result(record_ref="777")
    text = offer_violation(result, offered_refs=_OFFERED, returned_sources=frozenset())
    assert "record_ref" in text
    assert "777" not in text
    assert result.findings[0].record_type not in text


def test_offer_violation_half_pair_is_a_violation():
    assert offer_violation(_result(record_type=None), offered_refs=_OFFERED, returned_sources=frozenset())
    assert offer_violation(_result(record_ref=None), offered_refs=_OFFERED, returned_sources=frozenset())


def test_offer_violation_submission_scoped_finding_passes():
    result = _result(record_type=None, record_ref=None)
    assert offer_violation(result, offered_refs=frozenset(), returned_sources=frozenset()) is None


def test_offer_violation_retrieval_sources():
    result = parse_machine_review_payload(_with(retrieval_sources=["guidance.t1"]))
    assert offer_violation(result, offered_refs=_OFFERED, returned_sources=frozenset()) is not None
    assert offer_violation(result, offered_refs=_OFFERED, returned_sources=frozenset({"guidance.t1"})) is None


# ---------------------------------------------------------------------------
# exported JSON Schema
# ---------------------------------------------------------------------------

_ALLOWED_KEYWORDS = {
    "$schema", "type", "properties", "required", "additionalProperties", "items", "minItems", "maxItems",
    "enum", "const", "anyOf", "minLength", "maxLength", "description",
}


def _walk(node, path="$"):
    if isinstance(node, dict):
        yield path, node
        for key, value in node.items():
            if key == "properties":
                for name, sub in value.items():
                    yield from _walk(sub, f"{path}.{name}")
            elif key in {"items", "additionalProperties"}:
                yield from _walk(value, f"{path}.{key}")
            elif key == "anyOf":
                for index, sub in enumerate(value):
                    yield from _walk(sub, f"{path}|{index}")
    elif isinstance(node, list):
        for index, sub in enumerate(node):
            yield from _walk(sub, f"{path}[{index}]")


def test_export_is_a_valid_draft_07_schema():
    schema = provider_output_json_schema()
    Draft7Validator.check_schema(schema)
    assert schema["$schema"] == "http://json-schema.org/draft-07/schema#"


def test_export_is_usable_as_a_strict_grammar():
    schema = provider_output_json_schema()
    assert "$ref" not in json.dumps(schema) and "$defs" not in json.dumps(schema)
    seen_objects = seen_strings = 0
    for path, node in _walk(schema):
        extra = set(node) - _ALLOWED_KEYWORDS
        assert not extra, f"{path}: {extra}"
        if node.get("type") == "object":
            seen_objects += 1
            assert node["additionalProperties"] is False, path
            assert sorted(node["required"]) == sorted(node["properties"]), path
        if node.get("type") == "string" and "enum" not in node and "const" not in node:
            seen_strings += 1
            assert "maxLength" in node, path
    assert seen_objects >= 3 and seen_strings >= 6


def test_export_narrows_status_and_drops_identity():
    schema = provider_output_json_schema()
    status = schema["properties"]["status"]["enum"]
    assert sorted(status) == [
        "machine_screened_needs_attention", "machine_screened_pass", "machine_screened_warning",
    ]
    assert "model" not in schema["properties"] and "provider" not in schema["properties"]
    assert "used_rag" not in schema["properties"]


def test_export_is_ascii_and_carries_no_docstrings():
    text = json.dumps(provider_output_json_schema(), ensure_ascii=False)
    assert text.isascii()
    assert "Pure metadata" not in text and "Versioned provider payload" not in text


def test_export_returns_a_fresh_copy():
    first = provider_output_json_schema()
    first["properties"].clear()
    assert provider_output_json_schema()["properties"]


def _strip_identity(payload: dict) -> dict:
    return {k: v for k, v in payload.items() if k not in {"model", "provider"}}


def test_export_accepts_the_full_fixture_and_refuses_each_violation():
    validator = Draft7Validator(provider_output_json_schema())
    good = _strip_identity(json.loads(_FIXTURE.read_text())["audit_event_details_json"])
    assert not list(validator.iter_errors(good))

    def broken(mutate):
        bad = copy.deepcopy(good)
        mutate(bad)
        return bad

    cases = {
        "summary_281": lambda p: p.update(summary="s" * 281),
        "top_level_key": lambda p: p.update(extra="x"),
        "finding_key": lambda p: p["findings"][0].update(extra="x"),
        "not_run": lambda p: p.update(status="not_run"),
        "eleven_findings": lambda p: p.update(findings=[p["findings"][0]] * 11),
        "format_v1": lambda p: p.update(format={"name": "machine_review", "version": 1}),
    }
    for name, mutate in cases.items():
        assert list(validator.iter_errors(broken(mutate))), name
