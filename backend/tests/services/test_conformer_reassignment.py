"""Reassignment of existing conformer observations under the current scheme.

The states built here are the ones an earlier rule left behind: a basin split in two by a +0.5 / -0.5
degree wobble, and two different hydroxyl rotamers merged into one. ``reassign_conformer_groups`` must
repair both, move what pointed at a changed group by D5 (the group holding the observation a record's
own calculations came from, else the group holding the old representative), reset the review of every
group whose membership changed, and do it all through the declared-repair path when the rows are
approved. Nothing here may bypass the accepted-science triggers; one test shows the trigger still
refuses an undeclared write.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from app.db.models.app_user import AppUser
from app.db.models.common import (
    AppUserRole,
    RecordReviewStatus,
    StatmechModelScopeKind,
    SubmissionRecordType,
    ThermoTargetKind,
)
from app.db.models.record_review import RecordReview, RecordReviewEvent
from app.db.models.species import (
    ConformerGroup,
    ConformerGroupMergePending,
    ConformerObservation,
    ConformerSelection,
)
from app.db.models.statmech_declaration import StatmechModelDeclaration, StatmechModelMember
from app.db.models.thermo import Thermo
from app.services.conformer_reassignment import reassign_conformer_groups
from app.services.conformer_resolution import TORSION_BASIN_PARAMS, compute_fingerprint_for_observation
from app.services.record_review import ensure_record_review, set_record_review_status
from app.services.statmech_declaration_resolution import declaration_content_hash, recompute_content_hash
from tests.services.scientific_read._factories import (
    attach_geometry_atoms,
    attach_output_geometry,
    attach_statmech_source_calculation,
    attach_thermo_source_calculation,
    make_calculation,
    make_conformer_group,
    make_geometry,
    make_species,
    make_species_entry,
    make_statmech,
    make_thermo_scalar,
    make_workflow_tool_release,
    next_inchi_key,
)
from tests.services.test_basin_matching import _ARYL, with_dihedral

_REVISION = "test-reassign"
_NITRO = "c1ccccc1[N+](=O)[O-]"
_ETHANOL = "CCO"


def _ethanol(deg: float):
    return with_dihedral(_ETHANOL, "[#1][OX2][CH2][CH3]", deg, seed=5)


def _curator(session) -> AppUser:
    actor = AppUser(username=f"reassign-{uuid4().hex[:10]}", role=AppUserRole.curator)
    session.add(actor)
    session.flush()
    return actor


def _approve(session, record_type: SubmissionRecordType, record_id: int) -> None:
    ensure_record_review(session, record_type=record_type, record_id=record_id)
    set_record_review_status(
        session, record_type=record_type, record_id=record_id, status=RecordReviewStatus.approved,
        actor=_curator(session),
    )


def _seed(session, smiles: str, geometries: list, group_index: list[int]):
    """A species entry with one observation per geometry, filed under the groups ``group_index`` names.

    ``group_index[i]`` is the group (0-based, created in order) observation ``i`` is filed under. Each
    observation owns an ``opt`` calculation anchored to it whose final output geometry is its geometry,
    which is where reassignment finds the structure again.
    """
    species = make_species(session, smiles=smiles, inchi_key=next_inchi_key("REASS"))
    entry = make_species_entry(session, species)
    groups = [make_conformer_group(session, entry, label=f"conformer_{i + 1}") for i in range(max(group_index) + 1)]
    observations, calculations = [], []
    for atoms, g in zip(geometries, group_index, strict=True):
        observation = ConformerObservation(conformer_group_id=groups[g].id)
        session.add(observation)
        session.flush()
        calc = make_calculation(session, species_entry_id=entry.id, conformer_observation_id=observation.id)
        geometry = make_geometry(session, natoms=len(atoms))
        attach_geometry_atoms(
            session, geometry=geometry, symbols=[a[0] for a in atoms], coords=[list(a[1:]) for a in atoms]
        )
        attach_output_geometry(session, calculation=calc, geometry=geometry)
        if groups[g].representative_fingerprint_json is None:
            fingerprint, coords = compute_fingerprint_for_observation(smiles, tuple(atoms), dict(TORSION_BASIN_PARAMS))
            groups[g].representative_fingerprint_json = fingerprint.to_dict()
            groups[g].representative_coords_json = coords
        observations.append(observation)
        calculations.append(calc)
    session.flush()
    return entry, groups, observations, calculations


def _target_thermo(session, entry, group, calculations=()) -> Thermo:
    thermo = make_thermo_scalar(session, species_entry=entry)
    thermo.thermodynamic_target_kind = ThermoTargetKind.single_conformer
    thermo.target_conformer_group_id = group.id
    session.flush()
    for calc in calculations:
        attach_thermo_source_calculation(session, thermo=thermo, calculation=calc)
    return thermo


def _review_status(session, group_id: int):
    session.expire_all()
    return session.scalar(
        select(RecordReview.status).where(
            RecordReview.record_type == SubmissionRecordType.conformer_group, RecordReview.record_id == group_id
        )
    )


def _groups(session, entry) -> list[ConformerGroup]:
    session.expire_all()
    return list(session.scalars(select(ConformerGroup).where(ConformerGroup.species_entry_id == entry.id).order_by(ConformerGroup.id)))


# ---------------------------------------------------------------------------
# A false split is merged onto the older group
# ---------------------------------------------------------------------------


def _v1_scheme_id(session) -> int:
    return session.scalar(text("SELECT id FROM conformer_assignment_scheme WHERE name = 'torsion_basin' AND version = 1"))


def _current_scheme_id(session) -> int:
    return session.scalar(text("SELECT id FROM conformer_assignment_scheme WHERE name = 'torsion_basin' AND version = 3"))


def test_a_split_basin_is_merged_onto_the_older_group_and_its_dependents_follow(db_session) -> None:
    session = db_session
    entry, (older, newer), (obs_a, obs_b), (calc_a, calc_b) = _seed(
        session, _NITRO, [with_dihedral(_NITRO, _ARYL, 0.5), with_dihedral(_NITRO, _ARYL, -0.5)], [0, 1]
    )
    thermo = _target_thermo(session, entry, newer, [calc_b])
    v1 = _v1_scheme_id(session)
    session.add(ConformerSelection(conformer_group_id=older.id, selection_kind="lowest_energy"))
    session.add(ConformerSelection(conformer_group_id=newer.id, selection_kind="lowest_energy"))
    session.add(ConformerSelection(conformer_group_id=newer.id, selection_kind="display_default", assignment_scheme_id=v1))
    session.flush()
    for group in (older, newer):
        ensure_record_review(session, record_type=SubmissionRecordType.conformer_group, record_id=group.id)
        set_record_review_status(
            session, record_type=SubmissionRecordType.conformer_group, record_id=group.id,
            status=RecordReviewStatus.under_review, actor=_curator(session),
        )
    older_id, newer_id, thermo_id, obs_b_id = older.id, newer.id, thermo.id, obs_b.id

    report = reassign_conformer_groups(session, repair_revision=_REVISION, species_entry_ids=[entry.id])

    session.expire_all()
    assert session.get(ConformerObservation, obs_b_id).conformer_group_id == older_id
    # The emptied group is kept (deleting it would orphan its review and kill a public ref), emptied of its
    # representative, and its review is reset like every other group whose membership changed.
    assert [g.id for g in _groups(session, entry)] == [older_id, newer_id]
    assert session.get(ConformerGroup, newer_id).representative_fingerprint_json is None
    assert session.get(Thermo, thermo_id).target_conformer_group_id == older_id
    placed = {
        (s.conformer_group_id, s.selection_kind.value, s.assignment_scheme_id)
        for s in session.scalars(select(ConformerSelection)).all()
    }
    # The moved selection is re-stamped with the new scheme; the one that would duplicate a selection already on
    # the target is not deleted, it stays where it was.
    assert placed == {
        (older_id, "lowest_energy", None),
        (newer_id, "lowest_energy", None),
        (older_id, "display_default", _current_scheme_id(session)),
    }
    for group_id in (older_id, newer_id):
        assert _review_status(session, group_id) is RecordReviewStatus.not_reviewed
    events = session.scalars(
        select(RecordReviewEvent).join(RecordReview).where(RecordReview.record_id == newer_id)
        .where(RecordReview.record_type == SubmissionRecordType.conformer_group)
    ).all()
    assert any(e.to_status is RecordReviewStatus.not_reviewed and e.from_status is RecordReviewStatus.under_review for e in events)
    assert (report.observations_moved, report.groups_emptied_kept, report.thermo_moved) == (1, 1, 1)
    assert report.selections_left_duplicate == 1 and report.selections_moved == 1
    assert report.groups_review_reset == 2


def test_an_emptied_group_that_was_approved_loses_its_approval_too(db_session) -> None:
    session = db_session
    entry, (older, newer), *_ = _seed(
        session, _NITRO, [with_dihedral(_NITRO, _ARYL, 0.5), with_dihedral(_NITRO, _ARYL, -0.5)], [0, 1]
    )
    _approve(session, SubmissionRecordType.conformer_group, newer.id)
    newer_id = newer.id
    assert _review_status(session, newer_id) is RecordReviewStatus.approved
    reassign_conformer_groups(session, repair_revision=_REVISION, species_entry_ids=[entry.id])
    assert _review_status(session, newer_id) is RecordReviewStatus.not_reviewed
    assert session.get(ConformerGroup, newer_id) is not None
    events = session.scalars(
        select(RecordReviewEvent).join(RecordReview).where(RecordReview.record_id == newer_id)
        .where(RecordReview.record_type == SubmissionRecordType.conformer_group)
    ).all()
    assert any(e.from_status is RecordReviewStatus.approved and e.to_status is RecordReviewStatus.not_reviewed for e in events)


def test_a_group_split_out_by_reassignment_gets_a_real_public_ref(db_session) -> None:
    session = db_session
    entry, (group,), *_ = _seed(session, _ETHANOL, [_ethanol(180.0), _ethanol(60.0)], [0, 0])
    reassign_conformer_groups(session, repair_revision=_REVISION, species_entry_ids=[entry.id])
    new = _groups(session, entry)[1]
    assert new.public_ref.startswith("cg_") and len(new.public_ref) == len("cg_") + 26
    assert new.public_ref != group.public_ref


def test_reassignment_is_idempotent(db_session) -> None:
    session = db_session
    entry, *_ = _seed(
        session, _NITRO, [with_dihedral(_NITRO, _ARYL, 0.5), with_dihedral(_NITRO, _ARYL, -0.5)], [0, 1]
    )
    first = reassign_conformer_groups(session, repair_revision=_REVISION, species_entry_ids=[entry.id])
    assert first.observations_moved == 1
    second = reassign_conformer_groups(session, repair_revision=_REVISION, species_entry_ids=[entry.id])
    changed = {k: v for k, v in second.__dict__.items() if isinstance(v, int) and k not in ("entries_examined", "observations_examined")}
    assert not any(changed.values()), changed
    assert second.entries_changed == 0


# ---------------------------------------------------------------------------
# A false merge is split; the original row keeps the representative
# ---------------------------------------------------------------------------


def test_a_merged_basin_is_split_and_dependents_follow_the_evidence(db_session) -> None:
    session = db_session
    entry, (group,), (trans, gauche), (calc_trans, calc_gauche) = _seed(
        session, _ETHANOL, [_ethanol(180.0), _ethanol(60.0)], [0, 0]
    )
    follows_evidence = _target_thermo(session, entry, group, [calc_gauche])
    no_evidence = _target_thermo(session, entry, group, [])
    session.add(ConformerSelection(conformer_group_id=group.id, selection_kind="lowest_energy"))
    ensure_record_review(session, record_type=SubmissionRecordType.conformer_group, record_id=group.id)
    set_record_review_status(
        session, record_type=SubmissionRecordType.conformer_group, record_id=group.id,
        status=RecordReviewStatus.approved, actor=_curator(session),
    )
    session.flush()
    group_id, gauche_id, trans_id = group.id, gauche.id, trans.id
    evidence_id, bare_id = follows_evidence.id, no_evidence.id

    report = reassign_conformer_groups(session, repair_revision=_REVISION, species_entry_ids=[entry.id])

    groups = _groups(session, entry)
    assert len(groups) == 2 and groups[0].id == group_id  # the original row stays with the representative
    new_id = groups[1].id
    assert groups[1].label == "conformer_2"
    assert session.get(ConformerObservation, trans_id).conformer_group_id == group_id
    assert session.get(ConformerObservation, gauche_id).conformer_group_id == new_id
    assert session.get(Thermo, evidence_id).target_conformer_group_id == new_id  # its calculation was the gauche one
    assert session.get(Thermo, bare_id).target_conformer_group_id == group_id  # no evidence: stays with the representative
    selection = session.scalars(select(ConformerSelection)).one()
    assert selection.conformer_group_id == group_id
    assert _review_status(session, group_id) is RecordReviewStatus.not_reviewed  # was approved; membership changed
    assert _review_status(session, new_id) is RecordReviewStatus.not_reviewed
    assert report.groups_created == 1 and report.observations_moved == 1 and report.thermo_moved == 1


def test_evidence_spanning_two_new_groups_falls_back_to_the_representative(db_session) -> None:
    session = db_session
    entry, (group,), _, (calc_trans, calc_gauche) = _seed(session, _ETHANOL, [_ethanol(180.0), _ethanol(60.0)], [0, 0])
    both = _target_thermo(session, entry, group, [calc_trans, calc_gauche])
    both_id, group_id = both.id, group.id
    reassign_conformer_groups(session, repair_revision=_REVISION, species_entry_ids=[entry.id])
    session.expire_all()
    assert session.get(Thermo, both_id).target_conformer_group_id == group_id


# ---------------------------------------------------------------------------
# Approved rows: the declared-repair path, and nothing else
# ---------------------------------------------------------------------------


def test_approved_observation_and_thermo_target_move_through_a_declared_repair(db_session) -> None:
    session = db_session
    entry, (group,), (trans, gauche), (calc_trans, calc_gauche) = _seed(
        session, _ETHANOL, [_ethanol(180.0), _ethanol(60.0)], [0, 0]
    )
    thermo = _target_thermo(session, entry, group, [calc_gauche])
    _approve(session, SubmissionRecordType.conformer_observation, gauche.id)
    _approve(session, SubmissionRecordType.thermo, thermo.id)
    group_id, gauche_id, thermo_id = group.id, gauche.id, thermo.id

    # The trigger refuses an undeclared write to an approved observation: the baseline the repair has to clear.
    with pytest.raises(DBAPIError):
        with session.begin_nested():
            session.execute(
                text("UPDATE conformer_observation SET conformer_group_id = conformer_group_id WHERE id = :id"),
                {"id": gauche_id},
            )
            session.execute(text("UPDATE conformer_observation SET note = 'x' WHERE id = :id"), {"id": gauche_id})

    report = reassign_conformer_groups(session, repair_revision=_REVISION, species_entry_ids=[entry.id])

    session.expire_all()
    new_id = session.get(ConformerObservation, gauche_id).conformer_group_id
    assert new_id != group_id
    assert session.get(Thermo, thermo_id).target_conformer_group_id == new_id
    assert sorted(report.repairs_declared) == ["conformer_observation", "thermo"]

    declared = {
        row.target_table: row
        for row in session.execute(
            text("SELECT id, target_table, declared_columns, alembic_revision FROM accepted_science_repair "
                 "WHERE alembic_revision = :r"),
            {"r": _REVISION},
        )
    }
    assert set(declared) == {"conformer_observation", "thermo"}
    assert set(declared["conformer_observation"].declared_columns) == {
        "conformer_group_id", "assignment_scheme_id", "torsion_fingerprint_json", "mapped_coords_json"
    }
    assert list(declared["thermo"].declared_columns) == ["target_conformer_group_id"]

    def change(record_type: str, record_id: int):
        return session.execute(
            text("SELECT changed_columns, before_json, after_json FROM accepted_science_repair_change "
                 "WHERE record_type = CAST(:t AS submission_record_type) AND record_id = :id"),
            {"t": record_type, "id": record_id},
        ).one()

    observation_change = change("conformer_observation", gauche_id)
    assert "conformer_group_id" in observation_change.changed_columns
    assert observation_change.before_json["conformer_group_id"] == group_id
    assert observation_change.after_json["conformer_group_id"] == new_id
    thermo_change = change("thermo", thermo_id)
    assert list(thermo_change.changed_columns) == ["target_conformer_group_id"]
    assert (thermo_change.before_json["target_conformer_group_id"], thermo_change.after_json["target_conformer_group_id"]) == (group_id, new_id)


def test_reassignment_changes_only_the_declared_columns_of_an_approved_row(db_session) -> None:
    """The observation's note and every other column are untouched; only link, scheme, fingerprint and mapped coordinates move."""
    session = db_session
    entry, (group,), (trans, gauche), _ = _seed(session, _ETHANOL, [_ethanol(180.0), _ethanol(60.0)], [0, 0])
    gauche.note = "depositor note"
    session.flush()
    _approve(session, SubmissionRecordType.conformer_observation, gauche.id)
    gauche_id = gauche.id
    before = session.execute(
        text("SELECT note, scientific_origin::text AS origin, created_at FROM conformer_observation WHERE id = :id"),
        {"id": gauche_id},
    ).one()
    reassign_conformer_groups(session, repair_revision=_REVISION, species_entry_ids=[entry.id])
    after = session.execute(
        text("SELECT note, scientific_origin::text AS origin, created_at FROM conformer_observation WHERE id = :id"),
        {"id": gauche_id},
    ).one()
    assert tuple(after) == tuple(before)


def _basin_declaration(session, entry, group, calculation) -> tuple:
    statmech = make_statmech(session, species_entry=entry)
    attach_statmech_source_calculation(session, statmech=statmech, calculation=calculation)
    release = make_workflow_tool_release(session)
    # The checksum is written with the row: a declaration cannot be edited afterwards.
    content_hash = declaration_content_hash(
        version=1, scope_kind="basin", scope_basin_ref=group.public_ref, scope_determination_ref=None,
        recipe={}, members=[], sources=[], literature_ref=None, workflow_tool_release_ref=release.public_ref,
    )
    declaration = StatmechModelDeclaration(
        statmech_id=statmech.id,
        schema_version=1,
        scope_kind=StatmechModelScopeKind.basin,
        conformer_group_id=group.id,
        workflow_tool_release_id=release.id,
        recipe={},
        content_hash=content_hash,
    )
    session.add(declaration)
    session.flush()
    return statmech, declaration


def _declare_statmech_repair(session, table: str, columns: tuple[str, ...]) -> None:
    session.execute(
        text(
            "INSERT INTO accepted_science_repair (target_table, declared_columns, alembic_revision, reason) "
            "VALUES (:t, CAST(:c AS text[]), 'test', 'test repair')"
        ),
        {"t": table, "c": "{" + ",".join(columns) + "}"},
    )


def _ensemble_member(session, entry, statmech, group, member_statmech):
    """An ensemble declaration with one member in ``group``, its checksum written over that member (as production does)."""
    release = make_workflow_tool_release(session)
    content_hash = declaration_content_hash(
        version=1, scope_kind="explicit_equilibrium_ensemble", scope_basin_ref=None, scope_determination_ref=None,
        recipe={},
        members=[{
            "member_key": "m1", "statmech": member_statmech.public_ref, "basin": group.public_ref,
            "multiplicity": None, "relative_energy_kj_mol": None, "region_key": None,
        }],
        sources=[], literature_ref=None, workflow_tool_release_ref=release.public_ref,
    )
    declaration = StatmechModelDeclaration(
        statmech_id=statmech.id, schema_version=1, scope_kind=StatmechModelScopeKind.explicit_equilibrium_ensemble,
        workflow_tool_release_id=release.id, recipe={}, content_hash=content_hash,
    )
    session.add(declaration)
    session.flush()
    member = StatmechModelMember(
        declaration_id=declaration.id, member_key="m1", member_statmech_id=member_statmech.id, conformer_group_id=group.id
    )
    session.add(member)
    session.flush()
    session.expire(declaration)
    assert recompute_content_hash(session, declaration) == content_hash, "the helper must write a true checksum"
    return declaration, member


def test_an_approved_statmech_basin_declaration_moves_through_a_declared_repair(db_session) -> None:
    session = db_session
    entry, (group,), (trans, gauche), (calc_trans, calc_gauche) = _seed(
        session, _ETHANOL, [_ethanol(180.0), _ethanol(60.0)], [0, 0]
    )
    statmech, declaration = _basin_declaration(session, entry, group, calc_gauche)  # evidence says: the gauche basin
    _approve(session, SubmissionRecordType.statmech, statmech.id)
    group_id, declaration_id, gauche_id = group.id, declaration.id, gauche.id
    hash_before = declaration.content_hash

    report = reassign_conformer_groups(session, repair_revision=_REVISION, species_entry_ids=[entry.id])

    session.expire_all()
    new_id = session.get(ConformerObservation, gauche_id).conformer_group_id
    moved = session.get(StatmechModelDeclaration, declaration_id)
    assert new_id != group_id and moved.conformer_group_id == new_id
    assert hash_before != moved.content_hash == recompute_content_hash(session, moved)  # the checksum follows the basin
    assert report.statmech_declarations_moved == 1
    declared = session.execute(
        text("SELECT declared_columns FROM accepted_science_repair WHERE target_table = 'statmech_model_declaration'")
    ).one()
    assert sorted(declared.declared_columns) == ["conformer_group_id", "content_hash"]
    rows = session.execute(
        text(
            "SELECT changed_columns, before_json, after_json FROM accepted_science_repair_change "
            "WHERE target_table = 'statmech_model_declaration' AND (row_identity ->> 'id')::bigint = :i ORDER BY id"
        ),
        {"i": declaration_id},
    ).all()
    before = {k: v for r in rows for k, v in r.before_json.items()}
    after = {k: v for r in rows for k, v in r.after_json.items()}
    assert sorted(before) == sorted(after) == ["conformer_group_id", "content_hash"]
    assert (before["conformer_group_id"], after["conformer_group_id"]) == (group_id, new_id)
    assert before["content_hash"] == hash_before and after["content_hash"] == moved.content_hash

    # Idempotent: nothing left to move, so a second run declares and changes nothing.
    again = reassign_conformer_groups(session, repair_revision=_REVISION, species_entry_ids=[entry.id])
    assert again.statmech_declarations_moved == 0 and again.observations_moved == 0 and again.entries_changed == 0


def test_an_ensemble_member_moves_through_a_declared_repair_and_is_recorded(db_session) -> None:
    session = db_session
    entry, (group,), (trans, gauche), (calc_trans, calc_gauche) = _seed(
        session, _ETHANOL, [_ethanol(180.0), _ethanol(60.0)], [0, 0]
    )
    owner = make_statmech(session, species_entry=entry)
    cited = make_statmech(session, species_entry=entry)
    attach_statmech_source_calculation(session, statmech=cited, calculation=calc_gauche)
    declaration, member = _ensemble_member(session, entry, owner, group, cited)
    _approve(session, SubmissionRecordType.statmech, owner.id)
    member_id, group_id, gauche_id, declaration_id = member.id, group.id, gauche.id, declaration.id
    hash_before = declaration.content_hash

    report = reassign_conformer_groups(session, repair_revision=_REVISION, species_entry_ids=[entry.id])

    session.expire_all()
    new_id = session.get(ConformerObservation, gauche_id).conformer_group_id
    assert session.get(StatmechModelMember, member_id).conformer_group_id == new_id != group_id
    assert report.statmech_members_moved == 1
    # The member's basin is inside the parent declaration's checksum, so the parent's checksum is recomputed
    # even though the declaration's own basin did not move.
    parent = session.get(StatmechModelDeclaration, declaration_id)
    assert hash_before != parent.content_hash == recompute_content_hash(session, parent)
    change = session.execute(
        text(
            "SELECT before_json, after_json FROM accepted_science_repair_change "
            "WHERE target_table = 'statmech_model_member' AND (row_identity ->> 'id')::bigint = :i"
        ),
        {"i": member_id},
    ).one()
    assert (change.before_json["conformer_group_id"], change.after_json["conformer_group_id"]) == (group_id, new_id)


def test_an_unapproved_declaration_move_is_recorded_too(db_session) -> None:
    session = db_session
    entry, (group,), (trans, gauche), (calc_trans, calc_gauche) = _seed(
        session, _ETHANOL, [_ethanol(180.0), _ethanol(60.0)], [0, 0]
    )
    _statmech, declaration = _basin_declaration(session, entry, group, calc_gauche)
    declaration_id = declaration.id
    reassign_conformer_groups(session, repair_revision=_REVISION, species_entry_ids=[entry.id])
    recorded = session.execute(
        text("SELECT count(*) FROM accepted_science_repair_change WHERE target_table = 'statmech_model_declaration' "
             "AND (row_identity ->> 'id')::bigint = :i"),
        {"i": declaration_id},
    ).scalar()
    assert recorded >= 1


@pytest.mark.parametrize("approved", [False, True], ids=["unapproved", "approved"])
def test_an_undeclared_edit_of_the_basin_is_still_refused(db_session, approved: bool) -> None:
    session = db_session
    entry, (group,), (trans, gauche), (calc_trans, calc_gauche) = _seed(
        session, _ETHANOL, [_ethanol(180.0), _ethanol(60.0)], [0, 0]
    )
    other = make_conformer_group(session, entry, label="other")
    statmech, declaration = _basin_declaration(session, entry, group, calc_gauche)
    if approved:
        _approve(session, SubmissionRecordType.statmech, statmech.id)
    with pytest.raises(DBAPIError):
        with session.begin_nested():
            session.execute(
                text("UPDATE statmech_model_declaration SET conformer_group_id = :g WHERE id = :i"),
                {"g": other.id, "i": declaration.id},
            )


@pytest.mark.parametrize("approved", [False, True], ids=["unapproved", "approved"])
def test_any_other_declaration_column_is_refused_even_with_the_repair_declared(db_session, approved: bool) -> None:
    session = db_session
    entry, (group,), (trans, gauche), (calc_trans, calc_gauche) = _seed(
        session, _ETHANOL, [_ethanol(180.0), _ethanol(60.0)], [0, 0]
    )
    other = make_conformer_group(session, entry, label="other")
    statmech, declaration = _basin_declaration(session, entry, group, calc_gauche)
    if approved:
        _approve(session, SubmissionRecordType.statmech, statmech.id)
    # Even declaring more columns than the owner's decision allows does not open them.
    _declare_statmech_repair(session, "statmech_model_declaration", ("conformer_group_id", "schema_version", "content_hash"))
    for statement in (
        "UPDATE statmech_model_declaration SET schema_version = 2 WHERE id = :i",
        "UPDATE statmech_model_declaration SET content_hash = repeat('a', 64) WHERE id = :i",
        "UPDATE statmech_model_declaration SET conformer_group_id = :g, schema_version = 2 WHERE id = :i",
    ):
        with pytest.raises(DBAPIError):
            with session.begin_nested():
                session.execute(text(statement), {"i": declaration.id, "g": other.id})
    # ...while the one column the repair covers, declared, is allowed.
    session.execute(
        text("UPDATE statmech_model_declaration SET conformer_group_id = :g WHERE id = :i"),
        {"g": other.id, "i": declaration.id},
    )


# ---------------------------------------------------------------------------
# Things it must leave alone
# ---------------------------------------------------------------------------


def test_an_observation_with_no_recoverable_geometry_is_left_where_it_is(db_session) -> None:
    session = db_session
    entry, (group,), (kept, orphan), _ = _seed(session, _ETHANOL, [_ethanol(180.0), _ethanol(60.0)], [0, 0])
    session.execute(text("UPDATE calculation SET conformer_observation_id = NULL WHERE conformer_observation_id = :id"), {"id": orphan.id})
    report = reassign_conformer_groups(session, repair_revision=_REVISION, species_entry_ids=[entry.id])
    session.expire_all()
    assert session.get(ConformerObservation, orphan.id).conformer_group_id == group.id
    assert report.observations_unresolved == 1
    assert report.groups_created == 0


def test_the_per_entry_advisory_lock_is_taken(db_session) -> None:
    from sqlalchemy import event

    session = db_session
    entry, *_ = _seed(session, _ETHANOL, [_ethanol(180.0)], [0])
    captured: list[str] = []

    def _capture(conn, cursor, statement, parameters, context, executemany):
        captured.append(statement)

    event.listen(session.get_bind(), "before_cursor_execute", _capture)
    try:
        reassign_conformer_groups(session, repair_revision=_REVISION, species_entry_ids=[entry.id])
    finally:
        event.remove(session.get_bind(), "before_cursor_execute", _capture)
    assert any("pg_advisory_xact_lock" in s for s in captured)


# ---------------------------------------------------------------------------
# The trigger checks the new value, not only which column changed
# ---------------------------------------------------------------------------


def _foreign_group(session):
    other_species = make_species(session, smiles="CCCC", inchi_key=next_inchi_key("OTHER"))
    other_entry = make_species_entry(session, other_species)
    return make_conformer_group(session, other_entry, label="foreign")


def _fixture_with_member_and_declaration(session):
    entry, (group,), (trans, gauche), (calc_trans, calc_gauche) = _seed(
        session, _ETHANOL, [_ethanol(180.0), _ethanol(60.0)], [0, 0]
    )
    own_other = make_conformer_group(session, entry, label="own-other")
    owner = make_statmech(session, species_entry=entry)
    cited = make_statmech(session, species_entry=entry)
    declaration, member = _ensemble_member(session, entry, owner, group, cited)
    _statmech, basin_declaration = _basin_declaration(session, entry, group, calc_gauche)
    return entry, group, own_other, declaration, member, basin_declaration


@pytest.mark.parametrize("kind", ["member", "declaration"])
@pytest.mark.parametrize("value", ["NULL", "foreign"])
def test_a_declared_repair_cannot_point_a_basin_at_nothing_or_at_another_entrys_group(db_session, kind, value) -> None:
    session = db_session
    entry, group, own_other, declaration, member, basin_declaration = _fixture_with_member_and_declaration(session)
    foreign = _foreign_group(session)
    _declare_statmech_repair(session, "statmech_model_member", ("conformer_group_id",))
    _declare_statmech_repair(session, "statmech_model_declaration", ("conformer_group_id", "content_hash"))
    table, row_id = (
        ("statmech_model_member", member.id) if kind == "member" else ("statmech_model_declaration", basin_declaration.id)
    )
    target = "NULL" if value == "NULL" else str(foreign.id)
    with pytest.raises(DBAPIError):
        with session.begin_nested():
            session.execute(text(f"UPDATE {table} SET conformer_group_id = {target} WHERE id = :i"), {"i": row_id})
    # the same statement with a group of the record's own entry is the repair that is allowed
    session.execute(text(f"UPDATE {table} SET conformer_group_id = :g WHERE id = :i"), {"g": own_other.id, "i": row_id})


def test_content_hash_alone_is_refused_but_follows_a_basin_move_in_the_same_transaction(db_session) -> None:
    session = db_session
    entry, group, own_other, declaration, member, basin_declaration = _fixture_with_member_and_declaration(session)
    _declare_statmech_repair(session, "statmech_model_member", ("conformer_group_id",))
    _declare_statmech_repair(session, "statmech_model_declaration", ("conformer_group_id", "content_hash"))
    # no move of this declaration (or of any member of it) has happened in this transaction yet
    with pytest.raises(DBAPIError):
        with session.begin_nested():
            session.execute(
                text("UPDATE statmech_model_declaration SET content_hash = repeat('a', 64) WHERE id = :i"),
                {"i": declaration.id},
            )
    # a member of the declaration moves; now the parent's checksum may be rewritten
    session.execute(
        text("UPDATE statmech_model_member SET conformer_group_id = :g WHERE id = :i"), {"g": own_other.id, "i": member.id}
    )
    session.execute(
        text("UPDATE statmech_model_declaration SET content_hash = repeat('b', 64) WHERE id = :i"), {"i": declaration.id}
    )
    # but not a different declaration's, which nothing moved
    with pytest.raises(DBAPIError):
        with session.begin_nested():
            session.execute(
                text("UPDATE statmech_model_declaration SET content_hash = repeat('c', 64) WHERE id = :i"),
                {"i": basin_declaration.id},
            )


# ---------------------------------------------------------------------------
# Which geometry an observation is recomputed from, and when the step runs at all
# ---------------------------------------------------------------------------


def test_the_geometry_is_the_one_the_primary_calculation_carries_not_a_later_optimisation(db_session) -> None:
    from app.db.models.common import CalculationType
    from app.services.conformer_basin_store import observation_geometry_atoms as _observation_geometry_atoms
    from tests.services.scientific_read._factories import attach_input_geometry

    session = db_session
    entry, (group,), (obs,), (primary,) = _seed(session, _ETHANOL, [_ethanol(180.0)], [0])
    # The upload deposited a single-point on the conformer geometry (an input only); a later optimisation of the
    # same observation produced a different structure. The upload's own geometry is the primary's.
    primary.type = CalculationType.sp
    session.execute(text("DELETE FROM calculation_output_geometry WHERE calculation_id = :c"), {"c": primary.id})
    own = make_geometry(session, natoms=9)
    own_atoms = _ethanol(180.0)
    attach_geometry_atoms(session, geometry=own, symbols=[a[0] for a in own_atoms], coords=[list(a[1:]) for a in own_atoms])
    attach_input_geometry(session, calculation=primary, geometry=own)
    later = make_calculation(session, species_entry_id=entry.id, conformer_observation_id=obs.id)
    other_atoms = _ethanol(60.0)
    other = make_geometry(session, natoms=9)
    attach_geometry_atoms(session, geometry=other, symbols=[a[0] for a in other_atoms], coords=[list(a[1:]) for a in other_atoms])
    attach_output_geometry(session, calculation=later, geometry=other)
    session.flush()

    atoms = _observation_geometry_atoms(session, [obs.id])[obs.id]
    assert [round(c, 4) for a in atoms for c in a[1:]] == [round(c, 4) for a in own_atoms for c in a[1:]]


def test_the_step_does_nothing_unless_the_default_scheme_is_the_one_this_code_implements(db_session) -> None:
    session = db_session
    entry, (group,), *_ = _seed(session, _ETHANOL, [_ethanol(180.0), _ethanol(60.0)], [0, 0])
    session.execute(text("UPDATE conformer_assignment_scheme SET is_default = false WHERE is_default"))
    session.execute(
        text(
            "INSERT INTO conformer_assignment_scheme (name, version, scope, public_ref, is_default) "
            "VALUES ('torsion_basin', 99, CAST('canonical' AS conformer_assignment_scope_kind), 'cas_future_test', true)"
        )
    )
    report = reassign_conformer_groups(session, repair_revision=_REVISION, species_entry_ids=[entry.id])
    assert report.skipped_reason and "99" in report.skipped_reason
    assert report.entries_examined == 0 and report.groups_created == 0
    assert len(_groups(session, entry)) == 1


# ---------------------------------------------------------------------------
# A merge an upload could not make: owed, then completed by the owner-role reassignment
# ---------------------------------------------------------------------------


def _two_groups_then_what_points_at_the_younger(session, kind: str):
    """Two optimised butane structures 36 degrees apart (two groups) and one approved record on the YOUNGER group.

    Returns ``(entry, older_id, younger_id, observation_ids, where)``; ``where()`` is the group the record points at.
    """
    from app.db.models.calculation import Calculation
    from app.db.models.kinetics import KineticsInterpretationAssignment
    from tests.services.scientific_read._factories import make_chem_reaction, make_kinetics, make_reaction_entry
    from tests.services.test_conformer_basin_linkage import _BUTANE, butane, deposit_observations

    observations = deposit_observations(session, _BUTANE, [butane("A", 0.0), butane("C", 36.0)])
    a, c = (session.get(ConformerObservation, observations[n]) for n in ("A", "C"))
    older_id, younger_id = a.conformer_group_id, c.conformer_group_id
    assert older_id < younger_id
    younger = session.get(ConformerGroup, younger_id)
    entry = younger.species_entry
    calc_c = session.scalars(select(Calculation).where(Calculation.conformer_observation_id == c.id)).first()

    approve = (lambda *a: None) if kind.endswith("_unapproved") else _approve
    kind = kind.removesuffix("_unapproved")
    if kind == "observation":
        _approve(session, SubmissionRecordType.conformer_observation, c.id)
        where = lambda: session.get(ConformerObservation, c.id).conformer_group_id  # noqa: E731
    elif kind == "thermo":
        thermo = _target_thermo(session, entry, younger, [calc_c])
        _approve(session, SubmissionRecordType.thermo, thermo.id)
        thermo_id = thermo.id
        where = lambda: session.get(Thermo, thermo_id).target_conformer_group_id  # noqa: E731
    elif kind == "statmech_declaration":
        statmech, declaration = _basin_declaration(session, entry, younger, calc_c)
        approve(session, SubmissionRecordType.statmech, statmech.id)
        declaration_id = declaration.id
        where = lambda: session.get(StatmechModelDeclaration, declaration_id).conformer_group_id  # noqa: E731
    elif kind == "ensemble_member":
        owner = make_statmech(session, species_entry=entry)
        cited = make_statmech(session, species_entry=entry)
        attach_statmech_source_calculation(session, statmech=cited, calculation=calc_c)
        _declaration, member = _ensemble_member(session, entry, owner, younger, cited)
        approve(session, SubmissionRecordType.statmech, owner.id)
        member_id = member.id
        where = lambda: session.get(StatmechModelMember, member_id).conformer_group_id  # noqa: E731
    elif kind == "kinetics_selection":
        selection = ConformerSelection(conformer_group_id=younger_id, selection_kind="lowest_energy")
        session.add(selection)
        species = entry.species
        partner = make_species(session, smiles="C", inchi_key=next_inchi_key("PAIR"))
        partner_entry = make_species_entry(session, partner)
        reaction = make_chem_reaction(session, reactants=[species], products=[partner])
        reaction_entry = make_reaction_entry(
            session, reaction=reaction, reactant_entries=[entry], product_entries=[partner_entry]
        )
        kinetics = make_kinetics(session, reaction_entry=reaction_entry)
        statmech = make_statmech(session, species_entry=entry)
        session.flush()
        session.add(
            KineticsInterpretationAssignment(
                kinetics_id=kinetics.id,
                subject_key="reactant:1",
                role="reactant",
                statmech_id=statmech.id,
                conformer_selection_id=selection.id,
                ensemble_policy="lowest_energy_conformer",
                standard_state_convention="ideal_gas_1_bar",
                degeneracy_interpretation="no_symmetry_treatment",
            )
        )
        session.flush()
        _approve(session, SubmissionRecordType.kinetics, kinetics.id)
        selection_id = selection.id
        where = lambda: session.get(ConformerSelection, selection_id).conformer_group_id  # noqa: E731
    else:  # pragma: no cover
        raise AssertionError(kind)
    session.flush()
    return entry, older_id, younger_id, observations, where


@pytest.mark.parametrize(
    "kind",
    [
        "observation",
        "thermo",
        "statmech_declaration",
        "ensemble_member",
        "kinetics_selection",
        # Not approved, but the database refuses every UPDATE of a statmech basin declaration or member without a
        # declared repair, which the API role cannot make: the test is "would the trigger refuse", not "is it approved".
        "statmech_declaration_unapproved",
        "ensemble_member_unapproved",
    ],
)
def test_a_bridge_over_an_approved_record_is_owed_and_the_owner_job_completes_it(db_session, kind: str) -> None:
    from app.services.conformer_basin_store import entries_with_pending_merges
    from tests.services.test_conformer_basin_linkage import _BUTANE, butane, deposit_observations

    session = db_session
    entry, older_id, younger_id, observations, where = _two_groups_then_what_points_at_the_younger(session, kind)
    entry_id = entry.id
    for group_id in (older_id, younger_id):
        ensure_record_review(session, record_type=SubmissionRecordType.conformer_group, record_id=group_id)
        set_record_review_status(
            session, record_type=SubmissionRecordType.conformer_group, record_id=group_id,
            status=RecordReviewStatus.under_review, actor=_curator(session),
        )

    # The bridging upload runs as the API role, which may not touch the approved record.
    bridge = deposit_observations(session, _BUTANE, [butane("B", 18.0)])["B"]
    session.expire_all()
    assert session.get(ConformerObservation, bridge).conformer_group_id == older_id, "it joins the OLDEST linked group"
    assert session.get(ConformerObservation, observations["C"]).conformer_group_id == younger_id, "the merge is owed"
    assert where() == younger_id, "the approved record has not moved"
    owed = session.scalars(select(ConformerGroupMergePending)).all()
    assert [(p.species_entry_id, p.kept_conformer_group_id, p.absorbed_conformer_group_id, p.conformer_observation_id, p.completed_at)
            for p in owed] == [(entry_id, older_id, younger_id, bridge, None)]
    assert owed[0].reason
    assert session.get(ConformerGroup, younger_id).merged_into_conformer_group_id is None
    assert entries_with_pending_merges(session) == [entry_id]
    # nothing was declared by the API role
    assert session.execute(
        text("SELECT count(*) FROM accepted_science_repair WHERE xact_id = (pg_current_xact_id())::text::bigint")
    ).scalar() == 0

    # The owner-role job completes it through the declared-repair path.
    report = reassign_conformer_groups(
        session, repair_revision=_REVISION, species_entry_ids=entries_with_pending_merges(session)
    )

    session.expire_all()
    assert where() == older_id
    assert session.get(ConformerObservation, observations["C"]).conformer_group_id == older_id
    assert report.merges_pending_completed == 1 and report.groups_emptied_kept == 1
    assert report.repairs_declared, "an approved row moved, so a repair was declared"
    completed = session.scalars(select(ConformerGroupMergePending)).one()
    assert completed.completed_at is not None and completed.completed_by_revision == _REVISION
    assert entries_with_pending_merges(session) == []
    emptied = session.get(ConformerGroup, younger_id)
    assert emptied.merged_into_conformer_group_id == older_id
    assert emptied.representative_observation_id is None and emptied.representative_fingerprint_json is None
    assert session.get(ConformerGroup, older_id).representative_observation_id == bridge  # the middle structure
    for group_id in (older_id, younger_id):
        assert _review_status(session, group_id) is RecordReviewStatus.not_reviewed
    # every approved row that moved is recorded old -> new
    recorded = session.execute(
        text(
            "SELECT c.target_table, c.before_json, c.after_json FROM accepted_science_repair_change c "
            "JOIN accepted_science_repair r ON r.id = c.repair_id WHERE r.alembic_revision = :r"
        ),
        {"r": _REVISION},
    ).all()
    if kind == "kinetics_selection":
        # a selection is a curation row, not an accepted root: it moves with no repair to record
        assert recorded == []
    else:
        assert recorded, "an approved row moved, and a repair with no recorded change would be unauditable"
    # and a second run finds nothing to do
    again = reassign_conformer_groups(session, repair_revision=_REVISION, species_entry_ids=[entry_id])
    assert again.observations_moved == 0 and again.entries_changed == 0 and not again.repairs_declared


def test_a_live_upload_cannot_move_an_approved_record_itself(db_session) -> None:
    """Without the pending path the merge would be attempted as the API role; the trigger is what refuses it."""
    from app.services.conformer_basin_moves import ReassignmentReport, Repairs, move_dependents

    session = db_session
    entry, older_id, younger_id, observations, where = _two_groups_then_what_points_at_the_younger(session, "thermo")
    repairs = Repairs(session, "live upload", ReassignmentReport(), enabled=False)
    with pytest.raises(DBAPIError):
        with session.begin_nested():
            move_dependents(
                session, [younger_id], target_for=lambda *_: older_id, repairs=repairs,
                report=ReassignmentReport(), scheme_id=_current_scheme_id(session),
            )
    assert where() == younger_id


# ---------------------------------------------------------------------------
# A group row claims at most one basin
# ---------------------------------------------------------------------------


def test_a_row_that_holds_a_basin_does_not_also_claim_a_provisional_one(db_session) -> None:
    """opt 0, sp 18, opt -15, opt -30 all filed under ONE group (the old way). The optimised three are one wide
    basin whose medoid is -15; the sp matches nothing the recompute compares it with, so it is a provisional basin of
    its own. The row must hold the optimised basin, not be written a second time with the sp as its representative."""
    from app.db.models.common import CalculationType
    from tests.services.test_conformer_basin_linkage import _BUTANE, _BUTANE_DIHEDRAL

    session = db_session
    geometries = [with_dihedral(_BUTANE, _BUTANE_DIHEDRAL, d) for d in (0.0, 18.0, -15.0, -30.0)]
    entry, (group,), observations, calculations = _seed(session, _BUTANE, geometries, [0, 0, 0, 0])
    calculations[1].type = CalculationType.sp
    session.flush()
    group_id, sp_id, medoid_id = group.id, observations[1].id, observations[2].id

    first = reassign_conformer_groups(session, repair_revision=_REVISION, species_entry_ids=[entry.id])

    session.expire_all()
    groups = _groups(session, entry)
    assert len(groups) == 2
    holder = session.get(ConformerGroup, group_id)
    assert holder.representative_observation_id == medoid_id
    assert holder.basin_is_clique is False  # the wide flag is kept
    assert session.get(ConformerObservation, sp_id).conformer_group_id != group_id
    sp_group = session.get(ConformerGroup, session.get(ConformerObservation, sp_id).conformer_group_id)
    assert sp_group.representative_observation_id == sp_id and sp_group.basin_is_clique is None
    assert first.groups_created == 1

    second = reassign_conformer_groups(session, repair_revision=_REVISION, species_entry_ids=[entry.id])
    assert second.entries_changed == 0, second.summary()


# ---------------------------------------------------------------------------
# A declaration already in force in the transaction must cover what reassignment writes
# ---------------------------------------------------------------------------


def _approved_thermo_on_the_gauche_basin(session):
    entry, (group,), (trans, gauche), (calc_trans, calc_gauche) = _seed(
        session, _ETHANOL, [_ethanol(180.0), _ethanol(60.0)], [0, 0]
    )
    thermo = _target_thermo(session, entry, group, [calc_gauche])
    _approve(session, SubmissionRecordType.thermo, thermo.id)
    return entry, group, thermo


def test_the_exported_repair_columns_are_exactly_what_reassignment_writes() -> None:
    from app.services.conformer_basin_moves import REASSIGNMENT_REPAIR_COLUMNS

    assert {k: sorted(v) for k, v in REASSIGNMENT_REPAIR_COLUMNS.items()} == {
        "conformer_observation": [
            "assignment_scheme_id", "conformer_group_id", "mapped_coords_json", "torsion_fingerprint_json",
        ],
        "statmech_model_declaration": ["conformer_group_id", "content_hash"],
        "statmech_model_member": ["conformer_group_id"],
        "thermo": ["target_conformer_group_id"],
    }


def test_a_narrower_declaration_already_in_force_is_refused_by_name(db_session) -> None:
    from app.services.conformer_basin_moves import RepairDeclarationError

    session = db_session
    entry, group, thermo = _approved_thermo_on_the_gauche_basin(session)
    thermo_id, group_id = thermo.id, group.id
    # a revision that ran earlier in this transaction declared the table for ITS columns only
    _declare_statmech_repair(session, "thermo", ("species_entry_id",))
    with pytest.raises(RepairDeclarationError, match="target_conformer_group_id"):
        with session.begin_nested():
            reassign_conformer_groups(session, repair_revision=_REVISION, species_entry_ids=[entry.id])
    session.expire_all()
    assert session.get(Thermo, thermo_id).target_conformer_group_id == group_id  # nothing was written


def test_a_declaration_covering_a_superset_of_the_columns_is_used(db_session) -> None:
    session = db_session
    entry, group, thermo = _approved_thermo_on_the_gauche_basin(session)
    thermo_id, group_id = thermo.id, group.id
    _declare_statmech_repair(session, "thermo", ("species_entry_id", "target_conformer_group_id"))
    reassign_conformer_groups(session, repair_revision=_REVISION, species_entry_ids=[entry.id])
    session.expire_all()
    assert session.get(Thermo, thermo_id).target_conformer_group_id != group_id
