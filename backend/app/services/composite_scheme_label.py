"""The server-generated label of a user-built composite scheme (ADR 0021, P5).

A user scheme's level of theory has no method name of its own: the producer
sends a definition, not a name (owner decision 2). The server therefore writes
the ``level_of_theory.method`` text, and the scheme's ``name``, as a readable
function of the definition. The label is cosmetic -- the identity is the
``definition_hash`` -- but it is what every table, plot and citation shows, so it
must read like the recipe and be the same every time.

Format
------
``<head>[<term> + <term> + ...]`` where the head is ``CBS`` for an
``extrapolation`` scheme and ``Additive`` for an ``additive`` one, and a term is:

* a base or value term: ``[base ][<component>:]<level>``;
* an extrapolation term: ``[<component>:]<levels>; <formula>[ x=<exponent>]; n=<c1>,<c2>``;
* a difference term: ``d<component>:<high level> - <low level>``.

``<component>`` is omitted when it is ``total`` (``ref`` for ``reference``,
``corr`` for ``correlation``, ``(T)``, ``DBOC``, ``rel`` for the rest) and a level
is ``method/basis`` plus `` fc`` / `` ae`` for a stated core treatment and
`` (key=value, ...)`` for any other stated field (auxiliary basis, dispersion,
...). The levels of one extrapolation that differ only in basis are merged:
``CCSD(T)/cc-pV{T,Q}Z``. Examples::

    CBS[ref:HF/cc-pVQZ + corr:CCSD(T)/cc-pV{T,Q}Z; inverse_power x=3; n=3,4]
    Additive[CCSD(T)/cc-pVQZ + dCV:CCSD(T)/cc-pCVQZ ae - CCSD(T)/cc-pCVQZ fc]

The text is built from the stored level rows' own spellings, so two spellings of
one level that hash alike are labelled by whichever row the definition resolved
to. A label longer than :data:`LABEL_MAX_LENGTH` is cut and ends in
``...#<first 8 hex of the definition hash>``, so a long recipe stays unique where
it matters. The label is ASCII for ASCII input and never contains ``//``.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from app.db.models.common import CompositeSchemeKind, EnergyComponentKind

__all__ = ["LABEL_MAX_LENGTH", "LabelInput", "LabelTerm", "build_scheme_label"]

#: Longest label. Past it the label is cut and tagged with the definition hash.
LABEL_MAX_LENGTH = 200

_COMPONENT_ALIAS: dict[EnergyComponentKind, str] = {
    EnergyComponentKind.total: "",
    EnergyComponentKind.reference: "ref",
    EnergyComponentKind.correlation: "corr",
    EnergyComponentKind.triples: "(T)",
    EnergyComponentKind.dboc: "DBOC",
    EnergyComponentKind.scalar_relativistic: "rel",
}

_HEAD: dict[CompositeSchemeKind, str] = {
    CompositeSchemeKind.extrapolation: "CBS",
    CompositeSchemeKind.additive: "Additive",
}

_CORE_ABBREVIATION = {"frozen_core": "fc", "all_electron": "ae"}


@dataclass(frozen=True)
class LabelInput:
    """One input level of a term, as the label reads it.

    :param slot: ``value``, ``high``, ``low`` or ``cardinal``.
    :param cardinal_number: The declared cardinal number, if any.
    :param method: The level row's method.
    :param basis: Its basis, if any.
    :param core_treatment: ``frozen_core`` / ``all_electron`` / ``None``.
    :param extras: Any other stated identity field, ``name -> value``, in a
        fixed order (auxiliary basis, CABS basis, dispersion, solvent, solvent
        model, keywords, spin treatment).
    """

    slot: str
    cardinal_number: int | None
    method: str
    basis: str | None
    core_treatment: str | None = None
    extras: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class LabelTerm:
    """One term, as the label reads it."""

    operation: str
    energy_component: EnergyComponentKind
    formula: str | None
    exponent: float | None
    inputs: Sequence[LabelInput] = field(default_factory=tuple)


def _number(value: float) -> str:
    """``3`` for 3.0 and ``3.4`` for 3.4: the shortest form that round-trips."""
    return f"{value:.12g}"


def _level_text(item: LabelInput, *, with_basis: bool = True, with_method: bool = True) -> str:
    text = item.method if with_method else ""
    if with_basis and item.basis:
        text += f"/{item.basis}" if text else item.basis
    if item.core_treatment:
        text += f" {_CORE_ABBREVIATION.get(item.core_treatment, item.core_treatment)}"
    if item.extras:
        text += " (" + ", ".join(f"{key}={value}" for key, value in item.extras) + ")"
    return text


def _merge_bases(bases: Sequence[str]) -> str:
    """``cc-pV{T,Q}Z`` from ``cc-pVTZ`` and ``cc-pVQZ``; the plain list when no shape is shared."""
    prefix = bases[0]
    for basis in bases[1:]:
        while not basis.startswith(prefix):
            prefix = prefix[:-1]
    suffix = bases[0][len(prefix) :]
    for basis in bases[1:]:
        tail = basis[len(prefix) :]
        while not tail.endswith(suffix):
            suffix = suffix[1:]
    middles = [basis[len(prefix) : len(basis) - len(suffix)] for basis in bases]
    if (prefix or suffix) and all(middles) and len(set(middles)) == len(middles):
        return f"{prefix}{{{','.join(middles)}}}{suffix}"
    return "{" + ",".join(bases) + "}"


def _extrapolation_levels(inputs: Sequence[LabelInput]) -> str:
    ordered = sorted(inputs, key=lambda item: (item.cardinal_number is None, item.cardinal_number or 0))
    same = {(i.method, i.core_treatment, i.extras) for i in ordered}
    bases = [i.basis for i in ordered]
    if len(same) == 1 and all(bases):
        first = ordered[0]
        shared = _level_text(first, with_basis=False)
        merged = _merge_bases([b for b in bases if b])
        # method / merged-basis, then the core and extras the levels share.
        head = f"{first.method}/{merged}"
        return head + shared[len(first.method) :]
    return "{" + ",".join(_level_text(i) for i in ordered) + "}"


def _component_prefix(component: EnergyComponentKind, *, difference: bool = False) -> str:
    alias = _COMPONENT_ALIAS[component]
    if difference:
        return f"d{alias or 'E'}:"
    return f"{alias}:" if alias else ""


def _term_text(term: LabelTerm) -> str:
    operation = term.operation
    if operation == "extrapolation":
        ordered = sorted(term.inputs, key=lambda item: (item.cardinal_number is None, item.cardinal_number or 0))
        cardinals = ",".join(str(i.cardinal_number) for i in ordered)
        formula = term.formula or ""
        if term.exponent is not None:
            formula += f" x={_number(term.exponent)}"
        return f"{_component_prefix(term.energy_component)}{_extrapolation_levels(term.inputs)}; {formula}; n={cardinals}"
    if operation == "difference":
        by_slot = {i.slot: i for i in term.inputs}
        high, low = by_slot["high"], by_slot["low"]
        return f"{_component_prefix(term.energy_component, difference=True)}{_level_text(high)} - {_level_text(low)}"
    only = term.inputs[0]
    marker = "base " if operation == "base" else ""
    return f"{marker}{_component_prefix(term.energy_component)}{_level_text(only)}"


def build_scheme_label(kind: CompositeSchemeKind, terms: Sequence[LabelTerm], definition_hash: str) -> str:
    """The canonical label of a scheme.

    :param kind: ``extrapolation`` or ``additive``.
    :param terms: The terms in order, with the levels they read.
    :param definition_hash: The scheme's hash; its first eight characters tag a
        label that had to be cut.
    :returns: The label, at most :data:`LABEL_MAX_LENGTH` characters.
    """
    label = f"{_HEAD[kind]}[{' + '.join(_term_text(term) for term in terms)}]"
    if len(label) > LABEL_MAX_LENGTH:
        head = label[: LABEL_MAX_LENGTH - 13]
        label = f"{head}...#{definition_hash[:8]}"
    return label
