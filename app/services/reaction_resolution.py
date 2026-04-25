from __future__ import annotations

import hashlib
import json
from collections import Counter
from typing import Mapping, Sequence

from rdkit import Chem
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models.common import MoleculeKind, ReactionRole
from app.db.models.reaction import ChemReaction, ReactionFamily, ReactionParticipant
from app.db.models.species import Species, SpeciesEntry
from app.schemas.reaction_family import find_canonical_reaction_family
from app.schemas.utils import normalize_optional_text


def compress_species_stoichiometry(
    species_entries: Sequence[SpeciesEntry],
) -> dict[int, int]:
    """Compress resolved species entries into graph-level stoichiometry counts.

    :param species_entries: Ordered resolved participants on one side of a reaction.
    :returns: Mapping of ``species_id`` to stoichiometric coefficient.
    """

    return dict(Counter(species_entry.species_id for species_entry in species_entries))


def reaction_stoichiometry_hash(
    *,
    reversible: bool,
    reactants: Mapping[int, int],
    products: Mapping[int, int],
) -> str:
    """Build a canonical graph-identity hash for a reaction submission.

    :param reversible: Whether the submitted reaction is reversible.
    :param reactants: Graph-layer reactant stoichiometry keyed by ``species_id``.
    :param products: Graph-layer product stoichiometry keyed by ``species_id``.
    :returns: SHA-256 hash of the canonicalized graph-identity payload.
    """

    payload = {
        "reversible": reversible,
        "reactants": sorted(reactants.items()),
        "products": sorted(products.items()),
    }
    encoded = json.dumps(payload, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _element_counts_for_species(species: Species) -> Counter[str]:
    """Count element occurrences for one ordinary (molecule-kind) species.

    :raises ValueError: If the stored SMILES cannot be parsed by RDKit.
    """

    mol = Chem.MolFromSmiles(species.smiles)
    if mol is None:
        raise ValueError(
            f"Cannot parse stored SMILES for species_id={species.id} "
            "while validating reaction elemental balance."
        )
    mol = Chem.AddHs(mol)
    counts: Counter[str] = Counter()
    for atom in mol.GetAtoms():
        counts[atom.GetSymbol()] += 1
    return counts


def validate_reaction_elemental_balance(
    session: Session,
    *,
    reactant_stoichiometry: Mapping[int, int],
    product_stoichiometry: Mapping[int, int],
) -> None:
    """Enforce strict elemental balance for ordinary reactions.

    Fetches the referenced ``Species`` rows and compares element totals
    on the reactant and product sides. Reactions with any pseudo-species
    participant are exempted in this first-pass policy (pseudo species
    may represent lumped or phenomenological constructs rather than
    atom-resolved chemistry).

    :raises ValueError: If all participants are ordinary molecule species
        and the reactant/product element totals disagree.
    """

    species_ids = set(reactant_stoichiometry) | set(product_stoichiometry)
    if not species_ids:
        return

    species_rows = session.scalars(
        select(Species).where(Species.id.in_(species_ids))
    ).all()
    species_by_id = {species.id: species for species in species_rows}

    if any(
        species_by_id[species_id].kind == MoleculeKind.pseudo
        for species_id in species_ids
    ):
        return

    reactant_totals: Counter[str] = Counter()
    for species_id, coefficient in reactant_stoichiometry.items():
        for element, count in _element_counts_for_species(
            species_by_id[species_id]
        ).items():
            reactant_totals[element] += coefficient * count

    product_totals: Counter[str] = Counter()
    for species_id, coefficient in product_stoichiometry.items():
        for element, count in _element_counts_for_species(
            species_by_id[species_id]
        ).items():
            product_totals[element] += coefficient * count

    if reactant_totals != product_totals:
        raise ValueError(
            "Reaction is not element-balanced (reaction_mass_balance_failed)."
        )


def resolve_reaction_family(
    session: Session,
    reaction_family: str | None,
) -> ReactionFamily | None:
    """Resolve a canonical reaction-family lookup row."""

    canonical_name = find_canonical_reaction_family(reaction_family)
    if canonical_name is None:
        return None

    family = session.scalar(
        select(ReactionFamily).where(ReactionFamily.name == canonical_name)
    )
    if family is not None:
        return family

    raise RuntimeError(
        f"Missing seeded reaction_family row for canonical name {canonical_name!r}."
    )


def resolve_chem_reaction(
    session: Session,
    *,
    reversible: bool,
    reaction_family: str | None = None,
    reaction_family_source_note: str | None = None,
    reactant_stoichiometry: Mapping[int, int],
    product_stoichiometry: Mapping[int, int],
) -> ChemReaction:
    """Resolve or create the graph-identity reaction layer for an upload.

    :param session: Active SQLAlchemy session.
    :param reversible: Whether the submitted reaction is reversible.
    :param reaction_family: Optional reaction-family label using RMG family names.
    :param reaction_family_source_note: Optional provenance note for non-canonical family labels.
    :param reactant_stoichiometry: Compressed reactant stoichiometry keyed by ``species_id``.
    :param product_stoichiometry: Compressed product stoichiometry keyed by ``species_id``.
    :returns: Existing or newly created ``ChemReaction`` row.
    """

    validate_reaction_elemental_balance(
        session,
        reactant_stoichiometry=reactant_stoichiometry,
        product_stoichiometry=product_stoichiometry,
    )

    resolved_reaction_family = resolve_reaction_family(session, reaction_family)
    reaction_family_raw = (
        normalize_optional_text(reaction_family)
        if resolved_reaction_family is None
        else None
    )
    normalized_source_note = normalize_optional_text(reaction_family_source_note)

    stoichiometry_hash = reaction_stoichiometry_hash(
        reversible=reversible,
        reactants=reactant_stoichiometry,
        products=product_stoichiometry,
    )
    chem_reaction = session.scalar(
        select(ChemReaction).where(
            ChemReaction.stoichiometry_hash == stoichiometry_hash
        )
    )
    if chem_reaction is not None:
        if resolved_reaction_family is not None:
            if chem_reaction.reaction_family_id is None:
                chem_reaction.reaction_family = resolved_reaction_family
            elif chem_reaction.reaction_family_id != resolved_reaction_family.id:
                raise ValueError(
                    "Resolved reaction already has a different reaction_family: "
                    f"{chem_reaction.reaction_family.name!r} != "
                    f"{resolved_reaction_family.name!r}."
                )
        elif reaction_family_raw is not None:
            if chem_reaction.reaction_family_raw is None:
                chem_reaction.reaction_family_raw = reaction_family_raw
                chem_reaction.reaction_family_source_note = normalized_source_note
            elif chem_reaction.reaction_family_raw != reaction_family_raw:
                raise ValueError(
                    "Resolved reaction already has a different raw reaction_family: "
                    f"{chem_reaction.reaction_family_raw!r} != {reaction_family_raw!r}."
                )
            elif (
                chem_reaction.reaction_family_source_note is None
                and normalized_source_note is not None
            ):
                chem_reaction.reaction_family_source_note = normalized_source_note
        session.flush()
        return chem_reaction

    try:
        with session.begin_nested():
            chem_reaction = ChemReaction(
                reversible=reversible,
                stoichiometry_hash=stoichiometry_hash,
                reaction_family=resolved_reaction_family,
                reaction_family_raw=reaction_family_raw,
                reaction_family_source_note=normalized_source_note,
            )
            session.add(chem_reaction)
            session.flush()

            for species_id, stoichiometry in sorted(reactant_stoichiometry.items()):
                session.add(
                    ReactionParticipant(
                        reaction_id=chem_reaction.id,
                        species_id=species_id,
                        role=ReactionRole.reactant,
                        stoichiometry=stoichiometry,
                    )
                )

            for species_id, stoichiometry in sorted(product_stoichiometry.items()):
                session.add(
                    ReactionParticipant(
                        reaction_id=chem_reaction.id,
                        species_id=species_id,
                        role=ReactionRole.product,
                        stoichiometry=stoichiometry,
                    )
                )

            session.flush()
    except IntegrityError:
        chem_reaction = session.scalar(
            select(ChemReaction).where(ChemReaction.stoichiometry_hash == stoichiometry_hash)
        )

    return chem_reaction
