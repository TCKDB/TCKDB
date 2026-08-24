"""At what level was this record computed — answered per calculation type.

The evidence summaries answer *how much* evidence a record carries. Nothing
in them answered *at what level*, and until this module existed the answer
sat two levels down, one calculation at a time, behind ``include=calculations``.

Why a map of lists rather than one field
----------------------------------------
Measured on the deployed corpus (2026-08-24), 12 of 34 transition-state
entries carry **two** distinct levels of theory. They are not inconsistent
records — they are the standard composite workflow: optimise and take
frequencies at a cheap functional, then one high-level single point at that
geometry. So "the level of theory of this record" does not exist as a single
value on roughly a third of the corpus, and any field collapsing it to one
fabricates an answer there.

The map is keyed by *calculation type* because that is the axis the
multiplicity actually runs along: `opt` and `freq` at wb97xd/def2tzvp, `sp`
at MRCI+Davidson/aug-cc-pV(T+d)Z.

The value is a list even at length one. Nothing in the schema stops two `sp`
calculations at different levels hanging off one entry, and the same query
run over ``species_entry`` finds **27** (owner, type) pairs that already do
— benchmark comparisons, deposited today. A scalar would have to lie or
break on the first one of those to reach a surface here; a list at length
one costs two characters and never needs a version bump.

**This block reports; it never judges.** There is deliberately no
``levels_consistent`` flag, no ``comparable`` boolean, and no rule that all
calculations should share a level: such a rule would mark 12 of 34 correct
records as suspect. What the multiplicity means for comparability is the
reader's call. Where TCKDB does want to make that call it has a place for it
already — the deterministic trust rubrics, which are versioned, named and
opt-in precisely *because* they are judgements.

Absence and emptiness say different things
------------------------------------------
- **Key absent** — no calculation of that type is attached to this record.
- **Key present, empty list** — calculations of that type exist and not one
  of them names a level of theory (``calculation.lot_id`` is nullable).

Those are different facts about the provenance and must not share a
representation. Collapsing them would hide a gap behind an absence, which is
the same defect the include-omission rule exists to remove.

Cost
----
One grouped statement per *page*, never per record. Every entry point takes
an owner-id list and returns a :class:`LevelsOfTheoryIndex` the record
builders read out of, so a 200-record search page pays the same one
statement a 1-record detail read pays.
``tests/services/scientific_read/test_record_builder_statement_cost.py``
pins that, per surface, at two page sizes.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

from sqlalchemy import ColumnElement, select
from sqlalchemy.orm import Session

from app.db.models.calculation import Calculation
from app.db.models.common import CalculationType
from app.db.models.level_of_theory import LevelOfTheory
from app.db.models.species import ConformerObservation
from app.schemas.reads.scientific_common import LevelOfTheorySummary

#: Key order for the emitted map. The declaration order of
#: :class:`CalculationType`, which reads the way a workflow runs (optimise,
#: then frequencies, then the single point) rather than alphabetically.
#: Deterministic key order keeps the golden and the cross-endpoint equality
#: tests stable; it is presentation, and no consumer should depend on it.
_TYPE_ORDER: tuple[str, ...] = tuple(t.value for t in CalculationType)

#: The per-record map itself: calculation type → the levels observed for it.
LevelsOfTheoryMap = dict[str, list[LevelOfTheorySummary]]


def _type_key(value: object) -> str:
    return value.value if isinstance(value, CalculationType) else str(value)


def _ordered(types: Iterable[str]) -> list[str]:
    seen = set(types)
    known = [t for t in _TYPE_ORDER if t in seen]
    # A type the enum does not know about still gets emitted, after the
    # known ones. Dropping it would make the map quietly incomplete.
    return known + sorted(seen - set(known))


@dataclass(frozen=True)
class LevelsOfTheoryIndex:
    """Levels of theory for a batch of owners, resolved in one statement.

    *owner* is whatever the calling surface groups calculations by: a
    transition-state entry id, a conformer observation id, a conformer
    group id. The index knows nothing about which; it is built by the
    ``for_*`` constructors below, which each name their own owner column.

    Two readers:

    - :meth:`for_owner` — one owner's map, for a record at that grain.
    - :meth:`merged` — the union across several owners, for a record that
      pools them (a TS concept over its entries, a conformer group over its
      observations). The union is the honest answer at pooled grain: it
      states every level used anywhere under the record, which is exactly
      the fact a reader pooling entries needs and cannot get from a count.
    """

    #: owner id → calculation type → levels. Types with no attributed level
    #: are present with an empty tuple; types with no calculation at all are
    #: absent. See the module docstring.
    by_owner: Mapping[int, Mapping[str, tuple[LevelOfTheorySummary, ...]]]

    def for_owner(self, owner_id: int) -> LevelsOfTheoryMap:
        """Return one owner's map. An owner with no calculations gets ``{}``."""
        found = self.by_owner.get(owner_id)
        if not found:
            return {}
        return {key: list(found[key]) for key in _ordered(found)}

    def merged(self, owner_ids: Sequence[int]) -> LevelsOfTheoryMap:
        """Return the union of *owner_ids*' maps, deduplicated per type."""
        pooled: dict[str, dict[int, LevelOfTheorySummary]] = {}
        for owner_id in owner_ids:
            for key, summaries in self.by_owner.get(owner_id, {}).items():
                bucket = pooled.setdefault(key, {})
                for summary in summaries:
                    bucket[summary.level_of_theory_id] = summary
        return {
            key: _sorted_levels(pooled[key].values()) for key in _ordered(pooled)
        }


def _sorted_levels(
    summaries: Iterable[LevelOfTheorySummary],
) -> list[LevelOfTheorySummary]:
    """Order levels within one type deterministically.

    ``display`` first so the list reads alphabetically to a human, then the
    id to break the tie two rows rendering the same string would otherwise
    leave — which is a real case, since ``display`` is not an identity.
    """
    return sorted(summaries, key=lambda s: (s.display, s.level_of_theory_id))


_EMPTY = LevelsOfTheoryIndex(by_owner={})


def _build(
    session: Session,
    *,
    owner_column: ColumnElement[int | None],
    owner_ids: Sequence[int],
    join: tuple[object, ColumnElement[bool]] | None = None,
) -> LevelsOfTheoryIndex:
    """One grouped statement: (owner, calculation type) → distinct levels.

    ``LEFT JOIN`` on the level of theory, not an inner join: a calculation
    with ``lot_id IS NULL`` must still put its *type* in the map, carrying an
    empty list. An inner join would drop the row and the type would go
    missing entirely, which reports "no calculation of this type" about a
    record that has one.
    """
    ids = list(dict.fromkeys(owner_ids))
    if not ids:
        return _EMPTY

    stmt = select(
        owner_column.label("owner_id"),
        Calculation.type,
        LevelOfTheory.id,
        LevelOfTheory.public_ref,
        LevelOfTheory.method,
        LevelOfTheory.basis,
        LevelOfTheory.dispersion,
        LevelOfTheory.solvent,
    ).select_from(Calculation)
    if join is not None:
        target, onclause = join
        stmt = stmt.join(target, onclause)
    stmt = (
        stmt.outerjoin(LevelOfTheory, LevelOfTheory.id == Calculation.lot_id)
        .where(owner_column.in_(ids))
        .distinct()
    )

    collected: dict[int, dict[str, dict[int, LevelOfTheorySummary]]] = {}
    for row in session.execute(stmt):
        per_owner = collected.setdefault(row.owner_id, {})
        # ``setdefault`` on the type, then populate only if a level is
        # attributed: the type key exists as soon as a calculation of that
        # type does, whether or not it names a level.
        bucket = per_owner.setdefault(_type_key(row.type), {})
        if row.id is None:
            continue
        bucket[row.id] = LevelOfTheorySummary(
            level_of_theory_id=row.id,
            level_of_theory_ref=row.public_ref,
            method=row.method,
            basis=row.basis,
            dispersion=row.dispersion,
            solvent=row.solvent,
            label=None,
        )

    return LevelsOfTheoryIndex(
        by_owner={
            owner_id: {
                key: tuple(_sorted_levels(bucket.values()))
                for key, bucket in per_owner.items()
            }
            for owner_id, per_owner in collected.items()
        }
    )


def for_transition_state_entries(
    session: Session, entry_ids: Sequence[int]
) -> LevelsOfTheoryIndex:
    """Index keyed by ``transition_state_entry_id``."""
    return _build(
        session,
        owner_column=Calculation.transition_state_entry_id,
        owner_ids=entry_ids,
    )


def for_conformer_observations(
    session: Session, observation_ids: Sequence[int]
) -> LevelsOfTheoryIndex:
    """Index keyed by ``conformer_observation_id``."""
    return _build(
        session,
        owner_column=Calculation.conformer_observation_id,
        owner_ids=observation_ids,
    )


def for_conformer_groups(
    session: Session, group_ids: Sequence[int]
) -> LevelsOfTheoryIndex:
    """Index keyed by ``conformer_group_id``, via the observation table.

    The group surface's search page holds group ids and not observation
    ids, and resolving the observations first would be a statement per
    record. One join gets the whole page in the single statement the cost
    guard requires.
    """
    return _build(
        session,
        owner_column=ConformerObservation.conformer_group_id,
        owner_ids=group_ids,
        join=(
            ConformerObservation,
            ConformerObservation.id == Calculation.conformer_observation_id,
        ),
    )
