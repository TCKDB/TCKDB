"""
TCKDB backend app models common module
"""

import json
import msgpack
import numpy as np
from typing import Any

from pydantic.json import pydantic_encoder
from sqlalchemy.dialects.postgresql import BYTEA
from sqlalchemy.ext.mutable import Mutable
from sqlalchemy.types import TypeDecorator, VARCHAR


# JSON Ext
# Inspired by:
# https://docs.sqlalchemy.org/en/13/orm/extensions/mutable.html#establishing-mutability-on-scalar-column-values

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


# MSGPackExt
# Inspired by:
# https://github.com/MolSSI/QCElemental/blob/master/qcelemental/util/serialization.py


def msgpackext_encode(obj: Any) -> Any:
    """
    Encodes an object using pydantic and NumPy array serialization techniques suitable for msgpack.

    Args:
        obj (Any): Any object that can be serialized with pydantic and NumPy encoding techniques.

    Returns:
        Any: A msgpack compatible form of the object.
    """
    # First try pydantic base objects
    try:
        return pydantic_encoder(obj)
    except TypeError:
        pass

    if isinstance(obj, np.ndarray):
        if obj.shape:
            data = {b"_nd_": True, b"dtype": obj.dtype.str, b"data": np.ascontiguousarray(obj).tobytes()}
            if len(obj.shape) > 1:
                data[b"shape"] = obj.shape
            return data
        else:
            # Converts np.array(5) -> 5
            return obj.tolist()
    return obj


def msgpackext_decode(obj: Any) -> Any:
    """
    Decodes a msgpack objects from a dictionary representation.

    Args:
    obj (Any): An encoded object, likely a dictionary.

    Returns:
        Any: The decoded form of the object.
    """
    if b"_nd_" in obj:
        arr = np.frombuffer(obj[b"data"], dtype=obj[b"dtype"])
        if b"shape" in obj:
            arr.shape = obj[b"shape"]
        return arr
    return obj


def msgpackext_dumps(data: Any) -> bytes:
    """
    Safe serialization of a Python object to msgpack binary representation using all known encoders.
    For NumPy, encodes a specialized object format to encode all shape and type data.

    Args:
        data (Any): An encodable python object.

    Returns:
        bytes: A msgpack representation of the data in bytes.
    """
    return msgpack.dumps(data, default=msgpackext_encode, use_bin_type=True)


def msgpackext_loads(data: bytes) -> Any:
    """
    Deserializes a msgpack byte representation of known objects into those objects.

    Args:
    data (bytes): The serialized msgpack byte array.

    Returns:
        Any: The deserialized Python objects.
    """
    return msgpack.loads(data, object_hook=msgpackext_decode, raw=False)


class MsgpackExt(TypeDecorator):
    """
    Converts JSON-like data to msgpack with full NumPy Array support.
    """
    impl = BYTEA

    def process_bind_param(self, value, dialect):
        """
        Receive a bound parameter value to be converted.
        """
        if value is None:
            return value
        else:
            return msgpackext_dumps(value)

    def process_result_value(self, value, dialect):
        """
        Receive a result-row column value to be converted.
        """
        if value is None:
            return value
        else:
            return msgpackext_loads(value)
