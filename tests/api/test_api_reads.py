"""Tests for the read API endpoints.

Each test class uploads data first, then verifies reads work correctly.
"""

from __future__ import annotations

from app.db.models.author import Author
from app.db.models.calculation import CalculationConstraint
from app.db.models.common import ConformerSelectionKind, ConstraintKind
from app.db.models.energy_correction import (
    EnergyCorrectionScheme,
    FrequencyScaleFactor,
)
from sqlalchemy import select

from app.db.models.literature import Literature
from app.db.models.literature_author import LiteratureAuthor
from app.db.models.species import ConformerGroup, ConformerObservation
from app.db.models.transport import Transport


# ---------------------------------------------------------------------------
# Test payloads
# ---------------------------------------------------------------------------


def _hydrogen_conformer_payload(label: str = "conf-a") -> dict:
    return {
        "species_entry": {
            "smiles": "[H]",
            "charge": 0,
            "multiplicity": 2,
        },
        "geometry": {
            "xyz_text": "1\nH atom\nH 0.0 0.0 0.0",
        },
        "calculation": {
            "type": "sp",
            "software_release": {"name": "Gaussian", "version": "16"},
            "level_of_theory": {"method": "B3LYP", "basis": "6-31G(d)"},
        },
        "label": label,
    }


def _reaction_payload() -> dict:
    return {
        "reversible": True,
        "reactants": [
            {"species_entry": {"smiles": "[H]", "charge": 0, "multiplicity": 2}},
        ],
        "products": [
            {"species_entry": {"smiles": "[H]", "charge": 0, "multiplicity": 2}},
        ],
    }


def _thermo_payload() -> dict:
    return {
        "species_entry": {"smiles": "[H]", "charge": 0, "multiplicity": 2},
        "scientific_origin": "computed",
        "h298_kj_mol": 217.998,
    }


_XYZ_H2 = "2\nH2\nH 0.0 0.0 0.0\nH 0.0 0.0 0.74"


def _ts_upload_payload() -> dict:
    return {
        "reaction": {
            "reversible": True,
            "reactants": [
                {"species_entry": {"smiles": "[H][H]", "charge": 0, "multiplicity": 1}},
            ],
            "products": [
                {"species_entry": {"smiles": "[H][H]", "charge": 0, "multiplicity": 1}},
            ],
        },
        "charge": 0,
        "multiplicity": 1,
        "geometry": {"xyz_text": _XYZ_H2},
        "primary_opt": {
            "type": "opt",
            "software_release": {"name": "Gaussian", "version": "16"},
            "level_of_theory": {"method": "B3LYP", "basis": "6-31G(d)"},
            "opt_result": {
                "converged": True,
                "n_steps": 10,
                "final_energy_hartree": -1.17,
            },
        },
        "additional_calculations": [
            {
                "type": "freq",
                "software_release": {"name": "Gaussian", "version": "16"},
                "level_of_theory": {"method": "B3LYP", "basis": "6-31G(d)"},
                "freq_result": {
                    "n_imag": 1,
                    "imag_freq_cm1": -1500.0,
                    "zpe_hartree": 0.01,
                },
            },
            {
                "type": "sp",
                "software_release": {"name": "Gaussian", "version": "16"},
                "level_of_theory": {"method": "CCSD(T)", "basis": "cc-pVTZ"},
                "sp_result": {"electronic_energy_hartree": -1.23},
            },
        ],
    }


def _kinetics_payload() -> dict:
    return {
        "reaction": {
            "reversible": True,
            "reactants": [
                {"species_entry": {"smiles": "[H][H]", "charge": 0, "multiplicity": 1}},
                {"species_entry": {"smiles": "[OH]", "charge": 0, "multiplicity": 2}},
            ],
            "products": [
                {"species_entry": {"smiles": "O", "charge": 0, "multiplicity": 1}},
                {"species_entry": {"smiles": "[H]", "charge": 0, "multiplicity": 2}},
            ],
        },
        "scientific_origin": "experimental",
        "a": 2.16e8,
        "a_units": "cm3_mol_s",
        "n": 1.51,
        "reported_ea": 14.35,
        "reported_ea_units": "kj_mol",
    }


def _hydrogen_conformer_with_transport_payload(
    label: str = "conf-transport",
    *,
    scientific_origin: str = "computed",
    software_name: str = "Gaussian",
    software_version: str = "16",
    workflow_tool_name: str = "ARC",
    workflow_tool_version: str = "1.1.0",
    literature_title: str = "Transport Benchmark Paper",
    literature_year: int = 2024,
    sigma_angstrom: float = 2.05,
    epsilon_over_k_k: float = 145.0,
    dipole_debye: float = 0.0,
    polarizability_angstrom3: float = 0.67,
    rotational_relaxation: float = 1.0,
) -> dict:
    payload = _hydrogen_conformer_payload(label)
    payload["transport"] = {
        "scientific_origin": scientific_origin,
        "software_release": {"name": software_name, "version": software_version},
        "workflow_tool_release": {
            "name": workflow_tool_name,
            "version": workflow_tool_version,
        },
        "literature": {
            "kind": "article",
            "title": literature_title,
            "year": literature_year,
        },
        "sigma_angstrom": sigma_angstrom,
        "epsilon_over_k_k": epsilon_over_k_k,
        "dipole_debye": dipole_debye,
        "polarizability_angstrom3": polarizability_angstrom3,
        "rotational_relaxation": rotational_relaxation,
        "note": "transport test",
    }
    return payload


# ---------------------------------------------------------------------------
# Species reads
# ---------------------------------------------------------------------------


class TestSpeciesReads:
    def test_list_species_empty(self, client):
        resp = client.get("/api/v1/species")
        assert resp.status_code == 200
        data = resp.json()
        assert data["items"] == []
        assert data["total"] == 0

    def test_list_species_after_upload(self, client):
        client.post("/api/v1/uploads/conformers", json=_hydrogen_conformer_payload())
        resp = client.get("/api/v1/species")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] >= 1
        assert data["items"][0]["smiles"] == "[H]"

    def test_get_species_by_id(self, client):
        client.post("/api/v1/uploads/conformers", json=_hydrogen_conformer_payload())
        species_list = client.get("/api/v1/species").json()
        species_id = species_list["items"][0]["id"]

        resp = client.get(f"/api/v1/species/{species_id}")
        assert resp.status_code == 200
        assert resp.json()["id"] == species_id

    def test_get_species_not_found(self, client):
        resp = client.get("/api/v1/species/999999")
        assert resp.status_code == 404

    def test_get_species_entry(self, client):
        upload = client.post(
            "/api/v1/uploads/conformers", json=_hydrogen_conformer_payload()
        ).json()
        entry_id = upload["species_entry_id"]

        resp = client.get(f"/api/v1/species-entries/{entry_id}")
        assert resp.status_code == 200
        assert resp.json()["id"] == entry_id

    def test_list_conformers_for_entry(self, client):
        upload = client.post(
            "/api/v1/uploads/conformers", json=_hydrogen_conformer_payload()
        ).json()
        entry_id = upload["species_entry_id"]

        resp = client.get(f"/api/v1/species-entries/{entry_id}/conformers")
        assert resp.status_code == 200
        assert len(resp.json()) >= 1

    def test_list_thermo_for_entry(self, client):
        upload = client.post(
            "/api/v1/uploads/thermo", json=_thermo_payload()
        ).json()
        entry_id = upload["species_entry_id"]

        resp = client.get(f"/api/v1/species-entries/{entry_id}/thermo")
        assert resp.status_code == 200
        assert len(resp.json()) >= 1

    def test_species_entry_transport_subresource(self, client):
        upload = client.post(
            "/api/v1/uploads/conformers",
            json=_hydrogen_conformer_with_transport_payload(),
        ).json()
        entry_id = upload["species_entry_id"]

        resp = client.get(f"/api/v1/species-entries/{entry_id}/transport")
        assert resp.status_code == 200
        items = resp.json()
        assert len(items) == 1
        item = items[0]
        assert item["scientific_origin"] == "computed"
        assert item["sigma_angstrom"] == 2.05
        assert item["epsilon_over_k_k"] == 145.0
        assert item["source_calculations"] == []

    def test_species_entry_not_found(self, client):
        resp = client.get("/api/v1/species-entries/999999")
        assert resp.status_code == 404

    def test_filter_by_smiles(self, client):
        client.post("/api/v1/uploads/conformers", json=_hydrogen_conformer_payload())
        resp = client.get("/api/v1/species", params={"smiles": "[H]"})
        assert resp.status_code == 200
        assert resp.json()["total"] >= 1

    def test_filter_by_smiles_no_match(self, client):
        client.post("/api/v1/uploads/conformers", json=_hydrogen_conformer_payload())
        resp = client.get("/api/v1/species", params={"smiles": "[He]"})
        assert resp.status_code == 200
        assert resp.json()["total"] == 0

    def test_filter_by_charge_and_multiplicity(self, client):
        client.post("/api/v1/uploads/conformers", json=_hydrogen_conformer_payload())
        resp = client.get(
            "/api/v1/species", params={"charge": 0, "multiplicity": 2}
        )
        assert resp.json()["total"] >= 1


# ---------------------------------------------------------------------------
# Reaction reads
# ---------------------------------------------------------------------------


class TestReactionReads:
    def test_list_reactions_empty(self, client):
        resp = client.get("/api/v1/reactions")
        assert resp.status_code == 200
        assert resp.json()["total"] == 0

    def test_list_reactions_after_upload(self, client):
        client.post("/api/v1/uploads/reactions", json=_reaction_payload())
        resp = client.get("/api/v1/reactions")
        assert resp.status_code == 200
        assert resp.json()["total"] >= 1

    def test_get_reaction_entry(self, client):
        upload = client.post(
            "/api/v1/uploads/reactions", json=_reaction_payload()
        ).json()
        entry_id = upload["id"]

        resp = client.get(f"/api/v1/reaction-entries/{entry_id}")
        assert resp.status_code == 200
        assert resp.json()["id"] == entry_id

    def test_reaction_entry_not_found(self, client):
        resp = client.get("/api/v1/reaction-entries/999999")
        assert resp.status_code == 404

    def test_filter_by_reversible(self, client):
        client.post("/api/v1/uploads/reactions", json=_reaction_payload())
        resp = client.get("/api/v1/reactions", params={"reversible": True})
        assert resp.json()["total"] >= 1

    def test_filter_by_reversible_no_match(self, client):
        client.post("/api/v1/uploads/reactions", json=_reaction_payload())
        resp = client.get("/api/v1/reactions", params={"reversible": False})
        assert resp.json()["total"] == 0


# ---------------------------------------------------------------------------
# Kinetics reads
# ---------------------------------------------------------------------------


class TestKineticsReads:
    def test_list_kinetics_empty(self, client):
        resp = client.get("/api/v1/kinetics")
        assert resp.status_code == 200
        assert resp.json()["total"] == 0

    def test_kinetics_not_found(self, client):
        resp = client.get("/api/v1/kinetics/999999")
        assert resp.status_code == 404

    def test_list_kinetics_after_upload(self, client):
        upload = client.post(
            "/api/v1/uploads/kinetics", json=_kinetics_payload()
        )
        assert upload.status_code == 201
        resp = client.get("/api/v1/kinetics")
        assert resp.json()["total"] >= 1

    def test_filter_by_scientific_origin(self, client):
        client.post("/api/v1/uploads/kinetics", json=_kinetics_payload())
        resp = client.get(
            "/api/v1/kinetics", params={"scientific_origin": "experimental"}
        )
        assert resp.json()["total"] >= 1

    def test_filter_by_scientific_origin_no_match(self, client):
        client.post("/api/v1/uploads/kinetics", json=_kinetics_payload())
        resp = client.get(
            "/api/v1/kinetics", params={"scientific_origin": "computed"}
        )
        assert resp.json()["total"] == 0


# ---------------------------------------------------------------------------
# Thermo reads
# ---------------------------------------------------------------------------


class TestThermoReads:
    def test_list_thermo_empty(self, client):
        resp = client.get("/api/v1/thermo")
        assert resp.status_code == 200
        assert resp.json()["total"] == 0

    def test_get_thermo_after_upload(self, client):
        upload = client.post(
            "/api/v1/uploads/thermo", json=_thermo_payload()
        ).json()
        thermo_id = upload["id"]

        resp = client.get(f"/api/v1/thermo/{thermo_id}")
        assert resp.status_code == 200
        assert resp.json()["id"] == thermo_id

    def test_thermo_not_found(self, client):
        resp = client.get("/api/v1/thermo/999999")
        assert resp.status_code == 404

    def test_filter_by_scientific_origin(self, client):
        client.post("/api/v1/uploads/thermo", json=_thermo_payload())
        resp = client.get(
            "/api/v1/thermo", params={"scientific_origin": "computed"}
        )
        assert resp.json()["total"] >= 1


# ---------------------------------------------------------------------------
# Transition state reads
# ---------------------------------------------------------------------------


class TestTransitionStateReads:
    def test_list_empty(self, client):
        resp = client.get("/api/v1/transition-states")
        assert resp.status_code == 200
        assert resp.json()["total"] == 0

    def test_list_after_upload(self, client):
        client.post(
            "/api/v1/uploads/transition-states", json=_ts_upload_payload()
        )
        resp = client.get("/api/v1/transition-states")
        assert resp.json()["total"] >= 1

    def test_get_by_id(self, client):
        upload = client.post(
            "/api/v1/uploads/transition-states", json=_ts_upload_payload()
        ).json()
        ts_id = upload["transition_state_id"]
        resp = client.get(f"/api/v1/transition-states/{ts_id}")
        assert resp.status_code == 200
        assert resp.json()["id"] == ts_id

    def test_not_found(self, client):
        resp = client.get("/api/v1/transition-states/999999")
        assert resp.status_code == 404

    def test_filter_by_reaction_entry_id(self, client):
        upload = client.post(
            "/api/v1/uploads/transition-states", json=_ts_upload_payload()
        ).json()
        resp = client.get(
            "/api/v1/transition-states",
            params={"reaction_entry_id": upload["reaction_entry_id"]},
        )
        assert resp.json()["total"] >= 1


# ---------------------------------------------------------------------------
# Calculation reads
# ---------------------------------------------------------------------------


class TestCalculationReads:
    """Tests using TS upload which creates opt+freq+sp calculations with
    dependencies and geometry links."""

    def _upload_ts(self, client):
        return client.post(
            "/api/v1/uploads/transition-states", json=_ts_upload_payload()
        ).json()

    def test_list_empty(self, client):
        resp = client.get("/api/v1/calculations")
        assert resp.status_code == 200
        assert resp.json()["total"] == 0

    def test_list_after_upload(self, client):
        self._upload_ts(client)
        resp = client.get("/api/v1/calculations")
        assert resp.json()["total"] >= 3  # opt + freq + sp

    def test_get_by_id(self, client):
        self._upload_ts(client)
        calcs = client.get("/api/v1/calculations").json()["items"]
        calc_id = calcs[0]["id"]
        resp = client.get(f"/api/v1/calculations/{calc_id}")
        assert resp.status_code == 200
        assert resp.json()["id"] == calc_id
        assert "created_by" not in resp.json()  # policy: no uploader identity

    def test_not_found(self, client):
        resp = client.get("/api/v1/calculations/999999")
        assert resp.status_code == 404

    def test_filter_by_type(self, client):
        self._upload_ts(client)
        resp = client.get("/api/v1/calculations", params={"type": "opt"})
        assert resp.json()["total"] >= 1
        assert all(c["type"] == "opt" for c in resp.json()["items"])

    def test_filter_by_type_no_match(self, client):
        self._upload_ts(client)
        resp = client.get("/api/v1/calculations", params={"type": "scan"})
        assert resp.json()["total"] == 0

    def test_joined_filter_by_method(self, client):
        self._upload_ts(client)
        resp = client.get("/api/v1/calculations", params={"method": "B3LYP"})
        # opt + freq use B3LYP; sp uses CCSD(T)
        assert resp.json()["total"] >= 2

    def test_joined_filter_by_method_no_match(self, client):
        self._upload_ts(client)
        resp = client.get("/api/v1/calculations", params={"method": "MP2"})
        assert resp.json()["total"] == 0

    # -- SP / opt / freq results (1:1 → 404 when absent) --

    def test_opt_result_present(self, client):
        self._upload_ts(client)
        opt_calc = next(
            c for c in client.get("/api/v1/calculations").json()["items"]
            if c["type"] == "opt"
        )
        resp = client.get(f"/api/v1/calculations/{opt_calc['id']}/opt-result")
        assert resp.status_code == 200
        assert resp.json()["converged"] is True

    def test_freq_result_present(self, client):
        self._upload_ts(client)
        freq_calc = next(
            c for c in client.get("/api/v1/calculations").json()["items"]
            if c["type"] == "freq"
        )
        resp = client.get(f"/api/v1/calculations/{freq_calc['id']}/freq-result")
        assert resp.status_code == 200
        assert resp.json()["n_imag"] == 1

    def test_sp_result_present(self, client):
        self._upload_ts(client)
        sp_calc = next(
            c for c in client.get("/api/v1/calculations").json()["items"]
            if c["type"] == "sp"
        )
        resp = client.get(f"/api/v1/calculations/{sp_calc['id']}/sp-result")
        assert resp.status_code == 200
        assert resp.json()["electronic_energy_hartree"] is not None

    def test_result_missing_returns_404(self, client):
        self._upload_ts(client)
        # opt calc has no freq result
        opt_calc = next(
            c for c in client.get("/api/v1/calculations").json()["items"]
            if c["type"] == "opt"
        )
        resp = client.get(f"/api/v1/calculations/{opt_calc['id']}/freq-result")
        assert resp.status_code == 404

    def test_result_parent_missing_returns_404(self, client):
        resp = client.get("/api/v1/calculations/999999/sp-result")
        assert resp.status_code == 404

    # -- Input/output geometries (1:N → [] when empty) --

    def test_output_geometries_with_embedded_payload(self, client):
        self._upload_ts(client)
        opt_calc = next(
            c for c in client.get("/api/v1/calculations").json()["items"]
            if c["type"] == "opt"
        )
        resp = client.get(
            f"/api/v1/calculations/{opt_calc['id']}/output-geometries"
        )
        assert resp.status_code == 200
        geoms = resp.json()
        assert len(geoms) >= 1
        # Verify embedded geometry payload
        assert "geometry" in geoms[0]
        assert "natoms" in geoms[0]["geometry"]
        assert geoms[0]["output_order"] >= 1

    def test_input_geometries_ordered(self, client):
        self._upload_ts(client)
        opt_calc = next(
            c for c in client.get("/api/v1/calculations").json()["items"]
            if c["type"] == "opt"
        )
        resp = client.get(
            f"/api/v1/calculations/{opt_calc['id']}/input-geometries"
        )
        assert resp.status_code == 200
        geoms = resp.json()
        orders = [g["input_order"] for g in geoms]
        assert orders == sorted(orders)

    # -- Dependencies (1:N → [] when empty) --

    def test_dependencies_present(self, client):
        self._upload_ts(client)
        # freq and sp calculations depend on the opt calculation
        freq_calc = next(
            c for c in client.get("/api/v1/calculations").json()["items"]
            if c["type"] == "freq"
        )
        resp = client.get(
            f"/api/v1/calculations/{freq_calc['id']}/dependencies"
        )
        assert resp.status_code == 200, resp.json()
        deps = resp.json()
        assert len(deps) >= 1
        # Verify direction field is present
        assert all("direction" in d for d in deps)

    def test_dependencies_empty_when_none(self, client):
        # Upload a simple conformer (sp calc with no deps)
        client.post(
            "/api/v1/uploads/conformers", json=_hydrogen_conformer_payload()
        )
        calcs = client.get("/api/v1/calculations").json()["items"]
        sp_calc = next(c for c in calcs if c["type"] == "sp")
        resp = client.get(
            f"/api/v1/calculations/{sp_calc['id']}/dependencies"
        )
        assert resp.status_code == 200
        assert resp.json() == []

    # -- Constraints (1:N → [] when empty) --

    def test_constraints_empty_when_none(self, client):
        self._upload_ts(client)
        calcs = client.get("/api/v1/calculations").json()["items"]
        resp = client.get(
            f"/api/v1/calculations/{calcs[0]['id']}/constraints"
        )
        assert resp.status_code == 200
        assert resp.json() == []

    def test_constraints_with_data(self, client, db_session):
        self._upload_ts(client)
        calc_id = client.get("/api/v1/calculations").json()["items"][0]["id"]

        # Insert a constraint via raw ORM (no upload creates these)
        db_session.add(
            CalculationConstraint(
                calculation_id=calc_id,
                constraint_index=1,
                constraint_kind=ConstraintKind.bond,
                atom1_index=1,
                atom2_index=2,
                target_value=0.74,
            )
        )
        db_session.flush()

        resp = client.get(f"/api/v1/calculations/{calc_id}/constraints")
        assert resp.status_code == 200
        constraints = resp.json()
        assert len(constraints) == 1
        assert constraints[0]["constraint_index"] == 1
        assert constraints[0]["constraint_kind"] == "bond"


# ---------------------------------------------------------------------------
# Geometry reads
# ---------------------------------------------------------------------------


class TestGeometryReads:
    def test_list_empty(self, client):
        resp = client.get("/api/v1/geometries")
        assert resp.status_code == 200
        assert resp.json()["total"] == 0

    def test_list_after_upload(self, client):
        client.post("/api/v1/uploads/conformers", json=_hydrogen_conformer_payload())
        resp = client.get("/api/v1/geometries")
        assert resp.json()["total"] >= 1

    def test_get_by_id(self, client):
        client.post("/api/v1/uploads/conformers", json=_hydrogen_conformer_payload())
        geom = client.get("/api/v1/geometries").json()["items"][0]
        resp = client.get(f"/api/v1/geometries/{geom['id']}")
        assert resp.status_code == 200
        assert resp.json()["natoms"] == 1

    def test_not_found(self, client):
        resp = client.get("/api/v1/geometries/999999")
        assert resp.status_code == 404

    def test_filter_by_natoms(self, client):
        client.post("/api/v1/uploads/conformers", json=_hydrogen_conformer_payload())
        resp = client.get("/api/v1/geometries", params={"natoms": 1})
        assert resp.json()["total"] >= 1

    def test_filter_by_natoms_no_match(self, client):
        client.post("/api/v1/uploads/conformers", json=_hydrogen_conformer_payload())
        resp = client.get("/api/v1/geometries", params={"natoms": 100})
        assert resp.json()["total"] == 0


# ---------------------------------------------------------------------------
# Level of theory reads
# ---------------------------------------------------------------------------


class TestLevelOfTheoryReads:
    def test_list_empty(self, client):
        resp = client.get("/api/v1/levels-of-theory")
        assert resp.status_code == 200
        assert resp.json()["total"] == 0

    def test_list_after_upload(self, client):
        client.post("/api/v1/uploads/conformers", json=_hydrogen_conformer_payload())
        resp = client.get("/api/v1/levels-of-theory")
        assert resp.json()["total"] >= 1

    def test_get_by_id(self, client):
        client.post("/api/v1/uploads/conformers", json=_hydrogen_conformer_payload())
        lot = client.get("/api/v1/levels-of-theory").json()["items"][0]
        resp = client.get(f"/api/v1/levels-of-theory/{lot['id']}")
        assert resp.status_code == 200
        assert resp.json()["method"] == "B3LYP"

    def test_not_found(self, client):
        resp = client.get("/api/v1/levels-of-theory/999999")
        assert resp.status_code == 404

    def test_filter_by_method(self, client):
        client.post("/api/v1/uploads/conformers", json=_hydrogen_conformer_payload())
        resp = client.get("/api/v1/levels-of-theory", params={"method": "B3LYP"})
        assert resp.json()["total"] >= 1

    def test_filter_by_method_no_match(self, client):
        client.post("/api/v1/uploads/conformers", json=_hydrogen_conformer_payload())
        resp = client.get("/api/v1/levels-of-theory", params={"method": "MP2"})
        assert resp.json()["total"] == 0


# ---------------------------------------------------------------------------
# Software reads
# ---------------------------------------------------------------------------


class TestSoftwareReads:
    def test_list_empty(self, client):
        resp = client.get("/api/v1/software")
        assert resp.status_code == 200
        assert resp.json()["total"] == 0

    def test_list_after_upload(self, client):
        client.post("/api/v1/uploads/conformers", json=_hydrogen_conformer_payload())
        resp = client.get("/api/v1/software")
        assert resp.json()["total"] >= 1

    def test_get_by_id(self, client):
        client.post("/api/v1/uploads/conformers", json=_hydrogen_conformer_payload())
        sw = client.get("/api/v1/software").json()["items"][0]
        resp = client.get(f"/api/v1/software/{sw['id']}")
        assert resp.status_code == 200

    def test_not_found(self, client):
        resp = client.get("/api/v1/software/999999")
        assert resp.status_code == 404

    def test_filter_by_name(self, client):
        client.post("/api/v1/uploads/conformers", json=_hydrogen_conformer_payload())
        resp = client.get("/api/v1/software", params={"name": "Gaussian"})
        assert resp.json()["total"] >= 1


class TestSoftwareReleaseReads:
    def test_list_empty(self, client):
        resp = client.get("/api/v1/software-releases")
        assert resp.status_code == 200
        assert resp.json()["total"] == 0

    def test_list_after_upload(self, client):
        client.post("/api/v1/uploads/conformers", json=_hydrogen_conformer_payload())
        resp = client.get("/api/v1/software-releases")
        assert resp.json()["total"] >= 1

    def test_get_by_id(self, client):
        client.post("/api/v1/uploads/conformers", json=_hydrogen_conformer_payload())
        release = client.get("/api/v1/software-releases").json()["items"][0]
        resp = client.get(f"/api/v1/software-releases/{release['id']}")
        assert resp.status_code == 200

    def test_not_found(self, client):
        resp = client.get("/api/v1/software-releases/999999")
        assert resp.status_code == 404

    def test_filter_by_version(self, client):
        client.post("/api/v1/uploads/conformers", json=_hydrogen_conformer_payload())
        resp = client.get("/api/v1/software-releases", params={"version": "16"})
        assert resp.json()["total"] >= 1


# ---------------------------------------------------------------------------
# Literature reads (seeded via raw ORM insert — uploads don't expose authors)
# ---------------------------------------------------------------------------


class TestLiteratureReads:
    def test_list_empty(self, client):
        resp = client.get("/api/v1/literature")
        assert resp.status_code == 200
        assert resp.json()["total"] == 0

    def test_list_and_get_with_authors(self, client, db_session):
        author = Author(given_name="Jane", family_name="Doe", full_name="Jane Doe")
        db_session.add(author)
        db_session.flush()

        lit = Literature(
            kind="article",
            title="Test Paper",
            year=2024,
            journal="J. Test",
            doi="10.1234/test.2024",
        )
        db_session.add(lit)
        db_session.flush()
        db_session.add(
            LiteratureAuthor(
                literature_id=lit.id,
                author_id=author.id,
                author_order=1,
            )
        )
        db_session.flush()
        lit_id = lit.id

        resp = client.get("/api/v1/literature")
        assert resp.json()["total"] >= 1

        resp = client.get(f"/api/v1/literature/{lit_id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["title"] == "Test Paper"
        assert len(data["authors"]) == 1
        assert data["authors"][0]["author_order"] == 1

    def test_not_found(self, client):
        resp = client.get("/api/v1/literature/999999")
        assert resp.status_code == 404

    def test_filter_by_doi(self, client, db_session):
        db_session.add(
            Literature(
                kind="article",
                title="DOI Paper",
                doi="10.5555/test.doi",
            )
        )
        db_session.flush()
        resp = client.get("/api/v1/literature", params={"doi": "10.5555/test.doi"})
        assert resp.json()["total"] >= 1

    def test_filter_by_doi_no_match(self, client, db_session):
        db_session.add(
            Literature(
                kind="article",
                title="DOI Paper",
                doi="10.5555/test.doi",
            )
        )
        db_session.flush()
        resp = client.get("/api/v1/literature", params={"doi": "10.9999/no.match"})
        assert resp.json()["total"] == 0


# ---------------------------------------------------------------------------
# Conformer reads
# ---------------------------------------------------------------------------


class TestConformerGroupReads:
    def test_list_empty(self, client):
        resp = client.get("/api/v1/conformer-groups")
        assert resp.status_code == 200
        assert resp.json()["total"] == 0

    def test_list_after_upload(self, client):
        client.post("/api/v1/uploads/conformers", json=_hydrogen_conformer_payload())
        resp = client.get("/api/v1/conformer-groups")
        assert resp.json()["total"] >= 1

    def test_get_by_id(self, client):
        client.post("/api/v1/uploads/conformers", json=_hydrogen_conformer_payload())
        group = client.get("/api/v1/conformer-groups").json()["items"][0]
        resp = client.get(f"/api/v1/conformer-groups/{group['id']}")
        assert resp.status_code == 200

    def test_not_found(self, client):
        resp = client.get("/api/v1/conformer-groups/999999")
        assert resp.status_code == 404


class TestSpeciesEntryConformerGroupsListing:
    """Tests for GET /api/v1/species-entries/{id}/conformer-groups (basin-first)."""

    def _seed_multi_group_entry(
        self, client, db_session
    ) -> tuple[int, list[int], int]:
        """Upload a conformer (creating a species entry + one group + one obs),
        then add two more groups with 2 and 1 observations directly via ORM.

        Returns (entry_id, [group_ids], total_observation_count).
        """
        upload = client.post(
            "/api/v1/uploads/conformers", json=_hydrogen_conformer_payload()
        ).json()
        entry_id = upload["species_entry_id"]
        first_group_id = upload["conformer_group_id"]

        group_b = ConformerGroup(
            species_entry_id=entry_id,
            label="basin-b",
            representative_fingerprint_json={"quantized_bins": [4]},
        )
        group_c = ConformerGroup(species_entry_id=entry_id, label="basin-c")
        db_session.add_all([group_b, group_c])
        db_session.flush()

        db_session.add_all(
            [
                ConformerObservation(
                    conformer_group_id=group_b.id,
                    scientific_origin="computed",
                ),
                ConformerObservation(
                    conformer_group_id=group_b.id,
                    scientific_origin="computed",
                ),
                ConformerObservation(
                    conformer_group_id=group_c.id,
                    scientific_origin="computed",
                ),
            ]
        )
        db_session.flush()

        return entry_id, [first_group_id, group_b.id, group_c.id], 4

    def test_returns_grouped_response(self, client, db_session):
        entry_id, group_ids, _ = self._seed_multi_group_entry(client, db_session)

        resp = client.get(f"/api/v1/species-entries/{entry_id}/conformer-groups")
        assert resp.status_code == 200
        body = resp.json()
        assert body["species_entry_id"] == entry_id
        assert {g["id"] for g in body["groups"]} == set(group_ids)
        assert all(g["species_entry_id"] == entry_id for g in body["groups"])

    def test_conformer_group_count_is_correct(self, client, db_session):
        entry_id, group_ids, _ = self._seed_multi_group_entry(client, db_session)

        body = client.get(
            f"/api/v1/species-entries/{entry_id}/conformer-groups"
        ).json()
        assert body["conformer_group_count"] == len(group_ids) == 3

    def test_conformer_observation_count_is_correct(self, client, db_session):
        entry_id, _, total_obs = self._seed_multi_group_entry(client, db_session)

        body = client.get(
            f"/api/v1/species-entries/{entry_id}/conformer-groups"
        ).json()
        assert body["conformer_observation_count"] == total_obs

    def test_each_group_has_observation_count(self, client, db_session):
        entry_id, group_ids, _ = self._seed_multi_group_entry(client, db_session)

        body = client.get(
            f"/api/v1/species-entries/{entry_id}/conformer-groups"
        ).json()
        counts_by_id = {g["id"]: g["observation_count"] for g in body["groups"]}
        # first group (from upload) has 1 observation, group_b has 2, group_c has 1
        assert counts_by_id[group_ids[0]] == 1
        assert counts_by_id[group_ids[1]] == 2
        assert counts_by_id[group_ids[2]] == 1
        assert sum(counts_by_id.values()) == body["conformer_observation_count"]

    def test_includes_representative_fields(self, client, db_session):
        entry_id, group_ids, _ = self._seed_multi_group_entry(client, db_session)

        body = client.get(
            f"/api/v1/species-entries/{entry_id}/conformer-groups"
        ).json()
        for group in body["groups"]:
            assert "representative_fingerprint_json" in group
            assert "representative_coords_json" in group
            assert "selections" in group

    def test_nonexistent_species_entry_returns_404(self, client):
        resp = client.get("/api/v1/species-entries/999999/conformer-groups")
        assert resp.status_code == 404


class TestConformerGroupDetail:
    """Tests for GET /api/v1/conformer-groups/{id} (basin drill-down)."""

    def test_existing_group_returns_nested_observations(self, client, db_session):
        upload = client.post(
            "/api/v1/uploads/conformers", json=_hydrogen_conformer_payload("obs-a")
        ).json()
        group_id = upload["conformer_group_id"]
        # Attach a second observation via ORM so we can assert nesting of >1.
        db_session.add(
            ConformerObservation(
                conformer_group_id=group_id, scientific_origin="computed"
            )
        )
        db_session.flush()

        resp = client.get(f"/api/v1/conformer-groups/{group_id}")
        assert resp.status_code == 200
        body = resp.json()
        assert body["id"] == group_id
        assert body["observation_count"] == 2
        assert len(body["observations"]) == 2

    def test_nested_observations_belong_to_requested_group(self, client, db_session):
        # Upload two conformers; they dedupe into one group for [H]. Add a second
        # group with a distinct observation to verify isolation between groups.
        upload = client.post(
            "/api/v1/uploads/conformers", json=_hydrogen_conformer_payload("iso")
        ).json()
        entry_id = upload["species_entry_id"]
        other_group = ConformerGroup(species_entry_id=entry_id, label="other-basin")
        db_session.add(other_group)
        db_session.flush()
        db_session.add(
            ConformerObservation(
                conformer_group_id=other_group.id, scientific_origin="computed"
            )
        )
        db_session.flush()

        body = client.get(f"/api/v1/conformer-groups/{other_group.id}").json()
        assert body["id"] == other_group.id
        assert len(body["observations"]) == 1
        assert all(
            obs["conformer_group_id"] == other_group.id
            for obs in body["observations"]
        )

    def test_representative_fields_present(self, client):
        upload = client.post(
            "/api/v1/uploads/conformers", json=_hydrogen_conformer_payload()
        ).json()
        group_id = upload["conformer_group_id"]

        body = client.get(f"/api/v1/conformer-groups/{group_id}").json()
        # Fields must appear in the schema even if their values are null for
        # single-atom species (no fingerprint computed).
        assert "representative_fingerprint_json" in body
        assert "representative_coords_json" in body
        assert "selections" in body
        assert "observations" in body

    def test_includes_group_selections(self, client, db_session):
        # Selection creation is now curator/admin-only; the default
        # test user is role=``user``, so elevate for this one POST.
        from app.db.models.app_user import AppUser
        from app.db.models.common import AppUserRole

        test_user = db_session.scalar(
            select(AppUser).where(AppUser.username == "testuser")
        )
        test_user.role = AppUserRole.curator
        db_session.flush()

        upload = client.post(
            "/api/v1/uploads/conformers", json=_hydrogen_conformer_payload()
        ).json()
        group_id = upload["conformer_group_id"]
        client.post(
            f"/api/v1/conformer-groups/{group_id}/selections",
            json={"selection_kind": ConformerSelectionKind.display_default.value},
        )

        body = client.get(f"/api/v1/conformer-groups/{group_id}").json()
        assert len(body["selections"]) == 1
        assert body["selections"][0]["selection_kind"] == "display_default"

    def test_nonexistent_group_returns_404(self, client):
        resp = client.get("/api/v1/conformer-groups/999999")
        assert resp.status_code == 404


class TestSpeciesEntryConformerSummary:
    """Tests for the compact conformer summary embedded in SpeciesEntryRead."""

    def test_summary_present_after_upload(self, client):
        upload = client.post(
            "/api/v1/uploads/conformers", json=_hydrogen_conformer_payload()
        ).json()
        entry_id = upload["species_entry_id"]

        body = client.get(f"/api/v1/species-entries/{entry_id}").json()
        summary = body.get("conformer_summary")
        assert summary is not None
        assert summary["conformer_group_count"] == 1
        assert summary["conformer_observation_count"] == 1

    def test_summary_counts_match_listing(self, client, db_session):
        upload = client.post(
            "/api/v1/uploads/conformers", json=_hydrogen_conformer_payload()
        ).json()
        entry_id = upload["species_entry_id"]

        extra = ConformerGroup(species_entry_id=entry_id, label="extra")
        db_session.add(extra)
        db_session.flush()
        db_session.add(
            ConformerObservation(
                conformer_group_id=extra.id, scientific_origin="computed"
            )
        )
        db_session.flush()

        entry_body = client.get(f"/api/v1/species-entries/{entry_id}").json()
        listing_body = client.get(
            f"/api/v1/species-entries/{entry_id}/conformer-groups"
        ).json()
        summary = entry_body["conformer_summary"]
        assert summary["conformer_group_count"] == listing_body["conformer_group_count"]
        assert (
            summary["conformer_observation_count"]
            == listing_body["conformer_observation_count"]
        )

    def test_top_level_read_does_not_inline_groups(self, client, db_session):
        upload = client.post(
            "/api/v1/uploads/conformers", json=_hydrogen_conformer_payload()
        ).json()
        entry_id = upload["species_entry_id"]
        extra = ConformerGroup(species_entry_id=entry_id, label="extra")
        db_session.add(extra)
        db_session.flush()

        body = client.get(f"/api/v1/species-entries/{entry_id}").json()
        # Summary is present, but the full groups/observations lists are not.
        assert "conformer_summary" in body
        assert "groups" not in body
        assert "observations" not in body


class TestConformerObservationReads:
    def test_list_empty(self, client):
        resp = client.get("/api/v1/conformer-observations")
        assert resp.status_code == 200
        assert resp.json()["total"] == 0

    def test_list_after_upload(self, client):
        client.post("/api/v1/uploads/conformers", json=_hydrogen_conformer_payload())
        resp = client.get("/api/v1/conformer-observations")
        assert resp.json()["total"] >= 1

    def test_get_by_id(self, client):
        upload = client.post(
            "/api/v1/uploads/conformers", json=_hydrogen_conformer_payload()
        ).json()
        resp = client.get(f"/api/v1/conformer-observations/{upload['id']}")
        assert resp.status_code == 200

    def test_not_found(self, client):
        resp = client.get("/api/v1/conformer-observations/999999")
        assert resp.status_code == 404

    def test_filter_by_conformer_group_id(self, client):
        upload = client.post(
            "/api/v1/uploads/conformers", json=_hydrogen_conformer_payload()
        ).json()
        resp = client.get(
            "/api/v1/conformer-observations",
            params={"conformer_group_id": upload["conformer_group_id"]},
        )
        assert resp.json()["total"] >= 1


# ---------------------------------------------------------------------------
# Energy correction reads (seeded via raw ORM insert — reference layer)
# ---------------------------------------------------------------------------


class TestEnergyCorrectionSchemeReads:
    def test_list_empty(self, client):
        resp = client.get("/api/v1/energy-correction-schemes")
        assert resp.status_code == 200
        assert resp.json()["total"] == 0

    def test_list_and_get(self, client, db_session):
        scheme = EnergyCorrectionScheme(
            kind="bac_petersson",
            name="Test BAC Scheme",
        )
        db_session.add(scheme)
        db_session.flush()
        scheme_id = scheme.id

        resp = client.get("/api/v1/energy-correction-schemes")
        assert resp.json()["total"] >= 1

        resp = client.get(f"/api/v1/energy-correction-schemes/{scheme_id}")
        assert resp.status_code == 200
        assert resp.json()["name"] == "Test BAC Scheme"

    def test_not_found(self, client):
        resp = client.get("/api/v1/energy-correction-schemes/999999")
        assert resp.status_code == 404


class TestFrequencyScaleFactorReads:
    def test_list_empty(self, client):
        resp = client.get("/api/v1/frequency-scale-factors")
        assert resp.status_code == 200
        assert resp.json()["total"] == 0

    def test_list_and_get(self, client, db_session):
        # Need an LOT row first (created by conformer upload)
        client.post("/api/v1/uploads/conformers", json=_hydrogen_conformer_payload())
        lot = client.get("/api/v1/levels-of-theory").json()["items"][0]

        fsf = FrequencyScaleFactor(
            level_of_theory_id=lot["id"],
            scale_kind="fundamental",
            value=0.967,
        )
        db_session.add(fsf)
        db_session.flush()
        fsf_id = fsf.id

        resp = client.get("/api/v1/frequency-scale-factors")
        assert resp.json()["total"] >= 1

        resp = client.get(f"/api/v1/frequency-scale-factors/{fsf_id}")
        assert resp.status_code == 200
        assert resp.json()["value"] == 0.967

    def test_not_found(self, client):
        resp = client.get("/api/v1/frequency-scale-factors/999999")
        assert resp.status_code == 404


class TestAppliedEnergyCorrectionReads:
    def test_list_empty(self, client):
        resp = client.get("/api/v1/applied-energy-corrections")
        assert resp.status_code == 200
        assert resp.json()["total"] == 0

    def test_not_found(self, client):
        resp = client.get("/api/v1/applied-energy-corrections/999999")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Statmech reads
# ---------------------------------------------------------------------------


class TestStatmechReads:
    def test_list_empty(self, client):
        resp = client.get("/api/v1/statmech")
        assert resp.status_code == 200
        assert resp.json()["total"] == 0

    def test_not_found(self, client):
        resp = client.get("/api/v1/statmech/999999")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Transport reads
# ---------------------------------------------------------------------------


class TestTransportReads:
    def test_list_empty(self, client):
        resp = client.get("/api/v1/transport")
        assert resp.status_code == 200
        assert resp.json()["total"] == 0

    def test_not_found(self, client):
        resp = client.get("/api/v1/transport/999999")
        assert resp.status_code == 404

    def test_transport_happy_path(self, client, db_session):
        client.post(
            "/api/v1/uploads/conformers",
            json=_hydrogen_conformer_with_transport_payload(),
        )
        resp = client.get("/api/v1/transport")
        assert resp.status_code == 200
        assert resp.json()["total"] == 1

        item = resp.json()["items"][0]
        # Scalar transport fields
        assert item["scientific_origin"] == "computed"
        assert item["sigma_angstrom"] == 2.05
        assert item["epsilon_over_k_k"] == 145.0
        assert item["dipole_debye"] == 0.0
        assert item["polarizability_angstrom3"] == 0.67
        assert item["rotational_relaxation"] == 1.0
        # Provenance IDs — verify against ORM row
        transport = db_session.scalar(
            select(Transport).where(Transport.id == item["id"])
        )
        assert transport is not None
        assert item["species_entry_id"] == transport.species_entry_id
        assert item["software_release_id"] == transport.software_release_id
        assert item["workflow_tool_release_id"] == transport.workflow_tool_release_id
        assert item["literature_id"] == transport.literature_id
        assert item["created_by"] == transport.created_by
        assert item["note"] == "transport test"
        assert item["created_at"] is not None
        assert item["source_calculations"] == []

    def test_get_by_id(self, client, db_session):
        upload = client.post(
            "/api/v1/uploads/conformers",
            json=_hydrogen_conformer_with_transport_payload(),
        ).json()
        transport = db_session.scalar(
            select(Transport).where(
                Transport.species_entry_id == upload["species_entry_id"]
            )
        )
        assert transport is not None

        resp = client.get(f"/api/v1/transport/{transport.id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == transport.id
        assert data["scientific_origin"] == "computed"
        assert data["sigma_angstrom"] == 2.05
        assert data["epsilon_over_k_k"] == 145.0
        assert data["software_release_id"] == transport.software_release_id
        assert data["literature_id"] == transport.literature_id
        assert data["source_calculations"] == []

    def test_filter_by_species_entry_id(self, client):
        # Upload two conformers with different species identities
        upload_h = client.post(
            "/api/v1/uploads/conformers",
            json=_hydrogen_conformer_with_transport_payload("transport-H"),
        ).json()
        # Build a He conformer with transport (different species identity)
        he_payload = _hydrogen_conformer_with_transport_payload("transport-He")
        he_payload["species_entry"] = {
            "smiles": "[He]",
            "charge": 0,
            "multiplicity": 1,
        }
        he_payload["geometry"] = {"xyz_text": "1\nHe atom\nHe 0.0 0.0 0.0"}
        upload_he = client.post(
            "/api/v1/uploads/conformers", json=he_payload
        ).json()
        assert upload_h["species_entry_id"] != upload_he["species_entry_id"]

        resp = client.get(
            "/api/v1/transport",
            params={"species_entry_id": upload_h["species_entry_id"]},
        )
        assert resp.status_code == 200
        assert resp.json()["total"] == 1
        assert resp.json()["items"][0]["species_entry_id"] == upload_h["species_entry_id"]

    def test_filter_by_scientific_origin(self, client):
        # Upload two transport rows with different origins
        client.post(
            "/api/v1/uploads/conformers",
            json=_hydrogen_conformer_with_transport_payload(
                "transport-computed", scientific_origin="computed"
            ),
        )
        he_payload = _hydrogen_conformer_with_transport_payload(
            "transport-experimental", scientific_origin="experimental"
        )
        he_payload["species_entry"] = {
            "smiles": "[He]",
            "charge": 0,
            "multiplicity": 1,
        }
        he_payload["geometry"] = {"xyz_text": "1\nHe atom\nHe 0.0 0.0 0.0"}
        client.post("/api/v1/uploads/conformers", json=he_payload)

        resp = client.get(
            "/api/v1/transport",
            params={"scientific_origin": "experimental"},
        )
        assert resp.status_code == 200
        assert resp.json()["total"] == 1
        assert resp.json()["items"][0]["scientific_origin"] == "experimental"

    def test_filter_by_software_release_id(self, client, db_session):
        # Upload two transport rows with different software releases
        client.post(
            "/api/v1/uploads/conformers",
            json=_hydrogen_conformer_with_transport_payload(
                "transport-g16", software_name="Gaussian", software_version="16"
            ),
        )
        he_payload = _hydrogen_conformer_with_transport_payload(
            "transport-orca", software_name="ORCA", software_version="5.0"
        )
        he_payload["species_entry"] = {
            "smiles": "[He]",
            "charge": 0,
            "multiplicity": 1,
        }
        he_payload["geometry"] = {"xyz_text": "1\nHe atom\nHe 0.0 0.0 0.0"}
        client.post("/api/v1/uploads/conformers", json=he_payload)

        # Get all transport rows to find the distinct software_release_ids
        all_resp = client.get("/api/v1/transport")
        assert all_resp.json()["total"] == 2
        items = all_resp.json()["items"]
        sw_ids = {it["software_release_id"] for it in items}
        assert len(sw_ids) == 2

        target_id = items[0]["software_release_id"]
        resp = client.get(
            "/api/v1/transport", params={"software_release_id": target_id}
        )
        assert resp.status_code == 200
        assert resp.json()["total"] == 1
        assert resp.json()["items"][0]["software_release_id"] == target_id

    def test_filter_by_workflow_tool_release_id(self, client, db_session):
        # Upload two transport rows with different workflow tool releases
        client.post(
            "/api/v1/uploads/conformers",
            json=_hydrogen_conformer_with_transport_payload(
                "transport-arc1",
                workflow_tool_name="ARC",
                workflow_tool_version="1.1.0",
            ),
        )
        he_payload = _hydrogen_conformer_with_transport_payload(
            "transport-arc2",
            workflow_tool_name="ARC",
            workflow_tool_version="2.0.0",
        )
        he_payload["species_entry"] = {
            "smiles": "[He]",
            "charge": 0,
            "multiplicity": 1,
        }
        he_payload["geometry"] = {"xyz_text": "1\nHe atom\nHe 0.0 0.0 0.0"}
        client.post("/api/v1/uploads/conformers", json=he_payload)

        all_resp = client.get("/api/v1/transport")
        assert all_resp.json()["total"] == 2
        items = all_resp.json()["items"]
        wt_ids = {it["workflow_tool_release_id"] for it in items}
        assert len(wt_ids) == 2

        target_id = items[0]["workflow_tool_release_id"]
        resp = client.get(
            "/api/v1/transport",
            params={"workflow_tool_release_id": target_id},
        )
        assert resp.status_code == 200
        assert resp.json()["total"] == 1
        assert resp.json()["items"][0]["workflow_tool_release_id"] == target_id

    def test_filter_by_literature_id(self, client, db_session):
        # Upload two transport rows with different literature refs
        client.post(
            "/api/v1/uploads/conformers",
            json=_hydrogen_conformer_with_transport_payload(
                "transport-lit1",
                literature_title="Transport Paper Alpha",
                literature_year=2020,
            ),
        )
        he_payload = _hydrogen_conformer_with_transport_payload(
            "transport-lit2",
            literature_title="Transport Paper Beta",
            literature_year=2023,
        )
        he_payload["species_entry"] = {
            "smiles": "[He]",
            "charge": 0,
            "multiplicity": 1,
        }
        he_payload["geometry"] = {"xyz_text": "1\nHe atom\nHe 0.0 0.0 0.0"}
        client.post("/api/v1/uploads/conformers", json=he_payload)

        all_resp = client.get("/api/v1/transport")
        assert all_resp.json()["total"] == 2
        items = all_resp.json()["items"]
        lit_ids = {it["literature_id"] for it in items}
        assert len(lit_ids) == 2

        target_id = items[0]["literature_id"]
        resp = client.get(
            "/api/v1/transport", params={"literature_id": target_id}
        )
        assert resp.status_code == 200
        assert resp.json()["total"] == 1
        assert resp.json()["items"][0]["literature_id"] == target_id


# ---------------------------------------------------------------------------
# Pagination
# ---------------------------------------------------------------------------


class TestPagination:
    def test_species_pagination_params(self, client):
        resp = client.get("/api/v1/species?skip=0&limit=10")
        assert resp.status_code == 200
        data = resp.json()
        assert data["skip"] == 0
        assert data["limit"] == 10

    def test_invalid_limit(self, client):
        resp = client.get("/api/v1/species?limit=999")
        assert resp.status_code == 422
