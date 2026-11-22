"""Admin record deletion: history links, deleted-ref reads, repeated deletion and citers without a ref.

History links (a supersession edge, a structure finding about a calculation, a structure-determination source pinning
a calculation) do not keep a record undeletable: they are copied into the audit and removed with it, and the record on
the other side reads the deleted ref and its deletion date.
"""

from __future__ import annotations

from sqlalchemy import select, text

from app.db.models.common import (
    StructureFindingAuthority,
    StructureFindingKind,
    StructureFindingScope,
    StructureFindingVerdict,
    SubmissionRecordType,
)
from app.db.models.record_deletion import RecordDeletion
from app.db.models.structure_determination import StructureEvidenceFinding
from app.services.scientific_record_supersession import supersede_scientific_record
from tests.api.scientific._curated_sweep_selection_world import basin_determination
from tests.api.test_api_admin_record_deletion import (
    _approve,
    _count,
    _curator,
    _delete,
    _kinetics,
    _references_left,
    _species_entry,
)
from tests.services.scientific_read._factories import (
    make_applied_energy_correction,
    make_calculation,
    make_calculation_with_conformer,
    make_conformer_group,
    make_conformer_observation,
    make_frequency_scale_factor,
    make_geometry,
    make_statmech,
)

_RT = SubmissionRecordType


def _audit(session, public_ref: str) -> RecordDeletion:
    return session.scalar(select(RecordDeletion).where(RecordDeletion.record_public_ref == public_ref))


def _superseded_pair(session):
    """Two approved statmech records on one species entry, the first superseded by the second."""
    entry = _species_entry(session)
    old, new = make_statmech(session, species_entry=entry), make_statmech(session, species_entry=entry)
    _approve(session, _RT.statmech, old.id)
    _approve(session, _RT.statmech, new.id)
    supersede_scientific_record(
        session,
        record_type=_RT.statmech,
        superseded_record_id=old.id,
        superseding_record_id=new.id,
        actor=_curator(session),
        reason="corrected frequencies",
    )
    return old, new


def test_the_superseding_record_can_be_deleted_and_the_superseded_one_names_it_as_deleted(
    client, db_session, login_as, _api_admin_user
) -> None:
    old, new = _superseded_pair(db_session)
    old_ref, new_ref, new_id = old.public_ref, new.public_ref, new.id
    login_as(_api_admin_user)

    response = _delete(client, "statmech", new_ref)

    assert response.status_code == 201, response.json()
    assert _references_left(db_session, _RT.statmech, new_id) == {}
    audit = _audit(db_session, new_ref)
    (edge,) = audit.snapshot["history"]["scientific_record_supersession"]
    assert edge["superseded_record_id"] == old.id and edge["superseding_record_id"] == new_id
    notice = client.get(f"/api/v1/scientific/statmech/{old_ref}").json()["record"]["supersession"]
    assert notice["superseded_by"] == new_ref
    assert notice["superseded_by_deleted_at"] == audit.deleted_at.isoformat()
    assert notice["reason"] == "corrected frequencies"


def test_the_superseded_record_can_be_deleted(client, db_session, login_as, _api_admin_user) -> None:
    old, new = _superseded_pair(db_session)
    old_ref, old_id, new_ref = old.public_ref, old.id, new.public_ref
    login_as(_api_admin_user)

    response = _delete(client, "statmech", old_ref)

    assert response.status_code == 201, response.json()
    assert _references_left(db_session, _RT.statmech, old_id) == {}
    assert len(_audit(db_session, old_ref).snapshot["history"]["scientific_record_supersession"]) == 1
    # The successor was never superseded, so it carries no notice before or after.
    assert client.get(f"/api/v1/scientific/statmech/{new_ref}").json()["record"]["supersession"] is None


def test_findings_and_determination_sources_about_a_calculation_go_with_it(
    client, db_session, login_as, _api_admin_user
) -> None:
    entry = _species_entry(db_session)
    observation = make_conformer_observation(db_session, conformer_group=make_conformer_group(db_session, entry))
    calculation = make_calculation_with_conformer(db_session, species_entry=entry, conformer_observation=observation)
    determination = basin_determination(
        db_session,
        entry=entry,
        observation=observation,
        sources=[("energy", calculation)],
        quantity="electronic_energy",
        geometry=make_geometry(db_session, natoms=1),
    )
    finding = StructureEvidenceFinding(
        kind=StructureFindingKind.contradictory_characterization,
        scope=StructureFindingScope.calculation,
        subject_calculation_id=calculation.id,
        verdict=StructureFindingVerdict.invalidates,
        authority=StructureFindingAuthority.authorized_adjudication,
        rationale="two frequency sets disagree",
        semantic_version=1,
    )
    db_session.add(finding)
    db_session.flush()
    _approve(db_session, _RT.calculation, calculation.id)
    calc_id, calc_ref, finding_ref, determination_id = calculation.id, calculation.public_ref, finding.public_ref, determination.id
    login_as(_api_admin_user)

    response = _delete(client, "calculation", calc_ref)

    assert response.status_code == 201, response.json()
    assert _references_left(db_session, _RT.calculation, calc_id) == {}
    history = _audit(db_session, calc_ref).snapshot["history"]
    assert [row["public_ref"] for row in history["structure_evidence_finding"]] == [finding_ref]
    assert [row["calculation_id"] for row in history["structure_determination_source"]] == [calc_id]
    assert _count(db_session, "SELECT count(*) FROM structure_evidence_finding WHERE public_ref = :r", r=finding_ref) == 0
    # The determination itself is not the calculation's to remove.
    assert _count(db_session, "SELECT count(*) FROM structure_determination WHERE id = :id", id=determination_id) == 1


def test_an_integer_handle_of_a_deleted_record_answers_410(client, db_session, login_as, _api_admin_user) -> None:
    calculation = make_calculation(db_session, species_entry_id=_species_entry(db_session).id)
    _approve(db_session, _RT.calculation, calculation.id)
    calc_id, ref = calculation.id, calculation.public_ref
    login_as(_api_admin_user)
    assert _delete(client, "calculation", ref).status_code == 201

    response = client.get(f"/api/v1/scientific/calculations/{calc_id}")

    assert response.status_code == 410
    assert response.json()["code"] == "record_deleted"
    assert response.json()["context"]["public_ref"] == ref


def test_a_record_with_an_audit_row_already_is_refused_with_a_code(client, db_session, login_as, _api_admin_user) -> None:
    kinetics = _kinetics(db_session)
    db_session.execute(
        text(
            "INSERT INTO record_deletion (record_type, record_id, record_public_ref, first_approved_at, reason, "
            "deleted_by, snapshot) VALUES ('kinetics', :id, :ref, now(), 'earlier', :by, '{}'::jsonb)"
        ),
        {"id": kinetics.id, "ref": kinetics.public_ref, "by": _api_admin_user},
    )
    login_as(_api_admin_user)

    response = _delete(client, "kinetics", kinetics.public_ref)

    assert response.status_code == 410
    assert response.json()["code"] == "record_deleted"


def test_a_citer_without_a_ref_is_named_by_the_record_it_belongs_to(
    client, db_session, login_as, _api_admin_user
) -> None:
    entry = _species_entry(db_session)
    calculation = make_calculation(db_session, species_entry_id=entry.id)
    make_applied_energy_correction(
        db_session,
        target_species_entry=entry,
        frequency_scale_factor=make_frequency_scale_factor(db_session),
        source_calculation=calculation,
    )
    _approve(db_session, _RT.calculation, calculation.id)
    login_as(_api_admin_user)

    response = _delete(client, "calculation", calculation.public_ref)

    assert response.status_code == 409
    assert response.json()["context"]["cited_by"] == [
        {
            "record_type": "species_entry",
            "public_ref": entry.public_ref,
            "via": "applied_energy_correction.source_calculation_id",
        }
    ]
