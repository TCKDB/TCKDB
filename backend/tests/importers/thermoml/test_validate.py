"""``validate_bytes`` rejects schema-invalid ThermoML documents.

Mutation check (verified manually; see the PR body's mutation table):
bypassing ``schema.validate(doc)`` (e.g. hardcoding ``valid = True``)
makes ``test_schema_invalid_fixture_is_rejected`` go red.
"""

from __future__ import annotations

from pathlib import Path

from app.importers.thermoml.validate import validate_bytes

FIXTURES = Path(__file__).parents[3] / "app" / "importers" / "thermoml" / "fixtures"


def test_valid_fixture_passes():
    xml_bytes = (FIXTURES / "cp_gas_single_component.xml").read_bytes()
    report = validate_bytes(xml_bytes)
    assert report.valid is True
    assert report.errors == ()


def test_schema_invalid_fixture_is_rejected():
    xml_bytes = (FIXTURES / "schema_invalid.xml").read_bytes()
    report = validate_bytes(xml_bytes)
    assert report.valid is False
    assert len(report.errors) >= 1
    joined = " ".join(report.errors)
    assert "ePropName" in joined or "Property" in joined
