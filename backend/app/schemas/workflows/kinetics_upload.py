import math
from typing import Self

from pydantic import ConfigDict, Field, field_validator, model_validator
from tckdb_schemas.coded_error import CodedValidationError
from tckdb_schemas.fragments.kinetics_evidence import (
    T0_K_DESCRIPTION,
    KineticsInterpretationAssignmentUpload,
    KineticsTunnelingApplicationUpload,
    check_interpretation_set,
    check_tunneling_declaration_agrees,
    default_tunneling_model_from_application,
)
from tckdb_schemas.rights import DepositRights

from app.chemistry.units import validate_a_units_for_molecularity
from app.db.models.common import (
    ActivationEnergyUnits,
    ArrheniusAUnits,
    KineticsDegeneracyConvention,
    KineticsDirection,
    KineticsModelKind,
    KineticsUncertaintyKind,
    PressureContext,
    ScientificOriginKind,
    TunnelingModel,
)
from app.schemas.common import SchemaBase
from app.schemas.fragments.identity import SpeciesEntryIdentityPayload
from app.schemas.fragments.refs import LevelOfTheoryRef, SoftwareReleaseRef, WorkflowToolReleaseRef
from app.schemas.reaction_family import find_canonical_reaction_family
from app.schemas.utils import normalize_optional_text, normalize_tunneling_model
from app.schemas.workflows.literature_upload import LiteratureUploadRequest


def _validate_a_units_named(field: str, a_units: ArrheniusAUnits, molecularity: int) -> None:
    """Validate A-units against molecularity, naming the offending field on failure.

    Wraps :func:`validate_a_units_for_molecularity` so a rejected sibling
    A-factor (a ``multi_arrhenius`` term, PLOG entry, or falloff k0) reports
    which term failed without leaking database ids.

    Adding the field name must not cost the code
    -------------------------------------------
    The wrapped check raises :class:`~tckdb_schemas.coded_error.CodedValidationError`,
    which is a ``ValueError``, so the obvious ``except ValueError: raise
    ValueError(f"{field}: {exc}")`` re-raised a *plain* ``ValueError`` and
    the declared code was gone before any promotion rule could see it.
    order-2 ``a_units`` on a unimolecular ``plog_entries[1]`` reached the
    client as the generic ``request_validation_error`` -- these validators
    run while the request body is being parsed, so the fallback is the
    request one -- while the identical mistake on the main-line ``a_units``,
    which calls the check directly, arrived as
    ``arrhenius_a_units_molecularity_mismatch``. Same refusal, two contracts,
    decided by whether a wrapper happened to be in the way.

    So the coded case is caught first and re-raised *as itself*: same code,
    same context (plus the field, which is the machine-readable form of what
    the prefix says in prose), and ``str(exc)`` byte-identical to what the
    lossy version produced. ``message_prefix=False`` is what keeps it
    identical -- the default would insert the code ahead of the field and
    move a published message.

    Note what the prefix does *not* become. The message now starts with
    ``"plog_entries[1].a_units: "``, and #159 promotes a leading token only
    where :mod:`app.api.code_catalogue` calls it a code. A field path is not
    catalogued, so it cannot be mistaken for one; the code arrives because
    the exception declares it, never because of where it sits in a sentence.
    """
    try:
        validate_a_units_for_molecularity(a_units, molecularity)
    except CodedValidationError as exc:
        raise CodedValidationError(
            exc.code,
            f"{field}: {exc}",
            context={**exc.context, "field": field},
            message_prefix=False,
        ) from exc
    except ValueError as exc:
        # Defensive, and currently unreachable: every raise in
        # ``validate_a_units_for_molecularity`` is coded. Kept so that a
        # future uncoded ValueError still gets its field named rather than
        # silently losing the context this helper exists to add.
        raise ValueError(f"{field}: {exc}") from exc


class KineticsReactionParticipantUpload(SchemaBase):
    """Workflow-facing ordered participant slot for a kinetics upload.

    :param species_entry: Species-entry identity payload to resolve or create.
    :param note: Optional note stored on the structured participant row.
    """

    species_entry: SpeciesEntryIdentityPayload
    note: str | None = None

    @model_validator(mode="after")
    def normalize_note(self) -> Self:
        self.note = normalize_optional_text(self.note)
        return self


class KineticsReactionUpload(SchemaBase):
    """Workflow-facing reaction content embedded in a kinetics upload.

    :param reversible: Whether the uploaded reaction is reversible.
    :param reaction_family: Optional reaction-family label.
    :param reaction_family_source_note: Required when ``reaction_family`` is not a supported canonical family.
    :param reactants: Ordered structured participants on the reactant side.
    :param products: Ordered structured participants on the product side.
    """

    reversible: bool
    reaction_family: str | None = None
    reaction_family_source_note: str | None = None
    reactants: list[KineticsReactionParticipantUpload] = Field(min_length=1)
    products: list[KineticsReactionParticipantUpload] = Field(min_length=1)

    @field_validator("reaction_family", "reaction_family_source_note")
    @classmethod
    def normalize_reaction_family(cls, value: str | None) -> str | None:
        return normalize_optional_text(value)

    @model_validator(mode="after")
    def validate_reaction_family(self) -> Self:
        if self.reaction_family is None:
            if self.reaction_family_source_note is not None:
                raise ValueError(
                    "reaction_family_source_note requires reaction_family."
                )
            return self

        if find_canonical_reaction_family(self.reaction_family) is None:
            if self.reaction_family_source_note is None:
                raise ValueError(
                    "reaction_family_source_note is required when reaction_family "
                    "is not a supported canonical family."
                )
        return self


class FalloffUpload(SchemaBase):
    """Pressure-dependent falloff parameters (DR-0032 Part B).

    The high-pressure-limit (k∞) Arrhenius parameters are the top-level
    ``a``/``n``/``reported_ea`` on the kinetics request; this block carries
    the low-pressure-limit (k0) Arrhenius and the broadening coefficients.
    Which broadening columns matter is set by the request ``model_kind``
    (``lindemann`` = none; ``troe`` = ``troe_*``; ``sri`` = ``sri_*``).
    """

    low_a: float
    low_a_units: ArrheniusAUnits | None = None
    low_n: float | None = None
    low_ea_kj_mol: float | None = None

    troe_alpha: float | None = None
    troe_t3: float | None = None
    troe_t1: float | None = None
    troe_t2: float | None = None

    sri_a: float | None = None
    sri_b: float | None = None
    sri_c: float | None = None
    sri_d: float | None = None
    sri_e: float | None = None

    note: str | None = None


class ThirdBodyEfficiencyUpload(SchemaBase):
    """A per-collider third-body efficiency for a falloff/third-body rate.

    The collider is given by scientific content (a species identity), which
    the workflow resolves to a graph-level species. ``efficiency`` scales
    the effective bath-gas concentration [M] contributed by that collider.
    """

    collider: SpeciesEntryIdentityPayload
    efficiency: float = Field(ge=0)


class PlogEntryUpload(SchemaBase):
    """One pressure entry of a standalone PLOG rate (DR-0032 Part C)."""

    entry_index: int = Field(ge=1)
    pressure_bar: float = Field(gt=0)
    a: float
    a_units: ArrheniusAUnits | None = None
    n: float | None = None
    ea_kj_mol: float | None = None


class MultiArrheniusEntryUpload(SchemaBase):
    """One modified-Arrhenius term of a sum-of-Arrhenius rate (DR-0036).

    A Chemkin ``DUPLICATE`` channel's rate coefficient is the sum of these
    terms. Unlike a PLOG entry there is no pressure — the terms are summed,
    not interpolated. ``reported_ea``/``reported_ea_units`` are converted to
    ``ea_kj_mol`` by the workflow, mirroring the top-level Arrhenius fields.
    """

    entry_index: int = Field(ge=1)
    a: float
    a_units: ArrheniusAUnits | None = None
    n: float | None = None
    reported_ea: float | None = None
    reported_ea_units: ActivationEnergyUnits | None = None

    @model_validator(mode="after")
    def validate_reported_ea_pair(self) -> Self:
        has_value = self.reported_ea is not None
        has_units = self.reported_ea_units is not None
        if has_value != has_units:
            raise ValueError(
                "reported_ea and reported_ea_units must both be provided "
                "or both omitted."
            )
        return self


class ChebyshevUpload(SchemaBase):
    """A standalone Chebyshev k(T,P) surface (DR-0032 Part C).

    ``coefficients`` is the n_temperature × n_pressure coefficient matrix
    (list of rows).
    """

    n_temperature: int = Field(ge=1)
    n_pressure: int = Field(ge=1)
    tmin_k: float | None = Field(default=None, gt=0)
    tmax_k: float | None = Field(default=None, gt=0)
    pmin_bar: float | None = Field(default=None, gt=0)
    pmax_bar: float | None = Field(default=None, gt=0)
    coefficients: list[list[float]]

    @model_validator(mode="after")
    def validate_grid(self) -> Self:
        if len(self.coefficients) != self.n_temperature or any(
            len(row) != self.n_pressure for row in self.coefficients
        ):
            raise ValueError("Chebyshev coefficients must be an n_temperature x n_pressure matrix.")
        if any(not math.isfinite(value) for row in self.coefficients for value in row):
            raise ValueError("Chebyshev coefficients must all be finite.")
        if self.tmin_k is None or self.tmax_k is None or self.pmin_bar is None or self.pmax_bar is None:
            raise ValueError("Chebyshev kinetics requires finite T and P bounds.")
        if self.tmin_k > self.tmax_k or self.pmin_bar > self.pmax_bar:
            raise ValueError("Chebyshev bounds must be ordered.")
        return self


class KineticsUploadRequest(SchemaBase):
    """Workflow-facing kinetics upload payload.

    The backend resolves reaction identity/entry, optional literature, and
    optional software/workflow provenance, then creates the kinetics row.

    For computed kinetics, ``energy_level_of_theory`` declares the SP level
    of theory used for the electronic energies.  The backend automatically
    finds the matching SP calculations on each reaction participant's
    conformer and links them as source calculations.  If the lookup is
    ambiguous (e.g., multiple conformers), the upload fails with a clear
    error.

    :param reaction: Reaction described by scientific content.
    :param scientific_origin: Scientific origin category.
    :param model_kind: Kinetics functional form.
    :param is_third_body: True for a simple ``+M`` third-body reaction (no
        falloff), which raises the effective main-line Arrhenius A-units
        order by one.
    :param energy_level_of_theory: SP level of theory for source-calc auto-resolution.
    :param literature: Optional literature submission payload.
    :param software_release: Optional software provenance reference (fitting tool).
    :param workflow_tool_release: Optional workflow-tool provenance reference.
    :param a: Optional Arrhenius pre-exponential factor.
    :param a_units: Optional units for the pre-exponential factor.
    :param n: Optional temperature exponent.
    :param t0_k: Reference temperature T0 in K, so that
        ``k = A (T/T0)^n exp(-Ea/RT)``. Defaults to 1 K.
    :param reported_ea: Optional activation energy in reported units.
    :param reported_ea_units: Units for ``reported_ea`` (required when reported).
    :param tmin_k: Optional minimum valid temperature in K.
    :param tmax_k: Optional maximum valid temperature in K.
    :param degeneracy: Optional finite, strictly positive reaction-path degeneracy.
    :param degeneracy_convention: Whether degeneracy is already included in the rate.
    :param tunneling_model: Optional tunneling model label.
    :param note: Optional free-text note.
    """

    # A minimal valid payload. Published as the JSON Schema's ``examples``, in
    # the OpenAPI document, and in the producer contract, which validates it
    # against this model on every generation (generate_producer_contract.py).
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "reaction": {
                        "reversible": False,
                        "reactants": [
                            {
                                "species_entry": {
                                    "smiles": "[H]",
                                    "charge": 0,
                                    "multiplicity": 2
                                }
                            },
                            {
                                "species_entry": {
                                    "smiles": "[H]",
                                    "charge": 0,
                                    "multiplicity": 2
                                }
                            }
                        ],
                        "products": [
                            {
                                "species_entry": {
                                    "smiles": "[H][H]",
                                    "charge": 0,
                                    "multiplicity": 1
                                }
                            }
                        ]
                    },
                    "scientific_origin": "computed",
                    "model_kind": "modified_arrhenius",
                    "a": 1230000000000.0,
                    "a_units": "cm3_mol_s",
                    "n": 0.0
                }
            ]
        },
    )

    reaction: KineticsReactionUpload
    scientific_origin: ScientificOriginKind
    model_kind: KineticsModelKind = KineticsModelKind.modified_arrhenius
    direction: KineticsDirection | None = None
    is_third_body: bool = False

    energy_level_of_theory: LevelOfTheoryRef | None = None

    literature: LiteratureUploadRequest | None = None
    software_release: SoftwareReleaseRef | None = None
    workflow_tool_release: WorkflowToolReleaseRef | None = None

    # Deposit-time license agreement; see ``tckdb_schemas.rights``. Optional
    # so existing clients keep working -- absence bites at release time.
    rights: DepositRights | None = None

    # Public, opaque handle for a pressure-dependent network counterpart
    # (DR-0036). Upload payloads must never accept database primary keys;
    # the workflow resolves this handle to its internal FK.
    network_kinetics_ref: str | None = Field(default=None, min_length=1)

    a: float | None = None
    a_units: ArrheniusAUnits | None = None
    n: float | None = None
    t0_k: float = Field(
        default=1.0, gt=0, allow_inf_nan=False, description=T0_K_DESCRIPTION
    )
    reported_ea: float | None = None
    reported_ea_units: ActivationEnergyUnits | None = None

    a_uncertainty: float | None = None
    a_uncertainty_kind: KineticsUncertaintyKind | None = None
    n_uncertainty: float | None = None
    d_reported_ea: float | None = None

    tmin_k: float | None = Field(default=None, gt=0)
    tmax_k: float | None = Field(default=None, gt=0)

    degeneracy: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    degeneracy_convention: KineticsDegeneracyConvention = (
        KineticsDegeneracyConvention.unknown
    )
    tunneling_model: TunnelingModel | None = None
    interpretation_assignments: list[KineticsInterpretationAssignmentUpload] = Field(default_factory=list)
    tunneling_application: KineticsTunnelingApplicationUpload | None = None
    pressure_context: PressureContext | None = None
    pressure_bar: float | None = Field(default=None, gt=0)

    falloff: FalloffUpload | None = None
    third_body_efficiencies: list[ThirdBodyEfficiencyUpload] = Field(
        default_factory=list
    )
    plog_entries: list[PlogEntryUpload] = Field(default_factory=list)
    arrhenius_entries: list[MultiArrheniusEntryUpload] = Field(default_factory=list)
    chebyshev: ChebyshevUpload | None = None
    note: str | None = None

    @field_validator("tunneling_model", mode="before")
    @classmethod
    def _normalize_tunneling(cls, v):
        return normalize_tunneling_model(v)

    @model_validator(mode="after")
    def normalize_optional_text_fields(self) -> Self:
        self.note = normalize_optional_text(self.note)
        return self

    @model_validator(mode="after")
    def validate_pressure_context(self) -> Self:
        if (
            self.pressure_context == PressureContext.apparent_at_pressure
            and self.pressure_bar is None
        ):
            raise ValueError(
                "pressure_context='apparent_at_pressure' requires pressure_bar."
            )
        return self

    @model_validator(mode="after")
    def validate_reported_ea_pair(self) -> Self:
        has_value = self.reported_ea is not None
        has_units = self.reported_ea_units is not None
        if has_value != has_units:
            raise ValueError(
                "reported_ea and reported_ea_units must both be provided or both omitted."
            )
        return self

    @model_validator(mode="after")
    def validate_temperature_range(self) -> Self:
        if (
            self.tmin_k is not None
            and self.tmax_k is not None
            and self.tmin_k > self.tmax_k
        ):
            raise ValueError("tmin_k must be less than or equal to tmax_k.")
        return self

    @model_validator(mode="after")
    def validate_multi_arrhenius(self) -> Self:
        """Bind ``multi_arrhenius`` to its sum-of-terms child rows (DR-0036).

        A DUPLICATE channel is a sum of at least two modified-Arrhenius
        terms; the scalar ``a`` must stay unset because the coefficient lives
        in the child entries, and the entry indices must be unique.
        """
        is_multi = self.model_kind == KineticsModelKind.multi_arrhenius
        if is_multi:
            if len(self.arrhenius_entries) < 2:
                raise ValueError(
                    "model_kind='multi_arrhenius' requires at least two "
                    "arrhenius_entries (a sum of modified-Arrhenius terms)."
                )
            if self.a is not None:
                raise ValueError(
                    "model_kind='multi_arrhenius' must not set the scalar 'a'; "
                    "the terms live in arrhenius_entries."
                )
        elif self.arrhenius_entries:
            raise ValueError(
                "arrhenius_entries are only valid when "
                "model_kind='multi_arrhenius'."
            )
        indices = [e.entry_index for e in self.arrhenius_entries]
        if len(set(indices)) != len(indices):
            raise ValueError("arrhenius_entries entry_index values must be unique.")
        return self

    @model_validator(mode="after")
    def validate_a_uncertainty_kind(self) -> Self:
        has_value = self.a_uncertainty is not None
        has_kind = self.a_uncertainty_kind is not None
        if has_value != has_kind:
            raise ValueError(
                "a_uncertainty and a_uncertainty_kind must both be provided "
                "or both omitted."
            )
        if (
            self.a_uncertainty_kind == KineticsUncertaintyKind.multiplicative
            and self.a_uncertainty is not None
            and self.a_uncertainty < 1.0
        ):
            raise ValueError(
                "Multiplicative a_uncertainty must be >= 1.0 (factor f, "
                "with the true value within [A/f, A*f])."
            )
        return self

    def _main_line_molecularity(self) -> int:
        """Effective concentration order of the main-line Arrhenius rate.

        A *simple* third-body reaction (generic ``+M`` collider, no falloff)
        carries a ``[M]`` term on the main line, raising the order by one.
        Falloff reactions keep ``len(reactants)``: their main line is the
        high-pressure limit k∞ (M excluded); the low-pressure limit k0 is
        one order higher and validated via ``falloff.low_a_units``.
        """
        molecularity = len(self.reaction.reactants)
        if self.is_third_body and self.falloff is None:
            molecularity += 1
        return molecularity

    @model_validator(mode="after")
    def validate_third_body_is_meaningful(self) -> Self:
        """PLOG and Chebyshev are never third-body reactions.

        Both parameterizations already carry the full pressure dependence, so
        CHEMKIN does not admit a ``+M`` third-body designation on them.
        Accepting the flag was not merely cosmetic: it made
        :meth:`_main_line_molecularity` raise the expected A-unit order by
        one, so a PLOG entry carrying the CORRECT units for its molecularity
        was rejected while one carrying the units of the next order up was
        accepted.

        Declared ahead of the A-unit validators so the actionable message
        wins: validators run in definition order, and the inflated-order unit
        error would otherwise mask the real cause.
        """
        if self.is_third_body and self.model_kind in {
            KineticsModelKind.plog,
            KineticsModelKind.chebyshev,
        }:
            raise ValueError(
                f"model_kind='{self.model_kind.value}' cannot be a third-body "
                "reaction: the parameterization already encodes the pressure "
                "dependence, and is_third_body would raise the expected A-unit "
                "order by one."
            )
        return self

    @model_validator(mode="after")
    def validate_a_units_vs_molecularity(self) -> Self:
        if self.a_units is None:
            return self
        validate_a_units_for_molecularity(self.a_units, self._main_line_molecularity())
        return self

    @model_validator(mode="after")
    def validate_arrhenius_entries_a_units(self) -> Self:
        """Every summed ``multi_arrhenius`` term is the SAME reaction rate, so
        each term's ``a_units`` must match the main-line molecularity (DR-0036).
        """
        molecularity = self._main_line_molecularity()
        for entry in self.arrhenius_entries:
            if entry.a_units is None:
                continue
            _validate_a_units_named(
                f"arrhenius_entries[{entry.entry_index}].a_units",
                entry.a_units,
                molecularity,
            )
        return self

    @model_validator(mode="after")
    def validate_plog_entries_a_units(self) -> Self:
        """Each PLOG pressure entry's A is the reaction's rate at that pressure,
        so its ``a_units`` shares the main-line molecularity (DR-0032 Part C).
        """
        molecularity = self._main_line_molecularity()
        for entry in self.plog_entries:
            if entry.a_units is None:
                continue
            _validate_a_units_named(
                f"plog_entries[{entry.entry_index}].a_units",
                entry.a_units,
                molecularity,
            )
        return self

    @model_validator(mode="after")
    def validate_falloff_low_a_units(self) -> Self:
        """The low-pressure-limit k0 Arrhenius is by definition one order higher
        than k∞, so ``falloff.low_a_units`` validates at ``len(reactants) + 1``
        regardless of ``is_third_body`` (DR-0032 Part B).
        """
        if self.falloff is None or self.falloff.low_a_units is None:
            return self
        molecularity = len(self.reaction.reactants) + 1
        _validate_a_units_named(
            "falloff.low_a_units", self.falloff.low_a_units, molecularity
        )
        return self

    @model_validator(mode="after")
    def validate_model_scientific_content(self) -> Self:
        """A fit must carry parameters for the declared functional form."""
        scalar_models = {
            KineticsModelKind.arrhenius, KineticsModelKind.modified_arrhenius,
            KineticsModelKind.lindemann, KineticsModelKind.troe, KineticsModelKind.sri,
        }
        if self.model_kind in scalar_models and self.a is None:
            raise ValueError(
                f"model_kind='{self.model_kind.value}' requires scalar a: a declared "
                "functional form must carry its rate coefficient. (model_kind "
                "defaults to 'modified_arrhenius' when omitted.)"
            )
        if self.model_kind in {KineticsModelKind.lindemann, KineticsModelKind.troe, KineticsModelKind.sri} and self.falloff is None:
            raise ValueError(f"model_kind='{self.model_kind.value}' requires falloff parameters.")
        if self.model_kind == KineticsModelKind.plog and not self.plog_entries:
            raise ValueError("model_kind='plog' requires plog_entries.")
        if self.model_kind == KineticsModelKind.chebyshev and self.chebyshev is None:
            raise ValueError("model_kind='chebyshev' requires chebyshev.")
        # Which child blocks a functional form may carry, derived from what
        # CHEMKIN/Cantera actually permit rather than from tidiness.
        #
        # Per-collider third-body efficiencies belong to the ``+M`` term, NOT
        # to the rate expression. ``H + O2 + M <=> HO2 + M`` is a plain
        # modified-Arrhenius rate with an enhanced-efficiency list, and that
        # combination appears in every published combustion mechanism; a
        # DUPLICATE pair of such lines is equally routine. Efficiencies are
        # excluded only from PLOG and Chebyshev, whose parameterizations
        # already encode the bath-gas dependence — attaching efficiencies
        # there would double-count it (and Cantera rejects it outright).
        allowed_children = {
            KineticsModelKind.arrhenius: ("third_body_efficiencies",),
            KineticsModelKind.modified_arrhenius: ("third_body_efficiencies",),
            KineticsModelKind.multi_arrhenius: ("arrhenius_entries", "third_body_efficiencies"),
            KineticsModelKind.lindemann: ("falloff", "third_body_efficiencies"),
            KineticsModelKind.troe: ("falloff", "third_body_efficiencies"),
            KineticsModelKind.sri: ("falloff", "third_body_efficiencies"),
            KineticsModelKind.plog: ("plog_entries",), KineticsModelKind.chebyshev: ("chebyshev",),
        }[self.model_kind]
        present = {
            "falloff": self.falloff is not None,
            "third_body_efficiencies": bool(self.third_body_efficiencies),
            "plog_entries": bool(self.plog_entries),
            "arrhenius_entries": bool(self.arrhenius_entries),
            "chebyshev": self.chebyshev is not None,
        }
        forbidden = [name for name, has_value in present.items() if has_value and name not in allowed_children]
        if forbidden:
            raise ValueError(f"model_kind='{self.model_kind.value}' forbids {', '.join(forbidden)}.")
        return self

    @model_validator(mode="before")
    @classmethod
    def default_tunneling_model_from_application(cls, data):
        """Fill the label from the evidence block at parse time (shared body)."""
        return default_tunneling_model_from_application(data)

    @model_validator(mode="after")
    def validate_tunneling_declaration_agrees(self) -> Self:
        """Cross-check the tunneling label against its evidence block (shared)."""
        check_tunneling_declaration_agrees(
            self.tunneling_model, self.tunneling_application
        )
        return self

    @model_validator(mode="after")
    def validate_interpretation_content(self) -> Self:
        """An interpretation set, once offered, must be complete (shared)."""
        check_interpretation_set(
            self.interpretation_assignments,
            n_reactants=len(self.reaction.reactants),
            n_products=len(self.reaction.products),
            has_tunneling_application=self.tunneling_application is not None,
        )
        return self

    @model_validator(mode="after")
    def validate_t0_applies_to_a_scalar_rate(self) -> Self:
        """``t0_k`` belongs to the row's own scalar ``a``/``n``/``reported_ea``.

        PLOG entries, sum-of-Arrhenius terms and Chebyshev surfaces carry no
        T0 of their own (they are at 1 K), so a record of those forms that
        declared another T0 would be a number with nothing to apply to.
        """
        if self.t0_k != 1.0 and self.model_kind in {
            KineticsModelKind.plog,
            KineticsModelKind.chebyshev,
            KineticsModelKind.multi_arrhenius,
        }:
            raise ValueError(
                f"t0_k applies to the scalar Arrhenius parameters, which "
                f"model_kind='{self.model_kind.value}' does not carry; its "
                "child rows are always at T0 = 1 K."
            )
        return self
