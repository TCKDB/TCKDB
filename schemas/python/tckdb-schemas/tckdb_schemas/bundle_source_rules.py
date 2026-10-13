"""Codes and builders for the source-calculation rules of a bundle.

A bundle names the calculations behind a record by local key: a transport
record's ``source_calculations``, a stability verdict's
``source_calculation_key``. Two rules apply to such a citation beyond "the key
was declared" (:mod:`tckdb_schemas.local_key_codes`):

* the cited calculation must belong to the same subject (species entry or
  transition state) as the record citing it;
* a stability verdict's source must be a job on the same conformer as the
  calculation that carries the verdict.

A third rule sits beside them, on a single calculation rather than a citation:
the conformer a calculation says it is evidence for (``conformer_key``) and the
conformer whose geometry it says it ran on (``geometry_key``) must be the same
one. The citation rules above read the calculation's conformer, so they can
only be sound if the calculation never states two.

ADR 0017: a refusal's code and context belong to the check, not to the layer
that runs it. The request schema refuses these mistakes the moment the body is
parsed and the workflow refuses them again where it knows which entry each key
resolved to, so both raise the same error built by the functions below. The
constants live here, not under ``app``, for the reason
:mod:`tckdb_schemas.local_key_codes` gives: this package may not import the
backend.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any, Protocol

from tckdb_schemas.coded_error import CodedValidationError
from tckdb_schemas.local_key_codes import (
    W_CONFORMER_KEY_UNDECLARED,
    W_GEOMETRY_KEY_UNRESOLVED,
    undeclared_key_error,
)

__all__ = [
    "W_CALCULATION_GEOMETRY_CONFORMER_MISMATCH",
    "W_TRANSITION_STATE_CALCULATION_CONFORMER_KEY",
    "W_TRANSITION_STATE_CALCULATION_SPECIES_GEOMETRY",
    "W_SCF_STABILITY_SOURCE_CALCULATION_OWNER_MISMATCH",
    "W_SCF_STABILITY_SOURCE_CONFORMER_UNANCHORED",
    "W_SCF_STABILITY_SOURCE_CYCLE",
    "W_SCF_STABILITY_SOURCE_GEOMETRY_MISMATCH",
    "W_TRANSPORT_SOURCE_CALCULATION_OWNER_MISMATCH",
    "assert_calculation_geometry_matches_conformer",
    "assert_conformer_primaries_agree",
    "assert_transition_state_calculations_unanchored",
    "conformer_key_by_unique_geometry_key",
    "calculation_geometry_conformer_error",
    "find_scf_source_cycle",
    "owner_mismatch_context",
    "owner_mismatch_detail",
    "owner_mismatch_error",
    "scf_source_cycle_error",
    "scf_source_geometry_error",
    "scf_source_unanchored_error",
]

#: A species-side calculation states a ``conformer_key`` and a ``geometry_key``
#: that belong to different conformers: it says it is evidence for one conformer
#: and that it ran on another's geometry. Neither key wins; the deposit is
#: refused. A ``geometry_key`` that names no conformer's geometry at all is not
#: this case (``geometry_key_unresolved`` governs it).
W_CALCULATION_GEOMETRY_CONFORMER_MISMATCH = "calculation_geometry_conformer_mismatch"

#: A transition-state calculation states a ``conformer_key``. A transition state
#: has no conformers, so the key can anchor nothing; it is refused rather than
#: ignored (as ``source_conformer_key`` is on a transition state).
W_TRANSITION_STATE_CALCULATION_CONFORMER_KEY = "transition_state_calculation_conformer_key"

#: A transition-state calculation's ``geometry_key`` names a species conformer's
#: geometry. A saddle-point job cannot have run on a species minimum's geometry.
W_TRANSITION_STATE_CALCULATION_SPECIES_GEOMETRY = "transition_state_calculation_species_geometry"

#: An SCF stability verdict names, as the job that measured it, a calculation
#: owned by another subject than the calculation carrying the verdict
#: (``scf_stability.source_calculation_key`` on a bundle).
W_SCF_STABILITY_SOURCE_CALCULATION_OWNER_MISMATCH = (
    "scf_stability_source_calculation_owner_mismatch"
)

#: An SCF stability verdict names a measuring job that ran on another
#: conformer than the calculation carrying the verdict. A stability analysis
#: describes one wavefunction at one geometry, so a job on a different
#: conformer says nothing about this one. The sibling of
#: ``thermo_sp_geometry_mismatch`` and ``statmech_sp_geometry_mismatch``.
W_SCF_STABILITY_SOURCE_GEOMETRY_MISMATCH = "scf_stability_source_geometry_mismatch"

#: Stability verdicts that cite one another in a ring: ``a`` says ``b`` measured
#: it and ``b`` says ``a`` did. No job measured either.
W_SCF_STABILITY_SOURCE_CYCLE = "scf_stability_source_cycle"

#: A stability verdict and the job named as having measured it cannot be shown
#: to be on one conformer because at least one of them is not tied to any
#: conformer (no ``conformer_key``, and no ``geometry_key`` naming a conformer's
#: geometry). Distinct from ``scf_stability_source_geometry_mismatch``, which
#: says both are anchored and the anchors differ.
W_SCF_STABILITY_SOURCE_CONFORMER_UNANCHORED = "scf_stability_source_conformer_unanchored"

#: A transport source link cites a calculation owned by another subject.
W_TRANSPORT_SOURCE_CALCULATION_OWNER_MISMATCH = (
    "transport_source_calculation_owner_mismatch"
)


def owner_mismatch_detail(
    *, context: str, subject_noun: str, owner_noun: str, target: str
) -> str:
    """The sentence every owner-mismatch refusal carries, in both layers."""
    return (
        f"{context}: this {subject_noun} belongs to another {owner_noun}, "
        f"not to the {target} target. A supporting {subject_noun} must be "
        f"one of the target {owner_noun}'s own."
    )


def owner_mismatch_context(
    *, field: str, target: str, owner_noun: str
) -> dict[str, Any]:
    """``{"field", "target", "owner_kind"}``; never a row id."""
    return {
        "field": field,
        "target": target,
        "owner_kind": owner_noun.replace(" ", "_"),
    }


def owner_mismatch_error(
    code: str,
    *,
    context: str,
    field: str,
    target: str,
    owner_noun: str = "species entry",
    subject_noun: str = "calculation",
) -> CodedValidationError:
    """The refusal a request-schema validator raises for a cross-owner citation.

    Same ``code``, ``detail`` and ``context`` as the backend's
    ``assert_owned_by`` builds from the same two functions.

    :param context: Field path plus the offending value, as the backend's
        ``context`` argument spells it.
    :param field: Field path alone, for ``context["field"]``.
    :param owner_noun: ``"species entry"`` or ``"transition state entry"``:
        whose own calculation the citation had to be.
    """
    return CodedValidationError(
        code,
        owner_mismatch_detail(
            context=context,
            subject_noun=subject_noun,
            owner_noun=owner_noun,
            target=target,
        ),
        context=owner_mismatch_context(
            field=field, target=target, owner_noun=owner_noun
        ),
        message_prefix=False,
    )


class _KeyedCalculation(Protocol):
    key: str
    geometry_key: str | None
    conformer_key: str | None


def calculation_geometry_conformer_error(
    *,
    calculation_key: str,
    conformer_key: str,
    geometry_key: str,
    geometry_conformer_key: str,
    primary_of: str | None = None,
) -> CodedValidationError:
    """The refusal for a calculation whose two keys describe different conformers.

    :param primary_of: Set for a conformer's own (primary) calculation, which
        is filed under that conformer and ran on its geometry without saying
        so. Then ``conformer_key`` and ``geometry_key`` are the *effective*
        ones: a stated key, or the owning conformer's own where omitted.

    ``context`` is ``{"field", "calculation_key", "conformer_key",
    "geometry_key", "geometry_conformer_key"}``: the depositor's own local keys,
    never a row id. ``geometry_conformer_key`` is the conformer the named
    geometry actually belongs to.

    The owner-mismatch sentence (:func:`owner_mismatch_detail`) does not fit:
    it names one target and one owner kind, and this refusal has to name two
    keys and the conformer each points at.
    """
    if primary_of is not None:
        detail = (
            f"calculations['{calculation_key}'] is the primary calculation of conformer "
            f"'{primary_of}', so it belongs to that conformer and ran on its geometry; but "
            f"conformer_key='{conformer_key}' and geometry_key='{geometry_key}' describe "
            + (
                f"conformer '{geometry_conformer_key}' (geometry '{geometry_key}' belongs to "
                f"conformer '{geometry_conformer_key}'), a different conformer. "
                if conformer_key == geometry_conformer_key
                else f"different conformers (geometry '{geometry_key}' belongs to conformer "
                f"'{geometry_conformer_key}'). "
            )
            + "Neither key overrides the other: state the conformer's own keys or omit them."
        )
    else:
        detail = (
            f"calculations['{calculation_key}']: conformer_key='{conformer_key}' and "
            f"geometry_key='{geometry_key}' describe different conformers "
            f"(geometry '{geometry_key}' belongs to conformer '{geometry_conformer_key}'). "
            f"A calculation is evidence for one conformer and ran on that conformer's "
            f"geometry, so the two keys must name the same conformer; neither overrides "
            f"the other."
        )
    return CodedValidationError(
        W_CALCULATION_GEOMETRY_CONFORMER_MISMATCH,
        detail,
        context={
            "field": f"calculations['{calculation_key}']",
            "calculation_key": calculation_key,
            "conformer_key": conformer_key,
            "geometry_key": geometry_key,
            "geometry_conformer_key": geometry_conformer_key,
        },
        message_prefix=False,
    )


def conformer_key_by_unique_geometry_key(conformers: Iterable[Any]) -> dict[str, str]:
    """``conformer.geometry.key -> conformer.key``, leaving out any geometry key declared twice.

    A geometry key two conformers declare belongs to no one conformer, so the
    agreement checks must not name either; the request-level rule that
    geometry keys are globally unique refuses that payload instead.
    """
    owners: dict[str, list[str]] = {}
    for conf in conformers:
        owners.setdefault(conf.geometry.key, []).append(conf.key)
    return {geom: keys[0] for geom, keys in owners.items() if len(keys) == 1}


def assert_conformer_primaries_agree(conformers: Iterable[Any]) -> None:
    """Refuse a conformer's own calculation whose keys point at another conformer.

    A conformer's primary calculation (``conformers[*].calculation``) is filed
    under that conformer and ran on its geometry; neither needs stating. If it
    states them anyway they must say the same thing, and nothing is silently
    ignored: a ``conformer_key`` that is not the conformer's own key, or a
    ``geometry_key`` that is not its own geometry key, is refused.

    * both keys resolve to conformers but not to one conformer:
      ``calculation_geometry_conformer_mismatch`` (the stated key, or the
      conformer's own where omitted, against the other);
    * a key that names nothing declared: ``conformer_key_undeclared`` /
      ``geometry_key_unresolved``;
    * a geometry key declared by two conformers is left to the uniqueness rule.
    """
    conformers = list(conformers)
    conformer_keys = {c.key for c in conformers}
    all_geometry_keys = {c.geometry.key for c in conformers}
    owner_of = conformer_key_by_unique_geometry_key(conformers)
    for conf in conformers:
        calc = conf.calculation
        if calc.conformer_key is not None and calc.conformer_key not in conformer_keys:
            raise undeclared_key_error(
                W_CONFORMER_KEY_UNDECLARED,
                f"Conformer '{conf.key}' calculation '{calc.key}' conformer_key "
                f"must reference one of that species's own conformers.",
                field=f"calculations['{calc.key}'].conformer_key",
                key=calc.conformer_key,
                declared=conformer_keys,
            )
        if calc.geometry_key is not None and calc.geometry_key not in all_geometry_keys:
            raise undeclared_key_error(
                W_GEOMETRY_KEY_UNRESOLVED,
                f"Conformer '{conf.key}' calculation '{calc.key}' geometry_key "
                f"must reference one of that species's conformer geometries.",
                field=f"calculations['{calc.key}'].geometry_key",
                key=calc.geometry_key,
                declared=all_geometry_keys,
            )
        effective_conformer = calc.conformer_key or conf.key
        effective_geometry = calc.geometry_key or conf.geometry.key
        geometry_owner = owner_of.get(effective_geometry)
        if geometry_owner is None or geometry_owner == effective_conformer == conf.key:
            continue
        raise calculation_geometry_conformer_error(
            calculation_key=calc.key,
            conformer_key=effective_conformer,
            geometry_key=effective_geometry,
            geometry_conformer_key=geometry_owner,
            primary_of=conf.key,
        )


def assert_transition_state_calculations_unanchored(
    calculations: Iterable[Any], species_geometry_keys: Iterable[str]
) -> None:
    """Refuse, on a transition state's calculations, a ``conformer_key`` or a species minimum's geometry.

    A transition state has no conformers, so a ``conformer_key`` there anchors
    nothing and was silently dropped; and a saddle-point job cannot have run on
    a species conformer's geometry. Both are refused with their own codes.

    :param species_geometry_keys: Every conformer geometry key of the request's
        species.
    """
    species_geometry_keys = set(species_geometry_keys)
    for calc in calculations:
        if calc.conformer_key is not None:
            raise CodedValidationError(
                W_TRANSITION_STATE_CALCULATION_CONFORMER_KEY,
                f"calculations['{calc.key}'].conformer_key='{calc.conformer_key}': a "
                f"transition state has no conformers, so a calculation of it cannot "
                f"state a conformer_key. Remove it.",
                context={
                    "field": f"calculations['{calc.key}'].conformer_key",
                    "calculation_key": calc.key,
                    "conformer_key": calc.conformer_key,
                },
                message_prefix=False,
            )
        if calc.geometry_key is not None and calc.geometry_key in species_geometry_keys:
            raise CodedValidationError(
                W_TRANSITION_STATE_CALCULATION_SPECIES_GEOMETRY,
                f"calculations['{calc.key}'].geometry_key='{calc.geometry_key}' names a "
                f"species conformer's geometry. A transition-state calculation ran on "
                f"the saddle point, not on a species minimum.",
                context={
                    "field": f"calculations['{calc.key}'].geometry_key",
                    "calculation_key": calc.key,
                    "geometry_key": calc.geometry_key,
                },
                message_prefix=False,
            )


def assert_calculation_geometry_matches_conformer(
    calculations: Iterable[_KeyedCalculation],
    conformer_key_by_geometry_key: Mapping[str, str],
) -> None:
    """Refuse a calculation whose ``geometry_key`` belongs to another conformer than its ``conformer_key``.

    :param conformer_key_by_geometry_key: The species' own conformers, as
        ``conformer.geometry.key -> conformer.key``.

    A calculation stating only one key, or a ``geometry_key`` that names no
    conformer's geometry (or a ``conformer_key`` that names none), is left
    alone: the undeclared-key rules own those, and a free-standing geometry is
    not a disagreement.
    """
    for calc in calculations:
        if calc.conformer_key is None or calc.geometry_key is None:
            continue
        geometry_conformer = conformer_key_by_geometry_key.get(calc.geometry_key)
        if geometry_conformer is None or geometry_conformer == calc.conformer_key:
            continue
        raise calculation_geometry_conformer_error(
            calculation_key=calc.key,
            conformer_key=calc.conformer_key,
            geometry_key=calc.geometry_key,
            geometry_conformer_key=geometry_conformer,
        )


def scf_source_geometry_error(
    *, field: str, key: str, carrier_key: str
) -> CodedValidationError:
    """The refusal for a stability source on another conformer than its carrier.

    ``context`` is ``{"field", "key", "carrier_key"}``: the depositor's own
    local keys, never a row id.
    """
    return CodedValidationError(
        W_SCF_STABILITY_SOURCE_GEOMETRY_MISMATCH,
        f"{field}='{key}' names a calculation on a different conformer than "
        f"'{carrier_key}'. A stability analysis describes one wavefunction at "
        f"one geometry, so the job that measured a verdict must be on the "
        f"same conformer as the calculation that carries it.",
        context={"field": field, "key": key, "carrier_key": carrier_key},
        message_prefix=False,
    )


def scf_source_cycle_error(cycle: list[str]) -> CodedValidationError:
    """The refusal for stability verdicts that cite one another in a ring.

    ``context`` is ``{"cycle": [...]}``: the depositor's own calculation keys in
    the order they cite one another, never a row id.
    """
    ring = " -> ".join([*cycle, cycle[0]])
    return CodedValidationError(
        W_SCF_STABILITY_SOURCE_CYCLE,
        f"scf_stability.source_calculation_key forms a cycle: {ring}. A "
        f"stability verdict cannot be measured by a job whose own verdict it "
        f"measures.",
        context={"cycle": list(cycle)},
        message_prefix=False,
    )


def scf_source_unanchored_error(
    *, field: str, key: str, carrier_key: str, unanchored_key: str
) -> CodedValidationError:
    """The refusal for a stability source that cannot be placed on the carrier's conformer.

    ``context`` is ``{"field", "key", "carrier_key", "unanchored_key"}``, all of
    them the depositor's own local keys.
    """
    return CodedValidationError(
        W_SCF_STABILITY_SOURCE_CONFORMER_UNANCHORED,
        f"{field}='{key}': '{unanchored_key}' is not tied to any conformer (it "
        f"has no conformer_key, and no geometry_key naming a conformer's "
        f"geometry), so whether the job that measured the verdict is on the "
        f"same conformer as '{carrier_key}' cannot be checked. Give both "
        f"calculations a conformer_key.",
        context={
            "field": field,
            "key": key,
            "carrier_key": carrier_key,
            "unanchored_key": unanchored_key,
        },
        message_prefix=False,
    )


def find_scf_source_cycle(sources: Mapping[str, str]) -> list[str] | None:
    """Return a cycle in ``carrier key -> source key`` links, or ``None``.

    Each carrier cites at most one source, so the links form a functional
    graph and a walk from every start finds any cycle. A returned list names
    the keys on the cycle in order, e.g. ``["a", "b"]`` for a cites b and b
    cites a. A self-reference is a cycle of one.
    """
    done: set[str] = set()
    for start in sources:
        path: list[str] = []
        seen_on_path: dict[str, int] = {}
        node: str | None = start
        while node is not None and node not in done:
            if node in seen_on_path:
                return path[seen_on_path[node]:]
            seen_on_path[node] = len(path)
            path.append(node)
            node = sources.get(node)
        done.update(path)
    return None
