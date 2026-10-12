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

__all__ = [
    "W_CALCULATION_GEOMETRY_CONFORMER_MISMATCH",
    "W_SCF_STABILITY_SOURCE_CALCULATION_OWNER_MISMATCH",
    "W_SCF_STABILITY_SOURCE_CONFORMER_UNANCHORED",
    "W_SCF_STABILITY_SOURCE_CYCLE",
    "W_SCF_STABILITY_SOURCE_GEOMETRY_MISMATCH",
    "W_TRANSPORT_SOURCE_CALCULATION_OWNER_MISMATCH",
    "assert_calculation_geometry_matches_conformer",
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
) -> CodedValidationError:
    """The refusal for a calculation whose two keys describe different conformers.

    ``context`` is ``{"field", "calculation_key", "conformer_key",
    "geometry_key", "geometry_conformer_key"}``: the depositor's own local keys,
    never a row id. ``geometry_conformer_key`` is the conformer the named
    geometry actually belongs to.

    The owner-mismatch sentence (:func:`owner_mismatch_detail`) does not fit:
    it names one target and one owner kind, and this refusal has to name two
    keys and the conformer each points at.
    """
    return CodedValidationError(
        W_CALCULATION_GEOMETRY_CONFORMER_MISMATCH,
        f"calculations['{calculation_key}']: conformer_key='{conformer_key}' and "
        f"geometry_key='{geometry_key}' describe different conformers "
        f"(geometry '{geometry_key}' belongs to conformer "
        f"'{geometry_conformer_key}'). A calculation is evidence for one "
        f"conformer and ran on that conformer's geometry, so the two keys must "
        f"name the same conformer; neither overrides the other.",
        context={
            "field": f"calculations['{calculation_key}']",
            "calculation_key": calculation_key,
            "conformer_key": conformer_key,
            "geometry_key": geometry_key,
            "geometry_conformer_key": geometry_conformer_key,
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
