import pytest
from pydantic import ValidationError

from app.schemas.fragments.execution_environment import ExecutionEnvironmentManifestPayload


def _payload() -> dict:
    return {
        "schema_version": "tckdb.execution-environment.v1",
        "software_release": {"name": "Gaussian", "version": "16"},
        "runtime": {"runtime_kind": "container", "image": "registry.example/arc@sha256:" + "a" * 64},
        "executable": {"locator": "file:///opt/arc/bin/arc", "digest": "sha256:" + "b" * 64},
        "closure": [
            {"role": "runtime", "locator": "registry.example/arc@sha256:" + "a" * 64, "digest": "sha256:" + "a" * 64},
            {"role": "executable", "locator": "file:///opt/arc/bin/arc", "digest": "sha256:" + "b" * 64},
        ],
    }


def test_manifest_is_content_stable_when_closure_order_changes():
    first = ExecutionEnvironmentManifestPayload.model_validate(_payload())
    second_data = _payload()
    second_data["closure"].reverse()
    second = ExecutionEnvironmentManifestPayload.model_validate(second_data)
    assert first.content_digest() == second.content_digest()


@pytest.mark.parametrize(
    "data",
    [
        {"runtime": {"runtime_kind": "container", "image": "registry.example/arc:tag"}},
        {"executable": {"locator": "https://token@host/arc", "digest": "sha256:" + "b" * 64}},
    ],
)
def test_manifest_rejects_mutable_or_secret_bearing_locator(data):
    payload = _payload()
    payload.update(data)
    with pytest.raises(ValidationError):
        ExecutionEnvironmentManifestPayload.model_validate(payload)


def _described_payload() -> dict:
    """The ordinary shared-cluster case: named modules, no digests."""
    return {
        "schema_version": "tckdb.execution-environment.v1",
        "software_release": {"name": "Gaussian", "version": "16"},
        "runtime": {
            "runtime_kind": "described",
            "description": "Zeus cluster site install",
            "modules": [{"name": "gaussian", "version": "16.C01"}],
        },
        "executable": {"locator": "file:///opt/g16/g16"},
    }


def test_described_runtime_is_accepted_without_any_digest():
    """A user who ran ``module load`` can record their environment."""
    payload = ExecutionEnvironmentManifestPayload.model_validate(_described_payload())
    assert payload.closure_tier == "described"
    assert payload.runtime_locator == "module://gaussian/16.C01"
    assert payload.executable_locator == "file:///opt/g16/g16"
    assert payload.canonical_payload()["closure"] == []
    assert payload.content_digest().startswith("sha256:")


def test_described_runtime_falls_back_to_its_description_as_locator():
    data = _described_payload()
    data["runtime"]["modules"] = []
    payload = ExecutionEnvironmentManifestPayload.model_validate(data)
    assert payload.runtime_locator == "Zeus cluster site install"


def test_pinned_runtime_reports_the_content_addressed_tier():
    assert ExecutionEnvironmentManifestPayload.model_validate(_payload()).closure_tier == "content_addressed"


def test_described_and_pinned_manifests_have_distinct_identities():
    described = ExecutionEnvironmentManifestPayload.model_validate(_described_payload())
    pinned = ExecutionEnvironmentManifestPayload.model_validate(_payload())
    assert described.content_digest() != pinned.content_digest()


@pytest.mark.parametrize(
    ("mutation", "reason"),
    [
        ({"closure": [{"role": "runtime", "locator": "x", "digest": "sha256:" + "c" * 64}]}, "closure on described"),
        ({"runtime": {"runtime_kind": "described", "description": "api_key=abc"}}, "secret in description"),
    ],
)
def test_described_runtime_rejects_incoherent_or_unsafe_input(mutation, reason):
    payload = _described_payload()
    payload.update(mutation)
    with pytest.raises(ValidationError):
        ExecutionEnvironmentManifestPayload.model_validate(payload)


def test_pinned_runtime_still_requires_its_executable_digest():
    """Relaxing the described tier must not weaken a byte-closure claim."""
    payload = _payload()
    payload["executable"] = {"locator": "file:///opt/arc/bin/arc"}
    with pytest.raises(ValidationError):
        ExecutionEnvironmentManifestPayload.model_validate(payload)


def test_pinned_runtime_still_requires_a_full_closure():
    payload = _payload()
    payload["closure"] = [payload["closure"][0]]
    with pytest.raises(ValidationError):
        ExecutionEnvironmentManifestPayload.model_validate(payload)
