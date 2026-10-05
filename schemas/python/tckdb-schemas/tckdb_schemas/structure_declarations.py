"""Scientific declarations behind the calculation, conformer and transition-state energy selectors.

Two optional, attributed claims a depositor can make that the upload contract could not state:

* **an actual protocol** (:class:`ActualProtocolDeclaration`) on a calculation: the recipe that was
  *actually* run, in the facts a later comparison needs and no more. A level-of-theory label names a
  method and a basis; it does not say which electronic state or root, which reference, which
  relativistic treatment, which core electrons were correlated, which numerical approximations
  were material or which corrections the stored number includes.
* **a structure determination** (:class:`StructureDeterminationDeclaration`) on a conformer or
  transition-state upload: one source-attributed claim about a defined geometry, basin or saddle,
  with the calculations that play each role (energy, optimization, curvature, correction,
  connectivity) pinned by a local key or a public ref.

Both are *claims*, stored as made. None is inferred from anything else and none is backfilled: a
calculation or record deposited without one reads ``null``, which means "not stated", never
"standard", never "gas phase" and never "equal to another record's null".

**Three states for a fact, not two.** Every single fact is ``known`` (with a value),
``unknown`` (the producer says it does not know) or ``not_applicable`` (the fact does not exist for
this recipe, for example no effective core potential). A field that is *omitted* is *not stated*;
a comparison treats ``unknown`` and not stated alike (neither can establish an equivalence) but the
record keeps what the producer said. A list-valued fact is omitted (not stated) or a list, where an
empty list is the claim "none".

House rule for the vocabulary, as for the kinetics and thermo declarations: it stores only what a
job states, it starts small, and a value is added when a real deposit needs it. Adding an enum member
or an optional field is additive within version 1; removing or re-meaning one is a new version. The
accepted versions are :data:`ACTUAL_PROTOCOL_VERSIONS`; any other is refused.

Scientific references inside a declaration are local keys or public refs, never database ids.
"""

from __future__ import annotations

from enum import Enum
from typing import Annotated, Any

from pydantic import (
    Field,
    StrictInt,
    StringConstraints,
    field_validator,
    model_validator,
)

from tckdb_schemas.coded_error import CodedValidationError
from tckdb_schemas.common import SchemaBase
from tckdb_schemas.enums import (
    CoreTreatment,
    StructureDeterminationQuantity,
    StructureDeterminationTargetKind,
    StructureSourceRole,
)
from tckdb_schemas.fragments.refs import WorkflowToolReleaseRef
from tckdb_schemas.literature import LiteratureUploadRequest
from tckdb_schemas.local_key_codes import W_CALCULATION_KEY_UNDECLARED, undeclared_key_error
from tckdb_schemas.producer_rule import producer_rule

__all__ = [
    "ACTUAL_PROTOCOL_VERSIONS",
    "W_STRUCTURE_DECLARATION_INVALID",
    "W_STRUCTURE_DECLARATION_VERSION_UNSUPPORTED",
    "W_STRUCTURE_DETERMINATION_INVALID",
    "ActualProtocolDeclaration",
    "ConstraintTreatment",
    "CoreTreatmentFact",
    "DeclarationOrigin",
    "DeclarationSource",
    "ConstraintFact",
    "ElectronicStateFact",
    "FactState",
    "GeometrySide",
    "IncludedCorrection",
    "NumericalApproximation",
    "NumericalApproximationKind",
    "RelativisticFact",
    "RelativisticTreatment",
    "SolvationFact",
    "SolvationKind",
    "SpinFact",
    "SpinKind",
    "StructureCalculationPin",
    "StructureDeterminationDeclaration",
    "StructureEnergyConvention",
    "StructureEvaluatedGeometry",
    "StructureSourceDeclaration",
    "TokenFact",
    "ZeroPointTreatment",
    "assert_structure_pin_keys_declared",
    "structure_determination_error",
]

#: A determination contradicts itself: a source or geometry pin with neither or both locators,
#: a quantity without (or with an unneeded) energy convention, a repeated source, no energy source
#: for an energy quantity, or no source attribution.
W_STRUCTURE_DETERMINATION_INVALID = "structure_determination_invalid"
#: A declaration carries a ``version`` this server does not accept.
W_STRUCTURE_DECLARATION_VERSION_UNSUPPORTED = "structure_declaration_version_unsupported"
#: A declaration that reached a service without passing request validation and fails it there.
W_STRUCTURE_DECLARATION_INVALID = "structure_declaration_invalid"

#: Declaration versions the server accepts.
ACTUAL_PROTOCOL_VERSIONS: frozenset[int] = frozenset({1})

#: Free text, trimmed and never blank.
_Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
#: A lower-cased name (an effective core potential, a dispersion correction, a setting): trimmed, 1-64 characters,
#: no control characters. Lower-cased so that ``D3BJ`` and ``d3bj`` are one claim.
_Token = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True, to_lower=True, min_length=1, max_length=64, pattern=r"^[^\x00-\x1f\x7f]+$"
    ),
]


def version_is_supported(value: Any, supported: frozenset[int]) -> bool:
    """Exactly a supported integer: ``True``, ``"1"`` and ``1.0`` are not version 1."""
    return type(value) is int and value in supported


# ---------------------------------------------------------------------------
# Vocabulary
# ---------------------------------------------------------------------------


class FactState(str, Enum):
    """Whether one fact of a recipe is stated: ``known`` (with a value), ``unknown`` or ``not_applicable``."""

    known = "known"
    unknown = "unknown"
    not_applicable = "not_applicable"


class DeclarationOrigin(str, Enum):
    """Who produced the declaration: the depositor, or a parser reading the program output."""

    producer_declared = "producer_declared"
    parser_derived = "parser_derived"


class SpinKind(str, Enum):
    """The spin treatment of the reference wavefunction (restricted, unrestricted, restricted open-shell)."""

    restricted = "restricted"
    unrestricted = "unrestricted"
    restricted_open = "restricted_open"


class RelativisticTreatment(str, Enum):
    """The scalar-relativistic Hamiltonian: none (non-relativistic), Douglas-Kroll-Hess, X2C or ZORA."""

    none = "none"
    dkh = "dkh"
    x2c = "x2c"
    zora = "zora"


class SolvationKind(str, Enum):
    """The environment the energy refers to. ``gas_phase`` is a claim, never the reading of an absent field."""

    gas_phase = "gas_phase"
    implicit_solvent = "implicit_solvent"
    explicit_solvent = "explicit_solvent"


class ConstraintTreatment(str, Enum):
    """Whether the geometry search ran under constraints."""

    unconstrained = "unconstrained"
    constrained = "constrained"


class NumericalApproximationKind(str, Enum):
    """A numerical approximation that can change a number by more than rounding."""

    density_fitting = "density_fitting"
    resolution_of_identity = "resolution_of_identity"
    chain_of_spheres = "chain_of_spheres"
    local_correlation = "local_correlation"
    pair_natural_orbital = "pair_natural_orbital"
    integral_screening = "integral_screening"
    integration_grid = "integration_grid"
    scf_convergence_threshold = "scf_convergence_threshold"


class IncludedCorrection(str, Enum):
    """A correction the stored number already includes."""

    zero_point_energy = "zero_point_energy"
    thermal_energy = "thermal_energy"
    core_valence = "core_valence"
    scalar_relativistic = "scalar_relativistic"
    diagonal_born_oppenheimer = "diagonal_born_oppenheimer"
    higher_order_correlation = "higher_order_correlation"
    basis_set_extrapolation = "basis_set_extrapolation"
    dispersion = "dispersion"
    counterpoise = "counterpoise"


class ZeroPointTreatment(str, Enum):
    """How the zero-point energy inside a supplied E0 was obtained."""

    unscaled_harmonic = "unscaled_harmonic"
    scaled_harmonic = "scaled_harmonic"
    anharmonic = "anharmonic"
    composite_recipe = "composite_recipe"


class GeometrySide(str, Enum):
    """Which geometry of a calculation a pin names: its ``output`` geometry or its ``input`` geometry."""

    output = "output"
    input = "input"


# ---------------------------------------------------------------------------
# Facts
# ---------------------------------------------------------------------------


class _Fact(SchemaBase):
    """One fact of a recipe: a state, and a value exactly when the state is ``known``."""

    state: FactState

    def _values(self) -> dict[str, Any]:
        raise NotImplementedError

    @model_validator(mode="after")
    def validate_state_matches_value(self) -> _Fact:
        """``value`` is given exactly when ``state`` is ``known``."""
        values = self._values()
        if self.state is FactState.known:
            missing = [name for name, value in values.items() if value is None]
            if missing:
                raise ValueError(f"A known fact states its {', '.join(missing)}.")
        else:
            given = [name for name, value in values.items() if value is not None]
            if given:
                raise ValueError(
                    f"A fact whose state is {self.state.value!r} states no {', '.join(given)}; "
                    "use state 'known' to state one."
                )
        return self


class SpinFact(_Fact):
    """The reference spin treatment."""

    value: SpinKind | None = None

    def _values(self) -> dict[str, Any]:
        return {"value": self.value}


class RelativisticFact(_Fact):
    """The scalar-relativistic treatment."""

    value: RelativisticTreatment | None = None

    def _values(self) -> dict[str, Any]:
        return {"value": self.value}


class CoreTreatmentFact(_Fact):
    """Which electrons the post-SCF method correlated."""

    value: CoreTreatment | None = None

    def _values(self) -> dict[str, Any]:
        return {"value": self.value}


class ConstraintFact(_Fact):
    """Whether the geometry search was constrained."""

    value: ConstraintTreatment | None = None

    def _values(self) -> dict[str, Any]:
        return {"value": self.value}


class TokenFact(_Fact):
    """A named fact (an effective core potential, an auxiliary basis, a dispersion correction).

    ``not_applicable`` means the recipe used none.
    """

    value: _Token | None = None

    def _values(self) -> dict[str, Any]:
        return {"value": self.value}


class ElectronicStateFact(_Fact):
    """The electronic state: ``root`` 0 is the ground-state root of the stated spin and symmetry."""

    root: Annotated[StrictInt, Field(ge=0, le=999)] | None = None

    def _values(self) -> dict[str, Any]:
        return {"root": self.root}


class SolvationFact(_Fact):
    """The environment. ``detail`` names the solvent and model of an implicit or explicit environment.

    ``detail`` is optional on a known environment; a comparison reads an unstated detail as unknown.
    """

    kind: SolvationKind | None = None
    detail: _Token | None = None

    def _values(self) -> dict[str, Any]:
        return {"kind": self.kind}

    @model_validator(mode="after")
    def validate_detail(self) -> SolvationFact:
        """``detail`` only for a solvent environment."""
        if self.detail is not None and self.kind in (None, SolvationKind.gas_phase):
            raise ValueError("A gas-phase or unstated environment has no solvent detail.")
        return self


class NumericalApproximation(SchemaBase):
    """One material numerical approximation, with its setting when the producer states one."""

    kind: NumericalApproximationKind
    setting: _Token | None = None


class DeclarationSource(SchemaBase):
    """Where the declaration came from: who made the claim and with which tool and version.

    :param origin: ``producer_declared`` or ``parser_derived``.
    :param producer: The producing tool or person-facing label (at most 64 characters).
    :param producer_version: Its version.
    :param parser_version: The output parser's version, for a ``parser_derived`` declaration.
    """

    origin: DeclarationOrigin
    producer: _Token | None = None
    producer_version: _Token | None = None
    parser_version: _Token | None = None

    @model_validator(mode="after")
    def validate_parser_version(self) -> DeclarationSource:
        """``parser_version`` only for a ``parser_derived`` declaration."""
        if self.parser_version is not None and self.origin is not DeclarationOrigin.parser_derived:
            raise ValueError("parser_version is only stated on a parser_derived declaration.")
        return self


_FACT_FIELDS = (
    "electronic_state",
    "spin_treatment",
    "relativistic_treatment",
    "effective_core_potential",
    "core_treatment",
    "auxiliary_basis",
    "dispersion",
    "solvation",
    "constraints",
)

_DOI = Annotated[str, StringConstraints(strip_whitespace=True, to_lower=True, pattern=r"^10\.\d{4,9}/\S+$", max_length=200)]


class ActualProtocolDeclaration(SchemaBase):
    """The recipe a calculation actually ran. Version 1.

    Complements the level-of-theory reference (method, basis, auxiliary basis, dispersion, solvent) with the
    facts the label cannot carry. A declaration that restates a level-of-theory field is *compared* with it
    when a selection reads the record, and a disagreement is a finding; neither side wins by upload order.

    Every statement is optional; at least one is required. A fact left out is not stated. Operational
    settings that do not change the number (threads, memory, scratch) have no place here.

    :param version: Only the integer ``1``.
    :param source: Who made the claim.
    :param numerical_approximations: Omitted: not stated. Empty: none.
    :param included_corrections: Corrections the stored number already includes. Omitted: not stated. Empty: none.
    :param supporting_dois: Literature that documents the recipe (DOIs). Omitted: not stated. Empty: none.
    """

    version: StrictInt
    source: DeclarationSource
    electronic_state: ElectronicStateFact | None = None
    spin_treatment: SpinFact | None = None
    relativistic_treatment: RelativisticFact | None = None
    effective_core_potential: TokenFact | None = None
    core_treatment: CoreTreatmentFact | None = None
    auxiliary_basis: TokenFact | None = None
    dispersion: TokenFact | None = None
    solvation: SolvationFact | None = None
    constraints: ConstraintFact | None = None
    numerical_approximations: list[NumericalApproximation] | None = None
    included_corrections: list[IncludedCorrection] | None = None
    supporting_dois: list[_DOI] | None = Field(default=None, max_length=20)

    @field_validator("version")
    @classmethod
    def validate_version(cls, value: int) -> int:
        """``version`` is a supported version (``1``)."""
        if not version_is_supported(value, ACTUAL_PROTOCOL_VERSIONS):
            raise CodedValidationError(
                W_STRUCTURE_DECLARATION_VERSION_UNSUPPORTED,
                f"actual_protocol_declaration.version {value!r} is not supported; this server accepts "
                f"{sorted(ACTUAL_PROTOCOL_VERSIONS)}.",
                context={"block": "actual_protocol_declaration", "version": value},
                message_prefix=False,
            )
        return value

    @model_validator(mode="after")
    def validate_content(self) -> ActualProtocolDeclaration:
        """At least one statement; no repeated approximation kind or correction."""
        if (
            all(getattr(self, name) is None for name in _FACT_FIELDS)
            and self.numerical_approximations is None
            and self.included_corrections is None
        ):
            raise ValueError(
                "An actual protocol declaration states at least one of "
                f"{', '.join(_FACT_FIELDS)}, numerical_approximations or included_corrections."
            )
        if self.numerical_approximations is not None:
            kinds = [a.kind for a in self.numerical_approximations]
            if len(set(kinds)) != len(kinds):
                raise ValueError("numerical_approximations must not repeat a kind.")
        if self.included_corrections is not None and len(set(self.included_corrections)) != len(
            self.included_corrections
        ):
            raise ValueError("included_corrections must not repeat a correction.")
        return self


# ---------------------------------------------------------------------------
# Determination
# ---------------------------------------------------------------------------


class StructureCalculationPin(SchemaBase):
    """A calculation, by local key or public ref (exactly one).

    :param calculation_key: Key of a calculation in this request.
    :param calculation_ref: Public ref (``calc_...``) of a calculation already deposited.
    """

    calculation_key: str | None = Field(default=None, min_length=1)
    calculation_ref: str | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def validate_exactly_one(self) -> StructureCalculationPin:
        """Exactly one of ``calculation_key`` or ``calculation_ref``."""
        if (self.calculation_key is None) == (self.calculation_ref is None):
            raise ValueError("A calculation pin gives exactly one of calculation_key or calculation_ref.")
        return self


class StructureSourceDeclaration(StructureCalculationPin):
    """One calculation and the role it plays in the determination (one calculation can play several)."""

    role: StructureSourceRole


class StructureEvaluatedGeometry(StructureCalculationPin):
    """The geometry the determination is evaluated at: the named calculation's ``output`` or ``input`` geometry.

    A calculation with several geometries on that side does not pin one; the declaration is refused as
    ambiguous when it is resolved.
    """

    side: GeometrySide = GeometrySide.output


class StructureEnergyConvention(SchemaBase):
    """What a supplied E0 includes. Required with ``zero_kelvin_energy``; refused otherwise.

    :param zero_point_treatment: How the zero-point energy inside the number was obtained.
    :param included_corrections: Corrections the number includes. Omitted: not stated. Empty: none.
    """

    zero_point_treatment: ZeroPointTreatment
    included_corrections: list[IncludedCorrection] | None = None

    @model_validator(mode="after")
    def validate_unique(self) -> StructureEnergyConvention:
        """No repeated correction."""
        if self.included_corrections is not None and len(set(self.included_corrections)) != len(
            self.included_corrections
        ):
            raise ValueError("included_corrections must not repeat a correction.")
        return self


class StructureDeterminationDeclaration(SchemaBase):
    """One source-attributed claim about a geometry, basin or saddle, and the calculations behind it.

    The owner is the upload's own: a conformer upload's species entry (and, for ``conformer_basin``, the
    observation it creates), a transition-state upload's transition state entry. The backend finds or
    creates the determination from the owner, the source attribution and ``key``; stating the same key again
    joins it and must restate the same content.

    :param key: Source-scoped key (at most 128 characters).
    :param target_kind: ``geometry``, ``conformer_basin`` (conformer uploads) or ``saddle_point``
        (transition-state uploads).
    :param quantity: The energy this determination supplies; omitted for an evidence-only determination.
    :param energy_convention: Required with ``zero_kelvin_energy``; refused otherwise.
    :param actual_recipe: The composite recipe actually used, when it differs from or adds to any one
        calculation's own declaration.
    :param evaluated_geometry: The geometry the claim is about.
    :param sources: The calculations and the role each plays.
    :param literature: Source attribution (a paper), or
    :param workflow_tool_release: source attribution (a workflow tool release). At least one is required,
        because a key is scoped to a source.
    """

    key: _Text = Field(max_length=128)
    target_kind: StructureDeterminationTargetKind
    quantity: StructureDeterminationQuantity | None = None
    energy_convention: StructureEnergyConvention | None = None
    actual_recipe: ActualProtocolDeclaration | None = None
    evaluated_geometry: StructureEvaluatedGeometry
    sources: list[StructureSourceDeclaration] = Field(min_length=1, max_length=32)
    literature: LiteratureUploadRequest | None = None
    workflow_tool_release: WorkflowToolReleaseRef | None = None

    @model_validator(mode="after")
    def validate_determination(self) -> StructureDeterminationDeclaration:
        """The determination does not contradict itself."""
        error = structure_determination_error(self)
        if error is not None:
            code, message = error
            raise CodedValidationError(
                code, message, context={"field": "structure_determinations"}, message_prefix=False
            )
        return self


def _get(obj: Any, key: str) -> Any:
    if isinstance(obj, dict):
        return obj.get(key)
    return getattr(obj, key, None)


def _enum_value(value: Any) -> Any:
    return value.value if isinstance(value, Enum) else value


@producer_rule
def structure_determination_error(determination: Any) -> tuple[str, str] | None:
    """A structure determination states a consistent claim (refused with ``structure_determination_invalid``).

    * ``quantity`` ``zero_kelvin_energy`` needs ``energy_convention``; ``electronic_energy`` and an omitted
      quantity take none.
    * A determination that states a ``quantity`` pins at least one ``energy`` source.
    * A source is stated once per role and calculation.
    * A source attribution (``literature`` or ``workflow_tool_release``) is required: a key is scoped to a source.
    * ``target_kind`` ``conformer_basin`` belongs to a conformer upload and ``saddle_point`` to a
      transition-state upload (refused where the owner is resolved).

    Returns ``(code, message)`` or ``None``. Pure and transport-independent: the request validator, the
    workflows and the client builder all call it.
    """
    if determination is None:
        return None
    quantity = _enum_value(_get(determination, "quantity"))
    convention = _get(determination, "energy_convention")
    if quantity == StructureDeterminationQuantity.zero_kelvin_energy.value and convention is None:
        return (
            W_STRUCTURE_DETERMINATION_INVALID,
            "A zero_kelvin_energy determination states its energy_convention (how the zero-point "
            "energy was obtained); it is never assumed.",
        )
    if quantity != StructureDeterminationQuantity.zero_kelvin_energy.value and convention is not None:
        return (
            W_STRUCTURE_DETERMINATION_INVALID,
            "energy_convention describes a supplied E0 and goes only with quantity zero_kelvin_energy.",
        )
    sources = _get(determination, "sources") or []
    seen: set[tuple[Any, Any, Any]] = set()
    for source in sources:
        entry = (
            _enum_value(_get(source, "role")),
            _get(source, "calculation_key"),
            _get(source, "calculation_ref"),
        )
        if entry in seen:
            return (
                W_STRUCTURE_DETERMINATION_INVALID,
                "A determination states each (role, calculation) source once.",
            )
        seen.add(entry)
    if quantity is not None and not any(
        _enum_value(_get(s, "role")) == StructureSourceRole.energy.value for s in sources
    ):
        return (
            W_STRUCTURE_DETERMINATION_INVALID,
            "A determination that states a quantity pins the calculation that supplies it: add a source "
            "with role 'energy'.",
        )
    if _get(determination, "literature") is None and _get(determination, "workflow_tool_release") is None:
        return (
            W_STRUCTURE_DETERMINATION_INVALID,
            "A determination states its source attribution: give literature or workflow_tool_release.",
        )
    return None


def assert_structure_pin_keys_declared(
    determinations: list[StructureDeterminationDeclaration], declared: set[str]
) -> None:
    """Every ``calculation_key`` a determination pins names a calculation the same upload declared.

    The request validator and the workflow call this, so both refuse an undeclared key with the same code
    (``calculation_key_undeclared``) and context. A ``calculation_ref`` is not checked here: it names a
    calculation already deposited, which only the database can confirm.
    """
    for index, determination in enumerate(determinations):
        pins: list[tuple[str, StructureCalculationPin]] = [
            (f"structure_determinations[{index}].sources[{i}]", source)
            for i, source in enumerate(determination.sources)
        ]
        pins.append((f"structure_determinations[{index}].evaluated_geometry", determination.evaluated_geometry))
        for field, pin in pins:
            if pin.calculation_key is not None and pin.calculation_key not in declared:
                raise undeclared_key_error(
                    W_CALCULATION_KEY_UNDECLARED,
                    f"{field}.calculation_key '{pin.calculation_key}' does not name a calculation declared in "
                    "this upload. Put a matching 'key' on the calculation it means.",
                    field=field,
                    key=pin.calculation_key,
                    declared=declared,
                )
