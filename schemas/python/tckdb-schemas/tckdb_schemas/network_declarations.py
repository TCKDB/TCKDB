"""Scientific declarations a pressure-dependent network solve and its fits can carry.

Optional, attributed claims a depositor can make that the network tables could not state:

* **target** (on the solve) -- what the solve's outputs are outputs *of*: partition, boundary
  semantics, regime, physical validity domain, bath scope, the output catalog and the declared
  product sets;
* **protocol** (on the solve) -- the coupled recipe actually used, in the small vocabulary a later
  comparison needs;
* **validation** (on the solve) -- typed evidence the solve cites, stored as *declared*;
* **determination**, **representation** (on a fit) -- which complete determination of a channel's
  coefficient the fit is one representation of, what that coefficient is, and the fit's own key.

All are *claims*, stored as made, never inferred and never backfilled: ``null`` is "not stated",
never "valid everywhere" and never "standard". The vocabulary starts small and grows when a real
deposit needs a value; adding an enum member or an optional field is additive within version 1.
Accepted versions: :data:`NETWORK_DECLARATION_VERSIONS`.

States are named by local key in an upload and by composition hash once stored; determinations by
local key in an upload and by public ref once stored. The existing columns (bath gas, state
energies, grain settings, rate units) stay authoritative: a declaration that contradicts one is
refused, never reconciled.
"""

from __future__ import annotations

import hashlib
import json
import re
from enum import Enum
from typing import Annotated, Any

from pydantic import Field, StrictInt, StringConstraints, field_validator, model_validator

from tckdb_schemas.coded_error import CodedValidationError
from tckdb_schemas.common import SchemaBase
from tckdb_schemas.enums import NetworkRepresentationRole

__all__ = [
    "NETWORK_DECLARATION_VERSIONS",
    "NETWORK_PRODUCT_SET_MEMBERSHIP_VERSION",
    "W_NETWORK_DECLARATION_INVALID",
    "W_NETWORK_DECLARATION_VERSION_UNSUPPORTED",
    "NetworkBarrierBasis",
    "NetworkBathScope",
    "NetworkBoundaryKind",
    "NetworkClaimOrigin",
    "NetworkCoefficientBasis",
    "NetworkCollisionFrequencyModel",
    "NetworkComparisonObjective",
    "NetworkConformerTreatment",
    "NetworkDeterminationDeclaration",
    "NetworkFitOrigin",
    "NetworkLump",
    "NetworkMicrocanonicalTreatment",
    "NetworkObservable",
    "NetworkObservableDeclaration",
    "NetworkOutputAvailability",
    "NetworkOutputEntry",
    "NetworkPartition",
    "NetworkProductSet",
    "NetworkProductSetMember",
    "NetworkProtocolDeclaration",
    "NetworkReductionMethod",
    "NetworkRegime",
    "NetworkRegimeKind",
    "NetworkRepresentationDeclaration",
    "NetworkRepresentationRole",
    "NetworkRotorTreatment",
    "NetworkStateBoundary",
    "NetworkTargetDeclaration",
    "NetworkTunnelingTreatment",
    "NetworkValidationDeclaration",
    "NetworkValidationEntry",
    "NetworkValidationKind",
    "NetworkValidationMetric",
    "NetworkValidityDomain",
    "NetworkZeroBasis",
    "StoredNetworkProductSet",
    "StoredNetworkTargetDeclaration",
    "network_declaration_error",
    "network_product_set_content_hash",
    "validation_objective",
]

#: A declaration contradicts itself, a column of the record, the network's own topology, or a
#: determination is stated inconsistently. ``context['field']`` names the declaration.
W_NETWORK_DECLARATION_INVALID = "network_declaration_invalid"
#: A declaration carries a ``version`` this server does not accept.
W_NETWORK_DECLARATION_VERSION_UNSUPPORTED = "network_declaration_version_unsupported"

#: Declaration versions the server accepts (target, protocol, validation, observable, representation).
NETWORK_DECLARATION_VERSIONS: frozenset[int] = frozenset({1})

_Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
_Key = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=128)]
_HASH = "^[0-9a-f]{64}$"

#: Version of the normalization behind a stored product set's ``content_hash``.
NETWORK_PRODUCT_SET_MEMBERSHIP_VERSION = 1


def network_product_set_content_hash(members: list[tuple[str, list[str]]]) -> str:
    """Digest of a product set's normalized membership: ``(determination_ref, representation_keys)`` pairs.

    Order-free (sorted) and independent of the set's own key, so two sets with the same members pin
    the same content. Representation keys are sorted; an empty list means every fit of the determination.
    """
    canonical = json.dumps(
        {
            "version": NETWORK_PRODUCT_SET_MEMBERSHIP_VERSION,
            "members": sorted([ref, sorted(keys)] for ref, keys in members),
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _version(block: str, value: int) -> int:
    if type(value) is not int or value not in NETWORK_DECLARATION_VERSIONS:
        versions = sorted(NETWORK_DECLARATION_VERSIONS)
        raise CodedValidationError(
            W_NETWORK_DECLARATION_VERSION_UNSUPPORTED,
            f"{block}.version {value!r} is not supported; supported versions: {versions}.",
            context={"field": f"{block}.version", "version": value, "supported_versions": versions},
            message_prefix=False,
        )
    return value


def _invalid(block: str, message: str) -> CodedValidationError:
    return CodedValidationError(
        W_NETWORK_DECLARATION_INVALID, f"{block}: {message}", context={"field": block}, message_prefix=False
    )


class NetworkClaimOrigin(str, Enum):
    """Whose claim a declaration is: the source's own statement, or the depositor's reading."""

    source_publication = "source_publication"
    depositor_interpretation = "depositor_interpretation"


class NetworkComparisonObjective(str, Enum):
    """What a comparison between two products is a comparison *of*; agreement on one is not on another."""

    physical_accuracy = "physical_accuracy"
    model_fidelity = "model_fidelity"
    representation_fidelity = "representation_fidelity"


# ---------------------------------------------------------------------------
# Determination, observable, representation (on a fit)
# ---------------------------------------------------------------------------


class NetworkObservable(str, Enum):
    """The coefficient a determination reports for its directed channel.

    ``product_resolved_coefficient``: flux into this channel's sink. ``total_loss_coefficient``:
    loss of the source summed over every product; it says nothing about branching.
    """

    product_resolved_coefficient = "product_resolved_coefficient"
    total_loss_coefficient = "total_loss_coefficient"


class NetworkCoefficientBasis(str, Enum):
    """``kernel`` is the coefficient still to be multiplied by a collider concentration;
    ``composition_effective`` is already evaluated for the solve's declared bath."""

    kernel = "kernel"
    composition_effective = "composition_effective"


class NetworkObservableDeclaration(SchemaBase):
    """What a determination's coefficient is. Version 1. Immutable once the determination exists.

    :param version: Only the integer ``1``.
    :param reaction_order: Molecularity of the source state (1 to 3); the fit's own units must agree.
    :param degeneracy_applied: Whether reaction-path degeneracy is already in the coefficient.
    """

    version: StrictInt
    observable: NetworkObservable
    coefficient_basis: NetworkCoefficientBasis
    reaction_order: int = Field(ge=1, le=3, strict=True)
    degeneracy_applied: bool

    @field_validator("version")
    @classmethod
    def validate_version(cls, value: int) -> int:
        """``observable.version`` is a supported version (``1``)."""
        return _version("observable", value)


class NetworkDeterminationDeclaration(SchemaBase):
    """The determination a fit belongs to, and its role there.

    One determination is one complete determination of one channel's coefficient within one solve;
    alternate fits of it share it and are not independent support for each other. Fits that state
    the same ``key`` join it and must state the same channel and ``observable``.

    :param key: Solve-scoped key.
    :param observable: What the coefficient is.
    """

    key: _Key
    observable: NetworkObservableDeclaration
    representation_role: NetworkRepresentationRole


class NetworkFitOrigin(str, Enum):
    """``solver_output``: the coefficients are the solver's own. ``refit``: refitted from tabulated
    solver output. ``transcribed``: copied from a publication."""

    solver_output = "solver_output"
    refit = "refit"
    transcribed = "transcribed"


class NetworkRepresentationDeclaration(SchemaBase):
    """The fit's own identity within its determination. Version 1.

    :param version: Only the integer ``1``.
    :param key: Unique among the fits of one determination; what a product set names.
    :param fit_origin: Where the coefficients came from.
    """

    version: StrictInt
    key: _Key
    fit_origin: NetworkFitOrigin

    @field_validator("version")
    @classmethod
    def validate_version(cls, value: int) -> int:
        """``representation.version`` is a supported version (``1``)."""
        return _version("representation", value)


# ---------------------------------------------------------------------------
# Target (on the solve)
# ---------------------------------------------------------------------------


class NetworkBoundaryKind(str, Enum):
    """The boundary a state is in the solve: ``reversible``, ``absorbing`` (flux that reaches it
    is lost), ``open`` or ``driven``."""

    reversible = "reversible"
    absorbing = "absorbing"
    open = "open"
    driven = "driven"


class NetworkStateBoundary(SchemaBase):
    """One state's boundary. A state not listed has no stated boundary."""

    state_key: _Key
    kind: NetworkBoundaryKind


class NetworkLump(SchemaBase):
    """States treated as one in the solve; at least two."""

    members: list[_Key] = Field(min_length=2)


class NetworkPartition(SchemaBase):
    """How the network's states were treated: each is retained, eliminated or in one lump.

    :param retained: States kept as individual species.
    :param eliminated: States removed from the reduced description.
    :param lumps: Groups of states treated as one retained aggregate.
    """

    retained: list[_Key] = Field(default_factory=list)
    eliminated: list[_Key] = Field(default_factory=list)
    lumps: list[NetworkLump] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_disjoint(self) -> NetworkPartition:
        """Every state appears once; the partition names at least one retained state or lump."""
        names = [*self.retained, *self.eliminated, *(m for lump in self.lumps for m in lump.members)]
        if not (self.retained or self.lumps):
            raise ValueError("A partition retains at least one state or lump.")
        if len(set(names)) != len(names):
            raise ValueError("A state appears in the partition once: retained, eliminated or in one lump.")
        return self


class NetworkRegimeKind(str, Enum):
    """``time_independent``: a phenomenological coefficient valid for any initialization.
    ``initial_population_restricted``: valid only for the stated initial population."""

    time_independent = "time_independent"
    initial_population_restricted = "initial_population_restricted"


class NetworkRegime(SchemaBase):
    """The time regime the coefficients answer.

    :param initial_state_keys: Required for ``initial_population_restricted``; refused otherwise.
    """

    kind: NetworkRegimeKind
    initial_state_keys: list[_Key] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_initial(self) -> NetworkRegime:
        """Initial states go with the restricted regime and only with it."""
        restricted = self.kind is NetworkRegimeKind.initial_population_restricted
        if restricted != bool(self.initial_state_keys):
            raise ValueError("initial_state_keys are required for, and only for, initial_population_restricted.")
        return self


class NetworkValidityDomain(SchemaBase):
    """A temperature and pressure domain the producer states the output is physically valid over."""

    temperature_min_k: float = Field(gt=0, allow_inf_nan=False, strict=True)
    temperature_max_k: float = Field(gt=0, allow_inf_nan=False, strict=True)
    pressure_min_bar: float = Field(gt=0, allow_inf_nan=False, strict=True)
    pressure_max_bar: float = Field(gt=0, allow_inf_nan=False, strict=True)

    @model_validator(mode="after")
    def validate_order(self) -> NetworkValidityDomain:
        """Minimum at most maximum, on both axes."""
        if self.temperature_min_k > self.temperature_max_k or self.pressure_min_bar > self.pressure_max_bar:
            raise ValueError("A validity domain's minimum is at most its maximum on each axis.")
        return self


class NetworkBathScope(str, Enum):
    """``specified_collider``: one bath species. ``fixed_mixture``: the solve's bath mixture.
    ``composition_dependent``: a protocol that evaluates other compositions (unsupported by selection)."""

    specified_collider = "specified_collider"
    fixed_mixture = "fixed_mixture"
    composition_dependent = "composition_dependent"


class NetworkOutputAvailability(str, Enum):
    """``supplied``: a determination of this solve gives it. ``declared_zero``: the producer states it is
    zero (needs ``zero_basis``). ``unavailable``: the solve does not give it; that is never zero."""

    supplied = "supplied"
    declared_zero = "declared_zero"
    unavailable = "unavailable"


class NetworkZeroBasis(str, Enum):
    """What supports a zero claim: the ``source_statement`` or the ``solve_computed`` result."""

    source_statement = "source_statement"
    solve_computed = "solve_computed"


class NetworkOutputEntry(SchemaBase):
    """One output of the solve's catalog: a directed channel and whether the solve gives it.

    :param required: Whether the observable is incomplete without it (a sink, a reverse channel, a loss).
    :param validity: Narrower than the solve's declared domain, when it is.
    """

    channel_key: str = Field(min_length=1)
    availability: NetworkOutputAvailability
    zero_basis: NetworkZeroBasis | None = None
    required: bool = True
    validity: NetworkValidityDomain | None = None

    @model_validator(mode="after")
    def validate_zero(self) -> NetworkOutputEntry:
        """A zero claim states its basis, and only a zero claim does."""
        if (self.availability is NetworkOutputAvailability.declared_zero) != (self.zero_basis is not None):
            raise ValueError("zero_basis is required for, and only for, declared_zero.")
        return self


class NetworkProductSetMember(SchemaBase):
    """One determination of a product set, and which of its fits the set contains.

    Name the determination by ``determination_key`` (an upload) or ``determination_ref`` (stored), one of them.

    :param representation_keys: The fits of the determination the set contains; empty: all of them.
    """

    determination_key: _Key | None = None
    determination_ref: str | None = Field(default=None, min_length=1)
    representation_keys: list[_Key] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_exactly_one(self) -> NetworkProductSetMember:
        """Exactly one of the key or the ref; no repeated representation."""
        if (self.determination_key is None) == (self.determination_ref is None):
            raise ValueError("A member names its determination by exactly one of determination_key or determination_ref.")
        if len(set(self.representation_keys)) != len(self.representation_keys):
            raise ValueError("A member lists a representation once.")
        return self


class NetworkProductSet(SchemaBase):
    """A declared complete product set: the determinations that together answer a bundle of outputs.

    Immutable once stored. Several declared sets are distinct competitors, never combined.
    """

    product_set_key: _Key
    members: list[NetworkProductSetMember] = Field(min_length=1)


class StoredNetworkProductSet(NetworkProductSet):
    """The stored form: members by public ref, with the pinned normalized membership.

    :param membership_version: Version of the normalization that produced ``content_hash``.
    :param content_hash: Digest of the normalized membership.
    """

    membership_version: int = Field(ge=1, strict=True)
    content_hash: str = Field(pattern=_HASH)


class NetworkTargetDeclaration(SchemaBase):
    """What the solve's outputs are outputs of. Version 1.

    An attributed claim, stored as made; every statement is optional and an omitted one is unknown.
    States and channels are named by local key; each state takes exactly one place in the partition
    when a partition is stated, and ``bath_scope`` must agree with the solve's bath gas. ``claim_origin``
    is required.

    :param version: Only the integer ``1``.
    :param boundaries: Boundary semantics, per state.
    :param validity: Physical validity domain of the solve's outputs.
    :param outputs: The output catalog, one entry per directed channel; a channel is listed once.
    :param product_sets: Declared complete product sets.
    """

    version: StrictInt
    claim_origin: NetworkClaimOrigin
    partition: NetworkPartition | None = None
    boundaries: list[NetworkStateBoundary] = Field(default_factory=list)
    regime: NetworkRegime | None = None
    validity: NetworkValidityDomain | None = None
    bath_scope: NetworkBathScope | None = None
    outputs: list[NetworkOutputEntry] = Field(default_factory=list)
    product_sets: list[NetworkProductSet] = Field(default_factory=list)

    @field_validator("version")
    @classmethod
    def validate_version(cls, value: int) -> int:
        """``target.version`` is a supported version (``1``)."""
        return _version("target", value)

    @model_validator(mode="after")
    def validate_content(self) -> NetworkTargetDeclaration:
        """At least one claim; no repeated state, channel or product set."""
        if not (
            self.partition or self.boundaries or self.regime or self.validity or self.bath_scope
            or self.outputs or self.product_sets
        ):
            raise ValueError("A target declaration states at least one claim.")
        for label, names in (
            ("boundary state", [b.state_key for b in self.boundaries]),
            ("output channel", [o.channel_key for o in self.outputs]),
            ("product set", [p.product_set_key for p in self.product_sets]),
        ):
            if len(set(names)) != len(names):
                raise ValueError(f"A {label} is listed once.")
        return self


class StoredNetworkTargetDeclaration(NetworkTargetDeclaration):
    """The stored (and served) form: states by composition hash, product sets by public ref and pinned."""

    product_sets: list[StoredNetworkProductSet] = Field(default_factory=list)  # type: ignore[assignment]

    @model_validator(mode="after")
    def validate_stored_form(self) -> StoredNetworkTargetDeclaration:
        """Every state is a 64-hex composition hash; every product-set member a public ref."""
        names = [b.state_key for b in self.boundaries]
        if self.regime is not None:
            names += self.regime.initial_state_keys
        if self.partition is not None:
            names += [*self.partition.retained, *self.partition.eliminated]
            names += [m for lump in self.partition.lumps for m in lump.members]
        if any(re.fullmatch(_HASH, n) is None for n in names):
            raise ValueError("A stored declaration names states by composition hash.")
        if any(m.determination_ref is None for p in self.product_sets for m in p.members):
            raise ValueError("A stored product set names its determinations by public ref.")
        return self


# ---------------------------------------------------------------------------
# Protocol (on the solve)
# ---------------------------------------------------------------------------


class NetworkReductionMethod(str, Enum):
    """How the master equation was reduced to phenomenological coefficients."""

    chemically_significant_eigenvalues = "chemically_significant_eigenvalues"
    reservoir_state = "reservoir_state"
    modified_strong_collision = "modified_strong_collision"
    inverse_laplace = "inverse_laplace"
    other = "other"


class NetworkMicrocanonicalTreatment(str, Enum):
    """How the microcanonical rates entering the solve were obtained."""

    rrkm = "rrkm"
    variational_rrkm = "variational_rrkm"
    capture_theory = "capture_theory"
    phase_space_theory = "phase_space_theory"
    other = "other"


class NetworkBarrierBasis(str, Enum):
    """Whether the stated energies are electronic only or include zero-point energy."""

    classical_electronic = "classical_electronic"
    zpe_corrected = "zpe_corrected"


class NetworkRotorTreatment(str, Enum):
    """How internal rotation and vibration enter the densities of states."""

    rigid_rotor_harmonic_oscillator = "rigid_rotor_harmonic_oscillator"
    hindered_rotor = "hindered_rotor"
    anharmonic = "anharmonic"


class NetworkConformerTreatment(str, Enum):
    """How the conformers of a well or saddle point enter the solve."""

    single_conformer = "single_conformer"
    boltzmann_ensemble = "boltzmann_ensemble"
    interconversion_resolved = "interconversion_resolved"


class NetworkTunnelingTreatment(str, Enum):
    """The tunneling correction applied to saddle-point rates."""

    none = "none"
    wigner = "wigner"
    eckart = "eckart"
    other = "other"


class NetworkCollisionFrequencyModel(str, Enum):
    """How the collision frequency was obtained."""

    lennard_jones = "lennard_jones"
    other = "other"


class NetworkProtocolDeclaration(SchemaBase):
    """The coupled recipe a solve used. Version 1.

    An attributed claim, not checked against the linked calculations. Every statement is optional;
    at least one is required. Grain, energy-ceiling, bath and transfer values are the solve's own
    columns and rows and are not repeated here.

    :param version: Only the integer ``1``.
    """

    version: StrictInt
    reduction_method: NetworkReductionMethod | None = None
    microcanonical_treatment: NetworkMicrocanonicalTreatment | None = None
    barrier_basis: NetworkBarrierBasis | None = None
    rotor_treatment: NetworkRotorTreatment | None = None
    conformer_treatment: NetworkConformerTreatment | None = None
    tunneling_treatment: NetworkTunnelingTreatment | None = None
    collision_frequency_model: NetworkCollisionFrequencyModel | None = None

    @field_validator("version")
    @classmethod
    def validate_version(cls, value: int) -> int:
        """``protocol.version`` is a supported version (``1``)."""
        return _version("protocol", value)

    @model_validator(mode="after")
    def validate_content(self) -> NetworkProtocolDeclaration:
        """At least one statement."""
        if all(getattr(self, name) is None for name in type(self).model_fields if name != "version"):
            raise ValueError("A protocol declaration states at least one treatment.")
        return self


# ---------------------------------------------------------------------------
# Validation (on the solve)
# ---------------------------------------------------------------------------


class NetworkValidationKind(str, Enum):
    """The evidence a solve cites. Each of the first four is evidence for one comparison objective
    (:func:`validation_objective`); ``uncertainty`` and ``dependence`` are for none."""

    convergence = "convergence"
    model_fidelity = "model_fidelity"
    physical_validation = "physical_validation"
    representation_validation = "representation_validation"
    uncertainty = "uncertainty"
    dependence = "dependence"


class NetworkValidationMetric(str, Enum):
    """How the agreement is measured over the domain."""

    max_relative_error = "max_relative_error"
    rms_log10_error = "rms_log10_error"
    max_log10_error = "max_log10_error"


_OBJECTIVE_OF_KIND = {
    NetworkValidationKind.convergence: NetworkComparisonObjective.model_fidelity,
    NetworkValidationKind.model_fidelity: NetworkComparisonObjective.model_fidelity,
    NetworkValidationKind.physical_validation: NetworkComparisonObjective.physical_accuracy,
    NetworkValidationKind.representation_validation: NetworkComparisonObjective.representation_fidelity,
}


def validation_objective(kind: NetworkValidationKind | str) -> NetworkComparisonObjective | None:
    """The comparison objective a kind of evidence supports; ``None`` for uncertainty and dependence."""
    return _OBJECTIVE_OF_KIND.get(NetworkValidationKind(kind))


class NetworkValidationEntry(SchemaBase):
    """One piece of cited evidence, stored as *declared* (never as verified).

    :param channel_keys: Outputs it covers; empty: the whole solve.
    :param reference_solve_ref: Public ref (``nsolve_...``) of the pinned model a ``model_fidelity`` entry is against.
    :param reference_dataset: The named dataset a ``representation_validation`` or ``physical_validation`` is against.
    :param initialization: Initialization and source conditions the metric was taken under.
    :param window: Observation window.
    """

    kind: NetworkValidationKind
    metric: NetworkValidationMetric | None = None
    value: float | None = Field(default=None, ge=0, allow_inf_nan=False, strict=True)
    domain: NetworkValidityDomain | None = None
    channel_keys: list[str] = Field(default_factory=list)
    reference_solve_ref: str | None = Field(default=None, min_length=1)
    reference_dataset: _Text | None = Field(default=None, max_length=256)
    initialization: _Text | None = Field(default=None, max_length=256)
    window: _Text | None = Field(default=None, max_length=256)
    limitations: _Text | None = Field(default=None, max_length=1024)

    @model_validator(mode="after")
    def validate_content(self) -> NetworkValidationEntry:
        """A metric and its value go together; a comparison names its reference and its domain."""
        if (self.metric is None) != (self.value is None):
            raise ValueError("metric and value are stated together.")
        if len(set(self.channel_keys)) != len(self.channel_keys):
            raise ValueError("A channel is listed once.")
        objective = validation_objective(self.kind)
        if objective is not None and self.domain is None:
            raise ValueError(f"{self.kind.value} evidence states the domain it covers.")
        if self.kind is NetworkValidationKind.model_fidelity and self.reference_solve_ref is None:
            raise ValueError("model_fidelity evidence names the pinned model: reference_solve_ref.")
        if self.kind in {NetworkValidationKind.representation_validation, NetworkValidationKind.physical_validation} and (
            self.reference_dataset is None
        ):
            raise ValueError(f"{self.kind.value} evidence names its reference_dataset.")
        return self


class NetworkValidationDeclaration(SchemaBase):
    """The evidence a solve cites. Version 1. Declared, never verified at upload.

    :param version: Only the integer ``1``.
    """

    version: StrictInt
    entries: list[NetworkValidationEntry] = Field(min_length=1)

    @field_validator("version")
    @classmethod
    def validate_version(cls, value: int) -> int:
        """``validation.version`` is a supported version (``1``)."""
        return _version("validation", value)


# ---------------------------------------------------------------------------
# Rules that need the solve's own facts
# ---------------------------------------------------------------------------


def _get(obj: Any, key: str) -> Any:
    return obj.get(key) if isinstance(obj, dict) else getattr(obj, key, None)


def network_declaration_error(
    target: Any,
    *,
    state_keys: set[str],
    channel_keys: set[str],
    bath_species: int | None,
) -> tuple[str, str] | None:
    """``(code, message)`` when a target declaration contradicts the network or the solve, else ``None``.

    :param state_keys: The network's states, by the same key form the declaration uses.
    :param channel_keys: The network's channels.
    :param bath_species: Number of bath species the solve lists; ``None`` if not known at this layer.
    """
    if target is None:
        return None
    named = {
        "partition": [],
        "boundaries": [_get(b, "state_key") for b in _get(target, "boundaries") or []],
    }
    partition = _get(target, "partition")
    if partition is not None:
        named["partition"] = [
            *_get(partition, "retained"),
            *_get(partition, "eliminated"),
            *(m for lump in _get(partition, "lumps") for m in _get(lump, "members")),
        ]
    regime = _get(target, "regime")
    if regime is not None:
        named["regime"] = list(_get(regime, "initial_state_keys") or [])
    for where, keys in named.items():
        unknown = sorted(k for k in keys if k not in state_keys)
        if unknown:
            return W_NETWORK_DECLARATION_INVALID, f"target.{where} names state(s) the network does not have: {unknown}."
    if partition is not None and set(named["partition"]) != state_keys:
        missing = sorted(state_keys - set(named["partition"]))
        return (
            W_NETWORK_DECLARATION_INVALID,
            f"target.partition places every state of the network exactly once; not placed: {missing}.",
        )
    outputs = _get(target, "outputs") or []
    unknown_channels = sorted(
        c for c in (_get(o, "channel_key") for o in outputs) if c not in channel_keys
    )
    if unknown_channels:
        return W_NETWORK_DECLARATION_INVALID, f"target.outputs names channel(s) the network does not have: {unknown_channels}."
    scope = _get(target, "bath_scope")
    scope = getattr(scope, "value", scope)
    if scope is not None and bath_species is not None:
        if scope == "specified_collider" and bath_species != 1:
            return (
                W_NETWORK_DECLARATION_INVALID,
                f"target.bath_scope specified_collider contradicts a solve with {bath_species} bath species.",
            )
        if scope == "fixed_mixture" and bath_species < 2:
            return (
                W_NETWORK_DECLARATION_INVALID,
                f"target.bath_scope fixed_mixture contradicts a solve with {bath_species} bath species.",
            )
    validity = _get(target, "validity")
    if validity is not None:
        for o in outputs:
            narrower = _get(o, "validity")
            if narrower is None:
                continue
            if (
                _get(narrower, "temperature_min_k") < _get(validity, "temperature_min_k")
                or _get(narrower, "temperature_max_k") > _get(validity, "temperature_max_k")
                or _get(narrower, "pressure_min_bar") < _get(validity, "pressure_min_bar")
                or _get(narrower, "pressure_max_bar") > _get(validity, "pressure_max_bar")
            ):
                return (
                    W_NETWORK_DECLARATION_INVALID,
                    f"target.outputs['{_get(o, 'channel_key')}'].validity lies outside the solve's declared validity.",
                )
    return None
