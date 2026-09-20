"""Every fixture's expected outcome, end to end (validate -> parse -> map).

A regression here means either the parser/mapper drifted from what a
fixture is supposed to demonstrate, or a fixture's own expectations
changed without the test being updated -- both are real failures we
want to see. ``test_every_fixture_file_is_covered`` guards against the
"empty-fixture-set trivially passes" failure mode: it fails if a new
``.xml`` fixture is added without a matching case here, and it fails
if the case table itself were ever emptied out.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.importers.thermoml.mapping import map_document
from app.importers.thermoml.parser import parse_thermoml_document
from app.importers.thermoml.validate import validate_bytes

FIXTURES = Path(__file__).parents[3] / "app" / "importers" / "thermoml" / "fixtures"
DOI = "10.1016/j.fluid.2016.07.034"

# name -> expected outcome. Every field is checked; there is no
# "close enough" shortcut here, per the mutation-checked test policy.
CASES: dict[str, dict] = {
    "cp_gas_single_component.xml": {
        "schema_valid": True,
        "cp_tables": 1,
        "unsupported_blocks": (),
        "payloads": 3,
        "rejected_reasons": (),
    },
    "cp_ideal_gas_no_pressure.xml": {
        "schema_valid": True,
        "cp_tables": 1,
        "unsupported_blocks": (),
        "payloads": 1,
        "rejected_reasons": (),
    },
    "cp_liquid_unsupported.xml": {
        "schema_valid": True,
        "cp_tables": 0,
        "unsupported_blocks": ("unsupported_phase",),
        "payloads": 0,
        "rejected_reasons": (),
    },
    "cp_mixture_unsupported.xml": {
        "schema_valid": True,
        "cp_tables": 0,
        "unsupported_blocks": ("multi_component",),
        "payloads": 0,
        "rejected_reasons": (),
    },
    "cp_prediction_only.xml": {
        "schema_valid": True,
        "cp_tables": 1,
        "unsupported_blocks": (),
        "payloads": 1,
        "rejected_reasons": (),
    },
    "cp_gas_missing_pressure.xml": {
        "schema_valid": True,
        "cp_tables": 1,
        "unsupported_blocks": (),
        "payloads": 0,
        "rejected_reasons": ("gas_missing_pressure",),
    },
    "schema_invalid.xml": {
        "schema_valid": False,
    },
    "cp_ideal_gas_statistical_thermodynamics.xml": {
        "schema_valid": True,
        "cp_tables": 1,
        "unsupported_blocks": (),
        "payloads": 4,
        "rejected_reasons": (),
    },
    "cp_ambiguous_phase.xml": {
        "schema_valid": True,
        "cp_tables": 0,
        "unsupported_blocks": ("ambiguous_phase",),
        "payloads": 0,
        "rejected_reasons": (),
    },
    "cp_unsupported_standard_state.xml": {
        "schema_valid": True,
        "cp_tables": 1,
        "unsupported_blocks": (),
        "payloads": 0,
        "rejected_reasons": ("unsupported_standard_state",),
    },
    "cp_empty_uncertainty_value.xml": {
        "schema_valid": True,
        "cp_tables": 1,
        "unsupported_blocks": (),
        "payloads": 1,
        "rejected_reasons": ("empty_uncertainty_value",),
    },
    "cp_unrecognized_smethodname.xml": {
        "schema_valid": True,
        "cp_tables": 1,
        "unsupported_blocks": (),
        "payloads": 0,
        "rejected_reasons": ("unrecognized_smethodname",),
    },
    "cp_uncertainty_precedence_order.xml": {
        "schema_valid": True,
        "cp_tables": 1,
        "unsupported_blocks": (),
        "payloads": 1,
        "rejected_reasons": (),
    },
    "cp_gas_variable_pressure.xml": {
        "schema_valid": True,
        "cp_tables": 1,
        "unsupported_blocks": (),
        "payloads": 3,
        "rejected_reasons": (),
    },
}


def test_every_fixture_file_is_covered():
    assert CASES, "the fixture case table must not be empty"
    on_disk = {p.name for p in FIXTURES.glob("*.xml")}
    assert on_disk, "no .xml fixtures found on disk"
    assert on_disk == set(CASES), (
        f"fixture files and CASES table have drifted: "
        f"on disk only={on_disk - set(CASES)}, "
        f"CASES only={set(CASES) - on_disk}"
    )


@pytest.mark.parametrize("name", sorted(CASES))
def test_fixture_outcome(name):
    expected = CASES[name]
    xml_bytes = (FIXTURES / name).read_bytes()

    schema_report = validate_bytes(xml_bytes)
    assert schema_report.valid is expected["schema_valid"]
    if not expected["schema_valid"]:
        return

    document = parse_thermoml_document(xml_bytes)
    assert len(document.cp_tables) == expected["cp_tables"]
    assert tuple(sorted(u.reason for u in document.unsupported)) == tuple(
        sorted(expected["unsupported_blocks"])
    )

    result = map_document(document, doi=DOI)
    assert len(result.payloads) == expected["payloads"]
    assert tuple(sorted(r["reason"] for r in result.report.rejected)) == tuple(
        sorted(expected["rejected_reasons"])
    )
