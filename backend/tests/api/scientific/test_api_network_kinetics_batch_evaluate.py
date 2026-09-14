"""API tests for ``POST /scientific/networks/{ref}/kinetics/evaluate``.

The network-scoped batch companion to
``GET /scientific/network-kinetics/{ref}/evaluate`` (pinned in
``test_api_network_kinetics_evaluate.py``): evaluates every stored
``network_kinetics`` fit belonging to one network at a shared
(temperature_k, pressure_bar) grid, in one call, so a k(T,P) chart
covering a whole network needs one request instead of one per fit.

No new evaluation math here -- the per-fit Chebyshev/PLOG values are
pinned once in ``test_api_network_kinetics_evaluate.py`` and reused
verbatim below; these tests check the batch endpoint's own wiring:
network-scoped fit discovery, the ``network_kinetics_ref``-keyed
response shape (never collapsed by channel), the two size caps, and
that a network with no kinetics yet returns an empty list rather than a
404.
"""

from __future__ import annotations

import hashlib

import pytest

from app.db.models.common import (
    ArrheniusAUnits,
    NetworkChannelKind,
    NetworkKineticsModelKind,
    NetworkStateKind,
)
from app.services.scientific_read.network_kinetics import EVALUATE_GRID_POINT_CAP
from app.services.scientific_read.network_kinetics_batch_evaluate import (
    BATCH_EVALUATE_POINT_CAP,
)
from tests.services.scientific_read._factories import (
    attach_network_kinetics_chebyshev,
    attach_network_kinetics_plog,
    attach_network_state_participant,
    make_network,
    make_network_channel,
    make_network_kinetics,
    make_network_solve,
    make_network_state,
    make_species,
    make_species_entry,
    next_inchi_key,
)

# Same hand-computed hydrazine-network fixture as
# test_api_network_kinetics_evaluate.py, so the batch endpoint's values
# agree with the already-pinned per-record reference, not merely with
# themselves.
_CHEB_MATRIX = {
    "coeffs": [
        [-11.0588, -0.016371, -0.011291, -0.0061746],
        [17.589, 0.0118079, 0.0081, 0.00438967],
        [0.2061, -0.000396769, -0.000233379, -9.12421e-05],
        [0.0494353, 0.000901544, 0.000603299, 0.000313357],
        [0.0230819, -1.84333e-05, -4.53189e-06, 4.75099e-06],
        [0.00686308, 6.35813e-05, 4.01076e-05, 1.86553e-05],
    ]
}

_PLOG_ENTRIES = [
    (0.01, 59828.5, 2.40236, 225.147),
    (0.1, 59747.1, 2.40253, 225.147),
    (1.0, 58937.7, 2.40426, 225.144),
    (10.0, 51368.2, 2.42166, 225.117),
    (100.0, 13579.5, 2.58976, 224.788),
]


def _h(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


def _build_network(db_session, *, n_channels: int = 1):
    """One network with ``n_channels`` isomerization channels, each
    between its own fresh pair of states, sharing one solve."""
    network = make_network(db_session, name=f"net-{next_inchi_key('NET')}")
    sp = make_species(db_session, inchi_key=next_inchi_key("NA"))
    se = make_species_entry(db_session, sp)
    solve = make_network_solve(db_session, network=network)
    channels = []
    for i in range(n_channels):
        state_a = make_network_state(
            db_session,
            network=network,
            kind=NetworkStateKind.well,
            composition_hash=_h(f"state-a-{network.id}-{i}"),
        )
        state_b = make_network_state(
            db_session,
            network=network,
            kind=NetworkStateKind.well,
            composition_hash=_h(f"state-b-{network.id}-{i}"),
        )
        attach_network_state_participant(
            db_session, state=state_a, species_entry=se, stoichiometry=1
        )
        attach_network_state_participant(
            db_session, state=state_b, species_entry=se, stoichiometry=1
        )
        channel = make_network_channel(
            db_session,
            network=network,
            source_state=state_a,
            sink_state=state_b,
            kind=NetworkChannelKind.isomerization,
            channel_key=f"channel_{i + 1}",
        )
        channels.append(channel)
    return network, channels, solve


def _attach_chebyshev_fit(db_session, *, channel, solve):
    kin = make_network_kinetics(
        db_session,
        channel=channel,
        solve=solve,
        model_kind=NetworkKineticsModelKind.chebyshev,
        rate_units=ArrheniusAUnits.cm3_mol_s,
        stores_log10_k=True,
    )
    attach_network_kinetics_chebyshev(
        db_session, kinetics=kin, n_temperature=6, n_pressure=4,
        coefficients=_CHEB_MATRIX,
    )
    return kin


def _attach_plog_fit(db_session, *, channel, solve):
    kin = make_network_kinetics(
        db_session,
        channel=channel,
        solve=solve,
        model_kind=NetworkKineticsModelKind.plog,
        rate_units=ArrheniusAUnits.cm3_mol_s,
    )
    for pressure_bar, a, n, ea in _PLOG_ENTRIES:
        attach_network_kinetics_plog(
            db_session, kinetics=kin, pressure_bar=pressure_bar, a=a, n=n, ea_kj_mol=ea
        )
    return kin


def _batch_url(network_ref: str) -> str:
    return f"/api/v1/scientific/networks/{network_ref}/kinetics/evaluate"


# ---------------------------------------------------------------------------
# Happy paths
# ---------------------------------------------------------------------------


def test_batch_evaluates_the_one_stored_fit_matching_the_per_record_reference(
    client, db_session
):
    network, [channel], solve = _build_network(db_session)
    kin = _attach_chebyshev_fit(db_session, channel=channel, solve=solve)

    resp = client.post(
        _batch_url(network.public_ref),
        json={"temperature_k": [1000.0], "pressure_bar": [1.0]},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["network_ref"] == network.public_ref
    assert len(body["fits"]) == 1
    fit = body["fits"][0]
    assert fit["network_kinetics_ref"] == kin.public_ref
    assert fit["channel_key"] == "channel_1"
    assert fit["channel_kind"] == "isomerization"
    assert fit["network_solve_ref"] == solve.public_ref
    assert fit["model_kind"] == "chebyshev"
    assert fit["k_units"] == "cm3_mol_s"
    assert len(fit["points"]) == 1
    point = fit["points"][0]
    assert point["in_range"] is True
    assert point["k"] == pytest.approx(1.692622, rel=1e-5)


def test_batch_keys_by_kinetics_ref_two_fits_on_one_channel_neither_collapsed(
    client, db_session
):
    """The load-bearing shape claim: a channel carrying both a
    Chebyshev and a PLOG fit yields two response entries, not one --
    and neither value is picked, averaged, or dropped in favour of the
    other."""
    network, [channel], solve = _build_network(db_session)
    cheb = _attach_chebyshev_fit(db_session, channel=channel, solve=solve)
    plog = _attach_plog_fit(db_session, channel=channel, solve=solve)

    resp = client.post(
        _batch_url(network.public_ref),
        json={"temperature_k": [1000.0], "pressure_bar": [1.0]},
    )
    assert resp.status_code == 200, resp.text
    fits = resp.json()["fits"]
    assert len(fits) == 2
    by_ref = {f["network_kinetics_ref"]: f for f in fits}
    assert set(by_ref) == {cheb.public_ref, plog.public_ref}
    # Both entries point at the same channel...
    assert by_ref[cheb.public_ref]["channel_key"] == "channel_1"
    assert by_ref[plog.public_ref]["channel_key"] == "channel_1"
    # ...but carry materially different evaluated rates -- exactly the
    # disagreement a collapsing implementation would have hidden.
    cheb_k = by_ref[cheb.public_ref]["points"][0]["k"]
    plog_k = by_ref[plog.public_ref]["points"][0]["k"]
    assert cheb_k == pytest.approx(1.692622, rel=1e-5)
    assert plog_k == pytest.approx(1.671425, rel=1e-5)
    assert cheb_k != plog_k


def test_batch_covers_every_channel_in_the_network(client, db_session):
    network, channels, solve = _build_network(db_session, n_channels=3)
    kins = [
        _attach_chebyshev_fit(db_session, channel=c, solve=solve) for c in channels
    ]

    resp = client.post(
        _batch_url(network.public_ref),
        json={"temperature_k": [1000.0], "pressure_bar": [1.0]},
    )
    assert resp.status_code == 200, resp.text
    fits = resp.json()["fits"]
    assert {f["network_kinetics_ref"] for f in fits} == {k.public_ref for k in kins}
    assert {f["channel_key"] for f in fits} == {"channel_1", "channel_2", "channel_3"}


def test_batch_grid_is_shared_cartesian_product_per_fit(client, db_session):
    network, [channel], solve = _build_network(db_session)
    _attach_chebyshev_fit(db_session, channel=channel, solve=solve)

    resp = client.post(
        _batch_url(network.public_ref),
        json={"temperature_k": [500.0, 1000.0, 1500.0], "pressure_bar": [1.0, 10.0]},
    )
    assert resp.status_code == 200, resp.text
    points = resp.json()["fits"][0]["points"]
    assert len(points) == 6
    seen = {(p["temperature_k"], p["pressure_bar"]) for p in points}
    assert seen == {
        (500.0, 1.0), (500.0, 10.0),
        (1000.0, 1.0), (1000.0, 10.0),
        (1500.0, 1.0), (1500.0, 10.0),
    }


def test_batch_out_of_range_point_is_flagged_not_refused(client, db_session):
    network, [channel], solve = _build_network(db_session)
    _attach_chebyshev_fit(db_session, channel=channel, solve=solve)

    resp = client.post(
        _batch_url(network.public_ref),
        json={"temperature_k": [2500.0], "pressure_bar": [1.0]},
    )
    assert resp.status_code == 200, resp.text
    point = resp.json()["fits"][0]["points"][0]
    assert point["in_range"] is False
    assert isinstance(point["k"], float)


def test_batch_on_network_with_no_kinetics_returns_empty_fits_not_404(
    client, db_session
):
    network, _channels, _solve = _build_network(db_session, n_channels=0)

    resp = client.post(
        _batch_url(network.public_ref),
        json={"temperature_k": [1000.0], "pressure_bar": [1.0]},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["network_ref"] == network.public_ref
    assert body["fits"] == []


# ---------------------------------------------------------------------------
# Refusals
# ---------------------------------------------------------------------------


def test_batch_unknown_network_ref_returns_404(client, db_session):
    resp = client.post(
        _batch_url("net_doesnotexist00000000000000000"),
        json={"temperature_k": [1000.0], "pressure_bar": [1.0]},
    )
    assert resp.status_code == 404, resp.text


def test_batch_requires_at_least_one_temperature(client, db_session):
    network, [channel], solve = _build_network(db_session)
    _attach_chebyshev_fit(db_session, channel=channel, solve=solve)

    resp = client.post(
        _batch_url(network.public_ref),
        json={"temperature_k": [], "pressure_bar": [1.0]},
    )
    assert resp.status_code == 422, resp.text
    assert resp.json()["code"] == "network_kinetics_evaluate_missing_temperature"


def test_batch_requires_at_least_one_pressure(client, db_session):
    network, [channel], solve = _build_network(db_session)
    _attach_chebyshev_fit(db_session, channel=channel, solve=solve)

    resp = client.post(
        _batch_url(network.public_ref),
        json={"temperature_k": [1000.0], "pressure_bar": []},
    )
    assert resp.status_code == 422, resp.text
    assert resp.json()["code"] == "network_kinetics_evaluate_missing_pressure"


def test_batch_rejects_non_positive_pressure(client, db_session):
    network, [channel], solve = _build_network(db_session)
    _attach_chebyshev_fit(db_session, channel=channel, solve=solve)

    resp = client.post(
        _batch_url(network.public_ref),
        json={"temperature_k": [1000.0], "pressure_bar": [0.0]},
    )
    assert resp.status_code == 422, resp.text
    assert resp.json()["code"] == "network_kinetics_evaluate_invalid_point"


def test_batch_validates_axes_before_the_fit_count_can_hide_it(client, db_session):
    """A network with *zero* stored fits skips the per-fit loop entirely
    -- the only place axis validation would otherwise happen a second
    time via the reused ``evaluate_network_kinetics`` call. This is the
    one scenario that isolates the batch endpoint's *own* upfront axis
    checks: without them, an invalid grid against a fit-less network
    would silently come back ``200 {"fits": []}`` instead of a 422,
    since nothing downstream ever runs to catch it."""
    network, _channels, _solve = _build_network(db_session, n_channels=0)

    resp = client.post(
        _batch_url(network.public_ref),
        json={"temperature_k": [], "pressure_bar": [1.0]},
    )
    assert resp.status_code == 422, resp.text
    assert resp.json()["code"] == "network_kinetics_evaluate_missing_temperature"


def test_batch_rejects_unknown_query_string_fields(client, db_session):
    """POST search endpoints on this router reject query-string keys
    other than profile/release, so a caller cannot silently have a
    field ignored; the batch endpoint follows the same rule."""
    network, [channel], solve = _build_network(db_session)
    _attach_chebyshev_fit(db_session, channel=channel, solve=solve)

    resp = client.post(
        _batch_url(network.public_ref) + "?temperature_k=1000",
        json={"temperature_k": [1000.0], "pressure_bar": [1.0]},
    )
    assert resp.status_code == 422, resp.text
    assert "post_search_fields_must_be_in_body" in resp.json()["detail"]


def test_batch_per_fit_grid_too_large_returns_the_shared_per_record_code(
    client, db_session
):
    """A grid too large for even one fit is refused before the network
    is ever resolved, with the exact code/cap the per-record endpoint
    uses for the identical check -- reused deliberately, see
    ``network_kinetics_batch_evaluate.py``."""
    network, [channel], solve = _build_network(db_session)
    _attach_chebyshev_fit(db_session, channel=channel, solve=solve)

    n_temps = EVALUATE_GRID_POINT_CAP // 2 + 2
    n_press = 3
    assert n_temps * n_press > EVALUATE_GRID_POINT_CAP

    resp = client.post(
        _batch_url(network.public_ref),
        json={
            "temperature_k": [300.0 + i for i in range(n_temps)],
            "pressure_bar": [1.0 + i for i in range(n_press)],
        },
    )
    assert resp.status_code == 422, resp.text
    body = resp.json()
    assert body["code"] == "network_kinetics_evaluate_grid_too_large"
    assert body["context"]["cap"] == EVALUATE_GRID_POINT_CAP


def test_batch_grid_at_exact_per_fit_cap_is_accepted(client, db_session):
    network, [channel], solve = _build_network(db_session)
    _attach_chebyshev_fit(db_session, channel=channel, solve=solve)

    n_press = 4
    n_temps = EVALUATE_GRID_POINT_CAP // n_press
    assert n_temps * n_press == EVALUATE_GRID_POINT_CAP

    resp = client.post(
        _batch_url(network.public_ref),
        json={
            "temperature_k": [300.0 + i for i in range(n_temps)],
            "pressure_bar": [1.0 + i for i in range(n_press)],
        },
    )
    assert resp.status_code == 200, resp.text
    assert len(resp.json()["fits"][0]["points"]) == EVALUATE_GRID_POINT_CAP


def test_batch_aggregate_cap_fires_below_the_per_fit_cap(db_session, monkeypatch):
    """Service-level test: the aggregate ``fit_count * grid_size`` cap is
    its own check, distinct from the per-fit cap -- pinned by lowering
    :data:`BATCH_EVALUATE_POINT_CAP` far below what 2 fits x a
    2-point grid would need (4 points), rather than by actually
    creating tens of thousands of rows to reach the real default."""
    import app.services.scientific_read.network_kinetics_batch_evaluate as mod

    monkeypatch.setattr(mod, "BATCH_EVALUATE_POINT_CAP", 3)

    network, [channel_a, channel_b], solve = _build_network(db_session, n_channels=2)
    _attach_chebyshev_fit(db_session, channel=channel_a, solve=solve)
    _attach_chebyshev_fit(db_session, channel=channel_b, solve=solve)

    from app.api.error_contract import CodedValidationError

    with pytest.raises(CodedValidationError) as excinfo:
        mod.evaluate_network_kinetics_batch(
            db_session,
            network_handle=network.public_ref,
            temperatures_k=[1000.0, 1100.0],
            pressures_bar=[1.0],
        )
    assert excinfo.value.code == "network_kinetics_batch_evaluate_grid_too_large"
    assert excinfo.value.context["fit_count"] == 2
    assert excinfo.value.context["grid_size"] == 2
    assert excinfo.value.context["total_points"] == 4
    assert excinfo.value.context["cap"] == 3


def test_batch_aggregate_cap_at_exact_boundary_is_accepted(db_session, monkeypatch):
    import app.services.scientific_read.network_kinetics_batch_evaluate as mod

    monkeypatch.setattr(mod, "BATCH_EVALUATE_POINT_CAP", 4)

    network, [channel_a, channel_b], solve = _build_network(db_session, n_channels=2)
    _attach_chebyshev_fit(db_session, channel=channel_a, solve=solve)
    _attach_chebyshev_fit(db_session, channel=channel_b, solve=solve)

    result = mod.evaluate_network_kinetics_batch(
        db_session,
        network_handle=network.public_ref,
        temperatures_k=[1000.0, 1100.0],
        pressures_bar=[1.0],
    )
    assert len(result.fits) == 2
    assert all(len(f.points) == 2 for f in result.fits)


def test_batch_default_cap_constant_matches_documented_derivation():
    """Pins the derivation stated in the module docstring: the batch cap
    is the per-record cap squared (per-fit-grid cap x "how many rows one
    list response may return" cap), not an independently-chosen number
    that could drift out of step with either."""
    from app.api.config import settings

    assert BATCH_EVALUATE_POINT_CAP == EVALUATE_GRID_POINT_CAP * int(
        settings.public_max_limit
    )
