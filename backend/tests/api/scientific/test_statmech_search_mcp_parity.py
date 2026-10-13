"""The statmech MCP tools and the statmech search/detail API accept the same filters and includes (chunk 6.4).

Design: ``statmech_chunk6_design_2026-10-12.md`` (PR 6.4). The MCP holds no filter semantics of its own; it forwards
and the backend decides. Each test names the mutation it kills.
"""

from __future__ import annotations

import sys
from pathlib import Path

import httpx
import pytest

from app.schemas.reads.scientific_statmech_search import StatmechSearchRequest
from app.services.scientific_read import statmech as statmech_module
from app.services.scientific_read.statmech_search import _MEANINGFUL_FILTER_FIELDS
from tests.services.scientific_read._factories import (
    make_species,
    make_species_entry,
    make_statmech,
    next_inchi_key,
)

_MCP_SRC = Path(__file__).resolve().parents[4] / "integrations" / "mcp" / "src"
if str(_MCP_SRC) not in sys.path:
    sys.path.insert(0, str(_MCP_SRC))

from tckdb_mcp.config import Config
from tckdb_mcp.http_client import TCKDBHttpClient
from tckdb_mcp.server import dispatch_tool
from tckdb_mcp.tools import scientific_reads as tools

BASE = "http://testserver/api/v1"


def _search_tool():
    return tools.SEARCH_TOOLS["tckdb_statmech_search"]


class _Bridge(httpx.BaseTransport):
    def __init__(self, test_client) -> None:
        self._client = test_client

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        answer = self._client.request(
            request.method, str(request.url), content=request.content,
            headers={"content-type": request.headers.get("content-type", "application/json")},
        )
        return httpx.Response(
            answer.status_code, headers={"content-type": answer.headers["content-type"]},
            content=answer.content, request=request,
        )


@pytest.fixture
def mcp(client):
    with TCKDBHttpClient(BASE, None, 5.0, transport=_Bridge(client)) as c:
        yield lambda tool, arguments: dispatch_tool(tool, arguments, c, Config.from_env(env={}))


def test_discriminators_equal_the_backend_meaningful_filters():
    """Kills: a future backend filter not reaching the MCP; the pre-6.4 gap (9 of 18)."""
    assert set(_search_tool().required_one_of) == set(_MEANINGFUL_FILTER_FIELDS)


def test_fields_equal_the_request_model_exactly():
    """Kills: a backend request field the MCP refuses as unknown (including the review fields), and an MCP-only
    field the backend would reject or ignore."""
    assert set(_search_tool().fields) == set(StatmechSearchRequest.model_fields)


def test_includes_equal_backend_legal_tokens_minus_internal():
    """Kills: ``electronic_levels`` or ``trust`` missing from the MCP."""
    assert tools.STATMECH_INCLUDES == frozenset(
        statmech_module._LEGAL_INCLUDE_TOKENS - {"internal_ids", "assessments"}
    )


def _entry_with_statmech(db_session):
    entry = make_species_entry(db_session, make_species(db_session, inchi_key=next_inchi_key("MCP64")))
    return entry, make_statmech(db_session, species_entry=entry)


def test_false_filter_reaches_the_backend_and_filters(mcp, db_session):
    """Kills: ``_present`` changed to falsiness, or None-stripping applied to ``False``."""
    _, sm = _entry_with_statmech(db_session)
    no_torsions = mcp("tckdb_statmech_search", {"has_torsions": False})
    assert sm.public_ref in {r["statmech"]["statmech_ref"] for r in no_torsions["records"]}
    with_torsions = mcp("tckdb_statmech_search", {"has_torsions": True})
    assert sm.public_ref not in {r["statmech"]["statmech_ref"] for r in with_torsions["records"]}


def test_workflow_only_filter_is_accepted_alone(mcp, db_session):
    """Kills: workflow-only filters missing from the discriminators."""
    _entry_with_statmech(db_session)
    answer = mcp("tckdb_statmech_search", {"workflow_tool_version": "no-such-version"})
    assert answer["records"] == []


def test_all_is_not_expanded_to_trust(mcp, db_session):
    """Kills: MCP-side expansion adding ``trust``."""
    _, sm = _entry_with_statmech(db_session)
    answer = mcp("tckdb_statmech_search", {"statmech_ref": sm.public_ref, "include": ["all"]})
    assert "trust" not in answer["request"]["include"]
    assert "trust" not in answer["records"][0]["statmech"]


def test_detail_trust_is_forwarded_when_named(mcp, db_session):
    """Kills: detail tool not updated."""
    _, sm = _entry_with_statmech(db_session)
    answer = mcp("tckdb_statmech_detail", {"statmech_ref": sm.public_ref, "include": ["trust"]})
    assert "trust" in answer["record"]
    plain = mcp("tckdb_statmech_detail", {"statmech_ref": sm.public_ref, "include": ["electronic_levels"]})
    assert "trust" not in plain["record"]
