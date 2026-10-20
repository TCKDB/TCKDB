"""Format versions every manifest reader must refuse.

The readers look the recorded ``manifest_format_version`` up in a ``VersionRegistry`` that matches an exact
``int``. The old readers tested membership of a frozenset (or ``!=``), so ``2.0`` equalled ``2`` and ``True``
equalled ``1``; those, text, and a missing value are now refused. A deliberate tightening, pinned here.
"""

from __future__ import annotations


def bad_format_versions(current: int) -> list:
    return [None, str(current), float(current), True, False, current + 1000]
