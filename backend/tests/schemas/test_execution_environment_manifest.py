import pytest
from pydantic import ValidationError

from app.schemas.fragments.execution_environment import ExecutionEnvironmentManifestPayload


def _payload() -> dict:
    return {
        "schema_version": "tckdb.execution-environment.v1",
        "platform": "linux",
        "architecture": "x86_64",
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
