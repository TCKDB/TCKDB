from __future__ import annotations

from pathlib import Path
from typing import Optional

import psycopg
from rdkit import Chem
from rdkit.Chem import inchi

DB_DSN = "postgresql://tckdb:tckdb@127.0.0.1:5432/tckdb_dev"
SDF_PATH = Path("/home/calvin/code/chemprop_cmpnn/DATA/SDF/kfir_rxn_679.sdf")
CREATED_BY = 1


STABLE_TYPES = {"r1h", "r2h", "r1", "r2"}
TS_TYPES = {"ts"}


def get_prop(mol: Chem.Mol, name: str, default: Optional[str] = None) -> Optional[str]:
    return mol.GetProp(name).strip() if mol.HasProp(name) else default


def parse_multiplicity(mol: Chem.Mol) -> int:
    raw = get_prop(mol, "multiplicity")
    if raw is None:
        total_radicals = sum(atom.GetNumRadicalElectrons() for atom in mol.GetAtoms())
        return total_radicals + 1
    return int(float(raw))


def formal_charge(mol: Chem.Mol) -> int:
    return sum(atom.GetFormalCharge() for atom in mol.GetAtoms())


def validate_mol(mol: Chem.Mol) -> None:
    if mol is None:
        raise ValueError("RDKit failed to parse molecule")
    test = Chem.Mol(mol)
    Chem.SanitizeMol(test)


def identity_mol_from_input(mol: Chem.Mol) -> Chem.Mol:
    ident = Chem.Mol(mol)
    for atom in ident.GetAtoms():
        atom.SetAtomMapNum(0)
    ident = Chem.RemoveHs(ident)
    Chem.SanitizeMol(ident)
    return ident


def validated_entry_mol(mol: Chem.Mol) -> Chem.Mol:
    entry = Chem.Mol(mol)
    Chem.SanitizeMol(entry)
    return entry


def ts_unmapped_smiles(mol: Chem.Mol) -> str:
    ts = Chem.Mol(mol)
    for atom in ts.GetAtoms():
        atom.SetAtomMapNum(0)
    Chem.SanitizeMol(ts)
    return Chem.MolToSmiles(ts, canonical=True)


def canonical_smiles(mol: Chem.Mol) -> str:
    ident = identity_mol_from_input(mol)
    return Chem.MolToSmiles(ident, canonical=True)


def inchikey_from_mol(mol: Chem.Mol) -> str:
    ident = identity_mol_from_input(mol)
    return inchi.MolToInchiKey(ident)


def molblock(mol: Chem.Mol) -> str:
    return Chem.MolToMolBlock(validated_entry_mol(mol))


def get_or_create_species(cur: psycopg.Cursor, mol: Chem.Mol) -> int:
    smiles = canonical_smiles(mol)
    ik = inchikey_from_mol(mol)
    charge = formal_charge(mol)
    multiplicity = parse_multiplicity(mol)

    cur.execute(
        """
        INSERT INTO species (kind, smiles, inchi_key, charge, multiplicity)
        VALUES ('molecule', %s, %s, %s, %s)
        ON CONFLICT (inchi_key) DO UPDATE
        SET smiles = EXCLUDED.smiles
        RETURNING id
        """,
        (smiles, ik, charge, multiplicity),
    )
    return cur.fetchone()[0]


def get_or_create_species_entry(
    cur: psycopg.Cursor,
    species_id: int,
    mol: Chem.Mol,
    entry_kind: str,
    created_by: int,
) -> int:
    cur.execute(
        """
        SELECT id
        FROM species_entry
        WHERE species_id = %s AND kind = %s
        ORDER BY id
        LIMIT 1
        """,
        (species_id, entry_kind),
    )
    row = cur.fetchone()
    if row:
        return row[0]

    ctab = molblock(mol)
    cur.execute(
        """
        INSERT INTO species_entry (species_id, kind, mol, created_by)
        VALUES (%s, %s, mol_from_ctab(%s), %s)
        RETURNING id
        """,
        (species_id, entry_kind, ctab, created_by),
    )
    return cur.fetchone()[0]


def get_or_create_reaction(cur: psycopg.Cursor, reaction_key: str) -> int:
    # Placeholder stable hash strategy for now; replace with canonical reaction hashing later.
    # We keep it deterministic per imported bundle key for version 1.
    padded = (reaction_key[:64]).ljust(64, "0")
    cur.execute(
        """
        INSERT INTO chem_reaction (stoichiometry_hash, reversible)
        VALUES (%s, TRUE)
        ON CONFLICT (stoichiometry_hash) DO UPDATE
        SET reversible = EXCLUDED.reversible
        RETURNING id
        """,
        (padded,),
    )
    return cur.fetchone()[0]


def get_or_create_reaction_entry(
    cur: psycopg.Cursor, reaction_id: int, created_by: int
) -> int:
    cur.execute(
        """
        SELECT id
        FROM reaction_entry
        WHERE reaction_id = %s
        ORDER BY id
        LIMIT 1
        """,
        (reaction_id,),
    )
    row = cur.fetchone()
    if row:
        return row[0]

    cur.execute(
        """
        INSERT INTO reaction_entry (reaction_id, created_by)
        VALUES (%s, %s)
        RETURNING id
        """,
        (reaction_id, created_by),
    )
    return cur.fetchone()[0]


def get_or_create_transition_state(
    cur: psycopg.Cursor, reaction_entry_id: int, label: str, created_by: int
) -> int:
    cur.execute(
        """
        SELECT id
        FROM transition_state
        WHERE reaction_entry_id = %s
        ORDER BY id
        LIMIT 1
        """,
        (reaction_entry_id,),
    )
    row = cur.fetchone()
    if row:
        return row[0]

    cur.execute(
        """
        INSERT INTO transition_state (reaction_entry_id, label, created_by)
        VALUES (%s, %s, %s)
        RETURNING id
        """,
        (reaction_entry_id, label, created_by),
    )
    return cur.fetchone()[0]


def get_or_create_transition_state_entry(
    cur: psycopg.Cursor,
    transition_state_id: int,
    mol: Chem.Mol,
    status: str,
    created_by: int,
) -> int:
    unmapped = ts_unmapped_smiles(mol)
    cur.execute(
        """
        SELECT id, unmapped_smiles
        FROM transition_state_entry
        WHERE transition_state_id = %s
        ORDER BY id
        LIMIT 1
        """,
        (transition_state_id,),
    )
    row = cur.fetchone()
    if row:
        if row[1] is None:
            cur.execute(
                """
                UPDATE transition_state_entry
                SET unmapped_smiles = %s
                WHERE id = %s
                """,
                (unmapped, row[0]),
            )
        return row[0]

    ctab = molblock(mol)
    cur.execute(
        """
        INSERT INTO transition_state_entry (transition_state_id, mol, unmapped_smiles, status, created_by)
        VALUES (%s, mol_from_ctab(%s), %s, %s, %s)
        RETURNING id
        """,
        (transition_state_id, ctab, unmapped, status, created_by),
    )
    return cur.fetchone()[0]


def insert_reaction_participant(
    cur: psycopg.Cursor,
    reaction_id: int,
    species_id: int,
    role: str,
    stoichiometry: int = 1,
) -> None:
    cur.execute(
        """
        INSERT INTO reaction_participant (reaction_id, species_id, role, stoichiometry)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT DO NOTHING
        """,
        (reaction_id, species_id, role, stoichiometry),
    )


def classify_role(record_type: str) -> str:
    if record_type in {"r1h", "r2h"}:
        return "reactant"
    if record_type in {"r1", "r2"}:
        return "product"
    raise ValueError(f"Cannot classify role for type={record_type!r}")


def classify_species_entry_kind(record_type: str) -> str:
    if record_type in STABLE_TYPES:
        return "minimum"
    raise ValueError(f"Cannot classify species_entry kind for type={record_type!r}")


def import_bundle(
    records: list[Chem.Mol], conn: psycopg.Connection, created_by: int
) -> None:
    reaction_name = None
    stable_species_ids: list[tuple[int, str]] = []
    ts_mol: Optional[Chem.Mol] = None

    with conn.cursor() as cur:
        for mol in records:
            validate_mol(mol)
            record_type = get_prop(mol, "type")
            rxn = get_prop(mol, "reaction") or get_prop(mol, "rxn")

            if reaction_name is None:
                reaction_name = rxn

            if record_type in STABLE_TYPES:
                species_id = get_or_create_species(cur, mol)
                get_or_create_species_entry(
                    cur=cur,
                    species_id=species_id,
                    mol=mol,
                    entry_kind=classify_species_entry_kind(record_type),
                    created_by=created_by,
                )
                stable_species_ids.append((species_id, classify_role(record_type)))

            elif record_type in TS_TYPES:
                ts_mol = mol

            else:
                raise ValueError(f"Unsupported record type: {record_type!r}")

        if reaction_name is None:
            raise ValueError("No reaction key found in bundle.")

        reaction_id = get_or_create_reaction(cur, reaction_name)

        for species_id, role in stable_species_ids:
            insert_reaction_participant(cur, reaction_id, species_id, role, 1)

        if ts_mol is not None:
            reaction_entry_id = get_or_create_reaction_entry(
                cur, reaction_id, created_by
            )
            ts_id = get_or_create_transition_state(
                cur, reaction_entry_id, "TS0", created_by
            )
            ts_entry_id = get_or_create_transition_state_entry(
                cur, ts_id, ts_mol, "validated", created_by
            )

            cur.execute(
                """
                UPDATE reaction_entry
                SET preferred_ts_entry_id = %s
                WHERE id = %s
                """,
                (ts_entry_id, reaction_entry_id),
            )


def group_by_reaction(mols: list[Chem.Mol]) -> dict[str, list[Chem.Mol]]:
    grouped: dict[str, list[Chem.Mol]] = {}
    for mol in mols:
        rxn = get_prop(mol, "reaction") or get_prop(mol, "rxn")
        if rxn is None:
            raise ValueError("SDF record missing reaction/rxn property.")
        grouped.setdefault(rxn, []).append(mol)
    return grouped


def main() -> None:
    supplier = Chem.SDMolSupplier(str(SDF_PATH), removeHs=False)
    mols = [mol for mol in supplier if mol is not None]

    grouped = group_by_reaction(mols)

    with psycopg.connect(DB_DSN) as conn:
        for rxn_name, records in grouped.items():
            try:
                with conn.transaction():
                    import_bundle(records, conn, CREATED_BY)
                print(f"Imported bundle: {rxn_name}")
            except Exception as e:
                print(f"FAILED bundle {rxn_name}: {e}")


if __name__ == "__main__":
    main()
