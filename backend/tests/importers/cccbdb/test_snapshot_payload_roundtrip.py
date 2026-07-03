"""Snapshot → disk → real upload-schema round-trip tests.

These tests prove that the CCCBDB archive on disk is a *replayable*
contract: a snapshot produced by ``run_snapshot(write_payloads=True)``
contains payload JSON that the real TCKDB upload/request models accept
without further transformation.

The Phase 2a builder tests already validate in-memory builder output
against the same models. The point of this file is to additionally
exercise the on-disk artifact boundary, so subtle JSON
serialization/deserialization drift (enum values, datetime
encoding, missing-vs-null defaults) cannot regress unnoticed.

No DB writes. No live fetching. The same ``FixtureFetcher`` used by
``test_snapshot.py`` drives the runner.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import BaseModel, ValidationError
from tckdb_schemas.fragments.geometry import GeometryPayload
from tckdb_schemas.fragments.identity import SpeciesEntryIdentityPayload

from app.importers.cccbdb.crawl_plan import EXPERIMENTAL_PILOT
from app.importers.cccbdb.snapshot import SnapshotConfig, run_snapshot
from app.schemas.workflows.statmech_upload import StatmechUploadRequest
from app.schemas.workflows.thermo_upload import ThermoUploadRequest

# Reuse the same fixture-backed fetcher used by the snapshot unit tests.
from tests.importers.cccbdb.test_snapshot import FixtureFetcher

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _run_archive(tmp_path: Path) -> tuple[dict, Path]:
    """Build a fresh archive under ``tmp_path`` and return ``(manifest, root)``."""

    fetcher = FixtureFetcher.make()
    config = SnapshotConfig(
        output_dir=tmp_path,
        fetcher=fetcher,
        write_payloads=True,
        sleep_seconds=0.0,
        dry_run=False,
    )
    manifest = run_snapshot(EXPERIMENTAL_PILOT, config)
    return manifest, tmp_path


def _load_payload_from_disk(archive_root: Path, record: dict) -> dict:
    payload_path = archive_root / record["payload_json_path"]
    return json.loads(payload_path.read_text(encoding="utf-8"))


def _validate(
    model: type[BaseModel],
    data: dict,
    *,
    species_key: str,
    payload_path: str,
    sub_payload: str,
) -> BaseModel:
    """Validate ``data`` against ``model`` with a precise failure message."""

    try:
        return model.model_validate(data)
    except ValidationError as exc:  # pragma: no cover - only on regression
        pytest.fail(
            "Disk payload failed real-schema validation:\n"
            f"  species_key  = {species_key}\n"
            f"  payload_path = {payload_path}\n"
            f"  sub_payload  = {sub_payload}\n"
            f"  model        = {model.__name__}\n"
            f"  errors       = {exc.errors()}"
        )


# ---------------------------------------------------------------------------
# Top-level invariants on the archive
# ---------------------------------------------------------------------------


class TestManifestInvariants:
    def test_manifest_success_records_have_payload_paths_when_write_payloads_enabled(
        self, tmp_path: Path
    ) -> None:
        manifest, _ = _run_archive(tmp_path)
        assert manifest["records"], "snapshot produced no records"
        for record in manifest["records"]:
            assert (
                record["parser_error"] is None
            ), f"{record['species_key']}: parser_error={record['parser_error']}"
            assert (
                record["builder_error"] is None
            ), f"{record['species_key']}: builder_error={record['builder_error']}"
            assert record["content_sha256"]
            assert record["payload_json_path"], (
                f"{record['species_key']}: expected a payload_json_path "
                "when --write-payloads is on"
            )

    def test_stable_provenance_fields_match_cccbdb_release_22(
        self, tmp_path: Path
    ) -> None:
        manifest, _ = _run_archive(tmp_path)
        assert manifest["source"] == "CCCBDB"
        assert manifest["source_release"] == "22"
        assert manifest["source_database_doi"] == "10.18434/T47C7Z"
        for record in manifest["records"]:
            assert record["page_kind"] == "experimental_species"
            assert record["source_url"].startswith(
                "https://cccbdb.nist.gov/"
            )


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------


class TestPayloadDeterminism:
    def test_payload_files_are_json_serializable_and_deterministic(
        self, tmp_path: Path
    ) -> None:
        """Two independent archives over identical input must produce
        byte-identical payload JSON files and identical content hashes,
        modulo ``created_at`` / ``retrieved_at`` wall-clock fields."""

        archive_a = tmp_path / "a"
        archive_b = tmp_path / "b"
        manifest_a, _ = _run_archive(archive_a)
        manifest_b, _ = _run_archive(archive_b)

        # Same fixtures → same content hashes per species.
        by_species_a = {r["species_key"]: r for r in manifest_a["records"]}
        by_species_b = {r["species_key"]: r for r in manifest_b["records"]}
        for key in by_species_a:
            assert (
                by_species_a[key]["content_sha256"]
                == by_species_b[key]["content_sha256"]
            ), f"content_sha256 drift for {key}"

        # Each payload JSON file is parseable and contains the BuildResult shape.
        for record in manifest_a["records"]:
            payload = _load_payload_from_disk(archive_a, record)
            assert payload["external_source"]["name"] == "CCCBDB"
            assert payload["external_source"]["release"] == "22"
            assert payload["external_source"]["content_sha256"] == record[
                "content_sha256"
            ]

        # Builder output (sans BuildResult wrapper) is byte-identical
        # across the two archives: per-record contents minus the
        # parsed JSON which also has no wall-clock fields.
        for key in by_species_a:
            path_a = (
                archive_a / by_species_a[key]["payload_json_path"]
            ).read_bytes()
            path_b = (
                archive_b / by_species_b[key]["payload_json_path"]
            ).read_bytes()
            assert path_a == path_b, f"payload bytes differ for {key}"


# ---------------------------------------------------------------------------
# Real-schema validation of disk payloads
# ---------------------------------------------------------------------------


class TestPayloadSchemaRoundTrip:
    def test_snapshot_payloads_validate_against_real_upload_models(
        self, tmp_path: Path
    ) -> None:
        manifest, archive_root = _run_archive(tmp_path)

        validated_species: set[str] = set()
        for record in manifest["records"]:
            payload = _load_payload_from_disk(archive_root, record)
            species_key = record["species_key"]
            payload_path = record["payload_json_path"]

            # SpeciesEntryIdentityPayload: only validate when the
            # builder marked the dict as ready for the workflow
            # contract. Partial identity (e.g. CCCBDB pages without
            # SMILES) is a *documented* outcome, not a regression.
            if payload.get("species_entry_payload_is_valid"):
                _validate(
                    SpeciesEntryIdentityPayload,
                    payload["species_entry_payload"],
                    species_key=species_key,
                    payload_path=payload_path,
                    sub_payload="species_entry_payload",
                )

            if payload.get("thermo_payload_is_valid"):
                req = _validate(
                    ThermoUploadRequest,
                    payload["thermo_payload"],
                    species_key=species_key,
                    payload_path=payload_path,
                    sub_payload="thermo_payload",
                )
                assert req.scientific_origin.value == "experimental"

            if payload.get("statmech_payload_is_valid"):
                req = _validate(
                    StatmechUploadRequest,
                    payload["statmech_payload"],
                    species_key=species_key,
                    payload_path=payload_path,
                    sub_payload="statmech_payload",
                )
                assert req.scientific_origin.value == "experimental"

            if payload.get("geometry_payload") is not None:
                _validate(
                    GeometryPayload,
                    payload["geometry_payload"],
                    species_key=species_key,
                    payload_path=payload_path,
                    sub_payload="geometry_payload",
                )

            validated_species.add(species_key)

        # All three pilot species got at least *something* validated.
        assert validated_species == {"h2", "h2o", "benzene"}

    def test_h2o_geometry_xyz_text_round_trips(self, tmp_path: Path) -> None:
        """Spot-check a non-trivial sub-payload: H2O's geometry block
        survives JSON encoding and re-validates as a real ``GeometryPayload``."""

        manifest, archive_root = _run_archive(tmp_path)
        h2o = next(r for r in manifest["records"] if r["species_key"] == "h2o")
        payload = _load_payload_from_disk(archive_root, h2o)
        geom = GeometryPayload.model_validate(payload["geometry_payload"])
        # XYZ format: natoms on line 0, comment on line 1, then 3 atom lines.
        lines = geom.xyz_text.splitlines()
        assert lines[0] == "3"
        assert lines[1].startswith("CCCBDB experimental geometry")
        assert len(lines) == 5

    def test_payload_validation_reports_species_key_on_failure(
        self, tmp_path: Path
    ) -> None:
        """Sanity-check the diagnostic helper: when we feed it a known-bad
        payload, the produced failure message names the species, the path,
        and the sub-payload that failed."""

        # Build a synthetic thermo payload that the real
        # ``ThermoUploadRequest`` will reject (missing required species_entry).
        bad_payload = {"scientific_origin": "experimental"}
        with pytest.raises(pytest.fail.Exception) as exc_info:
            _validate(
                ThermoUploadRequest,
                bad_payload,
                species_key="benzene",
                payload_path="payloads/experimental_benzene_deadbeef.json",
                sub_payload="thermo_payload",
            )
        msg = str(exc_info.value)
        assert "benzene" in msg
        assert "thermo_payload" in msg
        assert "ThermoUploadRequest" in msg
        assert "experimental_benzene_deadbeef.json" in msg


# ---------------------------------------------------------------------------
# Documented non-validatable sub-payloads
# ---------------------------------------------------------------------------


class TestPartialIdentityNotValidated:
    def test_h2_species_payload_is_not_marked_valid(
        self, tmp_path: Path
    ) -> None:
        """H2's CCCBDB experimental page omits SMILES, so the importer
        intentionally produces a partial identity dict that does NOT
        satisfy ``SpeciesEntryIdentityPayload``. The round-trip test
        must honor that contract — not silently fail."""

        manifest, archive_root = _run_archive(tmp_path)
        h2 = next(r for r in manifest["records"] if r["species_key"] == "h2")
        payload = _load_payload_from_disk(archive_root, h2)
        assert payload["species_entry_payload_is_valid"] is False
        assert "smiles" not in payload["species_entry_payload"]
        # And the partial dict really would be rejected by the real model.
        with pytest.raises(ValidationError):
            SpeciesEntryIdentityPayload.model_validate(
                payload["species_entry_payload"]
            )
