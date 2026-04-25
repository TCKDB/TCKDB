"""End-to-end API test using real reaction data from kfir_rxn_2.sdf.

Reaction (H-abstraction):
    NH3 + [N]=c1onno1  →  [NH2] + N=c1onno1(H)

Role mapping (from SDF ``type`` property):
    r1h, r2 → reactant
    r2h, r1 → product

Everything is uploaded in a single ``POST /uploads/computed-reaction`` call —
one payload = one Arkane run.
"""

from __future__ import annotations

import pytest

# -- Unit conversion helpers --------------------------------------------------
_CALMOLK_TO_JMOLK = 4.184
_KJMOL_TO_HARTREE = 1.0 / 2625.5


# -- Shared provenance --------------------------------------------------------

_LOT_DFT = {"method": "wb97xd", "basis": "def2tzvp"}
_LOT_DLPNO = {"method": "DLPNO-CCSD(T)", "basis": "cc-pVTZ"}
_SOFTWARE_GAUSSIAN = {"name": "Gaussian", "version": "16"}
_SOFTWARE_ORCA = {"name": "ORCA", "version": "5.0"}


# -- NASA polynomial helper ---------------------------------------------------

def _nasa(poly1_coeffs, poly1_tmin, poly1_tmax,
          poly2_coeffs, poly2_tmin, poly2_tmax) -> dict:
    return {
        "t_low": poly1_tmin, "t_mid": poly1_tmax, "t_high": poly2_tmax,
        "a1": poly1_coeffs[0], "a2": poly1_coeffs[1], "a3": poly1_coeffs[2],
        "a4": poly1_coeffs[3], "a5": poly1_coeffs[4], "a6": poly1_coeffs[5],
        "a7": poly1_coeffs[6],
        "b1": poly2_coeffs[0], "b2": poly2_coeffs[1], "b3": poly2_coeffs[2],
        "b4": poly2_coeffs[3], "b5": poly2_coeffs[4], "b6": poly2_coeffs[5],
        "b7": poly2_coeffs[6],
    }


# ==========================================================================
# The single bundle payload
# ==========================================================================

_BUNDLE = {
    "software_release": {"name": "Arkane", "version": "3.0"},

    # ------------------------------------------------------------------
    # Species (4 species, each with conformer + DLPNO SP + thermo)
    # ------------------------------------------------------------------
    "species": [
        {
            "key": "nh3",
            "species_entry": {"smiles": "N", "charge": 0, "multiplicity": 1},
            "conformers": [{
                "key": "nh3-conf",
                "geometry": {
                    "key": "nh3-geom",
                    "xyz_text": (
                        "4\nNH3\n"
                        "N     0.0028   -0.0636    0.0360\n"
                        "H     0.1884    0.8118    0.5247\n"
                        "H    -0.9230    0.0102   -0.3848\n"
                        "H     0.6570   -0.1245   -0.7436"
                    ),
                },
                "calculation": {
                    "key": "nh3-opt",
                    "type": "opt",
                    "software_release": _SOFTWARE_GAUSSIAN,
                    "level_of_theory": _LOT_DFT,
                    "opt_converged": True,
                },
                "label": "nh3-opt",
            }],
            "calculations": [{
                "key": "nh3-dlpno-sp",
                "type": "sp",
                "geometry_key": "nh3-geom",
                "software_release": _SOFTWARE_ORCA,
                "level_of_theory": _LOT_DLPNO,
                "sp_electronic_energy_hartree": -148510.90647055782 * _KJMOL_TO_HARTREE,
            }],
            "thermo": {
                "h298_kj_mol": -148411.0,
                "s298_j_mol_k": 45.96257860853203 * _CALMOLK_TO_JMOLK,
                "tmin_k": 10.0,
                "tmax_k": 3000.0,
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
            "key": "furazan_rad",
            "species_entry": {"smiles": "[N]=c1onno1", "charge": 0, "multiplicity": 2},
            "conformers": [{
                "key": "furazan-rad-conf",
                "geometry": {
                    "key": "furazan-rad-geom",
                    "xyz_text": (
                        "6\nfurazan radical\n"
                        "N     1.9594    0.0134   -0.3734\n"
                        "C     0.7437    0.0051   -0.0788\n"
                        "O    -0.0745    1.0868    0.0233\n"
                        "N    -1.3223    0.5972    0.3682\n"
                        "N    -1.2899   -0.6150    0.4676\n"
                        "O    -0.0165   -1.0874    0.2015"
                    ),
                },
                "calculation": {
                    "key": "furazan-rad-opt",
                    "type": "opt",
                    "software_release": _SOFTWARE_GAUSSIAN,
                    "level_of_theory": _LOT_DFT,
                    "opt_converged": True,
                },
                "label": "furazan-rad-opt",
            }],
            "calculations": [{
                "key": "furazan-rad-dlpno-sp",
                "type": "sp",
                "geometry_key": "furazan-rad-geom",
                "software_release": _SOFTWARE_ORCA,
                "level_of_theory": _LOT_DLPNO,
                "sp_electronic_energy_hartree": -925831.9516442568 * _KJMOL_TO_HARTREE,
            }],
            "thermo": {
                "h298_kj_mol": -925752.0,
                "s298_j_mol_k": 69.02430482099071 * _CALMOLK_TO_JMOLK,
                "tmin_k": 10.0,
                "tmax_k": 3000.0,
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
            "key": "nh2",
            "species_entry": {"smiles": "[NH2]", "charge": 0, "multiplicity": 2},
            "conformers": [{
                "key": "nh2-conf",
                "geometry": {
                    "key": "nh2-geom",
                    "xyz_text": (
                        "3\nNH2 radical\n"
                        "N     0.0002    0.4233    0.0000\n"
                        "H    -0.8051   -0.2112    0.0000\n"
                        "H     0.8049   -0.2121    0.0000"
                    ),
                },
                "calculation": {
                    "key": "nh2-opt",
                    "type": "opt",
                    "software_release": _SOFTWARE_GAUSSIAN,
                    "level_of_theory": _LOT_DFT,
                    "opt_converged": True,
                },
                "label": "nh2-opt",
            }],
            "calculations": [{
                "key": "nh2-dlpno-sp",
                "type": "sp",
                "geometry_key": "nh2-geom",
                "software_release": _SOFTWARE_ORCA,
                "level_of_theory": _LOT_DLPNO,
                "sp_electronic_energy_hartree": -146715.64756148277 * _KJMOL_TO_HARTREE,
            }],
            "thermo": {
                "h298_kj_mol": -146656.0,
                "s298_j_mol_k": 46.48569697774424 * _CALMOLK_TO_JMOLK,
                "tmin_k": 10.0,
                "tmax_k": 3000.0,
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
            "key": "furazan_h",
            "species_entry": {"smiles": "N=c1onno1", "charge": 0, "multiplicity": 1},
            "conformers": [{
                "key": "furazan-h-conf",
                "geometry": {
                    "key": "furazan-h-geom",
                    "xyz_text": (
                        "7\nN=c1onno1 with H\n"
                        "N     0.2531   -1.2243    1.5746\n"
                        "C     0.0931   -0.4846    0.5919\n"
                        "O     0.2132    0.8791    0.6089\n"
                        "N    -0.0374    1.3282   -0.6635\n"
                        "N    -0.2861    0.3915   -1.4071\n"
                        "O    -0.2281   -0.7956   -0.7055\n"
                        "H     0.1287   -2.2435    1.4095"
                    ),
                },
                "calculation": {
                    "key": "furazan-h-opt",
                    "type": "opt",
                    "software_release": _SOFTWARE_GAUSSIAN,
                    "level_of_theory": _LOT_DFT,
                    "opt_converged": True,
                },
                "label": "furazan-h-opt",
            }],
            "calculations": [{
                "key": "furazan-h-dlpno-sp",
                "type": "sp",
                "geometry_key": "furazan-h-geom",
                "software_release": _SOFTWARE_ORCA,
                "level_of_theory": _LOT_DLPNO,
                "sp_electronic_energy_hartree": -927613.1854967066 * _KJMOL_TO_HARTREE,
            }],
            "thermo": {
                "h298_kj_mol": -927500.0,
                "s298_j_mol_k": 69.21240690289005 * _CALMOLK_TO_JMOLK,
                "tmin_k": 10.0,
                "tmax_k": 3000.0,
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
    ],

    # ------------------------------------------------------------------
    # Reaction: NH3 + [N]=c1onno1 → [NH2] + N=c1onno1(H)
    # ------------------------------------------------------------------
    "reversible": True,
    "reactant_keys": ["nh3", "furazan_rad"],
    "product_keys": ["nh2", "furazan_h"],

    # ------------------------------------------------------------------
    # Transition state
    # ------------------------------------------------------------------
    "transition_state": {
        "charge": 0,
        "multiplicity": 2,
        "unmapped_smiles": "[N]=c1onno1.N",
        "geometry": {
            "key": "ts-geom",
            "xyz_text": (
                "10\nTS kfir_rxn_2\n"
                "N     0.2264   -0.7425    1.4864\n"
                "C     0.0664   -0.0028    0.5037\n"
                "O     0.1865    1.3608    0.5207\n"
                "N    -0.0641    1.8099   -0.7517\n"
                "N    -0.3128    0.8733   -1.4952\n"
                "O    -0.2548   -0.3138   -0.7937\n"
                "N    -0.1466   -3.0163    0.6977\n"
                "H     0.0784   -1.9554    1.2900\n"
                "H    -1.0724   -2.9425    0.2769\n"
                "H     0.5077   -3.0773   -0.0819"
            ),
        },
        "calculation": {
            "key": "ts-opt",
            "type": "opt",
            "software_release": _SOFTWARE_GAUSSIAN,
            "level_of_theory": _LOT_DFT,
            "opt_converged": True,
            "opt_final_energy_hartree": -1074160.0 * _KJMOL_TO_HARTREE,
        },
        "calculations": [{
            "key": "ts-freq",
            "type": "freq",
            "geometry_key": "ts-geom",
            "software_release": _SOFTWARE_GAUSSIAN,
            "level_of_theory": _LOT_DFT,
            "freq_n_imag": 1,
            "freq_imag_freq_cm1": -1760.3933,
        }],
        "label": "kfir_rxn_2 TS",
    },

    # ------------------------------------------------------------------
    # Kinetics fits (3 from kinetics_summary_dlnpo.csv)
    # ------------------------------------------------------------------
    "kinetics": [
        {
            "reactant_keys": ["nh2", "furazan_h"],
            "product_keys": ["nh3", "furazan_rad"],
            "a": 85.6882,
            "a_units": "cm3_mol_s",
            "n": 3.14758,
            "reported_ea": 28.9767,
            "reported_ea_units": "kj_mol",
            "tmin_k": 300.0,
            "tmax_k": 2500.0,
            "note": "k_rev (TST) — no tunneling",
        },
        {
            "reactant_keys": ["nh2", "furazan_h"],
            "product_keys": ["nh3", "furazan_rad"],
            "a": 0.00161525,
            "a_units": "cm3_mol_s",
            "n": 4.47077,
            "reported_ea": 12.914,
            "reported_ea_units": "kj_mol",
            "tmin_k": 300.0,
            "tmax_k": 2500.0,
            "tunneling_model": "Eckart",
            "note": "k_rev (TST+T) — Eckart tunneling",
        },
        {
            "reactant_keys": ["nh3", "furazan_rad"],
            "product_keys": ["nh2", "furazan_h"],
            "a": 0.000352836,
            "a_units": "cm3_mol_s",
            "n": 4.75152,
            "reported_ea": 16.6648,
            "reported_ea_units": "kj_mol",
            "tmin_k": 300.0,
            "tmax_k": 2500.0,
            "tunneling_model": "Eckart",
            "note": "k_for (TST+T) — Eckart tunneling",
        },
    ],
}


# ==========================================================================
# Tests
# ==========================================================================


class TestKfirRxn2Bundle:
    """One POST, one Arkane run — everything in a single bundle."""

    def test_full_bundle_upload(self, client):
        resp = client.post("/api/v1/uploads/computed-reaction", json=_BUNDLE)
        assert resp.status_code == 201, resp.text
        data = resp.json()

        assert data["type"] == "computed_reaction"
        assert data["species_count"] == 4
        assert len(data["kinetics_ids"]) == 3
        assert len(data["thermo_ids"]) == 4
        assert data["transition_state_entry_id"] is not None
        assert data["reaction_id"] > 0
        reaction_entry_id = data["reaction_entry_id"]

        # -- Verify reads ------------------------------------------------

        # Reaction entry exists
        re_resp = client.get(f"/api/v1/reaction-entries/{reaction_entry_id}")
        assert re_resp.status_code == 200

        # Transition state is linked to the reaction
        ts_list = client.get(
            f"/api/v1/reaction-entries/{reaction_entry_id}/transition-states"
        ).json()
        assert len(ts_list) >= 1

        # Top-level kinetics list has all 3
        all_kin = client.get("/api/v1/kinetics").json()
        assert all_kin["total"] >= 3

        # Species list has all 4
        species = client.get("/api/v1/species").json()
        assert species["total"] >= 4

        # Graph-level reaction exists
        reactions = client.get("/api/v1/reactions").json()
        assert reactions["total"] >= 1

        # Forward kinetics values survived round trip
        fwd_kin_id = data["kinetics_ids"][2]  # k_for(TST+T)
        fwd_kin = client.get(f"/api/v1/kinetics/{fwd_kin_id}").json()
        assert fwd_kin["n"] == pytest.approx(4.75152)
        assert fwd_kin["ea_kj_mol"] == pytest.approx(16.6648, abs=0.01)
        assert fwd_kin["tunneling_model"] == "Eckart"

        # NH3 thermo with NASA coefficients survived
        thermo_id = data["thermo_ids"][0]  # NH3 thermo
        thermo = client.get(f"/api/v1/thermo/{thermo_id}").json()
        assert thermo["h298_kj_mol"] == pytest.approx(-148411.0, abs=0.1)
        assert thermo["tmin_k"] == pytest.approx(10.0)
        assert thermo["tmax_k"] == pytest.approx(3000.0)

        nasa = thermo.get("nasa")
        assert nasa is not None, "NASA block missing"
        assert nasa["t_low"] == pytest.approx(10.0)
        assert nasa["a1"] == pytest.approx(4.018066, abs=1e-4)
