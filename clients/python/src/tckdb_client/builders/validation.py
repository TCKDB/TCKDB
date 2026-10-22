"""Local validation helpers for the builder layer.

The builder layer raises a single dedicated exception for failures that
happen *before* the request leaves the process. Server-side rejections
keep using :class:`TCKDBValidationError` from
``tckdb_client.errors`` — the two exception types stay distinct so a
caller can tell "my inputs are inconsistent" apart from "the server
rejected my payload".
"""

from __future__ import annotations

import re

__all__ = [
    "TCKDBBuilderValidationError",
    "ensure_non_empty_str",
    "ensure_optional_non_empty_str",
    "ensure_int",
    "ensure_positive_int",
    "slugify_label",
]


class TCKDBBuilderValidationError(ValueError):
    """Local builder validation failure (no HTTP request was attempted)."""


def ensure_non_empty_str(value: object, *, field: str) -> str:
    """Require a non-empty string, raise otherwise."""
    if not isinstance(value, str):
        raise TCKDBBuilderValidationError(
            f"{field} must be a string, got {type(value).__name__}."
        )
    if not value.strip():
        raise TCKDBBuilderValidationError(f"{field} must be non-empty.")
    return value


def ensure_optional_non_empty_str(value: object, *, field: str) -> str | None:
    """Allow ``None``; require non-empty string otherwise."""
    if value is None:
        return None
    return ensure_non_empty_str(value, field=field)


def ensure_int(value: object, *, field: str) -> int:
    """Require an integer (bools are rejected even though they subtype int)."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise TCKDBBuilderValidationError(
            f"{field} must be an int, got {type(value).__name__}."
        )
    return value


def ensure_positive_int(value: object, *, field: str, minimum: int = 1) -> int:
    """Require an int >= ``minimum``."""
    out = ensure_int(value, field=field)
    if out < minimum:
        raise TCKDBBuilderValidationError(
            f"{field} must be >= {minimum}, got {out}."
        )
    return out


_SLUG_RE = re.compile(r"[^a-z0-9]+")


def slugify_label(label: str) -> str:
    """Lowercase the label and replace runs of non-alphanumerics with ``_``.

    Result is trimmed of leading/trailing underscores. If the label was
    purely punctuation and slugifies to the empty string, raises.
    """
    slug = _SLUG_RE.sub("_", label.lower()).strip("_")
    if not slug:
        raise TCKDBBuilderValidationError(
            f"label {label!r} slugifies to an empty string; "
            "use at least one alphanumeric character."
        )
    return slug
