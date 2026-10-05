"""Plain values for kinetics selection: the normalised request, one record's normalised facts,
its assessment, and a determination group.

Everything here is data. :class:`NormalizedKinetics` is the whole of what the assessor (and,
later, a preference rule) may look at: public refs, review state, timestamps, stored columns and
the declared claims, never a database id or an ORM row. That is what lets a decision manifest be
replayed without a database. ``id_rank`` is the record's position when the population is ordered
by database id: it stands in for the id as the last tie-break and is not an id.

What a declaration does not say is *unknown* here, not a default: a null stays ``None`` all the
way to the assessor, which turns it into ``unresolved``.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

from tckdb_schemas.kinetics_declarations import (
    MOLE_FRACTION_SUM_TOLERANCE,
    KineticsCoefficientBasis,
)

from app.db.models.common import (
    KineticsDeterminationTargetKind,
    KineticsDirection,
    RecordReviewStatus,
)
from app.schemas.reads.scientific_common import SelectionPolicy
from app.services.selection_kernel import Applicability, Outcome  # noqa: F401  (Outcome re-exported)

#: The one quantity this selector answers.
QUANTITY = "rate_coefficient"
#: The only phase this selector answers for.
PHASE = "gas"
#: A visible population larger than this is refused (HTTP 422), never truncated.
MAX_CANDIDATES = 500
#: Version of the DECISION PROCEDURE only: grouping into determinations, the rules, outcomes and ordering, which is
#: what ``replay_decision`` re-runs. How a record's eligibility is *assessed* (the evidence rubric version, the
#: source-finding gate) is a separate contract, ``ASSESSMENT_SEMANTICS_VERSION`` below, recorded in its own
#: structured manifest block; replay reads the recorded ``eligible`` flags and never reruns the assessment.
#: A decision manifest records both versions.
POLICY_NAME = "kinetics_method_preferred"
POLICY_VERSION = "1"
#: Version of how eligibility is assessed. 2: an automated geometry-validation fail is advisory (computed rubrics
#: at version 2) and a live confirmed finding about a source calculation excludes the record
#: (``structure_selection.source_findings``). A manifest with no block was made under ``pre_v2_assessment``.
ASSESSMENT_SEMANTICS_VERSION = "2"
SUPPORTED_ASSESSMENT_SEMANTICS = frozenset({"pre_v2_assessment", ASSESSMENT_SEMANTICS_VERSION})

#: A point pressure matches a record's own pressure within this (the existing browse tolerances).
PRESSURE_REL_TOL = 1.0e-9
PRESSURE_ABS_TOL = 1.0e-12


class PressureKind(str, Enum):
    independent = "independent"
    high_pressure_limit = "high_pressure_limit"
    finite = "finite"


@dataclass(frozen=True)
class PressureRequest:
    """Pressure as asked: independent, high-pressure limit, or a finite window in bar (a point when equal)."""

    kind: PressureKind
    min_bar: float | None = None
    max_bar: float | None = None

    def __post_init__(self) -> None:
        if self.kind is PressureKind.finite:
            for name, value in (("min_bar", self.min_bar), ("max_bar", self.max_bar)):
                if value is None or not math.isfinite(value) or value <= 0:
                    raise ValueError(f"a finite pressure needs a positive finite {name}")
            assert self.min_bar is not None and self.max_bar is not None
            if self.min_bar > self.max_bar:
                raise ValueError("pressure min_bar must not exceed max_bar")
        elif self.min_bar is not None or self.max_bar is not None:
            raise ValueError(f"a {self.kind.value} pressure carries no bounds")

    @property
    def is_point(self) -> bool:
        return self.kind is PressureKind.finite and self.min_bar == self.max_bar

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind.value, "min_bar": self.min_bar, "max_bar": self.max_bar}


@dataclass(frozen=True)
class TargetRequest:
    """The whole reaction, or one resolved channel named by a TS entry ref or a network channel locator."""

    kind: KineticsDeterminationTargetKind
    transition_state_entry_ref: str | None = None
    network_ref: str | None = None
    channel_key: str | None = None

    def __post_init__(self) -> None:
        ts, net, key = self.transition_state_entry_ref, self.network_ref, self.channel_key
        if self.kind is KineticsDeterminationTargetKind.whole_reaction:
            if ts or net or key:
                raise ValueError("a whole_reaction target carries no locator")
            return
        if bool(ts) == bool(net or key):
            raise ValueError("a resolved_channel target names a transition state entry or a network channel, not both")
        if (net is None) != (key is None):
            raise ValueError("a network channel target needs both network_ref and channel_key")

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "transition_state_entry_ref": self.transition_state_entry_ref,
            "network_ref": self.network_ref,
            "channel_key": self.channel_key,
        }


@dataclass(frozen=True)
class ColliderRequest:
    """A specified collider (``species_refs`` of one, no fractions) or an explicit mole-fraction mixture."""

    species_refs: tuple[str, ...]
    mole_fractions: tuple[float, ...] | None = None

    def __post_init__(self) -> None:
        if not self.species_refs:
            raise ValueError("a collider names at least one species")
        if len(set(self.species_refs)) != len(self.species_refs):
            raise ValueError("a collider component is listed twice")
        if self.mole_fractions is None:
            if len(self.species_refs) != 1:
                raise ValueError("several colliders need mole fractions; one collider is a specified collider")
            return
        if len(self.mole_fractions) != len(self.species_refs) or len(self.species_refs) < 2:
            raise ValueError("a mixture lists at least two colliders, each with a mole fraction")
        if any(not math.isfinite(f) or f <= 0 or f > 1 for f in self.mole_fractions):
            raise ValueError("each mole fraction is in (0, 1]")
        if abs(math.fsum(self.mole_fractions) - 1.0) > MOLE_FRACTION_SUM_TOLERANCE:
            raise ValueError("mole fractions must sum to 1; they are never renormalised")

    @property
    def is_mixture(self) -> bool:
        return self.mole_fractions is not None

    def to_dict(self) -> dict[str, Any]:
        return {
            "species_refs": list(self.species_refs),
            "mole_fractions": list(self.mole_fractions) if self.mole_fractions is not None else None,
        }


@dataclass(frozen=True)
class KineticsRequest:
    """The normalised request: a gas-phase rate coefficient for one reaction entry.

    :param direction: ``forward`` or ``reverse`` relative to the stored reaction-entry orientation.
    :param coefficient_basis: What the coefficient must still need (elementary, third-body kernel,
        or already composition-effective).
    :param collider: Required when material: a finite pressure, or a composition-effective coefficient.
    :param min_review_status: Optional caller floor; the read-profile floor is applied on top.
    :param admin_policy: Administrative order within a front. Never reorders fronts.
    :param max_candidates: Population cap; the endpoint contract is 500.
    """

    direction: KineticsDirection
    target: TargetRequest
    coefficient_basis: KineticsCoefficientBasis
    temperature_min_k: float
    temperature_max_k: float
    pressure: PressureRequest
    collider: ColliderRequest | None = None
    min_review_status: RecordReviewStatus | None = None
    admin_policy: SelectionPolicy = SelectionPolicy.default
    max_candidates: int = MAX_CANDIDATES
    apply_rules: bool = True

    def __post_init__(self) -> None:
        if self.direction not in (KineticsDirection.forward, KineticsDirection.reverse):
            raise ValueError("direction is forward or reverse")
        for name, value in (("temperature_min_k", self.temperature_min_k), ("temperature_max_k", self.temperature_max_k)):
            if not math.isfinite(value) or value <= 0:
                raise ValueError(f"{name} must be positive and finite")
        if self.temperature_min_k > self.temperature_max_k:
            raise ValueError("temperature_min_k must not exceed temperature_max_k")
        if self.max_candidates < 1:
            raise ValueError("max_candidates must be at least 1")
        if self.collider_is_material and self.collider is None:
            raise ValueError("a collider is required for a finite pressure or a composition-effective coefficient")

    @property
    def collider_is_material(self) -> bool:
        return (
            self.pressure.kind is PressureKind.finite
            or self.coefficient_basis is KineticsCoefficientBasis.composition_effective_coefficient
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "quantity": QUANTITY,
            "phase": PHASE,
            "direction": self.direction.value,
            "target": self.target.to_dict(),
            "coefficient_basis": self.coefficient_basis.value,
            "temperature_min_k": self.temperature_min_k,
            "temperature_max_k": self.temperature_max_k,
            "pressure": self.pressure.to_dict(),
            "collider": self.collider.to_dict() if self.collider is not None else None,
            "min_review_status": self.min_review_status.value if self.min_review_status else None,
            "administrative_policy": self.admin_policy.value,
            "apply_rules": self.apply_rules,
            "max_candidates": self.max_candidates,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> KineticsRequest:
        """Rebuild a request from its manifest form (the inverse of :meth:`to_dict`)."""
        if raw["quantity"] != QUANTITY or raw["phase"] != PHASE:
            raise ValueError("a manifest request is for a gas-phase rate coefficient")
        pressure = raw["pressure"]
        collider = raw["collider"]
        return cls(
            direction=KineticsDirection(raw["direction"]),
            target=TargetRequest(
                kind=KineticsDeterminationTargetKind(raw["target"]["kind"]),
                transition_state_entry_ref=raw["target"]["transition_state_entry_ref"],
                network_ref=raw["target"]["network_ref"],
                channel_key=raw["target"]["channel_key"],
            ),
            coefficient_basis=KineticsCoefficientBasis(raw["coefficient_basis"]),
            temperature_min_k=raw["temperature_min_k"],
            temperature_max_k=raw["temperature_max_k"],
            pressure=PressureRequest(PressureKind(pressure["kind"]), pressure["min_bar"], pressure["max_bar"]),
            collider=(
                ColliderRequest(
                    tuple(collider["species_refs"]),
                    tuple(collider["mole_fractions"]) if collider["mole_fractions"] is not None else None,
                )
                if collider is not None
                else None
            ),
            min_review_status=RecordReviewStatus(raw["min_review_status"]) if raw["min_review_status"] else None,
            admin_policy=SelectionPolicy(raw["administrative_policy"]),
            max_candidates=raw.get("max_candidates", MAX_CANDIDATES),
            apply_rules=raw["apply_rules"],
        )


@dataclass(frozen=True)
class DeterminationFacts:
    """The determination a record belongs to, as stored, with public refs only."""

    determination_ref: str
    direction: str
    target_kind: str
    transition_state_entry_ref: str | None
    network_ref: str | None
    channel_key: str | None

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> DeterminationFacts:
        return cls(**raw)


@dataclass(frozen=True)
class NormalizedKinetics:
    """What the assessor knows about one kinetics record (one parent; children are summarised).

    :param applicability_state: ``absent`` (no declaration), ``valid`` or ``unreadable`` (stored but
        not a version-1 declaration this server can read). Same for ``protocol_state``.
    :param plog_pressures_bar: Every stored PLOG anchor pressure (repeats kept: same-pressure terms sum).
    :param efficiencies: Third-body efficiencies by collider species public ref.
    :param network_channel_ref: ``network_ref/channel_key`` of the network channel a network-linked fit
        belongs to, and ``network_solve_ref`` its solve (generating context, disclosed).
    """

    kinetics_ref: str
    review_status: RecordReviewStatus
    created_at: datetime
    id_rank: int
    scientific_origin: str
    model_kind: str
    direction: str | None
    is_third_body: bool
    tmin_k: float | None
    tmax_k: float | None
    pressure_context: str | None
    pressure_bar: float | None
    a: float | None
    a_units: str | None
    degeneracy: float | None
    degeneracy_convention: str
    representation_role: str | None
    determination: DeterminationFacts | None
    applicability_state: str
    applicability: dict[str, Any] | None
    protocol_state: str
    protocol: dict[str, Any] | None
    arrhenius_terms: int = 0
    plog_pressures_bar: tuple[float, ...] = ()
    chebyshev: dict[str, Any] | None = None
    has_falloff: bool = False
    efficiencies: dict[str, float] = field(default_factory=dict)
    network_channel_ref: str | None = None
    network_solve_ref: str | None = None
    reactant_stoichiometries: tuple[int, ...] = ()
    #: Every term's and entry's own units, so a representation is judged on all of its content, not on a parent
    #: row: one per multi-Arrhenius term and one per PLOG entry (both ``None`` when not recorded).
    arrhenius_units: tuple[str | None, ...] = ()
    plog_units: tuple[str | None, ...] = ()
    #: The falloff block's own content (``low_a_units`` and the Troe and SRI parameters), ``None`` without one.
    falloff: dict[str, Any] | None = None
    product_stoichiometries: tuple[int, ...] = ()
    #: The reference temperature the stored expression is written against; disclosed, never used to change it.
    t0_k: float | None = None
    #: The levels of theory the record itself names, as ``{"source", "role", "method", "basis", ...}``:
    #: ``record_declared`` (the level the depositor declared for the whole record's energies,
    #: ``kinetics.energy_level_of_theory_id``; the claim a method rule reads), ``protocol_declared`` (the protocol's
    #: own supporting calculations, which can corroborate or refute the claim but have no role that says which side
    #: of the barrier they cover) and ``source_link`` (the record's own source-calculation links, which do not
    #: establish that an energy was used). Nothing is read from a sibling record.
    energy_levels: tuple[dict[str, Any], ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "kinetics_ref": self.kinetics_ref,
            "review_status": self.review_status.value,
            "created_at": self.created_at.isoformat(),
            "id_rank": self.id_rank,
            "scientific_origin": self.scientific_origin,
            "model_kind": self.model_kind,
            "direction": self.direction,
            "is_third_body": self.is_third_body,
            "tmin_k": self.tmin_k,
            "tmax_k": self.tmax_k,
            "pressure_context": self.pressure_context,
            "pressure_bar": self.pressure_bar,
            "a": self.a,
            "a_units": self.a_units,
            "degeneracy": self.degeneracy,
            "degeneracy_convention": self.degeneracy_convention,
            "representation_role": self.representation_role,
            "determination": self.determination.to_dict() if self.determination is not None else None,
            "applicability_state": self.applicability_state,
            "applicability": self.applicability,
            "protocol_state": self.protocol_state,
            "protocol": self.protocol,
            "arrhenius_terms": self.arrhenius_terms,
            "plog_pressures_bar": list(self.plog_pressures_bar),
            "chebyshev": self.chebyshev,
            "has_falloff": self.has_falloff,
            "efficiencies": dict(sorted(self.efficiencies.items())),
            "network_channel_ref": self.network_channel_ref,
            "network_solve_ref": self.network_solve_ref,
            "reactant_stoichiometries": list(self.reactant_stoichiometries),
            "arrhenius_units": list(self.arrhenius_units),
            "plog_units": list(self.plog_units),
            "falloff": dict(self.falloff) if self.falloff is not None else None,
            "product_stoichiometries": list(self.product_stoichiometries),
            "t0_k": self.t0_k,
            "energy_levels": [dict(level) for level in self.energy_levels],
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> NormalizedKinetics:
        det = raw["determination"]
        return cls(
            **{
                **raw,
                "review_status": RecordReviewStatus(raw["review_status"]),
                "created_at": datetime.fromisoformat(raw["created_at"]),
                "determination": DeterminationFacts.from_dict(det) if det is not None else None,
                "plog_pressures_bar": tuple(raw["plog_pressures_bar"]),
                "efficiencies": dict(raw["efficiencies"]),
                "reactant_stoichiometries": tuple(raw["reactant_stoichiometries"]),
                "arrhenius_units": tuple(raw["arrhenius_units"]),
                "plog_units": tuple(raw["plog_units"]),
                "falloff": dict(raw["falloff"]) if raw["falloff"] is not None else None,
                "product_stoichiometries": tuple(raw["product_stoichiometries"]),
                "energy_levels": tuple(dict(level) for level in raw["energy_levels"]),
            }
        )


@dataclass(frozen=True)
class SpeciesFact:
    """One participant of the reaction entry as a rule needs to see it (public ref, identity, state)."""

    species_entry_ref: str
    inchi_key: str
    charge: int
    multiplicity: int
    isotope_key: str | None
    electronic_state_kind: str
    entry_kind: str

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> SpeciesFact:
        return cls(**raw)


@dataclass(frozen=True)
class KineticsSubject:
    """The reaction entry every candidate belongs to: its participants in the entry's stored orientation."""

    reaction_entry_ref: str
    reactants: tuple[SpeciesFact, ...]
    products: tuple[SpeciesFact, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "reaction_entry_ref": self.reaction_entry_ref,
            "reactants": [s.to_dict() for s in self.reactants],
            "products": [s.to_dict() for s in self.products],
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> KineticsSubject:
        return cls(
            reaction_entry_ref=raw["reaction_entry_ref"],
            reactants=tuple(SpeciesFact.from_dict(s) for s in raw["reactants"]),
            products=tuple(SpeciesFact.from_dict(s) for s in raw["products"]),
        )


@dataclass(frozen=True)
class Reason:
    """One finding that moved a record away from ``applicable``."""

    code: str
    applicability: Applicability

    def to_dict(self) -> dict[str, str]:
        return {"code": self.code, "applicability": self.applicability.value}


@dataclass(frozen=True)
class KineticsAssessment:
    """The applicability verdict for one record, with every finding that fed it.

    :param blocking: Validation failures that exclude an otherwise applicable record (a hard-failed
        evidence evaluation of a computed rate). Excluding, not advisory.
    :param advisory: Findings disclosed without excluding the record.
    """

    kinetics_ref: str
    applicability: Applicability
    reasons: tuple[Reason, ...]
    blocking: tuple[str, ...] = ()
    advisory: tuple[str, ...] = ()

    @property
    def physically_eligible(self) -> bool:
        return self.applicability is Applicability.applicable and not self.blocking

    def to_dict(self) -> dict[str, Any]:
        return {
            "kinetics_ref": self.kinetics_ref,
            "applicability": self.applicability.value,
            "reasons": [r.to_dict() for r in self.reasons],
            "blocking": list(self.blocking),
            "advisory": list(self.advisory),
            "physically_eligible": self.physically_eligible,
        }


@dataclass(frozen=True)
class DeterminationGroup:
    """The eligible representations of one determination.

    The representation order is administrative (it carries no claim that one fit is more
    accurate), and the first is the group's representative. Alternate fits of one determination
    are one candidate, never independent confirmation.
    """

    determination_ref: str
    representation_refs: tuple[str, ...]
    representative_ref: str
    representative_id_rank: int
    representative_review_status: RecordReviewStatus
    representative_created_at: datetime

    def to_dict(self) -> dict[str, Any]:
        return {
            "determination_ref": self.determination_ref,
            "representation_refs": list(self.representation_refs),
            "representative_ref": self.representative_ref,
        }


@dataclass(frozen=True)
class KineticsAssessmentResult:
    """The assessment service's result: normalised request, population counts, assessments, groups.

    ``unresolved_refs`` and ``unsupported_refs`` are disclosures: those records do not compete here and
    might if a missing fact were recorded or their form evaluated. ``outcome`` is ``None`` here:
    ordering and outcomes are the rule-driven stage built on this result.
    """

    request: KineticsRequest
    subject: KineticsSubject
    effective_statuses: tuple[RecordReviewStatus, ...]
    total_rows: int
    visible_candidates: int
    excluded_by_review: tuple[dict[str, str], ...]
    excluded_count: int
    snapshot_isolation: str
    candidates: tuple[NormalizedKinetics, ...]
    assessments: tuple[KineticsAssessment, ...]
    groups: tuple[DeterminationGroup, ...]
    unresolved_refs: tuple[str, ...] = ()
    unsupported_refs: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()
