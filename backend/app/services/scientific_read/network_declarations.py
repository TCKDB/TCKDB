"""Project a network solve's declarations and its fits' determinations onto the read schemas.

A stored declaration is an attributed claim, served as stored. One that no longer parses under
its own model (a version this server dropped, a hand-edited row) is not served and not guessed at:
the field reads ``null`` and ``declaration_unreadable`` says so, so an unreadable claim is never
mistaken for "not stated".
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session
from tckdb_schemas.network_declarations import (
    NetworkObservableDeclaration,
    NetworkProtocolDeclaration,
    NetworkRepresentationDeclaration,
    NetworkValidationDeclaration,
    StoredNetworkTargetDeclaration,
)

from app.db.models.common import SubmissionRecordType
from app.db.models.network_pdep import (
    NetworkChannel,
    NetworkKinetics,
    NetworkKineticsDetermination,
    NetworkSolve,
)
from app.schemas.reads.scientific_network import (
    NetworkDeterminationRead,
    NetworkValidationEntryRead,
    NetworkValidationRead,
)
from app.services.scientific_read.common import evidence_visible_statuses, fetch_review_badges, visible_statuses


def _parse(model: type[BaseModel], raw: Any) -> tuple[Any, bool]:
    """``(value, unreadable)``: ``(None, False)`` for a NULL column, ``(None, True)`` for an unparseable one."""
    if raw is None:
        return None, False
    try:
        return model.model_validate(raw), False
    except ValidationError:
        return None, True


def solve_ref_is_visible(session: Session, ref: str, *, as_evidence: bool = False) -> bool:
    """A solve exists and the read profile lets the caller know it does. A hidden one reads as absent.

    ``as_evidence`` is the READ side of a returned solve's validation entry (ADR 0023): the reference solve is evidence the
    entry rests on, so under ``curated`` it is known unless it is rejected or deprecated, however unreviewed. The upload
    check and the selection loader keep the default (the record floor); they move with the loaders' own populations (step 5).
    """
    solve_id = session.scalar(select(NetworkSolve.id).where(NetworkSolve.public_ref == ref))
    if solve_id is None:
        return False
    badge = fetch_review_badges(session, record_type=SubmissionRecordType.network_solve, record_ids=[solve_id])[solve_id]
    if as_evidence:
        return badge.status in evidence_visible_statuses()
    return badge.status in visible_statuses(min_review_status=None, include_rejected=True, include_deprecated=True)


def _served_validation(
    session: Session, validation: NetworkValidationDeclaration | None
) -> NetworkValidationRead | None:
    if validation is None:
        return None
    entries = []
    for entry in validation.entries:
        body = entry.model_dump(mode="json")
        ref = body["reference_solve_ref"]
        if ref is not None and not solve_ref_is_visible(session, ref, as_evidence=True):
            # Nulled like any other ref to a withheld record (rule 2 / D1) and nothing more: upload refuses a reference to
            # a solve its depositor cannot see, so ``reference_withheld`` is false on every default-profile read and a
            # ``true`` here would be the one signal that a hidden solve exists.
            body["reference_solve_ref"] = None
        entries.append(NetworkValidationEntryRead(**body, reference_withheld=False))
    return NetworkValidationRead(version=validation.version, entries=entries)


def solve_declarations(session: Session, solve: NetworkSolve) -> dict[str, Any]:
    """The ``target``, ``protocol`` and ``validation`` fields of a solve's core block.

    A validation entry's reference to a solve the read profile hides is withheld, so the served declaration
    cannot be used to learn that a hidden solve exists.
    """
    target, bad_target = _parse(StoredNetworkTargetDeclaration, solve.target_declaration)
    protocol, bad_protocol = _parse(NetworkProtocolDeclaration, solve.protocol_declaration)
    validation, bad_validation = _parse(NetworkValidationDeclaration, solve.validation_declaration)
    return {
        "target": target,
        "protocol": protocol,
        "validation": _served_validation(session, validation),
        "declarations_unreadable": bad_target or bad_protocol or bad_validation,
    }


def determination_read(row: NetworkKineticsDetermination, channel_key: str) -> NetworkDeterminationRead | None:
    """A determination as served; ``None`` if its observable no longer parses."""
    observable, unreadable = _parse(NetworkObservableDeclaration, row.observable_declaration)
    if unreadable or observable is None:
        return None
    return NetworkDeterminationRead(
        determination_ref=row.public_ref,
        determination_key=row.determination_key,
        channel_key=channel_key,
        observable=observable,
    )


def solve_determinations(session: Session, solve_id: int) -> list[NetworkDeterminationRead]:
    """Every determination of a solve, in creation order, with its channel's key."""
    rows = session.execute(
        select(NetworkKineticsDetermination, NetworkChannel.channel_key)
        .join(NetworkChannel, NetworkChannel.id == NetworkKineticsDetermination.channel_id)
        .where(NetworkKineticsDetermination.solve_id == solve_id)
        .order_by(NetworkKineticsDetermination.id)
    ).all()
    out = []
    for row, channel_key in rows:
        read = determination_read(row, channel_key)
        if read is not None:
            out.append(read)
    return out


def fit_declarations(session: Session, nk: NetworkKinetics) -> dict[str, Any]:
    """The ``determination``, ``representation_role`` and ``representation`` fields of a fit's core block."""
    representation, bad_representation = _parse(NetworkRepresentationDeclaration, nk.representation_declaration)
    determination = None
    bad_determination = False
    if nk.determination_id is not None:
        row = session.get(NetworkKineticsDetermination, nk.determination_id)
        channel_key = (
            session.scalar(select(NetworkChannel.channel_key).where(NetworkChannel.id == row.channel_id))
            if row is not None
            else None
        )
        determination = determination_read(row, channel_key) if row is not None and channel_key is not None else None
        bad_determination = determination is None
    return {
        "determination": determination,
        "representation_role": nk.representation_role,
        "representation": representation,
        "declaration_unreadable": bad_representation or bad_determination,
    }
