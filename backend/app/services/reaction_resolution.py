from __future__ import annotations

import hashlib
import json
from collections import Counter
from typing import Mapping, Sequence

from rdkit import Chem
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.chemistry.geometry import resolve_element_symbol
from app.chemistry.species import element_counts_from_smiles, format_element_counts
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
    """Count element occurrences for one reaction participant.

    A free electron contributes an empty count — not because its composition
    is unknown, but because it is known to be nothing. That is the whole
    difference between it and a pseudo-species, which is skipped by
    :func:`_load_participant_species` before it ever gets here: an electron
    still has to balance, it simply has no atoms to bring to the balance.

    :raises ValueError: If the stored SMILES cannot be parsed by RDKit.
    """

    if species.kind == MoleculeKind.electron:
        return Counter()

    try:
        return element_counts_from_smiles(species.smiles)
    except ValueError as exc:
        raise ValueError(
            f"Cannot parse stored SMILES for species_id={species.id} "
            "while validating reaction elemental balance."
        ) from exc


def _load_participant_species(
    session: Session,
    *,
    reactant_stoichiometry: Mapping[int, int],
    product_stoichiometry: Mapping[int, int],
) -> dict[int, Species] | None:
    """Fetch the participant species rows, or ``None`` if the check is exempt.

    ``None`` means "do not judge this reaction": either it has no participants
    at all, or one of them is a pseudo-species. Pseudo-species are lumped or
    phenomenological constructs rather than atom-resolved chemistry, so neither
    their elements nor their charge is a quantity a conservation law applies
    to. Shared by both conservation checks so the two can never drift into
    exempting different reactions.

    **A declared electron does not land here.** ``MoleculeKind.electron`` is
    checked against ``pseudo`` specifically, not against "anything that is not
    a molecule", and that is load-bearing rather than incidental. An electron
    is exactly known — zero atoms, charge -1 — so it participates in both
    conservation laws instead of suspending them. Were it routed through this
    exemption, adding an electron to any reaction would switch its mass
    balance off, which is a larger hole than the one the electron exists to
    close.
    """

    species_ids = set(reactant_stoichiometry) | set(product_stoichiometry)
    if not species_ids:
        return None

    species_rows = session.scalars(
        select(Species).where(Species.id.in_(species_ids))
    ).all()
    species_by_id = {species.id: species for species in species_rows}

    if any(
        species_by_id[species_id].kind == MoleculeKind.pseudo
        for species_id in species_ids
    ):
        return None

    return species_by_id


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

    A declared free electron is **not** exempted — it contributes an empty
    element count, so a reaction carrying one still has to balance atom for
    atom. See :func:`_load_participant_species` for why that separation
    matters.

    Charge is the other conserved quantity and is checked separately by
    :func:`validate_reaction_charge_conservation`.

    :raises ValueError: If no participant is a pseudo-species and the
        reactant/product element totals disagree.
    """

    species_by_id = _load_participant_species(
        session,
        reactant_stoichiometry=reactant_stoichiometry,
        product_stoichiometry=product_stoichiometry,
    )
    if species_by_id is None:
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


def validate_reaction_charge_conservation(
    session: Session,
    *,
    reactant_stoichiometry: Mapping[int, int],
    product_stoichiometry: Mapping[int, int],
) -> None:
    """Enforce charge conservation for ordinary reactions.

    Charge is conserved by every elementary and overall chemical reaction, in
    exactly the sense that atoms are: electrons are neither created nor
    destroyed by rearranging bonds, so the summed formal charge of the
    reactants equals that of the products. ``[OH-] + [H] -> H2O`` is not a
    reaction that is hard to compute — it is not a reaction, because it loses
    an electron on the way across. Under ADR 0008 that is a **definition**, not
    an expectation: no correct calculation can produce it, so the check blocks
    rather than warns, and it sits beside
    :func:`validate_reaction_elemental_balance` because it is the same
    conservation argument applied to the other conserved quantity.

    Nothing checked this before. Elemental balance compared element totals and
    said nothing about charge, so a charge-losing reaction deposited with no
    error and no warning at all. That gap reached further than itself:
    :func:`validate_transition_state_composition` checks a saddle point's
    charge against its *reactants*, and until now the reactant side carried no
    conservation guarantee of its own for that rule to be anchored to.

    Per-species charge is trustworthy input: ``Species.charge`` is compared
    against the formal charge of its own SMILES by
    :func:`app.chemistry.species.canonical_species_identity`, which blocks, so
    this function sums values that have already been reconciled with the
    structures they label.

    The stoichiometric coefficients and the pseudo-species exemption are the
    same as elemental balance's, by construction — both read
    :func:`_load_participant_species`.

    **Conservation, not neutrality.** A reaction may carry any net charge as
    long as both sides carry the same one. Requiring neutrality would refuse
    every ion-molecule reaction in the literature, which is exactly the false
    positive ADR 0008 disqualifies a blocking check for.

    **Electron-transferring processes balance here too.** Associative
    detachment (``OH- + H -> H2O + e-``), dissociative attachment,
    photoionization and photodetachment all release or consume a free
    electron, and all are real measured gas-phase chemistry. They are
    deposited by declaring the electron as a participant —
    ``{"molecule_kind": "electron", "smiles": "[e-]", "charge": -1,
    "multiplicity": 2}`` — which contributes -1 to the side it sits on and
    lets the reaction balance as written.

    That door has to exist for this check to be allowed to block at all.
    Charge conservation is definitional only if the participant list can be
    *complete*; with no way to name the electron, the rule would in fact be
    asserting "every participant was declared", which is an expectation about
    the depositor rather than a definition of a reaction, and ADR 0008
    disqualifies an expectation from blocking. The electron is what makes the
    stated claim the claim actually enforced.

    Declaring one exempts nothing. Unlike a ``pseudo`` participant, an
    electron is precisely known, so it contributes 0 atoms to elemental
    balance and -1 to this sum, and both checks still have to pass. See
    :func:`_load_participant_species`.

    :raises ValueError: If no participant is a pseudo-species and the
        reactant/product charge totals disagree.
    """

    species_by_id = _load_participant_species(
        session,
        reactant_stoichiometry=reactant_stoichiometry,
        product_stoichiometry=product_stoichiometry,
    )
    if species_by_id is None:
        return

    reactant_charge = sum(
        coefficient * species_by_id[species_id].charge
        for species_id, coefficient in reactant_stoichiometry.items()
    )
    product_charge = sum(
        coefficient * species_by_id[species_id].charge
        for species_id, coefficient in product_stoichiometry.items()
    )

    if reactant_charge != product_charge:
        raise ValueError(
            f"Reaction reactants total charge {reactant_charge:+d} but products "
            f"total {product_charge:+d} (reaction_charge_not_conserved). Charge "
            "is conserved across a reaction, so the two sides describe "
            "different numbers of electrons and cannot be the same reaction. "
            "A net charge is fine as long as both sides carry the same one. If "
            "the process genuinely releases or consumes a free electron — "
            "associative or dissociative attachment, photoionization, "
            "photodetachment — declare the electron as a participant: "
            '{"molecule_kind": "electron", "smiles": "[e-]", "charge": -1, '
            '"multiplicity": 2}. It balances the charge and still leaves the '
            "elemental balance to be satisfied on its own terms."
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
                f"{format_element_counts(ts_counts)}, but the reaction it sits "
                f"in is {format_element_counts(reactant_totals)} "
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

    Both branches are resolved through
    :func:`~app.chemistry.geometry.resolve_element_symbol` before they are
    counted, and so is the reactant side, because the two sources spell
    elements differently by construction: ``geometry_atom.element`` holds
    whatever the depositor's XYZ said, while ``_element_counts_for_species``
    reads RDKit's title-case ``GetSymbol()``. Comparing them raw makes a saddle
    point written ``CL``, ``c`` or ``D`` contradict a reaction it is in fact
    made of, which refuses correct chemistry over a string — the failure ADR
    0008 puts out of bounds for a blocking check.
    """

    if transition_state_geometry_id is not None:
        elements = session.scalars(
            select(GeometryAtom.element).where(
                GeometryAtom.geometry_id == transition_state_geometry_id
            )
        ).all()
        if elements:
            return Counter(resolve_element_symbol(element) for element in elements)

    if transition_state_smiles is not None:
        mol = Chem.MolFromSmiles(transition_state_smiles)
        if mol is not None:
            mol = Chem.AddHs(mol)
            return Counter(
                resolve_element_symbol(atom.GetSymbol()) for atom in mol.GetAtoms()
            )

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
    validate_reaction_charge_conservation(
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
