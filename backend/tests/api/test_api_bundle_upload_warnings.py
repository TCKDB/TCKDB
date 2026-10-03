"""``/bundles/submit`` and ``/bundles/dry-run`` must report the upload warnings (#647).

What was wrong
--------------
``POST /uploads/thermo`` answers a record carrying a ``composite_delta``
correction with ``composite_delta_prefer_scheme_terms``. The same record sent
inside a bundle was answered with ``['ingestion_succeeded']`` by submit and
``[]`` by dry-run: the bundle importer called ``persist_thermo_upload`` and
``persist_kinetics_upload`` without a warnings sink, and never assembled the
request-derived warnings the direct routes add, so every scheme, provenance and
correction warning was dropped on the way in.

How these tests pin it
----------------------
Each test compares the bundle's messages with what the *direct route* returns
for the very same record -- code, message, field and order -- rather than with a
hand-written list, so a warning added to the direct route tomorrow is expected
here without anyone editing this file. The direct route runs inside a savepoint
that is rolled back, so the bundle then meets the same database it would have
met first.

A comparison of two empty lists would pass whatever was broken, so every test
first requires the direct route to have produced a named warning.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import func, select

from app.db.models.thermo import Thermo
from tests.api.test_api_composite_program_run import _CBS_QB3, _inline_thermo, _sp
from tests.api.test_api_scheme_frequency_level import _correction

REPO_ROOT = Path(__file__).resolve().parents[3]
EXAMPLES_DIR = REPO_ROOT / "examples" / "bundles"
DRY_RUN = "/api/v1/bundles/dry-run"
SUBMIT = "/api/v1/bundles/submit"

#: Raised by the workflow while it creates a ``composite_delta`` correction row.
_WORKFLOW_WARNING = "composite_delta_prefer_scheme_terms"
#: Raised by the workflow for an inline sp at a named composite method's level.
_WORKFLOW_WARNING_2 = "named_composite_deposited_as_sp"
#: Derived from the request alone, before the workflow runs.
_REQUEST_WARNING = "missing_software_release_provenance"


def _example(kind: str) -> dict:
    return json.loads((EXAMPLES_DIR / f"{kind}-bundle-v0.json").read_text())


def _thermo_record() -> dict:
    """A thermo record that earns warnings from the workflow and from the request."""
    record = _inline_thermo({"x": _sp(_CBS_QB3)}, [("x", "sp")])
    correction = _correction("composite_delta")
    correction["source_calculation_key"] = "x"
    record["applied_energy_corrections"] = [correction]
    return record


def _thermo_bundle() -> dict:
    bundle = _example("thermo")
    bundle["records"]["thermo_uploads"] = [_thermo_record()]
    return bundle


def _kinetics_bundle() -> dict:
    return _example("kinetics")


@pytest.fixture(autouse=True)
def _doi_metadata(monkeypatch):
    """The DOI's own title, so a depositor title that disagrees earns a warning
    without a network call."""
    monkeypatch.setattr(
        "app.services.literature_resolution.fetch_doi_metadata",
        lambda doi: {
            "title": "Enthalpy of formation of water",
            "container-title": ["J. Phys. Chem. Ref. Data"],
            "issued": 1998,
            "URL": f"https://doi.org/{doi}",
        },
    )


def _kinetics_with_literature_bundle() -> dict:
    """The kinetics example, citing a paper under a title the DOI contradicts."""
    bundle = _kinetics_bundle()
    bundle["records"]["kinetics_uploads"][0]["literature"] = {
        "doi": "10.1063/1.555991",
        "title": "A Completely Different Paper",
    }
    return bundle


def _direct_warnings(client, url: str, record: dict) -> list[tuple[str, str, str]]:
    """What the direct route answers for ``record``, with the write undone."""
    savepoint = client._db_session.begin_nested()
    try:
        resp = client.post(url, json=record)
    finally:
        savepoint.rollback()
    assert resp.status_code == 201, resp.text[:600]
    return [(w["code"], w["message"], w["field"]) for w in resp.json()["warnings"]]


def _bundle_warnings(messages: list[dict], local_ref: str) -> list[tuple[str, str, str]]:
    return [
        (m["code"], m["message"], m["field"])
        for m in messages
        if m["level"] == "warning" and m.get("local_ref") == local_ref
    ]


CASES: dict[str, dict[str, Any]] = {
    "thermo": {
        "bundle": _thermo_bundle,
        "record": lambda bundle: bundle["records"]["thermo_uploads"][0],
        "url": "/api/v1/uploads/thermo",
        "local_ref": "thermo_uploads[0]",
        "required": (_WORKFLOW_WARNING, _WORKFLOW_WARNING_2, _REQUEST_WARNING),
    },
    "kinetics": {
        "bundle": _kinetics_with_literature_bundle,
        "record": lambda bundle: bundle["records"]["kinetics_uploads"][0],
        "url": "/api/v1/uploads/kinetics",
        "local_ref": "kinetics_uploads[0]",
        # One derived from the request, one reported by the workflow.
        "required": ("missing_kinetics_interpretation_assignments", "literature_title_mismatch"),
    },
}


@pytest.mark.parametrize("kind", sorted(CASES))
def test_bundle_submit_reports_the_warnings_the_direct_route_reports(client, kind: str) -> None:
    case = CASES[kind]
    bundle = case["bundle"]()
    expected = _direct_warnings(client, case["url"], case["record"](bundle))
    assert {code for code, _m, _f in expected} >= set(case["required"]), expected

    resp = client.post(SUBMIT, json=bundle)
    assert resp.status_code == 201, resp.text[:600]
    body = resp.json()

    assert _bundle_warnings(body["messages"], case["local_ref"]) == expected
    # The summary counts what the messages carry, and the closing note is last.
    assert body["summary"]["warnings"] == sum(1 for m in body["messages"] if m["level"] == "warning")
    assert body["messages"][-1]["code"] == "ingestion_succeeded"
    # Warnings never become refusals.
    assert body["summary"]["records_imported"] == 1


@pytest.mark.parametrize("kind", sorted(CASES))
def test_bundle_dry_run_reports_the_warnings_submit_reports_and_writes_nothing(
    client, db_session, kind: str
) -> None:
    case = CASES[kind]
    bundle = case["bundle"]()
    expected = _direct_warnings(client, case["url"], case["record"](bundle))
    assert {code for code, _m, _f in expected} >= set(case["required"]), expected

    thermo_rows = select(func.count()).select_from(Thermo)
    before = db_session.scalar(thermo_rows)
    resp = client.post(DRY_RUN, json=bundle)
    assert resp.status_code == 200, resp.text[:600]
    body = resp.json()

    assert body["bundle_valid"] is True
    dry_run_warnings = _bundle_warnings(body["messages"], case["local_ref"])
    assert dry_run_warnings == expected
    # Each warning is reported once: the preview's own messages and submit's
    # are not both appended.
    all_codes = [m["code"] for m in body["messages"] if m["level"] == "warning"]
    for code in case["required"]:
        assert all_codes.count(code) == [c for c, _m, _f in expected].count(code), code
    assert body["summary"]["warnings"] == sum(1 for m in body["messages"] if m["level"] == "warning")
    assert body["summary"]["errors"] == 0
    # Nothing was written by the rehearsal.
    assert db_session.scalar(thermo_rows) == before

    # ...and the real submit then says exactly what the dry run said.
    submitted = client.post(SUBMIT, json=bundle)
    assert submitted.status_code == 201, submitted.text[:600]
    assert _bundle_warnings(submitted.json()["messages"], case["local_ref"]) == dry_run_warnings


def test_a_refused_bundle_reports_the_refusal_and_not_partial_warnings(client) -> None:
    """A refusal is the verdict; warnings gathered before it are not reported."""
    bundle = _thermo_bundle()
    del bundle["records"]["thermo_uploads"][0]["enthalpy_reference_kind"]
    resp = client.post(DRY_RUN, json=bundle)
    assert resp.status_code == 200, resp.text[:600]
    body = resp.json()
    assert body["bundle_valid"] is False
    assert [m for m in body["messages"] if m["level"] == "error"]
    assert _bundle_warnings(body["messages"], "thermo_uploads[0]") == []
