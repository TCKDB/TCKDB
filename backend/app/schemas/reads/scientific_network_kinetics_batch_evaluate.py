"""Read schemas for network-scoped batch k(T,P) evaluation.

Covers:

- ``POST /api/v1/scientific/networks/{ref}/kinetics/evaluate``

The per-record surface (``GET /scientific/network-kinetics/{ref}/evaluate``,
see ``scientific_network_kinetics_evaluate.py``) evaluates one stored fit.
This surface evaluates *every* stored ``network_kinetics`` fit belonging to
one network at a shared ``(temperature_k, pressure_bar)`` grid, in one call
— the batch variant §2.4/§4 A of
``docs/plans/pressure-dependent-network-surface.md`` calls for, so a
k(T,P) chart covering a whole network needs one request instead of one per
fit (42 on the live hydrazine network: 21 channels, each carrying both a
Chebyshev and a PLOG fit).

**Keyed by ``network_kinetics_ref``, not by channel.** A channel can carry
more than one fit (both a Chebyshev and a PLOG parameterization on the same
channel, which is the norm on the live archive, not an edge case), and the
two are never picked, preferred, averaged, or otherwise collapsed — they can
disagree materially (measured ~1.6x-2.9x apart on one live channel), and
presenting one as *the* rate for that channel would be a false scientific
claim. Every stored fit is served; the client decides how to group and
render the (possibly two) fits sharing a channel.

Evaluation itself is unchanged: the same server-side Chebyshev/PLOG math in
``app/chemistry/network_kinetics_eval.py``, the same ``in_range`` semantics
(a point outside a fit's own validity range is still evaluated and flagged,
never dropped and never a hard error — including PLOG's pressure axis being
judged against its own fitted-pressure table rather than the envelope's
``pmin_bar``/``pmax_bar``). See that module and
``scientific_network_kinetics_evaluate.py`` for the full reasoning; this
module only shapes the network-scoped request/response wire types.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.db.models.common import ArrheniusAUnits, NetworkChannelKind, NetworkKineticsModelKind
from app.schemas.reads.scientific_network_kinetics_evaluate import (
    NetworkKineticsEvaluatedPoint,
)


class NetworkKineticsBatchEvaluateRequest(BaseModel):
    """Request body for ``POST /scientific/networks/{ref}/kinetics/evaluate``.

    Both axes are required. Non-emptiness is enforced service-side, not
    here with a Pydantic ``min_length`` — deliberately, so an empty list
    is still a *valid* body that reaches
    :func:`app.services.scientific_read.network_kinetics_batch_evaluate.evaluate_network_kinetics_batch`
    and is refused there with the same coded 422s the per-record endpoint
    uses for the identical check
    (``network_kinetics_evaluate_missing_temperature`` /
    ``..._missing_pressure``), rather than with FastAPI's generic
    ``request_validation_error``. A caller who wants a result must supply
    both non-empty; this one shared grid is evaluated against *every* fit
    stored for the network — there is no per-fit or per-channel override,
    since the whole point of the batch endpoint is one grid, one request,
    every fit.
    """

    temperature_k: list[float] = Field(
        ...,
        description="One or more temperatures, K. Shared by every fit.",
    )
    pressure_bar: list[float] = Field(
        ...,
        description="One or more pressures, bar. Shared by every fit.",
    )


class NetworkKineticsBatchFitEvaluation(BaseModel):
    """One evaluated stored fit within a batch response.

    ``channel_key`` is depositor-supplied free text (``NetworkChannel`` has
    no public ref — see ``docs/plans/pressure-dependent-network-surface.md``
    §1) but is the identifier already carried on the network detail
    endpoint's ``channels[].channel_key``, so it is what lets a client join
    this entry back to the channel it rendered from that earlier call.
    ``source_state_composition_hash`` / ``sink_state_composition_hash``
    are carried alongside it as the server-computed, non-arbitrary
    anchor — the same pair ``NetworkKineticsChannelContext`` and
    ``NetworkChannelSummary`` already expose — for a client that wants a
    sturdier join than depositor text.

    ``k_units`` / ``tmin_k`` / ``tmax_k`` / ``pmin_bar`` / ``pmax_bar`` are
    carried per fit, not once at the envelope level, because two fits on
    the same channel (a Chebyshev and a PLOG) are not guaranteed to share a
    validity range or even a rate-unit convention.
    """

    network_kinetics_ref: str
    channel_key: str
    channel_kind: NetworkChannelKind
    source_state_composition_hash: str
    sink_state_composition_hash: str
    network_solve_ref: str
    model_kind: NetworkKineticsModelKind
    k_units: ArrheniusAUnits
    tmin_k: float | None = None
    tmax_k: float | None = None
    pmin_bar: float | None = None
    pmax_bar: float | None = None
    points: list[NetworkKineticsEvaluatedPoint] = Field(default_factory=list)


class NetworkKineticsBatchEvaluateResponse(BaseModel):
    """Response envelope for
    ``POST /scientific/networks/{ref}/kinetics/evaluate``.

    ``fits`` is empty (never a 404) for a network that exists but has no
    stored kinetics yet — an honest "nothing to evaluate", the same
    "absent, not zero" stance the rest of this surface takes. One entry
    per stored ``network_kinetics`` row, in ascending row-id order.
    """

    network_ref: str
    fits: list[NetworkKineticsBatchFitEvaluation] = Field(default_factory=list)


__all__ = [
    "NetworkKineticsBatchEvaluateRequest",
    "NetworkKineticsBatchEvaluateResponse",
    "NetworkKineticsBatchFitEvaluation",
]
