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
:data:`_ENABLED_ROUTE_PREFIXES` lists the route families on which the hook withholds. PR-2a enables the
species-side families (:data:`SPECIES_SIDE_PREFIXES`); the TS/reaction and network families follow.
"""

from __future__ import annotations

import logging
import re
import types
from collections.abc import Collection, Mapping
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from typing import Annotated, Any, Union, get_args, get_origin
from urllib.parse import unquote, urlsplit

from pydantic import BaseModel
from sqlalchemy import and_, or_, select, union_all
from sqlalchemy.orm import Session
from sqlalchemy.sql import ColumnElement
from tckdb_schemas.thermo_declarations import StoredThermoProtocolCalculationRef

from app.db.models.common import ProfileRecommendation, RecordReviewStatus, SubmissionRecordType
from app.db.models.kinetics import Kinetics
from app.db.models.molecular_property_observation import MolecularPropertyObservation
from app.db.models.record_review import RecordReview
from app.db.models.thermo import Thermo
from app.schemas.reads.scientific_calculation import (
    CalculationCompositeInputSummary,
    CalculationConformerSummary,
    CalculationDependencySummary,
    TransitionStateEntryOwnerSummary,
)
from app.schemas.reads.scientific_common import CalculationEvidenceSummary, SupersessionNotice
from app.schemas.reads.scientific_conformer import (
    ConformerCalculationSummary,
    ConformerGeometryLink,
    ConformerGroupCoreBlock,
    ConformerObservationGroupSummary,
    ConformerObservationSiblingSummary,
)
from app.schemas.reads.scientific_geometry import (
    GeometryIdentity,
    GeometryIdentityOwnerRef,
    GeometryProvenanceCalcLink,
    GeometryTransitionStateIdentity,
)
from app.schemas.reads.scientific_species_calculations import (
    CalculationProvenanceBlock,
    ConformerContextBlock,
    SupportingCalculationRef,
)
from app.schemas.reads.scientific_statmech import (
    StatmechConformerContextItem,
    StatmechCurvatureWitness,
    StatmechSourceCalculationSummary,
    StatmechTransitionStateContext,
)
from app.schemas.reads.scientific_transition_state import (
    TransitionStateStatmechSourceCalculation,
    TransitionStateStatmechSummary,
)
from app.schemas.reads.scientific_transport import TransportSourceCalculationSummary
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

#: A ref is a whole-string value of a gated prefix and the 26-character body. ``\Z``, not ``$``: ``$`` also matches
#: before a trailing newline, which would let ``calc_...\n`` count as a ref in one place and not in another.
#: Uppercase and suffixed look-alikes (``CALC_...``, ``calc_...x``) are not refs: no route resolves them.
GATED_REF_RE = re.compile(
    "^(?:" + "|".join(sorted((re.escape(p) for p in GATED_TYPES), key=len, reverse=True)) + r")_[a-z0-9]{26}\Z"
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
        # PR-2a: the species-side family.
        StatmechTransitionStateContext,
        CalculationCompositeInputSummary,
        CalculationConformerSummary,
        CalculationDependencySummary,
        ConformerContextBlock,
        ConformerObservationGroupSummary,
        ConformerObservationSiblingSummary,
        ConformerCalculationSummary,
        ConformerGeometryLink,
        GeometryIdentityOwnerRef,
        GeometryProvenanceCalcLink,
        GeometryTransitionStateIdentity,
        StoredThermoProtocolCalculationRef,
        SupersessionNotice,
        SupportingCalculationRef,
        TransitionStateEntryOwnerSummary,
        TransportSourceCalculationSummary,
        # Owner decision D1: a visible observation whose conformer group is hidden stays visible with the group
        # block blanked. Registering the block is what lets the guard blank it; widening the field alone is not
        # enough (design note, "Correction from the #770 review").
        ConformerGroupCoreBlock,
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
_SCIENTIFIC_BASE = "/api/v1/scientific"

#: PR-2a: the species-side route families. Matched on whole path segments, so ``/species`` does not cover
#: ``/species-entries`` (which is listed on its own).
SPECIES_SIDE_PREFIXES: frozenset[str] = frozenset(
    f"{_SCIENTIFIC_BASE}/{segment}"
    for segment in (
        "statmech",
        "thermo",
        "species-entries",
        "species",
        "transport",
        "conformer-groups",
        "conformer-observations",
        "conformers",
        "calculations",
        "species-calculations",
        "artifacts",
        "geometries",
    )
)

_ENABLED_ROUTE_PREFIXES: frozenset[str] = SPECIES_SIDE_PREFIXES


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


_URL_START = re.compile(r"^(?:[A-Za-z][A-Za-z0-9+.\-]*://|[/?#;])")
#: How many times a component is percent-decoded looking for a stable form. Three layers is already hostile; a
#: component still changing after this many is refused (fail closed), never kept.
_MAX_DECODE_ROUNDS = 5


def _is_url_valued(key: Any) -> bool:
    """Fields registered as URL-valued: ``endpoint`` and ``*_endpoint``.

    These are read as URLs whatever their length or content. The prose shortcut below applies to every OTHER field.
    """
    return isinstance(key, str) and (key == "endpoint" or key.endswith("_endpoint"))


def _decode_stable(component: str) -> str:
    """Percent-decode ``component`` until it stops changing; a form that never settles raises ``ValueError``."""
    for _ in range(_MAX_DECODE_ROUNDS):
        decoded = unquote(component)
        if decoded == component:
            return component
        component = decoded
    raise ValueError("percent-encoding does not settle")


def _tokens(component: str) -> list[str]:
    """The pieces of one URL component a ref can be: the decoded component split on ``/``, ``;``, ``,``, ``&``, ``=``
    and whitespace (so a ref followed by an encoded newline still comes out as a ref)."""
    return [t for t in re.split(r"[/;,&=\s]", _decode_stable(component)) if t]


def _refs_named_by(value: str, *, url_valued: bool = False) -> list[str]:
    """Every gated ref a string value names.

    A value is a ref when the whole string is one. It also names refs when it is a URL and any of these is one: a
    path segment (``/calculations/{ref}/scan``, in the middle or at the end), a ``;params`` item, a query key or
    value, or the fragment. Components are percent-decoded until stable, so ``%2F``, ``%63alc_...`` and
    ``%2563alc_...`` do not hide one.

    In a URL-valued field (``url_valued``: see :func:`_is_url_valued`) the value is parsed whatever its length or
    whitespace; a value that cannot be parsed raises ``ValueError``, which the caller turns into
    :class:`RefGuardUnplaceable` (fail closed). In any other field a string is read as a URL only when it looks like
    one (an absolute ``scheme://`` URL, or a start of ``/``, ``?``, ``#`` or ``;``) and has no whitespace: whitespace
    marks free text, which this reader leaves alone. A hidden ref inside free text is therefore NOT withheld at run
    time; a field that can carry one needs its own rule. (The sweep's raw-body check finds such a leak in tests only.)
    """
    if len(value) <= 40 and GATED_REF_RE.match(value):
        return [value]
    if not url_valued and (not _URL_START.match(value) or any(c.isspace() for c in value)):
        return []
    try:
        parts = urlsplit(value)
    except ValueError:
        if url_valued:
            raise
        return []  # free text that merely looks like a URL and is not one
    found: list[str] = []
    for component in (parts.path, parts.query, parts.fragment):
        try:
            tokens = _tokens(component)
        except ValueError:
            if url_valued:
                raise
            return []
        found.extend(t for t in tokens if GATED_REF_RE.match(t))
    return found


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
                try:
                    refs = _refs_named_by(value, url_valued=_is_url_valued(key))
                except ValueError as exc:
                    # A URL-valued field the guard cannot read is refused, never kept (owner decision D4).
                    raise RefGuardUnplaceable(
                        type(node).__name__, f"{_path_text((*path, (node, key)))}<unparseable URL>", "url"
                    ) from exc
                sites.extend(((*path, (node, key)), ref) for ref in refs)
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


#: Pointer lists that travel with a bare list of integer ids, in step: ``(model, list field) -> (id list field,
#: the id field on a row)``. The ids carry no ref the guard could see, so deleting a pointer row must delete the id
#: that is *that row's*, or the integer pk of a hidden record stays on the wire. Matching is by value, never by
#: position: a same-length list in another order would otherwise drop the visible id and keep the hidden one.
ALIGNED_ID_LISTS: Mapping[tuple[type, str], tuple[str, str]] = {
    (CalculationProvenanceBlock, "supporting_calculations"): ("supporting_calculation_ids", "calculation_id"),
}


def _aligned_id_deletions(owner_frame: _Frame, deleted_from: list, index: Any, text: str, ref: str) -> list[_Action]:
    owner, owner_key = owner_frame
    names = ALIGNED_ID_LISTS.get((type(owner), owner_key)) if isinstance(owner, BaseModel) else None
    if names is None:
        return []
    ids_field, row_id_field = names
    ids = getattr(owner, ids_field, None)
    if not isinstance(ids, list) or not ids:
        return []  # stripped or never populated: nothing of the record's id is on the wire
    row_id = getattr(deleted_from[index], row_id_field, None)
    if len(ids) != len(deleted_from) or row_id is None or ids[index] != row_id:
        # Not the same list in the same order: dropping an index could keep a hidden id and lose a visible one.
        raise RefGuardUnplaceable(type(owner).__name__, text, ref.split("_", 1)[0])
    return [_Action("delete", ids, index)]


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
            actions = [_Action("delete", container, key)]
            if isinstance(container, list) and i >= 1:
                actions.extend(_aligned_id_deletions(path[i - 1], container, key, text, ref))
            return actions, WithheldSite(ref, text, "delete_pointer", name)
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


def _geometry_identity_is_empty(node: BaseModel) -> bool:
    """A ``GeometryIdentity`` with no owner block and no listed owner says nothing; it is not emitted at all."""
    return (
        getattr(node, "species_entry", None) is None
        and getattr(node, "transition_state_entry", None) is None
        and not getattr(node, "ambiguous_owners", None)
    )


#: Blocks that must not survive as an empty shell once their contents were withheld: the field that holds one is set
#: to ``None``, exactly as when there was never anything to show. A client cannot tell "every owner withheld" from
#: "no owner" (hidden equals unknown). ``model -> is it empty``.
COLLAPSE_WHEN_EMPTY: Mapping[type, Any] = {GeometryIdentity: _geometry_identity_is_empty}


def _collapse_empty(sites: list[tuple[tuple[_Frame, ...], str]], hidden: set[str]) -> list[WithheldSite]:
    done: list[WithheldSite] = []
    seen: set[tuple[int, Any]] = set()
    for path, ref in sites:
        if ref not in hidden:
            continue
        for i in range(len(path) - 1):
            container, key = path[i]
            node = path[i + 1][0]
            check = COLLAPSE_WHEN_EMPTY.get(type(node))
            if check is None or not check(node) or not isinstance(container, BaseModel):
                continue
            if (id(container), key) in seen or not _field_traits(type(container), key)[0]:
                continue
            seen.add((id(container), key))
            setattr(container, key, None)
            done.append(WithheldSite(ref, _path_text(path[: i + 1]), "collapse_empty", type(node).__name__))
    return done


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
        report.sites.extend(_collapse_empty(sites, report.hidden))
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
    "ALIGNED_ID_LISTS",
    "EXEMPT_ROUTE_PREFIXES",
    "GATED_REF_RE",
    "GATED_TYPES",
    "POINTER_MODELS",
    "SPECIES_SIDE_PREFIXES",
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
