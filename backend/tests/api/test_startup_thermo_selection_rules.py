"""A bad pin on the thermo-selection rule manifest fails the deploy, not a request.

``default_rules()`` refuses to build when the packaged E1 manifest does not hash to the digest pinned
beside the rule. Left lazy, that refusal would first appear as a 500 on the first
``/thermo/select`` request after a deploy. ``create_app`` builds the registry at startup instead.
"""

from __future__ import annotations

import pytest

from app.api.app import create_app
from app.api.startup_checks import ThermoSelectionRulesError, validate_thermo_selection_rules
from app.chemistry.thermo_rules import e1_manifest
from app.services.thermo_selection import rules as selection_rules


@pytest.fixture
def fresh_rule_caches():
    """Both caches are process-wide; a tampered build must not leak into, or out of, this test."""

    def clear() -> None:
        e1_manifest.load_e1_manifest.cache_clear()
        selection_rules.default_rules.cache_clear()

    clear()
    yield
    clear()


def _tamper(tmp_path, monkeypatch, *, edit) -> None:
    original = e1_manifest.MANIFEST_PATH.read_bytes()
    edited = edit(original)
    assert edited != original, "the edit changed nothing; the test would prove nothing"
    path = tmp_path / "e1_manifest.yaml"
    path.write_bytes(edited)
    monkeypatch.setattr(e1_manifest, "MANIFEST_PATH", path)


def test_the_shipped_registry_builds_at_startup_and_is_the_one_requests_use(fresh_rule_caches):
    create_app()
    first = selection_rules.default_rules()
    assert [r.rule_id for r in first] == ["E1"]
    assert selection_rules.default_rules() is first


def test_a_tampered_manifest_stops_the_app_from_being_built(fresh_rule_caches, tmp_path, monkeypatch):
    _tamper(tmp_path, monkeypatch, edit=lambda raw: raw + b"\n# edited after the pin was taken\n")
    with pytest.raises(ThermoSelectionRulesError, match="registry failed to load"):
        create_app()


def test_a_member_edit_is_a_tampered_manifest_too(fresh_rule_caches, tmp_path, monkeypatch):
    _tamper(tmp_path, monkeypatch, edit=lambda raw: raw.replace(b"Methane", b"Methanol", 1))
    with pytest.raises(ThermoSelectionRulesError):
        validate_thermo_selection_rules()


def test_the_failure_names_its_cause_and_keeps_the_original_exception(fresh_rule_caches, tmp_path, monkeypatch):
    _tamper(tmp_path, monkeypatch, edit=lambda raw: raw + b" ")
    with pytest.raises(ThermoSelectionRulesError) as caught:
        validate_thermo_selection_rules()
    assert isinstance(caught.value.__cause__, e1_manifest.ManifestError)
    assert "ManifestError" in str(caught.value)


def test_a_failed_build_is_not_cached_so_a_restored_manifest_loads(fresh_rule_caches, tmp_path, monkeypatch):
    with monkeypatch.context() as patch:
        _tamper(tmp_path, patch, edit=lambda raw: raw + b" ")
        with pytest.raises(ThermoSelectionRulesError):
            validate_thermo_selection_rules()
    validate_thermo_selection_rules()
