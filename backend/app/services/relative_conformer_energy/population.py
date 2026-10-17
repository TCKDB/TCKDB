"""The population a relative conformer energy is asked over: which conformer groups, and which determinations of them.

The compared unit is the conformer group, so a hidden group must not be in the world at all. That is a *population
filter*, applied before anything is read or assessed: a group the read profile hides is dropped without trace (not
listed, not counted, not named), and so is every determination whose observation or group is hidden. Filtering
before the assessment is what keeps a hidden group's ref out of the structure assessment's own output (which names
the group of each observation it loads), and it cannot borrow: nothing is filled in from a visible sibling.

A visible group with no determination is still a member; it is reported absent, not dropped.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.errors import NotFoundError, not_found
from app.db.models.common import RecordReviewStatus, StructureDeterminationTargetKind, SubmissionRecordType
from app.db.models.species import ConformerGroup, ConformerObservation
from app.db.models.structure_determination import StructureDetermination
from app.services.relative_conformer_energy.bounds import check_members
from app.services.relative_conformer_energy.models import (
    GROUP_BELOW_REVIEW_FLOOR,
    GROUP_TERMINAL_REVIEW_STATUS,
    RelativeEnergyRequest,
)
from app.services.scientific_read.common import fetch_review_badges, visible_statuses
from app.services.structure_selection.loader import assert_subject_visible
from app.services.structure_selection.models import Grain

_TERMINAL = frozenset({RecordReviewStatus.rejected, RecordReviewStatus.deprecated})


@dataclass(frozen=True)
class Population:
    """The compared conformer groups and the determinations the assessment is asked about.

    :param group_refs: Every compared group the read profile shows, sorted by ref.
    :param excluded_groups: Compared groups the group's own review state excludes, with the reason code: below the
        caller's floor, or a terminal status (rejected, deprecated). They stay members, reported absent, and none of
        their determinations is assessed.
    :param floor_excluded_refs: Compared groups that have a determination the caller's own floor holds out (its
        observation, or the entry, is below the floor). It is not assessed; if the group has no other value, its
        absence says so rather than claiming there is no determination.
    :param terminal_excluded_refs: The same for a determination whose observation, or the entry, is rejected or
        deprecated (terminal, not a floor), for groups not already in ``floor_excluded_refs``.
    :param determination_refs: The ``sdet_`` refs to assess: basin determinations of a compared, in-floor group
        whose observation the profile shows and the floor admits. Sorted.
    """

    group_refs: tuple[str, ...]
    excluded_groups: dict[str, str]
    floor_excluded_refs: frozenset[str]
    terminal_excluded_refs: frozenset[str]
    determination_refs: tuple[str, ...]


def profile_statuses() -> frozenset[RecordReviewStatus]:
    """What the read profile itself shows (under ``curated``: ``approved`` and nothing below)."""
    return frozenset(visible_statuses(min_review_status=None, include_rejected=True, include_deprecated=True))


def effective_statuses(request: RelativeEnergyRequest) -> frozenset[RecordReviewStatus]:
    """What the request may use: the profile, the caller's floor, and no terminal status."""
    return frozenset(
        visible_statuses(min_review_status=request.min_review_status, include_rejected=False, include_deprecated=False)
    )


def _unknown(label: str, ref: str) -> NotFoundError:
    # One answer for a ref that names nothing, names another entry's group, or names one the profile hides.
    return not_found(label, ref=ref, code="handle_not_found")


def scan_population(session: Session, *, entry_id: int, request: RelativeEnergyRequest) -> Population:
    """The compared groups and the determinations to assess, with the profile applied before anything else is read.

    :raises NotFoundError: the entry, the reference or a named member is unknown or hidden (the cases are not told
        apart, and a group of another entry is the same answer here; the route reports ownership separately).
    :raises CodedValueError: ``relative_conformer_energy_population_too_large``; nothing is assessed.
    """
    profile = profile_statuses()
    effective = effective_statuses(request)
    assert_subject_visible(session, Grain.conformer, entry_id, profile=profile)

    rows = session.execute(
        select(ConformerGroup.id, ConformerGroup.public_ref)
        .where(ConformerGroup.species_entry_id == entry_id)
        .order_by(ConformerGroup.id)
    ).all()
    badges = fetch_review_badges(
        session, record_type=SubmissionRecordType.conformer_group, record_ids=[r.id for r in rows]
    )
    shown = {r.id: r.public_ref for r in rows if badges[r.id].status in profile}
    wanted = request.wanted_refs
    if wanted is not None:
        missing = sorted(wanted - set(shown.values()))
        if missing:
            raise _unknown("conformer_group", missing[0])
        shown = {i: ref for i, ref in shown.items() if ref in wanted}
    elif request.reference_conformer_ref not in set(shown.values()):
        raise _unknown("conformer_group", request.reference_conformer_ref)
    check_members(len(shown))

    excluded = {
        ref: (GROUP_TERMINAL_REVIEW_STATUS if badges[i].status in _TERMINAL else GROUP_BELOW_REVIEW_FLOOR)
        for i, ref in shown.items()
        if badges[i].status not in effective
    }
    in_floor = {i for i, ref in shown.items() if ref not in excluded}
    entry_status = fetch_review_badges(session, record_type=SubmissionRecordType.species_entry, record_ids=[entry_id])[
        entry_id
    ].status
    entry_in_floor = entry_status in effective

    det_rows = session.execute(
        select(StructureDetermination.id, StructureDetermination.public_ref, StructureDetermination.conformer_observation_id)
        .where(
            StructureDetermination.species_entry_id == entry_id,
            StructureDetermination.target_kind == StructureDeterminationTargetKind.conformer_basin,
            StructureDetermination.conformer_observation_id.is_not(None),
        )
        .order_by(StructureDetermination.id)
    ).all()
    observation_ids = {r.conformer_observation_id for r in det_rows}
    group_of = (
        dict(
            session.execute(
                select(ConformerObservation.id, ConformerObservation.conformer_group_id).where(
                    ConformerObservation.id.in_(observation_ids)
                )
            ).tuples().all()
        )
        if observation_ids
        else {}
    )
    observation_badges = fetch_review_badges(
        session, record_type=SubmissionRecordType.conformer_observation, record_ids=observation_ids
    )
    group_ref_of = shown
    determinations: list[str] = []
    floor_held: set[str] = set()
    terminal_held: set[str] = set()
    for r in det_rows:
        group_id = group_of.get(r.conformer_observation_id)
        if group_id not in in_floor:
            continue
        status = observation_badges[r.conformer_observation_id].status
        if status not in profile:
            continue
        if entry_in_floor and status in effective:
            determinations.append(r.public_ref)
        elif status in _TERMINAL or entry_status in _TERMINAL:
            # Rejected or deprecated is terminal, which is not a floor: the caller may have set none.
            terminal_held.add(group_ref_of[group_id])
        else:
            floor_held.add(group_ref_of[group_id])
    return Population(
        group_refs=tuple(sorted(shown.values())),
        excluded_groups=excluded,
        floor_excluded_refs=frozenset(floor_held),
        terminal_excluded_refs=frozenset(terminal_held - floor_held),
        determination_refs=tuple(sorted(determinations)),
    )
