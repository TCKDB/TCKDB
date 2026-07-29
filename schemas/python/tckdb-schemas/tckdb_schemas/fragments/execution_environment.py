"""Typed, secret-safe execution-environment closure contracts.

The manifest deliberately describes only environments that can be pinned by
bytes.  Human-readable module names and paths are useful locators, never the
evidence of reproducibility: every executable and closure item has its own
content digest.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Literal

from pydantic import Field, field_validator, model_validator

from tckdb_schemas.common import SchemaBase

Digest = str
_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_OCI_IMAGE = re.compile(r"^[a-z0-9][a-z0-9._/-]*@sha256:[0-9a-f]{64}$")
_SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,127}$")
_SECRET = re.compile(r"(?:password|secret|token|api[_-]?key|authorization|credential)", re.I)


def _validate_digest(value: str, *, field: str) -> str:
    if not _DIGEST.fullmatch(value):
        raise ValueError(f"{field} must be lowercase sha256:<64 hex>")
    return value


def _validate_locator(value: str, *, field: str) -> str:
    """Allow a locator, but never URI credentials, query/fragment, or secrets."""
    if not value or _SECRET.search(value) or "?" in value or "#" in value:
        raise ValueError(f"{field} must not contain credentials, secrets, query, or fragment")
    if "://" in value:
        authority = value.split("://", 1)[1].split("/", 1)[0]
        if "@" in authority:
            raise ValueError(f"{field} must not contain URI userinfo")
    return value


def _validate_safe_identifier(value: str, *, field: str) -> str:
    if _SECRET.search(value) or not _SAFE_NAME.fullmatch(value):
        raise ValueError(f"{field} must be a secret-free strict identifier")
    return value


class ContentAddressedReference(SchemaBase):
    """One safe locator whose referenced bytes are fixed by ``digest``."""

    locator: str = Field(min_length=1, max_length=2048)
    digest: Digest

    @field_validator("locator")
    @classmethod
    def validate_locator(cls, value: str) -> str:
        return _validate_locator(value, field="locator")

    @field_validator("digest")
    @classmethod
    def validate_digest(cls, value: str) -> str:
        return _validate_digest(value, field="digest")


class EnvironmentClosureEntry(ContentAddressedReference):
    """A named, unambiguous content-addressed component of the closure."""

    role: Literal["runtime", "executable", "lockfile", "module_closure", "dependency_manifest"]


class ModuleDescription(SchemaBase):
    """Descriptive HPC module identity; never sufficient closure evidence."""

    name: str = Field(min_length=1, max_length=128)
    version: str = Field(min_length=1, max_length=128)

    @field_validator("name", "version")
    @classmethod
    def validate_name(cls, value: str) -> str:
        return _validate_safe_identifier(value, field="module names and versions")


class ContainerRuntime(SchemaBase):
    runtime_kind: Literal["container"]
    image: str = Field(min_length=1, max_length=2048)

    @field_validator("image")
    @classmethod
    def validate_image(cls, value: str) -> str:
        if _SECRET.search(value) or not _OCI_IMAGE.fullmatch(value):
            raise ValueError("container image must be an immutable OCI @sha256:<64 lowercase hex> reference")
        return value

    @property
    def runtime_locator(self) -> str:
        return self.image


class CondaRuntime(SchemaBase):
    runtime_kind: Literal["conda"]
    lockfile: ContentAddressedReference

    @property
    def runtime_locator(self) -> str:
        return self.lockfile.locator


class HPCModuleRuntime(SchemaBase):
    runtime_kind: Literal["hpc_module"]
    modules: list[ModuleDescription] = Field(min_length=1, max_length=64)
    resolved_environment_digest: Digest
    dependency_manifest_digest: Digest

    @field_validator("resolved_environment_digest", "dependency_manifest_digest")
    @classmethod
    def validate_digest(cls, value: str) -> str:
        return _validate_digest(value, field="HPC closure digest")

    @model_validator(mode="after")
    def unique_modules(self):
        identities = [(module.name, module.version) for module in self.modules]
        if len(identities) != len(set(identities)):
            raise ValueError("HPC module descriptions must be unique")
        return self

    @property
    def runtime_locator(self) -> str:
        return "module://" + ",".join(f"{module.name}/{module.version}" for module in self.modules)


class ExecutionEnvironmentManifestPayload(SchemaBase):
    """A closed execution environment for container, conda, or HPC module runs.

    There is intentionally no bare-metal/VM variant: those deployments cannot
    make a rerunnable claim without an equivalent closed image or lockfile.
    """

    schema_version: Literal["tckdb.execution-environment.v1"]
    platform: Literal["linux", "darwin", "windows"]
    architecture: Literal["x86_64", "aarch64", "ppc64le"]
    runtime: ContainerRuntime | CondaRuntime | HPCModuleRuntime = Field(discriminator="runtime_kind")
    executable: ContentAddressedReference
    closure: list[EnvironmentClosureEntry] = Field(min_length=2, max_length=5)

    @model_validator(mode="after")
    def validate_closed(self):
        roles = [entry.role for entry in self.closure]
        locators = [entry.locator for entry in self.closure]
        if len(roles) != len(set(roles)) or len(locators) != len(set(locators)):
            raise ValueError("closure roles and locators must be unique")
        by_role = {entry.role: entry for entry in self.closure}
        executable = by_role.get("executable")
        if executable is None or (
            executable.locator != self.executable.locator or executable.digest != self.executable.digest
        ):
            raise ValueError("closure must contain the exact executable reference")
        if isinstance(self.runtime, ContainerRuntime):
            runtime = by_role.get("runtime")
            if runtime is None or runtime.locator != self.runtime.image:
                raise ValueError("closure runtime must name the immutable container image")
            image_digest = self.runtime.image.rsplit("@", 1)[1]
            if runtime.digest != image_digest:
                raise ValueError("container closure digest must equal the OCI image digest")
        elif isinstance(self.runtime, CondaRuntime):
            lockfile = by_role.get("lockfile")
            if lockfile is None or (
                lockfile.locator != self.runtime.lockfile.locator or lockfile.digest != self.runtime.lockfile.digest
            ):
                raise ValueError("closure must contain the exact conda lockfile reference")
        else:
            closure_entry = by_role.get("module_closure")
            dependency_entry = by_role.get("dependency_manifest")
            if closure_entry is None or closure_entry.digest != self.runtime.resolved_environment_digest:
                raise ValueError("closure must contain the resolved HPC environment digest")
            if dependency_entry is None or dependency_entry.digest != self.runtime.dependency_manifest_digest:
                raise ValueError("closure must contain the HPC dependency manifest digest")
        return self

    @property
    def runtime_kind(self) -> str:
        return self.runtime.runtime_kind

    @property
    def runtime_locator(self) -> str:
        return self.runtime.runtime_locator

    @property
    def executable_locator(self) -> str:
        return self.executable.locator

    def canonical_payload(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "platform": self.platform,
            "architecture": self.architecture,
            "runtime": self.runtime.model_dump(mode="json"),
            "executable": self.executable.model_dump(mode="json"),
            "closure": [entry.model_dump(mode="json") for entry in sorted(self.closure, key=lambda entry: entry.role)],
        }

    def content_digest(self) -> Digest:
        payload = json.dumps(self.canonical_payload(), sort_keys=True, separators=(",", ":")).encode()
        return "sha256:" + hashlib.sha256(payload).hexdigest()
