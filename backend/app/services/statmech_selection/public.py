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

from app.db.models.common import RecordReviewStatus
from app.db.models.species import ConformerGroup
from app.db.models.statmech_declaration import StatmechModelDeclaration
from app.db.models.structure_determination import StructureDetermination
from app.schemas.reads.scientific_common import REVIEW_RANK
from app.schemas.reads.scientific_statmech_selection import (
    StatmechSelectionCandidate,
    StatmechSelectionConflicts,
    StatmechSelectionIntegrity,
    StatmechSelectionManifestDescriptor,
    StatmechSelectionPopulation,
    StatmechSelectionRequest,
    StatmechSelectionRequestEcho,
    StatmechSelectionResponse,
    StatmechSelectionReview,
    StatmechSelectionSelection,
)
from app.services.statmech_selection.manifest import MANIFEST_FORMAT_VERSION, canonical
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


def resolve_target(session: Session, request: StatmechRequest) -> int:
    """The id of the species or transition-state entry the request names (404 when absent or hidden)."""
    if request.target.owner_kind is OwnerKind.species_entry:
        return resolve_species_entry_ref(session, request.target.owner_ref)
    return resolve_transition_state_entry_ref(session, request.target.owner_ref)


def check_request_refs(session: Session, body: StatmechSelectionRequest) -> None:
    """Every public ref the request names, other than candidates and a release, must exist and be visible (404 otherwise)."""
    scope = body.target.scope
    basin = getattr(scope, "conformer_group_ref", None)
    if basin is not None:
        _ref_only(session, ConformerGroup, basin, kind_label="conformer_group")
    determination = getattr(scope, "determination_ref", None)
    if determination is not None:
        _ref_only(session, StructureDetermination, determination, kind_label="structure_determination")
    ensemble = getattr(scope, "ensemble_declaration_ref", None)
    if ensemble is not None:
        _ref_only(session, StatmechModelDeclaration, ensemble, kind_label="statmech_model_declaration")
    if body.reference_model_ref is not None:
        _ref_only(session, StatmechModelDeclaration, body.reference_model_ref, kind_label="statmech_model_declaration")


def run_selection(session: Session, *, body: StatmechSelectionRequest) -> StatmechSelection:
    """Run the service for a public request.

    The session must be a read-only snapshot (see ``get_snapshot_db``), and this insists on it: a session that is not
    one raises ``SnapshotNotConsistentError`` instead of quietly answering under READ COMMITTED. Only a session that
    carries ``SNAPSHOT_OPT_OUT`` in its ``info`` is read as it is (the test harness holds one outer transaction that
    cannot become a snapshot); nothing in the application sets it.
    """
    request = body.service_request()
    check_request_refs(session, body)
    return select_statmech(
        session,
        request=request,
        owner_id=resolve_target(session, request),
        require_snapshot=not session.info.get(SNAPSHOT_OPT_OUT, False),
    )


def _candidate(facts: dict[str, Any], assessment: dict[str, Any]) -> StatmechSelectionCandidate:
    return StatmechSelectionCandidate(
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
    protocol = decision["protocol"]
    population = manifest["population"]
    statuses = sorted((RecordReviewStatus(v) for v in manifest["effective_review_statuses"]), key=lambda s: REVIEW_RANK[s])
    floor = statuses[-1] if statuses else RecordReviewStatus.approved
    facts = {c["candidate_ref"]: c for c in manifest["candidates"]}
    candidates = [_candidate(facts[a["candidate_ref"]], a) for a in manifest["assessments"]]
    return StatmechSelectionResponse(
        request=StatmechSelectionRequestEcho(**manifest["request"]),
        review=StatmechSelectionReview(effective_floor=floor, effective_statuses=statuses),
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
        edges=protocol["edges"],
        fronts=protocol["fronts"],
        conflicts=StatmechSelectionConflicts(
            opposing_pairs=protocol["opposing_pairs"],
            cycles=protocol["cycles"],
            overridden_edges=protocol["overridden_edges"],
        ),
        undecided=protocol["undecided"],
        advisories=decision["advisories"],
        notices=decision["notices"],
        protocol=protocol,
        manifest=StatmechSelectionManifestDescriptor(
            manifest_format_version=MANIFEST_FORMAT_VERSION,
            manifest_sha256=manifest["integrity"]["manifest_sha256"],
            canonical_bytes=len(canonical(manifest).encode()),
            path=MANIFEST_PATH,
            note=MANIFEST_NOTE,
        ),
        snapshot_isolation=manifest["snapshot_isolation"],
        integrity=StatmechSelectionIntegrity(**manifest["integrity"]),
    )


__all__ = [
    "MANIFEST_PATH",
    "SNAPSHOT_OPT_OUT",
    "build_response",
    "check_request_refs",
    "resolve_target",
    "run_selection",
]
