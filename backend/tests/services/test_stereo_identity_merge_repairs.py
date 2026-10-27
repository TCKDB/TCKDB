"""The merge step and the conformer reassignment it calls share one repair declaration per table.

The database allows a single ``accepted_science_repair`` declaration per table per transaction. The merge step
declares ``thermo`` for ``species_entry_id``; ``reassign_conformer_groups`` may then need
``thermo.target_conformer_group_id`` on the same table. If the first declaration named only its own columns, the
second would be skipped and the write refused. The declaration has to be the union.
"""

from __future__ import annotations

from sqlalchemy import text

from app.db.models.common import SubmissionRecordType
from app.db.models.species import ConformerObservation
from app.db.models.thermo import Thermo
from app.services.conformer_reassignment import reassign_conformer_groups
from app.services.stereo_identity_merge import MergeReport, _Catalogue, _plan_repairs, _Repairs
from tests.services.test_conformer_reassignment import (
    _ETHANOL,
    _approve,
    _ethanol,
    _seed,
    _target_thermo,
)

_REVISION = "test-stereo-merge-union"


def test_an_approved_thermo_target_moves_after_the_merge_step_declared_thermo(db_session) -> None:
    session = db_session
    entry, (group,), (_trans, gauche), (_calc_trans, calc_gauche) = _seed(
        session, _ETHANOL, [_ethanol(180.0), _ethanol(60.0)], [0, 0]
    )
    thermo = _target_thermo(session, entry, group, [calc_gauche])
    _approve(session, SubmissionRecordType.conformer_observation, gauche.id)
    _approve(session, SubmissionRecordType.thermo, thermo.id)
    group_id, gauche_id, thermo_id = group.id, gauche.id, thermo.id

    # What the merge step does first: plan, then declare ``thermo`` before it re-points ``species_entry_id``.
    report = MergeReport()
    catalogue = _Catalogue(session)
    repairs = _Repairs(session, _REVISION, report, catalogue)
    _plan_repairs(catalogue, repairs)
    repairs.ensure("thermo")

    reassign_conformer_groups(session, repair_revision=_REVISION, species_entry_ids=[entry.id])

    session.expire_all()
    new_id = session.get(ConformerObservation, gauche_id).conformer_group_id
    assert new_id != group_id
    assert session.get(Thermo, thermo_id).target_conformer_group_id == new_id
    declarations = session.execute(
        text("SELECT declared_columns FROM accepted_science_repair WHERE alembic_revision = :r AND target_table = 'thermo'"),
        {"r": _REVISION},
    ).all()
    assert len(declarations) == 1
    assert {"species_entry_id", "target_conformer_group_id"} <= set(declarations[0].declared_columns)
