"""
TCKDB backend app schemas common module
"""

import qcelemental as qcel
from pint.errors import DefinitionSyntaxError, DimensionalityError, RedefinitionError, UndefinedUnitError
from rdkit.Chem import MolFromSmiles
from rdkit.Chem.inchi import MolFromInchi


def lowercase_dict(dictionary: dict) -> dict:
    """
    Convert all string keys and values in a dictionary to lowercase.

    Args:
        dictionary (dict): A dictionary to process.

    Raises:
        TypeError: If ``dictionary`` is not a ``dict`` instance.

    Returns:
        dict: A dictionary with all string keys and values lowercase.
    """
    if not isinstance(dictionary, dict):
        raise TypeError(f'Expected a dictionary, got a {type(dictionary)}')
    new_dict = dict()
    for key, val in dictionary.items():
        new_key = key.lower() if isinstance(key, str) else key
        if isinstance(val, dict):
            val = lowercase_dict(val)
        new_val = val.lower() if isinstance(val, str) else val
        new_dict[new_key] = new_val
    return new_dict


def is_valid_energy_unit(unit: str,
                         raise_error: bool = False,
                         ) -> bool:
    """
    Check whether a string represents a valid energy unit.

    Args:
        unit (str): The string to be checked.
        raise_error (bool): Whether to raise a ValueError if the string does not represent an energy unit.

    Returns:
        bool: Whether the string represents a valid energy unit.
    """
    try:
        qcel.constants.conversion_factor(unit, 'kJ/mol')
    except (AttributeError,
            DefinitionSyntaxError,
            DimensionalityError,
            RedefinitionError,
            UndefinedUnitError):
        if raise_error:
            raise ValueError(f'The unit "{unit}" does not seem to be a valid energy unit.')
        else:
            return False
    return True


def is_valid_element_symbol(symbol: str,
                            raise_error: bool = False,
                            ) -> bool:
    """
    Check whether an element symbol is valid.

    Args:
        symbol (str): The element symbol to be checked.
        raise_error (bool): Whether to raise a ValueError if the element symbol is invalid.

    Returns:
        bool: Whether the string represents a valid element symbol.
    """
    if not isinstance(symbol, str):
        if raise_error:
            raise ValueError(f'The symbol "{symbol}" does not seem to correspond to a known chemical element.')
        return False
    try:
        qcel.periodictable.to_Z(symbol)
    except qcel.exceptions.NotAnElementError:
        if raise_error:
            raise ValueError(f'The symbol "{symbol}" does not seem to correspond to a known chemical element.')
        else:
            return False
    return True


def is_valid_inchi(inchi):
    """
    Check whether a string represents a valid InChI descriptor.

    Args:
        inchi (str): The string to be checked.

    Returns:
        bool: Whether the string represents a valid InChI descriptor.
    """
    if not isinstance(inchi, str):
        # this is important, not only a shortcut, since a try except block does not capture Boost.Python.ArgumentError
        # being raised if the argument does not match the C++ signature.
        return False
    try:
        rd_mol = MolFromInchi(inchi)
    except:
        return False
    if rd_mol is None:
        return False
    return True


def is_valid_smiles(smiles):
    """
    Check whether a string represents a valid SMILES descriptor.

    Args:
        smiles (str): The string to be checked.

    Returns:
        bool: Whether the string represents a valid SMILES descriptor.
    """
    if not isinstance(smiles, str):
        # this is important, not only a shortcut, since a try except block does not capture Boost.Python.ArgumentError
        # being raised if the argument does not match the C++ signature.
        return False
    try:
        rd_mol = MolFromSmiles(smiles)
    except:
        return False
    if rd_mol is None:
        return False
    return True
