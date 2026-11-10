"""Read schemas for ``POST /scientific/networks/{network_ref}/kinetics/select`` (+ ``/manifest``).

Selection among the stored pressure-dependent solves of one network, for one stated gas-phase rate-coefficient
question: which channel or declared bundle of outputs, over which temperature and pressure window, in which bath,
under which state partition, boundaries and regime, with which coefficient meaning. Which solves are eligible, and
their display order (``docs/specs/browse_order.md``); TCKDB ranks no method above another. A read: nothing is stored, no curator endorsement is created or changed, and the ordinary network
browse and evaluation endpoints are untouched. See ``docs/guides/selecting_network_kinetics.md``.

No field here carries a database id. References are public refs (``spe_``, ``nsolve_``) and composition hashes (the
content locators of a network's states); the network itself is named by the path, and ``channel_key`` is a body
field, never a path segment. Every violation of the request's own rules is an ordinary 422 naming the field.
"""

from __future__ import annotations

import math
from enum import Enum
from typing import Any, Final, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from tckdb_schemas.fragments.version import VersionRef
from tckdb_schemas.network_declarations import NetworkObservable, NetworkRegimeKind

from app.db.models.common import NetworkKineticsModelKind, PhaseKind, RecordReviewStatus
from app.schemas.reads.scientific_common import ProfiledRequestEcho
from app.schemas.reads.scientific_selection_common import (
    CalculationProvenanceFilters,
    ReviewEligibilityIn,
    SelectionStepsAnswer,
)

QUANTITY_RATE_COEFFICIENT: Final = "rate_coefficient"
MOLE_FRACTION_TOLERANCE: Final = 1e-9


class NetworkSelectionScope(str, Enum):
    single_channel = "single_channel"
    projected_bundle = "projected_bundle"
    full_network = "full_network"


class NetworkSelectionPolicy(str, Enum):
    """The administrative order the request asks for, exactly as on the browse endpoints: review rank then newest
    deposited (``default`` and ``most_reviewed``), or newest deposited first (``latest``). None is a claim about
    which method is better."""

    default = "default"
    most_reviewed = "most_reviewed"
    latest = "latest"


class NetworkSelectionMode(str, Enum):
    all = "all"
    first = "first"


class NetworkSelectionOutcome(str, Enum):
    incomparable_alternatives = "incomparable_alternatives"
    sole_eligible_candidate = "sole_eligible_candidate"
    no_applicable_candidate = "no_applicable_candidate"


class NetworkSelectionBathComponent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    species_ref: str = Field(min_length=1, max_length=64, description="Public species-entry ref.")
    mole_fraction: float | None = Field(default=None, allow_inf_nan=False, gt=0, le=1)


class NetworkSelectionBathIn(BaseModel):
    """A specified collider (one component, no fraction) or an explicit mixture (two or more, each with a mole
    fraction, summing to one; never renormalised)."""

    model_config = ConfigDict(extra="forbid")

    components: list[NetworkSelectionBathComponent] = Field(min_length=1, max_length=64)

    @model_validator(mode="after")
    def _specified_or_mixture(self) -> NetworkSelectionBathIn:
        refs = [c.species_ref for c in self.components]
        if len(set(refs)) != len(refs):
            raise ValueError("a bath component is listed twice")
        fractions = [c.mole_fraction for c in self.components]
        if len(self.components) == 1:
            if fractions[0] is not None:
                raise ValueError("one collider is a specified collider and carries no mole fraction")
            return self
        if any(f is None for f in fractions):
            raise ValueError("a mixture gives a mole fraction for every component")
        if abs(math.fsum(f for f in fractions if f is not None) - 1.0) > MOLE_FRACTION_TOLERANCE:
            raise ValueError("mole fractions must sum to 1; they are never renormalised")
        return self


class NetworkSelectionPartitionIn(BaseModel):
    """How the requested observable treats the network's states, by composition hash."""

    model_config = ConfigDict(extra="forbid")

    retained: list[str] = Field(default_factory=list, max_length=1000)
    eliminated: list[str] = Field(default_factory=list, max_length=1000)
    lumps: list[list[str]] = Field(default_factory=list, max_length=1000)

    @model_validator(mode="after")
    def _well_formed(self) -> NetworkSelectionPartitionIn:
        if not (self.retained or self.lumps):
            raise ValueError("a partition retains at least one state or lump")
        names = [*self.retained, *self.eliminated, *(m for lump in self.lumps for m in lump)]
        if len(set(names)) != len(names):
            raise ValueError("a state appears in the partition once")
        if any(len(lump) < 2 for lump in self.lumps):
            raise ValueError("a lump has at least two states")
        return self


class NetworkSelectionBoundaryIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    state: str = Field(min_length=1, max_length=128, description="Composition hash of the state.")
    kind: str = Field(min_length=1, max_length=64, description="Boundary kind, e.g. absorbing.")


class NetworkSelectionRegimeIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: NetworkRegimeKind = NetworkRegimeKind.time_independent
    initial_state_hashes: list[str] = Field(default_factory=list, max_length=1000)

    @model_validator(mode="after")
    def _initial_states(self) -> NetworkSelectionRegimeIn:
        restricted = self.kind is NetworkRegimeKind.initial_population_restricted
        if restricted != bool(self.initial_state_hashes):
            raise ValueError("initial states are stated for, and only for, an initial-population-restricted regime")
        return self


class NetworkSelectionOutputIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    channel_key: str = Field(min_length=1, max_length=256)
    observable: NetworkObservable


class NetworkSelectionFilters(CalculationProvenanceFilters):
    """The filters the network-kinetics search offers, with their meaning.

    ``model_kind`` keeps the fits of that form (a solve with none is out). The provenance fields keep a solve when at
    least one of its source calculations that the read profile shows matches every supplied field. Applied before
    eligibility; a filter excludes on a fact and ranks nothing.
    """

    model_kind: NetworkKineticsModelKind | None = None


class NetworkSelectionRequest(BaseModel):
    """Request body. ``quantity`` and ``phase`` may be omitted; if given they must agree with the endpoint.

    The network is named by the path. A single-channel request names ``channel_key`` and ``observable``; a bundle or
    full-network request lists ``outputs`` instead. The number of outputs is bounded by the server (a coded 422),
    not by this schema.
    """

    model_config = ConfigDict(extra="forbid")

    quantity: Literal["rate_coefficient"] = QUANTITY_RATE_COEFFICIENT
    phase: PhaseKind | None = Field(default=None, description="Normalised to gas. Any other phase is refused.")
    scope: NetworkSelectionScope = NetworkSelectionScope.single_channel
    channel_key: str | None = Field(default=None, min_length=1, max_length=256)
    observable: NetworkObservable | None = None
    outputs: list[NetworkSelectionOutputIn] = Field(default_factory=list, max_length=100000)
    coefficient_basis: Literal["kernel", "composition_effective"]
    degeneracy_applied: bool | None = None
    temperature_min_k: float = Field(allow_inf_nan=False, gt=0)
    temperature_max_k: float = Field(allow_inf_nan=False, gt=0)
    pressure_min_bar: float = Field(allow_inf_nan=False, gt=0)
    pressure_max_bar: float = Field(allow_inf_nan=False, gt=0)
    bath: NetworkSelectionBathIn
    partition: NetworkSelectionPartitionIn
    boundaries: list[NetworkSelectionBoundaryIn] = Field(default_factory=list, max_length=1000)
    regime: NetworkSelectionRegimeIn = Field(default_factory=NetworkSelectionRegimeIn)
    source_composition_hash: str | None = Field(default=None, max_length=128)
    sink_composition_hash: str | None = Field(default=None, max_length=128)
    policy: NetworkSelectionPolicy = NetworkSelectionPolicy.default
    mode: NetworkSelectionMode = NetworkSelectionMode.all
    min_review_status: RecordReviewStatus | None = Field(
        default=None,
        description="Optional floor, applied on top of the read profile's floor (the stricter wins).",
    )
    filters: NetworkSelectionFilters = Field(
        default_factory=NetworkSelectionFilters,
        description="Narrow the solves by facts before eligibility, with the network-kinetics search's own filters.",
    )
    eligibility: ReviewEligibilityIn = Field(
        default_factory=ReviewEligibilityIn,
        description="Lift named eligibility defaults: a solve's rejected or deprecated review status. Every default is "
        "applied unless lifted here; the answer lists each.",
    )

    @model_validator(mode="after")
    def _the_question_is_well_posed(self) -> NetworkSelectionRequest:
        if self.phase is not None and self.phase is not PhaseKind.gas:
            raise ValueError("rate_coefficient is answered for the gas phase only")
        if self.temperature_min_k > self.temperature_max_k:
            raise ValueError("temperature_min_k must not exceed temperature_max_k")
        if self.pressure_min_bar > self.pressure_max_bar:
            raise ValueError("pressure_min_bar must not exceed pressure_max_bar")
        if self.scope is NetworkSelectionScope.single_channel:
            if not self.channel_key or self.observable is None or self.outputs:
                raise ValueError("a single-channel request names channel_key and observable, and lists no outputs")
        else:
            if self.channel_key is not None or self.observable is not None or not self.outputs:
                raise ValueError("a bundle or full-network request lists outputs and names no single channel")
            keys = [o.channel_key for o in self.outputs]
            if len(set(keys)) != len(keys):
                raise ValueError("a required output is listed once")
        if (self.source_composition_hash is None) != (self.sink_composition_hash is None):
            raise ValueError("source and sink composition hashes are stated together")
        return self


# ---------------------------------------------------------------------------
# Response
# ---------------------------------------------------------------------------


class NetworkSelectionRequestEcho(ProfiledRequestEcho):
    """The normalised request, as the server understood it (further keys the decision recorded are kept)."""

    model_config = ConfigDict(extra="allow")

    network_ref: str
    quantity: Literal["rate_coefficient"] = QUANTITY_RATE_COEFFICIENT
    phase: Literal["gas"] = "gas"
    scope: NetworkSelectionScope
    channel_key: str | None = None
    observable: str | None = None
    outputs: list[dict[str, Any]] = Field(default_factory=list)
    coefficient_basis: str
    temperature_min_k: float
    temperature_max_k: float
    pressure_min_bar: float
    pressure_max_bar: float
    administrative_policy: str
    result_mode: str
    min_review_status: RecordReviewStatus | None = None
    effective_review_statuses: list[RecordReviewStatus] = Field(default_factory=list)


class NetworkSelectionReview(BaseModel):
    effective_floor: RecordReviewStatus = Field(
        description="The weakest review status a candidate may have. Rejected and deprecated never compete."
    )
    effective_statuses: list[RecordReviewStatus]


class NetworkSelectionPolicyInfo(BaseModel):
    name: str
    version: int
    bounds_version: int


class NetworkSelectionMember(BaseModel):
    determination_ref: str
    channel_key: str | None = None
    kinetics_refs: list[str] = Field(
        description="Every eligible fitted representation of the determination, in administrative order. Alternate "
        "fits of one determination are not independent confirmation."
    )


class NetworkSelectionSolve(BaseModel):
    """One solve that competed: its review status, and the default it passed only because the request lifted it.

    Review and provenance belong to the solve, so this is where the eligibility defaults act; every determination
    and bundle in the answer belongs to one of these.
    """

    solve_ref: str
    review_status: RecordReviewStatus
    passed_only_by_override: list[str] = Field(
        default_factory=list,
        description="Eligibility defaults this solve would fail but the request lifted: it competes because of that.",
    )


class NetworkSelectionPick(BaseModel):
    """The node this response names: a determination of one channel, or a declared product set of one solve."""

    node_ref: str = Field(description="A determination ref, or '<solve ref>/<product set key>' for a bundle.")
    scope: NetworkSelectionScope
    solve_ref: str
    members: list[NetworkSelectionMember]
    basis: Literal["sole_eligible_candidate", "administrative_first"]
    administrative: bool = Field(
        description="True when first in display order among several eligible alternatives: not a claim that it is "
        "better, preferred or method-superior."
    )
    explanation: str


class NetworkSelectionDisclosures(BaseModel):
    """What the result does not cover. A withheld list says so."""

    unresolved_refs: list[str] = Field(default_factory=list)
    unsupported_refs: list[str] = Field(default_factory=list)
    ungrouped_fit_refs: list[str] = Field(default_factory=list)
    population: dict[str, int] = Field(default_factory=dict, description="Counts of the assessed population.")
    excluded_by_review: list[dict[str, str]] = Field(default_factory=list)
    excluded_count: int
    excluded_by_review_withheld: bool = Field(
        description="True whenever the read profile has a review floor of its own (curated): records outside the "
        "effective floor are then never listed or counted, so the field cannot reveal that they exist."
    )
    notes: list[str] = Field(default_factory=list)


class NetworkSelectionResponse(SelectionStepsAnswer):
    """The decision, its basis and everything it rests on, as public refs only."""

    request: NetworkSelectionRequestEcho
    review: NetworkSelectionReview
    policy: NetworkSelectionPolicyInfo
    outcome: NetworkSelectionOutcome
    basis: str = Field(description="One sentence saying why this outcome, in plain terms.")
    selection: NetworkSelectionPick | None = None
    fronts: list[list[str]] = Field(
        default_factory=list,
        description="The eligible nodes that tie, in display order. With no ranking between methods there is "
        "exactly one front (none when nothing is eligible); its order is not a preference.",
    )
    administrative_order: list[str] = Field(default_factory=list)
    determinations: list[dict[str, Any]] = Field(
        default_factory=list, description="The assessment of every determination, with every finding behind it."
    )
    bundles: list[dict[str, Any]] = Field(
        default_factory=list, description="The assessment of every declared product set (bundle and full-network)."
    )
    solves: list[NetworkSelectionSolve] = Field(
        default_factory=list,
        description="Every solve that competed (passed the filters and the applied defaults), with the default it "
        "passed only by override, if any.",
    )
    representations: list[dict[str, Any]] = Field(
        default_factory=list,
        description="The alternate fits inside each eligible node, in display order. They rank no solve and add no "
        "independent confirmation.",
    )
    disclosures: NetworkSelectionDisclosures


class NetworkSelectionManifest(BaseModel):
    """The replayable decision manifest of ``POST .../kinetics/select/manifest``.

    Public refs only: the normalised request, the snapshot isolation, every captured solve with its normalised facts,
    every assessment and the administrative order. Enough to recompute the
    decision at both levels with no database.
    """

    model_config = ConfigDict(extra="allow")

    format: VersionRef
    policy: dict[str, Any]
    declaration_versions: list[int] = Field(default_factory=list)
    request: NetworkSelectionRequestEcho
    visibility: dict[str, Any]
    snapshot_isolation: str
    network: dict[str, Any]
    population: dict[str, Any]
    solves: list[dict[str, Any]]
    assessments: dict[str, Any]
    decision: dict[str, Any]
    outcome: NetworkSelectionOutcome
    disclosures: dict[str, Any] = Field(default_factory=dict)
    replay_boundary: str
    digest: dict[str, str]
