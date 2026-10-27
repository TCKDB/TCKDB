"""Shared plumbing for the legacy entity reads (``GET /api/v1/{thermo,kinetics,calculations,...}``).

These routes are the raw entity layer: one row per record, internal ids, a stable ``id`` ascending order. They sit
beside the scientific reads (``/api/v1/scientific/*``), not under them, and they used to ignore every visibility rule
the scientific reads obey. This module gives them the same three:

* **Default visibility.** Rejected and deprecated records are hidden unless ``include_rejected`` /
  ``include_deprecated`` is set (:class:`LegacyVisibility`), by the same function the scientific reads use
  (:func:`~app.services.scientific_read.common.visible_statuses`).
* **The read profile.** ``?profile=curated`` raises the floor to ``approved`` through that same function, and is
  resolved by the same dependency as on the scientific surface. The record floor decides which rows a list returns
  and counts. A record's own detail route serves it unless it is rejected or deprecated (ADR 0023 step 7), and a
  withheld one
  answers exactly as an id that names nothing.
* **Linked evidence (ADR 0023).** The floor decides which rows a list returns; a detail route answers 404 for a withheld record only (step 7). What a returned
  row NAMES (a pointer column, an embedded record such as a conformer group's observations) is linked evidence: under
  ``curated`` only a rejected or deprecated record is blanked or dropped, and one that is merely below the floor is named as
  it is under the default profile. These payloads carry raw ids and no badge field, so the response says the badge in the
  ``X-TCKDB-Review-Status`` header (:data:`REVIEW_STATUS_HEADER`). An id-typed filter naming a withheld record is rewritten to name
  nothing; one naming an unreviewed record is kept (a filter is not a subject).
* **Disclosure.** A list under the exploratory profile reports how many records each default exclusion hid
  (``hidden_by_default``). Under ``curated`` it reports nothing about records below the floor.

The order of a list is not the browse display order. It is ``id`` ascending, said so in the response
(:data:`ADMIN_LISTING_ORDER`).
"""

from __future__ import annotations

import functools
import inspect
import re
from dataclasses import dataclass
from typing import Any, Generic, TypeVar, get_args

from fastapi import Query, Response
from fastapi.routing import APIRoute
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.routes._pagination import PaginatedResponse
from app.db.models.common import ProfileRecommendation, ReadProfile, RecordReviewStatus, SubmissionRecordType
from app.db.models.record_review import RecordReview
from app.schemas.reads.scientific_common import (
    HiddenByDefault,
    OrderEcho,
    OrderKeyEcho,
)
from app.services.scientific_read.common import evidence_visible_statuses, fetch_review_badges, visible_statuses
from app.services.scientific_read.hidden_by_default import hidden_by_default_block
from app.services.scientific_read.profile import current_read_profile
from app.services.scientific_read.ref_guard import current_ref_guard_context
from app.services.scientific_read.sql_review import join_review, visible_review_filter

T = TypeVar("T")

#: Set on a legacy read that named a merged-away row and was answered with the row it was merged into.
MERGED_FROM_HEADER = "X-TCKDB-Merged-From"


def sub_response(response: Response):
    """The response a detail read sets its header on.

    A dependency, not a handler parameter: the legacy route class reads a ``Response`` parameter as "this is a bare
    list that discloses what it hid" and documents that header, which a detail read does not carry.
    """

    return response

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
    #: was excluded by default, and always under ``curated`` (which reports no count of records below the floor).
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

    def hidden(
        self, session: Session, record_type: SubmissionRecordType, ids: Any, *, counted: str = "records"
    ) -> HiddenByDefault | None:
        """What the default hid among ``ids`` (a ``select`` of the matched ids): one statement, or none.

        ``counted`` names what is counted when it is not the listed records themselves (``"species_entries"``).
        """
        return hidden_by_default_block(
            session,
            record_type=record_type,
            ids=ids,
            include_rejected=self.include_rejected,
            include_deprecated=self.include_deprecated,
            counted=counted,
        )


    def hidden_child(self, session: Session, record_type: SubmissionRecordType, ids: Any) -> HiddenByDefault | None:
        """What the default hid among ``ids`` for a record that is shown only with its parent (:data:`PARENT_OF`).

        A child is hidden by its own status and by its parent's. It is counted under EVERY excluding status that applies
        to it (once per status), so each count is the number of matching records that exclusion hides. A child hidden by
        two exclusions (itself deprecated, its group rejected) is in both counts and needs both switches. One statement,
        or none.
        """
        from sqlalchemy import and_, func, or_
        from sqlalchemy.orm import aliased

        from app.services.scientific_read.hidden_by_default import excluded_by_default, hidden_by_default_from_counts

        model, fk, parent_type = PARENT_OF[record_type]
        excluded = excluded_by_default(include_rejected=self.include_rejected, include_deprecated=self.include_deprecated)
        if not excluded:
            return None
        own = aliased(RecordReview)
        parent = aliased(RecordReview)
        row = session.execute(
            select(*(func.count().filter(or_(own.status == status, parent.status == status)) for status in excluded))
            .select_from(model)
            .outerjoin(own, and_(own.record_type == record_type, own.record_id == model.id))
            .outerjoin(parent, and_(parent.record_type == parent_type, parent.record_id == getattr(model, fk)))
            .where(model.id.in_(ids))
        ).one()
        return hidden_by_default_from_counts(
            dict(zip(excluded, row, strict=True)),
            include_rejected=self.include_rejected,
            include_deprecated=self.include_deprecated,
        )


HIDDEN_HEADER = "X-TCKDB-Hidden-By-Default"

#: The review status of the record a legacy DETAIL read served under a profile with a review floor (``curated``). The
#: legacy payloads are raw entity rows with no badge field, and since step 7 of ADR 0023 a detail serves a record that is
#: merely ``not_reviewed`` or ``under_review``, so the status travels here. Absent under the default profile, on a list, and
#: on a detail whose path names no gated record.
REVIEW_STATUS_HEADER = "X-TCKDB-Review-Status"

#: The id a withheld record is replaced by before the route runs: it names no row, so the route answers for the withheld
#: record exactly as it answers for an id that does not exist. Shared by the path-parameter gates and
#: the id-typed filters.
ABSENT_ID = -1

#: The OpenAPI declaration of the headers legacy responses may carry.
REVIEW_STATUS_HEADER_SPEC = {
    "description": (
        "Under profile=curated, the review status (not_reviewed, under_review, approved, ...) of the single record this "
        "response serves. Absent under the default profile, on lists, and on any response that is not the single gated "
        "record itself (sub-resource lists, and single-object sub-resources such as /calculations/{id}/sp-result)."
    ),
    "schema": {"type": "string"},
}
HIDDEN_HEADER_SPEC = {
    "description": (
        "On a list that is a bare JSON array: how many records each default exclusion hid, as `deprecated=2; rejected=1` (keys sorted). "
        "Absent when nothing was excluded and always absent under profile=curated."
    ),
    "schema": {"type": "string"},
}


def disclose_in_header(response: Response, block: HiddenByDefault | None) -> None:
    """A list that is a bare JSON array has no envelope to carry ``hidden_by_default``; it goes in a header.

    ``deprecated=2; rejected=1``, one entry per default exclusion. Absent when nothing was excluded by default and
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


@dataclass(frozen=True)
class Gate:
    """A path parameter that names a record the profile may hide (judged with its parent, see :data:`PARENT_OF`)."""

    record_type: SubmissionRecordType
    label: str


T = SubmissionRecordType

#: Kind of a ``network_kinetics`` row, which is reviewed through its solve (it has no review row of its own).
NETWORK_KINETICS = "network_kinetics"


def _evidence_statuses() -> set[RecordReviewStatus]:
    """The statuses linked evidence may have and still be shown under the profile (ADR 0023): all but ``rejected`` and
    ``deprecated`` under a floor, every status without one."""
    return set(evidence_visible_statuses())


def _parent_of() -> dict:
    from app.db.models.species import ConformerObservation
    from app.db.models.transition_state import TransitionStateEntry

    return {
        T.conformer_observation: (ConformerObservation, "conformer_group_id", T.conformer_group),
        T.transition_state_entry: (TransitionStateEntry, "transition_state_id", T.transition_state),
    }


#: Records that cannot be shown without their parent: ``kind -> (ORM model, foreign-key attribute, parent kind)``. One
#: rule for every way a child can be reached or named: the detail gate, a pointer at it, an id filter naming it, an
#: embedded copy of it. A child whose own review passes is still hidden when its parent is hidden, as the scientific
#: reads hide a ``NetworkKinetics`` through its solve. The SQL lists apply the same rule with ``LegacyVisibility.restrict``
#: on the foreign key.
PARENT_OF = _parent_of()

#: Every id column of a legacy payload (and every id-typed query filter) that names a record of a review-gated kind,
#: and the kind it names. A visible record must not name a record the profile hides, so the walk below blanks such a
#: column (or drops the list element that carries it) by THIS table, not by what the column is called. The registry
#: test (``test_every_id_field_of_a_legacy_payload_is_classified``) fails for a field in a reachable response model
#: that is in neither this table nor :data:`UNGATED_ID_FIELDS`.
POINTER_KINDS: dict[str, SubmissionRecordType] = {
    "calculation_id": T.calculation,
    "parent_calculation_id": T.calculation,
    "child_calculation_id": T.calculation,
    "source_calculation_id": T.calculation,
    "source_scan_calculation_id": T.calculation,
    "zpe_calculation_id": T.calculation,
    "conformer_observation_id": T.conformer_observation,
    "source_conformer_observation_id": T.conformer_observation,
    "conformer_group_id": T.conformer_group,
    "target_conformer_group_id": T.conformer_group,
    "statmech_id": T.statmech,
    "thermo_id": T.thermo,
    "kinetics_id": T.kinetics,
    "transport_id": T.transport,
    "transition_state_entry_id": T.transition_state_entry,
    "transition_state_id": T.transition_state,
    "network_id": T.network,
    "solve_id": T.network_solve,
    "network_kinetics_id": NETWORK_KINETICS,
}

#: Id columns that name something the review gate does not cover, each with the reason. Parents that are scoping
#: context (species and reaction entries, species, reactions) are not gated by the scientific reads either.
UNGATED_ID_FIELDS: dict[str, str] = {
    **dict.fromkeys(
        (
            "species_entry_id", "target_species_entry_id", "collider_species_entry_id", "reaction_entry_id",
            "target_reaction_entry_id", "species_id", "reaction_id",
        ),
        "scoping parent (identity context), not review-gated on the scientific reads either",
    ),
    **dict.fromkeys(
        (
            "literature_id", "source_literature_id", "author_id", "software_id", "software_release_id", "workflow_tool_id",
            "workflow_tool_release_id", "lot_id", "level_of_theory_id", "energy_level_of_theory_id",
            "frequency_level_of_theory_id", "scheme_id", "frequency_scale_factor_id", "reaction_family_id",
            "assignment_scheme_id", "geometry_id", "input_geometry_id", "output_geometry_id", "external_source_record_id",
            "determination_id", "applied_correction_id",
        ),
        "reference or provenance row with no review state of its own",
    ),
    **dict.fromkeys(
        ("state_id", "source_state_id", "sink_state_id", "channel_id", "torsion_id", "source_artifact_id"),
        "structural part of a record that is gated itself (network state/channel, statmech torsion, artifact)",
    ),
    **dict.fromkeys(
        (
            "actor_user_id", "assessor_user_id", "user_id", "submission_id", "related_submission_id",
            "source_submission_id", "supersedes_submission_id", "upload_job_id", "record_id", "superseded_record_id",
            "superseding_record_id",
        ),
        "curation or audit row; not served by a legacy entity read",
    ),
}

#: Columns that only make sense together with a pointer: blanking the pointer blanks them too, so the record stays valid
#: (a ``single_conformer`` thermo target must name its group; with the group hidden the target is not stated).
COUPLED_FIELDS: dict[str, tuple[str, ...]] = {"target_conformer_group_id": ("thermodynamic_target_kind",)}

#: Read models that are a gated record, by the kind their ``id`` names. A record embedded in another response
#: (``TransitionStateRead.entries``, a group's observations) is dropped when the request does not show it.
RECORD_MODELS: dict[str, SubmissionRecordType] = {
    "ThermoRead": T.thermo,
    "KineticsRead": T.kinetics,
    "StatmechRead": T.statmech,
    "TransportRead": T.transport,
    "CalculationRead": T.calculation,
    "TransitionStateRead": T.transition_state,
    "TransitionStateEntryRead": T.transition_state_entry,
    "ConformerGroupRead": T.conformer_group,
    "ConformerGroupSummaryRead": T.conformer_group,
    "ConformerGroupDetailRead": T.conformer_group,
    "ConformerObservationRead": T.conformer_observation,
    "NetworkSolveListItemRead": T.network_solve,
    "NetworkKineticsRead": NETWORK_KINETICS,
}


class LegacyPayloadUnplaceable(RuntimeError):
    """A hidden record is named where it can be neither blanked nor dropped. Fail closed (decision D4)."""


def _hidden_ids(session: Session, kind: SubmissionRecordType, ids: set[int], statuses: set[RecordReviewStatus]) -> set[int]:
    """The ``ids`` of ``kind`` whose review status is not in ``statuses`` (a missing review row is ``not_reviewed``)."""
    if not ids:
        return set()
    if kind is NETWORK_KINETICS:
        from app.db.models.network_pdep import NetworkKinetics

        solve_of = dict(
            session.execute(select(NetworkKinetics.id, NetworkKinetics.solve_id).where(NetworkKinetics.id.in_(ids))).all()
        )
        hidden_solves = _hidden_ids(session, T.network_solve, {s for s in solve_of.values() if s is not None}, statuses)
        return {i for i in ids if solve_of.get(i) is None or solve_of[i] in hidden_solves}
    badges = fetch_review_badges(session, record_type=kind, record_ids=ids)
    hidden = {i for i in ids if badges[i].status not in statuses}
    parent = PARENT_OF.get(kind)
    if parent is not None:
        model, fk, parent_type = parent
        parent_ids = dict(session.execute(select(model.id, getattr(model, fk)).where(model.id.in_(ids))).all())
        hidden_parents = _hidden_ids(session, parent_type, {p for p in parent_ids.values() if p is not None}, statuses)
        hidden |= {i for i, p in parent_ids.items() if p in hidden_parents}
    return hidden


def _is_required(model: type, name: str) -> bool:
    annotation = model.model_fields[name].annotation
    return type(None) not in get_args(annotation) and annotation is not type(None)


def _collect(node: Any, pointers: dict, records: dict) -> None:
    if isinstance(node, BaseModel):
        for name in type(node).model_fields:
            value = getattr(node, name, None)
            if name in POINTER_KINDS and isinstance(value, int):
                pointers.setdefault(POINTER_KINDS[name], set()).add(value)
            elif isinstance(value, (list, tuple)):
                for item in value:
                    kind = _record_kind(item)
                    if kind is not None and isinstance(getattr(item, "id", None), int):
                        records.setdefault(kind, set()).add(item.id)
                    _collect(item, pointers, records)
            elif isinstance(value, BaseModel):
                _collect(value, pointers, records)
    elif isinstance(node, (list, tuple)):
        for item in node:
            _collect(item, pointers, records)


def _record_kind(item: Any) -> SubmissionRecordType | None:
    if not isinstance(item, BaseModel):
        return None
    for cls in type(item).__mro__:
        kind = RECORD_MODELS.get(cls.__name__)
        if kind is not None:
            return kind
    return None


def _strip(node: Any, hidden_pointers: dict, hidden_records: dict, *, top: bool) -> Any:
    """Remove every mention of a hidden record: a nullable pointer column becomes ``None``; a list element that
    carries a hidden required pointer, or is itself a hidden record, is dropped. Only ever removes."""
    if isinstance(node, BaseModel):
        for name in type(node).model_fields:
            value = getattr(node, name, None)
            if name in POINTER_KINDS and isinstance(value, int):
                if value in hidden_pointers.get(POINTER_KINDS[name], ()):
                    if _is_required(type(node), name):
                        raise _Drop(name)
                    setattr(node, name, None)
                    for coupled in COUPLED_FIELDS.get(name, ()):
                        setattr(node, coupled, None)
            elif isinstance(value, (list, tuple)):
                kept = []
                for item in value:
                    kind = _record_kind(item)
                    if kind is not None and getattr(item, "id", None) in hidden_records.get(kind, ()):
                        continue
                    try:
                        kept.append(_strip(item, hidden_pointers, hidden_records, top=False))
                    except _Drop:
                        continue
                setattr(node, name, kept)
            elif isinstance(value, BaseModel):
                try:
                    _strip(value, hidden_pointers, hidden_records, top=False)
                except _Drop as exc:
                    raise LegacyPayloadUnplaceable(f"{type(node).__name__}.{name} names a hidden record") from exc
        return node
    if isinstance(node, list):
        kept = []
        for item in node:
            try:
                kept.append(_strip(item, hidden_pointers, hidden_records, top=False))
            except _Drop:
                continue
        return kept
    return node


class _Drop(Exception):
    """A required pointer names a hidden record: the carrying list element goes."""


def withhold_hidden_records(session: Session, payload: Any, *, request_statuses: set[RecordReviewStatus]) -> Any:
    """Strip a legacy response of every mention of a record the request does not show.

    * Pointer columns and id-typed links (:data:`POINTER_KINDS`) are linked evidence: under a floor only ``rejected`` and
      ``deprecated`` records are withheld (ADR 0023).
    * Records embedded in the response (:data:`RECORD_MODELS`) are judged by the request's visibility (the default
      exclusions) without a floor, and by the same evidence rule under one.

    One statement per kind present; none when the response names nothing gated, and no pointer statement without a
    floor. The payload's own top-level rows are already filtered in SQL and are not dropped here.
    """
    items = getattr(payload, "items", None)
    body = list(items) if isinstance(items, list) else payload
    pointers: dict = {}
    records: dict = {}
    _collect(body, pointers, records)
    floor = current_read_profile().review_floor is not None
    # ADR 0023: under a floor a nested pointer, and a record embedded in another (a group's observation), is linked
    # evidence. Only rejected and deprecated evidence is withheld; a not-reviewed one is named as it always was (these
    # payloads carry raw ids and no badge field). The floor still decides which TOP-LEVEL rows a list returns, in SQL.
    evidence_statuses = _evidence_statuses()
    hidden_pointers = (
        {kind: _hidden_ids(session, kind, ids, evidence_statuses) for kind, ids in pointers.items()} if floor else {}
    )
    embedded_statuses = evidence_statuses if floor else request_statuses
    hidden_records = {kind: _hidden_ids(session, kind, ids, embedded_statuses) for kind, ids in records.items()}
    if not any(hidden_pointers.values()) and not any(hidden_records.values()):
        return payload
    stripped = _strip(body, hidden_pointers, hidden_records, top=True)
    if isinstance(items, list):
        payload.items = stripped
        return payload
    return stripped


_PATH_PARAM = re.compile(r"{(\w+)(?::[^}]*)?}")


def served_param_of(path: str, gates: dict[str, Gate]) -> str | None:
    """The gated path parameter whose record a route SERVES: the one its path ends with (``/thermo/{thermo_id}``).

    A route whose path continues past a gated parameter (``/networks/{network_id}/solves``,
    ``/calculations/{calculation_id}/dependencies``) serves a list of sub-resources, not that record, and states no review
    status. On ``/networks/{network_id}/solves/{solve_id}`` the served record is the solve. ``None`` when no gated
    parameter ends the path.
    """
    last = path.rstrip("/").rsplit("/", 1)[-1]
    match = _PATH_PARAM.fullmatch(last)
    return match.group(1) if match and match.group(1) in gates else None


def _wrap(endpoint: Any, gates: dict[str, Gate], path_params: set[str], served_param: str | None = None) -> Any:
    """The endpoint, with the gates and the walk applied around it.

    Gates read the value FastAPI already validated and bound to the parameter, so every spelling of an id that
    reaches the route (``+5``, ``5.0``, `` 5``) is judged as the id it is.
    """

    @functools.wraps(endpoint)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        context = current_ref_guard_context()
        floor = current_read_profile().review_floor is not None
        if context is not None and floor:
            session = context.session
            for param, gate in gates.items():
                value = kwargs.get(param)
                if isinstance(value, int) and _hidden_ids(session, gate.record_type, {value}, _evidence_statuses()):
                    # A withheld record is the id of nothing; see ``ABSENT_ID``.
                    kwargs[param] = ABSENT_ID
                    continue
                if isinstance(value, int) and param == served_param:
                    # The record this route serves states its own review status (ADR 0023 step 7).
                    badge = fetch_review_badges(session, record_type=gate.record_type, record_ids=[value]).get(value)
                    if badge is not None:
                        context.served_review_status = badge.status.value
            # An id-typed filter naming a WITHHELD record (rejected or deprecated) is the filter for an id that names
            # nothing. A filter naming a not-reviewed one is kept: a filter is not a subject (ADR 0023).
            for name, value in list(kwargs.items()):
                kind = POINTER_KINDS.get(name)
                if kind is not None and name not in path_params and isinstance(value, int) and name not in gates:
                    if _hidden_ids(session, kind, {value}, _evidence_statuses()):
                        kwargs[name] = ABSENT_ID
        result = endpoint(*args, **kwargs)
        if context is None:
            return result
        visibility = kwargs.get("visibility")
        statuses = (
            visibility.statuses
            if isinstance(visibility, LegacyVisibility)
            else visible_statuses(min_review_status=None, include_rejected=False, include_deprecated=False)
        )
        return withhold_hidden_records(context.session, result, request_statuses=statuses)

    return wrapper


def legacy_route_class(gates: dict[str, Gate] | None = None) -> type[APIRoute]:
    """A route class for a legacy entity router: its path-parameter ``gates``, id-typed filters and response walk.

    Declared once on the router, so a route added to it tomorrow is covered the day it exists.
    """
    declared = dict(gates or {})

    class LegacyRoute(APIRoute):
        legacy_gates = declared

        def __init__(self, path: str, endpoint: Any, **kwargs: Any) -> None:
            methods = {m.upper() for m in (kwargs.get("methods") or [])}
            if "GET" in methods and not inspect.iscoroutinefunction(endpoint):
                served = served_param_of(path, declared)
                headers: dict[str, Any] = {}
                if served is not None:
                    headers[REVIEW_STATUS_HEADER] = REVIEW_STATUS_HEADER_SPEC
                if "response" in inspect.signature(endpoint).parameters:
                    headers[HIDDEN_HEADER] = HIDDEN_HEADER_SPEC
                if headers:
                    responses = dict(kwargs.get("responses") or {})
                    ok = dict(responses.get(200) or {})
                    ok["headers"] = {**(ok.get("headers") or {}), **headers}
                    ok.setdefault("description", "Successful Response")
                    responses[200] = ok
                    kwargs["responses"] = responses
                endpoint = _wrap(endpoint, declared, set(_PATH_PARAM.findall(path)), served)
            super().__init__(path, endpoint, **kwargs)

        def get_route_handler(self) -> Any:
            handler = super().get_route_handler()

            async def with_review_status(request: Any) -> Any:
                response = await handler(request)
                context = current_ref_guard_context()
                if context is not None and context.served_review_status is not None:
                    response.headers[REVIEW_STATUS_HEADER] = context.served_review_status
                return response

            return with_review_status

    return LegacyRoute


LegacyReadRoute = legacy_route_class()


__all__ = [
    "ABSENT_ID",
    "ADMIN_LISTING_ORDER",
    "COUPLED_FIELDS",
    "HIDDEN_HEADER",
    "NETWORK_KINETICS",
    "PARENT_OF",
    "POINTER_KINDS",
    "RECORD_MODELS",
    "REVIEW_STATUS_HEADER",
    "UNGATED_ID_FIELDS",
    "EntityListResponse",
    "Gate",
    "LegacyReadRoute",
    "LegacyVisibility",
    "disclose_in_header",
    "entity_page",
    "legacy_route_class",
    "withhold_hidden_records",
]
