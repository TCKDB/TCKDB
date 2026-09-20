"""``ThermoMLCpTable`` -> ``MolecularPropertyObservationCreate`` mapping.

Reject, don't guess: identity is a hint only (``species_entry_id`` is
never set here); no unit conversion beyond the recorded identity
conversion (``J/K/mol`` -> ``J/mol/K``); no k=2<->95% inference; no
real-gas correction. ThermoML tag names live in ``raw_payload_json``
and the :class:`MappingReport`, never in a TCKDB field name.

``ScientificOriginKind`` is imported from the wire package
(``tckdb_schemas.enums``), not ``app.db.models.common`` -- it is
mirrored there byte-for-byte (see
``backend/tests/schemas/test_tckdb_schemas_enum_drift.py``) and this
importer never needs an ORM-adjacent symbol to use it. The other three
enums here (``ObservedStateBasis``, ``ObservedUncertaintyAssessor``,
``ObservedUncertaintyKind``) have no wire mirror yet, so
``app.db.models.common`` stays the one pinned ``app.db`` import this
package makes -- see ``tests/importers/thermoml/test_layering.py``,
which documents and guards that exception the same way
``test_cccbdb_molecular_property_import.py::test_service_does_not_import_parsers_or_fetchers``
documents CCCBDB's analogous layering boundary.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from tckdb_schemas.enums import ScientificOriginKind

from app.db.models.common import (
    ObservedStateBasis,
    ObservedUncertaintyAssessor,
    ObservedUncertaintyKind,
)
from app.importers.thermoml import MAPPING_VERSION, PARSER_VERSION
from app.importers.thermoml.models import (
    ThermoMLCompound,
    ThermoMLCpTable,
    ThermoMLCpValue,
    ThermoMLParsedDocument,
    ThermoMLUncertaintyDefinition,
    ThermoMLValueUncertainty,
)
from app.schemas.entities.molecular_property_observation import (
    HEAT_CAPACITY_CP_UNIT,
    MolecularPropertyObservationCreate,
)

REPORT_SCHEMA = "thermoml.mapping.v1"

#: kPa -> bar divisor. 1 bar = 100 kPa exactly (both SI-derived units);
#: this is the one unit conversion this mapper performs, and it must be
#: exact for the archive's decimal inputs. Divide by this, do NOT
#: multiply by a reciprocal constant (``0.01``): ``1020 * 0.01`` is
#: ``10.200000000000001`` in IEEE-754 binary64, while ``1020 / 100.0``
#: is exactly ``10.2`` -- 100.0 is exactly representable in binary64
#: and float division by it rounds correctly for every value this
#: archive's ``nConstraintValue``/``nVarValue`` decimal literals can
#: produce, where multiplying by ``0.01`` (itself not exactly
#: representable) compounds two roundings instead of one.
_KPA_PER_BAR = 100.0

_STATE_BASIS_BY_PHASE = {
    "Ideal gas": ObservedStateBasis.ideal_gas,
    "Gas": ObservedStateBasis.real_gas,
}

#: ``Property/eStandardState`` values other than this (see
#: ``schema/ThermoML.xsd``'s enumeration: "Pure compound", "Pure
#: liquid solute", "Standard molality (1 mol/kg) solute", "Standard
#: amount concentration (1 mol/dm3) solute", "Infinite dilution
#: solute") describe a solute/solution reference state, not the
#: single-component gas-phase Cp this importer maps. Absent is
#: equivalent to "Pure compound" (the field is optional and that is
#: its evident default for a single-component block).
_SUPPORTED_STANDARD_STATE = "Pure compound"

#: Observed-method-string rule (plan C2 amendment, ``rule
#: id=origin.smethodname_allowlist.v1``): when a Cp property carries
#: neither ``eMethodName`` nor a ``Prediction`` but does carry a free-text
#: ``sMethodName``, origin is decided from this EXPLICIT allowlist of
#: verbatim strings -- never a keyword/substring match. A string not in
#: this table rejects the row rather than guessing "probably
#: experimental". Populated from the 15 single-component "Ideal
#: gas"/"Gas" Cp blocks with no ``eMethodName``/``Prediction`` found by
#: the archive scan on branch ``phase-c-wp0-thermoml-scan``
#: (``docs/validation/thermoml_cp_pilot_scan.json``,
#: ``free_method_ideal_gas_candidates``), cross-checked against the
#: pinned archive (``ThermoML.v2020-09-30.tgz``, sha256
#: ``231161b5e443dc1ae0e5da8429d86a88474cb722016e5b790817bb31c58d7ec2``)
#: 2026-09-20. Two distinct strings denote a statistical-mechanics
#: calculation of the ideal-gas Cp reference value (never a direct
#: measurement), so both map to ``computed``:
#:   * "statistical thermodynamics" -- DOI 10.1016/j.jct.2013.05.032
#:     (cesium hydroxide iodide) and 10.1016/j.jct.2013.08.022 (benzene,
#:     the pilot article).
#:   * "Statistical thermodynamic calculations" -- DOIs
#:     10.1016/j.jct.2012.11.031, 10.1016/j.jct.2013.01.009 (x4),
#:     10.1016/j.tca.2014.07.018.
#: The remaining observed strings ("STD", "Statistical thermodynamics"
#: [note the capitalization, distinct key from the lowercase form
#: above], "Predicted", "derived from presented speed of sound
#: measurements", "Derived with speed of sound") are NOT on this
#: allowlist: none of them is unambiguous enough to classify without
#: reading the source article, which this importer does not do, so a
#: block using any of them is rejected with the verbatim string in the
#: report rather than guessed at.
_S_METHOD_NAME_ORIGIN: dict[str, ScientificOriginKind] = {
    "statistical thermodynamics": ScientificOriginKind.computed,
    "Statistical thermodynamic calculations": ScientificOriginKind.computed,
}

#: Uncertainty precedence, versioned as ``uncertainty.precedence.v1``:
#: the typed columns hold the first present of combined expanded,
#: combined standard, expanded, standard.
_PRECEDENCE: tuple[tuple[str, str, ObservedUncertaintyKind], ...] = (
    ("CombinedUncertainty", "expand_value", ObservedUncertaintyKind.combined_expanded),
    ("CombinedUncertainty", "std_value", ObservedUncertaintyKind.combined_standard),
    ("PropUncertainty", "expand_value", ObservedUncertaintyKind.expanded),
    ("PropUncertainty", "std_value", ObservedUncertaintyKind.standard),
)

_ASSESSOR_BY_SOURCE = {
    "PropUncertainty": ObservedUncertaintyAssessor.source_author,
    "CombinedUncertainty": ObservedUncertaintyAssessor.source_evaluator,
}

_COMPUTED_PREDICTION_TYPES = {"Statistical mechanics", "Ab initio"}


class ThermoMLLiteratureFragment(BaseModel):
    """DOI/title/year/journal/authors, for C-E3's literature resolver.

    Never resolved to a ``literature_id`` here --
    ``resolve_or_create_literature`` (Phase C-E3) is the only thing
    allowed to do that.
    """

    model_config = ConfigDict(extra="forbid")

    doi: str | None = None
    title: str | None = None
    year: int | None = None
    journal: str | None = None
    authors: list[str] = Field(default_factory=list)


class MappingReport(BaseModel):
    """Compact, versioned summary of one mapping run.

    :param schema: Report schema id (:data:`REPORT_SCHEMA`), so a
        consumer of a persisted report JSON can tell which shape it is
        reading without guessing.
    :param parser_version: :data:`app.importers.thermoml.PARSER_VERSION`
        at the time of the run.
    :param mapping_version: :data:`app.importers.thermoml.MAPPING_VERSION`
        at the time of the run.
    :param transformed: Rule ids that fired at least once (e.g.
        ``"unit.j_mol_k.identity"``, ``"uncertainty.precedence.v1"``).
    :param retained_only: Rule ids naming content carried through
        verbatim into ``raw_payload_json`` without further
        interpretation (e.g. every non-selected uncertainty entry).
    :param unsupported: Blocks or per-value entries recognized but not
        mapped, each a dict with at least ``reason`` and ``detail``.
    :param rejected: Rows that would otherwise map but fail a mapping
        precondition (e.g. a real-gas row with no pressure), each a
        dict with at least ``reason`` and ``detail``.
    :param counts: Summary counts for the run.
    :param identity: One identity-hint dict per distinct compound seen
        (deduplicated by standard InChIKey), never resolved to a
        ``species_entry_id``.
    """

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    schema_: str = Field(default=REPORT_SCHEMA, alias="schema")
    parser_version: str = PARSER_VERSION
    mapping_version: str = MAPPING_VERSION
    transformed: list[str] = Field(default_factory=list)
    retained_only: list[str] = Field(default_factory=list)
    unsupported: list[dict[str, Any]] = Field(default_factory=list)
    rejected: list[dict[str, Any]] = Field(default_factory=list)
    counts: dict[str, int] = Field(default_factory=dict)
    identity: list[dict[str, Any]] = Field(default_factory=list)


class ThermoMLMappingResult(BaseModel):
    """Everything :func:`map_document` produces for one ThermoML document."""

    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    payloads: list[MolecularPropertyObservationCreate] = Field(default_factory=list)
    report: MappingReport
    literature: ThermoMLLiteratureFragment


def _identity_hint(compound: ThermoMLCompound) -> dict[str, Any]:
    return {
        "standard_inchi": compound.standard_inchi,
        "standard_inchi_key": compound.standard_inchi_key,
        "smiles": list(compound.smiles),
        "formula_molec": compound.formula_molec,
        "common_names": list(compound.common_names),
        "cas_rn": compound.cas_rn,
    }


def _select_uncertainty(
    value: ThermoMLCpValue,
    definitions: dict[tuple[str, int], ThermoMLUncertaintyDefinition],
) -> tuple[
    ThermoMLValueUncertainty | None,
    ObservedUncertaintyKind | None,
    float | None,
    list[dict[str, Any]],
    list[dict[str, Any]],
    dict[str, Any] | None,
]:
    """Pick the highest-precedence usable uncertainty entry for one row.

    :returns: ``(chosen_entry, kind, scalar_uncertainty, retained,
        unsupported, fatal)`` where ``retained`` lists every
        uncertainty entry NOT selected (verbatim, for
        ``raw_payload_json["uncertainties"]``), ``unsupported`` lists
        entries that were skipped because they carry only an
        asymmetric value, and ``fatal`` -- when not ``None`` -- means
        the ROW itself must be rejected (never mapped, never silently
        given ``uncertainty_kind=standard``/``scalar_uncertainty=None``):
        a per-value uncertainty entry that carries neither
        ``std_value`` nor ``expand_value`` and is not flagged
        asymmetric is malformed, not merely unselected, so the caller
        must reject the row rather than build a payload with a typed
        uncertainty kind pointing at no value.
    """

    fatal: dict[str, Any] | None = None
    for entry in value.uncertainties:
        if (
            entry.std_value is None
            and entry.expand_value is None
            and not entry.has_asymmetric
        ):
            fatal = {
                "reason": "empty_uncertainty_value",
                "detail": (
                    f"{entry.source} assess_num={entry.assess_num} carries "
                    "neither a standard nor an expanded uncertainty value"
                ),
            }
            break
    if fatal is not None:
        return None, None, None, [], [], fatal

    by_key: dict[tuple[str, str], ThermoMLValueUncertainty] = {}
    for entry in value.uncertainties:
        by_key[(entry.source, "expand_value" if entry.expand_value is not None else "std_value")] = entry

    retained: list[dict[str, Any]] = []
    unsupported: list[dict[str, Any]] = []
    chosen: ThermoMLValueUncertainty | None = None
    chosen_field: str | None = None
    chosen_kind: ObservedUncertaintyKind | None = None

    for source, field_name, kind in _PRECEDENCE:
        entry = by_key.get((source, field_name))
        if entry is None:
            continue
        if chosen is None:
            if entry.has_asymmetric and getattr(entry, field_name) is None:
                unsupported.append(
                    {
                        "reason": "asymmetric_uncertainty",
                        "detail": f"{source} assess_num={entry.assess_num} carries "
                        "only an asymmetric value",
                    }
                )
                continue
            chosen = entry
            chosen_field = field_name
            chosen_kind = kind

    for entry in value.uncertainties:
        if entry is chosen:
            continue
        retained.append(
            {
                "source": entry.source,
                "assess_num": entry.assess_num,
                "std_value": entry.std_value,
                "expand_value": entry.expand_value,
                "has_asymmetric": entry.has_asymmetric,
            }
        )

    scalar_uncertainty = (
        getattr(chosen, chosen_field) if chosen is not None and chosen_field else None
    )
    return chosen, chosen_kind, scalar_uncertainty, retained, unsupported, None


def map_cp_table(
    table: ThermoMLCpTable,
    *,
    doi: str,
) -> tuple[list[MolecularPropertyObservationCreate], MappingReport, dict[str, Any]]:
    """Map one :class:`ThermoMLCpTable` to observation payloads.

    :returns: ``(payloads, report, identity_hint)``. ``report``'s
        ``schema``/``parser_version``/``mapping_version`` fields are
        already populated; callers combining multiple tables should
        merge ``transformed``/``retained_only``/``unsupported``/
        ``rejected``/``counts`` themselves (see :func:`map_document`).
    """

    definitions = {
        (d.source, d.assess_num): d for d in table.uncertainty_definitions
    }

    transformed: set[str] = set()
    retained_only: set[str] = set()
    unsupported: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    payloads: list[MolecularPropertyObservationCreate] = []

    state_basis = _STATE_BASIS_BY_PHASE.get(table.phase_raw)
    if state_basis is None:  # pragma: no cover - parser already filters this
        rejected.append(
            {
                "reason": "unsupported_phase",
                "detail": f"ePhase={table.phase_raw!r}",
                "block_index": table.block_index,
            }
        )
        return payloads, MappingReport(unsupported=unsupported, rejected=rejected), _identity_hint(
            table.compound
        )

    if table.standard_state_raw is not None and table.standard_state_raw != _SUPPORTED_STANDARD_STATE:
        rejected.append(
            {
                "reason": "unsupported_standard_state",
                "detail": f"eStandardState={table.standard_state_raw!r}",
                "block_index": table.block_index,
            }
        )
        return payloads, MappingReport(unsupported=unsupported, rejected=rejected), _identity_hint(
            table.compound
        )

    base_rules: set[str] = {"state_basis.phase_map.v1"}

    if table.method_kind == "eMethodName":
        scientific_origin = ScientificOriginKind.experimental
        method_note = table.method_name
        base_rules.add("origin.method.experimental")
    elif table.method_kind == "sMethodName":
        origin = _S_METHOD_NAME_ORIGIN.get(table.method_name or "")
        if origin is None:
            rejected.append(
                {
                    "reason": "unrecognized_smethodname",
                    "detail": (
                        f"sMethodName={table.method_name!r} not on the "
                        "observed-method-string allowlist "
                        "(origin.smethodname_allowlist.v1)"
                    ),
                    "block_index": table.block_index,
                }
            )
            return payloads, MappingReport(unsupported=unsupported, rejected=rejected), _identity_hint(
                table.compound
            )
        scientific_origin = origin
        method_note = table.method_name
        base_rules.add("origin.smethodname_allowlist.v1")
    else:
        prediction_type = table.prediction_type or ""
        if prediction_type in _COMPUTED_PREDICTION_TYPES:
            scientific_origin = ScientificOriginKind.computed
        else:
            scientific_origin = ScientificOriginKind.estimated
        method_note = table.prediction_type
        base_rules.add("origin.prediction_type_map.v1")

    transformed.update(base_rules)

    for value in table.values:
        record_key = (
            f"{doi}#PureOrMixtureData[{table.block_index}]/"
            f"Property[{table.prop_number}]/NumValues[{value.record_index}]"
        )

        if value.temperature_k is None:
            rejected.append(
                {
                    "reason": "missing_temperature",
                    "detail": "no Temperature, K constraint or variable resolved",
                    "record_key": record_key,
                }
            )
            continue

        pressure_kpa = value.pressure_kpa
        if state_basis == ObservedStateBasis.real_gas and pressure_kpa is None:
            rejected.append(
                {
                    "reason": "gas_missing_pressure",
                    "detail": (
                        "phase 'Gas' (real-gas basis) with no 'Pressure, kPa' "
                        "constraint or variable; not comparable without a "
                        "stated pressure"
                    ),
                    "record_key": record_key,
                }
            )
            continue

        row_rules: set[str] = set(base_rules)

        pressure_bar = (
            pressure_kpa / _KPA_PER_BAR if pressure_kpa is not None else None
        )
        if pressure_kpa is not None:
            row_rules.add("pressure.kpa_to_bar.v1")

        chosen, kind, scalar_uncertainty, retained, unc_unsupported, unc_fatal = (
            _select_uncertainty(value, definitions)
        )
        if unc_fatal is not None:
            rejected.append({**unc_fatal, "record_key": record_key})
            continue
        for entry in unc_unsupported:
            unsupported.append({**entry, "record_key": record_key})
        if retained:
            retained_only.add("raw_payload_json.uncertainties")

        uncertainty_kind: ObservedUncertaintyKind | None = None
        uncertainty_coverage_factor: float | None = None
        uncertainty_level_of_confidence_pct: float | None = None
        uncertainty_assessor: ObservedUncertaintyAssessor | None = None
        if chosen is not None and kind is not None:
            row_rules.add("uncertainty.precedence.v1")
            uncertainty_kind = kind
            definition = definitions.get((chosen.source, chosen.assess_num))
            if definition is not None:
                if kind in (
                    ObservedUncertaintyKind.expanded,
                    ObservedUncertaintyKind.combined_expanded,
                ):
                    uncertainty_coverage_factor = definition.coverage_factor
                uncertainty_level_of_confidence_pct = (
                    definition.level_of_confidence_pct
                )
                uncertainty_assessor = _ASSESSOR_BY_SOURCE.get(definition.source)

        row_rules.add("unit.j_mol_k.identity")
        transformed.update(row_rules)

        raw_payload_json = {
            "mapping": {
                "schema": REPORT_SCHEMA,
                "parser_version": PARSER_VERSION,
                "mapping_version": MAPPING_VERSION,
                # Per-row, not the table-wide accumulator: this is a
                # fresh set built for THIS row alone, so it is
                # deterministic and does not depend on what rules
                # earlier rows happened to have already triggered
                # (see PR body finding 8 -- the accumulator is still
                # kept, in `transformed`, but only for the table-level
                # MappingReport summary, never for a single row's own
                # raw_payload_json).
                "rules": sorted(row_rules),
            },
            "source_value": {
                "nPropValue": value.value,
                "nPropDigits": value.digits,
                "uncertainties": retained
                + (
                    [
                        {
                            "source": chosen.source,
                            "assess_num": chosen.assess_num,
                            "std_value": chosen.std_value,
                            "expand_value": chosen.expand_value,
                            "has_asymmetric": chosen.has_asymmetric,
                            "selected": True,
                        }
                    ]
                    if chosen is not None
                    else []
                ),
            },
            "identity_hint": _identity_hint(table.compound),
            "citation": {"doi": doi},
            "thermoml": {
                "property_label": table.property_label,
                "phase_raw": table.phase_raw,
                "standard_state_raw": table.standard_state_raw,
                "method_kind": table.method_kind,
                "prediction_type": table.prediction_type,
                "prediction_method_name": table.prediction_method_name,
                "block_index": table.block_index,
                "prop_number": table.prop_number,
                "record_index": value.record_index,
                "pressure_source": table.pressure_source,
            },
        }

        payload = MolecularPropertyObservationCreate(
            scientific_origin=scientific_origin,
            property_kind="heat_capacity_cp",
            property_label=table.property_label,
            scalar_value=value.value,
            scalar_unit=HEAT_CAPACITY_CP_UNIT,
            scalar_uncertainty=scalar_uncertainty,
            uncertainty_kind=uncertainty_kind,
            uncertainty_coverage_factor=uncertainty_coverage_factor,
            uncertainty_level_of_confidence_pct=uncertainty_level_of_confidence_pct,
            uncertainty_assessor=uncertainty_assessor,
            temperature_k=value.temperature_k,
            pressure_bar=pressure_bar,
            state_basis=state_basis,
            method_note=method_note,
            state_label_raw=table.phase_raw,
            external_source_record_key=record_key,
            external_source_doi=doi,
            raw_payload_json=raw_payload_json,
        )
        payloads.append(payload)

    report = MappingReport(
        transformed=sorted(transformed),
        retained_only=sorted(retained_only),
        unsupported=unsupported,
        rejected=rejected,
        counts={
            "values_total": len(table.values),
            "payloads_built": len(payloads),
            "rejected": len(rejected),
        },
    )
    return payloads, report, _identity_hint(table.compound)


def map_document(
    document: ThermoMLParsedDocument, *, doi: str
) -> ThermoMLMappingResult:
    """Map every supported Cp table in ``document`` to observation payloads.

    :param document: Parser output (see ``parser.py``).
    :param doi: The article's own DOI (distinct from the archive
        dataset DOI), used to build each row's
        ``external_source_record_key``.
    """

    all_payloads: list[MolecularPropertyObservationCreate] = []
    transformed: set[str] = set()
    retained_only: set[str] = set()
    unsupported: list[dict[str, Any]] = [
        {
            "reason": u.reason,
            "detail": u.detail,
            "block_index": u.block_index,
        }
        for u in document.unsupported
    ]
    rejected: list[dict[str, Any]] = []
    identity_by_key: dict[str, dict[str, Any]] = {}

    for table in document.cp_tables:
        payloads, table_report, identity_hint = map_cp_table(table, doi=doi)
        all_payloads.extend(payloads)
        transformed.update(table_report.transformed)
        retained_only.update(table_report.retained_only)
        unsupported.extend(table_report.unsupported)
        rejected.extend(table_report.rejected)
        key = identity_hint.get("standard_inchi_key") or f"block-{table.block_index}"
        identity_by_key[key] = identity_hint

    report = MappingReport(
        transformed=sorted(transformed),
        retained_only=sorted(retained_only),
        unsupported=unsupported,
        rejected=rejected,
        counts={
            "cp_tables_parsed": len(document.cp_tables),
            "unsupported_blocks": len(document.unsupported),
            "payloads_built": len(all_payloads),
            "rejected": len(rejected),
        },
        identity=list(identity_by_key.values()),
    )
    literature = ThermoMLLiteratureFragment(
        doi=document.citation.doi,
        title=document.citation.title,
        year=document.citation.year,
        journal=document.citation.journal,
        authors=list(document.citation.authors),
    )
    return ThermoMLMappingResult(
        payloads=all_payloads, report=report, literature=literature
    )


__all__ = [
    "REPORT_SCHEMA",
    "MappingReport",
    "ThermoMLLiteratureFragment",
    "ThermoMLMappingResult",
    "map_cp_table",
    "map_document",
]
