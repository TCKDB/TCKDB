"""The new evidence kinds on the pressure-dependent network bundle (#621).

The fragment ``TransitionStateValidationEvidenceIn`` and the persistence seam
are shared by three deposit paths. The computed-reaction bundle and the
standalone upload are covered in ``test_api_ts_contract_621.py``; this is the
third, because widening a shared ``kind`` without checking the path that has a
calculation-key namespace spanning every species *and* every transition state
would leave one route accepting an energy taken from the wrong molecule.

Assertions are on ``code`` and ``context``, never on a substring of ``detail``
alone.
"""

from __future__ import annotations

import copy

from sqlalchemy import select

from app.db.models.transition_state import TransitionStateEntry
from tests.api.test_api_ts_contract_621 import _MISSING_IRC, _codes, _energy, state_stored_energies
from tests.workflows.test_network_pdep_upload import _full_payload

_PDEP_URL = "/api/v1/uploads/networks/pdep"


def _ordering(**overrides) -> dict:
    """``ethylperoxy -> ethene + HO2``: the saddle point above the well and the exit."""
    record: dict = {
        "kind": "energy_ordering",
        "passed": True,
        "rationale": "TS above both wells",
        "energies": [
            _energy("ts", "electronic", -229.0, "ts_elim_sp"),
            _energy("reactant:1", "electronic", -229.3, "etoo_sp"),
            _energy("product:1", "electronic", -78.5, "ethene_sp"),
            _energy("product:2", "electronic", -150.6, "HO2_sp"),
        ],
    }
    record.update(overrides)
    return record


def _mode(**overrides) -> dict:
    record: dict = {
        "kind": "imaginary_mode",
        "passed": True,
        "rationale": "One imaginary mode along the H transfer",
        "source_calculation_key": "ts_elim_freq",
        "imaginary_frequency_count": 1,
        "imaginary_frequency_cm1": -1500.0,
        "mode_displacement_agrees": True,
    }
    record.update(overrides)
    return record


#: The energies ``_ordering`` states, as the calculations it cites store them
#: (issue #638): an ordering is held against the stored values.
_STORED_SP_HARTREE = {
    "ts_elim_sp": -229.0,
    "etoo_sp": -229.3,
    "ethene_sp": -78.5,
    "HO2_sp": -150.6,
}


def _payload(*extra: dict) -> dict:
    payload = state_stored_energies(copy.deepcopy(_full_payload()), sp=_STORED_SP_HARTREE, zpe={})
    payload["transition_states"][0]["validation_evidence"].extend(extra)
    return payload


def _post(client, payload: dict):
    return client.post(_PDEP_URL, json=payload)


def _entry_ref(db_session) -> str:
    return db_session.scalars(select(TransitionStateEntry.public_ref)).one()


def _evidence(client, ref: str) -> dict[str, dict]:
    response = client.get(
        f"/api/v1/scientific/transition-state-entries/{ref}?include=validation_evidence"
    )
    assert response.status_code == 200, response.text[:800]
    return {item["kind"]: item for item in response.json()["record"]["validation_evidence"]}


class TestEvidenceKindsOnThePdepBundle:
    def test_both_new_kinds_are_stored_beside_the_irc_and_read_back(self, client, db_session):
        response = _post(client, _payload(_ordering(), _mode()))
        assert response.status_code == 201, response.text[:1200]

        by_kind = _evidence(client, _entry_ref(db_session))
        assert sorted(by_kind) == ["energy_ordering", "imaginary_mode", "irc"]
        mode = by_kind["imaginary_mode"]
        assert mode["imaginary_frequency_count"] == 1
        assert mode["imaginary_frequency_cm1"] == -1500.0
        assert mode["mode_displacement_agrees"] is True
        energies = {
            (e["participant"], e["energy_kind"]): e["energy_hartree"]
            for e in by_kind["energy_ordering"]["compared_energies"]
        }
        assert energies == {
            ("ts", "electronic"): -229.0,
            ("reactant:1", "electronic"): -229.3,
            ("product:1", "electronic"): -78.5,
            ("product:2", "electronic"): -150.6,
        }
        assert all(
            e["source_calculation_ref"] for e in by_kind["energy_ordering"]["compared_energies"]
        )

    def test_the_irc_warning_depends_on_the_irc_alone(self, client):
        payload = _payload(_ordering(), _mode())
        payload["transition_states"][0]["validation_evidence"][0]["passed"] = False
        response = _post(client, payload)
        assert response.status_code == 201, response.text[:1200]
        assert _MISSING_IRC in _codes(response.json())

    def test_an_energy_cannot_come_from_a_species_that_is_not_that_participant(self, client):
        record = _ordering()
        record["energies"][1] = _energy("reactant:1", "electronic", -229.3, "ethyl_sp")
        response = _post(client, _payload(record))
        assert response.status_code == 422, response.text[:1200]
        body = response.json()
        assert body["code"] == "calculation_key_undeclared", body
        assert body["context"]["key"] == "ethyl_sp", body
        assert "etoo_sp" in body["context"]["declared_keys"], body

    def test_the_saddle_points_energy_cannot_come_from_a_species(self, client):
        record = _ordering()
        record["energies"][0] = _energy("ts", "electronic", -229.0, "etoo_sp")
        response = _post(client, _payload(record))
        assert response.status_code == 422, response.text[:1200]
        assert response.json()["code"] == "calculation_key_undeclared", response.json()

    def test_a_passing_ordering_with_a_side_left_out_is_refused(self, client):
        record = _ordering()
        record["energies"] = [e for e in record["energies"] if e["participant"] != "product:2"]
        response = _post(client, _payload(record))
        assert response.status_code == 422, response.text[:1200]
        assert "product:2" in str(response.json()["detail"])

    def test_a_pass_the_energies_contradict_is_refused(self, client):
        record = _ordering()
        record["energies"][0] = _energy("ts", "electronic", -229.4, "ts_elim_sp")
        response = _post(client, _payload(record))
        assert response.status_code == 422, response.text[:1200]
        assert "at or below" in str(response.json()["detail"])

    def test_an_imaginary_mode_must_name_a_freq_calculation(self, client):
        response = _post(client, _payload(_mode(source_calculation_key="ts_elim_irc")))
        assert response.status_code == 422, response.text[:1200]
        assert "requires a freq calculation" in str(response.json()["detail"])

    def test_an_imaginary_mode_cannot_name_a_species_calculation(self, client):
        response = _post(client, _payload(_mode(source_calculation_key="ethyl_freq")))
        assert response.status_code == 422, response.text[:1200]
        assert response.json()["code"] == "calculation_key_undeclared", response.json()

    def test_a_pass_with_no_imaginary_mode_is_refused(self, client):
        record = _mode(imaginary_frequency_count=0)
        del record["imaginary_frequency_cm1"]
        response = _post(client, _payload(record))
        assert response.status_code == 422, response.text[:1200]

    def test_a_second_record_of_one_kind_is_refused(self, client):
        response = _post(client, _payload(_mode(), _mode()))
        assert response.status_code == 422, response.text[:1200]
