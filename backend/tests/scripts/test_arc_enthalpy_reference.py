"""ARC numbers are not evidence of an enthalpy convention."""

from types import SimpleNamespace

import pytest

from scripts.arc_ingestion import builder
from scripts.arc_ingestion.species_yaml import NASAPolynomial, SpeciesThermo


def _inputs(monkeypatch):
    monkeypatch.setattr(builder, "_make_opt_calculation", lambda *args: {})
    thermo = SpeciesThermo(
        h298_kj_mol=0, s298_j_mol_k=10, tmin_k=200, tmax_k=3000,
        low_poly=NASAPolynomial(200, 1000, [1] * 7),
        high_poly=NASAPolynomial(1000, 3000, [1] * 7),
    )
    species = SimpleNamespace(
        smiles="[H]", charge=0, multiplicity=2, xyz_file=None,
        yaml_data=SimpleNamespace(smiles="[H]", thermo=thermo),
        freq_result=None, arkane_output_path=None,
    )
    run = SimpleNamespace(sp_is_same_as_opt=True, energy_correction_note=None)
    return species, run


@pytest.mark.parametrize("reference", [None, "absolute_quantum_enthalpy"])
def test_arc_requires_explicit_adapter_declaration(monkeypatch, reference):
    species, run = _inputs(monkeypatch)
    with pytest.raises(ValueError, match="ARC output does not declare"):
        builder._build_species_payload("H", species, run, False, reference)


def test_arc_emits_configured_reference_without_changing_numbers(monkeypatch):
    species, run = _inputs(monkeypatch)
    payload = builder._build_species_payload("H", species, run, False, "formation_from_elements_298k")
    assert payload["thermo"]["enthalpy_reference_kind"] == "formation_from_elements_298k"
    assert payload["thermo"]["h298_kj_mol"] == 0
    assert payload["thermo"]["nasa"]["a6"] == 1


def test_arc_fit_only_does_not_fabricate_h298(monkeypatch):
    from scripts.arc_ingestion.species_yaml import _parse_thermo

    def polynomial(low, high):
        return {"Tmin": {"value": low}, "Tmax": {"value": high},
                "coeffs": {"class": "np_array", "object": [1] * 7}}

    species, run = _inputs(monkeypatch)
    species.yaml_data.thermo = _parse_thermo({"thermo": {
        "Tmin": {"value": 200}, "Tmax": {"value": 3000},
        "polynomials": {"polynomial1": polynomial(200, 1000), "polynomial2": polynomial(1000, 3000)},
    }})
    payload = builder._build_species_payload("H", species, run, False, "formation_from_elements_298k")
    assert payload["thermo"]["h298_kj_mol"] is None
    assert payload["thermo"]["nasa"]["a6"] == 1
    assert payload["thermo"]["enthalpy_reference_kind"] == "formation_from_elements_298k"
