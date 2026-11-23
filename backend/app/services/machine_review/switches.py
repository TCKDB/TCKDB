"""Admin switches for machine review, stored in one settings row.

The deployment setting ``AI_REVIEW_ASSISTANT_MODE=off`` always wins; the two
database switches only narrow what that setting allows. An absent row means
both switches are on.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.api.config import settings as app_settings
from app.api.errors import DomainError
from app.db.models.app_user import AppUser
from app.db.models.common import AppUserRole
from app.db.models.machine_review_job import MachineReviewSettings

_SETTINGS_ROW_ID = 1


class MachineReviewSwitchForbidden(DomainError):
    """Only an admin may change the machine-review switches."""

    code = "machine_review_switch_forbidden"


@dataclass(frozen=True)
class MachineReviewSwitches:
    """The effective switches: two stored flags and the deployment-level off."""

    machine_review_enabled: bool
    curator_requests_enabled: bool
    mode_off: bool

    @property
    def review_allowed(self) -> bool:
        """Whether any machine review may be requested or run."""
        return not self.mode_off and self.machine_review_enabled

    @property
    def curator_allowed(self) -> bool:
        """Whether a curator may request a review."""
        return self.review_allowed and self.curator_requests_enabled


def read_machine_review_switches(session: Session, settings_obj: Any = app_settings) -> MachineReviewSwitches:
    """Read the stored switches, defaulting to on when no row exists."""
    row = session.scalar(
        select(MachineReviewSettings)
        .where(MachineReviewSettings.id == _SETTINGS_ROW_ID)
        .execution_options(populate_existing=True)
    )
    return MachineReviewSwitches(
        machine_review_enabled=True if row is None else row.machine_review_enabled,
        curator_requests_enabled=True if row is None else row.curator_requests_enabled,
        mode_off=settings_obj.ai_review_assistant_mode == "off",
    )


def write_machine_review_switches(
    session: Session,
    *,
    actor: AppUser,
    machine_review_enabled: bool | None = None,
    curator_requests_enabled: bool | None = None,
) -> None:
    """Set the given switches (``None`` leaves one unchanged); admin only. Flushes, never commits."""
    if actor.role is not AppUserRole.admin:
        raise MachineReviewSwitchForbidden("Only an admin may change the machine-review switches.")
    values: dict[str, Any] = {
        "updated_by": actor.id,
        "updated_at": datetime.now(timezone.utc).replace(tzinfo=None),
    }
    if machine_review_enabled is not None:
        values["machine_review_enabled"] = machine_review_enabled
    if curator_requests_enabled is not None:
        values["curator_requests_enabled"] = curator_requests_enabled
    stmt = pg_insert(MachineReviewSettings).values(id=_SETTINGS_ROW_ID, **values)
    session.execute(stmt.on_conflict_do_update(index_elements=["id"], set_=values))
    session.flush()


__all__ = [
    "MachineReviewSwitchForbidden",
    "MachineReviewSwitches",
    "read_machine_review_switches",
    "write_machine_review_switches",
]
