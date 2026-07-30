from tckdb_schemas.workflows.computed_reaction_upload import ComputedReactionCalculationIn
from tckdb_schemas.workflows.computed_species_upload import CalculationInBundle
import copy

import pytest
from pydantic import ValidationError


def _environment() -> dict:
    return {
        "schema_version": "tckdb.execution-environment.v1",
        "software_release": {"name": "Gaussian", "version": "16"},
        "platform": "linux", "architecture": "x86_64",
        "runtime": {"runtime_kind": "container", "image": "registry.example/arc@sha256:" + "a" * 64},
        "executable": {"locator": "file:///opt/arc/bin/arc", "digest": "sha256:" + "b" * 64},
        "closure": [
            {"role": "runtime", "locator": "registry.example/arc@sha256:" + "a" * 64, "digest": "sha256:" + "a" * 64},
            {"role": "executable", "locator": "file:///opt/arc/bin/arc", "digest": "sha256:" + "b" * 64},
        ],
    }


def test_computed_bundle_calculations_accept_optional_environment_manifest():
    common = {"key": "calc-1", "type": "sp", "software_release": {"name": "Gaussian", "version": "16"},
              "level_of_theory": {"method": "wb97xd", "basis": "def2-svp"}, "execution_environment": _environment()}
    assert CalculationInBundle.model_validate(common).execution_environment is not None
    assert ComputedReactionCalculationIn.model_validate(common).execution_environment is not None


def test_computed_bundle_calculations_remain_backward_compatible_without_manifest():
    common = {
        "key": "calc-1",
        "type": "sp",
        "software_release": {"name": "Gaussian", "version": "16"},
        "level_of_theory": {"method": "wb97xd", "basis": "def2-svp"},
    }
    assert CalculationInBundle.model_validate(common).execution_environment is None
    assert ComputedReactionCalculationIn.model_validate(common).execution_environment is None


def test_manifest_canonicalizes_closure_order_independently():
    from tckdb_schemas.fragments.execution_environment import ExecutionEnvironmentManifestPayload

    first = ExecutionEnvironmentManifestPayload.model_validate(_environment())
    reversed_payload = _environment()
    reversed_payload["closure"].reverse()
    second = ExecutionEnvironmentManifestPayload.model_validate(reversed_payload)
    assert first.canonical_payload() == second.canonical_payload()
    assert first.content_digest() == second.content_digest()


def test_manifest_release_bindings_are_canonical_and_exclude_mutable_release_metadata():
    from tckdb_schemas.fragments.execution_environment import ExecutionEnvironmentManifestPayload

    first_data = _environment()
    first_data["workflow_tool_release"] = {"name": " ARC ", "version": " 1.0 ", "git_commit": "abc123"}
    first = ExecutionEnvironmentManifestPayload.model_validate(first_data)
    second = ExecutionEnvironmentManifestPayload.model_validate(
        {**_environment(), "workflow_tool_release": {"name": "ARC", "version": "1.0", "git_commit": "abc123"}}
    )
    assert first.canonical_payload()["software_release"] == {
        "name": "Gaussian", "version": "16", "revision": None, "build": None
    }
    assert first.canonical_payload()["workflow_tool_release"] == {
        "name": "ARC", "version": "1.0", "git_commit": "abc123"
    }
    assert first.content_digest() == second.content_digest()


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("runtime", "image"), "registry.example/arc:latest"),
        (("runtime", "image"), "registry.example/arc@sha256:" + "a" * 64 + "?token=x"),
        (("runtime", "image"), "registry.example/arc@sha256:" + "a" * 64 + "#fragment"),
        (("runtime", "image"), "registry.example/token/arc@sha256:" + "a" * 64),
        (("executable", "locator"), "https://user@host/arc"),
        (("executable", "locator"), "file:///opt/password/arc"),
        (("closure", 1, "locator"), "file:///opt/api_key/arc"),
    ],
)
def test_manifest_rejects_secrets_and_non_immutable_locators(path, value):
    payload = copy.deepcopy(_environment())
    target = payload
    for part in path[:-1]:
        target = target[part]
    target[path[-1]] = value
    from tckdb_schemas.fragments.execution_environment import ExecutionEnvironmentManifestPayload

    with pytest.raises(ValidationError):
        ExecutionEnvironmentManifestPayload.model_validate(payload)


def test_conda_requires_exact_lockfile_closure_entry():
    from tckdb_schemas.fragments.execution_environment import ExecutionEnvironmentManifestPayload

    payload = _environment()
    lock_digest = "sha256:" + "c" * 64
    payload["runtime"] = {
        "runtime_kind": "conda",
        "lockfile": {"locator": "file:///locks/arc.lock", "digest": lock_digest},
    }
    payload["closure"][0] = {
        "role": "lockfile", "locator": "file:///locks/arc.lock", "digest": "sha256:" + "d" * 64,
    }
    with pytest.raises(ValidationError):
        ExecutionEnvironmentManifestPayload.model_validate(payload)


def test_hpc_requires_both_resolved_environment_and_dependency_manifest():
    from tckdb_schemas.fragments.execution_environment import ExecutionEnvironmentManifestPayload

    payload = _environment()
    resolved = "sha256:" + "c" * 64
    dependencies = "sha256:" + "d" * 64
    payload["runtime"] = {
        "runtime_kind": "hpc_module",
        "modules": [{"name": "gaussian", "version": "16-C.01"}],
        "resolved_environment_digest": resolved,
        "dependency_manifest_digest": dependencies,
    }
    payload["closure"] = [
        payload["closure"][1],
        {"role": "module_closure", "locator": "file:///env/resolved.json", "digest": resolved},
    ]
    with pytest.raises(ValidationError):
        ExecutionEnvironmentManifestPayload.model_validate(payload)


@pytest.mark.parametrize("field", ["name", "version"])
def test_hpc_module_text_rejects_secret_keywords(field):
    from tckdb_schemas.fragments.execution_environment import ExecutionEnvironmentManifestPayload

    payload = _environment()
    payload["runtime"] = {
        "runtime_kind": "hpc_module",
        "modules": [{"name": "gaussian", "version": "16"}],
        "resolved_environment_digest": "sha256:" + "c" * 64,
        "dependency_manifest_digest": "sha256:" + "d" * 64,
    }
    payload["runtime"]["modules"][0][field] = "api_token"
    payload["closure"] = [
        payload["closure"][1],
        {"role": "module_closure", "locator": "file:///env/resolved.json", "digest": "sha256:" + "c" * 64},
        {"role": "dependency_manifest", "locator": "file:///env/deps.json", "digest": "sha256:" + "d" * 64},
    ]
    with pytest.raises(ValidationError):
        ExecutionEnvironmentManifestPayload.model_validate(payload)
