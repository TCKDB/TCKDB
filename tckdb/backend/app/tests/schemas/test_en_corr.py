"""
TCKDB backend app tests schemas test_en_corr module
"""

import pytest
from pydantic import ValidationError

from tckdb.backend.app.schemas.en_corr import EnCorrBase


def test_en_corr_schema():
    """Test creating an instance of EnCorr"""
    supported_elements = ['H', 'C', 'N', 'O', 'S', 'P']
    aec = {'H': -0.502155915123, 'C': -37.8574709934, 'N': -54.6007233609,
           'O': -75.0909131284, 'P': -341.281730319, 'S': -398.134489850}
    bac = {'C-H': 0.25, 'C-C': -1.89, 'C=C': -0.40, 'C#C': -1.50, 'O-H': -1.09, 'C-O': -1.18, 'C=O': -0.01,
           'N-H': 1.36, 'C-N': -0.44, 'C#N': 0.22, 'C-S': -2.35, 'O=S': -5.19, 'S-H': -0.52}

    en_corr_1 = EnCorrBase(level_id=1,
                           supported_elements=supported_elements,
                           energy_unit='hartree',
                           aec=aec,
                           bac=bac)
    assert en_corr_1.level_id == 1
    assert en_corr_1.supported_elements == supported_elements
    assert en_corr_1.energy_unit == 'hartree'
    assert en_corr_1.aec == aec
    assert en_corr_1.bac == bac
    assert en_corr_1.isodesmic_reactions is None
    assert en_corr_1.isodesmic_high_level_id is None
    assert en_corr_1.reviewer_flags == dict()

    isodesmic_reactions = [{'reactants': ['[CH2]CCCC', '[CH]'],
                            'products': ['[C]C', '[CH2]C(C)C'],
                            'stoichiometry': [1, 1, 1, 1],
                            'DHrxn298': 16.809},
                           {'reactants': ['InChI=1S/C5H11/c1-3-5-4-2/h1,3-5H2,2H3', '[CH3]'],
                            'products': ['CCCC', 'InChI=1S/C2H4/c1-2/h1H,2H3'],
                            'stoichiometry': [1, 1, 1, 1],
                            'DHrxn298': 15.409},
                           ]

    en_corr_2 = EnCorrBase(level_id=1,
                           supported_elements=supported_elements,
                           energy_unit='kcal/mol',
                           isodesmic_reactions=isodesmic_reactions,
                           isodesmic_high_level_id=3)
    assert en_corr_2.level_id == 1
    assert en_corr_2.supported_elements == supported_elements
    assert en_corr_2.energy_unit == 'kcal/mol'
    assert en_corr_2.aec is None
    assert en_corr_2.bac is None
    assert en_corr_2.isodesmic_reactions == isodesmic_reactions
    assert en_corr_2.isodesmic_high_level_id == 3

    with pytest.raises(ValidationError):
        # invalid element in supported_elements
        EnCorrBase(level_id=1,
                   supported_elements=['M', 'C', 'N', 'O', 'S', 'P'],
                   energy_unit='hartree',
                   aec=aec,
                   bac=bac)
    with pytest.raises(ValidationError):
        # invalid energy units
        EnCorrBase(level_id=1,
                   supported_elements=supported_elements,
                   energy_unit='wrong',
                   aec=aec,
                   bac=bac)
    with pytest.raises(ValidationError):
        # aec element not in supported_elements
        EnCorrBase(level_id=1,
                   supported_elements=supported_elements,
                   energy_unit='hartree',
                   aec={'Si': -0.502155915123, 'C': -37.8574709934, 'N': -54.6007233609,
                        'O': -75.0909131284, 'P': -341.281730319, 'S': -398.134489850},
                   bac=bac)
    with pytest.raises(ValidationError):
        # aec and supported_elements have different lengths
        EnCorrBase(level_id=1,
                   supported_elements=supported_elements,
                   energy_unit='hartree',
                   aec={'H': -0.502155915123, 'C': -37.8574709934, 'N': -54.6007233609,
                        'O': -75.0909131284, 'P': -341.281730319},
                   bac=bac)
    with pytest.raises(ValidationError):
        # space in bac
        EnCorrBase(level_id=1,
                   supported_elements=supported_elements,
                   energy_unit='hartree',
                   aec=aec,
                   bac={'C- H': 0.25, 'C-C': -1.89})
    with pytest.raises(ValidationError):
        # no bond descriptor in bac
        EnCorrBase(level_id=1,
                   supported_elements=supported_elements,
                   energy_unit='hartree',
                   aec=aec,
                   bac={'CH': 0.25, 'C-C': -1.89})
    with pytest.raises(ValidationError):
        # two bond descriptors in bac
        EnCorrBase(level_id=1,
                   supported_elements=supported_elements,
                   energy_unit='hartree',
                   aec=aec,
                   bac={'C-=H': 0.25, 'C-C': -1.89})
    with pytest.raises(ValidationError):
        # bac element not in supported_elements
        EnCorrBase(level_id=1,
                   supported_elements=supported_elements,
                   energy_unit='hartree',
                   aec=aec,
                   bac={'C-Cl': 0.25, 'C-C': -1.89})
    with pytest.raises(ValidationError):
        # no bac nor isodesmic
        EnCorrBase(level_id=1,
                   supported_elements=supported_elements,
                   energy_unit='hartree',
                   aec=aec)
    with pytest.raises(ValidationError):
        # no aec nor isodesmic
        EnCorrBase(level_id=1,
                   supported_elements=supported_elements,
                   energy_unit='hartree',
                   bac=bac)
    with pytest.raises(ValidationError):
        # both isodesmic and aec
        EnCorrBase(level_id=1,
                   supported_elements=supported_elements,
                   energy_unit='hartree',
                   aec=aec,
                   isodesmic_reactions=isodesmic_reactions,
                   isodesmic_high_level_id=3)
    with pytest.raises(ValidationError):
        # both isodesmic and bac
        EnCorrBase(level_id=1,
                   supported_elements=supported_elements,
                   energy_unit='hartree',
                   bac=bac,
                   isodesmic_reactions=isodesmic_reactions,
                   isodesmic_high_level_id=3)
    with pytest.raises(ValidationError):
        # both isodesmic and aec/bac
        EnCorrBase(level_id=1,
                   supported_elements=supported_elements,
                   energy_unit='hartree',
                   aec=aec,
                   bac=bac,
                   isodesmic_reactions=isodesmic_reactions,
                   isodesmic_high_level_id=3)
    with pytest.raises(ValidationError):
        # isodesmic 'reactant' not a list
        EnCorrBase(level_id=1,
                   supported_elements=supported_elements,
                   energy_unit='kcal/mol',
                   isodesmic_reactions=[{'reactants': '[CH2]CCCC+[CH]',
                                         'products': ['[C]C', '[CH2]C(C)C'],
                                         'stoichiometry': [1, 1, 1, 1],
                                         'DHrxn298': 16.809}],
                   isodesmic_high_level_id=3)
    with pytest.raises(ValidationError):
        # isodesmic 'product' not a list
        EnCorrBase(level_id=1,
                   supported_elements=supported_elements,
                   energy_unit='kcal/mol',
                   isodesmic_reactions=[{'reactants': ['[CH2]CCCC', '[CH]'],
                                         'products': '[C]C+[CH2]C(C)C',
                                         'stoichiometry': [1, 1, 1, 1],
                                         'DHrxn298': 16.809}],
                   isodesmic_high_level_id=3)
    with pytest.raises(ValidationError):
        # isodesmic 'product' has an invalid identifier
        EnCorrBase(level_id=1,
                   supported_elements=supported_elements,
                   energy_unit='kcal/mol',
                   isodesmic_reactions=[{'reactants': ['[CH2]CCCC', '[CH]'],
                                         'products': ['[C]C++++f151_invalid', '[CH2]C(C)C'],
                                         'stoichiometry': [1, 1, 1, 1],
                                         'DHrxn298': 16.809}],
                   isodesmic_high_level_id=3)
    with pytest.raises(ValidationError):
        # isodesmic 'stoichiometry' is not a list
        EnCorrBase(level_id=1,
                   supported_elements=supported_elements,
                   energy_unit='kcal/mol',
                   isodesmic_reactions=[{'reactants': ['[CH2]CCCC', '[CH]'],
                                         'products': ['[C]C', '[CH2]C(C)C'],
                                         'stoichiometry': '*1 *1 *1 *1',
                                         'DHrxn298': 16.809}],
                   isodesmic_high_level_id=3)
    with pytest.raises(ValidationError):
        # isodesmic 'stoichiometry' coefficient is not an integer
        EnCorrBase(level_id=1,
                   supported_elements=supported_elements,
                   energy_unit='kcal/mol',
                   isodesmic_reactions=[{'reactants': ['[CH2]CCCC', '[CH]'],
                                         'products': ['[C]C', '[CH2]C(C)C'],
                                         'stoichiometry': ['one', 1, 1, 1],
                                         'DHrxn298': 16.809}],
                   isodesmic_high_level_id=3)
    with pytest.raises(ValidationError):
        # isodesmic 'stoichiometry' DHrxn298 is not a float
        EnCorrBase(level_id=1,
                   supported_elements=supported_elements,
                   energy_unit='kcal/mol',
                   isodesmic_reactions=[{'reactants': ['[CH2]CCCC', '[CH]'],
                                         'products': ['[C]C', '[CH2]C(C)C'],
                                         'stoichiometry': [1, 1, 1, 1],
                                         'DHrxn298': (16.809, 'kJ/mol')}],
                   isodesmic_high_level_id=3)
    with pytest.raises(ValidationError):
        # isodesmic reaction has a wrong key
        EnCorrBase(level_id=1,
                   supported_elements=supported_elements,
                   energy_unit='kcal/mol',
                   isodesmic_reactions=[{'reactants': ['[CH2]CCCC', '[CH]'],
                                         'products': ['[C]C', '[CH2]C(C)C'],
                                         'stoichiometry': [1, 1, 1, 1],
                                         'enthalpy_chane_of_reaction': 16.809}],
                   isodesmic_high_level_id=3)
    with pytest.raises(ValidationError):
        # isodesmic reaction is missing a key
        EnCorrBase(level_id=1,
                   supported_elements=supported_elements,
                   energy_unit='kcal/mol',
                   isodesmic_reactions=[{'products': ['[C]C', '[CH2]C(C)C'],
                                         'stoichiometry': [1, 1, 1, 1],
                                         'DHrxn298': 16.809}],
                   isodesmic_high_level_id=3)
    with pytest.raises(ValidationError):
        # isodesmic reaction is missing a key
        EnCorrBase(level_id=1,
                   supported_elements=supported_elements,
                   energy_unit='kcal/mol',
                   isodesmic_reactions=[{'reactants': ['[CH2]CCCC', '[CH]'],
                                         'stoichiometry': [1, 1, 1, 1],
                                         'DHrxn298': 16.809}],
                   isodesmic_high_level_id=3)
    with pytest.raises(ValidationError):
        # isodesmic reaction is missing a key
        EnCorrBase(level_id=1,
                   supported_elements=supported_elements,
                   energy_unit='kcal/mol',
                   isodesmic_reactions=[{'reactants': ['[CH2]CCCC', '[CH]'],
                                         'products': ['[C]C', '[CH2]C(C)C'],
                                         'DHrxn298': 16.809}],
                   isodesmic_high_level_id=3)
    with pytest.raises(ValidationError):
        # isodesmic reaction is missing a key
        EnCorrBase(level_id=1,
                   supported_elements=supported_elements,
                   energy_unit='kcal/mol',
                   isodesmic_reactions=[{'reactants': ['[CH2]CCCC', '[CH]'],
                                         'products': ['[C]C', '[CH2]C(C)C'],
                                         'stoichiometry': [1, 1, 1, 1]}],
                   isodesmic_high_level_id=3)
    with pytest.raises(ValidationError):
        # isodesmic reaction has an extra key
        EnCorrBase(level_id=1,
                   supported_elements=supported_elements,
                   energy_unit='kcal/mol',
                   isodesmic_reactions=[{'reactants': ['[CH2]CCCC', '[CH]'],
                                         'products': ['[C]C', '[CH2]C(C)C'],
                                         'stoichiometry': [1, 1, 1, 1],
                                         'index': 152,
                                         'DHrxn298': 16.809}],
                   isodesmic_high_level_id=3)
    with pytest.raises(ValidationError):
        # isodesmic reaction with no isodesmic_high_level_id
        EnCorrBase(level_id=1,
                   supported_elements=supported_elements,
                   energy_unit='kcal/mol',
                   isodesmic_reactions=[{'reactants': ['[CH2]CCCC', '[CH]'],
                                         'products': ['[C]C', '[CH2]C(C)C'],
                                         'stoichiometry': [1, 1, 1, 1],
                                         'DHrxn298': 16.809}])
