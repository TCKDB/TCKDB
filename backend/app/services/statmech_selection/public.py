"""The public face of statmech selection: reference checks, running the service, building the response.

``select_statmech`` (selection.py) decides and records; this module is the thin layer the HTTP route, and nothing
else, goes through. It does four things:

1. maps the public request onto the service's request (``StatmechSelectionRequest.service_request``): no database id is
   accepted from the caller and the engineering bounds are the server's own;
2. checks every public ref the request names (the target entry, a basin, a determination, an ensemble declaration, a
   reference model) through the ordinary path-handle resolver, so a typo is a 404 and not a silent "nothing applies",
   and a ref the read profile hides answers exactly as it does anywhere else. Explicit candidate refs are checked by
   the service against the target's own authorized population (one answer for unknown, another target's, hidden and a
   declared record's own ref);
3. runs the service in a read-only snapshot, and insists on it;
4. builds the response from the decision manifest, so the response and the downloadable manifest cannot disagree.

Visibility of ``excluded_by_review``. Records that fail the effective review floor are listed (and counted) only when
the read profile has no floor of its own (``exploratory``). Under ``curated`` a record below the floor is one the
caller could not have reached through any other curated read, so naming or counting it would be a leak. The redaction
is made *before* the manifest's checksum is computed (see ``manifest.build_manifest``), so the downloaded manifest
still verifies and replays.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session
from tckdb_schemas.fragments.version import VersionRef

from app.api.error_contract import CodedValueError
from app.api.errors import NotFoundError, not_found
from app.db.models.common import RecordReviewStatus, SubmissionRecordType
from app.db.models.species import ConformerGroup
from app.db.models.statmech import Statmech
from app.db.models.statmech_declaration import StatmechModelDeclaration
from app.db.models.structure_determination import StructureDetermination
from app.schemas.reads.scientific_common import REVIEW_FLOOR_ORDER
from app.schemas.reads.scientific_selection_common import (
    ELIGIBILITY_CONFIRMED_FINDINGS,
    ELIGIBILITY_DEPRECATED,
    ELIGIBILITY_ENTRY_STATUS,
    ELIGIBILITY_REJECTED,
)
from app.schemas.reads.scientific_statmech_selection import (
    StatmechSelectionCandidate,
    StatmechSelectionIntegrity,
    StatmechSelectionManifestDescriptor,
    StatmechSelectionPopulation,
    StatmechSelectionRequest,
    StatmechSelectionRequestEcho,
    StatmechSelectionResponse,
    StatmechSelectionReview,
    StatmechSelectionSelection,
)
from app.services.scientific_read.common import fetch_review_badges
from app.services.selection_kernel.requested import answer_steps
from app.services.statmech_selection.loader import _statuses as statuses_for
from app.services.statmech_selection.loader import assert_subject_visible, entry_visible
from app.services.statmech_selection.manifest import MANIFEST_FORMAT_NAME, MANIFEST_FORMAT_VERSION, canonical
from app.services.statmech_selection.models import StatmechRequest
from app.services.statmech_selection.selection import StatmechSelection, select_statmech
from app.services.statmech_selection.vocabulary import Outcome, OwnerKind
from app.services.structure_selection.public import (
    SNAPSHOT_OPT_OUT,
    _ref_only,
    resolve_species_entry_ref,
    resolve_transition_state_entry_ref,
)

#: Where the complete decision manifest of the same request is served.
MANIFEST_PATH = "/api/v1/scientific/statmech/select/manifest"
MANIFEST_NOTE = (
    "A new snapshot of the current data answering the same request, not a retrieval of a stored decision. Its digests "
    "are checksums, not signatures."
)


CODE_SCOPE_OWNER_MISMATCH = "statmech_selection_scope_owner_mismatch"


def resolve_target(session: Session, request: StatmechRequest) -> int:
    """The id of the species or transition-state entry the request names (404 when absent or hidden)."""
    if request.target.owner_kind is OwnerKind.species_entry:
        return resolve_species_entry_ref(session, request.target.owner_ref)
    return resolve_transition_state_entry_ref(session, request.target.owner_ref)


def _unknown(kind_label: str, ref: str) -> NotFoundError:
    """The one 404 an unknown ref and a ref the read profile hides both answer with (the ref is the caller's own)."""
    return not_found(kind_label, ref=ref, code="handle_not_found")


def _mismatch(field: str) -> CodedValueError:
    """A visible ref that belongs to another entry: a contradictory question. Names the field, never the other owner."""
    return CodedValueError(
        CODE_SCOPE_OWNER_MISMATCH,
        f"{field} names a record that belongs to another entry than the target; nothing was assessed.",
        context={"field": field},
        message_prefix=False,
    )


def _owner_of(row: Any) -> tuple[OwnerKind, int] | None:
    if getattr(row, "species_entry_id", None) is not None:
        return OwnerKind.species_entry, row.species_entry_id
    if getattr(row, "transition_state_entry_id", None) is not None:
        return OwnerKind.transition_state_entry, row.transition_state_entry_id
    return None


def _check_owned(
    session: Session,
    *,
    field: str,
    kind_label: str,
    ref: str,
    owner: tuple[OwnerKind, int] | None,
    target: tuple[OwnerKind, int],
    statmech_id: int | None,
    profile: frozenset[RecordReviewStatus],
) -> None:
    """Hidden is unknown first; only a ref the caller may see and that is not the target's own is a 422.

    A record is hidden when the read profile hides its owning entry, or (for a declaration) its statmech record.
    """
    if owner is None or not entry_visible(session, owner[0], owner[1], profile=profile):
        raise _unknown(kind_label, ref)
    if statmech_id is not None:
        status = fetch_review_badges(session, record_type=SubmissionRecordType.statmech, record_ids=[statmech_id])[statmech_id].status
        if status not in profile:
            raise _unknown(kind_label, ref)
    if owner != target:
        raise _mismatch(field)


def check_request_refs(
    session: Session, body: StatmechSelectionRequest, *, owner_kind: OwnerKind, owner_id: int, profile: frozenset[RecordReviewStatus]
) -> None:
    """Every public ref the request names, other than candidates and a release, must exist, be visible and be the target's.

    Candidates are checked by the service against the target's own authorized population (one answer for unknown,
    another target's, hidden and a declared record's own ref). For every other ref the order is fixed: a ref that
    names nothing or that the read profile hides is the same 404 ``handle_not_found``; a ref the caller may see but that
    belongs to another entry is the coded 422 ``statmech_selection_scope_owner_mismatch``; and so the 422 can never
    reveal a record the caller could not otherwise see.
    """
    target = (owner_kind, owner_id)
    scope = body.target.scope
    basin = getattr(scope, "conformer_group_ref", None)
    if basin is not None:
        group_id = _ref_only(session, ConformerGroup, basin, kind_label="conformer_group")
        group = session.get(ConformerGroup, group_id)
        _check_owned(
            session, field="conformer_group_ref", kind_label="conformer_group", ref=basin,
            owner=_owner_of(group), target=target, statmech_id=None, profile=profile,
        )
    determination = getattr(scope, "determination_ref", None)
    if determination is not None:
        det_id = _ref_only(session, StructureDetermination, determination, kind_label="structure_determination")
        saddle = session.get(StructureDetermination, det_id)
        _check_owned(
            session, field="determination_ref", kind_label="structure_determination", ref=determination,
            owner=_owner_of(saddle), target=target, statmech_id=None, profile=profile,
        )
    for field, ref in (("ensemble_declaration_ref", getattr(scope, "ensemble_declaration_ref", None)),):
        if ref is None:
            continue
        declaration_id = _ref_only(session, StatmechModelDeclaration, ref, kind_label="statmech_model_declaration")
        declaration = session.get(StatmechModelDeclaration, declaration_id)
        host = session.get(Statmech, declaration.statmech_id) if declaration is not None else None
        _check_owned(
            session, field=field, kind_label="statmech_model_declaration", ref=ref,
            owner=_owner_of(host), target=target, statmech_id=host.id if host is not None else None, profile=profile,
        )


def run_selection(session: Session, *, body: StatmechSelectionRequest) -> StatmechSelection:
    """Run the service for a public request.

    The session must be a read-only snapshot (see ``get_snapshot_db``), and this insists on it: a session that is not
    one raises ``SnapshotNotConsistentError`` instead of quietly answering under READ COMMITTED. Only a session that
    carries ``SNAPSHOT_OPT_OUT`` in its ``info`` is read as it is (the test harness holds one outer transaction that
    cannot become a snapshot); nothing in the application sets it.
    """
    request = body.service_request()
    owner_id = resolve_target(session, request)
    _, profile = statuses_for(request)
    # The target first: a target the profile hides is the same 404 as an unknown one, and every other ref is judged
    # against a target the caller may see.
    assert_subject_visible(session, request.target.owner_kind, owner_id, profile=profile, ref=request.target.owner_ref)
    check_request_refs(session, body, owner_kind=request.target.owner_kind, owner_id=owner_id, profile=profile)
    return select_statmech(
        session,
        request=request,
        owner_id=owner_id,
        require_snapshot=not session.info.get(SNAPSHOT_OPT_OUT, False),
    )


#: The eligibility defaults a statmech selection applies, in the order the answer lists them.
STATMECH_DEFAULTS = (ELIGIBILITY_REJECTED, ELIGIBILITY_DEPRECATED, ELIGIBILITY_CONFIRMED_FINDINGS)


def statmech_defaults(owner_kind: str) -> tuple[str, ...]:
    """A species entry is a scoping parent; a transition-state entry still gates its candidates."""
    return (*STATMECH_DEFAULTS, ELIGIBILITY_ENTRY_STATUS) if owner_kind == OwnerKind.transition_state_entry.value else STATMECH_DEFAULTS


def _candidate(facts: dict[str, Any], assessment: dict[str, Any], verdict: dict[str, Any]) -> StatmechSelectionCandidate:
    return StatmechSelectionCandidate(
        blocked_by=verdict["blocked_by"],
        passed_only_by_override=verdict["passed_only_by_override"],
        candidate_ref=assessment["candidate_ref"],
        kind=assessment["kind"],
        owner_ref=facts["owner_ref"],
        review_status=facts["review_status"],
        created_at=facts["created_at"],
        applicability=assessment["applicability"],
        reasons=assessment["reasons"],
        advisory=assessment["advisory"],
        comparative_unknown=assessment["comparative_unknown"],
        evidence=assessment["evidence"],
        recipe=assessment["recipe"],
        supersession=assessment["supersession"],
        eligible=assessment["eligible"],
    )


def build_response(selection: StatmechSelection) -> StatmechSelectionResponse:
    """The typed response, built from the manifest. Public refs only."""
    manifest = selection.manifest
    decision = manifest["decision"]
    ordering = decision["ordering"]
    population = manifest["population"]
    statuses = sorted((RecordReviewStatus(v) for v in manifest["effective_review_statuses"]), key=lambda s: REVIEW_FLOOR_ORDER[s])
    floor = statuses[-1] if statuses else RecordReviewStatus.approved
    facts = {c["candidate_ref"]: c for c in manifest["candidates"]}
    candidates = [_candidate(facts[a["candidate_ref"]], a, manifest["verdicts"][a["candidate_ref"]]) for a in manifest["assessments"]]
    return StatmechSelectionResponse(
        request=StatmechSelectionRequestEcho(**manifest["request"]),
        review=StatmechSelectionReview(
            effective_floor=floor,
            effective_statuses=statuses,
            candidate_statuses=sorted(
                (RecordReviewStatus(v) for v in manifest["candidate_review_statuses"]), key=lambda s: REVIEW_FLOOR_ORDER[s]
            ),
        ),
        **answer_steps(
            request_filters=manifest["request"]["filters"],
            eligibility_record=manifest["request"]["eligibility"],
            funnel_record=population["funnel"],
            names=statmech_defaults(manifest["request"]["target"]["kind"]),
            profile_floor=manifest["read_profile"]["review_floor"] is not None,
            extra_applied=(
                {"population": manifest["request"]["population"]}
                if manifest["request"]["population"]["kind"] == "model_revisions"
                else None
            ),
        ),
        versions=manifest["versions"],
        outcome=Outcome(manifest["outcome"]),
        basis=decision["basis"],
        selection=StatmechSelectionSelection(**decision["selection"]),
        summary=decision["summary"],
        population=StatmechSelectionPopulation(
            visible_candidates=population["visible_candidates"],
            nested_rows=population["nested_rows"],
            excluded_by_review=population["excluded_by_review"],
            excluded_count=population["excluded_count"],
            excluded_by_review_withheld=population["excluded_by_review_withheld"],
        ),
        coverage=decision["coverage"],
        candidates=candidates,
        fronts=ordering["fronts"],
        administrative_order=ordering["administrative_order"],
        administrative_key=ordering["admin_key"],
        advisories=decision["advisories"],
        notices=decision["notices"],
        manifest=StatmechSelectionManifestDescriptor(
            manifest_format=VersionRef(name=MANIFEST_FORMAT_NAME, version=MANIFEST_FORMAT_VERSION),
            manifest_sha256=manifest["integrity"]["manifest_sha256"],
            canonical_bytes=len(canonical(manifest).encode()),
            path=MANIFEST_PATH,
            note=MANIFEST_NOTE,
        ),
        snapshot_isolation=manifest["snapshot_isolation"],
        integrity=StatmechSelectionIntegrity(**manifest["integrity"]),
    )


__all__ = [
    "CODE_SCOPE_OWNER_MISMATCH",
    "MANIFEST_PATH",
    "SNAPSHOT_OPT_OUT",
    "build_response",
    "check_request_refs",
    "resolve_target",
    "run_selection",
]
