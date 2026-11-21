"""Method and basis-set catalogue: what a stored spelling names, and the articles that define it.

Curated reference data shipped with TCKDB, never deposited. A level of theory stores a producer's spelling of its
method, dispersion and basis sets; an entry here says what the spelling's identity key names (short name, full name,
one definitional sentence) and which ``literature`` rows define it. The entries are written only by
``scripts/ops/load_method_catalogue.py`` from the seed files in ``app/data/catalogue/``.

* :class:`MethodCatalogue` -- one method or dispersion correction (ref prefix ``mcat_``), keyed by
  ``method_identity_key`` (``dispersion_identity_key`` for a dispersion correction).
* :class:`BasisSetCatalogue` -- one basis set (ref prefix ``bcat_``), keyed by ``basis_identity_key``.
* ``*_catalogue_key`` -- every identity key that names the entry, its own ``key`` included. Several keys may name
  one entry; a key names at most one. ``level_of_theory.lot_hash`` is untouched.
* ``*_catalogue_literature`` -- the entry's articles in citation order, each with its role.

A method's family and DFT rung are not stored: they stay derived from ``app.chemistry.method_families``.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    SmallInteger,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, PublicRefMixin, TimestampMixin
from app.db.models.common import BasisSetRole, CatalogueMethodKind, CatalogueReferenceRole

if TYPE_CHECKING:
    from app.db.models.literature import Literature


class MethodCatalogue(Base, TimestampMixin, PublicRefMixin):
    """Curation: one method or dispersion correction TCKDB describes (ref prefix ``mcat_``, from ``key``)."""

    __tablename__ = "method_catalogue"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    key: Mapped[str] = mapped_column(Text, nullable=False)
    kind: Mapped[CatalogueMethodKind] = mapped_column(
        SAEnum(CatalogueMethodKind, name="catalogue_method_kind"), nullable=False
    )
    short_name: Mapped[str] = mapped_column(Text, nullable=False)
    full_name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    seed_version: Mapped[int] = mapped_column(Integer, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=False), server_default=func.now(), nullable=False)

    keys: Mapped[list["MethodCatalogueKey"]] = relationship(
        back_populates="entry", cascade="all, delete-orphan", order_by="MethodCatalogueKey.key"
    )
    references: Mapped[list["MethodCatalogueLiterature"]] = relationship(
        back_populates="entry", cascade="all, delete-orphan", order_by="MethodCatalogueLiterature.position"
    )

    __table_args__ = (
        UniqueConstraint("key"),
        CheckConstraint("key = lower(btrim(key)) AND key <> ''", name="key_is_normalised"),
        CheckConstraint("seed_version >= 1", name="seed_version_positive"),
    )


class MethodCatalogueKey(Base):
    """Curation: a ``method_identity_key`` or ``dispersion_identity_key`` value naming a method catalogue entry."""

    __tablename__ = "method_catalogue_key"

    key: Mapped[str] = mapped_column(Text, primary_key=True)
    method_catalogue_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(
            "method_catalogue.id",
            ondelete="CASCADE",
            deferrable=True,
            initially="IMMEDIATE",
            name="fk_method_catalogue_key_entry",
        ),
        nullable=False,
        index=True,
    )

    entry: Mapped["MethodCatalogue"] = relationship(back_populates="keys")

    __table_args__ = (CheckConstraint("key = lower(btrim(key)) AND key <> ''", name="key_is_normalised"),)


class MethodCatalogueLiterature(Base):
    """Curation: one article a method catalogue entry cites, with its role and citation position."""

    __tablename__ = "method_catalogue_literature"

    method_catalogue_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(
            "method_catalogue.id",
            ondelete="CASCADE",
            deferrable=True,
            initially="IMMEDIATE",
            name="fk_method_catalogue_literature_entry",
        ),
        primary_key=True,
    )
    literature_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("literature.id", deferrable=True, initially="IMMEDIATE"),
        primary_key=True,
        index=True,
    )
    role: Mapped[CatalogueReferenceRole] = mapped_column(
        SAEnum(CatalogueReferenceRole, name="catalogue_reference_role"), nullable=False
    )
    position: Mapped[int] = mapped_column(SmallInteger, nullable=False)

    entry: Mapped["MethodCatalogue"] = relationship(back_populates="references")
    literature: Mapped["Literature"] = relationship()

    __table_args__ = (
        UniqueConstraint(
            "method_catalogue_id", "position", name="uq_method_catalogue_literature_position"
        ),
        CheckConstraint("position >= 1", name="position_positive"),
    )


class BasisSetCatalogue(Base, TimestampMixin, PublicRefMixin):
    """Curation: one basis set TCKDB describes (ref prefix ``bcat_``, content-derived from ``key``).

    ``family`` is open-ended text (``Karlsruhe def2``, ``Dunning correlation-consistent``, ...); the loader accepts
    only the families listed in ``app.services.method_catalogue_seed.BASIS_SET_FAMILIES``.
    """

    __tablename__ = "basis_set_catalogue"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    key: Mapped[str] = mapped_column(Text, nullable=False)
    role: Mapped[BasisSetRole] = mapped_column(SAEnum(BasisSetRole, name="basis_set_role"), nullable=False)
    family: Mapped[str] = mapped_column(Text, nullable=False)
    short_name: Mapped[str] = mapped_column(Text, nullable=False)
    full_name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    seed_version: Mapped[int] = mapped_column(Integer, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=False), server_default=func.now(), nullable=False)

    keys: Mapped[list["BasisSetCatalogueKey"]] = relationship(
        back_populates="entry", cascade="all, delete-orphan", order_by="BasisSetCatalogueKey.key"
    )
    references: Mapped[list["BasisSetCatalogueLiterature"]] = relationship(
        back_populates="entry", cascade="all, delete-orphan", order_by="BasisSetCatalogueLiterature.position"
    )

    __table_args__ = (
        UniqueConstraint("key"),
        CheckConstraint("key = lower(btrim(key)) AND key <> ''", name="key_is_normalised"),
        CheckConstraint("seed_version >= 1", name="seed_version_positive"),
    )


class BasisSetCatalogueKey(Base):
    """Curation: a ``basis_identity_key`` value that names a basis-set catalogue entry."""

    __tablename__ = "basis_set_catalogue_key"

    key: Mapped[str] = mapped_column(Text, primary_key=True)
    basis_set_catalogue_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(
            "basis_set_catalogue.id",
            ondelete="CASCADE",
            deferrable=True,
            initially="IMMEDIATE",
            name="fk_basis_set_catalogue_key_entry",
        ),
        nullable=False,
        index=True,
    )

    entry: Mapped["BasisSetCatalogue"] = relationship(back_populates="keys")

    __table_args__ = (CheckConstraint("key = lower(btrim(key)) AND key <> ''", name="key_is_normalised"),)


class BasisSetCatalogueLiterature(Base):
    """Curation: one article a basis-set catalogue entry cites, with its role and citation position."""

    __tablename__ = "basis_set_catalogue_literature"

    basis_set_catalogue_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(
            "basis_set_catalogue.id",
            ondelete="CASCADE",
            deferrable=True,
            initially="IMMEDIATE",
            name="fk_basis_set_catalogue_literature_entry",
        ),
        primary_key=True,
    )
    literature_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("literature.id", deferrable=True, initially="IMMEDIATE"),
        primary_key=True,
        index=True,
    )
    role: Mapped[CatalogueReferenceRole] = mapped_column(
        SAEnum(CatalogueReferenceRole, name="catalogue_reference_role"), nullable=False
    )
    position: Mapped[int] = mapped_column(SmallInteger, nullable=False)

    entry: Mapped["BasisSetCatalogue"] = relationship(back_populates="references")
    literature: Mapped["Literature"] = relationship()

    __table_args__ = (
        UniqueConstraint(
            "basis_set_catalogue_id", "position", name="uq_basis_set_catalogue_literature_position"
        ),
        CheckConstraint("position >= 1", name="position_positive"),
    )
