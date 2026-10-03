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
from tckdb_schemas.composite_formulas import ExtrapolationError, extrapolation_coefficients, is_linear_formula

from app.api.errors import not_found
from app.db.models.common import (
    CompositeExtrapolationFormula,
    CompositeInputSlot,
    CompositeTermLinearity,
    CompositeTermOperation,
)
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

    inputs_by_term: dict[int, list[CompositeSchemeTermInput]] = {}
    for inp in inputs:
        inputs_by_term.setdefault(inp.term_id, []).append(inp)

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
    term_records = []
    for t in terms:
        term_inputs = inputs_by_term.get(t.id, [])
        linearity = term_linearity(t.operation, t.formula)
        coefficients = term_coefficients(t.operation, t.formula, t.exponent, term_inputs)
        term_records.append(
            CompositeSchemeTermRecord(
                position=t.position,
                operation=t.operation,
                energy_component=t.energy_component,
                formula=t.formula,
                exponent=t.exponent,
                linearity=linearity,
                inputs=[
                    CompositeSchemeTermInputRecord(
                        slot=inp.slot,
                        cardinal_number=inp.cardinal_number,
                        level_of_theory=levels[inp.level_of_theory_id],
                        coefficient=coefficients.get(inp.id),
                    )
                    for inp in term_inputs
                ],
            )
        )
    record = ScientificCompositeSchemeRecord(
        composite_scheme=core,
        linear_in_energies=scheme_linearity([r.linearity for r in term_records]),
        terms=term_records,
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
            core_treatment=lot.core_treatment,
            label=None,
            composite_scheme=schemes.get(lot.id),
        )
        for lot in rows
    }


def term_linearity(
    operation: CompositeTermOperation, formula: CompositeExtrapolationFormula | None
) -> CompositeTermLinearity:
    """Whether a term is a fixed linear combination of its inputs' energies.

    ``base``, ``value`` and ``difference`` always are; an ``extrapolation`` is when its formula is
    (:func:`tckdb_schemas.composite_formulas.is_linear_formula`); an ``empirical`` term is not
    computed from inputs at all.
    """
    if operation is CompositeTermOperation.empirical:
        return CompositeTermLinearity.not_applicable
    if operation is CompositeTermOperation.extrapolation:
        if formula is None:
            return CompositeTermLinearity.not_applicable
        return CompositeTermLinearity.linear if is_linear_formula(formula) else CompositeTermLinearity.nonlinear
    return CompositeTermLinearity.linear


def term_coefficients(
    operation: CompositeTermOperation,
    formula: CompositeExtrapolationFormula | None,
    exponent: float | None,
    inputs: list[CompositeSchemeTermInput],
) -> dict[int, float]:
    """``{term input id: coefficient}`` for a linear term; empty when the term is not linear.

    A coefficient is the weight of the input's ``energy_component`` in the term's value:
    ``+1`` for a ``base`` / ``value`` input, ``+1`` / ``-1`` for the ``high`` / ``low`` input of a
    ``difference``, and the signed closed-form weights of a two-point extrapolation (smaller
    cardinal first; they sum to 1). A term that cannot be read as stored (an extrapolation whose
    inputs do not fit its formula) gets no coefficients rather than a guess.
    """
    if operation in (CompositeTermOperation.base, CompositeTermOperation.value):
        return {i.id: 1.0 for i in inputs if i.slot is CompositeInputSlot.value}
    if operation is CompositeTermOperation.difference:
        signs = {CompositeInputSlot.high: 1.0, CompositeInputSlot.low: -1.0}
        return {i.id: signs[i.slot] for i in inputs if i.slot in signs}
    if operation is CompositeTermOperation.extrapolation and formula is not None:
        cardinal_inputs = [i for i in inputs if i.slot is CompositeInputSlot.cardinal and i.cardinal_number is not None]
        cardinals = [i.cardinal_number for i in cardinal_inputs if i.cardinal_number is not None]
        try:
            weights = extrapolation_coefficients(formula, cardinals, exponent)
        except ExtrapolationError:
            return {}
        if weights is None:
            return {}
        ordered = sorted(cardinal_inputs, key=lambda i: i.cardinal_number or 0)
        return {i.id: w for i, w in zip(ordered, weights, strict=True)}
    return {}


def scheme_linearity(term_linearities: list[CompositeTermLinearity]) -> bool | None:
    """``None`` with no terms; ``False`` when any term is non-linear; else ``True``.

    Terms that are ``not_applicable`` (empirical) are not computed from inputs and do not decide it;
    a scheme of only such terms says nothing (``None``).
    """
    relevant = [x for x in term_linearities if x is not CompositeTermLinearity.not_applicable]
    if not relevant:
        return None
    return all(x is CompositeTermLinearity.linear for x in relevant)
