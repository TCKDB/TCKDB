"""Requested eligibility: today's exclusions as named defaults a select request can lift.

Step 2 of a select call (after the caller's filters, before the policy). Each exclusion a selection applies has a stable
machine name and a request field that lifts it. This module holds the pure part, with no database and no ORM:

* :class:`Eligibility`, the resolved settings (what a manifest records and replay reads);
* :func:`judge`, which says whether one assessed candidate competes under those settings, and which defaults it passed
  only because the caller lifted them;
* :func:`echo`, the list every answer carries: name, whether applied, and by whose choice.

The read profile's review floor is not here. ``curated`` shows approved records only and hidden equals unknown equals
absent; that happens in :func:`app.services.scientific_read.common.visible_statuses` before any of this runs, and a
lifted ``include_rejected`` under it lifts nothing (the echo says ``read_profile``). An override can only lift an
exclusion within what the read profile already shows.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from pydantic import BaseModel

from app.db.models.common import RecordReviewStatus
from app.schemas.reads.scientific_selection_common import (
    ELIGIBILITY_CONFIRMED_FINDINGS,
    ELIGIBILITY_DEPRECATED,
    ELIGIBILITY_HARD_FAILED_EVIDENCE,
    ELIGIBILITY_REJECTED,
    ELIGIBILITY_REJECTED_QUALITY,
    OVERRIDE_FIELD,
    EligibilityDefaultEcho,
    EligibilitySource,
    SelectionFilterAnswer,
    SelectionFunnel,
)

#: The prefix an evidence-rubric hard failure carries in a candidate's ``blocking`` list.
EVIDENCE_HARD_FAIL_PREFIX = "evidence_hard_failed:"

_FIELDS = tuple(OVERRIDE_FIELD[n] for n in (ELIGIBILITY_REJECTED, ELIGIBILITY_DEPRECATED, ELIGIBILITY_HARD_FAILED_EVIDENCE, ELIGIBILITY_CONFIRMED_FINDINGS))


class UnreadableEligibility(ValueError):
    """A recorded eligibility block is missing or malformed."""


@dataclass(frozen=True)
class Eligibility:
    """The eligibility settings of one request: ``True`` lifts the named default."""

    include_rejected: bool = False
    include_deprecated: bool = False
    include_hard_failed_evidence: bool = False
    include_confirmed_findings: bool = False

    @classmethod
    def from_request(cls, request: BaseModel | None) -> Eligibility:
        """The settings of a request; a field the request's model does not offer is not lifted."""
        if request is None:
            return cls()
        return cls(**{f: bool(getattr(request, f, False)) for f in _FIELDS})

    def to_record(self) -> dict[str, bool]:
        """What a manifest records: every field, so a stored decision never depends on a default that later changes."""
        return {f: getattr(self, f) for f in _FIELDS}

    @classmethod
    def from_record(cls, raw: object) -> Eligibility:
        if not isinstance(raw, dict) or set(raw) != set(_FIELDS) or any(type(v) is not bool for v in raw.values()):
            raise UnreadableEligibility("the manifest has no well-formed eligibility record")
        return cls(**raw)

    def lifts(self, name: str) -> bool:
        return bool(getattr(self, OVERRIDE_FIELD[name]))


@dataclass(frozen=True)
class Verdict:
    """Whether one assessed candidate competes, and why not or why only by an override.

    :param blocked_by: Applied defaults the candidate fails (it does not compete).
    :param passed_only_by_override: Defaults the candidate fails but the caller lifted (it competes because of that).
    """

    competes: bool
    blocked_by: tuple[str, ...] = ()
    passed_only_by_override: tuple[str, ...] = ()


def blocking_default(code: str) -> str:
    """The named default a ``blocking`` code belongs to: an evidence hard failure, otherwise a confirmed finding."""
    return ELIGIBILITY_HARD_FAILED_EVIDENCE if code.startswith(EVIDENCE_HARD_FAIL_PREFIX) else ELIGIBILITY_CONFIRMED_FINDINGS


def review_default(status: RecordReviewStatus) -> str | None:
    """The named default a review status trips, or ``None`` for a status no default excludes."""
    if status is RecordReviewStatus.rejected:
        return ELIGIBILITY_REJECTED
    if status is RecordReviewStatus.deprecated:
        return ELIGIBILITY_DEPRECATED
    return None


def judge(
    *,
    applicable: bool,
    review_status: RecordReviewStatus,
    blocking: Sequence[str],
    eligibility: Eligibility,
    extra_defaults: Iterable[str] = (),
) -> Verdict:
    """Whether a candidate competes under *eligibility*.

    A candidate that is not applicable never competes: applicability is the question's answer, not a default.
    *extra_defaults* are further named defaults the candidate trips (a rejected calculation quality, say).
    A review status that is excluded by default is normally out of the population before this runs; it appears here
    when the caller lifted it, which is how ``passed_only_by_override`` learns of it.
    """
    tripped: list[str] = []
    status_default = review_default(review_status)
    if status_default is not None:
        tripped.append(status_default)
    tripped.extend(blocking_default(code) for code in blocking)
    tripped.extend(extra_defaults)
    names = tuple(dict.fromkeys(tripped))
    blocked = tuple(n for n in names if not eligibility.lifts(n))
    lifted = tuple(n for n in names if eligibility.lifts(n))
    return Verdict(competes=applicable and not blocked, blocked_by=blocked, passed_only_by_override=lifted)


def funnel(
    *,
    considered: int,
    removed_by_filters: int,
    terminal_excluded: dict[str, int],
    verdicts: Iterable[Verdict],
    unit: str = "candidates",
) -> dict:
    """The recorded funnel: what each step removed, and per default how many it kept out or admitted by override.

    Each candidate is attributed to the first step that removed it: the filters, then the review defaults, then the
    findings and evidence defaults that need the candidate assessed. *terminal_excluded* counts the rows a review default
    removed before they were loaded.
    """
    excluded: dict[str, int] = dict(terminal_excluded)
    admitted: dict[str, int] = {}
    blocked = 0
    for v in verdicts:
        for name in v.blocked_by:
            excluded[name] = excluded.get(name, 0) + 1
        for name in v.passed_only_by_override:
            admitted[name] = admitted.get(name, 0) + 1
        blocked += 1 if v.blocked_by else 0
    removed_by_eligibility = sum(terminal_excluded.values()) + blocked
    return {
        "unit": unit,
        "considered": considered,
        "removed_by_filters": removed_by_filters,
        "removed_by_eligibility": removed_by_eligibility,
        "remaining": considered - removed_by_filters - removed_by_eligibility,
        "excluded_by_default": excluded,
        "admitted_by_override": admitted,
    }


def echo(
    eligibility: Eligibility,
    *,
    profile_floor: bool,
    names: Sequence[str],
    excluded: dict[str, int],
    admitted: dict[str, int],
) -> list[EligibilityDefaultEcho]:
    """The list every select answer carries: each named default, whether applied, and by whose choice."""
    out: list[EligibilityDefaultEcho] = []
    for name in names:
        asked_to_lift = eligibility.lifts(name)
        floor_blocks = profile_floor and name in (ELIGIBILITY_REJECTED, ELIGIBILITY_DEPRECATED)
        if floor_blocks:
            applied, source = True, (EligibilitySource.read_profile if asked_to_lift else EligibilitySource.default)
        elif asked_to_lift:
            applied, source = False, EligibilitySource.request
        else:
            applied, source = True, EligibilitySource.default
        out.append(
            EligibilityDefaultEcho(
                name=name,
                override_field=OVERRIDE_FIELD[name],
                applied=applied,
                source=source,
                excluded=excluded.get(name, 0),
                admitted_by_override=admitted.get(name, 0),
            )
        )
    return out


def answer_steps(
    *,
    request_filters: dict,
    min_review_status: RecordReviewStatus | None,
    eligibility_record: object,
    funnel_record: dict,
    names: Sequence[str],
    profile_floor: bool,
) -> dict:
    """The ``filters``, ``eligibility`` and ``funnel`` fields of a select answer, built from a manifest's records.

    Every select answer states steps 1 and 2 the same way, from the same recorded numbers the downloadable manifest
    carries, so the response and the download cannot disagree.
    """
    return {
        "filters": SelectionFilterAnswer(
            applied={
                **request_filters,
                **({"min_review_status": min_review_status.value} if min_review_status is not None else {}),
            },
            removed=funnel_record["removed_by_filters"],
        ),
        "eligibility": echo(
            Eligibility.from_record(eligibility_record),
            profile_floor=profile_floor,
            names=names,
            excluded=funnel_record["excluded_by_default"],
            admitted=funnel_record["admitted_by_override"],
        ),
        "funnel": SelectionFunnel(
            **{k: funnel_record[k] for k in ("unit", "considered", "removed_by_filters", "removed_by_eligibility", "remaining")}
        ),
    }


__all__ = [
    "answer_steps",
    "ELIGIBILITY_REJECTED_QUALITY",
    "EVIDENCE_HARD_FAIL_PREFIX",
    "Eligibility",
    "UnreadableEligibility",
    "Verdict",
    "blocking_default",
    "echo",
    "funnel",
    "judge",
    "review_default",
]
