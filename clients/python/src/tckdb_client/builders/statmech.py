"""Statmech builder for the Phase-4 upload path.

Targets the shared fields between ``StatmechInBundle``
(``app/schemas/workflows/computed_species_upload.py``) and
``BundleStatmechIn``
(``app/schemas/workflows/computed_reaction_upload.py``). Both bundle
schemas carry ``source_calculations: list[StatmechSourceCalcInBundle]``
on the wire, so unlike :class:`tckdb_client.builders.thermo.Thermo`,
the assemblers on both endpoints emit the field — the
``allow_source_calculations`` flag exists for symmetry with thermo
and as a forward-compat lever, not as a per-endpoint gate today.

This builder intentionally exposes only the common subset of fields
(no ``literature`` / ``software_release`` / ``workflow_tool_release``
on the computed-species variant, no ``freq_scale_factor`` ref object,
no ``torsions``). Producers needing those still fall back to the raw
payload form.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable

from tckdb_client.builders.validation import (
    TCKDBBuilderValidationError,
    ensure_non_empty_str,
    ensure_optional_non_empty_str,
    ensure_positive_int,
)

if TYPE_CHECKING:  # pragma: no cover — type-only import
    from tckdb_client.builders.calculation import Calculation

__all__ = ["Statmech"]


# Backend enums — see ``app/db/models/common.py`` for definitions.
_RIGID_ROTOR_KIND_VALUES: frozenset[str] = frozenset(
    {"atom", "linear", "spherical_top", "symmetric_top", "asymmetric_top"}
)
_STATMECH_TREATMENT_VALUES: frozenset[str] = frozenset(
    {"rrho", "rrho_1d", "rrho_nd", "rrho_1d_nd", "rrho_ad", "rrao"}
)
# Identity-only alias map; matches the ``StatmechCalculationRole`` enum.
_STATMECH_ROLE_ALIASES: dict[str, str] = {
    "opt": "opt",
    "freq": "freq",
    "sp": "sp",
    "scan": "scan",
    "composite": "composite",
    "imported": "imported",
}


def _resolve_statmech_role(role: str) -> str:
    if not isinstance(role, str) or not role.strip():
        raise TCKDBBuilderValidationError(
            "Statmech source_calculations keys must be non-empty strings."
        )
    resolved = _STATMECH_ROLE_ALIASES.get(role.strip())
    if resolved is None:
        raise TCKDBBuilderValidationError(
            f"unknown statmech source-calculation role {role!r}; "
            f"supported: {sorted(_STATMECH_ROLE_ALIASES)}."
        )
    return resolved


def _normalise_statmech_source_calculations(value: Any) -> list[tuple[str, Any]]:
    """Coerce ``source_calculations`` into ``[(role, calc), …]``.

    Same accepted shapes as kinetics/thermo: ``dict[role, Calc]``,
    ``dict[role, list[Calc]]``, or ``list[(role, Calc)]``.
    """
    if not value:
        return []

    from tckdb_client.builders.calculation import Calculation as _Calculation
    from tckdb_client.builders.sources import SourceCalculations as _SourceCalculations

    if isinstance(value, _SourceCalculations):
        value = value.as_list()
        if not value:
            return []

    pairs: list[tuple[str, Any]] = []

    if isinstance(value, dict):
        for role, item in value.items():
            wire_role = _resolve_statmech_role(role)
            if isinstance(item, _Calculation):
                pairs.append((wire_role, item))
                continue
            if isinstance(item, (list, tuple)):
                for sub in item:
                    if not isinstance(sub, _Calculation):
                        raise TCKDBBuilderValidationError(
                            f"Statmech.source_calculations[{role!r}] "
                            "list entries must be Calculation builders, "
                            f"got {type(sub).__name__}."
                        )
                    pairs.append((wire_role, sub))
                continue
            raise TCKDBBuilderValidationError(
                f"Statmech.source_calculations[{role!r}] must be a "
                "Calculation builder or a list of them, got "
                f"{type(item).__name__}."
            )
        return pairs

    if isinstance(value, (list, tuple)):
        for i, entry in enumerate(value):
            if not isinstance(entry, (list, tuple)) or len(entry) != 2:
                raise TCKDBBuilderValidationError(
                    "Statmech.source_calculations list entries must be "
                    f"(role, Calculation) 2-tuples; entry {i} is "
                    f"{type(entry).__name__}."
                )
            role, calc = entry
            wire_role = _resolve_statmech_role(role)
            if not isinstance(calc, _Calculation):
                raise TCKDBBuilderValidationError(
                    f"Statmech.source_calculations[{i}] expected a "
                    f"Calculation, got {type(calc).__name__}."
                )
            pairs.append((wire_role, calc))
        return pairs

    raise TCKDBBuilderValidationError(
        "Statmech.source_calculations must be a dict, list of "
        f"(role, Calculation) tuples, or None; got {type(value).__name__}."
    )


@dataclass(eq=False)
class Statmech:
    """One statmech block attached to a species.

    Field set is the common subset of the backend's two statmech
    bundle shapes (``StatmechInBundle`` for computed-species and
    ``BundleStatmechIn`` for computed-reaction). Producers needing
    ``literature`` / ``software_release`` / ``freq_scale_factor`` /
    ``torsions`` should still use the raw payload form.

    ``source_calculations`` accepts the same three shapes as
    :class:`tckdb_client.builders.thermo.Thermo` and
    :class:`tckdb_client.builders.kinetics.Kinetics` — ``dict``,
    ``dict-of-list``, or ``list-of-tuples`` — and is normalised on
    construction.  Both upload endpoints emit the field on the wire
    today; the assembler supplies a ``calc_key_lookup`` so on-wire
    ``calculation_key`` values resolve into the bundle's global calc
    namespace.
    """

    external_symmetry: int | None = None
    point_group: str | None = None
    is_linear: bool | None = None
    rigid_rotor_kind: str | None = None
    statmech_treatment: str | None = None
    uses_projected_frequencies: bool | None = None
    # The default is ``None``; ``__post_init__`` normalises into the
    # canonical ``list[tuple[role, Calculation]]`` representation.
    source_calculations: Any = None
    note: str | None = None
    label: str | None = None

    def __post_init__(self) -> None:
        if self.external_symmetry is not None:
            self.external_symmetry = ensure_positive_int(
                self.external_symmetry, field="external_symmetry", minimum=1,
            )
        if self.point_group is not None:
            self.point_group = ensure_non_empty_str(
                self.point_group, field="point_group",
            )
        if self.is_linear is not None and not isinstance(self.is_linear, bool):
            raise TCKDBBuilderValidationError(
                f"Statmech.is_linear must be a bool, got "
                f"{type(self.is_linear).__name__}."
            )
        if self.rigid_rotor_kind is not None:
            if (
                not isinstance(self.rigid_rotor_kind, str)
                or self.rigid_rotor_kind not in _RIGID_ROTOR_KIND_VALUES
            ):
                raise TCKDBBuilderValidationError(
                    "Statmech.rigid_rotor_kind must be one of "
                    f"{sorted(_RIGID_ROTOR_KIND_VALUES)}, got "
                    f"{self.rigid_rotor_kind!r}."
                )
        if self.statmech_treatment is not None:
            if (
                not isinstance(self.statmech_treatment, str)
                or self.statmech_treatment not in _STATMECH_TREATMENT_VALUES
            ):
                raise TCKDBBuilderValidationError(
                    "Statmech.statmech_treatment must be one of "
                    f"{sorted(_STATMECH_TREATMENT_VALUES)}, got "
                    f"{self.statmech_treatment!r}."
                )
        if (
            self.uses_projected_frequencies is not None
            and not isinstance(self.uses_projected_frequencies, bool)
        ):
            raise TCKDBBuilderValidationError(
                "Statmech.uses_projected_frequencies must be a bool, got "
                f"{type(self.uses_projected_frequencies).__name__}."
            )
        # Accept whatever shape the caller passed; normalise to the
        # canonical list-of-pairs once. If the assembler has already
        # done normalisation (it stores its internal copy that way),
        # the normaliser is a no-op.
        self.source_calculations = _normalise_statmech_source_calculations(
            self.source_calculations
        )
        self.note = ensure_optional_non_empty_str(self.note, field="note")
        self.label = ensure_optional_non_empty_str(self.label, field="label")

    # ------------------------------------------------------------------
    # Assembly helpers
    # ------------------------------------------------------------------

    def source_calculations_iter(self):
        """Iterate over ``(role, Calculation)`` source-link entries."""
        yield from self.source_calculations

    def to_payload(
        self,
        *,
        allow_source_calculations: bool = True,
        calc_key_lookup: Callable[[Any], str] | None = None,
    ) -> dict[str, Any]:
        """Render the dict accepted by the backend statmech bundle shape.

        Both backend statmech variants carry the same fields the builder
        exposes plus ``scientific_origin`` (defaulted server-side).
        ``allow_source_calculations`` is kept for symmetry with the
        :class:`Thermo` API; the default is ``True`` because both
        endpoints accept the field today. Whenever source calcs are
        actually emitted, ``calc_key_lookup`` is required so the
        on-wire ``calculation_key`` values resolve into the bundle's
        global calc namespace without any ``id()`` use.
        """
        out: dict[str, Any] = {}
        if self.external_symmetry is not None:
            out["external_symmetry"] = self.external_symmetry
        if self.point_group is not None:
            out["point_group"] = self.point_group
        if self.is_linear is not None:
            out["is_linear"] = self.is_linear
        if self.rigid_rotor_kind is not None:
            out["rigid_rotor_kind"] = self.rigid_rotor_kind
        if self.statmech_treatment is not None:
            out["statmech_treatment"] = self.statmech_treatment
        if self.uses_projected_frequencies is not None:
            out["uses_projected_frequencies"] = self.uses_projected_frequencies
        if self.note is not None:
            out["note"] = self.note
        if allow_source_calculations and self.source_calculations:
            if calc_key_lookup is None:
                raise TCKDBBuilderValidationError(
                    "Statmech.to_payload(allow_source_calculations=True) "
                    "requires a calc_key_lookup callable so source "
                    "calculations resolve to bundle-local keys."
                )
            out["source_calculations"] = [
                {
                    "calculation_key": calc_key_lookup(calc),
                    "role": role,
                }
                for role, calc in self.source_calculations
            ]
        return out
