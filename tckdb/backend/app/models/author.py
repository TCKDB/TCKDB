from sqlalchemy import Column, Integer, String, UniqueConstraint, Index
from sqlalchemy.orm import relationship
from tckdb.backend.app.models.literatureauthor import literature_author  # Import the intermediary table

from tckdb.backend.app.db.base_class import Base

class Author(Base):
    __tablename__ = 'author'

    id = Column(Integer, primary_key=True, index=True)
    first_name = Column(String, nullable=False)
    last_name = Column(String, nullable=False)

    # Unique constraint to prevent duplicate authors
    __table_args__ = (
        UniqueConstraint('first_name', 'last_name', name='_author_name_uc'),
    )

    # Establish many-to-many relationship with Literature
    literatures = relationship(
        "Literature",
        secondary=literature_author,
        back_populates="authors"
    )

    def __repr__(self):
        return f"<Author(id={self.id}, first_name='{self.first_name}', last_name='{self.last_name}')>"


# Adding a composite index for first_name and last_name for faster lookup
Index('ix_author_full_name', Author.first_name, Author.last_name, unique=True)
