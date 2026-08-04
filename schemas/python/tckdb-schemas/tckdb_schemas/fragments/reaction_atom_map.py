"""Depositor-supplied atom correspondence across one reaction (ADR 0011).

A reaction payload says which species react, which saddle point sits between
them, and what rate results. It does not say *which atom is which*. This
fragment is how a depositor says it: that the hydrogen leaving carbon 3 of the
reactant is the hydrogen half-transferred in the transition state and the
hydrogen bonded to oxygen 1 of the product.

**The map is supplied, never derived.** TCKDB does not run a mapping algorithm
and does not guess. For anything but the simplest abstraction there are several
chemically distinct maps consistent with the same reactants and products, and
choosing one by algorithm and storing it unlabelled would manufacture
provenance. The depositor ran the calculation and followed the intrinsic
reaction coordinate; they know. :class:`~tckdb_schemas.enums.AtomMapSource`
carries how the map was obtained, and an inferred map never reads back as a
declared one.

Two legs, both toward the transition state
------------------------------------------
``reactants → TS`` and ``products → TS``. The saddle point is the physical
pivot and both legs are what the IRC actually traverses; composing them gives
reactants → products for free. The reverse composition is not stored, because
storing it instead would discard the very thing that makes the map worth
having.

Indices are geometry-relative
-----------------------------
An atom index is a property of a *geometry*, not of a species: "reactant atom
3" is meaningless until it is said which geometry is being counted. Every
participant therefore names the geometry its indices count into, and the map
names the transition-state geometry, even though in a single-transition-state
bundle there is only one it could be. That redundancy is the point — the
failure mode of implicit indexing is a map that looks fine and refers to a
different atom order than the depositor intended.

Tiering (ADR 0008)
------------------
Absence warns; contradiction blocks. An unmapped reaction is an *incomplete*
record, not a false one — the rate constant is still the rate constant — so a
deposit without a map is accepted and annotated. A map that contradicts itself
is refused: see :func:`validate_reaction_atom_map` for the exact list and why
each entry is definitional rather than an expectation.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Self

from pydantic import Field, model_validator

from tckdb_schemas.common import SchemaBase
from tckdb_schemas.enums import AtomMapSource, ReactionRole

__all__ = [
    "AtomMapParticipantGeometry",
    "ReactionAtomMapIn",
    "ReactionAtomMapParticipantIn",
    "parse_xyz_elements",
    "validate_reaction_atom_map",
]


def parse_xyz_elements(xyz_text: str) -> list[str]:
    """Return the element symbol of each atom of an XYZ block, in file order.

    The returned list is 0-based; atom index ``n`` in a payload is
    ``elements[n - 1]``. Raises :class:`ValueError` on a block whose header
    count and body disagree, because a geometry that cannot be counted cannot
    have a map checked against it.
    """

    lines = xyz_text.strip().splitlines()
    if len(lines) < 1:
        raise ValueError("geometry is not a valid XYZ block.")
    try:
        declared = int(lines[0].strip())
    except ValueError as exc:
        raise ValueError("geometry is not a valid XYZ block.") from exc
    body = [line for line in lines[2:] if line.strip()]
    if len(body) != declared:
        raise ValueError(
            f"geometry declares {declared} atoms but carries {len(body)} "
            "coordinate lines."
        )
    elements: list[str] = []
    for line in body:
        symbol = line.split()[0]
        elements.append(symbol[:1].upper() + symbol[1:].lower())
    return elements


class ReactionAtomMapParticipantIn(SchemaBase):
    """One participant molecule's atoms, identified with transition-state atoms.

    :param side: Which leg this participant sits on.
    :param species_key: Bundle-local key of the species this participant is.
    :param participant_index: 1-based position of this participant on its side.
        Required because the same species may appear twice on one side
        (``2 CH3 → C2H6``) and the two copies map to different saddle-point
        atoms.
    :param geometry_key: Bundle-local key of the geometry the atom indices
        count into. Named explicitly; see the module docstring.
    :param atom_to_ts: ``{atom index in this participant's geometry: atom index
        in the transition-state geometry}``. Both sides 1-based.
    """

    side: ReactionRole
    species_key: str = Field(min_length=1)
    participant_index: int = Field(ge=1)
    geometry_key: str = Field(min_length=1)
    atom_to_ts: dict[int, int] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_indices_are_one_based(self) -> Self:
        for atom_index, ts_atom_index in self.atom_to_ts.items():
            if atom_index < 1 or ts_atom_index < 1:
                raise ValueError(
                    f"atom_map participant '{self.species_key}' "
                    f"[{self.side.value} {self.participant_index}] uses 1-based "
                    "atom indices on both sides."
                )
        return self


class ReactionAtomMapIn(SchemaBase):
    """A complete atom correspondence for one micro reaction.

    :param source: How the map was obtained. Required — there is no default,
        because ``declared`` would manufacture human attribution and
        ``inferred`` would understate a real declaration.
    :param ts_geometry_key: Bundle-local key of the saddle-point geometry both
        legs map onto.
    :param participants: One entry per participant molecule that is mapped.
    :param equivalent_map_count: How many maps are equivalent to this one under
        the symmetry of the participating structures, where known. Omit when
        unknown — ``null`` is not ``1``. Symmetry makes a valid map routinely
        non-unique and TCKDB does not attempt to canonicalise among the
        alternatives.
    :param note: Free text. **Required when ``source`` is ``inferred``**, where
        it must name the algorithm that produced the map.
    """

    source: AtomMapSource
    ts_geometry_key: str = Field(min_length=1)
    participants: list[ReactionAtomMapParticipantIn] = Field(min_length=1)
    equivalent_map_count: int | None = Field(default=None, ge=1)
    note: str | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def validate_inferred_names_its_algorithm(self) -> Self:
        """An inferred map must say what inferred it.

        Definitional, therefore blocking. ADR 0011 permits inference only as a
        *labelled and separable* thing; an inferred map whose origin is
        anonymous is one careless consumer away from being read as a human
        assertion.
        """
        if self.source is AtomMapSource.inferred and self.note is None:
            raise ValueError(
                "atom_map.source='inferred' requires atom_map.note naming the "
                "algorithm that produced the map. An inferred map is recorded "
                "only as a labelled, separable thing (ADR 0011)."
            )
        return self

    @model_validator(mode="after")
    def validate_participants_are_distinct(self) -> Self:
        """Refuse two mappings for the same participant molecule."""
        seen: set[tuple[ReactionRole, int]] = set()
        for participant in self.participants:
            slot = (participant.side, participant.participant_index)
            if slot in seen:
                raise ValueError(
                    f"atom_map names {participant.side.value} "
                    f"{participant.participant_index} more than once. Each "
                    "participant molecule is mapped at most once."
                )
            seen.add(slot)
        return self


@dataclass(frozen=True)
class AtomMapParticipantGeometry:
    """One declared participant of the reaction, with the geometry it may use.

    Assembled by the caller from whichever payload shape it has, so the
    validation below is identical on every deposit path.

    :param side: Which side of the reaction this participant sits on.
    :param species_key: The participant's species key.
    :param participant_index: 1-based position on its side.
    :param geometry_keys: Every geometry key legitimately belonging to this
        participant's species. A map naming anything else is refused.
    :param xyz_by_geometry_key: XYZ text for each of those geometries.
    """

    side: ReactionRole
    species_key: str
    participant_index: int
    geometry_keys: frozenset[str]
    xyz_by_geometry_key: dict[str, str]


def _element_counts(elements: Sequence[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for element in elements:
        counts[element] = counts.get(element, 0) + 1
    return counts


def validate_reaction_atom_map(
    atom_map: ReactionAtomMapIn | None,
    *,
    participants: Sequence[AtomMapParticipantGeometry],
    ts_geometry_key: str | None,
    ts_xyz_text: str | None,
    field_path: str = "atom_map",
) -> None:
    """Refuse an atom map that contradicts itself. Accept an incomplete one.

    Blocking rules, each definitional under ADR 0008 — none of them could fire
    on a correct novel result, because each names a record that cannot be what
    it says it is:

    1. **A map with no transition state.** Both legs point at a saddle point;
       without one there is nothing for them to point at. A barrierless channel
       legitimately has no map, and is not warned about either.
    2. **A geometry the map may not name** — a ``ts_geometry_key`` that is not
       the transition state's geometry, or a participant ``geometry_key`` that
       does not belong to that participant's species. An index counted against
       the wrong geometry silently means the wrong atom.
    3. **An index that is not an atom of the named geometry.** It names
       something that does not exist.
    4. **An element changing across the map.** Carbon does not become nitrogen.
    5. **An atom claimed twice** — one saddle-point atom claimed by two atoms of
       the same leg. A map is a bijection or it is not a map. (The reverse
       direction is structural: ``atom_to_ts`` is a dict.)
    6. **A reactant atom unaccounted for in the products, where no species is
       missing.** Checked only when both legs are complete over every declared
       participant *and* the declared reaction is atom-balanced. When the
       reaction is not balanced a species genuinely is missing, and the same
       discrepancy is incompleteness rather than contradiction — see
       :func:`reaction_atom_map_warnings` in the backend service layer.

    Incompleteness — a participant with no mapping, a mapped participant with
    unmapped atoms, saddle-point atoms nobody claims — is **not** refused here.
    Those are true-but-partial records and they warn.

    :raises ValueError: On any of the above.
    """

    if atom_map is None:
        return

    if ts_xyz_text is None or ts_geometry_key is None:
        raise ValueError(
            f"{field_path} was supplied for a reaction with no transition "
            "state. Both legs of an atom map run toward the saddle point "
            "(ADR 0011), so a reaction without one cannot carry a map."
        )

    if atom_map.ts_geometry_key != ts_geometry_key:
        raise ValueError(
            f"{field_path}.ts_geometry_key='{atom_map.ts_geometry_key}' is not "
            f"the transition state's geometry ('{ts_geometry_key}'). Atom "
            "indices are geometry-relative; a map written against a different "
            "geometry refers to different atoms."
        )

    ts_elements = parse_xyz_elements(ts_xyz_text)
    ts_natoms = len(ts_elements)

    by_slot = {
        (participant.side, participant.participant_index): participant
        for participant in participants
    }

    # Rule 5 is per-leg, so accumulate the claimed saddle-point atoms per side.
    claimed_ts_by_side: dict[ReactionRole, dict[int, str]] = {
        ReactionRole.reactant: {},
        ReactionRole.product: {},
    }
    fully_mapped_slots: set[tuple[ReactionRole, int]] = set()
    elements_by_slot: dict[tuple[ReactionRole, int], list[str]] = {}

    for mapping in atom_map.participants:
        slot = (mapping.side, mapping.participant_index)
        declared = by_slot.get(slot)
        if declared is None:
            raise ValueError(
                f"{field_path} names {mapping.side.value} "
                f"{mapping.participant_index}, which this reaction does not "
                "declare."
            )
        if declared.species_key != mapping.species_key:
            raise ValueError(
                f"{field_path} names species '{mapping.species_key}' as "
                f"{mapping.side.value} {mapping.participant_index}, but that "
                f"participant is species '{declared.species_key}'."
            )
        if mapping.geometry_key not in declared.geometry_keys:
            raise ValueError(
                f"{field_path} maps {mapping.side.value} "
                f"{mapping.participant_index} against geometry "
                f"'{mapping.geometry_key}', which does not belong to species "
                f"'{mapping.species_key}'. Atom indices are geometry-relative."
            )

        elements = parse_xyz_elements(
            declared.xyz_by_geometry_key[mapping.geometry_key]
        )
        natoms = len(elements)
        label = (
            f"{mapping.side.value} {mapping.participant_index} "
            f"('{mapping.species_key}')"
        )

        for atom_index, ts_atom_index in sorted(mapping.atom_to_ts.items()):
            if atom_index > natoms:
                raise ValueError(
                    f"{field_path} maps atom {atom_index} of {label}, but "
                    f"geometry '{mapping.geometry_key}' has {natoms} atoms."
                )
            if ts_atom_index > ts_natoms:
                raise ValueError(
                    f"{field_path} maps atom {atom_index} of {label} onto "
                    f"transition-state atom {ts_atom_index}, but geometry "
                    f"'{atom_map.ts_geometry_key}' has {ts_natoms} atoms."
                )

            element = elements[atom_index - 1]
            ts_element = ts_elements[ts_atom_index - 1]
            if element != ts_element:
                raise ValueError(
                    f"{field_path} maps atom {atom_index} of {label}, which is "
                    f"{element}, onto transition-state atom {ts_atom_index}, "
                    f"which is {ts_element}. An element does not change across "
                    "a reaction."
                )

            claimed = claimed_ts_by_side[mapping.side]
            if ts_atom_index in claimed:
                raise ValueError(
                    f"{field_path} claims transition-state atom "
                    f"{ts_atom_index} twice on the {mapping.side.value} leg: "
                    f"from {claimed[ts_atom_index]} and from atom "
                    f"{atom_index} of {label}. One saddle-point atom is one "
                    "atom."
                )
            claimed[ts_atom_index] = f"atom {atom_index} of {label}"

        elements_by_slot[slot] = elements
        if len(mapping.atom_to_ts) == natoms:
            fully_mapped_slots.add(slot)

    # Rule 6. Only when nothing is missing: every declared participant mapped,
    # every one of their atoms mapped, and the declared reaction balanced.
    if fully_mapped_slots != set(by_slot):
        return
    if not _reaction_is_balanced(elements_by_slot):
        return

    reactant_claims = set(claimed_ts_by_side[ReactionRole.reactant])
    product_claims = set(claimed_ts_by_side[ReactionRole.product])
    if reactant_claims != product_claims:
        missing_in_products = sorted(reactant_claims - product_claims)
        missing_in_reactants = sorted(product_claims - reactant_claims)
        raise ValueError(
            f"{field_path} is complete over every declared participant of an "
            "atom-balanced reaction, yet its two legs do not cover the same "
            "saddle-point atoms: "
            f"{missing_in_products or 'none'} appear only on the reactant leg "
            f"and {missing_in_reactants or 'none'} only on the product leg. "
            "No species is missing, so those atoms are unaccounted for."
        )

    unclaimed = sorted(set(range(1, ts_natoms + 1)) - reactant_claims)
    if unclaimed:
        raise ValueError(
            f"{field_path} is complete over every declared participant of an "
            "atom-balanced reaction, yet transition-state atom(s) "
            f"{unclaimed} are claimed by neither leg. No species is missing, "
            "so the saddle point cannot hold atoms the reaction does not."
        )


def _reaction_is_balanced(
    elements_by_slot: dict[tuple[ReactionRole, int], list[str]],
) -> bool:
    """Say whether the mapped participants conserve every element.

    This is how ADR 0011's hedge "where no species is missing" is decided,
    rather than left to judgement: if the reactant and product element
    multisets agree, nothing is missing and an unaccounted atom is a
    contradiction. If they disagree, a species genuinely is absent from the
    deposit and the same discrepancy is incompleteness, which warns.

    The counts come from the geometries the *map itself* names, so a species
    carrying several conformers leaves nothing undetermined: the depositor said
    which conformer each participant is counted in.
    """

    sides: dict[ReactionRole, dict[str, int]] = {
        ReactionRole.reactant: {},
        ReactionRole.product: {},
    }
    for (side, _index), elements in elements_by_slot.items():
        for element, count in _element_counts(elements).items():
            sides[side][element] = sides[side].get(element, 0) + count
    return sides[ReactionRole.reactant] == sides[ReactionRole.product]
