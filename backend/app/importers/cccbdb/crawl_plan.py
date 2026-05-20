"""Allowlisted CCCBDB URLs for the snapshot archive.

The Phase 0 spec (``backend/docs/specs/cccbdb_importer.md``) restricts
the importer to an explicit allowlist. No discovered link is followed,
no formula is auto-fetched, and no rate-limit-busting expansion is
performed by the snapshot command.

Extending the list to the full pilot set (~14 species) is intentional
future work — adding a new entry here is the only place a maintainer
needs to touch to grow the snapshot.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

PageKind = Literal["experimental_species"]


@dataclass(frozen=True)
class CrawlTarget:
    """One allowlisted CCCBDB page.

    :param species_key: Short kebab-case identifier used in filenames
        (``"h2"``, ``"h2o"``, ``"benzene"``). Must be filesystem-safe.
    :param source_url: Exact URL the snapshot will fetch.
    :param page_kind: CCCBDB page family. Only
        ``"experimental_species"`` is supported in Phase 2b.
    """

    species_key: str
    source_url: str
    page_kind: PageKind = "experimental_species"


EXPERIMENTAL_PILOT: tuple[CrawlTarget, ...] = (
    CrawlTarget(
        species_key="h2",
        source_url="https://cccbdb.nist.gov/exp1x.asp?casno=1333740",
    ),
    CrawlTarget(
        species_key="h2o",
        source_url="https://cccbdb.nist.gov/exp1x.asp?casno=7732185",
    ),
    CrawlTarget(
        species_key="benzene",
        source_url="https://cccbdb.nist.gov/exp1x.asp?casno=71432",
    ),
)


PILOTS: dict[str, tuple[CrawlTarget, ...]] = {
    "experimental": EXPERIMENTAL_PILOT,
}
