"""Plain values for pressure-dependent network selection: the normalised request, one solve's
normalised facts, and the assessment of a determination or a product-set bundle.

Everything here is data. :class:`SolveFacts` is the whole of what the assessor (and, later, a rule)
may look at: public refs, composition hashes, review state, timestamps, stored columns and the
declared claims; never a database id or an ORM row. That is what lets a decision manifest be
replayed without a database. ``id_rank`` is a record's position when its population is ordered by
database id: it stands in for the id as the last tie-break and is not an id.

What a declaration does not say is *unknown* here, not a default: a null stays ``None`` (or the
state ``absent``) all the way to the assessor, which turns it into ``unresolved``.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

from tckdb_schemas.network_declarations import (
    NetworkComparisonObjective,
    NetworkObservable,
    NetworkRegimeKind,
)

from app.db.models.common import RecordReviewStatus
from app.schemas.reads.scientific_common import SelectionPolicy
from app.services.selection_kernel import Applicability

QUANTITY = "rate_coefficient"
PHASE = "gas"
#: Version of the selection semantics (eligibility, grouping, certification). A change to any of them is a
#: new version, and a decision manifest records the version it was made under.
POLICY_NAME = "network_method_preferred"
POLICY_VERSION = "1"
#: Absolute tolerance on a bath mixture's mole fractions: the one the upload applies.
MOLE_FRACTION_TOLERANCE = 1e-9
#: A request bound matches a declared or stored bound within this (the existing browse tolerances).
BOUND_REL_TOL = 1.0e-9
BOUND_ABS_TOL = 1.0e-12


@dataclass(frozen=True)
class SelectionBounds:
    """Supported server-side engineering limits. Resource limits, not confidence measures.

    A limit is a documented revision of ``version``; changing one is not a change of scientific rule.
    Exceeding any of them refuses the decision (HTTP 422): nothing is selected from a prefix, and the
    refusal names the bound and the visible count, never a hidden population.
    """

    version: str = "1"
    solves: int = 200
    kinetics_parents: int = 2000
    channel_nodes: int = 500
    bundle_nodes: int = 500
    states: int = 1000
    channels: int = 2000
    required_outputs: int = 200
    evidence_entries: int = 10000
    numeric_cells: int = 100000
    snapshot_bytes: int = 8 * 1024 * 1024

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> SelectionBounds:
        return cls(**raw)


BOUNDS_V1 = SelectionBounds()


class Scope(str, Enum):
    single_channel = "single_channel"
    projected_bundle = "projected_bundle"
    full_network = "full_network"


@dataclass(frozen=True)
class PartitionRequest:
    """How the requested observable treats the network's states, by composition hash (a content locator)."""

    retained: tuple[str, ...] = ()
    eliminated: tuple[str, ...] = ()
    lumps: tuple[tuple[str, ...], ...] = ()

    def __post_init__(self) -> None:
        names = [*self.retained, *self.eliminated, *(m for lump in self.lumps for m in lump)]
        if not (self.retained or self.lumps):
            raise ValueError("a partition retains at least one state or lump")
        if len(set(names)) != len(names):
            raise ValueError("a state appears in the partition once")
        if any(len(lump) < 2 for lump in self.lumps):
            raise ValueError("a lump has at least two states")

    def normalized(self) -> tuple[tuple[str, ...], tuple[str, ...], tuple[tuple[str, ...], ...]]:
        return (
            tuple(sorted(self.retained)),
            tuple(sorted(self.eliminated)),
            tuple(sorted(tuple(sorted(lump)) for lump in self.lumps)),
        )

    def to_dict(self) -> dict[str, Any]:
        retained, eliminated, lumps = self.normalized()
        return {"retained": list(retained), "eliminated": list(eliminated), "lumps": [list(x) for x in lumps]}

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> PartitionRequest:
        return cls(
            tuple(raw["retained"]), tuple(raw["eliminated"]), tuple(tuple(lump) for lump in raw["lumps"])
        )


@dataclass(frozen=True)
class BathRequest:
    """A specified collider (one species, no fractions) or an explicit mixture (never renormalised)."""

    species_refs: tuple[str, ...]
    mole_fractions: tuple[float, ...] | None = None

    def __post_init__(self) -> None:
        if not self.species_refs:
            raise ValueError("a bath names at least one species")
        if len(set(self.species_refs)) != len(self.species_refs):
            raise ValueError("a bath component is listed twice")
        if self.mole_fractions is None:
            if len(self.species_refs) != 1:
                raise ValueError("several bath species need mole fractions; one is a specified collider")
            return
        if len(self.mole_fractions) != len(self.species_refs) or len(self.species_refs) < 2:
            raise ValueError("a mixture lists at least two species, each with a mole fraction")
        if any(not math.isfinite(f) or f <= 0 or f > 1 for f in self.mole_fractions):
            raise ValueError("each mole fraction is in (0, 1]")
        if abs(math.fsum(self.mole_fractions) - 1.0) > MOLE_FRACTION_TOLERANCE:
            raise ValueError("mole fractions must sum to 1; they are never renormalised")

    @property
    def is_mixture(self) -> bool:
        return self.mole_fractions is not None

    def to_dict(self) -> dict[str, Any]:
        return {
            "species_refs": list(self.species_refs),
            "mole_fractions": list(self.mole_fractions) if self.mole_fractions is not None else None,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> BathRequest:
        fractions = raw["mole_fractions"]
        return cls(tuple(raw["species_refs"]), tuple(fractions) if fractions is not None else None)


@dataclass(frozen=True)
class OutputRequest:
    """One required output: a directed channel and the coefficient meaning asked of it."""

    channel_key: str
    observable: NetworkObservable

    def to_dict(self) -> dict[str, str]:
        return {"channel_key": self.channel_key, "observable": self.observable.value}


@dataclass(frozen=True)
class NetworkRequest:
    """The normalised request: a gas-phase, finite-pressure rate coefficient of one network.

    :param scope: ``single_channel`` (``channel_key``), ``projected_bundle`` or ``full_network`` (``outputs``).
    :param observable: The coefficient meaning of a single channel; a bundle states one per output.
    :param coefficient_basis: ``kernel`` or ``composition_effective``.
    :param degeneracy_applied: Whether reaction-path degeneracy must already be in the coefficient.
    :param bath: Required: a finite pressure is always collider-dependent.
    :param partition: Required: the states' treatment, by composition hash.
    :param boundaries: ``(composition_hash, boundary kind)`` pairs the observable requires; unlisted states unconstrained.
    :param source_composition_hash: With ``sink_composition_hash``, the directed endpoints expected of a single channel.
    :param objective: What the comparison is a comparison of. ``reference_model_ref`` is required for
        ``model_fidelity`` and ``reference_outputs`` for ``representation_fidelity``, and refused otherwise.
    """

    network_ref: str
    scope: Scope
    coefficient_basis: str
    temperature_min_k: float
    temperature_max_k: float
    pressure_min_bar: float
    pressure_max_bar: float
    bath: BathRequest
    partition: PartitionRequest
    objective: NetworkComparisonObjective
    regime_kind: NetworkRegimeKind = NetworkRegimeKind.time_independent
    initial_state_hashes: tuple[str, ...] = ()
    boundaries: tuple[tuple[str, str], ...] = ()
    channel_key: str | None = None
    observable: NetworkObservable | None = None
    outputs: tuple[OutputRequest, ...] = ()
    degeneracy_applied: bool | None = None
    source_composition_hash: str | None = None
    sink_composition_hash: str | None = None
    reference_model_ref: str | None = None
    reference_outputs: str | None = None
    min_review_status: RecordReviewStatus | None = None
    admin_policy: SelectionPolicy = SelectionPolicy.default
    first_only: bool = False
    apply_rules: bool = True
    bounds: SelectionBounds = field(default=BOUNDS_V1)

    def __post_init__(self) -> None:
        if self.coefficient_basis not in {"kernel", "composition_effective"}:
            raise ValueError("coefficient_basis is kernel or composition_effective")
        for name in ("temperature_min_k", "temperature_max_k", "pressure_min_bar", "pressure_max_bar"):
            value = getattr(self, name)
            if not math.isfinite(value) or value <= 0:
                raise ValueError(f"{name} must be positive and finite")
        if self.temperature_min_k > self.temperature_max_k or self.pressure_min_bar > self.pressure_max_bar:
            raise ValueError("a minimum must not exceed its maximum")
        if self.scope is Scope.single_channel:
            if not self.channel_key or self.observable is None or self.outputs:
                raise ValueError("a single-channel request names channel_key and observable, and no outputs")
        else:
            if self.channel_key is not None or self.observable is not None or not self.outputs:
                raise ValueError("a bundle request lists required outputs and names no single channel")
            keys = [o.channel_key for o in self.outputs]
            if len(set(keys)) != len(keys):
                raise ValueError("a required output is listed once")
            if len(self.outputs) > self.bounds.required_outputs:
                raise ValueError(f"at most {self.bounds.required_outputs} required outputs")
        if (self.source_composition_hash is None) != (self.sink_composition_hash is None):
            raise ValueError("source and sink composition hashes are stated together")
        has_model, has_outputs = self.reference_model_ref is not None, self.reference_outputs is not None
        if self.objective is NetworkComparisonObjective.model_fidelity and not has_model:
            raise ValueError("a model-fidelity comparison pins its reference model")
        if self.objective is NetworkComparisonObjective.representation_fidelity and not has_outputs:
            raise ValueError("a representation-fidelity comparison pins its reference outputs")
        if has_model and self.objective is not NetworkComparisonObjective.model_fidelity:
            raise ValueError("reference_model_ref is only for the model_fidelity objective")
        if has_outputs and self.objective is not NetworkComparisonObjective.representation_fidelity:
            raise ValueError("reference_outputs is only for the representation_fidelity objective")
        restricted = self.regime_kind is NetworkRegimeKind.initial_population_restricted
        if restricted != bool(self.initial_state_hashes):
            raise ValueError("initial states are stated for, and only for, an initial-population-restricted regime")

    def required_outputs(self) -> tuple[OutputRequest, ...]:
        if self.scope is Scope.single_channel:
            assert self.channel_key is not None and self.observable is not None
            return (OutputRequest(self.channel_key, self.observable),)
        return self.outputs

    def to_dict(self) -> dict[str, Any]:
        return {
            "quantity": QUANTITY,
            "phase": PHASE,
            "network_ref": self.network_ref,
            "scope": self.scope.value,
            "channel_key": self.channel_key,
            "observable": self.observable.value if self.observable else None,
            "outputs": [o.to_dict() for o in self.outputs],
            "coefficient_basis": self.coefficient_basis,
            "degeneracy_applied": self.degeneracy_applied,
            "temperature_min_k": self.temperature_min_k,
            "temperature_max_k": self.temperature_max_k,
            "pressure_min_bar": self.pressure_min_bar,
            "pressure_max_bar": self.pressure_max_bar,
            "bath": self.bath.to_dict(),
            "partition": self.partition.to_dict(),
            "boundaries": [list(b) for b in sorted(self.boundaries)],
            "regime_kind": self.regime_kind.value,
            "initial_state_hashes": sorted(self.initial_state_hashes),
            "source_composition_hash": self.source_composition_hash,
            "sink_composition_hash": self.sink_composition_hash,
            "objective": self.objective.value,
            "reference_model_ref": self.reference_model_ref,
            "reference_outputs": self.reference_outputs,
            "min_review_status": self.min_review_status.value if self.min_review_status else None,
            "administrative_policy": self.admin_policy.value,
            "result_mode": "first" if self.first_only else "all",
            "apply_rules": self.apply_rules,
            "bounds": self.bounds.to_dict(),
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> NetworkRequest:
        if raw["quantity"] != QUANTITY or raw["phase"] != PHASE:
            raise ValueError("a manifest request is for a gas-phase rate coefficient")
        return cls(
            network_ref=raw["network_ref"],
            scope=Scope(raw["scope"]),
            channel_key=raw["channel_key"],
            observable=NetworkObservable(raw["observable"]) if raw["observable"] else None,
            outputs=tuple(OutputRequest(o["channel_key"], NetworkObservable(o["observable"])) for o in raw["outputs"]),
            coefficient_basis=raw["coefficient_basis"],
            degeneracy_applied=raw["degeneracy_applied"],
            temperature_min_k=raw["temperature_min_k"],
            temperature_max_k=raw["temperature_max_k"],
            pressure_min_bar=raw["pressure_min_bar"],
            pressure_max_bar=raw["pressure_max_bar"],
            bath=BathRequest.from_dict(raw["bath"]),
            partition=PartitionRequest.from_dict(raw["partition"]),
            boundaries=tuple((b[0], b[1]) for b in raw["boundaries"]),
            regime_kind=NetworkRegimeKind(raw["regime_kind"]),
            initial_state_hashes=tuple(raw["initial_state_hashes"]),
            source_composition_hash=raw["source_composition_hash"],
            sink_composition_hash=raw["sink_composition_hash"],
            objective=NetworkComparisonObjective(raw["objective"]),
            reference_model_ref=raw["reference_model_ref"],
            reference_outputs=raw["reference_outputs"],
            min_review_status=RecordReviewStatus(raw["min_review_status"]) if raw["min_review_status"] else None,
            admin_policy=SelectionPolicy(raw["administrative_policy"]),
            first_only=raw["result_mode"] == "first",
            apply_rules=raw["apply_rules"],
            bounds=SelectionBounds.from_dict(raw["bounds"]),
        )


# ---------------------------------------------------------------------------
# What the loader captures
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class StateFact:
    """A network state: its composition hash (the locator) and the exact content behind it."""

    composition_hash: str
    kind: str
    participants: tuple[tuple[str, int], ...]  # (species_entry_ref, stoichiometry), sorted

    def to_dict(self) -> dict[str, Any]:
        return {
            "composition_hash": self.composition_hash,
            "kind": self.kind,
            "participants": [list(p) for p in self.participants],
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> StateFact:
        return cls(raw["composition_hash"], raw["kind"], tuple((p[0], p[1]) for p in raw["participants"]))


@dataclass(frozen=True)
class ChannelFact:
    channel_key: str
    source_hash: str
    sink_hash: str
    kind: str
    mechanism: str

    def to_dict(self) -> dict[str, str]:
        return dict(self.__dict__)

    @classmethod
    def from_dict(cls, raw: dict[str, str]) -> ChannelFact:
        return cls(**raw)


@dataclass(frozen=True)
class NetworkFacts:
    """The network a population belongs to: its states (content-backed) and its directed channels."""

    network_ref: str
    states: tuple[StateFact, ...]
    channels: tuple[ChannelFact, ...]

    def state(self, composition_hash: str) -> StateFact | None:
        return next((s for s in self.states if s.composition_hash == composition_hash), None)

    def channel(self, channel_key: str) -> ChannelFact | None:
        return next((c for c in self.channels if c.channel_key == channel_key), None)

    def to_dict(self) -> dict[str, Any]:
        return {
            "network_ref": self.network_ref,
            "states": [s.to_dict() for s in self.states],
            "channels": [c.to_dict() for c in self.channels],
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> NetworkFacts:
        return cls(
            raw["network_ref"],
            tuple(StateFact.from_dict(s) for s in raw["states"]),
            tuple(ChannelFact.from_dict(c) for c in raw["channels"]),
        )


@dataclass(frozen=True)
class FitFacts:
    """One network kinetics fit, summarised for assessment (its numbers are not part of the decision).

    ``chebyshev`` and ``plog`` summarise integrity (shape, finiteness, anchors); the coefficient values
    themselves stay in the database, and a selection never evaluates one.
    """

    fit_ref: str
    id_rank: int
    channel_key: str
    model_kind: str
    tmin_k: float | None
    tmax_k: float | None
    pmin_bar: float | None
    pmax_bar: float | None
    rate_units: str | None
    pressure_units: str | None
    temperature_units: str | None
    stores_log10_k: bool | None
    determination_ref: str | None
    representation_role: str | None
    representation: dict[str, Any] | None
    representation_state: str
    chebyshev: dict[str, Any] | None = None
    plog_pressures_bar: tuple[float, ...] = ()
    plog_units: tuple[str | None, ...] = ()
    plog_finite: bool = True
    point_cells: tuple[tuple[float, float], ...] = ()
    point_values_finite: bool = True

    def to_dict(self) -> dict[str, Any]:
        d = dict(self.__dict__)
        d["plog_pressures_bar"] = list(self.plog_pressures_bar)
        d["plog_units"] = list(self.plog_units)
        d["point_cells"] = [list(c) for c in self.point_cells]
        return d

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> FitFacts:
        return cls(
            **{
                **raw,
                "plog_pressures_bar": tuple(raw["plog_pressures_bar"]),
                "plog_units": tuple(raw["plog_units"]),
                "point_cells": tuple((c[0], c[1]) for c in raw["point_cells"]),
            }
        )


@dataclass(frozen=True)
class DeterminationFacts:
    determination_ref: str
    id_rank: int
    determination_key: str
    channel_key: str
    observable_state: str  # absent | valid | unreadable
    observable: dict[str, Any] | None

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> DeterminationFacts:
        return cls(**raw)


def _tuple_pairs(rows: list[list[Any]]) -> tuple[tuple[Any, ...], ...]:
    return tuple(tuple(r) for r in rows)


@dataclass(frozen=True)
class SolveFacts:
    """One visible, review-admitted solve with its declarations, determinations and fits.

    ``target_state`` / ``protocol_state`` / ``validation_state`` are ``absent`` (nothing stated),
    ``valid`` or ``unreadable`` (stored but not a version this server reads): the assessor reads the
    first as unresolved and the last as unresolved with its own reason, never as a default.
    """

    solve_ref: str
    review_status: RecordReviewStatus
    created_at: datetime
    id_rank: int
    kind: str
    tmin_k: float | None
    tmax_k: float | None
    pmin_bar: float | None
    pmax_bar: float | None
    bath: tuple[tuple[str, float], ...]
    target_state: str
    target: dict[str, Any] | None
    protocol_state: str
    protocol: dict[str, Any] | None
    validation_state: str
    validation: dict[str, Any] | None
    determinations: tuple[DeterminationFacts, ...] = ()
    fits: tuple[FitFacts, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "solve_ref": self.solve_ref,
            "review_status": self.review_status.value,
            "created_at": self.created_at.isoformat(),
            "id_rank": self.id_rank,
            "kind": self.kind,
            "tmin_k": self.tmin_k,
            "tmax_k": self.tmax_k,
            "pmin_bar": self.pmin_bar,
            "pmax_bar": self.pmax_bar,
            "bath": [list(b) for b in self.bath],
            "target_state": self.target_state,
            "target": self.target,
            "protocol_state": self.protocol_state,
            "protocol": self.protocol,
            "validation_state": self.validation_state,
            "validation": self.validation,
            "determinations": [d.to_dict() for d in self.determinations],
            "fits": [f.to_dict() for f in self.fits],
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> SolveFacts:
        return cls(
            **{
                **raw,
                "review_status": RecordReviewStatus(raw["review_status"]),
                "created_at": datetime.fromisoformat(raw["created_at"]),
                "bath": _tuple_pairs(raw["bath"]),
                "determinations": tuple(DeterminationFacts.from_dict(d) for d in raw["determinations"]),
                "fits": tuple(FitFacts.from_dict(f) for f in raw["fits"]),
            }
        )


# ---------------------------------------------------------------------------
# Assessment
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Reason:
    """One finding that moved a candidate away from ``applicable``."""

    code: str
    applicability: Applicability

    def to_dict(self) -> dict[str, str]:
        return {"code": self.code, "applicability": self.applicability.value}


@dataclass(frozen=True)
class DeterminationAssessment:
    """The verdict for one determination of one channel, with every finding that fed it.

    :param eligible_fit_refs: The fits of the determination that are complete representations and pass every
        fit-level check; empty unless ``physically_eligible``. Alternate fits are one candidate, never
        independent confirmation.
    :param blocking: Demonstrated failures that exclude an otherwise applicable determination.
    :param advisory: Findings disclosed without excluding it (an undeclared protocol, for one).
    :param evidence: The state of each cited kind of evidence: ``declared`` (stated, never verified here),
        ``unavailable`` (not stated). ``verified`` and ``contradicted`` need a verification this release
        does not make.
    """

    determination_ref: str
    solve_ref: str
    channel_key: str
    applicability: Applicability
    reasons: tuple[Reason, ...]
    eligible_fit_refs: tuple[str, ...] = ()
    blocking: tuple[str, ...] = ()
    advisory: tuple[str, ...] = ()
    evidence: dict[str, str] = field(default_factory=dict)

    @property
    def physically_eligible(self) -> bool:
        return self.applicability is Applicability.applicable and not self.blocking and bool(self.eligible_fit_refs)

    def to_dict(self) -> dict[str, Any]:
        return {
            "determination_ref": self.determination_ref,
            "solve_ref": self.solve_ref,
            "channel_key": self.channel_key,
            "applicability": self.applicability.value,
            "reasons": [r.to_dict() for r in self.reasons],
            "eligible_fit_refs": list(self.eligible_fit_refs),
            "blocking": list(self.blocking),
            "advisory": list(self.advisory),
            "evidence": dict(sorted(self.evidence.items())),
            "physically_eligible": self.physically_eligible,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> DeterminationAssessment:
        return cls(
            determination_ref=raw["determination_ref"],
            solve_ref=raw["solve_ref"],
            channel_key=raw["channel_key"],
            applicability=Applicability(raw["applicability"]),
            reasons=tuple(Reason(r["code"], Applicability(r["applicability"])) for r in raw["reasons"]),
            eligible_fit_refs=tuple(raw["eligible_fit_refs"]),
            blocking=tuple(raw["blocking"]),
            advisory=tuple(raw["advisory"]),
            evidence=dict(raw["evidence"]),
        )


@dataclass(frozen=True)
class OutputCoverage:
    """How one required output is answered inside a bundle node."""

    channel_key: str
    status: str  # determination | declared_zero | missing | not_in_catalog | unavailable
    determination_ref: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> OutputCoverage:
        return cls(**raw)


@dataclass(frozen=True)
class BundleAssessment:
    """The verdict for one declared complete product set of one solve against a bundle request.

    ``node_ref`` is ``<solve ref>/<product_set_key>``: a bundle node is a declared solve and product set, never
    a combination manufactured from available fits. Members are named by determination ref; each member's
    own assessment is in ``DeterminationAssessment`` form under the same ref.
    """

    node_ref: str
    solve_ref: str
    product_set_key: str
    content_hash: str
    id_rank: int
    applicability: Applicability
    reasons: tuple[Reason, ...]
    member_refs: tuple[str, ...] = ()
    coverage: tuple[OutputCoverage, ...] = ()

    @property
    def physically_eligible(self) -> bool:
        return self.applicability is Applicability.applicable

    def to_dict(self) -> dict[str, Any]:
        return {
            "node_ref": self.node_ref,
            "solve_ref": self.solve_ref,
            "product_set_key": self.product_set_key,
            "content_hash": self.content_hash,
            "id_rank": self.id_rank,
            "applicability": self.applicability.value,
            "reasons": [r.to_dict() for r in self.reasons],
            "member_refs": list(self.member_refs),
            "coverage": [c.to_dict() for c in self.coverage],
            "physically_eligible": self.physically_eligible,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> BundleAssessment:
        return cls(
            node_ref=raw["node_ref"],
            solve_ref=raw["solve_ref"],
            product_set_key=raw["product_set_key"],
            content_hash=raw["content_hash"],
            id_rank=raw["id_rank"],
            applicability=Applicability(raw["applicability"]),
            reasons=tuple(Reason(r["code"], Applicability(r["applicability"])) for r in raw["reasons"]),
            member_refs=tuple(raw["member_refs"]),
            coverage=tuple(OutputCoverage.from_dict(c) for c in raw["coverage"]),
        )


@dataclass(frozen=True)
class NetworkAssessmentResult:
    """The assessment service's result: normalised request, population counts, facts and verdicts.

    ``unresolved_refs`` and ``unsupported_refs`` are disclosures: those determinations or nodes do not
    compete here and might if a missing fact were recorded or their form evaluated. ``ungrouped_fit_refs``
    are fits of the requested channels that state no determination: they are browsable and evaluable but
    belong to no determination, so selection cannot say how they relate to the others.
    """

    request: NetworkRequest
    network: NetworkFacts
    effective_statuses: tuple[RecordReviewStatus, ...]
    counts: dict[str, int]
    excluded_by_review: tuple[dict[str, str], ...]
    excluded_count: int
    snapshot_isolation: str
    solves: tuple[SolveFacts, ...]
    determination_assessments: tuple[DeterminationAssessment, ...]
    bundle_assessments: tuple[BundleAssessment, ...] = ()
    ungrouped_fit_refs: tuple[str, ...] = ()
    unresolved_refs: tuple[str, ...] = ()
    unsupported_refs: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()
