"""Tests for the CCCBDB form-result → MolecularPropertyObservation builder
and its disk-driven dry-run.

All tests are offline.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from app.db.models.common import MolecularPropertyKind, ScientificOriginKind
from app.importers.cccbdb.form_payload_builder import (
    build_atomization_energy_payloads_from_form_result,
    load_parsed_form_result,
)
from app.importers.cccbdb.form_resolver import (
    FormQueueRecord,
    FormResolverConfig,
    SelectionPolicy,
    SessionResponse,
    run_form_resolver_queue,
)
from app.importers.cccbdb.parsers import (
    FormResultRow,
    parse_form_result_page,
)
from app.importers.cccbdb.parsers.form_result import (
    CCCBDBFormResultTable,
)
from app.schemas.entities.molecular_property_observation import (
    MolecularPropertyObservationCreate,
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


def _parse_h2o_table() -> CCCBDBFormResultTable:
    return parse_form_result_page(
        _load("form_result_ea_h2o.html"),
        target_kind="atomization_energy",
        source_url="https://cccbdb.nist.gov/ea1x.asp",
        final_url="https://cccbdb.nist.gov/ea2x.asp",
    )


# ---------------------------------------------------------------------------
# Builder: row → MolecularPropertyObservationCreate
# ---------------------------------------------------------------------------


class TestBuilderMapping:
    def test_h2o_row_yields_workflow_ready_payload(self):
        results = build_atomization_energy_payloads_from_form_result(
            _parse_h2o_table(),
            selection_metadata=None,
            resolver_strategy="requests_session_form_post",
            species_key="h2o",
        )
        assert len(results) == 1
        r = results[0]
        assert r.is_workflow_ready is True
        assert r.payload is not None
        assert r.payload.property_kind == MolecularPropertyKind.atomization_energy
        assert r.payload.property_label == "atomization_energy_0k"
        assert r.payload.scientific_origin == ScientificOriginKind.experimental
        assert r.payload.scalar_value == pytest.approx(917.8)
        assert r.payload.scalar_unit == "kJ/mol"
        assert r.payload.scalar_uncertainty == pytest.approx(0.1)
        # 0 K is encoded via property_label; the schema's temperature_k
        # validator requires > 0 so we deliberately leave it None.
        assert r.payload.temperature_k is None

    def test_298K_secondary_value_preserved(self):
        results = build_atomization_energy_payloads_from_form_result(
            _parse_h2o_table()
        )
        payload = results[0].payload
        assert payload is not None
        secondary = payload.raw_payload_json["secondary_values"]
        assert secondary == {"298K": pytest.approx(927.0)}

    def test_source_metadata_preserved(self):
        results = build_atomization_energy_payloads_from_form_result(
            _parse_h2o_table(), species_key="h2o"
        )
        payload = results[0].payload
        assert payload is not None
        assert payload.external_source_name == "CCCBDB"
        assert payload.external_source_url == "https://cccbdb.nist.gov/ea1x.asp"
        assert payload.external_source_page_kind == "experimental_form_result"
        assert payload.external_source_record_key == "h2o"
        assert payload.external_source_content_sha256 is not None
        # The raw_payload_json must echo source provenance too.
        meta = payload.raw_payload_json["source_metadata"]
        assert meta["source"] == "CCCBDB"
        assert payload.raw_payload_json["final_url"] \
            == "https://cccbdb.nist.gov/ea2x.asp"

    def test_selection_metadata_preserved(self):
        selection = {
            "selection_policy": "exact_match",
            "selection_status": "selected",
            "selection_match_basis": "formula+name",
            "selected_name": "Water",
            "selected_cas_number": "7732185",
        }
        results = build_atomization_energy_payloads_from_form_result(
            _parse_h2o_table(),
            selection_metadata=selection,
        )
        payload = results[0].payload
        assert payload is not None
        assert payload.raw_payload_json["selection"] == selection

    def test_resolver_strategy_recorded(self):
        results = build_atomization_energy_payloads_from_form_result(
            _parse_h2o_table(),
            resolver_strategy="requests_session_form_post",
        )
        payload = results[0].payload
        assert payload is not None
        assert (
            payload.raw_payload_json["resolver_strategy"]
            == "requests_session_form_post"
        )

    def test_identity_hint_present(self):
        results = build_atomization_energy_payloads_from_form_result(
            _parse_h2o_table()
        )
        payload = results[0].payload
        assert payload is not None
        assert payload.raw_payload_json["identity_hint"] == {
            "formula": "H2O",
            "name": "Water",
        }

    def test_species_entry_id_remains_null(self):
        """Identity resolution is the workflow layer's job, not this
        builder's. Phase 8 emits payloads with ``species_entry_id=None``."""

        results = build_atomization_energy_payloads_from_form_result(
            _parse_h2o_table()
        )
        payload = results[0].payload
        assert payload is not None
        assert payload.species_entry_id is None

    def test_payloads_validate_through_pydantic_roundtrip(self):
        results = build_atomization_energy_payloads_from_form_result(
            _parse_h2o_table()
        )
        for r in results:
            if r.payload is None:
                continue
            # If the builder emitted it, model_validate must accept it.
            MolecularPropertyObservationCreate.model_validate(
                r.payload.model_dump(mode="json")
            )


# ---------------------------------------------------------------------------
# Builder: skip rules
# ---------------------------------------------------------------------------


class TestBuilderSkipRules:
    def test_row_without_value_is_skipped_with_warning(self):
        table = CCCBDBFormResultTable(
            target_kind="atomization_energy",
            title="Experimental Atomization Energies",
            column_names=["Species", "Name", "0K", "298K", "unc."],
            raw_units="kJ/mol",
            rows=[
                FormResultRow(
                    row_index=0,
                    formula="X",
                    name="Mystery molecule",
                    value=None,  # <-- no 0K value
                    unit=None,
                ),
            ],
            source_url="https://cccbdb.nist.gov/ea1x.asp",
            final_url="https://cccbdb.nist.gov/ea2x.asp",
            content_sha256="0" * 64,
        )
        results = build_atomization_energy_payloads_from_form_result(table)
        assert len(results) == 1
        assert results[0].payload is None
        assert results[0].is_workflow_ready is False
        assert any("no numeric 0 K" in w for w in results[0].warnings)

    def test_unsupported_target_yields_no_payloads(self):
        table = CCCBDBFormResultTable(
            target_kind="vibrational_frequency",
            title="Experimental Vibrational Frequencies",
            column_names=[],
            raw_units=None,
            rows=[],
            content_sha256="0" * 64,
        )
        results = build_atomization_energy_payloads_from_form_result(table)
        assert len(results) == 1
        assert results[0].payload is None
        assert any(
            "unsupported target_kind" in w for w in results[0].warnings
        )


# ---------------------------------------------------------------------------
# Disk-driven loader
# ---------------------------------------------------------------------------


class TestLoadParsedFormResult:
    def test_round_trip_through_archive_format(self, tmp_path):
        """Write a parsed-form JSON via the resolver helper shape, then
        read it back with ``load_parsed_form_result`` and confirm the
        round-trip preserves rows + selection metadata."""

        data = {
            "target_kind": "atomization_energy",
            "title": "Experimental Atomization Energies",
            "column_names": ["Species", "Name", "0K", "298K", "unc."],
            "raw_units": "kJ/mol",
            "source_url": "https://cccbdb.nist.gov/ea1x.asp",
            "final_url": "https://cccbdb.nist.gov/ea2x.asp",
            "content_sha256": "1" * 64,
            "source_metadata": {
                "source": "CCCBDB",
                "resolver_strategy": "requests_session_form_post",
                "species_key": "ethanol",
                "queue_formula": "C2H6O",
                "queue_name": "Ethanol",
            },
            "rows": [
                {
                    "row_index": 0,
                    "formula": "C2H6O",
                    "name": "Ethanol",
                    "value": 3182.5,
                    "unit": "kJ/mol",
                    "uncertainty": None,
                    "secondary_values": {"298K": 3225.4},
                    "raw_row": {"0K": "3182.5", "298K": "3225.4"},
                    "reference_label": None,
                    "reference_comment": None,
                    "warnings": [],
                }
            ],
            "warnings": [],
            "selection": {
                "selection_policy": "exact_match",
                "selection_status": "selected",
                "selection_match_basis": "formula+name",
                "selected_name": "Ethanol",
                "selected_cas_number": "64175",
            },
        }
        path = tmp_path / "form_atomization_energy_ethanol_abc.json"
        path.write_text(json.dumps(data), encoding="utf-8")

        loaded = load_parsed_form_result(path)
        assert loaded.target_kind == "atomization_energy"
        assert loaded.species_key == "ethanol"
        assert loaded.resolver_strategy == "requests_session_form_post"
        assert loaded.selection_metadata is not None
        assert loaded.selection_metadata["selection_match_basis"] \
            == "formula+name"
        assert len(loaded.table.rows) == 1
        assert loaded.table.rows[0].value == pytest.approx(3182.5)

    def test_missing_target_kind_raises(self, tmp_path):
        path = tmp_path / "bad.json"
        path.write_text(json.dumps({"rows": []}))
        with pytest.raises(ValueError, match="target_kind"):
            load_parsed_form_result(path)


# ---------------------------------------------------------------------------
# Dry-run integration
# ---------------------------------------------------------------------------


def _populate_parsed_archive(archive_dir: Path) -> None:
    """Write a single parsed/form_atomization_energy_*.json file under
    ``archive_dir`` so the dry-run script has something to read."""

    parsed_dir = archive_dir / "parsed"
    parsed_dir.mkdir(parents=True, exist_ok=True)
    data = {
        "target_kind": "atomization_energy",
        "title": "Experimental Atomization Energies",
        "column_names": ["Species", "Name", "0K", "298K", "unc."],
        "raw_units": "kJ/mol",
        "source_url": "https://cccbdb.nist.gov/ea1x.asp",
        "final_url": "https://cccbdb.nist.gov/ea2x.asp",
        "content_sha256": "2" * 64,
        "source_metadata": {
            "source": "CCCBDB",
            "resolver_strategy": "requests_session_form_post",
            "species_key": "h2o",
            "queue_formula": "H2O",
            "queue_name": "Water",
        },
        "rows": [
            {
                "row_index": 0,
                "formula": "H2O",
                "name": "Water",
                "value": 917.8,
                "unit": "kJ/mol",
                "uncertainty": 0.1,
                "secondary_values": {"298K": 927.0},
                "raw_row": {},
                "reference_label": None,
                "reference_comment": None,
                "warnings": [],
            }
        ],
        "warnings": [],
    }
    (parsed_dir / "form_atomization_energy_h2o_222222222222.json").write_text(
        json.dumps(data)
    )


class TestFormPayloadDryRun:
    def test_dryrun_writes_summary_and_target_files(self, tmp_path):
        from scripts.cccbdb_form_payload_dryrun import run_form_payload_dryrun

        archive = tmp_path / "archive"
        out = tmp_path / "out"
        _populate_parsed_archive(archive)

        summary = run_form_payload_dryrun(
            archive_dir=archive, output_dir=out
        )
        assert (out / "summary.json").exists()
        assert (out / "atomization_energy.json").exists()
        assert summary.health == "healthy"
        assert summary.total_payload_count == 1
        assert summary.total_invalid_payload_count == 0

    def test_dryrun_payload_validates_through_schema(self, tmp_path):
        from scripts.cccbdb_form_payload_dryrun import run_form_payload_dryrun

        archive = tmp_path / "archive"
        out = tmp_path / "out"
        _populate_parsed_archive(archive)
        run_form_payload_dryrun(archive_dir=archive, output_dir=out)

        data = json.loads(
            (out / "atomization_energy.json").read_text()
        )
        assert data["parsed_file_count"] == 1
        assert data["payload_count"] == 1
        for payload in data["payloads"]:
            MolecularPropertyObservationCreate.model_validate(payload)

    def test_dryrun_empty_archive_is_healthy(self, tmp_path):
        """An archive with no parsed files is considered healthy —
        there's nothing to gate on."""

        from scripts.cccbdb_form_payload_dryrun import run_form_payload_dryrun

        archive = tmp_path / "archive"
        out = tmp_path / "out"
        (archive / "parsed").mkdir(parents=True)

        summary = run_form_payload_dryrun(
            archive_dir=archive, output_dir=out
        )
        assert summary.health == "healthy"
        assert summary.total_parsed_files == 0
        assert summary.total_payload_count == 0

    def test_dryrun_unhealthy_when_all_rows_skipped(self, tmp_path):
        """A parsed file with no buildable rows must mark the dry-run
        as unhealthy — that's exactly the silent-empty scenario the
        flat property-table health gate also catches."""

        from scripts.cccbdb_form_payload_dryrun import run_form_payload_dryrun

        archive = tmp_path / "archive"
        out = tmp_path / "out"
        parsed_dir = archive / "parsed"
        parsed_dir.mkdir(parents=True)

        # One parsed file with a row that has no numeric value → skip.
        data = {
            "target_kind": "atomization_energy",
            "title": "X",
            "column_names": ["Species", "Name", "0K", "298K", "unc."],
            "raw_units": "kJ/mol",
            "source_url": "https://cccbdb.nist.gov/ea1x.asp",
            "final_url": "https://cccbdb.nist.gov/ea2x.asp",
            "content_sha256": "3" * 64,
            "source_metadata": {"source": "CCCBDB", "species_key": "x"},
            "rows": [
                {
                    "row_index": 0,
                    "formula": "X", "name": "Mystery",
                    "value": None, "unit": None, "uncertainty": None,
                    "secondary_values": {}, "raw_row": {},
                    "reference_label": None, "reference_comment": None,
                    "warnings": [],
                }
            ],
            "warnings": [],
        }
        (parsed_dir / "form_atomization_energy_x_333333333333.json"
         ).write_text(json.dumps(data))

        summary = run_form_payload_dryrun(
            archive_dir=archive, output_dir=out
        )
        assert summary.health == "unhealthy"
        assert summary.total_parsed_files == 1
        assert summary.total_payload_count == 0
        assert summary.health_summary["atomization_energy"] == "unhealthy"

    def test_cli_returns_zero_on_success(self, tmp_path):
        from scripts.cccbdb_form_payload_dryrun import main

        archive = tmp_path / "archive"
        out = tmp_path / "out"
        _populate_parsed_archive(archive)

        rc = main(
            [
                "--archive-dir", str(archive),
                "--output-dir", str(out),
            ]
        )
        assert rc == 0

    def test_cli_returns_two_when_archive_missing(self, tmp_path):
        from scripts.cccbdb_form_payload_dryrun import main

        rc = main(
            [
                "--archive-dir", str(tmp_path / "nope"),
                "--output-dir", str(tmp_path / "out"),
            ]
        )
        assert rc == 2


# ---------------------------------------------------------------------------
# End-to-end: resolver + builder + dry-run for ethanol via exact-match
# ---------------------------------------------------------------------------


@dataclass
class FakeSession:
    canned_get: dict[str, SessionResponse] = field(default_factory=dict)
    canned_post: dict[str, SessionResponse] = field(default_factory=dict)
    posts: list[tuple[str, dict]] = field(default_factory=list)

    def get(self, url, *, timeout=None):
        return self.canned_get.get(
            url, SessionResponse(text="", status_code=404, url=url)
        )

    def post(self, url, *, data, timeout=None):
        self.posts.append((url, dict(data)))
        return self.canned_post.get(
            url, SessionResponse(text="", status_code=404, url=url)
        )


def test_end_to_end_ethanol_exact_match_emits_payload(tmp_path):
    """The full Phase 6→7→8 pipeline against an ethanol queue record:

    1. Fake session returns the live-shape choosex.asp page for the
       initial POST.
    2. Resolver applies EXACT_MATCH, picks ethanol, POSTs to
       fixchoicex.asp.
    3. Fake session returns the H2O atomization-energy result fixture
       (used as a stand-in — only the table shape matters here).
    4. parsed/form_*.json gets written with selection metadata.
    5. cccbdb_form_payload_dryrun reads the parsed file and emits a
       workflow-ready MolecularPropertyObservationCreate.
    """

    archive = tmp_path / "archive"
    archive.mkdir()
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
    cfg = FormResolverConfig(
        output_dir=archive,
        session_factory=lambda: session,
        sleep_seconds=0,
        selection_policy=SelectionPolicy.EXACT_MATCH,
    )
    record = FormQueueRecord(
        species_key="ethanol",
        formula="C2H6O",
        name="Ethanol",
        target_kind="atomization_energy",
        entry_url="https://cccbdb.nist.gov/ea1x.asp",
    )
    resolver_summary = run_form_resolver_queue([record], cfg)
    assert resolver_summary.accepted == 1

    # The resolver should have written a parsed/form_*.json file with
    # selection metadata.
    parsed_files = list((archive / "parsed").iterdir())
    assert len(parsed_files) == 1
    parsed = json.loads(parsed_files[0].read_text())
    assert parsed["selection"]["selection_status"] == "selected"
    assert parsed["selection"]["selected_name"] == "Ethanol"

    # Now run the form-payload dry-run against the same archive.
    from scripts.cccbdb_form_payload_dryrun import run_form_payload_dryrun

    out = tmp_path / "out"
    summary = run_form_payload_dryrun(archive_dir=archive, output_dir=out)
    assert summary.health == "healthy"
    assert summary.total_payload_count == 1

    target = json.loads(
        (out / "atomization_energy.json").read_text()
    )
    payload_json = target["payloads"][0]
    payload = MolecularPropertyObservationCreate.model_validate(payload_json)
    # The fixture is the H2O result so the scalar is H2O's atomization
    # energy. What matters end-to-end is that the payload carries:
    # (a) atomization_energy kind, (b) the 0K value, (c) selection
    # metadata flowing through.
    assert payload.property_kind == MolecularPropertyKind.atomization_energy
    assert payload.property_label == "atomization_energy_0k"
    assert payload.scalar_value == pytest.approx(917.8)
    assert payload.scalar_unit == "kJ/mol"
    assert (
        payload.raw_payload_json["secondary_values"]["298K"]
        == pytest.approx(927.0)
    )
    sel = payload.raw_payload_json["selection"]
    assert sel["selection_status"] == "selected"
    assert sel["selection_match_basis"] == "formula+name"
    assert sel["selected_name"] == "Ethanol"


# ---------------------------------------------------------------------------
# Structural / no-DB invariants
# ---------------------------------------------------------------------------


def test_form_payload_builder_has_no_orm_sessions():
    from app.importers.cccbdb import form_payload_builder

    for name in (
        "Session", "sessionmaker", "create_engine", "scoped_session",
    ):
        assert name not in form_payload_builder.__dict__


def test_form_payload_dryrun_has_no_orm_sessions():
    from scripts import cccbdb_form_payload_dryrun

    for name in (
        "Session", "sessionmaker", "create_engine", "scoped_session",
    ):
        assert name not in cccbdb_form_payload_dryrun.__dict__
