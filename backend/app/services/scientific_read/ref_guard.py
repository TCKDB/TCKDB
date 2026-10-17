"""One guard for refs of records the read profile hides.

Under ``?profile=curated`` a record below the review floor answers 404 on its own detail route. A visible
record can still *name* such a record (its source calculations, its transition state, its conformer group).
A name is a ref, and a ref proves the record exists and lets a reader track it. This module is the single
place that finds those refs in a finished response and removes them.

What it does, and refuses to do
-------------------------------
It only ever **deletes or blanks**. It never picks another record to fill a gap (the substitution bug
fixed in #767), because it runs after every service has made its picks and can only subtract from what they
produced. It does nothing under the default (exploratory) profile.

How a hidden ref is removed (:func:`withhold_in_model`)
-------------------------------------------------------
1. The nearest enclosing *pointer model* (:data:`POINTER_MODELS`) goes whole: a block whose subject is the
   linked record, not the visible one. A list element is deleted; a nullable field is set to ``None``.
2. With no pointer on the way: an element of a ``list[str]`` is deleted (and the same index of an aligned
   ``*_ids`` sibling); a nullable field, or a value inside an untyped ``dict``/``Any`` subtree, becomes
   ``None`` (and a same-stem ``*_id`` sibling with it).
3. Anything else is **unplaceable** and raises :class:`RefGuardUnplaceable` (fail closed, owner decision D4).
   The subject of a response (``record``, the elements of ``records``) is never deleted as a pointer: a
   hidden subject ref means a service forgot its population filter, which is exactly what should be loud.

Rollout
-------
:data:`_ENABLED_ROUTE_PREFIXES` is empty in this change: the hook is wired into
:func:`app.services.scientific_read.internal_ids.apply_internal_ids_visibility` and counts that it was
reached, but no route family is enabled, so nothing changes on the wire. Each family PR adds its prefixes.
"""

from __future__ import annotations

import logging
import re
import types
from collections.abc import Collection, Mapping
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from typing import Annotated, Any, Union, get_args, get_origin

from pydantic import BaseModel
from sqlalchemy import and_, or_, select, union_all
from sqlalchemy.orm import Session
from sqlalchemy.sql import ColumnElement

from app.db.models.common import ProfileRecommendation, RecordReviewStatus, SubmissionRecordType
from app.db.models.kinetics import Kinetics
from app.db.models.molecular_property_observation import MolecularPropertyObservation
from app.db.models.record_review import RecordReview
from app.db.models.thermo import Thermo
from app.schemas.reads.scientific_common import CalculationEvidenceSummary
from app.schemas.reads.scientific_statmech import (
    StatmechConformerContextItem,
    StatmechCurvatureWitness,
    StatmechSourceCalculationSummary,
)
from app.schemas.reads.scientific_transition_state import (
    TransitionStateStatmechSourceCalculation,
    TransitionStateStatmechSummary,
)
from app.services.public_refs import PREFIXES
from app.services.scientific_read import handles
from app.services.scientific_read.handles import status_hidden_under_profile
from app.services.scientific_read.profile import ResolvedReadProfile, current_read_profile

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Registry of gated types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GatedType:
    """One review-gated record type: its ref prefix, ORM class and where its review row lives."""

    prefix: str
    model: type
    record_type: SubmissionRecordType
    #: For records reviewed through a parent (``NetworkKinetics`` via its solve): the FK column name ...
    parent_fk: str | None = None
    #: ... and the parent's review vocabulary term. A record with an orphaned FK is hidden.
    parent_record_type: SubmissionRecordType | None = None


#: Gated types that have a ``record_review`` row of their own but no detail route, so the handle gate does
#: not cover them. Owner decision D3: they are review-gated in every list and their refs appear nested in
#: other responses (supersession notices, literature links, selection disclosures).
_NO_DETAIL_ROUTE_GATED: dict[type, SubmissionRecordType] = {
    Thermo: SubmissionRecordType.thermo,
    Kinetics: SubmissionRecordType.kinetics,
    MolecularPropertyObservation: SubmissionRecordType.molecular_property_observation,
}


def _build_gated_types() -> dict[str, GatedType]:
    """Derive the registry from the handle gate and ``public_refs.PREFIXES``; never a hand-typed prefix list."""
    out: dict[str, GatedType] = {}
    for model, record_type in handles._REVIEWABLE_HANDLE_TYPES.items():
        prefix = PREFIXES[model.__name__]
        out[prefix] = GatedType(prefix=prefix, model=model, record_type=record_type)
    for model, (fk_column, parent) in handles._PARENT_DERIVED_HANDLE_TYPES.items():
        prefix = PREFIXES[model.__name__]
        out[prefix] = GatedType(
            prefix=prefix,
            model=model,
            record_type=handles._REVIEWABLE_HANDLE_TYPES[parent],
            parent_fk=fk_column,
            parent_record_type=handles._REVIEWABLE_HANDLE_TYPES[parent],
        )
    for model, record_type in _NO_DETAIL_ROUTE_GATED.items():
        prefix = PREFIXES[model.__name__]
        out[prefix] = GatedType(prefix=prefix, model=model, record_type=record_type)
    return out


GATED_TYPES: Mapping[str, GatedType] = _build_gated_types()

#: Review-vocabulary terms that are reviewable but deliberately not gated, each with its reason. A new
#: ``SubmissionRecordType`` is in neither this map nor :data:`GATED_TYPES` until someone classifies it, and
#: the registry test fails until they do.
UNGATED_REVIEWABLE: Mapping[SubmissionRecordType, str] = {
    SubmissionRecordType.species: "scoping parent: names which records to look under, not what is returned",
    SubmissionRecordType.species_entry: "scoping parent",
    SubmissionRecordType.reaction: "scoping parent",
    SubmissionRecordType.reaction_entry: "scoping parent",
    SubmissionRecordType.applied_energy_correction: "no public ref and no detail route",
    SubmissionRecordType.artifact: "contribution evidence; never carries a record_review row",
}

#: A ref is a whole-string value of a gated prefix and the 26-character body.
GATED_REF_RE = re.compile(
    "^(?:" + "|".join(sorted((re.escape(p) for p in GATED_TYPES), key=len, reverse=True)) + ")_[a-z0-9]{26}$"
)

# ---------------------------------------------------------------------------
# Pointer registry
# ---------------------------------------------------------------------------

#: A pointer model is a block whose subject is the *linked* record, not the visible one. When the linked
#: record is hidden the whole block goes, including facts it carries about the hidden record. The visible
#: record's own facts live elsewhere and stay.
#:
#: Seeded with the blocks that #764, #765 and #767 already hid by hand (their per-surface tests pin the
#: shape). Each family PR registers its own.
POINTER_MODELS: frozenset[type[BaseModel]] = frozenset(
    {
        StatmechSourceCalculationSummary,
        StatmechCurvatureWitness,
        StatmechConformerContextItem,
        TransitionStateStatmechSummary,
        TransitionStateStatmechSourceCalculation,
        CalculationEvidenceSummary,
    }
)

#: Route templates the guard never touches, each with its reason. Matched as a prefix.
EXEMPT_ROUTE_PREFIXES: Mapping[str, str] = {
    "/api/v1/scientific/releases": "release and archive projections keep full provenance (owner decision)",
    "/api/v1/scientific/meta": "vocabulary (software, methods, families) carries no gated refs",
    "/api/v1/scientific/artifacts/{sha256}/download": "opaque bytes, not a JSON document",
}

#: Rollout allowlist: route-template prefixes on which the hook actually withholds. Empty in PR-1, so the
#: hook is inert on the wire. Deleted once every family is enabled.
_ENABLED_ROUTE_PREFIXES: frozenset[str] = frozenset()


class RefGuardUnplaceable(RuntimeError):
    """A hidden ref sits where it can be neither removed nor blanked (a required field, no pointer)."""

    def __init__(self, model_name: str, field_path: str, ref_prefix: str) -> None:
        self.model_name = model_name
        self.field_path = field_path
        self.ref_prefix = ref_prefix
        super().__init__(
            f"curated ref guard cannot place a hidden {ref_prefix}_ ref at {field_path} ({model_name}): "
            "make this field optional, or register the model that holds it in POINTER_MODELS AND make the field that "
            "holds that model optional (widening an owner field alone is not enough: a required ref inside a "
            "required block still has nowhere to go)"
        )


# ---------------------------------------------------------------------------
# Request context
# ---------------------------------------------------------------------------


@dataclass
class RefGuardContext:
    """What the boundary hook needs, published once per scientific request by the profile dependency."""

    session: Session
    #: The route template, e.g. ``/api/v1/scientific/statmech/{statmech_ref_or_id}``.
    route_path: str
    #: Times the hook was reached for this request (whatever it then decided).
    reached: int = 0
    #: Times the hook actually withheld (families enabled, curated, not exempt).
    applied: int = 0
    reports: list[WithholdReport] = field(default_factory=list)


_CURRENT_CONTEXT: ContextVar[RefGuardContext | None] = ContextVar("tckdb_ref_guard_context", default=None)


def set_ref_guard_context(ctx: RefGuardContext) -> Token:
    return _CURRENT_CONTEXT.set(ctx)


def reset_ref_guard_context(token: Token) -> None:
    _CURRENT_CONTEXT.reset(token)


def current_ref_guard_context() -> RefGuardContext | None:
    return _CURRENT_CONTEXT.get()


# ---------------------------------------------------------------------------
# Resolution: which of these refs are hidden
# ---------------------------------------------------------------------------


def hidden_refs(session: Session, refs: Collection[str], *, profile: ResolvedReadProfile) -> set[str]:
    """The subset of ``refs`` the profile hides. At most one statement; none when nothing can be hidden."""
    if profile.review_floor is None or not refs:
        return set()
    hidden_statuses = [s for s in RecordReviewStatus if status_hidden_under_profile(s, profile=profile)]
    if not hidden_statuses:
        return set()
    missing_row_hidden = status_hidden_under_profile(RecordReviewStatus.not_reviewed, profile=profile)

    by_prefix: dict[str, list[str]] = {}
    for ref in refs:
        prefix = ref.split("_", 1)[0]
        if prefix in GATED_TYPES:
            by_prefix.setdefault(prefix, []).append(ref)

    parts = []
    for prefix, group in by_prefix.items():
        gated = GATED_TYPES[prefix]
        model: Any = gated.model
        if gated.parent_fk is None:
            join_id = model.id
            record_type = gated.record_type
        else:
            join_id = getattr(model, gated.parent_fk)
            record_type = gated.parent_record_type or gated.record_type
        status_hidden: ColumnElement[bool] = RecordReview.status.in_(hidden_statuses)
        if missing_row_hidden:
            status_hidden = or_(status_hidden, RecordReview.id.is_(None))
        parts.append(
            select(model.public_ref)
            .select_from(model)
            .outerjoin(
                RecordReview,
                and_(RecordReview.record_type == record_type, RecordReview.record_id == join_id),
            )
            .where(model.public_ref.in_(group), status_hidden)
        )
    if not parts:
        return set()
    stmt = parts[0] if len(parts) == 1 else union_all(*parts)
    return set(session.execute(stmt).scalars())


# ---------------------------------------------------------------------------
# Planner
# ---------------------------------------------------------------------------

_Frame = tuple[Any, Any]  # (container, key-or-index): the value is container[key] / getattr(container, key)


@dataclass(frozen=True)
class WithheldSite:
    """One removal the guard made (or would make)."""

    ref: str
    path: str
    action: str  # "delete_pointer" | "null_pointer" | "delete_list_element" | "null_field" | "null_untyped"
    pointer: str | None = None


@dataclass
class WithholdReport:
    refs_seen: int = 0
    hidden: set[str] = field(default_factory=set)
    sites: list[WithheldSite] = field(default_factory=list)


@dataclass(frozen=True)
class _Action:
    kind: str  # "delete" | "null"
    container: Any
    key: Any


_FIELD_CACHE: dict[tuple[type, str], tuple[bool, bool]] = {}


def _annotation_has(annotation: Any, predicate) -> bool:
    if predicate(annotation):
        return True
    origin = get_origin(annotation)
    if origin is Annotated:
        return _annotation_has(get_args(annotation)[0], predicate)
    return any(_annotation_has(a, predicate) for a in get_args(annotation))


def _is_none_type(a: Any) -> bool:
    return a is type(None)


def _is_any(a: Any) -> bool:
    return a is Any or a is object


def _field_traits(model_cls: type, name: str) -> tuple[bool, bool]:
    """``(nullable, untyped)`` for a model field, from its annotation (cached per class)."""
    key = (model_cls, name)
    cached = _FIELD_CACHE.get(key)
    if cached is not None:
        return cached
    info = model_cls.model_fields.get(name)  # type: ignore[attr-defined]
    if info is None:
        traits = (False, False)
    else:
        annotation = info.annotation
        origin = get_origin(annotation)
        union_like = origin is Union or origin is types.UnionType
        nullable = (union_like and any(_is_none_type(a) for a in get_args(annotation))) or _annotation_has(
            annotation, _is_any
        )
        traits = (bool(nullable), _annotation_has(annotation, _is_any))
    _FIELD_CACHE[key] = traits
    return traits


class _DictKey(str):
    """Marks a site whose ref is the key of a dict rather than a value in it."""


def _children(node: Any, *, top: bool):
    if isinstance(node, BaseModel):
        for name in type(node).model_fields:
            if top and name == "request":
                # The caller's own input, echoed verbatim.
                continue
            yield name, getattr(node, name, None)
    elif isinstance(node, (list, tuple)):
        yield from enumerate(node)
    elif isinstance(node, dict):
        yield from node.items()


def _collect_sites(root: BaseModel) -> list[tuple[tuple[_Frame, ...], str]]:
    sites: list[tuple[tuple[_Frame, ...], str]] = []
    stack: list[tuple[Any, tuple[_Frame, ...]]] = [(root, ())]
    while stack:
        node, path = stack.pop()
        for key, value in _children(node, top=not path):
            if isinstance(node, dict) and isinstance(key, str) and len(key) <= 40 and GATED_REF_RE.match(key):
                # A ref used as a dict KEY cannot be blanked or deleted without renaming the entry. Recorded as a
                # site so that, if it is hidden, the guard fails closed instead of letting it through.
                sites.append(((*path, (node, _DictKey(key))), key))
            if isinstance(value, str):
                if len(value) <= 40 and GATED_REF_RE.match(value):
                    sites.append(((*path, (node, key)), value))
            elif isinstance(value, (BaseModel, list, tuple, dict)):
                stack.append((value, (*path, (node, key))))
    return sites


def _path_text(path: tuple[_Frame, ...]) -> str:
    out = ""
    for container, key in path:
        if isinstance(container, (list, tuple)):
            out += "[*]"
        else:
            out += ("." if out else "") + str(key)
    return out


def _is_subject(path: tuple[_Frame, ...], i: int) -> bool:
    """Is the node at frame ``i`` the response's subject (``record`` or an element of ``records``)?"""
    if i == 0:
        return path[0][1] == "record"
    return i == 1 and path[0][1] == "records"


def _plan_site(path: tuple[_Frame, ...], ref: str) -> tuple[list[_Action], WithheldSite]:
    text = _path_text(path)
    n = len(path) - 1

    # Rule 1: the nearest registered pointer on the way out.
    for i in range(n - 1, -1, -1):
        container, key = path[i]
        node = path[i + 1][0]
        if not isinstance(node, BaseModel) or type(node) not in POINTER_MODELS or _is_subject(path, i):
            continue
        name = type(node).__name__
        if isinstance(container, (list, dict)):
            return [_Action("delete", container, key)], WithheldSite(ref, text, "delete_pointer", name)
        if isinstance(container, BaseModel) and _field_traits(type(container), key)[0]:
            return [_Action("null", container, key)], WithheldSite(ref, text, "null_pointer", name)
        # Held by a required field: keep looking outward.

    # Rule 2: no pointer.
    holder, key = path[n]
    if isinstance(key, _DictKey):
        raise RefGuardUnplaceable(type(holder).__name__, f"{text}<dict key>", ref.split("_", 1)[0])
    if isinstance(holder, list):
        owner, owner_key = path[n - 1] if n >= 1 else (None, None)
        if isinstance(owner, dict):
            # A list inside an untyped dict (a stored protocol blob): blank the element, and the aligned id if any.
            actions = [_Action("null", holder, key)]
            if isinstance(owner_key, str) and owner_key.endswith("_refs"):
                sibling = owner.get(owner_key[: -len("_refs")] + "_ids")
                if isinstance(sibling, list) and len(sibling) == len(holder):
                    actions.append(_Action("null", sibling, key))
            return actions, WithheldSite(ref, text, "null_untyped")
        if isinstance(owner, BaseModel):
            if isinstance(owner_key, str) and _field_traits(type(owner), owner_key)[1]:
                return [_Action("null", holder, key)], WithheldSite(ref, text, "null_untyped")
            actions = [_Action("delete", holder, key)]
            if isinstance(owner_key, str) and owner_key.endswith("_refs"):
                sibling = getattr(owner, owner_key[: -len("_refs")] + "_ids", None)
                if isinstance(sibling, list):
                    if len(sibling) != len(holder):
                        raise RefGuardUnplaceable(type(owner).__name__, text, ref.split("_", 1)[0])
                    actions.append(_Action("delete", sibling, key))
            return actions, WithheldSite(ref, text, "delete_list_element")
    elif isinstance(holder, dict):
        actions = [_Action("null", holder, key)]
        if isinstance(key, str) and key.endswith("_ref"):
            sibling_name = key[: -len("_ref")] + "_id"
            if holder.get(sibling_name) is not None:
                actions.append(_Action("null", holder, sibling_name))
        return actions, WithheldSite(ref, text, "null_untyped")
    elif isinstance(holder, BaseModel):
        nullable, untyped = _field_traits(type(holder), key)
        if nullable:
            actions = [_Action("null", holder, key)]
            if isinstance(key, str) and key.endswith("_ref"):
                sibling_name = key[: -len("_ref")] + "_id"
                if sibling_name in type(holder).model_fields and getattr(holder, sibling_name) is not None:
                    actions.append(_Action("null", holder, sibling_name))
            return actions, WithheldSite(ref, text, "null_untyped" if untyped else "null_field")
    raise RefGuardUnplaceable(type(holder).__name__, text, ref.split("_", 1)[0])


def _apply(actions: Collection[_Action]) -> None:
    for action in actions:
        if action.kind == "null":
            if isinstance(action.container, BaseModel):
                setattr(action.container, action.key, None)
            else:
                action.container[action.key] = None
    deletes = [a for a in actions if a.kind == "delete"]
    # Highest index first, so earlier deletions in the same list do not shift later ones.
    deletes.sort(key=lambda a: (id(a.container), -a.key if isinstance(a.key, int) else 0))
    for action in deletes:
        if isinstance(action.container, dict):
            action.container.pop(action.key, None)
        else:
            del action.container[action.key]


def withhold_in_model(
    session: Session,
    payload: BaseModel,
    *,
    profile: ResolvedReadProfile,
    apply: bool = True,
) -> WithholdReport:
    """Remove every ref the profile hides from a response model, in place.

    With ``apply=False`` nothing is mutated (a dry run that reports what would go). Raises
    :class:`RefGuardUnplaceable` for a hidden ref that has nowhere safe to go, in either mode.
    """
    report = WithholdReport()
    if profile.review_floor is None:
        return report
    sites = _collect_sites(payload)
    report.refs_seen = len(sites)
    if not sites:
        return report
    report.hidden = hidden_refs(session, {ref for _, ref in sites}, profile=profile)
    if not report.hidden:
        return report
    planned: dict[tuple[int, Any, str], _Action] = {}
    for path, ref in sites:
        if ref not in report.hidden:
            continue
        actions, site = _plan_site(path, ref)
        report.sites.append(site)
        for action in actions:
            planned.setdefault((id(action.container), action.key, action.kind), action)
    if apply:
        _apply(list(planned.values()))
    return report


# ---------------------------------------------------------------------------
# Boundary hook
# ---------------------------------------------------------------------------


def route_matches(route_path: str, prefixes: Collection[str]) -> bool:
    """Prefix match on whole path segments: ``/meta`` covers ``/meta/x`` but not ``/metadata``."""
    return any(route_path == p or route_path.startswith(p + "/") for p in prefixes)


def withhold_for_current_request(payload: BaseModel) -> None:
    """The boundary hook: called by ``apply_internal_ids_visibility`` for every enveloped scientific response.

    Returns without a statement or a mutation when there is no context (outside a scientific request), no
    review floor (exploratory: the default), a release-backed profile, an exempt route, or a route family
    that is not enabled yet.
    """
    ctx = _CURRENT_CONTEXT.get()
    if ctx is None:
        return
    ctx.reached += 1
    profile = current_read_profile()
    if profile.review_floor is None:
        return
    if profile.recommendation is ProfileRecommendation.tckdb_curated_release:
        return
    if route_matches(ctx.route_path, EXEMPT_ROUTE_PREFIXES):
        return
    if not route_matches(ctx.route_path, _ENABLED_ROUTE_PREFIXES):
        return
    try:
        report = withhold_in_model(ctx.session, payload, profile=profile)
    except RefGuardUnplaceable as exc:
        logger.error(
            "ref_guard_unplaceable model=%s path=%s prefix=%s route=%s",
            exc.model_name,
            exc.field_path,
            exc.ref_prefix,
            ctx.route_path,
        )
        raise
    ctx.applied += 1
    ctx.reports.append(report)


__all__ = [
    "EXEMPT_ROUTE_PREFIXES",
    "GATED_REF_RE",
    "GATED_TYPES",
    "POINTER_MODELS",
    "UNGATED_REVIEWABLE",
    "GatedType",
    "RefGuardContext",
    "RefGuardUnplaceable",
    "WithheldSite",
    "WithholdReport",
    "current_ref_guard_context",
    "hidden_refs",
    "reset_ref_guard_context",
    "route_matches",
    "set_ref_guard_context",
    "withhold_for_current_request",
    "withhold_in_model",
]
