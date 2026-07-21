from typing import Self

from pydantic import Field, field_validator, model_validator

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
    """
    try:
        validate_a_units_for_molecularity(a_units, molecularity)
    except ValueError as exc:
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
    :param degeneracy: Optional reaction-path degeneracy.
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

    # Programmatic bridge to a pressure-dependent network counterpart
    # (DR-0036). A raw database id, mirroring the ``existing_*_id`` convention
    # (advanced / machine-to-machine): contributor-facing UX links by local
    # keys through the bundle endpoints, not by raw FK. The workflow verifies
    # the referenced ``network_kinetics`` row exists.
    existing_network_kinetics_id: int | None = Field(default=None, gt=0)

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

    degeneracy: float | None = None
    degeneracy_convention: KineticsDegeneracyConvention = (
        KineticsDegeneracyConvention.unknown
    )
    tunneling_model: TunnelingModel | None = None
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
