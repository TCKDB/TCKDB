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
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from tckdb_schemas.composite_scheme_rules import (
    COMPOSITE_SCHEME_NESTED,
    assert_composite_scheme_definition,
)
from tckdb_schemas.enums import CoreTreatment

from app.api.error_contract import CodedValueError
from app.chemistry.composite_methods import CompositeMethod, InternalLevel, composite_method_for
from app.db.models.common import (
    CompositeBindingSource,
    CompositeExtrapolationFormula,
    CompositeInputSlot,
    CompositeSchemeKind,
    CompositeTermOperation,
    EnergyComponentKind,
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

    # Method, basis and, when the catalogue states it, the core treatment: a
    # frozen-core and an all-electron level are two levels (ADR 0021, decision 8),
    # so an internal level that depends on it must resolve to the right one.
    row = resolve_level_of_theory_ref(
        session,
        LevelOfTheoryRef(
            method=level.method,
            basis=level.basis,
            core_treatment=CoreTreatment(level.core_treatment) if level.core_treatment else None,
        ),
    )
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


# ---------------------------------------------------------------------------
# User-built schemes (ADR 0021, P5)
# ---------------------------------------------------------------------------


def declared_scheme_lot_payload(definition_hash: str) -> str:
    """The canonical JSON a declared scheme's ``lot_hash`` is taken over.

    ``{"composite_scheme": <definition_hash>}``: sorted keys, no whitespace.
    A normal level's hash payload (``calculation_resolution._level_of_theory_hash``)
    is an object that always has a ``"method"`` key and never a
    ``"composite_scheme"`` one, so the two canonical strings can never be equal
    and the two hashes cannot collide short of a SHA-256 collision. Pinned by a
    test; changing it re-keys every declared level.
    """
    return json.dumps({"composite_scheme": definition_hash}, sort_keys=True, separators=(",", ":"))


def declared_scheme_lot_hash(definition_hash: str) -> str:
    """``lot_hash`` of the level of theory that names a user scheme.

    The one replica every other consumer must agree with: the resolver below
    writes it, and ``scripts/ops/merge_duplicate_levels_of_theory.py`` recomputes
    it from the bound scheme (a declared level's columns cannot reproduce it:
    ``method`` is only the generated label and every other field is ``NULL``).
    """
    return hashlib.sha256(declared_scheme_lot_payload(definition_hash).encode("utf-8")).hexdigest()


def _enum_value(member: Any) -> str:
    return str(getattr(member, "value", member))


def _nested(detail: str, **context: object) -> CodedValueError:
    return CodedValueError(
        COMPOSITE_SCHEME_NESTED,
        detail,
        context={"field": "level_of_theory.composite_scheme", **context},
        message_prefix=False,
    )


@dataclass(frozen=True)
class _ResolvedInput:
    slot: CompositeInputSlot
    cardinal_number: int | None
    level: LevelOfTheory


def _resolve_scheme_inputs(session: Session, definition: Any) -> list[list[_ResolvedInput]]:
    """Resolve every input level of a definition, following merges, refusing composites.

    :raises CodedValueError: ``composite_scheme_nested`` for an input that is itself
        a composite: it carries a ``composite_scheme``, names a catalogued
        composite method, or resolves to a level bound to a scheme.
    """
    # Imported here for the reason given in ``_internal_level_id``.
    from app.services.calculation_resolution import resolve_level_of_theory_ref

    resolved: list[list[_ResolvedInput]] = []
    for term in definition.terms:
        row: list[_ResolvedInput] = []
        for term_input in term.inputs:
            ref = term_input.level_of_theory
            where = f"term {term.key!r}"
            if getattr(ref, "composite_scheme", None) is not None:
                raise _nested(
                    f"{where}: an input level carries its own composite_scheme. A composite is built "
                    "from ordinary levels of theory; nesting composites is refused.",
                    term_key=term.key,
                )
            method = getattr(ref, "method", None)
            if method is None:
                raise _nested(f"{where}: an input level names no method.", term_key=term.key)
            if composite_method_for(method) is not None:
                # Refused before resolving so no named-method level or binding is created.
                raise _nested(
                    f"{where}: input method {method!r} is a named composite method. A composite is "
                    "built from ordinary levels of theory (the single points it would run); send "
                    "those as the term's inputs.",
                    term_key=term.key,
                    method=method,
                )
            level = resolve_level_of_theory_ref(session, ref)
            try:
                assert_ordinary_input_level(session, level.id)
            except CompositeInputLevelError as exc:
                raise _nested(f"{where}: {exc}", term_key=term.key) from exc
            slot = CompositeInputSlot(_enum_value(term_input.slot))
            row.append(_ResolvedInput(slot, term_input.cardinal_number, level))
        resolved.append(row)
    return resolved


def _sorted_inputs(inputs: list[_ResolvedInput]) -> list[_ResolvedInput]:
    """Inputs of a term in canonical order: by slot, then cardinal number."""
    slot_rank = {slot: rank for rank, slot in enumerate(CompositeInputSlot)}
    return sorted(inputs, key=lambda i: (slot_rank[i.slot], -1 if i.cardinal_number is None else i.cardinal_number))


def scheme_definition_canonical_json(definition: Any, resolved: list[list[_ResolvedInput]]) -> str:
    """The canonical JSON a user scheme's ``definition_hash`` is taken over.

    What is in it (ADR 0021, section 2.4): the ``kind``; for each term, **in
    order**, its operation, energy component, formula and exponent; and for each
    input, sorted by slot then cardinal number, its slot, its declared cardinal
    number and the ``lot_hash`` of the level it resolved to (merges followed, so
    two spellings of one level are one input). Anything that changes the number
    for fixed component energies is identity: the formula, the exponent and the
    cardinals.

    What is not: a term's ``key`` (a local name), the literature (provenance),
    and the generated label.

    :param definition: The ``CompositeSchemeDefinition``.
    :param resolved: Its input levels, from :func:`_resolve_scheme_inputs`.
    """
    terms = []
    for term, inputs in zip(definition.terms, resolved, strict=True):
        terms.append(
            {
                "operation": _enum_value(term.operation),
                "energy_component": _enum_value(term.energy_component),
                "formula": None if term.formula is None else _enum_value(term.formula),
                "exponent": None if term.exponent is None else float(term.exponent),
                "inputs": [
                    {
                        "slot": i.slot.value,
                        "cardinal_number": i.cardinal_number,
                        "level_of_theory": i.level.lot_hash,
                    }
                    for i in _sorted_inputs(inputs)
                ],
            }
        )
    payload = {"kind": _enum_value(definition.kind), "terms": terms}
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def scheme_definition_hash(definition: Any, resolved: list[list[_ResolvedInput]]) -> str:
    """``sha256`` hex digest of :func:`scheme_definition_canonical_json`."""
    return hashlib.sha256(scheme_definition_canonical_json(definition, resolved).encode("utf-8")).hexdigest()


def _label_for(definition: Any, resolved: list[list[_ResolvedInput]], definition_hash: str) -> str:
    from app.services.composite_scheme_label import LabelInput, LabelTerm, build_scheme_label

    terms = []
    for term, inputs in zip(definition.terms, resolved, strict=True):
        label_inputs = []
        for item in _sorted_inputs(inputs):
            level = item.level
            extras = tuple(
                (name, _enum_value(value))
                for name, value in (
                    ("aux", level.aux_basis),
                    ("cabs", level.cabs_basis),
                    ("disp", level.dispersion),
                    ("solv", level.solvent),
                    ("solv_model", level.solvent_model),
                    ("kw", level.keywords),
                    ("spin", level.spin_treatment),
                )
                if value is not None
            )
            label_inputs.append(
                LabelInput(
                    slot=item.slot.value,
                    cardinal_number=item.cardinal_number,
                    method=level.method,
                    basis=level.basis,
                    core_treatment=None if level.core_treatment is None else _enum_value(level.core_treatment),
                    extras=extras,
                )
            )
        terms.append(
            LabelTerm(
                operation=_enum_value(term.operation),
                energy_component=EnergyComponentKind(_enum_value(term.energy_component)),
                formula=None if term.formula is None else _enum_value(term.formula),
                exponent=None if term.exponent is None else float(term.exponent),
                inputs=label_inputs,
            )
        )
    return build_scheme_label(CompositeSchemeKind(_enum_value(definition.kind)), terms, definition_hash)


def _get_or_create_user_scheme(
    session: Session,
    definition: Any,
    resolved: list[list[_ResolvedInput]],
    definition_hash: str,
) -> CompositeScheme:
    scheme = _select_scheme(session, definition_hash)
    if scheme is not None:
        return scheme
    literature_id = None
    if getattr(definition, "literature", None) is not None:
        from app.services.literature_resolution import resolve_or_create_literature

        literature_id = resolve_or_create_literature(session, definition.literature).id
    label = _label_for(definition, resolved, definition_hash)
    try:
        with session.begin_nested():
            scheme = CompositeScheme(
                kind=CompositeSchemeKind(_enum_value(definition.kind)),
                name=label,
                definition_hash=definition_hash,
                source_literature_id=literature_id,
            )
            session.add(scheme)
            session.flush()
            for position, (term, inputs) in enumerate(zip(definition.terms, resolved, strict=True)):
                term_row = CompositeSchemeTerm(
                    scheme_id=scheme.id,
                    position=position,
                    operation=CompositeTermOperation(_enum_value(term.operation)),
                    energy_component=EnergyComponentKind(_enum_value(term.energy_component)),
                    formula=None if term.formula is None else CompositeExtrapolationFormula(_enum_value(term.formula)),
                    exponent=None if term.exponent is None else float(term.exponent),
                )
                session.add(term_row)
                session.flush()
                for item in _sorted_inputs(inputs):
                    add_scheme_term_input(
                        session,
                        term_row,
                        slot=item.slot,
                        level_of_theory_id=item.level.id,
                        cardinal_number=item.cardinal_number,
                    )
    except IntegrityError:
        scheme = _select_scheme(session, definition_hash)
        if scheme is None:  # pragma: no cover - the conflict was not ours
            raise
    return scheme


def _select_level_by_hash(session: Session, lot_hash: str) -> LevelOfTheory | None:
    return session.scalar(
        select(LevelOfTheory).where(LevelOfTheory.lot_hash == lot_hash).execution_options(populate_existing=True)
    )


def resolve_declared_scheme_level(session: Session, ref: Any) -> LevelOfTheory:
    """Resolve a ``level_of_theory.composite_scheme`` to the level of theory that names it.

    The path ``resolve_level_of_theory_ref`` takes for a ref carrying a user
    scheme. In order: re-run the wire shape rules (a payload built with
    ``model_copy`` skips validators); resolve each input level, following merges,
    refusing a composite one; canonicalise the definition and hash it; get or
    create the :class:`CompositeScheme` with its terms and inputs; get or create
    the level of theory (``method`` = the generated label, every other field
    ``NULL``, ``lot_hash`` = :func:`declared_scheme_lot_hash`); get or create the
    ``declared`` :class:`LevelOfTheoryComposite` binding.

    Every get-or-create is an insert in a savepoint that falls back to a select on
    ``IntegrityError``, so two uploads racing on one recipe share one scheme, one
    level and one binding.

    :param session: Active SQLAlchemy session.
    :param ref: A ``LevelOfTheoryRef`` whose ``composite_scheme`` is set.
    :returns: The level-of-theory row (merges followed). Its ``method`` is the
        server's label, and nothing about it came from the producer.
    :raises CodedValidationError: ``composite_scheme_malformed`` and its siblings.
    :raises CodedValueError: ``composite_scheme_nested``.
    """
    definition = ref.composite_scheme
    if getattr(ref, "method", None) is not None:
        from tckdb_schemas.composite_scheme_rules import assert_method_xor_composite_scheme

        assert_method_xor_composite_scheme(ref.method, definition)
    assert_composite_scheme_definition(definition)

    resolved = _resolve_scheme_inputs(session, definition)
    definition_hash = scheme_definition_hash(definition, resolved)
    scheme = _get_or_create_user_scheme(session, definition, resolved, definition_hash)

    lot_hash = declared_scheme_lot_hash(definition_hash)
    level = _select_level_by_hash(session, lot_hash)
    if level is None:
        try:
            with session.begin_nested():
                level = LevelOfTheory(method=scheme.name, lot_hash=lot_hash)
                session.add(level)
                session.flush()
        except IntegrityError:
            level = _select_level_by_hash(session, lot_hash)
            if level is None:  # pragma: no cover - the conflict was not ours
                raise
    kept_id = session.scalar(
        select(LevelOfTheoryMerge.into_lot_id).where(LevelOfTheoryMerge.merged_lot_id == level.id)
    )
    if kept_id is not None:
        level = session.get(LevelOfTheory, kept_id)
        assert level is not None

    binding = session.get(LevelOfTheoryComposite, level.id)
    if binding is None:
        session.info.pop("composite_scheme_summary_by_lot", None)
        try:
            with session.begin_nested():
                session.add(
                    LevelOfTheoryComposite(
                        level_of_theory_id=level.id,
                        scheme_id=scheme.id,
                        binding_source=CompositeBindingSource.declared,
                    )
                )
                session.flush()
        except IntegrityError:
            binding = session.scalar(
                select(LevelOfTheoryComposite)
                .where(LevelOfTheoryComposite.level_of_theory_id == level.id)
                .execution_options(populate_existing=True)
            )
            if binding is None:  # pragma: no cover - the conflict was not ours
                raise
    if binding is not None and (
        binding.scheme_id != scheme.id or binding.binding_source != CompositeBindingSource.declared
    ):
        # The hash names the scheme, so a level bound anywhere else is a corrupted
        # row, never a producer's mistake: stop rather than attach a calculation to it.
        raise RuntimeError(
            f"level of theory {level.public_ref} is bound to a different scheme than the definition hashes to"
        )
    return level
