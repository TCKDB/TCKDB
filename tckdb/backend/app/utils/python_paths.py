# find_molecule_env_python.py

import os
import sys
from typing import Optional


def find_molecule_env_python() -> Optional[str]:
    """
    Searches for the Python executable within the 'molecule_env' Conda environment
    by checking multiple common installation paths.

    Returns:
        Optional[str]: The full path to the Python executable if found, else None.
    """
    home = os.path.expanduser("~")

    molecule_pypath_1 = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(sys.executable))),
        "molecule_env",
        "bin",
        "python",
    )
    molecule_pypath_2 = os.path.join(
        home, "anaconda3", "envs", "molecule_env", "bin", "python"
    )
    molecule_pypath_3 = os.path.join(
        home, "miniconda3", "envs", "molecule_env", "bin", "python"
    )
    molecule_pypath_4 = os.path.join(
        home, ".conda", "envs", "molecule_env", "bin", "python"
    )
    molecule_pypath_5 = os.path.join(
        "/Local/ce_dana", "anaconda3", "envs", "molecule_env", "bin", "python"
    )

    potential_paths = [
        molecule_pypath_1,
        molecule_pypath_2,
        molecule_pypath_3,
        molecule_pypath_4,
        molecule_pypath_5,
    ]

    for molecule_pypath in potential_paths:
        if os.path.isfile(molecule_pypath):
            return molecule_pypath

    return None


MOLECULE_PYTHON = find_molecule_env_python()

if MOLECULE_PYTHON is None:
    raise FileNotFoundError(
        "Python executable for 'molecule_env' not found. "
        "Please ensure that the 'molecule_env' environment exists and the path is correct."
    )
