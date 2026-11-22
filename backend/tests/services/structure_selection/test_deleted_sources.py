"""A determination that lost a pinned source to an admin deletion: marked, and not eligible if the source was required.

Deleting a calculation removes the ``structure_determination_source`` rows that pin it (a history link). The
determination still reads every source it was made from: a removed one is marked ``source_deleted`` with the deleted
calculation's ref and the deletion time, from the deletion's audit copy. A determination that states a quantity and has
lost every energy source is blocked with ``required_source_deleted``; losing a source the quantity does not need leaves
it eligible.
"""

from __future__ import annotations

from app.db.models.app_user import AppUser
from app.db.models.common import (
    AppUserRole,
    StructureFindingAuthority,
    StructureFindingKind,
    StructureFindingScope,
    StructureFindingVerdict,
    SubmissionRecordType,
)
from app.db.models.structure_determination import StructureEvidenceFinding
from app.services.record_deletion import delete_approved_record
from app.services.structure_evidence_finding import present_findings
from app.services.structure_selection import assess_entry_structures
from app.services.structure_selection.assessment import REQUIRED_SOURCE_DELETED
from tests.services.structure_selection._support import make_world, minimum_request
from tests.services.structure_selection.test_service import build_basin, by_ref


def _delete(session, calculation) -> tuple[str, str]:
    admin = AppUser(username=f"source-deletion-admin-{calculation.id}", role=AppUserRole.admin)
    session.add(admin)
    session.flush()
    ref = calculation.public_ref
    deletion = delete_approved_record(
        session, record_type=SubmissionRecordType.calculation, public_ref=ref, reason="withdrawn", actor=admin
    )
    return ref, deletion.deleted_at.isoformat()


def _assess(session, world, **request):
    return assess_entry_structures(
        session, entry_id=world.entry.id, request=minimum_request(**request), require_snapshot=False
    )


def _marked(result, determination_ref):
    (det,) = [d for d in result.determinations if d.determination_ref == determination_ref]
    return [s for s in det.sources if s.deleted_calculation_ref is not None], det


def test_a_deleted_energy_source_is_marked_and_blocks_the_determination(db_session) -> None:
    world = make_world(db_session)
    d, _opt, sp, _f = build_basin(world)
    world.settle()
    d_ref = d.public_ref
    sp_ref, deleted_at = _delete(db_session, sp)

    result = _assess(db_session, world)

    marked, det = _marked(result, d_ref)
    assert [(s.role, s.deleted_calculation_ref, s.deleted_at, s.unavailable_reason) for s in marked] == [
        ("energy", sp_ref, deleted_at, "source_deleted")
    ]
    assert {s.role for s in det.sources} == {"geometry_optimization", "energy", "curvature"}
    assessment = by_ref(result)[d_ref]
    assert REQUIRED_SOURCE_DELETED in assessment.blocking
    assert not assessment.physically_eligible
    serialised = det.to_dict()["sources"]
    assert {"role": "energy", "geometry_ref": None, "calculation_ref": None, "unavailable_reason": "source_deleted",
            "deleted_calculation_ref": sp_ref, "deleted_at": deleted_at} in serialised


def test_a_deleted_source_the_quantity_does_not_need_is_marked_and_stays_eligible(db_session) -> None:
    world = make_world(db_session)
    d, opt, _sp, _f = build_basin(world, wire=False)
    world.settle()
    d_ref = d.public_ref
    assert by_ref(_assess(db_session, world))[d_ref].physically_eligible
    opt_ref, deleted_at = _delete(db_session, opt)

    result = _assess(db_session, world)

    marked, _det = _marked(result, d_ref)
    assert [(s.role, s.deleted_calculation_ref, s.deleted_at) for s in marked] == [
        ("geometry_optimization", opt_ref, deleted_at)
    ]
    assessment = by_ref(result)[d_ref]
    assert REQUIRED_SOURCE_DELETED not in assessment.blocking
    assert assessment.physically_eligible


def test_an_untouched_source_serialises_without_the_deletion_keys(db_session) -> None:
    world = make_world(db_session)
    d, *_ = build_basin(world)
    world.settle()
    (det,) = [x for x in _assess(db_session, world).determinations if x.determination_ref == d.public_ref]
    assert all(set(s) == {"role", "geometry_ref", "calculation_ref", "unavailable_reason"} for s in det.to_dict()["sources"])


def test_a_finding_whose_adjudication_was_removed_reads_the_removal(db_session) -> None:
    world = make_world(db_session)
    d, _opt, sp, _f = build_basin(world)
    world.settle()
    finding = world.finding(determination=d, kind=StructureFindingKind.identity_incompatibility)
    adjudication = StructureEvidenceFinding(
        kind=StructureFindingKind.adjudication,
        scope=StructureFindingScope.determination,
        subject_determination_id=d.id,
        verdict=StructureFindingVerdict.does_not_invalidate,
        authority=StructureFindingAuthority.authorized_adjudication,
        rationale="settled on a reconstruction",
        semantic_version=1,
        source_calculation_id=sp.id,
        supersedes_finding_id=finding.id,
    )
    db_session.add(adjudication)
    db_session.flush()
    adjudication_ref = adjudication.public_ref
    assert present_findings(db_session, [finding], d.public_ref)[0]["in_force"] is False
    sp_ref, deleted_at = _delete(db_session, sp)

    (read,) = present_findings(db_session, [finding], d.public_ref)

    assert read["removed_adjudications"] == [
        {"finding_ref": adjudication_ref, "deleted_calculation_ref": sp_ref, "deleted_at": deleted_at}
    ]
    # The adjudication went with the calculation it rested on, so the finding is in force again.
    assert read["in_force"] is True
