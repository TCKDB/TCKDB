"""Namespace-aware ThermoML XML -> :mod:`app.importers.thermoml.models` parser.

Walks ``Compound``, ``Citation`` and ``PureOrMixtureData``; joins
``NumValues`` rows to ``Variable``/``Constraint`` by number and
per-value uncertainties to their Property-level definitions by
assessment number.

``PureOrMixtureData/Component`` is an XSD ``choice`` (see
``schema/ThermoML.xsd`` around the ``Component`` element definitions):
a component is keyed EITHER by ``nCompIndex`` OR by ``RegNum`` (which
in turn is ``{nCASRNum?, nOrgNum?}``, both optional). ``Compound``
mirrors this -- both ``nCompIndex`` and ``RegNum`` are optional there
too. The real NIST TRC ThermoML Archive (``mds2-2422``,
``ThermoML.v2020-09-30.tgz``) keys every Component/Compound pair we
have inspected by ``RegNum/nOrgNum``, never by ``nCompIndex`` -- so
both keys are resolved here and joined against whichever key the
Compound was keyed by.

Only ``xml.etree.ElementTree`` (stdlib) is used here -- schema
validation (which does need ``lxml``) happens earlier in
``validate.py``, and by the time a document reaches this module it is
already known to be schema-valid, so this parser does not need a
validating parser of its own.

A block that is multi-component, about a different property, in an
unsupported phase, a ``ReactionData`` block, or a ``CriticalEvaluation``
is recorded in :attr:`~app.importers.thermoml.models.ThermoMLParsedDocument.unsupported`
with the source's own verbatim strings -- never raised on. Malformed
XML structure (a required element genuinely absent, a join that fails)
raises :class:`ThermoMLMalformedDocumentError`, because that indicates
the document did not actually match the schema it claimed to.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from typing import Literal

from app.importers.thermoml.models import (
    ThermoMLCitation,
    ThermoMLCompound,
    ThermoMLCpTable,
    ThermoMLCpValue,
    ThermoMLParsedDocument,
    ThermoMLUncertaintyDefinition,
    ThermoMLUnsupportedBlock,
    ThermoMLValueUncertainty,
)

NS_URI = "http://www.iupac.org/namespaces/ThermoML"

#: The one ``ePropName`` this importer's mapper knows how to price in
#: J/mol/K. Every other heat-capacity-family label (Cv, Cp/Cv ratio,
#: specific/per-volume variants, saturation-pressure variants, ...) is
#: a different physical quantity and is recorded unsupported.
CP_PROPERTY_LABEL = "Molar heat capacity at constant pressure, J/K/mol"

#: Phases the mapper can express as an :class:`ObservedStateBasis`.
SUPPORTED_PHASES = ("Ideal gas", "Gas")

#: ThermoML constraint/variable type labels for temperature and
#: pressure, exactly as ``eTemperature``/``ePressure`` enumerate them.
_TEMPERATURE_LABEL = "Temperature, K"
_PRESSURE_LABEL = "Pressure, kPa"


class ThermoMLMalformedDocumentError(ValueError):
    """A document claims to be schema-valid but a required join failed.

    Distinct from an *unsupported* block: unsupported content is
    recognized and skipped; malformed content means a required
    reference (e.g. a ``Component/nCompIndex`` with no matching
    ``Compound``) could not be resolved, which should not happen for
    a document that actually validated against ``ThermoML.xsd``.
    """


def _q(tag: str) -> str:
    return f"{{{NS_URI}}}{tag}"


def _text(el: ET.Element | None) -> str | None:
    if el is None or el.text is None:
        return None
    stripped = el.text.strip()
    return stripped or None


def _int(el: ET.Element | None) -> int | None:
    t = _text(el)
    return int(t) if t is not None else None


def _float(el: ET.Element | None) -> float | None:
    t = _text(el)
    return float(t) if t is not None else None


def parse_thermoml_document(xml_bytes: bytes) -> ThermoMLParsedDocument:
    """Parse one ThermoML ``DataReport`` document.

    :param xml_bytes: The raw XML bytes. Callers should have already
        run this through :func:`app.importers.thermoml.validate.validate_bytes`
        -- this function does not itself validate.
    :raises ThermoMLMalformedDocumentError: A required cross-reference
        inside the document could not be resolved.
    """

    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as exc:
        raise ThermoMLMalformedDocumentError(f"not well-formed XML: {exc}") from exc

    citation = _parse_citation(root.find(_q("Citation")))
    compounds = tuple(
        _parse_compound(el) for el in root.findall(_q("Compound"))
    )
    compounds_by_index = {
        c.n_comp_index: c for c in compounds if c.n_comp_index is not None
    }
    compounds_by_org_num = {
        c.reg_org_num: c for c in compounds if c.reg_org_num is not None
    }

    cp_tables: list[ThermoMLCpTable] = []
    unsupported: list[ThermoMLUnsupportedBlock] = []
    warnings: list[str] = []

    for block_index, pomd_el in enumerate(
        root.findall(_q("PureOrMixtureData")), start=1
    ):
        outcome = _parse_pure_or_mixture_data(
            pomd_el,
            compounds_by_index,
            compounds_by_org_num,
            block_index=block_index,
        )
        if isinstance(outcome, ThermoMLCpTable):
            cp_tables.append(outcome)
        else:
            unsupported.append(outcome)

    for block_index, _rxn_el in enumerate(
        root.findall(_q("ReactionData")), start=1
    ):
        unsupported.append(
            ThermoMLUnsupportedBlock(
                block_index=block_index,
                reason="reaction_data",
                detail="ReactionData blocks are never Cp observations",
            )
        )

    return ThermoMLParsedDocument(
        citation=citation,
        compounds=compounds,
        cp_tables=tuple(cp_tables),
        unsupported=tuple(unsupported),
        warnings=tuple(warnings),
    )


def _parse_citation(el: ET.Element | None) -> ThermoMLCitation:
    if el is None:
        return ThermoMLCitation(doi=None, title=None, year=None, journal=None)
    authors = tuple(
        t for a in el.findall(_q("sAuthor")) if (t := _text(a)) is not None
    )
    return ThermoMLCitation(
        doi=_text(el.find(_q("sDOI"))),
        title=_text(el.find(_q("sTitle"))),
        year=_int(el.find(_q("yrPubYr"))),
        journal=_text(el.find(_q("sPubName"))),
        authors=authors,
    )


def _parse_compound(el: ET.Element) -> ThermoMLCompound:
    reg_num = el.find(_q("RegNum"))
    cas_rn = _text(reg_num.find(_q("nCASRNum"))) if reg_num is not None else None
    reg_org_num = _int(reg_num.find(_q("nOrgNum"))) if reg_num is not None else None
    smiles = tuple(
        t for s in el.findall(_q("sSmiles")) if (t := _text(s)) is not None
    )
    common_names = tuple(
        t for c in el.findall(_q("sCommonName")) if (t := _text(c)) is not None
    )
    return ThermoMLCompound(
        n_comp_index=_int(el.find(_q("nCompIndex"))),
        cas_rn=cas_rn,
        standard_inchi=_text(el.find(_q("sStandardInChI"))),
        standard_inchi_key=_text(el.find(_q("sStandardInChIKey"))),
        smiles=smiles,
        formula_molec=_text(el.find(_q("sFormulaMolec"))),
        common_names=common_names,
        reg_org_num=reg_org_num,
    )


def _component_ref(component_el: ET.Element) -> tuple[str, int] | None:
    """Resolve one ``Component``'s join key: ``("index", n)`` for
    ``nCompIndex``, or ``("org_num", n)`` for ``RegNum/nOrgNum``. The
    XSD makes these a ``choice`` -- exactly one branch is present (a
    ``RegNum`` with only ``nCASRNum`` and no ``nOrgNum`` does not
    resolve to a join key; CAS numbers are never used for this join).
    """

    idx = _int(component_el.find(_q("nCompIndex")))
    if idx is not None:
        return ("index", idx)
    reg_num = component_el.find(_q("RegNum"))
    if reg_num is not None:
        org_num = _int(reg_num.find(_q("nOrgNum")))
        if org_num is not None:
            return ("org_num", org_num)
    return None


def _parse_pure_or_mixture_data(
    pomd_el: ET.Element,
    compounds_by_index: dict[int, ThermoMLCompound],
    compounds_by_org_num: dict[int, ThermoMLCompound],
    *,
    block_index: int,
) -> ThermoMLCpTable | ThermoMLUnsupportedBlock:
    component_refs = [
        _component_ref(c) for c in pomd_el.findall(_q("Component"))
    ]
    if len(component_refs) != 1:
        return ThermoMLUnsupportedBlock(
            block_index=block_index,
            reason="multi_component",
            detail=f"{len(component_refs)} components",
        )
    (ref,) = component_refs
    compound: ThermoMLCompound | None = None
    if ref is not None:
        kind, key = ref
        if kind == "index":
            compound = compounds_by_index.get(key)
        else:
            compound = compounds_by_org_num.get(key)
    if compound is None:
        raise ThermoMLMalformedDocumentError(
            f"PureOrMixtureData[{block_index}] Component references "
            f"{ref!r}, no matching Compound"
        )

    property_els = pomd_el.findall(_q("Property"))
    cp_property_els = [
        p
        for p in property_els
        if _text(
            p.find(
                _q("Property-MethodID")
                + "/"
                + _q("PropertyGroup")
                + "/"
                + _q("HeatCapacityAndDerivedProp")
                + "/"
                + _q("ePropName")
            )
        )
        == CP_PROPERTY_LABEL
    ]
    if not cp_property_els:
        found_labels = sorted(
            {
                label
                for p in property_els
                if (label := _find_any_prop_name(p)) is not None
            }
        )
        return ThermoMLUnsupportedBlock(
            block_index=block_index,
            reason="not_heat_capacity_cp",
            detail=f"property labels present: {found_labels!r}",
        )
    if len(cp_property_els) > 1:
        return ThermoMLUnsupportedBlock(
            block_index=block_index,
            reason="multiple_cp_properties_in_block",
            detail=f"{len(cp_property_els)} Cp properties in one block",
        )
    (property_el,) = cp_property_els
    prop_number = _int(property_el.find(_q("nPropNumber")))
    if prop_number is None:
        raise ThermoMLMalformedDocumentError(
            f"PureOrMixtureData[{block_index}] Property has no nPropNumber"
        )

    method_id = property_el.find(
        _q("Property-MethodID") + "/" + _q("PropertyGroup") + "/" + _q(
            "HeatCapacityAndDerivedProp"
        )
    )
    assert method_id is not None  # guaranteed by the cp_property_els filter above

    critical_eval = method_id.find(_q("CriticalEvaluation"))
    if critical_eval is not None:
        return ThermoMLUnsupportedBlock(
            block_index=block_index,
            reason="critical_evaluation",
            detail="Property-MethodID carries CriticalEvaluation, not a raw observation",
        )

    e_method = _text(method_id.find(_q("eMethodName")))
    s_method = _text(method_id.find(_q("sMethodName")))
    prediction_el = method_id.find(_q("Prediction"))

    if e_method is not None:
        method_kind = "eMethodName"
        method_name: str | None = e_method
        prediction_type: str | None = None
        prediction_method_name: str | None = None
    elif s_method is not None:
        method_kind = "sMethodName"
        method_name = s_method
        prediction_type = None
        prediction_method_name = None
    elif prediction_el is not None:
        method_kind = "Prediction"
        method_name = None
        prediction_type = _text(prediction_el.find(_q("ePredictionType")))
        prediction_method_name = _text(
            prediction_el.find(_q("sPredictionMethodName"))
        )
        if prediction_type is None:
            raise ThermoMLMalformedDocumentError(
                f"PureOrMixtureData[{block_index}] Prediction has no ePredictionType"
            )
    else:
        raise ThermoMLMalformedDocumentError(
            f"PureOrMixtureData[{block_index}] Property-MethodID has none "
            "of eMethodName/sMethodName/CriticalEvaluation/Prediction"
        )

    # Phase resolution: the property's OWN PropPhaseID/ePropPhase wins
    # when present -- a single-component block can still list several
    # PhaseIDs (e.g. "Crystal"/"Gas" for a sublimation-adjacent block,
    # or "Crystal"/"Liquid"/"Air at 1 atmosphere" for a fusion block),
    # and PhaseID is unbounded (schema/ThermoML.xsd, PureOrMixtureData
    # element, ~line 1336), so falling back to "the first PhaseID"
    # would silently pick an arbitrary phase whenever a property omits
    # its own PropPhaseID. Fall back to the block's PhaseID only when
    # there is exactly one; more than one with no PropPhaseID is
    # genuinely ambiguous and the property is rejected, not guessed.
    prop_phase_raw = _text(
        property_el.find(_q("PropPhaseID") + "/" + _q("ePropPhase"))
    )
    block_phase_raws = [
        t for e in pomd_el.findall(_q("PhaseID") + "/" + _q("ePhase"))
        if (t := _text(e)) is not None
    ]
    if prop_phase_raw is not None:
        phase_raw: str | None = prop_phase_raw
    elif len(block_phase_raws) == 1:
        phase_raw = block_phase_raws[0]
    elif not block_phase_raws:
        raise ThermoMLMalformedDocumentError(
            f"PureOrMixtureData[{block_index}] has no PhaseID/ePhase and "
            "its Property has no PropPhaseID/ePropPhase"
        )
    else:
        return ThermoMLUnsupportedBlock(
            block_index=block_index,
            reason="ambiguous_phase",
            detail=(
                f"no PropPhaseID/ePropPhase on Property[{prop_number}]; "
                f"block PhaseIDs={block_phase_raws!r}"
            ),
        )
    if phase_raw not in SUPPORTED_PHASES:
        return ThermoMLUnsupportedBlock(
            block_index=block_index,
            reason="unsupported_phase",
            detail=f"ePhase={phase_raw!r}",
        )

    standard_state_raw = _text(property_el.find(_q("eStandardState")))

    uncertainty_definitions = _parse_uncertainty_definitions(property_el)

    constraints = _parse_constraints(pomd_el)
    variables = _parse_variables(pomd_el)

    pressure_source: Literal["constraint", "variable"] | None
    if _PRESSURE_LABEL in constraints:
        pressure_source = "constraint"
    elif _PRESSURE_LABEL in variables.values():
        pressure_source = "variable"
    else:
        pressure_source = None

    values = _parse_num_values(
        pomd_el,
        prop_number=prop_number,
        constraints=constraints,
        variables=variables,
    )

    return ThermoMLCpTable(
        block_index=block_index,
        prop_number=prop_number,
        compound=compound,
        property_label=CP_PROPERTY_LABEL,
        phase_raw=phase_raw,
        standard_state_raw=standard_state_raw,
        pressure_source=pressure_source,
        method_kind=method_kind,
        method_name=method_name,
        prediction_type=prediction_type,
        prediction_method_name=prediction_method_name,
        uncertainty_definitions=uncertainty_definitions,
        values=values,
    )


def _find_any_prop_name(property_el: ET.Element) -> str | None:
    """Find any ``ePropName``-shaped text under a ``Property`` element,
    regardless of which ``PropertyGroup`` choice member it came from --
    used only to report what a non-Cp block actually contained."""

    for el in property_el.iter():
        if el.tag == _q("ePropName"):
            return _text(el)
    return None


def _parse_uncertainty_definitions(
    property_el: ET.Element,
) -> tuple[ThermoMLUncertaintyDefinition, ...]:
    defs: list[ThermoMLUncertaintyDefinition] = []
    for comb_el in property_el.findall(_q("CombinedUncertainty")):
        assess_num = _int(comb_el.find(_q("nCombUncertAssessNum")))
        if assess_num is None:
            continue
        defs.append(
            ThermoMLUncertaintyDefinition(
                assess_num=assess_num,
                source="CombinedUncertainty",
                evaluator=_text(comb_el.find(_q("sCombUncertEvaluator"))),
                eval_method=(
                    _text(comb_el.find(_q("eCombUncertEvalMethod")))
                    or _text(comb_el.find(_q("sCombUncertEvalMethod")))
                ),
                coverage_factor=_float(comb_el.find(_q("nCombCoverageFactor"))),
                level_of_confidence_pct=_float(
                    comb_el.find(_q("nCombUncertLevOfConfid"))
                ),
            )
        )
    for prop_el in property_el.findall(_q("PropUncertainty")):
        assess_num = _int(prop_el.find(_q("nUncertAssessNum")))
        if assess_num is None:
            continue
        defs.append(
            ThermoMLUncertaintyDefinition(
                assess_num=assess_num,
                source="PropUncertainty",
                evaluator=_text(prop_el.find(_q("sUncertEvaluator"))),
                eval_method=_text(prop_el.find(_q("sUncertEvalMethod"))),
                coverage_factor=_float(prop_el.find(_q("nCoverageFactor"))),
                level_of_confidence_pct=_float(
                    prop_el.find(_q("nUncertLevOfConfid"))
                ),
            )
        )
    return tuple(defs)


def _parse_constraints(pomd_el: ET.Element) -> dict[str, float]:
    """Return ``{"Temperature, K": value, "Pressure, kPa": value}`` for
    whichever of those two constraint types the block fixes."""

    out: dict[str, float] = {}
    for c_el in pomd_el.findall(_q("Constraint")):
        label = _constraint_or_variable_label(c_el, id_tag="ConstraintID", type_tag="ConstraintType")
        if label not in (_TEMPERATURE_LABEL, _PRESSURE_LABEL):
            continue
        value = _float(c_el.find(_q("nConstraintValue")))
        if value is not None:
            out[label] = value
    return out


def _parse_variables(pomd_el: ET.Element) -> dict[int, str]:
    """Return ``{nVarNumber: "Temperature, K" | "Pressure, kPa" | ...}``
    for every ``Variable`` in the block, keyed by its own number."""

    out: dict[int, str] = {}
    for v_el in pomd_el.findall(_q("Variable")):
        number = _int(v_el.find(_q("nVarNumber")))
        if number is None:
            continue
        label = _constraint_or_variable_label(v_el, id_tag="VariableID", type_tag="VariableType")
        if label is not None:
            out[number] = label
    return out


def _constraint_or_variable_label(
    el: ET.Element, *, id_tag: str, type_tag: str
) -> str | None:
    """Extract the enumerated label (e.g. ``"Temperature, K"``) from a
    ``Constraint/ConstraintID/ConstraintType`` or
    ``Variable/VariableID/VariableType`` element, which is itself a
    ``choice`` of typed sub-elements (``eTemperature``, ``ePressure``,
    ``eComponentComposition``, ...). We only need the two thermal
    types; every other ``ConstraintVariableType`` branch is ignored
    here (composition variables are out of scope for a single-component
    Cp table)."""

    id_el = el.find(_q(id_tag))
    if id_el is None:
        return None
    type_el = id_el.find(_q(type_tag))
    if type_el is None:
        return None
    for child in type_el:
        text = _text(child)
        if text is not None:
            return text
    return None


def _parse_num_values(
    pomd_el: ET.Element,
    *,
    prop_number: int,
    constraints: dict[str, float],
    variables: dict[int, str],
) -> tuple[ThermoMLCpValue, ...]:
    out: list[ThermoMLCpValue] = []
    for record_index, nv_el in enumerate(pomd_el.findall(_q("NumValues")), start=1):
        var_values: dict[str, float] = dict(constraints)
        for vv_el in nv_el.findall(_q("VariableValue")):
            var_number = _int(vv_el.find(_q("nVarNumber")))
            var_value = _float(vv_el.find(_q("nVarValue")))
            if var_number is None or var_value is None:
                continue
            label = variables.get(var_number)
            if label in (_TEMPERATURE_LABEL, _PRESSURE_LABEL):
                var_values[label] = var_value

        temperature_k = var_values.get(_TEMPERATURE_LABEL)
        pressure_kpa = var_values.get(_PRESSURE_LABEL)

        for pv_el in nv_el.findall(_q("PropertyValue")):
            pv_prop_number = _int(pv_el.find(_q("nPropNumber")))
            if pv_prop_number != prop_number:
                continue
            value = _float(pv_el.find(_q("nPropValue")))
            digits = _int(pv_el.find(_q("nPropDigits")))
            if value is None or digits is None:
                raise ThermoMLMalformedDocumentError(
                    f"NumValues[{record_index}] PropertyValue missing "
                    "nPropValue/nPropDigits"
                )
            uncertainties = _parse_value_uncertainties(pv_el)
            out.append(
                ThermoMLCpValue(
                    record_index=record_index,
                    prop_number=prop_number,
                    value=value,
                    digits=digits,
                    temperature_k=temperature_k,
                    pressure_kpa=pressure_kpa,
                    uncertainties=uncertainties,
                )
            )
    return tuple(out)


def _parse_value_uncertainties(
    pv_el: ET.Element,
) -> tuple[ThermoMLValueUncertainty, ...]:
    out: list[ThermoMLValueUncertainty] = []
    for comb_el in pv_el.findall(_q("CombinedUncertainty")):
        assess_num = _int(comb_el.find(_q("nCombUncertAssessNum")))
        if assess_num is None:
            continue
        std_value = _float(comb_el.find(_q("nCombStdUncertValue")))
        expand_value = _float(comb_el.find(_q("nCombExpandUncertValue")))
        has_asymmetric = (
            comb_el.find(_q("AsymCombStdUncert")) is not None
            or comb_el.find(_q("AsymCombExpandUncert")) is not None
        )
        out.append(
            ThermoMLValueUncertainty(
                assess_num=assess_num,
                source="CombinedUncertainty",
                std_value=std_value,
                expand_value=expand_value,
                has_asymmetric=has_asymmetric,
            )
        )
    for prop_el in pv_el.findall(_q("PropUncertainty")):
        assess_num = _int(prop_el.find(_q("nUncertAssessNum")))
        if assess_num is None:
            continue
        std_value = _float(prop_el.find(_q("nStdUncertValue")))
        expand_value = _float(prop_el.find(_q("nExpandUncertValue")))
        has_asymmetric = (
            prop_el.find(_q("AsymStdUncert")) is not None
            or prop_el.find(_q("AsymExpandUncert")) is not None
        )
        out.append(
            ThermoMLValueUncertainty(
                assess_num=assess_num,
                source="PropUncertainty",
                std_value=std_value,
                expand_value=expand_value,
                has_asymmetric=has_asymmetric,
            )
        )
    return tuple(out)


__all__ = [
    "CP_PROPERTY_LABEL",
    "SUPPORTED_PHASES",
    "ThermoMLMalformedDocumentError",
    "parse_thermoml_document",
]
