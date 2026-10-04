"""Read schemas for ``POST /scientific/reaction-entries/{ref}/kinetics/select``.

Method-aware selection among the stored rate coefficients of one reaction entry, for one stated gas-phase
kinetic question (direction, target, coefficient basis, temperature window, pressure, collider). A read:
nothing is stored, no curator endorsement is created or changed, and the ordinary kinetics browse order
(review, then creation time, then id) is untouched. See ``docs/guides/selecting_kinetics.md``.

This request is deliberately separate from the shared read requests and does not extend the cross-product
``SelectionPolicy`` enum: ``method_preferred`` is a policy of this one quantity, not of every product.

No field here carries a database id. References are public refs (``rxe_``, ``tse_``, ``net_``, ``spc_``), and
every violation of the request's own rules is an ordinary 422 naming the field.
"""

from __future__ import annotations

import math
from datetime import datetime
from enum import Enum
from typing import Any, Final, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from tckdb_schemas.kinetics_declarations import MOLE_FRACTION_SUM_TOLERANCE, KineticsCoefficientBasis

from app.db.models.common import (
    KineticsDeterminationTargetKind,
    PhaseKind,
    RecordReviewStatus,
)
from app.schemas.reads.scientific_common import ProfiledRequestEcho

#: The one quantity this endpoint answers.
QUANTITY_RATE_COEFFICIENT: Final = "rate_coefficient"


class KineticsSelectionDirection(str, Enum):
    """Relative to the reaction entry's stored reactant-to-product orientation. A net rate is not selectable."""

    forward = "forward"
    reverse = "reverse"


class KineticsSelectionPolicy(str, Enum):
    """Which ordering the request asks for.

    ``method_preferred``  apply the registered, audited kinetics rules; the review/recency order only sorts
                          within a preference front. Every rule shipped in this release is inactive, so the
                          answer is the administrative order among eligible determinations, labelled as such.
    ``default``, ``most_reviewed``, ``latest``
                          administrative order only (no rules), exactly as on the browse endpoints.
    """

    method_preferred = "method_preferred"
    default = "default"
    most_reviewed = "most_reviewed"
    latest = "latest"


class KineticsSelectionMode(str, Enum):
    """``all`` returns the whole assessed population; ``first`` also names a determination when it can."""

    all = "all"
    first = "first"


class KineticsSelectionOutcome(str, Enum):
    """The selection basis (the service ``Outcome`` outside the bounded-search refusal, which the endpoint reports
    as the 422 ``kinetics_selection_population_too_large`` instead)."""

    policy_preferred = "policy_preferred"
    incomparable_alternatives = "incomparable_alternatives"
    sole_eligible_candidate = "sole_eligible_candidate"
    no_applicable_candidate = "no_applicable_candidate"
    policy_conflict = "policy_conflict"


class KineticsSelectionPressureKind(str, Enum):
    independent = "independent"
    high_pressure_limit = "high_pressure_limit"
    finite = "finite"


class KineticsSelectionTargetIn(BaseModel):
    """The rate asked for: the whole reaction, or one resolved channel."""

    model_config = ConfigDict(extra="forbid")

    kind: KineticsDeterminationTargetKind = Field(
        description="whole_reaction, or resolved_channel together with a transition state entry or a network channel."
    )
    transition_state_entry_ref: str | None = Field(
        default=None, max_length=64, description="Public transition-state-entry ref (tse_...). Resolved channels only."
    )
    network_ref: str | None = Field(
        default=None, max_length=64, description="Public network ref (net_...). With channel_key, resolved channels only."
    )
    channel_key: str | None = Field(
        default=None, max_length=256, description="The network channel's key. With network_ref, resolved channels only."
    )

    @model_validator(mode="after")
    def _locator_matches_kind(self) -> KineticsSelectionTargetIn:
        ts, net, key = self.transition_state_entry_ref, self.network_ref, self.channel_key
        if self.kind is KineticsDeterminationTargetKind.whole_reaction:
            if ts or net or key:
                raise ValueError("a whole_reaction target names no transition state entry or network channel")
            return self
        if bool(ts) == bool(net or key):
            raise ValueError(
                "a resolved_channel target names either a transition state entry or a network channel, not both"
            )
        if (net is None) != (key is None):
            raise ValueError("a network channel target needs both network_ref and channel_key")
        return self


class KineticsSelectionPressureIn(BaseModel):
    """Pressure as asked: independent, the high-pressure limit, or a finite window in bar (a point when equal)."""

    model_config = ConfigDict(extra="forbid")

    kind: KineticsSelectionPressureKind
    min_bar: float | None = Field(default=None, allow_inf_nan=False, gt=0)
    max_bar: float | None = Field(default=None, allow_inf_nan=False, gt=0)

    @model_validator(mode="after")
    def _bounds_match_kind(self) -> KineticsSelectionPressureIn:
        if self.kind is KineticsSelectionPressureKind.finite:
            if self.min_bar is None or self.max_bar is None:
                raise ValueError("a finite pressure needs min_bar and max_bar (equal for a point)")
            if self.min_bar > self.max_bar:
                raise ValueError("pressure min_bar must not exceed max_bar")
        elif self.min_bar is not None or self.max_bar is not None:
            raise ValueError(f"a {self.kind.value} pressure carries no bounds")
        return self


class KineticsSelectionColliderComponent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    species_ref: str = Field(min_length=1, max_length=64, description="Public species ref (spc_...).")
    mole_fraction: float | None = Field(default=None, allow_inf_nan=False, gt=0, le=1)


class KineticsSelectionColliderIn(BaseModel):
    """A specified collider (one component, no fraction) or an explicit mole-fraction mixture (two or more
    components, each with a fraction, summing to one; never renormalised)."""

    model_config = ConfigDict(extra="forbid")

    components: list[KineticsSelectionColliderComponent] = Field(min_length=1, max_length=64)

    @model_validator(mode="after")
    def _specified_or_mixture(self) -> KineticsSelectionColliderIn:
        refs = [c.species_ref for c in self.components]
        if len(set(refs)) != len(refs):
            raise ValueError("a collider component is listed twice")
        fractions = [c.mole_fraction for c in self.components]
        if len(self.components) == 1:
            if fractions[0] is not None:
                raise ValueError("one collider is a specified collider and carries no mole fraction")
            return self
        if any(f is None for f in fractions):
            raise ValueError("a mixture gives a mole fraction for every component")
        if abs(math.fsum(f for f in fractions if f is not None) - 1.0) > MOLE_FRACTION_SUM_TOLERANCE:
            raise ValueError("mole fractions must sum to 1; they are never renormalised")
        return self


class KineticsSelectionRequest(BaseModel):
    """Request body. ``quantity`` and ``phase`` may be omitted; if given they must agree with the endpoint."""

    model_config = ConfigDict(extra="forbid")

    quantity: Literal["rate_coefficient"] = QUANTITY_RATE_COEFFICIENT
    phase: PhaseKind | None = Field(default=None, description="Normalised to gas. Any other phase is refused.")
    direction: KineticsSelectionDirection
    target: KineticsSelectionTargetIn
    coefficient_basis: KineticsCoefficientBasis
    temperature_min_k: float = Field(allow_inf_nan=False, gt=0)
    temperature_max_k: float = Field(allow_inf_nan=False, gt=0)
    pressure: KineticsSelectionPressureIn
    collider: KineticsSelectionColliderIn | None = Field(
        default=None,
        description="Required for a finite pressure and for a composition-effective coefficient; optional otherwise.",
    )
    policy: KineticsSelectionPolicy = KineticsSelectionPolicy.method_preferred
    mode: KineticsSelectionMode = KineticsSelectionMode.all
    min_review_status: RecordReviewStatus | None = Field(
        default=None,
        description="Optional floor, applied on top of the read profile's floor (the stricter wins).",
    )

    @model_validator(mode="after")
    def _the_question_is_well_posed(self) -> KineticsSelectionRequest:
        if self.phase is not None and self.phase is not PhaseKind.gas:
            raise ValueError("rate_coefficient is answered for the gas phase only")
        if self.temperature_min_k > self.temperature_max_k:
            raise ValueError("temperature_min_k must not exceed temperature_max_k")
        material = (
            self.pressure.kind is KineticsSelectionPressureKind.finite
            or self.coefficient_basis is KineticsCoefficientBasis.composition_effective_coefficient
        )
        if material and self.collider is None:
            raise ValueError(
                "a collider is required for a finite pressure or a composition-effective coefficient"
            )
        return self


# ---------------------------------------------------------------------------
# Response
# ---------------------------------------------------------------------------


class KineticsSelectionTargetEcho(BaseModel):
    kind: KineticsDeterminationTargetKind
    transition_state_entry_ref: str | None = None
    network_ref: str | None = None
    channel_key: str | None = None


class KineticsSelectionPressureEcho(BaseModel):
    kind: KineticsSelectionPressureKind
    min_bar: float | None = None
    max_bar: float | None = None


class KineticsSelectionColliderEcho(BaseModel):
    components: list[KineticsSelectionColliderComponent]


class KineticsSelectionRequestEcho(ProfiledRequestEcho):
    """The normalised request, as the server understood it."""

    reaction_entry_ref: str
    quantity: Literal["rate_coefficient"] = QUANTITY_RATE_COEFFICIENT
    phase: Literal["gas"] = "gas"
    direction: KineticsSelectionDirection
    target: KineticsSelectionTargetEcho
    coefficient_basis: KineticsCoefficientBasis
    temperature_min_k: float
    temperature_max_k: float
    pressure: KineticsSelectionPressureEcho
    collider: KineticsSelectionColliderEcho | None = None
    policy: KineticsSelectionPolicy
    mode: KineticsSelectionMode
    min_review_status: RecordReviewStatus | None = None


class KineticsSelectionReview(BaseModel):
    """The review floor actually applied: the request's floor combined with the read profile's."""

    effective_floor: RecordReviewStatus = Field(
        description="The weakest review status a candidate may have. Rejected and deprecated never compete."
    )
    effective_statuses: list[RecordReviewStatus]


class KineticsSelectionPolicyInfo(BaseModel):
    """The selection semantics and the rule versions that were consulted."""

    name: str
    version: str
    rules_applied: bool = Field(
        description="False under an administrative policy: no rule was consulted, only the administrative order."
    )
    rules: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Registry entries consulted: rule id, version, status (every rule shipped is inactive), "
        "objective, evidence type and limits, and why an inactive rule is inactive.",
    )


class KineticsSelectionPick(BaseModel):
    """The determination this response names (with every one of its eligible fitted representations)."""

    determination_ref: str
    kinetics_refs: list[str] = Field(
        description="Every eligible fitted representation of the determination, in administrative order. Alternate "
        "fits of one determination are not independent confirmation, and the first is not scientifically superior."
    )
    basis: Literal["policy_preferred", "sole_eligible_candidate", "administrative_first"]
    administrative: bool = Field(
        description="True when the determination was chosen by review/recency order among unranked alternatives. "
        "That is not a claim that it is method-superior."
    )
    explanation: str


class KineticsSelectionReason(BaseModel):
    code: str
    applicability: str


class KineticsSelectionCandidate(BaseModel):
    """One assessed record: its stored claims, its applicability to the question, and whether it competed."""

    kinetics_ref: str
    review_status: RecordReviewStatus
    created_at: datetime
    scientific_origin: str
    model_kind: str
    direction: str | None = None
    determination_ref: str | None = None
    representation_role: str | None = None
    applicability_state: str = Field(description="absent, valid or unreadable. Absent is 'not stated', never a default.")
    applicability_declaration: dict[str, Any] | None = None
    protocol_state: str = Field(description="absent, valid or unreadable. Absent is 'not stated', never 'standard'.")
    protocol: dict[str, Any] | None = None
    applicability: str = Field(description="applicable, incompatible, unsupported or unresolved.")
    reasons: list[KineticsSelectionReason] = Field(default_factory=list)
    blocking: list[str] = Field(default_factory=list)
    advisory: list[str] = Field(default_factory=list)
    eligible: bool = Field(description="Applicable, with no blocking finding: it competes in the ordering.")


class KineticsSelectionDetermination(BaseModel):
    determination_ref: str
    representation_refs: list[str]
    representative_ref: str
    representation_count: int


class KineticsSelectionEdge(BaseModel):
    preferred: str
    dispreferred: str
    rule_id: str = Field(description="The registry key of the rule. Not a database id.")
    rule_version: str
    label: str = Field(description="Always 'expected-performance inference': a benchmark, not a measurement.")
    objective_key: str


class KineticsSelectionRelations(BaseModel):
    """Preference relations between eligible determinations, and any contradiction among them."""

    edges: list[KineticsSelectionEdge] = Field(default_factory=list)
    overridden_edges: list[dict[str, Any]] = Field(default_factory=list)
    opposing_pairs: list[dict[str, Any]] = Field(default_factory=list)
    cycles: list[list[str]] = Field(default_factory=list)
    unused_edges: list[dict[str, Any]] = Field(
        default_factory=list, description="Edges under more than one objective are not composed; they are listed here."
    )


class KineticsSelectionExcluded(BaseModel):
    kinetics_ref: str
    review_status: RecordReviewStatus
    reason: str


class KineticsSelectionDisclosures(BaseModel):
    """What the result does not cover. Never empty by omission: a withheld list says so."""

    unresolved_refs: list[str] = Field(default_factory=list)
    unsupported_refs: list[str] = Field(default_factory=list)
    visible_candidates: int = Field(description="Records at or above the effective floor (the assessed population).")
    excluded_by_review: list[KineticsSelectionExcluded] = Field(default_factory=list)
    excluded_count: int = Field(description="How many records the effective floor excluded (0 when withheld).")
    excluded_by_review_withheld: bool = Field(
        description="True whenever the read profile has a review floor of its own (curated): records outside the "
        "effective floor are then never listed or counted, whether or not any exist, so the field cannot reveal "
        "that they do. False under a profile with no floor, where they are listed."
    )
    notes: list[str] = Field(default_factory=list)


class KineticsSelectionResponse(BaseModel):
    """The decision, its basis, and everything it rests on, as public refs only."""

    request: KineticsSelectionRequestEcho
    review: KineticsSelectionReview
    policy: KineticsSelectionPolicyInfo
    outcome: KineticsSelectionOutcome
    basis: str = Field(description="One sentence saying why this outcome, in plain terms.")
    selection: KineticsSelectionPick | None = None
    fronts: list[list[str]] = Field(
        default_factory=list, description="Preference fronts of determinations, best first; within a front unranked."
    )
    administrative_order: list[str] = Field(default_factory=list)
    determinations: list[KineticsSelectionDetermination] = Field(default_factory=list)
    candidates: list[KineticsSelectionCandidate] = Field(default_factory=list)
    relations: KineticsSelectionRelations = Field(default_factory=KineticsSelectionRelations)
    rule_matches: list[dict[str, Any]] = Field(default_factory=list)
    pair_checks: list[dict[str, Any]] = Field(default_factory=list)
    disclosures: KineticsSelectionDisclosures


class KineticsSelectionManifestRequest(ProfiledRequestEcho):
    """The manifest's normalised request, with the read profile it was made under. Further keys the decision
    recorded are kept."""

    model_config = ConfigDict(extra="allow")

    quantity: str
    phase: str
    direction: str
    target: dict[str, Any]
    coefficient_basis: str
    temperature_min_k: float
    temperature_max_k: float
    pressure: dict[str, Any]
    collider: dict[str, Any] | None = None
    min_review_status: RecordReviewStatus | None = None
    effective_review_statuses: list[RecordReviewStatus]
    administrative_policy: str


class KineticsSelectionManifest(BaseModel):
    """The replayable decision manifest of ``POST .../kinetics/select/manifest``.

    Public refs only. Holds the normalised request, the snapshot isolation it was read under, every assessed
    candidate with its normalised inputs (``id_rank`` is an ordinal over the visible records, not an id), the
    determinations the eligible ones form, the rules consulted with each rule's verdict per determination, the
    pair checks, the preference edges, the fronts and the administrative order: enough to recompute the decision
    with no database.
    """

    manifest_format_version: int
    policy: dict[str, Any]
    request: KineticsSelectionManifestRequest
    snapshot_isolation: str
    subject: dict[str, Any]
    population: dict[str, Any]
    candidates: list[dict[str, Any]]
    determinations: list[dict[str, Any]]
    decision: dict[str, Any]
    outcome: KineticsSelectionOutcome
    disclosures: dict[str, Any] = Field(default_factory=dict)
