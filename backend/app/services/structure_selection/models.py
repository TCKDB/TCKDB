"""Plain values for structure selection: the normalised request, one unit's normalised facts, its assessment.

Everything here is data. The assessor (and, later, a decision engine and a replay) may look at nothing but these
values: public refs, review state, timestamps, stored columns and declared claims, never a database id or an ORM
row. That is what lets a decision manifest be replayed without a database. ``id_rank`` is a unit's position when the
population is ordered by database id: it stands in for the id as the last tie-break and is not an id.

What a record does not say is *unknown* here, not a default: a null stays ``None`` all the way to the assessor,
which turns it into ``unresolved``.

Versions. ``ASSESSMENT_VERSION`` is the semantics of eligibility (what blocks, what is unresolved, what is
unsupported) and ``NORMALIZER_VERSION`` the semantics of recipe normalisation (which facts establish a comparable
cohort). A change to either is a new version, and a decision manifest records the versions it was made under.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

from app.db.models.common import CalculationQuality, RecordReviewStatus
from app.services.selection_kernel import Applicability

#: Version of the eligibility semantics.
ASSESSMENT_VERSION = "1"
#: Version of the recipe normaliser (which facts a cohort needs established).
NORMALIZER_VERSION = "1"
#: The finding semantics this release reads. A finding of a newer version is carried and ignored by the assessor.
SUPPORTED_FINDING_VERSIONS = frozenset({1})


@dataclass(frozen=True)
class SelectionBounds:
    """The versioned engineering limits of a structure selection.

    These are resource limits, not chemical confidence thresholds. Every count is taken over the *complete
    authorized population*, so exceeding a bound refuses the decision outright: there is never a selection from a
    prefix. Raising a limit is a documented revision of ``version``, not a scientific rule change.

    :param candidates: Top-level candidate units at the requested grain.
    :param nested_rows: Necessary nested rows (result, geometry-link, constraint, source and finding rows).
    :param dependency_depth: How many typed-dependency hops a lineage traversal follows.
    :param manifest_bytes: Canonical normalised manifest payload.
    """

    version: str = "1"
    candidates: int = 500
    nested_rows: int = 5_000
    dependency_depth: int = 8
    manifest_bytes: int = 10 * 1024 * 1024

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "candidates": self.candidates,
            "nested_rows": self.nested_rows,
            "dependency_depth": self.dependency_depth,
            "manifest_bytes": self.manifest_bytes,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> SelectionBounds:
        return cls(**raw)


class Grain(str, Enum):
    """The unit a selection answers at: a calculation record, a conformer basin or a saddle determination."""

    calculation = "calculation"
    conformer = "conformer"
    transition_state = "transition_state"


class Intent(str, Enum):
    """What the caller asks for. Restricted by grain; evidence-only qualification asks for no number."""

    recorded_minimum = "recorded_minimum"
    validated_minimum = "validated_minimum"
    validated_saddle = "validated_saddle"
    qualify_evidence = "qualify_evidence"
    protocol_preferred = "protocol_preferred"


class Quantity(str, Enum):
    """The energy a request compares. Anything else is a recognised deferred quantity or an invalid request."""

    electronic_energy = "electronic_energy"
    zero_kelvin_energy = "zero_kelvin_energy"


#: Quantities the project knows it will want and does not evaluate yet. A request for one is refused with a
#: structured ``structure_selection_unsupported``; it never falls back to an energy that was not asked for.
DEFERRED_QUANTITIES = frozenset(
    {"enthalpy", "enthalpy_298", "gibbs_energy", "entropy", "heat_capacity", "barrier", "rate", "free_energy"}
)


class CoverageRequirement(str, Enum):
    """``known_values``: an ordering of what is known, conditional on it. ``all_requested_members``: a complete claim."""

    known_values = "known_values"
    all_requested_members = "all_requested_members"


class ValidationClaim(str, Enum):
    """The structural claim a validated intent asks the evidence to support."""

    local_minimum = "local_minimum"
    first_order_saddle = "first_order_saddle"
    higher_order_saddle = "higher_order_saddle"
    reactive_connectivity = "reactive_connectivity"


class ResultMode(str, Enum):
    all = "all"
    first = "first"


class AdminPolicy(str, Enum):
    """Named administrative order, applied only within a scientific front or among exact ties."""

    default = "default"
    latest = "latest"
    earliest = "earliest"


class Objective(str, Enum):
    """What a protocol preference optimises. ``model_fidelity`` is relative to a *pinned* reference model."""

    physical_accuracy = "physical_accuracy"
    expected_accuracy = "expected_accuracy"
    model_fidelity = "model_fidelity"


class StructureOutcome(str, Enum):
    """What a decision found. A negative scientific outcome is a valid answer, not an error."""

    no_candidates = "no_candidates"
    energy_unavailable = "energy_unavailable"
    unresolved_comparability = "unresolved_comparability"
    no_applicable_candidate = "no_applicable_candidate"
    recorded_minimum = "recorded_minimum"
    #: The lowest value among each target's administrative representative; never the minimum of all stored values.
    representative_minimum = "representative_minimum"
    qualified_evidence = "qualified_evidence"
    validated_corpus_minimum = "validated_corpus_minimum"
    policy_preferred = "policy_preferred"
    sole_eligible_candidate = "sole_eligible_candidate"
    incomparable_alternatives = "incomparable_alternatives"
    policy_conflict = "policy_conflict"
    evidence_conflict = "evidence_conflict"


class RepeatPolicy(str, Enum):
    """How several determinations of one target (one basin, geometry or saddle) are treated.

    ``retain_alternates``: every one stays; a target whose repeats disagree exactly has no single value, so the
    cohort it sits in cannot be ordered. ``administrative_representative``: the first repeat in the administrative
    order stands for its target, and the decision says so. That representative is a labelled administrative
    choice: it is never advertised as the minimum of all stored values.
    """

    retain_alternates = "retain_alternates"
    administrative_representative = "administrative_representative"


#: Which intents each grain accepts.
INTENTS_BY_GRAIN: dict[Grain, frozenset[Intent]] = {
    Grain.calculation: frozenset({Intent.recorded_minimum, Intent.protocol_preferred}),
    Grain.conformer: frozenset({Intent.validated_minimum, Intent.qualify_evidence, Intent.protocol_preferred}),
    Grain.transition_state: frozenset({Intent.validated_saddle, Intent.qualify_evidence, Intent.protocol_preferred}),
}

#: The claim each structure-consuming intent implies at each grain when the caller names none (plan section 6: for
#: transition-state use, the conventional first-order characterization).
DEFAULT_CLAIM: dict[tuple[Intent, Grain], ValidationClaim] = {
    (Intent.validated_minimum, Grain.conformer): ValidationClaim.local_minimum,
    (Intent.validated_saddle, Grain.transition_state): ValidationClaim.first_order_saddle,
    # Every intent that consumes a structure states the conventional characterization when the caller names none: a
    # preferred protocol is preferred *for a minimum* (or *for a first-order saddle*), never for any stationary point
    # that happens to carry an energy.
    (Intent.protocol_preferred, Grain.conformer): ValidationClaim.local_minimum,
    (Intent.protocol_preferred, Grain.transition_state): ValidationClaim.first_order_saddle,
}

#: The claims each intent can state (a recorded minimum states none; protocol preference may name any the grain has).
INTENT_CLAIMS: dict[Intent, frozenset[ValidationClaim]] = {
    Intent.recorded_minimum: frozenset(),
    Intent.validated_minimum: frozenset({ValidationClaim.local_minimum}),
    Intent.validated_saddle: frozenset({ValidationClaim.first_order_saddle, ValidationClaim.higher_order_saddle}),
    Intent.qualify_evidence: frozenset(ValidationClaim),
    Intent.protocol_preferred: frozenset(ValidationClaim),
}

#: The claims each grain can be asked to support.
CLAIMS_BY_GRAIN: dict[Grain, frozenset[ValidationClaim]] = {
    Grain.calculation: frozenset(),
    Grain.conformer: frozenset({ValidationClaim.local_minimum}),
    Grain.transition_state: frozenset(
        {
            ValidationClaim.first_order_saddle,
            ValidationClaim.higher_order_saddle,
            ValidationClaim.reactive_connectivity,
        }
    ),
}

#: Calculation quality a request permits when it names none: everything but ``rejected``.
DEFAULT_PERMITTED_QUALITY = frozenset({CalculationQuality.raw, CalculationQuality.curated})


@dataclass(frozen=True)
class StructureRequest:
    """The normalised request.

    :param grain: What a unit is (a calculation, a basin determination, a saddle determination).
    :param intent: What is asked; restricted by grain.
    :param quantity: The energy compared; ``None`` only for evidence-only qualification.
    :param coverage: ``known_values`` or ``all_requested_members``.
    :param validation_claim: The structural claim a validated intent needs supported (defaults by intent).
    :param min_review_status: Optional caller floor; the read profile's floor is applied on top, at every grain.
    :param permitted_quality: Calculation qualities that qualify. ``None``: everything except ``rejected``.
    :param geometry_ref: A fixed-geometry target: only units evaluated at this geometry compete.
    :param member_refs: An explicit population (calculation refs, or determination refs); ``None``: the complete
        authorized corpus matching the target.
    :param recipe: A requested actual recipe (stored form of an ``ActualProtocolDeclaration``). The units' own
        facts must establish it; a request never manufactures a missing fact.
    :param require_stable_reference: Require SCF stability evidence on the energy source.
    :param require_connectivity: Also require reactive-connectivity evidence (transition-state grain).
    :param admin_policy: Administrative order within a front. Never reorders fronts.
    :param result_mode: ``all``, or ``first`` (a labelled administrative presentation, never past a conflict).
    :param apply_rules: Whether the rule registry takes part (the decision stage; ignored by assessment).
    :param objective: What a protocol preference optimises (``protocol_preferred`` only, and then required).
    :param reference_model: The pinned reference model a ``model_fidelity`` objective is measured against.
    :param repeat_policy: How repeated determinations of one target are treated.
    :param bounds: The engineering limits; the endpoint contract is the default.
    """

    grain: Grain
    intent: Intent
    quantity: Quantity | None = Quantity.electronic_energy
    coverage: CoverageRequirement = CoverageRequirement.known_values
    validation_claim: ValidationClaim | None = None
    min_review_status: RecordReviewStatus | None = None
    permitted_quality: frozenset[CalculationQuality] | None = None
    geometry_ref: str | None = None
    member_refs: tuple[str, ...] | None = None
    recipe: dict[str, Any] | None = None
    require_stable_reference: bool = False
    require_connectivity: bool = False
    admin_policy: AdminPolicy = AdminPolicy.default
    result_mode: ResultMode = ResultMode.all
    apply_rules: bool = True
    objective: Objective | None = None
    reference_model: str | None = None
    repeat_policy: RepeatPolicy = RepeatPolicy.retain_alternates
    bounds: SelectionBounds = field(default_factory=SelectionBounds)

    def __post_init__(self) -> None:
        if self.intent not in INTENTS_BY_GRAIN[self.grain]:
            allowed = ", ".join(sorted(i.value for i in INTENTS_BY_GRAIN[self.grain]))
            raise ValueError(f"intent {self.intent.value!r} is not available at the {self.grain.value} grain ({allowed})")
        if self.intent is Intent.qualify_evidence:
            if self.quantity is not None:
                raise ValueError("evidence-only qualification asks for no energy: quantity must be omitted")
            if self.validation_claim is None:
                raise ValueError("evidence-only qualification states the validation_claim it qualifies")
        elif self.quantity is None:
            raise ValueError(f"intent {self.intent.value!r} compares an energy: state the quantity")
        if self.validation_claim is not None:
            allowed_claims = INTENT_CLAIMS[self.intent] & CLAIMS_BY_GRAIN[self.grain]
            if self.validation_claim not in allowed_claims:
                raise ValueError(
                    f"validation_claim {self.validation_claim.value!r} does not fit intent {self.intent.value!r} "
                    f"at the {self.grain.value} grain"
                )
        if self.require_connectivity and self.grain is not Grain.transition_state:
            raise ValueError("require_connectivity belongs to the transition_state grain")
        if self.intent is Intent.protocol_preferred:
            if self.objective is None:
                raise ValueError("protocol preference states its comparison objective")
            if self.objective is Objective.model_fidelity and not self.reference_model:
                raise ValueError("a model_fidelity objective names the pinned reference_model it is measured against")
            if self.grain is Grain.calculation and self.geometry_ref is None:
                raise ValueError("protocol preference between calculations compares at one fixed geometry: state geometry_ref")
        elif self.objective is not None or self.reference_model is not None:
            raise ValueError("objective and reference_model belong to protocol preference only")
        if self.reference_model is not None and self.objective is not Objective.model_fidelity:
            raise ValueError("reference_model belongs to a model_fidelity objective")
        if self.member_refs is not None and not self.member_refs:
            raise ValueError("member_refs names no member; omit it to ask for the complete authorized corpus")
        if self.member_refs is not None and len(set(self.member_refs)) != len(self.member_refs):
            raise ValueError("member_refs names a member twice")
        # Normalise once, so what a request says and what a manifest records are the same value.
        object.__setattr__(self, "permitted_quality", self.quality_set)
        object.__setattr__(self, "validation_claim", self.effective_claim)

    @property
    def effective_claim(self) -> ValidationClaim | None:
        """The claim the units must support: the stated one, else the intent's default."""
        return self.validation_claim if self.validation_claim is not None else DEFAULT_CLAIM.get((self.intent, self.grain))

    @property
    def quality_set(self) -> frozenset[CalculationQuality]:
        return self.permitted_quality if self.permitted_quality is not None else DEFAULT_PERMITTED_QUALITY

    def to_dict(self) -> dict[str, Any]:
        return {
            "grain": self.grain.value,
            "intent": self.intent.value,
            "quantity": self.quantity.value if self.quantity is not None else None,
            "coverage": self.coverage.value,
            "validation_claim": self.effective_claim.value if self.effective_claim is not None else None,
            "min_review_status": self.min_review_status.value if self.min_review_status else None,
            "permitted_quality": sorted(q.value for q in self.quality_set),
            "geometry_ref": self.geometry_ref,
            "member_refs": list(self.member_refs) if self.member_refs is not None else None,
            "recipe": self.recipe,
            "require_stable_reference": self.require_stable_reference,
            "require_connectivity": self.require_connectivity,
            "administrative_policy": self.admin_policy.value,
            "result_mode": self.result_mode.value,
            "apply_rules": self.apply_rules,
            "objective": self.objective.value if self.objective is not None else None,
            "reference_model": self.reference_model,
            "repeat_policy": self.repeat_policy.value,
            "bounds": self.bounds.to_dict(),
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> StructureRequest:
        """Rebuild a request from its manifest form (the inverse of :meth:`to_dict`)."""
        return cls(
            grain=Grain(raw["grain"]),
            intent=Intent(raw["intent"]),
            quantity=Quantity(raw["quantity"]) if raw["quantity"] is not None else None,
            coverage=CoverageRequirement(raw["coverage"]),
            validation_claim=ValidationClaim(raw["validation_claim"]) if raw["validation_claim"] else None,
            min_review_status=RecordReviewStatus(raw["min_review_status"]) if raw["min_review_status"] else None,
            permitted_quality=frozenset(CalculationQuality(q) for q in raw["permitted_quality"]),
            geometry_ref=raw["geometry_ref"],
            member_refs=tuple(raw["member_refs"]) if raw["member_refs"] is not None else None,
            recipe=raw["recipe"],
            require_stable_reference=raw["require_stable_reference"],
            require_connectivity=raw["require_connectivity"],
            admin_policy=AdminPolicy(raw["administrative_policy"]),
            result_mode=ResultMode(raw["result_mode"]),
            apply_rules=raw["apply_rules"],
            objective=Objective(raw["objective"]) if raw["objective"] is not None else None,
            reference_model=raw["reference_model"],
            repeat_policy=RepeatPolicy(raw["repeat_policy"]),
            bounds=SelectionBounds.from_dict(raw["bounds"]),
        )


# ---------------------------------------------------------------------------
# Normalised facts
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LevelFacts:
    """A calculation's level of theory as stored (canonical, merges followed). Nothing is defaulted."""

    level_ref: str | None
    method: str | None
    basis: str | None
    aux_basis: str | None
    dispersion: str | None
    solvent: str | None
    solvent_model: str | None
    spin_treatment: str | None
    core_treatment: str | None
    composite_scheme_ref: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> LevelFacts:
        return cls(**raw)


@dataclass(frozen=True)
class EnergyFacts:
    """The energies a calculation's own result rows state. ``None`` is not stated, never zero."""

    sp_electronic_hartree: float | None = None
    sp_uncertainty_hartree: float | None = None
    opt_final_hartree: float | None = None
    opt_converged: bool | None = None
    composite_assembly: str | None = None
    composite_electronic_hartree: float | None = None
    composite_e0_hartree: float | None = None
    composite_recipe_zpe_hartree: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> EnergyFacts:
        return cls(**raw)


@dataclass(frozen=True)
class CurvatureFacts:
    """What a calculation's own frequency or Hessian result states about curvature at a geometry.

    ``structural_flag`` is the owner's persisted judgement (ADR 0012): ``True`` an imaginary mode at or above tau
    beyond the reaction coordinate, ``False`` judged and clear, ``None`` not judged. ``imaginary_modes`` are the
    stored imaginary modes as ``(mode_index, frequency_cm1, disposition)`` (disposition ``None`` when undeclared);
    together with ``imag_freq_cm1``, the reaction coordinate designation and the stored ``tau_cm1``/``tau_basis`` they
    are exactly what the shared stationary-point owner judges, so the assessment re-evaluates through that owner
    rather than counting.
    """

    has_freq_result: bool = False
    n_imag: int | None = None
    reaction_coordinate_mode_index: int | None = None
    structural_flag: bool | None = None
    tau_cm1: float | None = None
    tau_basis: str | None = None
    has_hessian: bool = False
    hessian_geometry_ref: str | None = None
    imag_freq_cm1: float | None = None
    imaginary_modes: tuple[tuple[int, float, str | None], ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {**self.__dict__, "imaginary_modes": [list(m) for m in self.imaginary_modes]}

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> CurvatureFacts:
        modes = tuple((int(m[0]), float(m[1]), m[2]) for m in raw.get("imaginary_modes", ()))
        return cls(**{**raw, "imaginary_modes": modes})


@dataclass(frozen=True)
class FindingFacts:
    """One appended finding, with whether a later finding supersedes it (``superseded`` is computed by the loader
    over the complete authorized finding set and is re-derived by replay)."""

    finding_ref: str
    kind: str
    scope: str
    subject_ref: str
    role: str | None
    verdict: str
    authority: str
    semantic_version: int
    supersedes_ref: str | None

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> FindingFacts:
        return cls(**raw)


@dataclass(frozen=True)
class LineageEdge:
    """One typed dependency edge a lineage traversal followed: ``child`` depends on ``parent`` by ``role``."""

    child_ref: str
    parent_ref: str
    role: str

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> LineageEdge:
        return cls(**raw)


@dataclass(frozen=True)
class NormalizedCalculation:
    """What the assessor knows about one calculation (an energy-bearing candidate, or a determination's source).

    :param declaration_state: ``absent`` (nothing declared), ``valid`` or ``unreadable`` (stored but not a
        version-1 declaration this server can read).
    :param constraint_rows: How many constraint rows the calculation states. Rows establish "constrained"; their
        absence is not "unconstrained".
    :param owner_ref: Public ref of the species entry or transition state entry that owns the calculation.
    :param scf_stability: ``stable`` / ``unstable`` / ``inconclusive``, or ``None`` (never checked).
    :param geometry_validation: The automated geometry check's status, or ``None``. Heuristic evidence only.
    """

    calculation_ref: str
    type: str
    quality: str
    review_status: RecordReviewStatus
    created_at: datetime
    id_rank: int
    owner_ref: str
    level: LevelFacts
    software: str | None
    declaration_state: str
    declaration: dict[str, Any] | None
    constraint_rows: int
    energy: EnergyFacts
    curvature: CurvatureFacts
    input_geometry_refs: tuple[str, ...]
    output_geometry_refs: tuple[str, ...]
    scf_stability: str | None = None
    geometry_validation: str | None = None
    conformer_observation_ref: str | None = None
    #: Typed parents followed from this calculation (one edge per stored dependency, depth-limited).
    lineage: tuple[LineageEdge, ...] = ()
    #: True when the traversal stopped at the depth bound with parents unexplored.
    lineage_truncated: bool = False
    #: True when the walk met a calculation it had already passed through (a dependency cycle).
    lineage_cyclic: bool = False
    findings: tuple[FindingFacts, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "calculation_ref": self.calculation_ref,
            "type": self.type,
            "quality": self.quality,
            "review_status": self.review_status.value,
            "created_at": self.created_at.isoformat(),
            "id_rank": self.id_rank,
            "owner_ref": self.owner_ref,
            "level": self.level.to_dict(),
            "software": self.software,
            "declaration_state": self.declaration_state,
            "declaration": self.declaration,
            "constraint_rows": self.constraint_rows,
            "energy": self.energy.to_dict(),
            "curvature": self.curvature.to_dict(),
            "input_geometry_refs": list(self.input_geometry_refs),
            "output_geometry_refs": list(self.output_geometry_refs),
            "scf_stability": self.scf_stability,
            "geometry_validation": self.geometry_validation,
            "conformer_observation_ref": self.conformer_observation_ref,
            "lineage": [e.to_dict() for e in self.lineage],
            "lineage_truncated": self.lineage_truncated,
            "lineage_cyclic": self.lineage_cyclic,
            "findings": [f.to_dict() for f in self.findings],
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> NormalizedCalculation:
        return cls(
            **{
                **raw,
                "review_status": RecordReviewStatus(raw["review_status"]),
                "created_at": datetime.fromisoformat(raw["created_at"]),
                "level": LevelFacts.from_dict(raw["level"]),
                "energy": EnergyFacts.from_dict(raw["energy"]),
                "curvature": CurvatureFacts.from_dict(raw["curvature"]),
                "input_geometry_refs": tuple(raw["input_geometry_refs"]),
                "output_geometry_refs": tuple(raw["output_geometry_refs"]),
                "lineage": tuple(LineageEdge.from_dict(e) for e in raw["lineage"]),
                "findings": tuple(FindingFacts.from_dict(f) for f in raw["findings"]),
            }
        )


@dataclass(frozen=True)
class NormalizedSource:
    """One calculation pinned to one role of a determination.

    ``calculation`` is ``None`` when the pinned calculation is not available to this request (hidden by the read
    profile, rejected or deprecated, or below the caller's floor): the role then reads as unavailable, with no
    ref and no reason that would name a record the caller cannot see.
    """

    role: str
    geometry_ref: str | None
    calculation_ref: str | None
    unavailable_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> NormalizedSource:
        return cls(**raw)


@dataclass(frozen=True)
class NormalizedDetermination:
    """What the assessor knows about one structure determination (a basin or saddle claim)."""

    determination_ref: str
    target_kind: str
    quantity: str | None
    energy_convention: dict[str, Any] | None
    actual_recipe: dict[str, Any] | None
    key: str
    owner_ref: str
    owner_kind: str
    conformer_observation_ref: str | None
    conformer_group_ref: str | None
    evaluated_geometry_ref: str
    review_status: RecordReviewStatus
    created_at: datetime
    id_rank: int
    entry_kind: str | None
    sources: tuple[NormalizedSource, ...]
    findings: tuple[FindingFacts, ...] = ()
    #: Validation evidence rows of the owning transition state entry: ``(kind, passed, calculation_ref,
    #: geometry_ref)``, for the connectivity claim.
    validation_evidence: tuple[dict[str, Any], ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "determination_ref": self.determination_ref,
            "target_kind": self.target_kind,
            "quantity": self.quantity,
            "energy_convention": self.energy_convention,
            "actual_recipe": self.actual_recipe,
            "key": self.key,
            "owner_ref": self.owner_ref,
            "owner_kind": self.owner_kind,
            "conformer_observation_ref": self.conformer_observation_ref,
            "conformer_group_ref": self.conformer_group_ref,
            "evaluated_geometry_ref": self.evaluated_geometry_ref,
            "review_status": self.review_status.value,
            "created_at": self.created_at.isoformat(),
            "id_rank": self.id_rank,
            "entry_kind": self.entry_kind,
            "sources": [s.to_dict() for s in self.sources],
            "findings": [f.to_dict() for f in self.findings],
            "validation_evidence": [dict(v) for v in self.validation_evidence],
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> NormalizedDetermination:
        return cls(
            **{
                **raw,
                "review_status": RecordReviewStatus(raw["review_status"]),
                "created_at": datetime.fromisoformat(raw["created_at"]),
                "sources": tuple(NormalizedSource.from_dict(s) for s in raw["sources"]),
                "findings": tuple(FindingFacts.from_dict(f) for f in raw["findings"]),
                "validation_evidence": tuple(dict(v) for v in raw["validation_evidence"]),
            }
        )


@dataclass(frozen=True)
class StructureSubject:
    """The entry every unit belongs to: a species entry or a transition state entry, with its identity facts."""

    kind: str
    entry_ref: str
    stationary_point_kind: str | None
    electronic_state_kind: str | None
    isotope_key: str | None
    charge: int | None
    multiplicity: int | None

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> StructureSubject:
        return cls(**raw)


# ---------------------------------------------------------------------------
# Assessment
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Reason:
    """One finding that moved a unit away from ``applicable``."""

    code: str
    applicability: Applicability

    def to_dict(self) -> dict[str, str]:
        return {"code": self.code, "applicability": self.applicability.value}


class EnergyScope(str, Enum):
    """What a supplied number is a number *of*."""

    recorded_single_point = "recorded_single_point"
    optimized_endpoint = "optimized_endpoint"
    unconverged_endpoint = "unconverged_endpoint"
    composite_electronic = "composite_electronic"
    composite_zero_kelvin = "composite_zero_kelvin"


@dataclass(frozen=True)
class EnergyValue:
    """One unit's value for the requested quantity, with where it came from and what it is a value of."""

    quantity: str
    hartree: float
    scope: EnergyScope
    calculation_ref: str
    geometry_ref: str | None
    uncertainty_hartree: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "quantity": self.quantity,
            "hartree": self.hartree,
            "scope": self.scope.value,
            "calculation_ref": self.calculation_ref,
            "geometry_ref": self.geometry_ref,
            "uncertainty_hartree": self.uncertainty_hartree,
        }


@dataclass(frozen=True)
class RecipeFact:
    """One normalised fact of a recipe: ``known`` (with a value), ``unknown`` or ``not_applicable``, and whose it is."""

    name: str
    state: str
    value: str | None
    source: str

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


@dataclass(frozen=True)
class NormalizedRecipe:
    """A unit's actual recipe, normalised: the facts, the contradictions, the cohort it can be compared in."""

    facts: tuple[RecipeFact, ...]
    conflicts: tuple[str, ...]
    unestablished: tuple[str, ...]
    cohort_key: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "facts": [f.to_dict() for f in self.facts],
            "conflicts": list(self.conflicts),
            "unestablished": list(self.unestablished),
            "cohort_key": self.cohort_key,
        }


@dataclass(frozen=True)
class ClaimSupport:
    """What a validated or qualifying unit's evidence supports, stated on its own surface.

    ``surface_level_refs`` are the levels of theory the supporting curvature evidence was computed at; when they
    differ from the energy's level, the claim is "a minimum (or saddle) on that surface", not on the energy's.
    """

    claim: str
    supported: bool
    witness_refs: tuple[str, ...] = ()
    surface_level_refs: tuple[str, ...] = ()
    treatment: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "claim": self.claim,
            "supported": self.supported,
            "witness_refs": list(self.witness_refs),
            "surface_level_refs": list(self.surface_level_refs),
            "treatment": self.treatment,
        }


@dataclass(frozen=True)
class StructureAssessment:
    """The verdict for one unit, with every finding that fed it.

    :param blocking: Known failures that exclude an otherwise applicable unit from the requested claim.
    :param advisory: Findings disclosed without excluding the unit.
    :param comparative_unknown: Comparative metadata the unit lacks; it creates no preference edge and excludes
        nothing.
    """

    unit_ref: str
    applicability: Applicability
    reasons: tuple[Reason, ...]
    blocking: tuple[str, ...] = ()
    advisory: tuple[str, ...] = ()
    comparative_unknown: tuple[str, ...] = ()
    energy: EnergyValue | None = None
    recipe: NormalizedRecipe | None = None
    claim: ClaimSupport | None = None

    @property
    def physically_eligible(self) -> bool:
        return self.applicability is Applicability.applicable and not self.blocking

    def to_dict(self) -> dict[str, Any]:
        return {
            "unit_ref": self.unit_ref,
            "applicability": self.applicability.value,
            "reasons": [r.to_dict() for r in self.reasons],
            "blocking": list(self.blocking),
            "advisory": list(self.advisory),
            "comparative_unknown": list(self.comparative_unknown),
            "energy": self.energy.to_dict() if self.energy is not None else None,
            "recipe": self.recipe.to_dict() if self.recipe is not None else None,
            "claim": self.claim.to_dict() if self.claim is not None else None,
            "physically_eligible": self.physically_eligible,
        }


@dataclass(frozen=True)
class StructureAssessmentResult:
    """The assessment service's result: the normalised request, population counts, units, assessments.

    ``unresolved_refs`` and ``unsupported_refs`` are disclosures: those units do not compete here and might if a
    missing fact were recorded or their form evaluated. ``excluded_by_review`` names only records the caller's
    own floor, quality filter or a terminal status excluded; a record the read profile hides is dropped without
    trace.
    """

    request: StructureRequest
    subject: StructureSubject
    effective_statuses: tuple[RecordReviewStatus, ...]
    total_units: int
    visible_units: int
    excluded_by_review: tuple[dict[str, str], ...]
    excluded_count: int
    nested_rows: int
    snapshot_isolation: str
    calculations: tuple[NormalizedCalculation, ...]
    determinations: tuple[NormalizedDetermination, ...]
    assessments: tuple[StructureAssessment, ...]
    unresolved_refs: tuple[str, ...] = ()
    unsupported_refs: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()


def finite(value: float | None) -> bool:
    """A stored number that can take part in an ordering."""
    return value is not None and math.isfinite(value)
