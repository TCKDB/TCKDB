"""
TCKDB backend app db base_class module
allows the creation of classes that include directives to describe the actual database table they will be mapped to
"""

import sqlalchemy
from sqlalchemy import Column, DateTime, func
from sqlalchemy.ext.declarative import declared_attr
from sqlalchemy.orm import Session


class CustomBase(object):
    """
    A custom base class for generating __tablename__ automatically
    """

    @declared_attr
    def __tablename__(cls):
        return cls.__name__.lower()


class AuditMixin:
    """
    Mixin to add auditing and soft delete fields to a model
    """

    created_at = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=True,
    )
    deleted_at = Column(DateTime(timezone=True), nullable=True)

    def soft_delete(self):
        """
        Marks the record as deleted by setting the deleted_at field
        """
        self.deleted_at = func.now()

    def hard_delete(self, db: Session):
        """
        Permanently deletes the record from the database
        """
        db.delete(self)


Base = sqlalchemy.orm.declarative_base()
