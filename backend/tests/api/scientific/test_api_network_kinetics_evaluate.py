"""API tests for ``GET /scientific/network-kinetics/{ref}/evaluate``.

Server-side k(T,P) evaluation (owner ruling, see
``docs/plans/pressure-dependent-network-surface.md`` §2.4). The pure
math is pinned independently in
``backend/tests/services/test_network_kinetics_eval.py``; this file
checks the endpoint's wiring: handle resolution, the grid-of-points
request shape, the coded-422 contract, and that "no kinetics" never
serves as a fabricated k=0.
"""

from __future__ import annotations

import hashlib

import pytest

from app.api.errors import DataIntegrityError
from app.db.models.common import (
    ArrheniusAUnits,
    NetworkChannelKind,
    NetworkKineticsModelKind,
    NetworkStateKind,
)
from app.services.scientific_read.network_kinetics import (
    EVALUATE_GRID_POINT_CAP,
    evaluate_network_kinetics,
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

# Real hydrazine-network fixture (same values pinned in
# test_network_kinetics_eval.py) so the API-level assertions agree with
# the hand-computed reference, not merely with themselves.
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


def _h(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


def _build_channel(db_session):
    n = make_network(db_session, name=f"net-{next_inchi_key('NET')}")
    sp = make_species(db_session, inchi_key=next_inchi_key("NA"))
    se = make_species_entry(db_session, sp)
    state_a = make_network_state(
        db_session,
        network=n,
        kind=NetworkStateKind.well,
        composition_hash=_h(f"state-a-{n.id}"),
    )
    state_b = make_network_state(
        db_session,
        network=n,
        kind=NetworkStateKind.well,
        composition_hash=_h(f"state-b-{n.id}"),
    )
    attach_network_state_participant(
        db_session, state=state_a, species_entry=se, stoichiometry=1
    )
    attach_network_state_participant(
        db_session, state=state_b, species_entry=se, stoichiometry=1
    )
    channel = make_network_channel(
        db_session,
        network=n,
        source_state=state_a,
        sink_state=state_b,
        kind=NetworkChannelKind.isomerization,
    )
    solve = make_network_solve(db_session, network=n)
    return n, channel, solve


def _evaluate_url(handle: str, **params) -> str:
    base = f"/api/v1/scientific/network-kinetics/{handle}/evaluate"
    qs = "&".join(f"{k}={v}" for k, v in params.items())
    return f"{base}?{qs}" if params else base


# ---------------------------------------------------------------------------
# Happy paths
# ---------------------------------------------------------------------------


def test_evaluate_chebyshev_matches_hand_computed_reference(client, db_session):
    _, channel, solve = _build_channel(db_session)
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

    resp = client.get(_evaluate_url(kin.public_ref, temperature_k=1000.0, pressure_bar=1.0))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["network_kinetics_ref"] == kin.public_ref
    assert body["model_kind"] == "chebyshev"
    assert body["k_units"] == "cm3_mol_s"
    assert len(body["points"]) == 1
    point = body["points"][0]
    assert point["temperature_k"] == 1000.0
    assert point["pressure_bar"] == 1.0
    assert point["in_range"] is True
    assert point["k"] == pytest.approx(1.692622, rel=1e-5)


def test_evaluate_plog_matches_hand_computed_reference(client, db_session):
    _, channel, solve = _build_channel(db_session)
    kin = make_network_kinetics(
        db_session,
        channel=channel,
        solve=solve,
        model_kind=NetworkKineticsModelKind.plog,
        rate_units=ArrheniusAUnits.cm3_mol_s,
    )
    for pressure_bar, a, n, ea in [
        (0.01, 59828.5, 2.40236, 225.147),
        (0.1, 59747.1, 2.40253, 225.147),
        (1.0, 58937.7, 2.40426, 225.144),
        (10.0, 51368.2, 2.42166, 225.117),
        (100.0, 13579.5, 2.58976, 224.788),
    ]:
        attach_network_kinetics_plog(
            db_session, kinetics=kin, pressure_bar=pressure_bar, a=a, n=n, ea_kj_mol=ea
        )

    resp = client.get(_evaluate_url(kin.public_ref, temperature_k=1000.0, pressure_bar=1.0))
    assert resp.status_code == 200, resp.text
    point = resp.json()["points"][0]
    assert point["in_range"] is True
    assert point["k"] == pytest.approx(1.671425, rel=1e-5)


def test_evaluate_grid_is_cartesian_product(client, db_session):
    _, channel, solve = _build_channel(db_session)
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
    url = (
        f"/api/v1/scientific/network-kinetics/{kin.public_ref}/evaluate"
        "?temperature_k=500&temperature_k=1000&temperature_k=1500"
        "&pressure_bar=1&pressure_bar=10"
    )
    resp = client.get(url)
    assert resp.status_code == 200, resp.text
    points = resp.json()["points"]
    assert len(points) == 6  # 3 temperatures x 2 pressures
    seen = {(p["temperature_k"], p["pressure_bar"]) for p in points}
    assert seen == {
        (500.0, 1.0), (500.0, 10.0),
        (1000.0, 1.0), (1000.0, 10.0),
        (1500.0, 1.0), (1500.0, 10.0),
    }


def test_evaluate_out_of_range_point_is_flagged_not_refused(client, db_session):
    _, channel, solve = _build_channel(db_session)
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
    # tmax_k=2000 on the factory default; ask for 2500K.
    resp = client.get(_evaluate_url(kin.public_ref, temperature_k=2500.0, pressure_bar=1.0))
    assert resp.status_code == 200, resp.text
    point = resp.json()["points"][0]
    assert point["in_range"] is False
    assert isinstance(point["k"], float)


# ---------------------------------------------------------------------------
# Refusals
# ---------------------------------------------------------------------------


def test_evaluate_grid_too_large_returns_coded_422(client, db_session):
    _, channel, solve = _build_channel(db_session)
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
    # (cap + 1) temperatures x 1 pressure exceeds the cap.
    temps = "&".join(f"temperature_k={300 + i}" for i in range(EVALUATE_GRID_POINT_CAP + 1))
    url = f"/api/v1/scientific/network-kinetics/{kin.public_ref}/evaluate?{temps}&pressure_bar=1"
    resp = client.get(url)
    assert resp.status_code == 422, resp.text
    assert resp.json()["code"] == "network_kinetics_evaluate_grid_too_large"


def test_evaluate_missing_rate_units_returns_coded_422(client, db_session):
    _, channel, solve = _build_channel(db_session)
    kin = make_network_kinetics(
        db_session,
        channel=channel,
        solve=solve,
        model_kind=NetworkKineticsModelKind.chebyshev,
        rate_units=None,
        stores_log10_k=True,
    )
    attach_network_kinetics_chebyshev(
        db_session, kinetics=kin, n_temperature=6, n_pressure=4,
        coefficients=_CHEB_MATRIX,
    )
    resp = client.get(_evaluate_url(kin.public_ref, temperature_k=1000.0, pressure_bar=1.0))
    assert resp.status_code == 422, resp.text
    assert resp.json()["code"] == "network_kinetics_rate_units_missing"


def test_evaluate_unknown_ref_returns_404(client, db_session):
    resp = client.get(_evaluate_url("nkin_doesnotexist000000000000000", temperature_k=1000.0, pressure_bar=1.0))
    assert resp.status_code == 404, resp.text


def test_evaluate_requires_at_least_one_temperature(client, db_session):
    _, channel, solve = _build_channel(db_session)
    kin = make_network_kinetics(
        db_session,
        channel=channel,
        solve=solve,
        model_kind=NetworkKineticsModelKind.chebyshev,
        rate_units=ArrheniusAUnits.cm3_mol_s,
    )
    resp = client.get(f"/api/v1/scientific/network-kinetics/{kin.public_ref}/evaluate?pressure_bar=1")
    assert resp.status_code == 422, resp.text


def test_evaluate_rejects_non_positive_pressure(client, db_session):
    _, channel, solve = _build_channel(db_session)
    kin = make_network_kinetics(
        db_session,
        channel=channel,
        solve=solve,
        model_kind=NetworkKineticsModelKind.chebyshev,
        rate_units=ArrheniusAUnits.cm3_mol_s,
    )
    resp = client.get(_evaluate_url(kin.public_ref, temperature_k=1000.0, pressure_bar=0.0))
    assert resp.status_code == 422, resp.text


# ---------------------------------------------------------------------------
# Absence stays absence: a channel/fit with no kinetics never evaluates
# to a fabricated k=0.
# ---------------------------------------------------------------------------


def test_evaluate_chebyshev_with_no_coefficients_raises_data_integrity_not_zero(
    db_session,
):
    _, channel, solve = _build_channel(db_session)
    kin = make_network_kinetics(
        db_session,
        channel=channel,
        solve=solve,
        model_kind=NetworkKineticsModelKind.chebyshev,
        rate_units=ArrheniusAUnits.cm3_mol_s,
        stores_log10_k=True,
    )
    # Deliberately no attach_network_kinetics_chebyshev(...) call: this
    # channel's fit declares model_kind=chebyshev but stores none of the
    # coefficients that would require.
    with pytest.raises(DataIntegrityError):
        evaluate_network_kinetics(
            db_session,
            network_kinetics_handle=kin.public_ref,
            temperatures_k=[1000.0],
            pressures_bar=[1.0],
        )


def test_evaluate_plog_with_no_entries_raises_data_integrity_not_zero(db_session):
    _, channel, solve = _build_channel(db_session)
    kin = make_network_kinetics(
        db_session,
        channel=channel,
        solve=solve,
        model_kind=NetworkKineticsModelKind.plog,
        rate_units=ArrheniusAUnits.cm3_mol_s,
    )
    # Deliberately no attach_network_kinetics_plog(...) call.
    with pytest.raises(DataIntegrityError):
        evaluate_network_kinetics(
            db_session,
            network_kinetics_handle=kin.public_ref,
            temperatures_k=[1000.0],
            pressures_bar=[1.0],
        )


def test_evaluate_service_rejects_empty_axes(db_session):
    _, channel, solve = _build_channel(db_session)
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
    from app.api.error_contract import CodedValidationError

    with pytest.raises(CodedValidationError):
        evaluate_network_kinetics(
            db_session,
            network_kinetics_handle=kin.public_ref,
            temperatures_k=[],
            pressures_bar=[1.0],
        )
    with pytest.raises(CodedValidationError):
        evaluate_network_kinetics(
            db_session,
            network_kinetics_handle=kin.public_ref,
            temperatures_k=[1000.0],
            pressures_bar=[],
        )
