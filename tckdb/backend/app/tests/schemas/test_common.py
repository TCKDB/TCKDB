"""
TCKDB backend app tests schemas test_freq module
"""

import pytest
from tckdb.backend.app.schemas.common import (lowercase_dict,
                                              is_valid_energy_unit,
                                              is_valid_element_symbol,
                                              is_valid_inchi,
                                              is_valid_smiles,
                                              )


def test_lowercase_dict():
    """Test attaining a dictionary with lowercase keys and values"""
    d_1 = {'D': 1}
    assert lowercase_dict(d_1) == {'d': 1}
    d_2 = {'D': 'Z'}
    assert lowercase_dict(d_2) == {'d': 'z'}
    d_3 = {5: 'Z'}
    assert lowercase_dict(d_3) == {5: 'z'}
    d_4 = {'D': {'A': 'Z', 'v': 'n', 'H': 54, 'f': 'L', 7: 0, 8: {'Q': 9}}}
    assert lowercase_dict(d_4) == {'d': {'a': 'z', 'v': 'n', 'h': 54, 'f': 'l', 7: 0, 8: {'q': 9}}}
    with pytest.raises(TypeError):
        lowercase_dict('D')


def test_is_valid_energy_unit():
    """Test whether an energy unit is valid"""
    assert is_valid_energy_unit('hartree')
    assert is_valid_energy_unit('kJ / mol')
    assert is_valid_energy_unit('kJ/mol')
    assert is_valid_energy_unit('kcal')
    assert is_valid_energy_unit('kcal/mol')
    assert is_valid_energy_unit('eV')
    assert not is_valid_energy_unit('km')
    with pytest.raises(ValueError):
        is_valid_energy_unit('inch', raise_error=True)
    with pytest.raises(ValueError):
        is_valid_energy_unit('r3', raise_error=True)
    with pytest.raises(ValueError):
        is_valid_energy_unit(5.6, raise_error=True)


def test_is_valid_element_symbol():
    """Test whether an energy unit is valid"""
    assert is_valid_element_symbol('H')
    assert is_valid_element_symbol('N')
    assert is_valid_element_symbol('Cr')
    assert is_valid_element_symbol('Cl')
    assert is_valid_element_symbol('Ar')
    assert is_valid_element_symbol('C')
    assert is_valid_element_symbol('Zn')
    assert not is_valid_element_symbol('M')
    with pytest.raises(ValueError):
        is_valid_element_symbol('L', raise_error=True)
    with pytest.raises(ValueError):
        is_valid_element_symbol(8.7, raise_error=True)


def test_is_valid_inchi():
    """Test whether an InChI descriptor is valid"""
    assert is_valid_inchi('InChI=1S/CH4/h1H4')
    assert is_valid_inchi('InChI=1S/C7H8O/c8-6-7-4-2-1-3-5-7/h1-5,8H,6H2')
    assert is_valid_inchi('InChI=1S/C19H37NO8/c1-11(27-17-9-16(24-5)14(22)10-26-17)8-15(12(2)21)28-19-18(23)13(20(3)4)'
                          '6-7-25-19/h11-19,21-23H,6-10H2,1-5H3')
    assert is_valid_inchi('InChI=1S/CH3ClFNS/c2-1(5)4-3/h1,4-5H')
    assert not is_valid_inchi('not_an_inchi')
    assert not is_valid_inchi(15)


def test_is_valid_smiles():
    """Test whether a SMILES descriptor is valid"""
    assert is_valid_smiles('C')
    assert is_valid_smiles('CCC=CC(=O)O')
    assert is_valid_smiles('CN(C(O[O])CCN1C2C=CC=CC=2CCC2C1=CC=CC=2)C')
    assert is_valid_smiles('FNC(Cl)S')
    assert not is_valid_smiles('not_a_smiles')
    assert not is_valid_smiles(15)
