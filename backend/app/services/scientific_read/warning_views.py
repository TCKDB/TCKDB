"""Who may read what of a deposit warning, applied once at the response boundary.

A warning item is built in TCKDB's words (see :mod:`app.services.deposit_warning_messages`). The text the
depositor's own deposit produced rides along privately on each item. This module decides, per request, whether the
reader may see it:

* the deposit's **owner** (the submission's creator) and **curators and admins** get it as ``depositor_message`` and
  ``depositor_field``, the access rule ``GET /jobs/{id}`` uses;
* everyone else, including an authenticated reader who owns nothing, gets neither key.

It also applies the existing ``submission_ref`` rule: the key is served only to an authenticated caller.

The reader is published per request by :func:`app.api.routes.scientific._actor.publish_read_actor`, onto a context
variable, for the same reason the read profile travels that way (the services that build a response do not take the
caller). A route that publishes none reads as anonymous, which is the safe default.
"""

from __future__ import annotations

from contextvars import ContextVar, Token
from dataclasses import dataclass
from typing import Any

from app.db.models.common import AppUserRole

_CURATION_ROLES = frozenset({AppUserRole.curator, AppUserRole.admin})


@dataclass(frozen=True)
class ReadActor:
    user_id: int
    role: AppUserRole


_READ_ACTOR: ContextVar[ReadActor | None] = ContextVar("tckdb_read_actor", default=None)


def set_read_actor(actor: ReadActor | None) -> Token:
    return _READ_ACTOR.set(actor)


def reset_read_actor(token: Token) -> None:
    _READ_ACTOR.reset(token)


def current_read_actor() -> ReadActor | None:
    return _READ_ACTOR.get()


def _may_see_depositor_text(actor: ReadActor | None, owner_id: int | None) -> bool:
    if actor is None:
        return False
    if actor.role in _CURATION_ROLES:
        return True
    return owner_id is not None and owner_id == actor.user_id


def reveal_warning_views(payload: Any) -> None:
    """Fill ``depositor_message``/``depositor_field`` for a permitted reader and drop ``submission_ref`` for an anonymous one."""
    records = getattr(payload, "record_warnings", None)
    deposits = getattr(payload, "deposit_warnings", None)
    if not records and not deposits:
        return
    actor = current_read_actor()
    items = [item for block in records or () for item in block.items]
    for block in deposits or ():
        if actor is None:
            block.submission_ref = None
        items.extend(block.items)
    for item in items:
        if _may_see_depositor_text(actor, item._owner_id):
            item.depositor_message = item._raw_message
            item.depositor_field = item._raw_field
        else:
            item.depositor_message = None
            item.depositor_field = None


__all__ = [
    "ReadActor",
    "current_read_actor",
    "reset_read_actor",
    "reveal_warning_views",
    "set_read_actor",
]
