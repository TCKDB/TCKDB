"""Plain values for the relative conformer energy operation: the request, and the answer.

The question: how much higher or lower in energy is each conformer of one species entry than a conformer the
caller *names*? The compared unit is the conformer group (the basin); its observations are repeats of it, never
separate members. Three things are never done here:

* a missing value is reported as absent with a coded reason, never as zero;
* the reference is never re-chosen: a negative difference stays negative;
* two stored uncertainties are never combined (nothing records whether they are correlated).

Everything here is data over public refs. No database id appears in any value.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from app.chemistry.units import HARTREE_TO_KJ_MOL
from app.db.models.common import CalculationQuality, RecordReviewStatus
from app.services.structure_selection.models import CoverageRequirement, RepeatPolicy

#: Version of the relative-energy semantics (grouping, level filter, repeats, differencing) and of the document that
#: records them. 3: a hidden ref in the captured facts is a placeholder token (``withheld:<n>``) under a profile with a
#: review floor (guard PR-3); nothing is differenced differently.
RELATIVE_ENERGY_VERSION = "3"
#: Version of the engineering bounds below.
BOUNDS_VERSION = "1"
#: The most conformer groups one request is answered for. Same number as the structure candidate bound.
MAX_MEMBERS = 500

NOTE_ORDER = "members are ordered by conformer ref; the order is not a ranking"


class EnergyKind(str, Enum):
    """The energy compared. The last two are named so they can be refused with a coded error, not guessed at."""

    electronic_energy = "electronic_energy"
    zero_kelvin_energy = "zero_kelvin_energy"
    enthalpy = "enthalpy"
    gibbs_energy = "gibbs_energy"


#: Kinds this release evaluates.
SUPPORTED_KINDS = frozenset({EnergyKind.electronic_energy, EnergyKind.zero_kelvin_energy})


class Certificate(str, Enum):
    """What curvature evidence a member needs. ``none`` is named only so it can be refused (owner-gated)."""

    local_minimum_any_surface = "local_minimum_any_surface"
    local_minimum_on_energy_surface = "local_minimum_on_energy_surface"
    none = "none"


SUPPORTED_CERTIFICATES = frozenset({Certificate.local_minimum_any_surface, Certificate.local_minimum_on_energy_surface})


class RelativeEnergyOutcome(str, Enum):
    """What the operation found. A negative scientific outcome is an answer, not an error."""

    relative_energies = "relative_energies"
    reference_only = "reference_only"
    reference_unavailable = "reference_unavailable"
    multiple_recipes_at_level = "multiple_recipes_at_level"
    incomplete_coverage = "incomplete_coverage"
    no_members = "no_members"


class MemberStatus(str, Enum):
    reference = "reference"
    present = "present"
    absent = "absent"
    unresolved = "unresolved"


# Closed vocabulary of reasons a member has no usable value (``absent``) or no single value (``unresolved``).
NO_STRUCTURE_DETERMINATION = "no_structure_determination"
NO_DETERMINATION_SUPPLIES_KIND = "no_determination_supplies_requested_kind"
NO_VALUE_AT_LEVEL = "no_value_at_requested_level"
RECIPE_NOT_ESTABLISHED = "recipe_not_established"
RECIPE_DIFFERS = "recipe_differs_from_request"
NOT_ELIGIBLE = "not_eligible"
CERTIFICATE_NOT_SUPPORTED = "certificate_not_supported"
CERTIFICATE_NOT_ON_ENERGY_SURFACE = "certificate_not_on_energy_surface"
CONTESTED_CERTIFICATE = "contested_certificate"
REPEAT_VALUES_DISAGREE = "repeat_values_disagree"
MULTIPLE_RECIPES_AT_LEVEL = "multiple_recipes_at_level"
#: The group's own review state is below the caller's floor.
GROUP_BELOW_REVIEW_FLOOR = "group_below_review_floor"
#: The group is rejected or deprecated: terminal, which is not a floor.
GROUP_TERMINAL_REVIEW_STATUS = "group_terminal_review_status"
#: A determination exists but the caller's floor holds it out (its observation, or the entry, is below the floor or
#: terminal). The group has no other value.
DETERMINATION_BELOW_REVIEW_FLOOR = "determination_below_review_floor"
#: A determination exists but is held out because its observation, or the entry, is rejected or deprecated: terminal,
#: which is not a floor (the caller may have set none).
DETERMINATION_TERMINAL_REVIEW_STATUS = "determination_terminal_review_status"
#: A detail code (not a reason): the named level is bound to a composite scheme, which is what a value is filed under.
LEVEL_BOUND_TO_COMPOSITE_SCHEME = "level_bound_to_composite_scheme"
REFERENCE_UNAVAILABLE_DETAIL = "reference_unavailable"

REASONS = frozenset(
    {
        NO_STRUCTURE_DETERMINATION,
        NO_DETERMINATION_SUPPLIES_KIND,
        NO_VALUE_AT_LEVEL,
        RECIPE_NOT_ESTABLISHED,
        RECIPE_DIFFERS,
        NOT_ELIGIBLE,
        CERTIFICATE_NOT_SUPPORTED,
        CERTIFICATE_NOT_ON_ENERGY_SURFACE,
        CONTESTED_CERTIFICATE,
        REPEAT_VALUES_DISAGREE,
        MULTIPLE_RECIPES_AT_LEVEL,
        GROUP_BELOW_REVIEW_FLOOR,
        GROUP_TERMINAL_REVIEW_STATUS,
        DETERMINATION_BELOW_REVIEW_FLOOR,
        DETERMINATION_TERMINAL_REVIEW_STATUS,
    }
)

BASIS_RELATIVE = (
    "each difference is the member's stored value minus the named reference conformer's stored value, both at the one "
    "requested level and in one recipe cohort, taken in hartree and converted once; a negative value is below the "
    "reference and is not re-referenced"
)
BASIS_REFERENCE_ONLY = "the named reference conformer has a value; no other member has a usable value at this level"
BASIS_REFERENCE_UNAVAILABLE = (
    "the named reference conformer has no single value at this level, so no difference is computed for any member; "
    "the reference is never replaced by another conformer"
)
BASIS_MULTIPLE_RECIPES = (
    "values exist at the requested level in more than one recipe cohort; nothing is computed, because total energies "
    "of different recipes are not on one scale. Narrow the request with a recipe"
)
BASIS_INCOMPLETE = (
    "a complete answer over every requested member was asked for and at least one member has no usable value; the "
    "differences that exist are listed"
)
BASIS_NO_MEMBERS = "the entry has no visible conformer group; nothing was assessed"
BASIS_SURFACE_NOTE = "the minimum is certified on the listed surface, not on the surface of the energy"


@dataclass(frozen=True)
class LevelSelector:
    """The one level a comparison is made at: exactly one of a level of theory or a composite scheme."""

    level_of_theory_ref: str | None = None
    composite_scheme_ref: str | None = None

    def __post_init__(self) -> None:
        if (self.level_of_theory_ref is None) == (self.composite_scheme_ref is None):
            raise ValueError("a level names exactly one of level_of_theory_ref and composite_scheme_ref")

    def to_dict(self) -> dict[str, Any]:
        return {"level_of_theory_ref": self.level_of_theory_ref, "composite_scheme_ref": self.composite_scheme_ref}

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> LevelSelector:
        return cls(**raw)


@dataclass(frozen=True)
class RelativeEnergyRequest:
    """The normalised request.

    The scientific choices (``reference_conformer_ref``, ``energy_kind``, ``level``, ``certificate``) have no default.
    Coverage and repeat handling keep structure selection's defaults, which only make the answer more conservative.

    :param reference_conformer_ref: The named reference basin (a ``cg_`` ref of the entry).
    :param member_refs: The conformer groups compared; ``None``: every visible group of the entry. The reference is
        always included.
    :param temperature_k: For the deferred enthalpy and Gibbs kinds only; refused for the supported ones.
    """

    reference_conformer_ref: str
    energy_kind: EnergyKind
    level: LevelSelector
    certificate: Certificate
    recipe: dict[str, Any] | None = None
    member_refs: tuple[str, ...] | None = None
    coverage: CoverageRequirement = CoverageRequirement.known_values
    repeat_policy: RepeatPolicy = RepeatPolicy.retain_alternates
    min_review_status: RecordReviewStatus | None = None
    permitted_quality: frozenset[CalculationQuality] | None = None
    require_stable_reference: bool = False
    temperature_k: float | None = None

    def __post_init__(self) -> None:
        if self.member_refs is not None:
            if not self.member_refs:
                raise ValueError("member_refs names no member; omit it to ask for every visible conformer group")
            if len(set(self.member_refs)) != len(self.member_refs):
                raise ValueError("member_refs names a member twice")
        if self.temperature_k is not None:
            if self.energy_kind in SUPPORTED_KINDS:
                raise ValueError("temperature_k belongs to enthalpy and gibbs_energy only")
            if not self.temperature_k > 0:
                raise ValueError("temperature_k must be positive")

    @property
    def wanted_refs(self) -> frozenset[str] | None:
        """The groups compared: the named members plus the reference, or ``None`` for every visible group."""
        if self.member_refs is None:
            return None
        return frozenset({self.reference_conformer_ref, *self.member_refs})

    def to_dict(self) -> dict[str, Any]:
        return {
            "reference_conformer_ref": self.reference_conformer_ref,
            "energy_kind": self.energy_kind.value,
            "temperature_k": self.temperature_k,
            "level": self.level.to_dict(),
            "certificate": self.certificate.value,
            "recipe": self.recipe,
            "member_refs": list(self.member_refs) if self.member_refs is not None else None,
            "coverage_requirement": self.coverage.value,
            "repeat_policy": self.repeat_policy.value,
            "min_review_status": self.min_review_status.value if self.min_review_status else None,
            "permitted_quality": sorted(q.value for q in self.permitted_quality) if self.permitted_quality else None,
            "require_stable_reference": self.require_stable_reference,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> RelativeEnergyRequest:
        return cls(
            reference_conformer_ref=raw["reference_conformer_ref"],
            energy_kind=EnergyKind(raw["energy_kind"]),
            temperature_k=raw["temperature_k"],
            level=LevelSelector.from_dict(raw["level"]),
            certificate=Certificate(raw["certificate"]),
            recipe=raw["recipe"],
            member_refs=tuple(raw["member_refs"]) if raw["member_refs"] is not None else None,
            coverage=CoverageRequirement(raw["coverage_requirement"]),
            repeat_policy=RepeatPolicy(raw["repeat_policy"]),
            min_review_status=RecordReviewStatus(raw["min_review_status"]) if raw["min_review_status"] else None,
            permitted_quality=(
                frozenset(CalculationQuality(q) for q in raw["permitted_quality"]) if raw["permitted_quality"] else None
            ),
            require_stable_reference=raw["require_stable_reference"],
        )


@dataclass(frozen=True)
class MemberCertificate:
    """What the member's curvature evidence supports, and on which surface."""

    claim: str
    supported: bool
    surface_level_refs: tuple[str, ...]
    on_energy_surface: bool
    witness_refs: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "claim": self.claim,
            "supported": self.supported,
            "surface_level_refs": list(self.surface_level_refs),
            "on_energy_surface": self.on_energy_surface,
            "witness_refs": list(self.witness_refs),
        }


@dataclass(frozen=True)
class Alternate:
    """Another stored value of the same member (a repeat that was not chosen, or that disagrees)."""

    determination_ref: str
    value_hartree: float

    def to_dict(self) -> dict[str, Any]:
        return {"determination_ref": self.determination_ref, "value_hartree": self.value_hartree}


@dataclass(frozen=True)
class MemberResult:
    """One conformer group's answer.

    ``relative_*`` are ``None`` unless the member has a value and the reference has one too.
    ``value_uncertainty_hartree`` is the uncertainty stored with ``value_hartree``, echoed as it is. There is no
    uncertainty on the difference: combining two stored uncertainties would assume they are uncorrelated.
    """

    conformer_ref: str
    status: MemberStatus
    relative_energy_kj_mol: float | None = None
    relative_energy_hartree: float | None = None
    value_hartree: float | None = None
    value_uncertainty_hartree: float | None = None
    representative: bool = False
    determination_refs: tuple[str, ...] = ()
    energy_calculation_refs: tuple[str, ...] = ()
    evaluated_geometry_refs: tuple[str, ...] = ()
    #: The one geometry the stored value was evaluated at: set only when every record that gives this value names the
    #: same geometry. ``None`` when the records of the value name several or none (it is never taken from a sibling).
    value_geometry_ref: str | None = None
    certificate: MemberCertificate | None = None
    alternates: tuple[Alternate, ...] = ()
    absent_reason: str | None = None
    detail: tuple[str, ...] = ()

    @property
    def has_value(self) -> bool:
        return self.value_hartree is not None

    def to_dict(self) -> dict[str, Any]:
        return {
            "conformer_ref": self.conformer_ref,
            "status": self.status.value,
            "relative_energy_kj_mol": self.relative_energy_kj_mol,
            "relative_energy_hartree": self.relative_energy_hartree,
            "value_hartree": self.value_hartree,
            "value_uncertainty_hartree": self.value_uncertainty_hartree,
            "representative": self.representative,
            "determination_refs": list(self.determination_refs),
            "energy_calculation_refs": list(self.energy_calculation_refs),
            "evaluated_geometry_refs": list(self.evaluated_geometry_refs),
            "value_geometry_ref": self.value_geometry_ref,
            "certificate": self.certificate.to_dict() if self.certificate is not None else None,
            "alternates": [a.to_dict() for a in self.alternates],
            "absent_reason": self.absent_reason,
            "detail": list(self.detail),
        }


@dataclass(frozen=True)
class RelativeEnergyResult:
    """The pure answer over public refs."""

    outcome: RelativeEnergyOutcome
    basis: str
    energy_kind: EnergyKind
    level: LevelSelector
    recipe_cohort_id: str | None
    recipe_facts: tuple[dict[str, Any], ...]
    reference_ref: str
    members: tuple[MemberResult, ...]
    coverage: dict[str, Any]
    unresolved: tuple[dict[str, Any], ...] = ()
    cohorts: tuple[dict[str, Any], ...] = ()
    notes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def reference(self) -> MemberResult | None:
        return next((m for m in self.members if m.conformer_ref == self.reference_ref), None)

    def to_dict(self) -> dict[str, Any]:
        from app.services.structure_selection.decision import SEARCH_COMPLETENESS

        ref = self.reference or MemberResult(self.reference_ref, MemberStatus.absent)
        return {
            "relative_energy_version": RELATIVE_ENERGY_VERSION,
            "outcome": self.outcome.value,
            "basis": self.basis,
            "energy_kind": self.energy_kind.value,
            "units": {"relative": "kJ/mol", "stored": "hartree", "hartree_to_kj_mol": HARTREE_TO_KJ_MOL},
            "level": {
                **self.level.to_dict(),
                "recipe_cohort_id": self.recipe_cohort_id,
                "recipe_facts": [dict(f) for f in self.recipe_facts],
            },
            "reference": {
                "conformer_ref": ref.conformer_ref,
                "status": "present" if ref.has_value else ref.status.value,
                "value_hartree": ref.value_hartree,
                "value_uncertainty_hartree": ref.value_uncertainty_hartree,
                "determination_refs": list(ref.determination_refs),
                "absent_reason": ref.absent_reason,
            },
            "members": [m.to_dict() for m in self.members],
            "coverage": dict(self.coverage),
            "unresolved": [dict(u) for u in self.unresolved],
            "cohorts": [dict(c) for c in self.cohorts],
            "notes": list(self.notes),
            "search_completeness": SEARCH_COMPLETENESS,
        }
