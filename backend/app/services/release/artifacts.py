"""Deterministic, checksummable artifacts of a curated dataset release.

A release ships four NDJSON files, and shipping all four is the point:

``selected_records.ndjson``   what TCKDB recommends, each line carrying the
                              attribution (policy version, curator, rationale)
                              that makes it a recommendation rather than a
                              guess.
``candidate_records.ndjson``  every record the selection was made *from*,
                              selected or not, with its review state. A release
                              that shipped only the winners would be exactly
                              the "trust us" artifact this layer exists to
                              avoid.
``review_history.ndjson``     the human review state and event log behind every
                              record above.
``selection_ledger.ndjson``   every selection row in the release including
                              superseded and withdrawn ones, so a reader can
                              audit how the curators' view changed.

These bytes are rendered **once**, at publication, and then stored on
``release_artifact.content``; every later download returns the stored bytes.
Rendering on read was tried and is wrong: one ordinary upload for a released
species grows the candidate set, which legitimately changes the rendered file
and would make a published citation stop resolving. See
``app/services/release/manifest.py`` for the frozen-vs-live split.

This module is therefore only reached from two places: freezing a release, and
the advisory ``live_divergence`` check that reports how far the corpus has
moved since.

Determinism still matters — the divergence check compares a fresh rendering
against the published digests, and re-rendering the same database state must
give the same bytes:

* JSON is emitted with ``sort_keys=True`` and compact separators;
* every collection is ordered by a stable database key, never by set or dict
  iteration order;
* ``\\n`` line endings, UTF-8, one JSON object per line, trailing newline.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from typing import Any, Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.app_user import AppUser
from app.db.models.common import (
    ReleaseArtifactKind,
    ReleaseSelectionAction,
    SubmissionRecordType,
)
from app.db.models.dataset_release import (
    CurationPolicy,
    DatasetRelease,
    ReleaseSelection,
)
from app.db.models.record_review import RecordReview, RecordReviewEvent
from app.services.release.records import (
    CANDIDATE_SOURCES,
    calculation_provenance,
    candidate_ids_for_subjects,
    cited_calculation_ids,
    encode_scalar,
    public_refs_for,
    serialize_records,
    subject_identities,
)

NDJSON_MEDIA_TYPE = "application/x-ndjson"

ARTIFACT_PATHS: dict[ReleaseArtifactKind, str] = {
    ReleaseArtifactKind.selected_records: "selected_records.ndjson",
    ReleaseArtifactKind.candidate_records: "candidate_records.ndjson",
    ReleaseArtifactKind.review_history: "review_history.ndjson",
    ReleaseArtifactKind.selection_ledger: "selection_ledger.ndjson",
}


@dataclass(frozen=True)
class RenderedArtifact:
    """One release file plus the integrity facts recorded about it."""

    kind: ReleaseArtifactKind
    path: str
    media_type: str
    content: bytes
    record_count: int

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.content).hexdigest()

    @property
    def byte_count(self) -> int:
        return len(self.content)


class NonFiniteValueError(ValueError):
    """A stored double is NaN or ±Inf and cannot be published.

    ``allow_nan=False`` is deliberate: JSON has no NaN, and emitting the
    non-standard ``NaN``/``Infinity`` tokens would produce a citable file that
    a conforming parser rejects. But the bare ``ValueError`` json raises says
    only "Out of range float values are not JSON compliant", which surfaces as
    an undiagnosable 500 at publish time. This names the offending path so a
    curator can find the record.
    """


def canonical_json(payload: Any) -> str:
    """Byte-stable JSON. Used for both artifact lines and the manifest.

    :raises NonFiniteValueError: the payload contains NaN or ±Inf, with the
        JSON path to the offending value.
    """
    try:
        return json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
    except ValueError as exc:
        location = _find_non_finite(payload)
        raise NonFiniteValueError(
            "non_finite_value: a stored numeric value is NaN or infinite and "
            "cannot be serialized into a citable release"
            + (f" (at {location})" if location else "")
            + ". Correct or supersede the record, then publish."
        ) from exc


def _find_non_finite(node: Any, path: str = "$") -> str | None:
    """Locate the first NaN/Inf in a payload, for a diagnosable error."""
    if isinstance(node, float) and not math.isfinite(node):
        return path
    if isinstance(node, dict):
        for key, value in node.items():
            found = _find_non_finite(value, f"{path}.{key}")
            if found is not None:
                return found
    elif isinstance(node, list):
        for index, value in enumerate(node):
            found = _find_non_finite(value, f"{path}[{index}]")
            if found is not None:
                return found
    return None


def _ndjson(lines: Iterable[dict[str, Any]]) -> tuple[bytes, int]:
    rendered = [canonical_json(line) for line in lines]
    if not rendered:
        return b"", 0
    return ("\n".join(rendered) + "\n").encode("utf-8"), len(rendered)


# ---------------------------------------------------------------------------
# Selection state
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SelectionState:
    """The selections of one release, split into the ledger and what stands."""

    all_selections: list[ReleaseSelection]
    active: list[ReleaseSelection]


def load_selection_state(session: Session, release: DatasetRelease) -> SelectionState:
    """Read a release's selection rows and resolve which ones still stand.

    A selection stands if nothing supersedes it and it is not itself a
    ``withdraw``. Because supersession is append-only and
    ``supersedes_selection_id`` is UNIQUE, this is a straightforward
    head-of-chain computation rather than a mutable "is_current" flag — which
    is the whole reason the table has no such flag.
    """
    rows = list(
        session.scalars(
            select(ReleaseSelection)
            .where(ReleaseSelection.dataset_release_id == release.id)
            .order_by(ReleaseSelection.id)
        )
    )
    superseded_ids = {
        row.supersedes_selection_id
        for row in rows
        if row.supersedes_selection_id is not None
    }
    active = [
        row
        for row in rows
        if row.id not in superseded_ids
        and row.action is not ReleaseSelectionAction.withdraw
    ]
    return SelectionState(all_selections=rows, active=active)


def _actor_labels(session: Session, user_ids: set[int]) -> dict[int, dict[str, Any]]:
    """Attribution block per curator: who, by name and ORCID, not by row id."""
    if not user_ids:
        return {}
    rows = session.scalars(select(AppUser).where(AppUser.id.in_(user_ids))).all()
    return {
        user.id: {
            "username": user.username,
            "full_name": user.full_name,
            "orcid": user.orcid,
            "affiliation": user.affiliation,
        }
        for user in rows
    }


def _policy_labels(
    session: Session, policy_ids: set[int]
) -> dict[int, dict[str, Any]]:
    if not policy_ids:
        return {}
    rows = session.scalars(
        select(CurationPolicy).where(CurationPolicy.id.in_(policy_ids))
    ).all()
    return {
        policy.id: {
            "curation_policy_ref": policy.public_ref,
            "name": policy.name,
            "version": policy.version,
        }
        for policy in rows
    }


def _subject_refs(
    session: Session, pairs: set[tuple[SubmissionRecordType, int]]
) -> dict[tuple[SubmissionRecordType, int], str | None]:
    """Resolve subject ids to public refs, grouped by subject type."""
    out: dict[tuple[SubmissionRecordType, int], str | None] = {}
    by_type: dict[SubmissionRecordType, list[int]] = {}
    for subject_type, subject_id in pairs:
        by_type.setdefault(subject_type, []).append(subject_id)
    for subject_type, ids in by_type.items():
        refs = public_refs_for(session, record_type=subject_type, record_ids=sorted(ids))
        for subject_id in ids:
            out[(subject_type, subject_id)] = refs.get(subject_id)
    return out


def _review_state(
    session: Session, pairs: set[tuple[SubmissionRecordType, int]]
) -> dict[tuple[SubmissionRecordType, int], RecordReview | None]:
    if not pairs:
        return {}
    by_type: dict[SubmissionRecordType, list[int]] = {}
    for record_type, record_id in pairs:
        by_type.setdefault(record_type, []).append(record_id)
    out: dict[tuple[SubmissionRecordType, int], RecordReview | None] = dict.fromkeys(pairs)
    for record_type, ids in by_type.items():
        rows = session.scalars(
            select(RecordReview).where(
                RecordReview.record_type == record_type,
                RecordReview.record_id.in_(ids),
            )
        ).all()
        for row in rows:
            out[(record_type, row.record_id)] = row
    return out


# ---------------------------------------------------------------------------
# Artifact rendering
# ---------------------------------------------------------------------------


def render_artifacts(
    session: Session, release: DatasetRelease
) -> list[RenderedArtifact]:
    """Render all four release artifacts, in a stable order."""
    state = load_selection_state(session, release)

    policies = _policy_labels(
        session, {row.curation_policy_id for row in state.all_selections}
    )
    actors = _actor_labels(session, {row.selected_by for row in state.all_selections})

    selected_pairs = {(row.record_type, row.record_id) for row in state.active}

    # Subjects are collected from *every* selection row, not only the standing
    # ones. A subject whose selection was withdrawn has no active row, and
    # scoping to ``state.active`` would render its ledger entries with a null
    # subject ref — losing exactly the information a reader needs to see that
    # the release deliberately recommends nothing there.
    subject_pairs = {(row.subject_type, row.subject_id) for row in state.all_selections}
    ledger_pairs = {(row.record_type, row.record_id) for row in state.all_selections}

    # Every candidate the selection was made from, per subject. Keyed by the
    # (record_type, record_id) pair rather than by record id alone: two record
    # types can share an integer id, and silently attributing a kinetics record
    # to a species entry would be an invisible corruption of the release.
    candidate_pairs: set[tuple[SubmissionRecordType, int]] = set()
    candidates_by_subject: dict[
        tuple[SubmissionRecordType, int], tuple[SubmissionRecordType, int]
    ] = {}
    for record_type in sorted({rt for rt, _ in ledger_pairs}, key=lambda t: t.value):
        _model, _fk, subject_type = CANDIDATE_SOURCES[record_type]
        subject_ids = sorted(
            sid for stype, sid in subject_pairs if stype == subject_type
        )
        grouped = candidate_ids_for_subjects(
            session, record_type=record_type, subject_ids=subject_ids
        )
        for subject_id, ids in grouped.items():
            for record_id in ids:
                candidates_by_subject[(record_type, record_id)] = (
                    subject_type,
                    subject_id,
                )
            candidate_pairs |= {(record_type, rid) for rid in ids}

    # Every record any selection ever named must appear, even if its subject
    # linkage is unusual — never let the "recommended" file or the ledger
    # reference something the release does not ship.
    candidate_pairs |= selected_pairs | ledger_pairs

    all_pairs = candidate_pairs
    refs = _all_refs(session, all_pairs)
    payloads = _all_payloads(session, all_pairs)
    reviews = _review_state(session, all_pairs)
    subject_ref_map = _subject_refs(session, subject_pairs)
    # Chemical identity per subject, and level-of-theory/software per cited
    # calculation. Without these a deposited release states numbers against
    # opaque handles: not interpretable or checkable offline, and less science
    # than the unauthenticated read API already publishes.
    subject_identity_map = _subject_identities(session, subject_pairs)
    provenance_map = _record_provenance(session, all_pairs)

    artifacts = [
        _render_selected(
            state,
            refs,
            payloads,
            reviews,
            subject_ref_map,
            subject_identity_map,
            provenance_map,
            policies,
            actors,
        ),
        _render_candidates(
            candidates_by_subject,
            selected_pairs,
            refs,
            payloads,
            reviews,
            subject_ref_map,
            subject_identity_map,
            provenance_map,
        ),
        _render_review_history(session, all_pairs, refs, reviews),
        _render_ledger(state, refs, subject_ref_map, policies, actors),
    ]
    return artifacts


def _subject_identities(
    session: Session, subject_pairs: set[tuple[SubmissionRecordType, int]]
) -> dict[tuple[SubmissionRecordType, int], dict[str, Any]]:
    """Chemical identity per subject, batched by subject type."""
    by_type: dict[SubmissionRecordType, list[int]] = {}
    for subject_type, subject_id in subject_pairs:
        by_type.setdefault(subject_type, []).append(subject_id)
    out: dict[tuple[SubmissionRecordType, int], dict[str, Any]] = {}
    for subject_type, ids in by_type.items():
        found = subject_identities(
            session, subject_type=subject_type, subject_ids=sorted(ids)
        )
        for subject_id, identity in found.items():
            out[(subject_type, subject_id)] = identity
    return out


def _record_provenance(
    session: Session, pairs: set[tuple[SubmissionRecordType, int]]
) -> dict[tuple[SubmissionRecordType, int], list[dict[str, Any]]]:
    """Level of theory + software for every calculation each record cites."""
    by_type: dict[SubmissionRecordType, list[int]] = {}
    for record_type, record_id in pairs:
        by_type.setdefault(record_type, []).append(record_id)

    out: dict[tuple[SubmissionRecordType, int], list[dict[str, Any]]] = {}
    for record_type, ids in by_type.items():
        cited = cited_calculation_ids(
            session, record_type=record_type, record_ids=sorted(ids)
        )
        every_id = sorted({cid for values in cited.values() for cid in values})
        details = calculation_provenance(session, every_id)
        for record_id in ids:
            out[(record_type, record_id)] = [
                details[cid] for cid in cited.get(record_id, []) if cid in details
            ]
    return out


def _all_refs(
    session: Session, pairs: set[tuple[SubmissionRecordType, int]]
) -> dict[tuple[SubmissionRecordType, int], str | None]:
    out: dict[tuple[SubmissionRecordType, int], str | None] = {}
    by_type: dict[SubmissionRecordType, list[int]] = {}
    for record_type, record_id in pairs:
        by_type.setdefault(record_type, []).append(record_id)
    for record_type, ids in by_type.items():
        found = public_refs_for(
            session, record_type=record_type, record_ids=sorted(ids)
        )
        for record_id in ids:
            out[(record_type, record_id)] = found.get(record_id)
    return out


def _all_payloads(
    session: Session, pairs: set[tuple[SubmissionRecordType, int]]
) -> dict[tuple[SubmissionRecordType, int], dict[str, Any]]:
    out: dict[tuple[SubmissionRecordType, int], dict[str, Any]] = {}
    by_type: dict[SubmissionRecordType, list[int]] = {}
    for record_type, record_id in pairs:
        by_type.setdefault(record_type, []).append(record_id)
    for record_type, ids in by_type.items():
        rendered = serialize_records(
            session, record_type=record_type, record_ids=sorted(ids)
        )
        for record_id, payload in rendered.items():
            out[(record_type, record_id)] = payload
    return out


def _review_block(review: RecordReview | None) -> dict[str, Any]:
    if review is None:
        return {"status": "not_reviewed", "reviewed_at": None, "first_approved_at": None}
    return {
        "status": review.status.value,
        "reviewed_at": encode_scalar(review.reviewed_at),
        "first_approved_at": encode_scalar(review.first_approved_at),
    }


def _render_selected(
    state: SelectionState,
    refs: dict[tuple[SubmissionRecordType, int], str | None],
    payloads: dict[tuple[SubmissionRecordType, int], dict[str, Any]],
    reviews: dict[tuple[SubmissionRecordType, int], RecordReview | None],
    subject_refs: dict[tuple[SubmissionRecordType, int], str | None],
    subject_identity: dict[tuple[SubmissionRecordType, int], dict[str, Any]],
    provenance: dict[tuple[SubmissionRecordType, int], list[dict[str, Any]]],
    policies: dict[int, dict[str, Any]],
    actors: dict[int, dict[str, Any]],
) -> RenderedArtifact:
    lines = []
    for row in sorted(state.active, key=lambda r: r.public_ref):
        key = (row.record_type, row.record_id)
        subject_key = (row.subject_type, row.subject_id)
        lines.append(
            {
                "record_type": row.record_type.value,
                "record_ref": refs.get(key),
                "subject_type": row.subject_type.value,
                "subject_ref": subject_refs.get(subject_key),
                # SMILES / InChIKey / charge / multiplicity, or reactant and
                # product SMILES: what makes the number mean something.
                "subject": subject_identity.get(subject_key, {}),
                # Level of theory and software for each cited calculation.
                "provenance": {"calculations": provenance.get(key, [])},
                "selection": {
                    "selection_ref": row.public_ref,
                    "action": row.action.value,
                    "rationale": row.rationale,
                    "selected_at": encode_scalar(row.created_at),
                    "curator": actors.get(row.selected_by),
                    "curation_policy": policies.get(row.curation_policy_id),
                },
                "review": _review_block(reviews.get(key)),
                "record": payloads.get(key, {}),
            }
        )
    content, count = _ndjson(lines)
    return RenderedArtifact(
        kind=ReleaseArtifactKind.selected_records,
        path=ARTIFACT_PATHS[ReleaseArtifactKind.selected_records],
        media_type=NDJSON_MEDIA_TYPE,
        content=content,
        record_count=count,
    )


def _render_candidates(
    candidates_by_subject: dict[
        tuple[SubmissionRecordType, int], tuple[SubmissionRecordType, int]
    ],
    selected_pairs: set[tuple[SubmissionRecordType, int]],
    refs: dict[tuple[SubmissionRecordType, int], str | None],
    payloads: dict[tuple[SubmissionRecordType, int], dict[str, Any]],
    reviews: dict[tuple[SubmissionRecordType, int], RecordReview | None],
    subject_refs: dict[tuple[SubmissionRecordType, int], str | None],
    subject_identity: dict[tuple[SubmissionRecordType, int], dict[str, Any]],
    provenance: dict[tuple[SubmissionRecordType, int], list[dict[str, Any]]],
) -> RenderedArtifact:
    lines = []
    for key in sorted(payloads, key=lambda k: (k[0].value, refs.get(k) or "", k[1])):
        subject_key = candidates_by_subject.get(key)
        lines.append(
            {
                "record_type": key[0].value,
                "record_ref": refs.get(key),
                "subject_type": subject_key[0].value if subject_key else None,
                "subject_ref": subject_refs.get(subject_key) if subject_key else None,
                # A rejected candidate is only useful if a reader can tell what
                # it is and how it was produced, same as the selected one.
                "subject": subject_identity.get(subject_key, {}) if subject_key else {},
                "provenance": {"calculations": provenance.get(key, [])},
                "selected_in_release": key in selected_pairs,
                "review": _review_block(reviews.get(key)),
                "record": payloads.get(key, {}),
            }
        )
    content, count = _ndjson(lines)
    return RenderedArtifact(
        kind=ReleaseArtifactKind.candidate_records,
        path=ARTIFACT_PATHS[ReleaseArtifactKind.candidate_records],
        media_type=NDJSON_MEDIA_TYPE,
        content=content,
        record_count=count,
    )


def _render_review_history(
    session: Session,
    pairs: set[tuple[SubmissionRecordType, int]],
    refs: dict[tuple[SubmissionRecordType, int], str | None],
    reviews: dict[tuple[SubmissionRecordType, int], RecordReview | None],
) -> RenderedArtifact:
    review_ids = [r.id for r in reviews.values() if r is not None]
    events_by_review: dict[int, list[RecordReviewEvent]] = {}
    if review_ids:
        rows = session.scalars(
            select(RecordReviewEvent)
            .where(RecordReviewEvent.record_review_id.in_(review_ids))
            .order_by(RecordReviewEvent.id)
        ).all()
        for event in rows:
            events_by_review.setdefault(event.record_review_id, []).append(event)

    lines = []
    for key in sorted(pairs, key=lambda k: (k[0].value, refs.get(k) or "", k[1])):
        review = reviews.get(key)
        events = events_by_review.get(review.id, []) if review is not None else []
        lines.append(
            {
                "record_type": key[0].value,
                "record_ref": refs.get(key),
                "review": _review_block(review),
                "note": review.note if review is not None else None,
                "events": [
                    {
                        "event_kind": event.event_kind.value,
                        "from_status": (
                            event.from_status.value if event.from_status else None
                        ),
                        "to_status": event.to_status.value if event.to_status else None,
                        "reason": event.reason,
                        "created_at": encode_scalar(event.created_at),
                    }
                    for event in events
                ],
            }
        )
    content, count = _ndjson(lines)
    return RenderedArtifact(
        kind=ReleaseArtifactKind.review_history,
        path=ARTIFACT_PATHS[ReleaseArtifactKind.review_history],
        media_type=NDJSON_MEDIA_TYPE,
        content=content,
        record_count=count,
    )


def _render_ledger(
    state: SelectionState,
    refs: dict[tuple[SubmissionRecordType, int], str | None],
    subject_refs: dict[tuple[SubmissionRecordType, int], str | None],
    policies: dict[int, dict[str, Any]],
    actors: dict[int, dict[str, Any]],
) -> RenderedArtifact:
    by_id = {row.id: row for row in state.all_selections}
    active_ids = {row.id for row in state.active}
    lines = []
    for row in state.all_selections:
        superseded = by_id.get(row.supersedes_selection_id or -1)
        lines.append(
            {
                "selection_ref": row.public_ref,
                "action": row.action.value,
                "stands": row.id in active_ids,
                "record_type": row.record_type.value,
                "record_ref": refs.get((row.record_type, row.record_id)),
                "subject_type": row.subject_type.value,
                "subject_ref": subject_refs.get((row.subject_type, row.subject_id)),
                "supersedes_selection_ref": (
                    superseded.public_ref if superseded is not None else None
                ),
                "rationale": row.rationale,
                "curator": actors.get(row.selected_by),
                "curation_policy": policies.get(row.curation_policy_id),
                "created_at": encode_scalar(row.created_at),
            }
        )
    content, count = _ndjson(lines)
    return RenderedArtifact(
        kind=ReleaseArtifactKind.selection_ledger,
        path=ARTIFACT_PATHS[ReleaseArtifactKind.selection_ledger],
        media_type=NDJSON_MEDIA_TYPE,
        content=content,
        record_count=count,
    )


__all__ = [
    "ARTIFACT_PATHS",
    "NDJSON_MEDIA_TYPE",
    "NonFiniteValueError",
    "RenderedArtifact",
    "SelectionState",
    "canonical_json",
    "load_selection_state",
    "render_artifacts",
]
