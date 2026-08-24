"""Phase C handle resolution: integer PKs and public refs are interchangeable.

A *handle* is a string that names a row by either:

- its integer primary key (``"42"``), or
- its public ref (``"spe_..."``, ``"rxe_..."``, ``"lot_..."``, ...).

Routes that historically accepted ``{id}`` path parameters keep working
because integer strings are parsed as PKs. Search endpoints gain
``*_ref`` query/body fields that resolve to the same row as their
``*_id`` siblings.

Errors raised here use the project's existing `ValueError` → 422 and
`NotFoundError` → 404 convention. The exception ``args[0]`` is formatted
as ``"<stable_code>: <human message>"`` so callers see a stable error
code in ``response.json()["detail"]``.
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING, Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.error_contract import CodedValueError
from app.api.errors import not_found
from app.db.models.calculation import Calculation
from app.db.models.common import RecordReviewStatus, SubmissionRecordType
from app.db.models.energy_correction import (
    EnergyCorrectionScheme,
    FrequencyScaleFactor,
)
from app.db.models.geometry import Geometry
from app.db.models.level_of_theory import LevelOfTheory
from app.db.models.literature import Literature
from app.db.models.network import Network
from app.db.models.network_pdep import NetworkKinetics, NetworkSolve
from app.db.models.reaction import ChemReaction, ReactionEntry
from app.db.models.record_review import RecordReview
from app.db.models.species import (
    ConformerGroup,
    ConformerObservation,
    Species,
    SpeciesEntry,
)
from app.db.models.statmech import Statmech
from app.db.models.transition_state import TransitionState, TransitionStateEntry
from app.db.models.transport import Transport
from app.schemas.reads.scientific_common import REVIEW_RANK
from app.services.public_refs import PREFIXES

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    pass


# ---------------------------------------------------------------------------
# Handle grammar
# ---------------------------------------------------------------------------


_INTEGER_RE = re.compile(r"^[1-9]\d*$")
# Public refs are <prefix>_<base32 lowercase body, 26 chars in current spec>.
# Allow alphanumerics in the body to stay forward-compatible with future
# encodings; the body length is validated by attempting a row lookup, not
# by a strict regex.
_REF_RE = re.compile(r"^([a-z]+)_([A-Za-z0-9]+)$")


def is_integer_handle(value: str) -> bool:
    """Return True iff *value* is a positive integer string (PK form)."""
    return bool(_INTEGER_RE.match(value))


def is_ref_handle(value: str) -> bool:
    """Return True iff *value* matches the ``<prefix>_<body>`` grammar."""
    return bool(_REF_RE.match(value))


def parse_handle(value: str) -> tuple[str, Any]:
    """Classify *value* as ``("id", int)`` or ``("ref", str)``.

    Whitespace-only or empty input is rejected with ``invalid_handle``.
    Strings that match neither shape are also ``invalid_handle``.
    """
    if not isinstance(value, str) or not value.strip():
        raise ValueError("invalid_handle: handle must be a non-empty string")
    stripped = value.strip()
    if is_integer_handle(stripped):
        return ("id", int(stripped))
    if is_ref_handle(stripped):
        return ("ref", stripped)
    raise ValueError(
        f"invalid_handle: {stripped!r} is neither an integer id nor a "
        "<prefix>_<body> public ref"
    )


def _handle_type_mismatch(
    kind_label: str,
    expected_prefix: str,
    prefix: str,
    *,
    noun: str,
) -> CodedValueError:
    """The 422 for a public ref carrying the wrong resource's prefix.

    ``handle_type_mismatch`` names a relationship — two prefixes that are
    not the same one — and names neither of them, which is the case
    :class:`~app.api.code_catalogue.Shape.relationship` is about. The two
    prefixes and the resource expected therefore go in ``context``, where
    a client can read them without parsing the sentence. Nothing here is a
    database id: a prefix is a schema-level constant and the supplied ref
    is the caller's own input.

    ``message_prefix=True`` keeps ``str(exc)`` -- and so the response's
    ``detail`` -- byte-identical to the ``ValueError(f"handle_type_mismatch:
    ...")`` this replaced. Only the *route* into ``code`` changes: it is
    now declared on the exception instead of promoted out of the sentence,
    which is why the catalogue entry moved from
    :attr:`~app.api.code_catalogue.Surface.message_prefix` to
    :attr:`~app.api.code_catalogue.Surface.coded_exception`.

    :param noun: ``"handle"`` on a path lookup, ``"ref"`` on a filter --
        the two call sites word it differently and the published prose is
        preserved exactly, prefix included.
    """
    return CodedValueError(
        "handle_type_mismatch",
        f"expected a {kind_label} {noun} "
        f"(prefix {expected_prefix!r}) but got prefix {prefix!r}",
        context={
            "expected_kind": kind_label,
            "expected_prefix": expected_prefix,
            "supplied_prefix": prefix,
        },
    )


def prefix_for(model_cls: type) -> str:
    """Return the public-ref prefix registered for *model_cls* (e.g. ``'spe'``).

    Raises ``KeyError`` (server-side bug) if the model has no prefix —
    that indicates ``PREFIXES`` is missing an entry, which should be
    fixed in ``app.services.public_refs``.
    """
    return PREFIXES[model_cls.__name__]


# ---------------------------------------------------------------------------
# Path-handle resolution (404 on missing)
# ---------------------------------------------------------------------------



# ---------------------------------------------------------------------------
# Curated-profile floor on detail-by-ref reads
# ---------------------------------------------------------------------------


#: ORM class → review vocabulary term, for handles that address a record the
#: endpoint then *returns*.
#:
#: Two exclusions, both deliberate.
#:
#: **Structure, vocabulary and provenance** — geometry, level of theory,
#: literature, frequency scale factors, energy-correction schemes — carry no
#: ``record_review`` row and make no reviewable claim of their own. Gating them
#: would make ``profile=curated`` 404 on the level of theory that a curated
#: record cites. The requirement attaches to the claim a record makes, not to
#: its type.
#:
#: **Scoping parents** — ``species``, ``species_entry``, ``chem_reaction``,
#: ``reaction_entry`` — are absent even though they *are* reviewable. Their
#: handles appear as the path parameter of subresource reads
#: (``/species-entries/{ref}/thermo``), where they say *which* records to look
#: under, not what is returned. Gating them would hide an approved thermo
#: behind an identity row that merely had not been reviewed yet — and on a
#: corpus where everything starts ``not_reviewed``, it would 404 the entire
#: curated surface. The floor for those reads is applied where it belongs, to
#: the products themselves, by ``visible_statuses``.
_REVIEWABLE_HANDLE_TYPES: dict[type, SubmissionRecordType] = {
    Calculation: SubmissionRecordType.calculation,
    Statmech: SubmissionRecordType.statmech,
    Transport: SubmissionRecordType.transport,
    ConformerGroup: SubmissionRecordType.conformer_group,
    ConformerObservation: SubmissionRecordType.conformer_observation,
    TransitionState: SubmissionRecordType.transition_state,
    TransitionStateEntry: SubmissionRecordType.transition_state_entry,
    Network: SubmissionRecordType.network,
    NetworkSolve: SubmissionRecordType.network_solve,
}


#: ORM class → ``(fk column, parent ORM class)`` for records whose review state
#: lives on a *parent*.
#:
#: ``network_kinetics`` is a returned record with its own detail endpoint, but
#: it has no ``record_review`` row in principle: a set of k(T,P) coefficients is
#: reviewed as part of the ``network_solve`` that produced it, never on its own.
#: Left out of the map entirely it sailed through the curated floor — a
#: never-reviewed ``network_kinetics`` returned 200 with an
#: ``approved_floor_only`` echo while its own parent correctly 404'd. That echo
#: is documented as "every record shown is at or above the approved review
#: floor", so the response was making a false machine-readable claim. It is the
#: narrow, non-endorsing member of the same class of false claim that made the
#: earlier profile bug blocking.
_PARENT_DERIVED_HANDLE_TYPES: dict[type, tuple[str, type]] = {
    NetworkKinetics: ("solve_id", NetworkSolve),
}


def _resolve_review_target(
    session: Session, model_cls: type, row_id: int
) -> tuple[SubmissionRecordType, int] | None:
    """The ``(record_type, record_id)`` whose review governs this handle.

    Returns ``None`` for handles the curated floor does not gate at all.
    """
    record_type = _REVIEWABLE_HANDLE_TYPES.get(model_cls)
    if record_type is not None:
        return record_type, row_id

    derived = _PARENT_DERIVED_HANDLE_TYPES.get(model_cls)
    if derived is None:
        return None
    fk_column, parent_cls = derived
    parent_id = session.scalar(
        select(getattr(model_cls, fk_column)).where(model_cls.id == row_id)
    )
    if parent_id is None:
        # An orphan child cannot inherit approval, so it is not part of the
        # curated surface either.
        return _REVIEWABLE_HANDLE_TYPES[parent_cls], -1
    return _REVIEWABLE_HANDLE_TYPES[parent_cls], parent_id


def _enforce_curated_floor(
    session: Session, model_cls: type, row_id: int, kind_label: str
) -> None:
    """Under ``profile=curated``, hide a record below the approval floor.

    Search endpoints get the floor from ``visible_statuses``, but roughly half
    the read services never call it — every detail-by-ref read resolves a
    handle and returns the row directly. Without this, ``profile=curated``
    happily returned never-reviewed statmech and transport records while
    echoing a curated contract, which is precisely the false claim the profile
    exists to prevent.

    This is the one function every detail-by-ref read passes through, so the
    floor cannot be forgotten by an endpoint. Records whose review state lives
    on a parent are gated by that parent — see
    :data:`_PARENT_DERIVED_HANDLE_TYPES`.

    The response is the same 404 ``handle_not_found`` an unknown ref produces.
    That is deliberate and matches the existing posture elsewhere in this
    module: under the curated contract the record is simply not part of the
    addressable surface, and a distinguishable "exists but is not approved"
    would leak review state to anonymous callers.

    :raises NotFoundError: the record is below the curated review floor.
    """
    from app.services.scientific_read.profile import current_read_profile

    floor = current_read_profile().review_floor
    if floor is None:
        return
    target = _resolve_review_target(session, model_cls, row_id)
    if target is None:
        return
    record_type, record_id = target

    review = session.scalars(
        select(RecordReview).where(
            RecordReview.record_type == record_type,
            RecordReview.record_id == record_id,
        )
    ).first()
    status = review.status if review is not None else RecordReviewStatus.not_reviewed
    if REVIEW_RANK[status] <= REVIEW_RANK[floor]:
        return

    logger.info(
        "path_handle_below_curated_floor kind=%s row_id=%d status=%s",
        kind_label,
        row_id,
        status.value,
    )
    raise not_found(kind_label, code="handle_not_found")


def resolve_path_handle(
    session: Session,
    model_cls: type,
    handle: str,
    *,
    kind_label: str,
) -> int:
    """Resolve a path-level handle (integer PK or public ref) to an integer id.

    Used by detail routes like ``/species-entries/{handle}/thermo``.

    - Integer handle: SELECT by id. Missing → 404.
    - Public ref with the expected prefix: SELECT by ``public_ref``.
      Missing → 404. Wrong prefix → 422 ``handle_type_mismatch``.
    - Malformed handle: 422 ``invalid_handle``.

    :param session: SQLAlchemy session.
    :param model_cls: ORM class whose row is being addressed.
    :param handle: raw string from the path parameter.
    :param kind_label: human-readable resource label for error messages
        (e.g. ``"species_entry"``).
    :returns: integer primary key of the resolved row.
    :raises ValueError: 422 for malformed or wrong-type handles.
    :raises NotFoundError: 404 when the row does not exist.
    """
    kind, parsed = parse_handle(handle)
    expected_prefix = prefix_for(model_cls)

    if kind == "id":
        row_id = int(parsed)
        exists = session.scalar(
            select(model_cls.id).where(model_cls.id == row_id)
        )
        if exists is None:
            # F7/F18: log the integer id server-side so operators can
            # still correlate 404s with traffic; do not echo it back
            # to the public caller. The stable ``handle_not_found``
            # code is shared with the ref branch so unknown-integer
            # and unknown-ref responses are indistinguishable above
            # the network-timing layer.
            logger.info(
                "path_handle_not_found kind=%s lookup=id row_id=%d",
                kind_label,
                row_id,
            )
            raise not_found(kind_label, code="handle_not_found")
        _enforce_curated_floor(session, model_cls, row_id, kind_label)
        return row_id

    # kind == "ref"
    ref = parsed
    prefix = ref.split("_", 1)[0]
    if prefix != expected_prefix:
        raise _handle_type_mismatch(
            kind_label, expected_prefix, prefix, noun="handle"
        )
    row_id = session.scalar(
        select(model_cls.id).where(model_cls.public_ref == ref)
    )
    if row_id is None:
        # The ref is public-by-design, so echoing it back is fine and
        # actually useful for client debugging.
        logger.info(
            "path_handle_not_found kind=%s lookup=ref ref=%s",
            kind_label,
            ref,
        )
        # ``row_id`` is deliberately not passed: the specific log line
        # above already carries more than the helper would, and the ref
        # is the half a 404 may echo.
        raise not_found(kind_label, ref=ref, code="handle_not_found")
    _enforce_curated_floor(session, model_cls, row_id, kind_label)
    return row_id


# ---------------------------------------------------------------------------
# Query-filter ref resolution (empty result on missing)
# ---------------------------------------------------------------------------


def resolve_filter_ref(
    session: Session,
    model_cls: type,
    ref: str,
    *,
    kind_label: str,
) -> int | None:
    """Resolve a ``*_ref`` *filter* to an integer id, or ``None`` if absent.

    Used for ``*_ref`` query/body filter parameters where an unknown ref
    is a well-formed query with no matching data — the caller should
    convert the ``None`` result into an empty record set rather than 404.

    - Malformed or wrong-prefix ref: 422 (``invalid_handle`` /
      ``handle_type_mismatch``).
    - Unknown ref of the right prefix: returns ``None``.

    :returns: resolved integer id, or ``None`` if the ref does not exist.
    :raises ValueError: 422 for malformed or wrong-type refs.
    """
    if not is_ref_handle(ref):
        raise ValueError(
            f"invalid_handle: {ref!r} is not a <prefix>_<body> public ref"
        )
    expected_prefix = prefix_for(model_cls)
    prefix = ref.split("_", 1)[0]
    if prefix != expected_prefix:
        raise _handle_type_mismatch(
            kind_label, expected_prefix, prefix, noun="ref"
        )
    return session.scalar(
        select(model_cls.id).where(model_cls.public_ref == ref)
    )


# ---------------------------------------------------------------------------
# id + ref pair reconciliation
# ---------------------------------------------------------------------------


# Sentinel return when an explicit ref filter resolved to a non-existent row.
# Services should treat this as "match nothing" and short-circuit to an
# empty result set per the Phase C unknown-filter rule.
NO_MATCH: object = object()


def reconcile_id_ref(
    session: Session,
    model_cls: type,
    *,
    id_value: int | None,
    ref_value: str | None,
    kind_label: str,
    conflict_code: str,
) -> int | object | None:
    """Reconcile sibling ``*_id`` and ``*_ref`` filter inputs.

    Returns one of:

    - ``None`` if both inputs are ``None`` (filter not supplied).
    - The integer id if only ``id_value`` was supplied (no DB hit).
    - The integer id if only ``ref_value`` was supplied and resolved.
    - The integer id if both were supplied **and** they resolve to the
      same row.
    - ``NO_MATCH`` (the module-level sentinel) if only ``ref_value`` was
      supplied but no row exists for it. Services convert this to an
      empty result set.

    :raises ValueError: 422 if the ref is malformed / wrong-typed, or
        if the id and ref were both supplied but disagree
        (``conflict_code``).
    """
    if id_value is None and ref_value is None:
        return None
    if ref_value is None:
        return int(id_value)
    resolved = resolve_filter_ref(
        session, model_cls, ref_value, kind_label=kind_label
    )
    if id_value is None:
        return resolved if resolved is not None else NO_MATCH
    # Both supplied — require consistency.
    if resolved is None:
        # The ref points at no row; that contradicts the supplied id by
        # definition, which is a 422 conflict (not silent empty results).
        raise ValueError(
            f"{conflict_code}: the supplied {kind_label}_id and "
            f"{kind_label}_ref={ref_value!r} do not refer to the same row "
            f"(the ref does not exist)"
        )
    if resolved != int(id_value):
        # The row id the ref resolves to is deliberately absent. Echoing
        # it made this endpoint an oracle for the whole ref-to-id
        # mapping: supply a public ref and any wrong id, and the 422 hands
        # back the real one. That mapping is exactly what
        # ``internal_ids`` exists to withhold, so the one place in the
        # read layer that gave it away was a 422 nobody thought of as a
        # disclosure. The caller supplied both inputs and is told which
        # two disagreed, which is all it needs to fix the request.
        raise ValueError(
            f"{conflict_code}: the supplied {kind_label}_id and "
            f"{kind_label}_ref={ref_value!r} resolve to different rows"
        )
    return resolved


# ---------------------------------------------------------------------------
# Per-resource convenience wrappers — used by routes/services.
# ---------------------------------------------------------------------------


def resolve_species_entry_handle(session: Session, handle: str) -> int:
    """Resolve a species-entry path handle (int or ``spe_...``) → row id."""
    return resolve_path_handle(
        session, SpeciesEntry, handle, kind_label="species_entry"
    )


def resolve_reaction_entry_handle(session: Session, handle: str) -> int:
    """Resolve a reaction-entry path handle (int or ``rxe_...``) → row id."""
    return resolve_path_handle(
        session, ReactionEntry, handle, kind_label="reaction_entry"
    )


def resolve_geometry_handle(session: Session, handle: str) -> int:
    """Resolve a geometry path handle (int or ``geom_...``) → row id."""
    return resolve_path_handle(
        session, Geometry, handle, kind_label="geometry"
    )


def resolve_calculation_handle(session: Session, handle: str) -> int:
    """Resolve a calculation path handle (int or ``calc_...``) → row id."""
    return resolve_path_handle(
        session, Calculation, handle, kind_label="calculation"
    )


def resolve_transition_state_handle(session: Session, handle: str) -> int:
    """Resolve a transition-state path handle (int or ``ts_...``) → row id."""
    return resolve_path_handle(
        session, TransitionState, handle, kind_label="transition_state"
    )


def resolve_transition_state_entry_handle(session: Session, handle: str) -> int:
    """Resolve a transition-state-entry path handle (int or ``tse_...``) → row id."""
    return resolve_path_handle(
        session,
        TransitionStateEntry,
        handle,
        kind_label="transition_state_entry",
    )


def resolve_conformer_group_handle(session: Session, handle: str) -> int:
    """Resolve a conformer-group path handle (int or ``cg_...``) → row id."""
    return resolve_path_handle(
        session, ConformerGroup, handle, kind_label="conformer_group"
    )


def resolve_conformer_observation_handle(session: Session, handle: str) -> int:
    """Resolve a conformer-observation path handle (int or ``co_...``) → row id."""
    return resolve_path_handle(
        session,
        ConformerObservation,
        handle,
        kind_label="conformer_observation",
    )


def resolve_statmech_handle(session: Session, handle: str) -> int:
    """Resolve a statmech path handle (int or ``sm_...``) → row id."""
    return resolve_path_handle(
        session, Statmech, handle, kind_label="statmech"
    )


def resolve_transport_handle(session: Session, handle: str) -> int:
    """Resolve a transport path handle (int or ``trn_...``) → row id."""
    return resolve_path_handle(
        session, Transport, handle, kind_label="transport"
    )


def resolve_network_handle(session: Session, handle: str) -> int:
    """Resolve a network path handle (int or ``net_...``) → row id."""
    return resolve_path_handle(session, Network, handle, kind_label="network")


def resolve_network_solve_handle(session: Session, handle: str) -> int:
    """Resolve a network-solve path handle (int or ``nsolve_...``) → row id."""
    return resolve_path_handle(
        session, NetworkSolve, handle, kind_label="network_solve"
    )


def resolve_network_kinetics_handle(session: Session, handle: str) -> int:
    """Resolve a network-kinetics path handle (int or ``nkin_...``) → row id."""
    return resolve_path_handle(
        session, NetworkKinetics, handle, kind_label="network_kinetics"
    )


def resolve_literature_handle(session: Session, handle: str) -> int:
    """Resolve a literature path handle (int or ``lit_...``) → row id."""
    return resolve_path_handle(
        session, Literature, handle, kind_label="literature"
    )


def resolve_frequency_scale_factor_handle(session: Session, handle: str) -> int:
    """Resolve a frequency-scale-factor path handle (int or ``fsf_...``) → row id."""
    return resolve_path_handle(
        session,
        FrequencyScaleFactor,
        handle,
        kind_label="frequency_scale_factor",
    )


def resolve_energy_correction_scheme_handle(
    session: Session, handle: str
) -> int:
    """Resolve an energy-correction-scheme path handle (int or ``ecs_...``) → row id."""
    return resolve_path_handle(
        session,
        EnergyCorrectionScheme,
        handle,
        kind_label="energy_correction_scheme",
    )


def reconcile_species_pair(
    session: Session, *, id_value: int | None, ref_value: str | None
) -> int | object | None:
    """Reconcile ``species_id`` + ``species_ref`` filter pair."""
    return reconcile_id_ref(
        session,
        Species,
        id_value=id_value,
        ref_value=ref_value,
        kind_label="species",
        conflict_code="species_handle_conflict",
    )


def reconcile_species_entry_pair(
    session: Session, *, id_value: int | None, ref_value: str | None
) -> int | object | None:
    """Reconcile ``species_entry_id`` + ``species_entry_ref`` filter pair."""
    return reconcile_id_ref(
        session,
        SpeciesEntry,
        id_value=id_value,
        ref_value=ref_value,
        kind_label="species_entry",
        conflict_code="species_entry_handle_conflict",
    )


def reconcile_reaction_pair(
    session: Session, *, id_value: int | None, ref_value: str | None
) -> int | object | None:
    """Reconcile ``reaction_id`` + ``reaction_ref`` filter pair."""
    return reconcile_id_ref(
        session,
        ChemReaction,
        id_value=id_value,
        ref_value=ref_value,
        kind_label="reaction",
        conflict_code="reaction_handle_conflict",
    )


def reconcile_reaction_entry_pair(
    session: Session, *, id_value: int | None, ref_value: str | None
) -> int | object | None:
    """Reconcile ``reaction_entry_id`` + ``reaction_entry_ref`` filter pair."""
    return reconcile_id_ref(
        session,
        ReactionEntry,
        id_value=id_value,
        ref_value=ref_value,
        kind_label="reaction_entry",
        conflict_code="reaction_entry_handle_conflict",
    )


def reconcile_level_of_theory_pair(
    session: Session, *, id_value: int | None, ref_value: str | None
) -> int | object | None:
    """Reconcile ``level_of_theory_id`` + ``level_of_theory_ref`` filter pair."""
    return reconcile_id_ref(
        session,
        LevelOfTheory,
        id_value=id_value,
        ref_value=ref_value,
        kind_label="level_of_theory",
        conflict_code="level_of_theory_handle_conflict",
    )


def reconcile_calculation_pair(
    session: Session, *, id_value: int | None, ref_value: str | None
) -> int | object | None:
    """Reconcile ``calculation_id`` + ``calculation_ref`` filter pair."""
    return reconcile_id_ref(
        session,
        Calculation,
        id_value=id_value,
        ref_value=ref_value,
        kind_label="calculation",
        conflict_code="calculation_handle_conflict",
    )


__all__ = [
    "NO_MATCH",
    "is_integer_handle",
    "is_ref_handle",
    "parse_handle",
    "prefix_for",
    "reconcile_calculation_pair",
    "reconcile_id_ref",
    "reconcile_level_of_theory_pair",
    "reconcile_reaction_entry_pair",
    "reconcile_reaction_pair",
    "reconcile_species_entry_pair",
    "reconcile_species_pair",
    "resolve_calculation_handle",
    "resolve_conformer_group_handle",
    "resolve_conformer_observation_handle",
    "resolve_energy_correction_scheme_handle",
    "resolve_filter_ref",
    "resolve_frequency_scale_factor_handle",
    "resolve_geometry_handle",
    "resolve_literature_handle",
    "resolve_network_handle",
    "resolve_network_kinetics_handle",
    "resolve_network_solve_handle",
    "resolve_path_handle",
    "resolve_reaction_entry_handle",
    "resolve_species_entry_handle",
    "resolve_statmech_handle",
    "resolve_transition_state_entry_handle",
    "resolve_transition_state_handle",
    "resolve_transport_handle",
]
