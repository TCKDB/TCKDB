"""Kinetics builder + factory and the SDK's small unit vocabulary.

Phase 2 exposes :meth:`Kinetics.modified_arrhenius` only — that covers
the kinetics fits ARC / Arkane produce today. Future factories
(plain Arrhenius, Lindemann, multi-channel PDep) plug in here without
disturbing the wire shape of existing uploads.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Iterator

from tckdb_client.builders.validation import (
    TCKDBBuilderValidationError,
    ensure_non_empty_str,
    ensure_optional_non_empty_str,
)

if TYPE_CHECKING:  # pragma: no cover — type-only import
    from tckdb_client.builders.calculation import Calculation

__all__ = ["Kinetics"]


# ---------------------------------------------------------------------------
# Unit vocabulary
# ---------------------------------------------------------------------------

# Arrhenius-A user-facing aliases → backend ``ArrheniusAUnits`` value.
# Kept intentionally small; producers with exotic units should reach
# for the raw payload form instead of expanding this map.
_A_UNIT_ALIASES: dict[str, str] = {
    # per-second
    "s^-1": "per_s",
    "1/s": "per_s",
    "s-1": "per_s",
    "per_s": "per_s",
    # cm3/mol/s
    "cm3/mol/s": "cm3_mol_s",
    "cm3 mol s": "cm3_mol_s",
    "cm3_mol_s": "cm3_mol_s",
    # cm3/molecule/s
    "cm3/molecule/s": "cm3_molecule_s",
    "cm3_molecule_s": "cm3_molecule_s",
    # m3/mol/s
    "m3/mol/s": "m3_mol_s",
    "m3_mol_s": "m3_mol_s",
}

# Ea aliases → (backend ``ActivationEnergyUnits`` enum value,
# multiplier-to-kJ/mol). The builder always emits ``kj_mol`` on the
# wire (server-side enum) so producers only ever have to think in
# their familiar units up-front.
_EA_UNIT_HANDLERS: dict[str, tuple[str, float]] = {
    "kJ/mol": ("kj_mol", 1.0),
    "kj/mol": ("kj_mol", 1.0),
    "kj_mol": ("kj_mol", 1.0),
    "kcal/mol": ("kj_mol", 4.184),
    "kcal_mol": ("kj_mol", 4.184),
}

# Kinetics source-role aliases accepted by ``source_calculations``
# kwargs → backend ``KineticsCalculationRole`` enum value.
_SOURCE_ROLE_ALIASES: dict[str, str] = {
    "reactant_energy": "reactant_energy",
    "product_energy": "product_energy",
    "ts_energy": "ts_energy",
    "freq": "freq",
    "irc": "irc",
    "fit_source": "fit_source",
}


def _resolve_a_units(value: str) -> str:
    """Map a user-facing Arrhenius-A unit string to the wire enum value."""
    if not isinstance(value, str) or not value.strip():
        raise TCKDBBuilderValidationError(
            "Kinetics A_units must be a non-empty string."
        )
    resolved = _A_UNIT_ALIASES.get(value.strip())
    if resolved is None:
        raise TCKDBBuilderValidationError(
            f"unknown Arrhenius A unit {value!r}; supported: "
            f"{sorted(set(_A_UNIT_ALIASES))}."
        )
    return resolved


def _resolve_ea(value: float, units: str) -> tuple[float, str]:
    """Convert ``(Ea, Ea_units)`` to ``(Ea_kJ_per_mol, 'kj_mol')``."""
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise TCKDBBuilderValidationError(
            f"Kinetics Ea must be numeric, got {type(value).__name__}."
        )
    if not isinstance(units, str) or not units.strip():
        raise TCKDBBuilderValidationError(
            "Kinetics Ea_units must be a non-empty string."
        )
    handler = _EA_UNIT_HANDLERS.get(units.strip())
    if handler is None:
        raise TCKDBBuilderValidationError(
            f"unknown Ea units {units!r}; supported: "
            f"{sorted(set(_EA_UNIT_HANDLERS))}."
        )
    wire_unit, multiplier = handler
    return float(value) * multiplier, wire_unit


def _normalise_source_calculations(value: Any) -> list[tuple[str, Any]]:
    """Coerce the user's ``source_calculations`` value into ``[(role, calc), …]``.

    Accepts three input shapes so callers don't have to pre-flatten:

    - ``None`` / empty → empty list.
    - ``dict`` where values are :class:`Calculation` builders.
    - ``dict`` where values are lists of :class:`Calculation` builders.
    - ``list`` of ``(role, Calculation)`` tuples — explicit duplicate roles.

    The returned list preserves caller order (insertion order for
    dicts on CPython 3.7+; literal order for tuples). Role names are
    routed through :func:`_resolve_source_role` so we surface a clean
    validation error before reaching payload time.
    """
    if not value:
        return []

    # Import here to dodge the calculation ↔ kinetics circular
    # import that would otherwise occur if Calculation grew a
    # reference to Kinetics in the future.
    from tckdb_client.builders.calculation import Calculation as _Calculation
    from tckdb_client.builders.sources import SourceCalculations as _SourceCalculations

    # Producers may pass a ``SourceCalculations`` helper directly; the
    # canonical list-of-tuples shape it emits is exactly what the
    # remainder of this function expects, so flatten and fall through.
    if isinstance(value, _SourceCalculations):
        value = value.as_list()
        if not value:
            return []

    pairs: list[tuple[str, Any]] = []

    if isinstance(value, dict):
        for role, item in value.items():
            wire_role = _resolve_source_role(role)
            if isinstance(item, _Calculation):
                pairs.append((wire_role, item))
                continue
            if isinstance(item, (list, tuple)):
                for sub in item:
                    if not isinstance(sub, _Calculation):
                        raise TCKDBBuilderValidationError(
                            f"Kinetics.source_calculations[{role!r}] "
                            "list entries must be Calculation builders, "
                            f"got {type(sub).__name__}."
                        )
                    pairs.append((wire_role, sub))
                continue
            raise TCKDBBuilderValidationError(
                f"Kinetics.source_calculations[{role!r}] must be a "
                "Calculation builder or a list of them, got "
                f"{type(item).__name__}."
            )
        return pairs

    if isinstance(value, (list, tuple)):
        for i, entry in enumerate(value):
            if (
                not isinstance(entry, (list, tuple))
                or len(entry) != 2
            ):
                raise TCKDBBuilderValidationError(
                    "Kinetics.source_calculations list entries must be "
                    f"(role, Calculation) 2-tuples; entry {i} is "
                    f"{type(entry).__name__}."
                )
            role, calc = entry
            wire_role = _resolve_source_role(role)
            if not isinstance(calc, _Calculation):
                raise TCKDBBuilderValidationError(
                    f"Kinetics.source_calculations[{i}] expected a "
                    f"Calculation, got {type(calc).__name__}."
                )
            pairs.append((wire_role, calc))
        return pairs

    raise TCKDBBuilderValidationError(
        "Kinetics.source_calculations must be a dict, list of "
        f"(role, Calculation) tuples, or None; got {type(value).__name__}."
    )


def _resolve_source_role(role: str) -> str:
    if not isinstance(role, str) or not role.strip():
        raise TCKDBBuilderValidationError(
            "Kinetics source_calculations keys must be non-empty strings."
        )
    resolved = _SOURCE_ROLE_ALIASES.get(role.strip())
    if resolved is None:
        raise TCKDBBuilderValidationError(
            f"unknown kinetics source-calculation role {role!r}; "
            f"supported: {sorted(set(_SOURCE_ROLE_ALIASES))}."
        )
    return resolved


# ---------------------------------------------------------------------------
# Kinetics
# ---------------------------------------------------------------------------


@dataclass
class Kinetics:
    """One kinetics fit attached to a reaction.

    Construct via :meth:`modified_arrhenius`; the bare constructor is
    reserved for internal use and tests. The ``model_kind`` field is
    the discriminator that selects which factory built the object —
    Phase 2 only supports ``"modified_arrhenius"``.

    The Arrhenius parameters are stored in the wire-ready form: ``a``
    is the pre-exponential factor, ``a_units`` is the backend enum
    value (e.g. ``"cm3_mol_s"``), and ``reported_ea`` is already in
    kJ/mol because the builder converts up front.
    """

    model_kind: str
    a: float
    a_units: str
    n: float
    reported_ea: float
    reported_ea_units: str
    tmin_k: float | None = None
    tmax_k: float | None = None
    degeneracy: float | None = None
    tunneling_model: str | None = None
    note: str | None = None
    label: str | None = None
    # role → Calculation builder. Order-preserving from the user's
    # source_calculations dict; payload emission walks this list to
    # produce ``(calculation_key, role)`` entries.
    source_calculations: list[tuple[str, Any]] = field(default_factory=list)
    _validated: bool = field(default=False, init=False, repr=False)

    def __post_init__(self) -> None:
        self._validated = True

    # ----- factories ------------------------------------------------

    @classmethod
    def modified_arrhenius(
        cls,
        A: float,
        A_units: str,
        n: float,
        Ea: float,
        Ea_units: str = "kJ/mol",
        *,
        Tmin: float | None = None,
        Tmax: float | None = None,
        degeneracy: float | None = None,
        tunneling_model: str | None = None,
        source_calculations: (
            "dict[str, Calculation]"
            " | dict[str, list[Calculation]]"
            " | list[tuple[str, Calculation]]"
            " | None"
        ) = None,
        label: str | None = None,
        note: str | None = None,
    ) -> "Kinetics":
        """Build a modified-Arrhenius kinetics record.

        ``A`` must be strictly positive and numeric. ``A_units`` is
        normalised via the SDK's unit alias map (see module docstring).
        ``Ea`` is converted to kJ/mol up front so the on-wire value is
        always in the backend's ``kj_mol`` enum; ``Ea_units`` controls
        only the *input* units.

        ``source_calculations`` accepts three shapes so producers don't
        have to pre-flatten their data:

        - ``dict[str, Calculation]`` — one calc per role (Phase 2).
        - ``dict[str, list[Calculation]]`` — multiple calcs per role
          (e.g. two ``reactant_energy`` entries for a bimolecular
          reaction).
        - ``list[tuple[str, Calculation]]`` — explicit ordering and
          duplicate roles. The most flexible form; preserves caller
          order verbatim.

        Phase-2 roles: ``reactant_energy``, ``product_energy``,
        ``ts_energy``, ``freq``, ``irc``, ``fit_source``. The
        :class:`tckdb_client.builders.uploads.ComputedReactionUpload`
        assembler resolves each :class:`Calculation` to its
        bundle-local key at payload time.
        """
        if isinstance(A, bool) or not isinstance(A, (int, float)):
            raise TCKDBBuilderValidationError(
                f"Kinetics.A must be numeric, got {type(A).__name__}."
            )
        if A <= 0:
            raise TCKDBBuilderValidationError(
                f"Kinetics.A must be > 0, got {A!r}."
            )
        if isinstance(n, bool) or not isinstance(n, (int, float)):
            raise TCKDBBuilderValidationError(
                f"Kinetics.n must be numeric, got {type(n).__name__}."
            )

        a_units_wire = _resolve_a_units(A_units)
        ea_kj, ea_units_wire = _resolve_ea(Ea, Ea_units)

        if Tmin is not None:
            if isinstance(Tmin, bool) or not isinstance(Tmin, (int, float)):
                raise TCKDBBuilderValidationError(
                    f"Kinetics.Tmin must be numeric, got {type(Tmin).__name__}."
                )
            if Tmin <= 0:
                raise TCKDBBuilderValidationError(
                    f"Kinetics.Tmin must be > 0, got {Tmin!r}."
                )
        if Tmax is not None:
            if isinstance(Tmax, bool) or not isinstance(Tmax, (int, float)):
                raise TCKDBBuilderValidationError(
                    f"Kinetics.Tmax must be numeric, got {type(Tmax).__name__}."
                )
            if Tmax <= 0:
                raise TCKDBBuilderValidationError(
                    f"Kinetics.Tmax must be > 0, got {Tmax!r}."
                )
        if Tmin is not None and Tmax is not None and Tmin > Tmax:
            raise TCKDBBuilderValidationError(
                f"Kinetics: Tmin ({Tmin}) must be <= Tmax ({Tmax})."
            )

        if degeneracy is not None:
            if isinstance(degeneracy, bool) or not isinstance(degeneracy, (int, float)):
                raise TCKDBBuilderValidationError(
                    f"Kinetics.degeneracy must be numeric, got "
                    f"{type(degeneracy).__name__}."
                )
            if degeneracy <= 0:
                raise TCKDBBuilderValidationError(
                    f"Kinetics.degeneracy must be > 0, got {degeneracy!r}."
                )

        tunneling_model_clean = ensure_optional_non_empty_str(
            tunneling_model, field="tunneling_model"
        )
        label_clean = ensure_optional_non_empty_str(label, field="label")
        note_clean = ensure_optional_non_empty_str(note, field="note")

        resolved_sources = _normalise_source_calculations(source_calculations)

        return cls(
            model_kind="modified_arrhenius",
            a=float(A),
            a_units=a_units_wire,
            n=float(n),
            reported_ea=ea_kj,
            reported_ea_units=ea_units_wire,
            tmin_k=float(Tmin) if Tmin is not None else None,
            tmax_k=float(Tmax) if Tmax is not None else None,
            degeneracy=float(degeneracy) if degeneracy is not None else None,
            tunneling_model=tunneling_model_clean,
            note=note_clean,
            label=label_clean,
            source_calculations=resolved_sources,
        )

    # ----- assembly helpers ----------------------------------------

    def source_calculations_iter(self) -> Iterator[tuple[str, Any]]:
        """Iterate over ``(role, Calculation)`` source-link entries."""
        yield from self.source_calculations

    def to_payload(
        self,
        *,
        reactant_keys: list[str],
        product_keys: list[str],
        calc_key_lookup,
    ) -> dict[str, Any]:
        """Render the kinetics record as a ``BundleKineticsIn`` dict.

        ``calc_key_lookup(Calculation) -> str`` is supplied by the
        upload-level key minter so the kinetics record's
        ``source_calculations`` resolve to bundle-local keys without
        leaking ``id()``-based lookups into the payload tree.
        """
        out: dict[str, Any] = {
            "reactant_keys": list(reactant_keys),
            "product_keys": list(product_keys),
            "model_kind": self.model_kind,
            "a": self.a,
            "a_units": self.a_units,
            "n": self.n,
            "reported_ea": self.reported_ea,
            "reported_ea_units": self.reported_ea_units,
        }
        if self.tmin_k is not None:
            out["tmin_k"] = self.tmin_k
        if self.tmax_k is not None:
            out["tmax_k"] = self.tmax_k
        if self.degeneracy is not None:
            out["degeneracy"] = self.degeneracy
        if self.tunneling_model is not None:
            out["tunneling_model"] = self.tunneling_model
        if self.note is not None:
            out["note"] = self.note
        if self.source_calculations:
            out["source_calculations"] = [
                {
                    "calculation_key": calc_key_lookup(calc),
                    "role": role,
                }
                for role, calc in self.source_calculations
            ]
        return out
