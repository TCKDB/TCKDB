"""The ``?profile=`` knob, resolved once for the whole scientific surface.

Registered as a router-level dependency on ``scientific_router`` so that:

* every ``/api/v1/scientific/*`` operation declares ``profile`` (and
  ``release``) in the published OpenAPI document — there is no endpoint that
  can quietly not support it; and
* the resolved profile is published exactly once per request, into the context
  variable that :mod:`app.services.scientific_read.profile` owns.

See that module's docstring for why this dependency must be ``async`` and why
the value travels via a context variable rather than 65 route signatures.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from fastapi import Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.db.models.common import ReadProfile
from app.services.scientific_read.profile import (
    ResolvedReadProfile,
    UnknownReleaseError,
    reset_current_read_profile,
    resolve_read_profile,
    set_current_read_profile,
)

_PROFILE_DESCRIPTION = (
    "Read contract. 'exploratory' (default) returns every visible candidate "
    "with its review state and carries no TCKDB recommendation. 'curated' "
    "restricts results to records at or above the 'approved' review floor and "
    "claims nothing further (profile_recommendation='approved_floor_only'); it "
    "does NOT mean a curator selected these records. For records an attributed "
    "release selection actually names, use /api/v1/scientific/releases/*."
)

_RELEASE_DESCRIPTION = (
    "Reserved. Scoping a general read to one dataset release is not "
    "implemented and supplying this parameter is rejected (422) rather than "
    "silently ignored. To read exactly what a release selected, use "
    "GET /api/v1/scientific/releases/{tag}/selections or its "
    "selected_records.ndjson artifact."
)


async def resolve_profile_dependency(
    request: Request,
    profile: ReadProfile = Query(ReadProfile.exploratory, description=_PROFILE_DESCRIPTION),
    release: str | None = Query(None, description=_RELEASE_DESCRIPTION),
    session: Session = Depends(get_db),
) -> AsyncIterator[ResolvedReadProfile]:
    """Resolve, publish, and afterwards unpublish the request's read profile.

    Also stashes the result on ``request.state`` so the response boundary can
    read it without depending on context-variable propagation surviving
    whatever Starlette does between the endpoint returning and the response
    being serialized.

    :raises HTTPException: 422 when ``release`` is supplied — release scoping
        is not implemented on the general read surface, and accepting the
        parameter while ignoring it would be worse than refusing it.
    """
    try:
        resolved = resolve_read_profile(
            session, profile=profile, release_ref=release
        )
    except UnknownReleaseError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    request.state.read_profile = resolved
    token = set_current_read_profile(resolved)
    try:
        yield resolved
    finally:
        reset_current_read_profile(token)


#: Query-string keys this dependency owns. The POST search routes reject
#: unknown query-string keys so a typo can't be silently ignored; they must
#: allow these two through, since the router-level dependency puts them on
#: every operation including the POSTs.
PROFILE_QUERY_KEYS: frozenset[str] = frozenset({"profile", "release"})


__all__ = ["PROFILE_QUERY_KEYS", "resolve_profile_dependency"]
