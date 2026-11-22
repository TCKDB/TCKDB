"""``TCKDBClient.delete_record`` posts the deletion request and surfaces the server's refusals."""

from __future__ import annotations

import json
from urllib.parse import urlsplit

import httpx
import pytest

from conftest import make_client
from tckdb_client.errors import TCKDBHTTPError


def test_delete_record_posts_type_ref_and_reason_to_the_audit_collection():
    audit = {"record_type": "kinetics", "public_ref": "kin_abc", "reason": "duplicate"}

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(201, json=audit)

    client, recorder = make_client(handler)

    assert client.delete_record("kinetics", "kin_abc", "duplicate") == audit
    (sent,) = recorder.requests
    assert sent.method == "POST"
    assert urlsplit(sent.url).path.endswith("/api/v1/admin/record-deletions")
    assert json.loads(sent.content) == {"record_type": "kinetics", "public_ref": "kin_abc", "reason": "duplicate"}


def test_a_cited_record_is_raised_with_its_code_and_citers():
    body = {
        "code": "record_deletion_cited",
        "detail": "record_deletion_cited: ...",
        "context": {"cited_by": [{"record_type": "thermo", "public_ref": "thm_x", "via": "thermo_source_calculation.calculation_id"}]},
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(409, json=body)

    client, _ = make_client(handler)

    with pytest.raises(TCKDBHTTPError) as raised:
        client.delete_record("calculation", "calc_abc", "wrong basis set")
    assert raised.value.status_code == 409
    assert raised.value.code == "record_deletion_cited"
