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
   linked record, not the visible one. A list element (or a map entry) is deleted; a nullable field is set to
   ``None``. An untyped field that is itself such a block is a *pointer field* (:data:`POINTER_FIELDS`) and goes
   whole too.
2. With no pointer on the way: an element of a ``list[str]`` is deleted (and the same index of an aligned
   ``*_ids`` sibling); a nullable field, or a value inside an untyped ``dict``/``Any`` subtree, becomes
   ``None`` (and a same-stem ``*_id`` sibling with it).
3. Anything else is **unplaceable** and raises :class:`RefGuardUnplaceable` (fail closed, owner decision D4).
   The subject of a response (``record``, the elements of ``records``) is never deleted as a pointer: a
   hidden subject ref means a service forgot its population filter, which is exactly what should be loud.
4. Free text (a note, a label, a file name, a depositor URL) that names a hidden ref anywhere in its tokens is
   blanked as a whole field: ``None`` when the field is nullable, else ``""``. Never token by token (the shape of
   what was removed would show), and text whose percent-encoding does not settle is blanked too, never refused.
   Exploratory text is untouched. A required field whose WHOLE value is a bare hidden ref is not free text: it is
   a ref in the wrong place (most likely a pointer the registry missed), so it raises :class:`RefGuardUnplaceable`
   and the registry gets fixed (D4).

Rollout
-------
:data:`_ENABLED_ROUTE_PREFIXES` lists the route families on which the hook withholds. PR-2a enables the
species-side families (:data:`SPECIES_SIDE_PREFIXES`) and PR-2b the transition-state and reaction families
(:data:`TS_REACTION_PREFIXES`); PR-2c the network, literature, analytics and provenance-vocabulary families
(:data:`NETWORK_PROVENANCE_PREFIXES`). The selection documents and the exports follow.
"""

from __future__ import annotations

import copy
import functools
import logging
import re
import sys
import types
import unicodedata
from collections.abc import Collection, Mapping
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from typing import Annotated, Any, Literal, Union, get_args, get_origin
from urllib.parse import unquote, urlsplit

from pydantic import BaseModel
from sqlalchemy import and_, or_, select, union_all
from sqlalchemy.orm import Session
from sqlalchemy.sql import ColumnElement
from tckdb_schemas.kinetics_declarations import StoredKineticsProtocolCalculationRef
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
from app.schemas.reads.scientific_common import (
    CalculationEvidenceSummary,
    PathSearchSummary,
    SCFStabilitySummary,
    SupersessionNotice,
    ValidationSummary,
)
from app.schemas.reads.scientific_conformer import (
    ConformerCalculationSummary,
    ConformerGeometryLink,
    ConformerGroupCoreBlock,
    ConformerObservationGroupSummary,
    ConformerObservationSiblingSummary,
)
from app.schemas.reads.scientific_frequency_scale_factor import FrequencyScaleFactorUsageSummary
from app.schemas.reads.scientific_geometry import (
    GeometryIdentity,
    GeometryIdentityOwnerRef,
    GeometryProvenanceCalcLink,
    GeometryTransitionStateIdentity,
)
from app.schemas.reads.scientific_kinetics import (
    KineticsDeterminationBlock,
    KineticsInterpretationAssignmentBlock,
    KineticsProvenance,
    KineticsTunnelingApplicationBlock,
)
from app.schemas.reads.scientific_network import (
    NetworkChannelMicroreactionSummary,
    NetworkContextSummary,
    NetworkKineticsSummary,
    NetworkSolveStateEnergySourceSummary,
    NetworkSolveSummary,
    NetworkSourceCalculationSummary,
)
from app.schemas.reads.scientific_network_kinetics import NetworkKineticsNetworkContext
from app.schemas.reads.scientific_network_kinetics_batch_evaluate import NetworkKineticsBatchFitEvaluation
from app.schemas.reads.scientific_provenance import (
    ReactionAtomMapBadge,
    ReactionAtomMapDetail,
    ReactionFullCalculationArtifacts,
    ReactionFullCalculationEvidenceSummary,
    ReactionFullIRCItem,
    ReactionFullNetworkMembership,
    ReactionFullPathSearchItem,
    ReactionFullScanItem,
    TransitionStateCalculationSlot,
    TransitionStateDependency,
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
    ScientificTransitionStateEntryRecord,
    TransitionStateCalculationSummary,
    TransitionStateComparedEnergySummary,
    TransitionStateCoreBlock,
    TransitionStateEntryStatmech,
    TransitionStateEntryValidationEvidence,
    TransitionStateSaddlePointEvidence,
    TransitionStateStatmechSourceCalculation,
    TransitionStateStatmechSummary,
    TransitionStateValidationEvidenceSummary,
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
_GATED_PREFIX_ALT = "|".join(sorted((re.escape(p) for p in GATED_TYPES), key=len, reverse=True))
GATED_REF_RE = re.compile("^(?:" + _GATED_PREFIX_ALT + r")_[a-z0-9]{26}\Z")
#: A ref inside text. The boundaries are alphanumeric only: ``_`` is not one, so ``_calc_...``, ``__calc_...__``,
#: ``calc_..._freq.log`` and ``foo_calc_...`` all contain a ref, while a suffixed or prefixed alphanumeric
#: look-alike (``calc_...x``, ``xcalc_...``) does not.
_REF_IN_TEXT = re.compile("(?<![a-z0-9])(?:" + _GATED_PREFIX_ALT + ")_[a-z0-9]{26}(?![a-z0-9])")


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
        # PR-2b: the transition-state, reaction-entry (/full) and kinetics families.
        #
        # Owner decision D1: a visible TS entry whose transition state is hidden stays visible with the owner block
        # blanked. Registering the block is what lets the guard blank it; widening the field alone is not enough.
        TransitionStateCoreBlock,
        TransitionStateCalculationSummary,
        TransitionStateEntryStatmech,
        TransitionStateEntryValidationEvidence,
        # An entry record nested in another record's ``entries`` goes whole when the entry is hidden. The subject of a
        # response (``record``, ``records[*]``) is never deleted as a pointer, so this is inert there.
        ScientificTransitionStateEntryRecord,
        TransitionStateSaddlePointEvidence,
        TransitionStateValidationEvidenceSummary,
        TransitionStateComparedEnergySummary,
        ReactionFullCalculationEvidenceSummary,
        ReactionFullIRCItem,
        ReactionFullScanItem,
        ReactionFullPathSearchItem,
        ReactionFullCalculationArtifacts,
        ReactionFullNetworkMembership,
        ReactionAtomMapBadge,
        ReactionAtomMapDetail,
        TransitionStateCalculationSlot,
        TransitionStateDependency,
        KineticsDeterminationBlock,
        KineticsInterpretationAssignmentBlock,
        KineticsTunnelingApplicationBlock,
        PathSearchSummary,
        ValidationSummary,
        SCFStabilitySummary,
        StoredKineticsProtocolCalculationRef,
        # PR-2c: the network, provenance (level of theory, corrections, literature) and analytics families.
        #
        # Owner decision D1: a visible solve (or fit) whose network is hidden stays visible with the network block
        # blanked. Registering each owner block is what lets the guard blank it; widening the field alone is not enough.
        NetworkContextSummary,
        NetworkKineticsNetworkContext,
        # Blocks whose subject is the linked record: a solve or fit of the network, a calculation a solve rests on, a
        # microreaction's transition-state entry, a per-participant energy source, one fit of a batch evaluation, a
        # record that uses a scale factor.
        NetworkSolveSummary,
        NetworkKineticsSummary,
        NetworkSourceCalculationSummary,
        NetworkChannelMicroreactionSummary,
        NetworkSolveStateEnergySourceSummary,
        NetworkKineticsBatchFitEvaluation,
        FrequencyScaleFactorUsageSummary,
    }
)

#: Untyped (``dict``) fields that are themselves a block about a linked record. A hidden ref anywhere inside one takes
#: the whole field to ``None``: a skeleton left behind (``{"type": "irc", "calculation_ref": null}``) would still say
#: that a hidden record exists. ``(model, field name)``; the field must be nullable.
POINTER_FIELDS: frozenset[tuple[type[BaseModel], str]] = frozenset(
    {
        (KineticsProvenance, "irc"),
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

#: PR-2b: the transition-state and reaction route families. ``/reaction-entries`` covers the ``/full`` read, the
#: reaction entry's kinetics and (matched, though they never reach the hook) its selection documents; ``/kinetics`` is
#: the kinetics search. Matched on whole path segments, so ``/reactions`` does not cover ``/reaction-entries``.
TS_REACTION_PREFIXES: frozenset[str] = frozenset(
    f"{_SCIENTIFIC_BASE}/{segment}"
    for segment in (
        "transition-states",
        "transition-state-entries",
        "reaction-entries",
        "reactions",
        "kinetics",
    )
)

#: PR-2c: the network family (pressure-dependent networks, their solves and fits), literature, analytics and the
#: provenance vocabulary reads (levels of theory, frequency scale factors, energy-correction and composite schemes).
#: ``/network-solves`` is not a sub-path of ``/networks``, so each is listed.
NETWORK_PROVENANCE_PREFIXES: frozenset[str] = frozenset(
    f"{_SCIENTIFIC_BASE}/{segment}"
    for segment in (
        "networks",
        "network-solves",
        "network-kinetics",
        "literature",
        "analytics",
        "level-of-theories",
        "frequency-scale-factors",
        "energy-correction-schemes",
        "composite-schemes",
    )
)

_ENABLED_ROUTE_PREFIXES: frozenset[str] = SPECIES_SIDE_PREFIXES | TS_REACTION_PREFIXES | NETWORK_PROVENANCE_PREFIXES


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


def guard_active(profile: ResolvedReadProfile) -> bool:
    """Is the guard in force for this profile? The one predicate every entry point asks (boundary hook, document mode,
    text refusal, the export batcher).

    It is when the profile has a review floor (``curated``) and is not release-backed. A release-backed profile
    (``tckdb_curated_release``) is short-circuited: a release is an attributed, immutable selection that names exactly
    the records its curators chose, with their provenance, so withholding refs from it would change a published,
    checksummed artifact. The general routes cannot take a release (``?release=`` is a 422), so only the release
    projections and artifacts, which are exempt by route as well, ever carry that profile. Under the exploratory
    profile there is no floor and nothing is hidden.
    """
    return (
        profile.review_floor is not None and profile.recommendation is not ProfileRecommendation.tckdb_curated_release
    )


def hidden_refs(
    session: Session,
    refs: Collection[str],
    *,
    profile: ResolvedReadProfile,
    unknown_is_hidden: bool = False,
) -> set[str]:
    """The subset of ``refs`` the profile hides. At most one statement; none when nothing can be hidden.

    With ``unknown_is_hidden`` a gated-looking ref that names no row at all is hidden too: hidden equals unknown, so a
    ref the database does not know must be treated exactly as one it hides (it is a ref to nothing visible, and its
    shape alone says nothing the reader can use). The document mode and the text refusal ask this; the typed hook does
    not (its refs come from rows the service just read).
    """
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
            select(model.public_ref, status_hidden.label("is_hidden"))
            .select_from(model)
            .outerjoin(
                RecordReview,
                and_(RecordReview.record_type == record_type, RecordReview.record_id == join_id),
            )
            .where(model.public_ref.in_(group))
        )
    if not parts:
        return set()
    stmt = parts[0] if len(parts) == 1 else union_all(*parts)
    rows = session.execute(stmt).all()
    hidden = {ref for ref, is_hidden in rows if is_hidden}
    if unknown_is_hidden:
        known = {ref for ref, _ in rows}
        hidden |= {ref for group in by_prefix.values() for ref in group if ref not in known}
    return hidden


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
    #: Document mode only: the withheld copy of the facts passed in (the input is never modified).
    facts: list[dict] | None = None
    #: Document mode only: ``hidden ref -> token`` for the modes that replace a ref by a stand-in. Always empty in
    #: ``"null"`` mode (guard PR-3 fills it in ``"placeholder"`` mode).
    tokens: dict[str, str] = field(default_factory=dict)
    #: Document mode only: the sites where free text was blanked whole (a subset of ``sites``).
    blanked: list[WithheldSite] = field(default_factory=list)
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


class _FreeRef(str):
    """A ref found inside free text (or the stand-in for text whose encoding never settles).

    The site is blanked as a whole field: a free-text value that names a hidden record is not editable token by token
    (the position and shape of what was removed would say what it was).
    """


#: Stands in for a free-text value that does not settle under percent-decoding. It is "hidden" by definition: the text
#: is blanked rather than guessed at, and never raises (a depositor's odd text must not fail a page).
UNSETTLED_TEXT = "unsettled_encoding"

#: Any gated ref anywhere in a string, no boundaries: a cheap superset pre-filter so ordinary text is not decoded.
_REF_ANYWHERE = re.compile("(?:" + _GATED_PREFIX_ALT + ")_[a-z0-9]{26}")


@functools.cache
def _invisible_characters() -> re.Pattern[str]:
    """Unicode category ``Cf`` (format) characters: soft hyphen, zero-width space and joiners, bidi marks, tags."""
    chars = "".join(chr(c) for c in range(sys.maxunicode + 1) if unicodedata.category(chr(c)) == "Cf")
    return re.compile("[" + re.escape(chars) + "]")


def _strip_invisible(text: str) -> str:
    """The text with invisible format characters removed. Only the copy used for matching is stripped."""
    return text if text.isascii() else _invisible_characters().sub("", text)


def _refs_in_text(text: str) -> list[str]:
    """Every gated ref in ``text``, after percent-decoding until stable and dropping invisible characters.

    Raises ``ValueError`` when the encoding does not settle. Boundaries are alphanumeric only (see
    :data:`_REF_IN_TEXT`), so a ref joined to other text by ``_`` is found.
    """
    decoded = _strip_invisible(_decode_stable(_strip_invisible(text)))
    return [m.group(0) for m in _REF_IN_TEXT.finditer(decoded)]


def _free_text_refs(value: str) -> list[str]:
    """Gated refs named by free text (a note, a label, a file name, a depositor URL).

    Returns :class:`_FreeRef` sites. Text whose percent-encoding does not settle is returned as the
    :data:`UNSETTLED_TEXT` stand-in, so the field is blanked, never kept and never an error.
    """
    text = _strip_invisible(value)  # ASCII (the common case) is returned as is; a soft hyphen cannot split a ref
    if "%" not in text and not _REF_ANYWHERE.search(text):
        return []
    try:
        refs = _refs_in_text(text)
    except ValueError:
        return [_FreeRef(UNSETTLED_TEXT)]
    return [_FreeRef(r) for r in refs]


def _refs_named_by(value: str, *, url_valued: bool = False) -> list[str]:
    """Every gated ref a string value names.

    A value that is exactly one ref (a stray newline allowed) is that ref, handled by the placement rules.

    A URL-valued field (``url_valued``: see :func:`_is_url_valued`) is parsed as a URL whatever its length or
    whitespace: a path segment (in the middle or at the end), a ``;params`` item, a query key or value, or the
    fragment, percent-decoded until stable. A value that cannot be parsed raises ``ValueError``, which the caller
    turns into :class:`RefGuardUnplaceable` (fail closed).

    Every other string is free text (:func:`_free_text_refs`): any gated ref in it makes the whole field a site, and
    a hidden one blanks the field. Uppercase and alphanumeric-suffixed look-alikes (``CALC_...``, ``calc_...x``) are
    not refs: no route resolves them. Invisible format characters are ignored when matching.
    """
    if len(value) <= 40:
        if GATED_REF_RE.match(value):
            return [value]
        stripped = value.strip()  # ``calc_...\n`` is that ref with a stray newline, and must not slip past
        if stripped != value and GATED_REF_RE.match(stripped):
            return [stripped]
    if not url_valued:
        return _free_text_refs(value)
    parts = urlsplit(value)
    found: list[str] = []
    for component in (parts.path, parts.query, parts.fragment):
        found.extend(_refs_in_text(component))
    return found


def _collect_sites(root: BaseModel | dict) -> list[tuple[tuple[_Frame, ...], str]]:
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


def _plan_free_text(path: tuple[_Frame, ...], ref: str) -> tuple[list[_Action], WithheldSite]:
    """Blank the whole field: ``None`` when it may be null, else ``""``. A pointer model above does not apply: the
    text belongs to a record that stays visible, and only the text goes."""
    text = _path_text(path)
    holder, key = path[-1]
    if isinstance(holder, BaseModel):
        nullable = _field_traits(type(holder), key)[0]
        return [_Action("null" if nullable else "empty", holder, key)], WithheldSite(ref, text, "blank_free_text")
    if isinstance(holder, dict):
        return [_Action("null", holder, key)], WithheldSite(ref, text, "blank_free_text")
    if isinstance(holder, list):
        return [_Action("empty", holder, key)], WithheldSite(ref, text, "blank_free_text")
    raise RefGuardUnplaceable(type(holder).__name__, text, ref.split("_", 1)[0])


def _plan_site(path: tuple[_Frame, ...], ref: str) -> tuple[list[_Action], WithheldSite]:
    if isinstance(ref, _FreeRef):
        return _plan_free_text(path, ref)
    text = _path_text(path)
    n = len(path) - 1

    # Rule 1: the nearest registered pointer on the way out. A pointer FIELD (an untyped block) counts at the field.
    for i in range(n, -1, -1):
        container, key = path[i]
        if (
            isinstance(container, BaseModel)
            and isinstance(key, str)
            and (type(container), key) in POINTER_FIELDS
            and _field_traits(type(container), key)[0]
        ):
            site = WithheldSite(ref, text, "null_pointer", f"{type(container).__name__}.{key}")
            return [_Action("null", container, key)], site
        if i == n:
            continue
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
        if action.kind in ("null", "empty"):
            value = None if action.kind == "null" else ""
            if isinstance(action.container, BaseModel):
                setattr(action.container, action.key, value)
            else:
                action.container[action.key] = value
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
            if check is None or not check(node):
                continue
            if not isinstance(container, BaseModel) or not _field_traits(type(container), key)[0]:
                # An empty shell with nowhere to go: refuse the response, never emit it (owner decision D4).
                raise RefGuardUnplaceable(
                    type(container).__name__,
                    f"{_path_text(path[: i + 1])}<empty {type(node).__name__}>",
                    ref.split("_", 1)[0],
                )
            if (id(container), key) in seen:
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

    With ``apply=False`` nothing is mutated (a dry run on a copy, reporting what would go, collapses included). Raises
    :class:`RefGuardUnplaceable` for a hidden ref that has nowhere safe to go, in either mode.
    """
    report = WithholdReport()
    if profile.review_floor is None:
        return report
    if not apply:
        # A dry run works on a copy, so what it reports (collapses included) is exactly what an applied run would do.
        return withhold_in_model(session, payload.model_copy(deep=True), profile=profile, apply=True)
    sites = _collect_sites(payload)
    report.refs_seen = len(sites)
    if not sites:
        return report
    report.hidden = hidden_refs(session, {ref for _, ref in sites}, profile=profile)
    report.hidden |= {ref for _, ref in sites if ref == UNSETTLED_TEXT}
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
    _apply(list(planned.values()))
    report.sites.extend(_collapse_empty(sites, report.hidden))
    return report


# ---------------------------------------------------------------------------
# Untyped documents (the export streams)
# ---------------------------------------------------------------------------


def _plan_document_site(path: tuple[_Frame, ...], ref: str) -> tuple[list[_Action], WithheldSite]:
    """Untyped mode: a plain dict/list has no pointer registry and no field traits, so only two removals exist.

    * a hidden ref that is a VALUE becomes ``None`` (free text that names one is blanked whole, to ``None``);
    * a hidden ref that is a string ELEMENT of a list is dropped from the list.

    A hidden ref used as a dict KEY has nowhere to go and fails closed (:class:`RefGuardUnplaceable`).
    """
    text = _path_text(path)
    holder, key = path[-1]
    if isinstance(key, _DictKey):
        raise RefGuardUnplaceable(type(holder).__name__, f"{text}<dict key>", ref.split("_", 1)[0])
    if isinstance(ref, _FreeRef):
        if isinstance(holder, dict):
            return [_Action("null", holder, key)], WithheldSite(ref, text, "blank_free_text")
        if isinstance(holder, list):
            return [_Action("delete", holder, key)], WithheldSite(ref, text, "blank_free_text")
        raise RefGuardUnplaceable(type(holder).__name__, text, ref.split("_", 1)[0])
    if isinstance(holder, dict):
        return [_Action("null", holder, key)], WithheldSite(ref, text, "null_untyped")
    if isinstance(holder, list):
        return [_Action("delete", holder, key)], WithheldSite(ref, text, "delete_list_element")
    raise RefGuardUnplaceable(type(holder).__name__, text, ref.split("_", 1)[0])


def withhold_in_document(
    session: Session,
    facts: list[dict],
    *,
    profile: ResolvedReadProfile,
    mode: Literal["null", "placeholder"],
) -> WithholdReport:
    """Withhold every ref the profile hides from plain documents, returning a withheld COPY.

    For documents that are not response models: the export streams' records (guard PR-4) and the selection documents
    (guard PR-3). ``facts`` is a list of plain dicts; it is **not** modified. The withheld copy is
    ``report.facts`` (same order, same length), and a caller must use it: ignoring the return value withholds
    nothing. The report also carries ``tokens`` (always empty in ``"null"`` mode) and ``blanked`` (the free-text sites
    that were blanked whole). ``session`` is the leading positional argument and is always passed explicitly: the
    caller reads visibility on the session that read the rows (for a placeholder, the engine's snapshot session). ``profile`` is passed in, never read from the request context, because a stream outlives the request;
    ``session`` is the one that read the rows, so visibility is judged from the same snapshot. One visibility
    statement per call, however many documents, and none when no document names a gated ref.

    ``mode`` is required (no default) and says what a withheld ref becomes:

    - ``"null"`` (exports): a ref that is a *value* becomes ``None``; a ref that is a string *element of a list* is
      dropped from the list.
    - ``"placeholder"`` (selection documents, PR-3): not implemented here; raises ``NotImplementedError``.

    Rules every mode shares, so PR-3 inherits them:

    - **Dict key.** A ref used as a dict key has nowhere to go. In ``"null"`` mode it fails closed
      (:class:`RefGuardUnplaceable`); ``"placeholder"`` mode defines its own rule.
    - **Free text.** A string that merely names a hidden ref anywhere in it (a note, a label, a file name, a URL) is
      blanked whole (never token by token: what was removed would show), and in a list the element is dropped. Text
      whose percent-encoding never settles is blanked too. The blanking rule is the one PR-2a set for typed models:
      ``None`` when the field is nullable, ``""`` when it is a required ``str``. Untyped documents such as the
      exports carry no type information, so they always get ``None``.
    - **Unknown refs.** A gated-looking ref that names no row is treated as hidden (``unknown_is_hidden``): hidden
      equals unknown, so the two must read the same.
    - **Release-backed profiles.** Short-circuited with the exploratory profile (:func:`guard_active`): the copy is
      returned unchanged and no statement is issued, because a release is an attributed, immutable selection.
    """
    if mode not in ("null", "placeholder"):
        raise ValueError(f"mode must be 'null' or 'placeholder', not {mode!r}")
    if mode == "placeholder":
        raise NotImplementedError(
            "withhold_in_document(mode='placeholder') belongs to curated-guard PR-3 (selection documents); "
            "this PR implements mode='null' for the export streams"
        )
    report = WithholdReport()
    withheld = copy.deepcopy(facts)
    report.facts = withheld
    if not guard_active(profile):
        return report
    sites: list[tuple[tuple[_Frame, ...], str]] = []
    for document in withheld:
        sites.extend(_collect_sites(document))
    report.refs_seen = len(sites)
    if not sites:
        return report
    report.hidden = hidden_refs(session, {ref for _, ref in sites}, profile=profile, unknown_is_hidden=True)
    report.hidden |= {ref for _, ref in sites if ref == UNSETTLED_TEXT}
    if not report.hidden:
        return report
    planned: dict[tuple[int, Any, str], _Action] = {}
    for path, ref in sites:
        if ref not in report.hidden:
            continue
        actions, site = _plan_document_site(path, ref)
        report.sites.append(site)
        if site.action == "blank_free_text":
            report.blanked.append(site)
        for action in actions:
            planned.setdefault((id(action.container), action.key, action.kind), action)
    _apply(list(planned.values()))
    return report


def refuse_hidden_refs_in_text(session: Session, texts: Mapping[str, str], *, profile: ResolvedReadProfile) -> None:
    """Fail closed when generated text (the CHEMKIN files) names a hidden ref.

    Text has no structure to withhold from, so a hidden ref in it is a bug in whatever produced it (a source that
    should have been filtered), and the export is refused rather than emitted. ``texts`` maps a file name to its text.
    """
    if not guard_active(profile):
        return
    found: dict[str, str] = {}
    for name, text in texts.items():
        if not (_REF_ANYWHERE.search(text) or "%" in text):
            continue
        try:
            refs = _refs_in_text(text)
        except ValueError as exc:
            # Percent-encoding that never settles: the text cannot be read, so it is refused, never kept (D4).
            logger.error("ref_guard_unplaceable_text file=%s prefix=unsettled", name)
            raise RefGuardUnplaceable("text", f"{name}<unsettled encoding>", "text") from exc
        for ref in refs:
            found.setdefault(ref, name)
    if not found:
        return
    hidden = hidden_refs(session, set(found), profile=profile, unknown_is_hidden=True)
    if hidden:
        ref = sorted(hidden)[0]
        logger.error("ref_guard_unplaceable_text file=%s prefix=%s", found[ref], ref.split("_", 1)[0])
        raise RefGuardUnplaceable("text", found[ref], ref.split("_", 1)[0])


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
    if not guard_active(profile):
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
    "NETWORK_PROVENANCE_PREFIXES",
    "POINTER_FIELDS",
    "POINTER_MODELS",
    "SPECIES_SIDE_PREFIXES",
    "TS_REACTION_PREFIXES",
    "UNGATED_REVIEWABLE",
    "GatedType",
    "RefGuardContext",
    "RefGuardUnplaceable",
    "WithheldSite",
    "WithholdReport",
    "current_ref_guard_context",
    "guard_active",
    "hidden_refs",
    "refuse_hidden_refs_in_text",
    "reset_ref_guard_context",
    "route_matches",
    "set_ref_guard_context",
    "withhold_for_current_request",
    "withhold_in_document",
    "withhold_in_model",
]
