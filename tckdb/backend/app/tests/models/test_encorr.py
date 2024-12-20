"""
TCKDB backend app tests models test_encorr module
"""

from tckdb.backend.app.models.encorr import EnCorr


def test_encorr_model():
    """Test creating an instance of EnCorr"""
    aec = {
        "H": -0.499459,
        "C": -37.786694,
        "N": -54.524279,
        "O": -74.992097,
        "S": -397.648733,
    }
    bac = {
        "C-H": -0.46,
        "C-C": -0.68,
        "C=C": -1.90,
        "C#C": -3.13,
        "O-H": -0.51,
        "C-O": -0.23,
        "C=O": -0.69,
        "O-O": -0.02,
        "C-N": -0.67,
        "C=N": -1.46,
        "C#N": -2.79,
        "N-O": 0.74,
        "N_O": -0.23,
        "N=O": -0.51,
        "N-H": -0.69,
        "N-N": -0.47,
        "N=N": -1.54,
        "N#N": -2.05,
        "S-H": 0.87,
        "C-S": 0.42,
        "C=S": 0.51,
        "S-S": 0.86,
        "O-S": 0.23,
        "O=S": -0.53,
    }
    encorr_1 = EnCorr(
        level_id=1,
        supported_elements=["H", "C", "N", "O", "S"],
        energy_unit="Hartree",
        aec=aec,
        bac=bac,
    )
    assert encorr_1.level_id == 1
    assert encorr_1.supported_elements == ["H", "C", "N", "O", "S"]
    assert encorr_1.energy_unit == "Hartree"
    assert encorr_1.aec == aec
    assert encorr_1.bac == bac
    assert (
        str(encorr_1)
        == "<EnCorr(level_id='1', supported_elements=['H', 'C', 'N', 'O', 'S'])>"
    )
    assert (
        repr(encorr_1)
        == "<EnCorr(id=None, level_id=1, supported_elements=['H', 'C', 'N', 'O', 'S'], "
        "energy_unit='Hartree', aec={'H': -0.499459, 'C': -37.786694, 'N': -54.524279, "
        "'O': -74.992097, 'S': -397.648733}, bac={'C-H': -0.46, 'C-C': -0.68, 'C=C': -1.9, "
        "'C#C': -3.13, 'O-H': -0.51, 'C-O': -0.23, 'C=O': -0.69, 'O-O': -0.02, 'C-N': -0.67, "
        "'C=N': -1.46, 'C#N': -2.79, 'N-O': 0.74, 'N_O': -0.23, 'N=O': -0.51, 'N-H': -0.69, "
        "'N-N': -0.47, 'N=N': -1.54, 'N#N': -2.05, 'S-H': 0.87, 'C-S': 0.42, 'C=S': 0.51, "
        "'S-S': 0.86, 'O-S': 0.23, 'O=S': -0.53})>"
    )

    isodesmic_reactions = (
        [
            {
                "reactants": ["[CH2]CCCC", "[CH]"],
                "products": ["[C]C", "C[CH]CC"],
                "stoichiometry": [1, 1, 1, 1],
                "DHrxn298": 17.076,
            },
            {
                "reactants": ["[CH2]CCCC", "[CH3]"],
                "products": ["C[CH2]", "[CH2]C(C)C"],
                "stoichiometry": [1, 1, 1, 1],
                "DHrxn298": 14.507,
            },
        ],
    )
    encorr_2 = EnCorr(
        level_id=1,
        supported_elements=["H", "C", "N", "O", "S"],
        energy_unit="kcal/mol",
        isodesmic_reactions=isodesmic_reactions,
        isodesmic_high_level_id=2,
    )
    assert encorr_2.level_id == 1
    assert encorr_2.supported_elements == ["H", "C", "N", "O", "S"]
    assert encorr_2.energy_unit == "kcal/mol"
    assert encorr_2.aec is None
    assert encorr_2.bac is None
    assert encorr_2.isodesmic_reactions == isodesmic_reactions
    assert encorr_2.isodesmic_high_level_id == 2
    assert (
        str(encorr_2)
        == "<EnCorr(level_id='1', supported_elements=['H', 'C', 'N', 'O', 'S'])>"
    )
    assert (
        repr(encorr_2)
        == "<EnCorr(id=None, level_id=1, supported_elements=['H', 'C', 'N', 'O', 'S'], "
        "energy_unit='kcal/mol', isodesmic_reactions=([{'reactants': ['[CH2]CCCC', '[CH]'], "
        "'products': ['[C]C', 'C[CH]CC'], 'stoichiometry': [1, 1, 1, 1], 'DHrxn298': 17.076}, "
        "{'reactants': ['[CH2]CCCC', '[CH3]'], 'products': ['C[CH2]', '[CH2]C(C)C'], "
        "'stoichiometry': [1, 1, 1, 1], 'DHrxn298': 14.507}],), isodesmic_high_level_id=2)>"
    )
