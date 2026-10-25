"""The request and response fragments every ``.../select`` endpoint shares.

A select call is these steps, in this order (``docs/specs/browse_order.md``):

1. **User filters.** The caller narrows the candidates by facts, with the vocabulary the matching browse or search
   endpoint offers. A filter never ranks and never excludes on a judgement.
2. **Requested eligibility.** Each exclusion a selection applies is a *named default* the request may lift. The
   answer lists every one: its name, whether it was applied, and whether that was the default, the caller's choice or
   the read profile's.
3. **The policy.** There are no built-in method rankings.
4. **The result.** One record, or an explanation that several remain.

The read profile's review floor (``curated``: approved only, hidden equals unknown equals absent) is not an
eligibility default and no select request can lift it. An override can only lift an exclusion within what the read
profile already shows.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Final

from pydantic import BaseModel, ConfigDict, Field

from app.db.models.common import ScientificOriginKind
from app.schemas.reads._field_bounds import (
    MAX_BASIS_LENGTH,
    MAX_METHOD_LENGTH,
    MAX_PUBLIC_REF_LENGTH,
    MAX_SOFTWARE_NAME_LENGTH,
    MAX_WORKFLOW_TOOL_LENGTH,
)
from app.schemas.reads.scientific_common import ScientificResponse
from app.schemas.reads._method_family import DftRungBound, MethodFamilyFilter

#: The stable machine names of the eligibility defaults, in the order they are echoed.
ELIGIBILITY_REJECTED: Final = "rejected_review_status"
ELIGIBILITY_DEPRECATED: Final = "deprecated_review_status"
ELIGIBILITY_HARD_FAILED_EVIDENCE: Final = "evidence_hard_fail"
ELIGIBILITY_CONFIRMED_FINDINGS: Final = "confirmed_blocking_findings"
ELIGIBILITY_REJECTED_QUALITY: Final = "rejected_calculation_quality"
#: Not a default a request can lift. A transition-state entry is a gated kind: the evidence selection still excludes a
#: determination whose TS entry is rejected, deprecated or below the caller's floor (it hides with its parent), as the
#: transition-state trust fragment and evidence box do. A species or reaction entry is a scoping parent and never excludes.
#: It is echoed so the answer lists every exclusion it applies.
ELIGIBILITY_ENTRY_STATUS: Final = "entry_review_status"

#: The request field that lifts each default (``eligibility.<field>``), named like the browse ``include_rejected``.
OVERRIDE_FIELD: Final[dict[str, str]] = {
    ELIGIBILITY_REJECTED: "include_rejected",
    ELIGIBILITY_DEPRECATED: "include_deprecated",
    ELIGIBILITY_HARD_FAILED_EVIDENCE: "include_hard_failed_evidence",
    ELIGIBILITY_CONFIRMED_FINDINGS: "include_confirmed_findings",
    #: Not a flag of its own: a calculation-quality selection already names the qualities that qualify, and lifting this
    #: default is listing ``rejected`` there.
    ELIGIBILITY_REJECTED_QUALITY: "permitted_quality",
}


class SelectionEligibilityIn(BaseModel):
    """The eligibility defaults a request may lift. Every field defaults to ``false``: the default is applied.

    ``include_rejected`` and ``include_deprecated`` are the browse flags of the same names. Under a read profile with a
    review floor (``curated``) they lift nothing: the floor is the profile's, not an eligibility default, and the answer
    says so (``source: read_profile``).
    """

    model_config = ConfigDict(extra="forbid")

    include_rejected: bool = Field(
        default=False, description="Let a record whose review status is rejected compete (never under a profile floor)."
    )
    include_deprecated: bool = Field(
        default=False, description="Let a record whose review status is deprecated compete (never under a profile floor)."
    )
    include_hard_failed_evidence: bool = Field(
        default=False,
        description="Let a computed record whose evidence rubric hard-fails compete. The failure is still disclosed on the candidate.",
    )
    include_confirmed_findings: bool = Field(
        default=False,
        description="Let a record compete although a live, confirmed structure finding invalidates one of its sources. "
        "The finding is still disclosed on the candidate.",
    )


class ReviewEligibilityIn(BaseModel):
    """The defaults a record whose review and provenance belong to a parent (a network solve) can lift: its review status.

    A solve has no evidence rubric and no source findings of its own, so those two defaults are not offered here and a
    request naming them is refused rather than silently ignored.
    """

    model_config = ConfigDict(extra="forbid")

    include_rejected: bool = Field(default=False, description=SelectionEligibilityIn.model_fields["include_rejected"].description)
    include_deprecated: bool = Field(
        default=False, description=SelectionEligibilityIn.model_fields["include_deprecated"].description
    )


class FindingsEligibilityIn(ReviewEligibilityIn):
    """The defaults a statmech candidate can lift: its review status, and a confirmed finding about a source.

    A statmech candidate has no evidence rubric of its own, so ``include_hard_failed_evidence`` is not offered here.
    """

    include_confirmed_findings: bool = Field(
        default=False,
        description=SelectionEligibilityIn.model_fields["include_confirmed_findings"].description,
    )


class EligibilitySource(str, Enum):
    """Who decided whether a default was applied."""

    default = "default"
    request = "request"
    read_profile = "read_profile"


class EligibilityDefaultEcho(BaseModel):
    """One named eligibility default, as it was applied to this answer."""

    name: str = Field(description="Stable machine name of the exclusion.")
    override_field: str | None = Field(
        description="The request field that lifts it, or null for an exclusion no request can lift."
    )
    applied: bool = Field(description="True when records failing it were kept out of the competition.")
    source: EligibilitySource = Field(
        description="`default`: nobody asked. `request`: the caller lifted it. `read_profile`: the caller asked to lift "
        "it but the read profile's review floor keeps it applied."
    )
    excluded: int = Field(
        description="How many of the candidates that passed the filters it kept out. A candidate that fails several "
        "defaults is counted under each of them, so these numbers can sum to more than `funnel.removed_by_eligibility`, "
        "which counts every candidate once. Only records the read profile shows are counted, so the number cannot "
        "reveal a hidden one."
    )
    admitted_by_override: int = Field(
        default=0, description="How many candidates compete only because the caller lifted it."
    )


class SelectionFunnel(BaseModel):
    """How many candidates each step removed, so a small answer is not mistaken for a small population."""

    unit: str = Field(
        default="candidates",
        description="What is counted: the records the question picks among (`candidates`), or, where review and "
        "provenance belong to a parent record, those parents (`solves`).",
    )
    considered: int = Field(description="Candidates the read profile shows, before any step.")
    removed_by_filters: int = Field(
        description="Removed by the caller's filters (including a narrower `permitted_quality`, `member_refs` or "
        "`population`, which `filters.applied` also lists)."
    )
    removed_by_floor: int = Field(
        default=0, description="Removed by the caller's own `min_review_status` floor (the read profile's floor is not counted)."
    )
    removed_by_eligibility: int = Field(description="Removed by eligibility defaults that were applied.")
    remaining: int = Field(
        description="Candidates no step removed. Each candidate is counted once, at the first step that removed it, so "
        "`removed_by_filters + removed_by_floor + removed_by_eligibility + remaining` is `considered`."
    )


class SelectionFilterAnswer(BaseModel):
    """The filters as normalised, and what they did."""

    applied: dict[str, Any] = Field(
        default_factory=dict, description="Only the filters the request supplied, with their normalised values."
    )
    removed: int = Field(default=0, description="Candidates the filters removed (not the review floor: see the funnel).")
    note: str | None = Field(
        default=None, description="Set when the filters and the review floor removed every candidate: nothing was assessed."
    )


class SelectionStepsAnswer(ScientificResponse):
    """Steps 1 and 2 of a select call as the answer states them. Every select response inherits these fields."""

    filters: SelectionFilterAnswer = Field(
        default_factory=SelectionFilterAnswer,
        description="Step 1: the filters as normalised, and how many candidates they removed.",
    )
    eligibility: list[EligibilityDefaultEcho] = Field(
        default_factory=list,
        description="Step 2: every named eligibility default, whether applied, and by whose choice.",
    )
    funnel: SelectionFunnel


class CandidateEligibility(BaseModel):
    """What the eligibility defaults did to one candidate. Every select candidate inherits these fields."""

    blocked_by: list[str] = Field(
        default_factory=list, description="Applied eligibility defaults this candidate fails (why it does not compete)."
    )
    passed_only_by_override: list[str] = Field(
        default_factory=list,
        description="Eligibility defaults this candidate would fail but the request lifted. It competes because of that "
        "only if it is also applicable and fails no applied default.",
    )


def filters_echo(filters: BaseModel | None) -> dict[str, Any]:
    """The supplied (non-null) filters of a request, as plain JSON."""
    if filters is None:
        return {}
    return filters.model_dump(mode="json", exclude_none=True)


class CalculationProvenanceFilters(BaseModel):
    """The method, basis, software and workflow-tool filters every provenance-aware search offers, with its semantics.

    A candidate is kept when at least one of its source calculations, one the read profile shows, matches every supplied
    constraint at once (AND across the fields, OR across the candidate's calculations). A calculation without the
    relevant provenance link cannot satisfy that field. See
    :func:`app.services.scientific_read.calculation_provenance_filters.apply_calculation_provenance_filter`.
    """

    model_config = ConfigDict(extra="forbid")

    method: str | None = Field(default=None, max_length=MAX_METHOD_LENGTH)
    basis: str | None = Field(default=None, max_length=MAX_BASIS_LENGTH)
    software: str | None = Field(default=None, max_length=MAX_SOFTWARE_NAME_LENGTH)
    software_version: str | None = Field(default=None, max_length=128)
    workflow_tool: str | None = Field(default=None, max_length=MAX_WORKFLOW_TOOL_LENGTH)
    workflow_tool_version: str | None = Field(default=None, max_length=128)


class SourceCalculationFilters(BaseModel):
    """The filters the per-entry thermo and kinetics reads offer besides ``model_kind``, with their semantics.

    The provenance fields (``level_of_theory_ref``, ``software``, ``method``, ``basis``, ``software_version``,
    ``workflow_tool``, ``workflow_tool_version``, ``method_family``, ``dft_rung_min``, ``dft_rung_max``) are matched
    through the record's source calculations, and ONE source calculation has to satisfy every supplied field at once. A
    record never matches through two different calculations, never through a calculation it does not link to, and under
    ``curated`` never through a calculation the profile hides. ``method_family`` and the rung are a classification of
    the calculation's method, not a measure of accuracy, and they rank nothing. ``scientific_origin`` is matched on the
    record itself. A public ref only: a select request never carries a database id.
    """

    model_config = ConfigDict(extra="forbid")

    scientific_origin: ScientificOriginKind | None = None
    level_of_theory_ref: str | None = Field(default=None, max_length=MAX_PUBLIC_REF_LENGTH)
    method: str | None = Field(default=None, max_length=MAX_METHOD_LENGTH)
    basis: str | None = Field(default=None, max_length=MAX_BASIS_LENGTH)
    software: str | None = Field(default=None, max_length=MAX_SOFTWARE_NAME_LENGTH)
    software_version: str | None = Field(default=None, max_length=128)
    workflow_tool: str | None = Field(default=None, max_length=MAX_WORKFLOW_TOOL_LENGTH)
    workflow_tool_version: str | None = Field(default=None, max_length=128)
    method_family: MethodFamilyFilter = None
    dft_rung_min: DftRungBound = None
    dft_rung_max: DftRungBound = None

    def source_filter_values(self) -> dict[str, Any]:
        """The fields the service-layer ``SourceFilters`` take, as plain values (the model kind is the subclass's)."""
        return {
            "scientific_origin": self.scientific_origin.value if self.scientific_origin is not None else None,
            "level_of_theory_ref": self.level_of_theory_ref,
            "method": self.method,
            "basis": self.basis,
            "software": self.software,
            "software_version": self.software_version,
            "workflow_tool": self.workflow_tool,
            "workflow_tool_version": self.workflow_tool_version,
            "method_family": (
                tuple(sorted({f.value for f in self.method_family})) if self.method_family is not None else None
            ),
            "dft_rung_min": self.dft_rung_min,
            "dft_rung_max": self.dft_rung_max,
        }
