"""Audit ``PROPERTY_CONFIGS`` against the CCCBDB Experimental index.

This is a diagnostic, not a gatekeeper. Output answers four questions
about the state of the importer at a point in time:

1. Which CCCBDB experimental pages exist (live)?
2. Which are already configured (registered in ``PROPERTY_CONFIGS``)?
3. Which configured URLs are stale (point at a page CCCBDB no longer
   advertises in its Experimental sub-menu)?
4. Which high-value pages are still missing (advertised by CCCBDB but
   not configured here)?

The companion :mod:`app.importers.cccbdb.parsers.experimental_index`
enumerates CCCBDB's side; ``PROPERTY_CONFIGS`` is the local truth.
The audit joins them by ``target_guess`` (the parser's static
href → property_kind mapping) so the URL itself is the stable key.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.importers.cccbdb.crawl_plan import (
    EXPERIMENTAL_PROPERTIES_PILOT,
    CrawlTarget,
)
from app.importers.cccbdb.parsers.experimental_index import (
    ExperimentalIndex,
    ExperimentalIndexLink,
    is_form_only,
)
from app.importers.cccbdb.parsers.experimental_property_table import (
    PROPERTY_CONFIGS,
)


@dataclass(frozen=True)
class ConfiguredTarget:
    """One ``PROPERTY_CONFIGS`` entry projected for audit display."""

    property_kind: str
    source_url: str | None
    value_column: str

    def to_json(self) -> dict[str, Any]:
        return {
            "property_kind": self.property_kind,
            "source_url": self.source_url,
            "value_column": self.value_column,
        }


@dataclass
class PropertyConfigAuditResult:
    """Result of joining ``PROPERTY_CONFIGS`` with an
    :class:`ExperimentalIndex`."""

    configured_targets: list[ConfiguredTarget] = field(default_factory=list)
    experimental_index_links: list[ExperimentalIndexLink] = field(
        default_factory=list
    )
    matched_targets: list[str] = field(default_factory=list)
    unmatched_configured_targets: list[str] = field(default_factory=list)
    unconfigured_experimental_links: list[ExperimentalIndexLink] = field(
        default_factory=list
    )
    form_only_deferred_links: list[ExperimentalIndexLink] = field(
        default_factory=list
    )
    """
    Subset of ``unconfigured_experimental_links`` that are CCCBDB
    form-only pages (POST against ``getformx.asp``). These are NOT
    candidates for the single-GET property-table importer — they
    need a future session-aware resolver. The audit splits them out
    so the maintainer's "what's left" view is honest about which
    links are addressable today.
    """

    def to_json(self) -> dict[str, Any]:
        return {
            "configured_targets": [t.to_json() for t in self.configured_targets],
            "experimental_index_links": [
                link.to_json() for link in self.experimental_index_links
            ],
            "matched_targets": list(self.matched_targets),
            "unmatched_configured_targets": list(
                self.unmatched_configured_targets
            ),
            "unconfigured_experimental_links": [
                link.to_json() for link in self.unconfigured_experimental_links
            ],
            "form_only_deferred_links": [
                link.to_json() for link in self.form_only_deferred_links
            ],
        }


def _crawl_targets_by_kind() -> dict[str, CrawlTarget]:
    return {
        t.property_kind: t
        for t in EXPERIMENTAL_PROPERTIES_PILOT
        if t.property_kind is not None
    }


def audit_property_configs(
    index: ExperimentalIndex,
) -> PropertyConfigAuditResult:
    """Compare ``PROPERTY_CONFIGS`` against an experimental index.

    :param index: Parsed :class:`ExperimentalIndex` (typically from
        :func:`parse_experimental_index_page`).
    :returns: A :class:`PropertyConfigAuditResult` summarizing the
        join. Configured kinds that don't appear in the index land
        in ``unmatched_configured_targets`` (URL is likely stale).
        Index links that don't map to a configured kind land in
        ``unconfigured_experimental_links`` (likely-future targets).

    Matching is by ``link.target_guess`` against ``PROPERTY_CONFIGS``
    keys. Links with ``target_guess=None`` (unknown CCCBDB pages) are
    always classified as ``unconfigured_experimental_links``.
    """

    crawl_by_kind = _crawl_targets_by_kind()
    configured_kinds = set(PROPERTY_CONFIGS.keys())

    configured = [
        ConfiguredTarget(
            property_kind=kind,
            source_url=(
                crawl_by_kind[kind].source_url
                if kind in crawl_by_kind
                else None
            ),
            value_column=PROPERTY_CONFIGS[kind].value_column,
        )
        for kind in sorted(configured_kinds)
    ]

    matched: set[str] = set()
    unconfigured: list[ExperimentalIndexLink] = []
    form_only: list[ExperimentalIndexLink] = []
    for link in index.links:
        guess = link.target_guess
        if guess is not None and guess in configured_kinds:
            matched.add(guess)
            continue
        unconfigured.append(link)
        if is_form_only(link.href):
            form_only.append(link)

    unmatched = sorted(configured_kinds - matched)

    return PropertyConfigAuditResult(
        configured_targets=configured,
        experimental_index_links=list(index.links),
        matched_targets=sorted(matched),
        unmatched_configured_targets=unmatched,
        unconfigured_experimental_links=unconfigured,
        form_only_deferred_links=form_only,
    )


__all__ = [
    "ConfiguredTarget",
    "PropertyConfigAuditResult",
    "audit_property_configs",
]
