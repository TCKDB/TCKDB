"""Parser for CCCBDB's species-selection page (``choosex.asp``).

When the user submits a formula that matches multiple isomers / charge
states / conformers, CCCBDB returns a "Choose which species" page
with one row per candidate and a ``<form action="fixchoicex.asp">``
holding ``<input name="choice" value="<casno>">`` checkboxes.

This parser extracts every candidate row along with the form fields
needed to POST a selection back to ``fixchoicex.asp``. It does NOT
attempt to filter or rank candidates — the matching/selection logic
lives in :mod:`app.importers.cccbdb.form_resolver` so the matcher
can be exercised against arbitrary candidate fixtures in tests.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urljoin


_WS_RE = re.compile(r"\s+")
_SUB_TAGS_RE = re.compile(r"<sub>(.*?)</sub>", re.IGNORECASE | re.DOTALL)
_TAG_RE = re.compile(r"<[^>]+>")
_CAS_RE = re.compile(r"^\d{1,7}-?\d{0,2}-?\d?$")


def _clean_text(text: str) -> str:
    return _WS_RE.sub(" ", text).strip()


def _formula_from_html(html_cell: str) -> str | None:
    """Strip HTML tags from a CCCBDB formula cell, collapsing
    ``<sub>...</sub>`` so ``CH<sub>3</sub>OCH<sub>3</sub>`` →
    ``CH3OCH3``."""

    if not html_cell:
        return None
    no_sub = _SUB_TAGS_RE.sub(r"\1", html_cell)
    no_tags = _TAG_RE.sub("", no_sub)
    return _clean_text(no_tags) or None


@dataclass(frozen=True)
class CCCBDBSelectionCandidate:
    """One candidate row on a ``choosex.asp`` page."""

    formula: str | None
    name: str | None
    cas_number: str | None
    charge: str | None
    state: str | None
    config: str | None
    form_field_name: str
    form_field_value: str
    inchi: str | None = None
    inchikey: str | None = None
    raw_row: dict[str, str] = field(default_factory=dict)

    def form_fields(self) -> dict[str, str]:
        """Return the form-field key/value the matcher should POST to
        ``fixchoicex.asp`` (plus the submit button name CCCBDB expects)."""

        return {
            self.form_field_name: self.form_field_value,
            "submitselect": "Select",
        }


@dataclass
class CCCBDBSpeciesSelectionPage:
    """Result of parsing one ``choosex.asp`` page."""

    title: str | None
    heading: str | None
    form_action_url: str | None
    form_method: str
    candidates: list[CCCBDBSelectionCandidate] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


class _ChoosexParser(HTMLParser):
    """Walk a ``choosex.asp`` page and collect candidate rows."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title: str | None = None
        self.heading: str | None = None
        self.form_action: str | None = None
        self.form_method: str = "POST"
        # We capture every <tr> in the selection form's table as a
        # (cells_text, cells_html, choice_input) triple.
        self.candidate_rows: list[
            tuple[list[str], list[str], dict[str, str] | None]
        ] = []

        self._in_form = False
        self._in_table = False
        self._in_cell = False
        self._cell_buf: list[str] = []
        self._cell_html_buf: list[str] = []
        self._current_row: list[str] = []
        self._current_row_html: list[str] = []
        self._current_choice: dict[str, str] | None = None
        # The header row tells us nothing useful (it's COLSPAN-spanned);
        # we filter it out post hoc by row length / cell content.
        self._capture_title = False
        self._title_buf: list[str] = []
        self._capture_h1 = False
        self._h1_buf: list[str] = []

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        attrs_d = {k.lower(): (v or "") for k, v in attrs}
        if tag == "form":
            action = attrs_d.get("action", "")
            if "fixchoicex.asp" in action.lower() \
                    or "choosex.asp" in action.lower() \
                    or attrs_d.get("id", "").lower() == "form1":
                # The relevant selection form. Capture its action.
                if "fixchoicex" in action.lower():
                    self.form_action = action
                    self._in_form = True
                    self.form_method = (
                        attrs_d.get("method", "POST").upper() or "POST"
                    )
                else:
                    # A different form on the page (e.g. the
                    # Clear_All form posting to choosex.asp). Skip.
                    pass
        elif tag == "table" and self._in_form:
            self._in_table = True
        elif tag == "tr" and self._in_table:
            self._close_row_if_open()
            self._current_row = []
            self._current_row_html = []
            self._current_choice = None
        elif tag in {"td", "th"} and self._in_table:
            if self._in_cell:
                self._close_cell()
            self._in_cell = True
            self._cell_buf = []
            self._cell_html_buf = []
        elif tag == "input" and self._in_table:
            name = attrs_d.get("name", "")
            value = attrs_d.get("value", "")
            input_type = attrs_d.get("type", "").lower()
            if name.lower() == "choice" and input_type in {"checkbox", "radio"}:
                self._current_choice = {"name": name, "value": value}
        elif tag == "title" and self.title is None:
            self._capture_title = True
            self._title_buf = []
        elif tag == "h1" and self.heading is None:
            self._capture_h1 = True
            self._h1_buf = []
        if self._in_cell:
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

    def _close_row_if_open(self) -> None:
        if self._in_cell:
            self._close_cell()
        if self._current_row:
            self.candidate_rows.append(
                (
                    list(self._current_row),
                    list(self._current_row_html),
                    dict(self._current_choice) if self._current_choice else None,
                )
            )
        self._current_row = []
        self._current_row_html = []
        self._current_choice = None

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag in {"td", "th"} and self._in_cell:
            self._close_cell()
        elif tag == "tr" and self._in_table:
            self._close_row_if_open()
        elif tag == "table" and self._in_table:
            self._close_row_if_open()
            self._in_table = False
        elif tag == "form" and self._in_form:
            self._in_form = False
        elif tag == "title" and self._capture_title:
            self.title = _clean_text("".join(self._title_buf))
            self._capture_title = False
        elif tag == "h1" and self._capture_h1:
            self.heading = _clean_text("".join(self._h1_buf))
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


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


# Expected column layout for choosex.asp (live-verified May 2026):
#   [0] row number
#   [1] checkbox (no visible text; the choice input)
#   [2] formula (HTML with <sub>)
#   [3] charge
#   [4] state
#   [5] config
#   [6] name
#   [7] CAS number
#   [8] sketch (image)
#
# A heuristic is fine here: if a row has fewer than ~8 cells, it is
# probably the table header / submit-button row and gets dropped.
_MIN_CANDIDATE_CELL_COUNT = 7


def parse_species_selection_page(
    html: str,
    *,
    base_url: str,
) -> CCCBDBSpeciesSelectionPage:
    """Parse one ``choosex.asp`` page.

    :param html: Raw HTML body.
    :param base_url: The URL the page was served from; used to
        resolve the form action (e.g. ``fixchoicex.asp``) into an
        absolute URL.
    """

    parser = _ChoosexParser()
    parser.feed(html)
    parser.close()

    warnings: list[str] = []
    form_action_abs: str | None = None
    if parser.form_action is not None:
        form_action_abs = urljoin(base_url, parser.form_action)
    else:
        warnings.append(
            "no form posting to fixchoicex.asp found on selection page"
        )

    candidates: list[CCCBDBSelectionCandidate] = []
    for cells, cells_html, choice in parser.candidate_rows:
        if choice is None:
            # Header / button row — no choice control means no candidate.
            continue
        if len(cells) < _MIN_CANDIDATE_CELL_COUNT:
            continue
        candidate = _candidate_from_row(cells, cells_html, choice)
        if candidate is None:
            continue
        candidates.append(candidate)

    if not candidates:
        warnings.append(
            "no candidates extracted from selection page; "
            "the layout may have changed"
        )

    return CCCBDBSpeciesSelectionPage(
        title=parser.title,
        heading=parser.heading,
        form_action_url=form_action_abs,
        form_method=parser.form_method,
        candidates=candidates,
        warnings=warnings,
    )


def _candidate_from_row(
    cells: list[str],
    cells_html: list[str],
    choice: dict[str, str],
) -> CCCBDBSelectionCandidate | None:
    """Map one ``<tr>`` of choosex.asp into a candidate. Returns
    ``None`` when essential fields (formula or choice value) are
    missing — those rows are not viable selections."""

    # Layout indices (May 2026 live shape). We're defensive: missing
    # tail cells fall back to None.
    def get(i: int) -> str | None:
        return cells[i].strip() or None if i < len(cells) else None

    def get_html(i: int) -> str | None:
        return cells_html[i] if i < len(cells_html) else None

    raw_row = {
        f"col_{i}": cells[i] for i in range(len(cells))
    }
    # Try the canonical layout first.
    formula_html = get_html(2)
    formula = _formula_from_html(formula_html) if formula_html else None
    charge = get(3)
    state = get(4)
    config = get(5)
    name = get(6)
    cas_number = _normalize_cas(get(7))
    # If the formula slot didn't yield a formula, retry by scanning
    # cells for a single HTML cell that contains ``<sub>``.
    if formula is None:
        for i, html_cell in enumerate(cells_html):
            if "<sub" in html_cell.lower():
                formula = _formula_from_html(html_cell)
                break

    field_name = choice.get("name") or "choice"
    field_value = (choice.get("value") or "").strip()
    if not field_value:
        return None

    return CCCBDBSelectionCandidate(
        formula=formula,
        name=name,
        cas_number=cas_number,
        charge=charge,
        state=state,
        config=config,
        form_field_name=field_name,
        form_field_value=field_value,
        raw_row=raw_row,
    )


def _normalize_cas(text: str | None) -> str | None:
    """Return a CAS number stripped of whitespace, or ``None`` when the
    cell is blank / clearly not a CAS. We accept both fully-hyphenated
    (``64-17-5``) and the digit-only form CCCBDB sometimes prints
    (``64175``) — the matcher canonicalizes both via :func:`canonicalize_cas`.
    """

    if not text:
        return None
    cleaned = text.strip()
    if not cleaned:
        return None
    if _CAS_RE.match(cleaned.replace(" ", "")):
        return cleaned
    return cleaned


def canonicalize_cas(cas: str | None) -> str | None:
    """Strip CAS hyphens / whitespace for equality comparison.

    ``"64-17-5"`` and ``"64175"`` both normalize to ``"64175"``.
    Returns ``None`` for empty / non-digit input.
    """

    if not cas:
        return None
    digits = re.sub(r"[^0-9]", "", cas)
    return digits or None


_ATOM_TOKEN_RE = re.compile(r"([A-Z][a-z]?)(\d*)")


def structural_to_hill_formula(formula: str | None) -> str | None:
    """Convert a CCCBDB structural formula like ``CH3CH2OH`` to the
    Hill-system molecular formula ``C2H6O``.

    CCCBDB's ``choosex.asp`` displays structural formulas in the
    candidate table, but queue records typically carry molecular
    formulas (the same string the maintainer POSTed to ``getformx.asp``).
    This helper lets the selection matcher compare them.

    Supports the unparenthesized case (the only one we've observed on
    live ``choosex.asp`` pages): a sequence of ``<atom><count?>``
    tokens. Atom = uppercase letter followed by an optional lowercase
    letter (e.g. ``Cl``). Returns ``None`` for unparseable input or
    when parentheses / charges / dots are present — in those cases
    the caller should fall back to literal string comparison.
    """

    if not formula:
        return None
    text = formula.strip()
    if not text:
        return None
    if any(c in text for c in "()[]{}+-·.•"):
        return None
    counts: dict[str, int] = {}
    pos = 0
    n = len(text)
    while pos < n:
        ch = text[pos]
        if not ch.isalpha() or not ch.isupper():
            return None
        match = _ATOM_TOKEN_RE.match(text, pos)
        if match is None:
            return None
        atom = match.group(1)
        count_str = match.group(2)
        count = int(count_str) if count_str else 1
        counts[atom] = counts.get(atom, 0) + count
        pos = match.end()
    if not counts:
        return None

    # Hill ordering: C first, H second, then alphabetical (when C is
    # absent, everything is alphabetical including H).
    parts: list[str] = []
    if "C" in counts:
        parts.append(_atom_str("C", counts.pop("C")))
        if "H" in counts:
            parts.append(_atom_str("H", counts.pop("H")))
    for atom in sorted(counts.keys()):
        parts.append(_atom_str(atom, counts[atom]))
    return "".join(parts) or None


def _atom_str(atom: str, count: int) -> str:
    return atom if count == 1 else f"{atom}{count}"


__all__ = [
    "CCCBDBSelectionCandidate",
    "CCCBDBSpeciesSelectionPage",
    "canonicalize_cas",
    "parse_species_selection_page",
    "structural_to_hill_formula",
]
