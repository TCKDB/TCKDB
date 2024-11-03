import os

# trunk-ignore(bandit/B404)
import subprocess
import sys
from typing import Optional, Tuple

from tckdb.backend.app.utils.python_paths import MOLECULE_PYTHON


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

        # trunk-ignore(bandit/B603)
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


def multiplicity_from_adjlist(adjlist: str) -> Optional[int]:
    """
    Calculate the multiplicity of a molecule from its adjacency list.

    Args:
        adjlist (str): The adjacency list.

    Returns:
        Optional[int]: The multiplicity if successful, else None.
    """
    try:
        script_dir = os.path.dirname(os.path.abspath(__file__))
        conversion_script = os.path.join(script_dir, "molecule_env_scripts.py")
        cmd = [MOLECULE_PYTHON, conversion_script, "multiplicity"]

        # trunk-ignore(bandit/B603)
        result = subprocess.run(
            cmd, input=adjlist, text=True, capture_output=True, check=True
        )

        # Parse
        output = result.stdout.strip()
        if output:
            multiplicity = int(float(output))
            return multiplicity
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
