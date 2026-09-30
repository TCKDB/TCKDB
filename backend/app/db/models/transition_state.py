from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    Text,
    UniqueConstraint,
)
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, CreatedByMixin, PublicRefMixin, TimestampMixin
from app.db.models.common import (
    TransitionStateEntryStatus,
    TransitionStateSelectionKind,
)
from app.db.types import RDKitMol

if TYPE_CHECKING:
    from app.db.models.calculation import Calculation
    from app.db.models.geometry import Geometry
    from app.db.models.reaction import ReactionEntry
    from app.db.models.reaction_atom_map import ReactionAtomMap


class TransitionState(Base, TimestampMixin, CreatedByMixin, PublicRefMixin):
    """Reaction-channel-level transition-state concept.

    This groups candidate saddle-point structures that belong to the same
    reaction-channel interpretation.
    """

    __tablename__ = "transition_state"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    reaction_entry_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("reaction_entry.id", deferrable=True, initially="IMMEDIATE"),
        nullable=False,
    )

    label: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    reaction_entry: Mapped["ReactionEntry"] = relationship(
        back_populates="transition_states"
    )
    entries: Mapped[list["TransitionStateEntry"]] = relationship(
        back_populates="transition_state",
        cascade="all, delete-orphan",
    )
    selections: Mapped[list["TransitionStateSelection"]] = relationship(
        back_populates="transition_state",
        cascade="all, delete-orphan",
    )


class TransitionStateEntry(Base, TimestampMixin, CreatedByMixin, PublicRefMixin):
    """One candidate transition-state geometry family member under a TS concept.

    Calculations refine or validate this candidate.
    """

    __tablename__ = "transition_state_entry"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    transition_state_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("transition_state.id", deferrable=True, initially="IMMEDIATE"),
        nullable=False,
    )

    charge: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    multiplicity: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    mol: Mapped[Optional[str]] = mapped_column(RDKitMol(), nullable=True)
    unmapped_smiles: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    status: Mapped[TransitionStateEntryStatus] = mapped_column(
        SAEnum(TransitionStateEntryStatus, name="transition_state_entry_status"),
        nullable=False,
        default=TransitionStateEntryStatus.optimized,
        server_default=TransitionStateEntryStatus.optimized.value,
    )

    transition_state: Mapped["TransitionState"] = relationship(back_populates="entries")
    calculations: Mapped[list["Calculation"]] = relationship(
        back_populates="transition_state_entry",
        foreign_keys="Calculation.transition_state_entry_id",
    )
    validation_evidence: Mapped[list["TransitionStateValidationEvidence"]] = relationship(
        back_populates="transition_state_entry", cascade="all, delete-orphan"
    )
    #: Atom correspondence across the reaction this saddle point sits in
    #: (ADR 0011). At most one, keyed on the owning reaction entry. Distinct
    #: from ``validation_evidence``'s participant mappings, which partition the
    #: TS atoms among participant molecules without saying which atom is which.
    atom_maps: Mapped[list["ReactionAtomMap"]] = relationship(
        back_populates="transition_state_entry",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        CheckConstraint("multiplicity >= 1", name="multiplicity_ge_1"),
    )


class TransitionStateSelection(Base, TimestampMixin, CreatedByMixin):
    """Store explicit workflow, curation, or UI selections for transition states.

    This is the curation overlay analog of
    :class:`~app.db.models.species.ConformerSelection` for transition states.
    Unlike conformer selection there is deliberately no assignment-scheme
    dimension: a transition-state selection is a human/workflow choice, not the
    output of an algorithmic assignment step.
    """

    __tablename__ = "transition_state_selection"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)

    transition_state_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(
            "transition_state.id",
            name="fk_ts_selection_transition_state",
            deferrable=True,
            initially="IMMEDIATE",
        ),
        nullable=False,
    )

    selection_kind: Mapped[TransitionStateSelectionKind] = mapped_column(
        SAEnum(TransitionStateSelectionKind, name="transition_state_selection_kind"),
        nullable=False,
    )

    note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    transition_state: Mapped["TransitionState"] = relationship(
        back_populates="selections"
    )

    __table_args__ = (
        UniqueConstraint(
            "transition_state_id",
            "selection_kind",
            name="uq_transition_state_selection_transition_state_id",
        ),
    )


class TransitionStateValidationEvidence(Base, TimestampMixin, CreatedByMixin):
    """Structured validation result for one TS candidate.

    Three ``kind`` values, at most one row per kind per entry
    (``uq_ts_validation_evidence_kind``):

    ``irc``
        The reconstructed path connects the declared endpoints. Carries the
        optional participant mappings below.
    ``energy_ordering``
        The saddle point lies above both wells. The energies compared live in
        ``transition_state_validation_energy`` rows, each naming its own source
        calculation, so this row carries no source calculation of its own.
    ``imaginary_mode``
        The frequency calculation found the expected imaginary mode. Carries
        the count, the reaction-coordinate mode's frequency and the producer's
        displacement-agreement verdict; ``reconstruction_calculation_id`` is
        the ``freq`` calculation.

    ``reconstruction_calculation_id`` keeps the name it had when ``irc`` was
    the only kind. For ``irc`` it is the calculation that reconstructed the
    path; for ``imaginary_mode`` it is the calculation that found the mode.
    Renaming a column of a deployed table to say so would touch every reader
    for no change in what it holds, so the name stays and this says what it
    means. It is NULL exactly for ``energy_ordering``
    (``ck_transition_state_validation_evidence_source_calc_shape``).

    What is and is not stored about a mode
    --------------------------------------
    A producer's *conclusion* about a mode (``mode_displacement_agrees``) and
    the numbers it rests on (count, frequency) are stored, as a claim with its
    evidence. The arithmetic anyone can redo from the stored Hessian is not:
    ADR 0012's eigenvector projections still run at *read* time
    (``include=imaginary_mode_projections``) and write nothing, because
    ``calc_hessian`` stores the matrix those vectors diagonalise. The displacement
    flag is recorded as the verdict it is and is never recomputed here.
    ADR 0013's earlier position, that no mode evidence belongs in this table,
    is superseded for the conclusion and unchanged for the projections.

    Indices relative to what
    ------------------------
    ``reactant_participant_mapping`` and ``product_participant_mapping`` say
    which saddle-point atoms become which declared participant, by index. An
    atom index is a property of a *geometry*, not of a transition state:
    ``geometry_atom.atom_index`` counts into one specific set of coordinates,
    and a TS entry can accumulate several geometries as it is re-optimised or
    recalculated at another level of theory, with no guarantee that a later
    one lists its atoms in the same order. So an index with no geometry named
    beside it does not identify an atom — it identifies a position in an
    ordering the reader has to guess, and a wrong guess silently means a
    different atom.

    ``transition_state_geometry_id`` closes that, the same way ADR 0011
    settled it for ``reaction_atom_map``: the map names the geometry it is
    written against rather than relying on one being derivable. It is the
    identical claim in the identical units — ``reaction_atom_map``'s
    ``ts_atom_index`` and this table's mapping values both index the saddle
    point — and ``validate_atom_map_agrees_with_irc_evidence`` already holds
    the two against each other, which is only meaningful once both say which
    geometry they counted in.

    The column is nullable because the mappings are: evidence may be a
    ``rationale`` and a ``passed`` flag with no per-atom partition at all, and
    such a row has no indices and so needs no geometry. What is refused is the
    combination that cannot be read —
    ``ck_transition_state_validation_evidence_mapping_names_geometry`` requires
    a geometry exactly when a mapping is present. No composite foreign key
    into ``geometry_atom`` is possible here, unlike
    ``reaction_atom_map_pair``, because the indices live inside JSONB rather
    than in a column; naming the geometry is the half that can be enforced
    declaratively, and the bounds and element checks run in
    ``validate_ts_evidence_participant_composition`` against this same
    geometry.
    """

    __tablename__ = "transition_state_validation_evidence"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    transition_state_entry_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("transition_state_entry.id", name="fk_ts_validation_evidence_ts_entry", deferrable=True, initially="IMMEDIATE"), nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    passed: Mapped[bool] = mapped_column(nullable=False)
    rationale: Mapped[str] = mapped_column(Text, nullable=False)
    reconstruction_calculation_id: Mapped[Optional[int]] = mapped_column(BigInteger, ForeignKey("calculation.id", name="fk_ts_validation_evidence_reconstruction_calc", deferrable=True, initially="IMMEDIATE"), nullable=True)
    #: ``imaginary_mode`` only: how many imaginary modes the frequency
    #: calculation found. NULL is "not stated", and is not zero.
    imaginary_frequency_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    #: ``imaginary_mode`` only: the reaction-coordinate mode's frequency in
    #: cm^-1, negative by the convention ``calc_freq_mode`` uses.
    imaginary_frequency_cm1: Mapped[Optional[float]] = mapped_column(nullable=True)
    #: ``imaginary_mode`` only: the producer's verdict on whether the mode's
    #: atom displacements agree with the bonds the reaction breaks and forms.
    #: NULL is "not assessed", and is not false.
    mode_displacement_agrees: Mapped[Optional[bool]] = mapped_column(nullable=True)
    # Canonical participant -> atom-index mappings. JSON keeps the evidence
    # machine-readable; a free-text mapping cannot be validated or replayed.
    reactant_participant_mapping: Mapped[Optional[dict[str, list[int]]]] = mapped_column(JSONB, nullable=True)
    product_participant_mapping: Mapped[Optional[dict[str, list[int]]]] = mapped_column(JSONB, nullable=True)
    #: The saddle-point geometry the two mappings' atom indices count into.
    #: Required whenever either mapping is present; see the class docstring.
    transition_state_geometry_id: Mapped[Optional[int]] = mapped_column(
        BigInteger,
        ForeignKey(
            "geometry.id",
            name="fk_ts_validation_evidence_ts_geometry",
            deferrable=True,
            initially="IMMEDIATE",
        ),
        nullable=True,
    )
    transition_state_entry: Mapped["TransitionStateEntry"] = relationship(back_populates="validation_evidence")
    reconstruction_calculation: Mapped["Calculation"] = relationship()
    transition_state_geometry: Mapped[Optional["Geometry"]] = relationship()
    compared_energies: Mapped[list["TransitionStateValidationEnergy"]] = relationship(
        back_populates="evidence", order_by="TransitionStateValidationEnergy.id"
    )
    __table_args__ = (
        CheckConstraint(
            "kind IN ('irc', 'energy_ordering', 'imaginary_mode')",
            name="ts_validation_kind",
        ),
        # The source calculation is the one column whose meaning differs by
        # kind: an irc or imaginary_mode record is about one job, an
        # energy_ordering record is about several and names them per energy.
        CheckConstraint(
            "(kind = 'energy_ordering') = (reconstruction_calculation_id IS NULL)",
            name="source_calc_shape",
        ),
        CheckConstraint(
            "kind = 'imaginary_mode' OR (imaginary_frequency_count IS NULL "
            "AND imaginary_frequency_cm1 IS NULL AND mode_displacement_agrees IS NULL)",
            name="mode_cols_imag_only",
        ),
        CheckConstraint(
            "imaginary_frequency_count IS NULL OR imaginary_frequency_count >= 0",
            name="imag_count_ge_0",
        ),
        CheckConstraint(
            "imaginary_frequency_cm1 IS NULL OR (imaginary_frequency_cm1 < 0 "
            "AND imaginary_frequency_cm1 > '-Infinity'::float8)",
            name="imag_freq_negative",
        ),
        CheckConstraint(
            "kind = 'irc' OR (coalesce(jsonb_typeof(reactant_participant_mapping), 'null') = 'null' "
            "AND coalesce(jsonb_typeof(product_participant_mapping), 'null') = 'null')",
            name="mapping_irc_only",
        ),
        UniqueConstraint(
            "transition_state_entry_id", "kind", name="uq_ts_validation_evidence_kind"
        ),
        # An atom index with no geometry named beside it does not identify an
        # atom. Enforced by the database rather than by the service because
        # three deposit paths write this table and a fourth would inherit the
        # rule for free.
        #
        # "Absent" has two spellings in this column and the constraint has to
        # admit both. SQLAlchemy's JSONB type persists a Python ``None`` as
        # JSON ``null`` rather than as SQL NULL, so a row written through
        # ``persist_transition_state_validation_evidence`` with no mapping
        # holds ``'null'::jsonb``, while one whose attribute was never set at
        # all -- the column is simply omitted from the INSERT -- holds SQL
        # NULL. Both mean "this record partitions no atoms", and a constraint
        # testing only ``IS NULL`` would refuse every mapping-free row the
        # service writes.
        CheckConstraint(
            "(coalesce(jsonb_typeof(reactant_participant_mapping), 'null') = 'null' "
            "AND coalesce(jsonb_typeof(product_participant_mapping), 'null') = 'null') "
            "OR transition_state_geometry_id IS NOT NULL",
            name="mapping_names_geometry",
        ),
    )


class TransitionStateValidationEnergy(Base):
    """One energy an ``energy_ordering`` evidence row compared.

    ``participant`` says whose energy it is: ``ts`` for the saddle point, or
    ``reactant:N`` / ``product:N`` for the N-th declared participant of that
    side, the spelling the IRC participant mappings use. A side of the reaction
    with several participants is compared by the sum of theirs, which is why
    each participant is stored with its own energy and its own calculation
    rather than as a pre-summed total that no single calculation could source.

    ``energy_kind`` is ``electronic`` (the electronic energy) or ``e0`` (that
    plus the zero-point energy). They are different quantities and a
    comparison is only ever made between like kinds, so the kind is a column of
    the row and is part of its identity.

    ``source_calculation_id`` is required, and is a provenance reference, not
    ownership: the row is owned by its evidence row, and through it by the
    transition-state entry. Whether the calculation belongs to the thing the
    energy is of (the saddle point, or that participant's species) is checked
    when the row is written, by the same ownership check every other source
    calculation link uses.
    """

    __tablename__ = "transition_state_validation_energy"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    evidence_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(
            "transition_state_validation_evidence.id",
            name="fk_ts_validation_energy_evidence",
            deferrable=True,
            initially="IMMEDIATE",
        ),
        nullable=False,
    )
    participant: Mapped[str] = mapped_column(Text, nullable=False)
    energy_kind: Mapped[str] = mapped_column(Text, nullable=False)
    energy_hartree: Mapped[float] = mapped_column(nullable=False)
    source_calculation_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(
            "calculation.id",
            name="fk_ts_validation_energy_source_calc",
            deferrable=True,
            initially="IMMEDIATE",
        ),
        nullable=False,
    )
    evidence: Mapped["TransitionStateValidationEvidence"] = relationship(back_populates="compared_energies")
    source_calculation: Mapped["Calculation"] = relationship()
    __table_args__ = (
        CheckConstraint(
            "participant ~ '^(ts|reactant:[1-9][0-9]*|product:[1-9][0-9]*)$'",
            name="participant_shape",
        ),
        CheckConstraint("energy_kind IN ('electronic', 'e0')", name="energy_kind"),
        # Finite and negative: PostgreSQL orders NaN above every number, so
        # ``< 0`` refuses NaN as well as positive values, and the second arm
        # refuses -Infinity. A stored NaN would make every read of the record
        # fail JSON serialisation, permanently once the entry is approved.
        CheckConstraint(
            "energy_hartree < 0 AND energy_hartree > '-Infinity'::float8",
            name="energy_finite_negative",
        ),
        UniqueConstraint(
            "evidence_id",
            "participant",
            "energy_kind",
            name="uq_ts_validation_energy_slot",
        ),
        Index("ix_ts_validation_energy_source_calc", "source_calculation_id"),
    )
