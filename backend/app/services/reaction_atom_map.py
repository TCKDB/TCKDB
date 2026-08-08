"""Persistence seam for a reaction's atom map (ADR 0011).

Every deposit path that can carry a transition state routes through here, so
all of them report an identical gap. They do **not** all write maps: only
:func:`app.workflows.computed_reaction.persist_computed_reaction_upload`
accepts one, because only its bundle schema has an ``atom_map`` field.

``persist_network_pdep_upload`` calls this seam with ``atom_map=None`` for
every saddle point it deposits, which is not a formality — a pressure-dependent
network is a set of micro reactions, and before that call every one of them was
deposited unmapped *and silent*, which is exactly the invisible absence
ADR 0011 exists to remove. Until the PDep bundle grows an ``atom_map`` field
the warning is all that path can honestly offer, so it says so rather than
telling a depositor to fill in a field that does not exist: see
``_PDEP_ABSENCE_REMEDY`` in :mod:`app.workflows.network_pdep`.

Blocking validation is *not* here. A self-contradictory map is refused by
:func:`tckdb_schemas.fragments.reaction_atom_map.validate_reaction_atom_map`
at the schema boundary — the payload already holds every XYZ block the checks
need, so the refusal arrives as a clean 422 before anything is written — and by
the constraints on ``reaction_atom_map_pair``, which are the same rules stated
where a second write path cannot get around them. This module resolves keys to
rows, writes the map, and turns what is *missing* into warnings.

Absence warns
-------------
An unmapped reaction is an incomplete record, not a false one: the rate
constant is still the rate constant and what is missing is the mechanistic
detail. Refusing it would reject correct science over evidence the depositor
may not have, and would make every reaction already in the database
undepositable. So the gap is reported as a structured
:class:`~tckdb_schemas.upload_warning.UploadWarning` — loudly enough that a
depositor who *has* the mapping notices they are being asked for it, which is
the whole point of the tier. A warning nobody reads would leave the corpus
splitting between mapped and unmapped records for no reason.

A reaction with no transition state is not warned about. Both legs of a map run
toward the saddle point, so a barrierless channel has nothing to map onto; a
warning it could never satisfy would be noise that trains depositors to ignore
the one that matters.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session
from tckdb_schemas.enums import ReactionRole as WireReactionRole
from tckdb_schemas.fragments.reaction_atom_map import ReactionAtomMapIn
from tckdb_schemas.upload_warning import UploadWarning

from app.chemistry.geometry import normalize_element_symbol
from app.db.models.common import AtomMapSource, ReactionRole
from app.db.models.geometry import GeometryAtom
from app.db.models.reaction_atom_map import ReactionAtomMap, ReactionAtomMapPair
from app.scientific_checks import CheckTier, PythonCheck, ScientificCheck

#: Emitted when a reaction with a transition state is deposited without a map.
W_MISSING_REACTION_ATOM_MAP = "reaction_atom_map_absent"

#: Closing sentence of the absence warning on a path whose payload schema has
#: an ``atom_map`` field to fill in.
SUPPLY_THE_MAP_REMEDY = (
    "If you followed the intrinsic reaction coordinate, you already have "
    "this — supply it as 'atom_map' (ADR 0011)."
)

#: Emitted when a map omits a declared participant molecule entirely.
W_ATOM_MAP_PARTICIPANTS_INCOMPLETE = "reaction_atom_map_participants_incomplete"

#: Emitted when a mapped participant leaves some of its own atoms unmapped, or
#: when a leg leaves saddle-point atoms claimed by nobody.
W_ATOM_MAP_ATOMS_INCOMPLETE = "reaction_atom_map_atoms_incomplete"


@dataclass(frozen=True)
class ResolvedAtomMapParticipant:
    """A declared participant, resolved to the rows an atom map needs.

    :param side: Which leg the participant sits on.
    :param species_key: The participant's bundle-local species key.
    :param participant_index: 1-based position on its side.
    :param structure_participant_id: ``reaction_entry_structure_participant.id``.
    """

    side: ReactionRole
    species_key: str
    participant_index: int
    structure_participant_id: int


def _to_db_role(side: WireReactionRole) -> ReactionRole:
    return ReactionRole(side.value)


def persist_reaction_atom_map(
    session: Session,
    atom_map: ReactionAtomMapIn | None,
    *,
    reaction_entry_id: int,
    transition_state_entry_id: int | None,
    transition_state_geometry_id: int | None,
    participants: Sequence[ResolvedAtomMapParticipant],
    geometry_id_by_key: Mapping[str, int],
    field_path: str = "atom_map",
    absence_remedy: str = SUPPLY_THE_MAP_REMEDY,
    created_by: int | None = None,
    warnings: list[UploadWarning] | None = None,
) -> ReactionAtomMap | None:
    """Write the map if there is one; report what is absent if there is not.

    :param atom_map: The producer's map, already checked for self-contradiction
        at the schema boundary. ``None`` when the deposit carries no map.
    :param reaction_entry_id: The micro reaction the map belongs to.
    :param transition_state_entry_id: The saddle-point candidate both legs run
        toward. ``None`` for a reaction with no transition state.
    :param transition_state_geometry_id: The saddle-point geometry the map's
        transition-state indices count into.
    :param participants: Every participant the reaction declares, resolved.
    :param geometry_id_by_key: Bundle-local geometry key → persisted geometry
        id, covering every geometry the map may name.
    :param absence_remedy: Closing sentence of the absence warning, telling
        this deposit path's user what to actually do. Overridden by a path
        whose payload schema has nowhere to put a map, so the warning does not
        name a field that does not exist.
    :param warnings: Optional sink for the absence and incompleteness warnings.
    :returns: The persisted map, or ``None`` when none was supplied.
    """

    if atom_map is None:
        _warn_absent(
            warnings,
            field_path=field_path,
            transition_state_entry_id=transition_state_entry_id,
            absence_remedy=absence_remedy,
        )
        return None

    if transition_state_entry_id is None or transition_state_geometry_id is None:
        # The schema layer refuses this; repeated here because this seam is
        # callable from any path and a map with no saddle point to run toward
        # is not a map.
        raise ValueError(
            f"{field_path} was supplied for a reaction with no transition "
            "state. Both legs of an atom map run toward the saddle point "
            "(ADR 0011)."
        )

    row = ReactionAtomMap(
        reaction_entry_id=reaction_entry_id,
        transition_state_entry_id=transition_state_entry_id,
        transition_state_geometry_id=transition_state_geometry_id,
        source=AtomMapSource(atom_map.source.value),
        equivalent_map_count=atom_map.equivalent_map_count,
        note=atom_map.note,
        created_by=created_by,
    )
    session.add(row)
    session.flush()

    participant_by_slot = {
        (participant.side, participant.participant_index): participant
        for participant in participants
    }

    geometry_ids = {transition_state_geometry_id}
    for mapping in atom_map.participants:
        geometry_id = geometry_id_by_key.get(mapping.geometry_key)
        if geometry_id is None:
            raise ValueError(
                f"{field_path} names geometry '{mapping.geometry_key}', which "
                "this deposit does not define."
            )
        geometry_ids.add(geometry_id)

    element_by_atom = _element_index(session, geometry_ids)

    mapped_atom_counts: dict[tuple[ReactionRole, int], int] = {}
    claimed_ts_by_side: dict[ReactionRole, set[int]] = {
        ReactionRole.reactant: set(),
        ReactionRole.product: set(),
    }

    for mapping in atom_map.participants:
        side = _to_db_role(mapping.side)
        slot = (side, mapping.participant_index)
        participant = participant_by_slot.get(slot)
        if participant is None:
            raise ValueError(
                f"{field_path} names {side.value} {mapping.participant_index}, "
                "which this reaction does not declare."
            )
        geometry_id = geometry_id_by_key[mapping.geometry_key]

        for atom_index, ts_atom_index in sorted(mapping.atom_to_ts.items()):
            element = element_by_atom.get((geometry_id, atom_index))
            if element is None:
                raise ValueError(
                    f"{field_path} maps atom {atom_index} of {side.value} "
                    f"{mapping.participant_index}, which geometry "
                    f"'{mapping.geometry_key}' does not have."
                )
            ts_element = element_by_atom.get(
                (transition_state_geometry_id, ts_atom_index)
            )
            if ts_element is None:
                raise ValueError(
                    f"{field_path} maps onto transition-state atom "
                    f"{ts_atom_index}, which the saddle-point geometry does "
                    "not have."
                )
            # Compared normalised, stored raw. The two ends quote two
            # geometries, each of which stores the element symbol its own XYZ
            # wrote: ``Cl`` and ``CL`` are one element written by two
            # programs, and refusing that map would reject correct chemistry
            # over a capital letter, which ADR 0008 puts out of bounds for a
            # blocking check. Carbon becoming nitrogen still cannot be what it
            # says it is, and still blocks.
            if normalize_element_symbol(element) != normalize_element_symbol(
                ts_element
            ):
                raise ValueError(
                    f"{field_path} maps atom {atom_index} of {side.value} "
                    f"{mapping.participant_index}, which is {element}, onto "
                    f"transition-state atom {ts_atom_index}, which is "
                    f"{ts_element}. An element does not change across a "
                    "reaction."
                )
            session.add(
                ReactionAtomMapPair(
                    atom_map_id=row.id,
                    side=side,
                    structure_participant_id=participant.structure_participant_id,
                    geometry_id=geometry_id,
                    atom_index=atom_index,
                    transition_state_geometry_id=transition_state_geometry_id,
                    ts_atom_index=ts_atom_index,
                    # Each end stores what its own geometry stores, so both
                    # composite foreign keys into ``geometry_atom`` resolve
                    # even when the two geometries disagree about case.
                    element=element,
                    ts_element=ts_element,
                )
            )
            claimed_ts_by_side[side].add(ts_atom_index)

        mapped_atom_counts[slot] = len(mapping.atom_to_ts)

    session.flush()

    _warn_incomplete(
        warnings,
        field_path=field_path,
        participants=participants,
        mapped_atom_counts=mapped_atom_counts,
        claimed_ts_by_side=claimed_ts_by_side,
        atom_counts=_geometry_atom_counts(element_by_atom),
        geometry_id_by_slot={
            (_to_db_role(m.side), m.participant_index): geometry_id_by_key[
                m.geometry_key
            ]
            for m in atom_map.participants
        },
        transition_state_geometry_id=transition_state_geometry_id,
    )
    return row


def _element_index(
    session: Session, geometry_ids: set[int]
) -> dict[tuple[int, int], str]:
    """Return ``(geometry_id, atom_index) -> element`` for the named geometries.

    The element is read from the stored geometry rather than taken from the
    payload: the map's element consistency is a claim about what the deposited
    geometries actually contain, and ``reaction_atom_map_pair`` carries the
    values into two foreign keys that make the claim structural.

    Returned **as stored**, only unpadded — ``geometry_atom.element`` is
    ``character(2)``, so a one-letter symbol reads back as ``"C "``. It is not
    case-normalised here, because each value has to go back into its own
    geometry's foreign key exactly as that geometry spells it; callers
    normalise through
    :func:`~app.chemistry.geometry.normalize_element_symbol` when they
    *compare*.
    """

    rows = session.execute(
        select(GeometryAtom.geometry_id, GeometryAtom.atom_index, GeometryAtom.element)
        .where(GeometryAtom.geometry_id.in_(geometry_ids))
    ).all()
    return {
        (geometry_id, atom_index): element.strip()
        for geometry_id, atom_index, element in rows
    }


def _geometry_atom_counts(
    element_by_atom: Mapping[tuple[int, int], str],
) -> dict[int, int]:
    counts: dict[int, int] = {}
    for geometry_id, _atom_index in element_by_atom:
        counts[geometry_id] = counts.get(geometry_id, 0) + 1
    return counts


def _warn_absent(
    warnings: list[UploadWarning] | None,
    *,
    field_path: str,
    transition_state_entry_id: int | None,
    absence_remedy: str = SUPPLY_THE_MAP_REMEDY,
) -> None:
    if warnings is None or transition_state_entry_id is None:
        return
    warnings.append(
        UploadWarning(
            field=field_path,
            code=W_MISSING_REACTION_ATOM_MAP,
            message=(
                "This reaction was deposited without an atom map. The rate "
                "constant and the saddle point are stored, but nothing in the "
                "record says which atom of the reactants is which atom of the "
                "transition state and of the products, so the bonds that break "
                "and form cannot be read back, reaction-path degeneracy cannot "
                "be derived, a kinetic isotope effect cannot be followed atom "
                "by atom, and the reaction cannot be exported in the "
                "atom-mapped form reaction machine learning consumes. TCKDB "
                "will not infer one: several chemically distinct maps are "
                "usually consistent with the same reactants and products, and "
                "choosing one by algorithm would manufacture provenance. "
                + absence_remedy
            ),
        )
    )


def _warn_incomplete(
    warnings: list[UploadWarning] | None,
    *,
    field_path: str,
    participants: Sequence[ResolvedAtomMapParticipant],
    mapped_atom_counts: Mapping[tuple[ReactionRole, int], int],
    claimed_ts_by_side: Mapping[ReactionRole, set[int]],
    atom_counts: Mapping[int, int],
    geometry_id_by_slot: Mapping[tuple[ReactionRole, int], int],
    transition_state_geometry_id: int,
) -> None:
    """Report a map that is partial. Partial is true-but-incomplete, so it warns.

    A depositor who maps three of a molecule's five atoms has given an
    incomplete map, not a false one, and ADR 0008 disqualifies a check that
    could fire on a correct novel result from blocking. What the warning buys
    is that the gap is visible: a consumer reading the map back can tell the
    difference between "these atoms correspond" and "these atoms correspond and
    nothing else was said".
    """

    if warnings is None:
        return

    unmapped = [
        f"{participant.side.value} {participant.participant_index} "
        f"('{participant.species_key}')"
        for participant in participants
        if (participant.side, participant.participant_index)
        not in mapped_atom_counts
    ]
    if unmapped:
        warnings.append(
            UploadWarning(
                field=field_path,
                code=W_ATOM_MAP_PARTICIPANTS_INCOMPLETE,
                message=(
                    "The atom map does not cover every participant of this "
                    f"reaction: {', '.join(unmapped)} carry no mapping. The "
                    "map is stored as given; the unmapped molecules' atoms "
                    "cannot be followed across the reaction."
                ),
            )
        )

    gaps: list[str] = []
    for slot, mapped in sorted(
        mapped_atom_counts.items(), key=lambda item: (item[0][0].value, item[0][1])
    ):
        total = atom_counts.get(geometry_id_by_slot[slot], mapped)
        if mapped < total:
            gaps.append(
                f"{slot[0].value} {slot[1]} maps {mapped} of its {total} atoms"
            )

    ts_natoms = atom_counts.get(transition_state_geometry_id, 0)
    full_ts = set(range(1, ts_natoms + 1))
    for side, claimed in sorted(
        claimed_ts_by_side.items(), key=lambda item: item[0].value
    ):
        if not claimed:
            continue
        unclaimed = sorted(full_ts - claimed)
        if unclaimed:
            gaps.append(
                f"the {side.value} leg leaves transition-state atom(s) "
                f"{unclaimed} unclaimed"
            )

    if gaps:
        warnings.append(
            UploadWarning(
                field=field_path,
                code=W_ATOM_MAP_ATOMS_INCOMPLETE,
                message=(
                    "The atom map is partial: "
                    + "; ".join(gaps)
                    + ". A partial map says nothing about the atoms it omits."
                ),
            )
        )


CHECK_ATOM_MAP_ABSENT = ScientificCheck(
    group="Atom mapping across a reaction",
    sort_key=5,
    code=W_MISSING_REACTION_ATOM_MAP,
    asserts=(
        "A reaction that has a transition state should say which atom of the "
        "reactants is which atom of the saddle point and of the products."
    ),
    tier=CheckTier.warn,
    tier_rationale=(
        "Absence, not contradiction. An unmapped reaction is an incomplete "
        "record rather than a false one — the rate constant is still the rate "
        "constant and what is missing is the mechanistic detail. Blocking "
        "would reject correct science over evidence the depositor may not "
        "have, and would make every reaction already in the database "
        "undepositable."
    ),
    adr="0011, 0008",
    enforced_by=(
        PythonCheck(
            _warn_absent,
            note=(
                "A reaction with no transition state is not warned about: both "
                "legs of a map run toward the saddle point, so a barrierless "
                "channel has nothing to map onto and a warning it could never "
                "satisfy would train depositors to ignore the one that "
                "matters. The PDep bundle has no ``atom_map`` field yet, so on "
                "that path the warning carries a different remedy sentence "
                "rather than naming a field that does not exist."
            ),
        ),
    ),
    escape_hatch=(
        "None is needed — the warning *is* the accommodation. TCKDB "
        "deliberately will not infer a map: several chemically distinct maps "
        "are usually consistent with the same reactants and products, so "
        "choosing one by algorithm would manufacture provenance."
    ),
)

CHECK_ATOM_MAP_INCOMPLETE = ScientificCheck(
    group="Atom mapping across a reaction",
    sort_key=6,
    code=(W_ATOM_MAP_PARTICIPANTS_INCOMPLETE, W_ATOM_MAP_ATOMS_INCOMPLETE),
    asserts=(
        "A supplied atom map should cover every declared participant molecule, "
        "every atom of each mapped participant, and every atom of the saddle "
        "point."
    ),
    tier=CheckTier.warn,
    tier_rationale=(
        "Absence again, at finer grain. A partial map is a true-but-partial "
        "record; only a map that contradicts *itself* is refused, and that is "
        "handled at the blocking tier by ``validate_reaction_atom_map`` and by "
        "the constraints on ``reaction_atom_map_pair``."
    ),
    adr="0011, 0008",
    enforced_by=(
        PythonCheck(
            _warn_incomplete,
            note=(
                "Two codes from one seam: "
                "``reaction_atom_map_participants_incomplete`` when a declared "
                "molecule is missing from the map entirely, "
                "``reaction_atom_map_atoms_incomplete`` when a mapped "
                "participant leaves its own atoms unmapped or a leg leaves "
                "saddle-point atoms claimed by nobody."
            ),
        ),
    ),
    escape_hatch=None,
)


__all__ = [
    "SUPPLY_THE_MAP_REMEDY",
    "W_ATOM_MAP_ATOMS_INCOMPLETE",
    "W_ATOM_MAP_PARTICIPANTS_INCOMPLETE",
    "W_MISSING_REACTION_ATOM_MAP",
    "ResolvedAtomMapParticipant",
    "persist_reaction_atom_map",
]
