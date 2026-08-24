"""Curated vs exploratory read profiles for the ``/scientific/*`` surface.

One knob, ``?profile=``, selects which *contract* a scientific read answers
under. See ``backend/docs/specs/dataset_release_and_profiles.md``.

Why ``exploratory`` is the default
----------------------------------
Every record on the deployed database is currently ``not_reviewed``. A curated
default would return empty result sets and read as a broken database, so
``exploratory`` is the default and ``curated`` is opt-in. The miscitation risk
that creates is mitigated by *disclosure*, not by narrowing: the resolved
profile and an explicit recommendation token are echoed in **every** scientific
response and in every dataset manifest, and ``exploratory`` says in
machine-readable form that TCKDB recommends nothing.

How the resolved profile reaches the service layer
--------------------------------------------------
Threading a ``profile`` argument through 65 route signatures and ~40 service
entry points would guarantee that some endpoint silently forgets it — and a
profile that only some endpoints honour is worse than no profile at all,
because it teaches consumers to trust it. Instead the profile is resolved
exactly once, in an ``async`` FastAPI dependency registered on the whole
``scientific_router`` (``app/api/routes/scientific/_profile.py``), published
into the :data:`_CURRENT_PROFILE` context variable, and consumed at exactly two
documented seams:

1. :func:`app.services.scientific_read.common.visible_statuses` — the single
   function every read service already calls to decide which review statuses
   are visible. ``curated`` raises the floor to ``approved`` there, so it
   cannot be forgotten by an endpoint.
2. :func:`app.services.scientific_read.internal_ids.apply_internal_ids_visibility`
   — the one function every enveloped scientific route returns through, which
   calls :func:`stamp_read_profile` to write the resolved profile into the
   response envelope's ``request`` echo.

Non-enveloped endpoints (the streaming exports and ``/meta/*``) take the
profile explicitly, because a streaming generator can outlive the dependency's
context and must capture the value up front.

The dependency is ``async`` deliberately: FastAPI runs *sync* endpoints in a
worker thread whose context is **copied** from the request task. A context
variable set in an async dependency is therefore visible to the sync endpoint
and everything it calls, whereas one set inside a sync dependency would be set
in a different, discarded context copy.

Outside a request (unit tests, workers, CLI scripts) the context variable is
unset and everything falls back to ``exploratory`` — i.e. exactly the
pre-Stage-3 behaviour.
"""

from __future__ import annotations

from contextvars import ContextVar, Token
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.db.models.common import (
    ProfileRecommendation,
    ReadProfile,
    RecordReviewStatus,
)
from app.db.models.dataset_release import DatasetRelease

# The review floor the curated profile imposes. Records below ``approved`` are
# by definition not something TCKDB stands behind.
CURATED_REVIEW_FLOOR = RecordReviewStatus.approved


@dataclass(frozen=True)
class ResolvedReadProfile:
    """The read contract in force for one request.

    ``recommendation`` is deliberately *not* a restatement of ``profile``, and
    on the general read surface it is never ``tckdb_curated_release``.

    That token means "an attributed ``release_selection`` names *these
    records*". No per-record selection annotation exists anywhere in the read
    services, and the release backing a curated read was resolved per
    *database* (the newest published release), not per record — so emitting it
    claimed an endorsement for records no curator had ever looked at, including
    ones a curator had explicitly passed over. The honest token for "the
    approval floor was applied and nothing further is claimed" is
    ``approved_floor_only``; the real endorsement is served by
    ``/api/v1/scientific/releases/*``, where records are resolved *through* a
    selection.
    """

    profile: ReadProfile
    recommendation: ProfileRecommendation
    release_ref: str | None = None

    @property
    def review_floor(self) -> RecordReviewStatus | None:
        """Minimum review status this profile imposes, if any."""
        if self.profile is ReadProfile.curated:
            return CURATED_REVIEW_FLOOR
        return None

    def echo(self) -> dict[str, object]:
        """The block stamped into every scientific response's ``request`` echo."""
        return {
            "profile": self.profile.value,
            "profile_recommendation": self.recommendation.value,
            "profile_release_ref": self.release_ref,
        }


#: The default contract: every visible candidate, no TCKDB recommendation.
EXPLORATORY = ResolvedReadProfile(
    profile=ReadProfile.exploratory,
    recommendation=ProfileRecommendation.none,
)


_CURRENT_PROFILE: ContextVar[ResolvedReadProfile] = ContextVar(
    "tckdb_current_read_profile", default=EXPLORATORY
)


def current_read_profile() -> ResolvedReadProfile:
    """The profile in force, or :data:`EXPLORATORY` outside a scientific request."""
    return _CURRENT_PROFILE.get()


def set_current_read_profile(resolved: ResolvedReadProfile) -> Token:
    """Publish ``resolved`` for the current context; returns the reset token."""
    return _CURRENT_PROFILE.set(resolved)


def reset_current_read_profile(token: Token) -> None:
    """Restore the profile captured before :func:`set_current_read_profile`."""
    _CURRENT_PROFILE.reset(token)


def stamp_read_profile(payload: object) -> None:
    """Write the request's resolved profile into a response envelope's echo.

    Every scientific response envelope carries a ``request`` block whose model
    subclasses
    :class:`app.schemas.reads.scientific_common.ProfiledRequestEcho`. Calling
    this once, at the single boundary every enveloped route passes through
    (:func:`app.services.scientific_read.internal_ids.apply_internal_ids_visibility`),
    is what makes "the resolved profile is echoed in every response" a
    property of the code rather than a convention 63 service-layer
    construction sites are trusted to follow.

    In-place mutation is deliberate: it happens before serialization, so the
    declared ``response_model`` and the emitted JSON agree, and there is
    exactly one place to audit. Payloads without a ``request`` echo are left
    alone.
    """
    echo = getattr(payload, "request", None)
    if echo is None or not hasattr(echo, "profile"):
        return
    resolved = current_read_profile()
    echo.profile = resolved.profile
    echo.profile_recommendation = resolved.recommendation
    echo.profile_release_ref = resolved.release_ref


class UnknownReleaseError(ValueError):
    """Raised when ``?release=`` names no published release."""


#: The curated read surface is honest about what it does and does not claim.
#: ``CURATED`` applies the approval floor and says exactly that. The
#: release-backed endorsement lives on the release endpoints, which construct
#: their own resolved profile via :func:`release_backed_profile`.
CURATED = ResolvedReadProfile(
    profile=ReadProfile.curated,
    recommendation=ProfileRecommendation.approved_floor_only,
)


def release_backed_profile(release: DatasetRelease) -> ResolvedReadProfile:
    """The one place ``tckdb_curated_release`` may be claimed.

    Used by the release read endpoints, where every record served *was*
    resolved through an attributed selection in ``release``.
    """
    return ResolvedReadProfile(
        profile=ReadProfile.curated,
        recommendation=ProfileRecommendation.tckdb_curated_release,
        release_ref=release.public_ref,
    )


def resolve_read_profile(
    session: Session,
    *,
    profile: ReadProfile,
    release_ref: str | None = None,
) -> ResolvedReadProfile:
    """Resolve the requested profile for a general scientific read.

    ``?release=`` is **rejected** here rather than accepted and ignored.
    Scoping a general product search to the records one release selected is not
    implemented — it cannot be done at the single seam the profile uses, and a
    filter that silently worked on some endpoints and not others would be worse
    than none. Accepting the parameter while doing nothing with it was worse
    still: it read as scoping and was not. Release-scoped reads are served by
    ``/api/v1/scientific/releases/{tag}/selections`` and the release artifacts,
    which answer the question exactly.

    :raises UnknownReleaseError: ``release_ref`` was supplied.
    """
    del session
    if release_ref is not None:
        raise UnknownReleaseError(
            "release_scoping_not_implemented: ?release= is not supported on the "
            "general read surface -- it would silently do nothing here. Use "
            "GET /api/v1/scientific/releases/{tag}/selections, or the release's "
            "selected_records.ndjson artifact, to read exactly what a release "
            "selected."
        )
    if profile is ReadProfile.exploratory:
        return EXPLORATORY
    return CURATED


__all__ = [
    "CURATED",
    "CURATED_REVIEW_FLOOR",
    "EXPLORATORY",
    "ResolvedReadProfile",
    "UnknownReleaseError",
    "current_read_profile",
    "release_backed_profile",
    "reset_current_read_profile",
    "resolve_read_profile",
    "set_current_read_profile",
    "stamp_read_profile",
]
