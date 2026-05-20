"""Parser for CCCBDB experimental species pages.

The parser walks the HTML with :mod:`html.parser` (stdlib) and groups
content into ``<section>`` blocks. Each section becomes a dict of
heading → list of tables, where a table is a list of rows of cells.
The dispatch layer then matches by section id, heading text, or row
label to populate the typed intermediate models.

The parser is deliberately tolerant of missing fields: an absent
section, an absent row, or a blank cell becomes a ``None`` /
``[]`` / warning. Only the *source metadata* (URL, content hash) is
treated as required.
"""

from __future__ import annotations

import hashlib
import re
from html.parser import HTMLParser
from typing import Iterable

from app.importers.cccbdb import (
    PARSER_VERSION,
    SOURCE_DATABASE_DOI,
    SOURCE_NAME,
    SOURCE_RELEASE,
)
from app.importers.cccbdb.models import (
    CCCBDBExperimentalSpeciesRecord,
    CCCBDBFrequencyMode,
    CCCBDBGeometryAtom,
    CCCBDBGeometryRecord,
    CCCBDBRotationalConstants,
    CCCBDBSourceMetadata,
    CCCBDBSpeciesIdentity,
    CCCBDBStatmechRecord,
    CCCBDBThermoRecord,
    CCCBDBThermoValue,
    CCCBDBValueRef,
)
from app.importers.cccbdb.normalizers import identity as id_norm
from app.importers.cccbdb.normalizers.units import (
    UnsupportedUnitError,
    convert_to_canonical,
)

# ---------------------------------------------------------------------------
# Low-level HTML block extraction
# ---------------------------------------------------------------------------


_HEADING_TAGS = {"h1", "h2", "h3", "h4"}


class _SectionedBlockExtractor(HTMLParser):
    """Flatten HTML into ``{section_id -> {heading -> [tables]}}``.

    Tables outside any ``<section>`` land under section id ``""``.
    Tables that precede every heading inside a section land under
    heading key ``""``.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.sections: dict[str, dict[str, list[list[list[str]]]]] = {}
        self._section_stack: list[str] = []
        self._current_heading: dict[str, str] = {}
        self._heading_capture: str | None = None  # tag like "h2"
        self._heading_buf: list[str] = []
        # Table state.
        self._in_table = False
        self._current_table: list[list[str]] = []
        self._current_row: list[str] = []
        self._in_cell = False
        self._cell_buf: list[str] = []

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def section(self, section_id: str) -> dict[str, list[list[list[str]]]]:
        return self.sections.get(section_id, {})

    # ------------------------------------------------------------------
    # HTMLParser hooks
    # ------------------------------------------------------------------

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        tag = tag.lower()
        attr_map = {k.lower(): (v or "") for k, v in attrs}
        if tag == "section":
            section_id = attr_map.get("id", "")
            self._section_stack.append(section_id)
            self.sections.setdefault(section_id, {})
            self._current_heading[section_id] = ""
        elif tag in _HEADING_TAGS:
            self._heading_capture = tag
            self._heading_buf = []
        elif tag == "table":
            self._in_table = True
            self._current_table = []
        elif tag == "tr" and self._in_table:
            self._current_row = []
        elif tag in {"td", "th"} and self._in_table:
            self._in_cell = True
            self._cell_buf = []

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag == "section":
            if self._section_stack:
                self._section_stack.pop()
        elif tag in _HEADING_TAGS and self._heading_capture == tag:
            heading_text = _clean_text("".join(self._heading_buf))
            self._heading_capture = None
            self._heading_buf = []
            section_id = self._current_section_id()
            self._current_heading[section_id] = heading_text
            self.sections.setdefault(section_id, {}).setdefault(
                heading_text, []
            )
        elif tag == "table" and self._in_table:
            section_id = self._current_section_id()
            heading = self._current_heading.get(section_id, "")
            self.sections.setdefault(section_id, {}).setdefault(
                heading, []
            ).append(self._current_table)
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

    def handle_data(self, data: str) -> None:
        if self._in_cell:
            self._cell_buf.append(data)
        elif self._heading_capture is not None:
            self._heading_buf.append(data)

    # ------------------------------------------------------------------

    def _current_section_id(self) -> str:
        return self._section_stack[-1] if self._section_stack else ""


_WS_RE = re.compile(r"\s+")


def _clean_text(text: str) -> str:
    return _WS_RE.sub(" ", text).strip()


# ---------------------------------------------------------------------------
# Identity dispatch
# ---------------------------------------------------------------------------


_IDENTITY_KEY_MAP = {
    "name": "name",
    "preferred name": "name",
    "other names": "other_names",
    "synonyms": "other_names",
    "formula": "formula",
    "molecular formula": "formula",
    "cas": "cas",
    "cas number": "cas",
    "cas registry": "cas",
    "inchi": "inchi",
    "inchikey": "inchikey",
    "inchi key": "inchikey",
    "smiles": "smiles",
    "charge": "charge",
    "multiplicity": "multiplicity",
    "state": "state_label",
    "electronic state": "state_label",
    "conformation": "state_label",
}


def _parse_identity(
    section: dict[str, list[list[list[str]]]],
    warnings: list[str],
    raw_dump: dict[str, object],
) -> CCCBDBSpeciesIdentity:
    raw: dict[str, str] = {}
    for tables in section.values():
        for table in tables:
            for row in table:
                if len(row) < 2:
                    continue
                label = row[0].strip().lower().rstrip(":")
                value = row[1]
                key = _IDENTITY_KEY_MAP.get(label)
                if key is None:
                    warnings.append(
                        f"identity: unrecognized row label {row[0]!r}"
                    )
                    continue
                raw[key] = value
    raw_dump["raw_rows"] = dict(raw)

    other_names_raw = raw.get("other_names")
    other_names = (
        [
            id_norm.collapse_whitespace(part) or ""
            for part in re.split(r"[;,]\s*", other_names_raw)
            if part.strip()
        ]
        if other_names_raw
        else []
    )

    return CCCBDBSpeciesIdentity(
        name=id_norm.collapse_whitespace(raw.get("name")),
        other_names=[n for n in other_names if n],
        formula=id_norm.normalize_formula(raw.get("formula")),
        cas=id_norm.normalize_cas(raw.get("cas")),
        inchi=id_norm.normalize_inchi(raw.get("inchi")),
        inchikey=id_norm.normalize_inchikey(raw.get("inchikey")),
        smiles=id_norm.normalize_smiles(raw.get("smiles")),
        charge=id_norm.parse_int_or_none(raw.get("charge")),
        multiplicity=id_norm.parse_int_or_none(raw.get("multiplicity")),
        state_label=id_norm.parse_state_label(raw.get("state_label")),
    )


# ---------------------------------------------------------------------------
# Thermo dispatch
# ---------------------------------------------------------------------------


_THERMO_PROPERTY_MAP: dict[str, tuple[str, float | None]] = {
    # display label (lowercased, whitespace-collapsed) -> (kind, T_K)
    "hf(298.15 k)": ("hf_298", 298.15),
    "hf 298.15 k": ("hf_298", 298.15),
    "enthalpy of formation (298.15 k)": ("hf_298", 298.15),
    "hf(0 k)": ("hf_0", 0.0),
    "hf 0 k": ("hf_0", 0.0),
    "enthalpy of formation (0 k)": ("hf_0", 0.0),
    "s(298.15 k)": ("s_298", 298.15),
    "entropy (298.15 k)": ("s_298", 298.15),
    "cp(298.15 k)": ("cp_298", 298.15),
    "heat capacity (298.15 k)": ("cp_298", 298.15),
    "h(298.15 k) - h(0 k)": ("h_298_minus_h_0", 298.15),
    "integrated heat capacity (0 k - 298.15 k)": ("h_298_minus_h_0", 298.15),
}


def _parse_thermo(
    section: dict[str, list[list[list[str]]]],
    warnings: list[str],
    raw_dump: dict[str, object],
) -> CCCBDBThermoRecord:
    values: list[CCCBDBThermoValue] = []
    raw_rows: list[dict[str, str]] = []
    for heading, tables in section.items():
        for table in tables:
            if not table:
                continue
            header, *body = table
            header_norm = [c.strip().lower() for c in header]
            col_idx = _resolve_thermo_columns(header_norm)
            if col_idx is None:
                warnings.append(
                    f"thermo: could not resolve header {header!r} "
                    f"under heading {heading!r}"
                )
                continue
            for row in body:
                if len(row) < 3:
                    warnings.append(f"thermo: short row {row!r}")
                    continue
                raw_property = row[col_idx["property"]]
                raw_value = row[col_idx["value"]]
                raw_units = row[col_idx["units"]]
                raw_uncertainty = (
                    row[col_idx["uncertainty"]]
                    if col_idx.get("uncertainty") is not None
                    and len(row) > col_idx["uncertainty"]
                    else None
                )
                raw_reference = (
                    row[col_idx["reference"]]
                    if col_idx.get("reference") is not None
                    and len(row) > col_idx["reference"]
                    else None
                )
                raw_rows.append(
                    {
                        "property": raw_property,
                        "value": raw_value,
                        "units": raw_units,
                        "uncertainty": raw_uncertainty or "",
                        "reference": raw_reference or "",
                    }
                )

                prop_key = _normalize_property_label(raw_property)
                kind_temp = _THERMO_PROPERTY_MAP.get(prop_key)
                if kind_temp is None:
                    warnings.append(
                        f"thermo: unknown property label {raw_property!r}"
                    )
                    continue
                value_f = _parse_float_or_none(raw_value)
                if value_f is None:
                    warnings.append(
                        f"thermo: non-numeric value {raw_value!r} for "
                        f"{raw_property!r}"
                    )
                    continue
                dim = (
                    "entropy_or_heat_capacity"
                    if kind_temp[0] in {"s_298", "cp_298"}
                    else "energy"
                )
                try:
                    canonical_value, canonical_units = convert_to_canonical(
                        value_f, raw_units, dim
                    )
                except UnsupportedUnitError as exc:
                    warnings.append(
                        f"thermo: unsupported unit {exc.raw_units!r} for "
                        f"{raw_property!r}"
                    )
                    continue
                uncertainty_f = _parse_float_or_none(raw_uncertainty)
                if uncertainty_f is not None and dim == "energy":
                    uncertainty_f, _ = convert_to_canonical(
                        uncertainty_f, raw_units, dim
                    )
                elif uncertainty_f is not None and dim == "entropy_or_heat_capacity":
                    uncertainty_f, _ = convert_to_canonical(
                        uncertainty_f, raw_units, dim
                    )
                values.append(
                    CCCBDBThermoValue(
                        property_kind=kind_temp[0],
                        raw_property=raw_property,
                        value=canonical_value,
                        canonical_units=canonical_units,
                        raw_value=raw_value,
                        raw_units=raw_units,
                        temperature_k=kind_temp[1],
                        uncertainty=uncertainty_f,
                        reference=_make_value_ref(raw_reference),
                    )
                )
    raw_dump["raw_rows"] = raw_rows
    return CCCBDBThermoRecord(values=values)


def _resolve_thermo_columns(header_norm: list[str]) -> dict[str, int] | None:
    """Find the column indices for property/value/units/(uncertainty)/(reference)."""

    aliases = {
        "property": {"property", "quantity"},
        "value": {"value"},
        "units": {"units", "unit"},
        "uncertainty": {"uncertainty", "unc", "error"},
        "reference": {"reference", "ref", "source"},
    }
    idx: dict[str, int] = {}
    for col, names in aliases.items():
        for i, name in enumerate(header_norm):
            if name in names:
                idx[col] = i
                break
    if not all(k in idx for k in ("property", "value", "units")):
        return None
    return idx


def _normalize_property_label(label: str) -> str:
    cleaned = _clean_text(label).lower()
    cleaned = cleaned.replace("–", "-")  # en-dash -> hyphen
    return cleaned


# ---------------------------------------------------------------------------
# Statmech dispatch
# ---------------------------------------------------------------------------


_STATMECH_SUMMARY_KEYS = {
    "point group": "point_group",
    "symmetry number": "symmetry_number",
    "zpe": "zpe",
    "zero-point energy": "zpe",
}


def _parse_statmech(
    section: dict[str, list[list[list[str]]]],
    warnings: list[str],
    raw_dump: dict[str, object],
) -> CCCBDBStatmechRecord:
    point_group: str | None = None
    symmetry_number: int | None = None
    zpe_kj_mol: float | None = None
    frequencies: list[CCCBDBFrequencyMode] = []
    rotational: CCCBDBRotationalConstants | None = None
    raw_blocks: dict[str, object] = {}

    for heading, tables in section.items():
        heading_norm = heading.lower()
        if any(tok in heading_norm for tok in ("rotational",)):
            rotational = _parse_rotational(tables, warnings)
            raw_blocks["rotational_raw"] = [t for t in tables]
            continue
        if any(
            tok in heading_norm
            for tok in ("vibration", "frequencies", "fundamentals")
        ):
            frequencies = _parse_frequencies(tables, warnings)
            raw_blocks["frequencies_raw"] = [t for t in tables]
            continue
        # Otherwise treat as a summary table.
        for table in tables:
            for row in table:
                if len(row) < 2:
                    continue
                label = row[0].strip().lower().rstrip(":")
                target = _STATMECH_SUMMARY_KEYS.get(label)
                if target == "point_group":
                    point_group = id_norm.collapse_whitespace(row[1])
                elif target == "symmetry_number":
                    symmetry_number = id_norm.parse_int_or_none(row[1])
                elif target == "zpe":
                    raw_value = row[1]
                    raw_units = row[2] if len(row) > 2 else "kJ/mol"
                    val = _parse_float_or_none(raw_value)
                    if val is None:
                        warnings.append(
                            f"statmech: non-numeric ZPE value {raw_value!r}"
                        )
                        continue
                    try:
                        zpe_kj_mol, _ = convert_to_canonical(
                            val, raw_units, "energy"
                        )
                    except UnsupportedUnitError as exc:
                        warnings.append(
                            f"statmech: unsupported ZPE unit "
                            f"{exc.raw_units!r}"
                        )
                elif label not in {"property", ""}:
                    warnings.append(
                        f"statmech: unknown summary label {row[0]!r}"
                    )

    raw_dump.update(raw_blocks)
    return CCCBDBStatmechRecord(
        point_group=point_group,
        symmetry_number=symmetry_number,
        frequencies=frequencies,
        rotational_constants=rotational,
        zpe_kj_mol=zpe_kj_mol,
    )


def _parse_rotational(
    tables: Iterable[list[list[str]]], warnings: list[str]
) -> CCCBDBRotationalConstants | None:
    for table in tables:
        if not table:
            continue
        header, *body = table
        if not body:
            continue
        row = body[0]
        header_norm = [c.strip().upper() for c in header]
        units_idx = next(
            (i for i, h in enumerate(header_norm) if h in {"UNITS", "UNIT"}),
            None,
        )
        raw_units = row[units_idx] if units_idx is not None and len(row) > units_idx else "GHz"
        try:
            a = _convert_rot(row, header_norm, "A", raw_units)
            b = _convert_rot(row, header_norm, "B", raw_units)
            c = _convert_rot(row, header_norm, "C", raw_units)
        except UnsupportedUnitError as exc:
            warnings.append(
                f"statmech.rotational: unsupported unit {exc.raw_units!r}"
            )
            return None
        return CCCBDBRotationalConstants(
            a_ghz=a,
            b_ghz=b,
            c_ghz=c,
            raw_values=[row[i] for i, h in enumerate(header_norm) if h in {"A", "B", "C"} and i < len(row)],
            raw_units=raw_units,
        )
    return None


def _convert_rot(
    row: list[str], header_norm: list[str], label: str, raw_units: str
) -> float | None:
    try:
        idx = header_norm.index(label)
    except ValueError:
        return None
    if idx >= len(row):
        return None
    val = _parse_float_or_none(row[idx])
    if val is None:
        return None
    converted, _ = convert_to_canonical(val, raw_units, "rotational_constant")
    return converted


def _parse_frequencies(
    tables: Iterable[list[list[str]]], warnings: list[str]
) -> list[CCCBDBFrequencyMode]:
    out: list[CCCBDBFrequencyMode] = []
    for table in tables:
        if not table:
            continue
        header, *body = table
        header_norm = [c.strip().lower() for c in header]
        col = {
            name: i
            for i, name in enumerate(header_norm)
            if name in {"mode", "symmetry", "frequency", "units", "reference"}
        }
        units_idx = col.get("units")
        for row in body:
            if "frequency" not in col or col["frequency"] >= len(row):
                warnings.append(f"frequencies: missing frequency column in {row!r}")
                continue
            raw_value = row[col["frequency"]]
            raw_units = (
                row[units_idx]
                if units_idx is not None and units_idx < len(row)
                else "cm^-1"
            )
            val = _parse_float_or_none(raw_value)
            if val is None:
                warnings.append(f"frequencies: non-numeric {raw_value!r}")
                continue
            try:
                canonical_value, _ = convert_to_canonical(
                    val, raw_units, "frequency"
                )
            except UnsupportedUnitError as exc:
                warnings.append(
                    f"frequencies: unsupported unit {exc.raw_units!r}"
                )
                continue
            mode_index = (
                id_norm.parse_int_or_none(row[col["mode"]])
                if "mode" in col and col["mode"] < len(row)
                else None
            )
            symmetry = (
                id_norm.collapse_whitespace(row[col["symmetry"]])
                if "symmetry" in col and col["symmetry"] < len(row)
                else None
            )
            raw_ref = (
                row[col["reference"]]
                if "reference" in col and col["reference"] < len(row)
                else None
            )
            out.append(
                CCCBDBFrequencyMode(
                    mode_index=mode_index,
                    frequency_cm1=canonical_value,
                    symmetry_label=symmetry,
                    raw_value=raw_value,
                    raw_units=raw_units,
                    reference=_make_value_ref(raw_ref),
                )
            )
    return out


# ---------------------------------------------------------------------------
# Geometry dispatch
# ---------------------------------------------------------------------------


def _parse_geometry(
    section: dict[str, list[list[list[str]]]],
    warnings: list[str],
    raw_dump: dict[str, object],
) -> CCCBDBGeometryRecord | None:
    for tables in section.values():
        for table in tables:
            if not table:
                continue
            header, *body = table
            header_norm = [c.strip().lower() for c in header]
            cols = {
                name: i
                for i, name in enumerate(header_norm)
                if name in {"atom", "element", "x", "y", "z", "units"}
            }
            element_key = "atom" if "atom" in cols else "element" if "element" in cols else None
            if element_key is None or not all(k in cols for k in ("x", "y", "z")):
                continue
            units_idx = cols.get("units")
            atoms: list[CCCBDBGeometryAtom] = []
            raw_units: str | None = None
            for row in body:
                if max(cols.values()) >= len(row):
                    warnings.append(f"geometry: short row {row!r}")
                    continue
                element = id_norm.collapse_whitespace(row[cols[element_key]])
                x = _parse_float_or_none(row[cols["x"]])
                y = _parse_float_or_none(row[cols["y"]])
                z = _parse_float_or_none(row[cols["z"]])
                if not element or x is None or y is None or z is None:
                    warnings.append(f"geometry: incomplete atom row {row!r}")
                    continue
                row_units = (
                    row[units_idx]
                    if units_idx is not None and units_idx < len(row)
                    else "angstrom"
                )
                try:
                    x_c, _ = convert_to_canonical(x, row_units, "length")
                    y_c, _ = convert_to_canonical(y, row_units, "length")
                    z_c, _ = convert_to_canonical(z, row_units, "length")
                except UnsupportedUnitError as exc:
                    warnings.append(
                        f"geometry: unsupported unit {exc.raw_units!r}"
                    )
                    continue
                raw_units = row_units
                atoms.append(
                    CCCBDBGeometryAtom(
                        element=element,
                        x_angstrom=x_c,
                        y_angstrom=y_c,
                        z_angstrom=z_c,
                    )
                )
            if not atoms:
                return None
            raw_dump["raw_atom_count"] = len(atoms)
            return CCCBDBGeometryRecord(atoms=atoms, raw_units=raw_units)
    return None


# ---------------------------------------------------------------------------
# Misc helpers
# ---------------------------------------------------------------------------


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


def _make_value_ref(raw_reference: str | None) -> CCCBDBValueRef | None:
    cleaned = id_norm.collapse_whitespace(raw_reference)
    if not cleaned or cleaned == "-":
        return None
    return CCCBDBValueRef(
        reference_label=cleaned,
        raw_reference_text=cleaned,
    )


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def parse_experimental_species_page(
    html: str,
    *,
    source_url: str,
    source_record_key: str | None = None,
) -> CCCBDBExperimentalSpeciesRecord:
    """Parse one CCCBDB experimental species page.

    :param html: Raw HTML content of the page (as a string).
    :param source_url: The URL the HTML was fetched from. Used as
        provenance, not for live fetching.
    :param source_record_key: Optional caller-provided dedupe key
        (e.g. ``"H2O / experimental"``). When omitted, the SHA256 of
        the HTML is used so re-parsing the same content yields the
        same record key.
    :returns: A :class:`CCCBDBExperimentalSpeciesRecord` populated from
        what the parser could find on the page. Missing sections become
        ``None`` / empty collections and produce parser ``warnings``
        rather than raising.
    :raises ValueError: If ``source_url`` is empty (the parser refuses
        to produce a record with no provenance).
    """

    if not source_url or not source_url.strip():
        raise ValueError("source_url is required for provenance")

    content_sha256 = hashlib.sha256(html.encode("utf-8")).hexdigest()
    extractor = _SectionedBlockExtractor()
    extractor.feed(html)
    extractor.close()

    warnings: list[str] = []
    raw_sections: dict[str, object] = {}

    raw_identity: dict[str, object] = {}
    identity = _parse_identity(
        extractor.section("identity"), warnings, raw_identity
    )
    raw_sections["identity"] = raw_identity

    raw_thermo: dict[str, object] = {}
    thermo = _parse_thermo(
        extractor.section("thermo"), warnings, raw_thermo
    )
    raw_sections["thermo"] = raw_thermo

    raw_statmech: dict[str, object] = {}
    statmech = _parse_statmech(
        extractor.section("statmech"), warnings, raw_statmech
    )
    raw_sections["statmech"] = raw_statmech

    raw_geometry: dict[str, object] = {}
    geometry = _parse_geometry(
        extractor.section("geometry"), warnings, raw_geometry
    )
    if geometry is not None:
        raw_sections["geometry"] = raw_geometry

    source_metadata = CCCBDBSourceMetadata(
        source=SOURCE_NAME,  # type: ignore[arg-type]
        source_release=SOURCE_RELEASE,
        source_database_doi=SOURCE_DATABASE_DOI,
        source_url=source_url,
        source_record_key=source_record_key or content_sha256,
        page_kind="experimental_species",
        retrieved_at=None,
        content_sha256=content_sha256,
        parser_version=PARSER_VERSION,
    )

    if (
        identity.name is None
        and identity.formula is None
        and identity.inchi is None
    ):
        warnings.append(
            "identity: no name, formula, or InChI found on page"
        )

    return CCCBDBExperimentalSpeciesRecord(
        identity=identity,
        thermo=thermo,
        statmech=statmech,
        geometry=geometry,
        source_metadata=source_metadata,
        raw_sections=raw_sections,
        warnings=warnings,
    )
