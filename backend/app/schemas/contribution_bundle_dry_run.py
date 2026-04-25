"""Response schemas for the hosted contribution-bundle dry-run endpoint.

Dry-run is preview-only: nothing is created, accepted, curated, or imported.
The schemas here describe what *would* happen during a real import — they
intentionally use ``would_*`` action wording and never report actuals.
"""

from __future__ import annotations

from enum import Enum

from pydantic import Field

from app.schemas.common import SchemaBase
from app.schemas.workflows.contribution_bundle import BundleKind


class DryRunAction(str, Enum):
    """Preview action a real import would take for a given record/dependency."""

    would_reuse = "would_reuse"
    would_create = "would_create"
    would_append = "would_append"
    unsupported = "unsupported"
    error = "error"


class DryRunMessageLevel(str, Enum):
    """Severity of a dry-run message."""

    info = "info"
    warning = "warning"
    error = "error"


class DryRunRecordType(str, Enum):
    """The kind of record a dry-run item describes.

    Preview items describe one of the scientific identity/provenance rows
    a real import would have to resolve, plus the append-only product row
    itself (``thermo`` or ``kinetics``).
    """

    species = "species"
    species_entry = "species_entry"
    chem_reaction = "chem_reaction"
    literature = "literature"
    software_release = "software_release"
    workflow_tool_release = "workflow_tool_release"
    thermo = "thermo"
    kinetics = "kinetics"


class ContributionBundleDryRunItem(SchemaBase):
    """One preview entry in the dry-run result.

    Each item describes either an identity/provenance row that a real import
    would resolve (``would_reuse`` / ``would_create``) or the append-only
    scientific product row itself (``would_append``).

    :param record_type: What kind of record this item describes.
    :param action: What a real import would do for this record.
    :param reason: Short, user-facing explanation for the action.
    :param local_ref: Optional structured reference back into the bundle
        (e.g. ``thermo_uploads[0]`` or ``thermo_uploads[0].species_entry``).
        Not the bundle's ``local_refs`` map — that is informational only.
    :param target: Optional short identity descriptor (e.g. SMILES, DOI,
        ``"Gaussian 16"``). Never includes hosted database primary keys.
    :param hosted_identity: Optional structured identity hint that mirrors
        what the importer would use to dedupe (e.g. ``{"inchi_key": "..."}``).
    :param details: Optional free-form structured extras for clients that
        want to render richer previews.
    """

    record_type: DryRunRecordType
    action: DryRunAction
    reason: str = Field(min_length=1)

    local_ref: str | None = None
    target: str | None = None
    hosted_identity: dict[str, str | int | None] | None = None
    details: dict[str, str | int | float | bool | None] | None = None


class ContributionBundleDryRunMessage(SchemaBase):
    """A non-item-scoped message about the bundle as a whole.

    Item-scoped problems are expressed as ``ContributionBundleDryRunItem``
    rows with ``action=error``; messages here describe bundle-wide info,
    warnings, or errors that don't attach cleanly to one record.

    :param level: Severity (``info``/``warning``/``error``).
    :param code: Short stable machine code (e.g. ``unsupported_bundle_kind``).
    :param message: Human-readable explanation.
    :param field: Optional pointer at a bundle field (e.g. ``records``).
    :param local_ref: Optional bundle-local upload reference.
    :param record_type: Optional record type the message relates to.
    """

    level: DryRunMessageLevel
    code: str = Field(min_length=1)
    message: str = Field(min_length=1)

    field: str | None = None
    local_ref: str | None = None
    record_type: DryRunRecordType | None = None


class ContributionBundleDryRunSummary(SchemaBase):
    """Summary counts across all dry-run items.

    Counts are derived from ``ContributionBundleDryRunResult.items`` — they
    are not an alternative source of truth.
    """

    records_seen: int = Field(ge=0)
    would_create: int = Field(ge=0)
    would_reuse: int = Field(ge=0)
    would_append: int = Field(ge=0)
    unsupported: int = Field(ge=0)
    errors: int = Field(ge=0)
    warnings: int = Field(ge=0)


class ContributionBundleDryRunResult(SchemaBase):
    """Top-level dry-run preview response.

    A successful HTTP 200 response always carries this shape; bundle
    structural validation problems instead surface as a normal 422
    validation error (handled by FastAPI before this schema is built).
    """

    bundle_valid: bool
    bundle_kind: BundleKind
    summary: ContributionBundleDryRunSummary
    items: list[ContributionBundleDryRunItem] = Field(default_factory=list)
    messages: list[ContributionBundleDryRunMessage] = Field(default_factory=list)
