"""The entry stereo descriptor CHECK accepts allene ``Ra``/``Sa`` only as whole tokens."""

from __future__ import annotations

import pytest
from rdkit import Chem
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError


def _species(db_session, smiles: str) -> int:
    return db_session.scalar(
        text(
            "INSERT INTO species (kind, smiles, inchi_key, charge, multiplicity, stereo_kind) "
            "VALUES (CAST('molecule' AS molecule_kind), :s, :k, 0, 1, CAST('enantiomer' AS stereo_kind)) RETURNING id"
        ),
        {"s": smiles, "k": Chem.MolToInchiKey(Chem.MolFromSmiles(smiles))},
    )


def _entry(db_session, species: int, smiles: str, descriptor: str, resolution: str, term_symbol: str) -> None:
    db_session.execute(
        text(
            "INSERT INTO species_entry (species_id, mol, stereo_descriptor, stereo_resolution, stereo_label, term_symbol) "
            "VALUES (:sp, mol_from_smiles(CAST(:m AS cstring)), :d, CAST(:r AS stereo_resolution), :l, :t)"
        ),
        {"sp": species, "m": smiles, "d": descriptor, "r": resolution, "l": descriptor, "t": term_symbol},
    )


def test_the_descriptor_check_accepts_ra_sa_as_whole_tokens(db_session) -> None:
    species = _species(db_session, "CC=C=CC")
    for number, descriptor in enumerate(("Ra", "Sa", "R,Ra,?", "Ra,Sa")):
        with db_session.begin_nested():
            _entry(db_session, species, "CC=C=CC", descriptor, "resolved", f"T{number}")
    for number, bad in enumerate(("Rb", "RA", "Ra,", "a", "R a", "ra")):
        with pytest.raises(IntegrityError), db_session.begin_nested():
            _entry(db_session, species, "CC=C=CC", bad, "resolved", f"B{number}")
