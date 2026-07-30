"""Consistency checks for persisted execution-environment manifests.

This detects storage corruption and drift: whether a stored row's denormalized
columns still agree with its canonical payload and its own content digest. It
is not an honesty check on the uploader — a manifest that fails here indicates a
bug, a partial write, or an out-of-band edit on our side.
"""

from typing import Any

from tckdb_schemas.software import normalize_software_name

from app.schemas.fragments.execution_environment import ExecutionEnvironmentManifestPayload


def manifest_integrity_evidence(
    manifest,
    *,
    calculation=None,
    expected_payload: ExecutionEnvironmentManifestPayload | None = None,
    expected_software_release_id: int | None = None,
    expected_workflow_tool_release_id: int | None = None,
) -> tuple[bool, dict[str, Any]]:
    """Return canonical, release-binding, and attachment consistency evidence."""
    try:
        payload = ExecutionEnvironmentManifestPayload.model_validate(manifest.canonical_json)
    except Exception as exc:
        return False, {
            "reason": "execution_environment_manifest_invalid",
            "error": type(exc).__name__,
        }
    canonical = payload.canonical_payload()
    software = manifest.software_release
    workflow = manifest.workflow_tool_release
    software_ok = bool(
        software
        and software.software
        and normalize_software_name(payload.software_release.name) == software.software.name
        and payload.software_release.version == software.version
        and payload.software_release.revision == software.revision
        and payload.software_release.build == software.build
    )
    workflow_ok = bool(
        (payload.workflow_tool_release is None and workflow is None)
        or (
            payload.workflow_tool_release is not None
            and workflow is not None
            and workflow.workflow_tool is not None
            and payload.workflow_tool_release.name == workflow.workflow_tool.name
            and payload.workflow_tool_release.version == workflow.version
            and payload.workflow_tool_release.git_commit == workflow.git_commit
        )
    )
    expected_ok = bool(
        (expected_payload is None or canonical == expected_payload.canonical_payload())
        and (
            expected_software_release_id is None
            or manifest.software_release_id == expected_software_release_id
        )
        and (
            expected_workflow_tool_release_id is None
            or manifest.workflow_tool_release_id == expected_workflow_tool_release_id
        )
    )
    attachment_ok = bool(
        calculation is None
        or (
            calculation.software_release_id == manifest.software_release_id
            and calculation.workflow_tool_release_id == manifest.workflow_tool_release_id
        )
    )
    valid = bool(
        manifest.schema_version == payload.schema_version
        and manifest.content_digest == payload.content_digest()
        and manifest.runtime_kind == payload.runtime_kind
        and manifest.runtime_locator == payload.runtime_locator
        and manifest.executable_locator == payload.executable_locator
        and manifest.closure_json == canonical["closure"]
        and manifest.canonical_json == canonical
        and software_ok
        and workflow_ok
        and expected_ok
        and attachment_ok
    )
    return valid, {
        "environment_ref": manifest.content_digest,
        "schema_version": manifest.schema_version,
        "content_digest_valid": payload.content_digest() == manifest.content_digest,
        "closure_count": len(canonical["closure"]),
        "release_bindings_valid": software_ok and workflow_ok and attachment_ok and expected_ok,
    }
