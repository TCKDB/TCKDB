"""Same kfir_rxn_2 data as test_api_kfir_rxn.py, but uploaded via primitive
endpoints (Layer A) rather than the computed-reaction bundle (Layer B).

Demonstrates that the primitive endpoints compose correctly when the client
manages the upload order and cross-references (using returned IDs from
each response to feed into subsequent uploads).

Reaction (H-abstraction):
    NH3 + [N]=c1onno1  →  [NH2] + N=c1onno1(H)
"""

from __future__ import annotations

import pytest

_CALMOLK_TO_JMOLK = 4.184
_KJMOL_TO_HARTREE = 1.0 / 2625.5

_LOT_DFT = {"method": "wb97xd", "basis": "def2tzvp"}
_LOT_DLPNO = {"method": "DLPNO-CCSD(T)", "basis": "cc-pVTZ"}
_SOFTWARE_GAUSSIAN = {"name": "Gaussian", "version": "16"}
_SOFTWARE_ORCA = {"name": "ORCA", "version": "5.0"}


def _nasa(c1, t1min, t1max, c2, t2min, t2max):
    return {
        "t_low": t1min, "t_mid": t1max, "t_high": t2max,
        "a1": c1[0], "a2": c1[1], "a3": c1[2], "a4": c1[3],
        "a5": c1[4], "a6": c1[5], "a7": c1[6],
        "b1": c2[0], "b2": c2[1], "b3": c2[2], "b4": c2[3],
        "b5": c2[4], "b6": c2[5], "b7": c2[6],
    }


# -- Species data (same as test_api_kfir_rxn.py) ----------------------------

_SPECIES = [
    {
        "smiles": "N", "mult": 1, "label": "nh3-opt",
        "xyz": "4\nNH3\nN 0.0028 -0.0636 0.0360\nH 0.1884 0.8118 0.5247\nH -0.9230 0.0102 -0.3848\nH 0.6570 -0.1245 -0.7436",
        "e_elec": -148510.90647055782,
        "thermo": {
            "h298_kj_mol": -148411.0,
            "s298_j_mol_k": 45.96257860853203 * _CALMOLK_TO_JMOLK,
            "tmin_k": 10.0, "tmax_k": 3000.0,
            "nasa": _nasa(
                [4.018066180041628, -0.0010594558724668145, 7.006951846844162e-06,
                 -1.9450178277231155e-09, -1.690259773259874e-12,
                 -17850955.574752882, 0.26302757549391453],
                10.0, 661.0840282867927,
                [2.4516463520951244, 0.005556487715444479, -1.5108778746433696e-06,
                 9.615490736155179e-11, 1.429454369866963e-14,
                 -17850685.929684434, 7.644792990222567],
                661.0840282867927, 3000.0,
            ),
        },
    },
    {
        "smiles": "[N]=c1onno1", "mult": 2, "label": "furazan-rad-opt",
        "xyz": "6\nfurazan radical\nN 1.9594 0.0134 -0.3734\nC 0.7437 0.0051 -0.0788\nO -0.0745 1.0868 0.0233\nN -1.3223 0.5972 0.3682\nN -1.2899 -0.6150 0.4676\nO -0.0165 -1.0874 0.2015",
        "e_elec": -925831.9516442568,
        "thermo": {
            "h298_kj_mol": -925752.0,
            "s298_j_mol_k": 69.02430482099071 * _CALMOLK_TO_JMOLK,
            "tmin_k": 10.0, "tmax_k": 3000.0,
            "nasa": _nasa(
                [3.9482722177880523, 0.00286399391777489, 6.607304666914822e-05,
                 -1.1888758586418805e-07, 6.356745631073056e-11,
                 -111343943.33130501, 9.376822962946171],
                10.0, 615.6012172942251,
                [2.449527079036138, 0.025184330488228168, -1.8971272122591413e-05,
                 6.4121015114796695e-09, -8.006937785638129e-13,
                 -111343997.21073204, 13.943915403155506],
                615.6012172942251, 3000.0,
            ),
        },
    },
    {
        "smiles": "[NH2]", "mult": 2, "label": "nh2-opt",
        "xyz": "3\nNH2 radical\nN 0.0002 0.4233 0.0000\nH -0.8051 -0.2112 0.0000\nH 0.8049 -0.2121 0.0000",
        "e_elec": -146715.64756148277,
        "thermo": {
            "h298_kj_mol": -146656.0,
            "s298_j_mol_k": 46.48569697774424 * _CALMOLK_TO_JMOLK,
            "tmin_k": 10.0, "tmax_k": 3000.0,
            "nasa": _nasa(
                [4.006151608375513, -0.0003084801043868945, 1.192087457245553e-06,
                 1.4831713626345916e-09, -1.3499457570330705e-12,
                 -17639824.212463364, 0.5976382433244958],
                10.0, 785.7865750647027,
                [3.2706173092735047, 0.0018201245801067914, 2.127841353634995e-07,
                 -3.024868389204131e-10, 5.0613927895422387e-14,
                 -17639658.73974233, 4.2862278156336115],
                785.7865750647027, 3000.0,
            ),
        },
    },
    {
        "smiles": "N=c1onno1", "mult": 1, "label": "furazan-h-opt",
        "xyz": "7\nN=c1onno1 with H\nN 0.2531 -1.2243 1.5746\nC 0.0931 -0.4846 0.5919\nO 0.2132 0.8791 0.6089\nN -0.0374 1.3282 -0.6635\nN -0.2861 0.3915 -1.4071\nO -0.2281 -0.7956 -0.7055\nH 0.1287 -2.2435 1.4095",
        "e_elec": -927613.1854967066,
        "thermo": {
            "h298_kj_mol": -927500.0,
            "s298_j_mol_k": 69.21240690289005 * _CALMOLK_TO_JMOLK,
            "tmin_k": 10.0, "tmax_k": 3000.0,
            "nasa": _nasa(
                [4.064132331384431, -0.006464318285740491, 0.0001260927686818599,
                 -2.245288884010203e-07, 1.2483214386195704e-10,
                 -111554092.36130533, 9.737352080288133],
                10.0, 594.5444576074173,
                [2.6618231118848854, 0.027000979186583906, -1.8966399167901696e-05,
                 6.10976288698157e-09, -7.352069559300073e-13,
                 -111554350.33811045, 12.201696490273036],
                594.5444576074173, 3000.0,
            ),
        },
    },
]


class TestKfirRxn2Primitives:
    """Same data as TestKfirRxn2Bundle, uploaded via primitive endpoints.

    Demonstrates Layer A composability: each upload returns IDs that feed
    into subsequent uploads. The client manages orchestration.
    """

    def test_primitive_upload_workflow(self, client):
        entry_ids = {}

        # ----------------------------------------------------------------
        # 1. Conformers: primary opt (DFT) + DLPNO SP on same geometry
        # ----------------------------------------------------------------
        for sp in _SPECIES:
            resp = client.post("/api/v1/uploads/conformers", json={
                "species_entry": {
                    "smiles": sp["smiles"],
                    "charge": 0,
                    "multiplicity": sp["mult"],
                },
                "geometry": {"xyz_text": sp["xyz"]},
                "calculation": {
                    "type": "opt",
                    "software_release": _SOFTWARE_GAUSSIAN,
                    "level_of_theory": _LOT_DFT,
                    "opt_result": {"converged": True},
                },
                "additional_calculations": [{
                    "type": "sp",
                    "software_release": _SOFTWARE_ORCA,
                    "level_of_theory": _LOT_DLPNO,
                    "sp_result": {
                        "electronic_energy_hartree": sp["e_elec"] * _KJMOL_TO_HARTREE,
                    },
                }],
                "label": sp["label"],
            })
            assert resp.status_code == 201, resp.text
            entry_ids[sp["smiles"]] = resp.json()["species_entry_id"]

        assert len(set(entry_ids.values())) == 4

        # ----------------------------------------------------------------
        # 2. Thermo (references species by SMILES — dedup finds same entry)
        # ----------------------------------------------------------------
        for sp in _SPECIES:
            resp = client.post("/api/v1/uploads/thermo", json={
                "species_entry": {
                    "smiles": sp["smiles"],
                    "charge": 0,
                    "multiplicity": sp["mult"],
                },
                "scientific_origin": "computed",
                **sp["thermo"],
            })
            assert resp.status_code == 201, resp.text
            # Verify it attached to the same species entry
            assert resp.json()["species_entry_id"] == entry_ids[sp["smiles"]]

        # ----------------------------------------------------------------
        # 3. Transition state (embeds reaction by SMILES)
        # ----------------------------------------------------------------
        ts_resp = client.post("/api/v1/uploads/transition-states", json={
            "reaction": {
                "reversible": True,
                "reactants": [
                    {"species_entry": {"smiles": "N", "charge": 0, "multiplicity": 1}},
                    {"species_entry": {"smiles": "[N]=c1onno1", "charge": 0, "multiplicity": 2}},
                ],
                "products": [
                    {"species_entry": {"smiles": "[NH2]", "charge": 0, "multiplicity": 2}},
                    {"species_entry": {"smiles": "N=c1onno1", "charge": 0, "multiplicity": 1}},
                ],
            },
            "charge": 0,
            "multiplicity": 2,
            "unmapped_smiles": "[N]=c1onno1.N",
            "geometry": {
                "xyz_text": (
                    "10\nTS kfir_rxn_2\n"
                    "N 0.2264 -0.7425 1.4864\nC 0.0664 -0.0028 0.5037\n"
                    "O 0.1865 1.3608 0.5207\nN -0.0641 1.8099 -0.7517\n"
                    "N -0.3128 0.8733 -1.4952\nO -0.2548 -0.3138 -0.7937\n"
                    "N -0.1466 -3.0163 0.6977\nH 0.0784 -1.9554 1.2900\n"
                    "H -1.0724 -2.9425 0.2769\nH 0.5077 -3.0773 -0.0819"
                ),
            },
            "primary_opt": {
                "type": "opt",
                "software_release": _SOFTWARE_GAUSSIAN,
                "level_of_theory": _LOT_DFT,
                "opt_result": {
                    "converged": True,
                    "final_energy_hartree": -1074160.0 * _KJMOL_TO_HARTREE,
                },
            },
            "additional_calculations": [{
                "type": "freq",
                "software_release": _SOFTWARE_GAUSSIAN,
                "level_of_theory": _LOT_DFT,
                "freq_result": {"n_imag": 1, "imag_freq_cm1": -1760.3933},
            }],
            "label": "kfir_rxn_2 TS",
        })
        assert ts_resp.status_code == 201, ts_resp.text
        ts_data = ts_resp.json()
        ts_reaction_entry_id = ts_data["reaction_entry_id"]

        # ----------------------------------------------------------------
        # 4. Kinetics (3 fits — forward and reverse, with energy LOT)
        #    Each kinetics upload creates its own reaction entry via the
        #    embedded reaction content.
        # ----------------------------------------------------------------
        _fwd_reactants = [
            {"species_entry": {"smiles": "N", "charge": 0, "multiplicity": 1}},
            {"species_entry": {"smiles": "[N]=c1onno1", "charge": 0, "multiplicity": 2}},
        ]
        _fwd_products = [
            {"species_entry": {"smiles": "[NH2]", "charge": 0, "multiplicity": 2}},
            {"species_entry": {"smiles": "N=c1onno1", "charge": 0, "multiplicity": 1}},
        ]

        kinetics_ids = []
        for kin_data in [
            {
                "reactants": _fwd_products, "products": _fwd_reactants,
                "a": 85.6882, "n": 3.14758, "ea": 28.9767,
                "tunneling": None, "note": "k_rev (TST)",
            },
            {
                "reactants": _fwd_products, "products": _fwd_reactants,
                "a": 0.00161525, "n": 4.47077, "ea": 12.914,
                "tunneling": "Eckart", "note": "k_rev (TST+T)",
            },
            {
                "reactants": _fwd_reactants, "products": _fwd_products,
                "a": 0.000352836, "n": 4.75152, "ea": 16.6648,
                "tunneling": "Eckart", "note": "k_for (TST+T)",
            },
        ]:
            resp = client.post("/api/v1/uploads/kinetics", json={
                "reaction": {
                    "reversible": True,
                    "reactants": kin_data["reactants"],
                    "products": kin_data["products"],
                },
                "scientific_origin": "computed",
                "energy_level_of_theory": _LOT_DLPNO,
                "a": kin_data["a"],
                "a_units": "cm3_mol_s",
                "n": kin_data["n"],
                "reported_ea": kin_data["ea"],
                "reported_ea_units": "kj_mol",
                "tmin_k": 300.0,
                "tmax_k": 2500.0,
                "tunneling_model": kin_data["tunneling"],
                "software_release": {"name": "Arkane", "version": "3.0"},
                "note": kin_data["note"],
            })
            assert resp.status_code == 201, resp.text
            kinetics_ids.append(resp.json()["id"])

        assert len(kinetics_ids) == 3

        # ----------------------------------------------------------------
        # 5. Verify everything links together
        # ----------------------------------------------------------------

        # All 4 species exist
        species = client.get("/api/v1/species").json()
        assert species["total"] >= 4

        # Each species entry has conformers and thermo
        for smiles, eid in entry_ids.items():
            confs = client.get(f"/api/v1/species-entries/{eid}/conformers").json()
            assert len(confs) >= 1, f"No conformers for {smiles}"

            thermo = client.get(f"/api/v1/species-entries/{eid}/thermo").json()
            assert len(thermo) >= 1, f"No thermo for {smiles}"

        # TS is linked to a reaction entry
        ts_read = client.get(
            f"/api/v1/transition-states/{ts_data['transition_state_id']}"
        ).json()
        assert ts_read["reaction_entry_id"] == ts_reaction_entry_id

        # All 3 kinetics records exist
        all_kin = client.get("/api/v1/kinetics").json()
        assert all_kin["total"] >= 3

        # Forward kinetics values round-tripped correctly
        fwd_kin = client.get(f"/api/v1/kinetics/{kinetics_ids[2]}").json()
        assert fwd_kin["n"] == pytest.approx(4.75152)
        assert fwd_kin["ea_kj_mol"] == pytest.approx(16.6648, abs=0.01)
        assert fwd_kin["tunneling_model"] == "Eckart"

        # NH3 thermo NASA coefficients survived
        nh3_thermo = client.get(
            f"/api/v1/species-entries/{entry_ids['N']}/thermo"
        ).json()
        assert len(nh3_thermo) == 1
        nasa = nh3_thermo[0].get("nasa")
        assert nasa is not None
        assert nasa["a1"] == pytest.approx(4.018066, abs=1e-4)

        # Graph-level reaction exists (shared between TS and kinetics)
        reactions = client.get("/api/v1/reactions").json()
        assert reactions["total"] >= 1

        # DLPNO SP auto-resolved by energy_level_of_theory — this proves:
        # (a) the SP was created on the correct species_entry
        # (b) it was findable by (species, type=sp, LOT=DLPNO)
        # The kinetics upload in step 4 would have failed if any SP was
        # missing or ambiguous.

        # Verify the DLPNO SP is also anchored to the conformer observation
        # (not just floating on the species entry). We can check this by
        # querying the calculation directly via the DB session.
        from sqlalchemy import select
        from app.db.models.calculation import Calculation
        from app.db.models.common import CalculationType

        # The client fixture's session is accessible through the dependency override
        from app.api.deps import get_write_db
        test_session = client.app.dependency_overrides[get_write_db]()

        for eid in entry_ids.values():
            dlpno_calcs = test_session.scalars(
                select(Calculation).where(
                    Calculation.species_entry_id == eid,
                    Calculation.type == CalculationType.sp,
                )
            ).all()
            assert len(dlpno_calcs) >= 1
            for calc in dlpno_calcs:
                assert calc.conformer_observation_id is not None, (
                    f"DLPNO SP calc {calc.id} on species_entry {eid} is not "
                    f"anchored to a conformer observation"
                )
