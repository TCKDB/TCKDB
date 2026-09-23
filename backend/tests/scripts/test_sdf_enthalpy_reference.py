"""SDF adapters require a depositor declaration, independently of software labels."""

import pytest

from scripts.parse_sdf_to_bundle import sdf_to_bundle


@pytest.mark.parametrize("reference", [None, "absolute_quantum_enthalpy"])
def test_sdf_requires_explicit_adapter_declaration(reference):
    with pytest.raises(ValueError, match="SDF input does not declare"):
        sdf_to_bundle("rxn_146", enthalpy_reference_kind=reference)


def test_sdf_emits_configured_reference():
    bundle = sdf_to_bundle("rxn_146", enthalpy_reference_kind="formation_298k")
    thermos = [species["thermo"] for species in bundle["species"] if "thermo" in species]
    assert thermos
    assert all(thermo["enthalpy_reference_kind"] == "formation_298k" for thermo in thermos)
