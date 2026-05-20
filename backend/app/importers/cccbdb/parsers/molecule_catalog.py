"""Parser for CCCBDB's ``inchix.asp`` molecule catalog page.

CRITICAL POLICY (also documented in
``backend/app/importers/cccbdb/models.py``):

* The INChI index is **catalog-only**. Its outbound hyperlinks are
  preserved as ``raw_href`` for audit/debugging but are NEVER trusted
  as data-page URLs.
* ``CCCBDBCatalogEntry.trusted_property_url`` and
  ``.trusted_species_url`` are always ``None`` in Phase 3b. A future
  search/form resolver may populate them; this parser does not.
* The catalog must never be joined to property-table rows by formula
  alone as a hard identity match. Use
  :func:`app.importers.cccbdb.enrichment.propose_catalog_matches`
  for scored candidate matching, never silent enrichment.

The parser is tolerant by design: missing columns, broken hrefs,
blank rows, repeated formulas, and extra columns must all produce
warnings rather than crash. The page family (CCCBDB classic ASP) ships
old, occasionally-malformed HTML; we treat parser robustness as a
feature, not a polish item.
"""

from __future__ import annotations

import hashlib
import re
from html.parser import HTMLParser

from app.importers.cccbdb import (
    PARSER_VERSION,
    SOURCE_DATABASE_DOI,
    SOURCE_NAME,
    SOURCE_RELEASE,
)
from app.importers.cccbdb.models import (
    CCCBDBCatalogEntry,
    CCCBDBCatalogSourceMetadata,
    CCCBDBMoleculeCatalog,
)
from app.importers.cccbdb.normalizers import identity as id_norm

_WS_RE = re.compile(r"\s+")


def _clean_text(text: str) -> str:
    return _WS_RE.sub(" ", text).strip()


def _normalize_column(name: str) -> str:
    return _WS_RE.sub("", name).strip().lower()


# Each "logical" identifier may show up under several column-header
# spellings. The keys are the column targets; the values are the
# header names we accept (compared after _normalize_column).
_COLUMN_ALIASES: dict[str, set[str]] = {
    "formula": {"formula", "species", "molecule"},
    "name": {"name", "molecule name", "species name"},
    "inchi": {"inchi"},
    "inchikey": {"inchikey", "inchi key"},
    "smiles": {"smiles"},
    "cas_number": {"cas", "casno", "cas number", "cas no", "cas registry"},
}


class _CatalogTableExtractor(HTMLParser):
    """Walks one ``inchix.asp`` page and collects every ``<table>``
    along with the first ``<a href>`` seen in each cell.

    Each cell is captured as ``{"text": str, "href": str | None}``.
    Rows that contain a hyperlink retain the link's href for
    downstream audit-only inspection.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tables: list[list[list[dict[str, str | None]]]] = []
        self.title: str | None = None

        self._in_table = False
        self._current_table: list[list[dict[str, str | None]]] = []
        self._current_row: list[dict[str, str | None]] = []
        self._in_cell = False
        self._cell_text_buf: list[str] = []
        self._cell_href: str | None = None
        self._capture_heading: str | None = None
        self._heading_buf: list[str] = []
        # Anchor state. We only keep the first anchor per cell so
        # cells with multiple links collapse to one href for
        # debugging — that's a deliberate simplification.
        self._in_anchor = False

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        tag = tag.lower()
        attr_map = {k.lower(): (v or "") for k, v in attrs}
        if tag == "table":
            self._in_table = True
            self._current_table = []
        elif tag == "tr" and self._in_table:
            self._current_row = []
        elif tag in {"td", "th"} and self._in_table:
            self._in_cell = True
            self._cell_text_buf = []
            self._cell_href = None
        elif tag == "a" and self._in_cell and not self._in_anchor:
            self._in_anchor = True
            href = attr_map.get("href")
            if href and self._cell_href is None:
                self._cell_href = href.strip() or None
        elif tag in {"h1", "h2", "h3"} and self.title is None:
            self._capture_heading = tag
            self._heading_buf = []

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag == "table" and self._in_table:
            if self._current_table:
                self.tables.append(self._current_table)
            self._in_table = False
            self._current_table = []
        elif tag == "tr" and self._in_table:
            if self._current_row:
                self._current_table.append(self._current_row)
            self._current_row = []
        elif tag in {"td", "th"} and self._in_cell:
            self._current_row.append(
                {
                    "text": _clean_text("".join(self._cell_text_buf)),
                    "href": self._cell_href,
                }
            )
            self._in_cell = False
            self._cell_text_buf = []
            self._cell_href = None
        elif tag == "a" and self._in_anchor:
            self._in_anchor = False
        elif tag in {"h1", "h2", "h3"} and self._capture_heading == tag:
            text = _clean_text("".join(self._heading_buf))
            if text and self.title is None:
                self.title = text
            self._capture_heading = None
            self._heading_buf = []

    def handle_data(self, data: str) -> None:
        if self._in_cell:
            self._cell_text_buf.append(data)
        elif self._capture_heading is not None:
            self._heading_buf.append(data)


def _largest_table(
    tables: list[list[list[dict[str, str | None]]]],
) -> list[list[dict[str, str | None]]] | None:
    best = None
    best_size = 0
    for table in tables:
        if len(table) > best_size:
            best_size = len(table)
            best = table
    return best


def _resolve_column_indices(header_cells: list[dict[str, str | None]]):
    column_names = [str(c.get("text") or "") for c in header_cells]
    indices: dict[str, int] = {}
    normalized = [_normalize_column(n) for n in column_names]
    for target, aliases in _COLUMN_ALIASES.items():
        aliases_norm = {_normalize_column(a) for a in aliases}
        for i, name in enumerate(normalized):
            if name in aliases_norm:
                indices[target] = i
                break
    return column_names, indices


def parse_molecule_catalog_page(
    html: str,
    *,
    source_url: str,
    source_record_key: str | None = None,
) -> CCCBDBMoleculeCatalog:
    """Parse one CCCBDB ``inchix.asp`` snapshot.

    :param html: Raw HTML of the catalog page.
    :param source_url: URL the HTML was fetched from. Provenance only.
    :param source_record_key: Optional caller-provided dedupe key.
    :returns: A :class:`CCCBDBMoleculeCatalog` populated from whatever
        columns the parser recognized. Unrecognized columns ride
        along inside per-row ``raw_text`` only when populated; the
        catalog never invents identifiers.
    :raises ValueError: If ``source_url`` is empty.
    """

    if not source_url or not source_url.strip():
        raise ValueError("source_url is required for provenance")

    content_sha256 = hashlib.sha256(html.encode("utf-8")).hexdigest()
    extractor = _CatalogTableExtractor()
    extractor.feed(html)
    extractor.close()

    warnings: list[str] = []
    table = _largest_table(extractor.tables)
    if table is None or len(table) < 2:
        warnings.append("no catalog table found on page")
        column_names: list[str] = []
        body: list[list[dict[str, str | None]]] = []
        indices: dict[str, int] = {}
    else:
        column_names, indices = _resolve_column_indices(table[0])
        body = table[1:]
        if not indices:
            warnings.append(
                f"no recognized catalog columns; header={column_names!r}"
            )

    entries: list[CCCBDBCatalogEntry] = []
    for i, row in enumerate(body):
        entry = _build_entry(i, row, indices)
        if entry is not None:
            entries.append(entry)
        else:
            warnings.append(f"row {i}: dropped (no recognizable identifiers)")

    source_metadata = CCCBDBCatalogSourceMetadata(
        source=SOURCE_NAME,  # type: ignore[arg-type]
        source_release=SOURCE_RELEASE,
        source_database_doi=SOURCE_DATABASE_DOI,
        source_url=source_url,
        source_record_key=source_record_key or content_sha256,
        page_kind="molecule_catalog_inchi_index",
        retrieved_at=None,
        content_sha256=content_sha256,
        parser_version=PARSER_VERSION,
    )

    return CCCBDBMoleculeCatalog(
        title=extractor.title,
        column_names=column_names,
        entries=entries,
        source_metadata=source_metadata,
        warnings=warnings,
    )


def _cell_text(
    row: list[dict[str, str | None]], idx: int | None
) -> str | None:
    if idx is None or idx >= len(row):
        return None
    text = row[idx].get("text") or ""
    text = text.strip()
    return text or None


def _row_href(row: list[dict[str, str | None]]) -> str | None:
    for cell in row:
        href = cell.get("href")
        if href:
            return href
    return None


def _row_raw_text(row: list[dict[str, str | None]]) -> str | None:
    parts = [str(cell.get("text") or "").strip() for cell in row]
    joined = " | ".join(p for p in parts if p)
    return joined or None


def _build_entry(
    index: int,
    row: list[dict[str, str | None]],
    indices: dict[str, int],
) -> CCCBDBCatalogEntry | None:
    row_warnings: list[str] = []

    formula = id_norm.normalize_formula(
        _cell_text(row, indices.get("formula"))
    )
    name = id_norm.collapse_whitespace(_cell_text(row, indices.get("name")))
    inchi = id_norm.normalize_inchi(_cell_text(row, indices.get("inchi")))
    inchikey = id_norm.normalize_inchikey(
        _cell_text(row, indices.get("inchikey"))
    )
    smiles = id_norm.normalize_smiles(_cell_text(row, indices.get("smiles")))
    cas_number = id_norm.normalize_cas(
        _cell_text(row, indices.get("cas_number"))
    )

    # Drop genuinely empty rows. We keep rows where any single
    # identifier landed; the prompt is explicit that "missing fields
    # produce None" is acceptable.
    if not any([formula, name, inchi, inchikey, smiles, cas_number]):
        return None

    raw_href = _row_href(row)
    if raw_href is not None and not _looks_like_relative_url(raw_href):
        row_warnings.append(
            f"raw_href {raw_href!r} does not look like a relative CCCBDB URL "
            "(audit only — not trusted as data URL)"
        )

    return CCCBDBCatalogEntry(
        catalog_index=index,
        formula=formula,
        name=name,
        inchi=inchi,
        inchikey=inchikey,
        smiles=smiles,
        cas_number=cas_number,
        raw_text=_row_raw_text(row),
        raw_href=raw_href,
        # trusted_property_url and trusted_species_url are *deliberately*
        # left None — see CCCBDBCatalogEntry docstring + module banner.
        trusted_property_url=None,
        trusted_species_url=None,
        warnings=row_warnings,
    )


def _looks_like_relative_url(href: str) -> bool:
    """CCCBDB internal links are relative ASP paths like
    ``exp1.asp?casno=...``. This is an audit-only sniff test; the
    parser never *uses* the href for fetching either way."""

    href = href.strip()
    if not href:
        return False
    if href.startswith(("http://", "https://", "//")):
        return False
    # Plausibly-relative ASP/HTML paths.
    return bool(re.match(r"[\w\-/]+\.(asp|html?)(\?.*)?$", href, re.I))


# ---------------------------------------------------------------------------
# Future resolver placeholder
# ---------------------------------------------------------------------------


def resolve_species_data_page_from_search(
    catalog_entry: CCCBDBCatalogEntry,
) -> None:
    """Placeholder for a future CCCBDB search/form resolver.

    Do not use raw hrefs from ``inchix.asp`` as trusted URLs. A future
    implementation may use CCCBDB's search form/session flow, known
    CAS-number routes, or manually curated URL mappings. This stub
    raises so callers get a loud signal if they assume the resolver
    exists.
    """

    raise NotImplementedError(
        "Phase 3b ships catalog-only. The search/form resolver is "
        "intentionally deferred."
    )
