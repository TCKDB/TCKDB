"""Allowlisted CCCBDB URLs for the snapshot archive.

The Phase 0 spec (``backend/docs/specs/cccbdb_importer.md``) restricts
the importer to an explicit allowlist. No discovered link is followed,
no formula is auto-fetched, and no rate-limit-busting expansion is
performed by the snapshot command.

URL contract
============

CCCBDB does **not** expose stable per-species GET URLs for
experimental data. The single-molecule data flow (``exp1x.asp``) is a
POST form whose results are served via server-side session state, not
a query string. Empirical confirmation (May 2026):

* ``exp1x.asp?formula=H2O`` — returns the form page, not the data.
* ``exp1x.asp?casno=7732185`` — same; Cloudflare emits a 1015 for
  unrecognized URL patterns rather than a 404.

So the targets defined below carry an explicit
:attr:`CrawlTarget.is_validated_url` flag. The snapshot CLI refuses to
fetch any unvalidated URL unless ``--allow-unverified-urls`` is
passed, to avoid tripping the upstream rate limiter again.

Reaching real per-species data requires one of:

1. A session-aware fetcher (POST the formula form, follow cookies to
   the resulting data page). Out of scope for Phase 2b's polite
   single-GET runner.
2. Cross-species property tables such as ``xp1x.asp?prop=1`` (enthalpy
   of formation for all species). These are stable GET URLs but
   represent a *different* page kind that the current Phase 1
   experimental-species parser does not handle.

Tests use the bundled hand-authored fixtures via ``FixtureFetcher`` and
never touch these URLs. Extending the list to the full pilot set
(~14 species) is intentional future work tied to fixing the URL
strategy above.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

PageKind = Literal[
    "experimental_species",
    "experimental_property_table",
    "molecule_catalog_inchi_index",
]


@dataclass(frozen=True)
class CrawlTarget:
    """One allowlisted CCCBDB page.

    :param species_key: Short kebab-case identifier used in filenames
        (``"h2"``, ``"h2o"``, ``"benzene"``). Must be filesystem-safe.
    :param source_url: Exact URL the snapshot will fetch.
    :param page_kind: CCCBDB page family. Only
        ``"experimental_species"`` is supported in Phase 2b.
    :param is_validated_url: ``True`` only when the URL is known to
        resolve to per-species data without session state. CCCBDB's
        ``exp1x.asp`` family does *not* — see module docstring for
        the empirical confirmation. The CLI refuses to fetch an
        unvalidated URL unless ``--allow-unverified-urls`` is passed.
    :param notes: Free-text rationale for maintainers.
    :param property_kind: For ``experimental_property_table`` targets,
        a machine token naming the property the table represents
        (``hf_0``, ``hf_0_with_uncertainty``, ``dipole``,
        ``diatomic_spectroscopic``). Ignored for other page kinds.
    """

    species_key: str
    source_url: str
    page_kind: PageKind = "experimental_species"
    is_validated_url: bool = False
    notes: str = ""
    property_kind: str | None = None


# These URLs are placeholders pending a session-aware fetcher or a
# pivot to ``xp1x.asp`` property tables. They are NOT known to
# resolve to per-species data on the live site. The CLI guards
# against accidentally fetching them.
EXPERIMENTAL_PILOT: tuple[CrawlTarget, ...] = (
    CrawlTarget(
        species_key="h2",
        source_url="https://cccbdb.nist.gov/exp1x.asp?casno=1333740",
        notes="placeholder; exp1x.asp does not accept query-string molecule selection",
    ),
    CrawlTarget(
        species_key="h2o",
        source_url="https://cccbdb.nist.gov/exp1x.asp?casno=7732185",
        notes="placeholder; exp1x.asp does not accept query-string molecule selection",
    ),
    CrawlTarget(
        species_key="benzene",
        source_url="https://cccbdb.nist.gov/exp1x.asp?casno=71432",
        notes="placeholder; exp1x.asp does not accept query-string molecule selection",
    ),
)


# Cross-species property-table URLs (the "xp1x.asp-family"). These
# are the *durable* CCCBDB data path: one URL = one wide table = many
# species' rows for one property. Empirically confirmed flat single-GET
# resources (May 2026 WebFetch survey) — no session state, no form
# submission needed.
#
# Each entry's ``species_key`` is a target identifier used in archive
# filenames (``property_<species_key>_<sha12>.html``); for property
# tables it doubles as the property-table identifier.
EXPERIMENTAL_PROPERTIES_PILOT: tuple[CrawlTarget, ...] = (
    CrawlTarget(
        species_key="hf_0",
        source_url="https://cccbdb.nist.gov/hf0kx.asp",
        page_kind="experimental_property_table",
        property_kind="hf_0",
        is_validated_url=True,
        notes="Hf(0K) flat table; kJ/mol; columns Species|Name|Hfg 0K|Reference|DOI",
    ),
    CrawlTarget(
        species_key="hf_0_with_uncertainty",
        source_url="https://cccbdb.nist.gov/goodlistx.asp",
        page_kind="experimental_property_table",
        property_kind="hf_0_with_uncertainty",
        is_validated_url=True,
        notes="Well-known Hf(0K) + unc; kJ/mol; columns Element|Species|Enthalpy 0K|unc",
    ),
    CrawlTarget(
        species_key="dipole",
        source_url="https://cccbdb.nist.gov/diplistx.asp",
        page_kind="experimental_property_table",
        property_kind="dipole",
        is_validated_url=True,
        notes="Experimental dipoles; Debye; tot=magnitude; columns Molecule|name|state|x|y|z|tot|squib|comment",
    ),
    CrawlTarget(
        species_key="diatomic_spectroscopic",
        source_url="https://cccbdb.nist.gov/expdiatomicsx.asp",
        page_kind="experimental_property_table",
        property_kind="diatomic_spectroscopic",
        is_validated_url=True,
        notes="Diatomic spectroscopic constants; cm^-1; columns Species|name|we|wexe|weye|Be|De|alphae|reference",
    ),
)


# Molecule catalog (IDENTITY UNIVERSE ONLY). The ``inchix.asp`` page
# enumerates molecules with formula / name / InChI / InChIKey /
# SMILES. Its outbound links are NOT trusted as data-page URLs — see
# CCCBDBCatalogEntry's docstring and the README. A future search/form
# resolver may translate catalog entries into real data URLs; until
# then the catalog is enrichment metadata only.
CATALOG_PILOT: tuple[CrawlTarget, ...] = (
    CrawlTarget(
        species_key="inchix",
        source_url="https://cccbdb.nist.gov/inchix.asp",
        page_kind="molecule_catalog_inchi_index",
        is_validated_url=True,
        notes="CCCBDB molecule catalog (identity universe); links inside are NOT trusted as data URLs",
    ),
)


PILOTS: dict[str, tuple[CrawlTarget, ...]] = {
    "experimental": EXPERIMENTAL_PILOT,
    "experimental-properties": EXPERIMENTAL_PROPERTIES_PILOT,
    "catalog": CATALOG_PILOT,
}


class UnverifiedUrlError(RuntimeError):
    """Raised when the CLI is about to fetch an unverified CCCBDB URL."""


def assert_all_validated(targets: tuple[CrawlTarget, ...]) -> None:
    """Raise :class:`UnverifiedUrlError` if any target's URL is unverified.

    Called by the snapshot CLI before performing real network fetches.
    Tests with injected fetchers bypass this guard entirely.
    """

    unverified = [t for t in targets if not t.is_validated_url]
    if unverified:
        lines = [
            f"  {t.species_key}: {t.source_url}  ({t.notes or 'no rationale'})"
            for t in unverified
        ]
        raise UnverifiedUrlError(
            "Refusing to fetch unverified CCCBDB URL(s):\n"
            + "\n".join(lines)
            + "\nPass --allow-unverified-urls to override (you will likely "
            "trip Cloudflare 1015)."
        )
