"""Service implementation for the composite-scheme detail read (ADR 0021).

``GET /scientific/composite-schemes/{ref}`` returns one recipe: its identity,
its terms and their level-of-theory inputs, and the levels of theory bound to
it. Refs only; integer ids appear (inside the level-of-theory summaries) only
under ``include=internal_ids``, like every scientific read.

A ``composite_scheme`` is identity data with no review history, so the
envelope's ``review_summary`` is always empty, the same posture as
``level_of_theory``.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.errors import not_found
from app.db.models.composite_scheme import (
    CompositeScheme,
    CompositeSchemeTerm,
    CompositeSchemeTermInput,
    LevelOfTheoryComposite,
)
from app.db.models.level_of_theory import LevelOfTheory
from app.db.models.literature import Literature
from app.schemas.reads.scientific_common import LevelOfTheorySummary, ReviewStatusSummary
from app.schemas.reads.scientific_composite_scheme import (
    CompositeSchemeBoundLevel,
    CompositeSchemeCoreBlock,
    CompositeSchemeTermInputRecord,
    CompositeSchemeTermRecord,
    RequestEcho,
    ScientificCompositeSchemeDetailResponse,
    ScientificCompositeSchemeRecord,
)
from app.services.scientific_read.common import validate_includes
from app.services.scientific_read.composite_binding import composite_scheme_summaries
from app.services.scientific_read.handles import resolve_composite_scheme_handle
from app.services.scientific_read.internal_ids import filter_internal_ids_from_resolved

_LEGAL_INCLUDE_TOKENS: set[str] = {"internal_ids"}
_INTERNAL_INCLUDE_TOKENS: set[str] = {"internal_ids"}


def get_composite_scheme(
    session: Session,
    *,
    composite_scheme_handle: str,
    include: list[str] | None = None,
) -> ScientificCompositeSchemeDetailResponse:
    """Resolve a composite-scheme handle and return its scientific projection."""
    includes = validate_includes(
        include or [],
        _LEGAL_INCLUDE_TOKENS,
        "/scientific/composite-schemes/{composite_scheme_ref}",
        internal_tokens=_INTERNAL_INCLUDE_TOKENS,
    )
    includes = filter_internal_ids_from_resolved(includes)

    scheme_id = resolve_composite_scheme_handle(session, composite_scheme_handle)
    scheme = session.get(CompositeScheme, scheme_id)
    if scheme is None:  # pragma: no cover - defended by the resolver's 404
        raise not_found("composite_scheme", row_id=scheme_id, code="handle_not_found")

    terms = list(
        session.scalars(
            select(CompositeSchemeTerm)
            .where(CompositeSchemeTerm.scheme_id == scheme.id)
            .order_by(CompositeSchemeTerm.position)
        ).all()
    )
    term_ids = [t.id for t in terms]
    inputs = (
        list(
            session.scalars(
                select(CompositeSchemeTermInput)
                .where(CompositeSchemeTermInput.term_id.in_(term_ids))
                .order_by(CompositeSchemeTermInput.term_id, CompositeSchemeTermInput.id)
            ).all()
        )
        if term_ids
        else []
    )
    bindings = list(
        session.scalars(
            select(LevelOfTheoryComposite)
            .where(LevelOfTheoryComposite.scheme_id == scheme.id)
            .order_by(LevelOfTheoryComposite.level_of_theory_id)
        ).all()
    )

    lot_ids = {
        i
        for i in (
            scheme.geometry_level_of_theory_id,
            scheme.frequency_level_of_theory_id,
            *(inp.level_of_theory_id for inp in inputs),
            *(b.level_of_theory_id for b in bindings),
        )
        if i is not None
    }
    levels = _level_summaries(session, lot_ids)

    inputs_by_term: dict[int, list[CompositeSchemeTermInputRecord]] = {}
    for inp in inputs:
        inputs_by_term.setdefault(inp.term_id, []).append(
            CompositeSchemeTermInputRecord(
                slot=inp.slot,
                cardinal_number=inp.cardinal_number,
                level_of_theory=levels[inp.level_of_theory_id],
            )
        )

    core = CompositeSchemeCoreBlock(
        composite_scheme_ref=scheme.public_ref,
        kind=scheme.kind,
        name=scheme.name,
        definition_hash=scheme.definition_hash,
        geometry_level_of_theory=(
            levels[scheme.geometry_level_of_theory_id] if scheme.geometry_level_of_theory_id is not None else None
        ),
        frequency_level_of_theory=(
            levels[scheme.frequency_level_of_theory_id] if scheme.frequency_level_of_theory_id is not None else None
        ),
        recipe_zpe_scale_factor=scheme.recipe_zpe_scale_factor,
        source_literature_ref=(
            session.scalar(select(Literature.public_ref).where(Literature.id == scheme.source_literature_id))
            if scheme.source_literature_id is not None
            else None
        ),
        note=scheme.note,
        created_at=scheme.created_at,
    )
    record = ScientificCompositeSchemeRecord(
        composite_scheme=core,
        terms=[
            CompositeSchemeTermRecord(
                position=t.position,
                operation=t.operation,
                energy_component=t.energy_component,
                formula=t.formula,
                exponent=t.exponent,
                inputs=inputs_by_term.get(t.id, []),
            )
            for t in terms
        ],
        bound_levels_of_theory=[
            CompositeSchemeBoundLevel(level_of_theory=levels[b.level_of_theory_id], binding_source=b.binding_source)
            for b in bindings
        ],
    )
    return ScientificCompositeSchemeDetailResponse(
        request=RequestEcho(include=sorted(includes)),
        review_summary=ReviewStatusSummary(),
        record=record,
    )


def _level_summaries(session: Session, lot_ids: set[int]) -> dict[int, LevelOfTheorySummary]:
    """Summaries of the levels a scheme mentions, each with its own binding.

    An input level is never bound (the writer refuses it), so its
    ``composite_scheme`` is ``None``; the bound levels carry this scheme's.
    """
    if not lot_ids:
        return {}
    rows = session.scalars(select(LevelOfTheory).where(LevelOfTheory.id.in_(lot_ids))).all()
    schemes = composite_scheme_summaries(session, lot_ids)
    return {
        lot.id: LevelOfTheorySummary(
            level_of_theory_id=lot.id,
            level_of_theory_ref=lot.public_ref,
            method=lot.method,
            basis=lot.basis,
            dispersion=lot.dispersion,
            solvent=lot.solvent,
            spin_treatment=lot.spin_treatment,
            label=None,
            composite_scheme=schemes.get(lot.id),
        )
        for lot in rows
    }
