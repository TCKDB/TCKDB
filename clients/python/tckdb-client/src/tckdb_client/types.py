"""Shared type aliases for the TCKDB client."""

from __future__ import annotations

from typing import Any, Mapping

JSONValue = Any
JSONDict = dict[str, JSONValue]
HeadersLike = Mapping[str, str]

__all__ = ["JSONValue", "JSONDict", "HeadersLike"]
