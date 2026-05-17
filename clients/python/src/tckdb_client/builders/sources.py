"""``SourceCalculations`` — opt-in helper for role-tagged provenance.

The Phase-1 implementation of the design captured in
``clients/python/docs/source_calculation_ergonomics.md``. Reduces the
repetitive ``source_calculations=[(role, calc), …]`` tuple lists that
demos and producer code accumulate, **without** introducing any of the
footguns §10 of that doc rules out (no inference, no workflow-tool
presets, no domain-specific defaults).

Construction is by kwargs (one role token → one :class:`Calculation`
or a non-empty list of them). The escape hatch :meth:`add` accepts
role tokens that are not valid Python identifiers. Emission goes
through :meth:`as_list` (all entries, insertion order) or
:meth:`only` (filtered to the roles the caller names, in the order
the caller names them).

The helper performs only the lightest validation: role tokens are
non-empty strings, values are ``Calculation`` builders. Endpoint
role-vocabulary checks remain the responsibility of the individual
builders (``Thermo``, ``Statmech``, ``Kinetics``, ``Transport``).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Iterator

from tckdb_client.builders.validation import (
    TCKDBBuilderValidationError,
    ensure_non_empty_str,
)

if TYPE_CHECKING:  # pragma: no cover — type-only import
    from tckdb_client.builders.calculation import Calculation

__all__ = ["SourceCalculations"]


class SourceCalculations:
    """Reusable bag of role-tagged ``Calculation`` references.

    Construct via kwargs::

        sources = SourceCalculations(opt=opt, freq=freq, sp=sp)
        sources = SourceCalculations(
            reactant_energy=[ch3_sp, h_sp],
            ts_energy=ts_sp,
            freq=ts_freq,
        )

    Or via the :meth:`add` escape hatch for role tokens that are not
    valid Python identifiers (e.g. ``"k-inf"``).

    Pass the result through :meth:`as_list` (all entries, insertion
    order) or :meth:`only` (filtered, caller-requested role order)
    to any builder that accepts ``source_calculations=``.
    """

    __slots__ = ("_entries",)

    def __init__(self, **roles_to_calcs: Any) -> None:
        self._entries: list[tuple[str, Any]] = []
        for role, value in roles_to_calcs.items():
            self._absorb(role, value)

    # ------------------------------------------------------------------
    # Construction helpers
    # ------------------------------------------------------------------

    def _absorb(self, role: str, value: Any) -> None:
        """Append ``role`` → ``value`` (a ``Calculation`` or list of them)."""
        ensure_non_empty_str(role, field="SourceCalculations role")
        from tckdb_client.builders.calculation import Calculation as _Calculation

        if isinstance(value, _Calculation):
            self._entries.append((role, value))
            return
        if isinstance(value, (list, tuple)):
            if not value:
                raise TCKDBBuilderValidationError(
                    f"SourceCalculations[{role!r}] must not be an empty "
                    "list; pass at least one Calculation or omit the role."
                )
            for i, item in enumerate(value):
                if not isinstance(item, _Calculation):
                    raise TCKDBBuilderValidationError(
                        f"SourceCalculations[{role!r}][{i}] expected a "
                        f"Calculation, got {type(item).__name__}."
                    )
                self._entries.append((role, item))
            return
        raise TCKDBBuilderValidationError(
            f"SourceCalculations[{role!r}] must be a Calculation or a "
            "non-empty list/tuple of Calculation builders; got "
            f"{type(value).__name__}."
        )

    def add(self, role: str, calc: "Calculation") -> "SourceCalculations":
        """Append one ``(role, calc)`` entry; returns ``self`` for chaining.

        Use this for role tokens that aren't valid Python identifiers,
        e.g. ``sources.add("k-inf", sp_kinf)``.
        """
        ensure_non_empty_str(role, field="SourceCalculations role")
        from tckdb_client.builders.calculation import Calculation as _Calculation

        if not isinstance(calc, _Calculation):
            raise TCKDBBuilderValidationError(
                f"SourceCalculations.add({role!r}, …) expected a "
                f"Calculation, got {type(calc).__name__}."
            )
        self._entries.append((role, calc))
        return self

    # ------------------------------------------------------------------
    # Emission
    # ------------------------------------------------------------------

    def as_list(self) -> list[tuple[str, Any]]:
        """All entries in insertion order — the shape builders accept."""
        return list(self._entries)

    def only(self, *roles: str) -> list[tuple[str, Any]]:
        """Entries whose role appears in ``roles``, in the order requested.

        Roles are emitted in the order the caller passes them; entries
        sharing the same role keep their relative source order. Unknown
        roles raise :class:`TCKDBBuilderValidationError` so a typo
        surfaces at the call site rather than as a silently empty list.
        Duplicate roles in ``*roles`` repeat the matching entries.
        """
        if not roles:
            return []
        for r in roles:
            ensure_non_empty_str(r, field="SourceCalculations.only role")
        present = {role for role, _calc in self._entries}
        missing = [r for r in roles if r not in present]
        if missing:
            raise TCKDBBuilderValidationError(
                f"SourceCalculations.only(...) requested role(s) "
                f"{missing!r} not present; available roles: "
                f"{sorted(present)!r}."
            )
        out: list[tuple[str, Any]] = []
        for requested in roles:
            for role, calc in self._entries:
                if role == requested:
                    out.append((role, calc))
        return out

    # ------------------------------------------------------------------
    # Dunder helpers
    # ------------------------------------------------------------------

    def __len__(self) -> int:
        return len(self._entries)

    def __iter__(self) -> Iterator[tuple[str, Any]]:
        return iter(self._entries)

    def __repr__(self) -> str:
        roles = [role for role, _calc in self._entries]
        return f"SourceCalculations(roles={roles!r})"
