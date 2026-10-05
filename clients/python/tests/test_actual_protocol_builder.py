"""The builder carries a calculation's declared actual protocol to both bundle wires, and only when stated."""

from __future__ import annotations

import pytest

from tckdb_client.builders.base import TCKDBBuilderValidationError
from tckdb_client.builders.calculation import Calculation, LevelOfTheory, SoftwareRelease
from tckdb_client.builders.uploads import ComputedReactionUpload, ComputedSpeciesUpload
from tckdb_schemas.fragments.calculation import CalculationWithResultsPayload
from tckdb_schemas.workflows.computed_species_upload import CalculationInBundle


class _KeyMinter:
    def lookup(self, _calc):
        return "calc-1"


PROTOCOL = {
    "version": 1,
    "source": {"origin": "producer_declared", "producer": "ARC"},
    "electronic_state": {"state": "known", "root": 0},
    "effective_core_potential": {"state": "not_applicable"},
}


def _sp(**extra) -> Calculation:
    return Calculation.sp(
        SoftwareRelease("Orca", "6.0.1"),
        LevelOfTheory("DLPNO-CCSD(T)", "def2-tzvp"),
        electronic_energy_hartree=-76.4,
        **extra,
    )


def test_a_declared_protocol_is_validated_and_kept():
    calc = _sp(actual_protocol_declaration=PROTOCOL)
    assert calc.actual_protocol_declaration is not None
    assert calc.actual_protocol_declaration.electronic_state.root == 0


def test_both_wires_emit_the_same_stored_form_and_the_server_model_accepts_it():
    calc = _sp(actual_protocol_declaration=PROTOCOL)
    species = ComputedSpeciesUpload._calc_payload(object(), calc, _KeyMinter())
    reaction = ComputedReactionUpload._calc_payload_flat(
        object(), calc, key="calc-1", calc_keys=_KeyMinter(), geometry_key=None
    )
    assert species["actual_protocol_declaration"] == reaction["actual_protocol_declaration"]
    # Unstated facts are left out, not sent as null.
    assert "spin_treatment" not in species["actual_protocol_declaration"]
    assert CalculationInBundle.model_validate(species).actual_protocol_declaration is not None


def test_a_calculation_that_declares_nothing_sends_nothing():
    species = ComputedSpeciesUpload._calc_payload(object(), _sp(), _KeyMinter())
    assert "actual_protocol_declaration" not in species
    assert CalculationWithResultsPayload.model_fields["actual_protocol_declaration"].default is None


@pytest.mark.parametrize(
    "bad",
    [{"version": 2, "source": {"origin": "producer_declared"}, "dispersion": {"state": "unknown"}}, {"version": 1}],
    ids=["unsupported-version", "no-statement"],
)
def test_an_invalid_declaration_is_refused_by_the_builder(bad):
    with pytest.raises(TCKDBBuilderValidationError):
        _sp(actual_protocol_declaration=bad)
