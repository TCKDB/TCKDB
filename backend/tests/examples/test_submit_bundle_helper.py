"""Tests for ``examples/clients/submit_bundle.py`` response handling.

Covers the shell-friendly exit-code policy that lets users chain
``--dry-run && --submit`` from a shell. The helper itself contains no
business logic — these tests only assert the CLI contract (exit codes,
key strings printed to stdout) so the manual local-to-hosted flow stays
predictable. See
``docs/contribution-bundles/manual-local-to-hosted-v0.md``.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
HELPER_PATH = REPO_ROOT / "examples" / "clients" / "submit_bundle.py"


def _load_helper():
    spec = importlib.util.spec_from_file_location("submit_bundle", HELPER_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def helper():
    return _load_helper()


class _FakeResponse:
    def __init__(self, status_code: int, payload: dict | None = None, *, text: str = ""):
        self.status_code = status_code
        self._payload = payload
        self.text = text

    def json(self) -> dict:
        if self._payload is None:
            raise ValueError("no JSON body")
        return self._payload


def test_dry_run_with_errors_returns_nonzero(helper, capsys):
    response = _FakeResponse(
        200,
        {
            "bundle_valid": True,
            "bundle_kind": "thermo",
            "summary": {
                "records_seen": 1,
                "would_create": 0,
                "would_reuse": 0,
                "would_append": 0,
                "unsupported": 0,
                "errors": 1,
                "warnings": 0,
            },
            "items": [
                {
                    "record_type": "thermo",
                    "action": "error",
                    "reason": "blocking issue",
                }
            ],
            "messages": [],
        },
    )

    rc = helper._handle_response("dry-run", response)
    captured = capsys.readouterr().out

    assert rc == 1
    assert "errors=1" in captured


def test_dry_run_with_unsupported_returns_nonzero(helper, capsys):
    response = _FakeResponse(
        200,
        {
            "bundle_valid": True,
            "bundle_kind": "thermo",
            "summary": {
                "records_seen": 1,
                "would_create": 0,
                "would_reuse": 0,
                "would_append": 0,
                "unsupported": 2,
                "errors": 0,
                "warnings": 0,
            },
            "items": [],
            "messages": [],
        },
    )

    rc = helper._handle_response("dry-run", response)
    captured = capsys.readouterr().out

    assert rc == 1
    assert "unsupported=2" in captured


def test_clean_dry_run_returns_zero(helper, capsys):
    response = _FakeResponse(
        200,
        {
            "bundle_valid": True,
            "bundle_kind": "thermo",
            "summary": {
                "records_seen": 3,
                "would_create": 2,
                "would_reuse": 0,
                "would_append": 1,
                "unsupported": 0,
                "errors": 0,
                "warnings": 0,
            },
            "items": [
                {
                    "record_type": "species",
                    "action": "would_create",
                    "reason": "Not present yet.",
                },
                {
                    "record_type": "thermo",
                    "action": "would_append",
                    "reason": "Append-only.",
                },
            ],
            "messages": [],
        },
    )

    rc = helper._handle_response("dry-run", response)
    captured = capsys.readouterr().out

    assert rc == 0
    assert "would_create=2" in captured
    assert "would_append=1" in captured


def test_submit_success_returns_zero_and_prints_unreviewed_reminder(helper, capsys):
    response = _FakeResponse(
        201,
        {
            "submission_id": 123,
            "status": "pending",
            "review_status": "unreviewed",
            "bundle_kind": "thermo",
            "summary": {
                "records_imported": 1,
                "records_linked": 1,
                "warnings": 0,
            },
            "records": [],
            "messages": [],
        },
    )

    rc = helper._handle_response("submit", response)
    captured = capsys.readouterr().out

    assert rc == 0
    assert "submission_id=123" in captured
    assert "status=pending" in captured
    assert "review_status=unreviewed" in captured
    assert "unreviewed" in captured.lower()
    assert "curator review" in captured


def test_http_400_returns_nonzero_and_prints_payload(helper, capsys):
    response = _FakeResponse(
        400,
        {"detail": "dry-run gate rejected this bundle"},
    )

    rc = helper._handle_response("submit", response)
    captured = capsys.readouterr().out

    assert rc == 1
    parsed = json.loads(captured)
    assert parsed == {"detail": "dry-run gate rejected this bundle"}
