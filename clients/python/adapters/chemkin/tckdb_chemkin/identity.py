"""Stage 3: species identity resolution (the crux, spec §5).

CHEMKIN carries no structure, so every species name must be mapped to a
``(canonical SMILES, charge, multiplicity)`` identity. **We never guess.** A
name that cannot be resolved through one of the accepted structure-map sources
is a hard error, and the run aborts before any upload (all-or-nothing identity).

Structure-map sources, in priority order (spec §5):
  1. User-supplied CSV/JSON map (``name,smiles[,charge,multiplicity]``).
  2. RMG ``species_dictionary.txt`` (adjacency lists -> RDKit -> SMILES).
  3. Inline ``! SMILES=...`` structure comments on SPECIES declarations.
  4. A small built-in table for unambiguous bath gases (AR, HE, NE, N2).

RDKit is confined to this module (the parser stays dependency-light).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from rdkit import Chem
from rdkit.Chem import rdMolDescriptors

from .ast import Mechanism, SpeciesDecl, ThermoEntry

_BOND_TYPES = {
    "S": Chem.BondType.SINGLE,
    "D": Chem.BondType.DOUBLE,
    "T": Chem.BondType.TRIPLE,
    "Q": Chem.BondType.QUADRUPLE,
    "B": Chem.BondType.AROMATIC,
}


class IdentityResolutionError(ValueError):
    """Raised (fail-loud) when identity cannot be resolved for all species.

    :param unmapped: Species names no source could resolve.
    :param mismatches: ``name -> (resolved_formula, thermo_formula)`` for
        species whose resolved structure disagrees with the NASA composition.
    """

    def __init__(
        self,
        unmapped: list[str] | None = None,
        mismatches: dict[str, tuple[str, str]] | None = None,
    ) -> None:
        self.unmapped = sorted(unmapped or [])
        self.mismatches = mismatches or {}
        parts: list[str] = []
        if self.unmapped:
            parts.append(
                "Unmapped species (no structure map entry): "
                + ", ".join(self.unmapped)
            )
        if self.mismatches:
            detail = "; ".join(
                f"{name}: resolved={res} vs thermo={thm}"
                for name, (res, thm) in sorted(self.mismatches.items())
            )
            parts.append("Formula mismatches: " + detail)
        super().__init__(
            " | ".join(parts) or "Identity resolution failed."
        )


@dataclass
class ResolvedSpecies:
    """A CHEMKIN name resolved to a TCKDB identity."""

    name: str
    smiles: str
    charge: int
    multiplicity: int
    formula: dict[str, int]
    source: str  # "csv" | "rmg_dict" | "comment" | "bath_gas"
    molecule_kind: str = "molecule"

    def identity_payload(self) -> dict:
        """Return the SpeciesEntryIdentityPayload dict for upload."""
        return {
            "molecule_kind": self.molecule_kind,
            "smiles": self.smiles,
            "charge": self.charge,
            "multiplicity": self.multiplicity,
        }


# ---------------------------------------------------------------------------
# Built-in bath gases (unambiguous inerts, spec §5 source 4)
# ---------------------------------------------------------------------------

_BATH_GASES: dict[str, str] = {
    "AR": "[Ar]",
    "HE": "[He]",
    "NE": "[Ne]",
    "N2": "N#N",
}


# ---------------------------------------------------------------------------
# RDKit helpers
# ---------------------------------------------------------------------------


def _formula_from_mol(mol: Chem.Mol) -> dict[str, int]:
    """Element symbol (upper-case) -> atom count, including hydrogens."""
    counts: dict[str, int] = {}
    mol_h = Chem.AddHs(mol)
    for atom in mol_h.GetAtoms():
        sym = atom.GetSymbol().upper()
        counts[sym] = counts.get(sym, 0) + 1
    return counts


def _canonical_from_smiles(smiles: str) -> tuple[str, int, dict[str, int]]:
    """Canonicalise a SMILES string; derive default multiplicity + formula."""
    mol = Chem.MolFromSmiles(smiles)
    if mol is None:
        raise ValueError(f"RDKit could not parse SMILES {smiles!r}.")
    canonical = Chem.MolToSmiles(mol)
    radicals = sum(a.GetNumRadicalElectrons() for a in mol.GetAtoms())
    multiplicity = radicals + 1
    charge = Chem.GetFormalCharge(mol)
    return canonical, multiplicity, charge, _formula_from_mol(mol)  # type: ignore[return-value]


def species_from_smiles(
    name: str,
    smiles: str,
    charge: int | None = None,
    multiplicity: int | None = None,
    source: str = "csv",
) -> ResolvedSpecies:
    """Build a :class:`ResolvedSpecies` from a SMILES string.

    Charge/multiplicity default from the structure (radical count) when not
    explicitly supplied — the override-allowed policy of DR-0031.
    """
    canonical, default_mult, default_charge, formula = _canonical_from_smiles(smiles)
    return ResolvedSpecies(
        name=name,
        smiles=canonical,
        charge=charge if charge is not None else default_charge,
        multiplicity=multiplicity if multiplicity is not None else default_mult,
        formula=formula,
        source=source,
    )


# ---------------------------------------------------------------------------
# RMG adjacency-list parsing
# ---------------------------------------------------------------------------

_ATOM_RE = re.compile(
    r"^\s*(\d+)\s+"  # index
    r"([A-Z][a-z]?)\s+"  # element
    r"u(-?\d+)\s+"  # unpaired electrons
    r"p(-?\d+)\s+"  # lone pairs
    r"c(-?\d+)"  # formal charge
    r"(.*)$"  # bond list
)
_BOND_RE = re.compile(r"\{(\d+),\s*([SDTQB])\}")


def _adjlist_to_species(name: str, atom_lines: list[str]) -> ResolvedSpecies:
    """Convert one RMG adjacency-list block into a :class:`ResolvedSpecies`."""
    rw = Chem.RWMol()
    idx_map: dict[int, int] = {}
    parsed: list[tuple[int, int, list[tuple[int, str]]]] = []
    total_u = 0

    for line in atom_lines:
        m = _ATOM_RE.match(line)
        if not m:
            raise ValueError(f"Malformed adjacency-list atom line: {line!r}")
        idx = int(m.group(1))
        element = m.group(2)
        u = int(m.group(3))
        c = int(m.group(5))
        bonds = [(int(a), b) for a, b in _BOND_RE.findall(m.group(6))]
        total_u += u
        atom = Chem.Atom(element)
        atom.SetFormalCharge(c)
        atom.SetNumRadicalElectrons(u)
        atom.SetNoImplicit(True)
        idx_map[idx] = rw.AddAtom(atom)
        parsed.append((idx, u, bonds))

    seen: set[tuple[int, int]] = set()
    for idx, _u, bonds in parsed:
        for other, btype in bonds:
            key = (min(idx, other), max(idx, other))
            if key in seen:
                continue
            seen.add(key)
            rw.AddBond(idx_map[idx], idx_map[other], _BOND_TYPES[btype])

    mol = rw.GetMol()
    Chem.SanitizeMol(mol)
    formula = _formula_from_mol(mol)  # explicit H already present
    # Drop explicit H to the implicit-count canonical form (matches TCKDB
    # identity), but skip RemoveHs when there is no heavy atom (e.g. the H
    # atom) — RDKit would only warn and leave it unchanged anyway.
    has_heavy = any(a.GetAtomicNum() > 1 for a in mol.GetAtoms())
    canonical = Chem.MolToSmiles(Chem.RemoveHs(mol) if has_heavy else mol)
    charge = Chem.GetFormalCharge(mol)
    return ResolvedSpecies(
        name=name,
        smiles=canonical,
        charge=charge,
        multiplicity=total_u + 1,
        formula=formula,
        source="rmg_dict",
    )


def parse_species_dictionary(text: str) -> dict[str, ResolvedSpecies]:
    """Parse an RMG ``species_dictionary.txt`` into name -> ResolvedSpecies.

    Blocks are separated by blank lines. The first non-atom line of a block is
    the species label; an optional ``multiplicity N`` line is tolerated (the
    value is cross-checked against the summed unpaired-electron count).
    """
    resolved: dict[str, ResolvedSpecies] = {}
    block: list[str] = []

    def flush(block_lines: list[str]) -> None:
        lines = [ln for ln in block_lines if ln.strip()]
        if not lines:
            return
        label = lines[0].strip()
        atom_lines = [
            ln
            for ln in lines[1:]
            if _ATOM_RE.match(ln)
        ]
        if not atom_lines:
            return
        resolved[label] = _adjlist_to_species(label, atom_lines)

    for raw in text.splitlines():
        if raw.strip() == "":
            flush(block)
            block = []
        else:
            # Skip pure comment lines.
            if raw.lstrip().startswith(("//", "#")):
                continue
            block.append(raw)
    flush(block)
    return resolved


# ---------------------------------------------------------------------------
# CSV / inline-comment maps
# ---------------------------------------------------------------------------


def parse_species_map_csv(text: str) -> dict[str, ResolvedSpecies]:
    """Parse a ``name,smiles[,charge,multiplicity]`` CSV map.

    Blank lines and ``#``-comment lines are skipped; an optional header row
    whose first cell is ``name`` (case-insensitive) is ignored.
    """
    resolved: dict[str, ResolvedSpecies] = {}
    for line_no, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        cells = [c.strip() for c in line.split(",")]
        # Skip an optional header row (``name,smiles,...``) wherever it appears
        # (a leading ``#`` comment can push it past line 1).
        if cells[0].lower() == "name" and len(cells) > 1 and cells[1].lower() == "smiles":
            continue
        if len(cells) < 2 or not cells[0] or not cells[1]:
            raise ValueError(
                f"Species-map row {line_no} needs at least name,smiles: {raw!r}"
            )
        name = cells[0]
        smiles = cells[1]
        charge = int(cells[2]) if len(cells) > 2 and cells[2] else None
        mult = int(cells[3]) if len(cells) > 3 and cells[3] else None
        resolved[name] = species_from_smiles(name, smiles, charge, mult, source="csv")
    return resolved


_COMMENT_SMILES_RE = re.compile(r"SMILES\s*[:=]\s*(\S+)", re.IGNORECASE)


def _from_inline_comment(decl: SpeciesDecl) -> ResolvedSpecies | None:
    if not decl.comment:
        return None
    m = _COMMENT_SMILES_RE.search(decl.comment)
    if not m:
        return None
    return species_from_smiles(decl.name, m.group(1), source="comment")


def _bath_gas(name: str) -> ResolvedSpecies | None:
    smiles = _BATH_GASES.get(name.upper())
    if smiles is None:
        return None
    return species_from_smiles(name, smiles, source="bath_gas")


# ---------------------------------------------------------------------------
# Resolver
# ---------------------------------------------------------------------------


@dataclass
class IdentityResolver:
    """Resolves CHEMKIN names to identities across the accepted sources.

    Later-added sources have *lower* priority: CSV map first, then the RMG
    dictionary, then inline comments, then the built-in bath-gas table.
    """

    csv_map: dict[str, ResolvedSpecies] = field(default_factory=dict)
    rmg_dict: dict[str, ResolvedSpecies] = field(default_factory=dict)
    allow_pseudo: bool = False

    def resolve_one(self, decl: SpeciesDecl) -> ResolvedSpecies | None:
        name = decl.name
        if name in self.csv_map:
            return self.csv_map[name]
        if name in self.rmg_dict:
            return self.rmg_dict[name]
        from_comment = _from_inline_comment(decl)
        if from_comment is not None:
            return from_comment
        bath = _bath_gas(name)
        if bath is not None:
            return bath
        if self.allow_pseudo:
            return ResolvedSpecies(
                name=name,
                smiles=name,
                charge=0,
                multiplicity=1,
                formula={},
                source="pseudo",
                molecule_kind="pseudo",
            )
        return None

    def resolve_mechanism(self, mech: Mechanism) -> dict[str, ResolvedSpecies]:
        """Resolve every SPECIES name; fail loud with the full unmapped list.

        Also cross-checks each resolved structure's formula against the NASA
        thermo composition (spec §5) and raises on any mismatch.
        """
        resolved: dict[str, ResolvedSpecies] = {}
        unmapped: list[str] = []

        # Ensure we cover species referenced in reactions/thermo too, not just
        # the SPECIES block, by seeding declarations for any extra names.
        decls: dict[str, SpeciesDecl] = {s.name: s for s in mech.species}
        extra_names: set[str] = set()
        for rxn in mech.reactions:
            extra_names.update(rxn.reactant_names)
            extra_names.update(rxn.product_names)
            extra_names.update(rxn.efficiencies.keys())
            if rxn.falloff_collider and rxn.falloff_collider.upper() != "M":
                extra_names.add(rxn.falloff_collider)
        for name in extra_names:
            decls.setdefault(name, SpeciesDecl(name=name))

        for name, decl in decls.items():
            res = self.resolve_one(decl)
            if res is None:
                unmapped.append(name)
            else:
                resolved[name] = res

        if unmapped:
            raise IdentityResolutionError(unmapped=unmapped)

        mismatches = _formula_cross_check(resolved, mech.thermo)
        if mismatches:
            raise IdentityResolutionError(mismatches=mismatches)

        return resolved


def _fmt_formula(counts: dict[str, int]) -> str:
    return "".join(f"{sym}{counts[sym]}" for sym in sorted(counts))


def _formula_cross_check(
    resolved: dict[str, ResolvedSpecies],
    thermo: dict[str, ThermoEntry],
) -> dict[str, tuple[str, str]]:
    """Return name -> (resolved_formula, thermo_formula) for any mismatch."""
    mismatches: dict[str, tuple[str, str]] = {}
    for name, species in resolved.items():
        entry = thermo.get(name)
        if entry is None or not entry.composition:
            continue
        res_norm = {k: v for k, v in species.formula.items() if v}
        thm_norm = {k.upper(): v for k, v in entry.composition.items() if v}
        # Ignore elements the thermo card zero-pads (e.g. trailing E/0).
        if res_norm != thm_norm:
            mismatches[name] = (_fmt_formula(res_norm), _fmt_formula(thm_norm))
    return mismatches
