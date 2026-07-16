"""End-to-end tests for the NASA-9 + Wilhoit thermo representations.

Covers the full vertical: upload schema validation, workflow persistence of
``ThermoNASA9Interval`` / ``ThermoWilhoit`` rows plus ``thermo.model_kind``,
and read-back exposure through ``get_species_thermo``.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.common import ThermoModelKind
from app.db.models.thermo import ThermoNASA9Interval, ThermoWilhoit
from app.schemas.reads.scientific_thermo import (
    ThermoModelKindQuery,
    ThermoReadRequest,
)
from app.schemas.workflows.thermo_upload import ThermoUploadRequest
from app.services.scientific_read.thermo import get_species_thermo
from app.workflows.thermo import persist_thermo_upload


@pytest.fixture
def rb_session(db_engine):
    """A Session whose work is fully rolled back at teardown.

    These tests both persist and immediately read back, so they must not
    commit into the session-scoped test DB — a committed species with a
    common SMILES would collide (on the content-derived ``public_ref``)
    with species other tests create via ``unique_smiles()``. Binding the
    Session to a connection-level transaction that is rolled back keeps the
    flushed rows visible for the read-back yet leaves the DB untouched.
    """
    conn = db_engine.connect()
    txn = conn.begin()
    session = Session(bind=conn)
    try:
        yield session
    finally:
        session.close()
        txn.rollback()
        conn.close()


def _nasa9_intervals() -> list[dict]:
    """Three contiguous NASA-9 intervals with distinct coefficients."""
    return [
        {
            "interval_index": 1,
            "t_min_k": 200.0,
            "t_max_k": 1000.0,
            "a1": 1.0, "a2": 2.0, "a3": 3.0, "a4": 4.0, "a5": 5.0,
            "a6": 6.0, "a7": 7.0, "a8": 8.0, "a9": 9.0,
        },
        {
            "interval_index": 2,
            "t_min_k": 1000.0,
            "t_max_k": 3000.0,
            "a1": 11.0, "a2": 12.0, "a3": 13.0, "a4": 14.0, "a5": 15.0,
            "a6": 16.0, "a7": 17.0, "a8": 18.0, "a9": 19.0,
        },
        {
            "interval_index": 3,
            "t_min_k": 3000.0,
            "t_max_k": 6000.0,
            "a1": 21.0, "a2": 22.0, "a3": 23.0, "a4": 24.0, "a5": 25.0,
            "a6": 26.0, "a7": 27.0, "a8": 28.0, "a9": 29.0,
        },
    ]


def _wilhoit_block() -> dict:
    return {
        "cp0_j_mol_k": 33.3,
        "cp_inf_j_mol_k": 108.9,
        "b_k": 500.0,
        "a0": -1.5,
        "a1": 2.5,
        "a2": -0.3,
        "a3": 0.1,
        "h0_kj_mol": -241.8,
        "s0_j_mol_k": 188.8,
    }


def _request(smiles: str = "O", **overrides) -> ThermoUploadRequest:
    base: dict = {
        "species_entry": {"smiles": smiles, "charge": 0, "multiplicity": 1},
        "scientific_origin": "computed",
    }
    base.update(overrides)
    return ThermoUploadRequest(**base)


# ---------------------------------------------------------------------------
# NASA-9 persistence
# ---------------------------------------------------------------------------


def test_nasa9_upload_persists_three_intervals_and_model_kind(rb_session) -> None:
    session = rb_session
    thermo = persist_thermo_upload(
        session, _request(nasa9_intervals=_nasa9_intervals())
    )

    assert thermo.model_kind == ThermoModelKind.nasa9

    rows = session.scalars(
        select(ThermoNASA9Interval)
        .where(ThermoNASA9Interval.thermo_id == thermo.id)
        .order_by(ThermoNASA9Interval.interval_index)
    ).all()
    assert len(rows) == 3
    assert [r.interval_index for r in rows] == [1, 2, 3]
    assert rows[0].t_min_k == pytest.approx(200.0)
    assert rows[2].t_max_k == pytest.approx(6000.0)
    # Spot-check coefficient round-trip on the middle interval.
    assert rows[1].a1 == pytest.approx(11.0)
    assert rows[1].a9 == pytest.approx(19.0)


def test_nasa9_read_back_exposes_intervals(rb_session) -> None:
    session = rb_session
    thermo = persist_thermo_upload(
        session, _request(nasa9_intervals=_nasa9_intervals())
    )
    session.flush()
    response = get_species_thermo(
        session,
        species_entry_id=thermo.species_entry_id,
        request=ThermoReadRequest(),
    )
    record = response.records[0]
    assert record.model_kind == ThermoModelKindQuery.nasa9
    assert record.nasa9 is not None
    assert len(record.nasa9) == 3
    assert record.nasa9[0].interval_index == 1
    assert record.nasa9[2].a8 == pytest.approx(28.0)
    assert record.nasa is None
    assert record.wilhoit is None
    assert record.points is None


# ---------------------------------------------------------------------------
# Wilhoit persistence
# ---------------------------------------------------------------------------


def test_wilhoit_upload_persists_row_and_model_kind(rb_session) -> None:
    session = rb_session
    thermo = persist_thermo_upload(session, _request(wilhoit=_wilhoit_block()))

    assert thermo.model_kind == ThermoModelKind.wilhoit

    row = session.scalars(
        select(ThermoWilhoit).where(ThermoWilhoit.thermo_id == thermo.id)
    ).one()
    assert row.cp0_j_mol_k == pytest.approx(33.3)
    assert row.cp_inf_j_mol_k == pytest.approx(108.9)
    assert row.b_k == pytest.approx(500.0)
    assert row.a0 == pytest.approx(-1.5)
    assert row.h0_kj_mol == pytest.approx(-241.8)
    assert row.s0_j_mol_k == pytest.approx(188.8)


def test_wilhoit_read_back_exposes_block(rb_session) -> None:
    session = rb_session
    thermo = persist_thermo_upload(session, _request(wilhoit=_wilhoit_block()))
    session.flush()
    response = get_species_thermo(
        session,
        species_entry_id=thermo.species_entry_id,
        request=ThermoReadRequest(),
    )
    record = response.records[0]
    assert record.model_kind == ThermoModelKindQuery.wilhoit
    assert record.wilhoit is not None
    assert record.wilhoit.cp0_j_mol_k == pytest.approx(33.3)
    assert record.wilhoit.b_k == pytest.approx(500.0)
    assert record.nasa9 is None
    assert record.nasa is None


# ---------------------------------------------------------------------------
# model_kind filter on read
# ---------------------------------------------------------------------------


def test_read_model_kind_filter_selects_nasa9(rb_session) -> None:
    session = rb_session
    thermo = persist_thermo_upload(
        session, _request(nasa9_intervals=_nasa9_intervals())
    )
    session.flush()
    # nasa9 filter matches
    resp_hit = get_species_thermo(
        session,
        species_entry_id=thermo.species_entry_id,
        request=ThermoReadRequest(model_kind=ThermoModelKindQuery.nasa9),
    )
    assert len(resp_hit.records) == 1
    # wilhoit filter excludes it
    resp_miss = get_species_thermo(
        session,
        species_entry_id=thermo.species_entry_id,
        request=ThermoReadRequest(model_kind=ThermoModelKindQuery.wilhoit),
    )
    assert resp_miss.records == []


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def test_nasa9_and_wilhoit_together_rejected() -> None:
    with pytest.raises(ValidationError, match="at most one fit representation"):
        _request(nasa9_intervals=_nasa9_intervals(), wilhoit=_wilhoit_block())


def test_two_fits_together_rejected() -> None:
    """Two fitted models (NASA-9 + NASA-7) on one record are rejected."""
    nasa7 = {
        "t_low": 200.0, "t_mid": 1000.0, "t_high": 6000.0,
        "a1": 1.0, "a2": 0.0, "a3": 0.0, "a4": 0.0, "a5": 0.0,
        "a6": -1.0, "a7": 1.0,
        "b1": 1.0, "b2": 0.0, "b3": 0.0, "b4": 0.0, "b5": 0.0,
        "b6": -1.0, "b7": 1.0,
    }
    with pytest.raises(ValidationError, match="at most one fit representation"):
        _request(nasa=nasa7, nasa9_intervals=_nasa9_intervals())


def test_nasa9_and_points_coexist_accepted() -> None:
    """Tabulated points may accompany a fit; the fit wins model_kind."""
    req = _request(
        nasa9_intervals=_nasa9_intervals(),
        points=[{"temperature_k": 300.0, "cp_j_mol_k": 30.0}],
    )
    assert req.nasa9_intervals
    assert req.points


def test_model_kind_mismatch_rejected() -> None:
    with pytest.raises(ValidationError, match="requires the 'wilhoit' fit block"):
        _request(model_kind="wilhoit", nasa9_intervals=_nasa9_intervals())


def test_model_kind_scalar_with_block_rejected() -> None:
    with pytest.raises(ValidationError, match="scalar' is incompatible"):
        _request(model_kind="scalar", wilhoit=_wilhoit_block())


def test_nasa9_non_contiguous_indices_rejected() -> None:
    intervals = _nasa9_intervals()
    intervals[2]["interval_index"] = 4  # gap: 1, 2, 4
    with pytest.raises(ValidationError, match="contiguous"):
        _request(nasa9_intervals=intervals)


def test_nasa9_duplicate_indices_rejected() -> None:
    intervals = _nasa9_intervals()
    intervals[1]["interval_index"] = 1  # duplicate
    with pytest.raises(ValidationError, match="unique"):
        _request(nasa9_intervals=intervals)


def test_nasa9_interval_bad_bounds_rejected() -> None:
    intervals = _nasa9_intervals()
    intervals[0]["t_max_k"] = intervals[0]["t_min_k"]  # t_max <= t_min
    with pytest.raises(ValidationError, match="t_max_k must be greater"):
        _request(nasa9_intervals=intervals)


def test_matching_model_kind_accepted() -> None:
    """Explicit model_kind that agrees with the block is accepted."""
    req = _request(model_kind="nasa9", nasa9_intervals=_nasa9_intervals())
    assert req.model_kind == ThermoModelKind.nasa9


def test_scalar_infers_model_kind(rb_session) -> None:
    """A scalar-only upload infers model_kind=scalar."""
    thermo = persist_thermo_upload(
        rb_session, _request(h298_kj_mol=-241.8, s298_j_mol_k=188.8)
    )
    assert thermo.model_kind == ThermoModelKind.scalar
