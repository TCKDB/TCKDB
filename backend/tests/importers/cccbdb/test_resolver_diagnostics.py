"""Offline tests for the CCCBDB resolver-diagnostic tool.

Everything here uses synthetic HTML and a hand-rolled
:class:`FakeTransport`. No network. No CCCBDB. No fixtures from the
live site.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from app.importers.cccbdb.diagnostics.classifier import (
    Classification,
    classify_html,
    extract_title,
)
from app.importers.cccbdb.diagnostics.form_discovery import discover_forms
from app.importers.cccbdb.diagnostics.runner import (
    PILOT_TARGETS,
    DiagnosticTarget,
    Transport,
    TransportResponse,
    run_diagnostics,
)

# ---------------------------------------------------------------------------
# Synthetic HTML samples
# ---------------------------------------------------------------------------


_FORMULA_ENTRY_HTML = """
<html><head><title>CCCBDB: enter molecule</title></head><body>
<h1>One molecule with all properties</h1>
<form method="POST" action="getformx.asp">
  <input type="hidden" name="sid" value="abc123">
  <input type="text" name="formula" value="">
  <select name="route">
    <option value="experimental">Experimental</option>
    <option value="computed">Computed</option>
  </select>
  <input type="submit" name="go" value="Submit">
</form>
<p>Please enter the chemical formula</p>
</body></html>
"""

_MOLECULE_DATA_HTML = """
<html><head><title>All data for one species: H2O</title></head><body>
<h2>All data for one species</h2>
<table>
  <tr><th>InChI</th><td>InChI=1S/H2O/h1H2</td></tr>
  <tr><th>SMILES</th><td>O</td></tr>
</table>
<table><tr><th>Property</th><th>Value</th></tr>
  <tr><td>Hf(298 K)</td><td>-241.826 kJ/mol</td></tr>
</table>
</body></html>
"""

_PROPERTY_TABLE_HTML = """
<html><head><title>Experimental Dipoles</title></head><body>
<h2>Experimental Dipoles</h2>
<p>Dipole moments in Debye</p>
<table>
<tr><th>Molecule</th><th>tot</th></tr>
<tr><td>H2O</td><td>1.855</td></tr>
</table>
</body></html>
"""

_RATE_LIMIT_HTML = """
<html><head><title>Just a moment...</title></head><body>
<h1>Error 1015</h1>
<p>You are being rate limited. Cloudflare blocked this request.</p>
</body></html>
"""


# ---------------------------------------------------------------------------
# Classifier tests
# ---------------------------------------------------------------------------


class TestClassifier:
    def test_formula_entry_page(self):
        result = classify_html(
            _FORMULA_ENTRY_HTML,
            attempted_url="https://cccbdb.nist.gov/exp1x.asp",
        )
        assert result.classification == Classification.formula_entry_page

    def test_molecule_data_page(self):
        result = classify_html(
            _MOLECULE_DATA_HTML,
            attempted_url="https://cccbdb.nist.gov/alldata2x.asp?casno=7732185",
        )
        assert result.classification == Classification.molecule_data_page

    def test_property_table_page(self):
        result = classify_html(
            _PROPERTY_TABLE_HTML,
            attempted_url="https://cccbdb.nist.gov/diplistx.asp",
        )
        assert result.classification == Classification.property_table_page

    def test_rate_limit_or_error_page(self):
        result = classify_html(
            _RATE_LIMIT_HTML,
            attempted_url="https://cccbdb.nist.gov/alldata2x.asp?casno=12385136",
        )
        assert (
            result.classification == Classification.rate_limit_or_error_page
        )
        assert "rate" in result.reason or "cloudflare" in result.reason.lower()

    def test_redirect_landing_when_form_served_at_different_url(self):
        """An attempted alldata2x.asp?casno=... that comes back with
        the formula-entry form AT exp1x.asp must classify as
        redirect_landing_page, not formula_entry_page — that's the
        whole bug the diagnostic exists to characterize."""

        result = classify_html(
            _FORMULA_ENTRY_HTML,
            attempted_url="https://cccbdb.nist.gov/alldata2x.asp?casno=12385136",
            final_url="https://cccbdb.nist.gov/exp1x.asp",
        )
        assert result.classification == Classification.redirect_landing_page
        assert "formula-entry form" in result.reason

    def test_unknown_when_no_markers_match(self):
        result = classify_html(
            "<html><body><p>some unrelated page</p></body></html>",
            attempted_url="https://example.invalid/x",
        )
        assert result.classification == Classification.unknown

    def test_extract_title(self):
        assert (
            extract_title(_MOLECULE_DATA_HTML)
            == "All data for one species: H2O"
        )
        assert extract_title("<html></html>") is None


# ---------------------------------------------------------------------------
# Form discovery tests
# ---------------------------------------------------------------------------


class TestFormDiscovery:
    def test_extracts_method_action_and_fields(self):
        forms = discover_forms(_FORMULA_ENTRY_HTML)
        assert len(forms) == 1
        form = forms[0]
        assert form.method == "POST"
        assert form.action == "getformx.asp"
        names = {f.name for f in form.fields}
        assert {"sid", "formula", "route", "go"} <= names

    def test_hidden_field_default_value_preserved(self):
        forms = discover_forms(_FORMULA_ENTRY_HTML)
        named = forms[0].named_fields()
        assert named["sid"].input_type == "hidden"
        assert named["sid"].default_value == "abc123"

    def test_select_options_captured(self):
        forms = discover_forms(_FORMULA_ENTRY_HTML)
        route = forms[0].named_fields()["route"]
        assert route.kind == "select"
        assert "experimental" in route.options
        assert "computed" in route.options

    def test_no_forms_returns_empty_list(self):
        assert discover_forms("<html><body><p>nothing</p></body></html>") == []


# ---------------------------------------------------------------------------
# Runner tests (fake transport)
# ---------------------------------------------------------------------------


@dataclass
class FakeTransport(Transport):
    """Test transport that returns canned responses by URL.

    ``calls`` records every (method, url, payload) tuple so tests can
    assert on the orchestration order.
    """

    get_responses: dict[str, TransportResponse] = field(default_factory=dict)
    post_responses: dict[str, TransportResponse] = field(default_factory=dict)
    calls: list[tuple[str, str, dict[str, str] | None]] = field(
        default_factory=list
    )
    default_response: TransportResponse = field(
        default_factory=lambda: TransportResponse(
            status_code=200,
            final_url=None,
            text=_FORMULA_ENTRY_HTML,
            error=None,
        )
    )

    def get(
        self, url: str, *, params: dict[str, str] | None = None
    ) -> TransportResponse:
        self.calls.append(("GET", url, params))
        return self.get_responses.get(url, self.default_response)

    def post(
        self, url: str, *, data: dict[str, str]
    ) -> TransportResponse:
        self.calls.append(("POST", url, data))
        return self.post_responses.get(url, self.default_response)


_EXP1X = "https://cccbdb.nist.gov/exp1x.asp"
_ALLDATA2X = "https://cccbdb.nist.gov/alldata2x.asp"


def _h2o_only() -> tuple[DiagnosticTarget, ...]:
    return (PILOT_TARGETS[0],)


class TestRunnerOrchestration:
    def test_records_every_applicable_strategy_per_target(self):
        transport = FakeTransport()
        transport.get_responses[_EXP1X] = TransportResponse(
            status_code=200,
            final_url=_EXP1X,
            text=_FORMULA_ENTRY_HTML,
        )
        report = run_diagnostics(
            _h2o_only(), transport, sleep_seconds=0.0
        )
        # H2O has formula + name + casno + inchikey, so all four
        # strategies fire: direct_alldata2x_casno, exp1x_get_with_formula,
        # exp1x_form_post, exp1x_form_post_with_name.
        strategies = [r.strategy for r in report.records]
        assert strategies == [
            "direct_alldata2x_casno",
            "exp1x_get_with_formula",
            "exp1x_form_post",
            "exp1x_form_post_with_name",
        ]

    def test_records_final_url_and_classification(self):
        """An ``alldata2x.asp?casno=...`` request that gets redirected
        to ``exp1x.asp`` should land as redirect_landing_page in the
        report. This is the diagnostic's headline case."""

        transport = FakeTransport()
        transport.get_responses[_EXP1X] = TransportResponse(
            status_code=200,
            final_url=_EXP1X,
            text=_FORMULA_ENTRY_HTML,
        )
        transport.get_responses[_ALLDATA2X] = TransportResponse(
            status_code=200,
            final_url=_EXP1X,
            text=_FORMULA_ENTRY_HTML,
        )
        report = run_diagnostics(
            _h2o_only(), transport, sleep_seconds=0.0
        )
        direct = next(
            r
            for r in report.records
            if r.strategy == "direct_alldata2x_casno"
        )
        assert direct.final_url == _EXP1X
        assert direct.classification == Classification.redirect_landing_page
        assert direct.title == "CCCBDB: enter molecule"
        assert direct.content_sha256 and len(direct.content_sha256) == 64

    def test_classifies_real_molecule_data_response(self):
        transport = FakeTransport()
        transport.get_responses[_EXP1X] = TransportResponse(
            status_code=200,
            final_url=_EXP1X,
            text=_FORMULA_ENTRY_HTML,
        )
        # Simulate ``alldata2x.asp?casno=H2O`` actually returning data.
        transport.get_responses[_ALLDATA2X] = TransportResponse(
            status_code=200,
            final_url=f"{_ALLDATA2X}?casno=7732185",
            text=_MOLECULE_DATA_HTML,
        )
        report = run_diagnostics(
            _h2o_only(), transport, sleep_seconds=0.0
        )
        direct = next(
            r
            for r in report.records
            if r.strategy == "direct_alldata2x_casno"
        )
        assert direct.classification == Classification.molecule_data_page

    def test_classifies_cloudflare_rate_limit_response(self):
        transport = FakeTransport()
        transport.get_responses[_EXP1X] = TransportResponse(
            status_code=429,
            final_url=_EXP1X,
            text=_RATE_LIMIT_HTML,
        )
        transport.get_responses[_ALLDATA2X] = TransportResponse(
            status_code=429,
            final_url=_ALLDATA2X,
            text=_RATE_LIMIT_HTML,
        )
        report = run_diagnostics(
            _h2o_only(), transport, sleep_seconds=0.0
        )
        for rec in report.records:
            # The form-POST strategies short-circuit (no form found
            # in a rate-limit body), so they don't appear at all.
            assert rec.classification == Classification.rate_limit_or_error_page

    def test_form_post_uses_discovered_fields(self):
        """The POST strategy must include hidden-field defaults from
        the discovered form, not just the formula."""

        transport = FakeTransport()
        transport.get_responses[_EXP1X] = TransportResponse(
            status_code=200,
            final_url=_EXP1X,
            text=_FORMULA_ENTRY_HTML,
        )
        run_diagnostics(_h2o_only(), transport, sleep_seconds=0.0)
        post_calls = [
            (url, data) for method, url, data in transport.calls if method == "POST"
        ]
        assert post_calls, "expected at least one POST"
        url, data = post_calls[0]
        # Hidden field carried through.
        assert data.get("sid") == "abc123"
        # Formula went into the discovered text input.
        assert data.get("formula") == "H2O"
        # The action was resolved against exp1x.asp.
        assert url == "https://cccbdb.nist.gov/getformx.asp"

    def test_runner_handles_target_with_no_casno(self):
        """A target with ``casno=None`` must produce no
        direct_alldata2x_casno record (cleanly skipped, not crash)."""

        transport = FakeTransport()
        transport.get_responses[_EXP1X] = TransportResponse(
            status_code=200, final_url=_EXP1X, text=_FORMULA_ENTRY_HTML
        )
        target = DiagnosticTarget(
            molecule_key="x", formula="X", name="X-test"
        )
        report = run_diagnostics(
            (target,), transport, sleep_seconds=0.0
        )
        strategies = {r.strategy for r in report.records}
        assert "direct_alldata2x_casno" not in strategies
        assert "exp1x_get_with_formula" in strategies


# ---------------------------------------------------------------------------
# Report JSON shape
# ---------------------------------------------------------------------------


class TestReportSerialization:
    def test_to_json_round_trips(self, tmp_path: Path):
        transport = FakeTransport()
        transport.get_responses[_EXP1X] = TransportResponse(
            status_code=200, final_url=_EXP1X, text=_FORMULA_ENTRY_HTML
        )
        report = run_diagnostics(
            _h2o_only(), transport, sleep_seconds=0.0
        )
        path = tmp_path / "report.json"
        path.write_text(json.dumps(report.to_json(), indent=2))
        loaded = json.loads(path.read_text())
        assert loaded["diagnostic_version"]
        assert loaded["records"]
        for rec in loaded["records"]:
            # The schema documented in the prompt: every record carries
            # these keys, even when null.
            assert "molecule_key" in rec
            assert "strategy" in rec
            assert "attempted_url" in rec
            assert "classification" in rec
            assert "content_sha256" in rec
            assert "diagnostic_reason" in rec


# ---------------------------------------------------------------------------
# Pilot allowlist sanity
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("target", PILOT_TARGETS, ids=lambda t: t.molecule_key)
def test_pilot_targets_are_small_and_typed(target: DiagnosticTarget):
    # All pilot molecules have at least a formula; the prompt is
    # explicit about a "tiny allowlist". If this set grows beyond 6,
    # that's a signal a maintainer is drifting toward production
    # crawling — which this tool deliberately is not.
    assert len(PILOT_TARGETS) <= 6
    assert target.formula
