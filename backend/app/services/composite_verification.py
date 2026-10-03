"""How far a composite energy has been checked, derived at read time (ADR 0021, P7a).

:func:`verify_composite_calculations` answers, for composite calculations, one of
the states of :class:`~app.db.models.common.CompositeEnergyVerificationState`:

======================  ====================================================================
State                   Source of the answer
======================  ====================================================================
``recomputed``          ``assembled``: the stored inputs, run through the scheme with the wire
                        package's own arithmetic (:mod:`tckdb_schemas.composite_total`), give
                        the stated total within the weighted tolerance
``recompute_mismatch``  the same recomputation disagrees
``log_reconciled``      ``program_run``: a recorded ``confirmed`` log check
                        (``calc_composite_log_check``), and no recorded disagreement
``program_reported``    ``program_run`` with no confirming log on record
``unverifiable``        a number or input needed for the check is not stated
======================  ====================================================================

Two rules this module exists to keep:

* **Nothing recomputed is ever stored, and nothing is cached.** An assembled composite
  is recomputed on every read from whatever its inputs hold *now*, so an input energy
  deposited later is picked up, and a stored input that changed shows as
  ``recompute_mismatch`` instead of staying a stale ``recomputed``. Only the distance
  from the stated total is returned, never the recomputed total.
* **A log is never parsed on read.** A program run's state comes from the conclusion
  the upload hook recorded
  (:func:`app.services.composite_energy_extraction.try_reconcile_composite_energy_from_output_log`).
  A calculation whose log was uploaded before that record existed reads as
  ``program_reported`` until its log is deposited again.

A recorded disagreement (``mismatch``, ``method_mismatch``) does not turn a program run
into ``log_reconciled`` even beside a ``confirmed`` log: a number two logs disagree about
is not confirmed. It stays ``program_reported``, with ``reason`` naming the disagreement.

Bulk by design: a page of records is verified in a fixed number of statements, however
many composites it holds.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session
from tckdb_schemas.composite_total import InputEnergies, check_composite_total
from tckdb_schemas.enums import CompositeInputSlot as WireSlot
from tckdb_schemas.enums import EnergyComponentKind as WireComponent

from app.db.models.calculation import (
    Calculation,
    CalculationCompositeInput,
    CalculationCompositeLogCheck,
    CalculationCompositeResult,
    CalculationOptResult,
    CalculationSPEnergyComponent,
    CalculationSPResult,
)
from app.db.models.common import (
    CalculationType,
    CompositeAssembly,
    CompositeEnergyVerificationState,
    CompositeLogOutcome,
)
from app.db.models.composite_scheme import (
    CompositeSchemeTerm,
    CompositeSchemeTermInput,
    LevelOfTheoryComposite,
)
from app.schemas.reads.scientific_common import CompositeEnergyVerification

__all__ = [
    "REASON_LOG_METHOD_MISMATCH",
    "REASON_LOG_MISMATCH",
    "REASON_LOG_NOT_CONFIRMING",
    "REASON_NO_ENERGY_STATED",
    "REASON_NO_RESULT",
    "REASON_SCHEME_NOT_BOUND",
    "verify_composite_calculation",
    "verify_composite_calculations",
]

#: A recorded log check says a deposited number disagrees with the log.
REASON_LOG_MISMATCH = "log_mismatch"
#: A recorded log check says the log is a different method from the calculation's level.
REASON_LOG_METHOD_MISMATCH = "log_method_mismatch"
#: A log was checked and could neither confirm nor contradict (unreadable, or nothing to compare).
REASON_LOG_NOT_CONFIRMING = "log_did_not_confirm"
#: A program run states no energy at all, so there is nothing to confirm.
REASON_NO_ENERGY_STATED = "no_energy_stated"
#: The calculation has no composite result row.
REASON_NO_RESULT = "no_result_stated"
#: An assembled composite's level of theory is bound to no scheme, so there is no recipe to run.
REASON_SCHEME_NOT_BOUND = "scheme_not_bound"


def _as_dict(rows: Iterable[Any]) -> dict[Any, Any]:
    """``{first column: second column}`` of a two-column result (``dict(rows)`` does not type-check on ``Row``)."""
    return {row[0]: row[1] for row in rows}


@dataclass(frozen=True)
class _TermInput:
    slot: WireSlot
    cardinal_number: int | None


@dataclass(frozen=True)
class _Term:
    """A stored scheme term in the shape :func:`check_composite_total` reads (a definition)."""

    key: str
    operation: object
    energy_component: object
    formula: object
    exponent: float | None
    inputs: tuple[_TermInput, ...]


@dataclass(frozen=True)
class _Definition:
    terms: tuple[_Term, ...]


def verify_composite_calculation(session: Session, calculation_id: int) -> CompositeEnergyVerification | None:
    """The verification of one composite calculation, or ``None`` when it is not a composite.

    :param session: Active session.
    :param calculation_id: A calculation id.
    """
    return verify_composite_calculations(session, [calculation_id]).get(calculation_id)


def verify_composite_calculations(
    session: Session, calculation_ids: Iterable[int | None]
) -> dict[int, CompositeEnergyVerification]:
    """Verify composite calculations, in a fixed number of statements.

    :param session: Active session.
    :param calculation_ids: Calculation ids; ``None`` and duplicates are ignored.
        An id that is not a calculation of type ``composite`` is not a key of the result.
    :returns: ``{calculation id: verification}``, one entry per composite calculation.
    """
    wanted = {i for i in calculation_ids if i is not None}
    if not wanted:
        return {}
    composites: dict[int, int | None] = _as_dict(
        session.execute(
            select(Calculation.id, Calculation.lot_id).where(
                Calculation.id.in_(wanted), Calculation.type == CalculationType.composite
            )
        ).all()
    )
    if not composites:
        return {}
    results = {
        row.calculation_id: row
        for row in session.scalars(
            select(CalculationCompositeResult).where(CalculationCompositeResult.calculation_id.in_(composites))
        ).all()
    }
    program_run_ids = [
        cid for cid, row in results.items() if row.assembly is CompositeAssembly.program_run
    ]
    assembled_ids = [cid for cid, row in results.items() if row.assembly is CompositeAssembly.assembled]
    outcomes = _log_outcomes(session, program_run_ids)
    recomputed = _recompute_assembled(session, {cid: composites[cid] for cid in assembled_ids}, results)

    verified: dict[int, CompositeEnergyVerification] = {}
    for calculation_id in composites:
        row = results.get(calculation_id)
        if row is None:
            # A composite calculation with no result row: nothing was stated, so nothing is checkable.
            verified[calculation_id] = CompositeEnergyVerification(
                state=CompositeEnergyVerificationState.unverifiable,
                assembly=CompositeAssembly.program_run,
                reason=REASON_NO_RESULT,
            )
        elif row.assembly is CompositeAssembly.assembled:
            verified[calculation_id] = recomputed[calculation_id]
        else:
            verified[calculation_id] = _program_run(row, outcomes.get(calculation_id, frozenset()))
    return verified


def _log_outcomes(session: Session, calculation_ids: list[int]) -> dict[int, frozenset[CompositeLogOutcome]]:
    if not calculation_ids:
        return {}
    # Per log, the conclusion of the newest parser version that drew one; then the set of conclusions per calculation.
    newest: dict[tuple[int, str], tuple[int, CompositeLogOutcome]] = {}
    for calculation_id, sha, version, outcome in session.execute(
        select(
            CalculationCompositeLogCheck.calculation_id,
            CalculationCompositeLogCheck.artifact_sha256,
            CalculationCompositeLogCheck.parser_version,
            CalculationCompositeLogCheck.outcome,
        ).where(CalculationCompositeLogCheck.calculation_id.in_(calculation_ids))
    ).all():
        key = (calculation_id, sha)
        if key not in newest or version > newest[key][0]:
            newest[key] = (version, outcome)
    grouped: dict[int, set[CompositeLogOutcome]] = defaultdict(set)
    for (calculation_id, _sha), (_version, outcome) in newest.items():
        grouped[calculation_id].add(outcome)
    return {cid: frozenset(values) for cid, values in grouped.items()}


def _program_run(
    result: CalculationCompositeResult, outcomes: frozenset[CompositeLogOutcome]
) -> CompositeEnergyVerification:
    """A program run's state, from the log checks recorded at upload."""
    assembly = CompositeAssembly.program_run
    if result.electronic_energy_hartree is None and result.e0_hartree is None and result.recipe_zpe_hartree is None:
        return CompositeEnergyVerification(
            state=CompositeEnergyVerificationState.unverifiable, assembly=assembly, reason=REASON_NO_ENERGY_STATED
        )
    if CompositeLogOutcome.mismatch in outcomes:
        return CompositeEnergyVerification(
            state=CompositeEnergyVerificationState.program_reported, assembly=assembly, reason=REASON_LOG_MISMATCH
        )
    if CompositeLogOutcome.method_mismatch in outcomes:
        return CompositeEnergyVerification(
            state=CompositeEnergyVerificationState.program_reported,
            assembly=assembly,
            reason=REASON_LOG_METHOD_MISMATCH,
        )
    if CompositeLogOutcome.confirmed in outcomes:
        return CompositeEnergyVerification(state=CompositeEnergyVerificationState.log_reconciled, assembly=assembly)
    return CompositeEnergyVerification(
        state=CompositeEnergyVerificationState.program_reported,
        assembly=assembly,
        reason=REASON_LOG_NOT_CONFIRMING if outcomes else None,
    )


def _recompute_assembled(
    session: Session,
    lot_by_calculation: dict[int, int | None],
    results: dict[int, CalculationCompositeResult],
) -> dict[int, CompositeEnergyVerification]:
    """Recompute each assembled composite's total from its stored inputs, now."""
    if not lot_by_calculation:
        return {}
    assembly = CompositeAssembly.assembled
    lot_ids = {lot for lot in lot_by_calculation.values() if lot is not None}
    scheme_by_lot: dict[int, int] = (
        _as_dict(
            session.execute(
                select(LevelOfTheoryComposite.level_of_theory_id, LevelOfTheoryComposite.scheme_id).where(
                    LevelOfTheoryComposite.level_of_theory_id.in_(lot_ids)
                )
            ).all()
        )
        if lot_ids
        else {}
    )
    scheme_ids = set(scheme_by_lot.values())
    terms_by_scheme: dict[int, list[CompositeSchemeTerm]] = defaultdict(list)
    inputs_by_term: dict[int, list[CompositeSchemeTermInput]] = defaultdict(list)
    if scheme_ids:
        for term in session.scalars(
            select(CompositeSchemeTerm)
            .where(CompositeSchemeTerm.scheme_id.in_(scheme_ids))
            .order_by(CompositeSchemeTerm.scheme_id, CompositeSchemeTerm.position)
        ).all():
            terms_by_scheme[term.scheme_id].append(term)
        term_ids = [t.id for terms in terms_by_scheme.values() for t in terms]
        if term_ids:
            for term_input in session.scalars(
                select(CompositeSchemeTermInput)
                .where(CompositeSchemeTermInput.term_id.in_(term_ids))
                .order_by(CompositeSchemeTermInput.term_id, CompositeSchemeTermInput.id)
            ).all():
                inputs_by_term[term_input.term_id].append(term_input)

    stored_inputs: dict[int, list[CalculationCompositeInput]] = defaultdict(list)
    for row in session.scalars(
        select(CalculationCompositeInput).where(CalculationCompositeInput.calculation_id.in_(lot_by_calculation))
    ).all():
        stored_inputs[row.calculation_id].append(row)
    input_energies = _input_energies(
        session, {row.input_calculation_id for rows in stored_inputs.values() for row in rows}
    )

    verified: dict[int, CompositeEnergyVerification] = {}
    for calculation_id, lot_id in lot_by_calculation.items():
        scheme_id = scheme_by_lot.get(lot_id) if lot_id is not None else None
        if scheme_id is None:
            verified[calculation_id] = CompositeEnergyVerification(
                state=CompositeEnergyVerificationState.unverifiable,
                assembly=assembly,
                reason=REASON_SCHEME_NOT_BOUND,
            )
            continue
        terms = terms_by_scheme.get(scheme_id, [])
        definition = _Definition(
            terms=tuple(
                _Term(
                    key=f"term_{term.position}",
                    operation=term.operation,
                    energy_component=term.energy_component,
                    formula=term.formula,
                    exponent=term.exponent,
                    inputs=tuple(
                        _TermInput(WireSlot(i.slot.value), i.cardinal_number if i.slot.value == "cardinal" else None)
                        for i in inputs_by_term.get(term.id, [])
                    ),
                )
                for term in terms
            )
        )
        positions = [term.position for term in terms]
        by_slot = {
            (row.term_position, row.slot.value, row.cardinal_number): input_energies.get(row.input_calculation_id)
            for row in stored_inputs.get(calculation_id, [])
        }

        def energies_for(
            index: int,
            slot: WireSlot,
            cardinal: int | None,
            _positions: list[int] = positions,
            _by_slot: dict = by_slot,
        ) -> InputEnergies | None:
            return _by_slot.get((_positions[index], slot.value, cardinal))

        check = check_composite_total(definition, energies_for, results[calculation_id].electronic_energy_hartree)
        if check.status == "ok":
            verified[calculation_id] = CompositeEnergyVerification(
                state=CompositeEnergyVerificationState.recomputed,
                assembly=assembly,
                difference_hartree=check.gap,
                tolerance_hartree=check.tolerance,
            )
        elif check.status == "mismatch":
            verified[calculation_id] = CompositeEnergyVerification(
                state=CompositeEnergyVerificationState.recompute_mismatch,
                assembly=assembly,
                difference_hartree=check.gap,
                tolerance_hartree=check.tolerance,
            )
        else:
            verified[calculation_id] = CompositeEnergyVerification(
                state=CompositeEnergyVerificationState.unverifiable, assembly=assembly, reason=check.reason
            )
    return verified


def _input_energies(session: Session, input_ids: set[int]) -> dict[int, InputEnergies]:
    """The stored energies of input calculations: an sp's energy and components, an opt's final energy.

    Mirrors :func:`app.services.composite_input_resolution._input_energies` (the write-time
    reader) in bulk. A calculation of another type has no energies to offer and is absent from
    the result, which a caller reads as "not stated".
    """
    if not input_ids:
        return {}
    types: dict[int, CalculationType] = _as_dict(
        session.execute(select(Calculation.id, Calculation.type).where(Calculation.id.in_(input_ids))).all()
    )
    sp_ids = [cid for cid, kind in types.items() if kind is CalculationType.sp]
    opt_ids = [cid for cid, kind in types.items() if kind is CalculationType.opt]
    energies: dict[int, InputEnergies] = {}
    if opt_ids:
        finals: dict[int, float | None] = _as_dict(
            session.execute(
                select(CalculationOptResult.calculation_id, CalculationOptResult.final_energy_hartree).where(
                    CalculationOptResult.calculation_id.in_(opt_ids)
                )
            ).all()
        )
        for cid in opt_ids:
            energies[cid] = InputEnergies(total=finals.get(cid), components={})
    if sp_ids:
        totals: dict[int, float | None] = _as_dict(
            session.execute(
                select(CalculationSPResult.calculation_id, CalculationSPResult.electronic_energy_hartree).where(
                    CalculationSPResult.calculation_id.in_(sp_ids)
                )
            ).all()
        )
        components: dict[int, dict[WireComponent, float]] = defaultdict(dict)
        for cid, component, value in session.execute(
            select(
                CalculationSPEnergyComponent.calculation_id,
                CalculationSPEnergyComponent.component,
                CalculationSPEnergyComponent.value_hartree,
            ).where(CalculationSPEnergyComponent.calculation_id.in_(sp_ids))
        ).all():
            components[cid][WireComponent(component.value)] = value
        for cid in sp_ids:
            energies[cid] = InputEnergies(total=totals.get(cid), components=components.get(cid, {}))
    return energies
