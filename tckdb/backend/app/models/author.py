from sqlalchemy import Column, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import relationship

from tckdb.backend.app.db.base_class import Base
from tckdb.backend.app.models.literatureauthor import (  # Import the intermediary table
    literature_author,
)


class Author(Base):
    __tablename__ = "author"

    id = Column(Integer, primary_key=True, index=True)
    first_name = Column(String, nullable=False)
    last_name = Column(String, nullable=False)
    orcid = Column(String(19), nullable=True, unique=True, index=True)

    # Unique constraint to prevent duplicate authors
    __table_args__ = (
        UniqueConstraint("first_name", "last_name", name="_author_name_uc"),
    )

    # Establish many-to-many relationship with Literature
    literatures = relationship(
        "Literature", secondary=literature_author, back_populates="authors"
    )

    def __repr__(self):
        return f"<Author(id={self.id}, first_name='{self.first_name}', last_name='{self.last_name}')>"


# Adding a composite index for first_name and last_name for faster lookup
Index("ix_author_full_name", Author.first_name, Author.last_name, unique=True)
