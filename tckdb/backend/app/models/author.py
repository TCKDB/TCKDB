from sqlalchemy import Column, Integer, String
from sqlalchemy.orm import relationship

from tckdb.backend.app.db.base_class import Base

class Author(Base):
    id = Column(Integer, primary_key=True, index=True, nullable=False, autoincrement=True)
    first_name = Column(String(100), nullable=False)
    last_name = Column(String(100), nullable=False)

    literature = relationship("Literature", secondary="literature_author", back_populates="authors")

    def __repr__(self):
        return f"<Author(id={self.id}, first_name={self.first_name}, last_name={self.last_name})>"
