from __future__ import annotations

import hashlib
import json
import logging
from collections import Counter
from typing import Mapping, Sequence

from rdkit import Chem
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from tckdb_schemas.fragments.ts_validation_evidence import (
    TransitionStateValidationEvidenceIn,
)

from app.api.error_contract import CodedValueError
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
from app.scientific_checks import (
    CheckTier,
    CodeChannel,
    PythonCheck,
    ScientificCheck,
)

logger = logging.getLogger(__name__)

#: Raised when a reaction's two sides do not hold the same atoms.
W_REACTION_MASS_BALANCE_FAILED = "reaction_mass_balance_failed"

#: Raised when a reaction's two sides do not hold the same total charge.
W_REACTION_CHARGE_NOT_CONSERVED = "reaction_charge_not_conserved"

#: Raised when a saddle point is not made of its own reaction's atoms.
W_TRANSITION_STATE_COMPOSITION_MISMATCH = "transition_state_composition_mismatch"

#: Raised when a saddle point does not carry its reactants' total charge.
W_TRANSITION_STATE_CHARGE_MISMATCH = "transition_state_charge_mismatch"

#: Raised when a stored SMILES cannot be parsed while balancing a reaction.
W_STORED_SMILES_UNPARSEABLE = "stored_species_smiles_unparseable"


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

    :raises CodedValueError: If the stored SMILES cannot be parsed by RDKit.
    """

    if species.kind == MoleculeKind.electron:
        return Counter()

    try:
        return element_counts_from_smiles(species.smiles)
    except ValueError as exc:
        # The species is named by its public ref, never by ``species.id``.
        # A primary key is an internal identifier a caller can neither use
        # nor verify, and handing one out in an error body is the leak the
        # project rule forbids; the row id goes to the log, where whoever
        # has to fix the stored row can act on it.
        logger.warning(
            "Unparseable stored SMILES on species id=%s public_ref=%s: %r",
            species.id,
            species.public_ref,
            species.smiles,
        )
        raise CodedValueError(
            W_STORED_SMILES_UNPARSEABLE,
            f"Cannot parse the stored SMILES {species.smiles!r} of participant "
            f"{species.public_ref} while validating reaction elemental balance.",
            context={
                "species_ref": species.public_ref,
                "smiles": species.smiles,
            },
            message_prefix=False,
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
        raise CodedValueError(
            W_REACTION_MASS_BALANCE_FAILED,
            "Reaction is not element-balanced (reaction_mass_balance_failed).",
            context={
                "reactants": dict(sorted(reactant_totals.items())),
                "products": dict(sorted(product_totals.items())),
            },
            message_prefix=False,
        )


CHECK_REACTION_ELEMENTAL_BALANCE = ScientificCheck(
    group="Conservation across a reaction",
    sort_key=1,
    code="reaction_mass_balance_failed",
    asserts=(
        "The reactant and product sides of a reaction contain the same number "
        "of atoms of every element."
    ),
    tier=CheckTier.block,
    channel=CodeChannel.error_envelope,
    tier_rationale=(
        "Definitional. Mass balance is what makes a set of species a reaction "
        "rather than a list, so no correct calculation can produce an "
        "unbalanced one; the check cannot fire on a correct novel result."
    ),
    adr="0008",
    enforced_by=(
        PythonCheck(
            validate_reaction_elemental_balance,
            note=(
                "Called from ``resolve_chem_reaction``, so it fires on every "
                "path that resolves a reaction, including the PDep bundle."
            ),
        ),
    ),
    escape_hatch=(
        "Declare a participant with ``molecule_kind: pseudo``. A lumped or "
        "phenomenological construct has no atom-resolved composition, so one "
        "such participant suspends the law for the whole reaction. A declared "
        "electron does **not** exempt it — an electron contributes zero atoms "
        "and the reaction still has to balance."
    ),
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
        raise CodedValueError(
            W_REACTION_CHARGE_NOT_CONSERVED,
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
            "elemental balance to be satisfied on its own terms.",
            context={
                "reactant_charge": reactant_charge,
                "product_charge": product_charge,
            },
            message_prefix=False,
        )


CHECK_REACTION_CHARGE_CONSERVATION = ScientificCheck(
    group="Conservation across a reaction",
    sort_key=2,
    code="reaction_charge_not_conserved",
    asserts=(
        "The summed formal charge of a reaction's reactants equals that of its "
        "products."
    ),
    tier=CheckTier.block,
    channel=CodeChannel.error_envelope,
    tier_rationale=(
        "Definitional, and only because the escape hatch exists. Electrons are "
        "neither created nor destroyed by rearranging bonds. Without a way to "
        "name a free electron the rule would in fact assert 'every participant "
        "was declared', which is an expectation about the depositor rather "
        "than a definition of a reaction, and ADR 0008 disqualifies an "
        "expectation from blocking."
    ),
    adr="0008",
    enforced_by=(
        PythonCheck(
            validate_reaction_charge_conservation,
            note=(
                "Sums ``Species.charge``, which "
                "``canonical_species_identity`` has already reconciled against "
                "the formal charge of each species' own SMILES."
            ),
        ),
    ),
    escape_hatch=(
        "Declare the free electron as a participant — ``{\"molecule_kind\": "
        "\"electron\", \"smiles\": \"[e-]\", \"charge\": -1, \"multiplicity\": "
        "2}`` — which is how associative and dissociative attachment, "
        "photoionization and photodetachment are deposited. It contributes -1 "
        "to the side it sits on and zero atoms, so elemental balance still has "
        "to be satisfied separately. A ``pseudo`` participant suspends the law "
        "entirely, as it does for elemental balance. Conservation is not "
        "neutrality: any net charge is accepted as long as both sides carry "
        "the same one, so ion-molecule reactions are unaffected."
    ),
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

    The pseudo exemption is narrower here than in the balance checks, on purpose
    ----------------------------------------------------------------------------
    ``_load_participant_species`` exempts elemental balance and charge
    conservation when a pseudo-species appears on **either** side, because those
    two checks compare one side against the other and a lumped or
    phenomenological construct makes the side it sits on unknowable. This check
    compares the saddle point against the **reactant side only**, so only a
    *reactant* being pseudo can make its comparison meaningless. A pseudo
    *product* is therefore not exempted here, and that asymmetry is the correct
    behaviour rather than a drift: a lumped product says nothing about whether
    the reactant side is atom-resolved, so exempting on one would discard a
    guarantee that is still perfectly well-defined. It also matters most exactly
    there — a reaction with a pseudo product has already lost elemental balance
    and charge conservation, and this check is then the only atom-level
    statement left about the saddle point.

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
            raise CodedValueError(
                W_TRANSITION_STATE_COMPOSITION_MISMATCH,
                f"Transition state '{subject_label}' is "
                f"{format_element_counts(ts_counts)}, but the reaction it sits "
                f"in is {format_element_counts(reactant_totals)} "
                "(transition_state_composition_mismatch). A transition state is "
                "a stationary point on the potential energy surface of its "
                "reaction's atoms, so a saddle point made of different atoms "
                "cannot be that reaction's saddle point. If the saddle-point "
                "structure genuinely contains additional species, declare them "
                "as participants of the reaction.",
                context={
                    "transition_state": dict(sorted(ts_counts.items())),
                    "reaction": dict(sorted(reactant_totals.items())),
                },
                message_prefix=False,
            )

    if transition_state_charge is not None:
        reactant_charge = sum(species.charge for species in reactants)
        if transition_state_charge != reactant_charge:
            raise CodedValueError(
                W_TRANSITION_STATE_CHARGE_MISMATCH,
                f"Transition state '{subject_label}' carries charge "
                f"{transition_state_charge:+d}, but its reactants total "
                f"{reactant_charge:+d} "
                "(transition_state_charge_mismatch). Charge is conserved along "
                "a reaction coordinate, so a saddle point at a different charge "
                "is on a different potential energy surface.",
                context={
                    "transition_state_charge": transition_state_charge,
                    "reactant_charge": reactant_charge,
                },
                message_prefix=False,
            )


#: Why this check's pseudo exemption is deliberately narrower than the balance
#: checks', rather than drifted from them. Recorded on both legs of the
#: function, because both are scoped to the reactant side.
_TS_COMPOSITION_PSEUDO_SCOPE = (
    "The pseudo-species exemption here is narrower than "
    "``_load_participant_species``'s, and deliberately so. That helper exempts "
    "elemental balance and charge conservation on a pseudo participant on "
    "**either** side, because both compare one side against the other and a "
    "lumped construct makes the side it sits on unknowable. This check compares "
    "the saddle point against the **reactant side only**, so only a *reactant* "
    "being pseudo can make it meaningless; a pseudo *product* leaves the "
    "reactant side fully atom-resolved and is not exempted. Aligning the two "
    "would discard a guarantee that is still well-defined, and would discard it "
    "exactly where it is worth most: a reaction with a pseudo product has "
    "already lost elemental balance and charge conservation, so this is the "
    "only atom-level statement left about its saddle point."
)

CHECK_TRANSITION_STATE_COMPOSITION = ScientificCheck(
    group="Conservation across a reaction",
    sort_key=3,
    code="transition_state_composition_mismatch",
    asserts=(
        "A saddle point is made of exactly the atoms of the reaction it is "
        "declared to sit in."
    ),
    tier=CheckTier.block,
    channel=CodeChannel.error_envelope,
    tier_rationale=(
        "Definitional. A transition state is a stationary point on the "
        "potential energy surface *of those atoms*, so a saddle point with a "
        "different molecular formula cannot be that reaction's saddle point, "
        "whatever else is true of it."
    ),
    adr="0008",
    enforced_by=(
        PythonCheck(
            validate_transition_state_composition,
            note=(
                "Composition is read from the saddle-point geometry when there "
                "is one and from ``unmapped_smiles`` otherwise. The PDep path "
                "passes no SMILES, so it compares geometry only."
            ),
        ),
    ),
    escape_hatch=(
        "Declare the extra species as participants of the reaction. A "
        "``pseudo`` *reactant* exempts the reaction; a pseudo product does not, "
        "and that is not an oversight — see below. Absence does not block: no "
        "geometry and no parseable SMILES means nothing is compared, and an "
        "unparseable transition-state SMILES is treated as silence rather "
        "than as a contradiction, because a TS SMILES is a lossy label for a "
        "structure that is by construction not a stable molecule. "
        + _TS_COMPOSITION_PSEUDO_SCOPE
    ),
)

CHECK_TRANSITION_STATE_CHARGE = ScientificCheck(
    group="Conservation across a reaction",
    sort_key=4,
    code="transition_state_charge_mismatch",
    asserts=(
        "A saddle point carries the same total charge as the reactants it sits "
        "between."
    ),
    tier=CheckTier.block,
    channel=CodeChannel.error_envelope,
    tier_rationale=(
        "Definitional. Charge is conserved along a reaction coordinate, so a "
        "saddle point at a different charge is on a different potential energy "
        "surface — not a worse calculation of the same one."
    ),
    adr="0008",
    enforced_by=(
        PythonCheck(
            validate_transition_state_composition,
            note=(
                "Second, independent leg of the same function. Skipped "
                "entirely when the caller passes no "
                "``transition_state_charge``."
            ),
        ),
    ),
    escape_hatch=(
        "Omit the transition state's charge, which skips the comparison. "
        "Multiplicity is deliberately **not** checked here at all: spin is not "
        "conserved the way charge and atoms are — two doublets may react over "
        "a singlet or a triplet surface, and spin-forbidden reactions are real "
        "chemistry — so a multiplicity rule would fire on correct novel "
        "results. " + _TS_COMPOSITION_PSEUDO_SCOPE
    ),
)


#: Emitted when an IRC mapping hands a declared participant saddle-point atoms
#: whose elements are not that participant's own.
W_IRC_MAPPING_ELEMENT_MISMATCH = "transition_state_irc_mapping_element_mismatch"


def _resolved_participant_element_counts(species: Species) -> Counter[str] | None:
    """Element counts for one declared participant, or ``None`` where exempt.

    ``None`` means "this participant has no atom-resolved composition to compare
    against" and is returned only for a pseudo-species, matching the exemption
    :func:`validate_transition_state_composition` applies to the reactant side.
    A free electron is *not* exempt: it returns an empty count, because its
    composition is not unknown but known to be nothing.

    Counts arrive already resolved through
    :func:`~app.chemistry.geometry.resolve_element_symbol`, because
    :func:`~app.chemistry.species.element_counts_from_smiles` applies it on the
    SMILES side for exactly this reason — so that both sides of a comparison are
    counted by one rule rather than two that have to be remembered to agree. The
    caller resolves the geometry side with the same function, which is what
    keeps a deuterated saddle point written ``D`` from contradicting the
    ``[2H]`` its own SMILES spells.
    """

    if species.kind == MoleculeKind.pseudo:
        return None
    return _element_counts_for_species(species)


def validate_ts_evidence_participant_composition(
    session: Session,
    evidence: Sequence[TransitionStateValidationEvidenceIn],
    *,
    reaction_entry_id: int,
    transition_state_geometry_id: int | None,
    subject_label: str = "transition state",
    field_path: str = "validation_evidence",
) -> None:
    """Refuse an IRC mapping that makes a participant out of the wrong atoms.

    ``transition_state_validation_evidence.reactant_participant_mapping`` and
    ``product_participant_mapping`` say which saddle-point atom indices become
    which declared participant. Until this check existed those mappings were
    only ever *bounds*-checked — ``validate_ts_evidence_set`` verifies that the
    keys name every declared participant and that the indices partition the TS
    atoms exactly once — and nothing looked at what the atoms actually **are**.

    A well-formed partition of the wrong atoms therefore passed. The failure is
    not hypothetical: a nine-atom ``C C O O H H H H H`` saddle point for
    ``ethylperoxy -> ethene + HO2`` was deposited with ``product:1 = [1..6]``,
    handing ethene two oxygens and HO2 three hydrogens, under a comment that
    correctly read "C2H4 (six atoms)". The partition was valid; the chemistry
    was not.

    Why this blocks
    ---------------
    Definitional under ADR 0008, and the register's own consistency demands it.
    "These saddle-point atoms become C2H4" while those atoms are C2O2H2 is a
    contradiction no correct calculation can produce — the same class of claim
    as :func:`validate_transition_state_composition`, one level finer. Crucially,
    the *identical assertion expressed as a* ``reaction_atom_map`` **is already
    refused**, by ``CHECK_ATOM_MAP_ELEMENT_CONSERVED``'s composite foreign key
    into ``geometry_atom`` and by
    :func:`~app.services.reaction_atom_map.persist_reaction_atom_map`. Two
    surfaces enforcing different standards on the same claim is not a defensible
    position for either, so this closes the gap on the IRC side.

    This is a *composition* check, not a bijection: an atom map names which
    participant atom becomes which saddle-point atom, while an IRC mapping only
    names the set. So the comparison is between multisets of elements, which is
    the strongest statement the mapping's own shape supports.

    Only what is present is checked
    ------------------------------
    Records that are not ``passed``, and records carrying no mappings, are
    skipped: a mapping that does not claim to be evidence is not contradicted by
    anything, and evidence is optional on every path. A pseudo-species
    participant is skipped individually rather than exempting the whole record,
    because the other participants' compositions are still perfectly
    well-defined. Absent geometry means nothing is compared.

    :param evidence: Producer-declared evidence records, already shape-validated
        by ``validate_ts_evidence_set``.
    :param reaction_entry_id: The reaction whose participants the mapping names.
    :param transition_state_geometry_id: Saddle-point geometry the mapping's
        atom indices count into. ``None`` skips the check.
    :raises ValueError: If a participant is assigned saddle-point atoms whose
        elements are not that participant's own.
    """

    if transition_state_geometry_id is None:
        return
    if not any(
        record.passed and record.reactant_participant_mapping is not None
        for record in evidence
    ):
        return

    ts_element_by_index = {
        atom_index: resolve_element_symbol(element)
        for atom_index, element in session.execute(
            select(GeometryAtom.atom_index, GeometryAtom.element).where(
                GeometryAtom.geometry_id == transition_state_geometry_id
            )
        ).all()
    }
    if not ts_element_by_index:
        return

    participants = session.execute(
        select(
            ReactionEntryStructureParticipant.role,
            ReactionEntryStructureParticipant.participant_index,
            Species,
        )
        .select_from(ReactionEntryStructureParticipant)
        .join(
            SpeciesEntry,
            SpeciesEntry.id == ReactionEntryStructureParticipant.species_entry_id,
        )
        .join(Species, Species.id == SpeciesEntry.species_id)
        .where(
            ReactionEntryStructureParticipant.reaction_entry_id == reaction_entry_id
        )
    ).all()
    species_by_slot = {
        (role, participant_index): species
        for role, participant_index, species in participants
    }

    for record in evidence:
        if not record.passed or record.reactant_participant_mapping is None:
            continue
        assert record.product_participant_mapping is not None
        for role, mapping in (
            (ReactionRole.reactant, record.reactant_participant_mapping),
            (ReactionRole.product, record.product_participant_mapping),
        ):
            for participant_key, atom_indices in sorted(mapping.items()):
                _, _, index_text = participant_key.partition(":")
                try:
                    participant_index = int(index_text)
                except ValueError:  # pragma: no cover - shape already validated
                    continue
                species = species_by_slot.get((role, participant_index))
                if species is None:  # pragma: no cover - shape already validated
                    continue

                declared = _resolved_participant_element_counts(species)
                if declared is None:
                    continue

                assigned: Counter[str] = Counter()
                for atom_index in atom_indices:
                    element = ts_element_by_index.get(atom_index)
                    if element is None:  # pragma: no cover - bounds already checked
                        return
                    assigned[element] += 1

                if assigned != declared:
                    raise CodedValueError(
                        W_IRC_MAPPING_ELEMENT_MISMATCH,
                        f"Transition state '{subject_label}' {field_path} assigns "
                        f"saddle-point atoms "
                        f"{sorted(atom_indices)} to {participant_key}, which is "
                        f"{format_element_counts(assigned)}, but {role.value} "
                        f"{participant_index} is declared as '{species.smiles}', "
                        f"which is {format_element_counts(declared)} "
                        f"({W_IRC_MAPPING_ELEMENT_MISMATCH}). An IRC mapping says "
                        "which saddle-point atoms become which declared "
                        "participant, so a participant cannot be made of atoms it "
                        "does not contain. The identical claim written as an "
                        "atom_map is already refused; correct the mapping per "
                        "atom, not per count."
                    )


CHECK_TS_IRC_MAPPING_ELEMENTS = ScientificCheck(
    group="Conservation across a reaction",
    sort_key=5,
    code=W_IRC_MAPPING_ELEMENT_MISMATCH,
    asserts=(
        "The saddle-point atoms an IRC mapping assigns to a declared "
        "participant are that participant's own atoms, element for element."
    ),
    tier=CheckTier.block,
    channel=CodeChannel.error_envelope,
    tier_rationale=(
        "Definitional. 'These saddle-point atoms become C2H4' while those atoms "
        "are C2O2H2 is a contradiction no correct calculation can produce — the "
        "same class of claim ``CHECK_TRANSITION_STATE_COMPOSITION`` already "
        "blocks, one level finer, per participant rather than per side. It is "
        "also what the register's own consistency requires: the identical "
        "assertion expressed as a ``reaction_atom_map`` is refused by "
        "``CHECK_ATOM_MAP_ELEMENT_CONSERVED``, at the wire boundary and again "
        "by a composite foreign key into ``geometry_atom``. Two surfaces "
        "enforcing different standards on the same claim is not a defensible "
        "position for either — and the divergence was not theoretical: a "
        "well-formed partition handing ethene two oxygens and HO2 three "
        "hydrogens was accepted, under a fixture comment that correctly said "
        "'C2H4 (six atoms)'."
    ),
    adr="0008, 0011",
    enforced_by=(
        PythonCheck(
            validate_ts_evidence_participant_composition,
            note=(
                "Called from "
                "``persist_transition_state_validation_evidence``, the single "
                "seam every deposit path that can carry a transition state "
                "already routes through, so the PDep bundle, the "
                "computed-reaction bundle and the standalone transition-state "
                "upload cannot enforce different standards. It is a service-"
                "layer check rather than a wire-boundary one because a "
                "participant's composition comes from its SMILES, and "
                "``tckdb_schemas`` is chemistry-free — RDKit is not available "
                "where ``validate_ts_evidence_set`` runs. That function keeps "
                "the *shape* half of the rule: keys name every declared "
                "participant, indices partition the TS atoms exactly once."
            ),
        ),
    ),
    escape_hatch=(
        "Omit the participant mappings. They are optional on every path — "
        "evidence without them still deposits and still reads back as "
        "``irc: present`` — so a depositor who cannot resolve the partition per "
        "atom is never forced to guess at one. Declaring a participant "
        "``molecule_kind: pseudo`` skips that participant alone rather than the "
        "whole record, because the others' compositions are still well-defined. "
        "Isotopologues are safe by construction: both sides are compared "
        "through ``resolve_element_symbol``, so a geometry written ``D`` counts "
        "as the hydrogen its SMILES spells ``[2H]``."
    ),
    divergence=(
        "A zero-atom participant cannot be expressed. "
        "``TransitionStateValidationEvidenceIn`` refuses an empty atom list, "
        "and ``validate_ts_evidence_set`` requires every declared participant "
        "to be named, so a reaction releasing a free electron — "
        "``MoleculeKind.electron``, newly reachable — has no way to write "
        "``product:2: []``. Before this check such a reaction could deposit a "
        "*wrong* mapping that stole a real atom for the electron; it now "
        "correctly cannot, but it also cannot deposit a right one, and must "
        "omit the mappings instead. Widening the wire schema to accept an empty "
        "list for a participant with no atoms is the fix, and is a wire-package "
        "change with its own version bump."
    ),
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
    elements differently by construction: ``geometry_atom.element`` holds the
    depositor's isotope labelling, while ``_element_counts_for_species`` reads
    RDKit's title-case ``GetSymbol()``. Comparing them raw makes a saddle point
    written ``D`` contradict a reaction it is in fact made of, which refuses
    correct chemistry over a string — the failure ADR 0008 puts out of bounds
    for a blocking check. Capitalisation used to be the other half of this and
    is no longer: ``b4e7c1d20f83`` canonicalises the case at ingestion, so
    ``CL`` and ``c`` never reach a comparison. The resolution stays because
    ``D`` and ``T`` still do.
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
