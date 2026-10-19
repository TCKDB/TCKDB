"""The curator write path for structure-evidence findings.

Who may adjudicate, and what a finding means
--------------------------------------------
A finding is a curator's recorded judgment that a geometry, a calculation or a structure determination is the wrong
identity, state or path, that a role it fills is invalid, or that its characterization contradicts itself. Only a
curator or an admin has standing to record one (the route's role gate), so every finding written here carries
``authority = authorized_adjudication``. A depositor's own assertion about their record, and a challenge by a third
party, are different questions with their own policy; neither is representable here and neither is written.

A finding is an event, not a state. Rows are never edited or deleted (the database refuses both). A mistaken or
withdrawn finding is corrected by a later *adjudication* that names it: the adjudication settles the earlier finding,
which then stops being in force but stays on record.

What this module enforces, beyond the table's own checks
--------------------------------------------------------
* the subject ref's kind matches the declared scope, and the subject exists;
* a role is given exactly for a ``role_invalidation``;
* the rationale is not blank;
* a cited paper carries a DOI or an ISBN (the literature policy: a title alone does not identify a source);
* a correction is an adjudication. It names an earlier finding about the *same subject* that is still in force (any
  ruling in force, including an earlier correction) and states the replacement verdict: ``does_not_invalidate``,
  ``unresolved`` or ``invalidates``. Selections read the newest ruling in force of each chain (a continued role invalidation or contradictory
  characterization keeps its role and scope), so one append changes the effect with no window in between;
* an ordinary finding that rules ``does_not_invalidate`` and names nothing is refused: it would change nothing, and a
  curator who wants to withdraw a ruling does so with an adjudication naming it.

Row ids never reach a refusal: every message and context names public refs; ids are logged.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.error_contract import CodedValueError
from app.api.errors import NotFoundError, not_found
from app.db.models.app_user import AppUser
from app.db.models.calculation import Calculation
from app.db.models.common import (
    StructureFindingAuthority,
    StructureFindingKind,
    StructureFindingScope,
    StructureFindingVerdict,
    StructureSourceRole,
)
from app.db.models.geometry import Geometry
from app.db.models.literature import Literature
from app.db.models.structure_determination import StructureDetermination, StructureEvidenceFinding
from app.services.public_refs import PREFIXES
from app.services.scientific_read.handles import is_ref_handle
from app.services.structure_selection.models import SUPPORTED_FINDING_VERSIONS

logger = logging.getLogger(__name__)

#: scope -> (ORM class of the subject, the subject column on the finding)
_SUBJECTS: dict[StructureFindingScope, tuple[Any, str]] = {
    StructureFindingScope.geometry: (Geometry, "subject_geometry_id"),
    StructureFindingScope.calculation: (Calculation, "subject_calculation_id"),
    StructureFindingScope.determination: (StructureDetermination, "subject_determination_id"),
}


def _prefix(ref: str) -> str:
    return ref.split("_", 1)[0] if "_" in ref else ""


def _subject_column(finding: StructureEvidenceFinding) -> tuple[StructureFindingScope, int]:
    for scope, (_model, column) in _SUBJECTS.items():
        value = getattr(finding, column)
        if value is not None:
            return scope, value
    raise AssertionError("a finding names exactly one subject")  # pragma: no cover - a CHECK constraint guarantees it


def _resolve_subject(session: Session, scope: StructureFindingScope, subject_ref: str) -> int:
    """The row id of the subject a ref names, refusing a ref of another kind or an unknown one."""
    model, _column = _SUBJECTS[scope]
    expected = PREFIXES[model.__name__]
    if not is_ref_handle(subject_ref) or _prefix(subject_ref) != expected:
        raise CodedValueError(
            "structure_finding_scope_subject_mismatch",
            f"a {scope.value}-scope finding names a subject with the prefix {expected!r}",
            context={"scope": scope.value, "expected_prefix": expected, "supplied_prefix": _prefix(subject_ref)},
            message_prefix=False,
        )
    row_id = session.scalar(select(model.id).where(model.public_ref == subject_ref))
    if row_id is None:
        logger.info("structure finding subject not found: scope=%s ref=%s", scope.value, subject_ref)
        raise not_found(scope.value, ref=subject_ref, code="structure_finding_subject_not_found")
    return row_id


def _resolve_reference(session: Session, model: Any, ref: str, *, field: str) -> int:
    expected = PREFIXES[model.__name__]
    if not is_ref_handle(ref) or _prefix(ref) != expected:
        raise CodedValueError(
            "structure_finding_reference_invalid",
            f"{field} must be a public ref with the prefix {expected!r}",
            context={"field": field, "expected_prefix": expected},
            message_prefix=False,
        )
    row_id = session.scalar(select(model.id).where(model.public_ref == ref))
    if row_id is None:
        logger.info("structure finding reference not found: field=%s ref=%s", field, ref)
        raise NotFoundError(
            f"{field} names no record ({ref!r})",
            code="structure_finding_reference_not_found",
            context={"field": field, "ref": ref},
        )
    return row_id


def append_structure_evidence_finding(
    session: Session,
    *,
    actor: AppUser,
    scope: StructureFindingScope,
    subject_ref: str,
    kind: StructureFindingKind,
    verdict: StructureFindingVerdict,
    rationale: str,
    role: StructureSourceRole | None = None,
    source_calculation_ref: str | None = None,
    literature_ref: str | None = None,
    supersedes_finding_ref: str | None = None,
) -> StructureEvidenceFinding:
    """Append one authorized adjudication. Never commits; the caller owns the transaction.

    The caller must already have established that ``actor`` is a curator or an admin; this function records the
    authority that follows from that and does not re-decide it.
    """
    rationale = rationale.strip()
    if not rationale:
        raise CodedValueError(
            "structure_finding_rationale_required",
            "a finding needs a rationale: say why the evidence is or is not invalidated",
            message_prefix=False,
        )
    if (kind is StructureFindingKind.role_invalidation) != (role is not None):
        raise CodedValueError(
            "structure_finding_role_mismatch",
            "a role is named exactly for a role_invalidation finding",
            context={"kind": kind.value, "role_supplied": role is not None},
            message_prefix=False,
        )
    subject_id = _resolve_subject(session, scope, subject_ref)

    if (kind is StructureFindingKind.adjudication) and supersedes_finding_ref is None:
        raise CodedValueError(
            "structure_finding_adjudication_needs_target",
            "an adjudication corrects an earlier finding: name it in supersedes_finding_ref",
            message_prefix=False,
        )
    if supersedes_finding_ref is not None and kind is not StructureFindingKind.adjudication:
        raise CodedValueError(
            "structure_finding_correction_needs_adjudication",
            "only an adjudication names an earlier finding it corrects",
            context={"kind": kind.value},
            message_prefix=False,
        )

    if (
        supersedes_finding_ref is None
        and kind is not StructureFindingKind.adjudication
        and verdict is StructureFindingVerdict.does_not_invalidate
    ):
        raise CodedValueError(
            "structure_finding_dismissal_needs_target",
            "a does_not_invalidate ruling withdraws an earlier finding: record it as an adjudication naming that finding",
            context={"kind": kind.value},
            message_prefix=False,
        )

    source_calculation_id = (
        _resolve_reference(session, Calculation, source_calculation_ref, field="source_calculation_ref")
        if source_calculation_ref is not None
        else None
    )
    literature_id = None
    if literature_ref is not None:
        literature_id = _resolve_reference(session, Literature, literature_ref, field="literature_ref")
        cited = session.get(Literature, literature_id)
        if cited is None or not ((cited.doi or "").strip() or (cited.isbn or "").strip()):
            raise CodedValueError(
                "structure_finding_literature_unidentified",
                "a cited paper must carry a DOI or an ISBN; a title alone does not identify a source",
                context={"field": "literature_ref", "ref": literature_ref},
                message_prefix=False,
            )

    supersedes_id = None
    if supersedes_finding_ref is not None:
        supersedes_id = _resolve_reference(
            session, StructureEvidenceFinding, supersedes_finding_ref, field="supersedes_finding_ref"
        )
        # Serialise two curators correcting the same finding: the second sees it already settled.
        target = session.scalar(
            select(StructureEvidenceFinding).where(StructureEvidenceFinding.id == supersedes_id).with_for_update()
        )
        assert target is not None
        if _subject_column(target) != (scope, subject_id):
            raise CodedValueError(
                "structure_finding_correction_subject_mismatch",
                "a correction names an earlier finding about the same subject",
                context={"supersedes_finding_ref": supersedes_finding_ref, "subject_ref": subject_ref},
                message_prefix=False,
            )
        settled = session.scalar(
            select(StructureEvidenceFinding.public_ref).where(
                StructureEvidenceFinding.supersedes_finding_id == supersedes_id
            )
        )
        if settled is not None:
            raise CodedValueError(
                "structure_finding_already_settled",
                "this finding has already been corrected by an adjudication",
                context={"supersedes_finding_ref": supersedes_finding_ref, "settled_by_finding_ref": settled},
                message_prefix=False,
            )

    _model, column = _SUBJECTS[scope]
    finding = StructureEvidenceFinding(
        kind=kind,
        scope=scope,
        role=role,
        verdict=verdict,
        authority=StructureFindingAuthority.authorized_adjudication,
        rationale=rationale,
        semantic_version=max(SUPPORTED_FINDING_VERSIONS),
        source_calculation_id=source_calculation_id,
        literature_id=literature_id,
        supersedes_finding_id=supersedes_id,
        created_by=actor.id,
        **{column: subject_id},
    )
    session.add(finding)
    session.flush()
    logger.info(
        "structure finding appended: finding_id=%s scope=%s subject_id=%s kind=%s verdict=%s actor_id=%s",
        finding.id,
        scope.value,
        subject_id,
        kind.value,
        verdict.value,
        actor.id,
    )
    return finding


def list_findings_for_subject(
    session: Session, *, subject_ref: str, scope: StructureFindingScope | None, offset: int, limit: int
) -> tuple[StructureFindingScope, list[dict]]:
    """Every finding about one subject, oldest first, each with whether an adjudication has corrected it."""
    if scope is None:
        by_prefix = {PREFIXES[m.__name__]: s for s, (m, _c) in _SUBJECTS.items()}
        found = by_prefix.get(_prefix(subject_ref))
        if found is None or not is_ref_handle(subject_ref):
            raise CodedValueError(
                "structure_finding_scope_subject_mismatch",
                "the subject ref names no geometry, calculation or determination",
                context={"scope": "unknown", "expected_prefix": sorted(by_prefix), "supplied_prefix": _prefix(subject_ref)},
                message_prefix=False,
            )
        scope = found
    subject_id = _resolve_subject(session, scope, subject_ref)
    _model, column = _SUBJECTS[scope]
    rows = session.scalars(
        select(StructureEvidenceFinding)
        .where(getattr(StructureEvidenceFinding, column) == subject_id)
        .order_by(StructureEvidenceFinding.id)
        .offset(offset)
        .limit(limit)
    ).all()
    return scope, present_findings(session, rows, subject_ref)


def present_findings(session: Session, rows, subject_ref: str) -> list[dict]:
    """The read form of findings about ``subject_ref``: public refs only, and whether each is still in force."""
    ids = {r.id for r in rows}
    settlers: dict[int, str] = {}
    if ids:
        for superseded_id, settler_ref in session.execute(
            select(StructureEvidenceFinding.supersedes_finding_id, StructureEvidenceFinding.public_ref).where(
                StructureEvidenceFinding.supersedes_finding_id.in_(ids)
            )
        ):
            if superseded_id is not None:
                settlers[superseded_id] = settler_ref
    by_id = {
        r.id: r.public_ref
        for r in session.scalars(
            select(StructureEvidenceFinding).where(
                StructureEvidenceFinding.id.in_({r.supersedes_finding_id for r in rows if r.supersedes_finding_id})
            )
        )
    }
    calc_refs = dict(
        session.execute(
            select(Calculation.id, Calculation.public_ref).where(
                Calculation.id.in_({r.source_calculation_id for r in rows if r.source_calculation_id})
            )
        ).tuples().all()
    )
    lit_refs = dict(
        session.execute(
            select(Literature.id, Literature.public_ref).where(
                Literature.id.in_({r.literature_id for r in rows if r.literature_id})
            )
        ).tuples().all()
    )
    out = [
        {
            "finding_ref": r.public_ref,
            "scope": r.scope,
            "subject_ref": subject_ref,
            "kind": r.kind,
            "role": r.role,
            "verdict": r.verdict,
            "authority": r.authority,
            "rationale": r.rationale,
            "source_calculation_ref": calc_refs.get(r.source_calculation_id),
            "literature_ref": lit_refs.get(r.literature_id),
            "supersedes_finding_ref": by_id.get(r.supersedes_finding_id),
            "settled_by_finding_ref": settlers.get(r.id),
            "in_force": r.id not in settlers,
            "created_at": r.created_at,
        }
        for r in rows
    ]
    return out
