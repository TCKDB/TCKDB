"""Parser for CCCBDB cross-species experimental property-table pages.

These pages (``hf0kx.asp``, ``goodlistx.asp``, ``diplistx.asp``,
``expdiatomicsx.asp``, …) ship one large HTML ``<table>`` of species
rows for a single property. Unlike the per-species pages, they are
single-GET resources with no session state.

The parser is generic: it locates the largest table on the page,
extracts ``(column_names, rows-as-strings)``, and then a per-property
:class:`PropertyTableConfig` maps the raw columns onto
:class:`CCCBDBExperimentalPropertyRow` fields.

Adding a new property table only requires (a) adding a
:class:`PropertyTableConfig` entry keyed by ``property_kind`` and
(b) a :class:`CrawlTarget` in ``crawl_plan.py``. No new parser code.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Any, Iterable

from app.importers.cccbdb import (
    PARSER_VERSION,
    SOURCE_DATABASE_DOI,
    SOURCE_NAME,
    SOURCE_RELEASE,
)
from app.importers.cccbdb.models import (
    CCCBDBExperimentalPropertyRow,
    CCCBDBExperimentalPropertyTable,
    CCCBDBPropertyTableSourceMetadata,
    CCCBDBValueRef,
)
from app.importers.cccbdb.normalizers import identity as id_norm
from app.importers.cccbdb.normalizers.units import (
    UnsupportedUnitError,
    convert_to_canonical,
)


# ---------------------------------------------------------------------------
# Per-property configuration
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PropertyTableConfig:
    """How to interpret a flat CCCBDB property table.

    Column-name fields are compared case-insensitively against the
    parsed table header. A ``None`` field means "skip" / "no such
    column on this page". ``dimension`` is the unit normalizer
    dimension, or ``None`` to preserve the raw value without
    conversion.
    """

    value_column: str
    default_raw_unit: str
    dimension: str | None
    formula_column: str | None = None
    name_column: str | None = None
    state_column: str | None = None
    uncertainty_column: str | None = None
    reference_column: str | None = None
    comment_column: str | None = None
    doi_column: str | None = None


PROPERTY_CONFIGS: dict[str, PropertyTableConfig] = {
    "hf_0": PropertyTableConfig(
        value_column="Hfg 0K",
        default_raw_unit="kJ/mol",
        dimension="energy",
        formula_column="Species",
        name_column="Name",
        reference_column="Reference",
        doi_column="DOI",
    ),
    "hf_0_with_uncertainty": PropertyTableConfig(
        value_column="Enthalpy 0K",
        default_raw_unit="kJ/mol",
        dimension="energy",
        formula_column="Species",
        uncertainty_column="unc",
    ),
    "dipole": PropertyTableConfig(
        # ``tot`` is the total dipole magnitude (Debye). The
        # x/y/z components are kept in ``raw_row`` for now;
        # molecular_property_observation will surface them properly.
        value_column="tot",
        default_raw_unit="Debye",
        dimension=None,  # no Debye normalizer yet
        formula_column="Molecule",
        name_column="name",
        state_column="state",
        reference_column="squib",
        comment_column="commment",  # CCCBDB header has the typo
    ),
    "diatomic_spectroscopic": PropertyTableConfig(
        # Diatomic ωe (cm^-1). Other spectroscopic constants (ωexe,
        # Be, De, αe) ride along in ``raw_row`` and are addressed in
        # later phases.
        value_column="ωe",
        default_raw_unit="cm^-1",
        dimension="frequency",
        formula_column="Species",
        name_column="name",
        reference_column="reference",
    ),
    "polarizability_iso": PropertyTableConfig(
        # Phase 5c: isotropic polarizability (Bohr^3 on CCCBDB's
        # pollistx.asp). Sibling URL of diplistx.asp; live column
        # shape inferred from the dipole page and verified against
        # the bundled fixture. If a live fetch shows different
        # column headers, only this config needs updating — the
        # parser doesn't change.
        value_column="iso",
        default_raw_unit="Bohr^3",
        dimension=None,  # no Bohr^3 normalizer; raw value preserved
        formula_column="Molecule",
        name_column="name",
        state_column="state",
        reference_column="squib",
        comment_column="commment",
    ),
}


# ---------------------------------------------------------------------------
# Stdlib HTML extractor
# ---------------------------------------------------------------------------


_WS_RE = re.compile(r"\s+")


def _clean_text(text: str) -> str:
    return _WS_RE.sub(" ", text).strip()


class _FlatTableExtractor(HTMLParser):
    """Walks an HTML page and collects every ``<table>`` as rows of cells.

    Also captures the first non-trivial heading and any paragraph
    text that looks like a units statement (``Values in cm-1``,
    ``Enthalpies in kJ mol^-1``, ``Dipole moments in Debye`` …).
    """

    _UNIT_HINT_RE = re.compile(
        r"\b(?:in|values\s+in)\s+([A-Za-zµ⁻^\-/\s\.\d]{2,40})",
        re.IGNORECASE,
    )

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tables: list[list[list[str]]] = []
        self.title: str | None = None
        self.units_hint: str | None = None

        self._in_table = False
        self._current_table: list[list[str]] = []
        self._current_row: list[str] = []
        self._in_cell = False
        self._cell_buf: list[str] = []
        self._capture_heading: str | None = None
        self._heading_buf: list[str] = []
        self._capture_paragraph = False
        self._paragraph_buf: list[str] = []

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        tag = tag.lower()
        if tag == "table":
            self._in_table = True
            self._current_table = []
        elif tag == "tr" and self._in_table:
            self._current_row = []
        elif tag in {"td", "th"} and self._in_table:
            self._in_cell = True
            self._cell_buf = []
        elif tag in {"h1", "h2", "h3"} and self.title is None:
            self._capture_heading = tag
            self._heading_buf = []
        elif tag == "p" and self.units_hint is None:
            self._capture_paragraph = True
            self._paragraph_buf = []

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
            self._current_row.append(_clean_text("".join(self._cell_buf)))
            self._in_cell = False
            self._cell_buf = []
        elif tag in {"h1", "h2", "h3"} and self._capture_heading == tag:
            text = _clean_text("".join(self._heading_buf))
            if text and self.title is None:
                self.title = text
            self._capture_heading = None
            self._heading_buf = []
        elif tag == "p" and self._capture_paragraph:
            text = _clean_text("".join(self._paragraph_buf))
            self._capture_paragraph = False
            self._paragraph_buf = []
            if self.units_hint is None:
                match = self._UNIT_HINT_RE.search(text)
                if match:
                    self.units_hint = _clean_text(match.group(1))

    def handle_data(self, data: str) -> None:
        if self._in_cell:
            self._cell_buf.append(data)
        elif self._capture_heading is not None:
            self._heading_buf.append(data)
        elif self._capture_paragraph:
            self._paragraph_buf.append(data)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _largest_table(tables: Iterable[list[list[str]]]) -> list[list[str]] | None:
    """Return the table with the most rows (header + body)."""

    best: list[list[str]] | None = None
    best_size = 0
    for table in tables:
        if not table:
            continue
        if len(table) > best_size:
            best_size = len(table)
            best = table
    return best


def _normalize_column(name: str) -> str:
    return _WS_RE.sub("", name).strip().lower()


def _column_index(
    column_names: list[str], target: str | None
) -> int | None:
    """Case- and whitespace-insensitive column lookup. Returns ``None``
    when ``target`` is ``None`` or no column matches."""

    if target is None:
        return None
    target_norm = _normalize_column(target)
    for i, name in enumerate(column_names):
        if _normalize_column(name) == target_norm:
            return i
    return None


def _parse_float_or_none(value: str | None) -> float | None:
    if value is None:
        return None
    cleaned = value.strip()
    if not cleaned or cleaned in {"-", "—", "N/A", "n/a"}:
        return None
    cleaned = cleaned.replace(",", "")
    try:
        return float(cleaned)
    except ValueError:
        return None


def _cell_or_none(row: list[str], idx: int | None) -> str | None:
    if idx is None or idx >= len(row):
        return None
    cell = row[idx].strip()
    return cell or None


def _make_value_ref(
    raw_reference: str | None, raw_comment: str | None, doi: str | None
) -> CCCBDBValueRef | None:
    if not any([raw_reference, raw_comment, doi]):
        return None
    return CCCBDBValueRef(
        reference_label=raw_reference,
        reference_comment=raw_comment,
        raw_reference_text=raw_reference or raw_comment,
        parsed_literature_hint=doi,
    )


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def parse_experimental_property_table_page(
    html: str,
    *,
    property_kind: str,
    source_url: str,
    source_record_key: str | None = None,
) -> CCCBDBExperimentalPropertyTable:
    """Parse one CCCBDB cross-species property-table page.

    :param html: Raw HTML of the page.
    :param property_kind: Machine token naming the property (must
        appear in :data:`PROPERTY_CONFIGS`).
    :param source_url: URL the HTML was fetched from. Provenance only.
    :param source_record_key: Optional caller-provided dedupe key.
    :returns: A :class:`CCCBDBExperimentalPropertyTable` populated with
        identifier and value/unit fields per
        :data:`PROPERTY_CONFIGS`. Unrecognized columns ride along in
        ``rows[i].raw_row``; per-cell anomalies land in
        ``rows[i].warnings``.
    :raises ValueError: If ``source_url`` is empty or
        ``property_kind`` has no config registered.
    """

    if not source_url or not source_url.strip():
        raise ValueError("source_url is required for provenance")
    config = PROPERTY_CONFIGS.get(property_kind)
    if config is None:
        raise ValueError(
            f"unknown property_kind {property_kind!r}; "
            "register a PropertyTableConfig first"
        )

    content_sha256 = hashlib.sha256(html.encode("utf-8")).hexdigest()
    extractor = _FlatTableExtractor()
    extractor.feed(html)
    extractor.close()

    warnings: list[str] = []
    table = _largest_table(extractor.tables)

    if table is None or len(table) < 2:
        warnings.append("no data table found on page")
        column_names: list[str] = []
        body: list[list[str]] = []
    else:
        column_names = list(table[0])
        body = list(table[1:])

    value_idx = _column_index(column_names, config.value_column)
    if column_names and value_idx is None:
        warnings.append(
            f"value column {config.value_column!r} not found "
            f"in header {column_names!r}"
        )

    uncertainty_idx = _column_index(column_names, config.uncertainty_column)
    reference_idx = _column_index(column_names, config.reference_column)
    comment_idx = _column_index(column_names, config.comment_column)
    doi_idx = _column_index(column_names, config.doi_column)
    formula_idx = _column_index(column_names, config.formula_column)
    name_idx = _column_index(column_names, config.name_column)
    state_idx = _column_index(column_names, config.state_column)

    raw_units = extractor.units_hint or config.default_raw_unit
    canonical_unit: str | None
    if config.dimension is None:
        canonical_unit = raw_units  # no normalizer for this dimension yet
    else:
        try:
            _, canonical_unit = convert_to_canonical(
                1.0, raw_units, config.dimension  # type: ignore[arg-type]
            )
        except UnsupportedUnitError:
            canonical_unit = None
            warnings.append(
                f"could not resolve canonical unit for raw {raw_units!r} "
                f"(dimension {config.dimension!r})"
            )

    rows: list[CCCBDBExperimentalPropertyRow] = []
    for i, raw_row in enumerate(body):
        rows.append(
            _build_row(
                i,
                raw_row,
                column_names=column_names,
                config=config,
                raw_units=raw_units,
                value_idx=value_idx,
                uncertainty_idx=uncertainty_idx,
                reference_idx=reference_idx,
                comment_idx=comment_idx,
                doi_idx=doi_idx,
                formula_idx=formula_idx,
                name_idx=name_idx,
                state_idx=state_idx,
            )
        )

    source_metadata = CCCBDBPropertyTableSourceMetadata(
        source=SOURCE_NAME,  # type: ignore[arg-type]
        source_release=SOURCE_RELEASE,
        source_database_doi=SOURCE_DATABASE_DOI,
        source_url=source_url,
        source_record_key=source_record_key or content_sha256,
        page_kind="experimental_property_table",
        property_kind=property_kind,
        retrieved_at=None,
        content_sha256=content_sha256,
        parser_version=PARSER_VERSION,
    )

    return CCCBDBExperimentalPropertyTable(
        property_kind=property_kind,
        title=extractor.title,
        raw_units=raw_units,
        canonical_unit=canonical_unit,
        column_names=column_names,
        rows=rows,
        source_metadata=source_metadata,
        warnings=warnings,
    )


def _build_row(
    index: int,
    raw_row: list[str],
    *,
    column_names: list[str],
    config: PropertyTableConfig,
    raw_units: str,
    value_idx: int | None,
    uncertainty_idx: int | None,
    reference_idx: int | None,
    comment_idx: int | None,
    doi_idx: int | None,
    formula_idx: int | None,
    name_idx: int | None,
    state_idx: int | None,
) -> CCCBDBExperimentalPropertyRow:
    row_warnings: list[str] = []
    raw: dict[str, str] = {}
    for col_i, col_name in enumerate(column_names):
        if col_i < len(raw_row):
            raw[col_name] = raw_row[col_i]

    raw_value = _cell_or_none(raw_row, value_idx)
    value = _parse_float_or_none(raw_value)
    if value is None and raw_value is not None:
        row_warnings.append(f"non-numeric value cell {raw_value!r}")

    raw_uncertainty = _cell_or_none(raw_row, uncertainty_idx)
    uncertainty = _parse_float_or_none(raw_uncertainty)

    normalized_value: float | None = None
    normalized_uncertainty: float | None = None
    normalized_unit: str | None = None
    if config.dimension is None:
        # Preserve raw value as-is; no unit normalizer applies.
        normalized_value = value
        normalized_unit = raw_units
        normalized_uncertainty = uncertainty
    elif value is not None:
        try:
            normalized_value, normalized_unit = convert_to_canonical(
                value, raw_units, config.dimension  # type: ignore[arg-type]
            )
            if uncertainty is not None:
                normalized_uncertainty, _ = convert_to_canonical(
                    uncertainty, raw_units, config.dimension  # type: ignore[arg-type]
                )
        except UnsupportedUnitError as exc:
            row_warnings.append(
                f"unsupported unit {exc.raw_units!r} for value row"
            )

    return CCCBDBExperimentalPropertyRow(
        row_index=index,
        formula=id_norm.normalize_formula(_cell_or_none(raw_row, formula_idx)),
        name=id_norm.collapse_whitespace(_cell_or_none(raw_row, name_idx)),
        state_label_raw=id_norm.collapse_whitespace(
            _cell_or_none(raw_row, state_idx)
        ),
        value=value,
        unit=raw_units,
        normalized_value=normalized_value,
        normalized_unit=normalized_unit,
        uncertainty=uncertainty,
        normalized_uncertainty=normalized_uncertainty,
        reference=_make_value_ref(
            _cell_or_none(raw_row, reference_idx),
            _cell_or_none(raw_row, comment_idx),
            _cell_or_none(raw_row, doi_idx),
        ),
        raw_row=raw,
        warnings=row_warnings,
    )
