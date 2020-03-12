"""
TCKDB backend app models common module

Inspired by:
https://docs.sqlalchemy.org/en/13/orm/extensions/mutable.html#establishing-mutability-on-scalar-column-values
"""

import json

from sqlalchemy.ext.mutable import Mutable
from sqlalchemy.types import TypeDecorator, VARCHAR


class JSONEncodedDict(TypeDecorator):
    """
    Represents an immutable structure as a json-encoded string.
    """

    impl = VARCHAR

    def process_bind_param(self, value, dialect):
        if value is not None:
            value = json.dumps(value)
        return value

    def process_result_value(self, value, dialect):
        if value is not None:
            value = json.loads(value)
        return value


class MutableDict(Mutable, dict):
    """
    This dictionary suc-class routes all dictionary mutation events through __setitem__.
    The Mutable.changed() method is called whenever an in-place change to the data structure takes place.
    """

    @classmethod
    def coerce(cls, key, value):
        """Convert plain dictionaries to MutableDict."""

        if not isinstance(value, MutableDict):
            if isinstance(value, dict):
                return MutableDict(value)

            # this call will raise ValueError
            return Mutable.coerce(key, value)
        else:
            return value

    def __setitem__(self, key, value):
        """Detect dictionary set events and emit change events."""

        dict.__setitem__(self, key, value)
        self.changed()

    def __delitem__(self, key):
        """Detect dictionary del events and emit change events."""

        dict.__delitem__(self, key)
        self.changed()
