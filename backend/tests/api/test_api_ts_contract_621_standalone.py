"""Standalone transition-state route gaps (#621): scans, corrections, atom map.

The standalone ``POST /api/v1/uploads/transition-states`` route lagged the
reaction bundle in three ways. Each is closed here and read back over HTTP:

* rotor **scans** were not an allowed additional calculation;
* **applied energy corrections** had no field;
* an **atom map** (ADR 0011) had no field, and could not have one, because the
  payload described its participants by identity alone and a map counts its
  indices into a geometry. A participant may now carry a ``key`` and a
  ``geometry``, and the request a ``geometry_key`` for the saddle point.

The IRC-direction half of the issue is in
``test_api_ts_contract_621_irc.py``; the evidence kinds are in
``test_api_ts_contract_621.py``.
"""

from __future__ import annotations

import copy

from sqlalchemy import func, select

from app.db.models.calculation import Calculation
from app.db.models.energy_correction import AppliedEnergyCorrection, EnergyCorrectionScheme
from tests.api.test_api_ts_contract_621 import (
    _LOT,
    _SOFTWARE,
    _STANDALONE,
    _XYZ_CH3,
    _XYZ_CH4,
    _XYZ_H,
    _ch4_scan_result_payload,
    _codes,
    _entry_read,
    _entry_ref,
    _standalone,
    _standalone_ok,
    _standalone_refused,
)


def _ts_calculations(db_session, entry_id: int):
    return db_session.scalars(
        select(Calculation)
        .where(Calculation.transition_state_entry_id == entry_id)
        .order_by(Calculation.id)
    ).all()


# ---------------------------------------------------------------------------
# Scans
# ---------------------------------------------------------------------------


def _scan_calc() -> dict:
    return {
        "type": "scan",
        "software_release": _SOFTWARE,
        "level_of_theory": _LOT,
        "scan_result": _ch4_scan_result_payload(points=3),
    }


class TestStandaloneScans:
    def test_a_ts_scan_is_accepted_and_its_points_read_back(self, client, db_session):
        payload = _standalone()
        payload["additional_calculations"].append(_scan_calc())
        result = _standalone_ok(client, payload)

        scans = [c for c in _ts_calculations(db_session, result["id"]) if c.type.value == "scan"]
        assert len(scans) == 1
        read = client.get(f"/api/v1/scientific/calculations/{scans[0].public_ref}?include=results")
        assert read.status_code == 200, read.text[:800]
        results = read.json()["record"]["results"]
        assert results["kind"] == "scan"
        assert results["scan"]["dimension"] == 1
        assert results["scan"]["is_relaxed"] is True

        entry = _entry_read(client, _entry_ref(db_session, result["id"]), "calculations")
        assert "scan" in {c["type"] for c in entry["calculations"]}

    def test_a_scan_without_a_result_is_accepted_as_a_calculation(self, client, db_session):
        payload = _standalone()
        payload["additional_calculations"].append(
            {"type": "scan", "software_release": _SOFTWARE, "level_of_theory": _LOT}
        )
        result = _standalone_ok(client, payload)
        assert any(c.type.value == "scan" for c in _ts_calculations(db_session, result["id"]))

    def test_a_scan_result_on_another_type_is_still_refused(self, client):
        payload = _standalone()
        payload["additional_calculations"][0]["scan_result"] = _ch4_scan_result_payload()
        _standalone_refused(client, payload)

    def test_types_outside_the_allowed_set_are_still_refused(self, client):
        payload = _standalone()
        payload["additional_calculations"].append(
            {"type": "opt", "software_release": _SOFTWARE, "level_of_theory": _LOT}
        )
        body = _standalone_refused(client, payload)
        assert "not allowed" in str(body["detail"]), body


# ---------------------------------------------------------------------------
# Applied energy corrections
# ---------------------------------------------------------------------------

_AEC_LOT = {"method": "B3LYP", "basis": "6-31G(d)"}


def _aec(**overrides) -> dict:
    record: dict = {
        "scheme": {
            "kind": "atom_energy",
            "name": "AEC v1 (standalone TS)",
            "level_of_theory": dict(_AEC_LOT),
            "units": "hartree",
        },
        "application_role": "aec_total",
        "value": -0.111,
        "value_unit": "hartree",
    }
    record.update(overrides)
    return record


class TestStandaloneAppliedEnergyCorrections:
    def test_a_correction_targets_the_ts_entry_and_reads_back_through_its_scheme(
        self, client, db_session
    ):
        result = _standalone_ok(client, _standalone(applied_energy_corrections=[_aec()]))
        applied = db_session.scalars(
            select(AppliedEnergyCorrection).where(
                AppliedEnergyCorrection.target_transition_state_entry_id == result["id"]
            )
        ).one()
        assert applied.target_species_entry_id is None
        assert applied.target_reaction_entry_id is None
        assert applied.application_role.value == "aec_total"
        assert applied.source_calculation_id is None

        scheme = db_session.get(EnergyCorrectionScheme, applied.scheme_id)
        response = client.get(
            f"/api/v1/scientific/energy-correction-schemes/{scheme.public_ref}?include=used_by"
        )
        assert response.status_code == 200, response.text[:800]
        (usage,) = response.json()["record"]["used_by"]
        assert usage["record_type"] == "transition_state_entry"
        assert usage["record_ref"] == _entry_ref(db_session, result["id"])
        assert usage["applied_value"] == -0.111
        assert usage["application_role"] == "aec_total"

    def test_several_corrections_are_each_stored(self, client, db_session):
        corrections = [
            _aec(),
            _aec(
                application_role="soc_total",
                scheme={
                    "kind": "soc",
                    "name": "SOC v1 (standalone TS)",
                    "level_of_theory": dict(_AEC_LOT),
                    "units": "hartree",
                },
                value=-0.002,
            ),
        ]
        result = _standalone_ok(client, _standalone(applied_energy_corrections=corrections))
        count = db_session.scalar(
            select(func.count()).where(
                AppliedEnergyCorrection.target_transition_state_entry_id == result["id"]
            )
        )
        assert count == 2

    def test_a_source_calculation_key_is_refused_where_there_is_no_namespace(self, client):
        body = _standalone_refused(
            client,
            _standalone(applied_energy_corrections=[_aec(source_calculation_key="x")]),
        )
        assert "no calculation-key namespace" in str(body["detail"]), body

    def test_a_source_conformer_key_is_refused(self, client):
        body = _standalone_refused(
            client,
            _standalone(applied_energy_corrections=[_aec(source_conformer_key="x")]),
        )
        assert "declares no conformers" in str(body["detail"]), body

    def test_a_frequency_scale_factor_is_refused(self, client):
        correction = {
            "frequency_scale_factor": {
                "level_of_theory": dict(_AEC_LOT),
                "scale_kind": "fundamental",
                "value": 0.97,
            },
            "application_role": "zpe",
            "value": 0.97,
            "value_unit": "hartree",
            "source_calculation_key": "x",
        }
        body = _standalone_refused(client, _standalone(applied_energy_corrections=[correction]))
        assert "frequency_scale_factor is not accepted" in str(body["detail"]), body

    def test_a_bac_total_without_components_is_refused_as_on_the_bundle(self, client):
        correction = _aec(
            application_role="bac_total",
            scheme={
                "kind": "bac_petersson",
                "name": "BAC v1 (standalone TS)",
                "level_of_theory": dict(_AEC_LOT),
                "units": "hartree",
            },
        )
        response = client.post(_STANDALONE, json=_standalone(applied_energy_corrections=[correction]))
        assert response.status_code == 422, response.text[:800]
        assert response.json()["code"] == "bac_total_requires_components", response.json()


# ---------------------------------------------------------------------------
# Atom map
# ---------------------------------------------------------------------------

_ATOM_MAP_PARTICIPANTS = [
    {
        "side": "reactant",
        "species_key": "ch3",
        "participant_index": 1,
        "geometry_key": "ch3-geom",
        "atom_to_ts": {1: 1, 2: 2, 3: 3, 4: 4},
    },
    {
        "side": "reactant",
        "species_key": "h",
        "participant_index": 2,
        "geometry_key": "h-geom",
        "atom_to_ts": {1: 5},
    },
    {
        "side": "product",
        "species_key": "ch4",
        "participant_index": 1,
        "geometry_key": "ch4-geom",
        "atom_to_ts": {1: 1, 2: 2, 3: 3, 4: 4, 5: 5},
    },
]


def _mapped(**changes) -> dict:
    """The standalone CH3 + H -> CH4 upload with participant geometries and a map."""
    payload = _standalone()
    reaction = payload["reaction"]
    for member, key, xyz in (
        (reaction["reactants"][0], "ch3", _XYZ_CH3),
        (reaction["reactants"][1], "h", _XYZ_H),
        (reaction["products"][0], "ch4", _XYZ_CH4),
    ):
        member["key"] = key
        member["geometry"] = {"key": f"{key}-geom", "xyz_text": xyz}
    payload["geometry_key"] = "ts-geom"
    payload["atom_map"] = {
        "source": "declared",
        "ts_geometry_key": "ts-geom",
        "participants": copy.deepcopy(_ATOM_MAP_PARTICIPANTS),
    }
    payload.update(changes)
    return payload


def _full(client, reaction_entry_id: int, *includes: str) -> dict:
    query = f"?include={','.join(includes)}" if includes else ""
    response = client.get(f"/api/v1/scientific/reaction-entries/{reaction_entry_id}/full{query}")
    assert response.status_code == 200, response.text[:800]
    return response.json()


class TestStandaloneAtomMap:
    def test_a_map_is_stored_and_read_back_atom_by_atom(self, client):
        result = _standalone_ok(client, _mapped())
        assert "reaction_atom_map_absent" not in _codes(result)
        body = _full(client, result["reaction_entry_id"], "atom_map")

        (badge,) = body["reaction_entry"]["atom_maps"]
        assert badge["source"] == "declared"
        assert badge["reactant_atoms_mapped"] == 5
        assert badge["product_atoms_mapped"] == 5
        (detail,) = body["atom_map"]
        assert len(detail["pairs"]) == 10
        reactant_leg = {
            (p["participant_index"], p["atom_index"], p["ts_atom_index"])
            for p in detail["pairs"]
            if p["side"] == "reactant"
        }
        assert (2, 1, 5) in reactant_leg
        assert (1, 4, 4) in reactant_leg

    def test_without_a_map_the_absence_is_reported_and_unmapped_reads_as_unmapped(self, client):
        result = _standalone_ok(client, _standalone())
        assert "reaction_atom_map_absent" in _codes(result)
        assert _full(client, result["reaction_entry_id"])["reaction_entry"]["atom_maps"] == []

    def test_a_map_needs_the_saddle_point_geometry_named(self, client):
        payload = _mapped()
        del payload["geometry_key"]
        body = _standalone_refused(client, payload)
        assert "geometry_key" in str(body["detail"]), body

    def test_a_map_needs_every_participant_to_carry_a_key(self, client):
        payload = _mapped()
        del payload["reaction"]["reactants"][1]["key"]
        body = _standalone_refused(client, payload)
        assert "carry a key" in str(body["detail"]), body

    def test_duplicate_participant_keys_are_refused(self, client):
        payload = _mapped()
        payload["reaction"]["products"][0]["key"] = "ch3"
        body = _standalone_refused(client, payload)
        assert "participant keys must be unique" in str(body["detail"]), body

    def test_a_participant_geometry_may_not_reuse_the_saddle_point_key(self, client):
        payload = _mapped()
        payload["reaction"]["products"][0]["geometry"]["key"] = "ts-geom"
        body = _standalone_refused(client, payload)
        assert "geometry keys" in str(body["detail"]), body

    def test_a_map_naming_an_undeclared_geometry_is_refused_with_its_code(self, client):
        payload = _mapped()
        payload["atom_map"]["participants"][0]["geometry_key"] = "nope"
        body = _standalone_refused(client, payload)
        assert body["code"] == "atom_map_indices_not_geometry_relative", body

    def test_a_map_naming_the_wrong_saddle_point_geometry_is_refused(self, client):
        payload = _mapped()
        payload["atom_map"]["ts_geometry_key"] = "other"
        body = _standalone_refused(client, payload)
        assert body["code"] == "atom_map_indices_not_geometry_relative", body

    def test_a_map_that_changes_an_element_is_refused_with_its_code(self, client):
        payload = _mapped()
        # Send methane's carbon to a saddle-point hydrogen.
        payload["atom_map"]["participants"][2]["atom_to_ts"] = {1: 2, 2: 1, 3: 3, 4: 4, 5: 5}
        body = _standalone_refused(client, payload)
        assert body["code"] == "atom_map_element_not_conserved", body

    def test_a_participant_geometry_that_is_another_molecule_is_refused(self, client):
        """The map counts into the participant geometry, so it must be the participant's."""
        payload = _mapped()
        # Methane's five atoms under a methyl identity. The map covers four of
        # them, so it is self-consistent and only the composition rule can
        # refuse the geometry.
        payload["reaction"]["reactants"][0]["geometry"]["xyz_text"] = _XYZ_CH4
        body = _standalone_refused(client, payload)
        assert body["code"] == "species_geometry_composition_mismatch", body

    def test_a_participant_geometry_without_a_map_is_stored_without_one(self, client):
        payload = _mapped()
        del payload["atom_map"]
        result = _standalone_ok(client, payload)
        assert "reaction_atom_map_absent" in _codes(result)

    def test_a_map_that_contradicts_the_irc_partition_is_refused(self, client):
        payload = _mapped()
        payload["additional_calculations"].append(
            {"type": "irc", "software_release": _SOFTWARE, "level_of_theory": _LOT}
        )
        # Both are element-consistent. The IRC gives the lone hydrogen
        # saddle-point atom 4; the map gives it atom 5.
        payload["validation_evidence"] = [
            {
                "kind": "irc",
                "passed": True,
                "rationale": "IRC endpoints",
                "reactant_participant_mapping": {"reactant:1": [1, 2, 3, 5], "reactant:2": [4]},
                "product_participant_mapping": {"product:1": [1, 2, 3, 4, 5]},
            }
        ]
        body = _standalone_refused(client, payload)
        assert body["code"] == "atom_map_contradicts_irc_mapping", body
