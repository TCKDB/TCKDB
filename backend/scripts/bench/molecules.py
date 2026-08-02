"""Chemically real molecule generation for the Stage 4 benchmark corpus.

Why not random strings
----------------------
The benchmark has to exercise the RDKit GiST index on ``species_entry.mol``
and the ``mol_formula(mol_from_smiles(...))`` expression index on
``species.smiles``. Both are only meaningful over molecules the cartridge can
actually parse, fingerprint and substructure-match. Random strings would fail
``mol_from_smiles`` (silently producing NULL, which the search code treats as a
non-match) and would give every species a distinct formula, so the
formula-search shape — the broad one we most need to measure — would degenerate
to a one-row lookup.

How the corpus is grown
-----------------------
Start from a seed set of real combustion-chemistry species and grow by
*substitution*: replace an implicit hydrogen on a heavy atom with a substituent
drawn from a CHON fragment set, sanitize through RDKit, canonicalize, and
dedupe. Every emitted SMILES is therefore a valence-legal molecule RDKit has
sanitized, and the growth process naturally produces large families of
constitutional isomers — many distinct species sharing one molecular formula,
which is exactly the cardinality that makes a formula search a broad search.

Radical and ion character follow combustion reality rather than being uniform:
most species are closed-shell singlets, a substantial minority are doublet
radicals, and a small tail are triplets and ions.
"""

from __future__ import annotations

import random
from collections.abc import Iterator
from dataclasses import dataclass

from rdkit import Chem, RDLogger
from rdkit.Chem import rdMolDescriptors

# RDKit prints sanitization complaints to stderr for every rejected candidate.
# The generator rejects candidates by design, so the noise is pure distraction.
RDLogger.DisableLog("rdApp.*")


#: Real combustion / pyrolysis species used as growth seeds. Chosen to span
#: the CHON space TCKDB actually stores: alkanes, alkenes, alkynes, aromatics,
#: alcohols, aldehydes, ketones, ethers, acids, esters, amines, nitriles and
#: the small-molecule bath/product set.
SEED_SMILES: tuple[str, ...] = (
    # small molecules and bath gases
    "O", "C", "CO", "C=O", "O=C=O", "N", "C#N", "N#N", "O=O",
    # alkanes
    "CC", "CCC", "CCCC", "CC(C)C", "CCCCC", "CC(C)CC", "CC(C)(C)C",
    "CCCCCC", "CCCCCCC", "C1CCCCC1", "C1CCCC1", "C1CCC1",
    # alkenes / alkynes / dienes
    "C=C", "CC=C", "CC=CC", "C=CC=C", "C#C", "CC#C", "C=CCC", "CC(=C)C",
    # aromatics
    "c1ccccc1", "Cc1ccccc1", "c1ccc2ccccc2c1", "Cc1ccccc1C", "c1ccc(cc1)C=C",
    # oxygenates
    "CO", "CCO", "CCCO", "CC(C)O", "COC", "CCOCC", "C=O", "CC=O", "CCC=O",
    "CC(C)=O", "CCC(C)=O", "OC=O", "CC(=O)O", "COC=O", "CCOC(C)=O",
    "OCCO", "C1CO1", "C1CCOC1", "c1ccc(cc1)O",
    # peroxides / hydroperoxides (combustion-critical)
    "COO", "CCOO", "OO", "COOC",
    # nitrogen
    "CN", "CCN", "CNC", "CC#N", "CCC#N", "c1ccc(cc1)N", "NN", "CN(C)C",
    "C[N+](=O)[O-]", "NC=O", "CC(N)=O",
)

#: Substituent SMILES fragments attached at a hydrogen position. ``*`` marks
#: the attachment point consumed by :func:`_substitute`.
SUBSTITUENTS: tuple[str, ...] = (
    "*C", "*CC", "*CCC", "*C(C)C", "*C(C)(C)C",
    "*O", "*OC", "*OO", "*C=O", "*C(C)=O", "*C(=O)O",
    "*N", "*NC", "*C#N", "*C=C", "*C#C", "*c1ccccc1",
)


@dataclass(frozen=True)
class BenchSpecies:
    """One generated species identity, ready to insert into ``species``."""

    smiles: str
    inchi_key: str
    charge: int
    multiplicity: int
    formula: str
    heavy_atoms: int


def _canonical(mol: Chem.Mol) -> str | None:
    """Sanitize and canonicalize, returning ``None`` for anything illegal."""
    try:
        Chem.SanitizeMol(mol)
    except (ValueError, RuntimeError):
        return None
    return Chem.MolToSmiles(mol)


def _substitute(parent: str, substituent: str, rng: random.Random) -> str | None:
    """Attach ``substituent`` at one random hydrogen-bearing heavy atom.

    Returns the canonical SMILES of the product, or ``None`` when the
    combination is not a legal molecule (over-valent carbon, broken aromatic
    system, and so on). Rejection is expected and cheap.
    """
    mol = Chem.MolFromSmiles(parent)
    if mol is None:
        return None
    candidates = [a.GetIdx() for a in mol.GetAtoms() if a.GetTotalNumHs() > 0]
    if not candidates:
        return None

    frag = Chem.MolFromSmiles(substituent.replace("*", "[*]"))
    if frag is None:
        return None

    combined = Chem.RWMol(Chem.CombineMols(mol, frag))
    offset = mol.GetNumAtoms()
    dummy = next(
        (a.GetIdx() for a in combined.GetAtoms()
         if a.GetIdx() >= offset and a.GetAtomicNum() == 0),
        None,
    )
    if dummy is None:
        return None

    # The dummy's single neighbour becomes the new bond partner.
    neighbours = [n.GetIdx() for n in combined.GetAtomWithIdx(dummy).GetNeighbors()]
    if len(neighbours) != 1:
        return None
    attach_to = neighbours[0]

    anchor = rng.choice(candidates)
    combined.RemoveAtom(dummy)
    # Removing the dummy shifts every higher index down by one.
    shifted = attach_to - 1 if attach_to > dummy else attach_to
    try:
        combined.AddBond(anchor, shifted, Chem.BondType.SINGLE)
    except (ValueError, RuntimeError):
        return None
    return _canonical(combined.GetMol())


def _radicalize(
    smiles: str, rng: random.Random, *, sites: int = 1
) -> str | None:
    """Abstract ``sites`` hydrogens, as combustion's H-abstraction does.

    ``sites=1`` gives a doublet radical (the dominant open-shell species);
    ``sites=2`` gives a biradical, recorded as a triplet.
    """
    mol = Chem.MolFromSmiles(smiles)
    if mol is None:
        return None
    candidates = [
        a.GetIdx() for a in mol.GetAtoms()
        if a.GetTotalNumHs() > 0 and not a.GetIsAromatic()
    ]
    if len(candidates) < sites:
        return None
    rw = Chem.RWMol(mol)
    for index in rng.sample(candidates, sites):
        if _abstract_one(rw.GetAtomWithIdx(index)) is None:
            return None
    return _canonical(rw.GetMol())


def _abstract_one(atom: Chem.Atom) -> bool | None:
    """Remove one hydrogen from ``atom`` and leave a radical electron."""
    # Read the hydrogen count BEFORE pinning the count, because
    # ``SetNoImplicit(True)`` makes ``GetTotalNumHs()`` report explicit
    # hydrogens only — normally zero. Reading it afterwards drops *every*
    # hydrogen, leaving an under-valent atom that sanitization repairs by
    # adding a second radical electron, so what was meant to be a doublet
    # radical silently became a carbene.
    total_hs = atom.GetTotalNumHs()
    if total_hs < 1:
        return None
    atom.SetNoImplicit(True)
    atom.SetNumExplicitHs(total_hs - 1)
    atom.SetNumRadicalElectrons(atom.GetNumRadicalElectrons() + 1)
    return True


def _ionize(smiles: str, rng: random.Random) -> str | None:
    """Protonate a basic N or deprotonate an acidic O, making a real ion."""
    mol = Chem.MolFromSmiles(smiles)
    if mol is None:
        return None
    rw = Chem.RWMol(mol)
    nitrogens = [
        a.GetIdx() for a in rw.GetAtoms()
        if a.GetSymbol() == "N" and a.GetFormalCharge() == 0
        and a.GetNumRadicalElectrons() == 0 and a.GetTotalValence() == 3
    ]
    hydroxyls = [
        a.GetIdx() for a in rw.GetAtoms()
        if a.GetSymbol() == "O" and a.GetFormalCharge() == 0
        and a.GetNumRadicalElectrons() == 0 and a.GetTotalNumHs() == 1
    ]
    if nitrogens and (not hydroxyls or rng.random() < 0.5):
        atom = rw.GetAtomWithIdx(rng.choice(nitrogens))
        atom.SetFormalCharge(1)
        atom.SetNumExplicitHs(atom.GetTotalNumHs() + 1)
        atom.SetNoImplicit(True)
    elif hydroxyls:
        atom = rw.GetAtomWithIdx(rng.choice(hydroxyls))
        atom.SetFormalCharge(-1)
        atom.SetNumExplicitHs(0)
        atom.SetNoImplicit(True)
    else:
        return None
    return _canonical(rw.GetMol())


def _spin_and_charge(mol: Chem.Mol) -> tuple[int, int]:
    """Derive (charge, multiplicity) directly from the sanitized molecule.

    Multiplicity is the RDKit radical-electron count plus one, with no random
    component: a species generated as a mono-radical really is a doublet and a
    closed-shell species really is a singlet. Deriving rather than sampling is
    what keeps the multiplicity distribution chemically honest — an earlier
    version sampled a triplet tail and let radicals be grown recursively, which
    produced multiplicity-11 "species" that do not exist.
    """
    charge = Chem.GetFormalCharge(mol)
    radicals = sum(a.GetNumRadicalElectrons() for a in mol.GetAtoms())
    return charge, radicals + 1


def generate_species(
    count: int,
    *,
    seed: int = 20260801,
    radical_fraction: float = 0.35,
    biradical_fraction: float = 0.08,
    ion_fraction: float = 0.02,
    max_heavy_atoms: int = 14,
) -> list[BenchSpecies]:
    """Generate ``count`` distinct, RDKit-sanitized species identities.

    Identity uniqueness matches the database's own ``uq_species_identity``
    constraint — ``(smiles, charge, multiplicity)`` — so the returned list can
    be inserted without conflict handling.

    :param count: number of distinct species to return.
    :param seed: RNG seed; generation is deterministic for a given seed.
    :param radical_fraction: fraction of growth products that get a hydrogen
        abstracted, producing doublet radicals.
    :param max_heavy_atoms: growth ceiling, keeping molecules in the size range
        a kinetics database actually holds.
    """
    rng = random.Random(seed)
    seen: set[tuple[str, int, int]] = set()
    out: list[BenchSpecies] = []
    # Growth pool: **closed-shell** molecules eligible for further
    # substitution. Radicals and ions are terminal products and never re-enter
    # the pool, so radical electrons and formal charges cannot accumulate
    # across generations.
    pool: list[str] = []

    def emit(smiles: str) -> bool:
        mol = Chem.MolFromSmiles(smiles)
        if mol is None:
            return False
        heavy = mol.GetNumHeavyAtoms()
        if heavy == 0 or heavy > max_heavy_atoms:
            return False
        charge, multiplicity = _spin_and_charge(mol)
        key = (smiles, charge, multiplicity)
        if key in seen:
            return False
        inchi_key = Chem.MolToInchiKey(mol)
        if not inchi_key or len(inchi_key) != 27:
            return False
        seen.add(key)
        out.append(
            BenchSpecies(
                smiles=smiles,
                inchi_key=inchi_key,
                charge=charge,
                multiplicity=multiplicity,
                formula=rdMolDescriptors.CalcMolFormula(mol),
                heavy_atoms=heavy,
            )
        )
        return True

    for smiles in SEED_SMILES:
        canonical = _canonical(Chem.MolFromSmiles(smiles))
        if canonical is None:
            continue
        if emit(canonical):
            pool.append(canonical)
        elif canonical not in pool:
            pool.append(canonical)

    # Grow until the target count is reached. The attempt ceiling stops a
    # pathological seed from looping forever; in practice the yield rate is
    # high enough that it is never hit.
    attempts = 0
    max_attempts = count * 200
    while len(out) < count and attempts < max_attempts:
        attempts += 1
        parent = rng.choice(pool)
        product = _substitute(parent, rng.choice(SUBSTITUENTS), rng)
        if product is None:
            continue
        mol = Chem.MolFromSmiles(product)
        if mol is None or mol.GetNumHeavyAtoms() > max_heavy_atoms:
            continue

        # The closed-shell product is the growth carrier.
        grew = emit(product)
        if grew and mol.GetNumHeavyAtoms() < max_heavy_atoms:
            pool.append(product)

        # Terminal derivatives: a doublet radical (combustion's dominant open-
        # shell species) and, rarely, an ion. Neither re-enters the pool.
        if rng.random() < radical_fraction:
            # Biradicals are a real but minor combustion population; most
            # abstraction products are mono-radical doublets.
            sites = 2 if rng.random() < biradical_fraction else 1
            radical = _radicalize(product, rng, sites=sites)
            if radical is not None:
                emit(radical)
        if rng.random() < ion_fraction:
            ion = _ionize(product, rng)
            if ion is not None:
                emit(ion)

    if len(out) < count:
        raise RuntimeError(
            f"molecule generator produced only {len(out)} of {count} requested "
            f"species after {attempts} attempts; widen SUBSTITUENTS or raise "
            f"max_heavy_atoms"
        )
    return out


def formula_histogram(species: list[BenchSpecies]) -> dict[str, int]:
    """Count species per molecular formula — the isomer-family size profile."""
    histogram: dict[str, int] = {}
    for entry in species:
        histogram[entry.formula] = histogram.get(entry.formula, 0) + 1
    return histogram


def iter_batches(items: list, size: int) -> Iterator[list]:
    """Yield ``items`` in lists of at most ``size``."""
    for start in range(0, len(items), size):
        yield items[start : start + size]
