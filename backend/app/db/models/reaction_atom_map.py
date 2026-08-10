"""Atom correspondence across one micro reaction (ADR 0011).

A reaction records which species react, which transition state sits between
them, and what rate results. It does not record *which atom is which*. These
two tables do: they state that the hydrogen leaving carbon 3 of the reactant is
the hydrogen half-transferred in the saddle point and the hydrogen bonded to
oxygen 1 of the product.

Not to be confused with ``calc_geometry_validation.atom_mapping``
--------------------------------------------------------------
That column maps an *input* geometry to an *output* geometry for the **same
species**, beside ``is_isomorphic``, ``rmsd`` and ``n_mappings``. It answers
"did the optimiser hand back the molecule I gave it" — conformer alignment. It
says nothing across a reaction. These tables are deliberately named
``reaction_atom_map*`` so the two cannot be read as one another, and ADR 0011
leaves renaming the older column undecided.

Not to be confused with ``transition_state_validation_evidence``
---------------------------------------------------------------
``reactant_participant_mapping`` / ``product_participant_mapping`` there
*partition* the TS atoms among participant molecules — "TS atoms 1,2,3 belong
to reactant 1". They do not say which reactant atom each TS atom *is*. The
atom map is the refinement of that partition into a bijection, and it is scoped
to the micro reaction rather than to one IRC evidence record.

Shape
-----
The map belongs to the **micro reaction** (``reaction_entry``) — the object
that already ties reactants, transition state and products together. Not to a
species, which is deduped and shared and maps differently in every reaction it
appears in; not to a geometry, which knows nothing of the reaction.

Two legs are stored, **both toward the transition state**: reactants→TS and
products→TS. The saddle point is the physical pivot, both legs are what the IRC
actually traverses, and composing them gives reactants→products for free.
Storing reactants→products directly instead would discard the very thing that
makes the map worth having.

Indices are ``geometry_atom.atom_index`` values, and every geometry they count
into is named explicitly on the row. "Reactant atom 3" is meaningless until it
is said *which geometry* is being counted, because an atom index is a property
of a geometry, not of a species.

What the database refuses on its own
------------------------------------
Three of ADR 0011's blocking rules are structural here rather than only
validated at the API boundary, following the reasoning of ADR 0010 and
``f9b2e6c4a1d7``: a second write path must not be able to assert what the
first one refuses.

* **An index that is not a real atom of the named geometry** —
  ``fk_reaction_atom_map_pair_geometry_id_geometry_atom`` and its TS-side twin
  point at ``geometry_atom``'s ``(geometry_id, atom_index)`` key.
* **An element changing across the map** — each of those foreign keys carries
  an ``element`` column, and ``ck_reaction_atom_map_pair_element_matches``
  holds the two equal *case-insensitively*, so a pair can only exist if the
  source atom and the TS atom are the same element. The element columns are
  therefore deliberately redundant with ``geometry_atom``: the redundancy is
  what makes a definitional rule enforceable declaratively instead of in a
  trigger.

  There are **two** of them, ``element`` and ``ts_element``, rather than one
  column shared by both foreign keys. The reason was that
  ``geometry_atom.element`` used to be stored verbatim as the depositor's XYZ
  wrote it: ``CL`` and ``Cl`` are the same element and different
  ``character(2)`` values, so when a reactant geometry came out of one program
  and the saddle point out of another, *no single stored value satisfied both
  foreign keys* and a perfectly correct map could not be written at all. One
  column made capitalisation load-bearing — the same defect, one table over,
  that made a ``CL`` saddle point contradict its own reaction. Each end stores
  what its own geometry stores, and the ``upper(...)`` check carries the
  scientific rule the shared column used to carry.

  Alembic revision ``b4e7c1d20f83`` made that rare rather than impossible:
  ``geometry_atom.element`` is canonicalised at ingestion and older rows were
  backfilled, so two geometries deposited through the API now spell an element
  the same way. It is a convention and not a constraint — no CHECK enforces it,
  and a restore or a bulk import can reintroduce ``CL`` — so the two columns and
  the case-insensitive ``upper(...)`` check both remain load-bearing rather than
  vestigial. Collapsing them would require canonicality to be structural first,
  which is its own task.

  ``isotope_mass_number`` is *not* carried the same way, because it is nullable
  and a NULL column silently disables a MATCH SIMPLE foreign key — isotope
  consistency is validated in the service layer instead, where NULL means
  "most abundant natural isotope" rather than "unchecked".
* **An atom claimed twice** — one unique constraint per direction, per leg.

Completeness ("a reactant atom unaccounted for in the products") is a
cross-row aggregate over both legs and cannot be a constraint; it lives in
:mod:`app.services.reaction_atom_map`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from sqlalchemy import (
    CHAR,
    BigInteger,
    CheckConstraint,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    Text,
    UniqueConstraint,
)
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, CreatedByMixin, TimestampMixin
from app.db.models.common import AtomMapSource, ReactionRole

if TYPE_CHECKING:
    from app.db.models.geometry import Geometry
    from app.db.models.reaction import ReactionEntry, ReactionEntryStructureParticipant
    from app.db.models.transition_state import TransitionStateEntry


class ReactionAtomMap(Base, TimestampMixin, CreatedByMixin):
    """One depositor-supplied atom correspondence for one micro reaction.

    A reaction entry may carry several transition-state candidates; each has
    its own saddle-point geometry and therefore its own map, so the map is
    keyed by ``(reaction_entry_id, transition_state_entry_id)``.

    A reaction with no transition state has no map, and that is not a gap: the
    ADR stores both legs *toward the saddle point*, so a barrierless channel
    has nothing for the legs to point at.
    """

    __tablename__ = "reaction_atom_map"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)

    #: The micro reaction this map belongs to. Derivable through
    #: ``transition_state_entry → transition_state → reaction_entry``, and
    #: stored anyway: ADR 0011 puts the map on the micro reaction, and a read
    #: that filters by reaction entry should not have to go through the TS to
    #: find out whether one exists.
    reaction_entry_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(
            "reaction_entry.id",
            name="fk_reaction_atom_map_reaction_entry",
            deferrable=True,
            initially="IMMEDIATE",
        ),
        nullable=False,
    )

    transition_state_entry_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(
            "transition_state_entry.id",
            name="fk_reaction_atom_map_ts_entry",
            deferrable=True,
            initially="IMMEDIATE",
        ),
        nullable=False,
    )

    #: The saddle-point geometry both legs index into, named explicitly. Every
    #: pair row repeats it so its ``(geometry_id, atom_index)`` foreign key can
    #: reach ``geometry_atom``; the composite foreign key from the pair back to
    #: ``(id, transition_state_geometry_id)`` keeps the repetition honest.
    transition_state_geometry_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(
            "geometry.id",
            name="fk_reaction_atom_map_ts_geometry",
            deferrable=True,
            initially="IMMEDIATE",
        ),
        nullable=False,
    )

    #: How the correspondence was obtained. No default — see
    #: :class:`~app.db.models.common.AtomMapSource`.
    source: Mapped[AtomMapSource] = mapped_column(
        SAEnum(AtomMapSource, name="atom_map_source"),
        nullable=False,
    )

    #: How many maps are equivalent to this one under the symmetry of the
    #: participating structures, where the depositor knows. ``NULL`` means no
    #: claim is made — it does **not** mean 1. Symmetry makes a valid map
    #: routinely non-unique; ADR 0011 records the map that was used and does
    #: not attempt to canonicalise among the alternatives.
    equivalent_map_count: Mapped[Optional[int]] = mapped_column(
        Integer, nullable=True
    )

    #: Free text. Mandatory on an ``inferred`` map, where it must name the
    #: algorithm that produced it — an inferred map whose origin is anonymous
    #: is indistinguishable from a declared one the moment the token is
    #: dropped by a careless consumer.
    note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    reaction_entry: Mapped["ReactionEntry"] = relationship(
        back_populates="atom_maps",
    )
    transition_state_entry: Mapped["TransitionStateEntry"] = relationship(
        back_populates="atom_maps",
    )
    transition_state_geometry: Mapped["Geometry"] = relationship()
    pairs: Mapped[list["ReactionAtomMapPair"]] = relationship(
        back_populates="atom_map",
        cascade="all, delete-orphan",
        order_by=(
            "ReactionAtomMapPair.side, "
            "ReactionAtomMapPair.structure_participant_id, "
            "ReactionAtomMapPair.atom_index"
        ),
    )

    __table_args__ = (
        UniqueConstraint(
            "reaction_entry_id",
            "transition_state_entry_id",
            name="uq_reaction_atom_map_reaction_entry_id",
        ),
        # Target of the pair rows' composite foreign key. ``id`` alone is
        # already unique; this exists so a pair cannot name a TS geometry its
        # own map does not.
        UniqueConstraint(
            "id",
            "transition_state_geometry_id",
            name="uq_reaction_atom_map_id",
        ),
        CheckConstraint(
            "equivalent_map_count IS NULL OR equivalent_map_count >= 1",
            name="equivalent_map_count_ge_1",
        ),
        CheckConstraint(
            # ``note IS NOT NULL AND`` is load-bearing, not belt-and-braces. A
            # CHECK rejects only on FALSE, and ``btrim(NULL) <> ''`` is NULL,
            # so the trimmed test *alone* would admit the anonymous inferred
            # map this constraint exists to refuse.
            "source <> 'inferred' OR (note IS NOT NULL AND btrim(note) <> '')",
            name="inferred_requires_note",
        ),
    )


class ReactionAtomMapPair(Base):
    """One atom of one participant identified with one transition-state atom.

    ``(geometry_id, atom_index)`` names an atom of a reactant or product
    geometry; ``(transition_state_geometry_id, ts_atom_index)`` names the atom
    of the saddle point it becomes. ``element`` and ``ts_element`` are the
    element each end is, spelled as its own geometry spells it — see the module
    docstring for why they are stored rather than looked up, and why there are
    two of them.
    """

    __tablename__ = "reaction_atom_map_pair"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)

    atom_map_id: Mapped[int] = mapped_column(BigInteger, nullable=False)

    #: Which leg this pair belongs to. Redundant with the participant's own
    #: ``role`` and kept in step with it by a composite foreign key, because
    #: "a transition-state atom is claimed at most once *per leg*" has to be a
    #: unique constraint and SQL cannot dereference the participant to get
    #: there.
    side: Mapped[ReactionRole] = mapped_column(
        SAEnum(ReactionRole, name="reaction_role"),
        nullable=False,
    )

    #: Which participant *molecule* the atom belongs to. A participant, not a
    #: species entry: the same species may appear twice on one side (2 CH3 →
    #: C2H6) and the two copies map to different transition-state atoms.
    structure_participant_id: Mapped[int] = mapped_column(
        BigInteger, nullable=False
    )

    geometry_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    atom_index: Mapped[int] = mapped_column(Integer, nullable=False)

    transition_state_geometry_id: Mapped[int] = mapped_column(
        BigInteger, nullable=False
    )
    ts_atom_index: Mapped[int] = mapped_column(Integer, nullable=False)

    #: The participant atom's element, spelled as *its* geometry spells it.
    #: Read by the foreign key into ``geometry_atom``, so it cannot disagree
    #: with the stored atom.
    element: Mapped[str] = mapped_column(CHAR(2), nullable=False)

    #: The saddle-point atom's element, spelled as the *transition-state*
    #: geometry spells it. Held equal to ``element`` case-insensitively by
    #: ``ck_reaction_atom_map_pair_element_matches``, which is the rule "an
    #: element does not change across a reaction". It has its own column
    #: because the two geometries may capitalise the same element differently
    #: and a shared column would refuse that correct map outright. Ingestion
    #: canonicalisation (``b4e7c1d20f83``) makes that rare, not impossible —
    #: nothing enforces it — so the split stays; see the module docstring.
    ts_element: Mapped[str] = mapped_column(CHAR(2), nullable=False)

    atom_map: Mapped["ReactionAtomMap"] = relationship(back_populates="pairs")
    structure_participant: Mapped["ReactionEntryStructureParticipant"] = relationship()

    __table_args__ = (
        ForeignKeyConstraint(
            ["atom_map_id", "transition_state_geometry_id"],
            ["reaction_atom_map.id", "reaction_atom_map.transition_state_geometry_id"],
            name="fk_reaction_atom_map_pair_atom_map_id_reaction_atom_map",
            deferrable=True,
            initially="IMMEDIATE",
        ),
        ForeignKeyConstraint(
            ["structure_participant_id", "side"],
            [
                "reaction_entry_structure_participant.id",
                "reaction_entry_structure_participant.role",
            ],
            # Kept short by hand: the convention's expansion
            # ``fk_<table>_<column_0>_<referred_table>`` is 89 characters here
            # and PostgreSQL truncates identifiers at 63, which would show up
            # as permanent ``alembic check`` drift.
            name="fk_reaction_atom_map_pair_structure_participant",
            deferrable=True,
            initially="IMMEDIATE",
        ),
        ForeignKeyConstraint(
            ["geometry_id", "atom_index", "element"],
            [
                "geometry_atom.geometry_id",
                "geometry_atom.atom_index",
                "geometry_atom.element",
            ],
            name="fk_reaction_atom_map_pair_geometry_id_geometry_atom",
            deferrable=True,
            initially="IMMEDIATE",
        ),
        ForeignKeyConstraint(
            ["transition_state_geometry_id", "ts_atom_index", "ts_element"],
            [
                "geometry_atom.geometry_id",
                "geometry_atom.atom_index",
                "geometry_atom.element",
            ],
            name="fk_reaction_atom_map_pair_ts_geometry_id_geometry_atom",
            deferrable=True,
            initially="IMMEDIATE",
        ),
        # Injectivity, both directions, per leg.
        UniqueConstraint(
            "atom_map_id",
            "structure_participant_id",
            "atom_index",
            name="uq_reaction_atom_map_pair_atom_map_id",
        ),
        UniqueConstraint(
            "atom_map_id",
            "side",
            "ts_atom_index",
            name="uq_reaction_atom_map_pair_ts_atom_index",
        ),
        CheckConstraint("atom_index >= 1", name="atom_index_ge_1"),
        CheckConstraint("ts_atom_index >= 1", name="ts_atom_index_ge_1"),
        # "An element does not change across a reaction." Case-insensitive
        # because the two ends quote two geometries, and nothing guarantees the
        # two spell an element the same way: carbon becoming nitrogen is a
        # record that cannot be what it says it is, while ``Cl`` becoming
        # ``CL`` is one program shouting where another did not. Ingestion
        # canonicalisation (``b4e7c1d20f83``) makes disagreement rare on rows
        # written through the API, and does not make it impossible -- no
        # constraint holds the column canonical -- so this stays case-blind.
        CheckConstraint(
            "upper(element) = upper(ts_element)",
            name="element_matches",
        ),
    )
