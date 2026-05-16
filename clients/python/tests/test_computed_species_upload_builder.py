"""End-to-end builder tests for ``ComputedSpeciesUpload``.

These tests pin the bundle payload shape against a frozen snapshot and
the determinism invariant. If the snapshot drifts, the developer must
look at the diff and either accept it (server schema changed,
regenerate) or fix it (builder regressed).
"""

from __future__ import annotations

import json

import pytest

from tckdb_client.builders import (
    Calculation,
    ComputedSpeciesUpload,
    Geometry,
    LevelOfTheory,
    Species,
    SoftwareRelease,
    TCKDBBuilderValidationError,
)


# ----- fixtures --------------------------------------------------------


@pytest.fixture
def water_geom():
    return Geometry.from_xyz(
        "3\nwater\n"
        "O 0.0 0.0 0.117\n"
        "H 0.0 0.757 -0.469\n"
        "H 0.0 -0.757 -0.469"
    )


@pytest.fixture
def b3lyp():
    return LevelOfTheory(method="B3LYP", basis="6-31G(d)")


@pytest.fixture
def gaussian():
    return SoftwareRelease(software="Gaussian", version="16", revision="C.01")


@pytest.fixture
def water_species():
    return Species(smiles="O", charge=0, multiplicity=1)


@pytest.fixture
def water_upload(b3lyp, gaussian, water_geom, water_species):
    opt = Calculation.opt(
        gaussian,
        b3lyp,
        output_geometry=water_geom,
        final_energy_hartree=-76.4,
        converged=True,
    )
    freq = Calculation.freq(
        gaussian,
        b3lyp,
        input_geometry=water_geom,
        n_imag=0,
        zpe_hartree=0.0214,
        depends_on=opt,
    )
    return ComputedSpeciesUpload(
        species=water_species,
        calculations=[opt, freq],
        primary_calculation=opt,
    )


# ----- determinism + snapshot -----------------------------------------


def test_to_payload_is_deterministic(water_upload):
    p1 = water_upload.to_payload()
    p2 = water_upload.to_payload()
    assert p1 == p2
    assert json.dumps(p1, sort_keys=True) == json.dumps(p2, sort_keys=True)


def test_to_payload_snapshot(water_upload):
    payload = water_upload.to_payload()
    expected = {
        "species_entry": {
            "smiles": "O",
            "charge": 0,
            "multiplicity": 1,
        },
        "conformers": [
            {
                "key": "conformer_1",
                "geometry": {
                    "xyz_text": (
                        "3\nwater\n"
                        "O 0.0 0.0 0.117\n"
                        "H 0.0 0.757 -0.469\n"
                        "H 0.0 -0.757 -0.469"
                    )
                },
                "primary_calculation": {
                    "key": "calc_1",
                    "type": "opt",
                    "level_of_theory": {"method": "B3LYP", "basis": "6-31G(d)"},
                    "software_release": {
                        "name": "Gaussian",
                        "version": "16",
                        "revision": "C.01",
                    },
                    "output_geometries": [
                        {
                            "geometry": {
                                "xyz_text": (
                                    "3\nwater\n"
                                    "O 0.0 0.0 0.117\n"
                                    "H 0.0 0.757 -0.469\n"
                                    "H 0.0 -0.757 -0.469"
                                )
                            },
                            "role": "final",
                        }
                    ],
                    "opt_result": {
                        "converged": True,
                        "final_energy_hartree": -76.4,
                    },
                },
                "additional_calculations": [
                    {
                        "key": "calc_2",
                        "type": "freq",
                        "level_of_theory": {
                            "method": "B3LYP",
                            "basis": "6-31G(d)",
                        },
                        "software_release": {
                            "name": "Gaussian",
                            "version": "16",
                            "revision": "C.01",
                        },
                        "input_geometries": [
                            {
                                "xyz_text": (
                                    "3\nwater\n"
                                    "O 0.0 0.0 0.117\n"
                                    "H 0.0 0.757 -0.469\n"
                                    "H 0.0 -0.757 -0.469"
                                )
                            }
                        ],
                        "depends_on": [
                            {"parent_calculation_key": "calc_1", "role": "freq_on"}
                        ],
                        "freq_result": {"n_imag": 0, "zpe_hartree": 0.0214},
                    }
                ],
            }
        ],
    }
    assert payload == expected


# ----- local validation -----------------------------------------------


def test_requires_at_least_one_calculation(water_species):
    with pytest.raises(TCKDBBuilderValidationError):
        ComputedSpeciesUpload(species=water_species, calculations=[])


def test_primary_calc_must_be_in_calculations(
    b3lyp, gaussian, water_geom, water_species
):
    opt_in = Calculation.opt(
        gaussian, b3lyp, output_geometry=water_geom, converged=True,
    )
    opt_out = Calculation.opt(
        gaussian, b3lyp, output_geometry=water_geom, converged=True,
    )
    with pytest.raises(TCKDBBuilderValidationError):
        ComputedSpeciesUpload(
            species=water_species,
            calculations=[opt_in],
            primary_calculation=opt_out,
        )


def test_primary_calc_must_be_opt(
    b3lyp, gaussian, water_geom, water_species
):
    sp = Calculation.sp(
        gaussian, b3lyp, input_geometry=water_geom,
        electronic_energy_hartree=-76.4,
    )
    with pytest.raises(TCKDBBuilderValidationError):
        ComputedSpeciesUpload(
            species=water_species,
            calculations=[sp],
            primary_calculation=sp,
        )


def test_dependency_outside_upload_raises(
    b3lyp, gaussian, water_geom, water_species
):
    opt_inside = Calculation.opt(
        gaussian, b3lyp, output_geometry=water_geom, converged=True,
    )
    opt_outside = Calculation.opt(
        gaussian, b3lyp, output_geometry=water_geom, converged=True,
    )
    freq = Calculation.freq(
        gaussian, b3lyp, input_geometry=water_geom,
        n_imag=0, depends_on=opt_outside,
    )
    with pytest.raises(TCKDBBuilderValidationError):
        ComputedSpeciesUpload(
            species=water_species,
            calculations=[opt_inside, freq],
            primary_calculation=opt_inside,
        )


def test_primary_calculation_inferred_when_omitted(
    b3lyp, gaussian, water_geom, water_species
):
    opt = Calculation.opt(
        gaussian, b3lyp, output_geometry=water_geom, converged=True,
    )
    freq = Calculation.freq(
        gaussian, b3lyp, input_geometry=water_geom, n_imag=0, depends_on=opt,
    )
    upload = ComputedSpeciesUpload(
        species=water_species, calculations=[opt, freq]
    )
    assert upload.primary_calculation is opt


def test_primary_calculation_requires_an_opt(
    b3lyp, gaussian, water_geom, water_species
):
    sp = Calculation.sp(
        gaussian, b3lyp, input_geometry=water_geom,
        electronic_energy_hartree=-76.4,
    )
    with pytest.raises(TCKDBBuilderValidationError):
        ComputedSpeciesUpload(species=water_species, calculations=[sp])


def test_conformer_geometry_falls_back_to_input(
    b3lyp, gaussian, water_geom, water_species
):
    """An opt with only an input geometry still seeds the conformer."""
    opt = Calculation.opt(
        gaussian, b3lyp, input_geometry=water_geom, converged=True,
    )
    upload = ComputedSpeciesUpload(species=water_species, calculations=[opt])
    payload = upload.to_payload()
    assert payload["conformers"][0]["geometry"]["xyz_text"].startswith("3\n")


def test_conformer_geometry_required(
    b3lyp, gaussian, water_species
):
    opt_no_geom = Calculation.opt(
        gaussian, b3lyp, converged=True,
    )
    upload = ComputedSpeciesUpload(
        species=water_species, calculations=[opt_no_geom]
    )
    with pytest.raises(TCKDBBuilderValidationError):
        upload.to_payload()


# ----- local keys ------------------------------------------------------


def test_local_keys_use_labels_when_present(
    b3lyp, gaussian, water_geom, water_species
):
    opt = Calculation.opt(
        gaussian, b3lyp, output_geometry=water_geom,
        converged=True, label="initial opt",
    )
    freq = Calculation.freq(
        gaussian, b3lyp, input_geometry=water_geom,
        n_imag=0, depends_on=opt, label="harm freq",
    )
    payload = ComputedSpeciesUpload(
        species=water_species, calculations=[opt, freq]
    ).to_payload()
    assert payload["conformers"][0]["primary_calculation"]["key"] == "initial_opt"
    assert (
        payload["conformers"][0]["additional_calculations"][0]["key"]
        == "harm_freq"
    )
    assert (
        payload["conformers"][0]["additional_calculations"][0]["depends_on"][0][
            "parent_calculation_key"
        ]
        == "initial_opt"
    )


def test_label_collision_disambiguated_with_suffix(
    b3lyp, gaussian, water_geom, water_species
):
    opt1 = Calculation.opt(
        gaussian, b3lyp, output_geometry=water_geom,
        converged=True, label="foo",
    )
    opt2 = Calculation.opt(
        gaussian, b3lyp, output_geometry=water_geom,
        converged=True, label="foo",
    )
    payload = ComputedSpeciesUpload(
        species=water_species, calculations=[opt1, opt2],
        primary_calculation=opt1,
    ).to_payload()
    primary_key = payload["conformers"][0]["primary_calculation"]["key"]
    additional_key = payload["conformers"][0]["additional_calculations"][0]["key"]
    assert primary_key == "foo"
    assert additional_key == "foo_2"
