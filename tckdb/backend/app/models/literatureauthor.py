from sqlalchemy import Table, Column, Integer, ForeignKey
from tckdb.backend.app.db.base_class import Base


literature_author = Table(
    'literature_author', Base.metadata,
    Column('literature_id', Integer, ForeignKey('literature.id', ondelete="CASCADE"), primary_key=True),
    Column('author_id', Integer, ForeignKey('author.id', ondelete="RESTRICT"), primary_key=True)
)
