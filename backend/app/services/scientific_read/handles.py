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

from app.api.errors import NotFoundError

logger = logging.getLogger(__name__)
from app.db.models.calculation import Calculation
from app.db.models.geometry import Geometry
from app.db.models.level_of_theory import LevelOfTheory
from app.db.models.reaction import ChemReaction, ReactionEntry
from app.db.models.species import (
    ConformerGroup,
    ConformerObservation,
    Species,
    SpeciesEntry,
)
from app.db.models.transition_state import TransitionState, TransitionStateEntry
from app.services.public_refs import PREFIXES

if TYPE_CHECKING:
    from app.db.base import Base


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
            raise NotFoundError(
                f"{kind_label} not found", code="handle_not_found"
            )
        return row_id

    # kind == "ref"
    ref = parsed
    prefix = ref.split("_", 1)[0]
    if prefix != expected_prefix:
        raise ValueError(
            f"handle_type_mismatch: expected a {kind_label} handle "
            f"(prefix {expected_prefix!r}) but got prefix {prefix!r}"
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
        raise NotFoundError(
            f"{kind_label} not found ({kind_label}_ref={ref!r})",
            code="handle_not_found",
        )
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
        raise ValueError(
            f"handle_type_mismatch: expected a {kind_label} ref "
            f"(prefix {expected_prefix!r}) but got prefix {prefix!r}"
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
) -> int | None | object:
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
            f"{conflict_code}: {kind_label}_id={id_value} and "
            f"{kind_label}_ref={ref_value!r} do not refer to the same row "
            f"(ref does not exist)"
        )
    if resolved != int(id_value):
        raise ValueError(
            f"{conflict_code}: {kind_label}_id={id_value} and "
            f"{kind_label}_ref={ref_value!r} resolve to different rows "
            f"(ref → id={resolved})"
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


def reconcile_species_pair(
    session: Session, *, id_value: int | None, ref_value: str | None
) -> int | None | object:
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
) -> int | None | object:
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
) -> int | None | object:
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
) -> int | None | object:
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
) -> int | None | object:
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
) -> int | None | object:
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
    "resolve_path_handle",
    "resolve_filter_ref",
    "reconcile_id_ref",
    "resolve_species_entry_handle",
    "resolve_reaction_entry_handle",
    "resolve_geometry_handle",
    "resolve_calculation_handle",
    "resolve_transition_state_handle",
    "resolve_transition_state_entry_handle",
    "resolve_conformer_group_handle",
    "resolve_conformer_observation_handle",
    "reconcile_species_pair",
    "reconcile_species_entry_pair",
    "reconcile_reaction_pair",
    "reconcile_reaction_entry_pair",
    "reconcile_level_of_theory_pair",
    "reconcile_calculation_pair",
]
