"""Service implementation for
``POST /api/v1/scientific/networks/{ref}/kinetics/evaluate``.

One surface here: network-scoped batch k(T,P) evaluation. PR 1 of
``docs/plans/pressure-dependent-network-surface.md`` shipped per-record
evaluation only (``GET /scientific/network-kinetics/{ref}/evaluate``,
``app/services/scientific_read/network_kinetics.py``); the k(T,P) chart
(PR 4) needs one chart for a whole network, and the plan requires one
request per page load. With 42 stored fits on the live network (21
channels, every channel carrying both a Chebyshev and a PLOG fit), the
per-record endpoint would mean 42 requests. This module is that batch
endpoint's service layer.

**No new evaluation math.** Every point is produced by calling
:func:`app.services.scientific_read.network_kinetics.evaluate_network_kinetics`
once per stored fit — the exact function the per-record endpoint calls,
inheriting its rate-units guard, its Chebyshev/PLOG dispatch, its
``DataIntegrityError`` stance on a fit that claims data it does not store,
and (deliberately, see :data:`BATCH_EVALUATE_POINT_CAP` below) its
per-fit grid-size cap. This module only resolves "every fit belonging to
this network," bounds the aggregate response size, and assembles the
per-fit channel/solve context a client needs to group and label the
results honestly.

**Keyed by ``network_kinetics_ref``, not by channel.** See
``scientific_network_kinetics_batch_evaluate.py`` for why: a channel
routinely carries more than one fit (Chebyshev + PLOG), and they are
never collapsed into one value.
"""

from __future__ import annotations

import logging
import math

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.config import settings
from app.api.error_contract import CodedValueError
from app.api.errors import DataIntegrityError, not_found
from app.db.models.network import Network
from app.db.models.network_pdep import NetworkChannel, NetworkKinetics, NetworkSolve, NetworkState
from app.schemas.reads.scientific_network_kinetics_batch_evaluate import (
    NetworkKineticsBatchEvaluateResponse,
    NetworkKineticsBatchFitEvaluation,
)
from app.services.scientific_read.handles import resolve_network_handle
from app.services.scientific_read.network_kinetics import (
    EVALUATE_GRID_POINT_CAP,
    evaluate_network_kinetics,
)

logger = logging.getLogger(__name__)


#: Aggregate cap on ``fit_count * len(temperature_k) * len(pressure_bar)``
#: for one batch call. Deliberately *not* a fresh magic number: it is
#: ``EVALUATE_GRID_POINT_CAP`` (the per-record endpoint's own cap on one
#: fit's grid, inherited unchanged below -- see the per-fit check in
#: :func:`evaluate_network_kinetics_batch`) multiplied by
#: ``settings.public_max_limit`` again, this time standing in for "how
#: many fits may one call return," matching the ceiling every search
#: endpoint on this read surface already uses for "how many rows may one
#: list response return." A response shaped like "public_max_limit fits,
#: each at its own public_max_limit-point grid" is the largest this
#: endpoint will serve without a 422 -- 40,000 points today -- against a
#: real network's 42 fits this leaves ample headroom (e.g. a 50-temperature
#: x 4-pressure grid across all 42 fits is 8,400 points).
BATCH_EVALUATE_POINT_CAP = EVALUATE_GRID_POINT_CAP * int(settings.public_max_limit)


def evaluate_network_kinetics_batch(
    session: Session,
    *,
    network_handle: str,
    temperatures_k: list[float],
    pressures_bar: list[float],
) -> NetworkKineticsBatchEvaluateResponse:
    """Evaluate every stored k(T,P) fit belonging to one network.

    Path-handle semantics match every other detail surface on this
    router (integer id / ``net_…`` public ref / 404 / handle-shape 422).

    Both axes are required and validated with the exact codes the
    per-record endpoint uses for the identical checks
    (``network_kinetics_evaluate_missing_temperature`` /
    ``..._missing_pressure`` / ``..._invalid_point``) -- reused rather
    than reinvented because the validation is the same rule applied to
    the same shape of input, just shared across more than one fit.

    Two caps, checked in this order:

    1. **Per-fit grid cap** -- ``len(temperatures_k) * len(pressures_bar)``
       against :data:`EVALUATE_GRID_POINT_CAP`, the exact cap and the exact
       code (``network_kinetics_evaluate_grid_too_large``) the per-record
       endpoint enforces for one fit's own grid. Checked here too, up
       front, so a caller seeing it gets a message about *this* request
       rather than a per-record-flavoured 422 surfacing from partway
       through the per-fit loop below on whichever fit happens to be
       evaluated first.
    2. **Aggregate cap** -- ``fit_count * grid_size`` against
       :data:`BATCH_EVALUATE_POINT_CAP`, a new code
       (``network_kinetics_batch_evaluate_grid_too_large``) for the
       failure mode the per-record endpoint has no analogue for: a grid
       that is individually fine but, multiplied across every fit the
       network has, would not be. This endpoint evaluates every stored
       fit unconditionally -- there is no channel filter to shrink
       ``fit_count`` -- so the message tells the caller to shrink the
       grid instead.

    Refused, never truncated, in both cases: a chart silently missing
    channels because their fits were dropped past a cap would misrepresent
    the network's evaluated coverage as complete.

    A point outside a fit's own stated T/P validity range is still
    evaluated (mathematically well-defined for both supported forms) but
    flagged ``in_range=False`` -- the identical per-record semantics,
    inherited by calling that endpoint's own service function once per
    fit rather than re-implementing the flag.

    :raises CodedValueError: malformed request (empty axis, either cap
        exceeded, a non-finite/non-positive T or P value), or -- raised
        from inside the per-fit call -- a fit that cannot be evaluated as
        stored (missing rate units, an unsupported ``model_kind``).
    :raises DataIntegrityError: a stored ``network_kinetics`` row
        references a missing parent channel or solve, or (raised from
        inside the per-fit call) a ``model_kind`` that promises data not
        actually stored -- a stored-data invariant violation, not a
        client mistake. A batch call fails atomically on this: surfacing
        a corrupt row loudly during a chart load is the honest response,
        not silently thinning the network's rendered fit set, which
        would misrepresent it as complete.
    """
    if not temperatures_k:
        raise CodedValueError(
            "network_kinetics_evaluate_missing_temperature",
            "at least one temperature_k value is required to evaluate "
            "POST /scientific/networks/{ref}/kinetics/evaluate.",
        )
    if not pressures_bar:
        raise CodedValueError(
            "network_kinetics_evaluate_missing_pressure",
            "at least one pressure_bar value is required to evaluate "
            "POST /scientific/networks/{ref}/kinetics/evaluate.",
        )
    # Same screen as the per-record endpoint, and for the same reason:
    # ``t > 0`` alone is True for ``+inf``, which would otherwise sail
    # through to the Chebyshev evaluator inside the per-fit loop below.
    bad_temperatures = [
        t for t in temperatures_k if not (math.isfinite(t) and t > 0)
    ]
    bad_pressures = [
        p for p in pressures_bar if not (math.isfinite(p) and p > 0)
    ]
    if bad_temperatures or bad_pressures:
        raise CodedValueError(
            "network_kinetics_evaluate_invalid_point",
            "temperature_k and pressure_bar must be finite and strictly "
            f"positive; got invalid temperature_k={bad_temperatures!r}, "
            f"invalid pressure_bar={bad_pressures!r}.",
        )

    grid_size = len(temperatures_k) * len(pressures_bar)
    if grid_size > EVALUATE_GRID_POINT_CAP:
        raise CodedValueError(
            "network_kinetics_evaluate_grid_too_large",
            "every fit evaluated by a batch call shares one "
            f"(temperature_k x pressure_bar) grid; the requested grid has "
            f"{grid_size} points ({len(temperatures_k)} temperature(s) x "
            f"{len(pressures_bar)} pressure(s)), exceeding the per-fit cap "
            f"of {EVALUATE_GRID_POINT_CAP} shared with "
            "GET /scientific/network-kinetics/{ref}/evaluate. Request "
            "fewer temperatures or pressures, or split the grid across "
            "multiple calls.",
            context={"grid_size": grid_size, "cap": EVALUATE_GRID_POINT_CAP},
        )

    network_id = resolve_network_handle(session, network_handle)
    network = session.get(Network, network_id)
    if network is None:  # pragma: no cover — defended by resolver 404
        raise not_found("network", row_id=network_id, code="handle_not_found")

    solve_ids = session.scalars(
        select(NetworkSolve.id).where(NetworkSolve.network_id == network_id)
    ).all()
    if not solve_ids:
        return NetworkKineticsBatchEvaluateResponse(
            network_ref=network.public_ref, fits=[]
        )

    nk_rows = session.execute(
        select(
            NetworkKinetics.id,
            NetworkKinetics.public_ref,
            NetworkKinetics.channel_id,
            NetworkKinetics.solve_id,
        )
        .where(NetworkKinetics.solve_id.in_(solve_ids))
        .order_by(NetworkKinetics.id)
    ).all()
    if not nk_rows:
        return NetworkKineticsBatchEvaluateResponse(
            network_ref=network.public_ref, fits=[]
        )

    fit_count = len(nk_rows)
    total_points = fit_count * grid_size
    if total_points > BATCH_EVALUATE_POINT_CAP:
        raise CodedValueError(
            "network_kinetics_batch_evaluate_grid_too_large",
            f"this network has {fit_count} stored k(T,P) fit(s); "
            f"evaluating all of them at the requested {grid_size}-point "
            f"grid would produce {total_points} points, exceeding the "
            f"batch cap of {BATCH_EVALUATE_POINT_CAP}. This endpoint "
            "evaluates every stored fit -- there is no channel filter to "
            "narrow the fit count -- so request fewer temperatures or "
            "pressures instead.",
            context={
                "fit_count": fit_count,
                "grid_size": grid_size,
                "total_points": total_points,
                "cap": BATCH_EVALUATE_POINT_CAP,
            },
        )

    channel_ids = {row.channel_id for row in nk_rows}
    solve_id_set = {row.solve_id for row in nk_rows}
    channels_by_id = {
        c.id: c
        for c in session.scalars(
            select(NetworkChannel).where(NetworkChannel.id.in_(channel_ids))
        ).all()
    }
    solves_by_id = {
        s.id: s
        for s in session.scalars(
            select(NetworkSolve).where(NetworkSolve.id.in_(solve_id_set))
        ).all()
    }
    state_ids = {
        state_id
        for channel in channels_by_id.values()
        for state_id in (channel.source_state_id, channel.sink_state_id)
    }
    composition_hash_by_state_id: dict[int, str] = (
        dict(
            session.execute(
                select(NetworkState.id, NetworkState.composition_hash).where(
                    NetworkState.id.in_(state_ids)
                )
            ).all()
        )
        if state_ids
        else {}
    )

    fits: list[NetworkKineticsBatchFitEvaluation] = []
    for row in nk_rows:
        channel = channels_by_id.get(row.channel_id)
        solve = solves_by_id.get(row.solve_id)
        if channel is None or solve is None:  # pragma: no cover — NOT NULL, FK-constrained
            # ``network_kinetics.channel_id`` / ``.solve_id`` are NOT NULL
            # and FK-constrained, so a missing parent is referential
            # corruption -- the same guard, for the same reason, as
            # ``build_network_kinetics_record`` in network_kinetics.py.
            logger.error(
                "network_kinetics references a missing channel/solve "
                "(network_kinetics.id=%s, channel_id=%s, solve_id=%s)",
                row.id,
                row.channel_id,
                row.solve_id,
            )
            raise DataIntegrityError(
                "a network_kinetics row references a missing parent "
                f"channel or solve (network_kinetics_ref={row.public_ref!r})"
            )

        result = evaluate_network_kinetics(
            session,
            network_kinetics_handle=row.public_ref,
            temperatures_k=temperatures_k,
            pressures_bar=pressures_bar,
        )
        fits.append(
            NetworkKineticsBatchFitEvaluation(
                network_kinetics_ref=result.network_kinetics_ref,
                channel_key=channel.channel_key,
                channel_kind=channel.kind,
                source_state_composition_hash=composition_hash_by_state_id.get(
                    channel.source_state_id, ""
                ),
                sink_state_composition_hash=composition_hash_by_state_id.get(
                    channel.sink_state_id, ""
                ),
                network_solve_ref=solve.public_ref,
                model_kind=result.model_kind,
                k_units=result.k_units,
                tmin_k=result.tmin_k,
                tmax_k=result.tmax_k,
                pmin_bar=result.pmin_bar,
                pmax_bar=result.pmax_bar,
                points=result.points,
            )
        )

    return NetworkKineticsBatchEvaluateResponse(
        network_ref=network.public_ref, fits=fits
    )


__all__ = ["BATCH_EVALUATE_POINT_CAP", "evaluate_network_kinetics_batch"]
