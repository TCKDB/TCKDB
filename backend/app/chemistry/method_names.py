"""Method-name identity keys. The table and the key live in :mod:`tckdb_schemas.method_names`; this module re-exports them."""

from __future__ import annotations

from tckdb_schemas.method_names import (
    NAME_ALIASES,
    SUFFIX_ALIASES,
    SUFFIX_RULES,
    MethodAlias,
    method_identity_key,
)

__all__ = ["NAME_ALIASES", "SUFFIX_ALIASES", "SUFFIX_RULES", "MethodAlias", "method_identity_key"]
