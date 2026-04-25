"""Contribution bundle v0 — portable contribution format schemas.

These schemas define the *format* of a contribution bundle that moves
selected scientific records from a local/lab TCKDB instance toward the
hosted/community TCKDB instance.

Bundle v0 is a schema/format milestone only. It does **not** export from
the database, import into hosted, create submissions, or perform any
network/DB action. Hosted identity resolution and moderation happen later,
during dry-run/import milestones (see DR-0023).

These schemas are bundle-format schemas, not public route schemas. They
should not be wired into any FastAPI router.
"""

from __future__ import annotations

import re
from datetime import datetime
from enum import Enum
from typing import Literal, Self

from pydantic import Field, model_validator

from app.schemas.common import SchemaBase
from app.schemas.workflows.kinetics_upload import KineticsUploadRequest
from app.schemas.workflows.thermo_upload import ThermoUploadRequest

BUNDLE_FORMAT: Literal["tckdb-contribution-bundle"] = "tckdb-contribution-bundle"
BUNDLE_VERSION: Literal["0.1"] = "0.1"

# Bundle-local reference keys are namespaced strings like ``species:ethanol``.
# The namespace is one or more lowercase ascii words separated by ``_``,
# the local label is alnum + ``_-.``. Numeric-only labels are rejected to
# discourage smuggling raw DB primary keys as canonical local refs.
_LOCAL_REF_KEY_RE = re.compile(r"^[a-z][a-z0-9_]*:[A-Za-z0-9][A-Za-z0-9_\-.]*$")
_NUMERIC_LABEL_RE = re.compile(r"^\d+$")


class BundleKind(str, Enum):
    """Bundle families supported in v0.

    Future families (``statmech``, ``transport``, ``network``, ``mixed``,
    ``computed_reaction``) are intentionally not allowed yet — accepting them
    in v0 would silently let bundles ship that no exporter or importer is
    actually validating.
    """

    thermo = "thermo"
    kinetics = "kinetics"


class BundleSourceInstanceKind(str, Enum):
    """Where the bundle was exported from.

    ``hosted`` is intentionally excluded in v0; the contribution direction
    is local→hosted, not hosted→hosted.
    """

    local = "local"
    lab_server = "lab_server"


class BundleSubmissionSourceKind(str, Enum):
    """Bundle-format-level submission source kind.

    This is **not** the same as ``app.db.models.common.SubmissionSourceKind``.
    Adding ``local_bundle`` to the database enum is deferred to the
    hosted-import milestone (see DR-0023). The bundle format only states
    "this submission came from a local bundle"; the hosted instance maps
    that to its own submission machinery later.
    """

    local_bundle = "local_bundle"


class BundleLocalRefRecordType(str, Enum):
    """Lightweight tag for what a local ref points at.

    This is informational only. The hosted importer is responsible for
    resolving the actual scientific identity later.
    """

    species = "species"
    species_entry = "species_entry"
    reaction = "reaction"
    transition_state = "transition_state"
    calculation = "calculation"
    thermo = "thermo"
    kinetics = "kinetics"
    literature = "literature"


class BundleSourceInstance(SchemaBase):
    """Metadata about the TCKDB instance that produced the bundle."""

    instance_kind: BundleSourceInstanceKind
    instance_name: str = Field(min_length=1)
    schema_version: str = Field(min_length=1)
    software_version: str | None = None
    created_by_local_user: str | None = None
    notes: str | None = None


class BundleExporter(SchemaBase):
    """Provenance-only metadata about who exported the bundle.

    This is **not** the hosted actor identity; the hosted importer uses
    its own authenticated user, established at import time.
    """

    local_user_label: str = Field(min_length=1)
    orcid: str | None = None
    affiliation: str | None = None
    email: str | None = None
    notes: str | None = None


class BundleSubmissionMetadata(SchemaBase):
    """Bundle-level submission metadata.

    The hosted instance maps this to its own ``submission`` row at import
    time; the bundle does not create submissions.
    """

    title: str = Field(min_length=1)
    summary: str = Field(min_length=1)
    source_kind: BundleSubmissionSourceKind = BundleSubmissionSourceKind.local_bundle


class BundleLocalRefEntry(SchemaBase):
    """Lightweight metadata about a bundle-local reference key."""

    record_type: BundleLocalRefRecordType
    label: str = Field(min_length=1)
    note: str | None = None


class BundleRecordSet(SchemaBase):
    """Container for the scientific upload-equivalent payloads.

    Reuses existing workflow upload schemas directly so nested scientific
    validation runs through the same code paths as a normal API upload.
    """

    thermo_uploads: list[ThermoUploadRequest] = Field(default_factory=list)
    kinetics_uploads: list[KineticsUploadRequest] = Field(default_factory=list)


class BundleManifestFile(SchemaBase):
    """A single file entry in the bundle manifest."""

    path: str = Field(min_length=1)
    sha256: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")
    size_bytes: int | None = Field(default=None, ge=0)
    content_type: str | None = None
    role: str | None = None


class BundleManifest(SchemaBase):
    """Integrity metadata for any external artifacts shipped with the bundle.

    Artifact packaging is not implemented in v0; this only defines the
    manifest *shape* so future artifact bundling has a place to land.
    """

    sha256: str | None = Field(
        default=None, min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$"
    )
    files: list[BundleManifestFile] = Field(default_factory=list)
    created_by_tool: str | None = None
    notes: str | None = None

    @model_validator(mode="after")
    def validate_unique_paths(self) -> Self:
        paths = [f.path for f in self.files]
        if len(set(paths)) != len(paths):
            raise ValueError("manifest.files entries must have unique 'path' values.")
        return self


class ContributionBundleV0(SchemaBase):
    """Top-level contribution bundle v0 envelope.

    Validates without database access. Hosted identity resolution,
    deduplication, and moderation are deferred to the hosted-import
    milestone.
    """

    bundle_format: Literal["tckdb-contribution-bundle"]
    bundle_version: Literal["0.1"]
    bundle_kind: BundleKind
    created_at: datetime

    source_instance: BundleSourceInstance
    exporter: BundleExporter
    submission: BundleSubmissionMetadata

    records: BundleRecordSet
    local_refs: dict[str, BundleLocalRefEntry] = Field(default_factory=dict)
    manifest: BundleManifest

    @model_validator(mode="after")
    def validate_records_match_kind(self) -> Self:
        thermo = self.records.thermo_uploads
        kinetics = self.records.kinetics_uploads

        if self.bundle_kind is BundleKind.thermo:
            if not thermo:
                raise ValueError(
                    "thermo bundles must contain at least one thermo_uploads entry."
                )
            if kinetics:
                raise ValueError(
                    "thermo bundles must not carry kinetics_uploads in v0; "
                    "mixed bundles are not supported."
                )
        elif self.bundle_kind is BundleKind.kinetics:
            if not kinetics:
                raise ValueError(
                    "kinetics bundles must contain at least one kinetics_uploads entry."
                )
            if thermo:
                raise ValueError(
                    "kinetics bundles must not carry thermo_uploads in v0; "
                    "mixed bundles are not supported."
                )
        return self

    @model_validator(mode="after")
    def validate_local_ref_keys(self) -> Self:
        for key in self.local_refs:
            if not _LOCAL_REF_KEY_RE.match(key):
                raise ValueError(
                    f"local_refs key {key!r} is malformed; expected "
                    "'<namespace>:<label>' (e.g. 'species:ethanol')."
                )
            _, _, label = key.partition(":")
            if _NUMERIC_LABEL_RE.match(label):
                raise ValueError(
                    f"local_refs key {key!r} uses a purely numeric label; "
                    "raw DB primary keys must not be used as local refs."
                )
        return self
