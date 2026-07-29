"""Shared type aliases for the TCKDB client."""

from __future__ import annotations

from typing import Any, Mapping

from tckdb_schemas.fragments.execution_environment import ExecutionEnvironmentManifestPayload

JSONValue = Any
JSONDict = dict[str, JSONValue]
HeadersLike = Mapping[str, str]
ExecutionEnvironmentManifest = ExecutionEnvironmentManifestPayload

__all__ = ["ExecutionEnvironmentManifest", "HeadersLike", "JSONDict", "JSONValue"]
