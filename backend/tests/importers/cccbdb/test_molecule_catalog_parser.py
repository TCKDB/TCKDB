"""Parser tests for CCCBDB's molecule catalog (``inchix.asp``).

All tests offline. Catalog hyperlinks are preserved verbatim as
``raw_href`` but the parser must never set ``trusted_property_url`` /
``trusted_species_url`` — those are reserved for a future resolver.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.importers.cccbdb import (
    PARSER_VERSION,
    SOURCE_DATABASE_DOI,
    SOURCE_NAME,
    SOURCE_RELEASE,
)
from app.importers.cccbdb.parsers import (
    parse_molecule_catalog_page,
    resolve_species_data_page_from_search,
)

FIXTURES_DIR = (
    Path(__file__).resolve().parents[3]
    / "app"
    / "importers"
    / "cccbdb"
    / "fixtures"
)
CATALOG_URL = "https://cccbdb.nist.gov/inchix.asp"


@pytest.fixture(scope="module")
def catalog():
    html = (FIXTURES_DIR / "catalog_inchix.html").read_text(encoding="utf-8")
    return parse_molecule_catalog_page(html, source_url=CATALOG_URL)


class TestCatalogIdentifiers:
    def test_parses_multiple_entries(self, catalog):
        # 11 body rows in the fixture; one is all-blank and gets dropped.
        assert len(catalog.entries) == 10

    def test_h2o_full_identity(self, catalog):
        h2o = next(e for e in catalog.entries if e.name == "Water")
        assert h2o.formula == "H2O"
        assert h2o.inchi == "InChI=1S/H2O/h1H2"
        assert h2o.inchikey == "XLYOFNOQVPJJNP-UHFFFAOYSA-N"
        assert h2o.smiles == "O"
        assert h2o.cas_number == "7732-18-5"

    def test_isomer_pair_c2h6o_preserved(self, catalog):
        isomers = [e for e in catalog.entries if e.formula == "C2H6O"]
        assert len(isomers) == 2
        assert {e.name for e in isomers} == {"Ethanol", "Dimethyl ether"}

    def test_partial_identifier_only_row_kept(self, catalog):
        # Last fixture row has only InChI + InChIKey; no formula or name.
        partial = [
            e
            for e in catalog.entries
            if e.inchikey == "VGGSQFUCUMXWEO-UHFFFAOYSA-N"
        ]
        assert len(partial) == 1
        assert partial[0].formula is None
        assert partial[0].name is None
        assert partial[0].inchi == "InChI=1S/C2H4/c1-2/h1-2H2"

    def test_blank_row_dropped_with_warning(self, catalog):
        # Catalog-level warning lists the dropped row index.
        assert any("dropped" in w for w in catalog.warnings)


class TestCatalogHrefIsAuditOnly:
    def test_ch4_relative_href_preserved(self, catalog):
        ch4 = next(e for e in catalog.entries if e.formula == "CH4")
        # The hyperlink is captured for debugging but never promoted.
        assert ch4.raw_href == "exp1.asp?casno=64175"
        assert ch4.trusted_property_url is None
        assert ch4.trusted_species_url is None
        # Relative ASP-looking hrefs do NOT trigger the audit warning.
        assert all("audit" not in w for w in ch4.warnings)

    def test_broken_absolute_href_produces_audit_warning(self, catalog):
        co = next(e for e in catalog.entries if e.formula == "CO")
        assert co.raw_href == "https://example.invalid/not-a-cccbdb-page"
        assert co.trusted_property_url is None
        assert co.trusted_species_url is None
        # The audit warning fires for non-relative URLs.
        assert any("not trusted as data URL" in w for w in co.warnings)

    def test_no_entry_has_a_trusted_url_set(self, catalog):
        """Hard contract: Phase 3b parser never populates trusted URLs."""

        for entry in catalog.entries:
            assert entry.trusted_property_url is None
            assert entry.trusted_species_url is None

    def test_raw_href_is_never_used_as_source_url(self, catalog):
        """Catalog-level source URL must remain the page itself, never
        any of the row-level hrefs."""

        assert catalog.source_metadata.source_url == CATALOG_URL
        for entry in catalog.entries:
            if entry.raw_href is not None:
                assert entry.raw_href != CATALOG_URL


class TestCatalogProvenance:
    def test_source_metadata(self, catalog):
        meta = catalog.source_metadata
        assert meta.source == SOURCE_NAME
        assert meta.source_release == SOURCE_RELEASE
        assert meta.source_database_doi == SOURCE_DATABASE_DOI
        assert meta.page_kind == "molecule_catalog_inchi_index"
        assert meta.parser_version == PARSER_VERSION
        assert len(meta.content_sha256) == 64


class TestCatalogParserBehavior:
    def test_empty_source_url_rejected(self):
        with pytest.raises(ValueError):
            parse_molecule_catalog_page("<html></html>", source_url="")

    def test_empty_page_produces_warning_not_crash(self):
        result = parse_molecule_catalog_page(
            "<html><body></body></html>", source_url="https://example.invalid/"
        )
        assert result.entries == []
        assert any("no catalog table" in w for w in result.warnings)

    def test_table_with_no_recognized_columns_warns(self):
        html = """
        <html><body><h1>Catalog</h1><table>
        <tr><th>Foo</th><th>Bar</th></tr>
        <tr><td>x</td><td>y</td></tr>
        </table></body></html>
        """
        result = parse_molecule_catalog_page(
            html, source_url="https://example.invalid/weird"
        )
        # No recognized columns → catalog-level warning + entries
        # dropped (no identifiers means _build_entry returns None).
        assert any("no recognized catalog columns" in w for w in result.warnings)

    def test_deterministic_content_sha256(self):
        html = (FIXTURES_DIR / "catalog_inchix.html").read_text(
            encoding="utf-8"
        )
        a = parse_molecule_catalog_page(html, source_url=CATALOG_URL)
        b = parse_molecule_catalog_page(html, source_url=CATALOG_URL)
        assert (
            a.source_metadata.content_sha256
            == b.source_metadata.content_sha256
        )


class TestFutureResolverStub:
    def test_future_resolver_raises_not_implemented(self, catalog):
        """The placeholder must not silently no-op — callers must get
        a loud signal if they accidentally rely on it."""

        with pytest.raises(NotImplementedError):
            resolve_species_data_page_from_search(catalog.entries[0])
