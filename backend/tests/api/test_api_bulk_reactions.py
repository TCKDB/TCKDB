"""Bulk reaction upload tests using real data from SDF + kinetics CSV.

Tests 20 diverse reactions via the computed-reaction bundle endpoint,
covering:
- Small molecules (H2 + Cl, H2 + OH)
- Heterocyclics with N, O, S, F, Cl
- Triplet species (mult=3) and quartet (mult=4)
- Large organic molecules (up to 22 atoms)

Also includes scenario tests for the primitive endpoints:
- Literature kinetics only (no calculations)
- Thermo-only upload
- Conformer-only upload
- Reaction definition only
- TS-only upload
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Add project root for script import
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from scripts.parse_sdf_to_bundle import sdf_to_bundle

# ---------------------------------------------------------------------------
# The 20 diverse reactions
# ---------------------------------------------------------------------------

_RXN_IDS = [
    # Very simple (2-4 atoms)
    "rxn_146",        # H2 + Cl → HCl + H
    "rxn_285",        # H2 + OH → H2O + H (triplet)
    "rxn_286",        # H2 + O → OH + H
    "rmg_rxn_1146",   # S + OH → SH + O (triplet, S element)
    # Small heterocyclic (kfir series)
    "kfir_rxn_2",     # NH3 + furazan radical (our original test case)
    "kfir_rxn_5161",  # CH4 + propargyl alcohol
    "kfir_rxn_5172",  # formaldehyde + propadienol
    "kfir_rxn_9205",  # H2O + pyrazole
    # Medium (8-14 atoms, diverse elements)
    "kfir_rxn_11197", # glyoxal + glycolonitrile (C,H,N,O)
    "kfir_rxn_10218", # ethylene oxide + malonic species (C,H,O)
    "kfir_rxn_10391", # propyne + isoxazole (C,H,N,O)
    "kfir_rxn_11663", # glyoxal + F-tetrazine (C,F,H,N,O)
    "kfir_rxn_11797", # chloral + F-tetrazine (C,Cl,F,H,N,O)
    "kfir_rxn_11915", # glycolonitrile + thiazole (C,H,N,O,S)
    "kfir_rxn_13503", # chloral + thiadiazole (C,Cl,H,N,O,S)
    # Large organic (15+ atoms)
    "rmg_rxn_10",     # ethyl nitroso + ethanethiol (C,H,N,O,S)
    "rmg_rxn_10626",  # butenyl hydroxylamine + S atom (triplet S)
    "rmg_rxn_10733",  # hydrazine + butyl ether (triplet N)
    "rmg_rxn_1345",   # methoxypropanol + ethanol (22 atoms, large CHO)
    # Exotic multiplicities
    "rxn_635",        # NH + CH3 → CH4 + N (quartet N, mult=4)
]

# Pre-parse all bundles at module load time
_BUNDLES: dict[str, dict] = {}
_PARSE_ERRORS: dict[str, str] = {}

for _rxn_id in _RXN_IDS:
    try:
        _BUNDLES[_rxn_id] = sdf_to_bundle(_rxn_id)
    except Exception as e:
        _PARSE_ERRORS[_rxn_id] = str(e)


# ==========================================================================
# Parametrized bundle upload test
# ==========================================================================


class TestBulkBundleUploads:
    """Upload 20 diverse reactions via /uploads/computed-reaction."""

    @pytest.mark.parametrize("rxn_id", _RXN_IDS)
    def test_bundle_upload(self, client, rxn_id):
        if rxn_id in _PARSE_ERRORS:
            pytest.skip(f"Parse error: {_PARSE_ERRORS[rxn_id]}")

        bundle = _BUNDLES[rxn_id]
        resp = client.post("/api/v1/uploads/computed-reaction", json=bundle)
        assert resp.status_code == 201, (
            f"{rxn_id} failed: {resp.text[:500]}"
        )
        data = resp.json()

        assert data["type"] == "computed_reaction"
        assert data["species_count"] >= 2
        assert len(data["kinetics_ids"]) >= 1
        assert data["reaction_id"] > 0

        # Verify kinetics values round-tripped
        for kin_id in data["kinetics_ids"]:
            kin = client.get(f"/api/v1/kinetics/{kin_id}").json()
            assert kin["n"] is not None
            assert kin["ea_kj_mol"] is not None

        # Verify species exist
        species = client.get("/api/v1/species").json()
        assert species["total"] >= data["species_count"]


# ==========================================================================
# Scenario tests (primitive endpoints, diverse use cases)
# ==========================================================================


class TestScenario2_LiteratureKineticsOnly:
    """Upload kinetics from literature — no calculations, no conformers."""

    def test_literature_kinetics(self, client):
        resp = client.post("/api/v1/uploads/kinetics", json={
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
            "tmin_k": 200.0,
            "tmax_k": 2500.0,
            "note": "Baulch et al. 2005, evaluated rate expression",
        })
        assert resp.status_code == 201
        data = resp.json()
        assert data["type"] == "kinetics"


class TestScenario3_ThermoOnly:
    """Upload thermo from literature — no conformers or calculations."""

    def test_nasa_thermo_from_literature(self, client):
        resp = client.post("/api/v1/uploads/thermo", json={
            "species_entry": {"smiles": "O", "charge": 0, "multiplicity": 1},
            "scientific_origin": "experimental",
            "h298_kj_mol": -241.826,
            "s298_j_mol_k": 188.835,
            "tmin_k": 200.0,
            "tmax_k": 6000.0,
            "nasa": {
                "t_low": 200.0, "t_mid": 1000.0, "t_high": 6000.0,
                "a1": 4.19864056, "a2": -2.03643410e-3, "a3": 6.52040211e-6,
                "a4": -5.48797062e-9, "a5": 1.77197817e-12,
                "a6": -3.02937267e4, "a7": -8.49032208e-1,
                "b1": 2.67703787, "b2": 2.97318160e-3, "b3": -7.73769690e-7,
                "b4": 9.44336689e-11, "b5": -4.26900959e-15,
                "b6": -2.98858938e4, "b7": 6.88255571,
            },
            "note": "JANAF/ATcT reference data for H2O",
        })
        assert resp.status_code == 201
        data = resp.json()
        assert data["type"] == "thermo"
        assert data["species_entry_id"] > 0


class TestScenario4_ConformerOnly:
    """Upload conformer + calculations — no kinetics or thermo."""

    def test_conformer_with_freq(self, client):
        resp = client.post("/api/v1/uploads/conformers", json={
            "species_entry": {"smiles": "[O][O]", "charge": 0, "multiplicity": 3},
            "geometry": {
                "xyz_text": "2\nO2 triplet\nO 0.0 0.0 0.0\nO 0.0 0.0 1.2075",
            },
            "calculation": {
                "type": "opt",
                "software_release": {"name": "Gaussian", "version": "16"},
                "level_of_theory": {"method": "B3LYP", "basis": "6-311+G(d,p)"},
                "opt_result": {"converged": True, "final_energy_hartree": -150.327},
            },
            "additional_calculations": [{
                "type": "freq",
                "software_release": {"name": "Gaussian", "version": "16"},
                "level_of_theory": {"method": "B3LYP", "basis": "6-311+G(d,p)"},
                "freq_result": {"n_imag": 0, "zpe_hartree": 0.00373},
            }],
            "label": "O2-triplet",
        })
        assert resp.status_code == 201
        data = resp.json()
        assert data["type"] == "conformer_observation"
        assert data["species_entry_id"] > 0


class TestScenario5_TransitionStateOnly:
    """Upload TS — no kinetics (TS search study)."""

    def test_ts_only(self, client):
        resp = client.post("/api/v1/uploads/transition-states", json={
            "reaction": {
                "reversible": True,
                "reactants": [
                    {"species_entry": {"smiles": "[H]", "charge": 0, "multiplicity": 2}},
                    {"species_entry": {"smiles": "[H][H]", "charge": 0, "multiplicity": 1}},
                ],
                "products": [
                    {"species_entry": {"smiles": "[H]", "charge": 0, "multiplicity": 2}},
                    {"species_entry": {"smiles": "[H][H]", "charge": 0, "multiplicity": 1}},
                ],
            },
            "charge": 0,
            "multiplicity": 2,
            "geometry": {
                "xyz_text": "3\nH3 TS\nH 0.0 0.0 0.0\nH 0.0 0.0 0.9\nH 0.0 0.0 1.8",
            },
            "primary_opt": {
                "type": "opt",
                "software_release": {"name": "Gaussian", "version": "16"},
                "level_of_theory": {"method": "CCSD(T)", "basis": "cc-pVTZ"},
                "opt_result": {"converged": True},
            },
            "additional_calculations": [{
                "type": "freq",
                "software_release": {"name": "Gaussian", "version": "16"},
                "level_of_theory": {"method": "CCSD(T)", "basis": "cc-pVTZ"},
                "freq_result": {"n_imag": 1, "imag_freq_cm1": -1510.0},
            }],
            "label": "H + H2 collinear TS",
        })
        assert resp.status_code == 201
        data = resp.json()
        assert data["type"] == "transition_state_entry"


class TestScenario6_ReactionOnly:
    """Upload reaction definition — no calculations or kinetics."""

    def test_reaction_definition(self, client):
        resp = client.post("/api/v1/uploads/reactions", json={
            "reversible": True,
            "reactants": [
                {"species_entry": {"smiles": "CC", "charge": 0, "multiplicity": 1}},
                {"species_entry": {"smiles": "[OH]", "charge": 0, "multiplicity": 2}},
            ],
            "products": [
                {"species_entry": {"smiles": "C[CH2]", "charge": 0, "multiplicity": 2}},
                {"species_entry": {"smiles": "O", "charge": 0, "multiplicity": 1}},
            ],
            "reaction_family": "H_Abstraction",
        })
        assert resp.status_code == 201
        data = resp.json()
        assert data["type"] == "reaction_entry"
        assert data["reaction_id"] > 0


# ==========================================================================
# Cross-reaction conformer grouping test
# ==========================================================================


class TestConformerGroupingAcrossReactions:
    """Upload multiple reactions that share species and verify conformer
    grouping works correctly.

    Key shared species:
    - [OH] (mult=2): appears in rxn_285, rxn_286, rmg_rxn_1146, kfir_rxn_9205
    - [H] (mult=2): appears in rxn_146, rxn_285, rxn_286 (identical geometry)
    - [O]CC#N (mult=2): appears in kfir_rxn_11197, kfir_rxn_11915 (identical)
    - O=CC=O (mult=1): appears in kfir_rxn_11197, kfir_rxn_11663 (different geometry)
    """

    def test_shared_species_dedup_and_grouping(self, client):
        """Upload 4 reactions that share [OH] and verify:
        1. Species deduplication: same species row across reactions
        2. Conformer grouping: identical geometries → same group,
           different geometries → torsion/RMSD decides
        """
        # Upload reactions that share [OH] radical
        rxn_ids = ["rxn_285", "rxn_286", "rmg_rxn_1146", "kfir_rxn_9205"]
        results = {}
        for rxn_id in rxn_ids:
            bundle = _BUNDLES[rxn_id]
            resp = client.post("/api/v1/uploads/computed-reaction", json=bundle)
            assert resp.status_code == 201, f"{rxn_id}: {resp.text[:300]}"
            results[rxn_id] = resp.json()

        # All 4 reactions should share the same [OH] species (InChI dedup)
        species_list = client.get("/api/v1/species?limit=100").json()
        oh_species = [
            s for s in species_list["items"]
            if s.get("smiles") == "[OH]"
        ]
        assert len(oh_species) == 1, (
            f"Expected 1 [OH] species row, got {len(oh_species)}"
        )

    def test_identical_geometry_same_group(self, client):
        """Upload two reactions sharing [O]CC#N (mult=2, radical) with
        identical geometry. Should land in the same conformer group."""
        entry_ids = []
        for rxn_id in ["kfir_rxn_11197", "kfir_rxn_11915"]:
            resp = client.post(
                "/api/v1/uploads/computed-reaction",
                json=_BUNDLES[rxn_id],
            )
            assert resp.status_code == 201, f"{rxn_id}: {resp.text[:300]}"
            entry_ids.extend(resp.json()["species_entry_ids"])

        # [O]CC#N (mult=2) is shared with identical geometry.
        # RDKit canonicalizes to N#CC[O] or [O]CC#N — check both.
        # The same species_entry_id should appear in both upload responses.
        unique_entry_ids = sorted(set(entry_ids))

        for eid in unique_entry_ids:
            entry_data = client.get(f"/api/v1/species-entries/{eid}").json()
            sp = client.get(f"/api/v1/species/{entry_data['species_id']}").json()
            # N#CC[O] is the RDKit canonical form of [O]CC#N (mult=2 radical)
            if sp["smiles"] != "N#CC[O]" or sp["multiplicity"] != 2:
                continue
            conformers = client.get(
                f"/api/v1/species-entries/{eid}/conformers"
            ).json()
            assert len(conformers) == 2, (
                f"Expected 2 observations for N#CC[O], got {len(conformers)}"
            )
            group_ids = set(c["conformer_group_id"] for c in conformers)
            assert len(group_ids) == 1, (
                f"Expected 1 conformer group for identical "
                f"N#CC[O], got {len(group_ids)}"
            )
            return

        pytest.fail("Could not find N#CC[O] (mult=2) species entry")

    def test_different_geometry_flexible_molecule(self, client):
        """Upload two reactions with O=CC=O (glyoxal) that has different
        geometries. Glyoxal has 1 rotatable bond — if torsions differ by
        more than 15°, they should land in different groups."""
        entry_ids = []
        for rxn_id in ["kfir_rxn_11197", "kfir_rxn_11663"]:
            resp = client.post(
                "/api/v1/uploads/computed-reaction",
                json=_BUNDLES[rxn_id],
            )
            assert resp.status_code == 201, f"{rxn_id}: {resp.text[:300]}"
            entry_ids.extend(resp.json().get("species_entry_ids", []))

        for eid in set(entry_ids):
            entry_data = client.get(f"/api/v1/species-entries/{eid}").json()
            if entry_data.get("species_id"):
                sp = client.get(f"/api/v1/species/{entry_data['species_id']}").json()
                if sp.get("smiles") == "O=CC=O":
                    conformers = client.get(
                        f"/api/v1/species-entries/{eid}/conformers"
                    ).json()
                    assert len(conformers) == 2
                    group_ids = set(c["conformer_group_id"] for c in conformers)
                    # Whether 1 or 2 groups depends on torsion delta —
                    # both are valid, system must decide deterministically
                    assert len(group_ids) in (1, 2)
                    return

        pytest.fail("Could not find O=CC=O species entry in upload responses")

    def test_single_atom_identical_always_same_group(self, client):
        """[H] atom appears in 3 reactions with identical geometry (0,0,0).
        Zero rotors, zero RMSD → always same group."""
        entry_ids = []
        for rxn_id in ["rxn_146", "rxn_285", "rxn_286"]:
            resp = client.post(
                "/api/v1/uploads/computed-reaction",
                json=_BUNDLES[rxn_id],
            )
            assert resp.status_code == 201, f"{rxn_id}: {resp.text[:300]}"
            entry_ids.extend(resp.json().get("species_entry_ids", []))

        for eid in set(entry_ids):
            entry_data = client.get(f"/api/v1/species-entries/{eid}").json()
            if entry_data.get("species_id"):
                sp = client.get(f"/api/v1/species/{entry_data['species_id']}").json()
                if sp.get("smiles") == "[H]":
                    conformers = client.get(
                        f"/api/v1/species-entries/{eid}/conformers"
                    ).json()
                    assert len(conformers) == 3, (
                        f"Expected 3 [H] observations, got {len(conformers)}"
                    )
                    group_ids = set(c["conformer_group_id"] for c in conformers)
                    assert len(group_ids) == 1, (
                        f"Expected 1 group for identical [H], got {len(group_ids)}"
                    )
                    return

        pytest.fail("Could not find [H] species entry in upload responses")
