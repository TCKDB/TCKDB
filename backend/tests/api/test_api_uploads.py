"""Tests for the upload API endpoints."""

from __future__ import annotations


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
        "note": "test upload",
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


# ---------------------------------------------------------------------------
# Conformer upload
# ---------------------------------------------------------------------------


class TestConformerUpload:
    def test_success(self, client):
        resp = client.post(
            "/api/v1/uploads/conformers",
            json=_hydrogen_conformer_payload(),
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["type"] == "conformer_observation"
        assert "id" in data
        assert "species_entry_id" in data
        assert "conformer_group_id" in data

    def test_invalid_smiles_returns_422(self, client):
        payload = _hydrogen_conformer_payload()
        payload["species_entry"]["smiles"] = "NOT_A_SMILES"
        resp = client.post("/api/v1/uploads/conformers", json=payload)
        assert resp.status_code == 422

    def test_missing_geometry_returns_422(self, client):
        payload = _hydrogen_conformer_payload()
        del payload["geometry"]
        resp = client.post("/api/v1/uploads/conformers", json=payload)
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Reaction upload
# ---------------------------------------------------------------------------


class TestReactionUpload:
    def test_success(self, client):
        resp = client.post(
            "/api/v1/uploads/reactions",
            json=_reaction_payload(),
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["type"] == "reaction_entry"
        assert "id" in data
        assert "reaction_id" in data

    def test_missing_reactants_returns_422(self, client):
        payload = _reaction_payload()
        payload["reactants"] = []
        resp = client.post("/api/v1/uploads/reactions", json=payload)
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Thermo upload
# ---------------------------------------------------------------------------


class TestThermoUpload:
    def test_success(self, client):
        payload = {
            "species_entry": {"smiles": "[H]", "charge": 0, "multiplicity": 2},
            "scientific_origin": "computed",
            "h298_kj_mol": 217.998,
        }
        resp = client.post("/api/v1/uploads/thermo", json=payload)
        assert resp.status_code == 201
        data = resp.json()
        assert data["type"] == "thermo"
        assert "species_entry_id" in data

    def _seed_calc(self, client, *, calc_type: str = "sp") -> tuple[int, int]:
        """Insert a Calculation directly into the test session and return
        (species_entry_id, calculation_id). Mirrors the conformer-upload
        pre-state that DR-0028's existing_calculation_id path consumes."""
        from app.db.models.calculation import Calculation
        from app.db.models.common import CalculationType
        from app.schemas.fragments.identity import SpeciesEntryIdentityPayload
        from app.services.species_resolution import resolve_species_entry

        session = client._db_session
        entry = resolve_species_entry(
            session,
            SpeciesEntryIdentityPayload(
                smiles="CC", charge=0, multiplicity=1,
            ),
        )
        calc = Calculation(
            type=CalculationType(calc_type),
            species_entry_id=entry.id,
        )
        session.add(calc)
        session.flush()
        return entry.id, calc.id

    def test_existing_calculation_id_links_thermo_to_existing_row(self, client):
        """DR-0028: existing_calculation_id wired through the API produces a
        201, with the thermo attached to the same species entry."""
        species_entry_id, calc_id = self._seed_calc(client, calc_type="sp")
        payload = {
            "species_entry": {"smiles": "CC", "charge": 0, "multiplicity": 1},
            "scientific_origin": "computed",
            "h298_kj_mol": -83.7,
            "source_calculations": [
                {"existing_calculation_id": calc_id, "role": "sp"},
            ],
        }
        resp = client.post("/api/v1/uploads/thermo", json=payload)
        assert resp.status_code == 201, resp.text
        data = resp.json()
        assert data["species_entry_id"] == species_entry_id

    def test_existing_calculation_id_not_found_returns_404(self, client):
        payload = {
            "species_entry": {"smiles": "[H]", "charge": 0, "multiplicity": 2},
            "scientific_origin": "computed",
            "h298_kj_mol": 217.998,
            "source_calculations": [
                {"existing_calculation_id": 999_999_999, "role": "sp"},
            ],
        }
        resp = client.post("/api/v1/uploads/thermo", json=payload)
        assert resp.status_code == 404, resp.text
        assert "does not exist" in resp.json()["detail"]

    def test_existing_calc_wrong_species_entry_returns_422(self, client):
        # Seed a calc owned by a CC species, then upload thermo for a
        # different species (H) that references it.
        _, calc_id = self._seed_calc(client, calc_type="sp")
        payload = {
            "species_entry": {"smiles": "[H]", "charge": 0, "multiplicity": 2},
            "scientific_origin": "computed",
            "h298_kj_mol": 217.998,
            "source_calculations": [
                {"existing_calculation_id": calc_id, "role": "sp"},
            ],
        }
        resp = client.post("/api/v1/uploads/thermo", json=payload)
        assert resp.status_code == 422, resp.text
        detail = resp.json()["detail"]
        assert "different species entry" in detail
        # No internal id values leaked.
        assert str(calc_id) not in detail
        assert "species_entry_id=" not in detail

    def test_existing_calc_role_type_mismatch_returns_422(self, client):
        _, freq_calc_id = self._seed_calc(client, calc_type="freq")
        payload = {
            "species_entry": {"smiles": "CC", "charge": 0, "multiplicity": 1},
            "scientific_origin": "computed",
            "h298_kj_mol": -83.7,
            "source_calculations": [
                # Role=sp pointing at a freq calc
                {"existing_calculation_id": freq_calc_id, "role": "sp"},
            ],
        }
        resp = client.post("/api/v1/uploads/thermo", json=payload)
        assert resp.status_code == 422, resp.text
        assert "incompatible" in resp.json()["detail"]

    def test_both_reference_fields_set_returns_422(self, client):
        payload = {
            "species_entry": {"smiles": "[H]", "charge": 0, "multiplicity": 2},
            "scientific_origin": "computed",
            "h298_kj_mol": 217.998,
            "source_calculations": [
                {
                    "calculation_key": "ghost",
                    "existing_calculation_id": 1,
                    "role": "sp",
                }
            ],
        }
        resp = client.post("/api/v1/uploads/thermo", json=payload)
        assert resp.status_code == 422

    def test_neither_reference_field_set_returns_422(self, client):
        payload = {
            "species_entry": {"smiles": "[H]", "charge": 0, "multiplicity": 2},
            "scientific_origin": "computed",
            "h298_kj_mol": 217.998,
            "source_calculations": [
                {"role": "sp"},
            ],
        }
        resp = client.post("/api/v1/uploads/thermo", json=payload)
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Idempotency and deduplication
# ---------------------------------------------------------------------------


class TestUploadIdempotency:
    def test_duplicate_conformer_creates_separate_observations(self, client):
        """Two identical conformer uploads should create two observations
        but share the same species and species entry."""
        r1 = client.post(
            "/api/v1/uploads/conformers",
            json=_hydrogen_conformer_payload(label="obs-1"),
        )
        r2 = client.post(
            "/api/v1/uploads/conformers",
            json=_hydrogen_conformer_payload(label="obs-2"),
        )
        assert r1.status_code == 201
        assert r2.status_code == 201
        d1, d2 = r1.json(), r2.json()
        # Different observation rows
        assert d1["id"] != d2["id"]
        # Same species entry (deduplication)
        assert d1["species_entry_id"] == d2["species_entry_id"]

    def test_duplicate_reaction_creates_new_entry_same_graph(self, client):
        """Two identical reaction uploads should share the graph-level
        chem_reaction but create separate reaction entries."""
        r1 = client.post("/api/v1/uploads/reactions", json=_reaction_payload())
        r2 = client.post("/api/v1/uploads/reactions", json=_reaction_payload())
        assert r1.status_code == 201
        assert r2.status_code == 201
        d1, d2 = r1.json(), r2.json()
        # Different entries
        assert d1["id"] != d2["id"]
        # Same graph-level reaction (deduplication)
        assert d1["reaction_id"] == d2["reaction_id"]

    def test_repeated_thermo_creates_separate_records(self, client):
        """Two identical thermo uploads should create separate thermo records
        for the same species entry."""
        payload = {
            "species_entry": {"smiles": "[H]", "charge": 0, "multiplicity": 2},
            "scientific_origin": "computed",
            "h298_kj_mol": 217.998,
        }
        r1 = client.post("/api/v1/uploads/thermo", json=payload)
        r2 = client.post("/api/v1/uploads/thermo", json=payload)
        assert r1.status_code == 201
        assert r2.status_code == 201
        d1, d2 = r1.json(), r2.json()
        assert d1["id"] != d2["id"]
        assert d1["species_entry_id"] == d2["species_entry_id"]


# ---------------------------------------------------------------------------
# Read-after-write round trip
# ---------------------------------------------------------------------------


class TestReadAfterWrite:
    def test_conformer_upload_then_read(self, client):
        """Upload a conformer, then GET the species entry and verify the
        conformer appears in the nested list."""
        upload = client.post(
            "/api/v1/uploads/conformers",
            json=_hydrogen_conformer_payload(),
        ).json()
        entry_id = upload["species_entry_id"]

        conformers = client.get(
            f"/api/v1/species-entries/{entry_id}/conformers"
        ).json()
        assert any(c["id"] == upload["id"] for c in conformers)

    def test_reaction_upload_then_read(self, client):
        """Upload a reaction, then GET the reaction entry by ID."""
        upload = client.post(
            "/api/v1/uploads/reactions", json=_reaction_payload()
        ).json()
        entry = client.get(
            f"/api/v1/reaction-entries/{upload['id']}"
        )
        assert entry.status_code == 200
        assert entry.json()["id"] == upload["id"]

    def test_upload_with_explicit_input_geometry_for_opt_round_trip(
        self, client
    ):
        """A producer-explicit ``input_geometries`` for an opt calc must
        round-trip through ``GET /api/v1/calculations/{opt_id}/input-geometries``
        with the declared xyz preserved in the resolved geometry row."""
        pre_opt_xyz = "2\npre-opt H2\nH 0.0 0.0 0.0\nH 0.0 0.0 0.81"
        payload = {
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
                "software_release": {"name": "Gaussian", "version": "16"},
                "level_of_theory": {"method": "B3LYP", "basis": "6-31G(d)"},
                "opt_result": {"converged": True},
                "input_geometries": [{"xyz_text": pre_opt_xyz}],
            },
            "label": "h2-explicit-opt-input",
        }
        resp = client.post("/api/v1/uploads/conformers", json=payload)
        assert resp.status_code == 201, resp.text
        opt_id = resp.json()["primary_calculation"]["calculation_id"]

        listed = client.get(
            f"/api/v1/calculations/{opt_id}/input-geometries"
        )
        assert listed.status_code == 200
        rows = listed.json()
        assert len(rows) == 1
        assert rows[0]["input_order"] == 1
        # Two atoms came in; the resolved Geometry must reflect that.
        assert rows[0]["geometry"]["natoms"] == 2

    def test_conformer_upload_freq_sp_have_input_geometries(self, client):
        """Conformer upload with primary opt + freq + sp additionals must
        produce one row in calculation_input_geometry for each of the
        freq/sp calcs (visible via GET .../input-geometries) and zero
        rows for the primary opt calc.
        """
        payload = {
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
                "software_release": {"name": "Gaussian", "version": "16"},
                "level_of_theory": {"method": "B3LYP", "basis": "6-31G(d)"},
                "opt_result": {"converged": True},
            },
            "additional_calculations": [
                {
                    "type": "freq",
                    "software_release": {"name": "Gaussian", "version": "16"},
                    "level_of_theory": {
                        "method": "B3LYP", "basis": "6-31G(d)"
                    },
                    "freq_result": {"n_imag": 0, "zpe_hartree": 0.010},
                },
                {
                    "type": "sp",
                    "software_release": {"name": "Orca", "version": "5.0"},
                    "level_of_theory": {
                        "method": "CCSD(T)", "basis": "cc-pVTZ"
                    },
                    "sp_result": {"electronic_energy_hartree": -1.195},
                },
            ],
            "label": "h2-input-geom",
        }
        resp = client.post("/api/v1/uploads/conformers", json=payload)
        assert resp.status_code == 201
        body = resp.json()

        opt_id = body["primary_calculation"]["calculation_id"]
        additionals = {
            ref["type"]: ref["calculation_id"]
            for ref in body["additional_calculations"]
        }
        freq_id = additionals["freq"]
        sp_id = additionals["sp"]

        opt_inputs = client.get(
            f"/api/v1/calculations/{opt_id}/input-geometries"
        ).json()
        assert opt_inputs == []

        freq_inputs = client.get(
            f"/api/v1/calculations/{freq_id}/input-geometries"
        ).json()
        assert len(freq_inputs) == 1

        sp_inputs = client.get(
            f"/api/v1/calculations/{sp_id}/input-geometries"
        ).json()
        assert len(sp_inputs) == 1

    def test_upload_with_explicit_scan_output_geometries_round_trip(
        self, client
    ):
        """Bundle upload with a scan additional calc declaring three
        scan-point output geometries must round-trip through
        ``GET /api/v1/calculations/{scan_id}/output-geometries`` with
        the declared roles preserved and ordered by ``output_order``."""
        xyz_a = "1\nscan-1\nH 0.0 0.0 0.10"
        xyz_b = "1\nscan-2\nH 0.0 0.0 0.20"
        xyz_c = "1\nscan-3\nH 0.0 0.0 0.30"
        payload = {
            "species_entry": {
                "smiles": "[H]",
                "charge": 0,
                "multiplicity": 2,
            },
            "conformers": [
                {
                    "key": "c0",
                    "geometry": {"xyz_text": "1\nH atom\nH 0.0 0.0 0.0"},
                    "primary_calculation": {
                        "key": "opt0",
                        "type": "opt",
                        "software_release": {
                            "name": "Gaussian", "version": "16"
                        },
                        "level_of_theory": {
                            "method": "B3LYP", "basis": "6-31G(d)"
                        },
                        "opt_result": {"converged": True},
                    },
                    "additional_calculations": [
                        {
                            "key": "scan0",
                            "type": "scan",
                            "software_release": {
                                "name": "Gaussian", "version": "16"
                            },
                            "level_of_theory": {
                                "method": "B3LYP", "basis": "6-31G(d)"
                            },
                            "output_geometries": [
                                {
                                    "geometry": {"xyz_text": xyz_a},
                                    "role": "scan_point",
                                },
                                {
                                    "geometry": {"xyz_text": xyz_b},
                                    "role": "scan_point",
                                },
                                {
                                    "geometry": {"xyz_text": xyz_c},
                                    "role": "scan_point",
                                },
                            ],
                        }
                    ],
                }
            ],
        }
        resp = client.post(
            "/api/v1/uploads/computed-species", json=payload
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()

        scan_id = next(
            ref["calculation_id"]
            for ref in body["conformers"][0]["additional_calculations"]
            if ref["key"] == "scan0"
        )

        listed = client.get(
            f"/api/v1/calculations/{scan_id}/output-geometries"
        )
        assert listed.status_code == 200
        rows = listed.json()
        assert [r["output_order"] for r in rows] == [1, 2, 3]
        assert [r["role"] for r in rows] == [
            "scan_point", "scan_point", "scan_point"
        ]
        assert len({r["geometry"]["id"] for r in rows}) == 3
