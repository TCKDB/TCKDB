"""A network solve's energy sources belong to the subject they state an energy for (#668).

#642 / #665 made ``state_energies[].source_calculation_key``,
``channel_barriers[].source_calculation_key`` and the ``well_energy`` /
``barrier_energy`` roles check the *type* of the cited calculation. None of them checked
whose calculation it was, so a saddle point's barrier could be cited to a species single
point and a well's energy to another well's. The rule and its reasoning are in
``app.services.network_energy_sources``.

The payload is the parallel-path network of ``test_network_pdep_upload``:

* states ``[0]`` entrance (ethyl + O2, bimolecular), ``[1]`` well_RO2, ``[2]`` exit
  (ethene + HO2, bimolecular), ``[3]`` well_iso;
* barriers ``[0]`` ts_elim, ``[1]`` ts_elim_anti, ``[2]`` ts_isomer.

What belongs to what is written out here by hand and is not derived from the service.
Every refusal is asserted by ``code`` and ``context``, never by a substring of the
detail, and the accepted halves check the stored source row, so a payload that silently
dropped the citation cannot pass as "accepted".
"""

from __future__ import annotations

from copy import deepcopy

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.error_contract import CodedValueError
from app.db.models.app_user import AppUser
from app.db.models.calculation import Calculation
from app.db.models.network_pdep import (
    NetworkSolveChannelBarrier,
    NetworkSolveSourceCalculation,
    NetworkSolveStateEnergy,
)
from app.schemas.workflows.network_pdep_upload import NetworkPDepUploadRequest
from app.workflows.network_pdep import persist_network_pdep_upload
from tests.workflows.test_network_pdep_upload import _parallel_path_payload

_PDEP_URL = "/api/v1/uploads/networks/pdep"
_CODE = "network_energy_source_subject_mismatch"


def _payload() -> dict:
    payload = deepcopy(_parallel_path_payload())
    # The shape this file indexes into; if the shared payload drifts, fail loudly here.
    assert [e["state_key"] for e in payload["solve"]["state_energies"]] == [
        "entrance",
        "well_RO2",
        "exit",
        "well_iso",
    ]
    assert [b["transition_state_key"] for b in payload["solve"]["channel_barriers"]] == [
        "ts_elim",
        "ts_elim_anti",
        "ts_isomer",
    ]
    return payload


def _refused(resp, *, field: str, expected_owner_kind: str, actual_owner_kind: str) -> dict:
    assert resp.status_code == 422, resp.text
    body = resp.json()
    assert body.get("code") == _CODE, body
    context = body["context"]
    assert context["field"] == field, body
    assert context["expected_owner_kind"] == expected_owner_kind, body
    assert context["actual_owner_kind"] == actual_owner_kind, body
    assert "id" not in context, body
    return body


def _state_field(index: int) -> str:
    return f"solve.state_energies[{index}].source_calculation_key"


def _barrier_field(index: int) -> str:
    return f"solve.channel_barriers[{index}].source_calculation_key"


def _link_field(index: int) -> str:
    return f"solve.source_calculations[{index}].calculation_key"


def _owner_of(db_session, calculation_id: int) -> Calculation:
    return db_session.get(Calculation, calculation_id)


# ---------------------------------------------------------------------------
# state_energies
# ---------------------------------------------------------------------------


def test_the_unchanged_payload_is_accepted(client) -> None:
    """The negative half of every refusal below: each state cites its own species."""
    assert client.post(_PDEP_URL, json=_payload()).status_code == 201


@pytest.mark.parametrize(
    ("index", "key"),
    [
        (0, "O2_sp"),  # second participant of the entrance state
        (2, "HO2_sp"),  # second participant of the exit state
    ],
)
def test_a_bimolecular_state_accepts_any_one_of_its_species(client, db_session, index: int, key: str) -> None:
    payload = _payload()
    payload["solve"]["state_energies"][index]["source_calculation_key"] = key
    resp = client.post(_PDEP_URL, json=payload)
    assert resp.status_code == 201, resp.text
    energy = payload["solve"]["state_energies"][index]
    rows = db_session.scalars(select(NetworkSolveStateEnergy)).all()
    stored = next(r for r in rows if r.energy_kj_mol == energy["energy_kj_mol"])
    assert _owner_of(db_session, stored.source_calculation_id).species_entry_id is not None


def test_a_well_cannot_cite_another_wells_single_point(client) -> None:
    payload = _payload()
    payload["solve"]["state_energies"][1]["source_calculation_key"] = "ethyl_sp"
    body = _refused(
        client.post(_PDEP_URL, json=payload),
        field=_state_field(1),
        expected_owner_kind="species_entry",
        actual_owner_kind="species_entry",
    )
    assert body["context"]["stated_value"] == "well_RO2", body


def test_a_later_well_cannot_cite_another_wells_single_point(client) -> None:
    """Index 3, not 0: a check that looked only at ``[0]`` would pass this."""
    payload = _payload()
    payload["solve"]["state_energies"][3]["source_calculation_key"] = "etoo_sp"
    body = _refused(
        client.post(_PDEP_URL, json=payload),
        field=_state_field(3),
        expected_owner_kind="species_entry",
        actual_owner_kind="species_entry",
    )
    assert body["context"]["stated_value"] == "well_iso", body


def test_a_bimolecular_state_cannot_cite_a_species_outside_it(client) -> None:
    payload = _payload()
    payload["solve"]["state_energies"][0]["source_calculation_key"] = "etoo_sp"
    _refused(
        client.post(_PDEP_URL, json=payload),
        field=_state_field(0),
        expected_owner_kind="species_entry",
        actual_owner_kind="species_entry",
    )


def test_a_later_bimolecular_state_cannot_cite_a_species_outside_it(client) -> None:
    payload = _payload()
    payload["solve"]["state_energies"][2]["source_calculation_key"] = "ethyl_sp"
    _refused(
        client.post(_PDEP_URL, json=payload),
        field=_state_field(2),
        expected_owner_kind="species_entry",
        actual_owner_kind="species_entry",
    )


def test_a_state_cannot_cite_a_saddle_points_calculation(client) -> None:
    payload = _payload()
    payload["solve"]["state_energies"][1]["source_calculation_key"] = "ts_elim_sp"
    _refused(
        client.post(_PDEP_URL, json=payload),
        field=_state_field(1),
        expected_owner_kind="species_entry",
        actual_owner_kind="transition_state_entry",
    )


# ---------------------------------------------------------------------------
# channel_barriers
# ---------------------------------------------------------------------------


def test_each_barrier_cites_its_own_saddle_point(client, db_session) -> None:
    resp = client.post(_PDEP_URL, json=_payload())
    assert resp.status_code == 201, resp.text
    rows = db_session.scalars(select(NetworkSolveChannelBarrier)).all()
    assert len(rows) == 3
    for row in rows:
        calc = _owner_of(db_session, row.source_calculation_id)
        assert calc.transition_state_entry_id == row.transition_state_entry_id


def test_a_barrier_cannot_cite_a_species_single_point(client) -> None:
    """The case #665's own matrix stored with a 201."""
    payload = _payload()
    payload["solve"]["channel_barriers"][0]["source_calculation_key"] = "ethyl_sp"
    body = _refused(
        client.post(_PDEP_URL, json=payload),
        field=_barrier_field(0),
        expected_owner_kind="transition_state_entry",
        actual_owner_kind="species_entry",
    )
    assert body["context"]["stated_value"] == "ts_elim", body


def test_a_later_barrier_cannot_cite_another_saddle_points_calculation(client) -> None:
    payload = _payload()
    payload["solve"]["channel_barriers"][1]["source_calculation_key"] = "ts_elim_sp"
    body = _refused(
        client.post(_PDEP_URL, json=payload),
        field=_barrier_field(1),
        expected_owner_kind="transition_state_entry",
        actual_owner_kind="transition_state_entry",
    )
    assert body["context"]["stated_value"] == "ts_elim_anti", body


def test_the_last_barrier_cannot_cite_another_saddle_points_calculation(client) -> None:
    payload = _payload()
    payload["solve"]["channel_barriers"][2]["source_calculation_key"] = "ts_elim_anti_sp"
    _refused(
        client.post(_PDEP_URL, json=payload),
        field=_barrier_field(2),
        expected_owner_kind="transition_state_entry",
        actual_owner_kind="transition_state_entry",
    )


# ---------------------------------------------------------------------------
# well_energy / barrier_energy roles
# ---------------------------------------------------------------------------


def _links(payload: dict) -> list[dict]:
    return payload["solve"]["source_calculations"]


def test_the_roles_of_the_unchanged_payload_are_stored(client, db_session) -> None:
    assert client.post(_PDEP_URL, json=_payload()).status_code == 201
    rows = db_session.scalars(select(NetworkSolveSourceCalculation)).all()
    roles = {(r.role.value, _owner_of(db_session, r.calculation_id).type.value) for r in rows}
    assert ("well_energy", "sp") in roles
    assert ("barrier_energy", "sp") in roles


def test_a_well_energy_link_cannot_cite_a_saddle_points_calculation(client) -> None:
    payload = _payload()
    _links(payload)[0] = {"calculation_key": "ts_elim_sp", "role": "well_energy"}
    body = _refused(
        client.post(_PDEP_URL, json=payload),
        field=_link_field(0),
        expected_owner_kind="species_entry",
        actual_owner_kind="transition_state_entry",
    )
    assert body["context"]["stated_value"] == "well_energy", body


def test_a_barrier_energy_link_cannot_cite_a_species_calculation(client) -> None:
    payload = _payload()
    index = next(i for i, link in enumerate(_links(payload)) if link["role"] == "barrier_energy")
    _links(payload)[index] = {"calculation_key": "ethyl_sp", "role": "barrier_energy"}
    body = _refused(
        client.post(_PDEP_URL, json=payload),
        field=_link_field(index),
        expected_owner_kind="transition_state_entry",
        actual_owner_kind="species_entry",
    )
    assert body["context"]["stated_value"] == "barrier_energy", body


def test_a_later_well_energy_link_is_checked_too(client) -> None:
    payload = _payload()
    _links(payload).append({"calculation_key": "ts_isomer_sp", "role": "well_energy"})
    _refused(
        client.post(_PDEP_URL, json=payload),
        field=_link_field(len(_links(payload)) - 1),
        expected_owner_kind="species_entry",
        actual_owner_kind="transition_state_entry",
    )


def test_a_well_energy_link_for_a_species_in_no_state_is_refused(client) -> None:
    """The bath gas is a species of the upload but not a participant of any state."""
    payload = _payload()
    ar = next(sp for sp in payload["species"] if sp["key"] == "Ar")
    ar["calculations"] = [
        {
            "key": "Ar_sp",
            "type": "sp",
            "geometry_key": "Ar_geom",
            "software_release": payload["species"][0]["calculations"][0]["software_release"],
            "level_of_theory": payload["species"][0]["calculations"][0]["level_of_theory"],
            "sp_electronic_energy_hartree": -527.0,
        }
    ]
    _links(payload).append({"calculation_key": "Ar_sp", "role": "well_energy"})
    _refused(
        client.post(_PDEP_URL, json=payload),
        field=_link_field(len(_links(payload)) - 1),
        expected_owner_kind="species_entry",
        actual_owner_kind="species_entry",
    )


def test_roles_that_are_not_energy_roles_are_not_held_to_a_subject(client) -> None:
    """A fit or run role names another job; it is not constrained here."""
    payload = _payload()
    _links(payload).append({"calculation_key": "ethyl_opt", "role": "fit_source"})
    assert client.post(_PDEP_URL, json=payload).status_code == 201


# ---------------------------------------------------------------------------
# The service re-checks; it does not rely on the wire schema
# ---------------------------------------------------------------------------


def _persist(db_conn, request: NetworkPDepUploadRequest) -> None:
    with Session(db_conn) as session, session.begin():
        actor = AppUser(username="network_energy_subject_tester")
        session.add(actor)
        session.flush()
        persist_network_pdep_upload(session, request, created_by=actor.id)


def _unvalidated(**solve_updates) -> NetworkPDepUploadRequest:
    """A valid request, then edited with ``model_copy`` so no validator sees the edit."""
    request = NetworkPDepUploadRequest(**_payload())
    assert request.solve is not None
    return request.model_copy(update={"solve": request.solve.model_copy(update=solve_updates)})


def _with(items: list, index: int, **update):
    return [item.model_copy(update=update) if i == index else item for i, item in enumerate(items)]


def test_service_refuses_an_unvalidated_state_energy_owner(db_conn) -> None:
    base = NetworkPDepUploadRequest(**_payload())
    energies = _with(base.solve.state_energies, 3, source_calculation_key="etoo_sp")
    with pytest.raises(CodedValueError) as raised:
        _persist(db_conn, _unvalidated(state_energies=energies))
    assert raised.value.code == _CODE
    assert raised.value.context["field"] == _state_field(3)


def test_service_refuses_an_unvalidated_barrier_owner(db_conn) -> None:
    base = NetworkPDepUploadRequest(**_payload())
    barriers = _with(base.solve.channel_barriers, 1, source_calculation_key="ethyl_sp")
    with pytest.raises(CodedValueError) as raised:
        _persist(db_conn, _unvalidated(channel_barriers=barriers))
    assert raised.value.code == _CODE
    assert raised.value.context["field"] == _barrier_field(1)


def test_service_refuses_an_unvalidated_energy_role_owner(db_conn) -> None:
    base = NetworkPDepUploadRequest(**_payload())
    index = next(i for i, link in enumerate(base.solve.source_calculations) if link.role.value == "barrier_energy")
    links = _with(base.solve.source_calculations, index, calculation_key="ethyl_sp")
    with pytest.raises(CodedValueError) as raised:
        _persist(db_conn, _unvalidated(source_calculations=links))
    assert raised.value.code == _CODE
    assert raised.value.context["field"] == _link_field(index)


def test_service_accepts_the_same_request_without_the_edit(db_conn) -> None:
    """The negative half of the three tests above."""
    _persist(db_conn, NetworkPDepUploadRequest(**_payload()))
