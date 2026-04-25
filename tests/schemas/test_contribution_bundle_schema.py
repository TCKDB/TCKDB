"""Tests for app/schemas/workflows/contribution_bundle.py.

These are pure schema tests — no database, no FastAPI, no service layer.
They cover the contribution-bundle v0 format only.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.schemas.workflows.contribution_bundle import (
    BUNDLE_FORMAT,
    BUNDLE_VERSION,
    BundleKind,
    ContributionBundleV0,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
EXAMPLES_DIR = REPO_ROOT / "examples" / "bundles"


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------


def _thermo_upload() -> dict:
    return {
        "species_entry": {"smiles": "O", "charge": 0, "multiplicity": 1},
        "scientific_origin": "computed",
        "h298_kj_mol": -241.8,
        "s298_j_mol_k": 188.8,
    }


def _kinetics_upload() -> dict:
    return {
        "reaction": {
            "reversible": False,
            "reactants": [
                {"species_entry": {"smiles": "[H]", "charge": 0, "multiplicity": 2}},
                {"species_entry": {"smiles": "[H]", "charge": 0, "multiplicity": 2}},
            ],
            "products": [
                {"species_entry": {"smiles": "[H][H]", "charge": 0, "multiplicity": 1}},
            ],
        },
        "scientific_origin": "computed",
        "model_kind": "modified_arrhenius",
    }


def _bundle(kind: str, **overrides) -> dict:
    base: dict = {
        "bundle_format": BUNDLE_FORMAT,
        "bundle_version": BUNDLE_VERSION,
        "bundle_kind": kind,
        "created_at": "2026-04-25T00:00:00Z",
        "source_instance": {
            "instance_kind": "local",
            "instance_name": "test-instance",
            "schema_version": "d861dfd60891",
        },
        "exporter": {"local_user_label": "test-user"},
        "submission": {
            "title": "Test contribution",
            "summary": "Test bundle.",
            "source_kind": "local_bundle",
        },
        "records": {
            "thermo_uploads": [_thermo_upload()] if kind == "thermo" else [],
            "kinetics_uploads": [_kinetics_upload()] if kind == "kinetics" else [],
        },
        "local_refs": {},
        "manifest": {"sha256": None, "files": []},
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Valid bundles
# ---------------------------------------------------------------------------


class TestValidBundles:
    def test_minimal_thermo_bundle(self) -> None:
        bundle = ContributionBundleV0.model_validate(_bundle("thermo"))
        assert bundle.bundle_kind is BundleKind.thermo
        assert len(bundle.records.thermo_uploads) == 1
        assert bundle.records.kinetics_uploads == []

    def test_minimal_kinetics_bundle(self) -> None:
        bundle = ContributionBundleV0.model_validate(_bundle("kinetics"))
        assert bundle.bundle_kind is BundleKind.kinetics
        assert len(bundle.records.kinetics_uploads) == 1
        assert bundle.records.thermo_uploads == []

    def test_local_refs_accepted(self) -> None:
        bundle = ContributionBundleV0.model_validate(
            _bundle(
                "thermo",
                local_refs={
                    "species:ethanol": {"record_type": "species", "label": "ethanol"},
                    "thermo:ethanol_001": {"record_type": "thermo", "label": "ethanol_001"},
                },
            )
        )
        assert "species:ethanol" in bundle.local_refs

    def test_manifest_with_files(self) -> None:
        bundle = ContributionBundleV0.model_validate(
            _bundle(
                "thermo",
                manifest={
                    "sha256": "0" * 64,
                    "files": [
                        {
                            "path": "artifacts/output.log",
                            "sha256": "a" * 64,
                            "size_bytes": 1024,
                            "content_type": "text/plain",
                            "role": "output_log",
                        }
                    ],
                },
            )
        )
        assert len(bundle.manifest.files) == 1


# ---------------------------------------------------------------------------
# Example JSON files
# ---------------------------------------------------------------------------


class TestExampleJsonFiles:
    @pytest.mark.parametrize(
        "filename",
        ["thermo-bundle-v0.json", "kinetics-bundle-v0.json"],
    )
    def test_example_parses(self, filename: str) -> None:
        path = EXAMPLES_DIR / filename
        data = json.loads(path.read_text())
        ContributionBundleV0.model_validate(data)


# ---------------------------------------------------------------------------
# Invalid bundles
# ---------------------------------------------------------------------------


class TestInvalidTopLevel:
    def test_wrong_bundle_format(self) -> None:
        data = _bundle("thermo", bundle_format="not-a-tckdb-bundle")
        with pytest.raises(ValidationError):
            ContributionBundleV0.model_validate(data)

    def test_unsupported_bundle_version(self) -> None:
        data = _bundle("thermo", bundle_version="0.2")
        with pytest.raises(ValidationError):
            ContributionBundleV0.model_validate(data)

    def test_unsupported_bundle_kind(self) -> None:
        data = _bundle("thermo", bundle_kind="network")
        with pytest.raises(ValidationError):
            ContributionBundleV0.model_validate(data)

    def test_unknown_top_level_field_rejected(self) -> None:
        data = _bundle("thermo")
        data["species_id"] = 123  # raw DB identity must not be accepted
        with pytest.raises(ValidationError):
            ContributionBundleV0.model_validate(data)


class TestMissingMetadata:
    @pytest.mark.parametrize(
        "field",
        ["source_instance", "exporter", "submission", "records", "manifest", "created_at"],
    )
    def test_missing_top_level_field_rejected(self, field: str) -> None:
        data = _bundle("thermo")
        del data[field]
        with pytest.raises(ValidationError):
            ContributionBundleV0.model_validate(data)

    def test_missing_source_instance_kind(self) -> None:
        data = _bundle("thermo")
        del data["source_instance"]["instance_kind"]
        with pytest.raises(ValidationError):
            ContributionBundleV0.model_validate(data)

    def test_missing_exporter_user_label(self) -> None:
        data = _bundle("thermo")
        del data["exporter"]["local_user_label"]
        with pytest.raises(ValidationError):
            ContributionBundleV0.model_validate(data)


class TestRecordFamilyRules:
    def test_thermo_bundle_with_no_thermo_records_rejected(self) -> None:
        data = _bundle("thermo")
        data["records"]["thermo_uploads"] = []
        with pytest.raises(ValidationError, match="thermo bundles must contain"):
            ContributionBundleV0.model_validate(data)

    def test_kinetics_bundle_with_no_kinetics_records_rejected(self) -> None:
        data = _bundle("kinetics")
        data["records"]["kinetics_uploads"] = []
        with pytest.raises(ValidationError, match="kinetics bundles must contain"):
            ContributionBundleV0.model_validate(data)

    def test_mixed_thermo_with_kinetics_rejected(self) -> None:
        data = _bundle("thermo")
        data["records"]["kinetics_uploads"] = [_kinetics_upload()]
        with pytest.raises(ValidationError, match="must not carry kinetics_uploads"):
            ContributionBundleV0.model_validate(data)

    def test_mixed_kinetics_with_thermo_rejected(self) -> None:
        data = _bundle("kinetics")
        data["records"]["thermo_uploads"] = [_thermo_upload()]
        with pytest.raises(ValidationError, match="must not carry thermo_uploads"):
            ContributionBundleV0.model_validate(data)


class TestManifestRules:
    def test_duplicate_file_paths_rejected(self) -> None:
        data = _bundle("thermo")
        data["manifest"] = {
            "sha256": None,
            "files": [
                {"path": "out.log", "sha256": "a" * 64},
                {"path": "out.log", "sha256": "b" * 64},
            ],
        }
        with pytest.raises(ValidationError, match="unique 'path'"):
            ContributionBundleV0.model_validate(data)

    def test_invalid_sha256_rejected(self) -> None:
        data = _bundle("thermo")
        data["manifest"] = {
            "sha256": None,
            "files": [{"path": "out.log", "sha256": "not-hex"}],
        }
        with pytest.raises(ValidationError):
            ContributionBundleV0.model_validate(data)

    def test_empty_files_list_accepted(self) -> None:
        bundle = ContributionBundleV0.model_validate(
            _bundle("thermo", manifest={"sha256": None, "files": []})
        )
        assert bundle.manifest.files == []


class TestLocalRefRules:
    @pytest.mark.parametrize(
        "bad_key",
        [
            "ethanol",  # missing namespace
            "Species:ethanol",  # uppercase namespace
            "species: ethanol",  # whitespace in label
            "species:",  # empty label
            ":ethanol",  # empty namespace
            "species:ethanol!",  # disallowed char
            "species:123",  # purely numeric label (raw DB PK shape)
        ],
    )
    def test_malformed_local_ref_key_rejected(self, bad_key: str) -> None:
        data = _bundle(
            "thermo",
            local_refs={bad_key: {"record_type": "species", "label": "x"}},
        )
        with pytest.raises(ValidationError):
            ContributionBundleV0.model_validate(data)

    def test_unknown_record_type_rejected(self) -> None:
        data = _bundle(
            "thermo",
            local_refs={
                "species:ethanol": {"record_type": "not_a_type", "label": "ethanol"}
            },
        )
        with pytest.raises(ValidationError):
            ContributionBundleV0.model_validate(data)


class TestSourceInstanceRules:
    def test_hosted_source_instance_kind_rejected(self) -> None:
        data = _bundle("thermo")
        data["source_instance"]["instance_kind"] = "hosted"
        with pytest.raises(ValidationError):
            ContributionBundleV0.model_validate(data)


class TestSubmissionRules:
    def test_unknown_submission_source_kind_rejected(self) -> None:
        data = _bundle("thermo")
        data["submission"]["source_kind"] = "api"
        with pytest.raises(ValidationError):
            ContributionBundleV0.model_validate(data)


class TestNestedUploadValidation:
    """Existing thermo/kinetics upload validators must still fire when the
    payloads are embedded in a bundle."""

    def test_thermo_upload_without_scientific_content_rejected(self) -> None:
        bad_thermo = {
            "species_entry": {"smiles": "O", "charge": 0, "multiplicity": 1},
            "scientific_origin": "computed",
            "note": "identity-only, no science",
        }
        data = _bundle("thermo")
        data["records"]["thermo_uploads"] = [bad_thermo]
        with pytest.raises(ValidationError, match="at least one"):
            ContributionBundleV0.model_validate(data)

    def test_kinetics_upload_without_reactants_rejected(self) -> None:
        bad_kinetics = copy.deepcopy(_kinetics_upload())
        bad_kinetics["reaction"]["reactants"] = []
        data = _bundle("kinetics")
        data["records"]["kinetics_uploads"] = [bad_kinetics]
        with pytest.raises(ValidationError):
            ContributionBundleV0.model_validate(data)
