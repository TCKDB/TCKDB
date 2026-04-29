"""Test helpers for tckdb-client.

We use ``httpx.MockTransport`` so the suite never touches a real TCKDB
backend. ``make_client`` returns a configured client wired to the mock,
and ``record_requests`` exposes captured requests for assertions.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable

import httpx
import pytest

from tckdb_client import TCKDBClient

BASE_URL = "http://test.local/api/v1"


@dataclass
class CapturedRequest:
    method: str
    url: str
    headers: dict[str, str]
    content: bytes

    def json(self) -> Any:
        if not self.content:
            return None
        return json.loads(self.content.decode("utf-8"))


@dataclass
class Recorder:
    requests: list[CapturedRequest] = field(default_factory=list)

    @property
    def last(self) -> CapturedRequest:
        return self.requests[-1]


HandlerFn = Callable[[httpx.Request], httpx.Response]


def make_client(
    handler: HandlerFn,
    *,
    api_key: str | None = "tck_test_key_value_1234",
    timeout: float = 5.0,
) -> tuple[TCKDBClient, Recorder]:
    recorder = Recorder()

    def wrapper(request: httpx.Request) -> httpx.Response:
        recorder.requests.append(
            CapturedRequest(
                method=request.method,
                url=str(request.url),
                headers=dict(request.headers),
                content=request.content,
            )
        )
        return handler(request)

    transport = httpx.MockTransport(wrapper)
    client = TCKDBClient(
        BASE_URL, api_key=api_key, timeout=timeout, transport=transport
    )
    return client, recorder


@pytest.fixture
def base_url() -> str:
    return BASE_URL
