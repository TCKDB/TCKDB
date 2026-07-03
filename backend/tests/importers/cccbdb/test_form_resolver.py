"""Tests for the CCCBDB form-page POST resolver.

The resolver is exercised with a fake :class:`SessionLike` so the
test suite never touches the network. Live smoke tests are gated
behind the ``TCKDB_CCCBDB_LIVE_FORM_TESTS`` env var.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from app.importers.cccbdb.form_resolver import (
    FormQueueRecord,
    FormResolverConfig,
    SessionResponse,
    discover_form,
    load_queue_file,
    run_form_resolver_queue,
)

FIXTURES_DIR = (
    Path(__file__).resolve().parents[3]
    / "app"
    / "importers"
    / "cccbdb"
    / "fixtures"
)


def _load(name: str) -> str:
    return (FIXTURES_DIR / name).read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Fake transport
# ---------------------------------------------------------------------------


@dataclass
class FakeSession:
    """In-memory session: each (method, url) returns a canned response.

    Also tracks the order of calls and the data each POST sent, so
    tests can assert on cookie-equivalent flow + form-field shape.
    """

    canned_get: dict[str, SessionResponse] = field(default_factory=dict)
    canned_post: dict[str, SessionResponse] = field(default_factory=dict)
    calls: list[tuple[str, str, dict | None]] = field(default_factory=list)
    # Simulate session state by tagging cookies on each call.
    _cookies: dict[str, str] = field(default_factory=dict)

    def get(self, url: str, *, timeout: float | None = None) -> SessionResponse:
        self.calls.append(("GET", url, None))
        self._cookies["ASPSESSIONID"] = "fake-session-id"
        return self.canned_get.get(
            url,
            SessionResponse(text="", status_code=404, url=url),
        )

    def post(
        self, url: str, *, data: dict[str, str], timeout: float | None = None
    ) -> SessionResponse:
        self.calls.append(("POST", url, dict(data)))
        return self.canned_post.get(
            url,
            SessionResponse(text="", status_code=404, url=url),
        )

    def cookie_count(self) -> int:
        return len(self._cookies)


# ---------------------------------------------------------------------------
# Form discovery
# ---------------------------------------------------------------------------


class TestFormDiscovery:
    def test_discovers_getformx_form_on_ea1x(self):
        html = _load("form_entry_ea1x.html")
        form = discover_form(html, base_url="https://cccbdb.nist.gov/ea1x.asp")
        assert form is not None
        assert form.action_url == "https://cccbdb.nist.gov/getformx.asp"
        assert form.method == "POST"
        assert form.formula_field == "formula"

    def test_no_form_returns_none(self):
        form = discover_form(
            "<html><body><p>no form here</p></body></html>",
            base_url="https://cccbdb.nist.gov/",
        )
        assert form is None

    def test_form_without_formula_field_is_skipped(self):
        html = (
            '<html><body>'
            '<FORM ACTION="getformx.asp" METHOD="post">'
            '<input type="submit" name="go" value="Go">'
            '</FORM>'
            '</body></html>'
        )
        assert discover_form(html, base_url="https://cccbdb.nist.gov/") is None


# ---------------------------------------------------------------------------
# Resolver: happy path
# ---------------------------------------------------------------------------


def _h2o_session() -> FakeSession:
    return FakeSession(
        canned_get={
            "https://cccbdb.nist.gov/ea1x.asp": SessionResponse(
                text=_load("form_entry_ea1x.html"),
                status_code=200,
                url="https://cccbdb.nist.gov/ea1x.asp",
            )
        },
        canned_post={
            "https://cccbdb.nist.gov/getformx.asp": SessionResponse(
                text=_load("form_result_ea_h2o.html"),
                status_code=200,
                url="https://cccbdb.nist.gov/ea2x.asp",
            )
        },
    )


def _record(species_key: str = "h2o", formula: str = "H2O") -> FormQueueRecord:
    return FormQueueRecord(
        species_key=species_key,
        formula=formula,
        name="Water" if species_key == "h2o" else None,
        target_kind="atomization_energy",
        entry_url="https://cccbdb.nist.gov/ea1x.asp",
    )


class TestResolverHappyPath:
    def test_accepts_form_result_data_page(self, tmp_path):
        session = _h2o_session()
        cfg = FormResolverConfig(
            output_dir=tmp_path,
            session_factory=lambda: session,
            sleep_seconds=0,
        )
        summary = run_form_resolver_queue([_record()], cfg)
        assert summary.accepted == 1
        assert summary.rejected == 0
        result = summary.results[0]
        assert result.accepted_as_data is True
        assert result.classification == "form_result_data_page"
        assert result.final_url == "https://cccbdb.nist.gov/ea2x.asp"
        assert result.form_action_url == "https://cccbdb.nist.gov/getformx.asp"

    def test_post_carries_formula_field(self, tmp_path):
        session = _h2o_session()
        cfg = FormResolverConfig(
            output_dir=tmp_path,
            session_factory=lambda: session,
            sleep_seconds=0,
        )
        run_form_resolver_queue([_record()], cfg)
        post_calls = [c for c in session.calls if c[0] == "POST"]
        assert len(post_calls) == 1
        _, url, data = post_calls[0]
        assert url == "https://cccbdb.nist.gov/getformx.asp"
        assert data == {"formula": "H2O"}

    def test_session_state_preserved_across_get_and_post(self, tmp_path):
        """A real ``requests.Session`` propagates cookies; the fake
        session models this with a ``_cookies`` dict that grows on
        GET. After both calls finish, the same session instance must
        still carry the cookies — the resolver must NOT spin up a
        fresh session per request."""

        session = _h2o_session()
        cfg = FormResolverConfig(
            output_dir=tmp_path,
            session_factory=lambda: session,
            sleep_seconds=0,
        )
        run_form_resolver_queue([_record()], cfg)
        assert session.cookie_count() == 1

    def test_archives_accepted_raw_html(self, tmp_path):
        session = _h2o_session()
        cfg = FormResolverConfig(
            output_dir=tmp_path,
            session_factory=lambda: session,
            sleep_seconds=0,
        )
        run_form_resolver_queue([_record()], cfg)
        raw_files = list((tmp_path / "raw_html").iterdir())
        assert len(raw_files) == 1
        # Filename shape: form_<target_kind>_<species_key>_<sha12>.html
        assert raw_files[0].name.startswith("form_atomization_energy_h2o_")
        assert raw_files[0].name.endswith(".html")

    def test_writes_parsed_json(self, tmp_path):
        session = _h2o_session()
        cfg = FormResolverConfig(
            output_dir=tmp_path,
            session_factory=lambda: session,
            sleep_seconds=0,
        )
        run_form_resolver_queue([_record()], cfg)
        parsed_files = list((tmp_path / "parsed").iterdir())
        assert len(parsed_files) == 1
        data = json.loads(parsed_files[0].read_text(encoding="utf-8"))
        assert data["target_kind"] == "atomization_energy"
        assert data["raw_units"] == "kJ/mol"
        assert len(data["rows"]) == 1
        assert data["rows"][0]["formula"] == "H2O"


# ---------------------------------------------------------------------------
# Resolver: rejection paths
# ---------------------------------------------------------------------------


def _rejecting_session(
    result_fixture: str, final_url: str = "https://cccbdb.nist.gov/getformx.asp"
) -> FakeSession:
    return FakeSession(
        canned_get={
            "https://cccbdb.nist.gov/ea1x.asp": SessionResponse(
                text=_load("form_entry_ea1x.html"),
                status_code=200,
                url="https://cccbdb.nist.gov/ea1x.asp",
            )
        },
        canned_post={
            "https://cccbdb.nist.gov/getformx.asp": SessionResponse(
                text=_load(result_fixture),
                status_code=200,
                url=final_url,
            )
        },
    )


class TestResolverRejectionPaths:
    def test_species_selection_page_is_rejected(self, tmp_path):
        session = _rejecting_session(
            "form_result_choose_c2h6o.html",
            final_url="https://cccbdb.nist.gov/choosex.asp",
        )
        cfg = FormResolverConfig(
            output_dir=tmp_path,
            session_factory=lambda: session,
            sleep_seconds=0,
        )
        summary = run_form_resolver_queue(
            [_record(species_key="c2h6o", formula="C2H6O")], cfg
        )
        assert summary.accepted == 0
        assert summary.rejected == 1
        result = summary.results[0]
        assert result.accepted_as_data is False
        assert result.classification == "species_selection_page"
        # No archive written for rejected pages by default.
        assert result.raw_html_path is None
        assert result.parsed_json_path is None

    def test_rate_limit_page_is_rejected_and_stops_queue(self, tmp_path):
        session = _rejecting_session("form_result_rate_limit.html")
        cfg = FormResolverConfig(
            output_dir=tmp_path,
            session_factory=lambda: session,
            sleep_seconds=0,
            stop_after_rate_limit_errors=1,
        )
        summary = run_form_resolver_queue(
            [_record(species_key="h2o"), _record(species_key="ch4", formula="CH4")],
            cfg,
        )
        # The first record fires; the second is held back because
        # the rate-limit strike crossed the configured threshold.
        assert summary.records_seen == 1
        assert summary.rejected == 1
        assert summary.stopped_after_rate_limit is True

    def test_save_rejected_html_flag_archives_rejection(self, tmp_path):
        session = _rejecting_session(
            "form_result_choose_c2h6o.html",
            final_url="https://cccbdb.nist.gov/choosex.asp",
        )
        cfg = FormResolverConfig(
            output_dir=tmp_path,
            session_factory=lambda: session,
            sleep_seconds=0,
            save_rejected_html=True,
        )
        summary = run_form_resolver_queue(
            [_record(species_key="c2h6o", formula="C2H6O")], cfg
        )
        result = summary.results[0]
        assert result.rejected_html_path is not None
        assert (tmp_path / "rejected_html").exists()
        assert any((tmp_path / "rejected_html").iterdir())

    def test_unknown_page_is_rejected_by_default(self, tmp_path):
        session = FakeSession(
            canned_get={
                "https://cccbdb.nist.gov/ea1x.asp": SessionResponse(
                    text=_load("form_entry_ea1x.html"),
                    status_code=200,
                    url="https://cccbdb.nist.gov/ea1x.asp",
                )
            },
            canned_post={
                "https://cccbdb.nist.gov/getformx.asp": SessionResponse(
                    text="<html><body><p>nothing useful here</p></body></html>",
                    status_code=200,
                    url="https://cccbdb.nist.gov/getformx.asp",
                )
            },
        )
        cfg = FormResolverConfig(
            output_dir=tmp_path,
            session_factory=lambda: session,
            sleep_seconds=0,
        )
        summary = run_form_resolver_queue([_record()], cfg)
        assert summary.rejected == 1
        assert summary.results[0].classification == "unknown"
        assert summary.results[0].accepted_as_data is False

    def test_unknown_page_accepted_when_allow_unknown(self, tmp_path):
        session = FakeSession(
            canned_get={
                "https://cccbdb.nist.gov/ea1x.asp": SessionResponse(
                    text=_load("form_entry_ea1x.html"),
                    status_code=200,
                    url="https://cccbdb.nist.gov/ea1x.asp",
                )
            },
            canned_post={
                "https://cccbdb.nist.gov/getformx.asp": SessionResponse(
                    text="<html><body><p>nothing useful here</p></body></html>",
                    status_code=200,
                    url="https://cccbdb.nist.gov/getformx.asp",
                )
            },
        )
        cfg = FormResolverConfig(
            output_dir=tmp_path,
            session_factory=lambda: session,
            sleep_seconds=0,
            allow_unknown=True,
        )
        summary = run_form_resolver_queue([_record()], cfg)
        assert summary.accepted == 1
        # No parsed JSON (target_kind supported but no real data).
        # We still archive the raw HTML.
        assert (tmp_path / "raw_html").exists()


# ---------------------------------------------------------------------------
# Manifest merge
# ---------------------------------------------------------------------------


class TestManifestMerge:
    def test_manifest_records_appended(self, tmp_path):
        session = _h2o_session()
        cfg = FormResolverConfig(
            output_dir=tmp_path,
            session_factory=lambda: session,
            sleep_seconds=0,
        )
        run_form_resolver_queue([_record()], cfg)

        manifest = json.loads(
            (tmp_path / "manifest.json").read_text(encoding="utf-8")
        )
        assert manifest["records"]
        rec = next(
            r for r in manifest["records"]
            if r["species_key"] == "h2o"
        )
        assert rec["target_kind"] == "atomization_energy"
        assert rec["accepted_as_data"] is True
        assert rec["form_action_url"] == "https://cccbdb.nist.gov/getformx.asp"
        assert rec["resolver_strategy"] == "requests_session_form_post"

    def test_manifest_merge_preserves_prior_entries(self, tmp_path):
        """A second resolver run on a different species must NOT
        wipe the first species' entry from manifest.json."""

        # First run.
        session = _h2o_session()
        cfg = FormResolverConfig(
            output_dir=tmp_path,
            session_factory=lambda: session,
            sleep_seconds=0,
        )
        run_form_resolver_queue([_record()], cfg)

        # Second run, different species, different session-canned response
        # but same fixture (we only care about manifest-merge semantics).
        session2 = FakeSession(
            canned_get={
                "https://cccbdb.nist.gov/ea1x.asp": SessionResponse(
                    text=_load("form_entry_ea1x.html"),
                    status_code=200,
                    url="https://cccbdb.nist.gov/ea1x.asp",
                )
            },
            canned_post={
                "https://cccbdb.nist.gov/getformx.asp": SessionResponse(
                    text=_load("form_result_ea_h2o.html"),
                    status_code=200,
                    url="https://cccbdb.nist.gov/ea2x.asp",
                )
            },
        )
        cfg2 = FormResolverConfig(
            output_dir=tmp_path,
            session_factory=lambda: session2,
            sleep_seconds=0,
        )
        run_form_resolver_queue([_record(species_key="ch4", formula="CH4")], cfg2)

        manifest = json.loads(
            (tmp_path / "manifest.json").read_text(encoding="utf-8")
        )
        species_keys = {r["species_key"] for r in manifest["records"]}
        assert species_keys == {"h2o", "ch4"}


# ---------------------------------------------------------------------------
# Queue file loading + CLI
# ---------------------------------------------------------------------------


class TestQueueLoading:
    def test_load_queue_file(self, tmp_path):
        path = tmp_path / "queue.json"
        path.write_text(
            json.dumps(
                {
                    "records": [
                        {
                            "species_key": "h2",
                            "formula": "H2",
                            "name": "Hydrogen diatomic",
                            "target_kind": "atomization_energy",
                            "entry_url": "https://cccbdb.nist.gov/ea1x.asp",
                        }
                    ]
                }
            )
        )
        records = load_queue_file(path)
        assert len(records) == 1
        assert records[0].species_key == "h2"

    def test_missing_required_field_raises(self, tmp_path):
        path = tmp_path / "bad.json"
        path.write_text(
            json.dumps({"records": [{"formula": "H2"}]})
        )
        with pytest.raises(ValueError, match="required fields"):
            load_queue_file(path)

    def test_missing_records_key_raises(self, tmp_path):
        path = tmp_path / "wrong.json"
        path.write_text(json.dumps({"queue": []}))
        with pytest.raises(ValueError, match="records"):
            load_queue_file(path)


class TestCli:
    def test_cli_success_path(self, tmp_path, monkeypatch):
        # Patch the session factory at the module level so the CLI
        # uses our FakeSession instead of opening a real connection.
        session = _h2o_session()

        from app.importers.cccbdb import form_resolver as mod

        original = mod.RequestsSession

        class _FakeFactory:
            def __init__(self, *args, **kwargs):
                pass

            def get(self, url, *, timeout=None):
                return session.get(url)

            def post(self, url, *, data, timeout=None):
                return session.post(url, data=data)

        monkeypatch.setattr(mod, "RequestsSession", _FakeFactory)

        queue_path = tmp_path / "queue.json"
        queue_path.write_text(
            json.dumps(
                {
                    "records": [
                        {
                            "species_key": "h2o",
                            "formula": "H2O",
                            "name": "Water",
                            "target_kind": "atomization_energy",
                            "entry_url": "https://cccbdb.nist.gov/ea1x.asp",
                        }
                    ]
                }
            )
        )

        from scripts.cccbdb_resolve_form_page import main

        rc = main(
            [
                "--queue-file", str(queue_path),
                "--output-dir", str(tmp_path / "out"),
                "--sleep-seconds", "0",
                "--max-pages", "1",
            ]
        )
        assert rc == 0
        manifest = json.loads(
            (tmp_path / "out" / "manifest.json").read_text(encoding="utf-8")
        )
        assert any(
            r["accepted_as_data"] is True
            for r in manifest["records"]
        )

        monkeypatch.setattr(mod, "RequestsSession", original)

    def test_cli_missing_queue_returns_2(self, tmp_path):
        from scripts.cccbdb_resolve_form_page import main

        rc = main(
            [
                "--queue-file", str(tmp_path / "nope.json"),
                "--output-dir", str(tmp_path / "out"),
            ]
        )
        assert rc == 2

    def test_cli_bad_queue_returns_2(self, tmp_path):
        queue_path = tmp_path / "bad.json"
        queue_path.write_text('{"queue": []}')
        from scripts.cccbdb_resolve_form_page import main

        rc = main(
            [
                "--queue-file", str(queue_path),
                "--output-dir", str(tmp_path / "out"),
            ]
        )
        assert rc == 2

    def test_cli_empty_records_returns_2(self, tmp_path):
        queue_path = tmp_path / "empty.json"
        queue_path.write_text('{"records": []}')
        from scripts.cccbdb_resolve_form_page import main

        rc = main(
            [
                "--queue-file", str(queue_path),
                "--output-dir", str(tmp_path / "out"),
            ]
        )
        assert rc == 2


# ---------------------------------------------------------------------------
# No-DB invariant
# ---------------------------------------------------------------------------


def test_form_resolver_module_has_no_orm_sessions():
    """Structural guarantee — the resolver must never import a SQLAlchemy
    session or engine. Persistence is a separate workflow layer."""

    from app.importers.cccbdb import form_resolver

    for name in (
        "Session", "sessionmaker", "create_engine", "scoped_session",
    ):
        assert name not in form_resolver.__dict__, (
            f"form_resolver leaked DB symbol {name!r}"
        )


# ---------------------------------------------------------------------------
# Optional live smoke test
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    os.environ.get("TCKDB_CCCBDB_LIVE_FORM_TESTS") != "1",
    reason=(
        "TCKDB_CCCBDB_LIVE_FORM_TESTS not set; skipping live network "
        "smoke test (CCCBDB rate-limits aggressively)"
    ),
)
def test_live_form_resolver_h2o(tmp_path):  # pragma: no cover — live
    """End-to-end live test: GET ea1x.asp, POST formula=H2O, parse the
    result. Only runs when explicitly opted in."""

    cfg = FormResolverConfig(
        output_dir=tmp_path,
        sleep_seconds=5.0,
        max_pages=1,
    )
    summary = run_form_resolver_queue(
        [
            FormQueueRecord(
                species_key="h2o",
                formula="H2O",
                name="Water",
                target_kind="atomization_energy",
                entry_url="https://cccbdb.nist.gov/ea1x.asp",
            )
        ],
        cfg,
    )
    assert summary.accepted == 1
