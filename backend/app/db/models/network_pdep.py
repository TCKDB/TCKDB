from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Double,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    PrimaryKeyConstraint,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import CHAR, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, CreatedByMixin, PublicRefMixin, TimestampMixin
from app.db.models.common import (
    ArrheniusAUnits,
    EnergyCorrectionConvention,
    EnergyZeroConvention,
    NetworkChannelKind,
    NetworkChannelMechanism,
    NetworkEnergyTransferScope,
    NetworkKineticsModelKind,
    NetworkRepresentationRole,
    NetworkSolveCalculationRole,
    NetworkSolveKind,
    NetworkStateKind,
    PressureUnit,
    TemperatureUnit,
)

if TYPE_CHECKING:
    from app.db.models.calculation import Calculation
    from app.db.models.literature import Literature
    from app.db.models.network import Network
    from app.db.models.reaction import ReactionEntry
    from app.db.models.software import SoftwareRelease
    from app.db.models.species import SpeciesEntry
    from app.db.models.transition_state import TransitionStateEntry
    from app.db.models.workflow import WorkflowToolRelease


# ---------------------------------------------------------------------------
# Network state: macroscopic chemically meaningful state (well, bimolecular)
# ---------------------------------------------------------------------------


class NetworkState(Base):
    """A macroscopic state in a reaction network (well or bimolecular channel)."""

    __tablename__ = "network_state"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    network_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("network.id", deferrable=True, initially="IMMEDIATE"),
        nullable=False,
    )
    kind: Mapped[NetworkStateKind] = mapped_column(
        SAEnum(NetworkStateKind, name="network_state_kind"),
        nullable=False,
    )
    composition_hash: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    label: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    network: Mapped["Network"] = relationship(back_populates="states")
    participants: Mapped[list["NetworkStateParticipant"]] = relationship(
        back_populates="state",
        cascade="all, delete-orphan",
    )
    source_channels: Mapped[list["NetworkChannel"]] = relationship(
        back_populates="source_state",
        foreign_keys="NetworkChannel.source_state_id",
    )
    sink_channels: Mapped[list["NetworkChannel"]] = relationship(
        back_populates="sink_state",
        foreign_keys="NetworkChannel.sink_state_id",
    )

    __table_args__ = (
        UniqueConstraint("network_id", "composition_hash"),
    )


class NetworkStateParticipant(Base):
    """Species composition of a network state."""

    __tablename__ = "network_state_participant"

    state_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("network_state.id", deferrable=True, initially="IMMEDIATE"),
        nullable=False,
    )
    species_entry_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("species_entry.id", deferrable=True, initially="IMMEDIATE"),
        nullable=False,
    )
    stoichiometry: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=1, server_default="1"
    )

    state: Mapped["NetworkState"] = relationship(back_populates="participants")
    species_entry: Mapped["SpeciesEntry"] = relationship()

    __table_args__ = (
        PrimaryKeyConstraint("state_id", "species_entry_id"),
        CheckConstraint("stoichiometry >= 1", name="stoichiometry_ge_1"),
    )


# ---------------------------------------------------------------------------
# Network channel: directed phenomenological pathway (source → sink)
# ---------------------------------------------------------------------------


class NetworkChannel(Base):
    """A directed phenomenological channel between two network states."""

    __tablename__ = "network_channel"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    network_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("network.id", deferrable=True, initially="IMMEDIATE"),
        nullable=False,
    )
    source_state_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("network_state.id", deferrable=True, initially="IMMEDIATE"),
        nullable=False,
    )
    sink_state_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("network_state.id", deferrable=True, initially="IMMEDIATE"),
        nullable=False,
    )
    kind: Mapped[NetworkChannelKind] = mapped_column(
        SAEnum(NetworkChannelKind, name="network_channel_kind"),
        nullable=False,
    )
    # Mechanistic attribution, orthogonal to ``kind``. ``elementary`` channels
    # name their elementary step(s) on ``network_channel_microreaction``;
    # ``well_skipping`` channels carry none, because a chemically-activated
    # pathway has no single elementary step behind it. Storing the claim is
    # what keeps "multi-step ME channel" distinguishable from "the producer
    # omitted the paths" — a reader must never have to infer it from an empty
    # child collection.
    mechanism: Mapped[NetworkChannelMechanism] = mapped_column(
        SAEnum(NetworkChannelMechanism, name="network_channel_mechanism"),
        nullable=False,
        server_default=NetworkChannelMechanism.elementary.value,
    )
    # A stable, producer-visible identity is required because distinct
    # mechanistic pathways may have the same macroscopic source and sink.
    # NOT NULL on purpose: PostgreSQL treats NULLs as distinct, so a nullable
    # key would silently defeat ``uq_network_channel_key``, and the read
    # surface types ``channel_key`` as non-optional.
    channel_key: Mapped[str] = mapped_column(Text, nullable=False)

    network: Mapped["Network"] = relationship(back_populates="channels")
    source_state: Mapped["NetworkState"] = relationship(
        foreign_keys=[source_state_id],
        back_populates="source_channels",
    )
    sink_state: Mapped["NetworkState"] = relationship(
        foreign_keys=[sink_state_id],
        back_populates="sink_channels",
    )
    kinetics_records: Mapped[list["NetworkKinetics"]] = relationship(
        back_populates="channel",
        cascade="all, delete-orphan",
    )
    microreaction_links: Mapped[list["NetworkChannelMicroReaction"]] = relationship(
        back_populates="channel", cascade="all, delete-orphan"
    )

    __table_args__ = (
        UniqueConstraint("network_id", "channel_key", name="uq_network_channel_key"),
        CheckConstraint(
            "source_state_id <> sink_state_id",
            name="source_ne_sink",
        ),
    )


class NetworkChannelMicroReaction(Base):
    """Mechanistic evidence explicitly associating a channel with an elementary step/TS.

    ``transition_state_entry_id`` is nullable on purpose: barrierless and
    variational association/dissociation paths have no saddle point to point
    at, and they are ubiquitous in exactly the multi-well networks this table
    exists to describe. Path identity is therefore the surrogate ``id`` with
    two uniqueness rules — one saddle-point path per
    ``(channel, reaction, TS)``, and at most one barrierless path per
    ``(channel, reaction)`` (a NULL TS would otherwise be distinct from
    itself under SQL uniqueness semantics).
    """

    __tablename__ = "network_channel_microreaction"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    channel_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("network_channel.id", deferrable=True, initially="IMMEDIATE"),
        nullable=False,
    )
    reaction_entry_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(
            "reaction_entry.id",
            name="fk_network_channel_microreaction_reaction_entry",
            deferrable=True, initially="IMMEDIATE",
        ),
        nullable=False,
    )
    transition_state_entry_id: Mapped[Optional[int]] = mapped_column(
        BigInteger,
        ForeignKey(
            "transition_state_entry.id",
            name="fk_network_channel_microreaction_ts_entry",
            deferrable=True, initially="IMMEDIATE",
        ),
        nullable=True,
    )

    channel: Mapped["NetworkChannel"] = relationship(back_populates="microreaction_links")
    reaction_entry: Mapped["ReactionEntry"] = relationship()
    transition_state_entry: Mapped[Optional["TransitionStateEntry"]] = relationship()

    __table_args__ = (
        UniqueConstraint(
            "channel_id",
            "reaction_entry_id",
            "transition_state_entry_id",
            name="uq_network_channel_microreaction_path",
        ),
        Index(
            "uq_network_channel_microreaction_barrierless",
            "channel_id",
            "reaction_entry_id",
            unique=True,
            postgresql_where=text("transition_state_entry_id IS NULL"),
        ),
    )


class NetworkSolveChannelBarrier(Base):
    """Solve input barrier for one explicitly identified microscopic path.

    Barriers are signed and measured from ``energy_zero_convention``: a
    submerged entrance barrier is legitimately negative under an
    entrance-channel zero, so no positivity constraint applies. Only NaN is
    rejected — an unrepresentable barrier is a bug, not a scientific claim.
    Barrierless paths carry no row here at all.

    ``forward``/``reverse`` are oriented by the *channel*
    (source → sink), never by the micro reaction's own written direction,
    because the channel is the directed object this row is keyed on.
    """

    __tablename__ = "network_solve_channel_barrier"
    solve_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("network_solve.id", deferrable=True, initially="IMMEDIATE"), primary_key=True)
    channel_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("network_channel.id", deferrable=True, initially="IMMEDIATE"), primary_key=True)
    reaction_entry_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("reaction_entry.id", name="fk_network_solve_channel_barrier_reaction_entry", deferrable=True, initially="IMMEDIATE"), primary_key=True)
    transition_state_entry_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("transition_state_entry.id", name="fk_network_solve_channel_barrier_ts_entry", deferrable=True, initially="IMMEDIATE"), primary_key=True)
    forward_barrier_kj_mol: Mapped[float] = mapped_column(Double, nullable=False)
    reverse_barrier_kj_mol: Mapped[float] = mapped_column(Double, nullable=False)
    energy_zero_convention: Mapped[EnergyZeroConvention] = mapped_column(
        SAEnum(EnergyZeroConvention, name="energy_zero_convention", create_type=False),
        nullable=False,
    )
    correction_convention: Mapped[EnergyCorrectionConvention] = mapped_column(
        SAEnum(EnergyCorrectionConvention, name="energy_correction_convention", create_type=False),
        nullable=False,
    )
    convention_note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    source_calculation_id: Mapped[Optional[int]] = mapped_column(BigInteger, ForeignKey("calculation.id", name="fk_network_solve_channel_barrier_source_calc", deferrable=True, initially="IMMEDIATE"), nullable=True)

    solve: Mapped["NetworkSolve"] = relationship(back_populates="channel_barriers")
    channel: Mapped["NetworkChannel"] = relationship()
    reaction_entry: Mapped["ReactionEntry"] = relationship()
    transition_state_entry: Mapped["TransitionStateEntry"] = relationship()
    source_calculation: Mapped[Optional["Calculation"]] = relationship()

    __table_args__ = (
        CheckConstraint(
            "forward_barrier_kj_mol <> 'NaN'::double precision "
            "AND reverse_barrier_kj_mol <> 'NaN'::double precision",
            name="barriers_finite",
        ),
        CheckConstraint(
            "(energy_zero_convention <> 'other' AND correction_convention <> 'other') "
            "OR convention_note IS NOT NULL",
            name="other_note",
        ),
    )


# ---------------------------------------------------------------------------
# Network solve: one master-equation solution / provenance context
# ---------------------------------------------------------------------------


class NetworkSolve(Base, TimestampMixin, CreatedByMixin, PublicRefMixin):
    """The provenance envelope for one coherent set of k(T,P) on a network.

    ``kind`` says what stands behind it: ``computed`` means the master
    equation was solved here, and the ME settings and T/P envelope below
    describe that run; ``reported`` means the rates were transcribed from the
    publication named by ``literature_id``, and this database holds none of
    the derivation.

    The distinction is stored rather than inferred because every ME-specific
    column here is nullable — before ADR 0010 ``network_id`` was the only NOT
    NULL column, so a row could assert "master-equation solution" by table
    membership alone while carrying no evidence of one. ``kind`` is what
    turns that into a checkable claim.

    Two rules bind this table that are **not visible in this class**, because
    SQLAlchemy has no way to declare them:

    * ``ck_network_solve_reported_requires_literature`` below is only half the
      contract. The other half is a deferred constraint trigger installed by
      migration ``f9b2e6c4a1d7``, which refuses *at COMMIT* a ``computed``
      solve carrying no state energy, no energy-transfer model where the
      network declares a well, or no channel barrier where it declares a
      saddle-point path. It has to be deferred: this row is written before the
      children that carry its id, so a per-statement check would refuse every
      legitimate insert.
    * That trigger guarantees **existence**, not coverage. One energy per
      state, one ⟨ΔE⟩down per (well, collider) pair, one barrier per
      saddle-point path — those remain properties of the wired upload path,
      enforced by ``validate_mechanistic_channel_evidence`` in
      ``app/schemas/workflows/network_pdep_upload.py``. See ADR 0010's
      amendment.
    """

    __tablename__ = "network_solve"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    network_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("network.id", deferrable=True, initially="IMMEDIATE"),
        nullable=False,
    )

    # Whether the master equation was solved here or the rates were read out
    # of a publication. A ``reported`` solve is not required to carry state
    # energies, channel barriers or an energy-transfer model, and must name
    # the literature it was transcribed from (ADR 0010).
    kind: Mapped[NetworkSolveKind] = mapped_column(
        SAEnum(NetworkSolveKind, name="network_solve_kind"),
        nullable=False,
        server_default=NetworkSolveKind.computed.value,
    )

    # Provenance triple
    literature_id: Mapped[Optional[int]] = mapped_column(
        BigInteger,
        ForeignKey("literature.id", deferrable=True, initially="IMMEDIATE"),
        nullable=True,
        index=True,
    )
    software_release_id: Mapped[Optional[int]] = mapped_column(
        BigInteger,
        ForeignKey("software_release.id", deferrable=True, initially="IMMEDIATE"),
        nullable=True,
    )
    workflow_tool_release_id: Mapped[Optional[int]] = mapped_column(
        BigInteger,
        ForeignKey("workflow_tool_release.id", deferrable=True, initially="IMMEDIATE"),
        nullable=True,
    )

    # ME method and grain settings
    me_method: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    interpolation_model: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    grain_size_cm_inv: Mapped[Optional[float]] = mapped_column(Double, nullable=True)
    grain_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    emax_kj_mol: Mapped[Optional[float]] = mapped_column(Double, nullable=True)

    # T/P range of the solve
    tmin_k: Mapped[Optional[float]] = mapped_column(Double, nullable=True)
    tmax_k: Mapped[Optional[float]] = mapped_column(Double, nullable=True)
    pmin_bar: Mapped[Optional[float]] = mapped_column(Double, nullable=True)
    pmax_bar: Mapped[Optional[float]] = mapped_column(Double, nullable=True)

    # Versioned declarations (``tckdb_schemas.network_declarations``): what the outputs are
    # outputs of, the recipe, and the evidence cited. Attributed claims, stored in their
    # resolved form (states by composition hash, determinations by public ref) and never
    # inferred: NULL on every solve deposited before the columns or without them, and then
    # the solve's scientific meaning reads as unresolved. ``none_as_null``: Python ``None`` is
    # SQL NULL ("not stated"), never the JSON value ``null``.
    target_declaration: Mapped[Optional[dict]] = mapped_column(JSONB(none_as_null=True), nullable=True)
    protocol_declaration: Mapped[Optional[dict]] = mapped_column(JSONB(none_as_null=True), nullable=True)
    validation_declaration: Mapped[Optional[dict]] = mapped_column(JSONB(none_as_null=True), nullable=True)

    note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Relationships
    network: Mapped["Network"] = relationship(back_populates="solves")
    literature: Mapped[Optional["Literature"]] = relationship()
    software_release: Mapped[Optional["SoftwareRelease"]] = relationship(
        back_populates="network_solves",
    )
    workflow_tool_release: Mapped[Optional["WorkflowToolRelease"]] = relationship(
        back_populates="network_solves",
    )
    bath_gases: Mapped[list["NetworkSolveBathGas"]] = relationship(
        back_populates="solve",
        cascade="all, delete-orphan",
    )
    energy_transfers: Mapped[list["NetworkSolveEnergyTransfer"]] = relationship(
        back_populates="solve",
        cascade="all, delete-orphan",
    )
    source_calculations: Mapped[list["NetworkSolveSourceCalculation"]] = relationship(
        back_populates="solve",
        cascade="all, delete-orphan",
    )
    kinetics_records: Mapped[list["NetworkKinetics"]] = relationship(
        back_populates="solve",
        cascade="all, delete-orphan",
    )
    determinations: Mapped[list["NetworkKineticsDetermination"]] = relationship(
        back_populates="solve",
        cascade="all, delete-orphan",
    )
    state_energies: Mapped[list["NetworkSolveStateEnergy"]] = relationship(
        back_populates="solve", cascade="all, delete-orphan"
    )
    channel_barriers: Mapped[list["NetworkSolveChannelBarrier"]] = relationship(
        back_populates="solve", cascade="all, delete-orphan"
    )

    __table_args__ = (
        # A ``reported`` solve's whole claim is "this paper says so", which is
        # what pays for relaxing the coverage rules. With no literature it
        # would assert rates carrying neither a derivation nor a source —
        # weaker than either form this axis exists to admit.
        CheckConstraint(
            "kind <> 'reported' OR literature_id IS NOT NULL",
            name="reported_requires_literature",
        ),
        CheckConstraint("tmin_k IS NULL OR tmin_k > 0", name="tmin_k_gt_0"),
        CheckConstraint("tmax_k IS NULL OR tmax_k > 0", name="tmax_k_gt_0"),
        CheckConstraint(
            "tmin_k IS NULL OR tmax_k IS NULL OR tmin_k <= tmax_k",
            name="tmin_le_tmax",
        ),
        CheckConstraint("pmin_bar IS NULL OR pmin_bar > 0", name="pmin_bar_gt_0"),
        CheckConstraint("pmax_bar IS NULL OR pmax_bar > 0", name="pmax_bar_gt_0"),
        CheckConstraint(
            "pmin_bar IS NULL OR pmax_bar IS NULL OR pmin_bar <= pmax_bar",
            name="pmin_le_pmax",
        ),
        CheckConstraint(
            "grain_count IS NULL OR grain_count >= 1",
            name="grain_count_ge_1",
        ),
        # The ``coalesce`` matters: an object with no ``version`` key makes the predicate NULL,
        # and a CHECK passes on NULL.
        CheckConstraint(
            "target_declaration IS NULL OR (jsonb_typeof(target_declaration) = 'object' "
            "AND coalesce(jsonb_typeof(target_declaration -> 'version'), '') = 'number')",
            name="target_declaration_versioned_object",
        ),
        CheckConstraint(
            "protocol_declaration IS NULL OR (jsonb_typeof(protocol_declaration) = 'object' "
            "AND coalesce(jsonb_typeof(protocol_declaration -> 'version'), '') = 'number')",
            name="protocol_declaration_versioned_object",
        ),
        CheckConstraint(
            "validation_declaration IS NULL OR (jsonb_typeof(validation_declaration) = 'object' "
            "AND coalesce(jsonb_typeof(validation_declaration -> 'version'), '') = 'number')",
            name="validation_declaration_versioned_object",
        ),
    )


class NetworkSolveBathGas(Base):
    """Bath gas composition for one network solve."""

    __tablename__ = "network_solve_bath_gas"

    solve_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("network_solve.id", deferrable=True, initially="IMMEDIATE"),
        nullable=False,
    )
    species_entry_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("species_entry.id", deferrable=True, initially="IMMEDIATE"),
        nullable=False,
    )
    mole_fraction: Mapped[float] = mapped_column(Double, nullable=False)

    solve: Mapped["NetworkSolve"] = relationship(back_populates="bath_gases")
    species_entry: Mapped["SpeciesEntry"] = relationship()

    __table_args__ = (
        PrimaryKeyConstraint("solve_id", "species_entry_id"),
        CheckConstraint(
            "mole_fraction > 0 AND mole_fraction <= 1",
            name="mole_fraction_range",
        ),
    )


class NetworkSolveEnergyTransfer(Base):
    """Energy transfer model parameters for one network solve.

    ``scope`` says what the declaration was specified over. A ``per_well`` row
    resolves both axes and carries ``state_id`` and
    ``collider_species_entry_id``; a ``network_wide`` row carries neither,
    because the producer declared one model for the whole network and the
    well-resolved values simply do not exist. The check constraint keeps the
    two shapes from blurring into each other, so a NULL ``state_id`` is always
    an explained absence rather than a dropped field. See ADR 0009.
    """

    __tablename__ = "network_solve_energy_transfer"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    solve_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("network_solve.id", deferrable=True, initially="IMMEDIATE"),
        nullable=False,
    )
    scope: Mapped[NetworkEnergyTransferScope] = mapped_column(
        SAEnum(NetworkEnergyTransferScope, name="network_energy_transfer_scope"),
        nullable=False,
        server_default=NetworkEnergyTransferScope.per_well.value,
    )
    state_id: Mapped[Optional[int]] = mapped_column(
        BigInteger,
        ForeignKey(
            "network_state.id",
            name="fk_network_solve_energy_transfer_state",
            deferrable=True, initially="IMMEDIATE",
        ),
        nullable=True,
    )
    collider_species_entry_id: Mapped[Optional[int]] = mapped_column(
        BigInteger,
        ForeignKey(
            "species_entry.id",
            name="fk_network_solve_energy_transfer_collider",
            deferrable=True, initially="IMMEDIATE",
        ),
        nullable=True,
    )

    model: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    alpha0_cm_inv: Mapped[Optional[float]] = mapped_column(Double, nullable=True)
    t_exponent: Mapped[Optional[float]] = mapped_column(Double, nullable=True)
    t_ref_k: Mapped[Optional[float]] = mapped_column(Double, nullable=True)
    note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    solve: Mapped["NetworkSolve"] = relationship(back_populates="energy_transfers")
    state: Mapped[Optional["NetworkState"]] = relationship()
    collider_species_entry: Mapped[Optional["SpeciesEntry"]] = relationship()

    __table_args__ = (
        UniqueConstraint("solve_id", "state_id", "collider_species_entry_id", name="uq_network_solve_energy_transfer_scope"),
        CheckConstraint(
            "(scope = 'per_well' AND state_id IS NOT NULL "
            "AND collider_species_entry_id IS NOT NULL) OR "
            "(scope = 'network_wide' AND state_id IS NULL "
            "AND collider_species_entry_id IS NULL)",
            name="scope_columns_agree",
        ),
        # ``uq_..._scope`` above cannot police the network-wide row: Postgres
        # treats NULLs as distinct, so it would happily accept two of them.
        Index(
            "uq_network_solve_energy_transfer_network_wide",
            "solve_id",
            unique=True,
            postgresql_where=text("scope = 'network_wide'"),
        ),
    )


class NetworkSolveStateEnergy(Base):
    """Solve-specific state energy with an explicit zero/correction convention."""

    __tablename__ = "network_solve_state_energy"

    solve_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("network_solve.id", deferrable=True, initially="IMMEDIATE"), primary_key=True
    )
    state_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("network_state.id", deferrable=True, initially="IMMEDIATE"), primary_key=True
    )
    energy_kj_mol: Mapped[float] = mapped_column(Double, nullable=False)
    energy_zero_convention: Mapped[EnergyZeroConvention] = mapped_column(
        SAEnum(EnergyZeroConvention, name="energy_zero_convention", create_type=False),
        nullable=False,
    )
    correction_convention: Mapped[EnergyCorrectionConvention] = mapped_column(
        SAEnum(EnergyCorrectionConvention, name="energy_correction_convention", create_type=False),
        nullable=False,
    )
    convention_note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    source_calculation_id: Mapped[Optional[int]] = mapped_column(
        BigInteger, ForeignKey("calculation.id", deferrable=True, initially="IMMEDIATE"), nullable=True
    )

    #: What comparing the stated energy with the sum of its per-participant
    #: sources concluded at upload (#678): ``agrees`` or ``not_compared``. NULL
    #: means the row was deposited before the comparison existed, and is not a
    #: pass. A *disagreement* is never stored: it refuses the deposit. No computed
    #: total is stored either; the sum is recomputed, never kept.
    source_sum_comparison: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    #: Why the comparison could not be made. Present exactly when
    #: ``source_sum_comparison`` is ``not_compared``.
    source_sum_not_compared_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    #: The rounding unit of ``energy_kj_mol`` the producer stated, when it stated one. NULL is
    #: "not stated" (never inferred from the digits of the number).
    energy_precision_kj_mol: Mapped[Optional[float]] = mapped_column(Double, nullable=True)

    solve: Mapped["NetworkSolve"] = relationship(back_populates="state_energies")
    state: Mapped["NetworkState"] = relationship()
    source_calculation: Mapped[Optional["Calculation"]] = relationship()
    #: One calculation per participant (#678); empty on a row that used the
    #: single ``source_calculation_id`` slot.
    participant_sources: Mapped[list["NetworkSolveStateEnergySource"]] = relationship(
        back_populates="state_energy",
        viewonly=True,
    )

    __table_args__ = (
        CheckConstraint(
            "(energy_zero_convention <> 'other' AND correction_convention <> 'other') "
            "OR convention_note IS NOT NULL",
            name="other_note",
        ),
        CheckConstraint(
            "source_sum_comparison IS NULL OR source_sum_comparison IN ('agrees', 'not_compared')",
            name="sum_comparison",
        ),
        CheckConstraint(
            "(source_sum_comparison IS NOT DISTINCT FROM 'not_compared') = "
            "(source_sum_not_compared_reason IS NOT NULL)",
            name="sum_reason_shape",
        ),
        CheckConstraint(
            "source_sum_not_compared_reason IS NULL OR source_sum_not_compared_reason IN ("
            "'no_source_stated', 'sources_incomplete', 'convention_not_summable', "
            "'energy_zero_not_comparable', 'stored_energy_not_stated', 'zpe_not_in_source', "
            "'no_second_state_on_the_same_zero', 'stated_precision_unknown')",
            name="sum_reason_token",
        ),
        CheckConstraint(
            "energy_precision_kj_mol IS NULL OR "
            "(energy_precision_kj_mol > 0 AND energy_precision_kj_mol < 'Infinity'::float8)",
            name="energy_precision_positive",
        ),
    )


class NetworkSolveStateEnergySource(Base):
    """The calculation one participant of a state contributes to its energy (#678).

    A bimolecular state's energy is a sum over its species, so one source slot
    cannot name where it came from. One row per participant: ``stoichiometry``
    lives on the participant row (``2A`` is one participant with coefficient 2),
    so no copy index is needed. The composite foreign keys tie the row to an
    existing state energy and to a participant of that very state. Whose
    calculation it is, and its type, are checked by the upload service.
    """

    __tablename__ = "network_solve_state_energy_source"

    solve_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    # ``state_id`` and ``species_entry_id`` also carry plain single-column foreign keys to the
    # tables they identify. The composite keys below enforce the pairing, but the release
    # serializer resolves a foreign key to a public ref or natural key through the column's own
    # target, and a composite target (a state energy, a participant) has neither: without these
    # a released row would name no state and no species.
    state_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(
            "network_state.id",
            name="fk_nsse_source_state_id_network_state",
            deferrable=True,
            initially="IMMEDIATE",
        ),
        nullable=False,
    )
    species_entry_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(
            "species_entry.id",
            name="fk_nsse_source_species_entry_id_species_entry",
            deferrable=True,
            initially="IMMEDIATE",
        ),
        nullable=False,
    )
    calculation_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(
            "calculation.id",
            name="fk_nsse_source_calculation_id_calculation",
            deferrable=True,
            initially="IMMEDIATE",
        ),
        nullable=False,
    )

    state_energy: Mapped["NetworkSolveStateEnergy"] = relationship(
        back_populates="participant_sources",
        viewonly=True,
    )
    calculation: Mapped["Calculation"] = relationship()

    __table_args__ = (
        PrimaryKeyConstraint("solve_id", "state_id", "species_entry_id"),
        ForeignKeyConstraint(
            ["solve_id", "state_id"],
            ["network_solve_state_energy.solve_id", "network_solve_state_energy.state_id"],
            name="fk_nsse_source_state_energy",
            deferrable=True,
            initially="IMMEDIATE",
        ),
        ForeignKeyConstraint(
            ["state_id", "species_entry_id"],
            ["network_state_participant.state_id", "network_state_participant.species_entry_id"],
            name="fk_nsse_source_participant",
            deferrable=True,
            initially="IMMEDIATE",
        ),
    )


class NetworkSolveSourceCalculation(Base):
    """Links a network solve to supporting calculations by role."""

    __tablename__ = "network_solve_source_calculation"

    solve_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("network_solve.id", deferrable=True, initially="IMMEDIATE"),
        nullable=False,
    )
    calculation_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("calculation.id", deferrable=True, initially="IMMEDIATE"),
        nullable=False,
    )
    role: Mapped[NetworkSolveCalculationRole] = mapped_column(
        SAEnum(NetworkSolveCalculationRole, name="network_solve_calc_role"),
        nullable=False,
    )

    solve: Mapped["NetworkSolve"] = relationship(back_populates="source_calculations")
    calculation: Mapped["Calculation"] = relationship()

    __table_args__ = (
        PrimaryKeyConstraint("solve_id", "calculation_id", "role"),
    )


# ---------------------------------------------------------------------------
# Network kinetics: fitted k(T,P) for one channel under one solve
# ---------------------------------------------------------------------------


class NetworkKineticsDetermination(Base, TimestampMixin, CreatedByMixin, PublicRefMixin):
    """One complete determination of one channel's coefficient within one solve.

    What several fits of a channel are fits *of*: alternate representations (a PLOG and a
    Chebyshev fit of one solve's output) point here and share it, so they are never counted as
    independent support for one another. Separate solves, or separate channels, are separate
    determinations.

    **Identity, not provenance.** Its identity is content: the solve, the channel, the
    solve-scoped ``determination_key`` and the declared observable. ``identity_hash`` is the
    unique digest of that content, so the same content resolves to one row.

    **Immutable from creation**, including while shared by several fits: a trigger refuses every
    UPDATE (``trg_network_kinetics_determination_immutable``). It is an ownership child of its
    solve, so adding one under an accepted solve is refused like any other solve child.

    Nothing here is inferred: a determination exists only because a depositor stated one. Fits
    deposited without one have none and read as unresolved. That the channel belongs to the
    solve's network is a cross-table fact no CHECK can state; the write path enforces it
    (``app.services.network_declaration_resolution``).
    """

    __tablename__ = "network_kinetics_determination"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    solve_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("network_solve.id", deferrable=True, initially="IMMEDIATE"),
        nullable=False,
        index=True,
    )
    channel_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("network_channel.id", deferrable=True, initially="IMMEDIATE"),
        nullable=False,
    )
    determination_key: Mapped[str] = mapped_column(Text, nullable=False)
    observable_declaration: Mapped[dict] = mapped_column(JSONB, nullable=False)
    identity_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)

    solve: Mapped["NetworkSolve"] = relationship(back_populates="determinations")
    channel: Mapped["NetworkChannel"] = relationship()
    kinetics_records: Mapped[list["NetworkKinetics"]] = relationship(back_populates="determination")

    __table_args__ = (
        UniqueConstraint("solve_id", "determination_key", name="uq_network_kinetics_determination_key"),
        CheckConstraint(
            "length(btrim(determination_key)) > 0 AND length(determination_key) <= 128",
            name="key_bounded",
        ),
        CheckConstraint("identity_hash ~ '^[0-9a-f]{64}$'", name="identity_hash_sha256_hex"),
        CheckConstraint(
            "jsonb_typeof(observable_declaration) = 'object' "
            "AND coalesce(jsonb_typeof(observable_declaration -> 'version'), '') = 'number'",
            name="observable_versioned_object",
        ),
    )


class NetworkKinetics(Base, TimestampMixin, PublicRefMixin):
    """One fitted phenomenological k(T,P) for a channel from a specific solve."""

    __tablename__ = "network_kinetics"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    channel_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("network_channel.id", deferrable=True, initially="IMMEDIATE"),
        nullable=False,
    )
    solve_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("network_solve.id", deferrable=True, initially="IMMEDIATE"),
        nullable=False,
    )
    model_kind: Mapped[NetworkKineticsModelKind] = mapped_column(
        SAEnum(NetworkKineticsModelKind, name="network_kinetics_model_kind"),
        nullable=False,
    )

    # Parent-level units and ranges
    tmin_k: Mapped[Optional[float]] = mapped_column(Double, nullable=True)
    tmax_k: Mapped[Optional[float]] = mapped_column(Double, nullable=True)
    pmin_bar: Mapped[Optional[float]] = mapped_column(Double, nullable=True)
    pmax_bar: Mapped[Optional[float]] = mapped_column(Double, nullable=True)
    rate_units: Mapped[Optional[ArrheniusAUnits]] = mapped_column(
        SAEnum(ArrheniusAUnits, name="arrhenius_a_units", create_type=False),
        nullable=True,
    )
    pressure_units: Mapped[Optional[PressureUnit]] = mapped_column(
        SAEnum(PressureUnit, name="pressure_unit"),
        nullable=True,
    )
    temperature_units: Mapped[Optional[TemperatureUnit]] = mapped_column(
        SAEnum(TemperatureUnit, name="temperature_unit"),
        nullable=True,
    )
    stores_log10_k: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)

    # The determination this fit is one representation of, its role there and its own
    # declared identity (network selection). Attributed claims, never inferred: NULL on every
    # fit that predates the columns or was deposited without them, and then its relationship to
    # the channel's other fits reads as unresolved. Set together or not at all
    # (``ck_network_kinetics_determination_iff_role`` and ``..._iff_representation``). That the
    # determination is of this fit's channel and solve is enforced where the row is written.
    determination_id: Mapped[Optional[int]] = mapped_column(
        BigInteger,
        ForeignKey(
            "network_kinetics_determination.id",
            name="fk_network_kinetics_determination_ref",
            deferrable=True,
            initially="IMMEDIATE",
        ),
        nullable=True,
        index=True,
    )
    representation_role: Mapped[Optional[NetworkRepresentationRole]] = mapped_column(
        SAEnum(NetworkRepresentationRole, name="network_representation_role"),
        nullable=True,
    )
    representation_declaration: Mapped[Optional[dict]] = mapped_column(
        JSONB(none_as_null=True), nullable=True
    )

    note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Relationships
    determination: Mapped[Optional["NetworkKineticsDetermination"]] = relationship(
        back_populates="kinetics_records"
    )
    channel: Mapped["NetworkChannel"] = relationship(back_populates="kinetics_records")
    solve: Mapped["NetworkSolve"] = relationship(back_populates="kinetics_records")
    chebyshev: Mapped[Optional["NetworkKineticsChebyshev"]] = relationship(
        back_populates="kinetics",
        cascade="all, delete-orphan",
        uselist=False,
    )
    plog_entries: Mapped[list["NetworkKineticsPlog"]] = relationship(
        back_populates="kinetics",
        cascade="all, delete-orphan",
    )
    points: Mapped[list["NetworkKineticsPoint"]] = relationship(
        back_populates="kinetics",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        CheckConstraint("tmin_k IS NULL OR tmin_k > 0", name="tmin_k_gt_0"),
        CheckConstraint("tmax_k IS NULL OR tmax_k > 0", name="tmax_k_gt_0"),
        CheckConstraint(
            "tmin_k IS NULL OR tmax_k IS NULL OR tmin_k <= tmax_k",
            name="tmin_le_tmax",
        ),
        CheckConstraint("pmin_bar IS NULL OR pmin_bar > 0", name="pmin_bar_gt_0"),
        CheckConstraint("pmax_bar IS NULL OR pmax_bar > 0", name="pmax_bar_gt_0"),
        CheckConstraint(
            "pmin_bar IS NULL OR pmax_bar IS NULL OR pmin_bar <= pmax_bar",
            name="pmin_le_pmax",
        ),
        CheckConstraint(
            "(determination_id IS NULL) = (representation_role IS NULL)",
            name="determination_iff_role",
        ),
        CheckConstraint(
            "(determination_id IS NULL) = (representation_declaration IS NULL)",
            name="determination_iff_representation",
        ),
        CheckConstraint(
            "representation_declaration IS NULL OR (jsonb_typeof(representation_declaration) = 'object' "
            "AND coalesce(jsonb_typeof(representation_declaration -> 'version'), '') = 'number' "
            "AND coalesce(jsonb_typeof(representation_declaration -> 'key'), '') = 'string')",
            name="representation_declaration_versioned_object",
        ),
        # Alternate fits of one determination are told apart by their declared key.
        Index(
            "uq_network_kinetics_representation_key",
            "determination_id",
            text("(representation_declaration ->> 'key')"),
            unique=True,
            postgresql_where=text("determination_id IS NOT NULL"),
        ),
    )


# ---------------------------------------------------------------------------
# Per-parameterization child tables (1:1 or 1:many with network_kinetics)
# ---------------------------------------------------------------------------


class NetworkKineticsChebyshev(Base):
    """Chebyshev polynomial coefficients for a network kinetics record."""

    __tablename__ = "network_kinetics_chebyshev"

    network_kinetics_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(
            "network_kinetics.id",
            deferrable=True,
            initially="IMMEDIATE",
            name="fk_network_kinetics_chebyshev_network_kinetics_id",
        ),
        primary_key=True,
    )

    n_temperature: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    n_pressure: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    coefficients: Mapped[dict] = mapped_column(JSONB, nullable=False)

    kinetics: Mapped["NetworkKinetics"] = relationship(back_populates="chebyshev")

    __table_args__ = (
        CheckConstraint("n_temperature >= 1", name="n_temperature_ge_1"),
        CheckConstraint("n_pressure >= 1", name="n_pressure_ge_1"),
    )


class NetworkKineticsPlog(Base):
    """One PLOG entry: Arrhenius parameters at a discrete pressure."""

    __tablename__ = "network_kinetics_plog"

    network_kinetics_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("network_kinetics.id", deferrable=True, initially="IMMEDIATE"),
        nullable=False,
    )
    pressure_bar: Mapped[float] = mapped_column(Double, nullable=False)
    entry_index: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=1, server_default="1"
    )

    a: Mapped[float] = mapped_column(Double, nullable=False)
    a_units: Mapped[Optional[ArrheniusAUnits]] = mapped_column(
        SAEnum(ArrheniusAUnits, name="arrhenius_a_units", create_type=False),
        nullable=True,
    )
    n: Mapped[float] = mapped_column(Double, nullable=False)
    ea_kj_mol: Mapped[float] = mapped_column(Double, nullable=False)

    kinetics: Mapped["NetworkKinetics"] = relationship(back_populates="plog_entries")

    __table_args__ = (
        PrimaryKeyConstraint("network_kinetics_id", "pressure_bar", "entry_index"),
        CheckConstraint("pressure_bar > 0", name="pressure_bar_gt_0"),
        CheckConstraint("entry_index >= 1", name="entry_index_ge_1"),
    )


class NetworkKineticsPoint(Base):
    """One tabulated k(T,P) data point."""

    __tablename__ = "network_kinetics_point"

    network_kinetics_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("network_kinetics.id", deferrable=True, initially="IMMEDIATE"),
        nullable=False,
    )
    temperature_k: Mapped[float] = mapped_column(Double, nullable=False)
    pressure_bar: Mapped[float] = mapped_column(Double, nullable=False)

    rate_value: Mapped[float] = mapped_column(Double, nullable=False)

    kinetics: Mapped["NetworkKinetics"] = relationship(back_populates="points")

    __table_args__ = (
        PrimaryKeyConstraint("network_kinetics_id", "temperature_k", "pressure_bar"),
        CheckConstraint("temperature_k > 0", name="temperature_k_gt_0"),
        CheckConstraint("pressure_bar > 0", name="pressure_bar_gt_0"),
    )
