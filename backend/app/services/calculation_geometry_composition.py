"""One composition rule for every geometry linked to a calculation.

What the rule is
----------------
A geometry linked to a calculation must be made of the atoms of the subject
that calculation is filed under. The identity says what the thing is; the
coordinates are what every number on that calculation — energies, gradients,
frequencies, Hessians — is computed from. If the two name different
collections of atoms, the record is internally contradictory and every
consumer that trusts the label gets numbers for something nobody deposited.

Why it did not exist
--------------------
A *conformer* geometry has been compared against its species entry since
:func:`app.services.species_resolution.assert_geometry_composition_matches_identity`
landed. A *calculation* geometry was compared against nothing, on any path,
for any subject — that function's own docstring said so and called the input
half "a genuine open gap". Benzene coordinates attached to a ``smiles: "C"``
species entry were accepted, and so was methane attached to a C2H5O saddle
point. Both are reproduced in
``tests/services/test_calculation_geometry_composition.py``.

Which subject, and what it is made of
-------------------------------------
``calculation`` carries a ``one_owner`` CHECK constraint, so exactly one of
``species_entry_id`` and ``transition_state_entry_id`` is set and the question
has exactly two answers. Both references already existed:

* **species entry** -- the element counts of ``species.smiles``, which is what
  the conformer rule compares against.
* **transition-state entry** -- the *sum* of the element counts of the
  reaction's reactants, which is what
  :func:`app.services.reaction_resolution.validate_transition_state_composition`
  compares the saddle point itself against. A transition state is a stationary
  point on the potential energy surface of the whole reacting system; for
  ``CH3 + H -> CH4`` its geometry holds five atoms and is neither participant.
  ``transition_state.reaction_entry_id`` is ``NOT NULL``, so the sum is always
  reachable.

Deriving the TS reference any other way would refuse correct science on the
first deposit: all 31 calculation geometries in the repository's ARC-derived
fixture payloads are TS-owned, and every one of them is the whole reacting
system rather than any participant.

Output geometries are checked too, contrary to the note that stood here
----------------------------------------------------------------------
Two modules said closing the output half would be wrong because "an
optimisation that dissociated is science to record". That argument is correct
about **connectivity** and does not transfer to **composition**. Every one of
the seven ``CalculationType`` values is a map over a fixed set of nuclei --
no electronic-structure program adds or removes one -- so dissociation,
isomerisation, proton transfer and ring opening all *conserve* element counts.
:mod:`app.services.geometry_validation` records the demonstration one
paragraph above the exemption it justified: "Methane with one hydrogen pulled
out to 5 A, i.e. a dissociated fragment pair -- passes." The case cited to
justify the exemption is a case this rule accepts, so the exemption protected
no correct science while leaving the larger half unchecked (1806 output
geometries against 326 inputs on the live instance).

What is deliberately not compared
---------------------------------
**Elements, not nuclides.** ``D`` and ``T`` count as the hydrogen they are and
``[2H]`` counts as ``H``, so an isotopologue is never a mismatch. Isotope
agreement is a separate rule with its own code
(:func:`~app.services.species_resolution.assert_geometry_isotopes_match_identity`),
which owns it under ADR 0008 section 9.

**Counts, not positions.** No coordinate is read. A scan point at 5 A, an IRC
endpoint in a product well, a constitutional isomer and a dissociated fragment
pair all pass. A check that drifted from composition into plausibility would
refuse exactly the deliberately-distorted structures this database exists to
hold.

**Absence does not block.** A ``pseudo`` owner, a transition state whose
reaction records no reactants or records a ``pseudo`` reactant, and a stored
SMILES RDKit will not parse are all incompleteness rather than contradiction,
and get the tier an absent atom map gets. A free electron is *not* absence:
its composition is empty rather than unknown, so any geometry contradicts it.

What never appears in the refusal
---------------------------------
Row ids. ``context`` names the field the depositor wrote, which is the
identifier they can act on; the ids of the rows that disagreed go to the log,
where the operator is (DR-0028 Requirement 2).
"""

from __future__ import annotations

import logging
from collections import Counter

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.error_contract import CodedValueError
from app.chemistry.geometry import resolve_element_symbol
from app.chemistry.isotopes import normalize_isotope, render_isotope_substitutions
from app.chemistry.species import (
    element_counts_from_smiles,
    format_element_counts,
    isotope_substitutions,
)
from app.db.models.calculation import Calculation
from app.db.models.common import MoleculeKind, ReactionRole
from app.db.models.geometry import GeometryAtom
from app.db.models.reaction import ReactionEntryStructureParticipant
from app.db.models.species import Species, SpeciesEntry
from app.db.models.transition_state import TransitionState, TransitionStateEntry
from app.scientific_checks import (
    CheckTier,
    CodeChannel,
    PythonCheck,
    ScientificCheck,
)

logger = logging.getLogger(__name__)

#: A geometry linked to a calculation is not made of the atoms of the subject
#: the calculation is filed under.
#:
#: One code across both owner kinds, deliberately, on the precedent of
#: ``kinetics_interpretation_statmech_owner_mismatch``. The reference differs
#: -- a species entry's own formula, or a reaction's reactant sum -- but the
#: field is the same geometry link and the repair is the same sentence: attach
#: the structure this calculation was actually run on, or file the calculation
#: under the subject that structure belongs to. Which owner disagreed is a
#: fact about the request and is carried in ``context["owner_kind"]``, not a
#: different contract.
#:
#: Distinct from ``species_geometry_composition_mismatch``, which is about a
#: *conformer* geometry deposited with a species entry, and from
#: ``transition_state_composition_mismatch``, which is about the saddle point
#: a transition-state entry is resolved from. Those two are repaired in the
#: identity block of a payload; this one is repaired on a calculation.
W_CALCULATION_GEOMETRY_COMPOSITION_MISMATCH = (
    "calculation_geometry_composition_mismatch"
)

#: Cache key under which per-session reference compositions are memoised.
#: Scoped to the ``Session`` rather than the module so that nothing survives a
#: rolled-back test or leaks between requests.
_CACHE_KEY = "_calculation_geometry_composition_reference_cache"


def _species_entry_reference(
    session: Session, species_entry_id: int
) -> Counter[str] | None:
    """Return the element counts a species entry's own identity declares.

    ``None`` means "do not judge": a pseudo-species has no atom-resolved
    composition, and a stored SMILES RDKit will not parse is an absence this
    function is not the right place to report. A free electron returns an
    *empty* counter rather than ``None`` -- zero atoms is a fact, not a gap.
    """

    species = session.scalar(
        select(Species)
        .join(SpeciesEntry, SpeciesEntry.species_id == Species.id)
        .where(SpeciesEntry.id == species_entry_id)
    )
    if species is None:
        return None
    if species.kind == MoleculeKind.pseudo:
        return None
    if species.kind == MoleculeKind.electron:
        return Counter()
    try:
        return element_counts_from_smiles(species.smiles)
    except ValueError:
        # Named by its public ref in any message; the row id goes here only.
        logger.warning(
            "Unparseable stored SMILES on species id=%s public_ref=%s: %r; "
            "calculation geometry composition not judged.",
            species.id,
            species.public_ref,
            species.smiles,
        )
        return None


def _ts_entry_reactant_rows(
    session: Session, transition_state_entry_id: int
) -> list[tuple[Species, SpeciesEntry]]:
    """Return ``(species, species entry)`` for each reactant of a TS entry's reaction.

    The one query both the composition reference and the isotope reference
    read, so the two can never disagree about which reactants a transition
    state is made of.
    """

    return [
        (species, entry)
        for species, entry in session.execute(
            select(Species, SpeciesEntry)
            .select_from(ReactionEntryStructureParticipant)
            .join(
                TransitionState,
                TransitionState.reaction_entry_id
                == ReactionEntryStructureParticipant.reaction_entry_id,
            )
            .join(
                TransitionStateEntry,
                TransitionStateEntry.transition_state_id == TransitionState.id,
            )
            .join(
                SpeciesEntry,
                SpeciesEntry.id == ReactionEntryStructureParticipant.species_entry_id,
            )
            .join(Species, Species.id == SpeciesEntry.species_id)
            .where(
                TransitionStateEntry.id == transition_state_entry_id,
                ReactionEntryStructureParticipant.role == ReactionRole.reactant,
            )
        ).all()
    ]


def _transition_state_entry_reference(
    session: Session, transition_state_entry_id: int
) -> Counter[str] | None:
    """Return the element counts of the reaction a TS entry sits in.

    The reactant side, summed -- the same reference
    :func:`app.services.reaction_resolution.validate_transition_state_composition`
    compares the saddle point against, and for the same reason: a transition
    state is a stationary point on the potential energy surface of the whole
    reacting system.

    ``None`` where the comparison would be meaningless: no reactants recorded
    yet, or any reactant a pseudo-species. The pseudo exemption is scoped to
    the *reactant* side only, exactly as it is in that function -- a pseudo
    product leaves the reactant side fully atom-resolved and does not make the
    sum unknowable.
    """

    reactants = [
        species for species, _entry in _ts_entry_reactant_rows(session, transition_state_entry_id)
    ]
    if not reactants:
        return None
    if any(species.kind == MoleculeKind.pseudo for species in reactants):
        return None

    totals: Counter[str] = Counter()
    for species in reactants:
        if species.kind == MoleculeKind.electron:
            continue
        try:
            totals += element_counts_from_smiles(species.smiles)
        except ValueError:
            logger.warning(
                "Unparseable stored SMILES on species id=%s public_ref=%s: %r; "
                "calculation geometry composition not judged.",
                species.id,
                species.public_ref,
                species.smiles,
            )
            return None
    return totals


def _reference_for(
    calc: Calculation, session: Session
) -> tuple[str, Counter[str]] | None:
    """Return ``(owner_kind, element counts)`` for a calculation's subject.

    ``None`` means the subject has no atom-resolved composition to compare
    against, which is an absence and never a refusal.
    """

    owner_kind: str
    owner_id: int
    if calc.species_entry_id is not None:
        owner_kind, owner_id = "species_entry", calc.species_entry_id
    elif calc.transition_state_entry_id is not None:
        owner_kind, owner_id = (
            "transition_state_entry",
            calc.transition_state_entry_id,
        )
    else:
        # The ``one_owner`` CHECK forbids this, but the row may not have
        # reached the database yet. Nothing to compare against is an absence.
        return None

    # Memoised per ``Session`` rather than per module, so nothing survives a
    # rolled-back transaction or leaks between requests. One upload links many
    # geometries to calculations that share a handful of subjects.
    cache: dict[tuple[str, int], Counter[str]] = session.info.setdefault(
        _CACHE_KEY, {}
    )
    key = (owner_kind, owner_id)
    if key in cache:
        return owner_kind, cache[key]

    if owner_kind == "species_entry":
        reference = _species_entry_reference(session, owner_id)
    else:
        reference = _transition_state_entry_reference(session, owner_id)

    # Only a resolved answer is memoised. ``None`` can mean "the reactants are
    # not persisted yet", which a later call in the same upload may legitimately
    # answer differently; caching it would freeze an accident of ordering into
    # a permanent exemption.
    if reference is None:
        return None
    cache[key] = reference
    return owner_kind, reference


def _geometry_element_counts(session: Session, geometry_id: int) -> Counter[str]:
    """Count a stored geometry's elements the way every comparison counts them."""

    elements = session.scalars(
        select(GeometryAtom.element).where(GeometryAtom.geometry_id == geometry_id)
    ).all()
    return Counter(resolve_element_symbol(element) for element in elements)


def assert_calculation_geometry_composition(
    session: Session,
    *,
    calc: Calculation,
    geometry_id: int,
    field: str,
) -> None:
    """Refuse a geometry not made of the atoms of the calculation's subject.

    The one implementation. Every site that inserts a
    ``calculation_input_geometry`` or ``calculation_output_geometry`` row calls
    it, on both the producer-explicit branch and the ``geometry_key`` /
    fallback branch. Checking only the explicit branch would leave the second
    reproduction in the spec open: on the computed-reaction and PDep bundles
    the fallback id is not fixed, and a transition state's calculations resolve
    ``geometry_key`` against a bundle-global map that the wire schema narrows
    for a species' calculations and not for a transition state's.

    :param session: Active SQLAlchemy session.
    :param calc: The calculation the geometry is being linked to. Its owner
        columns must already be set; they are what the reference is derived
        from.
    :param geometry_id: The resolved geometry row. Its atoms must already be
        flushed, which ``resolve_geometry_payload`` guarantees.
    :param field: Field path naming the offending link the way the depositor
        wrote it, echoed verbatim into the refusal. Never a row id.
    :raises CodedValueError: If the geometry's element counts contradict the
        composition of the subject the calculation is filed under.
    """

    resolved = _reference_for(calc, session)
    if resolved is None:
        return
    owner_kind, reference = resolved

    observed = _geometry_element_counts(session, geometry_id)
    if observed == reference:
        return

    geometry_formula = format_element_counts(observed) or "empty"
    if owner_kind == "species_entry":
        subject = "the species this calculation belongs to"
        subject_formula = format_element_counts(reference) or "no atoms at all"
        repair = (
            "Attach the structure this calculation was actually run on, or "
            "file the calculation under the species that structure belongs "
            "to. A calculation's numbers describe its geometry; if the "
            "geometry is a different molecule than the identity, every one of "
            "them is attributed to the wrong species."
        )
    else:
        subject = "the reaction this transition state sits in"
        subject_formula = format_element_counts(reference) or "no atoms at all"
        repair = (
            "A transition state is a stationary point on the potential energy "
            "surface of its whole reacting system, so its calculations run on "
            "all of those atoms -- not on one participant. Attach the "
            "saddle-point structure, or declare the extra species as "
            "participants of the reaction."
        )

    logger.warning(
        "Calculation geometry composition mismatch: calculation id=%s "
        "species_entry_id=%s transition_state_entry_id=%s geometry id=%s "
        "field=%s observed=%s reference=%s",
        calc.id,
        calc.species_entry_id,
        calc.transition_state_entry_id,
        geometry_id,
        field,
        geometry_formula,
        subject_formula,
    )

    raise CodedValueError(
        W_CALCULATION_GEOMETRY_COMPOSITION_MISMATCH,
        f"{field}: geometry is {geometry_formula}, but {subject} is "
        f"{subject_formula} "
        "(calculation_geometry_composition_mismatch). "
        + repair
        + " Hydrogens are counted explicitly on both sides, and isotope "
        "labels are counted as their element -- SMILES [2H] and the XYZ "
        "symbols D and T all count as H -- so an isotopologue is not a "
        "mismatch, and only atom counts are compared, so a scan point, an "
        "IRC point or a dissociated optimisation is not one either.",
        context={
            "field": field,
            "owner_kind": owner_kind,
            "geometry_formula": geometry_formula,
            "subject_formula": subject_formula,
        },
        message_prefix=False,
    )


CHECK_CALCULATION_GEOMETRY_COMPOSITION = ScientificCheck(
    group="A structure against its own label",
    sort_key=3,
    code=W_CALCULATION_GEOMETRY_COMPOSITION_MISMATCH,
    asserts=(
        "Every geometry linked to a calculation is made of the atoms of the "
        "subject that calculation is filed under -- the species entry's own "
        "formula, or, for a transition state, the sum of its reaction's "
        "reactants."
    ),
    tier=CheckTier.block,
    channel=CodeChannel.error_envelope,
    tier_rationale=(
        "Definitional. A calculation's numbers are computed from its geometry "
        "and attributed to its subject; a geometry made of different atoms "
        "than the subject makes that attribution false rather than weak. No "
        "correct calculation can produce one, because no electronic-structure "
        "program adds or removes a nucleus -- which is also why this holds for "
        "output geometries and not only input ones: dissociation, "
        "isomerisation and proton transfer all conserve element counts."
    ),
    adr="0008",
    enforced_by=(
        PythonCheck(
            assert_calculation_geometry_composition,
            note=(
                "Called from every site that inserts a "
                "``calculation_input_geometry`` or "
                "``calculation_output_geometry`` row -- eight of them across "
                "four modules -- on both the producer-explicit branch and the "
                "``geometry_key``/fallback branch. A guard test fails if a "
                "ninth appears unchecked."
            ),
        ),
    ),
    escape_hatch=(
        "Declare the structure's real composition: give the calculation the "
        "geometry it was run on, or file it under the subject that geometry "
        "belongs to. Absence does not block -- a ``pseudo`` owner, a "
        "transition state whose reaction records no reactants or a pseudo "
        "reactant, and an unparseable stored SMILES are all left unjudged. "
        "Only atom counts are compared, so isotopologues, scan and IRC "
        "points, constitutional isomers and dissociated optimisations all "
        "pass."
    ),
    divergence=(
        "A documented false *acceptance*, restated because a referee will "
        "ask: counts alone cannot tell a constitutional isomer from its "
        "partner, so an ethanol calculation carrying dimethyl ether "
        "coordinates passes. Catching that needs bond perception from XYZ, "
        "which fails silently on exactly the strained, radical and stretched "
        "structures where it would matter -- the same reasoning that keeps "
        "``species_geometry_composition_mismatch`` to counts. It also refuses "
        "a geometry carrying ghost or dummy centres (counterpoise/BSSE), "
        "because ``Bq`` appears in no SMILES; that refusal is correct while "
        "``geometry_atom`` has no way to say 'no nucleus here', since such a "
        "row is already miscounted by ``natoms`` and every "
        "degrees-of-freedom read downstream."
    ),
)


# ---------------------------------------------------------------------------
# Isotopes
# ---------------------------------------------------------------------------

#: A geometry linked to a calculation carries different isotopic substitutions
#: than the subject the calculation is filed under.
W_CALCULATION_GEOMETRY_ISOTOPE_MISMATCH = "calculation_geometry_isotope_mismatch"

_ISOTOPE_CACHE_KEY = "_calculation_geometry_isotope_reference_cache"

IsotopeCounts = dict[tuple[str, int], int]


def _entry_isotope_counts(entry: SpeciesEntry) -> IsotopeCounts | None:
    """Return the ``(element, mass_number)`` counts a species entry declares.

    The entry's isotopes live on ``species_entry.isotope_key`` -- the canonical
    isotope-labelled SMILES -- and **not** on ``species.smiles``, which is
    isotope-blind by design (species identity is shared by every isotopologue).
    ``NULL`` is the all-standard entry, i.e. an empty mapping. An unparseable
    key is an absence, not a refusal.
    """

    if entry.isotope_key is None:
        return {}
    try:
        return isotope_substitutions(entry.isotope_key)
    except ValueError:
        logger.warning(
            "Unparseable isotope_key on species entry id=%s: %r; calculation "
            "geometry isotopes not judged.",
            entry.id,
            entry.isotope_key,
        )
        return None


def _species_entry_isotope_reference(
    session: Session, species_entry_id: int
) -> IsotopeCounts | None:
    entry = session.get(SpeciesEntry, species_entry_id)
    if entry is None:
        return None
    species = session.get(Species, entry.species_id)
    if species is None or species.kind == MoleculeKind.pseudo:
        return None
    if species.kind == MoleculeKind.electron:
        return {}
    return _entry_isotope_counts(entry)


def _transition_state_entry_isotope_reference(
    session: Session, transition_state_entry_id: int
) -> IsotopeCounts | None:
    """Sum the isotope counts of a TS entry's reactants.

    Mirrors :func:`_transition_state_entry_reference` exactly -- same reactants,
    same absences -- because a saddle point is made of every nucleus of the
    reacting system, isotopes included.
    """

    rows = _ts_entry_reactant_rows(session, transition_state_entry_id)
    if not rows or any(sp.kind == MoleculeKind.pseudo for sp, _ in rows):
        return None
    totals: IsotopeCounts = {}
    for species, entry in rows:
        if species.kind == MoleculeKind.electron:
            continue
        counts = _entry_isotope_counts(entry)
        if counts is None:
            return None
        for key, n in counts.items():
            totals[key] = totals.get(key, 0) + n
    return totals


def _isotope_reference_for(
    calc: Calculation, session: Session
) -> tuple[str, IsotopeCounts] | None:
    if calc.species_entry_id is not None:
        owner_kind, owner_id = "species_entry", calc.species_entry_id
    elif calc.transition_state_entry_id is not None:
        owner_kind, owner_id = (
            "transition_state_entry",
            calc.transition_state_entry_id,
        )
    else:
        return None

    cache: dict[tuple[str, int], IsotopeCounts] = session.info.setdefault(
        _ISOTOPE_CACHE_KEY, {}
    )
    key = (owner_kind, owner_id)
    if key in cache:
        return owner_kind, cache[key]
    if owner_kind == "species_entry":
        reference = _species_entry_isotope_reference(session, owner_id)
    else:
        reference = _transition_state_entry_isotope_reference(session, owner_id)
    if reference is None:
        return None
    cache[key] = reference
    return owner_kind, reference


def _geometry_isotope_counts(session: Session, geometry_id: int) -> IsotopeCounts:
    """Count a stored geometry's explicit, non-standard isotopes.

    Read from ``geometry_atom.isotope_mass_number`` only. A ``D`` or ``T``
    element spelling is deliberately *not* an isotope declaration here: the
    codebase defines it as composition-neutral and isotope-silent
    (:func:`app.chemistry.geometry.resolve_element_symbol`), and the conformer
    rule (``species_geometry_isotope_mismatch``) reads it the same way.
    """

    counts: IsotopeCounts = {}
    rows = session.execute(
        select(GeometryAtom.element, GeometryAtom.isotope_mass_number).where(
            GeometryAtom.geometry_id == geometry_id,
            GeometryAtom.isotope_mass_number.is_not(None),
        )
    ).all()
    for element, mass_number in rows:
        symbol = resolve_element_symbol(element)
        if normalize_isotope(symbol, mass_number) is None:
            continue
        key = (symbol, int(mass_number))
        counts[key] = counts.get(key, 0) + 1
    return counts


def assert_calculation_geometry_isotopes(
    session: Session,
    *,
    calc: Calculation,
    geometry_id: int,
    field: str,
) -> None:
    """Refuse a geometry whose isotopes disagree with the calculation's subject.

    Runs beside :func:`assert_calculation_geometry_composition` at every site
    that links a geometry to a calculation. That rule counts elements and reads
    ``D``/``T`` as hydrogen, so it cannot see that a deuterium geometry was
    attached to a protium species (or the reverse): a deuterium energy would be
    filed under a protium record.

    **Count-based, not atom-resolved.** The species entry carries an
    atom-resolved isotope key (``isotope_key``), but a calculation geometry has
    no atom map to the species graph, so there is nothing to align positions
    with. The comparison is therefore the multiset of ``(element,
    mass_number)`` substitutions, exactly as
    :func:`app.services.species_resolution.assert_geometry_isotopes_match_identity`
    does for conformer geometries. Consequence, stated plainly: isotopomers
    (CH2D-OH vs CH3-OD) are not distinguished -- a false acceptance, never a
    false refusal.

    The standard isotope is the empty mapping on both sides, so a geometry with
    no isotopes on a species with none is never touched.

    :raises CodedValueError: If the substitution multisets differ.
    """

    resolved = _isotope_reference_for(calc, session)
    if resolved is None:
        return
    owner_kind, reference = resolved

    observed = _geometry_isotope_counts(session, geometry_id)
    if observed == reference:
        return

    geometry_text = render_isotope_substitutions(observed)
    subject_text = render_isotope_substitutions(reference)
    subject = (
        "the species this calculation belongs to"
        if owner_kind == "species_entry"
        else "the reaction this transition state sits in"
    )

    logger.warning(
        "Calculation geometry isotope mismatch: calculation id=%s "
        "species_entry_id=%s transition_state_entry_id=%s geometry id=%s "
        "field=%s observed=%s reference=%s",
        calc.id,
        calc.species_entry_id,
        calc.transition_state_entry_id,
        geometry_id,
        field,
        geometry_text,
        subject_text,
    )

    repair = (
        "A deuterated or 13C-labelled geometry is a different molecule from "
        "the ordinary one: masses, frequencies and zero-point energy all "
        "differ. Declare the same substitution on both sides -- SMILES "
        "isotope notation (e.g. [2H]) on the species and geometry.isotopes "
        "on the structure -- or file the calculation under the isotopologue "
        "it was run on. Only the count of each substituted element is "
        "compared, so which atom carries the label is not checked."
    )

    # Built by concatenation, like the composition message: ``field`` is a
    # depositor-facing path, not a code, and a single f-string opening with
    # ``{field}: `` is the shape the catalogue gate reads as a code minted
    # from a parameter.
    raise CodedValueError(
        W_CALCULATION_GEOMETRY_ISOTOPE_MISMATCH,
        f"{field}: geometry isotopes are {geometry_text}, but {subject} "
        f"declares {subject_text} (calculation_geometry_isotope_mismatch). "
        + repair,
        context={
            "field": field,
            "owner_kind": owner_kind,
            "geometry_substitutions": geometry_text,
            "subject_substitutions": subject_text,
        },
        message_prefix=False,
    )


CHECK_CALCULATION_GEOMETRY_ISOTOPES = ScientificCheck(
    group="A structure against its own label",
    sort_key=7,  # Appended after #143's shift (slots 0-6 are taken); see CHECK_CALCULATION_GEOMETRY_COMPOSITION.
    code=W_CALCULATION_GEOMETRY_ISOTOPE_MISMATCH,
    asserts=(
        "The multiset of isotopic substitutions on every geometry linked to a "
        "calculation equals the multiset declared by the subject that "
        "calculation is filed under -- the species entry's isotope key, or, "
        "for a transition state, the sum over its reaction's reactants."
    ),
    tier=CheckTier.block,
    channel=CodeChannel.error_envelope,
    tier_rationale=(
        "Definitional. The element-composition rule reads D, T and [2H] as "
        "hydrogen, so a deuterium energy could be filed under a protium "
        "species. Isotopes change nuclear masses and therefore every "
        "mass-weighted number the calculation reports; a geometry of one "
        "isotopologue under the identity of another attributes those numbers "
        "to the wrong molecule."
    ),
    adr="0008",
    enforced_by=(
        PythonCheck(
            assert_calculation_geometry_isotopes,
            note=(
                "Called beside ``assert_calculation_geometry_composition`` at "
                "every site that inserts a ``calculation_input_geometry`` or "
                "``calculation_output_geometry`` row. A guard test fails if a "
                "site runs one and not the other."
            ),
        ),
    ),
    escape_hatch=(
        "Declare the same substitution on both sides, or file the "
        "calculation under the isotopologue it was run on. Absence does not "
        "block: a ``pseudo`` owner, a transition state with no reactants or a "
        "pseudo reactant, and an unparseable isotope key are left unjudged. "
        "A ``D`` or ``T`` element spelling is isotope-silent by the "
        "documented decision in ``resolve_element_symbol`` and is not "
        "compared; only ``geometry.isotopes`` / SMILES labels count."
    ),
    divergence=(
        "A documented false *acceptance*: only the multiset is compared, so "
        "isotopomers are not distinguished, because a calculation geometry has "
        "no atom map to the species graph. A ``D`` spelling on a protium "
        "species is also accepted, by design, until the mass-versus-identity "
        "reading of D/T is settled."
    ),
)
