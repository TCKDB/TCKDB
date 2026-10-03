"""Scientific declarations a thermo record can carry: its target and its protocol.

Two optional, attributed claims a depositor can make about a thermo record,
neither of which the record could state before:

* **the thermodynamic target** -- what the numbers are claimed to describe: the
  thermally equilibrated ensemble of the species' conformers, or one named
  conformer group;
* **the protocol** -- how the numbers were produced, in the small vocabulary a
  later method-aware comparison needs and no larger.

Both are *claims*, stored as the depositor made them. Neither is inferred from
anything else (a statmech link does not imply an equilibrium target, a method
name in a level of theory does not imply a recipe) and neither is ever
backfilled: every record deposited without one reads ``null``, which means
"not stated", not "equilibrium" and not "standard".

House rule for the protocol vocabulary: it stores only what a job states, it
starts small, and a value is added when a real deposit needs it. Adding an
enum member or an optional field is additive within version 1; removing or
re-meaning one is a new version. The accepted versions are
:data:`THERMO_PROTOCOL_VERSIONS`; any other is refused.

Scientific references inside a declaration are local keys or public refs, never
database ids, and the backend resolves them: a conformer key or group ref names
the target conformer group; a calculation key or ref names a supporting
calculation. What is *stored* carries public refs only
(:class:`StoredThermoProtocolDeclaration`).
"""

from __future__ import annotations

import re
from enum import Enum
from typing import Annotated, Any

from pydantic import Field, StrictInt, StringConstraints, ValidationError, field_validator, model_validator

from tckdb_schemas.coded_error import CodedValidationError
from tckdb_schemas.common import SchemaBase
from tckdb_schemas.enums import ThermoTargetKind
from tckdb_schemas.producer_rule import producer_rule

__all__ = [
    "THERMO_PROTOCOL_VERSIONS",
    "StoredThermoProtocolCalculationRef",
    "StoredThermoProtocolDeclaration",
    "ThermoDepartureComponent",
    "ThermoEnsembleRepresentation",
    "ThermoFormationDerivation",
    "ThermoFormationReference",
    "ThermoInternalMotion",
    "ThermoProtocolCalculationRef",
    "ThermoProtocolDeclaration",
    "ThermoProtocolDeparture",
    "ThermoProtocolRecipe",
    "ThermoRecipeName",
    "ThermoReferenceDataSource",
    "ThermoTargetDeclaration",
    "ThermoThermalApproximation",
    "W_THERMO_DECLARATION_INVALID",
    "W_THERMO_PROTOCOL_VERSION_UNSUPPORTED",
    "W_THERMO_RECIPE_NAME_LISTED",
    "W_THERMO_TARGET_GROUP_NOT_ALLOWED",
    "W_THERMO_TARGET_GROUP_REQUIRED",
    "thermo_declaration_error",
    "version_is_supported",
    "thermo_target_error",
]

#: A ``single_conformer`` target does not name exactly one conformer group: it
#: names none, or names it twice (a ref and a key).
W_THERMO_TARGET_GROUP_REQUIRED = "thermo_target_group_required"
#: An ``equilibrium_ensemble`` target names a conformer group. Refused rather
#: than ignored: a declaration is a claim, and a claim carrying a group the
#: record does not mean would be stored as if it were meant.
W_THERMO_TARGET_GROUP_NOT_ALLOWED = "thermo_target_group_not_allowed"
#: A protocol declaration carries a ``version`` this server does not accept.
W_THERMO_PROTOCOL_VERSION_UNSUPPORTED = "thermo_protocol_version_unsupported"
#: A target or protocol declaration that reached a service or the client builder
#: without passing request validation (a payload built with ``model_construct``)
#: and fails it there: an unrecognised target kind, or a protocol that does not
#: validate. A parsed request is refused earlier, as an ordinary validation error.
W_THERMO_DECLARATION_INVALID = "thermo_declaration_invalid"

#: Free text, trimmed and never blank.
_Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]

#: ``recipe.other_name`` spells a recipe the vocabulary lists (G3, G4, G4(MP2), G4(complete)).
#: Refused so that a standard recipe cannot be hidden from a method-aware comparison by
#: declaring it as ``other``; the repair is to name the enum member.
W_THERMO_RECIPE_NAME_LISTED = "thermo_recipe_name_listed"

#: Protocol declaration versions the server accepts.
THERMO_PROTOCOL_VERSIONS: frozenset[int] = frozenset({1})

#: Listed recipes, keyed by their name with case, spaces and punctuation removed.
_LISTED_RECIPES = {"g3": "g3", "g4": "g4", "g4mp2": "g4mp2", "g4complete": "g4_complete"}


def version_is_supported(value: Any) -> bool:
    """Exactly a supported integer: ``True``, ``"1"`` and ``1.0`` are not version 1."""
    return type(value) is int and value in THERMO_PROTOCOL_VERSIONS


# ---------------------------------------------------------------------------
# Target
# ---------------------------------------------------------------------------


class ThermoTargetDeclaration(SchemaBase):
    """What the record's values are claimed to describe.

    ``kind`` is ``equilibrium_ensemble`` or ``single_conformer``. A
    ``single_conformer`` target names exactly one conformer group of the
    record's own species entry, by ``conformer_group_ref`` (a public ref, on
    the standalone and contribution-bundle routes) or by ``conformer_key`` (a
    conformer declared in the same computed-species / computed-reaction
    bundle). An ``equilibrium_ensemble`` target names no group and is refused
    if it does.

    :param kind: The target kind.
    :param conformer_group_ref: Public ref (``cg_...``) of the conformer
        group. Standalone ``/uploads/thermo`` and contribution bundles only.
    :param conformer_key: Local key of a conformer the same bundle declares.
        Computed-species and computed-reaction bundles only.
    """

    kind: ThermoTargetKind
    conformer_group_ref: str | None = Field(default=None, min_length=1)
    conformer_key: str | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def validate_group_matches_kind(self) -> ThermoTargetDeclaration:
        """A ``single_conformer`` target names exactly one group; an ``equilibrium_ensemble`` target names none."""
        error = thermo_target_error(self)
        if error is not None:
            code, message = error
            raise CodedValidationError(
                code, message, context={"field": "thermodynamic_target"}, message_prefix=False
            )
        return self


def _get(obj: Any, key: str) -> Any:
    if isinstance(obj, dict):
        return obj.get(key)
    return getattr(obj, key, None)


def thermo_target_error(target: Any) -> tuple[str, str] | None:
    """Return ``(code, message)`` when a target declaration contradicts itself.

    Pure and transport-independent: the request validator, the workflows (for a
    payload built without validation) and the client builder all call it, so
    the same mistake is refused with the same code wherever it is caught.
    Whether the named group exists and belongs to the record's species entry
    is a database question and is answered by the backend, not here.
    """
    if target is None:
        return None
    kind = _get(target, "kind")
    kind = kind.value if isinstance(kind, Enum) else kind
    if kind not in {member.value for member in ThermoTargetKind}:
        return (
            W_THERMO_DECLARATION_INVALID,
            f"thermodynamic_target.kind {kind!r} is not recognised; use "
            f"{' or '.join(repr(member.value) for member in ThermoTargetKind)}. "
            "Matching is exact and case-sensitive.",
        )
    ref = _get(target, "conformer_group_ref")
    key = _get(target, "conformer_key")
    has_ref, has_key = ref is not None, key is not None
    if kind == ThermoTargetKind.single_conformer.value:
        if has_ref and has_key:
            return (
                W_THERMO_TARGET_GROUP_REQUIRED,
                "A single_conformer target names its conformer group once: give "
                "conformer_group_ref or conformer_key, not both.",
            )
        if not (has_ref or has_key):
            return (
                W_THERMO_TARGET_GROUP_REQUIRED,
                "A single_conformer target must name the conformer group it describes "
                "(conformer_group_ref, or conformer_key inside a bundle that declares the "
                "conformer). Declare equilibrium_ensemble if the record describes the "
                "whole ensemble.",
            )
    elif kind == ThermoTargetKind.equilibrium_ensemble.value:
        if has_ref or has_key:
            return (
                W_THERMO_TARGET_GROUP_NOT_ALLOWED,
                "An equilibrium_ensemble target describes the whole ensemble and does not "
                "name one conformer group. Remove the group, or declare single_conformer.",
            )
    return None


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------


class ThermoRecipeName(str, Enum):
    """The named computational recipe a record's energy comes from.

    The members are the recipes a later E1 match must tell apart: standard G3,
    standard G4, G4(MP2) and G4(complete) are four different recipes, and a
    modification of any of them is a *departure*, not a fifth name. ``other``
    declares a recipe outside this list; ``other_name`` then says which.
    """

    g3 = "g3"
    g4 = "g4"
    g4mp2 = "g4mp2"
    g4_complete = "g4_complete"
    other = "other"


class ThermoFormationDerivation(str, Enum):
    """How a formation enthalpy was derived from computed energies.

    ``atomization``: from the total atomization energy and the formation
    enthalpies of the gaseous atoms. ``isodesmic``: from an isodesmic reaction
    with reference species of known formation enthalpy. ``working_reaction``:
    from any other balanced working reaction (homodesmotic, hypohomodesmotic,
    isogyric, ...). A ranking established for one derivation does not transfer
    to another, so they are never merged.
    """

    atomization = "atomization"
    isodesmic = "isodesmic"
    working_reaction = "working_reaction"


class ThermoReferenceDataSource(str, Enum):
    """Where the reference formation enthalpies of the derivation came from.

    For ``atomization`` these are the atoms'; for a working reaction, the
    reference species'. ``other`` requires ``reference_data_detail``.
    """

    atct = "atct"
    nist_janaf = "nist_janaf"
    codata = "codata"
    other = "other"


class ThermoEnsembleRepresentation(str, Enum):
    """How the thermal ensemble is represented in the numbers.

    Deliberately separate from the target: the target says *what ensemble is
    meant*, this says *what stood in for it*. A record can target the
    equilibrium ensemble and represent it by its lowest conformer alone.
    """

    lowest_conformer = "lowest_conformer"
    boltzmann_conformers = "boltzmann_conformers"


class ThermoInternalMotion(str, Enum):
    """How low-frequency internal motion was treated in the thermal contribution."""

    harmonic = "harmonic"
    hindered_rotors = "hindered_rotors"
    anharmonic = "anharmonic"


class ThermoDepartureComponent(str, Enum):
    """The part of a standard recipe a depositor departed from."""

    geometry = "geometry"
    frequencies = "frequencies"
    zero_point_energy = "zero_point_energy"
    electronic_energy = "electronic_energy"
    empirical_correction = "empirical_correction"
    other = "other"


class ThermoProtocolRecipe(SchemaBase):
    """The recipe the energy was computed with.

    :param name: Which recipe; see :class:`ThermoRecipeName`.
    :param other_name: Required when ``name`` is ``other`` and refused
        otherwise: the recipe's own name, as the source writes it.
    :param recipe_version: Optional free text naming the recipe's version or
        reference (for example ``G4 (Curtiss 2007)``). Stored as written.
    """

    name: ThermoRecipeName
    other_name: _Text | None = Field(default=None, max_length=64)
    recipe_version: _Text | None = Field(default=None, max_length=64)

    @model_validator(mode="after")
    def validate_other_name(self) -> ThermoProtocolRecipe:
        """``recipe.other_name`` is required when ``recipe.name`` is ``other``, refused for a named recipe, and
        refused when it spells a listed recipe (G3, G4, G4(MP2), G4(complete) in any case or punctuation)."""
        if self.name is ThermoRecipeName.other and self.other_name is None:
            raise ValueError("recipe.other_name is required when recipe.name is 'other'.")
        if self.name is ThermoRecipeName.other and self.other_name is not None:
            listed = _LISTED_RECIPES.get(re.sub(r"[^a-z0-9]", "", self.other_name.lower()))
            if listed is not None:
                raise CodedValidationError(
                    W_THERMO_RECIPE_NAME_LISTED,
                    f"recipe.other_name {self.other_name!r} names a listed recipe; declare "
                    f"recipe.name = {listed!r} instead, so the recipe is not hidden as 'other'.",
                    context={"field": "recipe.other_name", "recipe_name": listed},
                    message_prefix=False,
                )
        if self.name is not ThermoRecipeName.other and self.other_name is not None:
            raise ValueError("recipe.other_name is only allowed when recipe.name is 'other'.")
        return self


class ThermoFormationReference(SchemaBase):
    """How the formation enthalpy was constructed from the computed energies.

    :param derivation: Atomization, isodesmic or another working reaction.
    :param reference_data_source: Where the reference formation enthalpies
        came from; omit when the source does not say.
    :param reference_data_detail: Optional free text naming the data set and
        its version (for example ``ATcT 1.122``). Required when
        ``reference_data_source`` is ``other``.
    """

    derivation: ThermoFormationDerivation
    reference_data_source: ThermoReferenceDataSource | None = None
    reference_data_detail: _Text | None = Field(default=None, max_length=128)

    @model_validator(mode="after")
    def validate_other_source_has_detail(self) -> ThermoFormationReference:
        """``formation_reference.reference_data_detail`` is required when ``reference_data_source`` is ``other``."""
        if (
            self.reference_data_source is ThermoReferenceDataSource.other
            and self.reference_data_detail is None
        ):
            raise ValueError(
                "formation_reference.reference_data_detail is required when "
                "reference_data_source is 'other'."
            )
        return self


class ThermoThermalApproximation(SchemaBase):
    """The approximation that represents the target ensemble in the numbers.

    Not the target itself (see :class:`ThermoEnsembleRepresentation`). At
    least one of the two fields must be given: an object that says nothing is
    not a statement.

    :param ensemble_representation: Lowest conformer alone, or a Boltzmann sum
        over several.
    :param internal_motion: Harmonic, hindered-rotor or anharmonic treatment.
    """

    ensemble_representation: ThermoEnsembleRepresentation | None = None
    internal_motion: ThermoInternalMotion | None = None

    @model_validator(mode="after")
    def validate_not_empty(self) -> ThermoThermalApproximation:
        """``thermal_approximation`` must state ``ensemble_representation`` or ``internal_motion``; omit the
        block to state nothing."""
        if self.ensemble_representation is None and self.internal_motion is None:
            raise ValueError(
                "thermal_approximation must state ensemble_representation or internal_motion; "
                "omit the block to state nothing."
            )
        return self


class ThermoProtocolDeparture(SchemaBase):
    """One stated departure from the standard form of the declared recipe.

    :param component: The part of the recipe that was changed.
    :param description: What was done instead, in the depositor's words.
    """

    component: ThermoDepartureComponent
    description: _Text = Field(max_length=500)



class ThermoProtocolCalculationRef(SchemaBase):
    """A supporting calculation, by local key or public ref.

    Exactly one of the two. ``calculation_key`` names a calculation the same
    request declares; ``calculation_ref`` (``calc_...``) names one already
    deposited for the same species entry. Computed-species and
    computed-reaction bundles accept keys only.

    :param calculation_key: Local key.
    :param calculation_ref: Public ref.
    """

    calculation_key: str | None = Field(default=None, min_length=1)
    calculation_ref: str | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def validate_exactly_one(self) -> ThermoProtocolCalculationRef:
        """A supporting calculation gives exactly one of ``calculation_key`` or ``calculation_ref``."""
        if (self.calculation_key is None) == (self.calculation_ref is None):
            raise ValueError(
                "A supporting calculation must give exactly one of calculation_key or "
                "calculation_ref."
            )
        return self


class ThermoProtocolDeclaration(SchemaBase):
    """The protocol a thermo record was produced with. Version 1.

    An attributed claim, stored as made: not checked for truth against the
    linked calculations here, and never a substitute for evidence a later rule
    requires. Every block is optional; at least one statement (a block, or an
    explicit ``departures`` list) must be present.

    ``departures`` distinguishes three states, and the difference matters: omitted
    (or null) means the depositor did not say; an empty list means the depositor
    states there are no departures from the standard recipe; a list names them.
    "Standard" can only be established by the empty list.

    :param version: Declaration format version; only the integer ``1`` is accepted
        (not ``true``, ``"1"`` or ``1.0``).
    :param recipe: The recipe (G3, G4, G4(MP2), G4(complete), other) and version.
    :param formation_reference: How the formation enthalpy was constructed.
    :param thermal_approximation: What represents the target ensemble.
    :param departures: Stated departures from the standard recipe. Three states:
        omitted means not stated; an empty list means the depositor states there
        are none; a list of ``{component, description}`` names them. Only an
        empty list says "standard".
    :param supporting_calculations: Calculations the declaration rests on.
    """

    version: StrictInt
    recipe: ThermoProtocolRecipe | None = None
    formation_reference: ThermoFormationReference | None = None
    thermal_approximation: ThermoThermalApproximation | None = None
    departures: list[ThermoProtocolDeparture] | None = None
    supporting_calculations: list[ThermoProtocolCalculationRef] = Field(default_factory=list)

    @field_validator("version")
    @classmethod
    def validate_version(cls, value: int) -> int:
        """``protocol.version`` must be a supported declaration version (only ``1``); any other is refused."""
        if not version_is_supported(value):
            raise _version_error(value)
        return value

    @model_validator(mode="after")
    def validate_states_something(self) -> ThermoProtocolDeclaration:
        """A protocol declaration states at least one of ``recipe``, ``formation_reference``,
        ``thermal_approximation``, ``departures`` (an explicit empty list counts) or
        ``supporting_calculations``."""
        if (
            self.recipe is None
            and self.formation_reference is None
            and self.thermal_approximation is None
            and self.departures is None
            and not self.supporting_calculations
        ):
            raise ValueError(
                "A protocol declaration must state at least one of recipe, "
                "formation_reference, thermal_approximation, departures or "
                "supporting_calculations."
            )
        return self

    @model_validator(mode="after")
    def validate_supporting_calculations_unique(self) -> ThermoProtocolDeclaration:
        """``supporting_calculations`` does not repeat a calculation."""
        seen = [tuple(sorted(c.model_dump().items())) for c in self.supporting_calculations]
        if len(set(seen)) != len(seen):
            raise ValueError("supporting_calculations must not repeat a calculation.")
        return self


def _version_error(value: Any) -> CodedValidationError:
    supported = sorted(THERMO_PROTOCOL_VERSIONS)
    return CodedValidationError(
        W_THERMO_PROTOCOL_VERSION_UNSUPPORTED,
        f"protocol.version {value!r} is not supported; supported versions: {supported}.",
        context={"field": "protocol.version", "version": value, "supported_versions": supported},
        message_prefix=False,
    )


class StoredThermoProtocolCalculationRef(SchemaBase):
    """A supporting calculation as stored and read back: public ref only."""

    calculation_ref: str = Field(min_length=1)


class StoredThermoProtocolDeclaration(ThermoProtocolDeclaration):
    """The stored (and served) form of :class:`ThermoProtocolDeclaration`.

    Identical except that every supporting calculation is a public ref: the
    backend resolved any local key to the calculation it named and wrote that
    calculation's ref. What is in the database never contains a local key and
    never a database id.
    """

    supporting_calculations: list[StoredThermoProtocolCalculationRef] = Field(  # type: ignore[assignment]
        default_factory=list
    )


# ---------------------------------------------------------------------------
# The one rule workflows and the client builder share
# ---------------------------------------------------------------------------


@producer_rule
def thermo_declaration_error(payload: Any) -> tuple[str, str] | None:
    """A thermo record's optional target and protocol declarations must be coherent.

    A ``single_conformer`` target names exactly one conformer group; an
    ``equilibrium_ensemble`` target names none. ``protocol`` carries ``version`` (only 1)
    and refuses unknown fields and values. Both are claims, never inferred or defaulted.
    """
    target = _get(payload, "thermodynamic_target")
    error = thermo_target_error(target)
    if error is not None:
        return error
    protocol = _get(payload, "protocol")
    if protocol is None:
        return None
    version = _get(protocol, "version")
    if not version_is_supported(version):
        err = _version_error(version)
        return err.code, err.detail
    raw = protocol if isinstance(protocol, dict) else protocol.model_dump(mode="json")
    try:
        ThermoProtocolDeclaration.model_validate(raw)
    except ValidationError as exc:
        first = exc.errors()[0]
        where = ".".join(str(part) for part in first["loc"]) or "protocol"
        reason = first["msg"]
        return (W_THERMO_DECLARATION_INVALID, f"The protocol declaration is not valid at {where}: {reason}")
    return None

