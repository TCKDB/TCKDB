import math
from typing import Self

from pydantic import Field, field_validator, model_validator
from tckdb_schemas.coded_error import CodedValidationError

from app.chemistry.units import validate_a_units_for_molecularity
from app.db.models.common import (
    ActivationEnergyUnits,
    ArrheniusAUnits,
    EnergyCorrectionConvention,
    EnergyZeroConvention,
    KineticsDegeneracyConvention,
    KineticsDegeneracyInterpretation,
    KineticsDirection,
    KineticsEnsemblePolicy,
    KineticsModelKind,
    KineticsStandardStateConvention,
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


class ConformerSelectionContentRef(SchemaBase):
    """Content-first locator for a conformer-selection interpretation."""

    species_entry: SpeciesEntryIdentityPayload
    selection_kind: str = Field(min_length=1)
    assignment_scheme_ref: str | None = Field(default=None, min_length=1)


class KineticsInterpretationAssignmentUpload(SchemaBase):
    """Exact statmech/conformer/TS interpretation for one rate role.

    The three convention fields are machine tokens, not free text: a rate
    coefficient is not reproducible without knowing how conformers were
    combined, which standard state the partition functions use, and how
    symmetry was counted. ``other`` on any of them requires
    ``convention_note``.
    """

    role: str = Field(pattern="^(reactant|product|transition_state)$")
    participant_index: int | None = Field(default=None, ge=1)
    statmech_ref: str = Field(min_length=1)
    conformer_selection: ConformerSelectionContentRef | None = None
    transition_state_entry_ref: str | None = Field(default=None, min_length=1)
    ensemble_policy: KineticsEnsemblePolicy
    standard_state_convention: KineticsStandardStateConvention
    degeneracy_interpretation: KineticsDegeneracyInterpretation
    convention_note: str | None = None

    @model_validator(mode="after")
    def validate_role_shape(self) -> Self:
        if self.role == "transition_state" and self.transition_state_entry_ref is None:
            raise ValueError("transition_state interpretation requires transition_state_entry_ref.")
        if self.role != "transition_state" and self.transition_state_entry_ref is not None:
            raise ValueError("transition_state_entry_ref is only valid for role='transition_state'.")
        if self.role == "transition_state" and self.participant_index is not None:
            raise ValueError("participant_index is only valid for reactant/product interpretations.")
        if self.role != "transition_state" and self.participant_index is None:
            raise ValueError("reactant/product interpretations require participant_index.")
        if self.role == "transition_state" and self.conformer_selection is not None:
            # ``kinetics_interpretation_subject_shape`` requires a NULL
            # conformer_selection_id for a TS subject. Without this check the
            # id reached the INSERT and surfaced as an IntegrityError at flush
            # (a 500) instead of a 422. Conformer selection is a species-side
            # curation overlay; a TS candidate is chosen through
            # transition_state_selection, not through a conformer group.
            raise ValueError(
                "conformer_selection is only valid for reactant/product "
                "interpretations; a transition state is selected through its "
                "own transition-state selection, not a conformer group."
            )
        return self

    @model_validator(mode="after")
    def validate_other_requires_note(self) -> Self:
        self.convention_note = normalize_optional_text(self.convention_note)
        if (
            KineticsEnsemblePolicy.other
            in {self.ensemble_policy}
            or self.standard_state_convention == KineticsStandardStateConvention.other
            or self.degeneracy_interpretation == KineticsDegeneracyInterpretation.other
        ) and self.convention_note is None:
            raise ValueError(
                "convention_note is required when an interpretation convention is 'other'."
            )
        return self


class KineticsTunnelingApplicationUpload(SchemaBase):
    """Typed tunneling inputs/results, linked by public TS handle.

    :param model: The correction family. ``other`` is accepted only when the
        deposit is still replayable: it must carry a ``model_identifier``
        machine token naming the actual correction plus a result-artifact
        locator, so a reader who does not recognise the model can still see
        what was computed and by what.
    :param source_calculation_ref: The calculation the energies/barriers below
        were read from. Without it the numbers this block returns are
        untraceable.
    """

    model: TunnelingModel
    model_identifier: str | None = Field(
        default=None, pattern=r"^[a-z0-9]+(_[a-z0-9]+)*$"
    )
    transition_state_entry_ref: str = Field(min_length=1)
    source_calculation_ref: str | None = Field(default=None, min_length=1)
    # Signed normal-mode frequency: a TS imaginary mode is negative cm^-1.
    imaginary_frequency_cm1: float | None = None
    frequency_sign_convention: str = "negative_imaginary_cm1"
    reactant_energy_kj_mol: float | None = None
    product_energy_kj_mol: float | None = None
    # Barriers are signed relative to ``energy_zero_convention``; a submerged
    # barrier is legitimately negative.
    forward_barrier_kj_mol: float | None = None
    reverse_barrier_kj_mol: float | None = None
    energy_zero_convention: EnergyZeroConvention | None = None
    energy_correction_convention: EnergyCorrectionConvention | None = None
    convention_note: str | None = None
    sct_path_integral_artifact_calculation_ref: str | None = None
    sct_path_integral_artifact_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    result_artifact_calculation_ref: str | None = Field(default=None, min_length=1)
    result_artifact_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_model_inputs(self) -> Self:
        self.convention_note = normalize_optional_text(self.convention_note)
        if (self.result_artifact_calculation_ref is None) != (self.result_artifact_sha256 is None):
            raise ValueError("result artifact requires both calculation ref and SHA-256.")
        if self.model == TunnelingModel.none:
            raise ValueError("tunneling_application.model must be a correction model, not 'none'.")
        if (self.sct_path_integral_artifact_calculation_ref is None) != (self.sct_path_integral_artifact_sha256 is None):
            raise ValueError("SCT path artifact requires both calculation ref and SHA-256.")
        if self.frequency_sign_convention != "negative_imaginary_cm1":
            raise ValueError("frequency_sign_convention must be 'negative_imaginary_cm1'.")
        if self.model in {TunnelingModel.wigner, TunnelingModel.eckart, TunnelingModel.sct} and self.imaginary_frequency_cm1 is None:
            raise ValueError("Wigner/Eckart tunneling requires imaginary_frequency_cm1.")
        if self.imaginary_frequency_cm1 is not None and self.imaginary_frequency_cm1 >= 0:
            raise ValueError("imaginary_frequency_cm1 must be negative under negative_imaginary_cm1.")
        if self.model == TunnelingModel.eckart and any(
            value is None for value in (self.reactant_energy_kj_mol, self.product_energy_kj_mol, self.forward_barrier_kj_mol, self.reverse_barrier_kj_mol, self.energy_zero_convention, self.energy_correction_convention)
        ):
            raise ValueError("Eckart tunneling requires reactant/product energies, forward/reverse barriers, and energy conventions.")
        if self.model == TunnelingModel.sct and self.sct_path_integral_artifact_calculation_ref is None:
            raise ValueError("SCT tunneling requires a path-integral artifact.")
        # An unrecognised correction is only useful if a reader can identify
        # it and re-derive it. Naming it 'other' and supplying nothing else is
        # an unfalsifiable claim, not evidence.
        if self.model == TunnelingModel.other:
            if self.model_identifier is None:
                raise ValueError(
                    "tunneling_application.model='other' requires model_identifier, "
                    "a machine token naming the correction actually applied "
                    "(e.g. 'zero_curvature_tunneling')."
                )
            if self.result_artifact_calculation_ref is None:
                raise ValueError(
                    "tunneling_application.model='other' requires a result artifact "
                    "(calculation ref + SHA-256) so the correction stays replayable."
                )
        elif self.model_identifier is not None:
            raise ValueError(
                "model_identifier is only valid when model='other'; the named "
                "models are already identified by 'model'."
            )
        if (
            self.energy_zero_convention == EnergyZeroConvention.other
            or self.energy_correction_convention == EnergyCorrectionConvention.other
        ) and self.convention_note is None:
            raise ValueError(
                "convention_note is required when an energy convention is 'other'."
            )
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
    :param reported_ea: Optional activation energy in reported units.
    :param reported_ea_units: Units for ``reported_ea`` (required when reported).
    :param tmin_k: Optional minimum valid temperature in K.
    :param tmax_k: Optional maximum valid temperature in K.
    :param degeneracy: Optional finite, strictly positive reaction-path degeneracy.
    :param degeneracy_convention: Whether degeneracy is already included in the rate.
    :param tunneling_model: Optional tunneling model label.
    :param note: Optional free-text note.
    """

    reaction: KineticsReactionUpload
    scientific_origin: ScientificOriginKind
    model_kind: KineticsModelKind = KineticsModelKind.modified_arrhenius
    direction: KineticsDirection | None = None
    is_third_body: bool = False

    energy_level_of_theory: LevelOfTheoryRef | None = None

    literature: LiteratureUploadRequest | None = None
    software_release: SoftwareReleaseRef | None = None
    workflow_tool_release: WorkflowToolReleaseRef | None = None

    # Public, opaque handle for a pressure-dependent network counterpart
    # (DR-0036). Upload payloads must never accept database primary keys;
    # the workflow resolves this handle to its internal FK.
    network_kinetics_ref: str | None = Field(default=None, min_length=1)

    a: float | None = None
    a_units: ArrheniusAUnits | None = None
    n: float | None = None
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
        """Fill the label from the evidence block at parse time.

        Normalisation belongs in a *before* validator. Doing it in an
        after-validator mutated an already-constructed model, so the parsed
        object silently diverged from the validated one.
        """
        if not isinstance(data, dict):
            return data
        application = data.get("tunneling_application")
        if application is None or data.get("tunneling_model") is not None:
            return data
        model = (
            application.get("model")
            if isinstance(application, dict)
            else getattr(application, "model", None)
        )
        if model is not None:
            data = {**data, "tunneling_model": model}
        return data

    @model_validator(mode="after")
    def validate_tunneling_declaration_agrees(self) -> Self:
        """Cross-check the tunneling declaration against its evidence block.

        ``tunneling_model`` is a *label*: a reported attribute of the rate, of
        the same kind a mechanism file or a paper's methods section carries.
        A literature rate whose authors state "Eckart tunneling was applied"
        genuinely has no imaginary frequency, no barriers and no artifact for
        the depositor to attach, and neither does a rate imported from a
        CHEMKIN mechanism. Demanding typed evidence for a label would force
        exactly the invention this schema exists to prevent, so its absence is
        reported as an upload warning instead.

        ``tunneling_application`` is *evidence*. When present it must be
        internally complete (enforced on that model) and must agree with the
        label it is offered for.
        """
        if (
            self.tunneling_application is not None
            and self.tunneling_model != self.tunneling_application.model
        ):
            raise ValueError("tunneling_application.model must match tunneling_model.")
        return self

    @model_validator(mode="after")
    def validate_interpretation_content(self) -> Self:
        """An interpretation set, once offered, must be complete.

        Supplying ``interpretation_assignments`` is the claim "this rate was
        built from these partition functions in this database". A *partial*
        set is worse than none: it looks like provenance while leaving the
        unnamed participants entirely unaccounted for. So the completeness
        requirement attaches to that claim, not to ``scientific_origin``.

        ``scientific_origin='computed'`` means only "this number came from a
        calculation" — it does not mean the calculation's partition functions
        live here. A rate read out of a CHEMKIN mechanism, or an Arkane TST
        result deposited without its statmech, is computed in origin and
        carries no assignments; rejecting it would lose a real record. Its
        lack of assignments is reported as an upload warning instead.

        ``participant_index`` is bounded here rather than only at the
        persistence seam, so an out-of-range slot is a 422 and not a late
        500-shaped failure.
        """
        n_reactants = len(self.reaction.reactants)
        n_products = len(self.reaction.products)
        for assignment in self.interpretation_assignments:
            if assignment.participant_index is None:
                continue
            limit = n_reactants if assignment.role == "reactant" else n_products
            if assignment.participant_index > limit:
                raise ValueError(
                    f"interpretation_assignments {assignment.role}:"
                    f"{assignment.participant_index} is outside the declared "
                    f"{assignment.role} list (1..{limit})."
                )
        subjects = [
            "transition_state" if assignment.role == "transition_state" else f"{assignment.role}:{assignment.participant_index}"
            for assignment in self.interpretation_assignments
        ]
        if len(subjects) != len(set(subjects)):
            raise ValueError("interpretation_assignments must be unique by role and participant_index.")
        if not self.interpretation_assignments:
            return self
        required = {f"reactant:{i}" for i in range(1, n_reactants + 1)} | {
            f"product:{i}" for i in range(1, n_products + 1)
        }
        # A tunneling correction is applied *to* a transition state, so the
        # rate's TS partition function must be named too.
        if self.tunneling_application is not None:
            required.add("transition_state")
        missing = sorted(required - set(subjects))
        if missing:
            raise ValueError(
                "computed kinetics requires one interpretation_assignment per "
                f"reaction subject; missing: {missing}."
            )
        return self
