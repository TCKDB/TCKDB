"""Publish the optional reader of a scientific detail read, for the deposit-warning views.

A route that carries ``record_warnings`` / ``deposit_warnings`` lists this as a route dependency. A credential that is
present and invalid still answers 401 (the rule :func:`app.api.deps.get_optional_current_user` states); a missing one
reads as anonymous. See :mod:`app.services.scientific_read.warning_views` for what the reader may see.

``async`` for the reason the read profile's dependency is: the value is published on a context variable, and a
synchronous dependency would publish it into a worker thread's copy of the context that the endpoint never sees.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from fastapi import Depends, Request

from app.api.credential_vary import STATE_KEY
from app.api.deps import get_optional_current_user
from app.db.models.app_user import AppUser
from app.services.scientific_read.warning_views import ReadActor, reset_read_actor, set_read_actor


async def publish_read_actor(
    request: Request, actor: AppUser | None = Depends(get_optional_current_user)
) -> AsyncIterator[None]:
    # The answer now depends on who asked: keep a shared cache from serving one caller's answer to another.
    setattr(request.state, STATE_KEY, True)
    token = set_read_actor(ReadActor(actor.id, actor.role) if actor is not None else None)
    try:
        yield
    finally:
        reset_read_actor(token)


__all__ = ["publish_read_actor"]
