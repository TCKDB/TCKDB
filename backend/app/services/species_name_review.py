"""The review-file contract shared by the offline name fetch and the name import. No network code."""

from __future__ import annotations

import re

REVIEW_FORMAT = "tckdb-species-names-review/1"

_INCHIKEY = re.compile(r"^[A-Z]{14}-[A-Z]{10}-[A-Z]$")


def is_inchikey(value: object) -> bool:
    return isinstance(value, str) and _INCHIKEY.fullmatch(value) is not None
