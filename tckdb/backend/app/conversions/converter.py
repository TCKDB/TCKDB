"""
TCKDB backend app conversions converter module
This module is used for converting in between various species identifiers

Todo: Use the ``raise_atomtype_exception`` and ``raise_charge_exception`` arguments
      in adjlist_from_smiles() and smiles_and_inchi_from_adjlist() once RMG's binaries are updated
"""

import math
import os
import subprocess
import sys
from typing import Dict, Optional, Tuple, Union

import numpy as np
import qcelemental as qcel
import requests
from chembl_webresource_client.new_client import new_client
from rdkit.Chem import MolFromSmiles, MolToSmiles
from rdkit.Chem.inchi import InchiToInchiKey, MolFromInchi, MolToInchi

from tckdb.backend.app.utils.python_paths import MOLECULE_PYTHON


def inchi_from_smiles(smiles: str) -> Union[str, None]:
    """
    Get an InChI descriptor from a SMILES descriptors.
    Uses RDKit for the conversion.

    Args:
        smiles (str): The SMILES descriptor.

    Returns:
        str: The corresponding InChI descriptor.
    """
    try:
        inchi = MolToInchi(MolFromSmiles(smiles))
    except:
        return None
    return inchi


def adjlist_from_smiles(smiles: str) -> Union[str, None]:

    url = f"https://rmg.mit.edu/adjacencylist/{smiles}"

    headers = {
        "User-Agent": "YourAppName/1.0",
        "Accept": "text/plain",
        "Referer": "https://rmg.mit.edu/molecule_search",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
    }

    try:
        # Send the GET request
        response = requests.get(url, headers=headers, timeout=10)

        if response.status_code == 200:
            adjacency_list = response.text
            return adjacency_list
        else:
            print(f"Error: Received status code {response.status_code}")
            print(f"Response: {response.text}")
            return None

    except Exception as e:
        print(f"An error occurred: {e}")
        return None


# def adjlist_from_smiles(smiles: str) -> Union[str, None]:
#     """
#     Get an RMG adjacency list from SMILES.
#     Uses RMG for the conversion.

#     Args:
#         smiles (str): The SMILES descriptor.

#     Returns:
#         str: The respective adjacency list.
#     """
#     try:
#         mol = Molecule().from_smiles(smilesstr=smiles,
#                                      raise_atomtype_exception=False)
#     except:
#         return None
#     if mol is not None:
#         adjlist = mol.to_adjacency_list()
#         return adjlist
#     return None


# def smiles_and_inchi_from_adjlist(adjlist: str) -> Union[Tuple[str, str], Tuple[None, None]]:
#     """
#     Get the SMILES and InChI descriptors corresponding to an RMG adjacency list
#     Uses RMG for the conversions.

#     Args:
#         adjlist (str): The adjacency list.

#     Returns:
#         Tuple[str, str]:
#             - The respective SMILES.
#             - The respective InChI.
#     """
#     try:
#         mol = Molecule().from_adjacency_list(adjlist=adjlist,
#                                              raise_atomtype_exception=False,
#                                              raise_charge_exception=False)
#     except:
#         return None, None
#     if mol is not None:
#         smiles = mol.to_smiles()
#         inchi = mol.to_inchi()
#         return smiles, inchi
#     return None, None


def smiles_and_inchi_from_adjlist(adjlist: str) -> Optional[Tuple[str, str]]:
    """
    Get the SMILEs and InChI descriptors corresponding to an RMG adjaceny list
    Uses a subprocess to call a script in molecule_env for the conversions.

    Args:
        adjlist (str): The adjacency list.

    Returns:
        Optional[Tuple[str,str]]:
            - The respective SMILES
            - The respective InChI
            Returns None if conversion fails
    """
    try:
        script_dir = os.path.dirname(os.path.abspath(__file__))
        conversion_script = os.path.join(script_dir, "molecule_env_scripts.py")
        cmd = [MOLECULE_PYTHON, conversion_script, "convert"]

        result = subprocess.run(
            cmd, input=adjlist, text=True, capture_output=True, check=True
        )

        # Parse
        output = result.stdout.strip().split("\n")
        if len(output) >= 2:
            smiles = output[0]
            inchi = output[1]
            return smiles, inchi
        else:
            print("Error: Unexpected output format.", file=sys.stderr)
    except subprocess.CalledProcessError as e:
        print(
            f"Subprocess error (exit code {e.returncode}): {e.stderr}", file=sys.stderr
        )
        return None
    except Exception as e:
        print(f"Unexpected error: {e}", file=sys.stderr)
        return None


def inchi_from_inchi_key(
    inchi_key: str,
    inchi_type: Optional[str] = "standardinchi",
) -> Union[str, None]:
    """
    Get an InChI descriptor from an InChI Key descriptor.
    Uses ChEMBL webresource client and 'https://www.ebi.ac.uk/unichem/'
    for the conversion.

    Note:
        This conversion is not robust and may return ``None`` even for valid InChI Keys.

    Args:
        inchi_key (str): The InChI Key descriptor.
        inchi_type (str, optional): The InChI type to return.

    Returns:
        str: The standard InChI descriptor.
    """
    # uni_chem_client = UniChemClient()

    # try:
    #     inchi = unichem.inchiFromKey(inchi_key)
    # except:
    #     return None
    # if len(inchi) and inchi_type in inchi[0]:
    #     return inchi[0][inchi_type]
    # return None

    molecule = new_client.molecule
    mol = molecule.filter(molecule_structures__standard_inchi_key=inchi_key).only(
        ["molecule_structures"]
    )
    if mol:
        return mol[0]["molecule_structures"]["standard_inchi"]
    return None


def inchi_key_from_inchi(inchi: str) -> Union[str, None]:
    """
    Get an InChI Key descriptor from an InChI descriptor.
    Uses RDKit for the conversion.

    Args:
        inchi (str): The InChI descriptor.

    Returns:
        str: The InChI Key descriptor.
    """
    try:
        inchi_key = InchiToInchiKey(inchi)
    except:
        return None
    return inchi_key


def smiles_from_inchi(inchi: str) -> Union[str, None]:
    """
    Get a SMILES descriptor from an InChI descriptor.
    Uses RDKit for the conversion.

    Args:
        inchi (str): The InChI descriptor.

    Returns:
        str: The SMILES descriptor.
    """
    try:
        rd_mol = MolFromInchi(inchi)
        smiles = MolToSmiles(
            rd_mol,
            isomericSmiles=True,
            canonical=True,
            allBondsExplicit=False,
            allHsExplicit=False,
        )
    except:
        return None
    return smiles


def add_common_isotopes_to_coords(
    xyz: Dict[
        str,
        Union[Tuple[Tuple[float, float, float], ...], Tuple[int, ...], Tuple[str, ...]],
    ]
):
    """
    Add the common isotopes to the coordinates dictionary if it's missing.
    This function modifies the input dict instead of returning it.

    Args:
        xyz (dict): The coordinates dictionary
    """
    if ("isotopes" not in xyz or not xyz["isotopes"]) and "symbols" in xyz:
        xyz["isotopes"] = tuple(
            qcel.periodictable.to_A(symbol) for symbol in xyz["symbols"]
        )


def str_to_xyz(xyz_str: str) -> dict:
    """
    Convert a string xyz format to the xyz dictionary style.
    The xyz string format may have optional Gaussian-style isotope information, e.g.::

        C(Iso=13)    0.6616514836    0.4027481525   -0.4847382281
        N           -0.6039793084    0.6637270105    0.0671637135
        H           -1.4226865648   -0.4973210697   -0.2238712255
        H           -0.4993010635    0.6531020442    1.0853092315
        H           -2.2115796924   -0.4529256762    0.4144516252
        H           -1.8113671395   -0.3268900681   -1.1468957003

    which will also be parsed into the xyz dictionary format, e.g.::

        {'symbols': ('C', 'N', 'H', 'H', 'H', 'H'),
         'isotopes': (13, 14, 1, 1, 1, 1),
         'coords': ((0.6616514836, 0.4027481525, -0.4847382281),
                    (-0.6039793084, 0.6637270105, 0.0671637135),
                    (-1.4226865648, -0.4973210697, -0.2238712255),
                    (-0.4993010635, 0.6531020442, 1.0853092315),
                    (-2.2115796924, -0.4529256762, 0.4144516252),
                    (-1.8113671395, -0.3268900681, -1.1468957003))}

    Args:
        xyz_str (str): The string xyz format to be converted.

    Raises:
        TypeError: If xyz_str has an incorrect type.
        ValueError: If xyz_str is unreadable.

    Returns:
        dict: The xyz dictionary format.
    """
    if not isinstance(xyz_str, str):
        raise TypeError(f"Expected a string input, got {type(xyz_str)}")
    xyz_str = xyz_str.replace(",", " ")
    xyz_dict = {"symbols": tuple(), "isotopes": tuple(), "coords": tuple()}
    if all([len(line.split()) == 6 for line in xyz_str.splitlines() if line.strip()]):
        # Convert Gaussian output format, e.g., "      1          8           0        3.132319    0.769111   -0.080869"
        # not considering isotopes in this method!
        for line in xyz_str.splitlines():
            if line.strip():
                splits = line.split()
                symbol = qcel.periodictable.to_E(int(splits[1]))
                coord = (float(splits[3]), float(splits[4]), float(splits[5]))
                xyz_dict["symbols"] += (symbol,)
                xyz_dict["isotopes"] += (qcel.periodictable.to_A(symbol),)
                xyz_dict["coords"] += (coord,)
    else:
        # this is a "regular" string xyz format, if it has isotope information it will be preserved
        for line in xyz_str.strip().splitlines():
            if line.strip():
                splits = line.split()
                if len(splits) != 4:
                    raise ValueError(
                        f"xyz_str has an incorrect format, expected 4 elements in each line, "
                        f'got line "{line}" in:\n{xyz_str}'
                    )
                symbol = splits[0]
                if "(iso=" in symbol.lower():
                    isotope = int(symbol.split("=")[1].strip(")"))
                    symbol = symbol.split("(")[0]
                else:
                    # no specific isotope is specified in str_xyz, assume the common isotope
                    isotope = qcel.periodictable.to_A(symbol)
                coord = (float(splits[1]), float(splits[2]), float(splits[3]))
                xyz_dict["symbols"] += (symbol,)
                xyz_dict["isotopes"] += (isotope,)
                xyz_dict["coords"] += (coord,)
    return xyz_dict


def xyz_to_str(xyz_dict, isotope_format=None):
    """
    Convert an xyz dictionary format, e.g.::

        {'symbols': ('C', 'N', 'H', 'H', 'H', 'H'),
         'isotopes': (13, 14, 1, 1, 1, 1),
         'coords': ((0.6616514836, 0.4027481525, -0.4847382281),
                    (-0.6039793084, 0.6637270105, 0.0671637135),
                    (-1.4226865648, -0.4973210697, -0.2238712255),
                    (-0.4993010635, 0.6531020442, 1.0853092315),
                    (-2.2115796924, -0.4529256762, 0.4144516252),
                    (-1.8113671395, -0.3268900681, -1.1468957003))}

    to a string xyz format with optional Gaussian-style isotope specification, e.g.::

        C(Iso=13)    0.6616514836    0.4027481525   -0.4847382281
        N           -0.6039793084    0.6637270105    0.0671637135
        H           -1.4226865648   -0.4973210697   -0.2238712255
        H           -0.4993010635    0.6531020442    1.0853092315
        H           -2.2115796924   -0.4529256762    0.4144516252
        H           -1.8113671395   -0.3268900681   -1.1468957003

    Args:
        xyz_dict (dict): The ARC xyz format to be converted.
        isotope_format (str, optional): The format for specifying the isotope if it is not the most abundant one.
                                        By default, isotopes will *not* be specified. Currently the only supported
                                        option is 'gaussian'.

    Raises:
        TypeError: If xyz_dict has an incorrect type.
        ValueError: If xyz_dict is unreadable.

    Returns:
        str: The string xyz format.
    """
    if xyz_dict is None:
        return None
    recognized_isotope_formats = ["gaussian"]
    if any(
        [key not in list(xyz_dict.keys()) for key in ["symbols", "isotopes", "coords"]]
    ):
        raise ValueError(
            f'Missing keys in the xyz dictionary. Expected to find "symbols", "isotopes", and '
            f'"coords", but got {list(xyz_dict.keys())} in\n{xyz_dict}'
        )
    if any(
        [
            len(xyz_dict["isotopes"]) != len(xyz_dict["symbols"]),
            len(xyz_dict["coords"]) != len(xyz_dict["symbols"]),
        ]
    ):
        raise ValueError(
            f'Got different lengths for "symbols", "isotopes", and "coords": '
            f'{len(xyz_dict["symbols"])}, {len(xyz_dict["isotopes"])}, and {len(xyz_dict["coords"])}, '
            f"respectively, in xyz:\n{xyz_dict}"
        )
    if any([len(xyz_dict["coords"][i]) != 3 for i in range(len(xyz_dict["coords"]))]):
        raise ValueError(
            f"Expected 3 coordinates for each atom (x, y, and z), got:\n{xyz_dict}"
        )
    xyz_list = list()
    for symbol, isotope, coord in zip(
        xyz_dict["symbols"], xyz_dict["isotopes"], xyz_dict["coords"]
    ):
        common_isotope = qcel.periodictable.to_A(symbol)
        if isotope_format is not None and common_isotope != isotope:
            # consider the isotope number
            if isotope_format == "gaussian":
                element_with_isotope = f"{symbol}(Iso={isotope})"
                row = f"{element_with_isotope:14}"
            else:
                raise ValueError(
                    f"Recognized isotope formats for printing are {recognized_isotope_formats}, "
                    f"got: {isotope_format}"
                )
        else:
            # don't consider the isotope number
            row = f"{symbol:4}"
        row += "{0:14.8f}{1:14.8f}{2:14.8f}".format(*coord)
        xyz_list.append(row)
    return "\n".join(xyz_list)


def normalize_coordinates(coords_dict):
    """
    Convert lists to tuples in the coordinates dictionary

    Args:
        coords_dict (dict): The coordinates dictionary with lists

    Returns:
        dict: The coordinates dictionary with tuples
    """
    return {
        "symbols": tuple(coords_dict.get("symbols", [])),
        "isotopes": tuple(coords_dict.get("isotopes", [])),
        "coords": tuple(tuple(coord) for coord in coords_dict.get("coords", [])),
    }


def generate_check_digit(base_digits: str) -> str:
    """
    Generates the ORCID check digit as per ISO 7064 11,2.

    Args:
        base_digits (str): The base string of digits.

    Returns:
        str: The check digit.
    """
    total = 0
    for char in base_digits:
        digit = int(char)
        total = (total + digit) * 2
    remainder = total % 11
    result = (12 - remainder) % 11
    return "X" if result == 10 else str(result)


def is_linear(coordinates):
    """
    Determine whether or not the species is linear from its 3D coordinates
    First, try to reduce the problem into just two dimensions, use 3D if the problem cannot be reduced
    `coordinates` is a numpy.array of the species' xyz coordinates
    """
    # epsilon is in degrees
    # (from our experience, linear molecules have precisely 180.0 degrees between all atom triples)
    epsilon = 0.1

    number_of_atoms = len(coordinates)
    if number_of_atoms == 1:
        return False
    if number_of_atoms == 2:
        return True

    # A tensor containing all distance vectors in the molecule
    d = -np.array([c[:, np.newaxis] - c[np.newaxis, :] for c in coordinates.T])
    for i in range(2, len(coordinates)):
        u1 = d[:, 0, 1] / np.linalg.norm(
            d[:, 0, 1]
        )  # unit vector between atoms 0 and 1
        u2 = d[:, 1, i] / np.linalg.norm(
            d[:, 1, i]
        )  # unit vector between atoms 1 and i
        a = math.degrees(
            np.arccos(np.clip(np.dot(u1, u2), -1.0, 1.0))
        )  # angle between atoms 0, 1, i
        if abs(180 - a) > epsilon and abs(a) > epsilon:
            return False
    return True
