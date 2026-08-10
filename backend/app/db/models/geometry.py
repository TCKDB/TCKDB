from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from sqlalchemy import (
    CHAR,
    BigInteger,
    CheckConstraint,
    ForeignKey,
    Integer,
    SmallInteger,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, PublicRefMixin, TimestampMixin

if TYPE_CHECKING:
    from app.db.models.calculation import (
        CalculationInputGeometry,
        CalculationOutputGeometry,
    )


class Geometry(Base, TimestampMixin, PublicRefMixin):
    """Stores a reusable molecular geometry and its serialized XYZ form."""

    __tablename__ = "geometry"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    natoms: Mapped[int] = mapped_column(Integer, nullable=False)
    geom_hash: Mapped[str] = mapped_column(CHAR(64), nullable=False, unique=True)

    #: The deposited XYZ block, coordinates reformatted to a fixed precision
    #: and **element symbols left exactly as the file wrote them**. This is the
    #: text ``geom_hash`` is taken over, and ``geom_hash`` is both the dedupe
    #: key and a citable public ref (``geometry:geom_hash=...``), so
    #: canonicalising a symbol here would re-key every geometry already stored.
    #: ``geometry_atom.element`` is canonical instead; see
    #: :func:`app.chemistry.geometry.parse_xyz` for the full argument.
    xyz_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    atoms: Mapped[list["GeometryAtom"]] = relationship(
        back_populates="geometry",
        cascade="all, delete-orphan",
    )

    calculation_outputs: Mapped[list["CalculationOutputGeometry"]] = relationship(
        back_populates="geometry",
    )
    calculation_inputs: Mapped[list["CalculationInputGeometry"]] = relationship(
        back_populates="geometry",
    )

    __table_args__ = (CheckConstraint("natoms >= 1", name="natoms_ge_1"),)


class GeometryAtom(Base):
    """Stores per-atom coordinates for a geometry row."""

    __tablename__ = "geometry_atom"

    geometry_id: Mapped[int] = mapped_column(
        ForeignKey("geometry.id"),
        primary_key=True,
    )

    atom_index: Mapped[int] = mapped_column(Integer, primary_key=True)

    #: Element symbol, **case-canonical by convention**: first character upper,
    #: rest lower (:func:`app.chemistry.geometry.normalize_element_symbol`,
    #: applied by :func:`app.chemistry.geometry.parse_xyz` and backfilled onto
    #: older rows by Alembic revision ``b4e7c1d20f83``). A file that writes
    #: ``CL`` stores ``Cl`` here while ``geometry.xyz_text`` keeps the ``CL`` it
    #: deposited; this column is the index the database joins and compares on.
    #:
    #: "By convention" is load-bearing: there is deliberately **no** CHECK
    #: constraint enforcing it, so a restore from a backup older than
    #: ``b4e7c1d20f83`` or a write path that bypasses ``parse_xyz`` can hold a
    #: non-canonical symbol. Readers that compare this column must normalise;
    #: none of them may assume it.
    #:
    #: Case is the *only* thing canonicalised. ``D`` and ``T`` are stored as
    #: ``D`` and ``T``: they are the depositor's isotope labelling, and code
    #: that counts elements resolves them with
    #: :func:`app.chemistry.geometry.resolve_element_symbol` at the point of
    #: counting.
    element: Mapped[str] = mapped_column(CHAR(2), nullable=False)
    x: Mapped[float] = mapped_column(nullable=False)
    y: Mapped[float] = mapped_column(nullable=False)
    z: Mapped[float] = mapped_column(nullable=False)

    #: Isotope mass number for this nucleus. ``NULL`` means the atom is at
    #: the element's most abundant natural isotope — it is *not* "unknown".
    #: Every geometry deposited before atom-resolved isotope support is by
    #: definition an ordinary isotopologue, so no backfill is required.
    #: This column is what makes isotope-specific frequencies, rotational
    #: constants, ZPE and Hessian reuse reconstructible: it is the per-atom
    #: mass that a downstream normal-mode analysis needs.
    isotope_mass_number: Mapped[Optional[int]] = mapped_column(
        SmallInteger,
        nullable=True,
    )

    geometry: Mapped[Geometry] = relationship(back_populates="atoms")

    __table_args__ = (
        CheckConstraint("atom_index >= 1", name="atom_index_ge_1"),
        CheckConstraint(
            "isotope_mass_number IS NULL OR isotope_mass_number >= 1",
            name="isotope_mass_number_ge_1",
        ),
        # Redundant with the primary key on its own terms — ``(geometry_id,
        # atom_index)`` already determines the row, so this adds no
        # restriction. It exists to be the target of
        # ``reaction_atom_map_pair``'s two foreign keys, which carry
        # ``element`` on both ends so that a mapped pair whose atoms are
        # different elements cannot be written. See ADR 0011 and
        # :mod:`app.db.models.reaction_atom_map`.
        UniqueConstraint(
            "geometry_id",
            "atom_index",
            "element",
            name="uq_geometry_atom_geometry_id_element",
        ),
    )
