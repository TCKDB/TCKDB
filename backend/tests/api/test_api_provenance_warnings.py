"""End-to-end API tests for provenance enforcement and warnings.

Covers the three scenarios from the provenance-enforcement spec, for
each of the four high-value upload workflows (thermo, transport,
statmech, kinetics):

1. **Provided provenance persists** — resolved FK ids land on the new
   row (read via the public read endpoints where available, and via the
   response envelope otherwise).
2. **Missing provenance succeeds with structured warnings** — the
   upload still succeeds, but the response surfaces the absence via
   ``UploadWarning`` entries with the shared shape.
3. **Invalid / unresolvable provenance fails clearly** — malformed
   provenance fragments are rejected at the API boundary (HTTP 422),
   never silently dropped into NULL FK columns.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

# ---------------------------------------------------------------------------
# Request factories
# ---------------------------------------------------------------------------


_SOFTWARE = {"name": "Gaussian", "version": "16"}
_WTR = {"name": "ARC", "version": "1.2.0"}
_LOT = {"method": "B3LYP", "basis": "6-31G(d)"}
_FSF = {
    "level_of_theory": _LOT,
    "scale_kind": "fundamental",
    "value": 0.97,
}


def _thermo_payload(**overrides) -> dict:
    base: dict = {
        "species_entry": {"smiles": "[H]", "charge": 0, "multiplicity": 2},
        "scientific_origin": "computed",
        "h298_kj_mol": 217.998,
    }
    base.update(overrides)
    return base


def _transport_payload(**overrides) -> dict:
    base: dict = {
        "species_entry": {"smiles": "[H]", "charge": 0, "multiplicity": 2},
        "scientific_origin": "computed",
        "sigma_angstrom": 2.05,
        "epsilon_over_k_k": 145.0,
    }
    base.update(overrides)
    return base


def _statmech_payload(**overrides) -> dict:
    base: dict = {
        "species_entry": {"smiles": "[H]", "charge": 0, "multiplicity": 2},
        "scientific_origin": "computed",
        "statmech_treatment": "rrho",
        "external_symmetry": 1,
    }
    base.update(overrides)
    return base


def _kinetics_payload(**overrides) -> dict:
    base: dict = {
        "reaction": {
            "reversible": False,
            "reactants": [
                {"species_entry": {"smiles": "[H]", "charge": 0, "multiplicity": 2}},
                {"species_entry": {"smiles": "[H]", "charge": 0, "multiplicity": 2}},
            ],
            "products": [
                {"species_entry": {"smiles": "[H][H]", "charge": 0, "multiplicity": 1}},
            ],
        },
        "scientific_origin": "computed",
        "model_kind": "modified_arrhenius",
        "a": 1.23e12,
        "a_units": "cm3_mol_s",
        "n": 0.0,
        "reported_ea": 10.0,
        "reported_ea_units": "kj_mol",
    }
    base.update(overrides)
    return base


def _codes(data: dict) -> set[str]:
    return {w["code"] for w in data.get("warnings", [])}


# ---------------------------------------------------------------------------
# Test 1. Provided provenance persists without spurious warnings
# ---------------------------------------------------------------------------


class TestProvenanceProvidedPersists:
    def test_thermo_with_full_provenance_persists_ids_and_no_warnings(
        self, client: TestClient
    ) -> None:
        payload = _thermo_payload(
            software_release=dict(_SOFTWARE),
            workflow_tool_release=dict(_WTR),
        )
        resp = client.post("/api/v1/uploads/thermo", json=payload)
        assert resp.status_code == 201, resp.text
        data = resp.json()

        # No missing-provenance warnings on a fully specified payload.
        assert _codes(data) == set()

        # The resolved FK ids actually landed on the persisted row.
        from app.db.models.thermo import Thermo
        thermo = client._db_session.get(Thermo, data["id"])
        assert thermo is not None
        assert thermo.software_release_id is not None
        assert thermo.workflow_tool_release_id is not None

    def test_transport_with_full_provenance_persists_ids(
        self, client: TestClient
    ) -> None:
        payload = _transport_payload(
            species_entry={"smiles": "N", "charge": 0, "multiplicity": 1},
            software_release=dict(_SOFTWARE),
            workflow_tool_release=dict(_WTR),
        )
        resp = client.post("/api/v1/uploads/transport", json=payload)
        assert resp.status_code == 201, resp.text
        data = resp.json()
        assert _codes(data) == set()

        from app.db.models.transport import Transport
        transport = client._db_session.get(Transport, data["id"])
        assert transport is not None
        assert transport.software_release_id is not None
        assert transport.workflow_tool_release_id is not None

    def test_statmech_with_full_provenance_persists_ids(
        self, client: TestClient
    ) -> None:
        payload = _statmech_payload(
            species_entry={"smiles": "CO", "charge": 0, "multiplicity": 1},
            software_release=dict(_SOFTWARE),
            workflow_tool_release=dict(_WTR),
            freq_scale_factor=dict(_FSF),
        )
        resp = client.post("/api/v1/uploads/statmech", json=payload)
        assert resp.status_code == 201, resp.text
        data = resp.json()
        assert _codes(data) == set()

        from app.db.models.statmech import Statmech
        statmech = client._db_session.get(Statmech, data["id"])
        assert statmech is not None
        assert statmech.software_release_id is not None
        assert statmech.workflow_tool_release_id is not None
        assert statmech.frequency_scale_factor_id is not None

    def test_kinetics_with_full_provenance_has_no_warnings(
        self, client: TestClient
    ) -> None:
        """With a declared energy_level_of_theory, the kinetics workflow
        auto-resolves reactant and product SP calculations. Each
        participant here is a distinct species entry so the SP auto-
        links do not collide on ``(kinetics_id, calculation_id, role)``."""
        def _conformer(smiles: str, multiplicity: int, xyz: str) -> dict:
            return {
                "species_entry": {
                    "smiles": smiles,
                    "charge": 0,
                    "multiplicity": multiplicity,
                },
                "geometry": {"xyz_text": xyz},
                "calculation": {
                    "type": "sp",
                    "software_release": dict(_SOFTWARE),
                    "level_of_theory": dict(_LOT),
                },
                "label": f"{smiles}-sp",
            }

        # Seed SPs for H, OH, and H2O at the declared LOT
        for payload in (
            _conformer("[H]", 2, "1\nH atom\nH 0.0 0.0 0.0"),
            _conformer("[OH]", 2, "2\nOH\nO 0.0 0.0 0.0\nH 0.0 0.0 0.96"),
            _conformer("O", 1, "3\nH2O\nO 0.0 0.0 0.0\nH 0.76 0.0 0.59\nH -0.76 0.0 0.59"),
        ):
            r = client.post("/api/v1/uploads/conformers", json=payload)
            assert r.status_code == 201, r.text

        payload = _kinetics_payload(
            reaction={
                "reversible": False,
                "reactants": [
                    {"species_entry": {"smiles": "[H]", "charge": 0, "multiplicity": 2}},
                    {"species_entry": {"smiles": "[OH]", "charge": 0, "multiplicity": 2}},
                ],
                "products": [
                    {"species_entry": {"smiles": "O", "charge": 0, "multiplicity": 1}},
                ],
            },
            software_release=dict(_SOFTWARE),
            workflow_tool_release=dict(_WTR),
            energy_level_of_theory=dict(_LOT),
        )
        resp = client.post("/api/v1/uploads/kinetics", json=payload)
        assert resp.status_code == 201, resp.text
        data = resp.json()
        # Species-entry reconciliation may emit layer-1 warnings, but
        # none of those should be missing-provenance codes.
        missing = {c for c in _codes(data) if c.startswith("missing_")}
        assert missing == set()


# ---------------------------------------------------------------------------
# Test 2. Missing provenance → structured warnings, 201 still returned
# ---------------------------------------------------------------------------


class TestProvenanceMissingEmitsWarnings:
    def test_thermo_missing_all_provenance_emits_warnings(
        self, client: TestClient
    ) -> None:
        resp = client.post(
            "/api/v1/uploads/thermo", json=_thermo_payload()
        )
        assert resp.status_code == 201
        assert _codes(resp.json()) == {
            "missing_software_release_provenance",
            "missing_workflow_tool_provenance",
        }

    def test_transport_missing_all_provenance_emits_warnings(
        self, client: TestClient
    ) -> None:
        resp = client.post(
            "/api/v1/uploads/transport", json=_transport_payload()
        )
        assert resp.status_code == 201
        assert _codes(resp.json()) == {
            "missing_software_release_provenance",
            "missing_workflow_tool_provenance",
        }

    def test_statmech_missing_all_provenance_emits_warnings(
        self, client: TestClient
    ) -> None:
        resp = client.post(
            "/api/v1/uploads/statmech", json=_statmech_payload()
        )
        assert resp.status_code == 201
        assert _codes(resp.json()) == {
            "missing_software_release_provenance",
            "missing_workflow_tool_provenance",
            "missing_frequency_scale_factor_provenance",
        }

    def test_kinetics_missing_all_provenance_emits_warnings(
        self, client: TestClient
    ) -> None:
        resp = client.post(
            "/api/v1/uploads/kinetics", json=_kinetics_payload()
        )
        assert resp.status_code == 201, resp.text
        # Kinetics may also carry species-entry reconciliation warnings;
        # filter to just the provenance codes.
        missing = {c for c in _codes(resp.json()) if c.startswith("missing_")}
        assert missing == {
            "missing_software_release_provenance",
            "missing_workflow_tool_provenance",
            "missing_level_of_theory_provenance",
        }

    def test_warning_shape_matches_shared_pattern(
        self, client: TestClient
    ) -> None:
        """Warnings use the same ``{field, code, message}`` shape as the
        rest of the upload reconciliation system — no bespoke fields."""
        resp = client.post("/api/v1/uploads/thermo", json=_thermo_payload())
        assert resp.status_code == 201
        warnings = resp.json()["warnings"]
        assert warnings, "expected provenance warnings to be present"
        for w in warnings:
            assert set(w.keys()) == {"field", "code", "message"}
            assert w["code"].startswith("missing_")
            assert w["message"]


# ---------------------------------------------------------------------------
# Test 3. Invalid / malformed provenance fails clearly (no silent NULL)
# ---------------------------------------------------------------------------


class TestInvalidProvenanceFailsClearly:
    def test_thermo_empty_software_name_returns_422(
        self, client: TestClient
    ) -> None:
        payload = _thermo_payload(software_release={"name": "", "version": "16"})
        resp = client.post("/api/v1/uploads/thermo", json=payload)
        assert resp.status_code == 422

    def test_transport_empty_workflow_tool_name_returns_422(
        self, client: TestClient
    ) -> None:
        payload = _transport_payload(workflow_tool_release={"name": ""})
        resp = client.post("/api/v1/uploads/transport", json=payload)
        assert resp.status_code == 422

    def test_statmech_empty_freq_scale_method_returns_422(
        self, client: TestClient
    ) -> None:
        bad_fsf = {
            "level_of_theory": {"method": ""},  # violates min_length=1
            "scale_kind": "fundamental",
            "value": 0.97,
        }
        payload = _statmech_payload(freq_scale_factor=bad_fsf)
        resp = client.post("/api/v1/uploads/statmech", json=payload)
        assert resp.status_code == 422

    def test_kinetics_empty_lot_method_returns_422(
        self, client: TestClient
    ) -> None:
        payload = _kinetics_payload(
            energy_level_of_theory={"method": ""},
        )
        resp = client.post("/api/v1/uploads/kinetics", json=payload)
        assert resp.status_code == 422

    def test_thermo_missing_required_literature_field_returns_422(
        self, client: TestClient
    ) -> None:
        """A literature fragment that neither carries a DOI nor ISBN nor
        title cannot be resolved — the upload must fail rather than
        persist a NULL literature_id as though nothing had been supplied."""
        payload = _thermo_payload(literature={})
        resp = client.post("/api/v1/uploads/thermo", json=payload)
        assert resp.status_code == 422
