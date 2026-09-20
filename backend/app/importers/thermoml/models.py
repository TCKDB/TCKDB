"""In-memory record shapes for a parsed ThermoML document.

Frozen dataclasses, not Pydantic models: these are internal parser
output, never a wire schema. Every field that carries a ThermoML
enumeration or free-text value is kept **verbatim** -- unit
conversion, state-basis classification and origin classification are
``mapping.py``'s job, not the parser's.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


@dataclass(frozen=True)
class ThermoMLCitation:
    """The ``Citation`` block of a ``DataReport``.

    :param doi: ``sDOI``, verbatim.
    :param title: ``sTitle``, verbatim.
    :param year: ``yrPubYr``, as an integer (the XSD type is
        ``xsd:gYear``).
    :param journal: ``sPubName``, verbatim.
    :param authors: ``sAuthor`` elements, in document order, verbatim.
    """

    doi: str | None
    title: str | None
    year: int | None
    journal: str | None
    authors: tuple[str, ...] = ()


@dataclass(frozen=True)
class ThermoMLCompound:
    """One ``Compound`` block. All identifiers are kept verbatim.

    ``Compound`` is joined to a block's ``Component`` by whichever of
    two keys the source used -- the XSD offers both, as a genuine
    choice, and the real ThermoML archive uses ``RegNum/nOrgNum``, not
    ``nCompIndex`` (see ``parser.py``'s module docstring). A document
    may carry either or both keys per compound.

    :param n_comp_index: ``nCompIndex`` -- the integer other blocks
        use to reference this compound (``Component/nCompIndex``).
        ``None`` when the source keys this compound by ``RegNum``
        instead (the common case in the real archive).
    :param reg_org_num: ``RegNum/nOrgNum`` -- the archive's own
        per-document organization number, used to reference this
        compound from ``Component/RegNum/nOrgNum``. Distinct from
        ``cas_rn`` (``RegNum/nCASRNum``), which is a hint only and
        never used for the Component/Compound join.
    :param cas_rn: ``RegNum/nCASRNum``, as the exact digit string the
        source carries (CAS numbers are not TCKDB identity; this is a
        hint only).
    :param standard_inchi: ``sStandardInChI``, verbatim.
    :param standard_inchi_key: ``sStandardInChIKey``, verbatim.
    :param smiles: ``sSmiles`` elements, verbatim, in document order.
    :param formula_molec: ``sFormulaMolec``, verbatim.
    :param common_names: ``sCommonName`` elements, verbatim, in
        document order.
    """

    n_comp_index: int | None
    cas_rn: str | None
    standard_inchi: str | None
    standard_inchi_key: str | None
    smiles: tuple[str, ...]
    formula_molec: str | None
    common_names: tuple[str, ...]
    reg_org_num: int | None = None


#: The two ThermoML elements a per-value uncertainty can appear under.
UncertaintySource = Literal["CombinedUncertainty", "PropUncertainty"]


@dataclass(frozen=True)
class ThermoMLUncertaintyDefinition:
    """A Property-level uncertainty *definition*, joined to per-value
    uncertainty entries in ``NumValues`` by ``assess_num``.

    Mirrors either a ``CombinedUncertainty`` element (source
    ``"CombinedUncertainty"``) or a ``PropUncertainty`` element
    (``PropVarUncertaintyType``, source ``"PropUncertainty"``) that is
    a direct child of ``Property``.

    :param assess_num: ``nCombUncertAssessNum`` or ``nUncertAssessNum``.
    :param source: which ThermoML element this definition came from.
    :param evaluator: ``sCombUncertEvaluator`` or ``sUncertEvaluator``,
        verbatim. Used to classify
        :class:`~app.db.models.common.ObservedUncertaintyAssessor`.
    :param eval_method: ``eCombUncertEvalMethod``/``sCombUncertEvalMethod``
        or ``sUncertEvalMethod``, verbatim.
    :param coverage_factor: ``nCombCoverageFactor`` or
        ``nCoverageFactor``.
    :param level_of_confidence_pct: ``nCombUncertLevOfConfid`` or
        ``nUncertLevOfConfid``.
    """

    assess_num: int
    source: UncertaintySource
    evaluator: str | None
    eval_method: str | None
    coverage_factor: float | None
    level_of_confidence_pct: float | None


@dataclass(frozen=True)
class ThermoMLValueUncertainty:
    """One per-value uncertainty entry inside a ``NumValues/PropertyValue``.

    Joined to a :class:`ThermoMLUncertaintyDefinition` sharing the same
    ``(source, assess_num)`` pair.

    :param std_value: ``nStdUncertValue`` / ``nCombStdUncertValue``.
    :param expand_value: ``nExpandUncertValue`` / ``nCombExpandUncertValue``.
    :param has_asymmetric: ``True`` when the source used the
        asymmetric-uncertainty branch (``AsymStdUncert`` /
        ``AsymExpandUncert`` / ``AsymCombStdUncert`` /
        ``AsymCombExpandUncert``) instead of the symmetric one. Asymmetric
        uncertainty is unsupported (see ``mapping.py``); this flag lets
        the mapper record it without re-parsing the XML.
    """

    assess_num: int
    source: UncertaintySource
    std_value: float | None
    expand_value: float | None
    has_asymmetric: bool = False


@dataclass(frozen=True)
class ThermoMLCpValue:
    """One ``NumValues`` row for the table's Cp property.

    :param record_index: 1-based position of this ``NumValues`` block
        within the ``PureOrMixtureData`` block (``i`` in the
        ``NumValues[i]`` record-key segment).
    :param prop_number: ``PropertyValue/nPropNumber`` -- must equal
        the table's ``ThermoMLCpTable.prop_number``.
    :param value: ``nPropValue``.
    :param digits: ``nPropDigits``.
    :param temperature_k: Value of whichever Constraint or Variable
        carries ``eTemperature`` = ``"Temperature, K"``, resolved for
        this row. ``None`` if the block states no temperature (should
        not occur for a Cp(T) table but is not assumed away).
    :param pressure_kpa: Value of whichever Constraint or Variable
        carries ``ePressure`` = ``"Pressure, kPa"``, resolved for this
        row. ``None`` when the block states no pressure at all.
    :param uncertainties: Every per-value uncertainty entry present on
        this ``PropertyValue``, regardless of whether the mapper's
        precedence rule will use it.
    """

    record_index: int
    prop_number: int
    value: float
    digits: int
    temperature_k: float | None
    pressure_kpa: float | None
    uncertainties: tuple[ThermoMLValueUncertainty, ...] = ()


@dataclass(frozen=True)
class ThermoMLCpTable:
    """One single-component ``PureOrMixtureData`` block with one Cp
    property, ready for ``mapping.py``.

    :param block_index: 1-based position of this ``PureOrMixtureData``
        element within the document (the ``n`` in
        ``PureOrMixtureData[n]``).
    :param prop_number: ``Property/nPropNumber`` for the Cp property.
    :param compound: The block's single component (resolved from
        ``Component/nCompIndex`` against the document's ``Compound``
        list).
    :param property_label: ``ePropName``, verbatim (expected to be
        ``"Molar heat capacity at constant pressure, J/K/mol"`` --
        callers should not assume this without checking, since
        ``parser.py`` only guarantees it is *a* recognized
        heat-capacity-family label).
    :param phase_raw: ``PhaseID/ePhase``, verbatim (``"Ideal gas"`` or
        ``"Gas"`` -- other phases are unsupported and never reach a
        :class:`ThermoMLCpTable`).
    :param standard_state_raw: ``Property/eStandardState``, verbatim,
        if present.
    :param method_kind: ``"eMethodName"``, ``"sMethodName"``, or
        ``"Prediction"`` -- which branch of the
        ``Property-MethodID/.../HeatCapacityAndDerivedProp`` choice
        supplied the method/origin information. ``CriticalEvaluation``
        blocks never reach a :class:`ThermoMLCpTable` (see
        ``parser.py``).
    :param method_name: ``eMethodName``/``sMethodName`` text, verbatim,
        when ``method_kind`` is one of those two.
    :param prediction_type: ``Prediction/ePredictionType``, verbatim,
        when ``method_kind == "Prediction"``.
    :param prediction_method_name: ``Prediction/sPredictionMethodName``,
        verbatim, when present.
    :param uncertainty_definitions: Every Property-level uncertainty
        definition on this Property.
    :param values: The table's ``NumValues`` rows, in document order.
    :param pressure_source: ``"constraint"`` when the block fixes
        pressure via a ``Constraint``, ``"variable"`` when it varies
        per row via a ``Variable``, ``None`` when the block states no
        pressure at all. Recorded for the mapping report (see
        ``mapping.py``) -- never inferred from whether
        ``ThermoMLCpValue.pressure_kpa`` happens to be set, since that
        conflates the two sources.
    """

    block_index: int
    prop_number: int
    compound: ThermoMLCompound
    property_label: str
    phase_raw: str
    standard_state_raw: str | None
    method_kind: Literal["eMethodName", "sMethodName", "Prediction"]
    method_name: str | None
    prediction_type: str | None
    prediction_method_name: str | None
    uncertainty_definitions: tuple[ThermoMLUncertaintyDefinition, ...] = ()
    values: tuple[ThermoMLCpValue, ...] = ()
    pressure_source: Literal["constraint", "variable"] | None = None


@dataclass(frozen=True)
class ThermoMLUnsupportedBlock:
    """A ``PureOrMixtureData`` (or ``ReactionData``) block the parser
    recognized but does not turn into a :class:`ThermoMLCpTable`.

    Never raised on -- collected so the caller sees exactly what was
    skipped and why, with the source's own strings preserved.

    :param block_index: 1-based position of the block in the document.
    :param reason: Machine token naming why the block was skipped
        (``"multi_component"``, ``"not_heat_capacity_cp"``,
        ``"unsupported_phase"``, ``"critical_evaluation"``,
        ``"reaction_data"``, ...).
    :param detail: Free-text elaboration, including verbatim source
        strings (property names, phase names, component count) where
        available.
    """

    block_index: int
    reason: str
    detail: str


@dataclass(frozen=True)
class ThermoMLParsedDocument:
    """Top-level parser output for one ThermoML XML document.

    :param citation: The document's ``Citation`` block.
    :param compounds: Every ``Compound`` block, in document order.
    :param cp_tables: Every single-component Cp block the parser
        could resolve.
    :param unsupported: Every block the parser recognized but skipped,
        never raised on.
    :param warnings: Free-text warnings that don't rise to a skipped
        block (e.g. a compound referenced by index but not declared).
    """

    citation: ThermoMLCitation
    compounds: tuple[ThermoMLCompound, ...]
    cp_tables: tuple[ThermoMLCpTable, ...]
    unsupported: tuple[ThermoMLUnsupportedBlock, ...] = ()
    warnings: tuple[str, ...] = field(default_factory=tuple)


__all__ = [
    "ThermoMLCitation",
    "ThermoMLCompound",
    "ThermoMLCpTable",
    "ThermoMLCpValue",
    "ThermoMLParsedDocument",
    "ThermoMLUncertaintyDefinition",
    "ThermoMLUnsupportedBlock",
    "ThermoMLValueUncertainty",
    "UncertaintySource",
]
