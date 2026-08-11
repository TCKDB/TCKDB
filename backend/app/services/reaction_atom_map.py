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

Validation a map can fail *on its own* is not here. A self-contradictory map is
refused by
:func:`tckdb_schemas.fragments.reaction_atom_map.validate_reaction_atom_map`
at the schema boundary — the payload already holds every XYZ block the checks
need, so the refusal arrives as a clean 422 before anything is written — and by
the constraints on ``reaction_atom_map_pair``, which are the same rules stated
where a second write path cannot get around them. This module resolves keys to
rows, writes the map, and turns what is *missing* into warnings.

One blocking rule does live here, because it is the only one that cannot be
seen from the ``atom_map`` fragment alone.
:func:`validate_atom_map_agrees_with_irc_evidence` compares the map against the
IRC participant mappings on ``transition_state_validation_evidence``, which the
same deposit writes through a different surface. Those mappings partition the
saddle-point atoms among the declared participants and the map refines that
partition into a bijection, so the two are one claim at two resolutions and may
not disagree. Both surfaces passed independently until this existed, and
nothing compared them.

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
from itertools import permutations

from sqlalchemy import select
from sqlalchemy.orm import Session
from tckdb_schemas.enums import ReactionRole as WireReactionRole
from tckdb_schemas.fragments.reaction_atom_map import (
    W_ATOM_MAP_ELEMENT_NOT_CONSERVED,
    W_ATOM_MAP_INDICES_NOT_GEOMETRY_RELATIVE,
    W_ATOM_MAP_PARTICIPANT_NOT_DECLARED,
    W_ATOM_MAP_WITHOUT_TRANSITION_STATE,
    ReactionAtomMapIn,
)
from tckdb_schemas.upload_warning import UploadWarning

from app.api.error_contract import CodedValueError
from app.chemistry.geometry import normalize_element_symbol
from app.db.models.common import AtomMapSource, ReactionRole
from app.db.models.geometry import GeometryAtom
from app.db.models.reaction import ReactionEntryStructureParticipant
from app.db.models.reaction_atom_map import ReactionAtomMap, ReactionAtomMapPair
from app.db.models.transition_state import TransitionStateValidationEvidence
from app.scientific_checks import (
    CheckTier,
    CodeChannel,
    PythonCheck,
    ScientificCheck,
)

#: Emitted when a reaction with a transition state is deposited without a map.
W_MISSING_REACTION_ATOM_MAP = "reaction_atom_map_absent"

#: Closing sentence of the absence warning on a path whose payload schema has
#: an ``atom_map`` field to fill in.
SUPPLY_THE_MAP_REMEDY = (
    "If you followed the intrinsic reaction coordinate, you already have "
    "this — supply it as 'atom_map' (ADR 0011)."
)

#: Raised when a deposit's atom map and its IRC participant mapping give two
#: different answers about which saddle-point atoms a participant is made of.
W_ATOM_MAP_CONTRADICTS_IRC_MAPPING = "atom_map_contradicts_irc_mapping"

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
    subject_label: str = "transition state",
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
    :param subject_label: Producer-facing name of the saddle point, used by the
        agreement check below when it has to name what it refused.
    :param warnings: Optional sink for the absence and incompleteness warnings.
    :returns: The persisted map, or ``None`` when none was supplied.

    A participant the map declares atomless -- a free electron -- writes no
    ``reaction_atom_map_pair`` rows, because a pair row is one atom identified
    with one saddle-point atom and it has none. It still counts as mapped here,
    so the incompleteness warning does not report it as a participant the
    depositor forgot. The stored map therefore cannot distinguish a
    participant declared to have no atoms from one the map left out; the
    distinction is only checkable where the payload states it, at the wire
    boundary, and recording it would need a per-participant row this table does
    not have.
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
        raise CodedValueError(
            W_ATOM_MAP_WITHOUT_TRANSITION_STATE,
            f"{field_path} was supplied for a reaction with no transition "
            "state. Both legs of an atom map run toward the saddle point "
            "(ADR 0011).",
            context={"field_path": field_path},
            message_prefix=False,
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
        if mapping.geometry_key is None:
            # A participant the map declares atomless. The wire boundary has
            # already checked that the reaction agrees it has no atoms; here
            # there is simply no geometry to read and no pair row to write.
            continue
        geometry_id = geometry_id_by_key.get(mapping.geometry_key)
        if geometry_id is None:
            raise CodedValueError(
                W_ATOM_MAP_INDICES_NOT_GEOMETRY_RELATIVE,
                f"{field_path} names geometry '{mapping.geometry_key}', which "
                "this deposit does not define.",
                context={"geometry_key": mapping.geometry_key},
                message_prefix=False,
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
            raise CodedValueError(
                W_ATOM_MAP_PARTICIPANT_NOT_DECLARED,
                f"{field_path} names {side.value} {mapping.participant_index}, "
                "which this reaction does not declare.",
                context={
                    "side": side.value,
                    "participant_index": mapping.participant_index,
                },
                message_prefix=False,
            )
        if mapping.geometry_key is None:
            # Atomless, so it contributes no pair rows -- but it *is* mapped,
            # and recording that is the whole point of letting it be named:
            # the incompleteness warning below must not report a free electron
            # as a participant the depositor forgot.
            mapped_atom_counts[slot] = 0
            continue
        geometry_id = geometry_id_by_key[mapping.geometry_key]

        for atom_index, ts_atom_index in sorted(mapping.atom_to_ts.items()):
            element = element_by_atom.get((geometry_id, atom_index))
            if element is None:
                raise CodedValueError(
                    W_ATOM_MAP_INDICES_NOT_GEOMETRY_RELATIVE,
                    f"{field_path} maps atom {atom_index} of {side.value} "
                    f"{mapping.participant_index}, which geometry "
                    f"'{mapping.geometry_key}' does not have.",
                    context={
                        "atom_index": atom_index,
                        "geometry_key": mapping.geometry_key,
                    },
                    message_prefix=False,
                )
            ts_element = element_by_atom.get(
                (transition_state_geometry_id, ts_atom_index)
            )
            if ts_element is None:
                raise CodedValueError(
                    W_ATOM_MAP_INDICES_NOT_GEOMETRY_RELATIVE,
                    f"{field_path} maps onto transition-state atom "
                    f"{ts_atom_index}, which the saddle-point geometry does "
                    "not have.",
                    context={"ts_atom_index": ts_atom_index},
                    message_prefix=False,
                )
            # Compared normalised, and it stays that way even though
            # `parse_xyz` now canonicalises the case at ingestion.
            #
            # Ingestion canonicalisation is a *convention held by application
            # code*, not an invariant the database enforces: no CHECK
            # constraint requires `geometry_atom.element` to be canonical, so
            # anything that writes the table without going through `parse_xyz`
            # -- a restore from a backup older than ``b4e7c1d20f83``, a bulk
            # import, a future write path -- can put `CL` back. This is a
            # blocking check, so it has to be correct on rows this process
            # never wrote. Dropping the normalisation would make it refuse a
            # perfectly correct map over a capital letter on exactly those
            # rows, which is what ADR 0008 disqualifies a blocking check from
            # doing.
            #
            # On canonical input it is a no-op, so correctness here costs
            # nothing. Carbon becoming nitrogen still cannot be what it says it
            # is, and still blocks.
            if normalize_element_symbol(element) != normalize_element_symbol(
                ts_element
            ):
                raise CodedValueError(
                    W_ATOM_MAP_ELEMENT_NOT_CONSERVED,
                    f"{field_path} maps atom {atom_index} of {side.value} "
                    f"{mapping.participant_index}, which is {element}, onto "
                    f"transition-state atom {ts_atom_index}, which is "
                    f"{ts_element}. An element does not change across a "
                    "reaction.",
                    context={
                        "atom_index": atom_index,
                        "element": element,
                        "ts_atom_index": ts_atom_index,
                        "ts_element": ts_element,
                    },
                    message_prefix=False,
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
                    # even where the two geometries disagree about case. On a
                    # deposit that came through `parse_xyz` they now agree, but
                    # the write path must not assume it any more than the
                    # comparison above does.
                    element=element,
                    ts_element=ts_element,
                )
            )
            claimed_ts_by_side[side].add(ts_atom_index)

        mapped_atom_counts[slot] = len(mapping.atom_to_ts)

    session.flush()

    # Blocking, and it has to run here rather than at the wire boundary: the
    # IRC partition lives in rows this payload's *other* surface wrote, not in
    # the atom_map fragment. Called from both seams so the deposit order does
    # not decide whether the contradiction is seen.
    validate_atom_map_agrees_with_irc_evidence(
        session,
        reaction_entry_id=reaction_entry_id,
        transition_state_entry_id=transition_state_entry_id,
        subject_label=subject_label,
        field_path=field_path,
    )

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
            if m.geometry_key is not None
        },
        transition_state_geometry_id=transition_state_geometry_id,
    )
    return row


def validate_atom_map_agrees_with_irc_evidence(
    session: Session,
    *,
    reaction_entry_id: int,
    transition_state_entry_id: int | None,
    subject_label: str = "transition state",
    field_path: str = "atom_map",
) -> None:
    """Refuse two contradictory partitions of one saddle point.

    ``transition_state_validation_evidence``'s participant mappings *partition*
    the saddle-point atoms among the declared participants — "TS atoms 1,2,3
    belong to reactant 1". An atom map says which reactant atom each of those
    saddle-point atoms **is**. As
    :mod:`app.db.models.reaction_atom_map` puts it, the atom map is "the
    refinement of that partition into a bijection": they are one claim at two
    resolutions, so a refinement that contradicts what it refines is a
    contradiction rather than a difference of detail. Until this existed both
    could be deposited on the same saddle point, stating incompatible
    partitions, and each passed on its own because nothing compared them.

    Reads both surfaces from the database rather than from either caller's
    payload, so it gives the same verdict from whichever seam runs second — see
    the ordering note on the call sites.

    What is compared, and what deliberately is not
    ----------------------------------------------
    Only saddle-point atoms *both* surfaces speak about. The rule is a subset
    containment per participant, not an equality, and that is what makes
    partiality safe:

    * **A partial atom map is not a contradiction.** A map covering three of a
      molecule's five atoms, or omitting a participant altogether, simply says
      nothing about the rest; the coverage guard next door already reports that
      as ``reaction_atom_map_participants_incomplete`` /
      ``reaction_atom_map_atoms_incomplete``. Absence is not disagreement.
    * **An absent IRC mapping is not a contradiction.** The mappings are
      optional on every path, so a deposit carrying a map and no partition is
      accepted with nothing said about it. A reaction releasing a free electron
      no longer has to be one of those: a zero-atom participant is expressed on
      both surfaces as an empty atom list, so such a deposit can carry a
      complete partition and be compared like any other. What is compared is
      still only the atoms both surfaces speak about, and a participant with no
      atoms contributes none to either.
    * **A barrierless channel has neither.** Both legs of a map run toward the
      saddle point, so a reaction with no transition state has no map to compare
      and no evidence to compare it with.
    * **Failed evidence is not compared.** ``passed=False`` records are skipped,
      matching ``validate_ts_evidence_participant_composition``: a mapping that
      does not claim to be evidence is not contradicted by anything, and
      ``validate_ts_evidence_set`` only holds *passing* mappings to covering
      every atom, so a failed one may be partial or exploratory by design.

    Indistinguishable participants
    ------------------------------
    Two participants on the same side that are the same species entry are
    interchangeable, and which one a depositor called "reactant 1" is arbitrary
    in each surface independently. A disagreement that a permutation *within
    such a group* would resolve is a labelling difference, not a contradiction,
    so it is not refused. The partition itself is still checked: swapping one
    atom between two identical methyls changes which atoms form a molecule and
    no permutation repairs that. Symmetry *inside* one molecule never reaches
    this check at all, because the comparison is between sets of saddle-point
    atoms rather than between per-atom bijections.

    :param reaction_entry_id: The micro reaction both surfaces describe.
    :param transition_state_entry_id: The saddle point both surfaces describe.
        ``None`` compares nothing.
    :raises ValueError: If the two surfaces assign a saddle-point atom to
        different participants.
    """

    if transition_state_entry_id is None:
        return

    atom_map_id = session.execute(
        select(ReactionAtomMap.id).where(
            ReactionAtomMap.reaction_entry_id == reaction_entry_id,
            ReactionAtomMap.transition_state_entry_id == transition_state_entry_id,
        )
    ).scalar_one_or_none()
    if atom_map_id is None:
        return

    evidence_rows = session.execute(
        select(
            TransitionStateValidationEvidence.reactant_participant_mapping,
            TransitionStateValidationEvidence.product_participant_mapping,
        ).where(
            TransitionStateValidationEvidence.transition_state_entry_id
            == transition_state_entry_id,
            TransitionStateValidationEvidence.passed.is_(True),
        )
    ).all()
    if not evidence_rows:
        return

    slot_by_participant_id: dict[int, tuple[ReactionRole, int]] = {}
    species_by_slot: dict[tuple[ReactionRole, int], int | None] = {}
    for participant_id, role, participant_index, species_entry_id in session.execute(
        select(
            ReactionEntryStructureParticipant.id,
            ReactionEntryStructureParticipant.role,
            ReactionEntryStructureParticipant.participant_index,
            ReactionEntryStructureParticipant.species_entry_id,
        ).where(
            ReactionEntryStructureParticipant.reaction_entry_id == reaction_entry_id
        )
    ).all():
        slot = (role, participant_index)
        slot_by_participant_id[participant_id] = slot
        species_by_slot[slot] = species_entry_id

    map_claims: dict[tuple[ReactionRole, int], set[int]] = {}
    for structure_participant_id, ts_atom_index in session.execute(
        select(
            ReactionAtomMapPair.structure_participant_id,
            ReactionAtomMapPair.ts_atom_index,
        ).where(ReactionAtomMapPair.atom_map_id == atom_map_id)
    ).all():
        slot = slot_by_participant_id.get(structure_participant_id)
        if slot is None:  # pragma: no cover - composite FK already guarantees it
            continue
        map_claims.setdefault(slot, set()).add(ts_atom_index)

    if not map_claims:  # pragma: no cover - a map with no pairs cannot be written
        return

    for reactant_mapping, product_mapping in evidence_rows:
        if reactant_mapping is None or product_mapping is None:
            continue
        irc_claims: dict[tuple[ReactionRole, int], set[int]] = {}
        for role, mapping in (
            (ReactionRole.reactant, reactant_mapping),
            (ReactionRole.product, product_mapping),
        ):
            for participant_key, atom_indices in mapping.items():
                _, _, index_text = participant_key.partition(":")
                try:
                    participant_index = int(index_text)
                except ValueError:  # pragma: no cover - shape already validated
                    continue
                irc_claims[(role, participant_index)] = set(atom_indices)
        _raise_on_partition_disagreement(
            map_claims=map_claims,
            irc_claims=irc_claims,
            species_by_slot=species_by_slot,
            subject_label=subject_label,
            field_path=field_path,
        )


def _raise_on_partition_disagreement(
    *,
    map_claims: Mapping[tuple[ReactionRole, int], set[int]],
    irc_claims: Mapping[tuple[ReactionRole, int], set[int]],
    species_by_slot: Mapping[tuple[ReactionRole, int], int | None],
    subject_label: str,
    field_path: str,
) -> None:
    """Compare the two partitions one side at a time, tolerating relabelling."""

    for role in (ReactionRole.reactant, ReactionRole.product):
        # ``==`` rather than ``is``: ``ReactionRole`` is a ``str`` enum, so a
        # driver handing back a bare ``'reactant'`` would compare equal and fail
        # an identity test, silently dropping a whole leg from the comparison.
        side_claims = {
            slot: atoms for slot, atoms in irc_claims.items() if slot[0] == role
        }
        if not side_claims:
            continue

        groups: dict[int | None, list[tuple[ReactionRole, int]]] = {}
        for slot in side_claims:
            groups.setdefault(species_by_slot.get(slot), []).append(slot)

        for slots in groups.values():
            slots.sort()
            mapped = [slot for slot in slots if map_claims.get(slot)]
            if not mapped:
                continue
            # ``tuple(mapped)`` is itself one of these candidates, so reaching
            # the raise below guarantees the identity assignment failed too.
            if any(
                all(
                    map_claims[claimed] <= side_claims[against]
                    for claimed, against in zip(mapped, candidate, strict=True)
                )
                for candidate in permutations(slots, len(mapped))
            ):
                continue

            offender = next(
                slot
                for slot in mapped
                if not map_claims[slot] <= side_claims[slot]
            )
            disputed = sorted(map_claims[offender] - side_claims[offender])
            elsewhere = sorted(
                {
                    f"{other[0].value} {other[1]}"
                    for other in side_claims
                    if side_claims[other] & set(disputed)
                }
            )
            raise CodedValueError(
                W_ATOM_MAP_CONTRADICTS_IRC_MAPPING,
                f"Transition state '{subject_label}' {field_path} contradicts "
                "its own IRC participant mapping: the atom map assigns "
                f"saddle-point atom(s) {disputed} to {offender[0].value} "
                f"{offender[1]}, which the IRC mapping assigns to "
                f"{', '.join(elsewhere) or 'no participant'} "
                f"({W_ATOM_MAP_CONTRADICTS_IRC_MAPPING}). An atom map is the "
                "refinement of that partition into a bijection, so the two are "
                "one claim about one saddle point at two resolutions and a "
                "record cannot assert both. Correct whichever surface is wrong, "
                "or omit one of them.",
                context={
                    "disputed_ts_atoms": disputed,
                    "atom_map_assigns_to": f"{offender[0].value} {offender[1]}",
                    "irc_mapping_assigns_to": elsewhere,
                },
                message_prefix=False,
            )


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
    *compare*. :func:`~app.chemistry.geometry.parse_xyz` canonicalises the case
    on the way in, so in practice the stored value is already canonical — but
    nothing in the schema requires it to be, so a caller that compares must
    still normalise.
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
        geometry_id = geometry_id_by_slot.get(slot)
        if geometry_id is None:
            # A participant the map declares atomless names no geometry. It
            # maps none of its zero atoms, which is complete, not partial.
            continue
        total = atom_counts.get(geometry_id, mapped)
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


CHECK_ATOM_MAP_AGREES_WITH_IRC_MAPPING = ScientificCheck(
    group="Atom mapping across a reaction",
    sort_key=5,
    code=W_ATOM_MAP_CONTRADICTS_IRC_MAPPING,
    asserts=(
        "Where a deposit carries both an atom map and an IRC participant "
        "mapping for the same saddle point, they agree about which "
        "saddle-point atoms each participant is made of."
    ),
    tier=CheckTier.block,
    channel=CodeChannel.error_envelope,
    tier_rationale=(
        "Definitional, because the two surfaces are one claim at two "
        "resolutions rather than two claims. An IRC participant mapping "
        "partitions the saddle-point atoms among the declared participants; an "
        "atom map is, in this schema's own words, 'the refinement of that "
        "partition into a bijection'. A refinement that contradicts what it "
        "refines is not extra detail, it is a record asserting that one saddle "
        "point is two incompatible things at once, and no correct calculation "
        "produces both halves of it — the depositor followed one intrinsic "
        "reaction coordinate, not two. This is the same class of claim as "
        "``CHECK_TS_IRC_MAPPING_ELEMENTS`` and "
        "``CHECK_ATOM_MAP_ELEMENT_CONSERVED``, which already block, and leaving "
        "it advisory would let the pair assert together what neither is allowed "
        "to assert alone."
    ),
    adr="0011, 0008",
    enforced_by=(
        PythonCheck(
            validate_atom_map_agrees_with_irc_evidence,
            note=(
                "Called from *both* seams — ``persist_reaction_atom_map`` and "
                "``persist_transition_state_validation_evidence`` — and reads "
                "both surfaces from the database rather than from either "
                "caller's payload, so whichever a deposit writes second "
                "delivers the same verdict. Today the second is always the "
                "atom map: the computed-reaction bundle is the only payload "
                "with an ``atom_map`` field and writes it after the evidence, "
                "and no path can attach a map to a saddle point deposited "
                "earlier because every transition-state entry is created fresh "
                "by the deposit that writes it. Both are incidental orderings, "
                "so neither is relied on."
            ),
        ),
    ),
    escape_hatch=(
        "Omit one surface, or correct whichever is wrong — the mappings are "
        "optional on every path and a partial atom map is always accepted. "
        "Three absences are deliberately *not* disagreements: an atom map that "
        "omits a participant or leaves atoms unmapped is compared only over "
        "what it does claim, a transition state with no passing IRC mapping is "
        "not compared at all, and a barrierless channel has neither surface. "
        "Two participants on one side that are the same species entry are "
        "interchangeable, so a disagreement a permutation within that group "
        "would resolve is treated as arbitrary labelling rather than "
        "contradiction. A participant with no atoms is not an absence: both "
        "surfaces can say it has none -- an empty atom list -- so a reaction "
        "releasing a free electron carries a complete partition on both and is "
        "compared like any other, with the electron contributing no atoms to "
        "either side of the comparison."
    ),
)

CHECK_ATOM_MAP_ABSENT = ScientificCheck(
    group="Atom mapping across a reaction",
    sort_key=6,
    code=W_MISSING_REACTION_ATOM_MAP,
    asserts=(
        "A reaction that has a transition state should say which atom of the "
        "reactants is which atom of the saddle point and of the products."
    ),
    tier=CheckTier.warn,
    channel=CodeChannel.upload_warning,
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
    sort_key=7,
    code=(W_ATOM_MAP_PARTICIPANTS_INCOMPLETE, W_ATOM_MAP_ATOMS_INCOMPLETE),
    asserts=(
        "A supplied atom map should cover every declared participant molecule, "
        "every atom of each mapped participant, and every atom of the saddle "
        "point."
    ),
    tier=CheckTier.warn,
    channel=CodeChannel.upload_warning,
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
    "W_ATOM_MAP_CONTRADICTS_IRC_MAPPING",
    "W_ATOM_MAP_PARTICIPANTS_INCOMPLETE",
    "W_MISSING_REACTION_ATOM_MAP",
    "ResolvedAtomMapParticipant",
    "persist_reaction_atom_map",
    "validate_atom_map_agrees_with_irc_evidence",
]
