"""Read schemas for server-side k(T,P) evaluation of one network-kinetics fit.

Covers:

- ``GET /api/v1/scientific/network-kinetics/{ref}/evaluate``

Evaluation happens server-side, not in the browser (owner ruling, see
``docs/plans/pressure-dependent-network-surface.md`` §2.4): the frontend
has no Chebyshev/PLOG math to build on, the formulas are easy to get
subtly wrong, and a wrong evaluation publishes a rate coefficient that
looks entirely plausible. The math itself lives in
``app/chemistry/network_kinetics_eval.py`` as dependency-free pure
functions; this module only shapes the request/response wire types.

See ``backend/app/chemistry/network_kinetics_eval.py`` for the formulas
and their literature references.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.db.models.common import ArrheniusAUnits, NetworkKineticsModelKind


class NetworkKineticsEvaluatedPoint(BaseModel):
    """One evaluated (T, P) -> k point.

    ``in_range`` is ``False`` (never omitted) when the requested point
    falls outside the fit's own stated ``[tmin_k, tmax_k]`` x
    ``[pmin_bar, pmax_bar]`` validity range. The value is still computed
    and served — refusing outright would make a caller unable to see
    "roughly how far off is this" — but it must never be presented, or
    mistaken for, an interpolated value.
    """

    temperature_k: float
    pressure_bar: float
    k: float
    in_range: bool


class NetworkKineticsEvaluateResponse(BaseModel):
    """Response envelope for
    ``GET /scientific/network-kinetics/{ref}/evaluate``.

    ``k_units`` is carried once at the envelope level, not per point:
    every point in one evaluate call comes from the same stored fit, so
    the unit is a property of the request, not of the individual point.
    It is always present — a fit whose stored ``rate_units`` is unset
    cannot be evaluated at all (refused with
    ``network_kinetics_rate_units_missing``) rather than served with an
    unlabeled number, per the archive's rule that a value never appears
    without the reader being able to tell what it is.
    """

    network_kinetics_ref: str
    model_kind: NetworkKineticsModelKind
    k_units: ArrheniusAUnits
    tmin_k: float | None = None
    tmax_k: float | None = None
    pmin_bar: float | None = None
    pmax_bar: float | None = None
    points: list[NetworkKineticsEvaluatedPoint] = Field(default_factory=list)


__all__ = [
    "NetworkKineticsEvaluateResponse",
    "NetworkKineticsEvaluatedPoint",
]
