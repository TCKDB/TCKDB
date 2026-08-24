"""The include-gated strip drops what a table declares, and nothing else.

The mechanism is easy to state and easy to over-generalise, and the
over-generalisation is dangerous rather than merely untidy. "Omit an
unrequested include-gated section" is the rule. "Drop null optional fields
from responses" is a different rule that looks like a simplification of it
and would corrupt data: ``clients/python/pagination.py`` reads an *absent*
``next_cursor`` as "this server predates the keyset contract, restart the
traversal from offset zero" and a *present-and-null* ``next_cursor`` as
"this was the last page". Drop that null and every completed traversal
restarts and yields the whole result set a second time, silently.

So the boundary is tested here structurally rather than trusted: the strip
takes a declared :class:`IncludeGatedSections` table and computes the keys
to pop from that table alone, and these tests pin both halves — the fields
in the table go, and a null field no table names stays, including under the
widest scope the strip has.
"""

from __future__ import annotations

import dataclasses
import json
from typing import Any

import pytest
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.api.public_openapi import project_hosted_openapi
from app.api.routes.scientific._response import (
    ALL_INCLUDE_GATED_TABLES,
    ANYWHERE_SCOPE,
    ASSESSMENTS_SECTION,
    CALCULATION_RECORD_SECTIONS,
    DOCUMENT_SCOPE,
    FULL_SCOPE,
    INCLUDE_GATED_COMPONENTS,
    REACTION_FULL_SECTIONS,
    TRUST_SECTION,
    IncludeGatedSections,
    omit_unrequested_calculation_sections,
    omit_unrequested_sections,
)


class _Request(BaseModel):
    include: list[str]


class _Payload(BaseModel):
    request: _Request


def _payload(*include: str) -> _Payload:
    return _Payload(request=_Request(include=list(include)))


def _body(response: Any) -> dict[str, Any]:
    assert isinstance(response, JSONResponse)
    return json.loads(response.body)


# ---------------------------------------------------------------------------
# The hard boundary
# ---------------------------------------------------------------------------


def test_the_strip_refuses_a_table_it_cannot_audit():
    """A bare mapping is not a declared table and is not accepted.

    The type is the only thing standing between this helper and a blanket
    null-strip, so it is checked rather than assumed. There is deliberately
    no argument that means "drop the nulls".
    """
    visibility = JSONResponse(
        {"request": {"include": []}, "record": {"trust": {"grade": "a"}}}
    )

    with pytest.raises(TypeError) as excinfo:
        omit_unrequested_sections(
            visibility,
            _payload(),
            table={"trust": ("trust",)},  # type: ignore[arg-type]
        )

    assert "IncludeGatedSections" in str(excinfo.value)


def test_a_null_field_no_table_names_survives_the_detail_strip():
    """``next_cursor`` is in scope, is null, and must still be there."""
    visibility = JSONResponse(
        {
            "request": {"include": []},
            "record": {
                "trust": {"grade": "a"},
                "next_cursor": None,
                "post_collapse_total": None,
                "supersession": None,
            },
        }
    )

    record = _body(omit_unrequested_sections(visibility, _payload(), table=TRUST_SECTION))[
        "record"
    ]

    assert "trust" not in record
    assert "next_cursor" in record and record["next_cursor"] is None
    assert "post_collapse_total" in record and record["post_collapse_total"] is None
    assert "supersession" in record and record["supersession"] is None


def test_a_null_field_no_table_names_survives_the_widest_scope():
    """``anywhere`` visits every dict in the payload and still pops only the table.

    This is the scope ``assessments`` runs under — it walks the whole tree,
    so if any scope were going to sweep up a same-shaped null it is this
    one.
    """
    visibility = JSONResponse(
        {
            "request": {"include": []},
            "pagination": {"total": 2, "next_cursor": None},
            "records": [
                {
                    "assessments": {"state": "unassessed"},
                    "next_cursor": None,
                    "nested": {"assessments": {"state": "stale"}, "next_cursor": None},
                }
            ],
        }
    )

    body = _body(
        omit_unrequested_sections(
            visibility, _payload(), table=ASSESSMENTS_SECTION, scope=ANYWHERE_SCOPE
        )
    )

    record = body["records"][0]
    assert "assessments" not in record
    assert "assessments" not in record["nested"]
    assert "next_cursor" in body["pagination"]
    assert body["pagination"]["next_cursor"] is None
    assert "next_cursor" in record and record["next_cursor"] is None
    assert "next_cursor" in record["nested"] and record["nested"]["next_cursor"] is None


def test_two_fields_with_one_name_get_opposite_treatment():
    """``workflow_tool_release`` twice on one response, two different answers.

    The record's own provenance field is null because the calculation
    references no workflow tool — a fact about the record, not about the
    request — so it keeps its null. The identically-named field nested in
    the include-gated ``execution_environment`` block goes with its
    section. Any implementation matching on field names gets one of these
    wrong, which is why the table is declared and per-surface.
    """
    visibility = JSONResponse(
        {
            "request": {"include": []},
            "record": {
                "calculation_ref": "calc_x",
                "workflow_tool_release": None,
                "execution_environment": {
                    "manifest_ref": "eem_x",
                    "workflow_tool_release": None,
                },
            },
        }
    )

    record = _body(omit_unrequested_calculation_sections(visibility, _payload()))["record"]

    assert "execution_environment" not in record
    assert "workflow_tool_release" in record
    assert record["workflow_tool_release"] is None


def test_a_declared_table_cannot_be_edited_at_runtime():
    """No code path can add ``next_cursor`` to a table after import."""
    with pytest.raises(TypeError):
        CALCULATION_RECORD_SECTIONS.sections["surprise"] = ("next_cursor",)  # type: ignore[index]

    with pytest.raises(dataclasses.FrozenInstanceError):
        CALCULATION_RECORD_SECTIONS.surface = "elsewhere"  # type: ignore[misc]


def test_an_unknown_scope_is_refused_rather_than_guessed():
    visibility = JSONResponse({"request": {"include": []}, "record": {"trust": {}}})

    with pytest.raises(ValueError):
        omit_unrequested_sections(
            visibility, _payload(), table=TRUST_SECTION, scope="records"
        )


# ---------------------------------------------------------------------------
# The three states
# ---------------------------------------------------------------------------


def test_a_requested_but_empty_section_keeps_its_null():
    """Requested-with-nothing-there is the middle state and is not omission."""
    visibility = JSONResponse(
        {
            "request": {"include": ["results"]},
            "record": {"results": None, "artifacts": None},
        }
    )

    record = _body(
        omit_unrequested_calculation_sections(visibility, _payload("results"))
    )["record"]

    assert "results" in record and record["results"] is None
    assert "artifacts" not in record


def test_the_table_is_not_the_identity_mapping():
    """``review`` governs ``review_history``, so token names cannot be reused as fields."""
    assert CALCULATION_RECORD_SECTIONS.sections["review"] == ("review_history",)

    visibility = JSONResponse(
        {
            "request": {"include": []},
            "record": {"review_history": [{"status": "approved"}]},
        }
    )

    record = _body(omit_unrequested_calculation_sections(visibility, _payload()))["record"]
    assert "review_history" not in record

    visibility = JSONResponse(
        {
            "request": {"include": ["review"]},
            "record": {"review_history": [{"status": "approved"}]},
        }
    )
    record = _body(
        omit_unrequested_calculation_sections(visibility, _payload("review"))
    )["record"]
    assert record["review_history"] == [{"status": "approved"}]


def test_fields_to_omit_is_computed_from_the_table_alone():
    table = IncludeGatedSections(
        surface="test", sections={"a": ("alpha", "alias"), "b": ("beta",)}
    )

    assert table.fields_to_omit([]) == {"alpha", "alias", "beta"}
    assert table.fields_to_omit(["a"]) == {"beta"}
    assert table.fields_to_omit(["a", "b"]) == set()
    assert table.fields_by_token() == {"alpha": "a", "alias": "a", "beta": "b"}


# ---------------------------------------------------------------------------
# The hosted OpenAPI marker
# ---------------------------------------------------------------------------


def test_the_marker_registry_only_names_declared_sections():
    """Every marked property comes from a declared table, and there is at least one."""
    declared: dict[str, str] = {}
    for table in ALL_INCLUDE_GATED_TABLES.values():
        declared.update(table.fields_by_token())

    assert INCLUDE_GATED_COMPONENTS, "the marker registry enumerates nothing"
    marked = 0
    for component, gating in INCLUDE_GATED_COMPONENTS.items():
        assert gating, f"{component} is registered with no gated properties"
        for field_name, token in gating.items():
            assert declared.get(field_name) == token, (
                f"{component}.{field_name} is marked as gated by {token!r} but no "
                "declared table says so"
            )
            marked += 1
    assert marked == 99, f"the marker registry names {marked} properties"


def test_the_hosted_document_marks_the_gated_properties(client):
    schema = client.get("/openapi.json").json()
    components = schema["components"]["schemas"]

    checked = 0
    for component, gating in INCLUDE_GATED_COMPONENTS.items():
        properties = components[component]["properties"]
        required = set(components[component].get("required", []))
        for field_name, token in gating.items():
            assert properties[field_name]["x-tckdb-include-gated"] == token
            # A marked property claims "normally absent", so the document
            # must not also demand it.
            assert field_name not in required
            checked += 1

    assert checked == 99, f"the hosted document carries {checked} markers"


def test_the_marker_is_not_stamped_on_ungated_properties():
    """``project_hosted_openapi`` touches the registered names and no others."""
    schema = {
        "components": {
            "schemas": {
                "ScientificCalculationRecord": {
                    "properties": {
                        "results": {"type": "array"},
                        "workflow_tool_release": {"type": "object"},
                        "calculation_ref": {"type": "string"},
                    }
                },
                "AnalyticsPagination": {
                    "properties": {"next_cursor": {"type": "string"}}
                },
            }
        }
    }

    projected = project_hosted_openapi(schema)
    record = projected["components"]["schemas"]["ScientificCalculationRecord"][
        "properties"
    ]
    pagination = projected["components"]["schemas"]["AnalyticsPagination"]["properties"]

    assert record["results"]["x-tckdb-include-gated"] == "results"
    assert "x-tckdb-include-gated" not in record["workflow_tool_release"]
    assert "x-tckdb-include-gated" not in record["calculation_ref"]
    assert "x-tckdb-include-gated" not in pagination["next_cursor"]


# ---------------------------------------------------------------------------
# ``document`` and ``full`` name the same route and mean opposite things
# ---------------------------------------------------------------------------


def _full_document() -> dict[str, Any]:
    """A ``/reaction-entries/{id}/full`` payload in miniature.

    Its own ten sections sit at the root beside ``request`` and
    ``review_summary``; the records embedded *inside* two of those sections
    each carry a nested ``trust``. Those are two different jobs on one
    response and they need two different scopes.
    """
    return {
        "request": {"include": ["kinetics"], "include_review": "summary"},
        "reaction_entry": {"reaction_entry_ref": "rxe_x"},
        "review_summary": {"approved": 1},
        "kinetics": [{"kinetics_ref": "kin_x", "trust": {"grade": "a"}}],
        "transition_states": [{"transition_state_ref": "ts_x", "trust": None}],
        "calculations": None,
        "irc": None,
        "scans": None,
        "atom_map": None,
        "review_records": None,
    }


def test_the_document_scope_reaches_the_root_sections():
    visibility = JSONResponse(_full_document())

    body = _body(
        omit_unrequested_sections(
            visibility,
            _payload("kinetics"),
            table=REACTION_FULL_SECTIONS,
            scope=DOCUMENT_SCOPE,
        )
    )

    assert "kinetics" in body
    for absent in ("calculations", "irc", "scans", "atom_map", "species"):
        assert absent not in body
    # Not a section, and not gated by any include token: ``review_records``
    # is produced by the separate ``include_review`` parameter, so an
    # include-driven strip has nothing true to say about it.
    assert "review_records" in body and body["review_records"] is None
    assert "request" in body and "review_summary" in body


def test_the_full_scope_is_not_a_substitute_for_the_document_scope():
    """The scope whose name matches the route is a silent no-op here.

    ``FULL_SCOPE`` yields the records embedded in the document, so applying
    the document's own table through it pops nothing and raises nothing --
    exactly the shape of mistake this pair of scopes exists to make
    visible.
    """
    visibility = JSONResponse(_full_document())

    body = _body(
        omit_unrequested_sections(
            visibility,
            _payload("kinetics"),
            table=REACTION_FULL_SECTIONS,
            scope=FULL_SCOPE,
        )
    )

    for still_there in ("calculations", "irc", "scans", "atom_map"):
        assert still_there in body, (
            "FULL_SCOPE was expected to leave the root sections untouched; "
            "if it now strips them the two scopes have converged and one of "
            "them should be deleted rather than quietly kept"
        )
    assert body["calculations"] is None


def test_the_full_scope_reaches_the_nested_trust_the_document_scope_cannot():
    """The other half of the pair, asserted so the no-op above means something."""
    visibility = JSONResponse(_full_document())

    body = _body(
        omit_unrequested_sections(
            visibility, _payload("kinetics"), table=TRUST_SECTION, scope=FULL_SCOPE
        )
    )
    assert "trust" not in body["kinetics"][0]
    assert "trust" not in body["transition_states"][0]

    visibility = JSONResponse(_full_document())
    body = _body(
        omit_unrequested_sections(
            visibility,
            _payload("kinetics"),
            table=TRUST_SECTION,
            scope=DOCUMENT_SCOPE,
        )
    )
    assert "trust" in body["kinetics"][0]


def test_the_document_scope_pops_from_the_root_and_nowhere_deeper():
    """It must not behave like ``anywhere`` by accident."""
    visibility = JSONResponse(
        {
            "request": {"include": []},
            "irc": None,
            "kinetics": [{"kinetics_ref": "kin_x", "irc": None}],
        }
    )

    body = _body(
        omit_unrequested_sections(
            visibility,
            _payload(),
            table=REACTION_FULL_SECTIONS,
            scope=DOCUMENT_SCOPE,
        )
    )

    assert "irc" not in body
    assert "kinetics" not in body
