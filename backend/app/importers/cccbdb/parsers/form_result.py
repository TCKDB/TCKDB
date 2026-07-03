"""Parser for CCCBDB form-result pages returned via getformx.asp.

The form-result pages are per-species data views that CCCBDB
publishes only after a session-aware POST through ``getformx.asp``.
Each ``target_kind`` (``atomization_energy``, ``rotational_constant``,
…) returns a different page shape, so the parser dispatches on the
target kind and returns a uniform :class:`CCCBDBFormResultTable`.

This module supports **only** ``atomization_energy`` in Phase 6.
Other targets (geometry, vibrations, rotation) are surfaced as
"unsupported target" parse errors so the resolver still archives the
raw HTML for later parsing.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Iterable

_WS_RE = re.compile(r"\s+")
_SUB_TAGS_RE = re.compile(r"<sub>(.*?)</sub>", re.IGNORECASE | re.DOTALL)
_TAG_RE = re.compile(r"<[^>]+>")


def _clean_text(text: str) -> str:
    return _WS_RE.sub(" ", text).strip()


def _formula_from_cell(cell_html: str) -> str | None:
    """Strip HTML tags from a CCCBDB formula cell, collapsing ``<sub>``
    tags so ``H<sub>2</sub>O`` becomes ``H2O``."""

    if not cell_html:
        return None
    no_sub = _SUB_TAGS_RE.sub(r"\1", cell_html)
    no_tags = _TAG_RE.sub("", no_sub)
    return _clean_text(no_tags) or None


@dataclass
class FormResultRow:
    """One row of a parsed form-result table."""

    row_index: int
    formula: str | None = None
    name: str | None = None
    value: float | None = None
    unit: str | None = None
    uncertainty: float | None = None
    secondary_values: dict[str, float] = field(default_factory=dict)
    """
    Additional scalar values that aren't the primary ``value`` (e.g.
    the 298K column on ``ea2x.asp``). Keyed by column-header label.
    """
    raw_row: dict[str, str] = field(default_factory=dict)
    reference_label: str | None = None
    reference_comment: str | None = None
    warnings: list[str] = field(default_factory=list)


@dataclass
class CCCBDBFormResultTable:
    """Result of parsing one form-result page."""

    target_kind: str
    title: str | None
    column_names: list[str]
    raw_units: str | None
    rows: list[FormResultRow] = field(default_factory=list)
    source_url: str | None = None
    final_url: str | None = None
    content_sha256: str | None = None
    warnings: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# HTML extractor (reuses the same shape as experimental_property_table)
# ---------------------------------------------------------------------------


class _TableExtractor(HTMLParser):
    """Walks the form-result HTML and captures every ``<table>`` as
    ``(column_names, rows_as_strings, rows_as_html)``. Cells are
    captured both as cleaned text AND as raw HTML, so the formula
    column (which contains ``<sub>`` markup) can be post-processed
    without re-running an HTML parse."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tables: list[list[list[str]]] = []
        # Parallel list: same shape as ``tables``, but each cell is
        # the raw HTML between the open/close tag.
        self.tables_html: list[list[list[str]]] = []
        self.title: str | None = None
        self.h1: str | None = None
        self.units_text: str | None = None

        self._in_table = False
        self._current_table: list[list[str]] = []
        self._current_table_html: list[list[str]] = []
        self._current_row: list[str] = []
        self._current_row_html: list[str] = []
        self._in_cell = False
        self._cell_buf: list[str] = []
        self._cell_html_buf: list[str] = []
        self._capture_title = False
        self._title_buf: list[str] = []
        self._capture_h1 = False
        self._h1_buf: list[str] = []
        # Track plain text outside tables for units-text capture.
        self._free_text_buf: list[str] = []

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag == "table":
            self._in_table = True
            self._current_table = []
            self._current_table_html = []
        elif tag == "tr" and self._in_table:
            # CCCBDB HTML often omits </td> and </tr> before the next
            # <tr>. Implicit-close anything still open at the same
            # level so the previous row gets appended to the table.
            if self._in_cell or self._current_row:
                self._close_row()
            self._current_row = []
            self._current_row_html = []
        elif tag in {"td", "th"} and self._in_table:
            # Implicit close: a new <td>/<th> closes the previous one.
            if self._in_cell:
                self._close_cell()
            self._in_cell = True
            self._cell_buf = []
            self._cell_html_buf = []
        elif tag == "title" and self.title is None:
            self._capture_title = True
            self._title_buf = []
        elif tag == "h1" and self.h1 is None:
            self._capture_h1 = True
            self._h1_buf = []
        if self._in_cell:
            # Reconstruct the start tag inside the cell HTML buffer so
            # nested tags (``<sub>``) survive intact.
            attrs_s = "".join(
                f' {k}="{v}"' if v is not None else f" {k}"
                for k, v in attrs
            )
            self._cell_html_buf.append(f"<{tag}{attrs_s}>")

    def _close_cell(self) -> None:
        text = _clean_text("".join(self._cell_buf))
        self._current_row.append(text)
        self._current_row_html.append("".join(self._cell_html_buf))
        self._in_cell = False
        self._cell_buf = []
        self._cell_html_buf = []

    def _close_row(self) -> None:
        if self._in_cell:
            self._close_cell()
        if self._current_row:
            self._current_table.append(self._current_row)
            self._current_table_html.append(self._current_row_html)
        self._current_row = []
        self._current_row_html = []

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag in {"td", "th"} and self._in_cell:
            self._close_cell()
        elif tag == "tr" and self._in_table:
            self._close_row()
        elif tag == "table" and self._in_table:
            # Implicit close of any open row/cell at table end.
            self._close_row()
            if self._current_table:
                self.tables.append(self._current_table)
                self.tables_html.append(self._current_table_html)
            self._in_table = False
            self._current_table = []
            self._current_table_html = []
        elif tag == "title" and self._capture_title:
            self.title = _clean_text("".join(self._title_buf))
            self._capture_title = False
        elif tag == "h1" and self._capture_h1:
            self.h1 = _clean_text("".join(self._h1_buf))
            self._capture_h1 = False
        if self._in_cell:
            self._cell_html_buf.append(f"</{tag}>")

    def handle_data(self, data):
        if self._in_cell:
            self._cell_buf.append(data)
            self._cell_html_buf.append(data)
        elif self._capture_title:
            self._title_buf.append(data)
        elif self._capture_h1:
            self._h1_buf.append(data)
        else:
            self._free_text_buf.append(data)

    def free_text(self) -> str:
        return _clean_text(" ".join(self._free_text_buf))


def _largest_table_with_header(
    extractor: _TableExtractor, expected_headers: tuple[str, ...]
) -> tuple[list[list[str]], list[list[str]]] | None:
    """Return ``(rows_text, rows_html)`` for the first table whose
    first row contains every ``expected_headers`` label
    (case-insensitive)."""

    for table, table_html in zip(extractor.tables, extractor.tables_html, strict=False):
        if not table:
            continue
        header_lc = [c.lower() for c in table[0]]
        if all(h.lower() in header_lc for h in expected_headers):
            return table, table_html
    return None


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


SUPPORTED_TARGET_KINDS: tuple[str, ...] = ("atomization_energy",)


def parse_form_result_page(
    html: str,
    *,
    target_kind: str,
    source_url: str | None = None,
    final_url: str | None = None,
) -> CCCBDBFormResultTable:
    """Parse one CCCBDB form-result HTML page.

    :param html: Raw HTML body.
    :param target_kind: One of :data:`SUPPORTED_TARGET_KINDS`. Other
        values yield a table with a single ``unsupported target``
        warning and zero rows.
    :param source_url: Provenance only.
    :param final_url: The URL the response was served from after
        redirects (e.g. ``ea2x.asp``).
    """

    extractor = _TableExtractor()
    extractor.feed(html)
    extractor.close()

    content_sha = hashlib.sha256(html.encode("utf-8")).hexdigest()

    if target_kind not in SUPPORTED_TARGET_KINDS:
        return CCCBDBFormResultTable(
            target_kind=target_kind,
            title=extractor.title,
            column_names=[],
            raw_units=None,
            rows=[],
            source_url=source_url,
            final_url=final_url,
            content_sha256=content_sha,
            warnings=[
                f"unsupported target_kind {target_kind!r}; "
                "raw HTML archived but no rows parsed"
            ],
        )

    return _parse_atomization_energy(
        extractor, content_sha, source_url, final_url
    )


# ---------------------------------------------------------------------------
# Per-target parsers
# ---------------------------------------------------------------------------


_EA_EXPECTED_HEADERS = ("Species", "Name", "0K", "298K")
_EA_UNIT_RE = re.compile(
    r"atomization energies in\s+(kJ mol[\s\-^]*1|kj/mol|kcal/mol|hartree)",
    re.IGNORECASE,
)


def _parse_atomization_energy(
    extractor: _TableExtractor,
    content_sha: str,
    source_url: str | None,
    final_url: str | None,
) -> CCCBDBFormResultTable:
    warnings: list[str] = []
    match = _largest_table_with_header(extractor, _EA_EXPECTED_HEADERS)
    if match is None:
        warnings.append(
            "no atomization-energy table found "
            f"(expected headers: {list(_EA_EXPECTED_HEADERS)})"
        )
        return CCCBDBFormResultTable(
            target_kind="atomization_energy",
            title=extractor.title,
            column_names=[],
            raw_units=None,
            rows=[],
            source_url=source_url,
            final_url=final_url,
            content_sha256=content_sha,
            warnings=warnings,
        )

    rows_text, rows_html = match
    column_names = list(rows_text[0])
    body_text = rows_text[1:]
    body_html = rows_html[1:]

    raw_units = "kJ/mol"
    unit_match = _EA_UNIT_RE.search(extractor.free_text())
    if unit_match:
        unit_text = unit_match.group(1).lower()
        if "kj" in unit_text:
            raw_units = "kJ/mol"
        elif "kcal" in unit_text:
            raw_units = "kcal/mol"
        elif "hartree" in unit_text:
            raw_units = "hartree"

    species_idx = _ci_index(column_names, "Species")
    name_idx = _ci_index(column_names, "Name")
    val0_idx = _ci_index(column_names, "0K")
    val298_idx = _ci_index(column_names, "298K")
    unc_idx = _ci_index(column_names, "unc.")

    rows: list[FormResultRow] = []
    for i, (cells, cells_html) in enumerate(zip(body_text, body_html, strict=False)):
        row = FormResultRow(row_index=i)
        for col_i, col_name in enumerate(column_names):
            if col_i < len(cells):
                row.raw_row[col_name] = cells[col_i]
        # Formula from HTML cell (preserves H<sub>2</sub>O → H2O).
        if species_idx is not None and species_idx < len(cells_html):
            row.formula = _formula_from_cell(cells_html[species_idx])
        if name_idx is not None and name_idx < len(cells):
            row.name = cells[name_idx] or None
        val0 = _parse_float(cells, val0_idx)
        val298 = _parse_float(cells, val298_idx)
        unc = _parse_float(cells, unc_idx)
        row.value = val0
        row.unit = raw_units if val0 is not None else None
        row.uncertainty = unc
        if val298 is not None:
            row.secondary_values["298K"] = val298
        if val0 is None:
            row.warnings.append("0K column empty / non-numeric")
        rows.append(row)

    return CCCBDBFormResultTable(
        target_kind="atomization_energy",
        title=extractor.title,
        column_names=column_names,
        raw_units=raw_units,
        rows=rows,
        source_url=source_url,
        final_url=final_url,
        content_sha256=content_sha,
        warnings=warnings,
    )


def _ci_index(column_names: Iterable[str], target: str) -> int | None:
    target_lc = target.lower().strip()
    for i, name in enumerate(column_names):
        if name.lower().strip() == target_lc:
            return i
    return None


def _parse_float(row: list[str], idx: int | None) -> float | None:
    if idx is None or idx >= len(row):
        return None
    text = row[idx].strip()
    if not text or text in {"-", "—", "N/A", "n/a"}:
        return None
    try:
        return float(text.replace(",", ""))
    except ValueError:
        return None


__all__ = [
    "SUPPORTED_TARGET_KINDS",
    "CCCBDBFormResultTable",
    "FormResultRow",
    "parse_form_result_page",
]
