"""Coverage for the software/workflow-tool-release backfill inside
revision ``b6d80e36dcec`` (correction-scheme-provenance plan, owner's
backfill ruling).

Calls ``_backfill_software_and_workflow_tool_release`` directly against a
plain connection (imported from the revision file via ``importlib`` --
the established pattern for this kind of proof, see
``test_scan_dihedral_axis_correction_migration.py``) rather than driving
it through ``alembic.command.upgrade``/``downgrade``. That is what makes
the idempotency and non-overwrite cases provable at all: alembic will not
replay an already-applied revision, so there is no way to call the data
step a second time against the same row through the command layer. The
function itself is what ``upgrade()`` calls, unmodified -- this is not a
reimplementation of the logic under a different name.

The safety condition under test, stated in the revision's own docstring:
for a scheme's level of theory, set ``software_id``/
``workflow_tool_release_id`` only when the calculations recorded at that
level agree on *exactly one* distinct value (workflow-tool-release counts
only the calculations that recorded one at all); zero or several, leave
``NULL``. Never overwrite a value already on the row. Idempotent.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models.energy_correction import EnergyCorrectionScheme
from tests.services.scientific_read._factories import (
    make_calculation,
    make_energy_correction_scheme,
    make_lot,
    make_software,
    make_software_release,
    make_species,
    make_species_entry,
    make_workflow_tool_release,
)

_REVISION_FILE = (
    Path(__file__).resolve().parents[2]
    / "alembic"
    / "versions"
    / "b6d80e36dcec_energy_correction_scheme_software_and_.py"
)


def _load_backfill_fn():
    spec = importlib.util.spec_from_file_location(
        "_ecs_provenance_revision", _REVISION_FILE
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module._backfill_software_and_workflow_tool_release


def _make_species_entry(session):
    sp = make_species(session)
    return make_species_entry(session, species=sp)


# ---------------------------------------------------------------------------
# Software: unambiguous case
# ---------------------------------------------------------------------------


def test_backfills_software_when_calculations_at_the_lot_agree(db_conn) -> None:
    backfill = _load_backfill_fn()
    with Session(db_conn) as session, session.begin():
        lot = make_lot(session)
        gaussian_release = make_software_release(session, name="gaussian", version="16")
        se = _make_species_entry(session)
        make_calculation(
            session, species_entry_id=se.id, lot_id=lot.id,
            software_release_id=gaussian_release.id,
        )
        make_calculation(
            session, species_entry_id=se.id, lot_id=lot.id,
            software_release_id=gaussian_release.id,
        )
        scheme = make_energy_correction_scheme(
            session, kind="atom_energy", lot=lot
        )
        assert scheme.software_id is None

        backfill(db_conn)
        session.expire(scheme)

        assert scheme.software_id == gaussian_release.software_id


def test_ambiguous_software_at_the_lot_stays_null(db_conn) -> None:
    """Two calculations at the same LOT, two different softwares: the
    safety condition this backfill exists for."""
    backfill = _load_backfill_fn()
    with Session(db_conn) as session, session.begin():
        lot = make_lot(session)
        gaussian_release = make_software_release(session, name="gaussian", version="16")
        orca_release = make_software_release(session, name="orca", version="5.0")
        se = _make_species_entry(session)
        make_calculation(
            session, species_entry_id=se.id, lot_id=lot.id,
            software_release_id=gaussian_release.id,
        )
        make_calculation(
            session, species_entry_id=se.id, lot_id=lot.id,
            software_release_id=orca_release.id,
        )
        scheme = make_energy_correction_scheme(
            session, kind="atom_energy", lot=lot
        )

        backfill(db_conn)
        session.expire(scheme)

        assert scheme.software_id is None


def test_non_software_scoped_kind_is_never_touched(db_conn) -> None:
    """atom_hf/atom_thermal/soc: the software axis does not apply, even
    if (unrealistically) a level of theory were attached."""
    backfill = _load_backfill_fn()
    with Session(db_conn) as session, session.begin():
        lot = make_lot(session)
        gaussian_release = make_software_release(session, name="gaussian", version="16")
        se = _make_species_entry(session)
        make_calculation(
            session, species_entry_id=se.id, lot_id=lot.id,
            software_release_id=gaussian_release.id,
        )
        scheme = make_energy_correction_scheme(
            session, kind="atom_hf", lot=lot
        )

        backfill(db_conn)
        session.expire(scheme)

        assert scheme.software_id is None


# ---------------------------------------------------------------------------
# Workflow-tool-release: unanimous-among-recorded case
# ---------------------------------------------------------------------------


def test_backfills_workflow_tool_release_when_the_recorded_minority_agrees(
    db_conn,
) -> None:
    """Most calculations at a LOT recorded no release at all; the ones
    that did all agree -- still unambiguous."""
    backfill = _load_backfill_fn()
    with Session(db_conn) as session, session.begin():
        lot = make_lot(session)
        release = make_software_release(session, name="gaussian", version="16")
        wtr = make_workflow_tool_release(session, name="arc", version="1.1.0")
        se = _make_species_entry(session)
        # Eight calculations with no recorded workflow-tool-release...
        for _ in range(8):
            make_calculation(
                session, species_entry_id=se.id, lot_id=lot.id,
                software_release_id=release.id,
            )
        # ...and two that agree on the same release.
        for _ in range(2):
            make_calculation(
                session, species_entry_id=se.id, lot_id=lot.id,
                software_release_id=release.id,
                workflow_tool_release_id=wtr.id,
            )
        scheme = make_energy_correction_scheme(
            session, kind="bac_petersson", lot=lot
        )

        backfill(db_conn)
        session.expire(scheme)

        assert scheme.workflow_tool_release_id == wtr.id


def test_ambiguous_workflow_tool_release_stays_null(db_conn) -> None:
    backfill = _load_backfill_fn()
    with Session(db_conn) as session, session.begin():
        lot = make_lot(session)
        release = make_software_release(session, name="gaussian", version="16")
        wtr_a = make_workflow_tool_release(session, name="arc", version="1.1.0")
        wtr_b = make_workflow_tool_release(session, name="arc", version="1.2.0")
        se = _make_species_entry(session)
        make_calculation(
            session, species_entry_id=se.id, lot_id=lot.id,
            software_release_id=release.id, workflow_tool_release_id=wtr_a.id,
        )
        make_calculation(
            session, species_entry_id=se.id, lot_id=lot.id,
            software_release_id=release.id, workflow_tool_release_id=wtr_b.id,
        )
        scheme = make_energy_correction_scheme(
            session, kind="bac_petersson", lot=lot
        )

        backfill(db_conn)
        session.expire(scheme)

        assert scheme.workflow_tool_release_id is None


# ---------------------------------------------------------------------------
# Non-overwrite + idempotency
# ---------------------------------------------------------------------------


def test_never_overwrites_an_already_set_software_id(db_conn) -> None:
    backfill = _load_backfill_fn()
    with Session(db_conn) as session, session.begin():
        lot = make_lot(session)
        gaussian_release = make_software_release(session, name="gaussian", version="16")
        orca = make_software(session, name="orca")
        se = _make_species_entry(session)
        make_calculation(
            session, species_entry_id=se.id, lot_id=lot.id,
            software_release_id=gaussian_release.id,
        )
        scheme = make_energy_correction_scheme(
            session, kind="atom_energy", lot=lot
        )
        # Simulate a value already recorded by another path (admin
        # attach-provenance, an earlier backfill, a real upload) --
        # deliberately the *other* software from what the calculations
        # at this LOT would derive.
        scheme.software_id = orca.id
        session.flush()

        backfill(db_conn)
        session.expire(scheme)

        assert scheme.software_id == orca.id


def test_backfill_is_idempotent(db_conn) -> None:
    backfill = _load_backfill_fn()
    with Session(db_conn) as session, session.begin():
        lot = make_lot(session)
        gaussian_release = make_software_release(session, name="gaussian", version="16")
        wtr = make_workflow_tool_release(session, name="arc", version="1.1.0")
        se = _make_species_entry(session)
        make_calculation(
            session, species_entry_id=se.id, lot_id=lot.id,
            software_release_id=gaussian_release.id, workflow_tool_release_id=wtr.id,
        )
        scheme = make_energy_correction_scheme(
            session, kind="atom_energy", lot=lot
        )

        backfill(db_conn)
        session.expire(scheme)
        first_software = scheme.software_id
        first_wtr = scheme.workflow_tool_release_id
        assert first_software is not None
        assert first_wtr is not None

        # Second call: nothing left NULL to touch, so nothing changes,
        # and -- since the UPDATE only ever targets NULL columns -- no
        # row is duplicated or re-written.
        backfill(db_conn)
        session.expire(scheme)

        assert scheme.software_id == first_software
        assert scheme.workflow_tool_release_id == first_wtr
        count = session.scalar(
            select(func.count())
            .select_from(EnergyCorrectionScheme)
            .where(EnergyCorrectionScheme.id == scheme.id)
        )
        assert count == 1
