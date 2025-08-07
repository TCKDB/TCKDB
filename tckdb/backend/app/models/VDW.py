from sqlalchemy import Column, Float, ForeignKey, Integer, String
from sqlalchemy.orm import relationship

from tckdb.backend.app.db.base_class import Base
from tckdb.backend.app.models.common import MsgpackExt


class VDW(Base):
    """A class representing the chemical identity of a VDW well"""

    __tablename__ = "vdw"

    id = Column(Integer, primary_key=True, index=True, nullable=False)
    inchi_augmented = Column(String(5000), nullable=False)
    constituents = Column(MsgpackExt, nullable=False)
    molecular_formula = Column(String(255), nullable=True)
    molecular_weight = Column(Float, nullable=True)
    charge = Column(Integer, nullable=False)
    multiplicity = Column(Integer, nullable=False)
    labels = Column(MsgpackExt, nullable=True)
    fragment_orientation = Column(MsgpackExt, nullable=True)

    entries = relationship(
        "VDWEntry", back_populates="vdw", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:  # pragma: no cover - simple representation
        return f"<{self.__class__.__name__}(id={self.id}, inchi_augmented='{self.inchi_augmented}')>"


class VDWEntry(Base):
    """A class representing a computed entry for a VDW well"""

    __tablename__ = "vdw_entry"

    id = Column(Integer, primary_key=True, index=True, nullable=False)
    vdw_id = Column(Integer, ForeignKey("vdw.id"), nullable=False)
    xyz = Column(MsgpackExt, nullable=True)
    energy = Column(Float, nullable=True)

    vdw = relationship("VDW", back_populates="entries")

    def __repr__(self) -> str:  # pragma: no cover - simple representation
        return f"<{self.__class__.__name__}(id={self.id}, vdw_id={self.vdw_id})>"
