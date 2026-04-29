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
