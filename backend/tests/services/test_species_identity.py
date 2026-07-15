"""Species identity tests (DR-0031).

Identity = canonical SMILES + charge + multiplicity. Covers:
- multiplicity is authoritative (spin states representable): singlet vs
  triplet CH2 resolve to distinct species;
- tautomers that standard InChIKey merges resolve to distinct species;
- cross-notation SMILES for the same molecule still dedup to one species;
- charge is still validated against the SMILES.

These tests roll back their transaction (rather than the commit-on-exit
pattern) so they never pollute the shared session-scoped test DB.
"""

from __future__ import annotations

from contextlib import contextmanager

import pytest
from rdkit import Chem
from rdkit.Chem import AllChem, inchi
from sqlalchemy.orm import Session

from app.chemistry.species import canonical_species_identity
from app.schemas.fragments.identity import SpeciesEntryIdentityPayload
from app.services.species_resolution import resolve_species, resolve_species_entry


def _identity(smiles: str, *, charge: int = 0, multiplicity: int) -> SpeciesEntryIdentityPayload:
    return SpeciesEntryIdentityPayload(
        smiles=smiles, charge=charge, multiplicity=multiplicity
    )


@contextmanager
def _rolled_back_session(db_engine):
    """A session whose work is always rolled back, to isolate the test."""
    with Session(db_engine) as session:
        trans = session.begin()
        try:
            yield session
        finally:
            trans.rollback()


# ---------------------------------------------------------------------------
# Chemistry-layer: multiplicity is no longer validated against SMILES
# ---------------------------------------------------------------------------


class TestCanonicalIdentityMultiplicity:
    def test_singlet_ch2_is_accepted_despite_smiles_implying_triplet(self) -> None:
        # RDKit reads [CH2] as a triplet (2 radical electrons); a singlet
        # carbene has the same connectivity. This must NOT be rejected.
        canonical_smiles, inchi_key = canonical_species_identity(
            _identity("[CH2]", multiplicity=1)
        )
        assert canonical_smiles  # canonicalized, non-empty
        assert len(inchi_key) == 27

    def test_charge_mismatch_still_rejected(self) -> None:
        # Charge is explicit in SMILES and remains validated.
        with pytest.raises(ValueError, match="does not match SMILES charge"):
            canonical_species_identity(_identity("[OH-]", charge=0, multiplicity=1))


# ---------------------------------------------------------------------------
# Resolution-layer: identity semantics
# ---------------------------------------------------------------------------


def test_singlet_and_triplet_ch2_are_distinct_species(db_engine) -> None:
    with _rolled_back_session(db_engine) as session:
        singlet = resolve_species(session, _identity("[CH2]", multiplicity=1))
        triplet = resolve_species(session, _identity("[CH2]", multiplicity=3))
        session.flush()

        assert singlet.id != triplet.id
        assert singlet.multiplicity == 1
        assert triplet.multiplicity == 3
        # Same graph → same canonical SMILES and same InChIKey; the spin
        # state is what makes them distinct species.
        assert singlet.smiles == triplet.smiles
        assert singlet.inchi_key == triplet.inchi_key


def test_tautomers_merged_by_inchikey_are_distinct_species(db_engine) -> None:
    # 2-pyridone vs 2-hydroxypyridine: distinct structures, but standard
    # InChIKey's mobile-H layer merges them.
    pyridone = "O=c1cccc[nH]1"
    hydroxypyridine = "Oc1ccccn1"
    with _rolled_back_session(db_engine) as session:
        s1 = resolve_species(session, _identity(pyridone, multiplicity=1))
        s2 = resolve_species(session, _identity(hydroxypyridine, multiplicity=1))
        session.flush()

        assert s1.id != s2.id
        assert s1.smiles != s2.smiles  # distinct canonical SMILES


def test_same_molecule_different_notation_dedups(db_engine) -> None:
    # Ethanol written two ways must resolve to ONE species.
    with _rolled_back_session(db_engine) as session:
        a = resolve_species(session, _identity("CCO", multiplicity=1))
        b = resolve_species(session, _identity("OCC", multiplicity=1))
        session.flush()
        assert a.id == b.id


def _xyz_from_smiles(smiles: str, *, seed: int = 0xC0FFEE) -> str:
    """Embed a 3D conformer for ``smiles`` and return its XYZ block."""
    mol = Chem.AddHs(Chem.MolFromSmiles(smiles))
    assert AllChem.EmbedMolecule(mol, randomSeed=seed) == 0, smiles
    AllChem.MMFFOptimizeMolecule(mol)
    return Chem.MolToXYZBlock(mol)


def test_cis_and_trans_diazene_resolve_to_distinct_entries(db_engine) -> None:
    """Geometry-derived stereo labels split stereoisomers at the entry level.

    Same SMILES ``N=N`` (same Species), cis vs trans 3D geometry. Before the
    ``derive_stereo_label_from_3d`` repair both got ``stereo_label=NULL`` and
    merged into ONE ``SpeciesEntry`` (the constraint is NULLS NOT DISTINCT).
    They must now be two distinct entries with labels Z and E.
    """
    cis_xyz = _xyz_from_smiles(r"[H]/N=N\[H]")
    trans_xyz = _xyz_from_smiles(r"[H]/N=N/[H]")
    with _rolled_back_session(db_engine) as session:
        cis_entry = resolve_species_entry(
            session, _identity("N=N", multiplicity=1), xyz_text=cis_xyz
        )
        trans_entry = resolve_species_entry(
            session, _identity("N=N", multiplicity=1), xyz_text=trans_xyz
        )
        session.flush()

        # Same underlying Species (identity is graph + charge + multiplicity)…
        assert cis_entry.species_id == trans_entry.species_id
        # …but two distinct entries carrying the geometry-derived labels.
        assert cis_entry.id != trans_entry.id
        assert cis_entry.stereo_label == "Z"
        assert trans_entry.stereo_label == "E"

        # Re-resolving the same geometry must dedup back onto the same entry.
        cis_again = resolve_species_entry(
            session, _identity("N=N", multiplicity=1), xyz_text=cis_xyz
        )
        assert cis_again.id == cis_entry.id


def test_inchikey_can_map_to_multiple_species(db_engine) -> None:
    """After the identity change, an InChIKey is no longer unique: the two
    CH2 spin states share one InChIKey but are two species rows."""
    ch2_inchikey = inchi.MolToInchiKey(Chem.MolFromSmiles("[CH2]"))
    with _rolled_back_session(db_engine) as session:
        singlet = resolve_species(session, _identity("[CH2]", multiplicity=1))
        triplet = resolve_species(session, _identity("[CH2]", multiplicity=3))
        session.flush()
        assert singlet.inchi_key == ch2_inchikey
        assert triplet.inchi_key == ch2_inchikey
        assert singlet.id != triplet.id
