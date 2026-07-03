"""Tests for the expanded chemistry-first lookup API.

Covers:
- ``/lookup/geometry``  — exact geom_hash match and miss
- ``/lookup/statmech``  — species-entry lookup, append-only visibility
- ``/lookup/transport`` — species-entry lookup, append-only visibility
- ``/lookup/network``   — membership-by-species-entry-set (contains-all)
- envelope consistency across all four endpoints
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Payload helpers
# ---------------------------------------------------------------------------


def _h2_conformer_payload() -> dict:
    return {
        "species_entry": {"smiles": "[H][H]", "charge": 0, "multiplicity": 1},
        "geometry": {"xyz_text": "2\nH2\nH 0.0 0.0 0.0\nH 0.0 0.0 0.74"},
        "calculation": {
            "type": "opt",
            "software_release": {"name": "gaussian", "version": "09"},
            "level_of_theory": {"method": "wb97xd", "basis": "def2tzvp"},
            "opt_result": {"converged": True, "n_steps": 3,
                           "final_energy_hartree": -1.17264},
        },
    }


def _statmech_payload(smiles: str = "[H]", mult: int = 2,
                      **overrides) -> dict:
    base: dict = {
        "species_entry": {"smiles": smiles, "charge": 0, "multiplicity": mult},
        "scientific_origin": "computed",
        "statmech_treatment": "rrho",
        "external_symmetry": 1,
    }
    base.update(overrides)
    return base


def _transport_payload(smiles: str = "[H]", mult: int = 2,
                       **overrides) -> dict:
    base: dict = {
        "species_entry": {"smiles": smiles, "charge": 0, "multiplicity": mult},
        "scientific_origin": "computed",
        "sigma_angstrom": 2.05,
        "epsilon_over_k_k": 145.0,
        "dipole_debye": 0.0,
    }
    base.update(overrides)
    return base


def _pdep_payload(name: str = "net-lookup") -> dict:
    xyz_ethyl = "3\n\nC 0.0 0.0 0.0\nC 1.54 0.0 0.0\nH 2.0 1.0 0.0"
    xyz_o2 = "2\n\nO 0.0 0.0 0.0\nO 1.21 0.0 0.0"
    xyz_etoo = "4\n\nC 0.0 0.0 0.0\nC 1.54 0.0 0.0\nO 2.5 0.0 0.0\nO 3.7 0.0 0.0"
    xyz_ts = "4\n\nC 0.0 0.0 0.0\nC 1.54 0.0 0.0\nO 2.2 0.0 0.0\nO 3.4 0.0 0.0"
    xyz_ar = "1\n\nAr 0.0 0.0 0.0"
    software = {"name": "Gaussian", "version": "16"}
    lot = {"method": "B3LYP", "basis": "6-31G(d)"}

    return {
        "name": name,
        "species": [
            {
                "key": "ethyl",
                "species_entry": {"smiles": "C[CH2]", "charge": 0, "multiplicity": 2},
                "conformers": [{
                    "key": "ethyl_conf",
                    "geometry": {"key": "ethyl_geom", "xyz_text": xyz_ethyl},
                    "calculation": {
                        "key": "ethyl_opt", "type": "opt",
                        "software_release": software, "level_of_theory": lot,
                    },
                }],
            },
            {
                "key": "O2",
                "species_entry": {"smiles": "[O][O]", "charge": 0, "multiplicity": 3},
                "conformers": [{
                    "key": "O2_conf",
                    "geometry": {"key": "O2_geom", "xyz_text": xyz_o2},
                    "calculation": {
                        "key": "O2_opt", "type": "opt",
                        "software_release": software, "level_of_theory": lot,
                    },
                }],
            },
            {
                "key": "ethylperoxy",
                "species_entry": {"smiles": "CCO[O]", "charge": 0, "multiplicity": 2},
                "conformers": [{
                    "key": "etoo_conf",
                    "geometry": {"key": "etoo_geom", "xyz_text": xyz_etoo},
                    "calculation": {
                        "key": "etoo_opt", "type": "opt",
                        "software_release": software, "level_of_theory": lot,
                    },
                }],
            },
            {
                "key": "Ar",
                "species_entry": {"smiles": "[Ar]", "charge": 0, "multiplicity": 1},
                "conformers": [{
                    "key": "Ar_conf",
                    "geometry": {"key": "Ar_geom", "xyz_text": xyz_ar},
                    "calculation": {
                        "key": "Ar_opt", "type": "opt",
                        "software_release": software, "level_of_theory": lot,
                    },
                }],
            },
        ],
        "transition_states": [{
            "key": "ts_assoc",
            "micro_reaction_key": "rxn_assoc",
            "charge": 0, "multiplicity": 2,
            "geometry": {"key": "ts_assoc_geom", "xyz_text": xyz_ts},
            "calculation": {
                "key": "ts_assoc_opt", "type": "opt",
                "software_release": software, "level_of_theory": lot,
                "opt_converged": True,
            },
        }],
        "micro_reactions": [{
            "key": "rxn_assoc", "reversible": True,
            "reactants": [{"species_key": "ethyl"}, {"species_key": "O2"}],
            "products": [{"species_key": "ethylperoxy"}],
        }],
        "states": [
            {"key": "entrance", "kind": "bimolecular",
             "participants": [{"species_key": "ethyl"}, {"species_key": "O2"}]},
            {"key": "well_RO2", "kind": "well",
             "participants": [{"species_key": "ethylperoxy"}]},
        ],
        "channels": [{
            "source_state_key": "entrance",
            "sink_state_key": "well_RO2",
            "kind": "association",
        }],
        "solve": {
            "me_method": "reservoir_state",
            "tmin_k": 300, "tmax_k": 2000,
            "pmin_bar": 0.01, "pmax_bar": 100,
            "grain_count": 250,
            "bath_gas": [{"species_key": "Ar", "mole_fraction": 1.0}],
            "energy_transfer": {"model": "single_exponential_down",
                                "alpha0_cm_inv": 300, "t_ref_k": 300},
        },
    }


def _species_entry_id(client, smiles: str, mult: int) -> int:
    resp = client.get(
        "/api/v1/lookup/species",
        params={"smiles": smiles, "charge": 0, "multiplicity": mult},
    )
    entries = [r for r in resp.json()["results"]
               if r["resource_type"] == "species_entry"]
    assert entries, resp.text
    return entries[0]["id"]


def _envelope_ok(data: dict, expected_kind: str) -> None:
    """All lookup endpoints must share the same top-level envelope shape."""
    assert "query" in data and "match" in data and "results" in data
    assert data["query"]["kind"] == expected_kind
    assert "inputs" in data["query"]
    assert "status" in data["match"]
    assert "detail_codes" in data["match"]
    assert "details" in data["match"]
    for item in data["results"]:
        assert "resource_type" in item
        assert "id" in item
        assert "links" in item and "self" in item["links"]
        assert "summary" in item


# ---------------------------------------------------------------------------
# /lookup/geometry
# ---------------------------------------------------------------------------


class TestGeometryLookup:
    def _upload_and_get_geom(self, client) -> tuple[int, str, int]:
        """Upload an H2 conformer and return (geometry_id, geom_hash, natoms)."""
        client.post("/api/v1/uploads/conformers", json=_h2_conformer_payload())
        # Find the geometry via the calculations lookup with include=geometry
        sc = client.get(
            "/api/v1/lookup/species-calculation",
            params={"smiles": "[H][H]", "charge": 0, "multiplicity": 1,
                    "type": "opt", "method": "wb97xd", "basis": "def2tzvp",
                    "include": "geometry"},
        )
        calc = next(r for r in sc.json()["results"]
                if r["resource_type"] == "calculation")
        geom_id = calc["summary"]["geometry"]["geometry_id"]
        # Fetch canonical geometry to obtain its stored hash
        geom_resp = client.get(f"/api/v1/geometries/{geom_id}")
        body = geom_resp.json()
        return geom_id, body["geom_hash"], body["natoms"]

    def test_hit_by_exact_hash(self, client):
        geom_id, geom_hash, natoms = self._upload_and_get_geom(client)
        resp = client.get("/api/v1/lookup/geometry",
                          params={"geom_hash": geom_hash})
        data = resp.json()
        assert resp.status_code == 200
        assert data["match"]["status"] == "exact"
        assert "geometry_identity_exact" in data["match"]["detail_codes"]
        assert len(data["results"]) == 1
        item = data["results"][0]
        assert item["resource_type"] == "geometry"
        assert item["id"] == geom_id
        assert item["summary"]["geom_hash"] == geom_hash
        assert item["summary"]["natoms"] == natoms
        assert item["links"]["self"] == f"/api/v1/geometries/{geom_id}"

    def test_miss_by_unknown_hash(self, client):
        resp = client.get(
            "/api/v1/lookup/geometry",
            params={"geom_hash": "0" * 64},
        )
        data = resp.json()
        assert resp.status_code == 200
        assert data["match"]["status"] == "none"
        assert "geometry_identity_none" in data["match"]["detail_codes"]
        assert data["results"] == []

    def test_envelope(self, client):
        resp = client.get("/api/v1/lookup/geometry",
                          params={"geom_hash": "0" * 64})
        _envelope_ok(resp.json(), "geometry")


# ---------------------------------------------------------------------------
# /lookup/statmech
# ---------------------------------------------------------------------------


class TestStatmechLookup:
    def test_hit_by_species_entry(self, client):
        r = client.post("/api/v1/uploads/statmech",
                        json=_statmech_payload())
        assert r.status_code == 201, r.text
        entry_id = r.json()["species_entry_id"]

        resp = client.get("/api/v1/lookup/statmech",
                          params={"species_entry_id": entry_id})
        data = resp.json()
        assert data["match"]["status"] == "exact"
        assert "statmech_exists" in data["match"]["detail_codes"]
        items = [r for r in data["results"] if r["resource_type"] == "statmech"]
        assert len(items) == 1
        assert items[0]["summary"]["scientific_origin"] == "computed"
        assert items[0]["summary"]["statmech_treatment"] == "rrho"
        assert items[0]["links"]["owner"] == f"/api/v1/species-entries/{entry_id}"

    def test_miss_when_no_statmech(self, client):
        client.post("/api/v1/uploads/conformers",
                    json=_h2_conformer_payload())
        entry_id = _species_entry_id(client, "[H][H]", 1)

        resp = client.get("/api/v1/lookup/statmech",
                          params={"species_entry_id": entry_id})
        data = resp.json()
        assert data["match"]["status"] == "none"
        assert "statmech_none" in data["match"]["detail_codes"]
        assert data["results"] == []

    def test_unknown_species_entry_returns_404(self, client):
        resp = client.get("/api/v1/lookup/statmech",
                          params={"species_entry_id": 999999})
        assert resp.status_code == 404

    def test_append_only_visibility(self, client):
        """Multiple statmech records on the same species entry all surface."""
        payload = _statmech_payload(smiles="N", mult=1)
        r1 = client.post("/api/v1/uploads/statmech", json=payload)
        r2 = client.post("/api/v1/uploads/statmech", json=payload)
        assert r1.status_code == 201 and r2.status_code == 201
        d1, d2 = r1.json(), r2.json()
        assert d1["species_entry_id"] == d2["species_entry_id"]
        assert d1["id"] != d2["id"]

        resp = client.get(
            "/api/v1/lookup/statmech",
            params={"species_entry_id": d1["species_entry_id"]},
        )
        ids = {item["id"] for item in resp.json()["results"]
               if item["resource_type"] == "statmech"}
        assert {d1["id"], d2["id"]}.issubset(ids)

    def test_envelope(self, client):
        client.post("/api/v1/uploads/conformers",
                    json=_h2_conformer_payload())
        entry_id = _species_entry_id(client, "[H][H]", 1)
        resp = client.get("/api/v1/lookup/statmech",
                          params={"species_entry_id": entry_id})
        _envelope_ok(resp.json(), "statmech")


# ---------------------------------------------------------------------------
# /lookup/transport
# ---------------------------------------------------------------------------


class TestTransportLookup:
    def test_hit_by_species_entry(self, client):
        r = client.post("/api/v1/uploads/transport",
                        json=_transport_payload())
        assert r.status_code == 201, r.text
        entry_id = r.json()["species_entry_id"]

        resp = client.get("/api/v1/lookup/transport",
                          params={"species_entry_id": entry_id})
        data = resp.json()
        assert data["match"]["status"] == "exact"
        assert "transport_exists" in data["match"]["detail_codes"]
        items = [r for r in data["results"] if r["resource_type"] == "transport"]
        assert len(items) == 1
        s = items[0]["summary"]
        assert s["sigma_angstrom"] == 2.05
        assert s["epsilon_over_k_k"] == 145.0
        assert s["dipole_debye"] == 0.0
        assert items[0]["links"]["self"].startswith("/api/v1/transport/")
        assert items[0]["links"]["owner"] == f"/api/v1/species-entries/{entry_id}"

    def test_miss_when_no_transport(self, client):
        client.post("/api/v1/uploads/conformers",
                    json=_h2_conformer_payload())
        entry_id = _species_entry_id(client, "[H][H]", 1)

        resp = client.get("/api/v1/lookup/transport",
                          params={"species_entry_id": entry_id})
        data = resp.json()
        assert data["match"]["status"] == "none"
        assert "transport_none" in data["match"]["detail_codes"]
        assert data["results"] == []

    def test_unknown_species_entry_returns_404(self, client):
        resp = client.get("/api/v1/lookup/transport",
                          params={"species_entry_id": 999999})
        assert resp.status_code == 404

    def test_append_only_visibility(self, client):
        payload = _transport_payload(smiles="N", mult=1)
        r1 = client.post("/api/v1/uploads/transport", json=payload)
        r2 = client.post("/api/v1/uploads/transport", json=payload)
        assert r1.status_code == 201 and r2.status_code == 201
        d1, d2 = r1.json(), r2.json()
        assert d1["species_entry_id"] == d2["species_entry_id"]
        assert d1["id"] != d2["id"]

        resp = client.get(
            "/api/v1/lookup/transport",
            params={"species_entry_id": d1["species_entry_id"]},
        )
        ids = {item["id"] for item in resp.json()["results"]
               if item["resource_type"] == "transport"}
        assert {d1["id"], d2["id"]}.issubset(ids)

    def test_envelope(self, client):
        client.post("/api/v1/uploads/conformers",
                    json=_h2_conformer_payload())
        entry_id = _species_entry_id(client, "[H][H]", 1)
        resp = client.get("/api/v1/lookup/transport",
                          params={"species_entry_id": entry_id})
        _envelope_ok(resp.json(), "transport")


# ---------------------------------------------------------------------------
# /lookup/network
# ---------------------------------------------------------------------------


class TestNetworkLookup:
    def _upload_and_member_ids(self, client) -> tuple[int, dict[str, int]]:
        resp = client.post("/api/v1/uploads/networks/pdep",
                           json=_pdep_payload("lookup-net"))
        assert resp.status_code in (200, 201), resp.text
        network_id = resp.json()["id"]
        ids = {
            "C[CH2]": _species_entry_id(client, "C[CH2]", 2),
            "[O][O]": _species_entry_id(client, "[O][O]", 3),
            "CCO[O]": _species_entry_id(client, "CCO[O]", 2),
            "[Ar]": _species_entry_id(client, "[Ar]", 1),
        }
        return network_id, ids

    def test_contains_all_hit(self, client):
        network_id, ids = self._upload_and_member_ids(client)
        # Use two participating species entries from the network
        requested = [ids["C[CH2]"], ids["[O][O]"]]
        resp = client.get("/api/v1/lookup/network",
                          params=[("species_entry_ids", rid) for rid in requested])
        data = resp.json()
        assert data["match"]["status"] == "exact"
        codes = data["match"]["detail_codes"]
        assert "network_membership_contains_all" in codes
        assert "network_exists" in codes
        items = [r for r in data["results"] if r["resource_type"] == "network"]
        assert any(it["id"] == network_id for it in items)
        matched = next(it for it in items if it["id"] == network_id)
        assert matched["summary"]["name"] == "lookup-net"
        assert matched["summary"]["species_count"] >= 4
        assert matched["links"]["self"] == f"/api/v1/networks/{network_id}"

    def test_contains_all_miss_when_extra_unmembered(self, client):
        """Adding a species-entry absent from the network must drop the match."""
        network_id, ids = self._upload_and_member_ids(client)
        # Upload an unrelated species entry and ask contains-all with it
        client.post("/api/v1/uploads/conformers", json=_h2_conformer_payload())
        outsider = _species_entry_id(client, "[H][H]", 1)

        requested = [ids["C[CH2]"], outsider]
        resp = client.get("/api/v1/lookup/network",
                          params=[("species_entry_ids", rid) for rid in requested])
        data = resp.json()
        assert data["match"]["status"] == "none"
        assert "network_none" in data["match"]["detail_codes"]
        assert data["results"] == []

    def test_unknown_species_entry_is_reported(self, client):
        resp = client.get(
            "/api/v1/lookup/network",
            params=[("species_entry_ids", 999998),
                    ("species_entry_ids", 999999)],
        )
        data = resp.json()
        assert data["match"]["status"] == "none"
        assert "species_entry_not_found" in data["match"]["detail_codes"]
        assert data["results"] == []

    def test_envelope(self, client):
        resp = client.get("/api/v1/lookup/network",
                          params=[("species_entry_ids", 999999)])
        _envelope_ok(resp.json(), "network")
