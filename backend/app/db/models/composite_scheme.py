"""Composite levels of theory: recipe identity bound to a level (ADR 0021).

A composite energy (CBS-QB3, a CCSD(T)/CBS extrapolation, a focal-point sum)
is a *recipe*, and a recipe is what these tables hold. The level of theory
stays the flat, hashed row it always was; a side table binds it to its recipe,
the way ``level_of_theory_merge`` annotates a level without widening it.

* :class:`CompositeScheme` -- the recipe. **Identity**: deduplicated by
  ``definition_hash`` and never updated in place.
* :class:`CompositeSchemeTerm` and :class:`CompositeSchemeTermInput` -- the
  recipe's arithmetic, one row per term and per input of a term. Identity too:
  they belong to one scheme and are written with it.
* :class:`LevelOfTheoryComposite` -- binds a level of theory to the scheme
  whose energy it names. A side table rather than columns on
  ``level_of_theory``: whole-row snapshots of a level (consistency-check
  inputs, reproducibility context hashes) would otherwise change for every
  row the day a column appeared, and the level's ``lot_hash`` must not move.

No scheme row is created for a named method until a level of theory spelled
with it is resolved; see ``app/services/composite_scheme_resolution.py``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from sqlalchemy import (
    CHAR,
    BigInteger,
    CheckConstraint,
    Double,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    Text,
    UniqueConstraint,
)
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, PublicRefMixin, TimestampMixin
from app.db.models.common import (
    CompositeBindingSource,
    CompositeExtrapolationFormula,
    CompositeInputSlot,
    CompositeSchemeKind,
    CompositeTermOperation,
    EnergyComponentKind,
)

if TYPE_CHECKING:
    from app.db.models.level_of_theory import LevelOfTheory
    from app.db.models.literature import Literature


class CompositeScheme(Base, TimestampMixin, PublicRefMixin):
    """One composite recipe (identity; ref prefix ``csch_``).

    ``definition_hash`` is the identity: for a named method it is
    ``sha256`` of the canonical JSON ``{"kind":"named_method","method":<key>}``.
    The remaining columns describe the recipe and are written once, with the
    row. ``geometry_level_of_theory_id`` and ``frequency_level_of_theory_id``
    are the levels the *recipe* runs internally; ``NULL`` means the source
    does not state it, never "the same as the energy level".
    ``recipe_zpe_scale_factor`` is ``NULL`` unless a source is cited, and a
    reader must not substitute 1.0.
    """

    __tablename__ = "composite_scheme"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)

    kind: Mapped[CompositeSchemeKind] = mapped_column(
        SAEnum(CompositeSchemeKind, name="composite_scheme_kind"), nullable=False
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    definition_hash: Mapped[str] = mapped_column(CHAR(64), nullable=False)

    geometry_level_of_theory_id: Mapped[Optional[int]] = mapped_column(
        BigInteger,
        ForeignKey(
            "level_of_theory.id",
            deferrable=True,
            initially="IMMEDIATE",
            name="fk_composite_scheme_geometry_level_of_theory_id",
        ),
        nullable=True,
    )
    frequency_level_of_theory_id: Mapped[Optional[int]] = mapped_column(
        BigInteger,
        ForeignKey(
            "level_of_theory.id",
            deferrable=True,
            initially="IMMEDIATE",
            name="fk_composite_scheme_frequency_level_of_theory_id",
        ),
        nullable=True,
    )
    recipe_zpe_scale_factor: Mapped[Optional[float]] = mapped_column(Double, nullable=True)
    source_literature_id: Mapped[Optional[int]] = mapped_column(
        BigInteger,
        ForeignKey("literature.id", deferrable=True, initially="IMMEDIATE"),
        nullable=True,
    )
    note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    geometry_level_of_theory: Mapped[Optional["LevelOfTheory"]] = relationship(
        foreign_keys=[geometry_level_of_theory_id]
    )
    frequency_level_of_theory: Mapped[Optional["LevelOfTheory"]] = relationship(
        foreign_keys=[frequency_level_of_theory_id]
    )
    source_literature: Mapped[Optional["Literature"]] = relationship()
    terms: Mapped[list["CompositeSchemeTerm"]] = relationship(
        back_populates="scheme", order_by="CompositeSchemeTerm.position"
    )

    __table_args__ = (
        UniqueConstraint("definition_hash"),
        CheckConstraint(
            "recipe_zpe_scale_factor IS NULL OR recipe_zpe_scale_factor > 0",
            name="recipe_zpe_scale_factor_positive",
        ),
    )


class CompositeSchemeTerm(Base):
    """One term of a recipe, in order.

    ``formula`` and ``exponent`` are identity: TZ/QZ with exponent 3 and with
    exponent 3.4 are two schemes. ``formula`` is set only on an extrapolation
    term, and ``exponent`` only alongside a formula.
    """

    __tablename__ = "composite_scheme_term"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    scheme_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("composite_scheme.id", deferrable=True, initially="IMMEDIATE"),
        nullable=False,
    )
    position: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    operation: Mapped[CompositeTermOperation] = mapped_column(
        SAEnum(CompositeTermOperation, name="composite_term_operation"), nullable=False
    )
    energy_component: Mapped[EnergyComponentKind] = mapped_column(
        SAEnum(EnergyComponentKind, name="energy_component_kind"), nullable=False
    )
    formula: Mapped[Optional[CompositeExtrapolationFormula]] = mapped_column(
        SAEnum(CompositeExtrapolationFormula, name="composite_extrapolation_formula"),
        nullable=True,
    )
    exponent: Mapped[Optional[float]] = mapped_column(Double, nullable=True)

    scheme: Mapped["CompositeScheme"] = relationship(back_populates="terms")
    inputs: Mapped[list["CompositeSchemeTermInput"]] = relationship(
        back_populates="term", order_by="CompositeSchemeTermInput.id"
    )

    __table_args__ = (
        UniqueConstraint("scheme_id", "position", name="uq_composite_scheme_term_scheme_id_position"),
        CheckConstraint("position >= 0", name="position_non_negative"),
        CheckConstraint(
            "formula IS NULL OR operation = 'extrapolation'",
            name="formula_only_on_extrapolation",
        ),
        CheckConstraint("exponent IS NULL OR formula IS NOT NULL", name="exponent_needs_formula"),
    )


class CompositeSchemeTermInput(Base):
    """One level-of-theory input of a term.

    ``level_of_theory_id`` is never a composite-bound level: a composite is
    built from ordinary levels, and a nested composite is refused. The check
    is :func:`app.services.composite_scheme_resolution.assert_ordinary_input_level`,
    which every writer of this table must go through (see its docstring for
    what it looks at). ``cardinal_number`` is declared by the depositor, not
    derived from the basis name; it is required on a ``cardinal`` slot and
    optional on the others.
    """

    __tablename__ = "composite_scheme_term_input"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    term_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("composite_scheme_term.id", deferrable=True, initially="IMMEDIATE"),
        nullable=False,
    )
    slot: Mapped[CompositeInputSlot] = mapped_column(
        SAEnum(CompositeInputSlot, name="composite_input_slot"), nullable=False
    )
    level_of_theory_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(
            "level_of_theory.id",
            deferrable=True,
            initially="IMMEDIATE",
            name="fk_composite_scheme_term_input_level_of_theory_id",
        ),
        nullable=False,
    )
    cardinal_number: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    term: Mapped["CompositeSchemeTerm"] = relationship(back_populates="inputs")
    level_of_theory: Mapped["LevelOfTheory"] = relationship()

    __table_args__ = (
        Index(
            "uq_composite_scheme_term_input_term_slot_cardinal",
            "term_id",
            "slot",
            "cardinal_number",
            unique=True,
            postgresql_nulls_not_distinct=True,
        ),
        CheckConstraint(
            "cardinal_number IS NULL OR cardinal_number >= 1",
            name="cardinal_number_positive",
        ),
        CheckConstraint(
            "slot <> 'cardinal' OR cardinal_number IS NOT NULL",
            name="cardinal_slot_needs_number",
        ),
        Index("ix_composite_scheme_term_input_level_of_theory_id", "level_of_theory_id"),
    )


class LevelOfTheoryComposite(Base, TimestampMixin):
    """Binds a level of theory to the composite scheme its energy names.

    Side table, one row per bound level (``level_of_theory_id`` is both the
    primary key and the foreign key), like ``level_of_theory_merge``. The
    level's ``lot_hash`` is untouched by a binding. A merged level is never
    bound: resolution follows ``level_of_theory_merge`` first and binds the
    row it lands on.
    """

    __tablename__ = "level_of_theory_composite"

    level_of_theory_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("level_of_theory.id", deferrable=True, initially="IMMEDIATE"),
        primary_key=True,
    )
    scheme_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("composite_scheme.id", deferrable=True, initially="IMMEDIATE"),
        nullable=False,
        index=True,
    )
    binding_source: Mapped[CompositeBindingSource] = mapped_column(
        SAEnum(CompositeBindingSource, name="composite_binding_source"), nullable=False
    )

    level_of_theory: Mapped["LevelOfTheory"] = relationship()
    scheme: Mapped["CompositeScheme"] = relationship()
