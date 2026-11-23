"""The DB-backed machine-review switches and the deployment-level off."""

from __future__ import annotations

import pytest
from sqlalchemy import text

from app.db.models.common import AppUserRole
from app.services.machine_review.switches import (
    MachineReviewSwitchForbidden,
    read_machine_review_switches,
    write_machine_review_switches,
)
from tests.services._machine_review_job_support import CLOUD_SETTINGS, FAKE_SETTINGS, OFF_SETTINGS, make_user


def test_defaults_are_on_when_no_row_exists(db_session):
    db_session.execute(text("DELETE FROM machine_review_settings"))

    switches = read_machine_review_switches(db_session, FAKE_SETTINGS)

    assert (switches.machine_review_enabled, switches.curator_requests_enabled) == (True, True)
    assert switches.review_allowed and switches.curator_allowed


def test_admin_write_persists_and_reads_back(db_session):
    admin = make_user(db_session, "sw-admin", AppUserRole.admin)

    write_machine_review_switches(db_session, actor=admin, curator_requests_enabled=False)

    switches = read_machine_review_switches(db_session, FAKE_SETTINGS)
    assert switches.machine_review_enabled is True
    assert switches.curator_requests_enabled is False
    assert switches.review_allowed and not switches.curator_allowed
    row = db_session.execute(text("SELECT updated_by FROM machine_review_settings")).scalar_one()
    assert row == admin.id

    write_machine_review_switches(db_session, actor=admin, machine_review_enabled=False)
    again = read_machine_review_switches(db_session, FAKE_SETTINGS)
    assert (again.machine_review_enabled, again.curator_requests_enabled) == (False, False)


def test_curator_write_is_refused_and_admin_twin_passes(db_session):
    curator = make_user(db_session, "sw-curator", AppUserRole.curator)
    admin = make_user(db_session, "sw-admin2", AppUserRole.admin)

    with pytest.raises(MachineReviewSwitchForbidden):
        write_machine_review_switches(db_session, actor=curator, machine_review_enabled=False)
    assert read_machine_review_switches(db_session, FAKE_SETTINGS).machine_review_enabled is True

    write_machine_review_switches(db_session, actor=admin, machine_review_enabled=False)
    assert read_machine_review_switches(db_session, FAKE_SETTINGS).machine_review_enabled is False


def test_mode_off_overrides_enabled_switches_and_cloud_twin_allows(db_session):
    admin = make_user(db_session, "sw-admin3", AppUserRole.admin)
    write_machine_review_switches(db_session, actor=admin, machine_review_enabled=True, curator_requests_enabled=True)

    off = read_machine_review_switches(db_session, OFF_SETTINGS)
    assert off.machine_review_enabled and off.curator_requests_enabled
    assert off.mode_off and not off.review_allowed and not off.curator_allowed

    cloud = read_machine_review_switches(db_session, CLOUD_SETTINGS)
    assert cloud.review_allowed and cloud.curator_allowed
