from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Double,
    ForeignKey,
    Integer,
    PrimaryKeyConstraint,
    SmallInteger,
    Text,
    UniqueConstraint,
)
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, CreatedByMixin, PublicRefMixin, TimestampMixin
from app.db.models.common import (
    ArrheniusAUnits,
    EnergyCorrectionConvention,
    EnergyZeroConvention,
    KineticsCalculationRole,
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

if TYPE_CHECKING:
    from app.db.models.calculation import Calculation, CalculationArtifact
    from app.db.models.literature import Literature
    from app.db.models.network_pdep import NetworkKinetics
    from app.db.models.reaction import ReactionEntry
    from app.db.models.software import SoftwareRelease
    from app.db.models.species import ConformerSelection, Species
    from app.db.models.statmech import Statmech
    from app.db.models.transition_state import TransitionStateEntry
    from app.db.models.workflow import WorkflowToolRelease


class Kinetics(Base, TimestampMixin, CreatedByMixin, PublicRefMixin):
    """Kinetics records attached to a reaction entry."""

    __tablename__ = "kinetics"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)

    reaction_entry_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("reaction_entry.id", deferrable=True, initially="IMMEDIATE"),
        nullable=False,
    )
    scientific_origin: Mapped[ScientificOriginKind] = mapped_column(
        SAEnum(ScientificOriginKind, name="scientific_origin_kind"),
        nullable=False,
    )
    model_kind: Mapped[KineticsModelKind] = mapped_column(
        SAEnum(KineticsModelKind, name="kinetics_model_kind"),
        nullable=False,
        default=KineticsModelKind.modified_arrhenius,
        server_default=KineticsModelKind.modified_arrhenius.value,
    )
    # Which direction of the reaction this fit describes (DR-0036). NULL =
    # unspecified (historical default), so existing rows are unaffected. Lets
    # a forward and a reverse Arrhenius fit for one ``reaction_entry`` coexist
    # distinctly (Chemkin/Cantera round-trip).
    direction: Mapped[Optional[KineticsDirection]] = mapped_column(
        SAEnum(KineticsDirection, name="kinetics_direction"),
        nullable=True,
    )
    # True for a *simple* third-body reaction (generic ``+M`` collider, no
    # falloff): the ``[M]`` term raises the effective concentration order of
    # the main-line Arrhenius rate by one (e.g. ``A + B + M`` is order-3).
    # Stays False for falloff reactions — their main-line Arrhenius is the
    # high-pressure limit k∞ (order = number of real reactants); the
    # low-pressure k0 (order + 1) lives on ``kinetics_falloff.low_a_units``.
    is_third_body: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default="false",
    )

    literature_id: Mapped[Optional[int]] = mapped_column(
        BigInteger,
        ForeignKey("literature.id", deferrable=True, initially="IMMEDIATE"),
        nullable=True,
        index=True,
    )
    workflow_tool_release_id: Mapped[Optional[int]] = mapped_column(
        BigInteger,
        ForeignKey("workflow_tool_release.id", deferrable=True, initially="IMMEDIATE"),
        nullable=True,
    )
    software_release_id: Mapped[Optional[int]] = mapped_column(
        BigInteger,
        ForeignKey("software_release.id", deferrable=True, initially="IMMEDIATE"),
        nullable=True,
    )
    # Bridge to the pressure-dependent network counterpart (DR-0036). When set,
    # this reaction-level HPL/apparent fit corresponds to the k(T,P) of a
    # specific ``network_kinetics`` channel-under-solve, so "give me k(T,P) for
    # reaction R" resolves in one join instead of a two-query split. Nullable /
    # additive: most kinetics rows have no network counterpart.
    network_kinetics_id: Mapped[Optional[int]] = mapped_column(
        BigInteger,
        ForeignKey("network_kinetics.id", deferrable=True, initially="IMMEDIATE"),
        nullable=True,
        index=True,
    )

    a: Mapped[Optional[float]] = mapped_column(Double, nullable=True)
    a_units: Mapped[Optional[ArrheniusAUnits]] = mapped_column(
        SAEnum(ArrheniusAUnits, name="arrhenius_a_units"),
        nullable=True,
    )
    n: Mapped[Optional[float]] = mapped_column(Double, nullable=True)
    ea_kj_mol: Mapped[Optional[float]] = mapped_column(Double, nullable=True)

    a_uncertainty: Mapped[Optional[float]] = mapped_column(Double, nullable=True)
    a_uncertainty_kind: Mapped[Optional[KineticsUncertaintyKind]] = mapped_column(
        SAEnum(KineticsUncertaintyKind, name="kinetics_uncertainty_kind"),
        nullable=True,
    )
    n_uncertainty: Mapped[Optional[float]] = mapped_column(Double, nullable=True)
    ea_uncertainty_kj_mol: Mapped[Optional[float]] = mapped_column(Double, nullable=True)

    tmin_k: Mapped[Optional[float]] = mapped_column(Double, nullable=True)
    tmax_k: Mapped[Optional[float]] = mapped_column(Double, nullable=True)

    degeneracy: Mapped[Optional[float]] = mapped_column(Double, nullable=True)
    degeneracy_convention: Mapped[KineticsDegeneracyConvention] = mapped_column(
        SAEnum(
            KineticsDegeneracyConvention,
            name="kinetics_degeneracy_convention",
        ),
        nullable=False,
        default=KineticsDegeneracyConvention.unknown,
        server_default=KineticsDegeneracyConvention.unknown.value,
    )
    tunneling_model: Mapped[Optional[TunnelingModel]] = mapped_column(
        SAEnum(TunnelingModel, name="tunneling_model"),
        nullable=True,
    )
    pressure_context: Mapped[Optional[PressureContext]] = mapped_column(
        SAEnum(PressureContext, name="pressure_context"),
        nullable=True,
    )
    pressure_bar: Mapped[Optional[float]] = mapped_column(Double, nullable=True)
    note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    reaction_entry: Mapped["ReactionEntry"] = relationship(
        back_populates="kinetics_records"
    )
    literature: Mapped[Optional["Literature"]] = relationship()
    workflow_tool_release: Mapped[Optional["WorkflowToolRelease"]] = relationship(
        back_populates="kinetics_records"
    )
    software_release: Mapped[Optional["SoftwareRelease"]] = relationship(
        back_populates="kinetics_records"
    )
    network_kinetics: Mapped[Optional["NetworkKinetics"]] = relationship(
        foreign_keys=[network_kinetics_id],
    )
    source_calculations: Mapped[list["KineticsSourceCalculation"]] = relationship(
        back_populates="kinetics",
        cascade="all, delete-orphan",
    )
    arrhenius_entries: Mapped[list["KineticsArrheniusEntry"]] = relationship(
        back_populates="kinetics",
        order_by="KineticsArrheniusEntry.entry_index",
        cascade="all, delete-orphan",
    )
    falloff: Mapped[Optional["KineticsFalloff"]] = relationship(
        back_populates="kinetics",
        uselist=False,
        cascade="all, delete-orphan",
    )
    third_body_efficiencies: Mapped[list["KineticsThirdBodyEfficiency"]] = relationship(
        back_populates="kinetics",
        cascade="all, delete-orphan",
    )
    plog_entries: Mapped[list["KineticsPlog"]] = relationship(
        back_populates="kinetics",
        order_by="KineticsPlog.entry_index",
        cascade="all, delete-orphan",
    )
    chebyshev: Mapped[Optional["KineticsChebyshev"]] = relationship(
        back_populates="kinetics",
        uselist=False,
        cascade="all, delete-orphan",
    )
    interpretation_assignments: Mapped[list["KineticsInterpretationAssignment"]] = relationship(
        back_populates="kinetics", cascade="all, delete-orphan"
    )
    tunneling_applications: Mapped[list["KineticsTunnelingApplication"]] = relationship(
        back_populates="kinetics", cascade="all, delete-orphan"
    )

    __table_args__ = (
        CheckConstraint("tmin_k IS NULL OR tmin_k > 0", name="tmin_k_gt_0"),
        CheckConstraint("tmax_k IS NULL OR tmax_k > 0", name="tmax_k_gt_0"),
        CheckConstraint(
            "tmin_k IS NULL OR tmax_k IS NULL OR tmin_k <= tmax_k",
            name="tmin_le_tmax",
        ),
        CheckConstraint(
            "(a_uncertainty IS NULL) = (a_uncertainty_kind IS NULL)",
            name="a_uncertainty_kind_required_with_value",
        ),
        CheckConstraint(
            "a_uncertainty_kind <> 'multiplicative' OR a_uncertainty >= 1.0",
            name="a_uncertainty_multiplicative_ge_1",
        ),
        CheckConstraint(
            "degeneracy IS NULL OR "
            "(degeneracy > 0 AND degeneracy < 'Infinity'::double precision)",
            name="degeneracy_finite_positive",
        ),
        CheckConstraint("pressure_bar IS NULL OR pressure_bar > 0", name="pressure_bar_gt_0"),
        # An apparent-at-pressure rate must state the pressure it applies at.
        CheckConstraint(
            "pressure_context <> 'apparent_at_pressure' OR pressure_bar IS NOT NULL",
            name="apparent_pressure_requires_pressure_bar",
        ),
    )


class KineticsSourceCalculation(Base):
    """Links kinetics records to supporting calculations by role."""

    __tablename__ = "kinetics_source_calculation"

    kinetics_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("kinetics.id", deferrable=True, initially="IMMEDIATE"),
        nullable=False,
    )
    calculation_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("calculation.id", deferrable=True, initially="IMMEDIATE"),
        nullable=False,
    )
    role: Mapped[KineticsCalculationRole] = mapped_column(
        SAEnum(KineticsCalculationRole, name="kinetics_calc_role"),
        nullable=False,
    )

    kinetics: Mapped["Kinetics"] = relationship(back_populates="source_calculations")
    calculation: Mapped["Calculation"] = relationship()

    __table_args__ = (PrimaryKeyConstraint("kinetics_id", "calculation_id", "role"),)


class KineticsInterpretationAssignment(Base):
    """Exact statistical-mechanics/conformer interpretation used for a rate role."""

    __tablename__ = "kinetics_interpretation_assignment"
    kinetics_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("kinetics.id", deferrable=True, initially="IMMEDIATE"), primary_key=True)
    # ``subject_key`` is ``reactant:<1-based slot>``, ``product:<slot>``, or
    # ``transition_state``.  A role alone is not unique for bimolecular or
    # higher-molecularity reactions.
    subject_key: Mapped[str] = mapped_column(Text, primary_key=True)
    role: Mapped[str] = mapped_column(Text, nullable=False)  # reactant, product, transition_state
    statmech_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("statmech.id", deferrable=True, initially="IMMEDIATE"), nullable=False)
    conformer_selection_id: Mapped[Optional[int]] = mapped_column(BigInteger, ForeignKey("conformer_selection.id", name="fk_kinetics_interp_conformer_selection", deferrable=True, initially="IMMEDIATE"), nullable=True)
    transition_state_entry_id: Mapped[Optional[int]] = mapped_column(BigInteger, ForeignKey("transition_state_entry.id", name="fk_kinetics_interp_ts_entry", deferrable=True, initially="IMMEDIATE"), nullable=True)
    ensemble_policy: Mapped[KineticsEnsemblePolicy] = mapped_column(
        SAEnum(KineticsEnsemblePolicy, name="kinetics_ensemble_policy", create_type=False),
        nullable=False,
    )
    standard_state_convention: Mapped[KineticsStandardStateConvention] = mapped_column(
        SAEnum(
            KineticsStandardStateConvention,
            name="kinetics_standard_state_convention",
            create_type=False,
        ),
        nullable=False,
    )
    degeneracy_interpretation: Mapped[KineticsDegeneracyInterpretation] = mapped_column(
        SAEnum(
            KineticsDegeneracyInterpretation,
            name="kinetics_degeneracy_interpretation",
            create_type=False,
        ),
        nullable=False,
    )
    convention_note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    kinetics: Mapped["Kinetics"] = relationship(back_populates="interpretation_assignments")
    statmech: Mapped["Statmech"] = relationship()
    conformer_selection: Mapped[Optional["ConformerSelection"]] = relationship()
    transition_state_entry: Mapped[Optional["TransitionStateEntry"]] = relationship()

    __table_args__ = (
        # ``substring(... from '[0-9]+$')`` returns NULL when the key has no
        # trailing digits, and PostgreSQL accepts a NULL CHECK, so the old form
        # let ``subject_key='reactantXYZ'`` through. Match the whole shape.
        CheckConstraint(
            "(role = 'transition_state' AND subject_key = 'transition_state' "
            "AND transition_state_entry_id IS NOT NULL AND conformer_selection_id IS NULL) "
            "OR (role IN ('reactant', 'product') "
            "AND subject_key ~ ('^' || role || ':[0-9]+$') "
            "AND transition_state_entry_id IS NULL)",
            name="subject_shape",
        ),
        CheckConstraint(
            "(ensemble_policy <> 'other' AND standard_state_convention <> 'other' "
            "AND degeneracy_interpretation <> 'other') OR convention_note IS NOT NULL",
            name="other_note",
        ),
    )


class KineticsTunnelingApplication(Base):
    """Typed reproducibility evidence for a tunneling correction applied to a rate."""

    __tablename__ = "kinetics_tunneling_application"
    kinetics_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("kinetics.id", deferrable=True, initially="IMMEDIATE"), primary_key=True)
    # The parent ``kinetics.tunneling_model`` owns the PostgreSQL enum type;
    # this column stays textual with the same value set (guarded by CHECK)
    # rather than re-declaring the type.
    model: Mapped[TunnelingModel] = mapped_column(Text, nullable=False)
    # Machine token naming the actual correction when ``model = 'other'``
    # (e.g. ``zero_curvature_tunneling``). Required for ``other`` so an
    # unrecognised correction is still identifiable and replayable.
    model_identifier: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    transition_state_entry_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("transition_state_entry.id", name="fk_kinetics_tunneling_ts_entry", deferrable=True, initially="IMMEDIATE"), nullable=False)
    # The calculation the barriers/energies below were read from. Without it
    # the Eckart barriers this table returns are untraceable.
    source_calculation_id: Mapped[Optional[int]] = mapped_column(BigInteger, ForeignKey("calculation.id", name="fk_kinetics_tunneling_source_calculation", deferrable=True, initially="IMMEDIATE"), nullable=True)
    imaginary_frequency_cm1: Mapped[Optional[float]] = mapped_column(Double, nullable=True)
    frequency_sign_convention: Mapped[str] = mapped_column(Text, nullable=False, server_default="negative_imaginary_cm1")
    reactant_energy_kj_mol: Mapped[Optional[float]] = mapped_column(Double, nullable=True)
    product_energy_kj_mol: Mapped[Optional[float]] = mapped_column(Double, nullable=True)
    forward_barrier_kj_mol: Mapped[Optional[float]] = mapped_column(Double, nullable=True)
    reverse_barrier_kj_mol: Mapped[Optional[float]] = mapped_column(Double, nullable=True)
    energy_zero_convention: Mapped[Optional[EnergyZeroConvention]] = mapped_column(
        SAEnum(EnergyZeroConvention, name="energy_zero_convention", create_type=False),
        nullable=True,
    )
    energy_correction_convention: Mapped[Optional[EnergyCorrectionConvention]] = mapped_column(
        SAEnum(EnergyCorrectionConvention, name="energy_correction_convention", create_type=False),
        nullable=True,
    )
    convention_note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    result_artifact_id: Mapped[Optional[int]] = mapped_column(BigInteger, ForeignKey("calculation_artifact.id", name="fk_kinetics_tunneling_result_artifact", deferrable=True, initially="IMMEDIATE"), nullable=True)
    sct_path_integral_artifact_id: Mapped[Optional[int]] = mapped_column(BigInteger, ForeignKey("calculation_artifact.id", name="fk_kinetics_tunneling_sct_artifact", deferrable=True, initially="IMMEDIATE"), nullable=True)

    kinetics: Mapped["Kinetics"] = relationship(back_populates="tunneling_applications")
    transition_state_entry: Mapped["TransitionStateEntry"] = relationship()
    source_calculation: Mapped[Optional["Calculation"]] = relationship()
    result_artifact: Mapped[Optional["CalculationArtifact"]] = relationship(foreign_keys=[result_artifact_id])
    sct_path_integral_artifact: Mapped[Optional["CalculationArtifact"]] = relationship(foreign_keys=[sct_path_integral_artifact_id])

    __table_args__ = (
        CheckConstraint(
            "model IN ('none', 'wigner', 'eckart', 'sct', 'other')",
            name="model_enum",
        ),
        CheckConstraint(
            "imaginary_frequency_cm1 IS NULL OR imaginary_frequency_cm1 < 0",
            name="imaginary_negative",
        ),
        CheckConstraint(
            "model <> 'other' OR (model_identifier IS NOT NULL AND result_artifact_id IS NOT NULL)",
            name="other_replayable",
        ),
        CheckConstraint(
            "(energy_zero_convention IS DISTINCT FROM 'other' "
            "AND energy_correction_convention IS DISTINCT FROM 'other') "
            "OR convention_note IS NOT NULL",
            name="other_note",
        ),
    )


class KineticsFalloff(Base):
    """Pressure-dependent falloff parameters for a kinetics record (DR-0032).

    Falloff reactions transition between a low-pressure limit (k0,
    effectively third-order) and a high-pressure limit (k∞, second-order).
    The k∞ Arrhenius parameters live on the parent ``kinetics`` row; this
    side table holds the **low-pressure** Arrhenius (k0) and the broadening
    parameters. The parent ``kinetics.model_kind`` (``lindemann`` /
    ``troe`` / ``sri``) selects which broadening columns are meaningful:
    Lindemann uses none, Troe uses ``troe_*``, SRI uses ``sri_*``.
    """

    __tablename__ = "kinetics_falloff"

    kinetics_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("kinetics.id", deferrable=True, initially="IMMEDIATE"),
        primary_key=True,
    )

    # Low-pressure-limit Arrhenius (k0).
    low_a: Mapped[float] = mapped_column(Double, nullable=False)
    low_a_units: Mapped[Optional[ArrheniusAUnits]] = mapped_column(
        SAEnum(ArrheniusAUnits, name="arrhenius_a_units"),
        nullable=True,
    )
    low_n: Mapped[Optional[float]] = mapped_column(Double, nullable=True)
    low_ea_kj_mol: Mapped[Optional[float]] = mapped_column(Double, nullable=True)

    # Troe broadening coefficients (T2 is optional in the Troe form).
    troe_alpha: Mapped[Optional[float]] = mapped_column(Double, nullable=True)
    troe_t3: Mapped[Optional[float]] = mapped_column(Double, nullable=True)
    troe_t1: Mapped[Optional[float]] = mapped_column(Double, nullable=True)
    troe_t2: Mapped[Optional[float]] = mapped_column(Double, nullable=True)

    # SRI broadening coefficients (d, e optional).
    sri_a: Mapped[Optional[float]] = mapped_column(Double, nullable=True)
    sri_b: Mapped[Optional[float]] = mapped_column(Double, nullable=True)
    sri_c: Mapped[Optional[float]] = mapped_column(Double, nullable=True)
    sri_d: Mapped[Optional[float]] = mapped_column(Double, nullable=True)
    sri_e: Mapped[Optional[float]] = mapped_column(Double, nullable=True)

    note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    kinetics: Mapped["Kinetics"] = relationship(back_populates="falloff")


class KineticsThirdBodyEfficiency(Base):
    """Per-collider third-body efficiency for a falloff/third-body reaction.

    Scales the effective bath-gas concentration [M] contributed by a
    specific collider species (e.g. H2O ~ 6, CO2 ~ 2, Ar ~ 0.7). The
    collider is a graph-level ``species`` (identity), resolved from the
    uploaded collider SMILES in the workflow.
    """

    __tablename__ = "kinetics_third_body_efficiency"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    kinetics_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("kinetics.id", deferrable=True, initially="IMMEDIATE"),
        nullable=False,
    )
    collider_species_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("species.id", deferrable=True, initially="IMMEDIATE"),
        nullable=False,
    )
    efficiency: Mapped[float] = mapped_column(Double, nullable=False)

    kinetics: Mapped["Kinetics"] = relationship(
        back_populates="third_body_efficiencies"
    )
    collider_species: Mapped["Species"] = relationship()

    __table_args__ = (
        UniqueConstraint(
            "kinetics_id", "collider_species_id", name="uq_kinetics_collider"
        ),
        CheckConstraint("efficiency >= 0", name="efficiency_ge_0"),
    )


class KineticsPlog(Base):
    """A single pressure entry of a PLOG (logarithmic-interpolation) rate.

    A PLOG rate coefficient is given as modified-Arrhenius parameters at a
    set of pressures; k(T,P) is interpolated in log P between the bracketing
    entries. Stored reaction-level (DR-0032 Part C) so a literature PLOG fit
    can be deposited without a master-equation network/solve. The parent
    ``kinetics.model_kind`` must be ``plog``.
    """

    __tablename__ = "kinetics_plog"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    kinetics_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("kinetics.id", deferrable=True, initially="IMMEDIATE"),
        nullable=False,
    )
    entry_index: Mapped[int] = mapped_column(Integer, nullable=False)
    pressure_bar: Mapped[float] = mapped_column(Double, nullable=False)
    a: Mapped[float] = mapped_column(Double, nullable=False)
    a_units: Mapped[Optional[ArrheniusAUnits]] = mapped_column(
        SAEnum(ArrheniusAUnits, name="arrhenius_a_units"),
        nullable=True,
    )
    n: Mapped[Optional[float]] = mapped_column(Double, nullable=True)
    ea_kj_mol: Mapped[Optional[float]] = mapped_column(Double, nullable=True)

    kinetics: Mapped["Kinetics"] = relationship(back_populates="plog_entries")

    __table_args__ = (
        UniqueConstraint(
            "kinetics_id", "entry_index", name="uq_kinetics_plog_entry"
        ),
        CheckConstraint("entry_index >= 1", name="plog_entry_index_ge_1"),
        CheckConstraint("pressure_bar > 0", name="plog_pressure_bar_gt_0"),
    )


class KineticsArrheniusEntry(Base):
    """One modified-Arrhenius term of a sum-of-Arrhenius rate (DR-0036).

    Represents a single term of a Chemkin ``DUPLICATE`` channel: the rate
    coefficient is the *sum* over these entries,
    ``k(T) = Σ_i A_i · T^{n_i} · exp(−Ea_i / RT)``. Stored reaction-level and
    modelled exactly like ``kinetics_plog`` (a per-index Arrhenius child row)
    but indexed by term rather than by pressure — a duplicate group is a sum,
    not a pressure interpolation. The parent ``kinetics.model_kind`` must be
    ``multi_arrhenius`` and its scalar ``a``/``n``/``ea_kj_mol`` stay null.
    """

    __tablename__ = "kinetics_arrhenius_entry"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    kinetics_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("kinetics.id", deferrable=True, initially="IMMEDIATE"),
        nullable=False,
    )
    entry_index: Mapped[int] = mapped_column(Integer, nullable=False)
    a: Mapped[float] = mapped_column(Double, nullable=False)
    a_units: Mapped[Optional[ArrheniusAUnits]] = mapped_column(
        SAEnum(ArrheniusAUnits, name="arrhenius_a_units"),
        nullable=True,
    )
    n: Mapped[Optional[float]] = mapped_column(Double, nullable=True)
    ea_kj_mol: Mapped[Optional[float]] = mapped_column(Double, nullable=True)

    kinetics: Mapped["Kinetics"] = relationship(back_populates="arrhenius_entries")

    __table_args__ = (
        UniqueConstraint(
            "kinetics_id", "entry_index", name="uq_kinetics_arrhenius_entry"
        ),
        CheckConstraint("entry_index >= 1", name="arrhenius_entry_index_ge_1"),
    )


class KineticsChebyshev(Base):
    """Chebyshev-polynomial k(T,P) surface for a kinetics record.

    Stores the n_T × n_P coefficient matrix and the T/P domain over which
    it is valid. Reaction-level (DR-0032 Part C) so a literature Chebyshev
    fit can be deposited without a master-equation solve. The parent
    ``kinetics.model_kind`` must be ``chebyshev``.
    """

    __tablename__ = "kinetics_chebyshev"

    kinetics_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("kinetics.id", deferrable=True, initially="IMMEDIATE"),
        primary_key=True,
    )
    n_temperature: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    n_pressure: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    tmin_k: Mapped[Optional[float]] = mapped_column(Double, nullable=True)
    tmax_k: Mapped[Optional[float]] = mapped_column(Double, nullable=True)
    pmin_bar: Mapped[Optional[float]] = mapped_column(Double, nullable=True)
    pmax_bar: Mapped[Optional[float]] = mapped_column(Double, nullable=True)
    coefficients: Mapped[list] = mapped_column(JSONB, nullable=False)

    kinetics: Mapped["Kinetics"] = relationship(back_populates="chebyshev")

    __table_args__ = (
        CheckConstraint("n_temperature >= 1", name="cheb_n_temperature_ge_1"),
        CheckConstraint("n_pressure >= 1", name="cheb_n_pressure_ge_1"),
    )
