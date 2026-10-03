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


def test_network_pdep_accepts_a_correctly_labelled_deuterated_ts(client) -> None:
    """The acceptance half: every saddle point of a deuterated well says so.

    Ethylperoxy carries one deuteron (atom 5 of the nine-atom structure is a
    hydrogen), and so does every transition state whose reactant it is.
    """

    from tests.workflows.test_network_pdep_upload import _parallel_path_payload

    payload = _parallel_path_payload()
    well = next(s for s in payload["species"] if s["key"] == "ethylperoxy")
    well["species_entry"]["smiles"] = "[2H]CCO[O]"
    well["conformers"][0]["geometry"]["isotopes"] = {5: 2}
    for ts in payload["transition_states"]:
        ts["geometry"]["isotopes"] = {5: 2}
    response = client.post("/api/v1/uploads/networks/pdep", json=payload)
    assert response.status_code == 201, response.text


def test_network_pdep_refuses_the_same_deuterated_well_with_an_unlabelled_ts(
    client,
) -> None:
    """Without this the acceptance above could pass with the check absent."""

    from tests.workflows.test_network_pdep_upload import _parallel_path_payload

    payload = _parallel_path_payload()
    well = next(s for s in payload["species"] if s["key"] == "ethylperoxy")
    well["species_entry"]["smiles"] = "[2H]CCO[O]"
    well["conformers"][0]["geometry"]["isotopes"] = {5: 2}
    assert _post(client, "/api/v1/uploads/networks/pdep", payload) == (422, CODE)


def _deuterated_reaction_payload() -> dict:
    """CH3 + [2H] -> [2H]C, every geometry labelled.

    Atom 5 of the saddle point and atom 2 of the methane are the deuteron.
    """

    from tests.workflows.test_computed_reaction_upload import _minimal_payload

    payload = _minimal_payload()
    species = {s["key"]: s for s in payload["species"]}
    species["h"]["species_entry"]["smiles"] = "[2H]"
    species["h"]["conformers"][0]["geometry"]["isotopes"] = {1: 2}
    species["ch4"]["species_entry"]["smiles"] = "[2H]C"
    species["ch4"]["conformers"][0]["geometry"]["isotopes"] = {2: 2}
    payload["transition_state"]["geometry"]["isotopes"] = {5: 2}
    return payload


def test_computed_reaction_accepts_a_correctly_labelled_deuterated_ts(client) -> None:
    """A TS ``geometry.isotopes`` must reach the stored geometry on this route."""

    response = client.post(
        "/api/v1/uploads/computed-reaction", json=_deuterated_reaction_payload()
    )
    assert response.status_code == 201, response.text


def test_computed_reaction_refuses_the_deuterated_reaction_with_an_unlabelled_ts(
    client,
) -> None:
    payload = _deuterated_reaction_payload()
    del payload["transition_state"]["geometry"]["isotopes"]
    assert _post(client, "/api/v1/uploads/computed-reaction", payload) == (422, CODE)


def test_an_unlabelled_computed_reaction_ts_keeps_its_geom_hash(db_conn) -> None:
    """Routing the TS through ``to_payload`` must not re-key an ordinary geometry.

    ``geom_hash`` is a public ref; the isotope suffix is appended to the hashed
    text only when a substitution exists, so an unlabelled TS hashes exactly
    as it did when this route built its payload from ``xyz_text`` alone.
    """

    import hashlib

    from sqlalchemy import select
    from sqlalchemy.orm import Session

    from app.chemistry.geometry import parse_xyz
    from app.db.models.geometry import Geometry
    from app.schemas.fragments.geometry import GeometryPayload
    from app.schemas.workflows.computed_reaction_upload import (
        ComputedReactionUploadRequest,
    )
    from app.workflows.computed_reaction import persist_computed_reaction_upload
    from tests.workflows.test_computed_reaction_upload import (
        _XYZ_TS_CH3H,
        _minimal_payload,
    )

    session = Session(bind=db_conn, expire_on_commit=False)
    try:
        persist_computed_reaction_upload(
            session, ComputedReactionUploadRequest(**_minimal_payload())
        )
        expected = hashlib.sha256(
            parse_xyz(GeometryPayload(xyz_text=_XYZ_TS_CH3H.strip())).hash_text.encode(
                "utf-8"
            )
        ).hexdigest()
        assert session.scalar(
            select(Geometry.id).where(Geometry.geom_hash == expected)
        ) is not None
    finally:
        session.close()
