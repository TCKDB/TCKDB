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
    """Structured IRC validation result for one TS candidate.

    Normal-mode-displacement ("nmd") evidence is deliberately absent: reading
    an imaginary mode's displacement vectors is a producer-side heuristic, not
    a database record, and TCKDB stores only the reconstructed-path evidence
    an IRC calculation actually produces.

    That decision is unchanged, and it is narrower than it has been read to
    be. ADR 0013 took it to mean the ADR 0012 eigenvector projections were
    uncomputable; they are not, because ``calc_hessian`` stores the matrix
    those vectors diagonalise. The projections now run at *read* time
    (``include=imaginary_mode_projections``) and write nothing — which is
    exactly what this docstring forbids storing. What stays out of the
    database is a producer's *conclusion* about a mode, not the arithmetic
    anyone can redo from the matrix.

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
    reconstruction_calculation_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("calculation.id", name="fk_ts_validation_evidence_reconstruction_calc", deferrable=True, initially="IMMEDIATE"), nullable=False)
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
    __table_args__ = (
        CheckConstraint("kind IN ('irc')", name="ts_validation_kind"),
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
