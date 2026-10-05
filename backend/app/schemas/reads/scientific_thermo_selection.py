"""Read schemas for ``POST /scientific/species-entries/{ref}/thermo/select``.

Method-aware selection of one species entry's thermo record for the formation
enthalpy at 298.15 K (gas phase). A read: nothing is stored, no curator
endorsement is created or changed, and the ordinary thermo browse order is
untouched. See ``docs/guides/selecting_thermo_for_h298.md``.

This request is deliberately separate from :class:`ThermoReadRequest` and does
not extend the cross-product ``SelectionPolicy`` enum: ``method_preferred`` is
a policy of this one quantity, not of every product.
"""

from __future__ import annotations

import math
from datetime import datetime
from enum import Enum
from typing import Any, Final, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from tckdb_schemas.coded_error import CodedValidationError

from app.db.models.common import PhaseKind, RecordReviewStatus, ThermoTargetKind
from app.schemas.reads.scientific_common import ProfiledRequestEcho

#: The one quantity this endpoint answers, and the conditions that define it.
QUANTITY_H298: Final = "formation_enthalpy_298k"
REFERENCE_TEMPERATURE_K = 298.15

#: Refusal for a temperature or phase that contradicts the quantity.
W_THERMO_SELECTION_CONDITION_CONFLICT = "thermo_selection_condition_conflict"
#: ``single_conformer`` with no group, and ``equilibrium_ensemble`` with one: same refusals as a deposit.
#: More visible records than one selection will assess (the service's fixed cap).
W_THERMO_SELECTION_POPULATION_TOO_LARGE = "thermo_selection_population_too_large"
W_TARGET_GROUP_REQUIRED = "thermo_target_group_required"
W_TARGET_GROUP_NOT_ALLOWED = "thermo_target_group_not_allowed"


class ThermoSelectionPolicy(str, Enum):
    """Which ordering the request asks for.

    ``method_preferred``  apply the registered, evidence-backed method rules; the review/recency order
                          only sorts within a preference front.
    ``default``, ``most_reviewed``, ``latest``
                          administrative order only (no method rules), exactly as on the browse endpoints.
    """

    method_preferred = "method_preferred"
    default = "default"
    most_reviewed = "most_reviewed"
    latest = "latest"


class ThermoSelectionMode(str, Enum):
    """``all`` returns the whole assessed population; ``first`` also names a single record when it can."""

    all = "all"
    first = "first"


class ThermoSelectionOutcome(str, Enum):
    """The selection basis. The service ``Outcome`` minus ``bounded_search_exceeded``, which the endpoint
    reports as the 422 ``thermo_selection_population_too_large`` instead (a test holds the two in step)."""

    policy_preferred = "policy_preferred"
    incomparable_alternatives = "incomparable_alternatives"
    sole_eligible_candidate = "sole_eligible_candidate"
    no_applicable_candidate = "no_applicable_candidate"
    policy_conflict = "policy_conflict"


class ThermoSelectionTargetIn(BaseModel):
    """The thermodynamic target the caller needs the enthalpy for."""

    model_config = ConfigDict(extra="forbid")

    kind: ThermoTargetKind = Field(
        description="equilibrium_ensemble, or single_conformer together with conformer_group_ref."
    )
    conformer_group_ref: str | None = Field(
        default=None,
        max_length=64,
        description="Public conformer-group ref (cg_...). Required for single_conformer, refused otherwise.",
    )

    @model_validator(mode="after")
    def _group_matches_kind(self) -> ThermoSelectionTargetIn:
        if self.kind is ThermoTargetKind.single_conformer and self.conformer_group_ref is None:
            raise CodedValidationError(
                W_TARGET_GROUP_REQUIRED,
                "a single_conformer target must name its conformer_group_ref.",
                context={"field": "target.conformer_group_ref"},
            )
        if self.kind is ThermoTargetKind.equilibrium_ensemble and self.conformer_group_ref is not None:
            raise CodedValidationError(
                W_TARGET_GROUP_NOT_ALLOWED,
                "an equilibrium_ensemble target does not name a conformer group.",
                context={"field": "target.conformer_group_ref"},
            )
        return self


class ThermoSelectionRequest(BaseModel):
    """Request body. ``quantity``, ``temperature_k`` and ``phase`` may be omitted; if given they must agree."""

    model_config = ConfigDict(extra="forbid")

    quantity: Literal["formation_enthalpy_298k"] = QUANTITY_H298
    temperature_k: float | None = Field(
        default=None,
        allow_inf_nan=False,
        description="Normalised to 298.15 K. Any other value is refused, and NaN and Infinity are invalid.",
    )
    phase: PhaseKind | None = Field(default=None, description="Normalised to gas. Any other phase is refused.")
    target: ThermoSelectionTargetIn
    policy: ThermoSelectionPolicy = ThermoSelectionPolicy.method_preferred
    result_mode: ThermoSelectionMode = ThermoSelectionMode.all
    min_review_status: RecordReviewStatus | None = Field(
        default=None,
        description="Optional floor, applied on top of the read profile's floor (the stricter wins).",
    )

    @model_validator(mode="after")
    def _conditions_agree_with_the_quantity(self) -> ThermoSelectionRequest:
        if self.temperature_k is not None and not math.isclose(
            self.temperature_k, REFERENCE_TEMPERATURE_K, abs_tol=1e-6
        ):
            raise CodedValidationError(
                W_THERMO_SELECTION_CONDITION_CONFLICT,
                f"{QUANTITY_H298} is defined at {REFERENCE_TEMPERATURE_K} K.",
                context={"field": "temperature_k", "supplied": self.temperature_k, "required": REFERENCE_TEMPERATURE_K},
            )
        if self.phase is not None and self.phase is not PhaseKind.gas:
            raise CodedValidationError(
                W_THERMO_SELECTION_CONDITION_CONFLICT,
                f"{QUANTITY_H298} is answered for the gas phase only.",
                context={"field": "phase", "supplied": self.phase.value, "required": PhaseKind.gas.value},
            )
        return self


# ---------------------------------------------------------------------------
# Response
# ---------------------------------------------------------------------------


class ThermoSelectionTargetEcho(BaseModel):
    kind: ThermoTargetKind
    conformer_group_ref: str | None = None


class ThermoSelectionRequestEcho(ProfiledRequestEcho):
    """The normalised request, as the server understood it."""

    species_entry_ref: str
    quantity: Literal["formation_enthalpy_298k"] = QUANTITY_H298
    temperature_k: float = REFERENCE_TEMPERATURE_K
    phase: Literal["gas"] = "gas"
    target: ThermoSelectionTargetEcho
    policy: ThermoSelectionPolicy
    result_mode: ThermoSelectionMode
    min_review_status: RecordReviewStatus | None = None


class ThermoSelectionReview(BaseModel):
    """The review floor actually applied: the request's floor combined with the read profile's."""

    effective_floor: RecordReviewStatus = Field(
        description="The weakest review status a candidate may have. Rejected and deprecated never compete."
    )
    effective_statuses: list[RecordReviewStatus]


class ThermoSelectionPolicyInfo(BaseModel):
    """The selection semantics and the rule versions that were applied."""

    name: str
    version: str
    rules: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Registry entries applied: rule id, version, objective, scope, evidence type and limits.",
    )


class ThermoSelectionPick(BaseModel):
    """The one record this response names, and exactly why it is named."""

    thermo_ref: str
    basis: Literal["policy_preferred", "sole_eligible_candidate", "administrative_first"]
    administrative: bool = Field(
        description="True when the record was chosen by review/recency order among unranked alternatives. "
        "That is not a claim that it is method-superior."
    )
    explanation: str


class ThermoSelectionReason(BaseModel):
    code: str
    applicability: str


class ThermoSelectionCandidate(BaseModel):
    """One assessed record: its declared claims, its H298 applicability, and whether it competed."""

    thermo_ref: str
    review_status: RecordReviewStatus
    created_at: datetime
    scientific_origin: str
    phase: str | None = None
    enthalpy_reference_kind: str | None = None
    target_kind: str | None = None
    target_group_ref: str | None = None
    protocol_state: str = Field(description="absent, valid or unreadable. Absent is 'not stated', never 'standard'.")
    protocol: dict[str, Any] | None = None
    linked_recipe_keys: list[str] = Field(default_factory=list)
    applicability: str = Field(description="applicable, incompatible, unsupported or unresolved.")
    reasons: list[ThermoSelectionReason] = Field(default_factory=list)
    answer_representation: str | None = None
    value_kj_mol: float | None = None
    representations: list[dict[str, Any]] = Field(default_factory=list)
    blocking: list[str] = Field(default_factory=list)
    advisory: list[str] = Field(default_factory=list)
    eligible: bool = Field(description="Applicable, with no blocking finding: it competes in the ordering.")


class ThermoSelectionEdge(BaseModel):
    preferred: str
    dispreferred: str
    rule_id: str = Field(description="The registry key of the rule (for example E1). Not a database id.")
    rule_version: str


class ThermoSelectionRelations(BaseModel):
    """Preference relations between eligible records, and any contradiction among them."""

    edges: list[ThermoSelectionEdge] = Field(default_factory=list)
    overridden_edges: list[dict[str, Any]] = Field(default_factory=list)
    opposing_pairs: list[dict[str, Any]] = Field(default_factory=list)
    cycles: list[list[str]] = Field(default_factory=list)


class ThermoSelectionExcluded(BaseModel):
    thermo_ref: str
    review_status: RecordReviewStatus
    reason: str


class ThermoSelectionDisclosures(BaseModel):
    """What the result does not cover. Never empty by omission: a withheld list says so."""

    unresolved_refs: list[str] = Field(default_factory=list)
    unsupported_refs: list[str] = Field(default_factory=list)
    visible_candidates: int = Field(description="Records at or above the effective floor (the assessed population).")
    excluded_by_review: list[ThermoSelectionExcluded] = Field(default_factory=list)
    excluded_by_review_withheld: bool = Field(
        description="True whenever the read profile has a review floor of its own (curated): records outside "
        "the effective floor are then never listed, whether or not any exist, so the field cannot reveal "
        "that they do. False under a profile with no floor, where they are listed."
    )
    notes: list[str] = Field(default_factory=list)


class ThermoSelectionResponse(BaseModel):
    """The decision, its basis, and everything it rests on, as public refs only."""

    request: ThermoSelectionRequestEcho
    review: ThermoSelectionReview
    policy: ThermoSelectionPolicyInfo
    outcome: ThermoSelectionOutcome
    basis: str = Field(description="One sentence saying why this outcome, in plain terms.")
    selection: ThermoSelectionPick | None = None
    fronts: list[list[str]] = Field(
        default_factory=list, description="Preference fronts, best first; records within a front are unranked."
    )
    administrative_order: list[str] = Field(default_factory=list)
    candidates: list[ThermoSelectionCandidate] = Field(default_factory=list)
    relations: ThermoSelectionRelations = Field(default_factory=ThermoSelectionRelations)
    rule_matches: list[dict[str, Any]] = Field(default_factory=list)
    disclosures: ThermoSelectionDisclosures


class ThermoSelectionManifestRequest(ProfiledRequestEcho):
    """The manifest's normalised request, with the read profile it was made under (the same echo every
    scientific response carries). Further keys the decision recorded are kept."""

    model_config = ConfigDict(extra="allow")

    quantity: str
    temperature_k: float
    phase: str
    target: dict[str, Any]
    min_review_status: RecordReviewStatus | None = None
    effective_review_statuses: list[RecordReviewStatus]
    administrative_policy: str


class ThermoSelectionManifest(BaseModel):
    """The replayable decision manifest of ``POST .../thermo/select/manifest``.

    Public refs only. Holds the normalised request, the effective review floor, every assessed candidate
    with its normalised inputs (``id_rank`` is an ordinal over the visible records, not an id), the rules
    applied with each rule's verdict per candidate, the preference edges, the fronts and the administrative
    order: enough to recompute the decision with no database.
    """

    manifest_format_version: int
    policy: dict[str, Any]
    assessment_semantics: dict[str, Any] | None = None
    """How each candidate's eligibility was assessed: ``version`` (``2`` today), the evidence rubric and the source-finding
    gate. Absent on a manifest made before this field existed, which is then ``pre_v2_assessment``. ``policy`` is the decision
    procedure replay re-runs; this is the assessment that produced the recorded ``eligible`` flags, which replay does not rerun."""
    request: ThermoSelectionManifestRequest
    subject: dict[str, Any]
    population: dict[str, Any]
    candidates: list[dict[str, Any]]
    decision: dict[str, Any]
    outcome: ThermoSelectionOutcome
    disclosures: dict[str, Any] = Field(default_factory=dict)
