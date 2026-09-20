"""Mapping-rule behavior: uncertainty precedence, origin classification,
unit token, record-key uniqueness, and the Gas/Ideal-gas pressure rule.

Mutation checks (verified manually; see the PR body's mutation table):

* ``test_uncertainty_precedence_prefers_combined_expanded`` /
  ``test_uncertainty_precedence_order_with_all_four_kinds_present``:
  swapping the precedence order in ``mapping._PRECEDENCE`` (combined/
  standard) makes these go red.
* ``test_prediction_statistical_mechanics_is_computed`` /
  ``test_prediction_other_type_is_estimated``: defaulting every
  ``Prediction`` block to ``scientific_origin=experimental`` makes
  these go red.
* ``test_gas_without_pressure_is_rejected`` /
  ``test_ideal_gas_without_pressure_is_accepted``: allowing a
  pressure-less "Gas" row through makes the first go red.
* ``test_smethodname_allowlist_hit_is_computed`` /
  ``test_smethodname_not_on_allowlist_is_rejected``: matching
  ``sMethodName`` by substring/keyword instead of an exact allowlist
  entry makes the second go red (e.g. a substring match on "thermo"
  would wrongly accept "STD" if it appeared in the same string).
* ``test_empty_uncertainty_entry_rejects_the_row``: falling back to
  ``by_key`` selection without the fatal pre-check makes this go red
  (the row would be built with ``uncertainty_kind=standard`` and
  ``scalar_uncertainty=None``).
* ``test_kpa_to_bar_is_exact_for_1020``: reverting to a ``* 0.01``
  multiplication makes this go red (``1020 * 0.01 !=
  10.2`` in binary64).
* ``test_mapping_rules_are_deterministic_per_row``: reverting to a
  single shared ``transformed`` accumulator snapshotted mid-loop makes
  this go red (row 0 would carry fewer rules than the last row).
"""

from __future__ import annotations

from pathlib import Path

from app.db.models.common import (
    ObservedStateBasis,
    ObservedUncertaintyAssessor,
    ObservedUncertaintyKind,
    ScientificOriginKind,
)
from app.importers.thermoml.mapping import map_document
from app.importers.thermoml.parser import parse_thermoml_document
from app.schemas.entities.molecular_property_observation import (
    HEAT_CAPACITY_CP_UNIT,
    MolecularPropertyObservationCreate,
)

FIXTURES = Path(__file__).parents[3] / "app" / "importers" / "thermoml" / "fixtures"
DOI = "10.1016/j.fluid.2016.07.034"


def _map(fixture_name: str):
    xml_bytes = (FIXTURES / fixture_name).read_bytes()
    document = parse_thermoml_document(xml_bytes)
    return map_document(document, doi=DOI)


def test_uncertainty_precedence_prefers_combined_expanded():
    """``cp_gas_single_component.xml`` carries ONLY a per-value
    ``CombinedUncertainty`` with ``nCombExpandUncertValue`` -- so the
    typed columns must land on ``combined_expanded``, with the
    definition's coverage factor and level of confidence carried
    through."""

    result = _map("cp_gas_single_component.xml")
    assert len(result.payloads) == 3
    for payload in result.payloads:
        assert payload.uncertainty_kind == ObservedUncertaintyKind.combined_expanded
        assert payload.uncertainty_coverage_factor == 2.0
        assert payload.uncertainty_level_of_confidence_pct == 95.0
        assert payload.scalar_uncertainty is not None
        # CombinedUncertainty -> source_evaluator (finding 6: assessor mapping).
        assert payload.uncertainty_assessor == ObservedUncertaintyAssessor.source_evaluator


def test_standard_uncertainty_carries_no_coverage_factor():
    """``cp_ideal_gas_no_pressure.xml`` carries only a ``PropUncertainty``
    with ``nStdUncertValue`` -- kind ``standard``, and the schema CHECK
    ``ck_mpo_coverage_factor_only_expanded`` forbids a coverage factor
    on a non-expanded kind, so it must be ``None`` here regardless of
    what the definition states."""

    result = _map("cp_ideal_gas_no_pressure.xml")
    assert len(result.payloads) == 1
    payload = result.payloads[0]
    assert payload.uncertainty_kind == ObservedUncertaintyKind.standard
    assert payload.uncertainty_coverage_factor is None
    assert payload.scalar_uncertainty == 0.15
    # PropUncertainty -> source_author (finding 6: assessor mapping).
    assert payload.uncertainty_assessor == ObservedUncertaintyAssessor.source_author


def test_prediction_statistical_mechanics_is_computed():
    result = _map("cp_prediction_only.xml")
    assert len(result.payloads) == 1
    payload = result.payloads[0]
    assert payload.scientific_origin == ScientificOriginKind.computed
    assert payload.method_note == "Statistical mechanics"


def test_prediction_other_type_is_estimated(monkeypatch):
    """A ``Prediction`` type outside {Ab initio, Statistical mechanics}
    (e.g. "Correlation") must map to ``estimated``, not ``computed``
    and not ``experimental``."""

    import app.importers.thermoml.mapping as mapping_mod
    from app.importers.thermoml.models import (
        ThermoMLCitation,
        ThermoMLCompound,
        ThermoMLCpTable,
        ThermoMLCpValue,
        ThermoMLParsedDocument,
    )

    compound = ThermoMLCompound(
        n_comp_index=1,
        cas_rn=None,
        standard_inchi=None,
        standard_inchi_key="TESTKEY-UHFFFAOYSA-N",
        smiles=(),
        formula_molec=None,
        common_names=(),
    )
    table = ThermoMLCpTable(
        block_index=1,
        prop_number=1,
        compound=compound,
        property_label="Molar heat capacity at constant pressure, J/K/mol",
        phase_raw="Ideal gas",
        standard_state_raw=None,
        method_kind="Prediction",
        method_name=None,
        prediction_type="Correlation",
        prediction_method_name="group contribution fit",
        values=(
            ThermoMLCpValue(
                record_index=1,
                prop_number=1,
                value=50.0,
                digits=4,
                temperature_k=300.0,
                pressure_kpa=None,
            ),
        ),
    )
    document = ThermoMLParsedDocument(
        citation=ThermoMLCitation(doi=DOI, title=None, year=None, journal=None),
        compounds=(compound,),
        cp_tables=(table,),
    )
    result = mapping_mod.map_document(document, doi=DOI)
    assert len(result.payloads) == 1
    payload = result.payloads[0]
    assert payload.scientific_origin == ScientificOriginKind.estimated
    assert payload.method_note == "Correlation"


def test_scalar_unit_is_exactly_j_mol_k():
    result = _map("cp_gas_single_component.xml")
    assert HEAT_CAPACITY_CP_UNIT == "J/mol/K"
    for payload in result.payloads:
        assert payload.scalar_unit == "J/mol/K"


def test_record_keys_are_unique_within_and_across_tables():
    result = _map("cp_gas_single_component.xml")
    keys = [p.external_source_record_key for p in result.payloads]
    assert len(keys) == len(set(keys))
    assert all(k is not None and k.startswith(DOI + "#PureOrMixtureData[1]") for k in keys)


def test_gas_without_pressure_is_rejected():
    result = _map("cp_gas_missing_pressure.xml")
    assert result.payloads == []
    reasons = {r["reason"] for r in result.report.rejected}
    assert reasons == {"gas_missing_pressure"}


def test_ideal_gas_without_pressure_is_accepted():
    result = _map("cp_ideal_gas_no_pressure.xml")
    assert len(result.payloads) == 1
    assert result.payloads[0].pressure_bar is None
    assert result.report.rejected == []


def test_pressure_conversion_kpa_to_bar():
    result = _map("cp_gas_single_component.xml")
    for payload in result.payloads:
        # Every value in the fixture is fixed at 101.325 kPa.
        assert payload.pressure_bar == 1.01325


def test_every_payload_validates_against_the_wire_schema():
    """Every payload emitted by the mapper is ALREADY a
    ``MolecularPropertyObservationCreate`` instance (mapping.py builds
    them directly), so this test additionally round-trips each one
    through dict -> model construction to catch any accidental
    ``model_construct``/bypass-validation shortcut."""

    for fixture_name in (
        "cp_gas_single_component.xml",
        "cp_ideal_gas_no_pressure.xml",
        "cp_prediction_only.xml",
    ):
        result = _map(fixture_name)
        assert result.payloads, f"{fixture_name} should produce payloads"
        for payload in result.payloads:
            assert isinstance(payload, MolecularPropertyObservationCreate)
            revalidated = MolecularPropertyObservationCreate.model_validate(
                payload.model_dump()
            )
            assert revalidated == payload


# ---------------------------------------------------------------------------
# Finding 1 (BLOCKING): RegNum/nOrgNum Component/Compound keying
# ---------------------------------------------------------------------------


def test_regnum_keyed_fixture_resolves_the_compound():
    """``cp_gas_single_component.xml`` is keyed by ``RegNum/nOrgNum``
    (matching the real archive), not ``nCompIndex``. A document that
    only supports the ``nCompIndex`` join would raise
    ``ThermoMLMalformedDocumentError`` here instead of returning a
    parsed table."""

    result = _map("cp_gas_single_component.xml")
    assert len(result.payloads) == 3
    assert result.literature.doi == "10.1016/j.fluid.2016.07.034"


def test_ncompindex_keyed_fixture_still_resolves():
    """``cp_ideal_gas_no_pressure.xml`` is the one deliberately kept
    ``nCompIndex``-keyed fixture (see its own docstring) -- the parser
    must still support this join path, not just RegNum/nOrgNum."""

    result = _map("cp_ideal_gas_no_pressure.xml")
    assert len(result.payloads) == 1


# ---------------------------------------------------------------------------
# Finding 2: phase resolution (PropPhaseID over block PhaseID; ambiguous
# multi-phase blocks rejected, not guessed)
# ---------------------------------------------------------------------------


def test_ambiguous_block_phase_with_no_prop_phase_is_unsupported():
    xml_bytes = (FIXTURES / "cp_ambiguous_phase.xml").read_bytes()
    document = parse_thermoml_document(xml_bytes)
    assert document.cp_tables == ()
    assert len(document.unsupported) == 1
    unsupported = document.unsupported[0]
    assert unsupported.reason == "ambiguous_phase"
    assert "Crystal" in unsupported.detail
    assert "Gas" in unsupported.detail


# ---------------------------------------------------------------------------
# Finding 3: eStandardState present and not "Pure compound" is rejected
# ---------------------------------------------------------------------------


def test_unsupported_standard_state_is_rejected():
    result = _map("cp_unsupported_standard_state.xml")
    assert result.payloads == []
    assert len(result.report.rejected) == 1
    rejected = result.report.rejected[0]
    assert rejected["reason"] == "unsupported_standard_state"
    assert "Infinite dilution solute" in rejected["detail"]


# ---------------------------------------------------------------------------
# Finding 4: a per-value uncertainty entry with no value child rejects
# the row, never chooses kind=standard with scalar_uncertainty=None
# ---------------------------------------------------------------------------


def test_empty_uncertainty_entry_rejects_the_row():
    result = _map("cp_empty_uncertainty_value.xml")
    # Row 1 (290 K) has a real PropUncertainty and is kept; row 2
    # (310 K)'s PropUncertainty carries no value at all and must be
    # rejected, never mapped with uncertainty_kind=standard/None.
    assert len(result.payloads) == 1
    assert result.payloads[0].temperature_k == 290.0
    assert len(result.report.rejected) == 1
    rejected = result.report.rejected[0]
    assert rejected["reason"] == "empty_uncertainty_value"
    assert "NumValues[2]" in rejected["record_key"]
    # And the one payload that WAS built never has an uncertainty kind
    # paired with no value.
    for payload in result.payloads:
        if payload.uncertainty_kind is not None:
            assert payload.scalar_uncertainty is not None


# ---------------------------------------------------------------------------
# Finding 5: observed-method-string allowlist (no keyword matching)
# ---------------------------------------------------------------------------


def test_smethodname_allowlist_hit_is_computed():
    """The benzene-modelled fixture's ``sMethodName`` is exactly
    "statistical thermodynamics", which IS on the allowlist -> computed,
    with the rule id recorded in both ``method_note`` and
    ``raw_payload_json["mapping"]["rules"]``."""

    result = _map("cp_ideal_gas_statistical_thermodynamics.xml")
    assert len(result.payloads) == 4
    for payload in result.payloads:
        assert payload.scientific_origin == ScientificOriginKind.computed
        assert payload.method_note == "statistical thermodynamics"
        assert "origin.smethodname_allowlist.v1" in payload.raw_payload_json["mapping"]["rules"]


def test_smethodname_not_on_allowlist_is_rejected():
    """"STD" is an observed archive string (DOI
    10.1016/j.fluid.2015.06.045) but is deliberately NOT on the
    allowlist -- the whole table is rejected, with the verbatim string
    in the report, rather than guessed at via a keyword/substring
    match (e.g. matching because "STD" contains no recognizable
    substring of "statistical thermodynamics" -- a substring-based
    implementation could easily be tricked either way, which is why
    the rule is an exact-string allowlist)."""

    result = _map("cp_unrecognized_smethodname.xml")
    assert result.payloads == []
    assert len(result.report.rejected) == 1
    rejected = result.report.rejected[0]
    assert rejected["reason"] == "unrecognized_smethodname"
    assert "STD" in rejected["detail"]


def test_smethodname_near_miss_superstring_is_rejected_not_matched():
    """A string that CONTAINS an allowlist entry as a substring but is
    not an exact match ("statistical thermodynamics (approximate)")
    must still be rejected. A substring-matching implementation
    (``allow_str in method_name`` instead of an exact dict lookup)
    would wrongly accept this -- this is the direction
    ``test_smethodname_not_on_allowlist_is_rejected``'s "STD" case
    cannot catch, since "STD" is not a superstring of any allowlist
    entry."""

    from app.importers.thermoml.mapping import map_cp_table
    from app.importers.thermoml.models import (
        ThermoMLCompound,
        ThermoMLCpTable,
        ThermoMLCpValue,
    )

    compound = ThermoMLCompound(
        n_comp_index=1,
        cas_rn=None,
        standard_inchi=None,
        standard_inchi_key="TESTKEY-UHFFFAOYSA-N",
        smiles=(),
        formula_molec=None,
        common_names=(),
    )
    table = ThermoMLCpTable(
        block_index=1,
        prop_number=1,
        compound=compound,
        property_label="Molar heat capacity at constant pressure, J/K/mol",
        phase_raw="Ideal gas",
        standard_state_raw=None,
        method_kind="sMethodName",
        method_name="statistical thermodynamics (approximate)",
        prediction_type=None,
        prediction_method_name=None,
        values=(
            ThermoMLCpValue(
                record_index=1,
                prop_number=1,
                value=50.0,
                digits=4,
                temperature_k=300.0,
                pressure_kpa=None,
            ),
        ),
    )
    payloads, report, _identity = map_cp_table(table, doi=DOI)
    assert payloads == []
    assert len(report.rejected) == 1
    assert report.rejected[0]["reason"] == "unrecognized_smethodname"


# ---------------------------------------------------------------------------
# Finding 6: mutation-survivor hardening
# ---------------------------------------------------------------------------


def test_uncertainty_precedence_order_with_all_four_kinds_present():
    """``cp_uncertainty_precedence_order.xml`` carries all four
    uncertainty kinds on ONE row: combined_expanded=2.50,
    combined_standard=1.10, expanded=2.80, standard=1.40. Precedence
    must select combined_expanded (2.50), not merely "a" kind."""

    result = _map("cp_uncertainty_precedence_order.xml")
    assert len(result.payloads) == 1
    payload = result.payloads[0]
    assert payload.uncertainty_kind == ObservedUncertaintyKind.combined_expanded
    assert payload.scalar_uncertainty == 2.50


def test_pressure_from_a_per_value_variable_differs_across_rows():
    """``cp_gas_variable_pressure.xml`` carries pressure as a per-row
    Variable (1150/2450/3050 kPa -- invented, not the real article's own
    endpoint pressures), not a block-wide Constraint -- ``pressure_bar``
    must differ across rows, and each payload's
    ``raw_payload_json["thermoml"]["pressure_source"]`` must say
    "variable"."""

    result = _map("cp_gas_variable_pressure.xml")
    assert len(result.payloads) == 3
    pressures = [p.pressure_bar for p in result.payloads]
    assert len(set(pressures)) == 3, "pressures must differ across rows"
    assert pressures == [11.5, 24.5, 30.5]
    for payload in result.payloads:
        assert payload.raw_payload_json["thermoml"]["pressure_source"] == "variable"


def test_species_entry_id_is_always_none():
    """Identity is a hint only -- ``species_entry_id`` must be ``None``
    on every payload this importer builds, across every fixture."""

    for fixture_name in (
        "cp_gas_single_component.xml",
        "cp_ideal_gas_no_pressure.xml",
        "cp_prediction_only.xml",
        "cp_ideal_gas_statistical_thermodynamics.xml",
        "cp_gas_variable_pressure.xml",
        "cp_uncertainty_precedence_order.xml",
    ):
        result = _map(fixture_name)
        assert result.payloads, f"{fixture_name} should produce payloads"
        for payload in result.payloads:
            assert payload.species_entry_id is None


def test_literature_fragment_carries_title_year_journal_authors():
    result = _map("cp_gas_single_component.xml")
    lit = result.literature
    assert lit.doi == "10.1016/j.fluid.2016.07.034"
    assert lit.title == (
        "Fixture: flow-calorimetry heat capacities of a fluorinated ethane "
        "(invented values)"
    )
    assert lit.year == 2016
    assert lit.journal == "Fluid Phase Equilib."
    assert lit.authors == ["Doe, J.", "Roe, R."]


def test_state_label_raw_equals_verbatim_phase():
    gas_result = _map("cp_gas_single_component.xml")
    for payload in gas_result.payloads:
        assert payload.state_label_raw == "Gas"

    ideal_gas_result = _map("cp_ideal_gas_no_pressure.xml")
    for payload in ideal_gas_result.payloads:
        assert payload.state_label_raw == "Ideal gas"


def test_external_source_doi_is_set():
    result = _map("cp_gas_single_component.xml")
    for payload in result.payloads:
        assert payload.external_source_doi == DOI


def test_state_basis_enum_values():
    """``ObservedStateBasis`` values, asserted directly (not just
    identity/enum-membership): "Gas" -> real_gas, "Ideal gas" ->
    ideal_gas."""

    gas_result = _map("cp_gas_single_component.xml")
    for payload in gas_result.payloads:
        assert payload.state_basis == ObservedStateBasis.real_gas
        assert payload.state_basis.value == "real_gas"

    ideal_gas_result = _map("cp_ideal_gas_no_pressure.xml")
    for payload in ideal_gas_result.payloads:
        assert payload.state_basis == ObservedStateBasis.ideal_gas
        assert payload.state_basis.value == "ideal_gas"


def test_record_keys_are_unique_asserts_nonempty_first():
    """``test_record_keys_are_unique_within_and_across_tables`` (above)
    already checks uniqueness; this test additionally guards against
    that check passing vacuously on an empty list."""

    result = _map("cp_gas_single_component.xml")
    keys = [p.external_source_record_key for p in result.payloads]
    assert len(keys) > 0, "the keys list must not be empty"
    assert len(keys) == len(set(keys))


# ---------------------------------------------------------------------------
# Finding 7: exact kPa -> bar conversion
# ---------------------------------------------------------------------------


def test_kpa_to_bar_is_exact_for_1020():
    """1020 kPa -> 10.2 bar EXACTLY. ``1020 * 0.01`` is
    ``10.200000000000001`` in IEEE-754 binary64; ``1020 / 100.0`` is
    exactly ``10.2``. This is a pure floating-point property of the
    conversion arithmetic (not tied to any fixture or real article), so
    it is exercised directly through ``map_cp_table`` on a synthetic
    in-memory table -- the same style ``test_prediction_other_type_is_estimated``
    uses -- rather than through an XML fixture."""

    from app.importers.thermoml.mapping import map_cp_table
    from app.importers.thermoml.models import (
        ThermoMLCompound,
        ThermoMLCpTable,
        ThermoMLCpValue,
    )

    assert 1020 / 100.0 == 10.2
    assert 1020 * 0.01 != 10.2

    compound = ThermoMLCompound(
        n_comp_index=1,
        cas_rn=None,
        standard_inchi=None,
        standard_inchi_key="TESTKEY-UHFFFAOYSA-N",
        smiles=(),
        formula_molec=None,
        common_names=(),
    )
    table = ThermoMLCpTable(
        block_index=1,
        prop_number=1,
        compound=compound,
        property_label="Molar heat capacity at constant pressure, J/K/mol",
        phase_raw="Gas",
        standard_state_raw=None,
        method_kind="eMethodName",
        method_name="Flow calorimetry",
        prediction_type=None,
        prediction_method_name=None,
        pressure_source="variable",
        values=(
            ThermoMLCpValue(
                record_index=1,
                prop_number=1,
                value=50.0,
                digits=4,
                temperature_k=300.0,
                pressure_kpa=1020.0,
            ),
        ),
    )
    payloads, _report, _identity = map_cp_table(table, doi=DOI)
    assert len(payloads) == 1
    assert payloads[0].pressure_bar == 10.2


# ---------------------------------------------------------------------------
# Finding 8: per-row deterministic mapping rules
# ---------------------------------------------------------------------------


def test_mapping_rules_are_deterministic_per_row():
    """Row 0 and the last row of the same table must carry the SAME
    unit rule (``unit.j_mol_k.identity``) -- a shared, order-dependent
    accumulator snapshotted mid-loop would give row 0 fewer rules than
    a later row that triggered an additional rule first (e.g.
    ``pressure.kpa_to_bar.v1``)."""

    result = _map("cp_gas_variable_pressure.xml")
    assert len(result.payloads) == 3
    first_rules = result.payloads[0].raw_payload_json["mapping"]["rules"]
    last_rules = result.payloads[-1].raw_payload_json["mapping"]["rules"]
    assert "unit.j_mol_k.identity" in first_rules
    assert "unit.j_mol_k.identity" in last_rules
    # Every row in this fixture has a pressure, so the full rule set is
    # identical across rows -- the point being it does NOT grow from
    # row 0 to the last row the way an order-dependent accumulator
    # snapshot would.
    assert first_rules == last_rules


def test_mapping_rules_reflect_only_that_rows_own_conditions():
    """Stronger version of the row-0-vs-last-row check above: two rows
    in the SAME table where only one triggers ``pressure.kpa_to_bar.v1``
    (row 0 has a pressure, row 1 does not -- both legal for an "Ideal
    gas" table, which never requires pressure). A shared accumulator
    that is never reset per row would leak row 0's
    ``pressure.kpa_to_bar.v1`` into row 1's own rules list (or, in the
    opposite iteration order, would omit it from row 0 until row 1 had
    already added it)."""

    from app.importers.thermoml.mapping import map_cp_table
    from app.importers.thermoml.models import (
        ThermoMLCompound,
        ThermoMLCpTable,
        ThermoMLCpValue,
    )

    compound = ThermoMLCompound(
        n_comp_index=1,
        cas_rn=None,
        standard_inchi=None,
        standard_inchi_key="TESTKEY-UHFFFAOYSA-N",
        smiles=(),
        formula_molec=None,
        common_names=(),
    )
    table = ThermoMLCpTable(
        block_index=1,
        prop_number=1,
        compound=compound,
        property_label="Molar heat capacity at constant pressure, J/K/mol",
        phase_raw="Ideal gas",
        standard_state_raw=None,
        method_kind="eMethodName",
        method_name="Flow calorimetry",
        prediction_type=None,
        prediction_method_name=None,
        values=(
            ThermoMLCpValue(
                record_index=1,
                prop_number=1,
                value=50.0,
                digits=4,
                temperature_k=300.0,
                pressure_kpa=100.0,
            ),
            ThermoMLCpValue(
                record_index=2,
                prop_number=1,
                value=55.0,
                digits=4,
                temperature_k=310.0,
                pressure_kpa=None,
            ),
        ),
    )
    payloads, _report, _identity = map_cp_table(table, doi=DOI)
    assert len(payloads) == 2
    row0_rules = payloads[0].raw_payload_json["mapping"]["rules"]
    row1_rules = payloads[1].raw_payload_json["mapping"]["rules"]
    assert "pressure.kpa_to_bar.v1" in row0_rules
    assert "pressure.kpa_to_bar.v1" not in row1_rules


# ---------------------------------------------------------------------------
# Finding 9: mapping report records the pressure source per property
# ---------------------------------------------------------------------------


def test_pressure_source_recorded_constraint_vs_variable():
    constraint_result = _map("cp_gas_single_component.xml")
    for payload in constraint_result.payloads:
        assert payload.raw_payload_json["thermoml"]["pressure_source"] == "constraint"

    variable_result = _map("cp_gas_variable_pressure.xml")
    for payload in variable_result.payloads:
        assert payload.raw_payload_json["thermoml"]["pressure_source"] == "variable"

    no_pressure_result = _map("cp_ideal_gas_no_pressure.xml")
    for payload in no_pressure_result.payloads:
        assert payload.raw_payload_json["thermoml"]["pressure_source"] is None


def test_identity_hint_uses_tckdb_key_names_and_keeps_thermoml_names():
    """The resolver reads ``inchikey``; the source's element names survive
    under ``thermoml_identifiers``. Renaming ``inchikey`` turns this red."""
    from app.importers.thermoml.mapping import _identity_hint
    from app.importers.thermoml.models import ThermoMLCompound

    compound = ThermoMLCompound(
        **{
            **{f.name: None for f in ThermoMLCompound.__dataclass_fields__.values()},
            "standard_inchi_key": "UHOVQNZJYSORNB-UHFFFAOYSA-N",
            "standard_inchi": "InChI=1S/C6H6/c1-2-4-6-5-3-1/h1-6H",
            "smiles": ("c1ccccc1",),
            "formula_molec": "C6H6",
            "common_names": ("benzene",),
            "cas_rn": "71-43-2",
        }
    )
    hint = _identity_hint(compound)
    assert hint["inchikey"] == "UHOVQNZJYSORNB-UHFFFAOYSA-N"
    assert hint["inchi"].startswith("InChI=1S/C6H6")
    assert hint["smiles"] == ["c1ccccc1"]
    assert hint["formula"] == "C6H6"
    assert hint["cas"] == "71-43-2"
    assert hint["names"] == ["benzene"]
    assert hint["thermoml_identifiers"]["sStandardInChIKey"] == hint["inchikey"]
    assert "standard_inchi_key" not in hint
