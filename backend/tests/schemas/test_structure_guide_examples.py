"""The JSON examples in the structure-evidence guide validate against the models they describe."""

from __future__ import annotations

import json
import re
from pathlib import Path

from tckdb_schemas.fragments.calculation import CalculationWithResultsPayload
from tckdb_schemas.structure_declarations import StructureDeterminationDeclaration

GUIDE = Path(__file__).resolve().parents[3] / "docs/guides/declaring_structure_evidence.md"


def _blocks() -> list[dict]:
    return [json.loads(b) for b in re.findall(r"```json\n(.*?)\n```", GUIDE.read_text(), flags=re.S)]


def test_the_guide_has_exactly_the_examples_this_test_checks():
    # A new example must be added here, or it is unchecked.
    assert len(_blocks()) == 2


def test_the_calculation_example_is_a_valid_calculation_with_its_declaration():
    calculation = CalculationWithResultsPayload.model_validate(_blocks()[0])
    declared = calculation.actual_protocol_declaration
    assert declared is not None
    assert declared.numerical_approximations is not None and declared.included_corrections == []
    assert declared.effective_core_potential is not None
    assert declared.effective_core_potential.state.value == "not_applicable"


def test_the_determination_example_is_a_valid_determination():
    block = _blocks()[1]
    determinations = [StructureDeterminationDeclaration.model_validate(d) for d in block["structure_determinations"]]
    assert len(determinations) == 1
    assert [s.role.value for s in determinations[0].sources] == ["geometry_optimization", "energy", "curvature"]
