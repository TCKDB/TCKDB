"""The closed vocabulary of a statmech selection request and its answer.

Every token a caller may send, and every outcome the service may return, is named here once. A token this module
does not know is an invalid request; a *deferred* token is recognised and answered with a scientific
``unsupported_task`` outcome (never substituted by a model the caller did not ask about).
"""

from __future__ import annotations

from enum import Enum

#: Version of the request/answer vocabulary and its cross-field rules. Version 2: the comparison objective, the
#: reference model and the outcomes only a method rule could reach were removed.
CONTRACT_VERSION = 2


class Task(str, Enum):
    """The supported claims. Each qualifies a *supplied* model; none computes a number."""

    rotational_component = "rotational_component"
    vibrational_component = "vibrational_component"
    zpe_component = "zpe_component"
    within_basin_thermal = "within_basin_thermal"
    canonical_model = "canonical_model"
    conventional_ts_transverse_model = "conventional_ts_transverse_model"


class DeferredTask(str, Enum):
    """Recognised tasks this release does not evaluate. They validate, then answer ``unsupported_task``."""

    density_of_states = "density_of_states"
    sum_of_states = "sum_of_states"
    higher_order_ts_model = "higher_order_ts_model"
    prepared_population = "prepared_population"
    trapped_population = "trapped_population"
    thermochemical_formation = "thermochemical_formation"
    numerical_evaluation = "numerical_evaluation"
    numerical_construction = "numerical_construction"


class Intent(str, Enum):
    qualify = "qualify"
    protocol_preferred = "protocol_preferred"


class Observable(str, Enum):
    """What the request is about. The first group describes a qualification, not an evaluated number."""

    rotational_characterization = "rotational_characterization"
    vibrational_treatment = "vibrational_treatment"
    zero_point_energy_interpretation = "zero_point_energy_interpretation"
    entropy = "entropy"
    heat_capacity = "heat_capacity"
    internal_partition = "internal_partition"
    full_partition = "full_partition"
    transverse_partition = "transverse_partition"
    # Recognised, answered only for deferred tasks.
    density_of_states = "density_of_states"
    sum_of_states = "sum_of_states"
    enthalpy = "enthalpy"
    gibbs_energy = "gibbs_energy"
    helmholtz_energy = "helmholtz_energy"
    formation_enthalpy = "formation_enthalpy"
    population = "population"


#: The one observable each component task qualifies.
COMPONENT_OBSERVABLE: dict[Task, Observable] = {
    Task.rotational_component: Observable.rotational_characterization,
    Task.vibrational_component: Observable.vibrational_treatment,
    Task.zpe_component: Observable.zero_point_energy_interpretation,
}
#: Observables a recognised deferred task accepts (it may also state none). A pairing outside these is invalid.
DEFERRED_TASK_OBSERVABLES: dict[DeferredTask, frozenset[Observable]] = {
    DeferredTask.density_of_states: frozenset({Observable.density_of_states}),
    DeferredTask.sum_of_states: frozenset({Observable.sum_of_states}),
    DeferredTask.higher_order_ts_model: frozenset({Observable.transverse_partition}),
    DeferredTask.prepared_population: frozenset({Observable.population}),
    DeferredTask.trapped_population: frozenset({Observable.population}),
    DeferredTask.thermochemical_formation: frozenset(
        {Observable.enthalpy, Observable.gibbs_energy, Observable.formation_enthalpy}
    ),
    DeferredTask.numerical_evaluation: frozenset(
        {
            Observable.entropy,
            Observable.heat_capacity,
            Observable.enthalpy,
            Observable.gibbs_energy,
            Observable.helmholtz_energy,
            Observable.internal_partition,
            Observable.full_partition,
        }
    ),
    DeferredTask.numerical_construction: frozenset(
        {
            Observable.entropy,
            Observable.heat_capacity,
            Observable.enthalpy,
            Observable.gibbs_energy,
            Observable.helmholtz_energy,
            Observable.formation_enthalpy,
        }
    ),
}
#: Observables a supported task accepts.
TASK_OBSERVABLES: dict[Task, frozenset[Observable]] = {
    Task.rotational_component: frozenset({Observable.rotational_characterization}),
    Task.vibrational_component: frozenset({Observable.vibrational_treatment}),
    Task.zpe_component: frozenset({Observable.zero_point_energy_interpretation}),
    Task.within_basin_thermal: frozenset({Observable.entropy, Observable.heat_capacity}),
    Task.canonical_model: frozenset({Observable.internal_partition, Observable.full_partition}),
    Task.conventional_ts_transverse_model: frozenset({Observable.transverse_partition}),
}
#: Tasks whose claim is thermal: they need a finite, ordered temperature domain.
THERMAL_TASKS = frozenset(
    {Task.within_basin_thermal, Task.canonical_model, Task.conventional_ts_transverse_model}
)


class ScopeKind(str, Enum):
    atom = "atom"
    basin = "basin"
    explicit_equilibrium_ensemble = "explicit_equilibrium_ensemble"
    ts_determination = "ts_determination"


class OwnerKind(str, Enum):
    species_entry = "species_entry"
    transition_state_entry = "transition_state_entry"


#: Scopes each task accepts. Components may also pin a TS surface.
TASK_SCOPES: dict[Task, frozenset[ScopeKind]] = {
    Task.rotational_component: frozenset({ScopeKind.atom, ScopeKind.basin, ScopeKind.ts_determination}),
    Task.vibrational_component: frozenset({ScopeKind.basin, ScopeKind.ts_determination}),
    Task.zpe_component: frozenset({ScopeKind.atom, ScopeKind.basin, ScopeKind.ts_determination}),
    Task.within_basin_thermal: frozenset({ScopeKind.atom, ScopeKind.basin}),
    Task.canonical_model: frozenset({ScopeKind.atom, ScopeKind.basin, ScopeKind.explicit_equilibrium_ensemble}),
    Task.conventional_ts_transverse_model: frozenset({ScopeKind.ts_determination}),
}
#: Scopes by owner kind: a TS entry has a determination, never a basin, an atom or an ensemble.
OWNER_SCOPES: dict[OwnerKind, frozenset[ScopeKind]] = {
    OwnerKind.species_entry: frozenset({ScopeKind.atom, ScopeKind.basin, ScopeKind.explicit_equilibrium_ensemble}),
    OwnerKind.transition_state_entry: frozenset({ScopeKind.ts_determination}),
}


class IsotopeConvention(str, Enum):
    entry_documented_masses = "entry_documented_masses"
    #: Recognised; not evaluated (``isotope_convention_unsupported``).
    natural_abundance_average = "natural_abundance_average"


class ElectronicScope(str, Enum):
    entry_state = "entry_state"
    declared_levels = "declared_levels"


class StereochemicalScope(str, Enum):
    entry_identity = "entry_identity"
    declared_stereoisomer_set = "declared_stereoisomer_set"


class DividingSurface(str, Enum):
    conventional_saddle_point = "conventional_saddle_point"
    #: Recognised; not evaluated.
    variational = "variational"


class EnvironmentKind(str, Enum):
    gas = "gas"
    #: Recognised; not evaluated.
    solution = "solution"
    surface = "surface"
    condensed = "condensed"


class Normalization(str, Enum):
    internal_only = "internal_only"
    fixed_volume = "fixed_volume"
    standard_state_pressure = "standard_state_pressure"
    standard_state_concentration = "standard_state_concentration"


class EnergyZero(str, Enum):
    electronic = "electronic"
    e0 = "e0"


class ZpeInclusion(str, Enum):
    included = "included"
    excluded = "excluded"


class PopulationKind(str, Enum):
    authorized_matching_models = "authorized_matching_models"
    model_revisions = "model_revisions"
    release = "release"


class CoverageRequirement(str, Enum):
    """``entire_requested_domain`` is a complete claim and refuses when coverage is missing; ``eligible_population``
    scopes the answer to what is eligible and says so."""

    entire_requested_domain = "entire_requested_domain"
    eligible_population = "eligible_population"


class ResultMode(str, Enum):
    all = "all"
    first = "first"


class CandidateKind(str, Enum):
    """How a candidate is represented: a declared model revision, or a legacy statmech record read as it is."""

    declared_model = "declared_model"
    legacy_record = "legacy_record"


class Outcome(str, Enum):
    """What a selection found. A negative scientific outcome is a valid answer (HTTP 200), not an error."""

    qualified_models = "qualified_models"
    no_candidates = "no_candidates"
    no_applicable_candidate = "no_applicable_candidate"
    unresolved_applicability = "unresolved_applicability"
    missing_required_data = "missing_required_data"
    unsupported_task = "unsupported_task"
    unsupported_model = "unsupported_model"
    evidence_conflict = "evidence_conflict"
    sole_eligible_candidate = "sole_eligible_candidate"
    incomparable_alternatives = "incomparable_alternatives"


#: Summary precedence when several categories coexist (first match wins; every per-candidate reason is kept).
OUTCOME_PRECEDENCE: tuple[str, ...] = (
    "bounded_refusal",
    "unsupported_request",
    "no_candidates",
    "none_qualified: evidence_conflict, unsupported_model, missing_required_data, unresolved_applicability, "
    "no_applicable_candidate",
    "listing: sole_eligible_candidate, incomparable_alternatives",
)

#: The machine code of a bounded-completeness refusal (HTTP 422). One code; the context names the bound.
CODE_BOUND_EXCEEDED = "statmech_selection_bound_exceeded"

#: Reason codes the service emits for a request-level or candidate-level finding (a closed list: a new one is a
#: contract revision).
REASON_CODES: tuple[str, ...] = (
    "required_recipe_unknown",
    "scope_incompatible",
    "required_source_unusable",
    "counting_conflict",
    "required_member_unavailable",
    "required_evidence_unavailable",
    "isotope_convention_unsupported",
    "environment_not_supported",
    "dividing_surface_not_supported",
    "declaration_version_unsupported",
    "treatment_not_supported",
)
