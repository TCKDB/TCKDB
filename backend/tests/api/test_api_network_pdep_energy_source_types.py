"""A network solve's energy sources are typed (#642).

``state_energies[].source_calculation_key``, ``channel_barriers[].source_calculation_key``
and the ``well_energy`` / ``barrier_energy`` roles of ``source_calculations`` used to
accept a calculation of any type, so a well's energy could be cited to an IRC point or
a rotor scan. The rule, and why an E0-style energy is held only to a floor, is in
``app.services.network_energy_sources``.

The expectation table below is written out per (stated energy, calculation type) and is
deliberately NOT derived from the service's own constants, so a wrong constant there
cannot make its own tests agree with it.

Each refusal is asserted by ``code`` and ``context``, never a substring of ``detail``,
and each accepted case checks the stored source row points at a calculation of the
type cited, so a payload that silently dropped the citation cannot pass as "accepted".
"""

from __future__ import annotations

from copy import deepcopy

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.error_contract import CodedValueError
from app.db.models.app_user import AppUser
from app.db.models.calculation import Calculation
from app.db.models.common import CalculationType
from app.db.models.network_pdep import (
    NetworkSolveChannelBarrier,
    NetworkSolveSourceCalculation,
    NetworkSolveStateEnergy,
)
from app.schemas.workflows.network_pdep_upload import NetworkPDepUploadRequest
from app.workflows.network_pdep import persist_network_pdep_upload
from tests.workflows.test_network_pdep_upload import (
    _LOT_DFT,
    _SOFTWARE,
    _full_payload,
    _parallel_path_payload,
)

_PDEP_URL = "/api/v1/uploads/networks/pdep"
_CODE = "network_energy_source_type_mismatch"

# One calculation of each type, keyed for citation. ``ethyl_opt`` and ``ethyl_freq`` and
# ``ethyl_sp`` and ``ts_elim_irc`` already exist in the shared payload; the rest are added
# by ``_payload`` so that no type is missing from the matrix.
_KEY_BY_TYPE = {
    "sp": "ethyl_sp",
    "opt": "ethyl_opt",
    "freq": "ethyl_freq",
    "irc": "ts_elim_irc",
    "scan": "ethyl_scan",
    "path_search": "ethyl_path",
    "conf": "ethyl_conf",
    "composite": "ethyl_comp",
}
_ALL_TYPES = tuple(_KEY_BY_TYPE)

#: The types each stated energy accepts, written out by hand.
_ACCEPTED = {
    "electronic_only": {"sp", "opt", "composite"},
    # Composed quantities: only the floor (a stationary-point energy carrier).
    "electronic_plus_zpe": {"sp", "opt", "freq", "composite"},
    "atom_and_bond_corrected": {"sp", "opt", "freq", "composite"},
    "thermal_enthalpy_298k": {"sp", "opt", "freq", "composite"},
    "other": {"sp", "opt", "freq", "composite"},
}
_ENERGY_ROLE_ACCEPTED = {"sp", "opt", "freq", "composite"}


def _payload(*, parallel: bool = False) -> dict:
    payload = deepcopy(_parallel_path_payload() if parallel else _full_payload())
    ethyl = next(sp for sp in payload["species"] if sp["key"] == "ethyl")
    base = {"geometry_key": "ethyl_geom", "software_release": _SOFTWARE, "level_of_theory": _LOT_DFT}
    ethyl["calculations"].extend(
        [
            {"key": "ethyl_scan", "type": "scan", **base},
            {"key": "ethyl_path", "type": "path_search", **base},
            {"key": "ethyl_conf", "type": "conf", **base},
            {
                "key": "ethyl_comp",
                "type": "composite",
                "geometry_key": "ethyl_geom",
                "software_release": _SOFTWARE,
                "level_of_theory": {"method": "CBS-QB3"},
                "composite_result": {
                    "assembly": "program_run",
                    "electronic_energy_hartree": -79.8,
                    "e0_hartree": -79.75,
                    "recipe_zpe_hartree": 0.05,
                },
            },
        ]
    )
    return payload


def _set_state_energy(payload: dict, key: str, correction: str) -> None:
    energy = payload["solve"]["state_energies"][0]
    energy["source_calculation_key"] = key
    energy["correction_convention"] = correction
    if correction == "other":
        energy["convention_note"] = "test"


def _set_barrier(payload: dict, key: str, correction: str) -> None:
    barrier = payload["solve"]["channel_barriers"][0]
    barrier["source_calculation_key"] = key
    barrier["correction_convention"] = correction
    if correction == "other":
        barrier["convention_note"] = "test"


def _refused(resp, *, field: str, calc_type: str) -> dict:
    assert resp.status_code == 422, resp.text
    body = resp.json()
    assert body.get("code") == _CODE, body
    assert body["context"]["field"] == field, body
    assert body["context"]["actual_calculation_type"] == calc_type, body
    assert "id" not in body["context"], body
    return body


# ---------------------------------------------------------------------------
# state_energies
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("calc_type", _ALL_TYPES)
@pytest.mark.parametrize("correction", sorted(_ACCEPTED))
def test_state_energy_source_type(client, db_session, correction: str, calc_type: str) -> None:
    payload = _payload()
    _set_state_energy(payload, _KEY_BY_TYPE[calc_type], correction)
    resp = client.post(_PDEP_URL, json=payload)
    if calc_type in _ACCEPTED[correction]:
        assert resp.status_code == 201, resp.text
        row = db_session.scalars(
            select(NetworkSolveStateEnergy).where(NetworkSolveStateEnergy.energy_kj_mol == 0.0)
        ).one()
        assert db_session.get(Calculation, row.source_calculation_id).type.value == calc_type
    else:
        body = _refused(
            resp,
            field="solve.state_energies[0].source_calculation_key",
            calc_type=calc_type,
        )
        assert body["context"]["stated_value"] == correction, body
        assert set(body["context"]["accepted_calculation_types"]) == _ACCEPTED[correction], body


# ---------------------------------------------------------------------------
# channel_barriers
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("calc_type", _ALL_TYPES)
@pytest.mark.parametrize("correction", ["electronic_only", "electronic_plus_zpe"])
def test_barrier_source_type(client, db_session, correction: str, calc_type: str) -> None:
    payload = _payload()
    _set_barrier(payload, _KEY_BY_TYPE[calc_type], correction)
    resp = client.post(_PDEP_URL, json=payload)
    if calc_type in _ACCEPTED[correction]:
        assert resp.status_code == 201, resp.text
        row = db_session.scalars(select(NetworkSolveChannelBarrier)).one()
        assert db_session.get(Calculation, row.source_calculation_id).type.value == calc_type
    else:
        body = _refused(
            resp,
            field="solve.channel_barriers[0].source_calculation_key",
            calc_type=calc_type,
        )
        assert body["context"]["stated_value"] == correction, body


# ---------------------------------------------------------------------------
# source_calculations roles
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("calc_type", _ALL_TYPES)
@pytest.mark.parametrize("role", ["well_energy", "barrier_energy"])
def test_energy_role_source_type(client, db_session, role: str, calc_type: str) -> None:
    payload = _payload()
    payload["solve"]["source_calculations"] = [
        {"calculation_key": _KEY_BY_TYPE[calc_type], "role": role}
    ]
    resp = client.post(_PDEP_URL, json=payload)
    if calc_type in _ENERGY_ROLE_ACCEPTED:
        assert resp.status_code == 201, resp.text
        row = db_session.scalars(select(NetworkSolveSourceCalculation)).one()
        assert row.role.value == role
        assert db_session.get(Calculation, row.calculation_id).type.value == calc_type
    else:
        body = _refused(
            resp,
            field="solve.source_calculations[0].calculation_key",
            calc_type=calc_type,
        )
        assert body["context"]["stated_value"] == role, body


# ---------------------------------------------------------------------------
# Every entry is checked, not only the first
# ---------------------------------------------------------------------------


def test_the_multi_entry_payload_is_accepted(client) -> None:
    """Accept-half: three barriers, four energies and several links, all fine."""
    payload = _payload(parallel=True)
    assert len(payload["solve"]["state_energies"]) >= 3
    assert len(payload["solve"]["channel_barriers"]) >= 2
    assert len(payload["solve"]["source_calculations"]) >= 3
    assert client.post(_PDEP_URL, json=payload).status_code == 201


def test_a_scan_in_a_later_state_energy_is_refused(client) -> None:
    payload = _payload(parallel=True)
    payload["solve"]["state_energies"][2]["source_calculation_key"] = "ethyl_scan"
    _refused(
        client.post(_PDEP_URL, json=payload),
        field="solve.state_energies[2].source_calculation_key",
        calc_type="scan",
    )


def test_a_scan_in_a_later_barrier_is_refused(client) -> None:
    payload = _payload(parallel=True)
    payload["solve"]["channel_barriers"][1]["source_calculation_key"] = "ethyl_scan"
    _refused(
        client.post(_PDEP_URL, json=payload),
        field="solve.channel_barriers[1].source_calculation_key",
        calc_type="scan",
    )


def test_a_scan_in_a_later_source_link_is_refused(client) -> None:
    payload = _payload(parallel=True)
    payload["solve"]["source_calculations"][2]["calculation_key"] = "ethyl_scan"
    _refused(
        client.post(_PDEP_URL, json=payload),
        field="solve.source_calculations[2].calculation_key",
        calc_type="scan",
    )


def test_roles_that_are_not_energy_roles_are_not_constrained(client) -> None:
    """The floor is for the two energy roles only; a fit or run role names another job."""
    payload = _payload()
    payload["solve"]["source_calculations"] = [
        {"calculation_key": "ts_elim_irc", "role": "fit_source"}
    ]
    assert client.post(_PDEP_URL, json=payload).status_code == 201


def test_composite_is_accepted_for_electronic_and_e0(client, db_session) -> None:
    """A composite carries both an electronic energy and an E0 (ADR 0021)."""
    for correction in ("electronic_only", "electronic_plus_zpe"):
        payload = _payload()
        _set_state_energy(payload, "ethyl_comp", correction)
        assert client.post(_PDEP_URL, json=payload).status_code == 201, correction


def test_the_unchanged_shared_payload_is_accepted(client) -> None:
    """The negative half: the matrix payload is refused for the source type and nothing else."""
    assert client.post(_PDEP_URL, json=_payload()).status_code == 201


# ---------------------------------------------------------------------------
# The service re-checks; it does not rely on the wire schema
# ---------------------------------------------------------------------------


def _persist(db_conn, request: NetworkPDepUploadRequest) -> None:
    with Session(db_conn) as session, session.begin():
        actor = AppUser(username="network_energy_source_tester")
        session.add(actor)
        session.flush()
        persist_network_pdep_upload(session, request, created_by=actor.id)


def _unvalidated(**solve_updates) -> NetworkPDepUploadRequest:
    """A valid request, then edited with ``model_copy`` so no validator sees the edit."""
    request = NetworkPDepUploadRequest(**_payload())
    assert request.solve is not None
    return request.model_copy(update={"solve": request.solve.model_copy(update=solve_updates)})


def test_service_refuses_an_unvalidated_state_energy_source(db_conn) -> None:
    base = NetworkPDepUploadRequest(**_payload())
    energies = [
        energy.model_copy(update={"source_calculation_key": "ethyl_scan"})
        if index == 0
        else energy
        for index, energy in enumerate(base.solve.state_energies)
    ]
    with pytest.raises(CodedValueError) as raised:
        _persist(db_conn, _unvalidated(state_energies=energies))
    assert raised.value.code == _CODE
    assert raised.value.context["field"] == "solve.state_energies[0].source_calculation_key"
    assert raised.value.context["actual_calculation_type"] == "scan"


def test_service_refuses_an_unvalidated_barrier_source(db_conn) -> None:
    base = NetworkPDepUploadRequest(**_payload())
    barriers = [
        base.solve.channel_barriers[0].model_copy(
            update={"source_calculation_key": "ethyl_freq", "correction_convention": "electronic_only"}
        )
    ]
    with pytest.raises(CodedValueError) as raised:
        _persist(db_conn, _unvalidated(channel_barriers=barriers))
    assert raised.value.code == _CODE
    assert raised.value.context["field"] == "solve.channel_barriers[0].source_calculation_key"
    assert raised.value.context["actual_calculation_type"] == "freq"


def test_service_refuses_an_unvalidated_energy_role_source(db_conn) -> None:
    base = NetworkPDepUploadRequest(**_payload())
    links = [
        base.solve.source_calculations[0].model_copy(update={"calculation_key": "ts_elim_irc"})
    ]
    with pytest.raises(CodedValueError) as raised:
        _persist(db_conn, _unvalidated(source_calculations=links))
    assert raised.value.code == _CODE
    assert raised.value.context["field"] == "solve.source_calculations[0].calculation_key"
    assert raised.value.context["actual_calculation_type"] == CalculationType.irc.value


def test_service_accepts_the_same_request_without_the_edit(db_conn) -> None:
    """The negative half of the three tests above."""
    _persist(db_conn, NetworkPDepUploadRequest(**_payload()))
