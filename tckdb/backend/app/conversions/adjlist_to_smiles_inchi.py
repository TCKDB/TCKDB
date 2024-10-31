#!/usr/bin/env python

# This script reads an adjacency list from the command-line or standard input,
# converts it to a SMILES and InChI string, and prints the result.
# Relies on molecule_env being installed.

import sys

from molecule.molecule import Molecule


def main():
    if len(sys.argv) > 1:
        # Read adjacency list from command-line argument
        adjlist = sys.argv[1]
    else:
        # Read adjacency list from standard input
        adjlist = sys.stdin.read()

    if not adjlist.strip():
        print("Error: No adjacency list provided.", file=sys.stderr)
        sys.exit(1)

    try:
        mol = Molecule().from_adjacency_list(adjlist)
        smiles = mol.to_smiles()
        inchi = mol.to_inchi()
        print(f"{smiles}\n{inchi}")
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
