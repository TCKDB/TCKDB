"""The public face of the relative conformer energy operation: ref checks, running the service, building the response.

The routes, and nothing else, go through this module. It does three things:

1. checks every public ref the request names in a fixed order: a malformed or wrong-prefix ref is a 422
   (``invalid_handle`` / ``handle_type_mismatch``); an unknown ref, or one the read profile hides, is the same 404
   ``handle_not_found``; and only a ref the caller may see that belongs to another species entry is the coded 422
   ``relative_conformer_energy_scope_owner_mismatch`` (naming the field, never the other owner). The ownership check
   comes last, so the 422 can never reveal a record the caller could not otherwise see;
2. canonicalises the level (a merged ``lot_`` resolves to the row it was merged into), so the answer is filed under
   one level identity;
3. builds the response from the manifest, so the response and the downloadable manifest cannot disagree.

The response carries no database id, so it is returned directly and not through the internal-id stripper.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.api.error_contract import CodedValueError
from app.db.models.common import RecordReviewStatus
from app.db.models.composite_scheme import CompositeScheme
from app.db.models.level_of_theory import LevelOfTheory
from app.db.models.species import ConformerGroup
from app.schemas.reads.scientific_common import REVIEW_RANK
from app.schemas.reads.scientific_relative_conformer_energy import (
    RelativeConformerEnergyCompareRequest,
    RelativeConformerEnergyCompareResponse,
    RelativeConformerEnergyRequest,
    RelativeConformerEnergyResponse,
    RelativeEnergyCompareRequestEcho,
    RelativeEnergyIntegrity,
    RelativeEnergyLevel,
    RelativeEnergyRequestEcho,
    RelativeEnergyReview,
)
from app.services.relative_conformer_energy.bounds import check_supported
from app.services.relative_conformer_energy.compare import ComparisonRequest
from app.services.relative_conformer_energy.models import LevelSelector
from app.services.relative_conformer_energy.population import profile_statuses
from app.services.relative_conformer_energy.service import (
    ComparisonRun,
    RelativeEnergyRun,
    run_comparison,
    run_relative_energies,
)
from app.services.scientific_read.handles import canonical_level_of_theory_id
from app.services.structure_selection.loader import assert_subject_visible
from app.services.structure_selection.models import Grain
from app.services.structure_selection.public import SNAPSHOT_OPT_OUT, _ref_only, resolve_species_entry_ref

CODE_SCOPE_OWNER_MISMATCH = "relative_conformer_energy_scope_owner_mismatch"


def _mismatch(field: str) -> CodedValueError:
    """A visible ref that belongs to another entry: a contradictory question. Names the field, never the other owner."""
    return CodedValueError(
        CODE_SCOPE_OWNER_MISMATCH,
        f"{field} names a conformer group that belongs to another species entry than the path's; nothing was assessed.",
        context={"field": field},
        message_prefix=False,
    )


def _resolve_group(session: Session, ref: str) -> ConformerGroup:
    group_id = _ref_only(session, ConformerGroup, ref, kind_label="conformer_group")  # malformed 422, unknown/hidden 404
    group = session.get(ConformerGroup, group_id)
    assert group is not None
    return group


def resolve_level(session: Session, body: RelativeConformerEnergyRequest) -> LevelSelector:
    """The level the request names, with a merged ``lot_`` resolved to the kept row's ref. Unknown is a 404."""
    return _resolve_selector(session, body.level)


def _resolve_selector(session: Session, level: RelativeEnergyLevel) -> LevelSelector:
    lot = level.level_of_theory_ref
    if lot is not None:
        row_id = _ref_only(session, LevelOfTheory, lot, kind_label="level_of_theory")
        canonical = session.get(LevelOfTheory, canonical_level_of_theory_id(session, row_id))
        assert canonical is not None
        return LevelSelector(level_of_theory_ref=canonical.public_ref)
    scheme = level.composite_scheme_ref
    assert scheme is not None
    _ref_only(session, CompositeScheme, scheme, kind_label="composite_scheme")
    return LevelSelector(composite_scheme_ref=scheme)


def check_request_refs(session: Session, body: RelativeConformerEnergyRequest, *, entry_id: int) -> LevelSelector:
    """Every ref the request names, in the fixed order. Returns the canonical level."""
    assert_subject_visible(session, Grain.conformer, entry_id, profile=profile_statuses())
    level = resolve_level(session, body)
    named = [("reference_conformer_ref", body.reference_conformer_ref), *(("member_refs", r) for r in body.member_refs or ())]
    # Every ref is resolved (malformed 422, unknown or hidden 404) before any ownership is judged.
    groups = [(field, _resolve_group(session, ref)) for field, ref in named]
    for field, group in groups:
        if group.species_entry_id != entry_id:
            raise _mismatch(field)
    return level


def run(session: Session, *, entry_id: int, body: RelativeConformerEnergyRequest) -> RelativeEnergyRun:
    """Check the refs, then run the service in the snapshot session (which it insists on)."""
    level = check_request_refs(session, body, entry_id=entry_id)
    return run_relative_energies(
        session,
        entry_id=entry_id,
        request=body.service_request(level=level),
        require_snapshot=not session.info.get(SNAPSHOT_OPT_OUT, False),
    )


def refuse_unsupported(body: RelativeConformerEnergyRequest) -> None:
    """Refuse (422 ``relative_conformer_energy_unsupported``) what this release does not evaluate, before any query.

    Called first by both routes, ahead of the entry lookup: the refusal depends on the request alone, so it is the same
    whatever the path names (an unknown entry included) and nothing is read to make it.
    """
    check_supported(body.service_request())


def resolve_entry(session: Session, ref: str) -> int:
    return resolve_species_entry_ref(session, ref)


def build_response(run_: RelativeEnergyRun, *, entry_ref: str) -> RelativeConformerEnergyResponse:
    """The typed response, built from the manifest. Public refs only."""
    manifest: dict[str, Any] = run_.manifest
    result = manifest["result"]
    request = manifest["request"]
    statuses = sorted((RecordReviewStatus(v) for v in manifest["effective_review_statuses"]), key=lambda s: REVIEW_RANK[s])
    floor = statuses[-1] if statuses else RecordReviewStatus.approved
    return RelativeConformerEnergyResponse(
        request=RelativeEnergyRequestEcho(entry_ref=entry_ref, **request),
        review=RelativeEnergyReview(effective_floor=floor, effective_statuses=statuses),
        outcome=manifest["outcome"],
        basis=result["basis"],
        energy_kind=result["energy_kind"],
        temperature_k=request["temperature_k"],
        units=result["units"],
        level=result["level"],
        reference=result["reference"],
        members=result["members"],
        coverage=result["coverage"],
        unresolved=result["unresolved"],
        cohorts=result["cohorts"],
        notes=result["notes"],
        search_completeness=result["search_completeness"],
        versions=manifest["versions"],
        snapshot_isolation=manifest["snapshot_isolation"],
        integrity=RelativeEnergyIntegrity(**manifest["integrity"]),
    )


def refuse_unsupported_compare(body: RelativeConformerEnergyCompareRequest) -> None:
    """Refuse what this release does not evaluate, before any query (the same 422 whatever the path names).

    A protocol compared with itself is not refused here: whether two levels are one level (a merged ``lot_``) is only
    known after the lookups, so that refusal is made in :func:`check_compare_refs`, after the entry, the levels and the
    conformer refs have been resolved. It therefore never answers for an entry, level or group the caller cannot see.
    """
    candidate, _ = body.service_requests()
    check_supported(candidate)


def check_compare_refs(session: Session, body: RelativeConformerEnergyCompareRequest, *, entry_id: int) -> ComparisonRequest:
    """Every ref the comparison names, in the fixed order of :func:`check_request_refs`. Returns the service request."""
    assert_subject_visible(session, Grain.conformer, entry_id, profile=profile_statuses())
    candidate_level = _resolve_selector(session, body.candidate.level)
    reference_level = _resolve_selector(session, body.reference.level)
    named = [("reference_conformer_ref", body.reference_conformer_ref), *(("member_refs", r) for r in body.member_refs or ())]
    groups = [(field, _resolve_group(session, ref)) for field, ref in named]
    for field, group in groups:
        if group.species_entry_id != entry_id:
            raise _mismatch(field)
    candidate, reference = body.service_requests(candidate_level=candidate_level, reference_level=reference_level)
    # Two spellings of one level (a merged lot_ and the one it was merged into) are still one protocol, and two recipes
    # that narrow by the same facts are one recipe: ComparisonRequest refuses that, last, after every ref above.
    return ComparisonRequest(candidate=candidate, reference=reference, pairing=body.pairing)


def run_compare(session: Session, *, entry_id: int, body: RelativeConformerEnergyCompareRequest) -> ComparisonRun:
    """Check the refs, then run the comparison in the snapshot session (which it insists on)."""
    request = check_compare_refs(session, body, entry_id=entry_id)
    return run_comparison(
        session,
        entry_id=entry_id,
        request=request,
        require_snapshot=not session.info.get(SNAPSHOT_OPT_OUT, False),
    )


def build_compare_response(run_: ComparisonRun, *, entry_ref: str) -> RelativeConformerEnergyCompareResponse:
    """The typed comparison response, built from the manifest. Public refs only."""
    manifest: dict[str, Any] = run_.manifest
    result = manifest["result"]
    request = manifest["request"]
    statuses = sorted((RecordReviewStatus(v) for v in manifest["runs"]["candidate"]["effective_review_statuses"]), key=lambda s: REVIEW_RANK[s])
    floor = statuses[-1] if statuses else RecordReviewStatus.approved
    return RelativeConformerEnergyCompareResponse(
        request=RelativeEnergyCompareRequestEcho(
            entry_ref=entry_ref,
            pairing=request["pairing"],
            candidate=request["candidate"],
            reference=request["reference"],
            **{k: v for k, v in request["shared"].items() if k != "reference_conformer_ref"},
            reference_conformer_ref=request["shared"]["reference_conformer_ref"],
        ),
        review=RelativeEnergyReview(effective_floor=floor, effective_statuses=statuses),
        outcome=result["outcome"],
        pairing=result["pairing"],
        coverage_requirement=result["coverage_requirement"],
        units=result["units"],
        statistics=result["statistics"],
        reference_conformer=result["reference_conformer"],
        pairs=result["pairs"],
        candidate=result["candidate"],
        reference=result["reference"],
        claims=result["claims"],
        comparison_version=result["comparison_version"],
        notes=result["notes"],
        versions=manifest["versions"],
        snapshot_isolation=manifest["snapshot_isolation"],
        integrity=RelativeEnergyIntegrity(**manifest["integrity"]),
    )


__all__ = [
    "build_compare_response",
    "build_response",
    "check_compare_refs",
    "check_request_refs",
    "refuse_unsupported",
    "refuse_unsupported_compare",
    "resolve_entry",
    "resolve_level",
    "run",
    "run_compare",
]
