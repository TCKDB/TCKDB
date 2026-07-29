"""Shared software identity normalization used by schema and backend layers."""

from tckdb_schemas.utils import normalize_required_text

_SOFTWARE_NAME_ALIASES = {"arc": "ARC", "gaussian": "Gaussian", "orca": "ORCA", "rmg": "RMG"}


def normalize_software_name(name: str) -> str:
    """Return the canonical software name used by registry identity/dedupe."""
    normalized = normalize_required_text(name)
    return _SOFTWARE_NAME_ALIASES.get(" ".join(normalized.split()).lower(), normalized)
