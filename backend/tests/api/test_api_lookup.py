"""Tests for the chemistry-first lookup API.

Tests validate the consistent response envelope (query, match, results)
and the machine-readable detail_codes across all lookup endpoints.
"""

from __future__ import annotations


def _h2_conformer_payload() -> dict:
    """H2 conformer at wb97xd/def2tzvp."""
    return {
        "species_entry": {
            "smiles": "[H][H]",
            "charge": 0,
            "multiplicity": 1,
        },
        "geometry": {
            "xyz_text": "2\nH2\nH 0.0 0.0 0.0\nH 0.0 0.0 0.74",
        },
        "calculation": {
            "type": "opt",
            "software_release": {"name": "gaussian", "version": "09"},
            "level_of_theory": {"method": "wb97xd", "basis": "def2tzvp"},
            "opt_result": {
                "converged": True,
                "n_steps": 3,
                "final_energy_hartree": -1.17264,
            },
        },
        "additional_calculations": [
            {
                "type": "freq",
                "software_release": {"name": "gaussian", "version": "09"},
                "level_of_theory": {"method": "wb97xd", "basis": "def2tzvp"},
                "freq_result": {"n_imag": 0, "zpe_hartree": 0.00987},
            },
        ],
    }


def _thermo_payload() -> dict:
    return {
        "species_entry": {"smiles": "[H][H]", "charge": 0, "multiplicity": 1},
        "h298_kj_mol": 0.0,
        "s298_j_mol_k": 130.68,
        "tmin_k": 200.0,
        "tmax_k": 6000.0,
    }


# ---------------------------------------------------------------------------
# Response envelope structure
# ---------------------------------------------------------------------------


class TestResponseEnvelope:
    """Every lookup endpoint returns query + match + results + detail_codes."""

    def test_envelope_keys(self, client):
        resp = client.get(
            "/api/v1/lookup/species",
            params={"smiles": "[He]", "charge": 0, "multiplicity": 1},
        )
        data = resp.json()
        assert "query" in data
        assert "match" in data
        assert "results" in data
        assert "detail_codes" in data["match"]
        assert "details" in data["match"]
        assert "status" in data["match"]

    def test_detail_codes_are_machine_readable(self, client):
        """detail_codes use underscored tokens, not prose."""
        client.post("/api/v1/uploads/conformers", json=_h2_conformer_payload())
        resp = client.get(
            "/api/v1/lookup/species",
            params={"smiles": "[H][H]", "charge": 0, "multiplicity": 1},
        )
        codes = resp.json()["match"]["detail_codes"]
        assert len(codes) > 0
        for code in codes:
            assert " " not in code, f"detail_code should not contain spaces: {code!r}"

    def test_results_have_links(self, client):
        client.post("/api/v1/uploads/conformers", json=_h2_conformer_payload())
        resp = client.get(
            "/api/v1/lookup/species",
            params={"smiles": "[H][H]", "charge": 0, "multiplicity": 1},
        )
        for item in resp.json()["results"]:
            assert "resource_type" in item
            assert "id" in item
            assert "links" in item
            assert "self" in item["links"]
            assert "summary" in item


# ---------------------------------------------------------------------------
# /lookup/species — identity resolution
# ---------------------------------------------------------------------------


class TestSpeciesLookup:
    def test_no_match_codes(self, client):
        resp = client.get(
            "/api/v1/lookup/species",
            params={"smiles": "[H][H]", "charge": 0, "multiplicity": 1},
        )
        data = resp.json()
        assert data["match"]["status"] == "none"
        assert "species_identity_none" in data["match"]["detail_codes"]

    def test_exact_match_codes(self, client):
        client.post("/api/v1/uploads/conformers", json=_h2_conformer_payload())
        resp = client.get(
            "/api/v1/lookup/species",
            params={"smiles": "[H][H]", "charge": 0, "multiplicity": 1},
        )
        data = resp.json()
        assert data["match"]["status"] == "exact"
        assert "species_identity_exact" in data["match"]["detail_codes"]

        types = [r["resource_type"] for r in data["results"]]
        assert "species" in types
        assert "species_entry" in types

    def test_species_summary(self, client):
        client.post("/api/v1/uploads/conformers", json=_h2_conformer_payload())
        resp = client.get(
            "/api/v1/lookup/species",
            params={"smiles": "[H][H]", "charge": 0, "multiplicity": 1},
        )
        species_item = [
            r for r in resp.json()["results"] if r["resource_type"] == "species"
        ][0]
        assert species_item["summary"]["charge"] == 0
        assert species_item["summary"]["multiplicity"] == 1
        assert species_item["links"]["self"].startswith("/api/v1/species/")

    def test_entry_summary_counts(self, client):
        client.post("/api/v1/uploads/conformers", json=_h2_conformer_payload())
        resp = client.get(
            "/api/v1/lookup/species",
            params={"smiles": "[H][H]", "charge": 0, "multiplicity": 1},
        )
        entry_items = [
            r for r in resp.json()["results"] if r["resource_type"] == "species_entry"
        ]
        assert entry_items[0]["summary"]["calculation_count"] >= 1

    def test_invalid_smiles(self, client):
        resp = client.get(
            "/api/v1/lookup/species",
            params={"smiles": "NOT_VALID", "charge": 0, "multiplicity": 1},
        )
        assert resp.json()["match"]["status"] == "none"
        assert "species_identity_none" in resp.json()["match"]["detail_codes"]


# ---------------------------------------------------------------------------
# /lookup/calculations — result lookup
# ---------------------------------------------------------------------------


class TestCalculationsLookup:
    def _upload_and_get_entry_id(self, client) -> int:
        client.post("/api/v1/uploads/conformers", json=_h2_conformer_payload())
        sp_resp = client.get(
            "/api/v1/lookup/species",
            params={"smiles": "[H][H]", "charge": 0, "multiplicity": 1},
        )
        entries = [
            r for r in sp_resp.json()["results"]
            if r["resource_type"] == "species_entry"
        ]
        return entries[0]["id"]

    def test_find_opt_with_lot_codes(self, client):
        entry_id = self._upload_and_get_entry_id(client)
        resp = client.get(
            "/api/v1/lookup/calculations",
            params={
                "species_entry_id": entry_id,
                "type": "opt",
                "method": "wb97xd",
                "basis": "def2tzvp",
            },
        )
        data = resp.json()
        assert data["match"]["status"] == "exact"
        assert "calculation_exists" in data["match"]["detail_codes"]
        assert "lot_method_exact" in data["match"]["detail_codes"]
        assert "lot_basis_exact" in data["match"]["detail_codes"]

        calcs = [r for r in data["results"] if r["resource_type"] == "calculation"]
        assert calcs[0]["summary"]["converged"] is True
        assert calcs[0]["summary"]["energy_hartree"] == -1.17264

    def test_no_match_codes(self, client):
        entry_id = self._upload_and_get_entry_id(client)
        resp = client.get(
            "/api/v1/lookup/calculations",
            params={
                "species_entry_id": entry_id,
                "type": "opt",
                "method": "ccsd(t)",
                "basis": "cc-pvtz",
            },
        )
        data = resp.json()
        assert data["match"]["status"] == "none"
        assert "calculation_none" in data["match"]["detail_codes"]

    def test_entry_not_found(self, client):
        resp = client.get(
            "/api/v1/lookup/calculations",
            params={"species_entry_id": 999999, "type": "opt"},
        )
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# /lookup/thermo — result lookup
# ---------------------------------------------------------------------------


class TestThermoLookup:
    def test_no_thermo_codes(self, client):
        client.post("/api/v1/uploads/conformers", json=_h2_conformer_payload())
        sp_resp = client.get(
            "/api/v1/lookup/species",
            params={"smiles": "[H][H]", "charge": 0, "multiplicity": 1},
        )
        entry_id = [
            r for r in sp_resp.json()["results"]
            if r["resource_type"] == "species_entry"
        ][0]["id"]

        resp = client.get("/api/v1/lookup/thermo", params={"species_entry_id": entry_id})
        assert resp.json()["match"]["status"] == "none"
        assert "thermo_none" in resp.json()["match"]["detail_codes"]

    def test_has_thermo_codes(self, client):
        client.post("/api/v1/uploads/conformers", json=_h2_conformer_payload())
        client.post("/api/v1/uploads/thermo", json=_thermo_payload())

        sp_resp = client.get(
            "/api/v1/lookup/species",
            params={"smiles": "[H][H]", "charge": 0, "multiplicity": 1},
        )
        entry_id = [
            r for r in sp_resp.json()["results"]
            if r["resource_type"] == "species_entry"
        ][0]["id"]

        resp = client.get("/api/v1/lookup/thermo", params={"species_entry_id": entry_id})
        data = resp.json()
        assert data["match"]["status"] == "exact"
        assert "thermo_exists" in data["match"]["detail_codes"]
        thermo_items = [r for r in data["results"] if r["resource_type"] == "thermo"]
        assert thermo_items[0]["summary"]["h298_kj_mol"] == 0.0


# ---------------------------------------------------------------------------
# /lookup/species-calculation — composed lookup
# ---------------------------------------------------------------------------


class TestSpeciesCalculationLookup:
    def test_full_hit_codes(self, client):
        client.post("/api/v1/uploads/conformers", json=_h2_conformer_payload())
        resp = client.get(
            "/api/v1/lookup/species-calculation",
            params={
                "smiles": "[H][H]", "charge": 0, "multiplicity": 1,
                "type": "opt", "method": "wb97xd", "basis": "def2tzvp",
            },
        )
        data = resp.json()
        codes = data["match"]["detail_codes"]
        assert data["match"]["status"] == "exact"
        assert "species_identity_exact" in codes
        assert "species_entry_exists" in codes
        assert "calculation_exists" in codes
        assert "lot_method_exact" in codes
        assert "lot_basis_exact" in codes

        types = [r["resource_type"] for r in data["results"]]
        assert "species" in types
        assert "species_entry" in types
        assert "calculation" in types

    def test_include_geometry(self, client):
        """include=geometry expands xyz_text inline with provenance."""
        client.post("/api/v1/uploads/conformers", json=_h2_conformer_payload())
        resp = client.get(
            "/api/v1/lookup/species-calculation",
            params={
                "smiles": "[H][H]", "charge": 0, "multiplicity": 1,
                "type": "opt", "method": "wb97xd", "basis": "def2tzvp",
                "include": "geometry",
            },
        )
        data = resp.json()
        assert data["match"]["status"] == "exact"
        calc = [r for r in data["results"] if r["resource_type"] == "calculation"][0]
        geom = calc["summary"]["geometry"]
        assert geom is not None
        assert "H" in geom["xyz_text"]
        assert geom["natoms"] == 2
        assert geom["role"] == "final"
        assert "source_calculation_id" in geom
        assert "geometry_id" in geom

    def test_include_geometry_on_freq_returns_linked_geometry(self, client):
        """Freq calcs have a linked geometry (the geometry they were performed at)."""
        client.post("/api/v1/uploads/conformers", json=_h2_conformer_payload())
        resp = client.get(
            "/api/v1/lookup/species-calculation",
            params={
                "smiles": "[H][H]", "charge": 0, "multiplicity": 1,
                "type": "freq", "method": "wb97xd", "basis": "def2tzvp",
                "include": "geometry",
            },
        )
        data = resp.json()
        calc = [r for r in data["results"] if r["resource_type"] == "calculation"][0]
        # Freq calc was performed at this geometry, so it's linked
        geom = calc["summary"]["geometry"]
        assert geom is not None
        assert geom["role"] == "final"
        assert "source_calculation_id" in geom

    def test_no_include_omits_geometry(self, client):
        """Without include=geometry, geometry key is absent entirely."""
        client.post("/api/v1/uploads/conformers", json=_h2_conformer_payload())
        resp = client.get(
            "/api/v1/lookup/species-calculation",
            params={
                "smiles": "[H][H]", "charge": 0, "multiplicity": 1,
                "type": "opt", "method": "wb97xd", "basis": "def2tzvp",
            },
        )
        calc = [r for r in resp.json()["results"] if r["resource_type"] == "calculation"][0]
        assert "geometry" not in calc["summary"]

    def test_no_species_codes(self, client):
        resp = client.get(
            "/api/v1/lookup/species-calculation",
            params={
                "smiles": "[He]", "charge": 0, "multiplicity": 1,
                "type": "opt", "method": "wb97xd", "basis": "def2tzvp",
            },
        )
        data = resp.json()
        assert data["match"]["status"] == "none"
        assert "species_identity_none" in data["match"]["detail_codes"]

    def test_species_exists_wrong_lot_codes(self, client):
        client.post("/api/v1/uploads/conformers", json=_h2_conformer_payload())
        resp = client.get(
            "/api/v1/lookup/species-calculation",
            params={
                "smiles": "[H][H]", "charge": 0, "multiplicity": 1,
                "type": "opt", "method": "b3lyp", "basis": "6-31g",
            },
        )
        data = resp.json()
        codes = data["match"]["detail_codes"]
        assert data["match"]["status"] == "partial"
        assert "species_identity_exact" in codes
        assert "species_entry_exists" in codes
        assert "calculation_none" in codes


# ---------------------------------------------------------------------------
# Selection modes
# ---------------------------------------------------------------------------


def _h2_conformer_high_energy() -> dict:
    """H2 conformer at same LOT but different geometry/energy."""
    return {
        "species_entry": {
            "smiles": "[H][H]",
            "charge": 0,
            "multiplicity": 1,
        },
        "geometry": {
            "xyz_text": "2\nH2 stretched\nH 0.0 0.0 0.0\nH 0.0 0.0 1.50",
        },
        "calculation": {
            "type": "opt",
            "software_release": {"name": "gaussian", "version": "09"},
            "level_of_theory": {"method": "wb97xd", "basis": "def2tzvp"},
            "opt_result": {
                "converged": True,
                "n_steps": 5,
                "final_energy_hartree": -1.10000,
            },
        },
    }


class TestSelectionModes:
    def test_selection_all_returns_multiple(self, client):
        client.post("/api/v1/uploads/conformers", json=_h2_conformer_payload())
        client.post("/api/v1/uploads/conformers", json=_h2_conformer_high_energy())

        resp = client.get(
            "/api/v1/lookup/species-calculation",
            params={
                "smiles": "[H][H]", "charge": 0, "multiplicity": 1,
                "type": "opt", "method": "wb97xd", "basis": "def2tzvp",
                "selection": "all",
            },
        )
        calcs = [r for r in resp.json()["results"] if r["resource_type"] == "calculation"]
        assert len(calcs) >= 2

    def test_selection_lowest_energy(self, client):
        client.post("/api/v1/uploads/conformers", json=_h2_conformer_payload())
        client.post("/api/v1/uploads/conformers", json=_h2_conformer_high_energy())

        resp = client.get(
            "/api/v1/lookup/species-calculation",
            params={
                "smiles": "[H][H]", "charge": 0, "multiplicity": 1,
                "type": "opt", "method": "wb97xd", "basis": "def2tzvp",
                "selection": "lowest_energy",
            },
        )
        data = resp.json()
        calcs = [r for r in data["results"] if r["resource_type"] == "calculation"]
        assert len(calcs) == 1
        # -1.17264 < -1.10000, so the lower energy one wins
        assert calcs[0]["summary"]["energy_hartree"] == -1.17264
        assert "selection_applied" in data["match"]["detail_codes"]

    def test_selection_latest(self, client):
        client.post("/api/v1/uploads/conformers", json=_h2_conformer_payload())
        client.post("/api/v1/uploads/conformers", json=_h2_conformer_high_energy())

        resp = client.get(
            "/api/v1/lookup/species-calculation",
            params={
                "smiles": "[H][H]", "charge": 0, "multiplicity": 1,
                "type": "opt", "method": "wb97xd", "basis": "def2tzvp",
                "selection": "latest",
            },
        )
        data = resp.json()
        calcs = [r for r in data["results"] if r["resource_type"] == "calculation"]
        assert len(calcs) == 1
        assert "selection_applied" in data["match"]["detail_codes"]

    def test_selection_earliest(self, client):
        client.post("/api/v1/uploads/conformers", json=_h2_conformer_payload())
        client.post("/api/v1/uploads/conformers", json=_h2_conformer_high_energy())

        resp = client.get(
            "/api/v1/lookup/species-calculation",
            params={
                "smiles": "[H][H]", "charge": 0, "multiplicity": 1,
                "type": "opt", "method": "wb97xd", "basis": "def2tzvp",
                "selection": "earliest",
            },
        )
        data = resp.json()
        calcs = [r for r in data["results"] if r["resource_type"] == "calculation"]
        assert len(calcs) == 1
        assert "selection_applied" in data["match"]["detail_codes"]

    def test_selection_on_single_result_is_noop(self, client):
        """Selection on a single match returns that match without selection_applied."""
        client.post("/api/v1/uploads/conformers", json=_h2_conformer_payload())

        resp = client.get(
            "/api/v1/lookup/species-calculation",
            params={
                "smiles": "[H][H]", "charge": 0, "multiplicity": 1,
                "type": "opt", "method": "wb97xd", "basis": "def2tzvp",
                "selection": "lowest_energy",
            },
        )
        data = resp.json()
        calcs = [r for r in data["results"] if r["resource_type"] == "calculation"]
        assert len(calcs) == 1
        # No selection_applied code because only one candidate
        assert "selection_applied" not in data["match"]["detail_codes"]


# ---------------------------------------------------------------------------
# /lookup/reaction — identity resolution
# ---------------------------------------------------------------------------


def _reaction_payload() -> dict:
    return {
        "reversible": True,
        "reaction_family": "H_Abstraction",
        "reactants": [
            {"species_entry": {"smiles": "[H]", "charge": 0, "multiplicity": 2}},
        ],
        "products": [
            {"species_entry": {"smiles": "[H]", "charge": 0, "multiplicity": 2}},
        ],
    }


class TestReactionLookup:
    def test_no_match(self, client):
        resp = client.get(
            "/api/v1/lookup/reaction",
            params={"reactants": "[H]", "products": "[H]"},
        )
        data = resp.json()
        assert data["match"]["status"] == "none"
        assert data["query"]["kind"] == "reaction"

    def test_exact_match(self, client):
        # Upload a reaction so the species + reaction exist
        client.post("/api/v1/uploads/reactions", json=_reaction_payload())

        resp = client.get(
            "/api/v1/lookup/reaction",
            params={
                "reactants": "[H]",
                "products": "[H]",
                "reactant_multiplicities": 2,
                "product_multiplicities": 2,
            },
        )
        data = resp.json()
        assert data["match"]["status"] == "exact"
        assert "reaction_identity_exact" in data["match"]["detail_codes"]
        assert "reactant_resolved" in data["match"]["detail_codes"]
        assert "product_resolved" in data["match"]["detail_codes"]

        types = [r["resource_type"] for r in data["results"]]
        assert "reaction" in types
        assert "reaction_entry" in types

    def test_reaction_summary(self, client):
        client.post("/api/v1/uploads/reactions", json=_reaction_payload())
        resp = client.get(
            "/api/v1/lookup/reaction",
            params={
                "reactants": "[H]",
                "products": "[H]",
                "reactant_multiplicities": 2,
                "product_multiplicities": 2,
            },
        )
        rxn = [r for r in resp.json()["results"] if r["resource_type"] == "reaction"][0]
        assert rxn["summary"]["reversible"] is True
        assert rxn["links"]["self"].startswith("/api/v1/reactions/")

    def test_entry_includes_participant_structure(self, client):
        client.post("/api/v1/uploads/reactions", json=_reaction_payload())
        resp = client.get(
            "/api/v1/lookup/reaction",
            params={
                "reactants": "[H]",
                "products": "[H]",
                "reactant_multiplicities": 2,
                "product_multiplicities": 2,
            },
        )
        entry_items = [
            r for r in resp.json()["results"]
            if r["resource_type"] == "reaction_entry"
        ]
        assert len(entry_items) >= 1
        entry = entry_items[0]
        # Explicit IDs for client logic (no link parsing needed)
        assert "reaction_id" in entry["summary"]
        assert "reaction_entry_id" in entry["summary"]

        # Resolved participant structure
        assert "participants" in entry["summary"]
        participants = entry["summary"]["participants"]
        assert len(participants) >= 1
        p = participants[0]
        assert p["side"] in ("reactant", "product")
        assert "index" in p
        assert "species_entry_id" in p
        assert "species_id" in p
        assert "smiles" in p
        assert "links" in p
        assert "species_entry" in p["links"]

    def test_unknown_reactant(self, client):
        resp = client.get(
            "/api/v1/lookup/reaction",
            params={"reactants": "[He]", "products": "[He]"},
        )
        assert resp.json()["match"]["status"] == "none"
        assert any(
            "reactant_not_found" in c or "product_not_found" in c
            for c in resp.json()["match"]["detail_codes"]
        )


# ---------------------------------------------------------------------------
# /lookup/kinetics — result lookup
# ---------------------------------------------------------------------------


class TestKineticsLookup:
    def test_entry_not_found(self, client):
        resp = client.get(
            "/api/v1/lookup/kinetics",
            params={"reaction_entry_id": 999999},
        )
        assert resp.status_code == 404

    def test_no_kinetics(self, client):
        # Create a reaction entry (no kinetics)
        client.post("/api/v1/uploads/reactions", json=_reaction_payload())
        # Find the entry
        rxn_resp = client.get(
            "/api/v1/lookup/reaction",
            params={
                "reactants": "[H]",
                "products": "[H]",
                "reactant_multiplicities": 2,
                "product_multiplicities": 2,
            },
        )
        entry_items = [
            r for r in rxn_resp.json()["results"]
            if r["resource_type"] == "reaction_entry"
        ]
        entry_id = entry_items[0]["id"]

        resp = client.get(
            "/api/v1/lookup/kinetics",
            params={"reaction_entry_id": entry_id},
        )
        assert resp.json()["match"]["status"] == "none"
        assert "kinetics_none" in resp.json()["match"]["detail_codes"]


# ---------------------------------------------------------------------------
# /lookup/reaction-kinetics — composed lookup
# ---------------------------------------------------------------------------


class TestReactionKineticsLookup:
    def test_no_reaction(self, client):
        resp = client.get(
            "/api/v1/lookup/reaction-kinetics",
            params={"reactants": "[He]", "products": "[Ne]"},
        )
        assert resp.json()["match"]["status"] == "none"

    def test_reaction_exists_no_kinetics(self, client):
        client.post("/api/v1/uploads/reactions", json=_reaction_payload())
        resp = client.get(
            "/api/v1/lookup/reaction-kinetics",
            params={
                "reactants": "[H]",
                "products": "[H]",
                "reactant_multiplicities": 2,
                "product_multiplicities": 2,
            },
        )
        data = resp.json()
        assert data["match"]["status"] == "partial"
        codes = data["match"]["detail_codes"]
        assert "reaction_identity_exact" in codes
        assert "kinetics_none" in codes
