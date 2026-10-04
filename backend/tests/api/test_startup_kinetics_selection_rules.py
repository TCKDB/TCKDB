"""A bad pin on the kinetics-selection rule manifest fails the deploy, not a request.

``default_rules()`` refuses to build when the packaged XYG3 barrier manifest does not hash to the digest pinned
beside the rule. Left lazy, that refusal would first appear as a 500 on the first ``/kinetics/select`` request after
a deploy. ``create_app`` builds the registry at startup instead.
"""

from __future__ import annotations

import pytest

from app.api.app import create_app
from app.api.startup_checks import KineticsSelectionRulesError, validate_kinetics_selection_rules
from app.chemistry.kinetics_rules import xyg3_barrier_manifest as manifest
from app.services.kinetics_selection import rules as selection_rules


@pytest.fixture
def fresh_rule_caches():
    """Both caches are process-wide; a tampered build must not leak into, or out of, this test."""

    def clear() -> None:
        manifest.load_xyg3_barrier_manifest.cache_clear()
        selection_rules.default_rules.cache_clear()

    clear()
    yield
    clear()


def _tamper(tmp_path, monkeypatch, *, edit) -> None:
    original = manifest.MANIFEST_PATH.read_bytes()
    edited = edit(original)
    assert edited != original, "the edit changed nothing; the test would prove nothing"
    path = tmp_path / "xyg3_b3lyp_barrier_manifest.yaml"
    path.write_bytes(edited)
    monkeypatch.setattr(manifest, "MANIFEST_PATH", path)


def test_the_shipped_registry_builds_at_startup_and_is_the_one_requests_use(fresh_rule_caches):
    create_app()
    first = selection_rules.default_rules()
    assert next(iter(first)).rule_id == "K-XYG3-B3LYP-BARRIER"
    assert all(r.status != "active" for r in first)  # nothing in this release is active
    assert selection_rules.default_rules() is first


def test_a_tampered_manifest_stops_the_app_from_being_built(fresh_rule_caches, tmp_path, monkeypatch):
    _tamper(tmp_path, monkeypatch, edit=lambda raw: raw + b"\n# edited after the pin was taken\n")
    with pytest.raises(KineticsSelectionRulesError, match="registry failed to load"):
        create_app()


def test_a_member_edit_is_a_tampered_manifest_too(fresh_rule_caches, tmp_path, monkeypatch):
    _tamper(tmp_path, monkeypatch, edit=lambda raw: raw.replace(b"molecular_formula: HCl", b"molecular_formula: HBr", 1))
    with pytest.raises(KineticsSelectionRulesError):
        validate_kinetics_selection_rules()


def test_the_failure_names_its_cause_and_keeps_the_original_exception(fresh_rule_caches, tmp_path, monkeypatch):
    _tamper(tmp_path, monkeypatch, edit=lambda raw: raw + b" ")
    with pytest.raises(KineticsSelectionRulesError) as caught:
        validate_kinetics_selection_rules()
    assert isinstance(caught.value.__cause__, manifest.ManifestError)
    assert "does not match its pinned digest" in str(caught.value)


def test_a_missing_manifest_file_is_reported_as_missing_not_as_a_bad_digest(fresh_rule_caches, tmp_path, monkeypatch):
    monkeypatch.setattr(manifest, "MANIFEST_PATH", tmp_path / "gone.yaml")
    with pytest.raises(KineticsSelectionRulesError) as caught:
        validate_kinetics_selection_rules()
    assert isinstance(caught.value.__cause__, FileNotFoundError)
    assert "could not be read" in str(caught.value) and "pinned digest" not in str(caught.value)


def test_a_failed_audit_is_reported_as_such(fresh_rule_caches, monkeypatch):
    def refuse(_raw):
        raise manifest.ManifestError("manifest is not approved for activation")

    monkeypatch.setattr(manifest, "parse_xyg3_barrier_manifest", refuse)
    monkeypatch.setattr(selection_rules, "XYG3_MANIFEST_SHA256", None)
    with pytest.raises(KineticsSelectionRulesError, match="not approved for activation"):
        validate_kinetics_selection_rules()


def test_a_failed_build_is_not_cached_so_a_restored_manifest_loads(fresh_rule_caches, tmp_path, monkeypatch):
    with monkeypatch.context() as patch:
        _tamper(tmp_path, patch, edit=lambda raw: raw + b" ")
        with pytest.raises(KineticsSelectionRulesError):
            validate_kinetics_selection_rules()
    validate_kinetics_selection_rules()
