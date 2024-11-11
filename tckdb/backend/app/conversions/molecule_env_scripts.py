#!/usr/bin/env python
import argparse
import sys
from typing import Optional, Tuple

# Import RMG-Py modules
try:
    from molecule.exceptions import InvalidAdjacencyListError
    from molecule.molecule import Molecule
    from molecule.molecule.adjlist import from_adjacency_list
except ImportError as e:
    print(f"Import Error: {e}", file=sys.stderr)
    sys.exit(2)  # Specific exit code for import errors


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
    if not isinstance(adjlist, str):
        return False, f'An adjacency list must be a string, got "{type(adjlist)}".'
    try:
        from_adjacency_list(adjlist, group=False, saturate_h=False)
    except InvalidAdjacencyListError as e:
        return False, str(e)
    except Exception as e:
        return False, f"An error occurred: {e}"
    return True, ""


def convert_adjlist(adjlist: str) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """
    Convert an adjacency list to SMILES and InChI.

    Args:
        adjlist (str): The adjacency list as a multi-line string.

    Returns:
        Tuple[Optional[str], Optional[str], Optional[str]]:
            - SMILES string if conversion is successful, else None.
            - InChI string if conversion is successful, else None.
            - Error message if any, else None.
    """
    try:
        mol = Molecule().from_adjacency_list(adjlist)
        smiles = mol.to_smiles()
        inchi = mol.to_inchi()
        return smiles, inchi, None
    except Exception as e:
        return None, None, f"Conversion Error: {e}"


def multiplicity_from_adjlist(adjlist: str) -> Optional[int]:
    """
    Calculate the multiplicity of a molecule from its adjacency list.

    Args:
        adjlist (str): The adjacency list.

    Returns:
        Optional[int]: The multiplicity if successful, else None.
    """
    try:
        mol = from_adjacency_list(adjlist, group=False, saturate_h=False)
        multiplicity = mol[1]
        return multiplicity, None
    except Exception as e:
        return None, f"Multiplicity Calculation Error: {e}"


def main():
    parser = argparse.ArgumentParser(
        description="Validate and convert RMG adjacency lists."
    )
    subparsers = parser.add_subparsers(
        dest="command", required=True, help="Sub-commands: validate or convert"
    )

    # Subparser for validation
    validate_parser = subparsers.add_parser(
        "validate", help="Validate an adjacency list."
    )
    validate_parser.add_argument(
        "-f",
        "--file",
        type=str,
        help="Path to a file containing the adjacency list. If omitted, reads from standard input.",
    )

    # Subparser for conversion
    convert_parser = subparsers.add_parser(
        "convert", help="Convert an adjacency list to SMILES and InChI."
    )
    convert_parser.add_argument(
        "-f",
        "--file",
        type=str,
        help="Path to a file containing the adjacency list. If omitted, reads from standard input.",
    )

    multiplicity_parser = subparsers.add_parser(
        "multiplicity",
        help="Calculate the multiplicity of a molecule from its adjacency list.",
    )
    multiplicity_parser.add_argument(
        "-f",
        "--file",
        type=str,
        help="Path to a file containing the adjacency list. If omitted, reads from standard input.",
    )

    args = parser.parse_args()

    # Read adjacency list from file or stdin
    if args.file:
        try:
            with open(args.file, "r") as file:
                adjlist = file.read()
            print(f"Read adjacency list from {args.file}.", file=sys.stderr)
        except Exception as e:
            print(f"File Read Error: {e}", file=sys.stderr)
            sys.exit(1)  # Exit code 1 for file read errors
    else:
        adjlist = sys.stdin.read()
        print("Read adjacency list from standard input.", file=sys.stderr)

    if not adjlist:
        print("Error: No adjacency list provided.", file=sys.stderr)
        sys.exit(1)

    if args.command == "validate":
        valid, message = is_valid_adjlist(adjlist)
        if valid:
            print("True")
            sys.exit(0)
        else:
            print(f"False: {message}", file=sys.stderr)
            sys.exit(1)

    elif args.command == "convert":
        smiles, inchi, error = convert_adjlist(adjlist)
        if error:
            print(f"Error: {error}", file=sys.stderr)
            sys.exit(1)
        else:
            print(f"{smiles}\n{inchi}")
            sys.exit(0)

    elif args.command == "multiplicity":
        multiplicity, error = multiplicity_from_adjlist(adjlist)
        if error:
            print(f"Error: {error}", file=sys.stderr)
            sys.exit(1)
        else:
            print(f"{multiplicity}")
            sys.exit(0)


if __name__ == "__main__":
    main()
