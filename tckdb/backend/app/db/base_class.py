"""
TCKDB backend app db base_class module
allows the creation of classes that include directives to describe the actual database table they will be mapped to
"""

from sqlalchemy.ext.declarative import declarative_base, declared_attr


class CustomBase(object):
    """
    A custom base class for generating __tablename__ automatically
    """
    @declared_attr
    def __tablename__(cls):
        return cls.__name__.lower()


Base = declarative_base(cls=CustomBase)
