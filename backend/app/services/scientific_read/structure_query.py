"""RDKit query parsing + mode/query-kind rules shared by every structure
filter in the read layer.

Extracted from :mod:`app.services.scientific_read.structure_search` (the
standalone ``/scientific/species/structure-search`` endpoint) so that
:mod:`app.services.scientific_read.species` (the ``/species/browse``
structure filter, added alongside the browse-page structure search UI)
does not fork a second copy of the same RDKit parsing/validation. Both
callers raise the identical ``"invalid_structure_query: ..."`` messages
this module defines, which is what keeps `` app.api.code_catalogue``'s
``invalid_structure_query`` entry honest about where the literal lives —
see that catalogue entry's note.

Nothing here talks to the database: these functions only parse a query
string via RDKit (raising a 422-shaped :class:`ValueError` on a bad
parse) or decide whether a ``(mode, query_kind)`` pair is a legal
combination. The actual cartridge SQL (``@>``, ``tanimoto_sml`` /
``morganbv_fp``) stays where each caller builds its own statement —
structure_search.py's raw parameterized SQL, species.py's SQLAlchemy
``EXISTS`` predicate — because the two shapes are not the same query
(one is a standalone paginated response, the other a composable filter
predicate), only the same underlying match.
"""

from __future__ import annotations

from rdkit import Chem
from rdkit.Chem import inchi as _inchi

from app.schemas.reads.scientific_structure_search import (
    StructureQueryKind,
    StructureSearchMode,
)

# Mode -> which query fields are accepted. Shared by every caller that
# validates a (mode, query_kind) pair -- the standalone endpoint's four
# query kinds and /species/browse's two-field (smiles/smarts) subset both
# check membership in the same table, so a mode's accepted kinds cannot
# drift between the two surfaces.
MODE_QUERY_KIND_RULES: dict[StructureSearchMode, set[StructureQueryKind]] = {
    StructureSearchMode.substructure: {
        StructureQueryKind.smiles,
        StructureQueryKind.smarts,
    },
    StructureSearchMode.similarity: {
        StructureQueryKind.smiles,
        StructureQueryKind.inchi,
    },
    StructureSearchMode.exact: {
        StructureQueryKind.smiles,
        StructureQueryKind.inchi,
        StructureQueryKind.inchi_key,
    },
}


def enforce_mode_query_compatibility(
    mode: StructureSearchMode, kind: StructureQueryKind
) -> None:
    """Reject mode/query-field combinations the cartridge does not
    support cleanly (e.g. similarity-by-InChIKey, exact-by-SMARTS)."""
    allowed = MODE_QUERY_KIND_RULES[mode]
    if kind not in allowed:
        raise ValueError(
            f"invalid_structure_query: mode={mode.value!r} does not "
            f"accept query_{kind.value}; supported query kinds for this "
            f"mode are {sorted(k.value for k in allowed)!r}."
        )


def parse_smiles_to_canonical(smiles: str) -> str:
    """Parse a SMILES via RDKit and return its canonical SMILES.

    Used to normalize callers' inputs before binding into SQL so the
    cartridge sees a parseable molecule we already validated client-side.
    """
    mol = Chem.MolFromSmiles(smiles)
    if mol is None:
        raise ValueError(
            "invalid_structure_query: RDKit could not parse the SMILES "
            "supplied as query_smiles."
        )
    canonical = Chem.MolToSmiles(mol, canonical=True)
    if not canonical:
        raise ValueError(
            "invalid_structure_query: RDKit produced an empty canonical "
            "SMILES from query_smiles."
        )
    return canonical


def parse_smarts(smarts: str) -> Chem.Mol:
    mol = Chem.MolFromSmarts(smarts)
    if mol is None:
        raise ValueError(
            "invalid_structure_query: RDKit could not parse the SMARTS "
            "supplied as query_smarts."
        )
    return mol


def parse_inchi_to_canonical_smiles(inchi_str: str) -> str:
    mol = Chem.MolFromInchi(inchi_str)
    if mol is None:
        raise ValueError(
            "invalid_structure_query: RDKit could not parse the InChI "
            "supplied as query_inchi."
        )
    return Chem.MolToSmiles(mol, canonical=True)


def inchi_key_from_query(kind: StructureQueryKind, value: str) -> str:
    """Compute the canonical InChIKey for an exact-mode query."""
    if kind is StructureQueryKind.inchi_key:
        return value
    if kind is StructureQueryKind.smiles:
        mol = Chem.MolFromSmiles(value)
        if mol is None:
            raise ValueError(
                "invalid_structure_query: RDKit could not parse the "
                "SMILES supplied as query_smiles."
            )
        return _inchi.MolToInchiKey(mol)
    if kind is StructureQueryKind.inchi:
        mol = Chem.MolFromInchi(value)
        if mol is None:
            raise ValueError(
                "invalid_structure_query: RDKit could not parse the "
                "InChI supplied as query_inchi."
            )
        return _inchi.MolToInchiKey(mol)
    raise ValueError(
        "invalid_structure_query: exact mode does not accept this "
        "query kind."
    )


__all__ = [
    "MODE_QUERY_KIND_RULES",
    "enforce_mode_query_compatibility",
    "inchi_key_from_query",
    "parse_inchi_to_canonical_smiles",
    "parse_smarts",
    "parse_smiles_to_canonical",
]
