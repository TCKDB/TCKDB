"""Minimal parser for CCCBDB ``alldata2x.asp`` per-species pages.

Phase 5a parser policy
----------------------

The goal of this parser is **triage**, not full property extraction.
What it produces:

* the page title,
* an ordered list of ``<h1>``/``<h2>``/``<h3>`` headings,
* any ``InChI`` / ``InChIKey`` / ``SMILES`` / formula / molecule name
  the parser can identify via cheap regex on the page body.

What it deliberately does **not** do:

* parse thermochemistry, geometry, vibrational frequencies, …
  (those are later phases when we have a real fixture corpus to
  validate against),
* assume CCCBDB markup is stable beyond well-known headings,
* invent identifiers when the page does not surface them.

The raw HTML is the durable artifact: every detected field is a
bonus, never a load-bearing claim.
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
from app.importers.cccbdb.diagnostics.classifier import extract_title
from app.importers.cccbdb.models import (
    CCCBDBSpeciesAllDataRecord,
    CCCBDBSpeciesAllDataSourceMetadata,
)
from app.importers.cccbdb.normalizers import identity as id_norm

_WS_RE = re.compile(r"\s+")


def _clean_text(text: str) -> str:
    return _WS_RE.sub(" ", text).strip()


_BLOCK_TAGS = {
    "td",
    "th",
    "tr",
    "p",
    "div",
    "li",
    "h1",
    "h2",
    "h3",
    "h4",
    "br",
}


class _HeadingsAndTextExtractor(HTMLParser):
    """Capture every heading and accumulate a flat body-text buffer.

    The text buffer is used to regex-scan for InChI / InChIKey /
    SMILES / formula tokens. We emit a space between block-level
    elements so cells run together as ``"InChIKey XLYO..."`` rather
    than ``"InChIKeyXLYO..."`` — without that, the InChIKey regex's
    word boundary fails and identifier extraction degrades silently.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.headings: list[str] = []
        self.body_text_parts: list[str] = []
        self._capture_heading: str | None = None
        self._heading_buf: list[str] = []

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        tag = tag.lower()
        if tag in _BLOCK_TAGS:
            self.body_text_parts.append(" ")
        if tag in {"h1", "h2", "h3"}:
            self._capture_heading = tag
            self._heading_buf = []

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in _BLOCK_TAGS:
            self.body_text_parts.append(" ")
        if tag in {"h1", "h2", "h3"} and self._capture_heading == tag:
            text = _clean_text("".join(self._heading_buf))
            if text:
                self.headings.append(text)
            self._capture_heading = None
            self._heading_buf = []

    def handle_data(self, data: str) -> None:
        if self._capture_heading is not None:
            self._heading_buf.append(data)
        self.body_text_parts.append(data)

    def body_text(self) -> str:
        return _WS_RE.sub(" ", "".join(self.body_text_parts)).strip()


# ---------------------------------------------------------------------------
# Cheap identifier regexes
# ---------------------------------------------------------------------------

# InChI strings begin with ``InChI=`` (case-sensitive on CCCBDB). We
# stop at whitespace or HTML quotes; CCCBDB never ships embedded
# spaces inside an InChI string.
_INCHI_RE = re.compile(r"InChI=[\w\-+()=*,;:./]+", re.ASCII)

# InChIKey is 14 letters + dash + 10 letters + dash + 1 letter,
# uppercase. Anchored on word boundaries to avoid matching free text
# coincidences.
_INCHIKEY_RE = re.compile(r"\b[A-Z]{14}-[A-Z]{10}-[A-Z]\b")

# CCCBDB's formula and name often appear in obvious labelled forms
# such as ``Formula: H2O`` or ``Name: Water``. The regex is permissive
# about whitespace and casing of the label.
_LABELLED_FORMULA_RE = re.compile(
    r"Formula\s*:?\s*([A-Z][A-Za-z0-9+\-()]*)"
)
# Match ``Name`` followed by a capitalized token and optional 1-2
# lowercase continuation words ("Water", "Hydrogen diatomic",
# "Carbon monoxide"). Numerals, dashes, commas terminate — CCCBDB
# names like ``1,3,5-cyclohexatriene`` deliberately don't match here
# (the regex is for triage; downstream code reads richer data from
# headings or the raw HTML).
_LABELLED_NAME_RE = re.compile(
    r"\bName\s+([A-Z][A-Za-z]+(?:\s+[a-z][a-z]+){0,2})\b"
)
_LABELLED_SMILES_RE = re.compile(
    r"SMILES\s*:?\s*([^\s<>\"']{1,120})"
)


def parse_species_all_data_page(
    html: str,
    *,
    source_url: str,
    cas_number: str | None = None,
    source_record_key: str | None = None,
) -> CCCBDBSpeciesAllDataRecord:
    """Parse one CCCBDB ``alldata2x.asp`` per-species page (minimal).

    :param html: Raw HTML body. Trust nothing about its structure
        beyond the extractor's regex sniffs.
    :param source_url: URL the HTML came from. Required for provenance.
    :param cas_number: CAS Registry Number used to fetch the page,
        for stable provenance even if CCCBDB ever re-renders the
        URL parameters.
    :param source_record_key: Optional caller-provided dedupe key
        (e.g. species_key). Falls back to the SHA256 of the HTML.
    :raises ValueError: when ``source_url`` is empty.
    """

    if not source_url or not source_url.strip():
        raise ValueError("source_url is required for provenance")

    content_sha256 = hashlib.sha256(html.encode("utf-8")).hexdigest()
    extractor = _HeadingsAndTextExtractor()
    extractor.feed(html)
    extractor.close()

    body_text = extractor.body_text()
    warnings: list[str] = []

    title = extract_title(html)
    detected_inchi = None
    match = _INCHI_RE.search(body_text)
    if match:
        detected_inchi = id_norm.normalize_inchi(match.group(0))
    detected_inchikey = None
    match = _INCHIKEY_RE.search(body_text)
    if match:
        detected_inchikey = id_norm.normalize_inchikey(match.group(0))
    detected_formula = None
    match = _LABELLED_FORMULA_RE.search(body_text)
    if match:
        detected_formula = id_norm.normalize_formula(match.group(1))
    detected_name = None
    match = _LABELLED_NAME_RE.search(body_text)
    if match:
        detected_name = id_norm.collapse_whitespace(match.group(1))
    detected_smiles = None
    match = _LABELLED_SMILES_RE.search(body_text)
    if match:
        detected_smiles = id_norm.normalize_smiles(match.group(1))

    if not extractor.headings:
        warnings.append("no headings found on page")

    source_metadata = CCCBDBSpeciesAllDataSourceMetadata(
        source=SOURCE_NAME,  # type: ignore[arg-type]
        source_release=SOURCE_RELEASE,
        source_database_doi=SOURCE_DATABASE_DOI,
        source_url=source_url,
        source_record_key=source_record_key or content_sha256,
        page_kind="species_all_data",
        cas_number=cas_number,
        retrieved_at=None,
        content_sha256=content_sha256,
        parser_version=PARSER_VERSION,
    )

    return CCCBDBSpeciesAllDataRecord(
        title=title,
        detected_name=detected_name,
        detected_formula=detected_formula,
        detected_inchi=detected_inchi,
        detected_inchikey=detected_inchikey,
        detected_smiles=detected_smiles,
        section_headings=extractor.headings,
        source_metadata=source_metadata,
        warnings=warnings,
    )
