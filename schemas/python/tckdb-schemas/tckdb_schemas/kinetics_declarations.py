"""Scientific declarations a kinetics record can carry.

Three optional, attributed claims a depositor can make about a rate record, none of which the
record could state before:

* **its determination** -- which complete determination of the rate the record is one fitted
  representation (or one additive component) of, and what that determination is the rate *of*;
* **its applicability** -- what quantity the stored coefficient is (phase, observable,
  coefficient basis, order, pressure and collider meaning);
* **its protocol** -- how the rate was produced, in the small vocabulary a later method-aware
  comparison needs and no larger.

All three are *claims*, stored as the depositor made them. None is inferred from anything else
(a model kind does not imply a pressure meaning, a source calculation does not imply a
protocol) and none is ever backfilled: a record deposited without one reads ``null``, which
means "not stated", never "universally valid" and never "standard".

House rule for the vocabulary, as for the thermo protocol: it stores only what a job states, it
starts small, and a value is added when a real deposit needs it. Adding an enum member or an
optional field is additive within version 1; removing or re-meaning one is a new version. The
accepted versions are :data:`KINETICS_APPLICABILITY_VERSIONS` and
:data:`KINETICS_PROTOCOL_VERSIONS`; any other is refused.

Scientific references inside a declaration are local keys or public refs, never database ids.
The backend resolves them, and what is *stored* carries public refs only (the ``Stored*``
models). The existing columns stay authoritative: direction, temperature bounds, the pressure
context and pressure, units, degeneracy and the model kind already say things about a record,
and a declaration that contradicts one of them is refused
(:func:`kinetics_applicability_error`), never reconciled.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum
from typing import Annotated, Any

from pydantic import (
    Field,
    StrictInt,
    StringConstraints,
    ValidationError,
    field_validator,
    model_validator,
)

from tckdb_schemas.coded_error import CodedValidationError
from tckdb_schemas.common import SchemaBase
from tckdb_schemas.enums import (
    KineticsDeterminationTargetKind,
    KineticsModelKind,
    KineticsRepresentationRole,
    ScientificOriginKind,
)
from tckdb_schemas.fragments.identity import SpeciesEntryIdentityPayload
from tckdb_schemas.producer_rule import producer_rule

__all__ = [
    "KINETICS_APPLICABILITY_VERSIONS",
    "KINETICS_PROTOCOL_VERSIONS",
    "MOLE_FRACTION_SUM_TOLERANCE",
    "KineticsApplicabilityDeclaration",
    "KineticsBarrierBasis",
    "KineticsCalculationPurpose",
    "KineticsClaimOrigin",
    "KineticsCoefficientBasis",
    "KineticsCollider",
    "KineticsColliderKind",
    "KineticsConformerTreatment",
    "KineticsDepartureComponent",
    "KineticsDeterminationDeclaration",
    "KineticsGeometryRelation",
    "KineticsMethodKind",
    "KineticsObservable",
    "KineticsPathTreatment",
    "KineticsPhase",
    "KineticsPressureDependence",
    "KineticsProtocolCalculationRef",
    "KineticsProtocolDeclaration",
    "KineticsRateProgressConvention",
    "KineticsRecordFacts",
    "KineticsRotorTreatment",
    "KineticsZeroPointTreatment",
    "StoredKineticsApplicabilityDeclaration",
    "StoredKineticsCollider",
    "StoredKineticsProtocolCalculationRef",
    "StoredKineticsProtocolDeclaration",
    "W_KINETICS_DECLARATION_CONTRADICTS_RECORD",
    "W_KINETICS_DECLARATION_INVALID",
    "W_KINETICS_DECLARATION_VERSION_UNSUPPORTED",
    "W_KINETICS_DETERMINATION_INVALID",
    "kinetics_applicability_error",
    "kinetics_declaration_context",
    "kinetics_declaration_error",
    "kinetics_determination_error",
    "kinetics_protocol_origin_error",
    "kinetics_record_facts",
    "version_is_supported",
]

#: A determination contradicts itself (neither or both locators, a target that names the wrong
#: locators for its kind, an unknown role), or a record joins one without stating what the join
#: needs: its own ``direction`` (never inferred) or a source attribution (literature or
#: workflow-tool release), since a determination key is scoped to a source.
#: ``context['missing']`` names which of the two.
W_KINETICS_DETERMINATION_INVALID = "kinetics_determination_invalid"
#: A declaration contradicts a stored column of its own record: an applicability claim against
#: the direction, pressure context, model kind, third-body flag, order or determination target,
#: or a protocol method against the record's scientific origin.
W_KINETICS_DECLARATION_CONTRADICTS_RECORD = "kinetics_declaration_contradicts_record"
#: A declaration carries a ``version`` this server does not accept.
W_KINETICS_DECLARATION_VERSION_UNSUPPORTED = "kinetics_declaration_version_unsupported"
#: A declaration that reached a service or the client builder without passing request
#: validation (a payload built with ``model_construct``) and fails it there, or a mixture that
#: lists one species twice.
W_KINETICS_DECLARATION_INVALID = "kinetics_declaration_invalid"

#: Declaration versions the server accepts.
KINETICS_APPLICABILITY_VERSIONS: frozenset[int] = frozenset({1})
KINETICS_PROTOCOL_VERSIONS: frozenset[int] = frozenset({1})

#: Absolute tolerance on the sum of a declared mixture's mole fractions: the one the network
#: upload applies to a bath gas, so the two cannot disagree about "sums to one".
MOLE_FRACTION_SUM_TOLERANCE = 1e-9

#: Free text, trimmed and never blank.
_Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]

#: Model kinds whose own parameters carry a pressure dependence.
_PRESSURE_DEPENDENT_MODELS = frozenset(
    {
        KineticsModelKind.lindemann.value,
        KineticsModelKind.troe.value,
        KineticsModelKind.sri.value,
        KineticsModelKind.plog.value,
        KineticsModelKind.chebyshev.value,
    }
)
_FALLOFF_MODELS = frozenset(
    {KineticsModelKind.lindemann.value, KineticsModelKind.troe.value, KineticsModelKind.sri.value}
)


def version_is_supported(value: Any, supported: frozenset[int]) -> bool:
    """Exactly a supported integer: ``True``, ``"1"`` and ``1.0`` are not version 1."""
    return type(value) is int and value in supported


def _get(obj: Any, key: str) -> Any:
    if isinstance(obj, dict):
        return obj.get(key)
    return getattr(obj, key, None)


def _value(obj: Any) -> Any:
    return obj.value if isinstance(obj, Enum) else obj


# ---------------------------------------------------------------------------
# Determination
# ---------------------------------------------------------------------------


class KineticsDeterminationDeclaration(SchemaBase):
    """The determination a record belongs to, and its role in it.

    One determination is one complete determination of a rate (a measurement set, one computed
    rate); its fitted representations share it and are not independent support for each other.

    Give exactly one locator: ``key`` with ``target_kind`` states the content (the backend finds
    or creates it from the reaction entry, direction, target, source and key), or
    ``determination_ref`` joins one already deposited. A ``resolved_channel`` names its channel
    once: ``transition_state_entry_ref``, or ``network_ref`` with ``channel_key``.

    :param determination_ref: Public ref (``kdet_...``) of an existing determination.
    :param key: Source-scoped key.
    :param target_kind: Required with ``key``.
    :param transition_state_entry_ref: Public ref (``tse_...``).
    :param network_ref: Public ref (``net_...``).
    :param channel_key: Channel key in that network.
    :param group: Contribution-bundle import only: groups records of one import. Never stored.
    """

    determination_ref: str | None = Field(default=None, min_length=1)
    key: _Text | None = Field(default=None, max_length=128)
    target_kind: KineticsDeterminationTargetKind | None = None
    transition_state_entry_ref: str | None = Field(default=None, min_length=1)
    network_ref: str | None = Field(default=None, min_length=1)
    channel_key: str | None = Field(default=None, min_length=1)
    group: str | None = Field(default=None, min_length=1, max_length=128)
    representation_role: KineticsRepresentationRole

    @model_validator(mode="after")
    def validate_locator(self) -> KineticsDeterminationDeclaration:
        """One of ``determination_ref`` or ``key``; a ``key`` needs a ``target_kind`` with its locators."""
        error = kinetics_determination_error(self)
        if error is not None:
            code, message = error
            raise CodedValidationError(
                code, message, context={"field": "determination"}, message_prefix=False
            )
        return self


def kinetics_determination_error(determination: Any) -> tuple[str, str] | None:
    """``(code, message)`` when a determination declaration contradicts itself, else ``None``.

    Pure and transport-independent: the request validator, the workflows (for a payload built
    without validation) and the client builder all call it. Whether the named transition state
    or channel exists, and belongs to the record's reaction, is a database question.
    """
    if determination is None:
        return None
    role = _value(_get(determination, "representation_role"))
    if role not in {member.value for member in KineticsRepresentationRole}:
        return (
            W_KINETICS_DETERMINATION_INVALID,
            f"determination.representation_role {role!r} is not recognised; use "
            f"{' or '.join(repr(m.value) for m in KineticsRepresentationRole)}.",
        )
    ref, key = _get(determination, "determination_ref"), _get(determination, "key")
    kind = _value(_get(determination, "target_kind"))
    ts_ref, network, channel = (
        _get(determination, name) for name in ("transition_state_entry_ref", "network_ref", "channel_key")
    )
    locators = [
        name
        for name, value in (
            ("transition_state_entry_ref", ts_ref),
            ("network_ref", network),
            ("channel_key", channel),
        )
        if value is not None
    ]
    if (ref is None) == (key is None):
        return (
            W_KINETICS_DETERMINATION_INVALID,
            "A determination gives exactly one of determination_ref (join an existing "
            "determination) or key with target_kind (state its content).",
        )
    if ref is not None and _get(determination, "group") is not None:
        return (
            W_KINETICS_DETERMINATION_INVALID,
            "A determination_ref names its determination already; a bundle group handle goes with a key.",
        )
    if ref is not None and (kind is not None or locators):
        return (
            W_KINETICS_DETERMINATION_INVALID,
            "A determination_ref already has a target; state no target_kind, "
            "transition_state_entry_ref, network_ref or channel_key with it.",
        )
    if ref is not None:
        return None
    if kind is None:
        return (
            W_KINETICS_DETERMINATION_INVALID,
            "A determination stated by key states its target_kind (whole_reaction or "
            "resolved_channel); it is never defaulted.",
        )
    if kind not in {member.value for member in KineticsDeterminationTargetKind}:
        return (
            W_KINETICS_DETERMINATION_INVALID,
            f"determination.target_kind {kind!r} is not recognised; use "
            f"{' or '.join(repr(m.value) for m in KineticsDeterminationTargetKind)}.",
        )
    if (network is None) != (channel is None):
        return (
            W_KINETICS_DETERMINATION_INVALID,
            "A network channel is named by network_ref and channel_key together.",
        )
    named = (1 if ts_ref is not None else 0) + (1 if network is not None else 0)
    if kind == KineticsDeterminationTargetKind.whole_reaction.value and named:
        return (
            W_KINETICS_DETERMINATION_INVALID,
            "A whole_reaction target names no transition state or network channel "
            f"({', '.join(locators)} given); remove it, or declare resolved_channel.",
        )
    if kind == KineticsDeterminationTargetKind.resolved_channel.value and named != 1:
        return (
            W_KINETICS_DETERMINATION_INVALID,
            "A resolved_channel target names its channel exactly once: give "
            "transition_state_entry_ref or network_ref with channel_key, "
            + ("not both." if named else "one of them is required."),
        )
    return None


# ---------------------------------------------------------------------------
# Applicability
# ---------------------------------------------------------------------------


class KineticsPhase(str, Enum):
    """The phase the coefficient describes. Gas only: the vocabulary starts here."""

    gas = "gas"


class KineticsObservable(str, Enum):
    """The kind of kinetic quantity the stored expression is.

    A rate of progress needs the concentrations of its law and a global law is a fitted
    empirical law; both can be stated so they are reported as what they are, and a
    rate-coefficient selector treats them as unsupported.
    """

    rate_coefficient = "rate_coefficient"
    rate_of_progress = "rate_of_progress"
    effective_global_law = "effective_global_law"


class KineticsCoefficientBasis(str, Enum):
    """What the coefficient still needs before it is a rate.

    ``third_body_kernel`` is the coefficient of a simple ``+M`` reaction, still to be multiplied
    by an effective collider concentration; ``composition_effective_coefficient`` is already
    evaluated for one declared mixture.
    """

    elementary_coefficient = "elementary_coefficient"
    third_body_kernel = "third_body_kernel"
    composition_effective_coefficient = "composition_effective_coefficient"


class KineticsRateProgressConvention(str, Enum):
    """How the coefficient is normalised for a reactant with stoichiometric coefficient above one.

    ``reactant_loss`` defines it on a reactant's loss rate, which for ``2 A -> products`` is
    twice the rate of progress.
    """

    reaction_progress = "reaction_progress"
    reactant_loss = "reactant_loss"


class KineticsPressureDependence(str, Enum):
    """What the coefficient means with respect to pressure.

    ``independent`` is established pressure independence (a null pressure context is not that);
    ``fixed_pressure`` is an apparent coefficient at the record's own ``pressure_bar``;
    ``pressure_dependent`` is a surface or model carrying its own pressure dependence.
    """

    independent = "independent"
    high_pressure_limit = "high_pressure_limit"
    fixed_pressure = "fixed_pressure"
    pressure_dependent = "pressure_dependent"


class KineticsColliderKind(str, Enum):
    """Which collider the coefficient is for.

    ``composition_dependent`` depends on the composition it is evaluated in, through the record's
    own third-body efficiencies.
    """

    not_dependent = "not_dependent"
    specified_collider = "specified_collider"
    fixed_mixture = "fixed_mixture"
    composition_dependent = "composition_dependent"


class KineticsClaimOrigin(str, Enum):
    """Whose claim a declaration is: the source's own statement, or the depositor's reading."""

    source_publication = "source_publication"
    depositor_interpretation = "depositor_interpretation"


class KineticsCollider(SchemaBase):
    """One collider by species content, and its mole fraction in a mixture.

    :param mole_fraction: Of each component of a ``fixed_mixture`` only.
    """

    species: SpeciesEntryIdentityPayload
    mole_fraction: float | None = Field(default=None, gt=0, le=1, allow_inf_nan=False, strict=True)


class StoredKineticsCollider(SchemaBase):
    """A collider as stored and read back: the species public ref, and its mole fraction."""

    species_ref: str = Field(min_length=1)
    mole_fraction: float | None = Field(default=None, gt=0, le=1, allow_inf_nan=False, strict=True)


def _check_colliders(kind: Any, colliders: list[Any], default_efficiency: Any) -> None:
    """Colliders are what the kind needs and nothing else; a mixture sums to one, without repeats."""
    kind_value = _value(kind)
    if kind_value is None:
        if colliders or default_efficiency is not None:
            raise ValueError("colliders and default_third_body_efficiency need a collider_kind.")
        return
    if kind_value == KineticsColliderKind.specified_collider.value:
        if len(colliders) != 1 or _get(colliders[0], "mole_fraction") is not None:
            raise ValueError(
                "A specified_collider declaration lists exactly one collider, with no mole fraction."
            )
    elif kind_value == KineticsColliderKind.fixed_mixture.value:
        fractions = [_get(c, "mole_fraction") for c in colliders]
        if len(colliders) < 2 or any(f is None for f in fractions):
            raise ValueError(
                "A fixed_mixture declaration lists at least two colliders, each with a mole "
                "fraction; one collider is specified_collider."
            )
        total = math.fsum(fractions)
        if abs(total - 1.0) > MOLE_FRACTION_SUM_TOLERANCE:
            raise ValueError(
                f"Mixture mole fractions sum to {total!r}, not 1 (absolute tolerance "
                f"{MOLE_FRACTION_SUM_TOLERANCE:g}); they are never renormalised."
            )
        seen = [repr(c.model_dump(mode="json", exclude={"mole_fraction"})) for c in colliders]
        if len(set(seen)) != len(seen):
            raise ValueError("A mixture component is listed twice.")
    elif colliders:
        raise ValueError(f"A {kind_value} declaration lists no colliders.")
    if default_efficiency is not None and kind_value != KineticsColliderKind.composition_dependent.value:
        raise ValueError("default_third_body_efficiency is only for collider_kind 'composition_dependent'.")


class KineticsApplicabilityDeclaration(SchemaBase):
    """What the stored coefficient is a coefficient *of*. Version 1.

    An attributed claim, stored as made. Every statement is optional: an omitted one is unknown,
    never "universally valid". ``claim_origin`` is required.

    ``collider_kind`` ``specified_collider`` lists one collider and no mole fraction;
    ``fixed_mixture`` lists at least two, each with a mole fraction, summing to one within an
    absolute 1e-9 (never renormalised), none repeated; the other kinds list none.

    :param version: Only the integer ``1``.
    :param scope: Agrees with the determination.
    :param pressure_domain_min_bar: Validity domain, with the maximum.
    :param default_third_body_efficiency: For ``composition_dependent``.

    """

    version: StrictInt
    phase: KineticsPhase | None = None
    observable: KineticsObservable | None = None
    coefficient_basis: KineticsCoefficientBasis | None = None
    scope: KineticsDeterminationTargetKind | None = None
    reaction_order: int | None = Field(default=None, ge=1, le=4, strict=True)
    rate_progress_convention: KineticsRateProgressConvention | None = None
    pressure_dependence: KineticsPressureDependence | None = None
    pressure_domain_min_bar: float | None = Field(default=None, gt=0, allow_inf_nan=False, strict=True)
    pressure_domain_max_bar: float | None = Field(default=None, gt=0, allow_inf_nan=False, strict=True)
    collider_kind: KineticsColliderKind | None = None
    colliders: list[KineticsCollider] = Field(default_factory=list)
    default_third_body_efficiency: float | None = Field(default=None, ge=0, allow_inf_nan=False, strict=True)
    claim_origin: KineticsClaimOrigin

    @field_validator("version")
    @classmethod
    def validate_version(cls, value: int) -> int:
        """``applicability.version`` is a supported version (``1``)."""
        if not version_is_supported(value, KINETICS_APPLICABILITY_VERSIONS):
            raise _version_error("applicability", value, KINETICS_APPLICABILITY_VERSIONS)
        return value

    @model_validator(mode="after")
    def validate_content(self) -> KineticsApplicabilityDeclaration:
        """At least one claim; a pressure domain with both bounds; colliders as the kind needs."""
        if all(
            getattr(self, name) is None
            for name in (
                "phase",
                "observable",
                "coefficient_basis",
                "scope",
                "reaction_order",
                "rate_progress_convention",
                "pressure_dependence",
                "collider_kind",
            )
        ):
            raise ValueError(
                "An applicability declaration must state at least one claim (phase, observable, "
                "coefficient_basis, scope, reaction_order, rate_progress_convention, "
                "pressure_dependence or collider_kind)."
            )
        _check_colliders(self.collider_kind, self.colliders, self.default_third_body_efficiency)
        low, high = self.pressure_domain_min_bar, self.pressure_domain_max_bar
        if (low is None) != (high is None):
            raise ValueError(
                "A pressure domain states both pressure_domain_min_bar and "
                "pressure_domain_max_bar, or neither: a half-open domain is not a stated domain."
            )
        if low is None:
            return self
        if self.pressure_dependence is not KineticsPressureDependence.pressure_dependent:
            raise ValueError(
                "A pressure domain is only for pressure_dependence 'pressure_dependent'; a fixed "
                "pressure is the record's own pressure_bar."
            )
        if low > high:  # type: ignore[operator]
            raise ValueError("pressure_domain_min_bar must be less than or equal to pressure_domain_max_bar.")
        return self


class StoredKineticsApplicabilityDeclaration(KineticsApplicabilityDeclaration):
    """The stored (and served) form: every collider is a species public ref."""

    colliders: list[StoredKineticsCollider] = Field(default_factory=list)  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------


class KineticsMethodKind(str, Enum):
    """How the rate was obtained.

    ``saddle_point_tst`` is transition-state theory at a fixed saddle point, ``variational_tst``
    a variational treatment, ``barrierless_capture`` a capture treatment and ``master_equation``
    a pressure-dependent rate from a solve. ``other`` requires ``method_other_name``.
    """

    experimental = "experimental"
    saddle_point_tst = "saddle_point_tst"
    variational_tst = "variational_tst"
    barrierless_capture = "barrierless_capture"
    master_equation = "master_equation"
    other = "other"


class KineticsBarrierBasis(str, Enum):
    """Which barrier the energies describe: electronic only, or including zero-point energy."""

    classical_electronic = "classical_electronic"
    zpe_corrected = "zpe_corrected"


class KineticsZeroPointTreatment(str, Enum):
    """How zero-point energy was obtained."""

    harmonic_unscaled = "harmonic_unscaled"
    harmonic_scaled = "harmonic_scaled"
    anharmonic = "anharmonic"
    none = "none"


class KineticsGeometryRelation(str, Enum):
    """Whether the stationary-point geometries come from the energy level or another level."""

    optimized_at_energy_level = "optimized_at_energy_level"
    optimized_at_other_level = "optimized_at_other_level"


class KineticsRotorTreatment(str, Enum):
    """How internal rotation and vibration enter the partition functions."""

    rigid_rotor_harmonic_oscillator = "rigid_rotor_harmonic_oscillator"
    hindered_rotor = "hindered_rotor"
    anharmonic = "anharmonic"


class KineticsConformerTreatment(str, Enum):
    """How the conformers of a stationary point enter the rate."""

    single_conformer = "single_conformer"
    boltzmann_ensemble = "boltzmann_ensemble"
    multistructural = "multistructural"


class KineticsPathTreatment(str, Enum):
    """How the reaction paths between the same reactants and products were treated."""

    single_path = "single_path"
    multipath_truncated = "multipath_truncated"
    multipath_full = "multipath_full"


class KineticsDepartureComponent(str, Enum):
    """The part of the declared method a depositor departed from."""

    geometry = "geometry"
    frequencies = "frequencies"
    zero_point_energy = "zero_point_energy"
    electronic_energy = "electronic_energy"
    empirical_correction = "empirical_correction"
    tunneling = "tunneling"
    other = "other"


class KineticsCalculationPurpose(str, Enum):
    """What a supporting calculation supplies to the rate."""

    geometry = "geometry"
    frequency = "frequency"
    electronic_energy = "electronic_energy"
    zero_point_energy = "zero_point_energy"
    irc = "irc"


class KineticsProtocolCalculationRef(SchemaBase):
    """A supporting calculation, by local key or public ref (exactly one), and what it supplies.

    :param calculation_key: Key of a calculation in this request.
    :param calculation_ref: Public ref (``calc_...``).
    """

    calculation_key: str | None = Field(default=None, min_length=1)
    calculation_ref: str | None = Field(default=None, min_length=1)
    purpose: KineticsCalculationPurpose

    @model_validator(mode="after")
    def validate_exactly_one(self) -> KineticsProtocolCalculationRef:
        """Exactly one of ``calculation_key`` or ``calculation_ref``."""
        if (self.calculation_key is None) == (self.calculation_ref is None):
            raise ValueError(
                "A supporting calculation must give exactly one of calculation_key or "
                "calculation_ref."
            )
        return self


_PROTOCOL_STATEMENTS = (
    "method_kind",
    "barrier_basis",
    "zero_point_treatment",
    "geometry_relation",
    "rotor_treatment",
    "conformer_treatment",
    "path_treatment",
)


class KineticsProtocolDeclaration(SchemaBase):
    """The protocol a rate was produced with. Version 1.

    An attributed claim, stored as made and not checked against the linked calculations (a later
    selection step reads what the links show and reports a claim as declared, verified or
    contradicted). Every statement is optional; at least one is required.

    :param version: Only the integer ``1``.
    :param method_other_name: Required for ``other``; refused otherwise.
    :param departures: Omitted: not stated. Empty: none.
    """

    version: StrictInt
    method_kind: KineticsMethodKind | None = None
    method_other_name: _Text | None = Field(default=None, max_length=64)
    barrier_basis: KineticsBarrierBasis | None = None
    zero_point_treatment: KineticsZeroPointTreatment | None = None
    geometry_relation: KineticsGeometryRelation | None = None
    rotor_treatment: KineticsRotorTreatment | None = None
    conformer_treatment: KineticsConformerTreatment | None = None
    path_treatment: KineticsPathTreatment | None = None
    departures: list[KineticsDepartureComponent] | None = None
    supporting_calculations: list[KineticsProtocolCalculationRef] = Field(default_factory=list)

    @field_validator("version")
    @classmethod
    def validate_version(cls, value: int) -> int:
        """``protocol.version`` is a supported version (``1``)."""
        if not version_is_supported(value, KINETICS_PROTOCOL_VERSIONS):
            raise _version_error("protocol", value, KINETICS_PROTOCOL_VERSIONS)
        return value

    @model_validator(mode="after")
    def validate_content(self) -> KineticsProtocolDeclaration:
        """``method_other_name`` for ``other`` only; at least one statement; no repeated calculation."""
        if self.method_kind is KineticsMethodKind.other and self.method_other_name is None:
            raise ValueError("method_other_name is required when method_kind is 'other'.")
        if self.method_kind is not KineticsMethodKind.other and self.method_other_name is not None:
            raise ValueError("method_other_name is only allowed when method_kind is 'other'.")
        if (
            all(getattr(self, name) is None for name in _PROTOCOL_STATEMENTS)
            and self.departures is None
            and not self.supporting_calculations
        ):
            raise ValueError(
                "A protocol declaration must state at least one of method_kind, barrier_basis, "
                "zero_point_treatment, geometry_relation, rotor_treatment, conformer_treatment, "
                "path_treatment, departures or supporting_calculations."
            )
        seen = [tuple(sorted(c.model_dump(mode="json").items())) for c in self.supporting_calculations]
        if len(set(seen)) != len(seen):
            raise ValueError("supporting_calculations must not repeat a calculation and purpose.")
        return self


class StoredKineticsProtocolCalculationRef(SchemaBase):
    """A supporting calculation as stored and read back: public ref and purpose."""

    calculation_ref: str = Field(min_length=1)
    purpose: KineticsCalculationPurpose


class StoredKineticsProtocolDeclaration(KineticsProtocolDeclaration):
    """The stored (and served) form: every supporting calculation is a public ref."""

    supporting_calculations: list[StoredKineticsProtocolCalculationRef] = Field(  # type: ignore[assignment]
        default_factory=list
    )


def _version_error(block: str, value: Any, supported: frozenset[int]) -> CodedValidationError:
    versions = sorted(supported)
    return CodedValidationError(
        W_KINETICS_DECLARATION_VERSION_UNSUPPORTED,
        f"{block}.version {value!r} is not supported; supported versions: {versions}.",
        context={"field": f"{block}.version", "version": value, "supported_versions": versions},
        message_prefix=False,
    )


# ---------------------------------------------------------------------------
# Rules that need the record's own columns
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class KineticsRecordFacts:
    """The columns of a record that a declaration must not contradict.

    ``None`` means the fact is not known at this layer (a resolved row has no request), and a
    rule that needs it is skipped, never assumed.
    """

    direction: str | None = None
    scientific_origin: str | None = None
    model_kind: str | None = None
    is_third_body: bool | None = None
    n_reactants: int | None = None
    n_products: int | None = None
    pressure_context: str | None = None
    pressure_bar: float | None = None
    has_network_kinetics: bool | None = None
    has_falloff: bool | None = None
    has_third_body_efficiencies: bool | None = None


def _has_field(payload: Any, name: str) -> bool:
    if isinstance(payload, dict):
        return name in payload
    return name in getattr(type(payload), "model_fields", {})


def kinetics_record_facts(payload: Any) -> KineticsRecordFacts:
    """The facts of a request payload (standalone upload, or a bundle kinetics block).

    A bundle kinetics block carries scalar Arrhenius fits only, so it has no falloff block and no
    third-body efficiencies: those facts are ``False``, not unknown.
    """
    reaction = _get(payload, "reaction")
    n_reactants: int | None
    n_products: int | None
    if reaction is not None:
        n_reactants = len(_get(reaction, "reactants") or [])
        n_products = len(_get(reaction, "products") or [])
    else:
        keys = _get(payload, "reactant_keys")
        n_reactants = len(keys) if keys is not None else None
        product_keys = _get(payload, "product_keys")
        n_products = len(product_keys) if product_keys is not None else None
    model_kind = _value(_get(payload, "model_kind"))
    if _has_field(payload, "falloff"):
        has_falloff = _get(payload, "falloff") is not None
    else:
        has_falloff = model_kind in _FALLOFF_MODELS
    if _has_field(payload, "third_body_efficiencies"):
        has_efficiencies = bool(_get(payload, "third_body_efficiencies"))
    else:
        has_efficiencies = False
    return KineticsRecordFacts(
        direction=_value(_get(payload, "direction")),
        scientific_origin=_value(_get(payload, "scientific_origin")),
        model_kind=model_kind,
        is_third_body=_get(payload, "is_third_body"),
        n_reactants=n_reactants or None,
        n_products=n_products or None,
        pressure_context=_value(_get(payload, "pressure_context")),
        pressure_bar=_get(payload, "pressure_bar"),
        has_network_kinetics=_get(payload, "network_kinetics_ref") is not None,
        has_falloff=has_falloff,
        has_third_body_efficiencies=has_efficiencies,
    )


def _contradiction(field: str, message: str) -> tuple[str, str]:
    return (W_KINETICS_DECLARATION_CONTRADICTS_RECORD, f"applicability.{field}: {message}")


def kinetics_applicability_error(
    applicability: Any, facts: KineticsRecordFacts, *, determination_target_kind: Any = None
) -> tuple[str, str] | None:
    """``(code, message)`` when an applicability declaration contradicts the record, else ``None``.

    The stored columns stay authoritative, so a declaration that disagrees with one is refused
    rather than reconciled. A column that is not stated (a null pressure context) is not a
    contradiction: the declaration is then the only statement.
    """
    if applicability is None:
        return None
    dependence = _value(_get(applicability, "pressure_dependence"))
    basis = _value(_get(applicability, "coefficient_basis"))
    collider_kind = _value(_get(applicability, "collider_kind"))
    scope = _value(_get(applicability, "scope"))
    model = facts.model_kind
    pd_model = model in _PRESSURE_DEPENDENT_MODELS

    if scope is not None and determination_target_kind is not None:
        target_kind = _value(determination_target_kind)
        if scope != target_kind:
            return _contradiction(
                "scope",
                f"declares scope {scope!r} but the determination's target is {target_kind!r}.",
            )

    if dependence in {"independent", "high_pressure_limit", "fixed_pressure"}:
        if pd_model:
            return _contradiction(
                "pressure_dependence",
                f"a {model} record carries its own pressure dependence and cannot be "
                f"declared {dependence}.",
            )
        if facts.has_network_kinetics:
            return _contradiction(
                "pressure_dependence",
                f"a network-linked record carries its pressure dependence in the solve and "
                f"cannot be declared {dependence}.",
            )
    if dependence == "independent" and facts.pressure_context is not None:
        return _contradiction(
            "pressure_dependence",
            f"declares pressure independence but the record's pressure_context is "
            f"{facts.pressure_context!r}.",
        )
    if dependence == "high_pressure_limit" and facts.pressure_context not in (None, "high_p_limit"):
        return _contradiction(
            "pressure_dependence",
            f"declares the high-pressure limit but the record's pressure_context is "
            f"{facts.pressure_context!r}.",
        )
    if dependence == "fixed_pressure" and (
        facts.pressure_context != "apparent_at_pressure" or facts.pressure_bar is None
    ):
        return _contradiction(
            "pressure_dependence",
            "a fixed_pressure coefficient states its pressure in the record itself: "
            "pressure_context 'apparent_at_pressure' and pressure_bar are required.",
        )
    if dependence == "pressure_dependent":
        if facts.pressure_context in ("high_p_limit", "apparent_at_pressure"):
            return _contradiction(
                "pressure_dependence",
                f"declares a pressure-dependent model but the record's pressure_context is "
                f"{facts.pressure_context!r}.",
            )
        if (
            model is not None
            and not pd_model
            and not facts.has_network_kinetics
            and facts.pressure_context != "pressure_dependent"
        ):
            return _contradiction(
                "pressure_dependence",
                f"a {model} record with no network link and no pressure_dependent context "
                "carries no pressure dependence to declare.",
            )

    if basis == "elementary_coefficient" and facts.is_third_body:
        return _contradiction(
            "coefficient_basis",
            "an elementary coefficient cannot be a third-body kernel (is_third_body is true).",
        )
    if basis == "third_body_kernel" and (facts.is_third_body is False or pd_model):
        return _contradiction(
            "coefficient_basis",
            "a third-body kernel is the coefficient of a simple +M reaction "
            "(is_third_body, no falloff, PLOG or Chebyshev form).",
        )
    if basis == "composition_effective_coefficient" and collider_kind not in (None, "fixed_mixture"):
        return _contradiction(
            "coefficient_basis",
            "a composition-effective coefficient is evaluated for one declared mixture; "
            f"the collider declaration is {collider_kind!r}.",
        )

    if collider_kind == "not_dependent" and (
        facts.is_third_body or facts.has_falloff or facts.has_third_body_efficiencies
    ):
        return _contradiction(
            "collider_kind",
            "declares no collider dependence but the record is a third-body or falloff "
            "rate with efficiencies.",
        )
    if collider_kind == "fixed_mixture" and basis not in (None, "composition_effective_coefficient"):
        return _contradiction(
            "collider_kind",
            f"a fixed_mixture coefficient is composition-effective, not {basis!r}.",
        )
    if collider_kind == "composition_dependent" and (
        facts.is_third_body is False
        and facts.has_falloff is False
        and facts.has_third_body_efficiencies is False
        and model is not None
        and model not in _FALLOFF_MODELS
    ):
        return _contradiction(
            "collider_kind",
            "composition-dependent applicability needs the record's own third-body or "
            "falloff treatment.",
        )

    order = _get(applicability, "reaction_order")
    # The coefficient's order is the number of species on the side it is a rate *of*: the reactants
    # for a forward rate, the products for a reverse one. A net rate, or one whose direction is not
    # stated, has no order this layer can state, so the rule is skipped, never assumed.
    side = {"forward": facts.n_reactants, "reverse": facts.n_products}.get(facts.direction or "")
    if order is not None and side is not None and facts.is_third_body is not None:
        expected = side + (1 if facts.is_third_body and not facts.has_falloff else 0)
        if order != expected:
            return _contradiction(
                "reaction_order",
                f"declares order {order} but the {facts.direction} coefficient has order {expected} "
                f"({side} {'reactant' if facts.direction == 'forward' else 'product'}(s)"
                + (", plus the third body" if expected > side else "")
                + ").",
            )
    return None


def kinetics_protocol_origin_error(protocol: Any, facts: KineticsRecordFacts) -> tuple[str, str] | None:
    """``(code, message)`` when a protocol's method contradicts the record's origin, else ``None``."""
    kind = _value(_get(protocol, "method_kind"))
    if kind is None or facts.scientific_origin is None:
        return None
    if kind == KineticsMethodKind.experimental.value:
        if facts.scientific_origin != ScientificOriginKind.experimental.value:
            return (
                W_KINETICS_DECLARATION_CONTRADICTS_RECORD,
                "protocol.method_kind 'experimental' contradicts the record's "
                f"scientific_origin {facts.scientific_origin!r}.",
            )
    elif kind in {
        KineticsMethodKind.saddle_point_tst.value,
        KineticsMethodKind.variational_tst.value,
        KineticsMethodKind.barrierless_capture.value,
        KineticsMethodKind.master_equation.value,
    }:
        if facts.scientific_origin != ScientificOriginKind.computed.value:
            return (
                W_KINETICS_DECLARATION_CONTRADICTS_RECORD,
                f"protocol.method_kind {kind!r} is a computed method but the record's "
                f"scientific_origin is {facts.scientific_origin!r}.",
            )
    return None


# ---------------------------------------------------------------------------
# The one rule workflows, services and the client builder share
# ---------------------------------------------------------------------------


def _model_error(block: str, model: type[SchemaBase], raw: Any) -> tuple[str, str] | None:
    try:
        model.model_validate(raw)
    except ValidationError as exc:
        first = exc.errors()[0]
        where = ".".join(str(part) for part in first["loc"]) or block
        return (
            W_KINETICS_DECLARATION_INVALID,
            f"The {block} declaration is not valid at {where}: {first['msg']}",
        )
    return None


def _raw(declaration: Any) -> Any:
    return declaration if isinstance(declaration, dict) else declaration.model_dump(mode="json")


def kinetics_declaration_context(code: str, message: str) -> dict[str, str]:
    """The ``context`` of a refusal from :func:`kinetics_declaration_error`: which field, and what is missing."""
    head = f"{code} {message}".split(":")[0].lower()
    if "protocol" in head:
        field = "protocol"
    elif "applicability" in head:
        field = "applicability"
    else:
        field = "determination"
    context = {"field": field}
    if "states its own direction" in message:
        context["missing"] = "direction"
    elif "names its source" in message:
        context["missing"] = "source"
    return context


@producer_rule
def kinetics_declaration_error(
    payload: Any, *, has_source: bool | None = None
) -> tuple[str, str] | None:
    """A kinetics record's optional determination, applicability and protocol must be coherent.

    A determination names one of ``determination_ref`` or ``key`` with a target, and a record that
    joins one states its ``direction`` and a source. ``applicability`` and ``protocol`` are versioned
    (only ``1``); unknown fields and values are refused, as is an applicability that contradicts a
    stored column of the record, or an experimental method on a computed record or the reverse.

    :param has_source: Whether the record names a source attribution. The caller that knows
        (the standalone request, a bundle root, a service holding resolved ids) passes it;
        ``None`` means this layer cannot tell and the source rule is left to one that can.
    """
    determination = _get(payload, "determination")
    error = kinetics_determination_error(determination)
    if error is not None:
        return error
    facts = kinetics_record_facts(payload)
    if determination is not None:
        if facts.direction is None:
            return (
                W_KINETICS_DETERMINATION_INVALID,
                "A record that joins a determination states its own direction (forward or "
                "reverse of the reaction as stored); it is never inferred.",
            )
        if has_source is False:
            return (
                W_KINETICS_DETERMINATION_INVALID,
                "A record that joins a determination names its source: literature or "
                "workflow_tool_release. A determination key is scoped to a source.",
            )
    applicability = _get(payload, "applicability")
    if applicability is not None:
        version = _get(applicability, "version")
        if not version_is_supported(version, KINETICS_APPLICABILITY_VERSIONS):
            err = _version_error("applicability", version, KINETICS_APPLICABILITY_VERSIONS)
            return err.code, err.detail
        shape = _model_error("applicability", KineticsApplicabilityDeclaration, _raw(applicability))
        if shape is not None:
            return shape
        contradiction = kinetics_applicability_error(
            applicability,
            facts,
            determination_target_kind=(
                _get(determination, "target_kind") if determination is not None else None
            ),
        )
        if contradiction is not None:
            return contradiction
    protocol = _get(payload, "protocol")
    if protocol is not None:
        version = _get(protocol, "version")
        if not version_is_supported(version, KINETICS_PROTOCOL_VERSIONS):
            err = _version_error("protocol", version, KINETICS_PROTOCOL_VERSIONS)
            return err.code, err.detail
        shape = _model_error("protocol", KineticsProtocolDeclaration, _raw(protocol))
        if shape is not None:
            return shape
        origin = kinetics_protocol_origin_error(protocol, facts)
        if origin is not None:
            return origin
    return None
