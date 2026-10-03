"""Every route that runs the composition check runs the isotope check (#666).

The service-level cases live in
``tests/services/test_calculation_geometry_isotopes.py``. This file proves the
check is reached from each deposit route, with the ``(status, code)`` pair as
the assertion -- never a substring of ``detail``.

The shape is the issue's: a ``[H]`` (protium) record whose calculation carries
an H geometry declaring ``isotopes {1: 2}``.
"""

from __future__ import annotations

import pytest

from app.services.calculation_geometry_composition import (
    W_CALCULATION_GEOMETRY_ISOTOPE_MISMATCH as CODE,
)

_SOFTWARE = {"name": "Gaussian", "version": "16"}
_LOT = {"method": "B3LYP", "basis": "6-31G(d)"}
_H = {"smiles": "[H]", "charge": 0, "multiplicity": 2}
_XYZ_H = "1\nH\nH 0.0 0.0 0.0"


def _sp(*, isotopes: dict[int, int] | None, via: str = "output") -> dict:
    geometry: dict = {"xyz_text": _XYZ_H}
    if isotopes is not None:
        geometry["isotopes"] = isotopes
    calc: dict = {
        "type": "sp",
        "software_release": _SOFTWARE,
        "level_of_theory": _LOT,
        "sp_result": {"electronic_energy_hartree": -0.5},
    }
    if via == "output":
        calc["output_geometries"] = [{"geometry": geometry, "role": "final"}]
    else:
        calc["input_geometries"] = [geometry]
    return calc


def _post(client, url: str, payload: dict):
    response = client.post(url, json=payload)
    return response.status_code, response.json().get("code")


@pytest.mark.parametrize("via", ["output", "input"])
def test_thermo_refuses_a_deuterium_sp_on_a_protium_species(client, via) -> None:
    payload = {
        "enthalpy_reference_kind": "formation_298k",
        "species_entry": _H,
        "scientific_origin": "computed",
        "h298_kj_mol": 218.0,
        "calculations": [{"key": "sp0", "calculation": _sp(isotopes={1: 2}, via=via)}],
        "source_calculations": [{"calculation_key": "sp0", "role": "sp"}],
    }
    assert _post(client, "/api/v1/uploads/thermo", payload) == (422, CODE)


def test_thermo_accepts_the_unlabelled_sp(client) -> None:
    """Without this, the refusal above would pass against any refusal at all."""

    payload = {
        "enthalpy_reference_kind": "formation_298k",
        "species_entry": _H,
        "scientific_origin": "computed",
        "h298_kj_mol": 218.0,
        "calculations": [{"key": "sp0", "calculation": _sp(isotopes=None)}],
        "source_calculations": [{"calculation_key": "sp0", "role": "sp"}],
    }
    assert client.post("/api/v1/uploads/thermo", json=payload).status_code == 201


@pytest.mark.parametrize("via", ["output", "input"])
def test_statmech_refuses_a_deuterium_sp_on_a_protium_species(client, via) -> None:
    payload = {
        "species_entry": _H,
        "scientific_origin": "computed",
        "statmech_treatment": "rrho",
        "external_symmetry": 1,
        "calculations": [{"key": "sp0", "calculation": _sp(isotopes={1: 2}, via=via)}],
        "source_calculations": [{"calculation_key": "sp0", "role": "sp"}],
    }
    assert _post(client, "/api/v1/uploads/statmech", payload) == (422, CODE)


def test_statmech_accepts_the_unlabelled_sp(client) -> None:
    payload = {
        "species_entry": _H,
        "scientific_origin": "computed",
        "statmech_treatment": "rrho",
        "external_symmetry": 1,
        "calculations": [{"key": "sp0", "calculation": _sp(isotopes=None)}],
        "source_calculations": [{"calculation_key": "sp0", "role": "sp"}],
    }
    assert client.post("/api/v1/uploads/statmech", json=payload).status_code == 201


def test_conformer_refuses_a_deuterium_calculation_geometry(client) -> None:
    payload = {
        "species_entry": _H,
        "geometry": {"xyz_text": _XYZ_H},
        "calculation": {
            "type": "opt",
            "software_release": _SOFTWARE,
            "level_of_theory": _LOT,
            "input_geometries": [{"xyz_text": _XYZ_H, "isotopes": {1: 2}}],
        },
        "label": "conf-a",
    }
    assert _post(client, "/api/v1/uploads/conformers", payload) == (422, CODE)


def test_the_conformer_route_accepts_the_matching_deuterium_geometry(client) -> None:
    deuterium = {"smiles": "[2H]", "charge": 0, "multiplicity": 2}
    payload = {
        "species_entry": deuterium,
        "geometry": {"xyz_text": _XYZ_H, "isotopes": {1: 2}},
        "calculation": {
            "type": "opt",
            "software_release": _SOFTWARE,
            "level_of_theory": _LOT,
            "input_geometries": [{"xyz_text": _XYZ_H, "isotopes": {1: 2}}],
        },
        "label": "conf-d",
    }
    assert client.post("/api/v1/uploads/conformers", json=payload).status_code == 201


def test_computed_reaction_refuses_a_labelled_species_calculation_geometry(
    client,
) -> None:
    from tests.workflows.test_computed_reaction_upload import (
        _XYZ_CH3,
        _payload_with_ts_irc,
    )

    payload = _payload_with_ts_irc()
    # Species 0 is CH3, an ordinary isotopologue; atom 2 is a hydrogen.
    payload["species"][0]["calculations"][0]["input_geometries"] = [
        {"xyz_text": _XYZ_CH3, "isotopes": {2: 2}}
    ]
    assert _post(client, "/api/v1/uploads/computed-reaction", payload) == (422, CODE)


def test_computed_reaction_accepts_the_unlabelled_equivalent(client) -> None:
    from tests.workflows.test_computed_reaction_upload import (
        _XYZ_CH3,
        _payload_with_ts_irc,
    )

    payload = _payload_with_ts_irc()
    payload["species"][0]["calculations"][0]["input_geometries"] = [
        {"xyz_text": _XYZ_CH3}
    ]
    response = client.post("/api/v1/uploads/computed-reaction", json=payload)
    assert response.status_code == 201, response.text


def test_network_pdep_refuses_a_labelled_ts_calculation_geometry(client) -> None:
    from tests.workflows.test_network_pdep_upload import _parallel_path_payload

    payload = _parallel_path_payload()
    ts = next(t for t in payload["transition_states"] if t["key"] == "ts_isomer")
    # Atom 5 of the nine-atom saddle point is a hydrogen.
    ts["geometry"]["isotopes"] = {5: 2}
    assert _post(client, "/api/v1/uploads/networks/pdep", payload) == (422, CODE)


def test_network_pdep_accepts_the_unlabelled_equivalent(client) -> None:
    from tests.workflows.test_network_pdep_upload import _parallel_path_payload

    response = client.post(
        "/api/v1/uploads/networks/pdep", json=_parallel_path_payload()
    )
    assert response.status_code == 201, response.text
