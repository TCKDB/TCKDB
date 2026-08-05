from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    ForeignKey,
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
    """Structured IRC validation result for one TS candidate.

    Normal-mode-displacement ("nmd") evidence is deliberately absent: reading
    an imaginary mode's displacement vectors is a producer-side heuristic, not
    a database record, and TCKDB stores only the reconstructed-path evidence
    an IRC calculation actually produces.
    """

    __tablename__ = "transition_state_validation_evidence"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    transition_state_entry_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("transition_state_entry.id", name="fk_ts_validation_evidence_ts_entry", deferrable=True, initially="IMMEDIATE"), nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    passed: Mapped[bool] = mapped_column(nullable=False)
    rationale: Mapped[str] = mapped_column(Text, nullable=False)
    reconstruction_calculation_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("calculation.id", name="fk_ts_validation_evidence_reconstruction_calc", deferrable=True, initially="IMMEDIATE"), nullable=False)
    # Canonical participant -> atom-index mappings. JSON keeps the evidence
    # machine-readable; a free-text mapping cannot be validated or replayed.
    reactant_participant_mapping: Mapped[Optional[dict[str, list[int]]]] = mapped_column(JSONB, nullable=True)
    product_participant_mapping: Mapped[Optional[dict[str, list[int]]]] = mapped_column(JSONB, nullable=True)
    transition_state_entry: Mapped["TransitionStateEntry"] = relationship(back_populates="validation_evidence")
    reconstruction_calculation: Mapped["Calculation"] = relationship()
    __table_args__ = (
        CheckConstraint("kind IN ('irc')", name="ts_validation_kind"),
        UniqueConstraint(
            "transition_state_entry_id", "kind", name="uq_ts_validation_evidence_kind"
        ),
    )
