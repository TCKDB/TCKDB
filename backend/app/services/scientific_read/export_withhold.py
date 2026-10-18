"""Withholding hidden refs from the export streams (curated-guard PR-4).

The NDJSON exports are generators of plain dicts and bypass the response boundary the other scientific routes
return through. This batcher is their seam: it buffers up to :data:`EXPORT_BATCH_SIZE` record dicts, asks the ref
guard once about the whole batch (:func:`~app.services.scientific_read.ref_guard.withhold_in_document`, untyped mode:
a hidden ref becomes ``null``, a hidden ref in a list of strings is dropped) and only then releases the lines. A
withheld ref is ``null`` in an export; there are no placeholders.

Two rules that the callers must keep:

* the profile is passed in, captured while the request was alive. A stream outlives the request, so the context
  variable is not readable here (or anywhere inside a generator);
* the session is the one that read the rows, so a batch is judged from the same snapshot as its content.

Under the exploratory profile nothing is buffered and nothing is asked: each line is released as it is built, exactly
as before.
"""

from __future__ import annotations

import json

from sqlalchemy.orm import Session

from app.db.models.common import ProfileRecommendation
from app.services.scientific_read import ref_guard
from app.services.scientific_read.profile import ResolvedReadProfile

#: Records per visibility statement.
EXPORT_BATCH_SIZE = 200


def dumps_line(obj: dict) -> str:
    """Byte-stable single-line JSON with its newline (sorted keys, compact separators)."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":")) + "\n"


class WithheldBatcher:
    """Buffer record dicts, withhold hidden refs once per batch, release JSON lines."""

    def __init__(self, session: Session, profile: ResolvedReadProfile) -> None:
        self._session = session
        self._profile = profile
        self._active = (
            profile.review_floor is not None and profile.recommendation is not ProfileRecommendation.tckdb_curated_release
        )
        self._pending: list[dict] = []

    def add(self, record: dict) -> list[str]:
        """Take one record; return the lines now releasable (none until the batch is full)."""
        if not self._active:
            return [dumps_line(record)]
        self._pending.append(record)
        if len(self._pending) >= EXPORT_BATCH_SIZE:
            return self.flush()
        return []

    def flush(self) -> list[str]:
        """Withhold in, and release, whatever is buffered."""
        if not self._pending:
            return []
        batch, self._pending = self._pending, []
        # Looked up on the module at call time, so a test (or the sweep) can observe the call.
        ref_guard.withhold_in_document(self._session, batch, profile=self._profile)
        return [dumps_line(record) for record in batch]


__all__ = ["EXPORT_BATCH_SIZE", "WithheldBatcher", "dumps_line"]
