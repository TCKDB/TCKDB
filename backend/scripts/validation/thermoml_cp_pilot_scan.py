#!/usr/bin/env python
"""Scan the pinned NIST ThermoML bulk archive for the Phase C ideal-gas Cp pilot.

WP0 of the Phase C implementation plan (``docs/research/tckdb-phase-c-implementation-plan.md``,
section "C0 -- preconditions and the pilot species scan") is a read-only measurement, not a
guess: find every single-component ``PureOrMixtureData`` block in the archive whose property is
"Molar heat capacity at constant pressure, J/K/mol", whose phase is "Ideal gas" or "Gas", whose
method is experimental (an ``eMethodName`` element -- not a ``Prediction`` and not an
``sMethodName``-only or ``CriticalEvaluation`` block), and whose compound's standard InChIKey is
one of the TCKDB playground species that already hold computed thermo. The output picks the one
article the ThermoML importer (C2/C3) is built and demonstrated against.

Nothing here writes to a database, touches the Pi, or fetches anything beyond the two URLs
verified once, by hand, before this script is invoked (see
``docs/validation/thermoml_cp_pilot_scan.md``). The archive itself is a local file this script
only reads; it never downloads it.

How it reads the archive
-------------------------
The bulk file (``ThermoML.v2020-09-30.tgz``, NIST ark:/88434/mds2-2422) is a gzipped tar of
11,923 article pairs, ``<doi-prefix>/<doi-suffix>.xml`` and the JSON twin of the same record. This
script iterates tar members with :mod:`tarfile` and never extracts to disk; only the stdlib
:mod:`xml.etree.ElementTree` is used to parse a member's bytes in memory, and only members whose
raw bytes contain the target property string are parsed at all (the archive has no per-property
index, so a cheap substring pre-filter is what keeps a full scan inside a couple of minutes). JSON
members are never parsed -- their SHA-256 is recorded for the manifest, nothing else.

Digest gate
-----------
The archive's SHA-256 is compared against the constant this module pins
(``PINNED_ARCHIVE_SHA256``, sourced from the NIST records API entry for the dataset, cross-checked
against the published ``.tgz.sha256`` sidecar) before a single tar member is read. A mismatch exits
``2`` and reads nothing -- the same discipline the CCCBDB and ThermoML importers use for their own
allowlisted URLs. ``THERMOML_PILOT_SCAN_ARCHIVE_SHA256`` overrides the pinned digest, for tests
only; it is not read for any other purpose and is not a supported way to point this script at a
different real archive.

ThermoML structure this script depends on (schema 4.0, ``https://trc.nist.gov/ThermoML.xsd``):
a ``DataReport`` holds ``Compound`` elements (``RegNum/nOrgNum`` -> ``sStandardInChIKey``,
``sCommonName``) and ``PureOrMixtureData`` blocks. A block is single-component when it has exactly
one ``Component``. Each ``Property`` carries ``Property-MethodID/PropertyGroup/*/ePropName`` and,
as a ``choice``, one of ``eMethodName`` (an enumerated, genuinely experimental method label),
``sMethodName`` (free-text, also experimental but not what this scan's brief asks for),
``CriticalEvaluation`` or ``Prediction``. Phase comes from ``PropPhaseID/ePropPhase`` when present,
else the block's ``PhaseID/ePhase``. ``NumValues`` entries carry the temperature (via a
``Variable``/``VariableValue`` pair whose ``VariableType`` is ``eTemperature``) and the Cp value
(``PropertyValue`` keyed by ``nPropNumber``), each optionally wrapped in ``CombinedUncertainty``
(``nCombStdUncertValue``/``nCombExpandUncertValue``) and/or ``PropUncertainty``
(``nStdUncertValue``/``nExpandUncertValue``); coverage factor and level of confidence are recorded
at the ``Property``-level uncertainty definitions the per-value entries reference by assessment
number, so this script reports their presence anywhere in the matched property, not per point.

Usage::

    python backend/scripts/validation/thermoml_cp_pilot_scan.py \\
        --archive /path/to/ThermoML.v2020-09-30.tgz \\
        --inchikeys /path/to/playground_inchikeys.txt \\
        --out /path/to/thermoml_cp_pilot_scan.json

``--inchikeys`` is a text file, one species per line, standard InChIKey first, the rest of the
line an optional free-text label (name, SMILES, both) this script never parses further. Blank
lines and lines starting with ``#`` are skipped.

Method precedence: a ``Property`` carries exactly one of ``eMethodName``, ``sMethodName``,
``CriticalEvaluation`` or ``Prediction`` per the schema's ``choice`` group, so they should not
co-occur -- but ``_property_method`` checks in that fixed order (``eMethodName`` first, then
``sMethodName``, then ``Prediction``, then ``CriticalEvaluation``), so if a non-conformant
document ever carried more than one, ``eMethodName`` wins and the property is treated as
``"experimental_enum"`` regardless of a co-occurring ``Prediction``.

Exit status: ``3`` when ``--archive`` does not point at an existing file (nothing is read);
``2`` when the archive's SHA-256 does not match the pinned digest (nothing is read); ``1`` when
the archive is valid but no playground species from ``--inchikeys`` was hit by any qualifying
property (the JSON and Markdown output still report the most-covered compounds in the archive
generally, as a fallback for an author decision -- this script does not choose a fallback species
itself); ``0`` when at least one playground species was hit.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tarfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

EXIT_OK = 0
EXIT_NO_PLAYGROUND_CANDIDATE = 1
EXIT_DIGEST_MISMATCH = 2
EXIT_ARCHIVE_NOT_FOUND = 3

#: NIST records API entry for ark:/88434/mds2-2422, component ``ThermoML.v2020-09-30.tgz``,
#: cross-checked against ``ThermoML.v2020-09-30.tgz.sha256`` -- both read 2026-09-19.
PINNED_ARCHIVE_SHA256 = "231161b5e443dc1ae0e5da8429d86a88474cb722016e5b790817bb31c58d7ec2"
PINNED_ARCHIVE_SIZE = 189433115

#: Test-only override for the pinned digest so a mismatch can be exercised against a small
#: synthetic archive without touching the real 189 MB file. Never used to point this script at a
#: different real archive.
_DIGEST_ENV_OVERRIDE = "THERMOML_PILOT_SCAN_ARCHIVE_SHA256"

TARGET_PROPERTY = "Molar heat capacity at constant pressure, J/K/mol"
TARGET_PHASES = ("Ideal gas", "Gas")

THERMOML_NS = "http://www.iupac.org/namespaces/ThermoML"


def _tag(local: str) -> str:
    return f"{{{THERMOML_NS}}}{local}"


UNCERTAINTY_VALUE_TAGS = (
    "nStdUncertValue",
    "nExpandUncertValue",
    "nCombStdUncertValue",
    "nCombExpandUncertValue",
)


def pinned_archive_sha256() -> str:
    """The digest a run should compare against -- the env override in tests, else the pin."""

    import os

    return os.environ.get(_DIGEST_ENV_OVERRIDE, PINNED_ARCHIVE_SHA256)


def sha256_file(path: Path, chunk_size: int = 1 << 20) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@dataclass(frozen=True)
class PlaygroundSpecies:
    inchikey: str
    label: str


def load_inchikeys(path: Path) -> list[PlaygroundSpecies]:
    species: list[PlaygroundSpecies] = []
    seen: set[str] = set()
    for raw_line in path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split(None, 1)
        key = parts[0].strip().upper()
        label = parts[1].strip() if len(parts) > 1 else ""
        if key in seen:
            continue
        seen.add(key)
        species.append(PlaygroundSpecies(inchikey=key, label=label))
    return species


@dataclass
class Compound:
    org_num: int
    inchikey: str | None
    has_standard_inchi: bool
    names: list[str] = field(default_factory=list)

    @property
    def name(self) -> str:
        return self.names[0] if self.names else "(unnamed)"


@dataclass
class Candidate:
    doi: str
    journal: str
    year: str
    compound_name: str
    inchikey: str
    n_values: int
    temperature_min_k: float | None
    temperature_max_k: float | None
    pressure_kind: str  # "constraint" | "variable" | "none"
    pressures_kpa: list[float]  # distinct values, sorted
    pressure_min_kpa: float | None
    pressure_max_kpa: float | None
    pressure_n_distinct: int
    e_phase: str
    e_method_name: str
    origin: str  # "experimental_enum" (eMethodName) | "experimental_free" (sMethodName)
    uncertainty_elements: list[str]
    has_coverage_factor: bool
    has_level_of_confidence: bool
    xml_member: str
    json_member: str
    xml_sha256: str
    json_sha256: str | None
    is_playground: bool = False

    def to_json(self) -> dict:
        return {
            "doi": self.doi,
            "journal": self.journal,
            "year": self.year,
            "compound_name": self.compound_name,
            "inchikey": self.inchikey,
            "n_values": self.n_values,
            "temperature_range_k": [self.temperature_min_k, self.temperature_max_k],
            "pressure_kind": self.pressure_kind,
            "pressures_kpa": self.pressures_kpa,
            "pressure_min_kpa": self.pressure_min_kpa,
            "pressure_max_kpa": self.pressure_max_kpa,
            "pressure_n_distinct": self.pressure_n_distinct,
            "e_phase": self.e_phase,
            "e_method_name": self.e_method_name,
            "origin": self.origin,
            "uncertainty_elements_present": self.uncertainty_elements,
            "has_coverage_factor": self.has_coverage_factor,
            "has_level_of_confidence": self.has_level_of_confidence,
            "xml_member": self.xml_member,
            "json_member": self.json_member,
            "xml_sha256": self.xml_sha256,
            "json_sha256": self.json_sha256,
        }


def _child_text(elem: ET.Element, local: str) -> str | None:
    found = elem.find(_tag(local))
    return found.text.strip() if found is not None and found.text else None


def _parse_compounds(root: ET.Element) -> dict[int, Compound]:
    compounds: dict[int, Compound] = {}
    for compound_elem in root.findall(_tag("Compound")):
        reg = compound_elem.find(_tag("RegNum"))
        if reg is None:
            continue
        org_num_text = _child_text(reg, "nOrgNum")
        if org_num_text is None:
            continue
        org_num = int(org_num_text)
        inchikey = _child_text(compound_elem, "sStandardInChIKey")
        has_standard_inchi = _child_text(compound_elem, "sStandardInChI") is not None
        names = [e.text.strip() for e in compound_elem.findall(_tag("sCommonName")) if e.text and e.text.strip()]
        compounds[org_num] = Compound(
            org_num=org_num, inchikey=inchikey, has_standard_inchi=has_standard_inchi, names=names
        )
    return compounds


def _parse_citation(root: ET.Element) -> tuple[str, str, str]:
    citation = root.find(_tag("Citation"))
    if citation is None:
        return "", "", ""
    doi = _child_text(citation, "sDOI") or ""
    journal = _child_text(citation, "sPubName") or ""
    year = _child_text(citation, "yrPubYr") or ""
    return doi, journal, year


def _property_method(property_elem: ET.Element) -> tuple[str | None, str | None, str | None]:
    """Return (ePropName, origin, method_text) for one ``Property`` element.

    ``origin`` is one of ``"experimental_enum"`` (an ``eMethodName``), ``"experimental_free"``
    (an ``sMethodName`` only), ``"critical_evaluation"`` or ``"prediction"``, or ``None`` when the
    property carries none of the four (should not happen for a schema-valid document).

    Precedence: the schema's ``choice`` group means a conformant document carries exactly one of
    the four, but this function checks in a fixed order regardless -- ``eMethodName`` first, then
    ``sMethodName``, then ``Prediction``, then ``CriticalEvaluation`` -- so a non-conformant
    document that somehow carried more than one (e.g. an ``eMethodName`` alongside a
    ``Prediction``) would be read as ``"experimental_enum"``; ``eMethodName`` always wins.
    """

    method_id = property_elem.find(_tag("Property-MethodID"))
    if method_id is None:
        return None, None, None
    group = method_id.find(_tag("PropertyGroup"))
    if group is None:
        return None, None, None
    # PropertyGroup has exactly one child, the property-type element (e.g.
    # HeatCapacityAndDerivedProp); its own children are ePropName plus the method choice.
    type_elem = next(iter(group), None)
    if type_elem is None:
        return None, None, None
    prop_name = _child_text(type_elem, "ePropName")
    e_method = _child_text(type_elem, "eMethodName")
    if e_method is not None:
        return prop_name, "experimental_enum", e_method
    s_method = _child_text(type_elem, "sMethodName")
    if s_method is not None:
        return prop_name, "experimental_free", s_method
    if type_elem.find(_tag("Prediction")) is not None:
        return prop_name, "prediction", None
    if type_elem.find(_tag("CriticalEvaluation")) is not None:
        return prop_name, "critical_evaluation", None
    return prop_name, None, None


def _property_phase(property_elem: ET.Element, block_elem: ET.Element) -> str | None:
    phase_id = property_elem.find(_tag("PropPhaseID"))
    if phase_id is not None:
        phase = _child_text(phase_id, "ePropPhase")
        if phase is not None:
            return phase
    block_phase_id = block_elem.find(_tag("PhaseID"))
    if block_phase_id is not None:
        return _child_text(block_phase_id, "ePhase")
    return None


def _temperature_var_numbers(block_elem: ET.Element) -> set[int]:
    numbers: set[int] = set()
    for variable in block_elem.findall(_tag("Variable")):
        var_id = variable.find(_tag("VariableID"))
        if var_id is None:
            continue
        var_type = var_id.find(_tag("VariableType"))
        if var_type is None:
            continue
        if var_type.find(_tag("eTemperature")) is not None:
            num_text = _child_text(variable, "nVarNumber")
            if num_text is not None:
                numbers.add(int(num_text))
    return numbers


def _pressure_constraints_kpa(block_elem: ET.Element) -> list[float]:
    """Block-level fixed pressure(s), held constant for every point via a ``Constraint``."""

    pressures: list[float] = []
    for constraint in block_elem.findall(_tag("Constraint")):
        constraint_id = constraint.find(_tag("ConstraintID"))
        if constraint_id is None:
            continue
        constraint_type = constraint_id.find(_tag("ConstraintType"))
        if constraint_type is None:
            continue
        if constraint_type.find(_tag("ePressure")) is not None:
            value_text = _child_text(constraint, "nConstraintValue")
            if value_text is not None:
                pressures.append(float(value_text))
    return pressures


def _pressure_var_numbers(block_elem: ET.Element) -> set[int]:
    """``nVarNumber``s of any block ``Variable`` whose ``VariableType`` is ``ePressure``.

    Unlike a ``Constraint`` (one fixed value for the whole block), a pressure ``Variable`` carries
    a per-point value in each ``NumValues``' ``VariableValue`` entries -- exactly like the
    temperature variable. A block may report pressure either way, or not at all.
    """

    numbers: set[int] = set()
    for variable in block_elem.findall(_tag("Variable")):
        var_id = variable.find(_tag("VariableID"))
        if var_id is None:
            continue
        var_type = var_id.find(_tag("VariableType"))
        if var_type is None:
            continue
        if var_type.find(_tag("ePressure")) is not None:
            num_text = _child_text(variable, "nVarNumber")
            if num_text is not None:
                numbers.add(int(num_text))
    return numbers


def _uncertainty_flags(property_value_elem: ET.Element) -> set[str]:
    present: set[str] = set()
    for tag in UNCERTAINTY_VALUE_TAGS:
        if property_value_elem.find(f".//{_tag(tag)}") is not None:
            present.add(tag)
    return present


def _property_level_uncertainty_meta(property_elem: ET.Element) -> tuple[bool, bool]:
    """Whether the Property's uncertainty *definitions* carry a coverage factor / confidence."""

    has_coverage = False
    has_confidence = False
    for combined in property_elem.findall(_tag("CombinedUncertainty")):
        if combined.find(_tag("nCombCoverageFactor")) is not None:
            has_coverage = True
        if combined.find(_tag("nCombUncertLevOfConfid")) is not None:
            has_confidence = True
    for prop_uncert in property_elem.findall(_tag("PropUncertainty")):
        if prop_uncert.find(_tag("nCoverageFactor")) is not None:
            has_coverage = True
        if prop_uncert.find(_tag("nUncertLevOfConfid")) is not None:
            has_confidence = True
    return has_coverage, has_confidence


@dataclass
class ArticleScanResult:
    doi: str
    matched_any_property: bool = False
    compounds_hit: set[str] = field(default_factory=set)
    # Every qualifying eMethodName (property, compound) match in this article, playground or not.
    matches: list[Candidate] = field(default_factory=list)
    # Every qualifying sMethodName-only (free-text method) match, same shape as `matches`, kept
    # separately: these are the "statistical-thermodynamics-derived ideal-gas" candidates a
    # playground-empty scan falls back to reporting (finding 4), not proposed as WP0 hits.
    free_method_matches: list[Candidate] = field(default_factory=list)
    skipped_free_method: int = 0
    skipped_prediction: int = 0
    skipped_critical_evaluation: int = 0
    skipped_mixture_blocks: int = 0
    # Compounds referenced by a qualifying (target property, target phase) block that carry an
    # sStandardInChI but no sStandardInChIKey -- silently dropped before this fix (finding 6).
    compounds_without_inchikey: list[dict] = field(default_factory=list)


def _scan_document(
    xml_bytes: bytes,
    xml_member: str,
    json_member: str,
    playground_keys: set[str],
) -> ArticleScanResult | None:
    # Trusted local archive, digest-verified as a whole before any member is read.
    root = ET.fromstring(xml_bytes)
    compounds = _parse_compounds(root)
    doi, journal, year = _parse_citation(root)
    result = ArticleScanResult(doi=doi)

    for block in root.findall(_tag("PureOrMixtureData")):
        components = block.findall(_tag("Component"))
        if len(components) != 1:
            if any(_property_method(p)[0] == TARGET_PROPERTY for p in block.findall(_tag("Property"))):
                result.skipped_mixture_blocks += 1
            continue
        reg = components[0].find(_tag("RegNum"))
        org_num_text = _child_text(reg, "nOrgNum") if reg is not None else None
        if org_num_text is None:
            continue
        compound = compounds.get(int(org_num_text))
        if compound is None:
            continue

        temp_var_numbers = _temperature_var_numbers(block)
        pressure_var_numbers = _pressure_var_numbers(block)
        constraint_pressures = _pressure_constraints_kpa(block)

        for prop in block.findall(_tag("Property")):
            prop_name, origin, method_text = _property_method(prop)
            if prop_name != TARGET_PROPERTY:
                continue
            phase = _property_phase(prop, block)
            if phase not in TARGET_PHASES:
                continue
            if origin == "prediction":
                result.skipped_prediction += 1
                continue
            if origin == "critical_evaluation":
                result.skipped_critical_evaluation += 1
                continue
            if origin not in ("experimental_free", "experimental_enum"):
                continue

            prop_number_text = _child_text(prop, "nPropNumber")
            if prop_number_text is None:
                continue
            prop_number = int(prop_number_text)

            has_coverage, has_confidence = _property_level_uncertainty_meta(prop)
            temps: list[float] = []
            pressures_from_vars: list[float] = []
            uncertainty_present: set[str] = set()
            n_values = 0
            for num_values in block.findall(_tag("NumValues")):
                value_elem = None
                for pv in num_values.findall(_tag("PropertyValue")):
                    if _child_text(pv, "nPropNumber") == str(prop_number):
                        value_elem = pv
                        break
                if value_elem is None:
                    continue
                n_values += 1
                uncertainty_present |= _uncertainty_flags(value_elem)
                for var_value in num_values.findall(_tag("VariableValue")):
                    var_number_text = _child_text(var_value, "nVarNumber")
                    if var_number_text is None:
                        continue
                    var_number = int(var_number_text)
                    if var_number in temp_var_numbers:
                        temp_text = _child_text(var_value, "nVarValue")
                        if temp_text is not None:
                            temps.append(float(temp_text))
                    if var_number in pressure_var_numbers:
                        pressure_text = _child_text(var_value, "nVarValue")
                        if pressure_text is not None:
                            pressures_from_vars.append(float(pressure_text))

            if n_values == 0:
                continue

            if not compound.inchikey:
                # Silently dropped before finding 6: counted and logged now, not just discarded.
                if compound.has_standard_inchi:
                    result.compounds_without_inchikey.append(
                        {"doi": doi, "compound_name": compound.name, "org_num": compound.org_num}
                    )
                if origin == "experimental_free":
                    result.skipped_free_method += 1
                continue

            if constraint_pressures:
                pressure_kind = "constraint"
                all_pressures = constraint_pressures
            elif pressures_from_vars:
                pressure_kind = "variable"
                all_pressures = pressures_from_vars
            else:
                pressure_kind = "none"
                all_pressures = []
            distinct_pressures = sorted(set(all_pressures))

            candidate = Candidate(
                doi=doi,
                journal=journal,
                year=year,
                compound_name=compound.name,
                inchikey=compound.inchikey,
                n_values=n_values,
                temperature_min_k=min(temps) if temps else None,
                temperature_max_k=max(temps) if temps else None,
                pressure_kind=pressure_kind,
                pressures_kpa=distinct_pressures,
                pressure_min_kpa=min(distinct_pressures) if distinct_pressures else None,
                pressure_max_kpa=max(distinct_pressures) if distinct_pressures else None,
                pressure_n_distinct=len(distinct_pressures),
                e_phase=phase,
                e_method_name=method_text or "",
                origin=origin,
                uncertainty_elements=sorted(uncertainty_present),
                has_coverage_factor=has_coverage,
                has_level_of_confidence=has_confidence,
                xml_member=xml_member,
                json_member=json_member,
                xml_sha256=sha256_bytes(xml_bytes),
                json_sha256=None,
                is_playground=compound.inchikey in playground_keys,
            )

            if origin == "experimental_free":
                result.skipped_free_method += 1
                result.free_method_matches.append(candidate)
                continue

            # origin == "experimental_enum"
            result.matched_any_property = True
            result.compounds_hit.add(compound.inchikey)
            result.matches.append(candidate)

    return (
        result
        if (
            result.matched_any_property
            or result.skipped_mixture_blocks
            or result.skipped_free_method
            or result.skipped_prediction
            or result.skipped_critical_evaluation
            or result.compounds_without_inchikey
        )
        else None
    )


@dataclass
class ScanSummary:
    articles_scanned: int = 0
    articles_with_any_idealgas_cp: int = 0
    distinct_compounds_hit: set[str] = field(default_factory=set)
    candidates: list[Candidate] = field(default_factory=list)
    coverage_by_compound: dict[str, dict] = field(default_factory=dict)
    skipped_free_method: int = 0
    skipped_prediction: int = 0
    skipped_critical_evaluation: int = 0
    skipped_mixture_blocks: int = 0
    playground_total: int = 0
    playground_hit: set[str] = field(default_factory=set)
    # Every single-component "Ideal gas"/"Gas" Cp block whose only method is a free-text
    # sMethodName -- the "statistical-thermodynamics-derived ideal-gas" set an author may pick
    # from when no playground species has a qualifying eMethodName record (finding 4).
    free_method_candidates: list[Candidate] = field(default_factory=list)
    # Compounds with sStandardInChI but no sStandardInChIKey, referenced by a qualifying block
    # (finding 6); previously silently dropped.
    compounds_without_inchikey: list[dict] = field(default_factory=list)


def scan_archive(
    archive_path: Path,
    playground: list[PlaygroundSpecies],
    *,
    compute_json_digests: bool = True,
) -> ScanSummary:
    playground_keys = {s.inchikey for s in playground}
    summary = ScanSummary(playground_total=len(playground))

    tf = tarfile.open(archive_path, "r:gz")
    xml_members: dict[str, tarfile.TarInfo] = {}
    json_members: dict[str, tarfile.TarInfo] = {}
    for member in tf:
        if not member.isfile():
            continue
        if member.name.endswith(".xml"):
            xml_members[member.name[: -len(".xml")]] = member
        elif member.name.endswith(".json"):
            json_members[member.name[: -len(".json")]] = member

    for stem, xml_member in xml_members.items():
        xml_bytes = tf.extractfile(xml_member).read()
        summary.articles_scanned += 1
        if TARGET_PROPERTY.encode("utf-8") not in xml_bytes:
            continue
        try:
            article = _scan_document(xml_bytes, xml_member.name, stem + ".json", playground_keys)
        except ET.ParseError:
            continue
        if article is None:
            continue
        summary.skipped_free_method += article.skipped_free_method
        summary.skipped_prediction += article.skipped_prediction
        summary.skipped_critical_evaluation += article.skipped_critical_evaluation
        summary.skipped_mixture_blocks += article.skipped_mixture_blocks
        summary.compounds_without_inchikey.extend(article.compounds_without_inchikey)
        for free_match in article.free_method_matches:
            if compute_json_digests:
                json_member = json_members.get(stem)
                if json_member is not None:
                    free_match.json_sha256 = sha256_bytes(tf.extractfile(json_member).read())
            summary.free_method_candidates.append(free_match)
        if article.matched_any_property:
            summary.articles_with_any_idealgas_cp += 1
            summary.distinct_compounds_hit |= article.compounds_hit
            for match in article.matches:
                bucket = summary.coverage_by_compound.setdefault(
                    match.inchikey,
                    {
                        "inchikey": match.inchikey,
                        "compound_name": match.compound_name,
                        "n_values": 0,
                        "n_articles": 0,
                    },
                )
                bucket["n_articles"] += 1
                bucket["n_values"] += match.n_values

                if match.is_playground:
                    summary.playground_hit.add(match.inchikey)
                    if compute_json_digests:
                        json_member = json_members.get(stem)
                        if json_member is not None:
                            match.json_sha256 = sha256_bytes(tf.extractfile(json_member).read())
                    summary.candidates.append(match)
    tf.close()
    return summary


def _candidate_rank_key(candidate: Candidate) -> tuple:
    has_expanded_with_coverage = (
        "nCombExpandUncertValue" in candidate.uncertainty_elements and candidate.has_coverage_factor
    )
    return (has_expanded_with_coverage, candidate.n_values)


def _format_pressure(c: Candidate) -> str:
    """Render a candidate's pressure for the report's P (kPa) column.

    Distinguishes a block-level fixed ``Constraint`` from a per-point ``Variable`` (finding 2):
    a single constraint value prints bare (e.g. ``3000``); several distinct values -- always from
    a per-point ``Variable`` -- print as a range with the distinct count and the kind.
    """

    if c.pressure_kind == "none" or not c.pressures_kpa:
        return "n/a"
    if c.pressure_n_distinct == 1:
        return f"{c.pressure_min_kpa:g} ({c.pressure_kind})"
    return f"{c.pressure_min_kpa:g}-{c.pressure_max_kpa:g} ({c.pressure_n_distinct} distinct, {c.pressure_kind})"


def _print_markdown(summary: ScanSummary, playground: list[PlaygroundSpecies]) -> None:
    print(f"Articles scanned: {summary.articles_scanned}")
    print(f"Articles with any ideal-gas/gas Cp (any compound): {summary.articles_with_any_idealgas_cp}")
    print(f"Distinct compounds with a qualifying Cp property: {len(summary.distinct_compounds_hit)}")
    print(f"Playground species hit: {len(summary.playground_hit)} / {summary.playground_total}")
    print(
        f"Skipped (sMethodName-only): {summary.skipped_free_method}  "
        f"Skipped (Prediction): {summary.skipped_prediction}  "
        f"Skipped (CriticalEvaluation): {summary.skipped_critical_evaluation}  "
        f"Skipped (mixture blocks): {summary.skipped_mixture_blocks}"
    )
    print(
        f"Compounds with sStandardInChI but no sStandardInChIKey (dropped): {len(summary.compounds_without_inchikey)}"
    )
    print()

    candidates = sorted(summary.candidates, key=_candidate_rank_key, reverse=True)
    if candidates:
        print(
            "| Rank | Compound | InChIKey | DOI | Journal | Year | N | T range (K) | "
            "P (kPa) | ePhase | eMethodName | Uncertainty | Coverage factor | LoC |"
        )
        print("| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |")
        for rank, c in enumerate(candidates, start=1):
            trange = f"{c.temperature_min_k:g}-{c.temperature_max_k:g}" if c.temperature_min_k is not None else "n/a"
            pressures = _format_pressure(c)
            uncertainty = ", ".join(c.uncertainty_elements) or "none"
            print(
                f"| {rank} | {c.compound_name} | {c.inchikey} | {c.doi} | {c.journal} | "
                f"{c.year} | {c.n_values} | {trange} | {pressures} | {c.e_phase} | "
                f"{c.e_method_name} | {uncertainty} | {c.has_coverage_factor} | "
                f"{c.has_level_of_confidence} |"
            )
    else:
        print("No playground species were hit by any qualifying property.")
        print()
        print("Most-covered compounds in the archive generally (fallback candidates):")
        top = sorted(summary.coverage_by_compound.values(), key=lambda b: b["n_values"], reverse=True)[:10]
        print("| InChIKey | Articles | Total values |")
        print("| --- | --- | --- |")
        for bucket in top:
            print(f"| {bucket['inchikey']} | {bucket['n_articles']} | {bucket['n_values']} |")

    print()
    free_candidates = sorted(summary.free_method_candidates, key=_candidate_rank_key, reverse=True)
    print(
        f"sMethodName-only single-component Ideal gas/Gas Cp candidates "
        f"(statistical-thermodynamics-derived set): {len(free_candidates)}"
    )
    if free_candidates:
        print(
            "| Compound | InChIKey | DOI | Journal | Year | N | T range (K) | P (kPa) | ePhase | "
            "sMethodName | Uncertainty |"
        )
        print("| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |")
        for c in free_candidates:
            trange = f"{c.temperature_min_k:g}-{c.temperature_max_k:g}" if c.temperature_min_k is not None else "n/a"
            pressures = _format_pressure(c)
            uncertainty = ", ".join(c.uncertainty_elements) or "none"
            print(
                f"| {c.compound_name} | {c.inchikey} | {c.doi} | {c.journal} | {c.year} | "
                f"{c.n_values} | {trange} | {pressures} | {c.e_phase} | {c.e_method_name} | {uncertainty} |"
            )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--archive", required=True, type=Path, help="path to the downloaded ThermoML .tgz")
    parser.add_argument("--inchikeys", required=True, type=Path, help="playground species InChIKey list")
    parser.add_argument("--out", required=True, type=Path, help="JSON output path")
    parser.add_argument(
        "--skip-json-digests",
        action="store_true",
        help="skip computing the JSON twin's SHA-256 for each candidate (faster, incomplete report)",
    )
    args = parser.parse_args(argv)

    if not args.archive.exists():
        print(f"error: archive not found: {args.archive}", file=sys.stderr)
        return EXIT_ARCHIVE_NOT_FOUND

    observed_digest = sha256_file(args.archive)
    expected_digest = pinned_archive_sha256()
    if observed_digest != expected_digest:
        print(
            f"error: archive SHA-256 mismatch: observed {observed_digest}, expected {expected_digest}. "
            "Refusing to read any archive member.",
            file=sys.stderr,
        )
        return EXIT_DIGEST_MISMATCH

    playground = load_inchikeys(args.inchikeys)
    summary = scan_archive(args.archive, playground, compute_json_digests=not args.skip_json_digests)

    _print_markdown(summary, playground)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "archive": str(args.archive),
        "archive_sha256": observed_digest,
        "archive_size": args.archive.stat().st_size,
        "target_property": TARGET_PROPERTY,
        "target_phases": list(TARGET_PHASES),
        "counts": {
            "articles_scanned": summary.articles_scanned,
            "articles_with_any_idealgas_cp": summary.articles_with_any_idealgas_cp,
            "distinct_compounds_hit": len(summary.distinct_compounds_hit),
            "playground_total": summary.playground_total,
            "playground_hit": len(summary.playground_hit),
            "playground_hit_inchikeys": sorted(summary.playground_hit),
            "skipped_free_method": summary.skipped_free_method,
            "skipped_prediction": summary.skipped_prediction,
            "skipped_critical_evaluation": summary.skipped_critical_evaluation,
            "skipped_mixture_blocks": summary.skipped_mixture_blocks,
            "compounds_without_inchikey": len(summary.compounds_without_inchikey),
        },
        "candidates": [c.to_json() for c in sorted(summary.candidates, key=_candidate_rank_key, reverse=True)],
        "fallback_coverage_by_compound": sorted(
            summary.coverage_by_compound.values(), key=lambda b: b["n_values"], reverse=True
        )[:10],
        # The sMethodName-only, single-component "Ideal gas"/"Gas" Cp set (finding 4): survives
        # even when the archive has zero playground hits, since scan_archive no longer discards
        # an article's skip counters/free-method matches just because it has no eMethodName hit.
        "free_method_ideal_gas_candidates": [
            c.to_json() for c in sorted(summary.free_method_candidates, key=_candidate_rank_key, reverse=True)
        ],
        "compounds_without_inchikey": summary.compounds_without_inchikey,
    }
    args.out.write_text(json.dumps(payload, indent=2, sort_keys=False))

    if not summary.candidates:
        return EXIT_NO_PLAYGROUND_CANDIDATE
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
