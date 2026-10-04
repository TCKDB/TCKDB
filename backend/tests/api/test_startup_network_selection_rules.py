"""A bad pin on the network-selection rule manifest fails the deploy, not a request."""

from __future__ import annotations

import pytest

from app.api.app import create_app
from app.api.startup_checks import NetworkSelectionRulesError, validate_network_selection_rules
from app.chemistry.network_rules import manifest as manifest_module
from app.services.network_selection import rules as selection_rules


@pytest.fixture
def fresh_rule_cache():
    selection_rules.default_rules.cache_clear()
    yield
    selection_rules.default_rules.cache_clear()


def test_the_shipped_registry_builds_at_startup_and_is_cached(fresh_rule_cache):
    create_app()
    first = selection_rules.default_rules()
    assert len(first) == 6 and selection_rules.default_rules() is first


def test_a_tampered_manifest_stops_the_app_from_being_built(fresh_rule_cache, tmp_path, monkeypatch):
    path = tmp_path / "network_rule_candidates.yaml"
    path.write_bytes(manifest_module.MANIFEST_PATH.read_bytes() + b"\n# edited after the pin was taken\n")
    monkeypatch.setattr(manifest_module, "MANIFEST_PATH", path)
    with pytest.raises(NetworkSelectionRulesError, match="pinned digest"):
        create_app()


def test_a_missing_manifest_file_is_named_not_swallowed(fresh_rule_cache, tmp_path, monkeypatch):
    monkeypatch.setattr(manifest_module, "MANIFEST_PATH", tmp_path / "absent.yaml")
    with pytest.raises(NetworkSelectionRulesError, match="could not be read") as caught:
        validate_network_selection_rules()
    assert isinstance(caught.value.__cause__, OSError)


def test_a_rule_refused_at_construction_stops_the_boot(fresh_rule_cache, monkeypatch):
    monkeypatch.setattr(selection_rules, "NETWORK_RULE_MANIFEST_SHA256", "0" * 64)
    with pytest.raises(NetworkSelectionRulesError, match="pinned digest"):
        validate_network_selection_rules()
