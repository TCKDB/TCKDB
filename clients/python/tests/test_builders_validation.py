"""Local-validation tests for the Phase-1 builder primitives.

These tests stay inside the builder layer: they never touch a real
backend or an HTTP transport, and they assert the contract the
builders advertise in ``docs/builder_api_mvp.md`` §9.
"""

from __future__ import annotations

import pytest

from tckdb_client.builders import (
    Calculation,
    Geometry,
    LevelOfTheory,
    Species,
    SoftwareRelease,
    TCKDBBuilderValidationError,
)


def _gaussian() -> SoftwareRelease:
    return SoftwareRelease(software="Gaussian", version="16")


def _b3lyp() -> LevelOfTheory:
    return LevelOfTheory(method="B3LYP", basis="6-31G(d)")


def _hydrogen() -> Geometry:
    return Geometry.from_xyz("1\nh\nH 0 0 0")


# --- Species -----------------------------------------------------------

class TestSpecies:
    def test_minimal_smiles_ok(self):
        s = Species(smiles="O", charge=0, multiplicity=1)
        assert s.smiles == "O"
        assert s.charge == 0
        assert s.multiplicity == 1

    def test_requires_some_identifier(self):
        with pytest.raises(TCKDBBuilderValidationError):
            Species(charge=0, multiplicity=1)

    def test_smiles_must_be_non_empty(self):
        with pytest.raises(TCKDBBuilderValidationError):
            Species(smiles="   ", charge=0, multiplicity=1)

    def test_multiplicity_must_be_positive_int(self):
        with pytest.raises(TCKDBBuilderValidationError):
            Species(smiles="O", charge=0, multiplicity=0)

    def test_multiplicity_rejects_bool(self):
        with pytest.raises(TCKDBBuilderValidationError):
            Species(smiles="O", charge=0, multiplicity=True)  # type: ignore[arg-type]

    def test_label_must_be_non_empty_when_given(self):
        with pytest.raises(TCKDBBuilderValidationError):
            Species(smiles="O", charge=0, multiplicity=1, label="")


# --- Geometry ----------------------------------------------------------

class TestGeometry:
    def test_from_xyz_standard_form_preserves_text(self):
        g = Geometry.from_xyz("1\nh atom\nH 0 0 0")
        assert g.natoms == 1
        assert "H 0 0 0" in g.xyz_text

    def test_from_xyz_normalises_symbol_capitalization(self):
        g = Geometry.from_xyz("1\nx\nh 0 0 0")
        assert "H 0 0 0" in g.xyz_text

    def test_from_xyz_two_letter_symbol(self):
        g = Geometry.from_xyz("1\nx\nNA 0 0 0")
        assert "Na 0 0 0" in g.xyz_text

    def test_from_xyz_bare_body_synthesises_header(self):
        g = Geometry.from_xyz("H 0 0 0\nH 0 0 0.5")
        assert g.natoms == 2
        assert g.xyz_text.startswith("2\n")

    def test_from_xyz_rejects_empty(self):
        with pytest.raises(TCKDBBuilderValidationError):
            Geometry.from_xyz("   ")

    def test_from_xyz_rejects_malformed_line(self):
        with pytest.raises(TCKDBBuilderValidationError):
            Geometry.from_xyz("1\nx\nthis is not xyz")

    def test_from_xyz_natoms_mismatch_raises(self):
        with pytest.raises(TCKDBBuilderValidationError):
            Geometry.from_xyz("2\nx\nH 0 0 0")


# --- LevelOfTheory / SoftwareRelease -----------------------------------

class TestProvenance:
    def test_lot_requires_method(self):
        with pytest.raises(TCKDBBuilderValidationError):
            LevelOfTheory(method="")

    def test_lot_optional_fields_must_be_non_empty(self):
        with pytest.raises(TCKDBBuilderValidationError):
            LevelOfTheory(method="B3LYP", basis="")

    def test_software_release_requires_name(self):
        with pytest.raises(TCKDBBuilderValidationError):
            SoftwareRelease(software="")

    def test_software_release_emits_name_field(self):
        # The bundle schema's wire field is ``name``, not ``software``.
        sr = SoftwareRelease(software="Gaussian", version="16")
        payload = sr.to_payload()
        assert payload == {"name": "Gaussian", "version": "16"}


# --- Calculation factories --------------------------------------------

class TestCalculationFactories:
    def test_opt_factory(self):
        g = _hydrogen()
        c = Calculation.opt(
            _gaussian(),
            _b3lyp(),
            output_geometry=g,
            final_energy_hartree=-0.5,
            converged=True,
            n_steps=10,
        )
        assert c.type == "opt"
        assert c.output_geometry is g
        kind, block = c.result_block()
        assert kind == "opt_result"
        assert block == {
            "converged": True,
            "n_steps": 10,
            "final_energy_hartree": -0.5,
        }

    def test_freq_factory_emits_freq_result(self):
        g = _hydrogen()
        c = Calculation.freq(
            _gaussian(),
            _b3lyp(),
            input_geometry=g,
            n_imag=0,
            zpe_hartree=0.01,
        )
        kind, block = c.result_block()
        assert kind == "freq_result"
        assert block == {"n_imag": 0, "zpe_hartree": 0.01}

    def test_freq_factory_emits_modes_from_frequencies(self):
        g = _hydrogen()
        c = Calculation.freq(
            _gaussian(),
            _b3lyp(),
            input_geometry=g,
            frequencies_cm1=[-1200.0, 800.0, 1600.0],
            n_imag=1,
        )
        kind, block = c.result_block()
        assert kind == "freq_result"
        assert block["n_imag"] == 1
        assert [m["frequency_cm1"] for m in block["modes"]] == [-1200.0, 800.0, 1600.0]
        assert [m["is_imaginary"] for m in block["modes"]] == [True, False, False]

    def test_freq_n_imag_disagrees_with_signs_raises(self):
        g = _hydrogen()
        c = Calculation.freq(
            _gaussian(),
            _b3lyp(),
            input_geometry=g,
            frequencies_cm1=[-1200.0, 800.0],
            n_imag=0,
        )
        with pytest.raises(TCKDBBuilderValidationError):
            c.result_block()

    def test_sp_factory_requires_energy_for_result_block(self):
        g = _hydrogen()
        c_no_energy = Calculation.sp(_gaussian(), _b3lyp(), input_geometry=g)
        assert c_no_energy.result_block() is None
        c = Calculation.sp(
            _gaussian(), _b3lyp(), input_geometry=g,
            electronic_energy_hartree=-76.4,
        )
        kind, block = c.result_block()
        assert kind == "sp_result"
        assert block == {"electronic_energy_hartree": -76.4}

    def test_type_rejects_unknown(self):
        with pytest.raises(TCKDBBuilderValidationError):
            Calculation(
                type="irc",  # phase 1 supports only opt/freq/sp
                software_release=_gaussian(),
                level_of_theory=_b3lyp(),
            )

    def test_depends_on_normalised_to_list(self):
        g = _hydrogen()
        opt = Calculation.opt(_gaussian(), _b3lyp(), output_geometry=g)
        freq = Calculation.freq(_gaussian(), _b3lyp(), input_geometry=g, depends_on=opt)
        assert freq.depends_on == [opt]

    def test_depends_on_rejects_non_calculation(self):
        g = _hydrogen()
        with pytest.raises(TCKDBBuilderValidationError):
            Calculation.freq(
                _gaussian(), _b3lyp(), input_geometry=g,
                depends_on=["not a calc"],  # type: ignore[list-item]
            )

    def test_dependency_role_inference(self):
        g = _hydrogen()
        opt = Calculation.opt(_gaussian(), _b3lyp(), output_geometry=g)
        freq = Calculation.freq(_gaussian(), _b3lyp(), input_geometry=g)
        sp = Calculation.sp(_gaussian(), _b3lyp(), input_geometry=g)
        opt2 = Calculation.opt(_gaussian(), _b3lyp(), output_geometry=g)
        assert freq.infer_dependency_role(opt) == "freq_on"
        assert sp.infer_dependency_role(opt) == "single_point_on"
        assert opt2.infer_dependency_role(opt) == "optimized_from"

    def test_dependency_role_ambiguous_raises(self):
        g = _hydrogen()
        opt = Calculation.opt(_gaussian(), _b3lyp(), output_geometry=g)
        freq = Calculation.freq(_gaussian(), _b3lyp(), input_geometry=g)
        sp = Calculation.sp(_gaussian(), _b3lyp(), input_geometry=g)
        # sp depending on freq is not a Phase-1 supported shape
        with pytest.raises(TCKDBBuilderValidationError):
            sp.infer_dependency_role(freq)
