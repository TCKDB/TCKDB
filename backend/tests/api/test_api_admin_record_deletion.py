"""``DELETE /api/v1/admin/records/{record_type}/{public_ref}`` and the deletion audit.

An approved record is immutable to everyone; this route is the one sanctioned way to remove one, admin only, with a
reason, refused while anything cites it or a published release selects it, and leaving an append-only audit row with a
JSON copy of every row it removed.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Enum as SAEnum
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.api.app import create_app
from app.api.deps import get_db, get_write_db
from app.db.base import Base
from app.db.models.app_user import AppUser
from app.db.models.common import (
    AppUserRole,
    DatasetReleaseStatus,
    RecordReviewStatus,
    ReleaseSelectionAction,
    SubmissionKind,
    SubmissionRecordType,
)
from app.db.models.dataset_release import CurationPolicy, DatasetRelease, ReleaseSelection
from app.db.models.deposit_warning import DepositWarning
from app.db.models.record_deletion import RecordDeletion
from app.services.deposit_warnings import content_hash
from app.services.record_deletion import OVERLAY_ROWS, OWNED_ROWS
from app.services.record_review import ensure_record_review, set_record_review_status
from app.services.submission import create_submission, link_record
from tests.services.scientific_read._factories import (
    attach_artifact,
    attach_dependency,
    attach_input_geometry,
    attach_sp_result,
    attach_thermo_source_calculation,
    make_calculation,
    make_chem_reaction,
    make_geometry,
    make_kinetics,
    make_reaction_entry,
    make_species,
    make_species_entry,
    make_thermo_scalar,
    next_inchi_key,
)

_RT = SubmissionRecordType
_WARNING_CODE = "sp_energy_payload_log_mismatch"


@pytest.fixture
def anon_client(db_session: Session):
    """DB overrides but no auth override, so real authentication runs."""
    app = create_app()
    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[get_write_db] = lambda: db_session
    with TestClient(app) as c:
        yield c


_USERS = iter(range(1, 10_000))


def _curator(session: Session) -> AppUser:
    curator = AppUser(username=f"deletion-curator-{next(_USERS)}", role=AppUserRole.curator)
    session.add(curator)
    session.flush()
    return curator


def _approve(session: Session, record_type: SubmissionRecordType, record_id: int) -> None:
    ensure_record_review(session, record_type=record_type, record_id=record_id)
    set_record_review_status(
        session, record_type=record_type, record_id=record_id, status=RecordReviewStatus.approved, actor=_curator(session)
    )


def _reaction_entry(session: Session):
    reactant = make_species(session, smiles="C=C", inchi_key=next_inchi_key("RDELR"))
    product = make_species(session, smiles="CC", inchi_key=next_inchi_key("RDELP"))
    reaction = make_chem_reaction(session, reactants=[reactant], products=[product])
    return make_reaction_entry(
        session,
        reaction=reaction,
        reactant_entries=[make_species_entry(session, species=reactant)],
        product_entries=[make_species_entry(session, species=product)],
    )


def _species_entry(session: Session):
    return make_species_entry(session, make_species(session, inchi_key=next_inchi_key("RDELS")))


def _kinetics(session: Session, *, approve: bool = True):
    kinetics = make_kinetics(session, reaction_entry=_reaction_entry(session))
    if approve:
        _approve(session, _RT.kinetics, kinetics.id)
    return kinetics


def _with_overlays(session: Session, record_type: SubmissionRecordType, record_id: int) -> None:
    """A submission link and a record-level deposit warning, as a real deposit leaves them."""
    depositor = AppUser(username=f"deletion-depositor-{record_id}", role=AppUserRole.user)
    session.add(depositor)
    session.flush()
    submission = create_submission(session, created_by=depositor.id, submission_kind=SubmissionKind.kinetics)
    link_record(session, submission=submission, record_type=record_type, record_id=record_id)
    session.add(
        DepositWarning(
            submission_id=submission.id,
            record_type=record_type,
            record_id=record_id,
            written_by_user_id=depositor.id,
            code=_WARNING_CODE,
            field="f",
            message="deposit-time warning",
            content_hash=content_hash(_WARNING_CODE, "f", "deposit-time warning", None),
            check_version=1,
        )
    )
    session.flush()


def _delete(client: TestClient, record_type: str, public_ref: str, reason: str = "duplicate of an earlier deposit"):
    return client.post(
        "/api/v1/admin/record-deletions",
        json={"record_type": record_type, "public_ref": public_ref, "reason": reason},
    )


def _count(session: Session, sql: str, **params) -> int:
    return session.execute(text(sql), params).scalar_one()


def _references_left(
    session: Session, record_type: SubmissionRecordType, record_id: int, *, kept: frozenset[str] = frozenset()
) -> dict[str, int]:
    """Every row anywhere that still points at the record: each foreign key into its table, each owned-row predicate,
    and each ``(record_type, record_id)`` column pair. ``kept`` names the ``table.column`` keys a caller expects to
    remain (release history); any other key is a dangling row."""
    left: dict[str, int] = {}
    root = record_type.value
    for table in Base.metadata.sorted_tables:
        for constraint in table.foreign_key_constraints:
            if constraint.referred_table.name != root:
                continue
            for element in constraint.elements:
                if element.column.name != "id":
                    continue
                n = _count(session, f"SELECT count(*) FROM {table.name} WHERE {element.parent.name} = :id", id=record_id)
                if n:
                    left[f"{table.name}.{element.parent.name}"] = n
        type_columns = [
            column.name
            for column in table.columns
            if isinstance(column.type, SAEnum) and column.type.enum_class is SubmissionRecordType
        ]
        for type_column in type_columns:
            id_column = {"record_type": "record_id", "subject_type": "subject_id"}.get(type_column)
            id_columns = (
                [id_column]
                if id_column
                else ["superseded_record_id", "superseding_record_id"]
                if table.name == "scientific_record_supersession"
                else []
            )
            for id_name in id_columns:
                if id_name not in table.columns:
                    continue
                if table.name == "record_deletion":
                    continue
                n = _count(
                    session,
                    f"SELECT count(*) FROM {table.name} WHERE {type_column}::text = :type AND {id_name} = :id",
                    type=root,
                    id=record_id,
                )
                if n:
                    left[f"{table.name}.{id_name}"] = n
    for owned in OWNED_ROWS.get(record_type, ()):
        n = _count(session, f"SELECT count(*) FROM {owned.table} WHERE {owned.predicate}", id=record_id)
        if n:
            left[owned.table] = n
    return {key: n for key, n in left.items() if key not in kept}


# ---------------------------------------------------------------------------
# The contract
# ---------------------------------------------------------------------------


def test_admin_deletes_an_approved_kinetics_record_and_nothing_references_it(
    client, db_session, login_as, _api_admin_user
) -> None:
    kinetics = _kinetics(db_session, approve=False)
    source = make_calculation(db_session, species_entry_id=_species_entry(db_session).id)
    db_session.execute(
        text("INSERT INTO kinetics_source_calculation (kinetics_id, calculation_id, role) VALUES (:k, :c, 'ts_energy')"),
        {"k": kinetics.id, "c": source.id},
    )
    _approve(db_session, _RT.kinetics, kinetics.id)
    _with_overlays(db_session, _RT.kinetics, kinetics.id)
    kinetics_id, ref, source_id = kinetics.id, kinetics.public_ref, source.id
    assert _references_left(db_session, _RT.kinetics, kinetics_id), "the scan must see the rows before deletion"

    login_as(_api_admin_user)
    response = _delete(client, "kinetics", ref)

    assert response.status_code == 201, response.json()
    body = response.json()
    assert body["record_type"] == "kinetics"
    assert body["public_ref"] == ref
    assert body["subject_type"] == "reaction_entry"
    assert [link["record_type"] for link in body["subject_chain"]] == ["reaction_entry", "reaction"]
    assert body["deleted_by"] == "testadmin"
    assert body["reason"] == "duplicate of an earlier deposit"
    assert body["removed_rows"]["kinetics"] == 1
    assert body["removed_rows"]["kinetics_source_calculation"] == 1
    assert body["removed_rows"]["record_review"] == 1
    assert body["removed_rows"]["record_review_event"] >= 1
    assert body["removed_rows"]["deposit_warning"] == 1
    assert body["removed_rows"]["submission_record_link"] == 1

    assert _count(db_session, "SELECT count(*) FROM kinetics WHERE id = :id", id=kinetics_id) == 0
    assert _references_left(db_session, _RT.kinetics, kinetics_id) == {}
    # The cited calculation is not the kinetics record's to remove.
    assert _count(db_session, "SELECT count(*) FROM calculation WHERE id = :id", id=source_id) == 1

    audit = db_session.scalar(select(RecordDeletion).where(RecordDeletion.record_public_ref == ref))
    assert audit is not None and audit.record_id == kinetics_id
    assert audit.snapshot["record"]["public_ref"] == ref
    assert audit.snapshot["owned"]["kinetics_source_calculation"][0]["calculation_id"] == source_id
    assert {overlay.table for overlay in OVERLAY_ROWS} == set(audit.snapshot["overlays"])
    assert audit.snapshot["overlays"]["record_review"][0]["first_approved_at"] is not None

    again = _delete(client, "kinetics", ref)
    assert again.status_code == 410
    assert again.json()["code"] == "record_deleted"


def test_a_calculation_is_deleted_with_its_children_and_its_artifacts_overlays(
    client, db_session, login_as, _api_admin_user
) -> None:
    entry = _species_entry(db_session)
    parent = make_calculation(db_session, species_entry_id=entry.id)
    calculation = make_calculation(db_session, species_entry_id=entry.id)
    attach_sp_result(db_session, calculation=calculation, electronic_energy_hartree=-40.5)
    attach_input_geometry(db_session, calculation=calculation, geometry=make_geometry(db_session, natoms=1))
    artifact = attach_artifact(db_session, calculation=calculation)
    attach_dependency(db_session, parent=parent, child=calculation)
    _approve(db_session, _RT.calculation, parent.id)
    _approve(db_session, _RT.calculation, calculation.id)
    ensure_record_review(db_session, record_type=_RT.artifact, record_id=artifact.id)
    calculation_id, artifact_id, ref, parent_id = calculation.id, artifact.id, calculation.public_ref, parent.id

    login_as(_api_admin_user)
    response = _delete(client, "calculation", ref)

    assert response.status_code == 201, response.json()
    assert _references_left(db_session, _RT.calculation, calculation_id) == {}
    assert _references_left(db_session, _RT.artifact, artifact_id) == {}
    # The approved parent the deleted calculation was derived from is untouched.
    assert _count(db_session, "SELECT count(*) FROM calculation WHERE id = :id", id=parent_id) == 1
    removed = response.json()["removed_rows"]
    assert removed["calculation_dependency"] == 1
    assert removed["calculation_artifact"] == 1
    assert removed["record_review"] == 2  # the calculation's and its artifact's


def test_a_read_of_a_deleted_ref_answers_410_with_the_reason_only_in_a_marked_field(
    client, db_session, login_as, _api_admin_user
) -> None:
    calculation = make_calculation(db_session, species_entry_id=_species_entry(db_session).id)
    _approve(db_session, _RT.calculation, calculation.id)
    ref = calculation.public_ref
    login_as(_api_admin_user)
    assert _delete(client, "calculation", ref, reason="Withdrawn by depositor: wrong basis set").status_code == 201

    response = client.get(f"/api/v1/scientific/calculations/{ref}")

    assert response.status_code == 410
    body = response.json()
    assert body["code"] == "record_deleted"
    assert body["context"]["public_ref"] == ref
    assert body["context"]["record_type"] == "calculation"
    assert body["context"]["deleted_at"]
    assert body["context"]["admin_supplied"] == {"reason": "Withdrawn by depositor: wrong basis set"}
    assert "wrong basis set" not in body["detail"]


def test_a_curator_is_refused_and_nothing_changes(client, db_session, login_as, _api_curator_user) -> None:
    kinetics = _kinetics(db_session)
    login_as(_api_curator_user)

    response = _delete(client, "kinetics", kinetics.public_ref)

    assert response.status_code == 403
    assert _count(db_session, "SELECT count(*) FROM kinetics WHERE id = :id", id=kinetics.id) == 1
    assert _count(db_session, "SELECT count(*) FROM record_deletion") == 0
    assert client.get("/api/v1/admin/record-deletions").status_code == 403


def test_a_depositor_is_refused_and_an_anonymous_caller_is_unauthenticated(client, db_session, anon_client) -> None:
    kinetics = _kinetics(db_session)
    assert _delete(client, "kinetics", kinetics.public_ref).status_code == 403
    assert _delete(anon_client, "kinetics", kinetics.public_ref).status_code == 401
    assert anon_client.get("/api/v1/admin/record-deletions").status_code == 401
    assert _count(db_session, "SELECT count(*) FROM kinetics WHERE id = :id", id=kinetics.id) == 1


def test_a_never_approved_record_is_refused(client, db_session, login_as, _api_admin_user) -> None:
    kinetics = _kinetics(db_session, approve=False)
    login_as(_api_admin_user)

    response = _delete(client, "kinetics", kinetics.public_ref)

    assert response.status_code == 409
    assert response.json()["code"] == "record_deletion_not_approved"
    assert _count(db_session, "SELECT count(*) FROM kinetics WHERE id = :id", id=kinetics.id) == 1


def test_a_blank_reason_and_an_unsupported_type_are_refused(client, db_session, login_as, _api_admin_user) -> None:
    kinetics = _kinetics(db_session)
    login_as(_api_admin_user)
    assert _delete(client, "kinetics", kinetics.public_ref, reason="   ").status_code == 422
    response = _delete(client, "species", "spc_whatever")
    assert response.status_code == 422
    assert response.json()["code"] == "record_deletion_type_not_supported"
    assert _count(db_session, "SELECT count(*) FROM kinetics WHERE id = :id", id=kinetics.id) == 1


def test_a_cited_calculation_is_refused_with_the_citing_records(client, db_session, login_as, _api_admin_user) -> None:
    entry = _species_entry(db_session)
    calculation = make_calculation(db_session, species_entry_id=entry.id)
    _approve(db_session, _RT.calculation, calculation.id)
    thermo = make_thermo_scalar(db_session, species_entry=entry)
    attach_thermo_source_calculation(db_session, thermo=thermo, calculation=calculation)
    kinetics = _kinetics(db_session, approve=False)
    db_session.execute(
        text("INSERT INTO kinetics_source_calculation (kinetics_id, calculation_id, role) VALUES (:k, :c, 'ts_energy')"),
        {"k": kinetics.id, "c": calculation.id},
    )
    login_as(_api_admin_user)

    response = _delete(client, "calculation", calculation.public_ref)

    assert response.status_code == 409
    body = response.json()
    assert body["code"] == "record_deletion_cited"
    assert sorted(body["context"]["cited_by"], key=lambda c: c["record_type"]) == [
        {"record_type": "kinetics", "public_ref": kinetics.public_ref, "via": "kinetics_source_calculation.calculation_id"},
        {"record_type": "thermo", "public_ref": thermo.public_ref, "via": "thermo_source_calculation.calculation_id"},
    ]
    assert _count(db_session, "SELECT count(*) FROM calculation WHERE id = :id", id=calculation.id) == 1
    assert _count(db_session, "SELECT count(*) FROM record_deletion") == 0


def _release(session: Session, *, status: DatasetReleaseStatus, tag: str) -> DatasetRelease:
    policy = CurationPolicy(name=f"policy-{tag}", version="1", description="test policy", criteria_json={})
    session.add(policy)
    session.flush()
    release = DatasetRelease(
        tag=tag,
        title=f"Release {tag}",
        status=status,
        curation_policy_id=policy.id,
        data_license="CC-BY-4.0",
        code_license="MIT",
        citation_text="cite me",
        contact="admin@example.org",
        published_at=None if status is DatasetReleaseStatus.draft else datetime.now(timezone.utc).replace(tzinfo=None),
    )
    session.add(release)
    session.flush()
    return release


def _select(session: Session, release: DatasetRelease, kinetics, actor_id: int) -> ReleaseSelection:
    selection = ReleaseSelection(
        dataset_release_id=release.id,
        curation_policy_id=release.curation_policy_id,
        record_type=_RT.kinetics,
        record_id=kinetics.id,
        subject_type=_RT.reaction_entry,
        subject_id=kinetics.reaction_entry_id,
        action=ReleaseSelectionAction.select,
        rationale="best available",
        selected_by=actor_id,
    )
    session.add(selection)
    session.flush()
    return selection


def test_a_record_in_a_published_release_is_refused_until_the_release_is_withdrawn(
    client, db_session, login_as, _api_admin_user
) -> None:
    kinetics = _kinetics(db_session)
    kinetics_id, ref = kinetics.id, kinetics.public_ref
    release = _release(db_session, status=DatasetReleaseStatus.published, tag="rdel-2026.1")
    release_ref = release.public_ref
    selection = _select(db_session, release, kinetics, _api_admin_user)
    selection_id, selection_ref = selection.id, selection.public_ref
    login_as(_api_admin_user)

    refused = _delete(client, "kinetics", ref)

    assert refused.status_code == 409
    assert refused.json()["code"] == "record_deletion_in_published_release"
    assert refused.json()["context"]["releases"] == [{"tag": "rdel-2026.1", "release_ref": release.public_ref}]
    assert _count(db_session, "SELECT count(*) FROM kinetics WHERE id = :id", id=kinetics_id) == 1

    release.status = DatasetReleaseStatus.withdrawn
    release.withdrawn_at = datetime.now(timezone.utc).replace(tzinfo=None)
    release.withdrawn_reason = "superseded"
    db_session.flush()

    deleted = _delete(client, "kinetics", ref)

    assert deleted.status_code == 201, deleted.json()
    # A withdrawn release's selection is its history: kept, and copied into the audit. It is the one row allowed to
    # keep naming the deleted id; anything else left behind fails here.
    assert _count(db_session, "SELECT count(*) FROM release_selection WHERE id = :id", id=selection_id) == 1
    history = frozenset({"release_selection.record_id"})
    assert _references_left(db_session, _RT.kinetics, kinetics_id, kept=history) == {}
    assert set(_references_left(db_session, _RT.kinetics, kinetics_id)) == history
    audit = db_session.scalar(select(RecordDeletion).where(RecordDeletion.record_public_ref == ref))
    assert [row["public_ref"] for row in audit.snapshot["release_selections"]] == [selection_ref]

    # The release still reads, and names the deleted record by its ref and deletion date rather than null.
    read = client.get(f"/api/v1/scientific/releases/{release_ref}/selections")
    assert read.status_code == 200, read.json()
    (row,) = [r for r in read.json()["records"] if r["selection_ref"] == selection_ref]
    assert row["record_ref"] == ref
    assert row["record_deleted_at"] == audit.deleted_at.isoformat()


def test_a_standing_draft_selection_is_a_citation(client, db_session, login_as, _api_admin_user) -> None:
    kinetics = _kinetics(db_session)
    selection = _select(db_session, _release(db_session, status=DatasetReleaseStatus.draft, tag="rdel-draft"), kinetics, _api_admin_user)
    login_as(_api_admin_user)

    response = _delete(client, "kinetics", kinetics.public_ref)

    assert response.status_code == 409
    assert response.json()["code"] == "record_deletion_cited"
    assert response.json()["context"]["cited_by"] == [
        {"record_type": "release_selection", "public_ref": selection.public_ref, "via": "dataset_release rdel-draft"}
    ]


def test_the_audit_is_listed_newest_first_and_read_with_its_copy(client, db_session, login_as, _api_admin_user) -> None:
    first, second = _kinetics(db_session).public_ref, _kinetics(db_session).public_ref
    login_as(_api_admin_user)
    assert _delete(client, "kinetics", first).status_code == 201
    assert _delete(client, "kinetics", second).status_code == 201

    listing = client.get("/api/v1/admin/record-deletions", params={"limit": 1})
    assert listing.status_code == 200
    page = listing.json()
    assert page["total"] == 2 and page["limit"] == 1 and len(page["items"]) == 1
    assert page["items"][0]["public_ref"] == second
    assert "snapshot" not in page["items"][0]

    detail = client.get(f"/api/v1/admin/record-deletions/{first}")
    assert detail.status_code == 200
    assert detail.json()["snapshot"]["record"]["public_ref"] == first
    assert client.get("/api/v1/admin/record-deletions/kin_nothing").status_code == 404
    assert db_session.scalar(select(func.count()).select_from(RecordDeletion)) == 2
