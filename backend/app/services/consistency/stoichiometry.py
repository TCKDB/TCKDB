"""Neutral stoichiometric facts shared by the advisory consistency checks.

Extracted from D3 (``consistency/kinetics.py``) so D5 and D6 count
participants, compositions and balance the same way. This module states
facts only -- slot counts, compositions, element and charge balance,
whether a species carries isotope labels -- and never decides what a fact
means for a check. Each check maps facts to its own reason tokens at its
call site (D3 keeps ``isotope_specific_equilibrium_unsupported``, for
example); the one exception is :func:`species_scope_reason`, the single
place the per-species scope of the thermochemical checks is declared, so
widening scope to ions or isotopologues later is a change here only.

Stoichiometry counts ``reaction_entry_structure_participant`` *slots*:
``2 CH3 -> C2H6`` lists CH3 twice and its coefficient is 2. Counting unique
species instead would make that reaction look unbalanced.
"""
from collections import Counter
from dataclasses import dataclass, replace

from rdkit import Chem

from app.chemistry.species import element_counts_from_smiles

CHARGED_SPECIES_OUT_OF_SCOPE = "charged_species_out_of_scope"
ISOTOPE_LABELLED_SPECIES_OUT_OF_SCOPE = "isotope_labelled_species_out_of_scope"
UNUSABLE_SPECIES_COMPOSITION = "unusable_species_composition"


@dataclass(frozen=True)
class SpeciesFacts:
    """What a species' stored SMILES and charge say, without judging it.

    ``parsed`` is False when RDKit cannot read the SMILES; the other
    structure facts are then unknown and reported as False/None.
    ``composition`` is None when the SMILES parsed but its elements could
    not be counted. Isotopes collapse to their element in ``composition``
    (``[2H]`` counts as H, as everywhere in TCKDB); ``has_isotopes`` records
    that a label was present. ``has_dummy_atoms`` flags wildcard/dummy
    atoms (atomic number 0), which have no element.
    """

    parsed: bool
    has_isotopes: bool
    has_dummy_atoms: bool
    composition: dict | None
    charge: int | None


def species_facts(species):
    molecule = Chem.MolFromSmiles(species.smiles)
    if molecule is None:
        return SpeciesFacts(False, False, False, None, species.charge)
    atoms = list(molecule.GetAtoms())
    try:
        composition = dict(element_counts_from_smiles(species.smiles))
    except ValueError:
        composition = None
    return SpeciesFacts(
        parsed=True,
        has_isotopes=any(atom.GetIsotope() for atom in atoms),
        has_dummy_atoms=any(atom.GetAtomicNum() == 0 for atom in atoms),
        composition=composition,
        charge=species.charge,
    )


def entry_facts(species_entry):
    """:func:`species_facts` for an entry's species, with the entry's isotope content.

    The upload path strips isotope labels before storing ``species.smiles``
    (``app.chemistry.species.canonical_species_identity``): isotopologues
    share one species row, and the labelling lives only on
    ``species_entry.isotope_key`` (``None`` = every atom at its most
    abundant isotope). So on persisted data the species SMILES never shows
    an isotope, and ``has_isotopes`` must come from the entry. A label on
    the species SMILES itself -- a row stored before labels were stripped
    (#66) -- still counts, so neither source can hide the other.
    """
    facts = species_facts(species_entry.species)
    return replace(facts, has_isotopes=facts.has_isotopes or species_entry.isotope_key is not None)


@dataclass(frozen=True)
class ParticipantSlots:
    """Reactant/product slot counts per species entry of one reaction entry.

    ``entries`` maps species-entry id to species entry in first-appearance
    order over the participant list. ``participant_count`` counts every
    participant row, whatever its role, so a role that is neither reactant
    nor product leaves ``accounted`` False rather than being dropped.
    """

    entries: dict
    reactants: Counter
    products: Counter
    participant_count: int

    @property
    def accounted(self):
        """True when both sides are non-empty and every slot is a reactant or product."""
        return (bool(self.reactants) and bool(self.products)
                and self.participant_count == sum(self.reactants.values()) + sum(self.products.values()))

    def coefficient(self, key):
        """Net stoichiometric coefficient: product slots minus reactant slots."""
        return self.products[key] - self.reactants[key]


def participant_slots(participants):
    participants = list(participants)
    return ParticipantSlots(
        entries={p.species_entry_id: p.species_entry for p in participants},
        reactants=Counter(p.species_entry_id for p in participants if p.role == "reactant"),
        products=Counter(p.species_entry_id for p in participants if p.role == "product"),
        participant_count=len(participants),
    )


def element_balance(slots, compositions, charges):
    """Return (net element Counter, net charge) of products minus reactants.

    ``compositions`` and ``charges`` are keyed by species-entry id and must
    cover every entry in ``slots``. A balanced reaction has every element
    count zero and a zero net charge.
    """
    balance = Counter()
    charge = 0
    for key in slots.entries:
        coefficient = slots.coefficient(key)
        for element, count in compositions[key].items():
            balance[element] += coefficient * count
        charge += coefficient * charges[key]
    return balance, charge


def is_balanced(balance, charge):
    return not any(balance.values()) and not charge


def species_scope_reason(species_entry):
    """The one per-species scope rule for the thermochemical checks, or None.

    Takes the species ENTRY, not the species: isotope content exists only
    on ``species_entry.isotope_key`` for persisted data (see
    :func:`entry_facts`), so a rule reading the species alone could never
    see a deuterated or 13C-labelled entry.

    Order, first match wins:

    1. ``charged_species_out_of_scope`` -- a non-zero recorded charge. Read
       from the column, so it is known even when the SMILES is unusable.
    2. ``unusable_species_composition`` -- the charge was never recorded,
       the SMILES does not parse, its elements cannot be counted, it counts
       no atoms at all, or it contains a dummy/wildcard atom. A structure
       that cannot be read is reported as such before anything else about it.
    3. ``isotope_labelled_species_out_of_scope`` -- the entry carries an
       ``isotope_key`` (or, for a legacy row, its species SMILES carries a
       label).

    Ions and isotopologues are out of scope pending the decisions the
    Phase D plan names (electron and nuclide reference conventions); they
    are reported, never silently treated as the neutral or natural species.
    """
    species = species_entry.species
    if species.charge is None:
        return UNUSABLE_SPECIES_COMPOSITION
    if species.charge != 0:
        return CHARGED_SPECIES_OUT_OF_SCOPE
    facts = entry_facts(species_entry)
    if not facts.parsed or not facts.composition or facts.has_dummy_atoms:
        return UNUSABLE_SPECIES_COMPOSITION
    if facts.has_isotopes:
        return ISOTOPE_LABELLED_SPECIES_OUT_OF_SCOPE
    return None
