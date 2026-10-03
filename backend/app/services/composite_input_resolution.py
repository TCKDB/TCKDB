"""Persisting an assembled composite's inputs, and the checks around them (ADR 0021, P5).

An ``assembled`` composite is arithmetic over other deposited calculations. Its
``composite_result.inputs`` name those calculations, one per slot of the scheme
its level of theory carries; this module resolves each name to a calculation,
checks the recipe against what was named, writes ``calc_composite_input`` and the
mirroring ``composite_input`` dependency edge, and finally recomputes the total
from the inputs' stored energies **to check the deposited one**. TCKDB never
stores a total it computed itself (owner decision 5): the recomputed number is
formed, compared, and discarded.

Why the write is deferred
-------------------------
:func:`app.services.composite_result_resolution.persist_composite_result` runs
while a calculation is being created, when the other calculations of the request
(and the request's key namespace) may not exist yet. It therefore only
*registers* an assembled composite here (:func:`register_pending_composite`);
every workflow calls :func:`finalize_composite_inputs` once all of its
calculations are persisted, next to the converged-opt and named-composite
warnings. ``tests/invariants/test_composite_p5_invariants.py`` fails any workflow
that reports named-composite deposits without finalising inputs, and a
``before_commit`` hook (``app/db/composite_commit_guard.py``) refuses to commit a
session that still holds an assembled composite with no inputs written: the one way for this to go silently wrong is a
workflow that forgets, and neither a green suite nor a quiet log would show it.

The checks (ADR 0008 tiers)
---------------------------
Block (a definition is violated; no correct deposit can satisfy it):

* ``composite_input_missing`` / ``_slot_unknown`` / ``_duplicate`` -- the wire
  matcher, re-run here (:func:`tckdb_schemas.composite_scheme_rules.match_inputs_to_definition`);
* ``composite_input_type_invalid`` -- an input is not an ``sp`` or an ``opt``;
* ``composite_input_owner_mismatch`` -- an input belongs to another species or
  transition-state entry than the composite;
* ``composite_input_level_mismatch`` -- an input ran at a different level of
  theory than its slot's (merges followed on both sides);
* ``composite_input_geometry_mismatch`` -- two inputs, or an input and the
  composite, that both declare a geometry declare different ones;
* ``composite_total_mismatch`` -- the deposited total is not what the scheme gives
  for the inputs' stored energies, beyond ``max(1e-6, 5e-7 * n)`` hartree.

Warn (an expectation is unmet; the deposit is kept):

* ``composite_input_geometry_undeclared`` -- an input declares no geometry, so
  "one geometry" cannot be checked for it;
* ``composite_total_unverifiable`` -- a needed energy or component is not stated,
  the correlation convention cannot be determined, or no total was deposited.

An input's *geometry* is the one its energy is at: an ``sp``'s input geometry, an
``opt``'s final output geometry.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session
from tckdb_schemas.composite_scheme_rules import MatchedInput, match_inputs_to_definition
from tckdb_schemas.composite_total import (
    InputEnergies,
    TotalCheck,
    assert_composite_total_recomputes,
    check_composite_total,
)
from tckdb_schemas.enums import CompositeInputSlot as WireSlot
from tckdb_schemas.enums import EnergyComponentKind as WireComponent
from tckdb_schemas.fragments.calculation import COMPOSITE_INPUT_REFERENCE_INVALID, CompositeResultPayload

from app.api.error_contract import CodedValueError
from app.db.composite_commit_guard import PENDING_COMPOSITE_KEY
from app.db.models.calculation import (
    Calculation,
    CalculationCompositeInput,
    CalculationInputGeometry,
    CalculationOptResult,
    CalculationOutputGeometry,
    CalculationSPEnergyComponent,
    CalculationSPResult,
)
from app.db.models.common import (
    CalculationDependencyRole,
    CalculationGeometryRole,
    CalculationType,
    CompositeInputSlot,
)
from app.db.models.composite_scheme import (
    CompositeSchemeTerm,
    CompositeSchemeTermInput,
    LevelOfTheoryComposite,
)
from app.db.models.level_of_theory import LevelOfTheory, LevelOfTheoryMerge
from app.schemas.upload_warning import UploadWarning
from app.services.calculation_ownership import W_COMPOSITE_INPUT_OWNER_MISMATCH, assert_calculation_owned_by
from app.services.composite_scheme_resolution import term_positions_for
from app.services.local_key_resolution import resolve_calculation_key
from app.services.upload_reference import W_UNKNOWN_CALCULATION_REF, unknown_reference

logger = logging.getLogger(__name__)

__all__ = [
    "COMPOSITE_INPUT_GEOMETRY_MISMATCH",
    "COMPOSITE_INPUT_LEVEL_MISMATCH",
    "COMPOSITE_INPUT_TYPE_INVALID",
    "W_COMPOSITE_INPUT_GEOMETRY_UNDECLARED",
    "W_COMPOSITE_TOTAL_UNVERIFIABLE",
    "finalize_composite_inputs",
    "register_pending_composite",
]

#: An input calculation is not an ``sp`` or an ``opt``.
COMPOSITE_INPUT_TYPE_INVALID = "composite_input_type_invalid"

#: An input calculation ran at a different level of theory than its slot's.
COMPOSITE_INPUT_LEVEL_MISMATCH = "composite_input_level_mismatch"

#: Inputs (or an input and the composite) declare different geometries.
COMPOSITE_INPUT_GEOMETRY_MISMATCH = "composite_input_geometry_mismatch"

#: An input declares no geometry.
W_COMPOSITE_INPUT_GEOMETRY_UNDECLARED = "composite_input_geometry_undeclared"

#: The deposited total cannot be checked.
W_COMPOSITE_TOTAL_UNVERIFIABLE = "composite_total_unverifiable"

_PENDING_KEY = PENDING_COMPOSITE_KEY


@dataclass(frozen=True)
class PendingComposite:
    """An assembled composite whose inputs have not been written yet.

    :param calculation_id: The composite calculation.
    :param payload: Its ``composite_result``.
    :param definition: The scheme definition its level of theory carried, which
        is what ``term_key`` is resolved against.
    """

    calculation_id: int
    payload: CompositeResultPayload
    definition: Any


def register_pending_composite(
    session: Session,
    calculation: Calculation,
    payload: CompositeResultPayload,
    definition: Any,
) -> None:
    """Remember an assembled composite until :func:`finalize_composite_inputs` writes its inputs."""
    session.info.setdefault(_PENDING_KEY, {})[calculation.id] = PendingComposite(
        calculation_id=calculation.id, payload=payload, definition=definition
    )


# ---------------------------------------------------------------------------
# Resolving the named calculations
# ---------------------------------------------------------------------------


def _resolve_input_calculation(
    session: Session,
    matched: MatchedInput,
    index: int,
    calculations_by_key: Mapping[str, Any] | None,
) -> Calculation:
    deposited = matched.input
    # The wire rule (exactly one of key and ref), re-run for a payload built without validation.
    if (deposited.calculation_key is None) == (deposited.calculation_ref is None):
        raise CodedValueError(
            COMPOSITE_INPUT_REFERENCE_INVALID,
            f"composite_result.inputs[{index}] must name its calculation by exactly one of calculation_key "
            "(a calculation declared in this request) or calculation_ref (a calc_... ref from an earlier deposit).",
            context={"field": f"composite_result.inputs[{index}]", "term_key": deposited.term_key},
            message_prefix=False,
        )
    if deposited.calculation_key is not None:
        field = f"composite_result.inputs[{index}].calculation_key"
        target = resolve_calculation_key(deposited.calculation_key, calculations_by_key or {}, field=field)
        if isinstance(target, Calculation):
            return target
        calculation = session.get(Calculation, target)
        if calculation is None:  # pragma: no cover - the namespace holds persisted rows
            raise LookupError(f"calculation key {deposited.calculation_key!r} resolved to no row")
        return calculation
    field = f"composite_result.inputs[{index}].calculation_ref"
    found = session.scalar(select(Calculation).where(Calculation.public_ref == deposited.calculation_ref))
    if found is None:
        raise unknown_reference(
            code=W_UNKNOWN_CALCULATION_REF,
            field=field,
            kind="calculation",
            ref=deposited.calculation_ref,
            remedy="Deposit the calculation first, or correct the ref.",
        )
    return found


def _name_of(matched: MatchedInput) -> str:
    """How the depositor named the input: their key or ref, never a row id."""
    deposited = matched.input
    return deposited.calculation_key or deposited.calculation_ref or "?"


# ---------------------------------------------------------------------------
# Level check
# ---------------------------------------------------------------------------


def _follow_merge(session: Session, level_id: int) -> int:
    merged_into = session.scalar(
        select(LevelOfTheoryMerge.into_lot_id).where(LevelOfTheoryMerge.merged_lot_id == level_id)
    )
    return merged_into if merged_into is not None else level_id


def _slot_levels(session: Session, scheme_id: int) -> dict[tuple[int, WireSlot, int | None], int]:
    """``(term position, slot, cardinal) -> level_of_theory_id`` of a stored scheme."""
    rows = session.execute(
        select(
            CompositeSchemeTerm.position,
            CompositeSchemeTermInput.slot,
            CompositeSchemeTermInput.cardinal_number,
            CompositeSchemeTermInput.level_of_theory_id,
        )
        .join(CompositeSchemeTermInput, CompositeSchemeTermInput.term_id == CompositeSchemeTerm.id)
        .where(CompositeSchemeTerm.scheme_id == scheme_id)
    ).all()
    return {
        (position, WireSlot(slot.value), cardinal if slot == CompositeInputSlot.cardinal else None): level_id
        for position, slot, cardinal, level_id in rows
    }


def _level_label(session: Session, level_id: int) -> str:
    level = session.get(LevelOfTheory, level_id)
    if level is None:  # pragma: no cover - a foreign key holds it
        return "unknown"
    return f"{level.method}/{level.basis}" if level.basis else level.method


# ---------------------------------------------------------------------------
# Geometry
# ---------------------------------------------------------------------------


def _energy_geometry_ids(session: Session, calculation: Calculation) -> frozenset[int]:
    """The geometries the calculation's energy is at: an sp's input, an opt's final output."""
    if calculation.type == CalculationType.opt:
        rows = session.scalars(
            select(CalculationOutputGeometry.geometry_id).where(
                CalculationOutputGeometry.calculation_id == calculation.id,
                CalculationOutputGeometry.role == CalculationGeometryRole.final,
            )
        ).all()
    else:
        rows = session.scalars(
            select(CalculationInputGeometry.geometry_id).where(CalculationInputGeometry.calculation_id == calculation.id)
        ).all()
    return frozenset(rows)


def _composite_input_geometry_ids(session: Session, composite: Calculation) -> frozenset[int]:
    return frozenset(
        session.scalars(
            select(CalculationInputGeometry.geometry_id).where(CalculationInputGeometry.calculation_id == composite.id)
        ).all()
    )


# ---------------------------------------------------------------------------
# Energies of an input
# ---------------------------------------------------------------------------


def _input_energies(session: Session, calculation: Calculation) -> InputEnergies:
    """The stored energies of an input: an sp's energy and components, an opt's final energy."""
    if calculation.type == CalculationType.opt:
        total = session.scalar(
            select(CalculationOptResult.final_energy_hartree).where(CalculationOptResult.calculation_id == calculation.id)
        )
        return InputEnergies(total=total, components={})
    total = session.scalar(
        select(CalculationSPResult.electronic_energy_hartree).where(CalculationSPResult.calculation_id == calculation.id)
    )
    rows = session.execute(
        select(CalculationSPEnergyComponent.component, CalculationSPEnergyComponent.value_hartree).where(
            CalculationSPEnergyComponent.calculation_id == calculation.id
        )
    ).all()
    return InputEnergies(total=total, components={WireComponent(c.value): v for c, v in rows})


# ---------------------------------------------------------------------------
# Finalising
# ---------------------------------------------------------------------------


def finalize_composite_inputs(
    session: Session,
    calculation_ids: Iterable[int],
    *,
    calculations_by_key: Mapping[str, Any] | None = None,
) -> list[UploadWarning]:
    """Write the inputs of every pending assembled composite among ``calculation_ids``.

    Called once by each workflow after all of its calculations exist. A workflow
    with no key namespace passes ``None``: a ``calculation_key`` input then
    refuses as undeclared, and a ``calculation_ref`` input still resolves.

    :param session: Active SQLAlchemy session.
    :param calculation_ids: Every calculation id the request persisted (or reused).
    :param calculations_by_key: The request's calculation-key namespace, mapping a
        local key to the persisted ``Calculation`` (or its id).
    :returns: The warning-tier findings, in composite order.
    :raises CodedValueError: and ``CodedValidationError``: for each block-tier
        refusal in the module docstring.
    """
    pending: dict[int, PendingComposite] = session.info.get(_PENDING_KEY, {})
    warnings: list[UploadWarning] = []
    for calculation_id in dict.fromkeys(calculation_ids):
        item = pending.pop(calculation_id, None)
        if item is not None:
            warnings.extend(_finalize_one(session, item, calculations_by_key))
    if not pending:
        session.info.pop(_PENDING_KEY, None)
    return warnings


def _finalize_one(
    session: Session,
    item: PendingComposite,
    calculations_by_key: Mapping[str, Any] | None,
) -> list[UploadWarning]:
    from app.services.calculation_resolution import (
        add_dependency_edge_idempotent,
        assert_dependency_role_type_compatible,
    )

    composite = session.get(Calculation, item.calculation_id)
    assert composite is not None
    payload = item.payload
    warnings: list[UploadWarning] = []
    # The producer's term order is not identity: map the positions it used to the canonical ones.
    positions = term_positions_for(session, item.definition)

    # The wire matcher ran on parse; a payload built with ``model_copy`` skipped it.
    # ``entry.term_position`` is where the producer listed the term (what the total check, which walks
    # the definition as sent, indexes by); ``positions[...]`` is its canonical, stored position.
    matched = match_inputs_to_definition(item.definition, payload.inputs)

    binding = session.get(LevelOfTheoryComposite, composite.lot_id) if composite.lot_id is not None else None
    if binding is None:  # pragma: no cover - persist_composite_result required the binding
        raise LookupError("assembled composite has no bound scheme")
    slot_levels = _slot_levels(session, binding.scheme_id)

    resolved: list[tuple[MatchedInput, Calculation]] = []
    for index, entry in enumerate(matched):
        calculation = _resolve_input_calculation(session, entry, index, calculations_by_key)
        field = f"composite_result.inputs[{index}]"
        name = _name_of(entry)

        if calculation.type not in (CalculationType.sp, CalculationType.opt):
            logger.info(
                "composite input type invalid: composite id=%s input id=%s type=%s",
                composite.id,
                calculation.id,
                calculation.type.value,
            )
            raise CodedValueError(
                COMPOSITE_INPUT_TYPE_INVALID,
                f"{field}: {name!r} is a '{calculation.type.value}' calculation. An assembled "
                "composite reads energies, so its inputs are single points ('sp') or optimisations "
                "('opt', whose final energy is the single-point value at their own level).",
                context={"field": field, "input": name, "calculation_type": calculation.type.value},
                message_prefix=False,
            )

        assert_calculation_owned_by(
            calculation,
            code=W_COMPOSITE_INPUT_OWNER_MISMATCH,
            target="composite",
            context=field,
            species_entry_id=composite.species_entry_id,
            transition_state_entry_id=composite.transition_state_entry_id,
        )

        cardinal = entry.cardinal_number if entry.slot == WireSlot.cardinal else None
        expected_id = _follow_merge(session, slot_levels[(positions[entry.term_position], entry.slot, cardinal)])
        actual_id = _follow_merge(session, calculation.lot_id) if calculation.lot_id is not None else None
        if actual_id != expected_id:
            expected_label = _level_label(session, expected_id)
            actual_label = _level_label(session, actual_id) if actual_id is not None else "unknown"
            raise CodedValueError(
                COMPOSITE_INPUT_LEVEL_MISMATCH,
                f"{field}: {name!r} ran at {actual_label}, but slot "
                f"{entry.term_key}/{entry.slot.value}"
                + (f"/{cardinal}" if cardinal is not None else "")
                + f" of the scheme is at {expected_label}. An input must be the calculation at the "
                "level of theory its slot names; a difference between spellings of one level is "
                "already followed.",
                context={
                    "field": field,
                    "input": name,
                    "term_key": entry.term_key,
                    "slot": entry.slot.value,
                    "expected_level_of_theory": expected_label,
                    "actual_level_of_theory": actual_label,
                },
                message_prefix=False,
            )
        resolved.append((entry, calculation))

    warnings.extend(_check_geometries(session, composite, resolved))

    for entry, calculation in resolved:
        cardinal = entry.cardinal_number if entry.slot == WireSlot.cardinal else None
        session.add(
            CalculationCompositeInput(
                calculation_id=composite.id,
                term_position=positions[entry.term_position],
                slot=CompositeInputSlot(entry.slot.value),
                input_calculation_id=calculation.id,
                cardinal_number=cardinal,
            )
        )
        assert_dependency_role_type_compatible(
            calculation,
            CalculationDependencyRole.composite_input,
            context=f"composite_result.inputs ({entry.term_key}/{entry.slot.value})",
        )
        add_dependency_edge_idempotent(
            session,
            parent_calculation_id=calculation.id,
            child_calculation_id=composite.id,
            dependency_role=CalculationDependencyRole.composite_input,
            context=f"composite_result.inputs ({entry.term_key}/{entry.slot.value})",
            derived=True,
        )
    session.flush()

    by_slot = {
        (entry.term_position, entry.slot, entry.cardinal_number if entry.slot == WireSlot.cardinal else None): calc
        for entry, calc in resolved
    }
    energies: dict[int, InputEnergies] = {}

    def energies_for(position: int, slot: WireSlot, cardinal: int | None) -> InputEnergies | None:
        calc = by_slot.get((position, slot, cardinal))
        if calc is None:
            return None
        if calc.id not in energies:
            energies[calc.id] = _input_energies(session, calc)
        return energies[calc.id]

    check = check_composite_total(item.definition, energies_for, payload.electronic_energy_hartree)
    assert_composite_total_recomputes(check)
    if check.status == "unverifiable":
        warnings.append(_unverifiable_warning(check))
    return warnings


def _check_geometries(
    session: Session,
    composite: Calculation,
    resolved: list[tuple[MatchedInput, Calculation]],
) -> list[UploadWarning]:
    warnings: list[UploadWarning] = []
    declared: list[tuple[str, frozenset[int]]] = []
    seen: set[int] = set()
    for index, (entry, calculation) in enumerate(resolved):
        if calculation.id in seen:
            continue
        seen.add(calculation.id)
        geometries = _energy_geometry_ids(session, calculation)
        name = _name_of(entry)
        if geometries:
            declared.append((name, geometries))
        else:
            warnings.append(
                UploadWarning(
                    field=f"composite_result.inputs[{index}]",
                    code=W_COMPOSITE_INPUT_GEOMETRY_UNDECLARED,
                    message=(
                        f"The input {name!r} declares no geometry, so TCKDB cannot check that every input "
                        "of this composite was computed at one geometry. Declare the single point's input "
                        "geometry (or the optimisation's final one) to make that checkable."
                    ),
                )
            )
    own = _composite_input_geometry_ids(session, composite)
    if own:
        declared.append(("the composite itself", own))
    if len({geometries for _, geometries in declared}) > 1:
        names = [name for name, _ in declared]
        raise CodedValueError(
            COMPOSITE_INPUT_GEOMETRY_MISMATCH,
            "the inputs of an assembled composite must all be computed at one geometry, but "
            f"{', '.join(repr(n) for n in names)} declare different ones. A recipe that sums energies "
            "taken at different geometries describes no single molecule.",
            context={"field": "composite_result.inputs", "inputs": names},
            message_prefix=False,
        )
    return warnings


_REASON_TEXT = {
    "input_energy_not_stated": "an input calculation has no stored energy",
    "component_not_stated": "an input does not state the energy component the scheme reads",
    "correlation_convention_undeterminable": (
        "an input's correlation component cannot be tied to its energy (the reference energy, the "
        "correlation or the energy is not stated, or they do not add up), so whether (T) is inside "
        "it is not known"
    ),
    "extrapolation_degenerate": "the extrapolation is degenerate for these energies",
    "no_total_deposited": "no electronic_energy_hartree was deposited to compare",
}


def _unverifiable_warning(check: TotalCheck) -> UploadWarning:
    reason = check.reason or ""
    where = f" (term {check.term_key!r})" if check.term_key else ""
    return UploadWarning(
        field="composite_result.electronic_energy_hartree",
        code=W_COMPOSITE_TOTAL_UNVERIFIABLE,
        message=(
            f"The deposited total could not be checked against the inputs{where}: "
            f"{_REASON_TEXT.get(reason, reason)}. It was stored as sent; TCKDB recomputes a total only "
            "to check it and never stores one."
        ),
    )
