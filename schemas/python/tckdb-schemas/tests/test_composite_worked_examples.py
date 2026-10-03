"""The worked payloads printed in the producer contract are valid and their totals recompute (ADR 0021, P5).

A documentation example that no model has parsed and no arithmetic has checked is
how a contract teaches a producer a payload the server refuses.
"""

from __future__ import annotations

import pytest

from tckdb_schemas.composite_total import InputEnergies, check_composite_total
from tckdb_schemas.composite_worked_examples import worked_payload_b, worked_payload_c
from tckdb_schemas.enums import EnergyComponentKind
from tckdb_schemas.workflows.computed_species_upload import ComputedSpeciesUploadRequest


def _recompute(payload: dict):
    request = ComputedSpeciesUploadRequest.model_validate(payload)
    calcs = request.conformers[0].additional_calculations
    by_key = {c.key: c for c in calcs}
    composite = next(c for c in calcs if c.type.value == "composite")
    result = composite.composite_result
    definition = composite.level_of_theory.composite_scheme
    from tckdb_schemas.composite_scheme_rules import match_inputs_to_definition

    matched = {
        (m.term_position, m.slot, m.cardinal_number if m.slot.value == "cardinal" else None): by_key[
            m.input.calculation_key
        ]
        for m in match_inputs_to_definition(definition, result.inputs)
    }

    def energies_for(position, slot, cardinal):
        calc = matched[(position, slot, cardinal)]
        return InputEnergies(
            total=calc.sp_result.electronic_energy_hartree,
            components={EnergyComponentKind(c.component.value): c.value_hartree for c in calc.sp_energy_components},
        )

    return check_composite_total(definition, energies_for, result.electronic_energy_hartree), composite


@pytest.mark.parametrize(("build", "quantities"), [(worked_payload_b, 3), (worked_payload_c, 8)])
def test_each_worked_payload_parses_and_its_deposited_total_recomputes(build, quantities):
    check, composite = _recompute(build())
    assert composite.software_release is None  # no program ran an assembled composite
    assert check.status == "ok", check
    assert check.quantities == quantities
    assert abs(check.gap) < 1e-9  # the payload prints nine decimals


def test_a_total_that_is_off_is_what_the_check_would_catch_in_the_documented_payload():
    payload = worked_payload_b()
    payload["conformers"][0]["additional_calculations"][-1]["composite_result"]["electronic_energy_hartree"] += 1e-3
    check, _ = _recompute(payload)
    assert check.status == "mismatch"
