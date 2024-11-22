"""
TCKDB backend app schemas common module
"""

import re
from pathlib import Path

# trunk-ignore(bandit/B404)
import subprocess
import sys
from typing import Dict, List, Optional, Tuple, Union, Any

import numpy as np
import qcelemental as qcel
from pint.errors import (
    DefinitionSyntaxError,
    DimensionalityError,
    RedefinitionError,
    UndefinedUnitError,
)
from pydantic import BaseModel, ConfigDict, Field, StringConstraints
from rdkit.Chem import MolFromSmiles
from rdkit.Chem.inchi import MolFromInchi
from typing_extensions import Annotated

from tckdb.backend.app.conversions.converter import inchi_from_inchi_key
from tckdb.backend.app.utils.python_paths import MOLECULE_PYTHON


class Coordinates(BaseModel):
    symbols: Tuple[Annotated[str, StringConstraints(max_length=10)], ...] = Field(
        ..., description="Chemical element symbols."
    )
    isotopes: Tuple[Annotated[int, Field(ge=1)], ...] = Field(
        ..., description="The respective isotopes."
    )
    coords: Tuple[Tuple[float, float, float], ...] = Field(
        ..., description="Cartesian coordinates in standard orientation."
    )
    model_config = ConfigDict(
        from_attributes=True,
        extra="forbid",
        json_schema_extra={
            "example": {
                "symbols": ("C", "H", "H", "H", "H"),
                "isotopes": (12, 1, 1, 1, 1),
                "coords": (
                    (0.0, 0.0, 0.0),
                    (0.6300326, 0.6300326, 0.6300326),
                    (-0.6300326, -0.6300326, 0.6300326),
                    (-0.6300326, 0.6300326, -0.6300326),
                    (0.6300326, -0.6300326, -0.6300326),
                ),
            }
        },
    )


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
        raise TypeError(f"Expected a dictionary, got a {type(dictionary)}")
    new_dict = dict()
    for key, val in dictionary.items():
        new_key = key.lower() if isinstance(key, str) else key
        if isinstance(val, dict):
            val = lowercase_dict(val)
        new_val = val.lower() if isinstance(val, str) else val
        new_dict[new_key] = new_val
    return new_dict


def is_valid_energy_unit(
    unit: str,
    raise_error: bool = False,
) -> Tuple[bool, str]:
    """
    Check whether a string represents a valid energy unit.

    Args:
        unit (str): The string to be checked.
        raise_error (bool): Whether to raise a ValueError if the string does not represent an energy unit.

    Returns:
        Tuple[bool, str]:
            - Whether the string represents a valid energy unit.
            - A reason for invalidating the argument.
    """
    try:
        qcel.constants.conversion_factor(unit, "kJ/mol")
    except (
        AttributeError,
        DefinitionSyntaxError,
        DimensionalityError,
        RedefinitionError,
        UndefinedUnitError,
    ) as e:
        if raise_error:
            raise ValueError(
                f'The unit "{unit}" does not seem to be a valid energy unit. Got:\n{e}'
            ) from e
        else:
            return False, str(e)
    return True, ""


def is_valid_element_symbol(
    symbol: str,
    raise_error: bool = False,
) -> Tuple[bool, str]:
    """
    Check whether an element symbol is valid.

    Args:
        symbol (str): The element symbol to be checked.
        raise_error (bool): Whether to raise a ValueError if the element symbol is invalid.

    Returns:
        Tuple[bool, str]:
            - Whether the string represents a valid element symbol.
            - A reason for invalidating the argument.
    """
    if not isinstance(symbol, str):
        if raise_error:
            raise ValueError(
                f'An element symbol must be a string, got "{symbol}" which is a {type(symbol)}.'
            )
        return (
            False,
            f'An element symbol must be a string, got "{symbol}" which is a {type(symbol)}.',
        )
    try:
        qcel.periodictable.to_Z(symbol)
    except qcel.exceptions.NotAnElementError as e:
        if raise_error:
            raise ValueError(
                f'The symbol "{symbol}" does not seem to correspond to a known chemical element.'
            ) from e
        else:
            return (
                False,
                f'The symbol "{symbol}" does not seem to correspond to a known chemical element.',
            )
    return True, ""


def is_valid_inchi(inchi: str) -> Tuple[bool, str]:
    """
    Check whether a string represents a valid InChI descriptor.

    Args:
        inchi (str): The string to be checked.

    Returns:
        Tuple[bool, str]:
            - Whether the string represents a valid InChI descriptor.
            - A reason for invalidating the argument.
    """
    if not isinstance(inchi, str):
        # this is important, not only a shortcut, since a try except block does not capture Boost.Python.ArgumentError
        # being raised if the argument does not match the C++ signature.
        return (
            False,
            f'An InChI descriptor must be a string, got "{inchi}" which is a {type(inchi)}.',
        )
    try:
        rd_mol = MolFromInchi(inchi)
    except Exception as e:
        return False, str(e)
    if rd_mol is None:
        return False, f'Could not generate an RDKit Molecule from InChI "{inchi}"'
    return True, ""


def is_valid_inchi_key(
    inchi_key: str,
    regex_only: bool = True,
) -> Tuple[bool, str]:
    """
    Check whether a string represents a valid InChI Key descriptor.
    Note that ``regex_only`` is set to ``True`` by default, since the InChI Key resolving method is not robust.

    Args:
        inchi_key (str): The string to be checked.
        regex_only (bool): Only check regex compatibility.

    Returns:
        Tuple[bool, str]:
            - Whether the string represents a valid InChI Key descriptor.
            - A reason for invalidating the argument.
    """
    if not isinstance(inchi_key, str):
        return (
            False,
            f'An InChI Key descriptor must be a string, got "{inchi_key}" which is a {type(inchi_key)}.',
        )
    inchi_key_regex = re.compile("[A-Z]{14}-[A-Z]{10}-[A-Z]")
    if not inchi_key_regex.match(inchi_key):
        return False, f'The InChI Key descriptor is corrupt, got: "{inchi_key}".'
    if regex_only:
        return True, ""
    try:
        inchi = inchi_from_inchi_key(inchi_key)
    except Exception as e:
        return False, f"Could not decode InChI Key {e.args}"
    if inchi is None:
        return False, "Could not decode InChI Key"
    return True, ""


def is_valid_smiles(smiles: str) -> Tuple[bool, str]:
    """
    Check whether a string represents a valid SMILES descriptor.

    Args:
        smiles (str): The string to be checked.

    Returns:
        Tuple[bool, str]:
            - Whether the string represents a valid SMILES descriptor.
            - A reason for invalidating the argument.
    """
    if not isinstance(smiles, str):
        # this is important, not only a shortcut, since a try except block does not capture Boost.Python.ArgumentError
        # being raised if the argument does not match the C++ signature.
        return (
            False,
            f'A SMILES descriptor must be a string, got "{smiles}" which is a {type(smiles)}.',
        )
    try:
        rd_mol = MolFromSmiles(smiles)
    except Exception as e:
        return False, f'Could not decode the SMILES string "{smiles}, {e.args}".'
    if rd_mol is None:
        return False, f'Could not decode the SMILES string "{smiles}".'
    return True, ""


def is_valid_adjlist(adjlist: str) -> Tuple[bool, str]:
    """
    Check whether a string represents a valid adjacency list.

    Args:
        adjlist (str): The string to be checked.

    Returns:
        Tuple[bool, str]:
            - Whether the string represents a valid adjacency list.
            - A reason for invalidating the argument.
    """
    try:
        script_dir = Path(__file__).resolve().parent
        utils_dir = script_dir.parent / "utils"
        conversion_script = utils_dir / "molecule_env_scripts.py"
        if not conversion_script.is_file():
            raise FileNotFoundError(
                f"Conversion script not found at: {conversion_script}"
            )

        cmd = [MOLECULE_PYTHON, conversion_script, "validate"]

        # trunk-ignore(bandit/B603)
        result = subprocess.run(
            cmd, input=adjlist, text=True, capture_output=True, check=True
        )

        output = result.stdout.strip()
        if output == "True":
            return True, ""
        else:
            return False, print(
                "Unexpected output: {output} and stderr:", file=sys.stderr
            )

    except subprocess.CalledProcessError as e:
        error_message = e.stderr.strip()
        if not error_message:
            error_message = "Unknown error occurred during validation."
        return False, f"Validation failed: {error_message}"
    except Exception as e:
        return False, f"Unexpected error: {e}"


def check_colliding_atoms(
    xyz: Union[Coordinates, Dict[str, Any]],
    threshold: float = 0.55,
) -> bool:
    """
    Check whether atoms are too close to each other.
    A default threshold of 55% of the covalent radii of two atoms is used.
    For example, this translates into:
    - C-O collide at 55% * 1.42 A = 0.781 A
    - N-N collide at 55% * 1.42 A = 0.781 A
    - C-N collide at 55% * 1.47 A = 0.808 A
    - C-H collide at 55% * 1.07 A = 0.588 A

    Todo:
        - perhaps find a better universal threshold
        - perhaps modify this threshold dynamically for specific element pirs

    Args:
        xyz (dict): The Cartesian coordinates.
        threshold (float, optional): The collision threshold to use.

    Returns:
         bool: ``True`` if there are colliding atoms in the input, ``False`` otherwise.
    """
    if isinstance(xyz, Coordinates):
        coords = xyz.coords
        symbols = xyz.symbols
    elif isinstance(xyz, dict):
        coords = xyz["coords"]
        symbols = xyz["symbols"]

    if len(xyz["symbols"]) == 1:
        # monoatomic
        return False
    # convert Angstrom to Bohr
    geometry = np.array(
        [np.array(coord, np.float64) * 1.8897259886 for coord in coords]
    )
    qcel_out = qcel.molutil.guess_connectivity(
        symbols=symbols, geometry=geometry, threshold=threshold
    )
    return bool(len(qcel_out))


def is_valid_coordinates(
    xyz: Union[Coordinates, Dict[str, Any]],
    allowed_keys: Optional[List[str]] = None,
    collision_threshold: Optional[float] = None,
) -> Tuple[bool, str]:
    """
    Validate the coordinates of a species, whether provided as a Coordinates instance or a dictionary.

    Args:
        xyz (Union[Coordinates, Dict[str, Any]]): The coordinates to validate.
        allowed_keys (Optional[List[str]]): Additional keys allowed in the coordinates dictionary.
        collision_threshold (Optional[float]): Threshold for detecting colliding atoms.

    Returns:
        Tuple[bool, str]: (True, "") if valid, otherwise (False, error_message)
    """
    valid_keys = ["symbols", "isotopes", "coords"]
    allowed_keys = allowed_keys or []

    if isinstance(xyz, Coordinates):
        # Validate Coordinates instance
        num_symbols = len(xyz.symbols)
        num_isotopes = len(xyz.isotopes)
        num_coords = len(xyz.coords)

        if num_symbols != num_isotopes or num_symbols != num_coords:
            return (
                False,
                f"Got {num_symbols} symbols, {num_isotopes} isotopes, and {num_coords} coordinates in\n{xyz}",
            )

        for idx, coord in enumerate(xyz.coords):
            if len(coord) != 3:
                return (
                    False,
                    f"All atom coordinates must be of length 3, got: {coord} at index {idx}",
                )

        if collision_threshold is not None:
            try:
                has_collision = check_colliding_atoms(
                    coordinates=xyz, threshold=collision_threshold
                )
            except TypeError as e:
                return False, str(e)
            if has_collision:
                return (
                    False,
                    f"The coordinates have colliding atoms (at a tolerance of {collision_threshold}).",
                )

        return True, ""

    elif isinstance(xyz, dict):
        # Validate dictionary input
        invalid_keys = [
            key for key in xyz.keys() if key not in valid_keys + allowed_keys
        ]
        for valid_key in valid_keys:
            if valid_key not in xyz:
                return (
                    False,
                    f'The "{valid_key}" key is missing from the coordinates dictionary.',
                )
        if len(invalid_keys):
            return (
                False,
                f"The coordinates dictionary has the following invalid key(s): {invalid_keys}.",
            )
        if len(xyz["coords"]) != len(xyz["symbols"]) or len(xyz["coords"]) != len(
            xyz["isotopes"]
        ):
            return (
                False,
                f'Got {len(xyz["symbols"])} symbols, {len(xyz["isotopes"])} isotopes, '
                f'and {len(xyz["coords"])} coordinates in\n{xyz}',
            )
        for coord in xyz["coords"]:
            if len(coord) != 3:
                return False, f"All atom coordinates must be of length 3, got:\n{xyz}"
        if collision_threshold is not None:
            try:
                has_collision = check_colliding_atoms(
                    coordinates=xyz, threshold=collision_threshold
                )
            except TypeError as e:
                return False, str(e)
            if has_collision:
                return (
                    False,
                    f"The coordinates have colliding atoms (at a tolerance of {collision_threshold}).",
                )
        return True, ""

    else:
        return (
            False,
            "Invalid type for coordinates. Expected Coordinates instance or dict.",
        )


def is_valid_atom_index(
    index: int,
    coordinates: Optional[dict] = None,
    existing_indices: Optional[List[int]] = None,
) -> Tuple[bool, str]:
    """
    Check whether an atom index is valid:
    1. it is not 0
    2. it is not in the existing indices list (if ``existing_indices`` is not ``None``)
    2. it is not higher than the total number of atoms

    Args:
        index (int): The atom index to be checked.
        existing_indices (list, optional): Entries are pre-checked atom indices.
        coordinates (dict, optional): The 3d coordinates from which the total number of atoms is deduced.

    Returns:
        Tuple[bool, str]:
            - Whether the atom index is valid.
            - A reason for invalidating the argument.
    """
    if index == 0:
        return False, "A 1-indexed atom index cannot be zero."
    if coordinates is not None and index > len(coordinates.symbols):
        return (
            False,
            f"An atom index {index} cannot be greater than the number of atoms {len(coordinates.symbols)}.",
        )
    if existing_indices is not None and index in existing_indices:
        return False, f"Atom index {index} appears more than once in this argument."
    return True, ""


def get_number_of_atoms(coords: Optional[dict]) -> Optional[int]:
    """
    Get the number of atoms in a coordinates dictionary.
    Has many safety checks to be safely used in the schema.

    Args:
        coords (dict): The coordinates dictionary.

    Returns:
        Optional[int]: The number of atoms in the coordinates matrix.
    """
    if coords is not None:
        if "coordinates" in coords:
            coords = coords["coordinates"]
        if isinstance(coords, Coordinates) and isinstance(
            coords.symbols, (list, tuple)
        ):
            return len(coords.symbols)
        elif isinstance(coords, dict):
            if "symbols" in coords:
                return len(coords["symbols"])
            elif "symbols" in coords.keys():
                return len(coords["symbols"])
    return None
