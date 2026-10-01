"""Composite schemes: lazy catalogue rows and level-of-theory binding (ADR 0021, P2).

A level of theory whose method is a catalogued named composite method
(``CBS-QB3``, ``G4``, ``W1U``, ...) is an ordinary ``level_of_theory`` row. This
module gives it the recipe it names, without touching its hash:

* get-or-create the ``named_method`` :class:`CompositeScheme`, identified by
  ``definition_hash = sha256`` of the canonical JSON
  ``{"kind":"named_method","method":<key>}``;
* get-or-create the :class:`LevelOfTheoryComposite` binding of the level to it,
  ``binding_source = named_method_catalogue``.

What the scheme row states
--------------------------
Only what ``app/chemistry/composite_methods.py`` states, and a ``None`` there is
``NULL`` here. The catalogue records an entry's internal geometry and frequency
levels and its recipe ZPE scale factor; the scheme row copies them. It creates no
terms: the catalogue holds no term list, and a term the source does not state is
not invented. ``source_literature_id`` stays ``NULL``: a ``literature`` row needs
a title, the catalogue holds a citation string rather than fields, and
inventing the rest would put a guess in an identity table. The defining paper's
DOI goes in ``note``.

The row is written once. A later change to the catalogue does not reach a row
that already exists (identity rows are never updated in place); a corrected
value is a new revision that says so.

Concurrency
-----------
Every get-or-create here is an insert inside a savepoint that falls back to a
select on ``IntegrityError``, the pattern ``resolve_level_of_theory_ref`` uses.
Two uploads racing on one method both end up holding the same scheme and the
same binding: the scheme is unique on ``definition_hash`` (and on its
content-derived ``public_ref``), the binding on its primary key.

Input levels are ordinary
-------------------------
:func:`assert_ordinary_input_level` is the one check that a term input is not a
composite-bound level. A level counts as composite when it has a binding row
**or** its method is a catalogued named method: bindings are created lazily, so
a level spelled ``CBS-QB3`` that has not been resolved yet has no binding row
and must still be refused. Merges are followed first, so a level merged into a
bound one is refused too. Every writer of ``composite_scheme_term_input`` goes
through :func:`add_scheme_term_input`.
"""

from __future__ import annotations

import hashlib
import json

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.chemistry.composite_methods import CompositeMethod, InternalLevel, composite_method_for
from app.db.models.common import (
    CompositeBindingSource,
    CompositeInputSlot,
    CompositeSchemeKind,
)
from app.db.models.composite_scheme import (
    CompositeScheme,
    CompositeSchemeTerm,
    CompositeSchemeTermInput,
    LevelOfTheoryComposite,
)
from app.db.models.level_of_theory import LevelOfTheory, LevelOfTheoryMerge


class CompositeInputLevelError(ValueError):
    """A composite scheme term was given a composite level as an input."""


def named_method_canonical_json(key: str) -> str:
    """The canonical JSON a named method's ``definition_hash`` is taken over.

    Sorted keys, no whitespace. Pinned by a test: changing it changes every
    named-method scheme's identity and ref.

    :param key: Catalogue key (``method_identity_key`` of the spelling).
    """
    return json.dumps({"kind": "named_method", "method": key}, sort_keys=True, separators=(",", ":"))


def named_method_definition_hash(key: str) -> str:
    """``sha256`` hex digest of :func:`named_method_canonical_json`."""
    return hashlib.sha256(named_method_canonical_json(key).encode("utf-8")).hexdigest()


def _select_scheme(session: Session, definition_hash: str) -> CompositeScheme | None:
    return session.scalar(
        select(CompositeScheme)
        .where(CompositeScheme.definition_hash == definition_hash)
        .execution_options(populate_existing=True)
    )


def _internal_level_id(session: Session, level: InternalLevel | None) -> int | None:
    """The level-of-theory row for a recipe's internal level, or ``None`` if unstated."""
    if level is None:
        return None
    # Imported here: ``calculation_resolution`` calls this module on every
    # level-of-theory resolution, and this is the one call back.
    from tckdb_schemas.fragments.refs import LevelOfTheoryRef

    from app.services.calculation_resolution import resolve_level_of_theory_ref

    row = resolve_level_of_theory_ref(session, LevelOfTheoryRef(method=level.method, basis=level.basis))
    if composite_method_for(row.method) is not None:
        raise CompositeInputLevelError(
            f"catalogue entry names a composite method {row.method!r} as an internal level"
        )
    return row.id


def get_or_create_named_method_scheme(session: Session, entry: CompositeMethod) -> CompositeScheme:
    """Get or create the ``named_method`` scheme for a catalogue entry.

    :param entry: The catalogue entry.
    :returns: The scheme row, committed by the caller with its transaction.
    """
    definition_hash = named_method_definition_hash(entry.key)
    scheme = _select_scheme(session, definition_hash)
    if scheme is not None:
        return scheme
    # Resolved before the savepoint: a race on an internal level is its own
    # get-or-create, and must not be rolled back with the scheme's.
    geometry_id = _internal_level_id(session, entry.geometry_level)
    frequency_id = _internal_level_id(session, entry.frequency_level)
    try:
        with session.begin_nested():
            scheme = CompositeScheme(
                kind=CompositeSchemeKind.named_method,
                name=entry.name,
                definition_hash=definition_hash,
                geometry_level_of_theory_id=geometry_id,
                frequency_level_of_theory_id=frequency_id,
                recipe_zpe_scale_factor=entry.recipe_zpe_scale_factor,
                note=(
                    f"Defining paper: doi:{entry.paper_doi}. Values as catalogued in "
                    "app/chemistry/composite_methods.py when the row was written."
                ),
            )
            session.add(scheme)
            session.flush()
    except IntegrityError:
        scheme = _select_scheme(session, definition_hash)
        if scheme is None:  # pragma: no cover - the conflict was not ours
            raise
    return scheme


def ensure_named_method_binding(session: Session, level: LevelOfTheory) -> LevelOfTheoryComposite | None:
    """Bind a level of theory to its named-method scheme, if its method is catalogued.

    Idempotent and race-safe. Does nothing, and issues no query, for a method
    that is not in the catalogue. Never changes the level's ``lot_hash``.

    :param level: The level-of-theory row resolution landed on (merges already
        followed).
    :returns: The binding, or ``None`` when the method is not a named method.
    """
    entry = composite_method_for(level.method)
    if entry is None:
        return None
    binding = session.get(LevelOfTheoryComposite, level.id)
    if binding is not None:
        return binding
    # A read memo on this session may hold "unbound" for this level.
    session.info.pop("composite_scheme_summary_by_lot", None)
    scheme = get_or_create_named_method_scheme(session, entry)
    try:
        with session.begin_nested():
            binding = LevelOfTheoryComposite(
                level_of_theory_id=level.id,
                scheme_id=scheme.id,
                binding_source=CompositeBindingSource.named_method_catalogue,
            )
            session.add(binding)
            session.flush()
    except IntegrityError:
        binding = session.scalar(
            select(LevelOfTheoryComposite)
            .where(LevelOfTheoryComposite.level_of_theory_id == level.id)
            .execution_options(populate_existing=True)
        )
        if binding is None:  # pragma: no cover - the conflict was not ours
            raise
    return binding


def assert_ordinary_input_level(session: Session, level_of_theory_id: int) -> None:
    """Refuse a composite level as the input of a scheme term.

    A level is composite when it is bound, or when its method is a catalogued
    named method (bindings are created lazily, so an unresolved ``CBS-QB3``
    level has no binding row yet). A merged level is checked as the level it
    was merged into.

    :raises CompositeInputLevelError: when the level is composite.
    :raises LookupError: when no such level exists.
    """
    merged_into = session.scalar(
        select(LevelOfTheoryMerge.into_lot_id).where(LevelOfTheoryMerge.merged_lot_id == level_of_theory_id)
    )
    resolved_id = merged_into if merged_into is not None else level_of_theory_id
    level = session.get(LevelOfTheory, resolved_id)
    if level is None:
        raise LookupError("level of theory not found")
    bound = session.get(LevelOfTheoryComposite, resolved_id) is not None
    if bound or composite_method_for(level.method) is not None:
        raise CompositeInputLevelError(
            f"level of theory {level.public_ref} is a composite level; a composite scheme is built "
            "from ordinary levels of theory and cannot be nested"
        )


def add_scheme_term_input(
    session: Session,
    term: CompositeSchemeTerm,
    *,
    slot: CompositeInputSlot,
    level_of_theory_id: int,
    cardinal_number: int | None = None,
) -> CompositeSchemeTermInput:
    """Add one input to a term, refusing a composite level.

    The only sanctioned writer of ``composite_scheme_term_input``. A merged
    level is stored as the level it was merged into, so the input never names
    a merged row.

    :raises CompositeInputLevelError: when the level is composite.
    """
    assert_ordinary_input_level(session, level_of_theory_id)
    merged_into = session.scalar(
        select(LevelOfTheoryMerge.into_lot_id).where(LevelOfTheoryMerge.merged_lot_id == level_of_theory_id)
    )
    row = CompositeSchemeTermInput(
        term_id=term.id,
        slot=slot,
        level_of_theory_id=merged_into if merged_into is not None else level_of_theory_id,
        cardinal_number=cardinal_number,
    )
    session.add(row)
    session.flush()
    return row
