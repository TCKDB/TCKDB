"""The public face of structure selection: request mapping, reference checks, response building.

``select_entry_structures`` (selection.py) decides and records; this module is the thin layer the HTTP routes, and
nothing else, go through. It does four things:

1. maps the public request onto the service's request, with no database id accepted from the caller and the
   engineering bounds the server's own (a caller cannot set or raise any of them, ``manifest_bytes`` included);
2. refuses a deferred quantity with the structured ``structure_selection_unsupported`` before anything is read;
3. checks every public ref the request names (the entry, a fixed geometry) through the ordinary path-handle
   resolver, so a typo is a 404 and not a silent "nothing applies", and a ref the read profile hides answers exactly
   as it does anywhere else;
4. builds the response from the decision manifest, so the response and the downloadable manifest cannot disagree.

Visibility of ``excluded_by_review``. Records that fail the effective review floor are listed (and counted) only when
the read profile has no floor of its own (``exploratory``). Under ``curated`` the floor is ``approved`` and a record
below it is one the caller could not have reached through any other curated read, so naming or counting it would be a
leak. The redaction is made *before* the manifest's checksum is computed (see ``manifest.build_manifest``), so the
downloaded manifest still verifies and replays.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.db.models.common import RecordReviewStatus
from app.db.models.geometry import Geometry
from app.db.models.species import SpeciesEntry
from app.db.models.transition_state import TransitionStateEntry
from app.schemas.reads.scientific_common import REVIEW_RANK
from app.schemas.reads.scientific_structure_selection import (
    CalculationSelectionRequest,
    ConformerSelectionRequest,
    StructureSelectionDisclosures,
    StructureSelectionIntegrity,
    StructureSelectionRequestEcho,
    StructureSelectionResponse,
    StructureSelectionReview,
    StructureSelectionUnit,
    TransitionStateEvidenceSelectionRequest,
)
from app.services.scientific_read.handles import is_ref_handle, resolve_path_handle
from app.services.structure_selection.bounds import parse_quantity
from app.services.structure_selection.models import Quantity, StructureOutcome, StructureRequest
from app.services.structure_selection.selection import StructureSelection, select_entry_structures

#: ``Session.info`` key a TEST harness sets, explicitly, to be read under whatever isolation it already has.
SNAPSHOT_OPT_OUT = "tckdb_read_snapshot_opt_out"

SelectionBody = CalculationSelectionRequest | ConformerSelectionRequest | TransitionStateEvidenceSelectionRequest


def _ref_only(session: Session, model_cls: type, handle: str, *, kind_label: str) -> int:
    """Resolve a public ref to a row id, refusing an integer id outright (``invalid_handle``)."""
    if not is_ref_handle(handle):
        raise ValueError(f"invalid_handle: {handle!r} is not a <prefix>_<body> public ref")
    return resolve_path_handle(session, model_cls, handle, kind_label=kind_label)


def resolve_species_entry_ref(session: Session, ref: str) -> int:
    return _ref_only(session, SpeciesEntry, ref, kind_label="species_entry")


def resolve_transition_state_entry_ref(session: Session, ref: str) -> int:
    return _ref_only(session, TransitionStateEntry, ref, kind_label="transition_state_entry")


def check_request_refs(session: Session, body: SelectionBody) -> None:
    """Every public ref the request names must exist and be visible to the caller (404 otherwise).

    Explicit ``member_refs`` are checked by the service against the entry's own authorized population (one answer for
    unknown, another entry's and hidden); only the fixed geometry is resolved here.
    """
    if body.geometry_ref is not None:
        _ref_only(session, Geometry, body.geometry_ref, kind_label="geometry")


def resolve_quantity(body: SelectionBody) -> Quantity | None:
    """The quantity a request names. A deferred one is the coded 422 ``structure_selection_unsupported``."""
    return parse_quantity(body.quantity) if body.quantity is not None else None


def to_service_request(body: SelectionBody) -> StructureRequest:
    """The service's request for a public one. The bounds are the service's defaults, never the caller's."""
    return body.service_request(quantity=resolve_quantity(body))


def run_selection(session: Session, *, entry_id: int, body: SelectionBody) -> StructureSelection:
    """Run the service for a public request.

    The session must be a read-only snapshot (see ``get_snapshot_db``), and this insists on it: a session that is not
    one raises ``SnapshotNotConsistentError`` instead of quietly answering under READ COMMITTED. Only a session that
    carries ``SNAPSHOT_OPT_OUT`` in its ``info`` is read as it is (the test harness holds one outer transaction that
    cannot become a snapshot); nothing in the application sets it.
    """
    return select_entry_structures(
        session,
        entry_id=entry_id,
        request=to_service_request(body),
        require_snapshot=not session.info.get(SNAPSHOT_OPT_OUT, False),
    )


def _echo(manifest: dict[str, Any], entry_ref: str) -> StructureSelectionRequestEcho:
    request = manifest["request"]
    return StructureSelectionRequestEcho(
        entry_ref=entry_ref,
        grain=request["grain"],
        intent=request["intent"],
        quantity=request["quantity"],
        coverage=request["coverage"],
        validation_claim=request["validation_claim"],
        min_review_status=request["min_review_status"],
        permitted_quality=request["permitted_quality"],
        geometry_ref=request["geometry_ref"],
        member_refs=request["member_refs"],
        recipe=request["recipe"],
        require_stable_reference=request["require_stable_reference"],
        require_connectivity=request["require_connectivity"],
        administrative_policy=request["administrative_policy"],
        result_mode=request["result_mode"],
        apply_rules=request["apply_rules"],
        objective=request["objective"],
        reference_model=request["reference_model"],
        repeat_policy=request["repeat_policy"],
        bounds=request["bounds"],
    )


def build_response(selection: StructureSelection, *, entry_ref: str) -> StructureSelectionResponse:
    """The typed response, built from the manifest. Public refs only."""
    manifest = selection.manifest
    decision = manifest["decision"]
    population = manifest["population"]
    disclosures = manifest.get("disclosures", {})
    statuses = sorted((RecordReviewStatus(v) for v in manifest["effective_review_statuses"]), key=lambda s: REVIEW_RANK[s])
    floor = statuses[-1] if statuses else RecordReviewStatus.approved
    by_unit = {a["unit_ref"]: a for a in manifest["assessments"]}
    rows = {c["calculation_ref"]: c for c in manifest["calculations"]} | {
        d["determination_ref"]: d for d in manifest["determinations"]
    }
    units = []
    for unit_ref, assessment in by_unit.items():
        row = rows[unit_ref]
        units.append(
            StructureSelectionUnit(
                unit_ref=unit_ref,
                review_status=row["review_status"],
                created_at=row["created_at"],
                applicability=assessment["applicability"],
                reasons=assessment["reasons"],
                blocking=assessment["blocking"],
                advisory=assessment["advisory"],
                comparative_unknown=assessment["comparative_unknown"],
                energy=assessment["energy"],
                recipe=assessment["recipe"],
                claim=assessment["claim"],
                eligible=assessment["physically_eligible"],
            )
        )
    return StructureSelectionResponse(
        request=_echo(manifest, entry_ref),
        review=StructureSelectionReview(effective_floor=floor, effective_statuses=statuses),
        outcome=StructureOutcome(manifest["outcome"]),
        basis=decision["basis"],
        selected_refs=decision["selected_refs"],
        administrative_first=decision["administrative_first"],
        cohorts=decision["cohorts"],
        contested_targets=decision["contested_targets"],
        unresolved=decision["unresolved"],
        coverage=decision["coverage"],
        representative_policy=decision["representative_policy"],
        protocol=decision["protocol"],
        administrative_key=decision["administrative_key"],
        search_completeness=decision["search_completeness"],
        notes=decision["notes"],
        units=units,
        disclosures=StructureSelectionDisclosures(
            unresolved_refs=disclosures.get("unresolved_refs", []),
            unsupported_refs=disclosures.get("unsupported_refs", []),
            visible_units=population["visible_units"],
            excluded_by_review=population["excluded_by_review"],
            excluded_count=population["excluded_count"],
            excluded_by_review_withheld=population["excluded_by_review_withheld"],
            notes=list(disclosures.get("notes", [])),
        ),
        versions=manifest["versions"],
        snapshot_isolation=manifest["snapshot_isolation"],
        integrity=StructureSelectionIntegrity(**manifest["integrity"]),
    )


__all__ = [
    "SNAPSHOT_OPT_OUT",
    "build_response",
    "check_request_refs",
    "resolve_quantity",
    "resolve_species_entry_ref",
    "resolve_transition_state_entry_ref",
    "run_selection",
    "to_service_request",
]
