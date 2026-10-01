"""Persisting a program-run composite energy, and the checks around it (ADR 0021, P3a).

A ``composite`` calculation is one composite energy at a scheme-bound level of
theory. This module owns the rules that decide whether such a calculation is
accepted and writes its ``calc_composite_result`` and ``calc_composite_term``
rows. Everything it stores is a number the producer (or the program's own
output) reported: TCKDB never stores a total it computed itself.

What is refused, and why
------------------------
Every refusal is a block-tier check under ADR 0008, because each asserts a
definition rather than an expectation:

* ``composite_assembled_not_accepted`` -- ``assembly = assembled`` means the
  energy is arithmetic over other deposited calculations, which only a
  user-built scheme (``extrapolation`` / ``additive``, ADR 0021 P5) can say. An
  assembled composite at a named method, or at a level bound to no scheme, is
  refused: there is no recipe to check its total against. (The wire refuses the
  same shape earlier; this is the seam's own guard.)
* ``composite_program_run_requires_software`` -- a program-run number is what a
  program printed, so the program must be named. An assembled composite names no
  software: no program ran it.
* ``composite_level_not_scheme_bound`` -- a composite calculation is always at a
  level of theory bound to a composite scheme: a catalogued named method
  (``CBS-QB3``, ``G4``, ...) or a user-built scheme sent inline. Binding happens
  when the level is resolved (``resolve_level_of_theory_ref``), so a level with
  no binding is one the catalogue does not know and no definition declared.
* ``composite_e0_inconsistent`` / ``composite_terms_do_not_sum`` -- the stated
  numbers contradict each other beyond printed precision (``max(1e-6, 5e-7 * n)``
  hartree for ``n`` rounded quantities). The functions live in the
  wire package (:func:`assert_composite_result_arithmetic`) and run on parse; they
  run again here because a payload built with ``model_copy`` skips validators.
* ``composite_term_position_unknown`` -- the calculation's scheme lists terms and
  a deposited term names a position it does not have. A named method lists none
  today, so there a term's position is the producer's own ordering.

The warning tier
----------------
:func:`collect_named_composite_deposit_warnings` answers a *new* ``opt`` or
``sp`` at a catalogued named-method level with ``named_composite_deposited_as_opt``
or ``..._sp``. Owner decision 7 (2026-10-01): warn now, refuse once the ARC
adapter ships the composite shape. Refusing today would block every real
CBS-QB3 deposit, because the producer cannot yet send the right shape.
"""

from __future__ import annotations

from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session
from tckdb_schemas.fragments.calculation import (
    COMPOSITE_ASSEMBLED_NOT_ACCEPTED as _WIRE_COMPOSITE_ASSEMBLED_NOT_ACCEPTED,
)
from tckdb_schemas.fragments.calculation import (
    CompositeResultPayload,
    assert_composite_result_arithmetic,
)

from app.api.error_contract import CodedValueError
from app.db.models.calculation import (
    Calculation,
    CalculationCompositeResult,
    CalculationCompositeTerm,
)
from app.db.models.common import (
    CalculationType,
    CompositeAssembly,
    CompositeBindingSource,
    CompositeSchemeKind,
)
from app.db.models.composite_scheme import (
    CompositeScheme,
    CompositeSchemeTerm,
    LevelOfTheoryComposite,
)
from app.db.models.level_of_theory import LevelOfTheory
from app.schemas.upload_warning import UploadWarning
from app.services.composite_input_resolution import register_pending_composite

#: An assembled composite is not at a user-built scheme. Defined with the wire
#: rule that raises it first (``tckdb_schemas.fragments.calculation``).
COMPOSITE_ASSEMBLED_NOT_ACCEPTED = _WIRE_COMPOSITE_ASSEMBLED_NOT_ACCEPTED

#: A ``program_run`` composite names no software.
COMPOSITE_PROGRAM_RUN_REQUIRES_SOFTWARE = "composite_program_run_requires_software"

#: A composite calculation's level of theory is not bound to a composite scheme.
COMPOSITE_LEVEL_NOT_SCHEME_BOUND = "composite_level_not_scheme_bound"

#: A deposited term names a position the calculation's scheme does not have.
COMPOSITE_TERM_POSITION_UNKNOWN = "composite_term_position_unknown"

#: A new ``opt`` / ``sp`` was deposited at a catalogued named-method level.
W_NAMED_COMPOSITE_DEPOSITED_AS_OPT = "named_composite_deposited_as_opt"
W_NAMED_COMPOSITE_DEPOSITED_AS_SP = "named_composite_deposited_as_sp"

_DEPOSITED_AS_WARNING_CODE: dict[CalculationType, str] = {
    CalculationType.opt: W_NAMED_COMPOSITE_DEPOSITED_AS_OPT,
    CalculationType.sp: W_NAMED_COMPOSITE_DEPOSITED_AS_SP,
}


def _lot_label(level: LevelOfTheory | None) -> str:
    if level is None:
        return "unknown"
    return f"{level.method}/{level.basis}" if level.basis else level.method


def _scheme_bound_to(session: Session, level_of_theory_id: int | None) -> CompositeScheme | None:
    """The scheme a level of theory is bound to, or ``None`` when it is unbound."""
    if level_of_theory_id is None:
        return None
    return session.scalar(
        select(CompositeScheme)
        .join(LevelOfTheoryComposite, LevelOfTheoryComposite.scheme_id == CompositeScheme.id)
        .where(LevelOfTheoryComposite.level_of_theory_id == level_of_theory_id)
    )


def persist_composite_result(
    session: Session,
    calculation: Calculation,
    payload: CompositeResultPayload,
    *,
    level_of_theory: object | None = None,
) -> CalculationCompositeResult:
    """Check and write a composite calculation's result and terms.

    For an ``assembled`` composite this also *registers* it for
    :func:`app.services.composite_input_resolution.finalize_composite_inputs`,
    which writes its inputs and runs the input and total checks once every
    calculation of the request exists.

    :param session: Active SQLAlchemy session.
    :param calculation: The composite calculation row, flushed, with its level
        of theory and software already resolved.
    :param payload: The ``composite_result`` block.
    :param level_of_theory: The calculation's ``level_of_theory`` ref as sent. An
        assembled composite needs its inline ``composite_scheme``: that is what
        the inputs' ``term_key`` values are resolved against.
    :returns: The ``calc_composite_result`` row.
    :raises ValueError: when the calculation is not of type ``composite`` (the
        wire validators refuse this first; this is the seam's own guard).
    :raises CodedValueError: for each refusal in the module docstring.
    """
    if calculation.type != CalculationType.composite:
        raise ValueError(
            "composite_result is only allowed on composite calculations "
            f"(got type '{calculation.type.value}')."
        )
    assembled = payload.assembly == CompositeAssembly.assembled
    if not assembled and calculation.software_release_id is None:
        raise CodedValueError(
            COMPOSITE_PROGRAM_RUN_REQUIRES_SOFTWARE,
            "a program-run composite needs the software that ran it: the number is what that "
            "program printed. Send software_release.",
            context={"field": "software_release"},
            message_prefix=False,
        )
    scheme = _scheme_bound_to(session, calculation.lot_id)
    if scheme is None:
        level = session.get(LevelOfTheory, calculation.lot_id) if calculation.lot_id is not None else None
        raise CodedValueError(
            COMPOSITE_LEVEL_NOT_SCHEME_BOUND,
            f"a composite calculation must be at a level of theory bound to a composite scheme, and "
            f"{_lot_label(level)} is not: only a catalogued named method (for example CBS-QB3, "
            "CBS-4M, CBS-APNO, G3, G3B3, G3MP2, G4, G4MP2, W1U, W1BD) or a user-built scheme sent "
            "inline as level_of_theory.composite_scheme is bound. If one program run of a named "
            "method produced this energy, send that method's name as the level_of_theory method; if "
            "the energy is a plain single point or optimisation, send type 'sp' or 'opt' instead.",
            context={"field": "level_of_theory", "level_of_theory": _lot_label(level)},
            message_prefix=False,
        )
    definition = getattr(level_of_theory, "composite_scheme", None)
    if assembled:
        binding = session.get(LevelOfTheoryComposite, calculation.lot_id)
        user_scheme = (
            binding is not None
            and binding.binding_source == CompositeBindingSource.declared
            and scheme.kind != CompositeSchemeKind.named_method
        )
        if not user_scheme or definition is None:
            raise CodedValueError(
                COMPOSITE_ASSEMBLED_NOT_ACCEPTED,
                "an assembled composite is arithmetic over other deposited calculations, which only a "
                "user-built scheme sent inline (level_of_theory.composite_scheme, kind 'extrapolation' "
                "or 'additive') can describe; "
                f"{_lot_label(session.get(LevelOfTheory, calculation.lot_id))} is not one. A named "
                "method such as CBS-QB3 is a program_run composite.",
                context={"field": "composite_result.assembly", "assembly": payload.assembly.value},
                message_prefix=False,
            )

    # The wire validator ran these on parse; a payload built with model_copy skipped it.
    assert_composite_result_arithmetic(payload)

    if payload.terms:
        scheme_positions = set(
            session.scalars(
                select(CompositeSchemeTerm.position).where(CompositeSchemeTerm.scheme_id == scheme.id)
            ).all()
        )
        if scheme_positions:
            unknown = sorted({t.term_position for t in payload.terms} - scheme_positions)
            if unknown:
                raise CodedValueError(
                    COMPOSITE_TERM_POSITION_UNKNOWN,
                    f"composite_result.terms names position(s) {', '.join(str(p) for p in unknown)}, "
                    f"which the scheme of level of theory {_lot_label(session.get(LevelOfTheory, calculation.lot_id))} "
                    f"does not have; its terms are at position(s) "
                    f"{', '.join(str(p) for p in sorted(scheme_positions))}.",
                    context={
                        "field": "composite_result.terms",
                        "unknown_term_positions": unknown,
                        "scheme_term_positions": sorted(scheme_positions),
                    },
                    message_prefix=False,
                )

    result = CalculationCompositeResult(
        calculation_id=calculation.id,
        assembly=CompositeAssembly(payload.assembly.value),
        electronic_energy_hartree=payload.electronic_energy_hartree,
        e0_hartree=payload.e0_hartree,
        recipe_zpe_hartree=payload.recipe_zpe_hartree,
    )
    session.add(result)
    for term in payload.terms:
        session.add(
            CalculationCompositeTerm(
                calculation_id=calculation.id,
                term_position=term.term_position,
                value_hartree=term.value_hartree,
            )
        )
    if assembled:
        # The inputs are written, and the total checked, by
        # ``finalize_composite_inputs`` once every calculation of the request exists.
        register_pending_composite(session, calculation, payload, definition)
    return result


def collect_named_composite_deposit_warnings(
    session: Session,
    calculation_ids: Iterable[int],
) -> list[UploadWarning]:
    """Warn on each new ``opt`` / ``sp`` among ``calculation_ids`` at a named-method level.

    A CBS-QB3 (or G4, W1U, ...) run is one job whose number is the recipe's
    final energy, not an optimisation's or a single point's. Stored as ``opt``
    or ``sp`` it is a calculation that was not what ran, and it is invisible to
    the composite level rules. The deposit is kept exactly as sent.

    **Warns, never refuses (owner decision 7, ADR 0021).** The ARC adapter
    cannot yet send the ``composite`` shape, so refusing now would block every
    real deposit; the refusal follows once it does.

    :param session: Active SQLAlchemy session.
    :param calculation_ids: Every calculation id the request persisted (or
        reused); other types are filtered out here.
    :returns: One warning per ``opt`` / ``sp`` at a named-method level, in
        calculation-id order.
    """
    ids = list(dict.fromkeys(calculation_ids))
    if not ids:
        return []
    rows = session.execute(
        select(Calculation.id, Calculation.type, LevelOfTheory.method)
        .join(LevelOfTheory, LevelOfTheory.id == Calculation.lot_id)
        .join(LevelOfTheoryComposite, LevelOfTheoryComposite.level_of_theory_id == LevelOfTheory.id)
        .join(CompositeScheme, CompositeScheme.id == LevelOfTheoryComposite.scheme_id)
        .where(
            Calculation.id.in_(ids),
            Calculation.type.in_(tuple(_DEPOSITED_AS_WARNING_CODE)),
            CompositeScheme.kind == CompositeSchemeKind.named_method,
        )
        .order_by(Calculation.id)
    ).all()
    warnings: list[UploadWarning] = []
    for _calc_id, calc_type, method in rows:
        warnings.append(
            UploadWarning(
                field="level_of_theory",
                code=_DEPOSITED_AS_WARNING_CODE[calc_type],
                message=(
                    f"This {calc_type.value} calculation is at the level of theory {method!r}, a named "
                    "composite method: one program run that produces a final energy from several "
                    f"internal steps. A {calc_type.value} calculation is not what ran. It was stored as "
                    "sent; send it as type 'composite' with a composite_result block (assembly "
                    "'program_run' and the energies the program printed) and, if the run's own "
                    "optimisation or frequency job is also deposited, send those separately at their "
                    "own levels. This will be refused once producers can send that shape."
                ),
            )
        )
    return warnings
