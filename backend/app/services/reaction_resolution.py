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
from app.db.models.geometry import GeometryAtom
from app.db.models.reaction import (
    ChemReaction,
    ReactionEntryStructureParticipant,
    ReactionFamily,
    ReactionParticipant,
)
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


def _format_formula(counts: Mapping[str, int]) -> str:
    """Render an element count as a formula, for an error a human can read."""

    return "".join(
        f"{element}{count if count > 1 else ''}"
        for element, count in sorted(counts.items())
    )


def validate_transition_state_composition(
    session: Session,
    *,
    reaction_entry_id: int,
    transition_state_charge: int | None = None,
    transition_state_smiles: str | None = None,
    transition_state_geometry_id: int | None = None,
    subject_label: str = "transition state",
) -> None:
    """Refuse a saddle point that is not made of its own reaction's atoms.

    A transition state is a stationary point on the potential energy surface
    **of those atoms**. A saddle point whose molecular formula differs from the
    reaction it is attached to cannot be that reaction's transition state,
    whatever else is true of it — it is a contradiction of exactly the class
    ``validate_reaction_elemental_balance`` already refuses one column to the
    left, and definitional under ADR 0008 rather than an expectation. Nothing
    checked it before this function: the elemental-balance rule compares
    reactants against products and has never mentioned the transition state, so
    a saddle point with the wrong atoms was accepted.

    The check must hold for **every** reaction, mapped or not. An atom map
    catches a formula mismatch implicitly, but only where a map was supplied,
    which would deliver the stronger guarantee exactly to the deposits that
    were already the most careful.

    Only what is present is checked
    ------------------------------
    Composition is read from the saddle-point geometry when there is one and
    from ``unmapped_smiles`` otherwise. The geometry is preferred because it
    *is* the stationary point, where the SMILES is a label attached to it. If
    neither is available, or the SMILES will not parse, that is an **absence**
    and it does not block — the same tier logic an absent atom map gets.

    Multiplicity is deliberately not checked. Spin is not conserved the way
    charge and atoms are: two doublets may react over a singlet or a triplet
    surface, and spin-forbidden reactions are real chemistry. A multiplicity
    rule would fire on correct novel results, which ADR 0008 disqualifies.

    Reactions carrying a pseudo-species participant are exempt, matching
    ``validate_reaction_elemental_balance``: a lumped or phenomenological
    construct has no atom-resolved composition to compare against.

    :raises ValueError: If the saddle point's elements or charge contradict
        the reactants it is declared to sit between.
    """

    reactants = session.scalars(
        select(Species)
        .select_from(ReactionEntryStructureParticipant)
        .join(
            SpeciesEntry,
            SpeciesEntry.id == ReactionEntryStructureParticipant.species_entry_id,
        )
        .join(Species, Species.id == SpeciesEntry.species_id)
        .where(
            ReactionEntryStructureParticipant.reaction_entry_id == reaction_entry_id,
            ReactionEntryStructureParticipant.role == ReactionRole.reactant,
        )
    ).all()
    if not reactants:
        return
    if any(species.kind == MoleculeKind.pseudo for species in reactants):
        return

    ts_counts = _transition_state_element_counts(
        session,
        transition_state_smiles=transition_state_smiles,
        transition_state_geometry_id=transition_state_geometry_id,
    )

    if ts_counts is not None:
        reactant_totals: Counter[str] = Counter()
        for species in reactants:
            reactant_totals += _element_counts_for_species(species)

        if ts_counts != reactant_totals:
            raise ValueError(
                f"Transition state '{subject_label}' is "
                f"{_format_formula(ts_counts)}, but the reaction it sits in is "
                f"{_format_formula(reactant_totals)} "
                "(transition_state_composition_mismatch). A transition state is "
                "a stationary point on the potential energy surface of its "
                "reaction's atoms, so a saddle point made of different atoms "
                "cannot be that reaction's saddle point. If the saddle-point "
                "structure genuinely contains additional species, declare them "
                "as participants of the reaction."
            )

    if transition_state_charge is not None:
        reactant_charge = sum(species.charge for species in reactants)
        if transition_state_charge != reactant_charge:
            raise ValueError(
                f"Transition state '{subject_label}' carries charge "
                f"{transition_state_charge:+d}, but its reactants total "
                f"{reactant_charge:+d} "
                "(transition_state_charge_mismatch). Charge is conserved along "
                "a reaction coordinate, so a saddle point at a different charge "
                "is on a different potential energy surface."
            )


def _transition_state_element_counts(
    session: Session,
    *,
    transition_state_smiles: str | None,
    transition_state_geometry_id: int | None,
) -> Counter[str] | None:
    """Count the saddle point's elements, or return ``None`` if nothing says.

    Geometry first: it is the stationary point itself, and its
    ``geometry_atom`` rows are the atoms a normal-mode analysis would run over.
    ``unmapped_smiles`` is a fallback label. An unparseable SMILES is treated
    as saying nothing rather than as a contradiction — a transition-state
    SMILES is a lossy description of a structure that is, by construction, not
    a stable molecule.
    """

    if transition_state_geometry_id is not None:
        elements = session.scalars(
            select(GeometryAtom.element).where(
                GeometryAtom.geometry_id == transition_state_geometry_id
            )
        ).all()
        if elements:
            return Counter(element.strip() for element in elements)

    if transition_state_smiles is not None:
        mol = Chem.MolFromSmiles(transition_state_smiles)
        if mol is not None:
            mol = Chem.AddHs(mol)
            return Counter(atom.GetSymbol() for atom in mol.GetAtoms())

    return None


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
