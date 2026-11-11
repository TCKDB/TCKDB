"""A shared applied energy correction is reused across computed-reaction uploads."""

from __future__ import annotations

import copy

import pytest
from sqlalchemy import select

from app.db.models.common import SubmissionRecordType
from app.db.models.energy_correction import (
    AppliedEnergyCorrection,
    AppliedEnergyCorrectionComponent,
)
from app.db.models.submission import SubmissionRecordLink
from tests.workflows.test_computed_reaction_upload import (
    _aec_scheme_ref_rxn,
    _bac_petersson_scheme_ref_rxn,
    _payload_with_aec_carriers,
)

_URL = "/api/v1/uploads/computed-reaction"
_CODE = "applied_energy_correction_value_conflict"

_CH_BOND = {
    "component_kind": "bond",
    "key": "C-H",
    "multiplicity": 4,
    "parameter_value": -0.11,
    "contribution_value": -0.44,
}


def _aec(value: float) -> dict:
    return {
        "scheme": _aec_scheme_ref_rxn(),
        "application_role": "aec_total",
        "value": value,
        "value_unit": "hartree",
    }


def _bac(value: float, components: list[dict]) -> dict:
    return {
        "scheme": _bac_petersson_scheme_ref_rxn(bond_params=[{"bond_key": "C-H", "value": -0.11}]),
        "application_role": "bac_total",
        "value": value,
        "value_unit": "hartree",
        "components": components,
    }


def _computed_species_payload() -> dict:
    lot = {"method": "B3LYP", "basis": "6-31G(d)"}
    software = {"name": "Gaussian", "version": "16"}
    return {
        "species_entry": {"smiles": "[H]", "charge": 0, "multiplicity": 2},
        "conformers": [
            {
                "key": "c0",
                "geometry": {"xyz_text": "1\nH atom\nH 0.0 0.0 0.0"},
                "primary_calculation": {
                    "key": "opt0",
                    "type": "opt",
                    "level_of_theory": lot,
                    "software_release": software,
                    "opt_result": {"converged": True},
                },
            }
        ],
    }


def _species_payload(correction: dict) -> dict:
    payload = _payload_with_aec_carriers()
    payload["species"][2]["applied_energy_corrections"] = [correction]
    return copy.deepcopy(payload)


def _ts_payload(correction: dict) -> dict:
    payload = _payload_with_aec_carriers()
    payload["transition_state"]["applied_energy_corrections"] = [correction]
    return copy.deepcopy(payload)


def _post(client, payload: dict):
    return client.post(_URL, json=payload)


def _assert_one_row_linked_twice(db_session) -> AppliedEnergyCorrection:
    rows = db_session.scalars(select(AppliedEnergyCorrection)).all()
    assert len(rows) == 1
    links = db_session.scalars(
        select(SubmissionRecordLink).where(
            SubmissionRecordLink.record_type == SubmissionRecordType.applied_energy_correction,
            SubmissionRecordLink.record_id == rows[0].id,
        )
    ).all()
    assert len({link.submission_id for link in links}) == 2
    return rows[0]


def test_species_aec_is_reused_by_a_second_upload(client, db_session) -> None:
    assert _post(client, _species_payload(_aec(-0.123))).status_code == 201
    second = _post(client, _species_payload(_aec(-0.123)))
    assert second.status_code == 201, second.text[:800]
    _assert_one_row_linked_twice(db_session)


def test_species_bac_with_components_is_reused_once(client, db_session) -> None:
    assert _post(client, _species_payload(_bac(-0.44, [_CH_BOND]))).status_code == 201
    second = _post(client, _species_payload(_bac(-0.44, [_CH_BOND])))
    assert second.status_code == 201, second.text[:800]
    row = _assert_one_row_linked_twice(db_session)
    components = db_session.scalars(
        select(AppliedEnergyCorrectionComponent).where(
            AppliedEnergyCorrectionComponent.applied_correction_id == row.id
        )
    ).all()
    assert len(components) == 1


def test_transition_state_corrections_stay_one_row_per_saddle_point(client, db_session) -> None:
    """Each upload creates its own transition state entry, so nothing is shared."""
    payload = _ts_payload(_bac(-0.44, [_CH_BOND]))
    assert _post(client, payload).status_code == 201
    assert _post(client, copy.deepcopy(payload)).status_code == 201
    rows = db_session.scalars(select(AppliedEnergyCorrection)).all()
    assert len(rows) == 2
    assert len({row.target_transition_state_entry_id for row in rows}) == 2


def test_computed_species_upload_reuses_the_shared_correction(client, db_session) -> None:
    payload = _computed_species_payload()
    payload["applied_energy_corrections"] = [_aec(-0.5)]
    url = "/api/v1/uploads/computed-species"
    assert client.post(url, json=payload).status_code == 201
    second = client.post(url, json=copy.deepcopy(payload))
    assert second.status_code == 201, second.text[:800]
    _assert_one_row_linked_twice(db_session)


def test_different_total_is_refused_with_the_registered_code(client, db_session) -> None:
    assert _post(client, _species_payload(_aec(-0.123))).status_code == 201
    second = _post(client, _species_payload(_aec(-0.124)))
    assert second.status_code == 422, second.text[:800]
    body = second.json()
    assert body["code"] == _CODE, body
    context = body["context"]
    assert context["record_kind"] == "applied_energy_correction"
    assert context["species_entry_ref"]
    assert context["scheme_ref"]
    assert context["differs"] == ["value"]
    assert context["field"] == "species['ch4'].applied_energy_corrections[0]"
    assert not any(key.endswith("_id") for key in context)
    assert len(db_session.scalars(select(AppliedEnergyCorrection)).all()) == 1


@pytest.mark.parametrize(
    "changed",
    [
        {**_CH_BOND, "multiplicity": 3},
        {**_CH_BOND, "contribution_value": -0.45},
    ],
)
def test_different_components_are_refused(client, changed) -> None:
    assert _post(client, _species_payload(_bac(-0.44, [_CH_BOND]))).status_code == 201
    second = _post(client, _species_payload(_bac(-0.44, [changed])))
    assert second.status_code == 422, second.text[:800]
    body = second.json()
    assert body["code"] == _CODE, body
    assert body["context"]["differs"] == ["components"]
