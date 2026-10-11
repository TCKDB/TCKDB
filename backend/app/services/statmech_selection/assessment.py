"""The deterministic statmech assessor: can this supplied model support the requested claim?

Pure over the normalised facts; it reads no database. For one candidate (a declared model revision, or a legacy record
read as it is) and one normalised request it answers ``applicable``, ``incompatible``, ``unsupported`` or ``unresolved``
(:class:`~app.services.selection_kernel.Applicability`), with every reason that led there. It never computes a partition
function, a thermochemical value or a population, and it never compares two candidates (that is the decision stage).

The order, which is the specification's:

1. the request's own support (a deferred task, a non-gas environment, ...) is judged once for the population;
2. representation and scope: a supported declaration version and treatment, and the candidate's scope against the target;
3. availability: the owner record and every necessary source and member at their own effective floor. A parent's approval
   never approves a source, and a source that is hidden, below the floor, terminal or rejected reads as unavailable with
   one generic reason that names no record;
4. the actual recipe, normalised: equal unknown settings are not equivalence;
5. coherent role use: the pinned geometry, the curvature witness on it, no latest/lowest-id witness and no Cartesian assembly
   of favourable pieces;
6. the stationary-point owner (ADR 0012: magnitude against the stored tau, never counts) and scoped findings, each at its
   own subject and role: a failed unrelated rerun does not erase a coherent model, a same-target disproof cannot be evaded,
   and a curvature invalidation does not invalidate a separately valid recorded energy;
7. the task's own accounting: scale purpose and application, rotor replacement, member and region counting, energy
   reference, normalization and the temperature domain.

What an unstated fact is: unknown, never a default. An unstated scale application is not unity; an unstated optical state
is not "counted" and not "uncounted"; a member multiplicity that is not stated is not one.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from app.db.models.common import RecordReviewStatus
from app.services.selection_kernel import Applicability
from app.services.statmech_selection.facts import (
    CandidateFacts,
    DeclarationFacts,
    SubjectFacts,
)
from app.services.statmech_selection.legacy import (
    AMBIGUOUS,
    MISSING,
    Readiness,
    classify,
)
from app.services.statmech_selection.models import StatmechRequest
from app.services.statmech_selection.reasons import REASONS, REQUEST_REASONS, Category, base, is_registered_advisory
from app.services.statmech_selection.vocabulary import (
    CandidateKind,
    DeferredTask,
    DividingSurface,
    ElectronicScope,
    EnvironmentKind,
    IsotopeConvention,
    Normalization,
    ScopeKind,
    StereochemicalScope,
    Task,
)
from app.services.structure_selection.assessment import (
    CURVATURE_SIDE,
    _apply_finding,
    _coherence,
    _Findings,
    _witness_support,
    live_findings,
)
from app.services.structure_selection.models import (
    NormalizedCalculation,
    StructureSubject,
    ValidationClaim,
)

#: Calculation qualities a request permits. A rejected calculation never rescues.
PERMITTED_QUALITY = frozenset({"raw", "curated"})

_CURVATURE_ROLES = frozenset({"curvature", "alternative_characterization"})
_TORSION_TREATMENTS = frozenset({"rrho_1d", "rrho_nd", "rrho_1d_nd"})
_ANHARMONIC_TREATMENTS = frozenset({"rrho_ad", "rrao"})

#: The scale-factor kind each wire contribution is calibrated for.
CONTRIBUTION_KIND = {
    "fundamental": "fundamental",
    "zero_point_energy": "zpe",
    "vibrational_entropy": "entropy",
    "vibrational_heat_capacity": "heat_capacity",
    "vibrational_enthalpy": "enthalpy",
}
#: The scale contribution each profile needs.
PROFILE_CONTRIBUTION = {
    "vibrational_component": "fundamental",
    "zpe_component": "zero_point_energy",
    "within_basin_thermal.entropy": "vibrational_entropy",
    "within_basin_thermal.heat_capacity": "vibrational_heat_capacity",
    "canonical_model.internal_partition": "fundamental",
    "canonical_model.full_partition": "fundamental",
    "conventional_ts_transverse_model": "fundamental",
}

#: Profiles that need a curvature witness, a rotor accounting, and the statistical factors.
_NEEDS_CURVATURE = frozenset(PROFILE_CONTRIBUTION)
_NEEDS_ACCOUNTING = frozenset(p for p in PROFILE_CONTRIBUTION if p not in ("vibrational_component", "zpe_component"))


def profile_of(request: StatmechRequest) -> str:
    """The claim profile of a supported request (the key the legacy classifier and the scale rules use)."""
    task = request.task
    assert isinstance(task, Task)
    if task is Task.within_basin_thermal:
        assert request.observable is not None
        return f"within_basin_thermal.{request.observable.value}"
    if task is Task.canonical_model:
        assert request.observable is not None
        return f"canonical_model.{request.observable.value}"
    return task.value


# ---------------------------------------------------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Reason:
    """One finding that moved a candidate away from ``applicable``."""

    code: str
    applicability: Applicability
    category: Category

    def to_dict(self) -> dict[str, str]:
        return {"code": self.code, "applicability": self.applicability.value, "category": self.category.value}


@dataclass(frozen=True)
class RecipeFact:
    name: str
    state: str
    value: str | None

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


@dataclass(frozen=True)
class NormalizedRecipe:
    """The actual recipe a candidate states, normalised: facts, the ones never established, the cohort it can be compared in."""

    facts: tuple[RecipeFact, ...]
    unestablished: tuple[str, ...]
    cohort_key: str | None

    def to_dict(self) -> dict[str, Any]:
        return {"facts": [f.to_dict() for f in self.facts], "unestablished": list(self.unestablished), "cohort_key": self.cohort_key}


@dataclass(frozen=True)
class StatmechAssessment:
    """The verdict for one candidate, with every finding that fed it.

    :param advisory: Findings disclosed without excluding the candidate.
    :param comparative_unknown: Comparative metadata the candidate lacks; it creates no preference edge and excludes nothing.
    :param evidence: The roles the claim rests on and the public refs filling them. A role with no ref is not listed.
    """

    candidate_ref: str
    kind: CandidateKind
    applicability: Applicability
    reasons: tuple[Reason, ...]
    advisory: tuple[str, ...] = ()
    comparative_unknown: tuple[str, ...] = ()
    evidence: Mapping[str, tuple[str, ...]] = None  # type: ignore[assignment]
    recipe: NormalizedRecipe | None = None
    supersession: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        if self.evidence is None:
            object.__setattr__(self, "evidence", {})

    @property
    def eligible(self) -> bool:
        return self.applicability is Applicability.applicable

    @property
    def categories(self) -> frozenset[Category]:
        return frozenset(r.category for r in self.reasons)

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_ref": self.candidate_ref,
            "kind": self.kind.value,
            "applicability": self.applicability.value,
            "reasons": [r.to_dict() for r in self.reasons],
            "advisory": list(self.advisory),
            "comparative_unknown": list(self.comparative_unknown),
            "evidence": {k: list(v) for k, v in sorted(self.evidence.items())},
            "recipe": self.recipe.to_dict() if self.recipe is not None else None,
            "supersession": dict(self.supersession) if self.supersession is not None else None,
            "eligible": self.eligible,
        }


class Collector:
    """Gathers reasons and notes for one candidate. A reason must be registered; a note must be a registered advisory."""

    def __init__(self) -> None:
        self.reasons: list[Reason] = []
        self.advisory: list[str] = []
        self.comparative_unknown: list[str] = []

    def add(self, code: str) -> None:
        spec = REASONS[base(code)]  # KeyError: an unregistered reason is a bug, not a new vocabulary
        reason = Reason(code, spec.applicability, spec.category)
        if reason not in self.reasons:
            self.reasons.append(reason)

    def note(self, code: str) -> None:
        if not is_registered_advisory(code):
            raise KeyError(f"unregistered advisory {code!r}")
        if code not in self.advisory:
            self.advisory.append(code)

    def unknown(self, code: str) -> None:
        if code not in self.comparative_unknown:
            self.comparative_unknown.append(code)

    def verdict(self) -> Applicability:
        present = {r.applicability for r in self.reasons}
        for level in (Applicability.incompatible, Applicability.unsupported, Applicability.unresolved):
            if level in present:
                return level
        return Applicability.applicable


# ---------------------------------------------------------------------------------------------------------------------
# The request's own support
# ---------------------------------------------------------------------------------------------------------------------


def request_support(request: StatmechRequest) -> tuple[str, ...]:
    """Reasons this release does not evaluate the request at all (outcome ``unsupported_task``), in a fixed order."""
    out: list[str] = []
    if isinstance(request.task, DeferredTask):
        out.append(f"task_deferred:{request.task.value}")
    if request.conditions.environment is not EnvironmentKind.gas:
        out.append(f"environment_not_supported:{request.conditions.environment.value}")
    if request.target.isotope_convention is not IsotopeConvention.entry_documented_masses:
        out.append(f"isotope_convention_unsupported:{request.target.isotope_convention.value}")
    if request.target.stereochemical_scope is not StereochemicalScope.entry_identity:
        out.append(f"stereochemical_scope_unsupported:{request.target.stereochemical_scope.value}")
    if request.target.scope.dividing_surface is DividingSurface.variational:
        out.append("dividing_surface_not_supported:variational")
    assert all(c.split(":", 1)[0] in REQUEST_REASONS for c in out)
    return tuple(out)


# ---------------------------------------------------------------------------------------------------------------------
# Helpers over the loaded calculations
# ---------------------------------------------------------------------------------------------------------------------


class _World:
    """What this assessment may read: the loaded calculations, and which of them the request can use."""

    def __init__(self, calculations: Mapping[str, NormalizedCalculation], effective: frozenset[RecordReviewStatus]) -> None:
        self.calculations = calculations
        self.effective = effective

    def usable(self, ref: str | None) -> NormalizedCalculation | None:
        """The calculation if it is loaded, at an effective status and not rejected; otherwise ``None`` (one answer)."""
        calc = self.calculations.get(ref) if ref is not None else None
        if calc is None or calc.review_status not in self.effective or calc.quality not in PERMITTED_QUALITY:
            return None
        return calc


def _geometry_of(calc: NormalizedCalculation, side: str) -> tuple[str, ...]:
    return calc.output_geometry_refs if side == "output" else calc.input_geometry_refs


_GEOMETRY_SIDE = {"opt": "output", "composite": "output", "freq": "input", "sp": "input"}


def _calc_geometry(calc: NormalizedCalculation) -> str | None:
    """The one geometry the calculation's own links name on the side its type reads, else ``None``."""
    side = _GEOMETRY_SIDE.get(calc.type)
    if side is None:
        return None
    refs = _geometry_of(calc, side)
    return refs[0] if len(refs) == 1 else None


def _structure_subject(subject: SubjectFacts) -> StructureSubject:
    return StructureSubject(
        kind=subject.kind.value,
        entry_ref=subject.entry_ref,
        stationary_point_kind=subject.stationary_point_kind,
        electronic_state_kind=subject.electronic_state_kind,
        isotope_key=subject.isotope_key,
        charge=subject.charge,
        multiplicity=subject.multiplicity,
    )


def _apply_findings(calc: NormalizedCalculation, roles: frozenset[str], f: Collector) -> None:
    """Apply a calculation's live findings at the roles this candidate uses it for.

    A role invalidation bites only the roles it names; a contradictory characterization is about curvature, so it bites the
    roles that read curvature and never bans a separately valid recorded energy on the same calculation. Only an authorized
    adjudication about the same subject settles a finding (``live_findings``).
    """
    for finding in live_findings(calc.findings):
        bites = finding.kind != "contradictory_characterization" or bool(roles & _CURVATURE_ROLES)
        role_needed = finding.role in roles if finding.role is not None else True
        scoped = _Findings()
        _apply_finding(scoped, finding, blocks=bites, role_needed=role_needed)
        for label in scoped.blocking:
            f.add(label)  # ``finding_invalidates:<kind>:<authority>`` or ``role_invalidated:<role>:<authority>``
        for reason in scoped.reasons:
            f.add(reason.code)  # ``finding_unresolved:<kind>``
        for note in scoped.advisory:
            f.note(note)


# ---------------------------------------------------------------------------------------------------------------------
# Evidence: the pinned geometry, the curvature witnesses
# ---------------------------------------------------------------------------------------------------------------------


@dataclass
class _Evidence:
    geometry_refs: set[str]
    geometry_sources: list[str]
    curvature: list[NormalizedCalculation]
    energy: list[str]
    parameters: list[str]
    scale_factors: list[Any]
    surface_unavailable: bool = False
    #: Roles an imported-parameter (literature) source supports. Parameters taken from the literature are supported
    #: provenance for the role: they are neither a fabricated local job nor re-validated here.
    imported: set[str] = field(default_factory=set)


def _declared_evidence(
    candidate: CandidateFacts, world: _World, f: Collector, *, needs_geometry: bool, needs_curvature: bool
) -> _Evidence:
    decl = candidate.declaration
    assert decl is not None
    ev = _Evidence(set(), [], [], [], [], [])
    unavailable_roles: set[str] = set()
    for src in decl.sources:
        if src.member_key is not None:
            continue  # a member's own sources are the member's evidence, not the model's
        if src.locator == "scale_factor":
            ev.scale_factors.append(src.scale_factor)
            continue
        if src.locator == "literature":
            if src.literature_identified is not True:
                # Kept, never trusted: a citation with neither DOI nor ISBN cannot be resolved, so it meets no role.
                f.note(f"source_literature_unidentified:{src.role}")
                unavailable_roles.add(src.role)
                continue
            if src.literature_ref:
                ev.parameters.append(src.literature_ref)
            if src.role == "geometry":
                ev.geometry_sources.append(f"literature:{src.literature_ref}")
            if src.role in ("geometry", "curvature"):
                ev.imported.add(src.role)
            continue
        calc = world.usable(src.calculation_ref)
        if calc is None:
            unavailable_roles.add(src.role)
            continue
        if src.role == "geometry":
            ref = src.geometry_ref or _calc_geometry(calc)
            if ref is None:
                f.add("multiple_geometry_alternatives")
            else:
                ev.geometry_refs.add(ref)
                ev.geometry_sources.append(calc.calculation_ref)
        elif src.role == "curvature":
            ev.curvature.append(calc)
            if src.geometry_ref:
                ev.geometry_refs.add(src.geometry_ref)
        elif src.role == "absolute_energy":
            ev.energy.append(calc.calculation_ref)
    for role in sorted(unavailable_roles):
        # One generic reason that names no record: hidden, absent, below the floor and rejected read the same.
        required = (role == "geometry" and needs_geometry) or (role == "curvature" and needs_curvature) or role == "torsional_surface"
        if required or role in ("absolute_energy", "relative_energy"):
            f.add(f"required_source_unusable:{role}")
    return ev


def _legacy_evidence(candidate: CandidateFacts, world: _World, f: Collector) -> _Evidence:
    ev = _Evidence(set(), [], [], [], [], [])
    seen: set[str] = set()
    for _role, ref in candidate.record.source_links:
        if ref in seen:
            continue
        seen.add(ref)
        calc = world.usable(ref)
        if calc is None:
            f.add("required_source_unusable:linked_calculation")
            continue
        if calc.type in ("opt", "composite", "freq"):
            geometry = _calc_geometry(calc)
            if geometry is not None:
                ev.geometry_refs.add(geometry)
                ev.geometry_sources.append(calc.calculation_ref)
            elif _geometry_of(calc, _GEOMETRY_SIDE[calc.type]):
                f.add("multiple_geometry_alternatives")
        if calc.curvature.has_freq_result or calc.curvature.has_hessian:
            ev.curvature.append(calc)
        if calc.type == "sp":
            ev.energy.append(calc.calculation_ref)
    return ev


def _curvature_claim(request: StatmechRequest) -> ValidationClaim | None:
    scope = request.target.scope.kind
    if scope is ScopeKind.ts_determination:
        return ValidationClaim.first_order_saddle
    if scope is ScopeKind.atom:
        return None
    return ValidationClaim.local_minimum


def _judge_curvature(
    ev: _Evidence,
    pinned: str | None,
    request: StatmechRequest,
    subject: SubjectFacts,
    f: Collector,
    *,
    roles_used: frozenset[str],
) -> tuple[str, ...]:
    """The curvature witnesses on the pinned geometry, judged by the shared stationary-point owner. Returns their refs."""
    claim = _curvature_claim(request)
    if claim is None:
        return ()
    if not ev.curvature:
        f.add("required_evidence_unavailable:curvature")
        return ()
    structure_subject = _structure_subject(subject)
    supports: list[NormalizedCalculation] = []
    contradicts: list[NormalizedCalculation] = []
    unsupported = False
    pending: list[str] = []
    for calc in ev.curvature:
        side = CURVATURE_SIDE.get(calc.type)
        if pinned is None:
            pending.append("multiple_geometry_alternatives" if len(ev.geometry_refs) > 1 else "required_evidence_unavailable:geometry")
            continue
        relation = _coherence(calc, side, pinned)
        if relation == "other":
            f.note("curvature_on_other_geometry")
            continue
        if relation in ("ambiguous", "unestablished"):
            pending.append("witness_ambiguous")
            continue
        verdict, how, notes = _witness_support(calc, claim, structure_subject)
        for note in notes:
            if is_registered_advisory(note):
                f.note(note)
        if verdict == "supports":
            supports.append(calc)
        elif verdict == "contradicts":
            contradicts.append(calc)
        elif verdict == "unsupported":
            unsupported = True
        else:
            pending.append("required_evidence_unavailable:" + (notes[0].split(":")[0] if notes else "curvature"))
        _ = how
    for calc in (*supports, *contradicts):
        _apply_findings(calc, roles_used, f)
    refs = tuple(sorted(c.calculation_ref for c in (*supports, *contradicts)))
    if supports and contradicts:
        f.add("curvature_witnesses_disagree")
    elif contradicts:
        f.add("curvature_contradicts_claim")
    elif unsupported and not supports:
        f.add("higher_order_saddle_flagged" if claim is not ValidationClaim.local_minimum else "treatment_not_supported:curvature")
    elif not supports:
        for code in dict.fromkeys(pending or ["required_evidence_unavailable:curvature"]):
            f.add(code)
    return refs


# ---------------------------------------------------------------------------------------------------------------------
# Scope
# ---------------------------------------------------------------------------------------------------------------------


def _check_scope(candidate: CandidateFacts, request: StatmechRequest, subject: SubjectFacts, f: Collector) -> None:
    scope = request.target.scope
    decl = candidate.declaration
    if decl is not None:
        if decl.scope_kind != scope.kind.value:
            f.add("scope_incompatible")
            return
        if scope.kind is ScopeKind.basin and decl.conformer_group_ref != scope.conformer_group_ref:
            f.add("scope_incompatible")
        elif scope.kind is ScopeKind.explicit_equilibrium_ensemble and decl.ref != scope.ensemble_declaration_ref:
            f.add("scope_incompatible")
        elif scope.kind is ScopeKind.ts_determination and decl.determination_ref != scope.determination_ref:
            f.add("scope_incompatible")
        elif scope.kind is ScopeKind.atom and subject.is_atom is False:
            f.add("scope_incompatible")
        return
    # A legacy record carries no declared scope: it can support a narrow claim only through what it links.
    if scope.kind is ScopeKind.explicit_equilibrium_ensemble:
        f.add("scope_incompatible")  # a legacy record is never a member set
    elif scope.kind is ScopeKind.atom:
        record = candidate.record
        if record.rigid_rotor_kind not in (None, "atom") or subject.is_atom is False:
            f.add("scope_incompatible")
        elif record.rigid_rotor_kind is None and subject.is_atom is None:
            f.add("scope_unestablished")
    elif scope.kind is ScopeKind.basin:
        anchors = candidate.anchor_group_refs
        if len(anchors) == 1 and anchors[0] != scope.conformer_group_ref:
            f.add("scope_incompatible")
        elif len(anchors) != 1:
            f.add("scope_unestablished")
    elif scope.kind is ScopeKind.ts_determination:
        det = candidate.determination
        if det is None or det.ref != scope.determination_ref:
            f.add("scope_incompatible" if det is not None else "scope_unestablished")


# ---------------------------------------------------------------------------------------------------------------------
# The declared recipe
# ---------------------------------------------------------------------------------------------------------------------


def _members_available(decl: DeclarationFacts, effective: frozenset[RecordReviewStatus], f: Collector) -> None:
    for member in decl.members:
        if member.review_status is None or member.review_status not in effective:
            f.add("required_member_unavailable")
            return


def _check_scale(decl: DeclarationFacts, profile: str, ev: _Evidence, f: Collector) -> None:
    recipe = decl.recipe
    application = recipe.get("scale_application")
    contributions = list(recipe.get("scale_contributions") or [])
    needed = PROFILE_CONTRIBUTION[profile]
    kinds = {sf.kind for sf in ev.scale_factors if sf is not None}
    if application in (None, "unknown"):
        f.add("scale_application_unknown")
        return
    if application == "not_applied":
        if kinds:
            f.add("scale_declaration_conflict")
        return
    if needed not in contributions:
        f.add("scale_purpose_mismatch")
        return
    if "fundamental" in contributions and any(c != "fundamental" for c in contributions):
        f.add("counting_conflict:double_scaling")
    wanted = {CONTRIBUTION_KIND[c] for c in contributions}
    if not kinds:
        f.add("required_evidence_unavailable:scale_factor")
    elif not wanted <= kinds or kinds - wanted:
        f.add("scale_purpose_mismatch")


def _check_energy_reference(decl: DeclarationFacts, request: StatmechRequest, profile: str, f: Collector) -> None:
    recipe = decl.recipe
    zero, zpe = recipe.get("energy_zero"), recipe.get("zpe_included_in_energies")
    if zero == "electronic" and zpe is True:
        f.add("energy_reference_conflict")
    wanted = request.conditions.energy_reference
    needs = profile == "zpe_component" or profile == "canonical_model.full_partition"
    if not needs:
        return
    if zero is None or zpe is None:
        f.add("required_recipe_unknown:energy_reference")
        return
    if wanted is not None and (wanted.zero.value != zero or (wanted.zpe.value == "included") != bool(zpe)):
        f.add("energy_reference_mismatch")


def _check_translation(decl: DeclarationFacts, request: StatmechRequest, profile: str, f: Collector) -> None:
    recipe = decl.recipe
    included = recipe.get("translation_included")
    full = profile == "canonical_model.full_partition"
    internal_claim = profile.startswith(("within_basin_thermal", "canonical_model.internal", "conventional_ts"))
    if not (full or internal_claim):
        return
    if included is None:
        f.add("required_recipe_unknown:translation")
        return
    if full:
        if included is False:
            f.add("model_is_internal_only")
            return
        wanted = request.conditions
        got = recipe.get("translational_normalization")
        if got is None:
            f.add("required_recipe_unknown:translational_normalization")
        elif wanted.normalization is None or got != wanted.normalization.value:
            f.add("normalization_mismatch")
        else:
            key = {
                Normalization.fixed_volume: ("reference_volume_m3", wanted.volume_m3),
                Normalization.standard_state_pressure: ("reference_pressure_pa", wanted.pressure_pa),
                Normalization.standard_state_concentration: ("reference_concentration_mol_m3", wanted.concentration_mol_m3),
            }[wanted.normalization]
            if recipe.get(key[0]) != key[1]:
                f.add("normalization_mismatch")
    elif included is True:
        f.add("model_includes_translation")


def _check_domain(decl: DeclarationFacts, request: StatmechRequest, f: Collector) -> None:
    domain = request.conditions.temperature_domain_k
    low, high = decl.recipe.get("validity_min_k"), decl.recipe.get("validity_max_k")
    if domain is None:
        return
    if low is None or high is None:
        f.note("validity_domain_not_stated")
        f.unknown("validity_domain_not_stated")
    elif domain[0] < low or domain[1] > high:
        f.add("domain_not_covered")


def _check_isotope(decl: DeclarationFacts, request: StatmechRequest, f: Collector) -> None:
    masses = decl.recipe.get("isotope_masses")
    if masses is None:
        f.add("required_recipe_unknown:isotope_masses")
    elif masses != request.target.isotope_convention.value:
        f.add("isotope_mismatch")


def _check_rotors(candidate: CandidateFacts, decl: DeclarationFacts, world: _World, f: Collector) -> None:
    recipe = decl.recipe
    rotors = {int(r["torsion_index"]): r for r in recipe.get("rotors") or []}
    torsions = {t.torsion_index: t for t in candidate.record.torsions}
    treatment = candidate.record.statmech_treatment
    bearing = bool(torsions) or treatment in _TORSION_TREATMENTS
    projected = set(recipe.get("projected_mode_indices") or [])
    if treatment in _ANHARMONIC_TREATMENTS or recipe.get("vibrational_scheme") == "anharmonic":
        f.add("treatment_not_supported:anharmonic")
    if bearing and not rotors:
        f.add("rotor_replacement_unstated")
    for index in torsions:
        if index not in rotors:
            f.add("rotor_replacement_unstated")
    replaced_by: dict[int, int] = {}
    for index, rotor in rotors.items():
        if rotor.get("dimension", 1) > 1:
            f.add("treatment_not_supported:multidimensional_rotor")
        torsion = torsions.get(index)
        if torsion is None:
            f.note("rotor_without_torsion_row")
        elif torsion.invalidated_reason is not None:
            f.add("required_rotor_invalidated")
        representation = rotor.get("representation")
        if representation == "local_scan":
            if torsion is None or torsion.scan_calculation_ref is None:
                f.add("required_evidence_unavailable:rotor_scan")
            elif world.usable(torsion.scan_calculation_ref) is None:
                f.add("required_source_unusable:torsional_surface")
        elif representation == "imported_potential":
            key = rotor.get("potential_source_key")
            source = next((s for s in decl.sources if s.source_key == key and s.role == "torsional_surface"), None)
            # A member-scoped source is skipped by _declared_evidence (it is the member's evidence), so an unidentified
            # literature potential is checked here as well as there.
            unidentified = source is not None and source.locator == "literature" and source.literature_identified is not True
            if source is None or (source.locator == "calculation" and world.usable(source.calculation_ref) is None) or unidentified:
                if unidentified:
                    f.note("source_literature_unidentified:torsional_surface")
                f.add("required_source_unusable:torsional_surface")
        for mode in rotor.get("replaces_mode_indices") or []:
            if mode in projected:
                f.add("counting_conflict:mode_projected_and_replaced")
            if mode in replaced_by and replaced_by[mode] != index:
                f.add("counting_conflict:mode_replaced_twice")
            replaced_by[mode] = index
    flag = candidate.record.uses_projected_frequencies
    if flag is False and projected:
        f.add("counting_conflict:projection_flag")
    if flag is True and not projected and bearing:
        f.add("required_recipe_unknown:projected_mode_indices")
    # A rotor's configurational region may not also be an explicit member.
    regions = {m.region_key for m in decl.members if m.region_key}
    if any(r.get("region_key") in regions for r in rotors.values() if r.get("region_key")):
        f.add("counting_conflict:rotor_member_overlap")


def _check_counting(candidate: CandidateFacts, decl: DeclarationFacts, subject: SubjectFacts, f: Collector) -> None:
    recipe, record = decl.recipe, candidate.record
    atom = subject.is_atom is True or record.rigid_rotor_kind == "atom"
    states = {
        "external_symmetry": recipe.get("external_symmetry_state"),
        "optical_factor": recipe.get("optical_state"),
        "electronic_degeneracy": recipe.get("electronic_levels_state"),
    }
    for name, state in states.items():
        if name == "external_symmetry" and atom:
            continue
        if name == "optical_factor" and atom:
            continue
        if state in (None, "unknown"):
            f.add(f"required_recipe_unknown:{name}")
        elif state == "not_counted":
            f.add(f"required_factor_not_counted:{name}")
    if states["external_symmetry"] == "counted" and not atom and record.external_symmetry is None:
        f.add("required_recipe_unknown:external_symmetry")
    multiplier = recipe.get("optical_multiplier")
    if states["optical_factor"] == "counted" and multiplier is not None and record.optical_isomers is not None:
        if multiplier != record.optical_isomers:
            f.add("counting_conflict:optical_factor")
    if states["optical_factor"] == "counted" and multiplier is None and record.optical_isomers is None:
        f.add("required_recipe_unknown:optical_factor")
    if states["electronic_degeneracy"] == "counted":
        if recipe.get("electronic_spin_multiplier") is not None:
            f.add("counting_conflict:electronic_spin")
        if not record.electronic_levels:
            f.add("required_recipe_unknown:electronic_levels")


def _check_ensemble(decl: DeclarationFacts, request: StatmechRequest, profile: str, f: Collector) -> None:
    if request.target.scope.kind is not ScopeKind.explicit_equilibrium_ensemble:
        return
    for m in decl.members:
        if m.counting_multiplicity is None:
            f.add("required_recipe_unknown:counting_multiplicity")
            break
    if any(m.relative_energy_kj_mol is None for m in decl.members) or decl.recipe.get("energy_zero") is None:
        f.add("relative_offsets_missing")
    if len(decl.members) == 1:
        f.unknown("single_member_ensemble")


def _check_ts(candidate: CandidateFacts, decl: DeclarationFacts | None, request: StatmechRequest, f: Collector) -> None:
    if decl is not None:
        surface = decl.recipe.get("dividing_surface")
        if surface is None:
            f.add("required_recipe_unknown:dividing_surface")
        elif surface == "variational":
            f.add("dividing_surface_not_supported")
        excluded = decl.recipe.get("reaction_coordinate_excluded")
        if excluded is None:
            f.add("required_recipe_unknown:reaction_coordinate_excluded")
        elif excluded is False:
            f.add("reaction_coordinate_not_excluded")


# ---------------------------------------------------------------------------------------------------------------------
# One candidate
# ---------------------------------------------------------------------------------------------------------------------


def _relevant_facts(profile: str) -> tuple[str, ...]:
    """The recipe facts a claim depends on. A fact the claim does not depend on is neither required nor 'unestablished'."""
    names: list[str] = []
    if profile in PROFILE_CONTRIBUTION:
        names += ["scale_application", "vibrational_scheme"]
    if profile != "zpe_component":
        names.append("isotope_masses")
    if profile in ("zpe_component", "canonical_model.full_partition"):
        names += ["energy_zero", "zpe_included_in_energies"]
    if profile in _NEEDS_ACCOUNTING:
        names += ["external_symmetry_state", "optical_state", "electronic_levels_state", "translation_included"]
    if profile == "canonical_model.full_partition":
        names.append("translational_normalization")
    if profile == "conventional_ts_transverse_model":
        names += ["dividing_surface", "reaction_coordinate_excluded"]
    return tuple(names)


def _normalize_recipe(candidate: CandidateFacts, f: Collector, profile: str) -> NormalizedRecipe:
    decl = candidate.declaration
    facts: list[RecipeFact] = []
    if decl is not None:
        r = decl.recipe
        for name in _relevant_facts(profile):
            value = r.get(name)
            facts.append(RecipeFact(name, "unknown" if value in (None, "unknown") else "known", None if value is None else str(value)))
    else:
        rec = candidate.record
        facts.append(RecipeFact("vibrational_scheme", "known" if rec.statmech_treatment else "unknown", rec.statmech_treatment))
        facts.append(RecipeFact("legacy_record", "known", "read_as_stored"))
    unestablished = tuple(sorted({x.name for x in facts if x.state == "unknown"}))
    blocking = any(r.applicability is not Applicability.applicable for r in f.reasons)
    key = None
    if decl is not None and not unestablished and not blocking:
        payload = {
            "normalizer": "1",
            "profile": profile,
            "recipe": decl.recipe,
            "sources": sorted((s.role, s.calculation_ref or s.literature_ref or (s.scale_factor.ref if s.scale_factor else ""), s.geometry_ref or "") for s in decl.sources if s.member_key is None),
        }
        key = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()
    return NormalizedRecipe(tuple(facts), unestablished, key)


def _legacy_checks(candidate: CandidateFacts, profile: str, f: Collector) -> None:
    legacy = candidate.legacy
    assert legacy is not None
    readiness, states = classify(profile, legacy)
    if readiness is Readiness.not_applicable:
        f.add("scope_incompatible")
        return
    for name, state in states.items():
        if state == MISSING:
            f.add(f"required_recipe_unknown:{name}")
        elif state == AMBIGUOUS:
            f.add(f"ambiguous_fact:{name}")
    if candidate.record.statmech_treatment in _ANHARMONIC_TREATMENTS:
        f.add("treatment_not_supported:anharmonic")
    for torsion in candidate.record.torsions:
        if torsion.treatment_kind == "hindered_rotor_dos":
            f.add("treatment_not_supported:density_of_states_rotor")
        if torsion.dimension > 1:
            f.add("treatment_not_supported:multidimensional_rotor")
    f.note("legacy_record_read_as_stored")


def assess_candidate(
    candidate: CandidateFacts,
    *,
    request: StatmechRequest,
    subject: SubjectFacts,
    calculations: Mapping[str, NormalizedCalculation],
    effective_statuses: frozenset[RecordReviewStatus],
) -> StatmechAssessment:
    """Assess one candidate against a supported request. Pure."""
    assert isinstance(request.task, Task)
    profile = profile_of(request)
    world = _World(calculations, effective_statuses)
    f = Collector()
    decl = candidate.declaration

    # 1. representation
    if decl is not None and decl.schema_version != 1:
        f.add("declaration_version_unsupported")
    # 2. scope
    _check_scope(candidate, request, subject, f)
    if candidate.supersession is not None:
        f.note("superseded_record")
    # 3. the owner record at its own floor
    if candidate.review_status not in effective_statuses:
        f.add("required_evidence_unavailable:record")
    needs_geometry = subject.is_atom is not True
    needs_curvature = profile in _NEEDS_CURVATURE and request.target.scope.kind is not ScopeKind.atom

    evidence: dict[str, tuple[str, ...]] = {}
    if decl is not None:
        _members_available(decl, effective_statuses, f)
        ev = _declared_evidence(candidate, world, f, needs_geometry=needs_geometry, needs_curvature=needs_curvature)
    else:
        _legacy_checks(candidate, profile, f)
        ev = _legacy_evidence(candidate, world, f)

    # 5. coherent role use
    pinned: str | None = None
    if needs_geometry:
        if len(ev.geometry_refs) == 1:
            pinned = next(iter(ev.geometry_refs))
        elif len(ev.geometry_refs) > 1:
            f.add("multiple_geometry_alternatives")
        elif "geometry" in ev.imported:
            f.note("imported_parameters_not_revalidated:geometry")
        elif decl is not None and not any(r.code.startswith("required_source_unusable:geometry") for r in f.reasons):
            f.add("required_evidence_unavailable:geometry")
    if request.target.scope.kind is ScopeKind.ts_determination and candidate.determination is not None:
        det = candidate.determination
        if det.review_status is None or det.review_status not in effective_statuses:
            f.add("required_evidence_unavailable:determination")
        elif pinned is None:
            pinned = det.evaluated_geometry_ref
        elif pinned != det.evaluated_geometry_ref:
            f.add("scope_incompatible")
    roles_used = frozenset({"curvature", "alternative_characterization", "energy", "geometry_optimization"})
    if needs_curvature and not ev.curvature and "curvature" in ev.imported:
        # Imported frequencies are supported parameters for the role, not a local job to fabricate; the stationary-point
        # character they were computed at is the source's own statement, and is disclosed as such.
        f.note("imported_parameters_not_revalidated:curvature")
    elif needs_curvature:
        evidence["curvature"] = _judge_curvature(ev, pinned, request, subject, f, roles_used=roles_used)
    for calc_ref in ev.geometry_sources:
        calc = world.calculations.get(calc_ref)
        if calc is not None:
            _apply_findings(calc, frozenset({"geometry_optimization"}), f)
    if ev.geometry_sources:
        evidence["geometry"] = tuple(sorted(ev.geometry_sources))
    if ev.energy:
        evidence["absolute_energy"] = tuple(sorted(ev.energy))
    if ev.parameters:
        evidence["parameters"] = tuple(sorted(ev.parameters))
    if any(c.lineage_cyclic for c in ev.curvature):
        f.add("dependency_cycle")

    # 6. the TS owner's own treatment
    if request.target.scope.kind is ScopeKind.ts_determination:
        _check_ts(candidate, decl, request, f)
        stated = decl.recipe.get("reaction_coordinate_mode_index") if decl is not None else None
        for witness in ev.curvature:
            designated = witness.curvature.reaction_coordinate_mode_index
            if stated is not None and designated is not None and stated != designated and witness.calculation_ref in evidence.get("curvature", ()):
                f.add("reaction_coordinate_mismatch")
        if decl is None and candidate.legacy is not None and candidate.legacy.reaction_coordinate_designated is False:
            f.add("required_recipe_unknown:reaction_coordinate")

    # 7. the declared recipe's accounting
    if decl is not None:
        if profile != "zpe_component":
            _check_isotope(decl, request, f)
        if profile in PROFILE_CONTRIBUTION:
            _check_scale(decl, profile, ev, f)
        _check_energy_reference(decl, request, profile, f)
        _check_translation(decl, request, profile, f)
        _check_domain(decl, request, f)
        if profile in _NEEDS_ACCOUNTING or profile == "vibrational_component":
            _check_rotors(candidate, decl, world, f)
        if profile in _NEEDS_ACCOUNTING:
            _check_counting(candidate, decl, subject, f)
        _check_ensemble(decl, request, profile, f)
        if request.target.electronic_scope is ElectronicScope.declared_levels and not candidate.record.electronic_levels:
            f.add("required_recipe_unknown:electronic_levels")
    else:
        if request.target.electronic_scope is ElectronicScope.declared_levels and not candidate.record.electronic_levels:
            f.add("required_recipe_unknown:electronic_levels")
        f.unknown("cohort_not_established")

    recipe = _normalize_recipe(candidate, f, profile)
    if recipe.cohort_key is None:
        f.unknown("cohort_not_established")
    for observable_key, refs in tuple(evidence.items()):
        if not refs:
            del evidence[observable_key]
    return StatmechAssessment(
        candidate_ref=candidate.candidate_ref,
        kind=candidate.kind,
        applicability=f.verdict(),
        reasons=tuple(f.reasons),
        advisory=tuple(f.advisory),
        comparative_unknown=tuple(f.comparative_unknown),
        evidence=evidence,
        recipe=recipe,
        supersession=candidate.supersession,
    )


# ---------------------------------------------------------------------------------------------------------------------
# The population
# ---------------------------------------------------------------------------------------------------------------------

_CATEGORY_PRECEDENCE = (
    Category.evidence_conflict,
    Category.unsupported_model,
    Category.missing_required_data,
    Category.unresolved_applicability,
)


@dataclass(frozen=True)
class QualificationSummary:
    """The population-level answer of a qualification, before any preference.

    ``outcome`` is one of the qualification outcomes (``qualified_models``, ``no_candidates``,
    ``no_applicable_candidate``, ``unresolved_applicability``, ``missing_required_data``, ``unsupported_task``,
    ``unsupported_model``, ``evidence_conflict``). Preference outcomes are the decision stage's.
    """

    outcome: str
    basis: str
    qualified_refs: tuple[str, ...]
    undecided_refs: tuple[str, ...]
    notes: tuple[str, ...]
    request_reasons: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "outcome": self.outcome,
            "basis": self.basis,
            "qualified_refs": list(self.qualified_refs),
            "undecided_refs": list(self.undecided_refs),
            "notes": list(self.notes),
            "request_reasons": list(self.request_reasons),
        }


def summarize(request: StatmechRequest, assessments: Sequence[StatmechAssessment]) -> QualificationSummary:
    """Deterministic summary precedence: unsupported request, no candidates, qualified or the reason none qualifies.

    Every per-candidate reason stays on the assessment; this reads categories only. With qualifying candidates and
    undecided ones, a request for the entire requested domain is not certified: the relevant unresolved outcome is
    returned and the qualified subset stays visible.
    """
    unsupported = request_support(request)
    if unsupported:
        return QualificationSummary(
            "unsupported_task",
            "this release does not evaluate the requested task or conditions; no other model is substituted for it",
            (),
            (),
            (),
            unsupported,
        )
    if not assessments:
        return QualificationSummary("no_candidates", "no authorized candidate matches this target", (), (), (), ())
    qualified = tuple(a.candidate_ref for a in assessments if a.eligible)
    undecided = tuple(a.candidate_ref for a in assessments if a.applicability in (Applicability.unresolved, Applicability.unsupported))
    pool = [a for a in assessments if not a.eligible]

    def category_outcome(group: Sequence[StatmechAssessment]) -> str:
        cats = set().union(*(a.categories for a in group)) if group else set()
        if Category.evidence_conflict in cats:
            return "evidence_conflict"
        # A decided incompatibility is a negative answer, not an undecided one; the undecided candidates are what the
        # category speaks about.
        open_ = [a for a in group if a.categories - {Category.known_incompatible}]
        if open_ and all(a.categories - {Category.known_incompatible} == {Category.unsupported_model} for a in open_):
            return "unsupported_model"
        decidable = cats - {Category.known_incompatible}
        if decidable == {Category.missing_required_data}:
            return "missing_required_data"
        if decidable:
            return "unresolved_applicability"
        return "no_applicable_candidate"

    if not qualified:
        outcome = category_outcome(pool)
        return QualificationSummary(
            outcome, f"no candidate qualifies: {outcome.replace('_', ' ')}", (), undecided, (), ()
        )
    notes: list[str] = []
    if undecided:
        notes.append(
            f"{len(undecided)} candidate(s) are unresolved or unsupported; they do not qualify here and might if their "
            "missing facts were stated or their form evaluated"
        )
        if request.coverage.value == "entire_requested_domain":
            undecided_group = [a for a in assessments if a.candidate_ref in set(undecided)]
            outcome = category_outcome(undecided_group)
            return QualificationSummary(
                outcome,
                "the entire requested domain is not decided: undecided candidates remain; the qualified subset is listed "
                "and nothing is certified for the whole population",
                qualified,
                undecided,
                tuple(notes),
                (),
            )
    return QualificationSummary(
        "qualified_models",
        "the listed models support the requested claim; the answer is scoped to the authorized, eligible population",
        qualified,
        undecided,
        tuple(notes),
        (),
    )
