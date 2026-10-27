"""One guard for refs of records the read profile withholds.

Under ``?profile=curated`` the approved **record floor** decides which records a list, search or select returns. A
returned record's **linked evidence** (its source calculations, its transition state, its conformer group) is shown with
its review badge, and only evidence at ``rejected`` or ``deprecated`` is withheld (ADR 0023). A name is still a ref, and
a ref to withheld evidence proves it exists and lets a reader track it. This module is the single place that finds
those refs in a finished response and removes them.

Two sets, never one
-------------------
* The **evidence set** (:func:`withheld_evidence_refs`): refs whose record is ``rejected`` or ``deprecated``. It drives
  EVERY nested withhold below: the typed hook, the document modes, the selection loaders and the export batcher. A nested
  pointer to a ``not_reviewed`` or ``under_review`` record is not in it, so it stays. Its badge travels in the response's
  ``review_status_by_ref`` map (:func:`attach_review_statuses`), because most pointer models have no badge field of their own.
* The **record-floor set** (:func:`below_floor_refs`): refs below ``approved``. It is used for one thing only: to fail
  loud when the SUBJECT of a response is below the floor, which means a service forgot its population filter. The
  subject's identity is declared per response model in :data:`SUBJECT_IDENTITY` (a path such as
  ``calculation.calculation_ref``), never inferred from a child named after the kind: a parent transition state or a
  solve's network is context. A test fails for a subject model with no entry. That safety net is not a rule about nested
  refs.

What it does, and refuses to do
-------------------------------
It only ever **deletes or blanks**. It never picks another record to fill a gap (the substitution bug
fixed in #767), because it runs after every service has made its picks and can only subtract from what they
produced. It does nothing under the default (exploratory) profile.

How a withheld ref is removed (:func:`withhold_in_model`)
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
   subject ref below the record floor means a service forgot its population filter, which is exactly what should be loud.
4. Free text (a note, a label, a file name, a depositor URL) that names a hidden ref anywhere in its tokens is
   blanked as a whole field: ``None`` when the field is nullable, else ``""``. Never token by token (the shape of
   what was removed would show), and text whose percent-encoding does not settle is blanked too, never refused.
   Exploratory text is untouched. A required field whose WHOLE value is a bare hidden ref is not free text: it is
   a ref in the wrong place (most likely a pointer the registry missed), so it raises :class:`RefGuardUnplaceable`
   and the registry gets fixed (D4).

Selection documents
-------------------
A selection engine is a pure function of the facts its loader captured, and the manifest it writes is hashed, replayed
and compared over those facts. Two kinds of nested ref are handled differently, per position:

* **Evidence** (a supporting or protocol calculation, an energy source, a network solve, a validation reference, an
  anchoring group): a rejected or deprecated record reads as **unavailable**, exactly like a reference to nothing
  (withheld == unknown). The loader leaves it out (:func:`unavailable_refs`), nothing stands in its place, and counts follow;
  the curated decision may differ from the exploratory one, which is intended. A not-reviewed or under-review record is
  available evidence. The proof is withheld == absent.
* **Identity** (a determination's target, a candidate's own target group, a supersession pointer): the engine needs the
  ref to compare or partition. Every such withheld ref in the captured facts becomes a placeholder token (``withheld:<n>``,
  :data:`WITHHELD_TOKEN_RE`, :func:`withhold_in_document` with ``mode="placeholder"``) *before* anything is assessed,
  decided or hashed. ``n`` follows the sorted order of the real refs and one real ref has one token in a document, so
  every equality survives and a replay of the manifest runs over the tokens and reproduces it byte for byte.

Scope
-----
The hook applies to every scientific route that is not listed in :data:`EXEMPT_ROUTE_PREFIXES`, each with its reason.
There is no allowlist: a route added tomorrow is guarded from the day it exists. The selection documents and the exports
reach the guard through their own seams (see above).
"""

from __future__ import annotations

import copy
import dataclasses
import enum
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
from sqlalchemy import and_, select, union_all
from sqlalchemy.orm import Session
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
from app.services.scientific_read.common import evidence_withheld_under_profile
from app.services.scientific_read.handles import status_hidden_under_profile
from app.services.scientific_read.profile import ResolvedReadProfile, current_read_profile
from app.services.trust.models import TransitionStateScopedAssessmentUnit

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
        # #723: one assessed declared target in the transition-state trust fragment. A unit's subject is the
        # declaration, so a hidden ref drops the whole unit instead of raising on a read.
        TransitionStateScopedAssessmentUnit,
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


class SubjectNotShown(RefGuardUnplaceable):
    """A record the read profile does not show was returned as the ANSWER of a read: the service's population filter is missing.

    For a list row that is a record below the record floor; for the ``record`` of a detail read it is a record in the
    withheld set (``rejected``, ``deprecated``), which the detail gate answers 404 for (ADR 0023 step 7).

    A subclass of :class:`RefGuardUnplaceable` (it fails the request the same way) with its own message: the remedy is the
    service's floor, not the field's optionality and not ``POINTER_MODELS``.
    """

    def __init__(self, model_name: str, field_path: str, ref_prefix: str) -> None:
        RuntimeError.__init__(
            self,
            f"a {ref_prefix}_ record the read profile does not show was returned as a result at {field_path} ({model_name}); "
            "the service's population filter is missing",
        )
        self.model_name = model_name
        self.field_path = field_path
        self.ref_prefix = ref_prefix


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
    #: Selection documents that passed the loader to engine seam (:func:`withhold_selection_facts`), and what it did.
    documents: int = 0
    document_reports: list[WithholdReport] = field(default_factory=list)
    #: The review status of every gated ref any lookup of this request fetched (:func:`classify_refs`).
    statuses: dict[str, RecordReviewStatus] = field(default_factory=dict)
    #: A legacy detail read under a floor: the review status of the record it served (``X-TCKDB-Review-Status``).
    served_review_status: str | None = None


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


def _review_statuses(session: Session, refs: Collection[str]) -> dict[str, RecordReviewStatus]:
    """The review status of every gated ref in ``refs`` that names a row. At most one statement; none for no gated ref.

    A record with no review row reads as ``not_reviewed``. A ref that names no row is absent from the result.
    """
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
        parts.append(
            select(model.public_ref, RecordReview.status)
            .select_from(model)
            .outerjoin(
                RecordReview,
                and_(RecordReview.record_type == record_type, RecordReview.record_id == join_id),
            )
            .where(model.public_ref.in_(group))
        )
    if not parts:
        return {}
    stmt = parts[0] if len(parts) == 1 else union_all(*parts)
    return {ref: (status if status is not None else RecordReviewStatus.not_reviewed) for ref, status in session.execute(stmt).all()}


def _gated_refs(refs: Collection[str]) -> set[str]:
    return {ref for ref in refs if ref.split("_", 1)[0] in GATED_TYPES}


@dataclass(frozen=True)
class RefSets:
    """What one lookup found: the evidence set, and which of the asked-about SUBJECT refs are below the record floor."""

    evidence: set[str]
    subject_below_floor: set[str]
    #: The review status of every gated ref the lookup found (a missing review row reads as ``not_reviewed``). It is what the
    #: lookup fetched anyway: it feeds the badge map (:func:`attach_review_statuses`) without another statement.
    statuses: dict[str, RecordReviewStatus] = field(default_factory=dict)


def classify_refs(
    session: Session,
    refs: Collection[str],
    *,
    profile: ResolvedReadProfile,
    subject_refs: Collection[str] = (),
    unknown_is_hidden: bool = False,
) -> RefSets:
    """Both sets from one statement (none when there is no floor, or no gated ref).

    ``evidence`` (ADR 0023) is the subset of ``refs`` whose record is ``rejected`` or ``deprecated`` (:data:`WITHHELD_
    EVIDENCE_STATUSES`) or, with ``unknown_is_hidden``, names no row (withheld equals unknown: a ref the database does not
    know reads exactly as one it withholds; the document modes and the text refusal ask this, the typed hook does not
    because its refs come from rows the service just read). A record that is merely ``not_reviewed`` or ``under_review``
    (or has no review row) is NOT in it: a nested pointer to such a record is shown, with its badge where the model has one.
    The evidence set drives every nested withhold: the typed hook, the document modes, the export batcher and the selection
    loaders.

    ``subject_below_floor`` is the subset of ``subject_refs`` below the profile's review floor (a missing row reads as
    ``not_reviewed``). It is used only to fail loud when a response's subject is below the floor, a service that forgot
    its population filter; it never decides what a nested pointer does.
    """
    if profile.review_floor is None or not refs:
        return RefSets(set(), set())
    gated = _gated_refs(refs)
    statuses = _review_statuses(session, gated)
    evidence = {ref for ref, status in statuses.items() if evidence_withheld_under_profile(status, profile=profile)}
    below = {
        ref
        for ref in subject_refs
        if ref in statuses and status_hidden_under_profile(statuses[ref], profile=profile)
    }
    if unknown_is_hidden:
        evidence |= {ref for ref in gated if ref not in statuses}
    ctx = _CURRENT_CONTEXT.get()
    if ctx is not None:
        ctx.statuses.update(statuses)
    return RefSets(evidence, below, dict(statuses))


def withheld_evidence_refs(
    session: Session,
    refs: Collection[str],
    *,
    profile: ResolvedReadProfile,
    unknown_is_hidden: bool = False,
) -> set[str]:
    """The EVIDENCE SET alone (:func:`classify_refs`): the refs whose record is ``rejected`` or ``deprecated``."""
    return classify_refs(session, refs, profile=profile, unknown_is_hidden=unknown_is_hidden).evidence


def below_floor_refs(
    session: Session,
    refs: Collection[str],
    *,
    profile: ResolvedReadProfile,
) -> set[str]:
    """The RECORD-FLOOR SET: the subset of ``refs`` whose record is below the profile's review floor.

    Not a rule about nested refs. It exists for the subject safety net (:func:`classify_refs`) and for tests that pin the
    two sets apart.
    """
    return classify_refs(session, refs, profile=profile, subject_refs=refs).subject_below_floor


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
    facts: Any = None
    #: Document mode only: ``hidden ref -> token`` for the modes that replace a ref by a stand-in. Always empty in
    #: ``"null"`` mode (guard PR-3 fills it in ``"placeholder"`` mode).
    tokens: dict[str, str] = field(default_factory=dict)
    #: Document mode only: the sites where free text was blanked whole (a subset of ``sites``).
    blanked: list[WithheldSite] = field(default_factory=list)
    refs_seen: int = 0
    hidden: set[str] = field(default_factory=set)
    #: Statuses of the gated refs the lookup found (the badge map's source for the export streams).
    statuses: dict[str, RecordReviewStatus] = field(default_factory=dict)
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
            if isinstance(node, dict) and isinstance(key, str):
                # A ref used as a dict KEY cannot be blanked or deleted without renaming the entry. Recorded as a site so
                # that, if it is hidden, the guard fails closed instead of letting it through. A key is read as a value
                # is: a stray newline, invisible characters, percent-encoding and a ref inside longer text all count.
                sites.extend(((*path, (node, _DictKey(key))), ref) for ref in _refs_named_by(key))
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


#: The identity of each response model that is the SUBJECT of a scientific read (the model of ``record`` or of an element of
#: ``records``): the field path from the subject to the ref that says which record the row IS. A subject whose identity ref is
#: below the record floor means a service forgot its population filter, and the guard fails loud (:func:`classify_refs`).
#:
#: Keyed by class name. The path is declared, never inferred from a child named after the kind: on a transition-state
#: entry record ``transition_state.transition_state_ref`` is the entry's parent (context, shown badged), and on a solve row
#: ``network.network_ref`` is the solve's network (context). ``test_every_subject_model_has_a_registered_identity`` fails for
#: a response model that appears as ``record`` / ``records`` and is in neither this table nor :data:`UNGATED_SUBJECTS`.
SUBJECT_IDENTITY: dict[str, tuple[str, ...]] = {
    "CalculationAnalyticsRecord": ("calculation_ref",),
    "KineticsAnalyticsRecord": ("kinetics_ref",),
    "KineticsRecord": ("kinetics_ref",),
    "KineticsSearchRecord": ("kinetics", "kinetics_ref"),
    #: Identity is ``record_ref`` whatever kind of record the row is; the ref's own prefix says which gate applies.
    "LiteratureLinkedRecordSummary": ("record_ref",),
    "MolecularPropertyObservationRecord": ("observation_ref",),
    #: An artifact is served under its calculation's floor (the artifact itself has no review row).
    "ScientificArtifactRecord": ("calculation", "calculation_ref"),
    "ScientificCalculationRecord": ("calculation", "calculation_ref"),
    "ScientificConformerGroupRecord": ("conformer_group", "conformer_group_ref"),
    "ScientificConformerObservationRecord": ("conformer_observation", "conformer_observation_ref"),
    "ScientificNetworkKineticsRecord": ("network_kinetics", "network_kinetics_ref"),
    "ScientificNetworkRecord": ("network", "network_ref"),
    "ScientificNetworkSolveRecord": ("network_solve", "network_solve_ref"),
    "ScientificStatmechRecord": ("statmech", "statmech_ref"),
    "ScientificTransitionStateEntryRecord": ("transition_state_entry", "transition_state_entry_ref"),
    "ScientificTransitionStateRecord": ("transition_state", "transition_state_ref"),
    "ScientificTransportRecord": ("transport", "transport_ref"),
    "SpeciesCalculationsSearchRecord": ("calculation", "calculation_ref"),
    "StatmechAnalyticsRecord": ("statmech_ref",),
    "ThermoAnalyticsRecord": ("thermo_ref",),
    "ThermoRecord": ("thermo_ref",),
    "ThermoSearchRecord": ("thermo", "thermo_ref"),
}

#: Subject models whose identity is not a review-gated record, each with the reason.
UNGATED_SUBJECTS: dict[str, str] = {
    **dict.fromkeys(
        ("SpeciesScientificRecord", "ReactionScientificRecord", "ScientificSpeciesStructureSearchRecord"),
        "species and reaction identity rows are scoping parents and never gate (ADR 0023)",
    ),
    **dict.fromkeys(
        (
            "ScientificCompositeSchemeRecord",
            "ScientificEnergyCorrectionSchemeRecord",
            "ScientificFrequencyScaleFactorRecord",
            "ScientificLevelOfTheoryRecord",
            "ScientificLiteratureRecord",
        ),
        "reference or provenance row with no review state of its own",
    ),
    **dict.fromkeys(
        ("DatasetReleaseSummary", "ReleaseSelectionRecord"),
        "dataset releases are exempt from the record floor and the evidence rule (ADR 0023, Q7)",
    ),
    "ArtifactIntegrityDigestSummary": "an integrity digest of a stored file; not a reviewed record",
}


def subject_identity_site(path: tuple[_Frame, ...]) -> tuple[str, ...] | None:
    """The identity path of the subject model a site sits under, when the site IS at that path; else ``None``."""
    for i in (0, 1):
        if not _is_subject(path, i) or len(path) <= i + 1:
            continue
        identity = SUBJECT_IDENTITY.get(type(path[i + 1][0]).__name__)
        keys = tuple(key for _, key in path[i + 1 :])
        return identity if identity is not None and keys == identity else None
    return None


def _is_detail_subject(path: tuple[_Frame, ...]) -> bool:
    """Is the site under the ``record`` of a response (the answer of a detail-by-ref read), not a row of ``records``?"""
    return bool(path) and path[0][1] == "record"


def _is_subject_own_ref(path: tuple[_Frame, ...], ref: str) -> bool:
    """Is this site the subject's identity ref, as the registry (:data:`SUBJECT_IDENTITY`) declares it?

    The subject is what the route returns as its answer. Every other ref on it (a source calculation, a parent transition
    state, a network) is linked evidence and follows the evidence rule.
    """
    return subject_identity_site(path) is not None and ref.split("_", 1)[0] in GATED_TYPES


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
#: "no owner" (withheld equals unknown). ``model -> is it empty``.
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
    if not guard_active(profile):
        return report
    if not apply:
        # A dry run works on a copy, so what it reports (collapses included) is exactly what an applied run would do.
        return withhold_in_model(session, payload.model_copy(deep=True), profile=profile, apply=True)
    sites = _collect_sites(payload)
    report.refs_seen = len(sites)
    if not sites:
        return report
    # A list subject (an element of ``records``) is judged by the record floor; a detail subject (``record``) is the answer
    # of a detail-by-ref read, which serves anything but the withheld set (ADR 0023 step 7), so it is judged by that set.
    subject_refs = {ref for path, ref in sites if _is_subject_own_ref(path, ref) and not _is_detail_subject(path)}
    found = classify_refs(session, {ref for _, ref in sites}, profile=profile, subject_refs=subject_refs)
    report.hidden = found.evidence
    report.statuses = found.statuses
    report.hidden |= {ref for _, ref in sites if ref == UNSETTLED_TEXT}
    # The safety net: a listed subject below the record floor, or a detail subject in the withheld set, means a service
    # forgot its population filter (or the detail gate), whatever its badge would say. Only the evidence set says what a
    # nested pointer does.
    for path, ref in sites:
        if not _is_subject_own_ref(path, ref):
            continue
        if ref in (found.evidence if _is_detail_subject(path) else found.subject_below_floor):
            raise SubjectNotShown(type(path[-1][0]).__name__, _path_text(path), ref.split("_", 1)[0])
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
    facts: Any,
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
    - ``"placeholder"`` (selection documents, PR-3): ``facts`` is whatever plain data a loader captured (frozen
      dataclasses, tuples, dicts, lists, strings, nested), not only dicts, and is never mutated: the withheld copy shares
      every subtree that held no hidden ref and is the very object passed in when nothing is hidden. A ref that is a whole
      string value, or a dict key, becomes its token ``withheld:<n>``; ``n`` follows the **sorted order of the real refs**,
      and one real ref has one token in a document. A composite ``<ref>/<channel key>`` in a field of
      :data:`COMPOSITE_REF_FIELDS`, and only there, with a key that matches the strict channel-key grammar and names no
      gated ref, has its ref replaced and its key kept. Any other string that names a hidden ref is blanked whole, to
      ``""`` and never ``None``: the one exception to the "``None`` when nullable" rule, because a selection engine reads
      the PRESENCE of such text (a torsion's ``invalidated_reason`` is tested with ``is not None``), and the
      invalidation is the visible record's own claim about itself. The walker builds new immutable structures rather
      than planning in-place actions, so it does not use the typed planner below.

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
        return _withhold_placeholders(session, facts, profile=profile)
    report = WithholdReport(facts=facts)
    if not guard_active(profile):
        return report
    # Read the documents first: nothing is copied unless something is withheld, and then ``report.facts`` is a copy.
    sites: list[tuple[tuple[_Frame, ...], str]] = []
    for document in facts:
        sites.extend(_collect_sites(document))
    report.refs_seen = len(sites)
    if not sites:
        return report
    found = classify_refs(session, {ref for _, ref in sites}, profile=profile, unknown_is_hidden=True)
    report.hidden = found.evidence
    report.statuses = found.statuses
    report.hidden |= {ref for _, ref in sites if ref == UNSETTLED_TEXT}
    if not report.hidden:
        return report
    withheld = copy.deepcopy(facts)
    report.facts = withheld
    sites = []
    for document in withheld:
        sites.extend(_collect_sites(document))
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


#: What a hidden gated ref becomes inside a selection document. Not a ref: no route resolves it, no prefix matches it.
WITHHELD_TOKEN_PREFIX = "withheld:"
WITHHELD_TOKEN_RE = re.compile(r"^withheld:[1-9][0-9]*\Z")

#: Fields of a selection fact whose value is ``<gated ref>/<channel key>``, compared for equality with the parts the engine
#: holds elsewhere. Only these are ever rewritten (ref to token); a composite-looking value in any other field is prose.
COMPOSITE_REF_FIELDS: frozenset[str] = frozenset({"network_channel_ref"})
#: ``<gated ref>/<key>``, the key a channel key as the schema writes them (``assoc``, ``elim_alt``, ``ch-1.2``): letters,
#: digits, ``.``, ``_`` and ``-``, starting with a letter or digit, no percent sign, no slash, no invisible character.
_COMPOSITE_REF = re.compile("^((?:" + _GATED_PREFIX_ALT + r")_[a-z0-9]{26})(/[A-Za-z0-9][A-Za-z0-9._\-]*)\Z")


def is_withheld_token(value: object) -> bool:
    """Whether ``value`` is exactly one placeholder token."""
    return isinstance(value, str) and WITHHELD_TOKEN_RE.match(value) is not None


def _document_string_refs(value: str, key: Any) -> list[str]:
    """Every gated ref a string value of a captured fact names (whole, composite or in free text)."""
    if not value:
        return []
    return _refs_named_by(value, url_valued=_is_url_valued(key))


def _rebuild(node: Any, changes: dict[Any, Any]) -> Any:
    """``node`` with ``changes`` ({field name: new child}) applied, as a shallow copy; the original is never mutated."""
    if dataclasses.is_dataclass(node) and not isinstance(node, type):
        clone = copy.copy(node)
        for name, value in changes.items():
            object.__setattr__(clone, name, value)
        return clone
    if isinstance(node, BaseModel):
        return node.model_copy(update=changes)
    raise TypeError(f"cannot rebuild {type(node).__name__}")  # pragma: no cover


def _transform(node: Any, key: Any, on_string, on_key) -> Any:
    """Walk plain captured facts, passing every string through ``on_string(value, key)`` and every dict key through
    ``on_key``, and return the result. A subtree that did not change is returned as the same object. Enums (including
    ``str`` enums), numbers, datetimes and anything that is not plain data pass through untouched."""
    if node is None or isinstance(node, (enum.Enum, bool, int, float, bytes)):
        return node
    if isinstance(node, str):
        return on_string(node, key)
    if isinstance(node, dict):
        items = [(on_key(k) if isinstance(k, str) else k, _transform(v, k, on_string, on_key)) for k, v in node.items()]
        unchanged = all(nk == k and nv is v for (nk, nv), (k, v) in zip(items, node.items(), strict=True))
        return node if unchanged else dict(items)
    if isinstance(node, (list, tuple, set, frozenset)):
        children = list(node)
        new = [_transform(c, key, on_string, on_key) for c in children]
        if all(a is b for a, b in zip(children, new, strict=True)):
            return node
        if isinstance(node, list):
            return new
        return type(node)(new) if isinstance(node, (set, frozenset)) else tuple(new)
    if dataclasses.is_dataclass(node) and not isinstance(node, type):
        changes = {}
        for f in dataclasses.fields(node):
            value = getattr(node, f.name)
            replaced = _transform(value, f.name, on_string, on_key)
            if replaced is not value:
                changes[f.name] = replaced
        return _rebuild(node, changes) if changes else node
    if isinstance(node, BaseModel):
        changes = {}
        for name in type(node).model_fields:
            value = getattr(node, name)
            replaced = _transform(value, name, on_string, on_key)
            if replaced is not value:
                changes[name] = replaced
        return _rebuild(node, changes) if changes else node
    return node


def _withhold_placeholders(session: Session, facts: Any, *, profile: ResolvedReadProfile) -> WithholdReport:
    """``mode="placeholder"`` of :func:`withhold_in_document`: hidden refs become ``withheld:<n>`` in a withheld copy."""
    report = WithholdReport(facts=facts)
    if not guard_active(profile):
        return report
    seen: dict[str, None] = {}

    def collect_string(value: str, key: Any) -> str:
        try:
            for ref in _document_string_refs(value, key):
                seen[ref] = None
        except ValueError as exc:
            # A URL-valued field the guard cannot read is refused, never kept (owner decision D4).
            raise RefGuardUnplaceable("document", str(key), "?") from exc
        return value

    def collect_key(key: str) -> str:
        for ref in _refs_named_by(key) if key else ():
            seen[ref] = None
        return key

    _transform(facts, None, collect_string, collect_key)
    report.refs_seen = len(seen)
    if not seen:
        return report
    found = classify_refs(session, set(seen), profile=profile, unknown_is_hidden=True)
    hidden = found.evidence
    if not hidden:
        return report
    report.hidden = set(hidden)
    tokens = {ref: f"{WITHHELD_TOKEN_PREFIX}{n}" for n, ref in enumerate(sorted(hidden), start=1)}
    report.tokens = tokens

    def blanked(value: str, key: Any) -> str:
        report.blanked.append(WithheldSite(value, str(key), "blank_free_text"))
        return ""

    def replace_string(value: str, key: Any) -> str:
        named = _document_string_refs(value, key)
        if UNSETTLED_TEXT in named:
            return blanked(value, key)  # text whose encoding never settles is blanked, never kept
        if not any(ref in tokens for ref in named):
            return value
        if value.strip() in tokens:
            return tokens[value.strip()]
        composite = _COMPOSITE_REF.match(value) if isinstance(key, str) and key in COMPOSITE_REF_FIELDS else None
        if composite is not None and composite.group(1) in tokens and not _free_text_refs(composite.group(2)):
            # ``<ref>/<channel key>`` in a registered field and exactly that: the key names no gated ref (hidden or not,
            # under the same boundary matcher as everywhere), so the ref becomes its token and the key stays.
            return tokens[composite.group(1)] + composite.group(2)
        # Any other string that names a hidden ref (prose, a depositor URL) is blanked whole, to ``""`` and never ``None``:
        # an engine may read the PRESENCE of the text, and replacing the ref inside it would have to find it through
        # percent-encoding and invisible characters again (a miss leaves the ref in place).
        return blanked(value, key)

    def replace_key(key: str) -> str:
        named = _refs_named_by(key) if key else []
        if not any(ref in tokens or ref == UNSETTLED_TEXT for ref in named):
            return key
        try:
            normalised = _strip_invisible(_decode_stable(_strip_invisible(key))).strip()
        except ValueError:
            normalised = None
        if normalised in tokens:
            return tokens[normalised]  # exactly a hidden ref once normalised: renamed to its token
        # A key that merely contains a hidden ref cannot be blanked (the entry would lose its name): fail closed.
        raise RefGuardUnplaceable("document", "<dict key>", next(iter(ref for ref in named if ref in tokens), "?").split("_", 1)[0])

    report.facts = _transform(facts, None, replace_string, replace_key)
    return report


def unavailable_refs(session: Session, refs: Collection[str]) -> set[str]:
    """Refs a selection treats as unavailable under the profile in force: withheld, or naming no row.

    Withheld == unknown: evidence that points at a record the reader cannot open reads exactly like evidence that
    points at
    nothing. Empty (and no statement) without a review floor, so an exploratory selection is unchanged; a release-backed
    profile is left alone as everywhere in the guard. ``session`` is the selection's own snapshot session.
    """
    profile = current_read_profile()
    if not guard_active(profile) or not refs:
        return set()
    return withheld_evidence_refs(session, refs, profile=profile, unknown_is_hidden=True)


def withhold_selection_facts(session: Session, facts: Any) -> Any:
    """The seam every selection engine calls once, between its loader and its assessment.

    Reads the profile in force; with no review floor (exploratory, the default) or a release-backed profile it returns
    ``facts`` untouched and runs no statement. ``session`` must be the selection's own snapshot session, so visibility
    comes from the same state as the population. Returns the facts with hidden refs tokenised; the report is recorded on
    the request's guard context when there is one (the sweep's spy counts the call as reaching the boundary).
    """
    ctx = _CURRENT_CONTEXT.get()
    if ctx is not None:
        ctx.documents += 1
    profile = current_read_profile()
    if not guard_active(profile):
        return facts
    report = withhold_in_document(session, facts, profile=profile, mode="placeholder")
    if ctx is not None:
        ctx.document_reports.append(report)
    return report.facts



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
    hidden = withheld_evidence_refs(session, set(found), profile=profile, unknown_is_hidden=True)
    if hidden:
        ref = sorted(hidden)[0]
        logger.error("ref_guard_unplaceable_text file=%s prefix=%s", found[ref], ref.split("_", 1)[0])
        raise RefGuardUnplaceable("text", found[ref], ref.split("_", 1)[0])


# ---------------------------------------------------------------------------
# Boundary hook
# ---------------------------------------------------------------------------


def refs_present(payload: Any) -> set[str]:
    """Every gated ref a response (or a plain document) names as a whole value, the request echo excepted."""
    return {ref for _, ref in _collect_sites(payload) if not isinstance(ref, _FreeRef)}


def badges_for(refs: Collection[str], statuses: Mapping[str, RecordReviewStatus]) -> dict[str, RecordReviewStatus]:
    """The badge map for ``refs``: those with a fetched status, sorted. A ref the lookup did not fetch has no entry."""
    return {ref: statuses[ref] for ref in sorted(refs) if ref in statuses}


def attach_review_statuses(payload: BaseModel) -> None:
    """Fill ``payload.review_status_by_ref`` with the review badge of every gated ref the finished response names.

    Called AFTER the response was withheld from, so a withheld ref is no longer in the payload and can never reach the map.
    The statuses are the ones the guard's lookups already fetched for this request (no statement of its own). Under the
    exploratory profile, a release-backed profile, an exempt route or a payload without the field, nothing is attached and the
    field stays null.
    """
    ctx = _CURRENT_CONTEXT.get()
    if ctx is None or not hasattr(payload, "review_status_by_ref"):
        return
    profile = current_read_profile()
    if not guard_active(profile) or route_matches(ctx.route_path, EXEMPT_ROUTE_PREFIXES):
        return
    payload.review_status_by_ref = badges_for(refs_present(payload), ctx.statuses)


def route_matches(route_path: str, prefixes: Collection[str]) -> bool:
    """Prefix match on whole path segments: ``/meta`` covers ``/meta/x`` but not ``/metadata``."""
    return any(route_path == p or route_path.startswith(p + "/") for p in prefixes)


def withhold_for_current_request(payload: BaseModel) -> None:
    """The boundary hook: called by ``apply_internal_ids_visibility`` for every enveloped scientific response.

    Returns without a statement or a mutation when there is no context (outside a scientific request), no
    review floor (exploratory: the default), a release-backed profile, or an exempt route.
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
    attach_review_statuses(payload)


__all__ = [
    "ALIGNED_ID_LISTS",
    "COMPOSITE_REF_FIELDS",
    "EXEMPT_ROUTE_PREFIXES",
    "GATED_REF_RE",
    "GATED_TYPES",
    "POINTER_FIELDS",
    "POINTER_MODELS",
    "SUBJECT_IDENTITY",
    "UNGATED_REVIEWABLE",
    "UNGATED_SUBJECTS",
    "WITHHELD_TOKEN_PREFIX",
    "WITHHELD_TOKEN_RE",
    "GatedType",
    "RefGuardContext",
    "RefGuardUnplaceable",
    "RefSets",
    "SubjectNotShown",
    "WithheldSite",
    "WithholdReport",
    "attach_review_statuses",
    "badges_for",
    "below_floor_refs",
    "classify_refs",
    "current_ref_guard_context",
    "guard_active",
    "is_withheld_token",
    "refs_present",
    "refuse_hidden_refs_in_text",
    "reset_ref_guard_context",
    "route_matches",
    "set_ref_guard_context",
    "unavailable_refs",
    "withheld_evidence_refs",
    "withhold_for_current_request",
    "withhold_in_document",
    "withhold_in_model",
    "withhold_selection_facts",
]
