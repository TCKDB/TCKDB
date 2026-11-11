"""A shared applied energy correction is reused across computed-reaction uploads."""

from __future__ import annotations

import copy
import functools
import itertools
import math
import operator
from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from app.db.models.common import RecordReviewStatus, SubmissionRecordType
from app.db.models.energy_correction import (
    AppliedEnergyCorrection,
    AppliedEnergyCorrectionComponent,
)
from app.db.models.submission import SubmissionRecordLink
from app.services.record_review import get_record_review
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


@pytest.fixture
def first_lookup_misses(monkeypatch):
    """Make the first lookup miss, as when a concurrent upload inserts the row after it."""
    from app.services import energy_correction_resolution as ecr

    real = ecr._find_applied_correction
    calls = {"n": 0}

    def _lookup(session, key):
        calls["n"] += 1
        return None if calls["n"] == 1 else real(session, key)

    monkeypatch.setattr(ecr, "_find_applied_correction", _lookup)
    return calls


def test_losing_the_insert_race_adopts_the_winner(client, db_session, first_lookup_misses) -> None:
    # The fixture patches the lookup, so the winner is deposited first and the patch counts from there.
    first_lookup_misses["n"] = 1
    assert _post(client, _species_payload(_aec(-0.123))).status_code == 201
    first_lookup_misses["n"] = 0
    second = _post(client, _species_payload(_aec(-0.123)))
    assert second.status_code == 201, second.text[:800]
    assert first_lookup_misses["n"] >= 2
    _assert_one_row_linked_twice(db_session)


def test_losing_the_insert_race_still_checks_the_value(client, first_lookup_misses) -> None:
    first_lookup_misses["n"] = 1
    assert _post(client, _species_payload(_aec(-0.123))).status_code == 201
    first_lookup_misses["n"] = 0
    second = _post(client, _species_payload(_aec(-0.2)))
    assert second.status_code == 422, second.text[:800]
    assert second.json()["code"] == _CODE


# ---------------------------------------------------------------------------
# Tolerance, units, component kind and key
# ---------------------------------------------------------------------------

_BASE = -114.9101091
_ATOM_ENERGIES = (-37.8450137, -0.4998098, -37.8450137, -38.7201091, -0.0123456)
_OTHER_X = {
    "component_kind": "other",
    "key": "x",
    "multiplicity": 1,
    "parameter_value": 0.01,
    "contribution_value": 0.01,
}


def _aec_in(value: float, unit: str) -> dict:
    return {**_aec(value), "value_unit": unit}


def test_totals_one_ulp_apart_are_reused(client, db_session) -> None:
    assert _post(client, _species_payload(_aec(_BASE))).status_code == 201
    second = _post(client, _species_payload(_aec(math.nextafter(_BASE, 0.0))))
    assert second.status_code == 201, second.text[:800]
    _assert_one_row_linked_twice(db_session)


def test_atom_energies_summed_in_a_different_order_are_reused(client, db_session) -> None:
    by_total = {}
    for order in itertools.permutations(_ATOM_ENERGIES):
        by_total.setdefault(functools.reduce(operator.add, order), order)
    assert len(by_total) > 1
    for total in by_total:
        assert _post(client, _species_payload(_aec(total))).status_code == 201
    assert len(db_session.scalars(select(AppliedEnergyCorrection)).all()) == 1


def test_totals_in_different_units_for_the_same_energy_are_reused(client, db_session) -> None:
    hartree = -0.123
    kj_mol = hartree * 2625.4996394798254
    assert _post(client, _species_payload(_aec_in(hartree, "hartree"))).status_code == 201
    second = _post(client, _species_payload(_aec_in(kj_mol, "kj_mol")))
    assert second.status_code == 201, second.text[:800]
    _assert_one_row_linked_twice(db_session)


def test_component_values_one_ulp_apart_are_reused(client, db_session) -> None:
    nudged = {
        **_CH_BOND,
        "parameter_value": math.nextafter(-0.11, 0.0),
        "contribution_value": math.nextafter(-0.44, 0.0),
    }
    assert _post(client, _species_payload(_bac(-0.44, [_CH_BOND]))).status_code == 201
    second = _post(client, _species_payload(_bac(-0.44, [nudged])))
    assert second.status_code == 201, second.text[:800]
    _assert_one_row_linked_twice(db_session)


def test_a_different_parameter_value_is_refused(client) -> None:
    assert _post(client, _species_payload(_bac(-0.44, [_CH_BOND]))).status_code == 201
    second = _post(client, _species_payload(_bac(-0.44, [{**_CH_BOND, "parameter_value": -0.12}])))
    assert second.status_code == 422, second.text[:800]
    assert second.json()["code"] == _CODE


@pytest.mark.parametrize(
    "changed",
    [
        {**_OTHER_X, "component_kind": "bond"},
        {**_OTHER_X, "key": "y"},
    ],
)
def test_a_different_component_kind_or_key_is_refused(client, changed) -> None:
    assert _post(client, _species_payload(_bac(-0.44, [_CH_BOND, _OTHER_X]))).status_code == 201
    second = _post(client, _species_payload(_bac(-0.44, [_CH_BOND, changed])))
    assert second.status_code == 422, second.text[:800]
    assert second.json()["code"] == _CODE


def test_the_refusal_states_both_totals_and_the_remedy(client) -> None:
    assert _post(client, _species_payload(_aec(-0.123))).status_code == 201
    body = _post(client, _species_payload(_aec_in(-0.5, "kj_mol"))).json()
    context = body["context"]
    assert (context["stored_value"], context["stored_unit"]) == (-0.123, "hartree")
    assert (context["supplied_value"], context["supplied_unit"]) == (-0.5, "kj_mol")
    assert "source_calculation_key" in body["detail"]
    assert "earlier in this upload" in body["detail"]
    assert not any(key.endswith("_id") for key in context)


def test_a_clash_inside_one_upload_collapses_to_one_row(client, db_session) -> None:
    payload = _species_payload(_aec(-0.123))
    payload["species"][2]["applied_energy_corrections"].append(_aec(-0.123))
    assert _post(client, payload).status_code == 201
    assert len(db_session.scalars(select(AppliedEnergyCorrection)).all()) == 1


def test_a_clash_inside_one_upload_with_different_values_is_refused(client) -> None:
    payload = _species_payload(_aec(-0.123))
    payload["species"][2]["applied_energy_corrections"].append(_aec(-0.2))
    response = _post(client, payload)
    assert response.status_code == 422, response.text[:800]
    assert response.json()["code"] == _CODE


# ---------------------------------------------------------------------------
# Warnings on reuse
# ---------------------------------------------------------------------------


def _codes(response) -> list[str]:
    return [w["code"] for w in response.json()["warnings"]]


def test_a_dropped_note_is_warned_about(client) -> None:
    assert _post(client, _species_payload({**_aec(-0.123), "note": "first note"})).status_code == 201
    second = _post(client, _species_payload({**_aec(-0.123), "note": "second note"}))
    assert second.status_code == 201, second.text[:800]
    warning = next(
        w for w in second.json()["warnings"] if w["code"] == "applied_energy_correction_note_not_stored"
    )
    assert "second note" not in warning["message"]
    assert "applied_energy_corrections[0]" in warning["field"]


def test_the_same_or_no_incoming_note_is_not_warned_about(client) -> None:
    assert _post(client, _species_payload({**_aec(-0.123), "note": "same"})).status_code == 201
    same = _post(client, _species_payload({**_aec(-0.123), "note": "same"}))
    absent = _post(client, _species_payload(_aec(-0.123)))
    for response in (same, absent):
        assert "applied_energy_correction_note_not_stored" not in _codes(response)


@pytest.mark.parametrize("status", [RecordReviewStatus.rejected, RecordReviewStatus.deprecated])
def test_reusing_a_rejected_or_deprecated_correction_is_warned_about(client, db_session, status) -> None:
    assert _post(client, _species_payload(_aec(-0.123))).status_code == 201
    row = db_session.scalars(select(AppliedEnergyCorrection)).one()
    review = get_record_review(
        db_session, record_type=SubmissionRecordType.applied_energy_correction, record_id=row.id
    )
    review.status = status
    review.reviewed_by = review.created_by
    review.reviewed_at = datetime.now(timezone.utc)
    db_session.flush()
    second = _post(client, _species_payload(_aec(-0.123)))
    assert second.status_code == 201, second.text[:800]
    warning = next(
        w for w in second.json()["warnings"] if w["code"] == "applied_energy_correction_reused_hidden"
    )
    assert warning["context"]["review_status"] == status.value


def test_reusing_a_pending_correction_raises_no_hidden_warning(client) -> None:
    assert _post(client, _species_payload(_aec(-0.123))).status_code == 201
    second = _post(client, _species_payload(_aec(-0.123)))
    assert "applied_energy_correction_reused_hidden" not in _codes(second)


# ---------------------------------------------------------------------------
# Other routes
# ---------------------------------------------------------------------------


def test_a_standalone_transition_state_correction_is_linked_and_reviewed(client, db_session) -> None:
    from tests.api.test_api_reads import _ts_upload_payload

    payload = _ts_upload_payload()
    payload["applied_energy_corrections"] = [_aec(-0.123)]
    response = client.post("/api/v1/uploads/transition-states", json=payload)
    assert response.status_code == 201, response.text[:800]
    row = db_session.scalars(select(AppliedEnergyCorrection)).one()
    link = db_session.scalars(
        select(SubmissionRecordLink).where(
            SubmissionRecordLink.record_type == SubmissionRecordType.applied_energy_correction,
            SubmissionRecordLink.record_id == row.id,
        )
    ).one()
    assert link.submission_id == response.json()["submission_id"]
    review = get_record_review(
        db_session, record_type=SubmissionRecordType.applied_energy_correction, record_id=row.id
    )
    assert review is not None


def test_thermo_route_reuses_the_shared_correction(client, db_session) -> None:
    payload = {
        "enthalpy_reference_kind": "formation_298k",
        "species_entry": {"smiles": "[H]", "charge": 0, "multiplicity": 2},
        "scientific_origin": "computed",
        "h298_kj_mol": 217.998,
        "applied_energy_corrections": [_aec(-0.123)],
    }
    assert client.post("/api/v1/uploads/thermo", json=payload).status_code == 201
    second = client.post("/api/v1/uploads/thermo", json=copy.deepcopy(payload))
    assert second.status_code == 201, second.text[:800]
    _assert_one_row_linked_twice(db_session)


def test_conformer_route_reuses_the_shared_correction(client, db_session) -> None:
    payload = {
        "species_entry": {"smiles": "[H]", "charge": 0, "multiplicity": 2},
        "geometry": {"xyz_text": "1\nH atom\nH 0.0 0.0 0.0"},
        "calculation": {
            "type": "sp",
            "software_release": {"name": "Gaussian", "version": "16"},
            "level_of_theory": {"method": "B3LYP", "basis": "6-31G(d)"},
        },
        "label": "h-conf",
        "applied_energy_corrections": [_aec(-0.123)],
    }
    assert client.post("/api/v1/uploads/conformers", json=payload).status_code == 201
    second = client.post("/api/v1/uploads/conformers", json=copy.deepcopy(payload))
    assert second.status_code == 201, second.text[:800]
    _assert_one_row_linked_twice(db_session)


# ---------------------------------------------------------------------------
# Refusal wording and per-route remedy
# ---------------------------------------------------------------------------


def test_a_component_only_difference_does_not_print_equal_totals(client) -> None:
    assert _post(client, _species_payload(_bac(-0.44, [_CH_BOND]))).status_code == 201
    body = _post(client, _species_payload(_bac(-0.44, [{**_CH_BOND, "multiplicity": 3}]))).json()
    assert "component breakdown differs" in body["detail"]
    assert "stored" not in body["detail"]
    assert body["context"]["stored_value"] == body["context"]["supplied_value"]


def test_a_total_difference_prints_both_totals(client) -> None:
    assert _post(client, _species_payload(_aec(-0.123))).status_code == 201
    body = _post(client, _species_payload(_aec(-0.2))).json()
    assert "total differs" in body["detail"]
    assert "stored -0.123 hartree, sent -0.2 hartree" in body["detail"]


def test_the_standalone_transition_state_refusal_does_not_advise_a_source_key(client) -> None:
    from tests.api.test_api_reads import _ts_upload_payload

    payload = _ts_upload_payload()
    payload["applied_energy_corrections"] = [_aec(-0.123), _aec(-0.2)]
    response = client.post("/api/v1/uploads/transition-states", json=payload)
    assert response.status_code == 422, response.text[:800]
    body = response.json()
    assert body["code"] == _CODE
    assert "source_calculation_key" not in body["detail"]
    assert "one scheme and role on one transition state must agree" in body["detail"]


def test_the_other_routes_still_advise_a_source_key(client) -> None:
    assert _post(client, _species_payload(_aec(-0.123))).status_code == 201
    body = _post(client, _species_payload(_aec(-0.2))).json()
    assert "source_calculation_key" in body["detail"]


def test_stored_components_in_kj_mol_are_reused_by_a_hartree_deposit(client, db_session) -> None:
    kj = 2625.4996394798254
    in_kj = {**_CH_BOND, "contribution_value": _CH_BOND["contribution_value"] * kj}
    first = {**_bac(-0.44 * kj, [in_kj]), "value_unit": "kj_mol"}
    assert _post(client, _species_payload(first)).status_code == 201
    second = _post(client, _species_payload(_bac(-0.44, [_CH_BOND])))
    assert second.status_code == 201, second.text[:800]
    _assert_one_row_linked_twice(db_session)


def test_components_in_another_unit_for_the_same_energy_are_reused(client, db_session) -> None:
    kj = 2625.4996394798254
    in_kj = {**_CH_BOND, "contribution_value": _CH_BOND["contribution_value"] * kj}
    assert _post(client, _species_payload(_bac(-0.44, [_CH_BOND]))).status_code == 201
    second = _post(client, _species_payload({**_bac(-0.44 * kj, [in_kj]), "value_unit": "kj_mol"}))
    assert second.status_code == 201, second.text[:800]
    _assert_one_row_linked_twice(db_session)
