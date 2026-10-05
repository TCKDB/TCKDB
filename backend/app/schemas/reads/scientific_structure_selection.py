"""Read schemas for the structure selection routes.

``POST /scientific/species-entries/{ref}/calculations/select``, ``.../conformers/select`` and
``POST /scientific/transition-state-entries/{ref}/evidence/select`` (each with a ``/manifest`` download).

Which stored energies, conformer basins or saddles can answer one stated question about one entry, and which of
them, if any, the evidence orders. A read: nothing is stored, no curator endorsement is created or changed, and the
ordinary browse order is untouched. See ``backend/docs/specs/structure_selection_decision.md`` for what a decision
is and is not, and ``docs/guides/selecting_structures.md`` for how to read one.

The requests are strict: unknown fields are refused, references are public refs (never a database id), there is no
caller-authored rule, no sort expression and no pagination (a selection is over the *complete* authorized
population, or it refuses), and the engineering bounds are the server's. A caller cannot set ``bounds`` or any limit.
Every violation of a request's own rules is an ordinary 422 naming the field.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from tckdb_schemas.structure_declarations import ActualProtocolDeclaration

from app.db.models.common import CalculationQuality, RecordReviewStatus
from app.schemas.reads.scientific_common import ProfiledRequestEcho
from app.services.structure_selection.models import (
    DEFERRED_QUANTITIES,
    AdminPolicy,
    CoverageRequirement,
    Grain,
    Intent,
    Objective,
    Quantity,
    RepeatPolicy,
    ResultMode,
    StructureOutcome,
    StructureRequest,
    ValidationClaim,
)

#: Quantity names a request may carry: the two evaluated ones, and the recognised deferred ones (a structured refusal).
_QUANTITY_NAMES = frozenset({q.value for q in Quantity} | DEFERRED_QUANTITIES)

_REF = Field(min_length=1, max_length=64)


class _StrictRequest(BaseModel):
    """Common fields. The route fixes the grain; subclasses fix the intents it accepts."""

    model_config = ConfigDict(extra="forbid")

    quantity: str | None = Field(
        default=Quantity.electronic_energy.value,
        description=(
            "electronic_energy or zero_kelvin_energy. Omit for evidence-only qualification (qualify_evidence). A "
            "recognised deferred quantity (enthalpy, gibbs_energy, entropy, heat_capacity, barrier, rate) is a "
            "structured 422 structure_selection_unsupported, never answered with an energy that was not asked for."
        ),
    )
    coverage_requirement: CoverageRequirement = Field(
        default=CoverageRequirement.known_values,
        description="known_values orders what is known, conditional on it; all_requested_members is a complete claim.",
    )
    validation_claim: ValidationClaim | None = Field(
        default=None,
        description="The structural claim the evidence must support. Omitted: the grain's conventional characterization.",
    )
    min_review_status: RecordReviewStatus | None = Field(
        default=None, description="A stricter review floor than the read profile's. Applied at every owning grain."
    )
    permitted_quality: list[CalculationQuality] | None = Field(
        default=None,
        max_length=3,
        description="Calculation qualities that qualify. Omitted: raw and curated (never rejected).",
    )
    geometry_ref: str | None = Field(default=None, min_length=1, max_length=64, description="A fixed-geometry target (a public geom_ ref).")
    member_refs: list[str] | None = Field(
        default=None,
        min_length=1,
        max_length=500,
        description="An explicit population (calculation or determination public refs). Omitted: the complete authorized corpus.",
    )
    recipe: ActualProtocolDeclaration | None = Field(
        default=None,
        description=(
            "A requested actual recipe (the declaration's shape, version 1). The units' own stated facts must establish "
            "it; a request never manufactures a missing fact."
        ),
    )
    require_stable_reference: bool = False
    administrative_policy: AdminPolicy = Field(
        default=AdminPolicy.default,
        description="Administrative order within a scientific front or among exact ties. Never reorders fronts.",
    )
    result_mode: ResultMode = ResultMode.all
    apply_rules: bool = Field(default=True, description="Whether the audited rule registry takes part (it holds no active rule in this release).")
    objective: Objective | None = Field(default=None, description="Required for protocol_preferred; refused otherwise.")
    reference_model: str | None = Field(default=None, min_length=1, max_length=200)
    repeat_policy: RepeatPolicy = RepeatPolicy.retain_alternates

    @field_validator("quantity")
    @classmethod
    def _known_quantity_name(cls, value: str | None) -> str | None:
        if value is not None and value not in _QUANTITY_NAMES:
            raise ValueError(f"unknown quantity {value!r}; use electronic_energy or zero_kelvin_energy")
        return value

    @field_validator("member_refs")
    @classmethod
    def _members_distinct(cls, value: list[str] | None) -> list[str] | None:
        if value is not None and len(set(value)) != len(value):
            raise ValueError("member_refs names a member twice")
        return value

    _grain: Grain
    _intents: frozenset[Intent]

    def service_request(self, *, quantity: Quantity | None) -> StructureRequest:
        """The service's request. ``quantity`` is resolved by the caller (a deferred one is refused before this)."""
        raise NotImplementedError  # pragma: no cover - subclasses


def _build(body: _StrictRequest, grain: Grain, intent: Intent, *, claim: ValidationClaim | None, connectivity: bool, quantity: Quantity | None) -> StructureRequest:
    return StructureRequest(
        grain=grain,
        intent=intent,
        quantity=quantity,
        coverage=body.coverage_requirement,
        validation_claim=claim,
        min_review_status=body.min_review_status,
        permitted_quality=frozenset(body.permitted_quality) if body.permitted_quality is not None else None,
        geometry_ref=body.geometry_ref,
        member_refs=tuple(body.member_refs) if body.member_refs is not None else None,
        recipe=body.recipe.model_dump(mode="json", exclude_none=True) if body.recipe is not None else None,
        require_stable_reference=body.require_stable_reference,
        require_connectivity=connectivity,
        admin_policy=body.administrative_policy,
        result_mode=body.result_mode,
        apply_rules=body.apply_rules,
        objective=body.objective,
        reference_model=body.reference_model,
        repeat_policy=body.repeat_policy,
    )


def _validate_against_service(body: Any, grain: Grain) -> None:
    """Refuse a request the service would refuse, as an ordinary 422 naming the problem.

    A deferred quantity is not judged here (it is a coded refusal raised by the route); it is validated as the
    electronic energy it will never be answered with, so every other rule of the request still applies.
    """
    quantity: Quantity | None
    if body.quantity is None:
        quantity = None
    elif body.quantity in {q.value for q in Quantity}:
        quantity = Quantity(body.quantity)
    else:
        quantity = Quantity.electronic_energy
    if body.intent is Intent.qualify_evidence and body.quantity is not None:
        raise ValueError("evidence-only qualification asks for no energy: omit quantity")
    claim = getattr(body, "validation_claim", None)
    connectivity = getattr(body, "require_connectivity", False)
    _build(body, grain, body.intent, claim=claim, connectivity=connectivity, quantity=quantity)


class CalculationSelectionRequest(_StrictRequest):
    """Which of one species entry's calculations supplies the lowest comparable recorded energy (or which protocol)."""

    intent: Literal[Intent.recorded_minimum, Intent.protocol_preferred] = Intent.recorded_minimum

    @model_validator(mode="after")
    def _service_rules(self) -> CalculationSelectionRequest:
        _validate_against_service(self, Grain.calculation)
        return self

    def service_request(self, *, quantity: Quantity | None) -> StructureRequest:
        return _build(
            self, Grain.calculation, Intent(self.intent), claim=self.validation_claim, connectivity=False, quantity=quantity
        )


class ConformerSelectionRequest(_StrictRequest):
    """Which of one species entry's validated conformer basins is lowest, which support a claim, or which protocol."""

    intent: Literal[Intent.validated_minimum, Intent.qualify_evidence, Intent.protocol_preferred] = Intent.validated_minimum

    @model_validator(mode="after")
    def _service_rules(self) -> ConformerSelectionRequest:
        _validate_against_service(self, Grain.conformer)
        return self

    def service_request(self, *, quantity: Quantity | None) -> StructureRequest:
        return _build(
            self, Grain.conformer, Intent(self.intent), claim=self.validation_claim, connectivity=False, quantity=quantity
        )


class TransitionStateEvidenceSelectionRequest(_StrictRequest):
    """Which of one transition state entry's saddle determinations support a claim, are lowest, or which protocol.

    One entry only: comparison across several entries needs an explicit validated same-path declaration, which this
    release does not take, so sibling entries are assessed one at a time.
    """

    intent: Literal[Intent.validated_saddle, Intent.qualify_evidence, Intent.protocol_preferred] = Intent.validated_saddle
    require_connectivity: bool = Field(
        default=False, description="Also require reactive-connectivity evidence (IRC or a supported alternative) bound to the saddle."
    )

    @model_validator(mode="after")
    def _service_rules(self) -> TransitionStateEvidenceSelectionRequest:
        _validate_against_service(self, Grain.transition_state)
        return self

    def service_request(self, *, quantity: Quantity | None) -> StructureRequest:
        return _build(
            self,
            Grain.transition_state,
            Intent(self.intent),
            claim=self.validation_claim,
            connectivity=self.require_connectivity,
            quantity=quantity,
        )


# ---------------------------------------------------------------------------
# Response
# ---------------------------------------------------------------------------


class StructureSelectionRequestEcho(ProfiledRequestEcho):
    """The normalised request the decision was made under, with the read profile it was made under."""

    model_config = ConfigDict(extra="allow")

    entry_ref: str
    grain: str
    intent: str
    quantity: str | None = None
    coverage: str
    validation_claim: str | None = None
    min_review_status: RecordReviewStatus | None = None
    permitted_quality: list[str]
    geometry_ref: str | None = None
    member_refs: list[str] | None = None
    recipe: dict[str, Any] | None = None
    require_stable_reference: bool
    require_connectivity: bool
    administrative_policy: str
    result_mode: str
    apply_rules: bool
    objective: str | None = None
    reference_model: str | None = None
    repeat_policy: str
    bounds: dict[str, Any] = Field(
        description="The server's engineering bounds this decision was made under (never settable by the caller)."
    )


class StructureSelectionReview(BaseModel):
    effective_floor: RecordReviewStatus
    effective_statuses: list[RecordReviewStatus]


class StructureSelectionReason(BaseModel):
    code: str
    applicability: str


class StructureSelectionUnit(BaseModel):
    """One assessed unit (a calculation, or a basin or saddle determination) with every finding that fed its verdict."""

    unit_ref: str
    review_status: RecordReviewStatus
    created_at: datetime
    applicability: str
    reasons: list[StructureSelectionReason]
    blocking: list[str]
    advisory: list[str]
    comparative_unknown: list[str]
    energy: dict[str, Any] | None = None
    recipe: dict[str, Any] | None = None
    claim: dict[str, Any] | None = None
    eligible: bool


class StructureSelectionDisclosures(BaseModel):
    unresolved_refs: list[str]
    unsupported_refs: list[str]
    visible_units: int
    excluded_by_review: list[dict[str, str]]
    excluded_count: int
    excluded_by_review_withheld: bool
    notes: list[str]


class StructureSelectionIntegrity(BaseModel):
    algorithm: str
    manifest_sha256: str
    note: str


class StructureSelectionResponse(BaseModel):
    """The outcome of a structure selection and everything it rests on, public refs only.

    ``outcome`` is the selection basis (13 values; see the spec). ``selected_refs`` holds every tied minimum, the
    qualified determinations, or the selected protocol's cohort id; it is empty where nothing is selected. The
    ``coverage`` block always says it is the caller's authorized population only, never a global-search certificate.
    ``integrity`` carries the manifest checksum: a checksum, not a signature.
    """

    request: StructureSelectionRequestEcho
    review: StructureSelectionReview
    outcome: StructureOutcome
    basis: str
    selected_refs: list[str]
    administrative_first: dict[str, str] | None = None
    cohorts: list[dict[str, Any]]
    contested_targets: list[dict[str, Any]]
    unresolved: list[dict[str, Any]]
    coverage: dict[str, Any]
    representative_policy: dict[str, Any] | None = None
    protocol: dict[str, Any] | None = None
    administrative_key: dict[str, Any]
    search_completeness: str
    notes: list[str]
    units: list[StructureSelectionUnit]
    disclosures: StructureSelectionDisclosures
    versions: dict[str, Any]
    snapshot_isolation: str
    integrity: StructureSelectionIntegrity


class StructureSelectionManifestRequest(ProfiledRequestEcho):
    """The manifest's normalised request. Every key the decision recorded is kept (``extra=allow``).

    The manifest is served as a download and is replayed offline, so the read profile it was made under is the
    manifest's own ``read_profile`` block; the profile echo fields here keep the published shape uniform with every
    other scientific envelope and are not part of the digested content.
    """

    model_config = ConfigDict(extra="allow")


class StructureSelectionManifest(BaseModel):
    """The replayable decision manifest of the ``/manifest`` downloads.

    Public refs only: the normalised request, read profile, snapshot isolation, subject, every normalised unit and
    source calculation, each assessment, the registry entries consulted and the decision. ``replay_structure_assessment``
    and ``replay_structure_decision`` (``tckdb_client``) recompute it offline. The digests in ``integrity`` are
    checksums, not signatures: they detect edits and anyone who edits a manifest can recompute them, so replay
    shows the reasoning follows from the captured inputs and proves neither that they are true nor that the
    population was complete. The download is a new snapshot of the current data, not a retrieval of an earlier decision.
    """

    model_config = ConfigDict(extra="allow")

    manifest_format_version: int
    versions: dict[str, Any]
    request: StructureSelectionManifestRequest
    effective_review_statuses: list[RecordReviewStatus]
    read_profile: dict[str, Any]
    snapshot_isolation: str
    subject: dict[str, Any]
    population: dict[str, Any]
    calculations: list[dict[str, Any]]
    determinations: list[dict[str, Any]]
    assessments: list[dict[str, Any]]
    decision: dict[str, Any]
    outcome: StructureOutcome
    disclosures: dict[str, Any] = Field(default_factory=dict)
    integrity: StructureSelectionIntegrity
