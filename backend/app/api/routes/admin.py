"""Admin-only management endpoints.

Scope in v1 is intentionally tiny: just role changes.  Everything here
is gated behind the ``admin`` role — curators cannot promote each other.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import get_write_db, require_admin
from app.db.models.app_user import AppUser
from app.db.models.common import AppUserRole

router = APIRouter()


class RoleChangeRequest(BaseModel):
    role: AppUserRole


class UserRoleResponse(BaseModel):
    id: int
    username: str
    role: AppUserRole


@router.patch("/users/{user_id}/role", response_model=UserRoleResponse)
def change_user_role(
    user_id: int,
    request: RoleChangeRequest,
    _admin: AppUser = Depends(require_admin),
    session: Session = Depends(get_write_db),
) -> UserRoleResponse:
    user = session.get(AppUser, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found.")
    user.role = request.role
    session.flush()
    return UserRoleResponse(id=user.id, username=user.username, role=user.role)
