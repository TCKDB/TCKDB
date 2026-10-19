"""Curator request and read schemas for structure-evidence findings.

A finding is an authorized adjudication about one geometry, calculation or structure determination. The request names the
subject by public ref and never carries a row id, an authority or an author: the authority is fixed by the route (only a
curator or admin may reach it) and the author is the authenticated caller.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import Field

from app.db.models.common import (
    StructureFindingAuthority,
    StructureFindingKind,
    StructureFindingScope,
    StructureFindingVerdict,
    StructureSourceRole,
)
from app.schemas.common import ORMBaseSchema, SchemaBase


class StructureEvidenceFindingCreate(SchemaBase):
    """One finding a curator appends. Findings are never edited or deleted; a correction is a later adjudication."""

    scope: StructureFindingScope = Field(description="The kind of subject the finding is about.")
    subject_ref: str = Field(
        min_length=1,
        max_length=64,
        description="Public ref of the subject: a geometry (geom_), a calculation (calc_) or a determination (sdet_). Its "
        "prefix must match scope.",
    )
    kind: StructureFindingKind
    role: StructureSourceRole | None = Field(
        default=None, description="The role a role_invalidation invalidates. Required for that kind, refused for every other."
    )
    verdict: StructureFindingVerdict
    rationale: str = Field(max_length=2000, description="Why the curator reached this verdict. Required.")
    source_calculation_ref: str | None = Field(
        default=None, max_length=64, description="Public ref of a calculation whose evidence supports the finding."
    )
    literature_ref: str | None = Field(
        default=None,
        max_length=64,
        description="Public ref of a paper that supports the finding. It must carry a DOI or an ISBN.",
    )
    supersedes_finding_ref: str | None = Field(
        default=None,
        max_length=64,
        description="Public ref of the earlier finding this one corrects. Only an adjudication names one, and it must be "
        "an earlier finding about the same subject that is still in force.",
    )


class StructureEvidenceFindingRead(ORMBaseSchema):
    """One recorded finding, by public refs only."""

    finding_ref: str
    scope: StructureFindingScope
    subject_ref: str
    kind: StructureFindingKind
    role: StructureSourceRole | None
    verdict: StructureFindingVerdict
    authority: StructureFindingAuthority
    rationale: str
    source_calculation_ref: str | None
    literature_ref: str | None
    supersedes_finding_ref: str | None
    settled_by_finding_ref: str | None = Field(
        description="The adjudication that corrected this finding, if one did. A settled finding is history and has no "
        "effect on a selection."
    )
    in_force: bool = Field(description="True when no adjudication has corrected this finding.")
    created_at: datetime


class StructureEvidenceFindingPageRead(ORMBaseSchema):
    subject_ref: str
    findings: list[StructureEvidenceFindingRead]
    offset: int
    limit: int
