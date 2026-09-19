import pytest

from tckdb_client.builders.thermo import Thermo
from tckdb_client.builders.validation import TCKDBBuilderValidationError
from tckdb_schemas.workflows.computed_reaction_upload import BundleThermoIn
from tckdb_schemas.workflows.computed_species_upload import ThermoInBundle


@pytest.mark.parametrize("schema", [BundleThermoIn, ThermoInBundle])
def test_builder_omission_null_and_zero_kelvin(schema):
    omitted = Thermo.scalar(enthalpy_formation_0k_kj_mol=0).to_payload()
    assert "phase" not in omitted and "reference_pressure_bar" not in omitted
    assert schema.model_validate(omitted).phase == "gas"
    explicit = Thermo.scalar(enthalpy_formation_0k_kj_mol=0,
                            enthalpy_formation_0k_uncertainty_kj_mol=0.1,
                            phase=None, reference_pressure_bar=None).to_payload()
    restored = schema.model_validate(explicit)
    assert restored.phase is None and restored.reference_pressure_bar is None
    assert restored.enthalpy_formation_0k_kj_mol == 0
    assert restored.enthalpy_formation_0k_uncertainty_kj_mol == 0.1


@pytest.mark.parametrize("factory", [
    lambda: Thermo.scalar(h298_kj_mol=float("nan")),
    lambda: Thermo.scalar(h298_kj_mol=0, reference_pressure_bar=float("inf")),
    lambda: Thermo.points([{"temperature_k": 298}]),
    lambda: Thermo.nasa(coeffs_low=[float("inf")]*7, coeffs_high=[0]*7,
                        t_low=200, t_mid=1000, t_high=3000),
])
def test_builder_invalid_content(factory):
    with pytest.raises(TCKDBBuilderValidationError):
        factory()
