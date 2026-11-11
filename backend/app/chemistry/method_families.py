"""Method-family classification. The table and the lookup live in :mod:`tckdb_schemas.method_families`; this module
re-exports them and holds the version registry the backend dispatches through.

Nothing in TCKDB orders, ranks, breaks a tie with, defaults to or labels "best" by a family or a rung.
"""

from __future__ import annotations

from tckdb_schemas.method_families import (
    CLASSIFICATION,
    CLASSIFICATION_NAME,
    CLASSIFICATION_REF,
    CLASSIFICATION_VERSION,
    DFT_BY_RUNG,
    DISPERSION_STRIP_PATTERN,
    DISPERSION_SUFFIXES,
    MAX_DFT_RUNG,
    MIN_DFT_RUNG,
    OTHER_BY_FAMILY,
    SPIN_PREFIXES,
    SUFFIX_STEM_OVERRIDES,
    SUFFIXED_KEYS,
    UNCLASSIFIED,
    MethodClass,
    MethodClassFilter,
    MethodFamily,
    method_stem_key,
)

from app.versioning import VersionRegistry

#: The registry the classification is held in: one name, one current version.
CLASSIFICATIONS: VersionRegistry[dict[str, MethodClass]] = VersionRegistry(
    "method_family_classifications", startup_checked=True
)
CLASSIFICATIONS.register(CLASSIFICATION_NAME, CLASSIFICATION_VERSION, CLASSIFICATION, current=True)
CLASSIFICATIONS.validate()


def classify_method(name: str | None, version: int | None = None) -> MethodClass:
    """The class of a method name under the registered version; ``unclassified`` for ``None``, blank or an unknown name."""
    if name is None or not name.strip():
        return UNCLASSIFIED
    return CLASSIFICATIONS.get(CLASSIFICATION_NAME, version).get(method_stem_key(name), UNCLASSIFIED)


__all__ = [
    "CLASSIFICATION",
    "CLASSIFICATIONS",
    "CLASSIFICATION_NAME",
    "CLASSIFICATION_REF",
    "CLASSIFICATION_VERSION",
    "DFT_BY_RUNG",
    "DISPERSION_STRIP_PATTERN",
    "DISPERSION_SUFFIXES",
    "MAX_DFT_RUNG",
    "MIN_DFT_RUNG",
    "OTHER_BY_FAMILY",
    "SPIN_PREFIXES",
    "SUFFIXED_KEYS",
    "SUFFIX_STEM_OVERRIDES",
    "UNCLASSIFIED",
    "MethodClass",
    "MethodClassFilter",
    "MethodFamily",
    "classify_method",
    "method_stem_key",
]
