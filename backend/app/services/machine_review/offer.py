"""Check that a review cites only what the run offered.

A finding may address a record only by a ``(record_type, record_ref)`` pair the
context listed, or by neither; ``retrieval_sources`` may name only keys a
guidance search returned. A violation fails the review: the reviewer does not
get to cite records or sources it was never shown.
"""

from __future__ import annotations

from app.services.machine_review.providers.interface import MachineReviewContext
from app.services.machine_review.schemas import MachineReviewProviderResult


def offered_refs_from_context(context: MachineReviewContext) -> frozenset[tuple[str, str]]:
    """The ``(record_type, record_ref)`` pairs a review of ``context`` may cite."""
    precheck = context.precheck_context
    if precheck is None:
        return frozenset()
    return frozenset((link.record_type, str(link.record_id)) for link in precheck.record_refs)


def offer_violation(
    result: MachineReviewProviderResult,
    *,
    offered_refs: frozenset[tuple[str, str]],
    returned_sources: frozenset[str],
) -> str | None:
    """Return a sentence naming the first thing ``result`` cites that was not offered, else ``None``."""
    for index, finding in enumerate(result.findings, start=1):
        if finding.record_type is None and finding.record_ref is None:
            continue
        if finding.record_type is None or finding.record_ref is None:
            return f"Finding {index} sets only one of record_type and record_ref."
        if (finding.record_type, finding.record_ref) not in offered_refs:
            return (
                f"Finding {index} cites a record_type and record_ref that were not offered: "
                f"{finding.record_type!r}, {finding.record_ref!r}."
            )
    for source in result.retrieval_sources:
        if source not in returned_sources:
            return f"retrieval_sources names a source that was not returned by a search: {source!r}."
    return None
