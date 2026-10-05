"""A bad pin on the structure-selection rule manifest fails the deploy, not a request.

``default_rules()`` refuses to build when the packaged candidate manifest does not hash to the digest pinned beside the
rules, and a wheel that did not carry the file could not read it at all. Left lazy, either would first appear as a 500
on the first structure selection after a deploy. ``create_app`` builds the registry at startup instead.
"""

from __future__ import annotations

import pytest

from app.api.app import create_app
from app.api.startup_checks import StructureSelectionRulesError, validate_structure_selection_rules
from app.chemistry.structure_rules import manifest as manifest_module
from app.services.structure_selection import rules as selection_rules


@pytest.fixture
def fresh_rule_cache():
    """The registry is cached process-wide; a tampered build must not leak into, or out of, this test."""
    selection_rules.default_rules.cache_clear()
    yield
    selection_rules.default_rules.cache_clear()


def test_the_shipped_registry_builds_at_startup_and_is_the_one_requests_use(fresh_rule_cache):
    create_app()
    first = selection_rules.default_rules()
    assert {r.rule_id for r in first} == {
        "S-ACONFL-LNO-LARGE-BASIS",
        "S-ACONFL-DLPNO-F12-VERYTIGHT",
        "S-FIXED-MODEL-CONVERGENCE",
    }
    assert all(r.status != "active" for r in first)  # nothing in this release is active
    assert selection_rules.default_rules() is first


def test_a_tampered_manifest_stops_the_app_from_being_built(fresh_rule_cache, tmp_path, monkeypatch):
    path = tmp_path / "structure_rule_candidates.yaml"
    path.write_bytes(manifest_module.MANIFEST_PATH.read_bytes() + b"\n# edited after the pin was taken\n")
    monkeypatch.setattr(manifest_module, "MANIFEST_PATH", path)
    with pytest.raises(StructureSelectionRulesError, match="registry failed to load"):
        create_app()


def test_a_missing_manifest_file_is_a_startup_failure_that_names_why(fresh_rule_cache, tmp_path, monkeypatch):
    """What a wheel without the package data would do: the file is simply not there."""
    monkeypatch.setattr(manifest_module, "MANIFEST_PATH", tmp_path / "absent.yaml")
    with pytest.raises(StructureSelectionRulesError, match="could not be read") as exc:
        validate_structure_selection_rules()
    assert isinstance(exc.value.__cause__, OSError)


def test_a_refused_entry_stops_startup_with_the_manifest_error_kept(fresh_rule_cache, tmp_path, monkeypatch):
    original = manifest_module.MANIFEST_PATH.read_bytes()
    edited = original.replace(b"activation_approved: false", b"activation_approved: true", 1)
    assert edited != original
    path = tmp_path / "structure_rule_candidates.yaml"
    path.write_bytes(edited)
    monkeypatch.setattr(manifest_module, "MANIFEST_PATH", path)
    with pytest.raises(StructureSelectionRulesError, match="refused") as exc:
        validate_structure_selection_rules()
    assert isinstance(exc.value.__cause__, manifest_module.ManifestError)
