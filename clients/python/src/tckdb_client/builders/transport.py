"""Transport builder for the Phase-5 upload path.

Targets the inline transport upload shape
(``TransportUploadPayload`` in
``app/schemas/workflows/transport_upload.py``). The Lennard-Jones
collision diameter and well depth are both-or-neither (the server
enforces this in ``TransportCreate.validate_lj_pair``); the builder
mirrors the same rule locally so producers see a deterministic error
before the request leaves the process.

**Bundle-payload caveat (read this).** The current backend bundle
schemas ``ComputedSpeciesUploadRequest`` and
``ComputedReactionUploadRequest`` do *not* yet carry transport
fields. Until they do, the bundle assemblers
(:class:`tckdb_client.builders.uploads.ComputedSpeciesUpload`,
:class:`tckdb_client.builders.uploads.ComputedReactionUpload`)
**accept** :class:`Transport` builders for forward compatibility and
validate them locally — but they do not emit the data on the wire.
The standalone ``/uploads/transport`` endpoint is the way to ship
transport today; ``Transport.to_payload()`` produces a dict the
inline ``TransportUploadPayload`` schema accepts.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable

from tckdb_client.builders.validation import (
    TCKDBBuilderValidationError,
    ensure_optional_non_empty_str,
)

if TYPE_CHECKING:  # pragma: no cover — type-only import
    from tckdb_client.builders.calculation import Calculation

__all__ = ["Transport"]


# Backend ``TransportCalculationRole`` enum — see
# ``app/db/models/common.py``.
_TRANSPORT_ROLE_ALIASES: dict[str, str] = {
    "full_transport": "full_transport",
    "dipole": "dipole",
    "polarizability": "polarizability",
    "supporting_geometry": "supporting_geometry",
}


def _resolve_transport_role(role: str) -> str:
    if not isinstance(role, str) or not role.strip():
        raise TCKDBBuilderValidationError(
            "Transport source_calculations keys must be non-empty strings."
        )
    resolved = _TRANSPORT_ROLE_ALIASES.get(role.strip())
    if resolved is None:
        raise TCKDBBuilderValidationError(
            f"unknown transport source-calculation role {role!r}; "
            f"supported: {sorted(_TRANSPORT_ROLE_ALIASES)}."
        )
    return resolved


def _normalise_transport_source_calculations(value: Any) -> list[tuple[str, Any]]:
    """Coerce ``source_calculations`` into ``[(role, calc), …]``.

    Same accepted shapes as kinetics / thermo / statmech.
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
            wire_role = _resolve_transport_role(role)
            if isinstance(item, _Calculation):
                pairs.append((wire_role, item))
                continue
            if isinstance(item, (list, tuple)):
                for sub in item:
                    if not isinstance(sub, _Calculation):
                        raise TCKDBBuilderValidationError(
                            f"Transport.source_calculations[{role!r}] "
                            "list entries must be Calculation builders, "
                            f"got {type(sub).__name__}."
                        )
                    pairs.append((wire_role, sub))
                continue
            raise TCKDBBuilderValidationError(
                f"Transport.source_calculations[{role!r}] must be a "
                "Calculation builder or a list of them, got "
                f"{type(item).__name__}."
            )
        return pairs

    if isinstance(value, (list, tuple)):
        for i, entry in enumerate(value):
            if not isinstance(entry, (list, tuple)) or len(entry) != 2:
                raise TCKDBBuilderValidationError(
                    "Transport.source_calculations list entries must be "
                    f"(role, Calculation) 2-tuples; entry {i} is "
                    f"{type(entry).__name__}."
                )
            role, calc = entry
            wire_role = _resolve_transport_role(role)
            if not isinstance(calc, _Calculation):
                raise TCKDBBuilderValidationError(
                    f"Transport.source_calculations[{i}] expected a "
                    f"Calculation, got {type(calc).__name__}."
                )
            pairs.append((wire_role, calc))
        return pairs

    raise TCKDBBuilderValidationError(
        "Transport.source_calculations must be a dict, list of "
        f"(role, Calculation) tuples, or None; got {type(value).__name__}."
    )


def _check_optional_positive(name: str, value: float | None) -> float | None:
    """Validate an optional strictly-positive numeric field."""
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TCKDBBuilderValidationError(
            f"Transport.{name} must be numeric, got {type(value).__name__}."
        )
    if value <= 0:
        raise TCKDBBuilderValidationError(
            f"Transport.{name} must be > 0, got {value!r}."
        )
    return float(value)


def _check_optional_numeric(name: str, value: float | None) -> float | None:
    """Validate an optional plain-numeric field (sign / magnitude free)."""
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TCKDBBuilderValidationError(
            f"Transport.{name} must be numeric, got {type(value).__name__}."
        )
    return float(value)


def _check_optional_non_negative(name: str, value: float | None) -> float | None:
    """Validate an optional non-negative numeric field."""
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TCKDBBuilderValidationError(
            f"Transport.{name} must be numeric, got {type(value).__name__}."
        )
    if value < 0:
        raise TCKDBBuilderValidationError(
            f"Transport.{name} must be >= 0, got {value!r}."
        )
    return float(value)


@dataclass(eq=False)
class Transport:
    """One transport-properties block for a species.

    Mirrors the inline shape from ``TransportUploadPayload`` minus the
    provenance-ref fields (``literature``, ``software_release``,
    ``workflow_tool_release``) — those are available via the raw
    payload form for producers that need them.

    Construction enforces:

    - at least one of the five transport values is supplied
    - LJ pair (``sigma_angstrom``, ``epsilon_over_k_k``) is both or
      neither, matching the server's ``validate_lj_pair`` rule
    - ``sigma_angstrom`` / ``epsilon_over_k_k`` > 0 when present
    - ``rotational_relaxation`` >= 0 when present
    - ``dipole_debye`` / ``polarizability_angstrom3`` are numeric
    - ``source_calculations`` role tokens are in the backend
      ``TransportCalculationRole`` enum

    ``source_calculations`` are emitted on the wire only when the
    caller opts in via ``allow_source_calculations=True`` and supplies
    a ``calc_key_lookup``. The Phase-5 bundle assemblers do *not* opt
    in — see the module docstring for the schema-gap rationale.
    """

    sigma_angstrom: float | None = None
    epsilon_over_k_k: float | None = None
    dipole_debye: float | None = None
    polarizability_angstrom3: float | None = None
    rotational_relaxation: float | None = None
    source_calculations: Any = None
    note: str | None = None
    label: str | None = None

    def __post_init__(self) -> None:
        self.sigma_angstrom = _check_optional_positive(
            "sigma_angstrom", self.sigma_angstrom
        )
        self.epsilon_over_k_k = _check_optional_positive(
            "epsilon_over_k_k", self.epsilon_over_k_k
        )
        self.dipole_debye = _check_optional_numeric(
            "dipole_debye", self.dipole_debye
        )
        self.polarizability_angstrom3 = _check_optional_numeric(
            "polarizability_angstrom3", self.polarizability_angstrom3
        )
        self.rotational_relaxation = _check_optional_non_negative(
            "rotational_relaxation", self.rotational_relaxation
        )

        # LJ pair both-or-neither (mirrors server's
        # ``TransportCreate.validate_lj_pair``).
        if (self.sigma_angstrom is None) != (self.epsilon_over_k_k is None):
            raise TCKDBBuilderValidationError(
                "Transport.sigma_angstrom and Transport.epsilon_over_k_k "
                "must be provided together or both omitted."
            )

        if not any(
            v is not None
            for v in (
                self.sigma_angstrom,
                self.epsilon_over_k_k,
                self.dipole_debye,
                self.polarizability_angstrom3,
                self.rotational_relaxation,
            )
        ):
            raise TCKDBBuilderValidationError(
                "Transport requires at least one of sigma_angstrom + "
                "epsilon_over_k_k, dipole_debye, polarizability_angstrom3, "
                "or rotational_relaxation."
            )

        self.source_calculations = _normalise_transport_source_calculations(
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
        allow_source_calculations: bool = False,
        calc_key_lookup: Callable[[Any], str] | None = None,
    ) -> dict[str, Any]:
        """Render the dict accepted by ``TransportUploadPayload``.

        Both upload-flag and lookup-callable follow the same pattern as
        :meth:`tckdb_client.builders.thermo.Thermo.to_payload`. The
        Phase-5 bundle assemblers call this with the default
        ``allow_source_calculations=False`` because neither bundle
        schema carries the ``source_calculations`` field today (and
        neither carries the top-level ``transport`` field at all —
        the bundle assemblers don't call this method).

        When ``allow_source_calculations=True`` is opted in by a future
        caller (typically a primitive-endpoint helper), the on-wire
        ``source_calculations`` entries' ``calculation_key`` values
        come from the supplied lookup.
        """
        out: dict[str, Any] = {}
        if self.sigma_angstrom is not None:
            out["sigma_angstrom"] = self.sigma_angstrom
        if self.epsilon_over_k_k is not None:
            out["epsilon_over_k_k"] = self.epsilon_over_k_k
        if self.dipole_debye is not None:
            out["dipole_debye"] = self.dipole_debye
        if self.polarizability_angstrom3 is not None:
            out["polarizability_angstrom3"] = self.polarizability_angstrom3
        if self.rotational_relaxation is not None:
            out["rotational_relaxation"] = self.rotational_relaxation
        if self.note is not None:
            out["note"] = self.note
        if allow_source_calculations and self.source_calculations:
            if calc_key_lookup is None:
                raise TCKDBBuilderValidationError(
                    "Transport.to_payload(allow_source_calculations=True) "
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
