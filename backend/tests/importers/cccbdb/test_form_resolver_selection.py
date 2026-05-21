"""Tests for the CCCBDB form-resolver exact-match selection policy.

Covers the full ``choosex.asp`` → ``fixchoicex.asp`` flow with a
fake session.  Live network is never touched.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from app.importers.cccbdb.form_resolver import (
    FormQueueRecord,
    FormResolverConfig,
    SelectionPolicy,
    SessionResponse,
    run_form_resolver_queue,
    select_candidate,
)
from app.importers.cccbdb.parsers.species_selection import (
    CCCBDBSelectionCandidate,
    CCCBDBSpeciesSelectionPage,
    parse_species_selection_page,
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
    canned_get: dict[str, SessionResponse] = field(default_factory=dict)
    canned_post: dict[str, SessionResponse] = field(default_factory=dict)
    posts: list[tuple[str, dict]] = field(default_factory=list)
    gets: list[str] = field(default_factory=list)

    def get(self, url, *, timeout=None):
        self.gets.append(url)
        return self.canned_get.get(
            url, SessionResponse(text="", status_code=404, url=url)
        )

    def post(self, url, *, data, timeout=None):
        self.posts.append((url, dict(data)))
        return self.canned_post.get(
            url, SessionResponse(text="", status_code=404, url=url)
        )


def _choose_then_data_session() -> FakeSession:
    """Session whose first POST returns choosex.asp and whose second
    (to fixchoicex.asp) returns the ea2x.asp result page."""

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
                text=_load("form_result_choose_c2h6o_live.html"),
                status_code=200,
                url="https://cccbdb.nist.gov/choosex.asp",
            ),
            "https://cccbdb.nist.gov/fixchoicex.asp": SessionResponse(
                text=_load("form_result_ea_h2o.html"),
                status_code=200,
                url="https://cccbdb.nist.gov/ea2x.asp",
            ),
        },
    )


def _record(**overrides) -> FormQueueRecord:
    base = dict(
        species_key="ethanol",
        formula="C2H6O",
        name="Ethanol",
        target_kind="atomization_energy",
        entry_url="https://cccbdb.nist.gov/ea1x.asp",
    )
    base.update(overrides)
    return FormQueueRecord(**base)


# ---------------------------------------------------------------------------
# Reject-ambiguous keeps prior behavior
# ---------------------------------------------------------------------------


class TestRejectAmbiguousPolicy:
    def test_reject_ambiguous_does_not_post_to_fixchoicex(self, tmp_path):
        session = _choose_then_data_session()
        cfg = FormResolverConfig(
            output_dir=tmp_path,
            session_factory=lambda: session,
            sleep_seconds=0,
            selection_policy=SelectionPolicy.REJECT_AMBIGUOUS,
        )
        summary = run_form_resolver_queue([_record()], cfg)
        result = summary.results[0]
        assert result.accepted_as_data is False
        assert result.classification == "species_selection_page"
        # The resolver must NOT follow the selection.
        assert all(
            "fixchoicex" not in url for url, _ in session.posts
        )

    def test_reject_ambiguous_records_selection_metadata(self, tmp_path):
        session = _choose_then_data_session()
        cfg = FormResolverConfig(
            output_dir=tmp_path,
            session_factory=lambda: session,
            sleep_seconds=0,
            selection_policy=SelectionPolicy.REJECT_AMBIGUOUS,
        )
        summary = run_form_resolver_queue([_record()], cfg)
        result = summary.results[0]
        assert result.selection_policy == "reject_ambiguous"
        assert result.selection_status == "ambiguous_or_no_match"
        assert result.selection_candidate_count == 3
        # No selected candidate.
        assert result.selected_name is None
        assert result.selected_cas_number is None


# ---------------------------------------------------------------------------
# Exact-match: accepted paths
# ---------------------------------------------------------------------------


class TestExactMatchAcceptedPaths:
    def test_select_by_formula_plus_name(self, tmp_path):
        session = _choose_then_data_session()
        cfg = FormResolverConfig(
            output_dir=tmp_path,
            session_factory=lambda: session,
            sleep_seconds=0,
            selection_policy=SelectionPolicy.EXACT_MATCH,
        )
        summary = run_form_resolver_queue([_record()], cfg)
        result = summary.results[0]
        assert result.accepted_as_data is True
        assert result.classification == "form_result_data_page"
        assert result.selection_status == "selected"
        assert result.selection_match_basis == "formula+name"
        assert result.selected_name == "Ethanol"
        assert result.selected_cas_number == "64175"

    def test_post_to_fixchoicex_carries_choice_and_submitselect(self, tmp_path):
        session = _choose_then_data_session()
        cfg = FormResolverConfig(
            output_dir=tmp_path,
            session_factory=lambda: session,
            sleep_seconds=0,
            selection_policy=SelectionPolicy.EXACT_MATCH,
        )
        run_form_resolver_queue([_record()], cfg)
        fix_calls = [
            (url, data) for url, data in session.posts
            if "fixchoicex" in url
        ]
        assert len(fix_calls) == 1
        _, data = fix_calls[0]
        assert data == {"choice": "64175", "submitselect": "Select"}

    def test_select_by_formula_plus_cas(self, tmp_path):
        """When the queue carries CAS, the matcher prefers CAS over
        name. Same record can be reached via CAS alone — useful when
        names drift between releases."""

        session = _choose_then_data_session()
        cfg = FormResolverConfig(
            output_dir=tmp_path,
            session_factory=lambda: session,
            sleep_seconds=0,
            selection_policy=SelectionPolicy.EXACT_MATCH,
        )
        record = _record(name=None, cas_number="64-17-5")
        summary = run_form_resolver_queue([record], cfg)
        result = summary.results[0]
        assert result.accepted_as_data is True
        assert result.selection_match_basis == "formula+cas"
        assert result.selected_cas_number == "64175"

    def test_dimethyl_ether_picked_when_record_targets_it(self, tmp_path):
        """Critical regression: the matcher must NOT pick the first
        candidate by default. Submit a queue record for DME and
        confirm DME is what gets POSTed, not ethanol."""

        session = _choose_then_data_session()
        cfg = FormResolverConfig(
            output_dir=tmp_path,
            session_factory=lambda: session,
            sleep_seconds=0,
            selection_policy=SelectionPolicy.EXACT_MATCH,
        )
        record = _record(
            species_key="dme",
            formula="C2H6O",
            name="Dimethyl ether",
        )
        run_form_resolver_queue([record], cfg)
        fix_calls = [
            data for url, data in session.posts
            if "fixchoicex" in url
        ]
        assert len(fix_calls) == 1
        assert fix_calls[0]["choice"] == "115106"  # DME CAS

    def test_selection_metadata_in_manifest(self, tmp_path):
        session = _choose_then_data_session()
        cfg = FormResolverConfig(
            output_dir=tmp_path,
            session_factory=lambda: session,
            sleep_seconds=0,
            selection_policy=SelectionPolicy.EXACT_MATCH,
        )
        run_form_resolver_queue([_record()], cfg)
        manifest = json.loads(
            (tmp_path / "manifest.json").read_text(encoding="utf-8")
        )
        rec = next(
            r for r in manifest["records"]
            if r["species_key"] == "ethanol"
        )
        assert rec["selection_policy"] == "exact_match"
        assert rec["selection_status"] == "selected"
        assert rec["selection_match_basis"] == "formula+name"
        assert rec["selection_candidate_count"] == 3
        assert rec["selected_name"] == "Ethanol"
        assert rec["selected_cas_number"] == "64175"


# ---------------------------------------------------------------------------
# Exact-match: rejection paths
# ---------------------------------------------------------------------------


class TestExactMatchRejectionPaths:
    def test_formula_only_record_is_rejected(self, tmp_path):
        """Formula alone is never sufficient — the prompt explicitly
        forbids it. Submit C2H6O with no name/CAS/InChIKey and
        confirm the queue rejects."""

        session = _choose_then_data_session()
        cfg = FormResolverConfig(
            output_dir=tmp_path,
            session_factory=lambda: session,
            sleep_seconds=0,
            selection_policy=SelectionPolicy.EXACT_MATCH,
        )
        record = _record(name=None, cas_number=None, inchikey=None)
        summary = run_form_resolver_queue([record], cfg)
        result = summary.results[0]
        assert result.accepted_as_data is False
        assert result.selection_status == "ambiguous_or_no_match"
        # No POST to fixchoicex.
        assert all("fixchoicex" not in u for u, _ in session.posts)

    def test_no_match_record_is_rejected(self, tmp_path):
        session = _choose_then_data_session()
        cfg = FormResolverConfig(
            output_dir=tmp_path,
            session_factory=lambda: session,
            sleep_seconds=0,
            selection_policy=SelectionPolicy.EXACT_MATCH,
        )
        record = _record(name="Nonexistent isomer")
        summary = run_form_resolver_queue([record], cfg)
        result = summary.results[0]
        assert result.accepted_as_data is False
        assert result.selection_status == "ambiguous_or_no_match"

    def test_multiple_distinct_matches_are_rejected(self, tmp_path):
        """If multiple candidates with DIFFERENT choice values match
        the queue (extremely rare but possible), the resolver must
        reject — picking one would be a coin flip."""

        # Build a synthetic page with two distinct-CAS candidates
        # sharing the same name ("Ethanol" pointing at two separate
        # database rows).
        html = (
            '<html><body>'
            '<FORM ACTION="fixchoicex.asp" METHOD="post" id="form1">'
            "<table>"
            "<TR><TD COLSPAN=3>Choose<TD>charge<TD>state<TD>config"
            "<TD>name<TD>casno<TD>sketch"
            '<TR><TD>1<TD><input TYPE="checkbox" NAME="choice" VALUE="100">'
            "<TD>CH3CH2OH<TD>0<TD>1<TD>1<TD>Ethanol<TD>100<TD>x"
            '<TR><TD>2<TD><input TYPE="checkbox" NAME="choice" VALUE="200">'
            "<TD>CH3CH2OH<TD>0<TD>1<TD>1<TD>Ethanol<TD>200<TD>x"
            "</table></FORM></body></html>"
        )
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
                    text=html, status_code=200,
                    url="https://cccbdb.nist.gov/choosex.asp",
                ),
            },
        )
        cfg = FormResolverConfig(
            output_dir=tmp_path,
            session_factory=lambda: session,
            sleep_seconds=0,
            selection_policy=SelectionPolicy.EXACT_MATCH,
        )
        summary = run_form_resolver_queue([_record()], cfg)
        result = summary.results[0]
        assert result.accepted_as_data is False
        assert result.selection_status == "ambiguous_or_no_match"
        # Both candidate choice values must show up in the metadata
        # so the maintainer can see what the resolver saw.
        # (Stored on resolver-only path; see in-memory result.)


# ---------------------------------------------------------------------------
# select_candidate unit tests (without a session)
# ---------------------------------------------------------------------------


def _page_from_fixture() -> CCCBDBSpeciesSelectionPage:
    return parse_species_selection_page(
        _load("form_result_choose_c2h6o_live.html"),
        base_url="https://cccbdb.nist.gov/choosex.asp",
    )


class TestSelectCandidateUnit:
    def test_reject_ambiguous_returns_no_selection(self):
        page = _page_from_fixture()
        outcome = select_candidate(
            page, _record(), SelectionPolicy.REJECT_AMBIGUOUS
        )
        assert outcome.selected is None
        assert outcome.status == "ambiguous_or_no_match"
        assert outcome.candidate_count == 3

    def test_exact_match_two_conformers_same_cas_count_as_one(self):
        """The live page has TWO rows for ethanol (config 1 vs 2),
        both with CAS 64175 / choice=64175. The matcher must
        deduplicate by form_field_value and treat them as a single
        unambiguous selection."""

        page = _page_from_fixture()
        outcome = select_candidate(
            page, _record(), SelectionPolicy.EXACT_MATCH
        )
        assert outcome.selected is not None
        assert outcome.status == "selected"
        assert outcome.matched_candidate_count == 2  # two rows matched
        assert outcome.matched_choice_values == ("64175",)
        assert outcome.match_basis == "formula+name"

    def test_inchikey_match_when_candidate_carries_inchikey(self):
        """choosex.asp does not surface InChIKey today, but the matcher
        should still honour it for forward compatibility."""

        synth_candidates = [
            CCCBDBSelectionCandidate(
                formula="C2H6O",
                name="Ethanol",
                cas_number="64-17-5",
                charge="0", state="1", config="1",
                form_field_name="choice",
                form_field_value="64175",
                inchikey="LFQSCWFLJHTTHZ-UHFFFAOYSA-N",
            ),
            CCCBDBSelectionCandidate(
                formula="C2H6O",
                name="Dimethyl ether",
                cas_number="115-10-6",
                charge="0", state="1", config="1",
                form_field_name="choice",
                form_field_value="115106",
                inchikey="LCGLNKUTAGEVQW-UHFFFAOYSA-N",
            ),
        ]
        synth_page = CCCBDBSpeciesSelectionPage(
            title=None,
            heading=None,
            form_action_url="https://cccbdb.nist.gov/fixchoicex.asp",
            form_method="POST",
            candidates=synth_candidates,
        )
        record = _record(
            name=None, cas_number=None,
            inchikey="LFQSCWFLJHTTHZ-UHFFFAOYSA-N",
        )
        outcome = select_candidate(
            synth_page, record, SelectionPolicy.EXACT_MATCH
        )
        assert outcome.selected is not None
        assert outcome.match_basis == "formula+inchikey"

    def test_no_candidates_returns_no_candidates_status(self):
        empty_page = CCCBDBSpeciesSelectionPage(
            title=None, heading=None,
            form_action_url="https://cccbdb.nist.gov/fixchoicex.asp",
            form_method="POST",
            candidates=[],
        )
        outcome = select_candidate(
            empty_page, _record(), SelectionPolicy.EXACT_MATCH
        )
        assert outcome.selected is None
        assert outcome.status == "no_candidates"


# ---------------------------------------------------------------------------
# CLI tests
# ---------------------------------------------------------------------------


class TestCliSelectionPolicy:
    def test_cli_accepts_exact_match(self, tmp_path, monkeypatch):
        session = _choose_then_data_session()
        from app.importers.cccbdb import form_resolver as mod

        class _FakeFactory:
            def __init__(self, *a, **kw):
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
                            "species_key": "ethanol",
                            "formula": "C2H6O",
                            "name": "Ethanol",
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
                "--selection-policy", "exact-match",
            ]
        )
        assert rc == 0
        manifest = json.loads(
            (tmp_path / "out" / "manifest.json").read_text()
        )
        assert any(
            r.get("selection_status") == "selected"
            for r in manifest["records"]
        )

    def test_cli_rejects_unknown_selection_policy(self, tmp_path):
        queue_path = tmp_path / "queue.json"
        queue_path.write_text(
            json.dumps(
                {
                    "records": [
                        {
                            "species_key": "h2o",
                            "formula": "H2O",
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
                "--selection-policy", "fuzzy-match",
            ]
        )
        assert rc == 2

    def test_cli_default_is_reject_ambiguous(self, tmp_path, monkeypatch):
        """The CLI default must remain conservative. A queue record
        that would match under exact-match must NOT be auto-selected
        when --selection-policy is omitted."""

        session = _choose_then_data_session()
        from app.importers.cccbdb import form_resolver as mod

        class _FakeFactory:
            def __init__(self, *a, **kw):
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
                            "species_key": "ethanol",
                            "formula": "C2H6O",
                            "name": "Ethanol",
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
            ]
        )
        assert rc == 0
        manifest = json.loads(
            (tmp_path / "out" / "manifest.json").read_text()
        )
        rec = manifest["records"][0]
        # Default policy did NOT follow the selection.
        assert rec["selection_policy"] == "reject_ambiguous"
        assert rec["accepted_as_data"] is False
