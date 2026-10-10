"""Plain values for statmech selection: the normalised request, its target, conditions and bounds.

Everything here is data over public refs and fixed-unit numbers; there is no database id and no free-text unit.
A request states what it asks and a service never fills in what it leaves out: an omitted optional condition is
absent, not a default, except the two documented echoes (a component task's own observable and an internal
model's ``internal_only`` normalisation), which are written into the normalised request so the request and its
echo are the same value.

Versions. ``ASSESSMENT_VERSION`` is the semantics of applicability, ``NORMALIZER_VERSION`` the semantics of recipe
normalisation, ``DECISION_VERSION`` the decision procedure and ``ADMIN_KEY_VERSION`` the administrative order. A
manifest records the versions it was made under; a change to any one is a new version.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from app.db.models.common import RecordReviewStatus
from app.schemas.reads.scientific_common import SelectionPolicy
from app.services.statmech_selection.vocabulary import (
    COMPONENT_OBSERVABLE,
    OWNER_SCOPES,
    TASK_OBSERVABLES,
    TASK_SCOPES,
    THERMAL_TASKS,
    CoverageRequirement,
    DeferredTask,
    DividingSurface,
    ElectronicScope,
    EnergyZero,
    EnvironmentKind,
    Intent,
    IsotopeConvention,
    Normalization,
    Objective,
    Observable,
    OwnerKind,
    PopulationKind,
    ResultMode,
    ScopeKind,
    StereochemicalScope,
    Task,
    ZpeInclusion,
)

ASSESSMENT_VERSION = "1"
NORMALIZER_VERSION = "1"
DECISION_VERSION = "1"
ADMIN_KEY_VERSION = "1"
#: The finding semantics this release reads (shared with structure selection).
SUPPORTED_FINDING_VERSIONS = frozenset({1})


@dataclass(frozen=True)
class SelectionBounds:
    """The versioned engineering limits of a statmech selection.

    Resource limits, not scientific tolerances. Every count is over the *complete authorized population*, so
    exceeding a bound refuses the answer outright; there is never a decision from a prefix. A caller cannot set a
    bound: the schema has no field for one. Raising a limit is a documented revision of ``version``.

    :param candidates: Candidate model revisions and legacy records.
    :param nested_rows: Necessary nested fact rows (members, sources, modes, levels, torsions, scan/potential
        points, findings, rules).
    :param dependency_depth: Typed-dependency hops followed from a source.
    :param manifest_bytes: Canonical normalised manifest payload.
    """

    version: str = "1"
    candidates: int = 500
    nested_rows: int = 25_000
    dependency_depth: int = 8
    manifest_bytes: int = 10 * 1024 * 1024

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> SelectionBounds:
        return cls(**raw)


def _finite_positive(value: float | None, name: str) -> None:
    if value is not None and (not math.isfinite(value) or value <= 0):
        raise ValueError(f"{name} must be a finite positive number")


def _domain(value: tuple[float, float] | None, name: str, *, positive: bool) -> None:
    if value is None:
        return
    low, high = value
    if not (math.isfinite(low) and math.isfinite(high)):
        raise ValueError(f"{name} must be finite")
    if positive and low <= 0:
        raise ValueError(f"{name} must be positive")
    if low > high:
        raise ValueError(f"{name} must be ordered (min <= max)")


@dataclass(frozen=True)
class Scope:
    """The grain a request is about, with the one public ref that pins it where it needs one."""

    kind: ScopeKind
    conformer_group_ref: str | None = None
    ensemble_declaration_ref: str | None = None
    determination_ref: str | None = None
    dividing_surface: DividingSurface | None = None
    reaction_coordinate_excluded: bool | None = None

    def __post_init__(self) -> None:
        needs = {
            ScopeKind.basin: ("conformer_group_ref", self.conformer_group_ref),
            ScopeKind.explicit_equilibrium_ensemble: ("ensemble_declaration_ref", self.ensemble_declaration_ref),
            ScopeKind.ts_determination: ("determination_ref", self.determination_ref),
        }
        given = {
            "conformer_group_ref": self.conformer_group_ref,
            "ensemble_declaration_ref": self.ensemble_declaration_ref,
            "determination_ref": self.determination_ref,
        }
        wanted = needs.get(self.kind)
        for name, value in given.items():
            if value is not None and (wanted is None or wanted[0] != name):
                raise ValueError(f"{name} does not belong to a {self.kind.value} scope")
        if wanted is not None and wanted[1] is None:
            if self.kind is ScopeKind.explicit_equilibrium_ensemble:
                raise ValueError(
                    "an explicit equilibrium ensemble needs the immutable membership declaration "
                    "(ensemble_declaration_ref); a filtered conformer context is not membership"
                )
            raise ValueError(f"a {self.kind.value} scope needs {wanted[0]}")
        if self.kind is not ScopeKind.ts_determination and (
            self.dividing_surface is not None or self.reaction_coordinate_excluded is not None
        ):
            raise ValueError("dividing_surface and reaction_coordinate_excluded belong to a ts_determination scope")

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "conformer_group_ref": self.conformer_group_ref,
            "ensemble_declaration_ref": self.ensemble_declaration_ref,
            "determination_ref": self.determination_ref,
            "dividing_surface": self.dividing_surface.value if self.dividing_surface else None,
            "reaction_coordinate_excluded": self.reaction_coordinate_excluded,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> Scope:
        return cls(
            kind=ScopeKind(raw["kind"]),
            conformer_group_ref=raw["conformer_group_ref"],
            ensemble_declaration_ref=raw["ensemble_declaration_ref"],
            determination_ref=raw["determination_ref"],
            dividing_surface=DividingSurface(raw["dividing_surface"]) if raw["dividing_surface"] else None,
            reaction_coordinate_excluded=raw["reaction_coordinate_excluded"],
        )


@dataclass(frozen=True)
class Target:
    """The physical subject: one species entry or one TS entry, its scope and its conventions."""

    owner_kind: OwnerKind
    owner_ref: str
    scope: Scope
    isotope_convention: IsotopeConvention
    electronic_scope: ElectronicScope
    stereochemical_scope: StereochemicalScope

    def __post_init__(self) -> None:
        if self.scope.kind not in OWNER_SCOPES[self.owner_kind]:
            allowed = ", ".join(sorted(s.value for s in OWNER_SCOPES[self.owner_kind]))
            raise ValueError(
                f"a {self.owner_kind.value} target takes a {allowed} scope, not {self.scope.kind.value}"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.owner_kind.value,
            "owner_ref": self.owner_ref,
            "scope": self.scope.to_dict(),
            "isotope_convention": self.isotope_convention.value,
            "electronic_scope": self.electronic_scope.value,
            "stereochemical_scope": self.stereochemical_scope.value,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> Target:
        return cls(
            owner_kind=OwnerKind(raw["kind"]),
            owner_ref=raw["owner_ref"],
            scope=Scope.from_dict(raw["scope"]),
            isotope_convention=IsotopeConvention(raw["isotope_convention"]),
            electronic_scope=ElectronicScope(raw["electronic_scope"]),
            stereochemical_scope=StereochemicalScope(raw["stereochemical_scope"]),
        )


@dataclass(frozen=True)
class EnergyReference:
    """Which zero the energies are measured from, and whether the zero-point energy is in them."""

    zero: EnergyZero
    zpe: ZpeInclusion

    def to_dict(self) -> dict[str, str]:
        return {"zero": self.zero.value, "zpe": self.zpe.value}

    @classmethod
    def from_dict(cls, raw: dict[str, str]) -> EnergyReference:
        return cls(EnergyZero(raw["zero"]), ZpeInclusion(raw["zpe"]))


@dataclass(frozen=True)
class Conditions:
    """Conditions that affect the claim. Fixed units only: kelvin, pascal, cubic metre, mol per cubic metre, kJ/mol."""

    temperature_domain_k: tuple[float, float] | None = None
    energy_domain_kj_mol: tuple[float, float] | None = None
    energy_reference: EnergyReference | None = None
    environment: EnvironmentKind = EnvironmentKind.gas
    normalization: Normalization | None = None
    pressure_pa: float | None = None
    concentration_mol_m3: float | None = None
    volume_m3: float | None = None

    def __post_init__(self) -> None:
        _domain(self.temperature_domain_k, "temperature_domain_k", positive=True)
        _domain(self.energy_domain_kj_mol, "energy_domain_kj_mol", positive=False)
        for name in ("pressure_pa", "concentration_mol_m3", "volume_m3"):
            _finite_positive(getattr(self, name), name)
        if self.energy_domain_kj_mol is not None and self.energy_reference is None:
            raise ValueError("an energy_domain_kj_mol states its energy_reference (zero and zpe inclusion)")
        supplied = {
            Normalization.fixed_volume: ("volume_m3", self.volume_m3),
            Normalization.standard_state_pressure: ("pressure_pa", self.pressure_pa),
            Normalization.standard_state_concentration: ("concentration_mol_m3", self.concentration_mol_m3),
        }
        given = {
            "volume_m3": self.volume_m3,
            "pressure_pa": self.pressure_pa,
            "concentration_mol_m3": self.concentration_mol_m3,
        }
        if self.normalization is None:
            extra = [n for n, v in given.items() if v is not None]
            if extra:
                raise ValueError(f"{extra[0]} needs the normalization it belongs to")
            return
        wanted = supplied.get(self.normalization)
        for name, value in given.items():
            if value is not None and (wanted is None or wanted[0] != name):
                raise ValueError(f"{name} does not belong to normalization {self.normalization.value}")
        if wanted is not None and wanted[1] is None:
            raise ValueError(f"normalization {self.normalization.value} needs {wanted[0]}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "temperature_domain_k": list(self.temperature_domain_k) if self.temperature_domain_k else None,
            "energy_domain_kj_mol": list(self.energy_domain_kj_mol) if self.energy_domain_kj_mol else None,
            "energy_reference": self.energy_reference.to_dict() if self.energy_reference else None,
            "environment": self.environment.value,
            "normalization": self.normalization.value if self.normalization else None,
            "pressure_pa": self.pressure_pa,
            "concentration_mol_m3": self.concentration_mol_m3,
            "volume_m3": self.volume_m3,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> Conditions:
        def pair(v: Any) -> tuple[float, float] | None:
            return (float(v[0]), float(v[1])) if v else None

        return cls(
            temperature_domain_k=pair(raw["temperature_domain_k"]),
            energy_domain_kj_mol=pair(raw["energy_domain_kj_mol"]),
            energy_reference=EnergyReference.from_dict(raw["energy_reference"]) if raw["energy_reference"] else None,
            environment=EnvironmentKind(raw["environment"]),
            normalization=Normalization(raw["normalization"]) if raw["normalization"] else None,
            pressure_pa=raw["pressure_pa"],
            concentration_mol_m3=raw["concentration_mol_m3"],
            volume_m3=raw["volume_m3"],
        )


@dataclass(frozen=True)
class Population:
    """Which candidate models compete. Never a page prefix."""

    kind: PopulationKind = PopulationKind.authorized_matching_models
    model_refs: tuple[str, ...] = ()
    release_ref: str | None = None

    def __post_init__(self) -> None:
        if self.kind is PopulationKind.model_revisions:
            if not self.model_refs:
                raise ValueError("a model_revisions population names at least one model revision ref")
            if len(set(self.model_refs)) != len(self.model_refs):
                raise ValueError("a model revision ref is named twice")
        elif self.model_refs:
            raise ValueError("model_refs belongs to a model_revisions population")
        if (self.kind is PopulationKind.release) != (self.release_ref is not None):
            raise ValueError("release_ref belongs to (and a release population needs) a release_ref")

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind.value, "model_refs": list(self.model_refs), "release_ref": self.release_ref}

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> Population:
        return cls(PopulationKind(raw["kind"]), tuple(raw["model_refs"]), raw["release_ref"])


@dataclass(frozen=True)
class StatmechRequest:
    """The normalised request. Construction validates every cross-field rule; a bad combination raises ``ValueError``."""

    task: Task | DeferredTask
    intent: Intent
    target: Target
    observable: Observable | None = None
    conditions: Conditions = field(default_factory=Conditions)
    population: Population = field(default_factory=Population)
    coverage: CoverageRequirement = CoverageRequirement.entire_requested_domain
    objective: Objective | None = None
    reference_model_ref: str | None = None
    admin_policy: SelectionPolicy = SelectionPolicy.default
    result_mode: ResultMode = ResultMode.all
    min_review_status: RecordReviewStatus | None = None
    bounds: SelectionBounds = field(default_factory=SelectionBounds)

    # -- derived ---------------------------------------------------------------------------------------------
    @property
    def deferred(self) -> bool:
        return isinstance(self.task, DeferredTask)

    def __post_init__(self) -> None:
        self._check_intent()
        if isinstance(self.task, DeferredTask):
            self._check_deferred()
        else:
            self._check_supported(self.task)

    def _check_intent(self) -> None:
        if self.intent is Intent.qualify:
            if self.objective is not None or self.reference_model_ref is not None:
                raise ValueError("a qualify intent compares nothing: omit comparison_objective and reference_model_ref")
            if self.result_mode is ResultMode.first:
                raise ValueError("a qualify intent returns assessments and no selection: result_mode first is contradictory")
            return
        if self.objective is None:
            raise ValueError("protocol_preferred states its comparison_objective")
        if self.objective is Objective.model_fidelity and not self.reference_model_ref:
            raise ValueError("a model_fidelity objective names the pinned reference_model_ref it is measured against")
        if self.objective is not Objective.model_fidelity and self.reference_model_ref is not None:
            raise ValueError("reference_model_ref belongs to a model_fidelity objective")

    def _check_deferred(self) -> None:
        if self.task is DeferredTask.higher_order_ts_model and self.target.owner_kind is not OwnerKind.transition_state_entry:
            raise ValueError("higher_order_ts_model needs a transition_state_entry target")

    def _check_supported(self, task: Task) -> None:
        allowed_scopes = TASK_SCOPES[task]
        if self.target.scope.kind not in allowed_scopes:
            allowed = ", ".join(sorted(s.value for s in allowed_scopes))
            raise ValueError(f"task {task.value} takes a {allowed} scope, not {self.target.scope.kind.value}")
        observable = self.observable
        if observable is None:
            if task in COMPONENT_OBSERVABLE:
                observable = COMPONENT_OBSERVABLE[task]
                object.__setattr__(self, "observable", observable)
            else:
                raise ValueError(f"task {task.value} states its observable")
        if observable not in TASK_OBSERVABLES[task]:
            allowed = ", ".join(sorted(o.value for o in TASK_OBSERVABLES[task]))
            raise ValueError(f"task {task.value} accepts observable {allowed}, not {observable.value}")
        if task is Task.conventional_ts_transverse_model and self.target.owner_kind is not OwnerKind.transition_state_entry:
            raise ValueError("conventional_ts_transverse_model needs a transition_state_entry target")
        scope = self.target.scope
        if task is Task.conventional_ts_transverse_model:
            if scope.dividing_surface is None or scope.reaction_coordinate_excluded is not True:
                raise ValueError(
                    "a conventional TS transverse request states its dividing_surface and that the reaction "
                    "coordinate is excluded (reaction_coordinate_excluded true)"
                )
        c = self.conditions
        if c.energy_domain_kj_mol is not None:
            raise ValueError("energy_domain_kj_mol belongs to the deferred energy-resolved tasks")
        if task in THERMAL_TASKS and c.temperature_domain_k is None:
            raise ValueError(f"task {task.value} is thermal: state a finite, positive, ordered temperature_domain_k")
        normalization = c.normalization
        if observable is Observable.full_partition:
            if normalization is None or normalization is Normalization.internal_only:
                raise ValueError(
                    "a full partition model states its translational normalization: fixed_volume with volume_m3, "
                    "or a standard state with pressure_pa or concentration_mol_m3"
                )
            if c.energy_reference is None:
                raise ValueError("a full partition model states its energy_reference (zero and zpe inclusion)")
            return
        if task in COMPONENT_OBSERVABLE:
            if normalization is not None:
                raise ValueError(f"task {task.value} is a component claim: a normalization does not affect it, omit it")
            if task is Task.zpe_component and c.energy_reference is None:
                raise ValueError("a ZPE interpretation states its energy_reference (zero and zpe inclusion)")
            return
        if task is Task.within_basin_thermal and normalization not in (None, Normalization.internal_only):
            if c.energy_reference is None:
                raise ValueError("a within-basin thermal request with a translational normalization states its energy_reference")
            return
        if normalization is None:
            object.__setattr__(self, "conditions", _with_internal(c))
        elif normalization is not Normalization.internal_only:
            raise ValueError(f"observable {observable.value} is an internal contribution: normalization is internal_only")

    # -- wire ------------------------------------------------------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        return {
            "contract_version": "statmech-selection-v1",
            "task": self.task.value,
            "intent": self.intent.value,
            "observable": self.observable.value if self.observable else None,
            "target": self.target.to_dict(),
            "conditions": self.conditions.to_dict(),
            "population": self.population.to_dict(),
            "coverage_requirement": self.coverage.value,
            "comparison_objective": self.objective.value if self.objective else None,
            "reference_model_ref": self.reference_model_ref,
            "administrative_policy": self.admin_policy.value,
            "result_mode": self.result_mode.value,
            "min_review_status": self.min_review_status.value if self.min_review_status else None,
            "bounds": self.bounds.to_dict(),
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> StatmechRequest:
        task_raw = raw["task"]
        task: Task | DeferredTask = Task(task_raw) if task_raw in {t.value for t in Task} else DeferredTask(task_raw)
        return cls(
            task=task,
            intent=Intent(raw["intent"]),
            target=Target.from_dict(raw["target"]),
            observable=Observable(raw["observable"]) if raw["observable"] else None,
            conditions=Conditions.from_dict(raw["conditions"]),
            population=Population.from_dict(raw["population"]),
            coverage=CoverageRequirement(raw["coverage_requirement"]),
            objective=Objective(raw["comparison_objective"]) if raw["comparison_objective"] else None,
            reference_model_ref=raw["reference_model_ref"],
            admin_policy=SelectionPolicy(raw["administrative_policy"]),
            result_mode=ResultMode(raw["result_mode"]),
            min_review_status=RecordReviewStatus(raw["min_review_status"]) if raw["min_review_status"] else None,
            bounds=SelectionBounds.from_dict(raw["bounds"]),
        )


def _with_internal(conditions: Conditions) -> Conditions:
    """The documented echo: an internal contribution is normalised ``internal_only``, and says so."""
    return Conditions(
        temperature_domain_k=conditions.temperature_domain_k,
        energy_domain_kj_mol=conditions.energy_domain_kj_mol,
        energy_reference=conditions.energy_reference,
        environment=conditions.environment,
        normalization=Normalization.internal_only,
    )
