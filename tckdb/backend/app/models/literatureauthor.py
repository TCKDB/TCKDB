from sqlalchemy import Column, Integer, ForeignKey
from tckdb.backend.app.db.base_class import Base

class LiteratureAuthor(Base):
    literature_id = Column(Integer, ForeignKey('literature.id'), primary_key=True)
    author_id = Column(Integer, ForeignKey('author.id'), primary_key=True)

    def __repr__(self):
        return f"<LiteratureAuthor(literature_id={self.literature_id}, author_id={self.author_id})>"
    