"""Shared plumbing for the legacy entity reads (``GET /api/v1/{thermo,kinetics,calculations,...}``).

These routes are the raw entity layer: one row per record, internal ids, a stable ``id`` ascending order. They sit
beside the scientific reads (``/api/v1/scientific/*``), not under them, and they used to ignore every visibility rule
the scientific reads obey. This module gives them the same three:

* **Default visibility.** Rejected and deprecated records are hidden unless ``include_rejected`` /
  ``include_deprecated`` is set (:class:`LegacyVisibility`), by the same function the scientific reads use
  (:func:`~app.services.scientific_read.common.visible_statuses`).
* **The read profile.** ``?profile=curated`` raises the floor to ``approved`` through that same function, and is
  resolved by the same dependency as on the scientific surface. A record below the floor is absent: from a list, from
  a count, and from its own detail route, where it answers exactly as an id that names nothing (decision D6, hidden ==
  unknown == absent).
* **Disclosure.** A list under the exploratory profile reports how many records each default exclusion hid
  (``hidden_by_default``). Under ``curated`` it reports nothing about records below the floor.

The order of a list is not the browse display order. It is ``id`` ascending, said so in the response
(:data:`ADMIN_LISTING_ORDER`).
"""

from __future__ import annotations

import functools
import inspect
from typing import Any, Generic, TypeVar

from fastapi import Depends, Query, Request, Response
from fastapi.routing import APIRoute
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.api.errors import NotFoundError
from app.api.routes._pagination import PaginatedResponse
from app.db.models.common import ProfileRecommendation, ReadProfile, RecordReviewStatus, SubmissionRecordType
from app.db.models.record_review import RecordReview
from app.schemas.reads.scientific_common import (
    HiddenByDefault,
    OrderEcho,
    OrderKeyEcho,
)
from app.services.scientific_read.common import ids_below_review_floor, visible_statuses
from app.services.scientific_read.handles import status_hidden_under_profile
from app.services.scientific_read.hidden_by_default import hidden_by_default_block
from app.services.scientific_read.profile import current_read_profile
from app.services.scientific_read.ref_guard import current_ref_guard_context
from app.services.scientific_read.sql_review import join_review, visible_review_filter

T = TypeVar("T")

ADMIN_LISTING_STATEMENT = (
    "Administrative listing: raw entity rows in stable id order. This is not the browse display order "
    "(review status, then deposition time), and it carries no scientific preference. Use the scientific reads for "
    "browsing, and their select endpoints to choose one record."
)

#: The order of every legacy entity list.
ADMIN_LISTING_ORDER = OrderEcho(
    keys=[
        OrderKeyEcho(
            key="id",
            direction="asc",
            meaning="administrative listing order: the row's internal id, lowest first; no scientific meaning",
        )
    ],
    statement=ADMIN_LISTING_STATEMENT,
)


class EntityListResponse(PaginatedResponse[T], Generic[T]):
    """A page of a legacy entity list: the items, and what the response applied."""

    #: Always the administrative ``id`` ascending order.
    order: OrderEcho = ADMIN_LISTING_ORDER
    #: The read profile this list answered under, and whether TCKDB endorses what it lists.
    profile: ReadProfile = ReadProfile.exploratory
    profile_recommendation: ProfileRecommendation = ProfileRecommendation.none
    #: Exploratory only: how many records each default exclusion hid, and how to include them. ``None`` when nothing
    #: was excluded by default, and always under ``curated`` (no count of records below the floor, decision D6).
    hidden_by_default: HiddenByDefault | None = None


class LegacyVisibility:
    """The ``include_rejected`` / ``include_deprecated`` query parameters of a legacy read and what they mean."""

    def __init__(
        self,
        include_rejected: bool = Query(False, description="Include rejected records (hidden by default)."),
        include_deprecated: bool = Query(False, description="Include deprecated records (hidden by default)."),
    ):
        self.include_rejected = include_rejected
        self.include_deprecated = include_deprecated

    @property
    def statuses(self) -> set[RecordReviewStatus]:
        """The review statuses this request shows. The curated floor narrows it (and overrides the opt-ins)."""
        return visible_statuses(
            min_review_status=None,
            include_rejected=self.include_rejected,
            include_deprecated=self.include_deprecated,
        )

    def restrict(self, stmt: Any, id_column: Any, record_type: SubmissionRecordType) -> Any:
        """``stmt`` limited to the rows of ``record_type`` this request shows (a missing review row is ``not_reviewed``)."""
        stmt, review = join_review(stmt, id_column, record_type)
        return stmt.where(visible_review_filter(review, self.statuses))

    def hidden(self, session: Session, record_type: SubmissionRecordType, ids: Any) -> HiddenByDefault | None:
        """What the default hid among ``ids`` (a ``select`` of the matched ids): one statement, or none."""
        return hidden_by_default_block(
            session,
            record_type=record_type,
            ids=ids,
            include_rejected=self.include_rejected,
            include_deprecated=self.include_deprecated,
        )


HIDDEN_HEADER = "X-TCKDB-Hidden-By-Default"


def disclose_in_header(response: Response, block: HiddenByDefault | None) -> None:
    """A list that is a bare JSON array has no envelope to carry ``hidden_by_default``; it goes in a header.

    ``rejected=1; deprecated=2``, one entry per default exclusion. Absent when nothing was excluded by default and
    always absent under ``curated`` (no count of records below the floor).
    """
    if block is None:
        return
    response.headers[HIDDEN_HEADER] = "; ".join(f"{k}={n}" for k, n in sorted(block.counts.items()))


def entity_page(
    items: list[Any],
    *,
    total: int,
    skip: int,
    limit: int,
    hidden: HiddenByDefault | None = None,
) -> EntityListResponse:
    """The response of a legacy list, stamped with the request's profile."""
    resolved = current_read_profile()
    return EntityListResponse(
        items=items,
        total=total,
        skip=skip,
        limit=limit,
        profile=resolved.profile,
        profile_recommendation=resolved.recommendation,
        hidden_by_default=hidden,
    )


def ensure_not_below_floor(
    session: Session, record_type: SubmissionRecordType, record_id: int, kind_label: str
) -> None:
    """Answer 404 for a record the active profile's review floor hides: exactly as an id that names nothing.

    No-op without a floor (exploratory), so the default behaviour of a detail read is unchanged.
    """
    if current_read_profile().review_floor is None:
        return
    status = session.scalar(
        select(RecordReview.status).where(RecordReview.record_type == record_type, RecordReview.record_id == record_id)
    )
    if status_hidden_under_profile(status if status is not None else RecordReviewStatus.not_reviewed):
        raise NotFoundError(f"{kind_label} not found")


_LINK_FIELD = "calculation_id"
_EDGE_FIELDS = ("parent_calculation_id", "child_calculation_id")


def _models_below(node: Any, found: set[int]) -> None:
    """Collect every calculation id a response tree names (a link's ``calculation_id``, an edge's ends, any
    ``*_calculation_id`` column)."""
    if isinstance(node, BaseModel):
        for name in type(node).model_fields:
            value = getattr(node, name, None)
            if name.endswith("calculation_id") and isinstance(value, int):
                found.add(value)
            else:
                _models_below(value, found)
    elif isinstance(node, (list, tuple)):
        for item in node:
            _models_below(item, found)


def _strip(node: Any, hidden: set[int], *, is_item: bool = False) -> Any:
    """Remove what names a hidden calculation: a list element that links one is dropped, a nullable column becomes
    ``None``. Returns the node (lists are rebuilt)."""
    if isinstance(node, BaseModel):
        for name in type(node).model_fields:
            value = getattr(node, name, None)
            if name.endswith("calculation_id") and isinstance(value, int):
                if value in hidden and name not in (_LINK_FIELD, *_EDGE_FIELDS):
                    setattr(node, name, None)
            elif isinstance(value, (list, tuple)):
                setattr(node, name, _strip(list(value), hidden))
            elif isinstance(value, BaseModel):
                _strip(value, hidden)
        return node
    if isinstance(node, list):
        kept = []
        for item in node:
            if isinstance(item, BaseModel):
                ids = {getattr(item, f) for f in (_LINK_FIELD, *_EDGE_FIELDS) if isinstance(getattr(item, f, None), int)}
                if ids & hidden:
                    continue
            kept.append(_strip(item, hidden))
        return kept
    return node


def withhold_hidden_calculations(session: Session, payload: Any) -> Any:
    """Under a review floor, remove from a legacy response every mention of a calculation the floor hides.

    A legacy payload names related calculations by integer id (a thermo's source calculations, a statmech torsion's
    scan, a dependency edge). A reader who cannot open a calculation must not learn it exists from one that cites it
    (decision D6), so the link is dropped, or a nullable column blanked. One statement for the whole response; none
    without a floor or when the response names no calculation. Only ever removes.
    """
    if current_read_profile().review_floor is None:
        return payload
    found: set[int] = set()
    items = getattr(payload, "items", None)
    _models_below(payload if items is None else items, found)
    if not found:
        return payload
    hidden = ids_below_review_floor(session, record_type=SubmissionRecordType.calculation, record_ids=found)
    if not hidden:
        return payload
    if items is not None:
        payload.items = _strip(list(items), hidden)
        return payload
    return _strip(payload, hidden)


def _withholding(endpoint: Any) -> Any:
    @functools.wraps(endpoint)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        result = endpoint(*args, **kwargs)
        context = current_ref_guard_context()
        if context is None or current_read_profile().review_floor is None:
            return result
        return withhold_hidden_calculations(context.session, result)

    return wrapper


class LegacyReadRoute(APIRoute):
    """Route class of the legacy entity routers: a GET's response is stripped of mentions of hidden calculations.

    Declared once on the router, so a route added to it tomorrow is covered the day it exists.
    """

    def __init__(self, path: str, endpoint: Any, **kwargs: Any) -> None:
        methods = {m.upper() for m in (kwargs.get("methods") or [])}
        if "GET" in methods and not inspect.iscoroutinefunction(endpoint):
            endpoint = _withholding(endpoint)
        super().__init__(path, endpoint, **kwargs)


def gate_path_record(param: str, record_type: SubmissionRecordType, kind_label: str):
    """A route dependency: the path parameter ``param`` names a record the profile hides -> 404, as for an unknown id.

    Attach it to the router that owns the path parameter, so every route under ``/{param}/...`` is gated by the one
    declaration and a route added there tomorrow is gated from the day it exists.
    """

    def dependency(request: Request, session: Session = Depends(get_db)) -> None:
        raw = request.path_params.get(param)
        if raw is None:
            return
        try:
            record_id = int(raw)
        except (TypeError, ValueError):
            return
        ensure_not_below_floor(session, record_type, record_id, kind_label)

    dependency.__name__ = f"gate_{param}"
    return dependency


__all__ = [
    "ADMIN_LISTING_ORDER",
    "EntityListResponse",
    "LegacyVisibility",
    "LegacyReadRoute",
    "disclose_in_header",
    "ensure_not_below_floor",
    "entity_page",
    "gate_path_record",
    "withhold_hidden_calculations",
]
