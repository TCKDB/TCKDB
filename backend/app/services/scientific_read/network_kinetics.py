"""Service implementation for the scientific Network-Kinetics standalone detail.

One detail surface here:

- ``GET /scientific/network-kinetics/{ref_or_id}`` — one PDep kinetics record.

The network and network-solve surfaces expose ``NetworkKinetics`` rows
only as bounded embedded summaries (shape metadata only); this surface
is the place where the model-specific payloads (Chebyshev coefficient
matrix, PLOG rows, point-tabulated triples) are surfaced under
explicit include tokens. Point arrays are capped via the public-limit
setting; the payload exposes ``points_truncated`` + ``point_count_total``
so callers can detect when the cap kicked in.

See ``backend/docs/specs/scientific_network_reads.md``.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Mapping
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.config import settings
from app.api.error_contract import CodedValueError
from app.api.errors import DataIntegrityError, not_found
from app.chemistry.network_kinetics_eval import (
    EvaluatedKPoint,
    KineticsEvaluationError,
    PlogEntry,
    evaluate_chebyshev,
    evaluate_plog,
)
from app.db.models.common import (
    NetworkKineticsModelKind,
    RecordReviewStatus,
    SubmissionRecordType,
)
from app.db.models.network import Network
from app.db.models.network_pdep import (
    NetworkChannel,
    NetworkKinetics,
    NetworkKineticsChebyshev,
    NetworkKineticsPlog,
    NetworkKineticsPoint,
    NetworkSolve,
    NetworkSolveSourceCalculation,
    NetworkState,
)
from app.schemas.reads.scientific_common import (
    RecordReviewBadge,
)
from app.schemas.reads.scientific_network import (
    NetworkReviewEntry,
    NetworkSourceCalculationSummary,
    RequestEcho,
)
from app.schemas.reads.scientific_network_kinetics import (
    AvailableNetworkKineticsSections,
    NetworkKineticsChannelContext,
    NetworkKineticsChebyshevCoefficient,
    NetworkKineticsChebyshevPayload,
    NetworkKineticsCoreBlock,
    NetworkKineticsEvidenceSummary,
    NetworkKineticsNetworkContext,
    NetworkKineticsPLOGEntry,
    NetworkKineticsPointEntry,
    NetworkKineticsSolveContext,
    NetworkStateComposition,
    ScientificNetworkKineticsDetailResponse,
    ScientificNetworkKineticsRecord,
)
from app.schemas.reads.scientific_network_kinetics_evaluate import (
    NetworkKineticsEvaluatedPoint,
    NetworkKineticsEvaluateResponse,
)
from app.services.scientific_read.common import (
    fetch_review_badges,
    review_summary,
    validate_includes,
)
from app.services.scientific_read.handles import (
    resolve_network_kinetics_handle,
)
from app.services.scientific_read.internal_ids import (
    filter_internal_ids_from_resolved,
)
from app.services.scientific_read.network_channel_chemistry import (
    build_network_state_composition,
)
from app.services.scientific_read.networks import (
    _build_solve_review_history,
    _build_source_calculations,
)

logger = logging.getLogger(__name__)

_LEGAL_INCLUDE_TOKENS: set[str] = {
    "coefficients",
    "plog",
    "points",
    "source_calculations",
    "review",
    "internal_ids",
    "all",
}
# ``points`` is excluded from ``include=all`` expansion: tabulated kinetics
# can grow large and should require explicit opt-in. ``internal_ids``
# follows the standard Phase D policy.
_INTERNAL_INCLUDE_TOKENS: set[str] = {"internal_ids", "points"}


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def get_network_kinetics(
    session: Session,
    *,
    network_kinetics_handle: str,
    include: list[str] | None = None,
) -> ScientificNetworkKineticsDetailResponse:
    """Resolve a network-kinetics handle and return its scientific projection.

    Path-handle semantics match the rest of the scientific read API:

    - Integer string: SELECT by id.
    - Public ref ``nkin_…``: SELECT by ``public_ref``.
    - Wrong prefix: 422 ``handle_type_mismatch``.
    - Malformed: 422 ``invalid_handle``.
    - Missing row: 404.

    Default response carries the kinetics core block + parent network /
    solve / channel context + bounded evidence and available_sections
    summaries. Optional includes (``coefficients`` / ``plog`` /
    ``points`` / ``source_calculations`` / ``review``) expand the
    response. ``include=all`` covers ``coefficients`` / ``plog`` /
    ``source_calculations`` / ``review`` but **not** ``points`` —
    tabulated kinetics can grow large and require explicit opt-in.
    """
    includes = validate_includes(
        include or [],
        _LEGAL_INCLUDE_TOKENS,
        "/scientific/network-kinetics/{network_kinetics_ref_or_id}",
        internal_tokens=_INTERNAL_INCLUDE_TOKENS,
    )
    includes = filter_internal_ids_from_resolved(includes)

    nk_id = resolve_network_kinetics_handle(session, network_kinetics_handle)
    nk = session.get(NetworkKinetics, nk_id)
    if nk is None:  # pragma: no cover — defended by resolver 404
        raise not_found("network_kinetics", row_id=nk_id, code="handle_not_found")

    # ``NetworkKinetics`` itself is not a reviewable record type; the
    # parent solve carries the badge. Falls back to ``not_reviewed`` when
    # the solve has no review row.
    badge = _load_parent_solve_badge(session, nk.solve_id)

    record = build_network_kinetics_record(
        session, nk=nk, badge=badge, includes=includes
    )
    return ScientificNetworkKineticsDetailResponse(
        request=RequestEcho(include=sorted(includes)),
        review_summary=review_summary([badge]),
        record=record,
    )


# ---------------------------------------------------------------------------
# k(T,P) evaluation
# ---------------------------------------------------------------------------

#: Cap on ``len(temperatures_k) * len(pressures_bar)`` per request. Shares
#: ``settings.public_max_limit`` with every other bounded list surface on
#: the read API (search ``limit``, the coefficient/PLOG/point include
#: caps) rather than inventing a second knob for the same "how big can one
#: response be" question.
EVALUATE_GRID_POINT_CAP = int(settings.public_max_limit)


def evaluate_network_kinetics(
    session: Session,
    *,
    network_kinetics_handle: str,
    temperatures_k: list[float],
    pressures_bar: list[float],
) -> NetworkKineticsEvaluateResponse:
    """Evaluate one stored k(T,P) fit at a grid of (T, P) points.

    Server-side evaluation, not client-side: see
    ``app/chemistry/network_kinetics_eval.py`` for the formulas and why
    the archive keeps this math in one place rather than reimplementing
    it in every consumer.

    Path-handle semantics match every other detail surface here
    (integer id / ``nkin_…`` public ref / 404 / handle-shape 422).

    The response's ``points`` is the Cartesian product of
    ``temperatures_k`` x ``pressures_bar`` — a caller asking for a chart
    grid (several temperatures at several pressures) gets it in one
    request. Both lists must be non-empty, and the product is capped at
    :data:`EVALUATE_GRID_POINT_CAP`; refused as
    ``network_kinetics_evaluate_grid_too_large`` above that, rather than
    silently truncated, so a caller who needs more points knows to split
    the request instead of receiving a partial, unlabeled chart.

    A point outside the fit's own stated T/P validity range is still
    evaluated (mathematically, extrapolation is well-defined for both
    supported forms — see the chemistry module) but flagged
    ``in_range=False``. Never silently presented as interpolated.

    :raises CodedValueError: malformed request (empty axis, grid too
        large, a non-finite/non-positive T or P value) or a fit that
        cannot be evaluated as stored (missing rate units, an
        unsupported ``model_kind``).
    :raises DataIntegrityError: the row's ``model_kind`` promises data
        (a Chebyshev coefficient matrix / PLOG entries) that is not
        actually there — a stored-data invariant violation, not a
        client mistake.
    """
    if not temperatures_k:
        raise CodedValueError(
            "network_kinetics_evaluate_missing_temperature",
            "at least one temperature_k value is required to evaluate "
            "/scientific/network-kinetics/{ref}/evaluate.",
        )
    if not pressures_bar:
        raise CodedValueError(
            "network_kinetics_evaluate_missing_pressure",
            "at least one pressure_bar value is required to evaluate "
            "/scientific/network-kinetics/{ref}/evaluate.",
        )
    # A single point screen shared by both model kinds, so a request
    # that is invalid (non-finite, non-positive) is refused identically
    # regardless of whether the record turns out to be Chebyshev or
    # PLOG -- rather than each per-model evaluator making its own
    # (potentially inconsistent) decision about what to do with e.g.
    # ``inf``. ``t > 0`` alone is not enough: it is True for ``+inf``,
    # which would otherwise sail through to the Chebyshev evaluator (no
    # crash, just a silently-computed, effectively meaningless point)
    # while the same value is correctly refused by PLOG's Arrhenius
    # evaluator -- an asymmetry between the two model kinds this screen
    # closes by construction.
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
            "requested evaluation grid has "
            f"{grid_size} points ({len(temperatures_k)} temperature(s) x "
            f"{len(pressures_bar)} pressure(s)), exceeding the cap of "
            f"{EVALUATE_GRID_POINT_CAP}. Request fewer temperatures or "
            "pressures, or split the grid across multiple calls.",
            context={"grid_size": grid_size, "cap": EVALUATE_GRID_POINT_CAP},
        )

    nk_id = resolve_network_kinetics_handle(session, network_kinetics_handle)
    nk = session.get(NetworkKinetics, nk_id)
    if nk is None:  # pragma: no cover — defended by resolver 404
        raise not_found("network_kinetics", row_id=nk_id, code="handle_not_found")

    if nk.rate_units is None:
        logger.error(
            "network_kinetics has no rate_units; refusing to evaluate "
            "(network_kinetics.id=%s)",
            nk.id,
        )
        raise CodedValueError(
            "network_kinetics_rate_units_missing",
            "this fit's rate-coefficient units are not recorded, so its "
            "evaluated k cannot be labeled "
            f"(network_kinetics_ref={nk.public_ref!r}); refusing to serve "
            "an unlabeled rate coefficient.",
        )

    if nk.model_kind == NetworkKineticsModelKind.chebyshev:
        points = _evaluate_chebyshev_points(session, nk, temperatures_k, pressures_bar)
    elif nk.model_kind == NetworkKineticsModelKind.plog:
        points = _evaluate_plog_points(session, nk, temperatures_k, pressures_bar)
    else:
        raise CodedValueError(
            "network_kinetics_evaluate_model_kind_not_supported",
            "server-side evaluation is not implemented for model_kind "
            f"{nk.model_kind.value!r} (supported: chebyshev, plog).",
        )

    return NetworkKineticsEvaluateResponse(
        network_kinetics_ref=nk.public_ref,
        model_kind=nk.model_kind,
        k_units=nk.rate_units,
        tmin_k=nk.tmin_k,
        tmax_k=nk.tmax_k,
        pmin_bar=nk.pmin_bar,
        pmax_bar=nk.pmax_bar,
        points=points,
    )


def _as_evaluated_points(
    results: list[EvaluatedKPoint],
) -> list[NetworkKineticsEvaluatedPoint]:
    return [
        NetworkKineticsEvaluatedPoint(
            temperature_k=r.temperature_k,
            pressure_bar=r.pressure_bar,
            k=r.k,
            in_range=r.in_range,
        )
        for r in results
    ]


def _evaluate_chebyshev_points(
    session: Session,
    nk: NetworkKinetics,
    temperatures_k: list[float],
    pressures_bar: list[float],
) -> list[NetworkKineticsEvaluatedPoint]:
    cheb_row = session.execute(
        select(
            NetworkKineticsChebyshev.n_temperature,
            NetworkKineticsChebyshev.n_pressure,
            NetworkKineticsChebyshev.coefficients,
        ).where(NetworkKineticsChebyshev.network_kinetics_id == nk.id)
    ).one_or_none()
    if cheb_row is None:
        logger.error(
            "network_kinetics.model_kind == chebyshev but no "
            "network_kinetics_chebyshev row exists (network_kinetics.id=%s)",
            nk.id,
        )
        raise DataIntegrityError(
            "network_kinetics declares model_kind=chebyshev but stores no "
            f"coefficients (network_kinetics_ref={nk.public_ref}); a "
            "channel/fit with no kinetics cannot be evaluated as if it had "
            "one."
        )
    matrix = _extract_chebyshev_matrix(cheb_row.coefficients)
    if (
        matrix is None
        or nk.tmin_k is None
        or nk.tmax_k is None
        or nk.pmin_bar is None
        or nk.pmax_bar is None
    ):
        logger.error(
            "network_kinetics.model_kind == chebyshev but is missing a "
            "usable coefficient matrix or T/P bounds "
            "(network_kinetics.id=%s)",
            nk.id,
        )
        raise DataIntegrityError(
            "network_kinetics declares model_kind=chebyshev but is missing "
            "the coefficient matrix or the T/P validity bounds required to "
            f"evaluate it (network_kinetics_ref={nk.public_ref})"
        )

    try:
        results = [
            evaluate_chebyshev(
                t,
                p,
                coefficients=matrix,
                tmin_k=nk.tmin_k,
                tmax_k=nk.tmax_k,
                pmin_bar=nk.pmin_bar,
                pmax_bar=nk.pmax_bar,
                stores_log10_k=nk.stores_log10_k,
            )
            for t in temperatures_k
            for p in pressures_bar
        ]
    except KineticsEvaluationError as exc:
        raise CodedValueError(
            "network_kinetics_evaluate_invalid_point", str(exc)
        ) from exc
    return _as_evaluated_points(results)


def _evaluate_plog_points(
    session: Session,
    nk: NetworkKinetics,
    temperatures_k: list[float],
    pressures_bar: list[float],
) -> list[NetworkKineticsEvaluatedPoint]:
    rows = session.scalars(
        select(NetworkKineticsPlog)
        .where(NetworkKineticsPlog.network_kinetics_id == nk.id)
        .order_by(
            NetworkKineticsPlog.pressure_bar.asc(),
            NetworkKineticsPlog.entry_index.asc(),
        )
    ).all()
    if not rows:
        logger.error(
            "network_kinetics.model_kind == plog but no "
            "network_kinetics_plog rows exist (network_kinetics.id=%s)",
            nk.id,
        )
        raise DataIntegrityError(
            "network_kinetics declares model_kind=plog but stores no PLOG "
            f"entries (network_kinetics_ref={nk.public_ref}); a "
            "channel/fit with no kinetics cannot be evaluated as if it had "
            "one."
        )
    entries = [
        PlogEntry(
            pressure_bar=r.pressure_bar,
            a=r.a,
            n=r.n,
            ea_kj_mol=r.ea_kj_mol,
            a_units=r.a_units.value if r.a_units is not None else None,
        )
        for r in rows
    ]
    try:
        results = [
            # tmin_k/tmax_k: PLOG has no per-entry temperature bound (only
            # pressure is discretized by the table), so the temperature
            # axis of in_range is judged against the parent row's own
            # declared bounds -- the same ones Chebyshev uses. Omitting
            # these here is exactly the bug that let a PLOG record call
            # 5000 K "in range" on a table only ever fit to 2000 K.
            evaluate_plog(
                t, p, entries=entries, tmin_k=nk.tmin_k, tmax_k=nk.tmax_k
            )
            for t in temperatures_k
            for p in pressures_bar
        ]
    except KineticsEvaluationError as exc:
        raise CodedValueError(
            "network_kinetics_evaluate_invalid_point", str(exc)
        ) from exc
    return _as_evaluated_points(results)


# ---------------------------------------------------------------------------
# Shared per-record builder (reused by search)
# ---------------------------------------------------------------------------


def build_network_kinetics_record(
    session: Session,
    *,
    nk: NetworkKinetics,
    badge: RecordReviewBadge,
    includes: set[str],
) -> ScientificNetworkKineticsRecord:
    """Project one ``NetworkKinetics`` row into the public scientific
    record shape. Shared between detail and search so both produce
    byte-identical per-record payloads for the same include set.
    """
    solve = session.get(NetworkSolve, nk.solve_id)
    if solve is None:  # pragma: no cover — NOT NULL, FK-constrained
        # Not a degradation case. ``network_kinetics.solve_id`` is NOT NULL
        # with an FK, so a missing parent means referential corruption. The
        # record's ``kind`` — whether these rates were derived here or read
        # out of a paper — is unknowable in that state, and rendering it as
        # ``computed`` would publish a provenance claim nothing supports
        # (ADR 0010). Failing is the honest response, and the alarm.
        # ``DataIntegrityError`` rather than a bare exception: it is the
        # repo's registered member for "persisted data violates a scientific
        # invariant assumed by the API", so it renders a structured envelope
        # with a machine-readable code instead of an unhandled 500 that tells
        # an operator nothing. The internal id goes to the log, never to the
        # response.
        logger.error(
            "network_kinetics references a missing network_solve "
            "(network_kinetics.id=%s, solve_id=%s)",
            nk.id,
            nk.solve_id,
        )
        raise DataIntegrityError(
            "network_kinetics references a missing network_solve "
            f"(network_kinetics_ref={nk.public_ref})"
        )
    channel = session.get(NetworkChannel, nk.channel_id)
    network = session.get(Network, solve.network_id)
    return _build_network_kinetics_record(
        session,
        nk=nk,
        solve=solve,
        channel=channel,
        network=network,
        badge=badge,
        includes=includes,
    )


# ---------------------------------------------------------------------------
# Record builder
# ---------------------------------------------------------------------------


def _build_network_kinetics_record(
    session: Session,
    *,
    nk: NetworkKinetics,
    # Not ``| None``: ``network_kinetics.solve_id`` is NOT NULL and
    # FK-constrained, so a caller never legitimately has None here.
    solve: NetworkSolve,
    channel: NetworkChannel | None,
    network: Network | None,
    badge: RecordReviewBadge,
    includes: set[str],
) -> ScientificNetworkKineticsRecord:
    # ---- counts for evidence / available_sections + shape metadata --------
    cheb_row = session.execute(
        select(
            NetworkKineticsChebyshev.n_temperature,
            NetworkKineticsChebyshev.n_pressure,
            NetworkKineticsChebyshev.coefficients,
        ).where(NetworkKineticsChebyshev.network_kinetics_id == nk.id)
    ).one_or_none()
    plog_count = _count(
        session,
        NetworkKineticsPlog,
        NetworkKineticsPlog.network_kinetics_id == nk.id,
    )
    point_count = _count(
        session,
        NetworkKineticsPoint,
        NetworkKineticsPoint.network_kinetics_id == nk.id,
    )
    source_calc_count = _count(
        session,
        NetworkSolveSourceCalculation,
        NetworkSolveSourceCalculation.solve_id == solve.id,
    )

    cheb_n_t = cheb_row.n_temperature if cheb_row is not None else None
    cheb_n_p = cheb_row.n_pressure if cheb_row is not None else None
    cheb_shape = (
        f"{cheb_n_t}x{cheb_n_p}" if cheb_row is not None else None
    )

    # ---- channel context (composition hashes from child state rows) -------
    src_hash = sink_hash = ""
    cap = max(1, int(settings.public_max_limit))
    source_state = NetworkStateComposition()
    sink_state = NetworkStateComposition()
    if channel is not None:
        state_hashes = dict(
            session.execute(
                select(NetworkState.id, NetworkState.composition_hash).where(
                    NetworkState.id.in_(
                        [channel.source_state_id, channel.sink_state_id]
                    )
                )
            ).all()
        )
        src_hash = state_hashes.get(channel.source_state_id, "")
        sink_hash = state_hashes.get(channel.sink_state_id, "")
        source_state = build_network_state_composition(
            session, state_id=channel.source_state_id, cap=cap
        )
        sink_state = build_network_state_composition(
            session, state_id=channel.sink_state_id, cap=cap
        )

    # ---- evidence summary --------------------------------------------------
    cheb_coeff_count = _chebyshev_coefficient_count(cheb_row)
    evidence = NetworkKineticsEvidenceSummary(
        has_chebyshev_coefficients=cheb_row is not None,
        chebyshev_coefficient_count=cheb_coeff_count,
        has_plog_entries=plog_count > 0,
        plog_entry_count=plog_count,
        has_point_entries=point_count > 0,
        point_count=point_count,
        source_calculation_count=source_calc_count,
    )
    available = AvailableNetworkKineticsSections(
        has_coefficients=cheb_row is not None,
        has_plog=plog_count > 0,
        has_points=point_count > 0,
        has_source_calculations=source_calc_count > 0,
        # ``NetworkKinetics`` itself is not in ``SubmissionRecordType``;
        # any review history is inherited from the parent solve.
        has_review=_exists_solve_review(session, solve.id),
    )

    # ---- core block --------------------------------------------------------
    core = NetworkKineticsCoreBlock(
        network_kinetics_id=nk.id,
        network_kinetics_ref=nk.public_ref,
        model_kind=nk.model_kind,
        tmin_k=nk.tmin_k,
        tmax_k=nk.tmax_k,
        pmin_bar=nk.pmin_bar,
        pmax_bar=nk.pmax_bar,
        rate_units=nk.rate_units,
        pressure_units=nk.pressure_units,
        temperature_units=nk.temperature_units,
        stores_log10_k=nk.stores_log10_k,
        chebyshev_shape=cheb_shape,
        plog_entry_count=plog_count or None,
        point_count=point_count or None,
        note=nk.note,
        created_at=nk.created_at,
        review=badge,
    )

    # ---- parent contexts ---------------------------------------------------
    network_ctx = NetworkKineticsNetworkContext(
        network_id=network.id if network is not None else None,
        network_ref=network.public_ref if network is not None else "",
        name=network.name if network is not None else None,
        description=network.description if network is not None else None,
    )
    solve_ctx = NetworkKineticsSolveContext(
        network_solve_id=solve.id,
        network_solve_ref=solve.public_ref,
        # Says whether *these* rates were derived here or transcribed from a
        # paper, without a second request to the solve surface (ADR 0010).
        kind=solve.kind,
        me_method=solve.me_method,
    )
    channel_ctx = NetworkKineticsChannelContext(
        network_channel_id=channel.id if channel is not None else None,
        network_channel_ref=None,
        channel_kind=channel.kind if channel is not None else None,
        source_state_composition_hash=src_hash,
        sink_state_composition_hash=sink_hash,
        source_state=source_state,
        sink_state=sink_state,
    )

    # ---- conditional include blocks ---------------------------------------
    # ``public_max_limit`` is the shared cap across all bounded
    # per-kinetics payloads (coefficients / plog / points). Keeping
    # one knob makes the safety story uniform; individual cap settings
    # are not warranted until a real consumer needs different bounds.
    coefficients_block: NetworkKineticsChebyshevPayload | None = None
    if "coefficients" in includes:
        coefficients_block = _build_chebyshev_payload(cheb_row, cap=cap)

    plog_block: list[NetworkKineticsPLOGEntry] | None = None
    plog_entries_truncated: bool | None = None
    plog_entry_count_total: int | None = None
    if "plog" in includes:
        plog_block, plog_entries_truncated = _build_plog_entries(
            session, nk.id, total=plog_count, cap=cap
        )
        plog_entry_count_total = plog_count

    points_block: list[NetworkKineticsPointEntry] | None = None
    points_truncated: bool | None = None
    point_count_total: int | None = None
    if "points" in includes:
        points_block, points_truncated = _build_point_entries(
            session, nk.id, cap=cap
        )
        point_count_total = point_count

    sources_block: list[NetworkSourceCalculationSummary] | None = None
    if "source_calculations" in includes:
        sources_block = _build_source_calculations(session, [solve])

    review_block: list[NetworkReviewEntry] | None = None
    if "review" in includes:
        review_block = _build_solve_review_history(session, solve.id)

    return ScientificNetworkKineticsRecord(
        network_kinetics=core,
        network=network_ctx,
        network_solve=solve_ctx,
        network_channel=channel_ctx,
        evidence_summary=evidence,
        available_sections=available,
        coefficients=coefficients_block,
        plog=plog_block,
        plog_entry_count_total=plog_entry_count_total,
        plog_entries_truncated=plog_entries_truncated,
        points=points_block,
        point_count_total=point_count_total,
        points_truncated=points_truncated,
        source_calculations=sources_block,
        review_history=review_block,
    )


# ---------------------------------------------------------------------------
# Include block builders
# ---------------------------------------------------------------------------


# Known JSONB shape variants for the Chebyshev coefficient matrix:
# - ``{"coeffs": [[…], …]}`` — used by the test factories
# - ``{"matrix": [[…], …]}`` — used by the legacy ``/networks/...`` tests
# - ``{"coefficients": [[…], …]}`` — alternate symmetric name
_CHEBYSHEV_MATRIX_KEYS: tuple[str, ...] = ("coeffs", "matrix", "coefficients")


def _extract_chebyshev_matrix(payload: Any) -> list[list[float]] | None:
    """Pull a 2D coefficient matrix out of the ``NetworkKineticsChebyshev.coefficients``
    JSONB blob, accepting any of the documented shape variants.
    """
    if payload is None:
        return None
    if isinstance(payload, Mapping):
        for key in _CHEBYSHEV_MATRIX_KEYS:
            value = payload.get(key)
            if isinstance(value, list) and value and isinstance(value[0], list):
                return value
        # Fall back: if there's exactly one list-of-list value, use it.
        candidates = [
            v
            for v in payload.values()
            if isinstance(v, list) and v and isinstance(v[0], list)
        ]
        if len(candidates) == 1:
            return candidates[0]
        return None
    if isinstance(payload, list) and payload and isinstance(payload[0], list):
        return payload
    return None


def _chebyshev_coefficient_count(cheb_row: Any) -> int:
    if cheb_row is None:
        return 0
    matrix = _extract_chebyshev_matrix(cheb_row.coefficients)
    if matrix is None:
        return 0
    return sum(len(row) for row in matrix)


def _build_chebyshev_payload(
    cheb_row: Any, *, cap: int
) -> NetworkKineticsChebyshevPayload | None:
    """Project the Chebyshev JSONB row into the bounded coefficient shape.

    Returns ``None`` for non-Chebyshev kinetics so callers can
    distinguish "kind doesn't apply" from "kind applies but no data".
    Iteration is deterministic in (temperature_order, pressure_order).

    The flattened coefficient list is truncated at ``cap`` rows;
    ``coefficient_count_total`` always reports the full flattened
    count so callers can detect the truncation.
    """
    if cheb_row is None:
        return None
    matrix = _extract_chebyshev_matrix(cheb_row.coefficients)
    total = 0
    coefficients: list[NetworkKineticsChebyshevCoefficient] = []
    if matrix is not None:
        for t_idx, row in enumerate(matrix):
            if not isinstance(row, list):
                continue
            for p_idx, value in enumerate(row):
                total += 1
                if len(coefficients) < cap:
                    coefficients.append(
                        NetworkKineticsChebyshevCoefficient(
                            temperature_order=t_idx,
                            pressure_order=p_idx,
                            coefficient=float(value),
                        )
                    )
    return NetworkKineticsChebyshevPayload(
        n_temperature=int(cheb_row.n_temperature),
        n_pressure=int(cheb_row.n_pressure),
        coefficients=coefficients,
        coefficient_count_total=total,
        coefficients_truncated=total > cap,
    )


def _build_plog_entries(
    session: Session,
    network_kinetics_id: int,
    *,
    total: int,
    cap: int,
) -> tuple[list[NetworkKineticsPLOGEntry], bool]:
    """Project PLOG rows for one kinetics record, capped at ``cap``.

    Returns ``(entries, truncated)``. Ordering is
    ``(pressure_bar ASC, entry_index ASC)`` — the primary-key tuple on
    ``network_kinetics_plog``, so each kinetics record has a stable,
    unique order. The DB-level ``LIMIT cap`` keeps the wire payload
    bounded; ``truncated`` is derived from ``total`` (the unbounded
    count from the parent shape-metadata query) so detecting truncation
    does not require a second scan.
    """
    rows = session.scalars(
        select(NetworkKineticsPlog)
        .where(NetworkKineticsPlog.network_kinetics_id == network_kinetics_id)
        .order_by(
            NetworkKineticsPlog.pressure_bar.asc(),
            NetworkKineticsPlog.entry_index.asc(),
        )
        .limit(cap)
    ).all()
    entries = [
        NetworkKineticsPLOGEntry(
            pressure_bar=r.pressure_bar,
            entry_index=r.entry_index,
            a=r.a,
            a_units=r.a_units,
            n=r.n,
            ea_kj_mol=r.ea_kj_mol,
        )
        for r in rows
    ]
    return entries, total > cap


def _build_point_entries(
    session: Session, network_kinetics_id: int, *, cap: int
) -> tuple[list[NetworkKineticsPointEntry], bool]:
    """Project point-tabulated kinetics, capped at ``cap`` rows.

    Returns ``(rows, truncated)`` where ``truncated`` is True iff the
    underlying table held more rows than the cap. The cap protects
    against pathological cases where tabulated kinetics grew large
    enough to bust response budgets.
    """
    rows = session.scalars(
        select(NetworkKineticsPoint)
        .where(
            NetworkKineticsPoint.network_kinetics_id == network_kinetics_id
        )
        .order_by(
            NetworkKineticsPoint.temperature_k.asc(),
            NetworkKineticsPoint.pressure_bar.asc(),
        )
        .limit(cap + 1)
    ).all()
    truncated = len(rows) > cap
    rows = rows[:cap]
    return (
        [
            NetworkKineticsPointEntry(
                temperature_k=r.temperature_k,
                pressure_bar=r.pressure_bar,
                rate_value=r.rate_value,
            )
            for r in rows
        ],
        truncated,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _count(session: Session, model_cls, where) -> int:
    return int(
        session.scalar(
            select(func.count()).select_from(model_cls).where(where)
        )
        or 0
    )


def _load_parent_solve_badge(
    session: Session, solve_id: int | None
) -> RecordReviewBadge:
    if solve_id is None:
        return RecordReviewBadge(status=RecordReviewStatus.not_reviewed)
    badges = fetch_review_badges(
        session,
        record_type=SubmissionRecordType.network_solve,
        record_ids=[solve_id],
    )
    return badges.get(
        solve_id, RecordReviewBadge(status=RecordReviewStatus.not_reviewed)
    )


def _exists_solve_review(session: Session, solve_id: int | None) -> bool:
    if solve_id is None:
        return False
    from app.db.models.record_review import RecordReview

    return bool(
        session.scalar(
            select(func.count()).select_from(RecordReview).where(
                RecordReview.record_type == SubmissionRecordType.network_solve,
                RecordReview.record_id == solve_id,
            )
        )
    )


__all__ = [
    "EVALUATE_GRID_POINT_CAP",
    "_INTERNAL_INCLUDE_TOKENS",
    "_LEGAL_INCLUDE_TOKENS",
    "build_network_kinetics_record",
    "evaluate_network_kinetics",
    "get_network_kinetics",
]
