"""Thermo builder for the computed-reaction (Phase 3B) upload path.

Targets ``BundleThermoIn`` in ``app/schemas/workflows/computed_reaction_upload.py``.
That wire shape supports three representations — scalar (h298 / s298 at
optional temperature bounds), NASA polynomial coefficients, and
tabulated points — surfaced here as three factories:

- :meth:`Thermo.scalar`
- :meth:`Thermo.nasa`
- :meth:`Thermo.points`

``source_calculations`` is accepted on every factory for forward
compatibility with future thermo endpoints that carry calc provenance
(see ``ThermoInBundle`` in
``app/schemas/workflows/computed_species_upload.py``). The
computed-reaction ``BundleThermoIn`` does **not** have a
``source_calculations`` field today, so
:class:`tckdb_client.builders.uploads.ComputedReactionUpload` rejects
non-empty values rather than silently dropping them.
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

__all__ = ["Thermo"]


# Backend ``ThermoCalculationRole`` enum. The builder accepts the
# same string tokens the server already accepts — there are no
# user-facing aliases to normalise for thermo (unlike kinetics' A
# units), so the alias map is identity-only and exists for symmetry
# with ``kinetics._resolve_source_role``.
_THERMO_ROLE_ALIASES: dict[str, str] = {
    "opt": "opt",
    "freq": "freq",
    "sp": "sp",
    "composite": "composite",
    "imported": "imported",
}

# NASA polynomials always carry seven coefficients per temperature range
# (``a1..a7`` low, ``b1..b7`` high) — see ``ThermoNASABase`` in
# ``app/schemas/entities/thermo.py``.
_NASA_COEFF_COUNT = 7


def _resolve_thermo_role(role: str) -> str:
    if not isinstance(role, str) or not role.strip():
        raise TCKDBBuilderValidationError(
            "Thermo source_calculations keys must be non-empty strings."
        )
    resolved = _THERMO_ROLE_ALIASES.get(role.strip())
    if resolved is None:
        raise TCKDBBuilderValidationError(
            f"unknown thermo source-calculation role {role!r}; "
            f"supported: {sorted(_THERMO_ROLE_ALIASES)}."
        )
    return resolved


def _normalise_thermo_source_calculations(value: Any) -> list[tuple[str, Any]]:
    """Coerce the user's ``source_calculations`` value into ``[(role, calc), …]``.

    Mirrors :func:`tckdb_client.builders.kinetics._normalise_source_calculations`
    — accepts ``dict[str, Calculation]``, ``dict[str, list[Calculation]]``,
    or ``list[(role, Calculation)]``. Kept as a separate helper so the
    thermo role vocabulary (which differs from kinetics') stays
    co-located with this module.
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
            wire_role = _resolve_thermo_role(role)
            if isinstance(item, _Calculation):
                pairs.append((wire_role, item))
                continue
            if isinstance(item, (list, tuple)):
                for sub in item:
                    if not isinstance(sub, _Calculation):
                        raise TCKDBBuilderValidationError(
                            f"Thermo.source_calculations[{role!r}] list "
                            "entries must be Calculation builders, got "
                            f"{type(sub).__name__}."
                        )
                    pairs.append((wire_role, sub))
                continue
            raise TCKDBBuilderValidationError(
                f"Thermo.source_calculations[{role!r}] must be a "
                "Calculation builder or a list of them, got "
                f"{type(item).__name__}."
            )
        return pairs

    if isinstance(value, (list, tuple)):
        for i, entry in enumerate(value):
            if not isinstance(entry, (list, tuple)) or len(entry) != 2:
                raise TCKDBBuilderValidationError(
                    "Thermo.source_calculations list entries must be "
                    f"(role, Calculation) 2-tuples; entry {i} is "
                    f"{type(entry).__name__}."
                )
            role, calc = entry
            wire_role = _resolve_thermo_role(role)
            if not isinstance(calc, _Calculation):
                raise TCKDBBuilderValidationError(
                    f"Thermo.source_calculations[{i}] expected a "
                    f"Calculation, got {type(calc).__name__}."
                )
            pairs.append((wire_role, calc))
        return pairs

    raise TCKDBBuilderValidationError(
        "Thermo.source_calculations must be a dict, list of "
        f"(role, Calculation) tuples, or None; got {type(value).__name__}."
    )


def _check_optional_temperature(name: str, value: float | None) -> float | None:
    """Validate an optional temperature scalar (``None`` or ``> 0``)."""
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TCKDBBuilderValidationError(
            f"Thermo.{name} must be numeric, got {type(value).__name__}."
        )
    if value <= 0:
        raise TCKDBBuilderValidationError(
            f"Thermo.{name} must be > 0, got {value!r}."
        )
    return float(value)


def _check_optional_scalar(name: str, value: float | None) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TCKDBBuilderValidationError(
            f"Thermo.{name} must be numeric, got {type(value).__name__}."
        )
    return float(value)


@dataclass(eq=False)
class Thermo:
    """One thermo block attached to a species.

    Construct via :meth:`scalar`, :meth:`nasa`, or :meth:`points`. The
    bare constructor is reserved for internal use and tests. Multiple
    representations may coexist on a single ``Thermo`` (a NASA fit can
    carry h298 / s298 from the same data, for instance) — the backend's
    ``BundleThermoIn`` simply emits whichever fields are present.

    ``source_calculations`` is held on the builder for forward
    compatibility; the computed-reaction endpoint does not consume
    it today (see module docstring).
    """

    h298_kj_mol: float | None = None
    s298_j_mol_k: float | None = None
    tmin_k: float | None = None
    tmax_k: float | None = None
    # Internal field names intentionally differ from the wire-side
    # ``nasa`` / ``points`` because the public class also exposes
    # factories named :meth:`nasa` and :meth:`points`. A dataclass
    # field with the same name as a classmethod gets silently shadowed
    # by the descriptor — leaving instances reading ``self.points`` as
    # a bound method. The wire emission in :meth:`to_payload` still
    # writes the backend's ``nasa`` / ``points`` keys.
    nasa_block: dict[str, Any] | None = None
    point_table: list[dict[str, Any]] = field(default_factory=list)
    note: str | None = None
    label: str | None = None
    source_calculations: list[tuple[str, Any]] = field(default_factory=list)

    # Tag for diagnostics / error messages; not emitted on the wire.
    _kind: str = field(default="generic", init=False, repr=False)

    @property
    def kind(self) -> str:
        """Return the thermo representation kind.

        One of ``"scalar"`` / ``"nasa"`` / ``"points"`` for instances
        built through the matching factory; ``"generic"`` for the bare
        constructor. The viewer surfaces consume this for previews
        (see :class:`UploadSummary`); the wire shape is unaffected.
        """
        return self._kind

    def __post_init__(self) -> None:
        # The factories do the upfront validation; this default path
        # still sanitises optional scalars so the bare constructor is
        # not a backdoor.
        self.h298_kj_mol = _check_optional_scalar("h298_kj_mol", self.h298_kj_mol)
        self.s298_j_mol_k = _check_optional_scalar(
            "s298_j_mol_k", self.s298_j_mol_k
        )
        self.tmin_k = _check_optional_temperature("tmin_k", self.tmin_k)
        self.tmax_k = _check_optional_temperature("tmax_k", self.tmax_k)
        if (
            self.tmin_k is not None
            and self.tmax_k is not None
            and self.tmin_k > self.tmax_k
        ):
            raise TCKDBBuilderValidationError(
                f"Thermo: tmin_k ({self.tmin_k}) must be <= "
                f"tmax_k ({self.tmax_k})."
            )
        self.note = ensure_optional_non_empty_str(self.note, field="note") \
            if self.note is not None else None
        # Allow empty strings for ``note`` to round-trip cleanly, but
        # whitespace-only is treated as no note.
        if self.label is not None:
            self.label = ensure_optional_non_empty_str(self.label, field="label")

    # ----- factories ------------------------------------------------

    @classmethod
    def scalar(
        cls,
        *,
        h298_kj_mol: float | None = None,
        s298_j_mol_k: float | None = None,
        tmin_k: float | None = None,
        tmax_k: float | None = None,
        source_calculations: (
            "dict[str, Calculation]"
            " | dict[str, list[Calculation]]"
            " | list[tuple[str, Calculation]]"
            " | None"
        ) = None,
        label: str | None = None,
        note: str | None = None,
    ) -> "Thermo":
        """Scalar h298 / s298 thermo, with optional temperature bounds.

        At least one of ``h298_kj_mol`` / ``s298_j_mol_k`` must be
        supplied — empty scalar blocks would be rejected by the
        server's validators downstream (and are meaningless anyway).
        """
        if h298_kj_mol is None and s298_j_mol_k is None:
            raise TCKDBBuilderValidationError(
                "Thermo.scalar requires at least one of h298_kj_mol or "
                "s298_j_mol_k."
            )
        out = cls(
            h298_kj_mol=h298_kj_mol,
            s298_j_mol_k=s298_j_mol_k,
            tmin_k=tmin_k,
            tmax_k=tmax_k,
            label=label,
            note=note,
            source_calculations=_normalise_thermo_source_calculations(
                source_calculations
            ),
        )
        out._kind = "scalar"
        return out

    @classmethod
    def nasa(
        cls,
        *,
        coeffs_low: list[float],
        coeffs_high: list[float],
        t_low: float,
        t_mid: float,
        t_high: float,
        h298_kj_mol: float | None = None,
        s298_j_mol_k: float | None = None,
        source_calculations: (
            "dict[str, Calculation]"
            " | dict[str, list[Calculation]]"
            " | list[tuple[str, Calculation]]"
            " | None"
        ) = None,
        label: str | None = None,
        note: str | None = None,
    ) -> "Thermo":
        """NASA 7-coefficient polynomial thermo.

        ``coeffs_low`` and ``coeffs_high`` must each have exactly
        seven entries (``a1..a7`` / ``b1..b7`` in the backend wire
        shape). ``t_low < t_mid < t_high`` is enforced locally because
        the backend's ``ThermoNASABase`` validator also rejects
        out-of-order bounds.
        """
        if not isinstance(coeffs_low, (list, tuple)) or len(coeffs_low) != _NASA_COEFF_COUNT:
            raise TCKDBBuilderValidationError(
                f"Thermo.nasa coeffs_low must be a length-{_NASA_COEFF_COUNT} "
                "list of floats."
            )
        if not isinstance(coeffs_high, (list, tuple)) or len(coeffs_high) != _NASA_COEFF_COUNT:
            raise TCKDBBuilderValidationError(
                f"Thermo.nasa coeffs_high must be a length-{_NASA_COEFF_COUNT} "
                "list of floats."
            )
        cleaned_low: list[float] = []
        cleaned_high: list[float] = []
        for label_in, lst, out in (
            ("coeffs_low", coeffs_low, cleaned_low),
            ("coeffs_high", coeffs_high, cleaned_high),
        ):
            for i, c in enumerate(lst):
                if isinstance(c, bool) or not isinstance(c, (int, float)):
                    raise TCKDBBuilderValidationError(
                        f"Thermo.nasa {label_in}[{i}] must be numeric, "
                        f"got {type(c).__name__}."
                    )
                out.append(float(c))
        t_low_f = _check_optional_temperature("t_low", t_low)
        t_mid_f = _check_optional_temperature("t_mid", t_mid)
        t_high_f = _check_optional_temperature("t_high", t_high)
        assert t_low_f is not None and t_mid_f is not None and t_high_f is not None
        if not (t_low_f < t_mid_f < t_high_f):
            raise TCKDBBuilderValidationError(
                "Thermo.nasa requires t_low < t_mid < t_high, got "
                f"({t_low_f}, {t_mid_f}, {t_high_f})."
            )

        nasa_dict: dict[str, Any] = {
            "t_low": t_low_f,
            "t_mid": t_mid_f,
            "t_high": t_high_f,
        }
        for i, value in enumerate(cleaned_low, start=1):
            nasa_dict[f"a{i}"] = value
        for i, value in enumerate(cleaned_high, start=1):
            nasa_dict[f"b{i}"] = value

        out = cls(
            h298_kj_mol=h298_kj_mol,
            s298_j_mol_k=s298_j_mol_k,
            tmin_k=t_low_f,
            tmax_k=t_high_f,
            nasa_block=nasa_dict,
            label=label,
            note=note,
            source_calculations=_normalise_thermo_source_calculations(
                source_calculations
            ),
        )
        out._kind = "nasa"
        return out

    @classmethod
    def points(
        cls,
        points: list[dict[str, Any]],
        *,
        tmin_k: float | None = None,
        tmax_k: float | None = None,
        h298_kj_mol: float | None = None,
        s298_j_mol_k: float | None = None,
        source_calculations: (
            "dict[str, Calculation]"
            " | dict[str, list[Calculation]]"
            " | list[tuple[str, Calculation]]"
            " | None"
        ) = None,
        label: str | None = None,
        note: str | None = None,
    ) -> "Thermo":
        """Tabulated thermo points (``temperature_k`` + cp/h/s/g).

        Each point is a dict with at minimum ``temperature_k`` (>0).
        Optional value keys: ``cp_j_mol_k``, ``h_kj_mol``,
        ``s_j_mol_k``, ``g_kj_mol`` (see
        ``app/schemas/entities/thermo.py::ThermoPointBase``).
        """
        if not isinstance(points, (list, tuple)) or not points:
            raise TCKDBBuilderValidationError(
                "Thermo.points requires a non-empty list of point dicts."
            )
        cleaned: list[dict[str, Any]] = []
        for i, p in enumerate(points):
            if not isinstance(p, dict):
                raise TCKDBBuilderValidationError(
                    f"Thermo.points[{i}] must be a dict, got "
                    f"{type(p).__name__}."
                )
            t = p.get("temperature_k")
            if t is None:
                raise TCKDBBuilderValidationError(
                    f"Thermo.points[{i}] missing required temperature_k."
                )
            t_f = _check_optional_temperature(
                f"points[{i}].temperature_k", t
            )
            assert t_f is not None
            entry: dict[str, Any] = {"temperature_k": t_f}
            for key in ("cp_j_mol_k", "h_kj_mol", "s_j_mol_k", "g_kj_mol"):
                if key in p and p[key] is not None:
                    val = p[key]
                    if isinstance(val, bool) or not isinstance(val, (int, float)):
                        raise TCKDBBuilderValidationError(
                            f"Thermo.points[{i}].{key} must be numeric, "
                            f"got {type(val).__name__}."
                        )
                    entry[key] = float(val)
            cleaned.append(entry)

        out = cls(
            h298_kj_mol=h298_kj_mol,
            s298_j_mol_k=s298_j_mol_k,
            tmin_k=tmin_k,
            tmax_k=tmax_k,
            point_table=cleaned,
            label=label,
            note=note,
            source_calculations=_normalise_thermo_source_calculations(
                source_calculations
            ),
        )
        out._kind = "points"
        return out

    # ----- assembly helpers ----------------------------------------

    def source_calculations_iter(self):
        """Iterate over ``(role, Calculation)`` source-link entries."""
        yield from self.source_calculations

    def to_payload(
        self,
        *,
        allow_source_calculations: bool = False,
        calc_key_lookup: Callable[[Any], str] | None = None,
    ) -> dict[str, Any]:
        """Render the dict accepted by ``BundleThermoIn`` / ``ThermoInBundle``.

        Two switches gate the most variable part of the wire shape:

        - ``allow_source_calculations`` — emit the
          ``source_calculations`` field when ``True``. The
          computed-reaction ``BundleThermoIn`` schema does not carry
          this field; the computed-species ``ThermoInBundle`` does.
          The upload-level assembler passes the right flag for its
          endpoint.
        - ``calc_key_lookup`` — a callable that resolves a
          :class:`Calculation` builder to its bundle-local key. Required
          whenever ``allow_source_calculations`` is True and the
          builder has registered source calculations; the assembler
          forwards its :class:`KeyMinter` lookup here so the on-wire
          ``calculation_key`` values resolve into the bundle's global
          calc namespace without any ``id()`` use.
        """
        out: dict[str, Any] = {}
        if self.h298_kj_mol is not None:
            out["h298_kj_mol"] = self.h298_kj_mol
        if self.s298_j_mol_k is not None:
            out["s298_j_mol_k"] = self.s298_j_mol_k
        if self.tmin_k is not None:
            out["tmin_k"] = self.tmin_k
        if self.tmax_k is not None:
            out["tmax_k"] = self.tmax_k
        if self.nasa_block is not None:
            out["nasa"] = dict(self.nasa_block)
        if self.point_table:
            out["points"] = [dict(p) for p in self.point_table]
        if self.note is not None:
            out["note"] = self.note
        if allow_source_calculations and self.source_calculations:
            if calc_key_lookup is None:
                raise TCKDBBuilderValidationError(
                    "Thermo.to_payload(allow_source_calculations=True) "
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
