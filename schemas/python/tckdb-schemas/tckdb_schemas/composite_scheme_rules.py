"""Shape rules for a user-built composite scheme (ADR 0021, P5).

A producer who builds a composite energy from its own recipe sends the recipe
inline, as ``level_of_theory.composite_scheme``. This module owns the rules that
say whether such a definition is well formed, and the coded refusals for the
two ways a ``level_of_theory`` can name a recipe wrongly. The wire models call it
on parse and the backend calls the same functions when it resolves the level
(a payload built with ``model_copy`` or ``model_construct`` skips validators),
so the rules cannot differ between the two.

A scheme is a list of terms, in order. Every term contributes one number to the
total, and the total is their **sum**:

* ``base`` / ``value`` -- one input, slot ``value``. The term is that input's
  energy component, as it is.
* ``extrapolation`` -- inputs in slot ``cardinal``, one per declared cardinal
  number, and a ``formula`` (plus an ``exponent`` where the formula has one).
  The term is the formula's basis-set limit of that component.
* ``difference`` -- two inputs, slot ``high`` and slot ``low``. The term is the
  component at ``high`` minus the component at ``low`` (a frozen-core versus
  all-electron pair, a larger basis minus a smaller one, ...).
* ``empirical`` -- refused. A fitted term belongs to a named method the program
  already ran; a producer's scheme has nothing to recompute it from.

``kind`` classifies the recipe. ``extrapolation`` is value and extrapolation
terms only (CCSD(T)/CBS from a reference energy and an extrapolated
correlation energy). ``additive`` has at least one ``difference`` term (a
focal-point scheme: a base plus core-valence, higher-order, relativistic and
DBOC corrections). ``named_method`` is the server's own kind for a catalogued
program recipe and is never sent.

What is *not* here: whether an input level is itself a named composite method
(that needs the catalogue, which lives in the backend), and anything that needs
a database.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from tckdb_schemas.coded_error import CodedValidationError
from tckdb_schemas.composite_formulas import EXPONENT_FORMULAS, FORMULA_POINT_COUNT
from tckdb_schemas.enums import (
    CompositeExtrapolationFormula,
    CompositeInputSlot,
    CompositeSchemeKind,
    CompositeTermOperation,
    EnergyComponentKind,
)

__all__ = [
    "COMPOSITE_INPUT_DUPLICATE",
    "COMPOSITE_INPUT_MISSING",
    "COMPOSITE_INPUT_SLOT_UNKNOWN",
    "COMPOSITE_SCHEME_MALFORMED",
    "COMPOSITE_SCHEME_NAMED_METHOD_NOT_SENDABLE",
    "COMPOSITE_SCHEME_NESTED",
    "LEVEL_OF_THEORY_METHOD_WITH_COMPOSITE_SCHEME",
    "LEVEL_OF_THEORY_REQUIRES_METHOD_OR_COMPOSITE_SCHEME",
    "assert_composite_scheme_definition",
    "MatchedInput",
    "assert_method_xor_composite_scheme",
    "assert_no_method_fields_with_composite_scheme",
    "match_inputs_to_definition",
]

#: A scheme definition's shape is wrong (a formula without an exponent, a slot
#: its operation does not take, a duplicate term key, ...). ``context["rule"]``
#: names which; one code because the producer's repair is always "fix this
#: definition", and the rule keeps the cases apart.
COMPOSITE_SCHEME_MALFORMED = "composite_scheme_malformed"

#: A scheme's input level is itself composite: it carries a ``composite_scheme``,
#: or its method is a catalogued named composite method (CBS-QB3, G4, ...). A
#: composite is built from ordinary levels of theory; nesting is refused.
COMPOSITE_SCHEME_NESTED = "composite_scheme_nested"

#: ``composite_scheme.kind = "named_method"``: that kind is the server's own for a
#: catalogued recipe, reached by sending the method's name.
COMPOSITE_SCHEME_NAMED_METHOD_NOT_SENDABLE = "composite_scheme_named_method_not_sendable"

#: Both ``method`` and ``composite_scheme`` were sent on one level of theory. A
#: named composite method plus an inline definition says the same recipe twice
#: and cannot be told apart from a contradiction.
LEVEL_OF_THEORY_METHOD_WITH_COMPOSITE_SCHEME = "level_of_theory_method_with_composite_scheme"

#: Neither ``method`` nor ``composite_scheme`` was sent.
LEVEL_OF_THEORY_REQUIRES_METHOD_OR_COMPOSITE_SCHEME = "level_of_theory_requires_method_or_composite_scheme"

#: Operations that take one ``value`` input.
_SINGLE_INPUT_OPERATIONS = frozenset({CompositeTermOperation.base, CompositeTermOperation.value})

#: Components a Karton-Martin term may extrapolate: it is a Hartree-Fock formula.
_KARTON_MARTIN_COMPONENTS = frozenset({EnergyComponentKind.reference, EnergyComponentKind.total})


def _malformed(rule: str, detail: str, **context: Any) -> CodedValidationError:
    return CodedValidationError(
        COMPOSITE_SCHEME_MALFORMED,
        detail,
        context={"field": "level_of_theory.composite_scheme", "rule": rule, **context},
        message_prefix=False,
    )


def assert_method_xor_composite_scheme(method: object, composite_scheme: object) -> None:
    """Exactly one of ``method`` and ``composite_scheme`` names the level.

    :raises CodedValidationError: ``level_of_theory_method_with_composite_scheme``
        or ``level_of_theory_requires_method_or_composite_scheme``.
    """
    if method is not None and composite_scheme is not None:
        raise CodedValidationError(
            LEVEL_OF_THEORY_METHOD_WITH_COMPOSITE_SCHEME,
            (
                "level_of_theory carries both method and composite_scheme. A level of theory is "
                "one or the other: send method for a program's own method (a named composite "
                "method such as CBS-QB3 is sent by name, with no definition), or send "
                "composite_scheme to define your own recipe from ordinary levels of theory."
            ),
            context={"field": "level_of_theory", "method": method},
            message_prefix=False,
        )
    if method is None and composite_scheme is None:
        raise CodedValidationError(
            LEVEL_OF_THEORY_REQUIRES_METHOD_OR_COMPOSITE_SCHEME,
            "level_of_theory needs either method (a program's own method) or composite_scheme "
            "(your own recipe); neither was sent.",
            context={"field": "level_of_theory"},
            message_prefix=False,
        )


#: The fields that describe one method's run, which a level naming a ``composite_scheme`` must not carry.
_METHOD_LEVEL_FIELDS = (
    "basis",
    "aux_basis",
    "cabs_basis",
    "dispersion",
    "solvent",
    "solvent_model",
    "keywords",
    "spin_treatment",
    "core_treatment",
)


def assert_no_method_fields_with_composite_scheme(ref: Any) -> None:
    """Refuse method-level fields (basis, dispersion, ...) sent next to a ``composite_scheme``.

    They describe a single method's run; a scheme's levels are stated on its inputs, and
    a stray ``basis`` would be stored nowhere and hashed nowhere.

    :param ref: A ``LevelOfTheoryRef`` (or any object with its attributes).
    :raises CodedValidationError: ``composite_scheme_malformed``, ``rule = ordinary_fields_with_scheme``.
    """
    if getattr(ref, "composite_scheme", None) is None:
        return
    ordinary = sorted(name for name in _METHOD_LEVEL_FIELDS if getattr(ref, name, None) is not None)
    if ordinary:
        raise CodedValidationError(
            COMPOSITE_SCHEME_MALFORMED,
            (
                f"level_of_theory carries composite_scheme together with {', '.join(ordinary)}. These describe a "
                "single method's run; a composite scheme's levels are stated on its inputs. Remove them from the "
                "level of theory and put them on the scheme's input levels."
            ),
            context={"field": "level_of_theory", "rule": "ordinary_fields_with_scheme", "fields": ordinary},
            message_prefix=False,
        )


def _value(member: object) -> str:
    return str(getattr(member, "value", member))


def assert_composite_scheme_definition(definition: Any) -> None:
    """Refuse a malformed user-built scheme definition.

    Takes the wire model or any object with the same attributes. Checks the
    shape only; nothing here looks at a database or at the catalogue.

    :param definition: A ``CompositeSchemeDefinition``.
    :raises CodedValidationError: ``composite_scheme_named_method_not_sendable``,
        ``composite_scheme_nested`` or ``composite_scheme_malformed``.
    """
    kind = CompositeSchemeKind(_value(definition.kind))
    if kind is CompositeSchemeKind.named_method:
        raise CodedValidationError(
            COMPOSITE_SCHEME_NAMED_METHOD_NOT_SENDABLE,
            (
                "composite_scheme.kind='named_method' is the server's own kind for a catalogued "
                "program recipe (CBS-QB3, G4, W1U, ...) and cannot be sent. To use one, send its "
                "name as level_of_theory.method; to define your own recipe, send kind "
                "'extrapolation' or 'additive'."
            ),
            context={"field": "level_of_theory.composite_scheme.kind", "kind": kind.value},
            message_prefix=False,
        )

    terms = list(definition.terms)
    if not terms:
        raise _malformed("no_terms", "composite_scheme needs at least one term.")

    keys = [term.key for term in terms]
    duplicated = sorted({key for key in keys if keys.count(key) > 1})
    if duplicated:
        raise _malformed(
            "term_key_duplicate",
            f"composite_scheme.terms repeats the key(s) {', '.join(repr(k) for k in duplicated)}. "
            "A term key names one term; the calculation's inputs refer to terms by it.",
            term_keys=duplicated,
        )

    operations = []
    for term in terms:
        operation = CompositeTermOperation(_value(term.operation))
        operations.append(operation)
        _assert_term(term, operation)

    has_difference = CompositeTermOperation.difference in operations
    if kind is CompositeSchemeKind.extrapolation and any(
        op not in (CompositeTermOperation.value, CompositeTermOperation.extrapolation) for op in operations
    ):
        raise _malformed(
            "kind_operation_mismatch",
            "kind 'extrapolation' takes only 'value' and 'extrapolation' terms. A recipe with a "
            "base or a difference term is an additive recipe: send kind 'additive'.",
            kind=kind.value,
        )
    if kind is CompositeSchemeKind.additive and not has_difference:
        raise _malformed(
            "kind_operation_mismatch",
            "kind 'additive' needs at least one 'difference' term (a correction such as core-valence "
            "or higher-order). A recipe with only value and extrapolation terms is kind 'extrapolation'.",
            kind=kind.value,
        )


def _assert_term(term: Any, operation: CompositeTermOperation) -> None:
    key = term.key
    formula = None if term.formula is None else CompositeExtrapolationFormula(_value(term.formula))
    exponent = term.exponent
    inputs = list(term.inputs)
    slots = [CompositeInputSlot(_value(i.slot)) for i in inputs]
    where = f"term {key!r}"

    if operation is CompositeTermOperation.empirical:
        raise _malformed(
            "empirical_not_user_definable",
            f"{where}: an 'empirical' term is a fitted correction of a named method that the program "
            "already included; a user-built scheme has nothing to recompute it from. Express a "
            "correction as a 'difference' or 'value' term, or send the named method itself.",
            term_key=key,
        )

    if operation is not CompositeTermOperation.extrapolation:
        if formula is not None or exponent is not None:
            raise _malformed(
                "formula_on_non_extrapolation",
                f"{where}: formula and exponent belong to an 'extrapolation' term only "
                f"(this term is '{operation.value}').",
                term_key=key,
            )

    if operation is not CompositeTermOperation.extrapolation:
        stray = [i.cardinal_number for i in inputs if i.cardinal_number is not None]
        if stray:
            raise _malformed(
                "cardinal_on_non_cardinal_slot",
                f"{where}: cardinal_number belongs to the 'cardinal' slots of an 'extrapolation' term; a "
                f"'{operation.value}' term states none (got {stray}). It would change the scheme's identity "
                "without changing the number.",
                term_key=key,
            )

    if operation in _SINGLE_INPUT_OPERATIONS:
        if slots != [CompositeInputSlot.value]:
            raise _malformed(
                "slot_mismatch",
                f"{where}: a '{operation.value}' term takes exactly one input, in slot 'value'.",
                term_key=key,
                slots=[s.value for s in slots],
            )
        return

    if operation is CompositeTermOperation.difference:
        if sorted(s.value for s in slots) != ["high", "low"]:
            raise _malformed(
                "slot_mismatch",
                f"{where}: a 'difference' term takes exactly two inputs, one in slot 'high' and one "
                "in slot 'low' (the term is high minus low).",
                term_key=key,
                slots=[s.value for s in slots],
            )
        return

    # extrapolation
    if formula is None:
        raise _malformed(
            "formula_required",
            f"{where}: an 'extrapolation' term needs a formula "
            f"({', '.join(f.value for f in CompositeExtrapolationFormula)}).",
            term_key=key,
        )
    if formula in EXPONENT_FORMULAS:
        if exponent is None:
            raise _malformed(
                "exponent_required",
                f"{where}: formula '{formula.value}' needs an exponent (the x in n**-x); the exponent "
                "is part of the recipe's identity, so there is no default.",
                term_key=key,
                formula=formula.value,
            )
        if not math.isfinite(exponent) or exponent <= 0:
            raise _malformed(
                "exponent_invalid",
                f"{where}: the exponent must be a finite number greater than zero, got {exponent!r}.",
                term_key=key,
                exponent=exponent,
            )
    elif exponent is not None:
        raise _malformed(
            "exponent_forbidden",
            f"{where}: formula '{formula.value}' has no exponent; sending one would make two "
            "identical recipes look different.",
            term_key=key,
            formula=formula.value,
        )
    if any(s is not CompositeInputSlot.cardinal for s in slots):
        raise _malformed(
            "slot_mismatch",
            f"{where}: every input of an 'extrapolation' term is in slot 'cardinal'.",
            term_key=key,
            slots=[s.value for s in slots],
        )
    needed = FORMULA_POINT_COUNT[formula]
    if len(inputs) != needed:
        raise _malformed(
            "cardinal_count",
            f"{where}: formula '{formula.value}' extrapolates exactly {needed} cardinal numbers, "
            f"got {len(inputs)}.",
            term_key=key,
            formula=formula.value,
            expected=needed,
        )
    cardinals = [i.cardinal_number for i in inputs]
    if any(c is None for c in cardinals):
        raise _malformed(
            "cardinal_required",
            f"{where}: every input of an 'extrapolation' term states its cardinal_number "
            "(2 for double-zeta, 3 for triple-zeta, ...); it is declared, not read from the basis name.",
            term_key=key,
        )
    if len(set(cardinals)) != len(cardinals):
        raise _malformed(
            "cardinal_duplicate",
            f"{where}: the cardinal numbers of one extrapolation must differ, got {sorted(cardinals)}.",
            term_key=key,
        )
    if formula is CompositeExtrapolationFormula.exponential_three_point:
        ordered = sorted(c for c in cardinals if c is not None)
        if ordered != list(range(ordered[0], ordered[0] + 3)):
            raise _malformed(
                "cardinals_not_consecutive",
                f"{where}: 'exponential_three_point' needs three consecutive cardinal numbers "
                f"(for example 2, 3, 4), got {ordered}.",
                term_key=key,
            )
    if (
        formula is CompositeExtrapolationFormula.karton_martin_scf
        and EnergyComponentKind(_value(term.energy_component)) not in _KARTON_MARTIN_COMPONENTS
    ):
        raise _malformed(
            "component_mismatch",
            f"{where}: 'karton_martin_scf' is a Hartree-Fock formula; its energy_component is "
            "'reference' (or 'total' of an HF level).",
            term_key=key,
            energy_component=_value(term.energy_component),
        )


#: An assembled composite does not name an input for every slot of its scheme
#: (or names none). Block tier (ADR 0008): the recipe cannot be evidenced.
COMPOSITE_INPUT_MISSING = "composite_input_missing"

#: A composite input names a term or slot the scheme does not have.
COMPOSITE_INPUT_SLOT_UNKNOWN = "composite_input_slot_unknown"

#: Two composite inputs fill the same slot of the same term.
COMPOSITE_INPUT_DUPLICATE = "composite_input_duplicate"


@dataclass(frozen=True)
class MatchedInput:
    """One deposited input, tied to the scheme slot it fills.

    :param term_position: The term's place in the definition's ``terms`` list
        (the ``position`` stored on the scheme).
    :param term_key: The depositor's key for the term.
    :param slot: The slot the input fills.
    :param cardinal_number: The slot's declared cardinal number, taken from the
        *definition* (so a ``value`` input carries whatever the definition
        declared), not from the deposited input.
    :param input: The deposited ``composite_result.inputs`` entry.
    """

    term_position: int
    term_key: str
    slot: CompositeInputSlot
    cardinal_number: int | None
    input: Any


def _slot_label(key: str, slot: CompositeInputSlot, cardinal: int | None) -> str:
    return f"{key}/{slot.value}" + (f"/{cardinal}" if cardinal is not None else "")


def match_inputs_to_definition(definition: Any, inputs: Iterable[Any]) -> list[MatchedInput]:
    """Tie every deposited input to its scheme slot, and require all slots filled.

    The one place a ``term_key`` becomes a position. The wire models call it on
    parse; the backend calls it again when it writes the inputs (a payload built
    with ``model_copy`` skips validators), with the definition the same
    calculation's level of theory carried.

    An input matches on ``(term_key, slot)`` for the ``value``, ``high`` and
    ``low`` slots and on ``(term_key, slot, cardinal_number)`` for a ``cardinal``
    slot. A ``cardinal_number`` sent on a non-``cardinal`` slot is ignored: the
    definition already says it.

    :param definition: The ``CompositeSchemeDefinition`` the calculation's level
        of theory carries.
    :param inputs: The deposited ``composite_result.inputs``.
    :returns: One :class:`MatchedInput` per deposited input, in deposit order.
    :raises CodedValidationError: ``composite_input_slot_unknown`` (a term or
        slot the scheme lacks), ``composite_input_duplicate``, or
        ``composite_input_missing`` (a slot with no input).
    """
    slots_by_key: dict[str, tuple[int, dict[tuple[CompositeInputSlot, int | None], Any]]] = {}
    for position, term in enumerate(definition.terms):
        table: dict[tuple[CompositeInputSlot, int | None], Any] = {}
        for term_input in term.inputs:
            slot = CompositeInputSlot(_value(term_input.slot))
            cardinal = term_input.cardinal_number if slot is CompositeInputSlot.cardinal else None
            table[(slot, cardinal)] = term_input
        slots_by_key[term.key] = (position, table)

    matched: list[MatchedInput] = []
    seen: set[tuple[str, CompositeInputSlot, int | None]] = set()
    for deposited in inputs:
        slot = CompositeInputSlot(_value(deposited.slot))
        cardinal = deposited.cardinal_number if slot is CompositeInputSlot.cardinal else None
        entry = slots_by_key.get(deposited.term_key)
        if entry is None or (slot, cardinal) not in entry[1]:
            known = sorted(
                _slot_label(key, s, c) for key, (_, table) in slots_by_key.items() for (s, c) in table
            )
            raise CodedValidationError(
                COMPOSITE_INPUT_SLOT_UNKNOWN,
                (
                    f"composite_result.inputs names {_slot_label(deposited.term_key, slot, cardinal)!r}, "
                    "which the calculation's composite_scheme does not have. Its slots are "
                    f"{', '.join(known)}."
                ),
                context={
                    "field": "composite_result.inputs",
                    "term_key": deposited.term_key,
                    "slot": slot.value,
                    "cardinal_number": cardinal,
                    "scheme_slots": known,
                },
                message_prefix=False,
            )
        marker = (deposited.term_key, slot, cardinal)
        if marker in seen:
            raise CodedValidationError(
                COMPOSITE_INPUT_DUPLICATE,
                (
                    f"composite_result.inputs fills {_slot_label(*marker)!r} more than once. "
                    "One calculation supplies each slot."
                ),
                context={
                    "field": "composite_result.inputs",
                    "term_key": deposited.term_key,
                    "slot": slot.value,
                    "cardinal_number": cardinal,
                },
                message_prefix=False,
            )
        seen.add(marker)
        position, table = entry
        matched.append(
            MatchedInput(
                term_position=position,
                term_key=deposited.term_key,
                slot=slot,
                cardinal_number=table[(slot, cardinal)].cardinal_number,
                input=deposited,
            )
        )

    unfilled = sorted(
        _slot_label(key, slot, cardinal)
        for key, (_, table) in slots_by_key.items()
        for (slot, cardinal) in table
        if (key, slot, cardinal) not in seen
    )
    if unfilled:
        raise CodedValidationError(
            COMPOSITE_INPUT_MISSING,
            (
                "an assembled composite must name a calculation for every slot of its scheme; "
                f"no input fills {', '.join(unfilled)}."
            ),
            context={"field": "composite_result.inputs", "unfilled_slots": unfilled},
            message_prefix=False,
        )
    return matched
