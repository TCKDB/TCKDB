"""Typed, secret-safe execution-environment contracts.

The manifest records an environment at one of two tiers, and **both are
accepted**:

``described``
    A named-but-unpinned environment: module names, an environment name, a
    human description. This is the common case — someone who runs
    ``module load gaussian/16`` on a shared cluster cannot produce byte digests
    for that install, and refusing the record would mean storing nothing where
    useful provenance was available.

``container`` / ``conda`` / ``hpc_module``
    A byte-pinned closure: an immutable OCI digest, a content-addressed
    lockfile, or resolved-environment plus dependency-manifest digests.

The tier is readable from ``runtime_kind`` and is deliberately not stored
separately — a derived fact with a second, un-arbitrated home is how the two
copies come to disagree.

Neither tier is scored by the reproducibility rubric. The manifest is additive
provenance: it says what an uploader was able to tell us, and a missing digest
means the digest was unavailable, not that a claim is suspect.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Literal

from pydantic import Field, field_validator, model_validator

from tckdb_schemas.common import SchemaBase
from tckdb_schemas.software import normalize_software_name
from tckdb_schemas.utils import normalize_optional_text, normalize_required_text

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


class ExecutableReference(SchemaBase):
    """Where the scientific executable was, and its digest when that is known.

    ``digest`` is optional because the great majority of uploaders run a shared
    or site-installed binary and have no reason to have hashed it. A pinned
    ``runtime_kind`` still requires it, because a byte closure that omits the
    executable is not a closure.
    """

    locator: str = Field(min_length=1, max_length=2048)
    digest: Digest | None = None

    @field_validator("locator")
    @classmethod
    def validate_locator(cls, value: str) -> str:
        return _validate_locator(value, field="locator")

    @field_validator("digest")
    @classmethod
    def validate_digest(cls, value: str | None) -> str | None:
        return None if value is None else _validate_digest(value, field="digest")


class ModuleDescription(SchemaBase):
    """Descriptive HPC module identity; never sufficient closure evidence."""

    name: str = Field(min_length=1, max_length=128)
    version: str = Field(min_length=1, max_length=128)

    @field_validator("name", "version")
    @classmethod
    def validate_name(cls, value: str) -> str:
        return _validate_safe_identifier(value, field="module names and versions")


class DescribedRuntime(SchemaBase):
    """A named-but-unpinned environment — the ordinary shared-cluster case.

    Accepted on purpose. ``module load gaussian/16`` on a site install is real
    provenance: it is enough to compare two records and to ask an operator what
    was there, even though it fixes no bytes.
    """

    runtime_kind: Literal["described"]
    description: str = Field(min_length=1, max_length=512)
    modules: list[ModuleDescription] = Field(default_factory=list, max_length=64)

    @field_validator("description")
    @classmethod
    def validate_description(cls, value: str) -> str:
        if _SECRET.search(value):
            raise ValueError("description must not contain credentials or secrets")
        return value

    @model_validator(mode="after")
    def unique_modules(self):
        identities = [(module.name, module.version) for module in self.modules]
        if len(identities) != len(set(identities)):
            raise ValueError("module descriptions must be unique")
        return self

    @property
    def runtime_locator(self) -> str:
        if self.modules:
            return "module://" + ",".join(f"{module.name}/{module.version}" for module in self.modules)
        return self.description


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


class ScientificSoftwareReleaseIdentity(SchemaBase):
    """Portable identity of the scientific executable's declared release.

    This deliberately contains only the four fields used by the software
    release registry's dedupe identity.  Dates and notes are descriptive,
    mutable provenance and must not affect a manifest's byte identity.
    """

    name: str = Field(min_length=1)
    version: str | None = None
    revision: str | None = None
    build: str | None = None

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        return normalize_software_name(value)

    @model_validator(mode="after")
    def normalize_optional_fields(self):
        self.version = normalize_optional_text(self.version)
        self.revision = normalize_optional_text(self.revision)
        self.build = normalize_optional_text(self.build)
        return self


class WorkflowToolReleaseIdentity(SchemaBase):
    """Portable identity of the optional workflow-tool code state."""

    name: str = Field(min_length=1)
    version: str | None = None
    git_commit: str | None = Field(default=None, min_length=1, max_length=40)

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        return normalize_required_text(value)

    @model_validator(mode="after")
    def normalize_optional_fields(self):
        self.version = normalize_optional_text(self.version)
        self.git_commit = normalize_optional_text(self.git_commit)
        return self


class ExecutionEnvironmentManifestPayload(SchemaBase):
    """An execution environment recorded at the tier the uploader could supply.

    A ``described`` runtime carries no digests and is fully acceptable; the
    pinned runtimes additionally fix the environment by bytes. See the module
    docstring for why both are accepted.
    """

    schema_version: Literal["tckdb.execution-environment.v1"]
    runtime: DescribedRuntime | ContainerRuntime | CondaRuntime | HPCModuleRuntime = Field(
        discriminator="runtime_kind"
    )
    software_release: ScientificSoftwareReleaseIdentity
    workflow_tool_release: WorkflowToolReleaseIdentity | None = None
    executable: ExecutableReference
    closure: list[EnvironmentClosureEntry] = Field(default_factory=list, max_length=5)

    @property
    def closure_tier(self) -> Literal["described", "content_addressed"]:
        """Which tier this manifest reached. Derived from ``runtime_kind``."""
        return "described" if isinstance(self.runtime, DescribedRuntime) else "content_addressed"

    @model_validator(mode="after")
    def validate_closure(self):
        roles = [entry.role for entry in self.closure]
        locators = [entry.locator for entry in self.closure]
        if len(roles) != len(set(roles)) or len(locators) != len(set(locators)):
            raise ValueError("closure roles and locators must be unique")
        if isinstance(self.runtime, DescribedRuntime):
            if self.closure:
                raise ValueError(
                    "a described runtime carries no closure digests; use a pinned runtime_kind to record them"
                )
            return self
        if self.executable.digest is None:
            raise ValueError("a pinned runtime requires the executable digest")
        if len(self.closure) < 2:
            raise ValueError("a pinned runtime requires at least the runtime and executable closure entries")
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
            "runtime": self.runtime.model_dump(mode="json"),
            "software_release": self.software_release.model_dump(mode="json"),
            "workflow_tool_release": (
                self.workflow_tool_release.model_dump(mode="json") if self.workflow_tool_release else None
            ),
            "executable": self.executable.model_dump(mode="json"),
            "closure": [entry.model_dump(mode="json") for entry in sorted(self.closure, key=lambda entry: entry.role)],
        }

    def content_digest(self) -> Digest:
        payload = json.dumps(self.canonical_payload(), sort_keys=True, separators=(",", ":")).encode()
        return "sha256:" + hashlib.sha256(payload).hexdigest()
