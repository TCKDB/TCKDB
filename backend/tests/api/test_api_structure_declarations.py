"""Actual-protocol declarations and structure determinations, end to end through the upload API.

Held here, each over HTTP so the code and context reach the client:

* a calculation's declared recipe is stored as made and read back as declared; one deposited without a
  declaration reads ``absent`` and is never given a recipe;
* a conformer upload can state a determination that pins its own calculations by local key; the rows record the
  owner, the basin's observation, the evaluated geometry and each role, and nothing the upload did not say;
* a transition-state upload can do the same for its saddle point, on the calculation keys it now accepts;
* a determination that does not fit its owner, names a key nothing declared, or states two claims for one
  determination is refused with a code, and a refusal writes nothing.
"""

from __future__ import annotations

import copy

from sqlalchemy import func, select

from app.db.models.calculation import Calculation
from app.db.models.common import StructureSourceRole
from app.db.models.structure_determination import StructureDetermination, StructureDeterminationSource
from tests.api.test_api_ts_contract_621 import _standalone, _standalone_ok, _standalone_refused

CONFORMERS = "/api/v1/uploads/conformers"
WTR = {"name": "ARC", "version": "1.1.0"}
PROTOCOL = {
    "version": 1,
    "source": {"origin": "producer_declared", "producer": "ARC", "producer_version": "1.1.0"},
    "electronic_state": {"state": "known", "root": 0},
    "relativistic_treatment": {"state": "known", "value": "none"},
    "effective_core_potential": {"state": "not_applicable"},
    "solvation": {"state": "known", "kind": "gas_phase"},
    "numerical_approximations": [],
    "included_corrections": ["dispersion"],
}


def _determination(**changes) -> dict:
    declared = {
        "key": "basin-1",
        "target_kind": "conformer_basin",
        "quantity": "electronic_energy",
        "evaluated_geometry": {"calculation_key": "opt"},
        "sources": [
            {"role": "geometry_optimization", "calculation_key": "opt"},
            {"role": "energy", "calculation_key": "sp"},
            {"role": "curvature", "calculation_key": "freq"},
        ],
        "workflow_tool_release": WTR,
    }
    declared.update(changes)
    return declared


def _conformer(**changes) -> dict:
    payload: dict = {
        "species_entry": {"smiles": "O", "charge": 0, "multiplicity": 1},
        "geometry": {
            "xyz_text": "3\nwater\nO 0.000000 0.000000 0.117300\nH 0.000000 0.757200 -0.469200\nH 0.000000 -0.757200 -0.469200"
        },
        "calculation": {
            "key": "opt",
            "type": "opt",
            "software_release": {"name": "Gaussian", "version": "16"},
            "level_of_theory": {"method": "wb97xd", "basis": "def2-tzvp"},
            "opt_result": {"converged": True, "n_steps": 5, "final_energy_hartree": -76.4},
        },
        "additional_calculations": [
            {
                "key": "freq",
                "type": "freq",
                "software_release": {"name": "Gaussian", "version": "16"},
                "level_of_theory": {"method": "wb97xd", "basis": "def2-tzvp"},
                "freq_result": {"n_imag": 0, "zpe_hartree": 0.02},
            },
            {
                "key": "sp",
                "type": "sp",
                "software_release": {"name": "Orca", "version": "6.0.1"},
                "level_of_theory": {"method": "DLPNO-CCSD(T)", "basis": "def2-tzvp"},
                "sp_result": {"electronic_energy_hartree": -76.43},
            },
        ],
        "workflow_tool_release": WTR,
    }
    payload.pop("workflow_tool_release")
    payload.update(changes)
    return payload


def _post(client, payload: dict) -> dict:
    response = client.post(CONFORMERS, json=payload)
    assert response.status_code in (200, 201), response.text[:1500]
    return response.json()


def _count(db_session, model) -> int:
    return db_session.scalar(select(func.count()).select_from(model))


def _calc_by_key(db_session) -> dict[str, Calculation]:
    rows = db_session.scalars(select(Calculation).order_by(Calculation.id.desc()).limit(3)).all()
    return {c.type.value: c for c in rows}


class TestCalculationActualProtocol:
    def test_a_declared_recipe_is_stored_as_made_and_read_back(self, client, db_session):
        payload = _conformer()
        payload["additional_calculations"][1]["actual_protocol_declaration"] = copy.deepcopy(PROTOCOL)
        _post(client, payload)
        calcs = _calc_by_key(db_session)

        assert calcs["sp"].actual_protocol_declaration is not None
        assert calcs["sp"].actual_protocol_declaration["electronic_state"] == {"state": "known", "root": 0}
        # An empty list is the claim "none", and is kept; an omitted statement stays omitted.
        assert calcs["sp"].actual_protocol_declaration["numerical_approximations"] == []
        assert "spin_treatment" not in calcs["sp"].actual_protocol_declaration
        # The other calculations of the same upload declared nothing and are given nothing.
        assert calcs["opt"].actual_protocol_declaration is None
        assert calcs["freq"].actual_protocol_declaration is None

        read = client.get(f"/api/v1/scientific/calculations/{calcs['sp'].public_ref}")
        assert read.status_code == 200, read.text[:800]
        provenance = read.json()["record"]["provenance"]
        assert provenance["actual_protocol_declaration_state"] == "valid"
        served = provenance["actual_protocol_declaration"]
        assert served["effective_core_potential"] == {"state": "not_applicable", "value": None}
        # What was never stated is served as null ("not stated"), not as a default recipe.
        assert served["spin_treatment"] is None and served["core_treatment"] is None
        assert served["numerical_approximations"] == []
        bare = client.get(f"/api/v1/scientific/calculations/{calcs['opt'].public_ref}").json()["record"]["provenance"]
        assert bare["actual_protocol_declaration_state"] == "absent"
        assert bare["actual_protocol_declaration"] is None

    def test_a_stored_declaration_this_server_cannot_read_is_withheld_not_repaired(self, client, db_session):
        _post(client, _conformer())
        sp = _calc_by_key(db_session)["sp"]
        sp.actual_protocol_declaration = {"version": 1, "unrecognised_fact": True}
        db_session.flush()
        provenance = client.get(f"/api/v1/scientific/calculations/{sp.public_ref}").json()["record"]["provenance"]
        assert provenance["actual_protocol_declaration_state"] == "unreadable"
        assert provenance["actual_protocol_declaration"] is None

    def test_an_unsupported_version_is_refused_with_its_code_and_stores_nothing(self, client, db_session):
        before = _count(db_session, Calculation)
        payload = _conformer()
        payload["calculation"]["actual_protocol_declaration"] = {**PROTOCOL, "version": 2}
        response = client.post(CONFORMERS, json=payload)
        assert response.status_code == 422, response.text[:800]
        assert response.json()["code"] == "structure_declaration_version_unsupported"
        assert _count(db_session, Calculation) == before


class TestConformerDetermination:
    def test_the_rows_record_what_was_stated_and_nothing_else(self, client, db_session):
        payload = _conformer(structure_determinations=[_determination()])
        _post(client, payload)

        determination = db_session.scalars(select(StructureDetermination)).one()
        calcs = _calc_by_key(db_session)
        assert determination.target_kind.value == "conformer_basin"
        assert determination.quantity is not None and determination.quantity.value == "electronic_energy"
        assert determination.energy_convention is None
        assert determination.actual_recipe is None
        assert determination.species_entry_id == calcs["opt"].species_entry_id
        assert determination.transition_state_entry_id is None
        assert determination.conformer_observation_id == calcs["opt"].conformer_observation_id
        assert determination.workflow_tool_release_id is not None
        assert determination.public_ref.startswith("sdet_")

        sources = db_session.scalars(
            select(StructureDeterminationSource).where(StructureDeterminationSource.determination_id == determination.id)
        ).all()
        by_role = {s.role: s for s in sources}
        assert set(by_role) == {
            StructureSourceRole.geometry_optimization,
            StructureSourceRole.energy,
            StructureSourceRole.curvature,
        }
        assert by_role[StructureSourceRole.energy].calculation_id == calcs["sp"].id
        assert by_role[StructureSourceRole.curvature].calculation_id == calcs["freq"].id
        # The evaluated geometry is the optimization's one output geometry; an SP and a frequency job each
        # describe their own one input geometry, which here is that same conformer geometry.
        assert determination.evaluated_geometry_id == by_role[StructureSourceRole.geometry_optimization].geometry_id
        assert by_role[StructureSourceRole.energy].geometry_id == determination.evaluated_geometry_id
        assert all(s.species_entry_id == determination.species_entry_id for s in sources)

    def test_one_calculation_can_play_two_roles_as_two_source_rows(self, client, db_session):
        declared = _determination(
            sources=[
                {"role": "energy", "calculation_key": "opt"},
                {"role": "geometry_optimization", "calculation_key": "opt"},
            ]
        )
        _post(client, _conformer(structure_determinations=[declared]))
        determination = db_session.scalars(select(StructureDetermination)).one()
        roles = db_session.scalars(
            select(StructureDeterminationSource.role).where(StructureDeterminationSource.determination_id == determination.id)
        ).all()
        assert sorted(r.value for r in roles) == ["energy", "geometry_optimization"]

    def test_an_energy_convention_and_recipe_are_stored_as_declared(self, client, db_session):
        declared = _determination(
            quantity="zero_kelvin_energy",
            energy_convention={"zero_point_treatment": "scaled_harmonic", "included_corrections": ["zero_point_energy"]},
            actual_recipe=copy.deepcopy(PROTOCOL),
        )
        _post(client, _conformer(structure_determinations=[declared]))
        determination = db_session.scalars(select(StructureDetermination)).one()
        assert determination.quantity is not None and determination.quantity.value == "zero_kelvin_energy"
        assert determination.energy_convention == {
            "zero_point_treatment": "scaled_harmonic",
            "included_corrections": ["zero_point_energy"],
        }
        assert determination.actual_recipe is not None and determination.actual_recipe["version"] == 1

    def test_an_upload_without_a_determination_makes_none(self, client, db_session):
        _post(client, _conformer())
        assert _count(db_session, StructureDetermination) == 0

    def test_a_redeposit_over_new_calculations_is_new_evidence_not_a_merge(self, client, db_session):
        payload = _conformer(structure_determinations=[_determination()])
        _post(client, payload)
        _post(client, payload)
        refs = db_session.scalars(select(StructureDetermination.public_ref)).all()
        assert len(refs) == 2 and len(set(refs)) == 2

    def test_a_pin_naming_an_undeclared_key_is_refused_before_anything_is_written(self, client, db_session):
        declared = _determination(sources=[{"role": "energy", "calculation_key": "nope"}])
        response = client.post(CONFORMERS, json=_conformer(structure_determinations=[declared]))
        assert response.status_code == 422, response.text[:800]
        body = response.json()
        assert body["code"] == "calculation_key_undeclared"
        assert body["context"]["key"] == "nope"
        assert body["context"]["declared_keys"] == ["freq", "opt", "sp"]
        assert _count(db_session, StructureDetermination) == 0

    def test_a_saddle_point_is_refused_on_a_conformer_upload(self, client, db_session):
        declared = _determination(target_kind="saddle_point")
        response = client.post(CONFORMERS, json=_conformer(structure_determinations=[declared]))
        assert response.status_code == 422, response.text[:800]
        body = response.json()
        assert body["code"] == "structure_determination_mismatch"
        assert body["context"]["reason"] == "target"
        # No determination row exists for the refused claim (the request's own transaction is what removes
        # the calculations it had already written; the test harness shares one session across the request).
        assert _count(db_session, StructureDetermination) == 0

    def test_a_pinned_calculation_with_no_single_geometry_on_the_named_side_is_refused(self, client, db_session):
        # The primary opt has one output geometry but no input geometry on this route.
        declared = _determination(evaluated_geometry={"calculation_key": "opt", "side": "input"})
        response = client.post(CONFORMERS, json=_conformer(structure_determinations=[declared]))
        assert response.status_code == 422, response.text[:800]
        body = response.json()
        assert body["code"] == "structure_determination_mismatch"
        assert body["context"]["reason"] == "geometry"

    def test_a_pin_by_ref_naming_another_subjects_calculation_is_refused(self, client, db_session):
        _post(client, _conformer(species_entry={"smiles": "[H][H]", "charge": 0, "multiplicity": 1},
                                 geometry={"xyz_text": "2\nh2\nH 0.0 0.0 0.0\nH 0.0 0.0 0.74"}))
        foreign = _calc_by_key(db_session)["opt"].public_ref
        declared = _determination(sources=[{"role": "energy", "calculation_ref": foreign}])
        response = client.post(CONFORMERS, json=_conformer(structure_determinations=[declared]))
        assert response.status_code == 422, response.text[:800]
        body = response.json()
        assert body["code"] == "structure_determination_mismatch"
        assert body["context"]["reason"] == "owner"

    def test_a_pin_by_ref_naming_no_calculation_is_a_404_that_names_the_ref(self, client, db_session):
        declared = _determination(sources=[{"role": "energy", "calculation_ref": "calc_" + "x" * 26}])
        response = client.post(CONFORMERS, json=_conformer(structure_determinations=[declared]))
        assert response.status_code == 404, response.text[:800]
        assert response.json()["code"] == "unknown_calculation_ref"


class TestTransitionStateDetermination:
    def _keyed(self, **changes) -> dict:
        payload = _standalone(**changes)
        payload["primary_opt"]["key"] = "ts-opt"
        payload["additional_calculations"][0]["key"] = "ts-freq"
        return payload

    def _saddle(self, **changes) -> dict:
        declared = {
            "key": "saddle-1",
            "target_kind": "saddle_point",
            "evaluated_geometry": {"calculation_key": "ts-opt"},
            "sources": [
                {"role": "geometry_optimization", "calculation_key": "ts-opt"},
                {"role": "curvature", "calculation_key": "ts-freq"},
            ],
            "workflow_tool_release": WTR,
        }
        declared.update(changes)
        return declared

    def test_a_saddle_determination_is_owned_by_the_transition_state_entry(self, client, db_session):
        result = _standalone_ok(client, self._keyed(structure_determinations=[self._saddle()]))
        determination = db_session.scalars(select(StructureDetermination)).one()
        assert determination.transition_state_entry_id == result["id"]
        assert determination.species_entry_id is None
        assert determination.conformer_observation_id is None
        assert determination.quantity is None  # evidence only: it supplies no energy
        roles = db_session.scalars(
            select(StructureDeterminationSource.role).where(StructureDeterminationSource.determination_id == determination.id)
        ).all()
        assert sorted(r.value for r in roles) == ["curvature", "geometry_optimization"]

    def test_calculation_keys_must_be_unique_on_this_route(self, client):
        payload = self._keyed()
        payload["additional_calculations"][0]["key"] = "ts-opt"
        _standalone_refused(client, payload)

    def test_a_conformer_basin_is_refused_on_a_transition_state_upload(self, client, db_session):
        payload = self._keyed(structure_determinations=[self._saddle(target_kind="conformer_basin")])
        body = _standalone_refused(client, payload)
        assert body["code"] == "structure_determination_mismatch"
        assert body["context"]["reason"] == "target"
        assert _count(db_session, StructureDetermination) == 0

    def test_an_upload_that_names_no_key_still_works_unchanged(self, client):
        _standalone_ok(client, _standalone())
