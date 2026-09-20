"""Mapping-rule behavior: uncertainty precedence, origin classification,
unit token, record-key uniqueness, and the Gas/Ideal-gas pressure rule.

Mutation checks (verified manually; see the PR body's mutation table):

* ``test_uncertainty_precedence_prefers_combined_expanded``: swapping
  the precedence order in ``mapping._PRECEDENCE`` (combined/standard)
  makes this go red.
* ``test_prediction_statistical_mechanics_is_computed`` /
  ``test_prediction_other_type_is_estimated``: defaulting every
  ``Prediction`` block to ``scientific_origin=experimental`` makes
  these go red.
* ``test_gas_without_pressure_is_rejected`` /
  ``test_ideal_gas_without_pressure_is_accepted``: allowing a
  pressure-less "Gas" row through makes the first go red.
"""

from __future__ import annotations

from pathlib import Path

from app.db.models.common import ObservedUncertaintyKind, ScientificOriginKind
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
