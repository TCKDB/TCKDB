"""Schema-level tests for IRC and path-search upload payloads."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.db.models.common import CalculationType, IRCDirection, PathSearchMethod
from app.schemas.fragments.calculation import (
    CalculationWithResultsPayload,
    IRCPointPayload,
    IRCResultPayload,
    PathSearchPointPayload,
    PathSearchResultPayload,
)


_SOFTWARE = {"name": "gaussian", "version": "16"}
_LOT = {"method": "B3LYP", "basis": "6-31G(d)"}


# ---------------------------------------------------------------------------
# IRC
# ---------------------------------------------------------------------------


def test_irc_result_accepts_valid_payload():
    payload = IRCResultPayload(
        direction=IRCDirection.both,
        has_forward=True,
        has_reverse=True,
        ts_point_index=0,
        points=[
            IRCPointPayload(point_index=0, is_ts=True),
            IRCPointPayload(point_index=1, direction=IRCDirection.forward),
            IRCPointPayload(point_index=2, direction=IRCDirection.reverse),
        ],
    )
    assert payload.direction == IRCDirection.both
    assert len(payload.points) == 3


def test_irc_point_index_must_be_non_negative():
    with pytest.raises(ValidationError):
        IRCPointPayload(point_index=-1)


def test_irc_point_count_must_be_non_negative():
    with pytest.raises(ValidationError):
        IRCResultPayload(
            direction=IRCDirection.forward,
            has_forward=True,
            has_reverse=False,
            point_count=-1,
        )


def test_irc_duplicate_point_indices_rejected():
    with pytest.raises(ValueError, match="point_index values must be unique"):
        IRCResultPayload(
            direction=IRCDirection.forward,
            has_forward=True,
            has_reverse=False,
            points=[
                IRCPointPayload(point_index=0, direction=IRCDirection.forward),
                IRCPointPayload(point_index=0, direction=IRCDirection.forward),
            ],
        )


def test_irc_ts_point_index_must_match_provided_point():
    with pytest.raises(ValueError, match="ts_point_index must match"):
        IRCResultPayload(
            direction=IRCDirection.both,
            has_forward=True,
            has_reverse=True,
            ts_point_index=99,
            points=[
                IRCPointPayload(point_index=0, is_ts=True),
                IRCPointPayload(point_index=1, direction=IRCDirection.forward),
            ],
        )


def test_irc_forward_point_requires_has_forward_flag():
    with pytest.raises(ValueError, match="has_forward must be true"):
        IRCResultPayload(
            direction=IRCDirection.forward,
            has_forward=False,
            has_reverse=False,
            points=[
                IRCPointPayload(point_index=0, direction=IRCDirection.forward),
            ],
        )


# ---------------------------------------------------------------------------
# Path search (NEB / GSM / string methods share one shape)
# ---------------------------------------------------------------------------


def test_path_search_result_accepts_neb_payload():
    payload = PathSearchResultPayload(
        method=PathSearchMethod.neb,
        is_double_ended=True,
        converged=True,
        n_points=3,
        points=[
            PathSearchPointPayload(point_index=0),
            PathSearchPointPayload(
                point_index=1, is_climbing_image=True, is_ts_guess=True
            ),
            PathSearchPointPayload(point_index=2),
        ],
    )
    assert payload.method is PathSearchMethod.neb
    assert len(payload.points) == 3


def test_path_search_result_accepts_gsm_payload():
    payload = PathSearchResultPayload(
        method=PathSearchMethod.gsm,
        is_double_ended=True,
        converged=True,
        n_points=2,
        selected_ts_point_index=1,
        points=[
            PathSearchPointPayload(point_index=0),
            PathSearchPointPayload(point_index=1, is_ts_guess=True),
        ],
    )
    assert payload.method is PathSearchMethod.gsm
    assert payload.selected_ts_point_index == 1


def test_path_search_point_index_must_be_non_negative():
    with pytest.raises(ValidationError):
        PathSearchPointPayload(point_index=-1)


def test_path_search_requires_at_least_one_point():
    with pytest.raises(ValidationError):
        PathSearchResultPayload(method=PathSearchMethod.neb, points=[])


def test_path_search_duplicate_point_indices_rejected():
    with pytest.raises(ValueError, match="point_index values must be unique"):
        PathSearchResultPayload(
            method=PathSearchMethod.neb,
            points=[
                PathSearchPointPayload(point_index=0),
                PathSearchPointPayload(point_index=0),
            ],
        )


def test_path_search_selected_ts_point_index_must_match():
    with pytest.raises(ValueError, match="selected_ts_point_index must match"):
        PathSearchResultPayload(
            method=PathSearchMethod.gsm,
            selected_ts_point_index=99,
            points=[
                PathSearchPointPayload(point_index=0),
                PathSearchPointPayload(point_index=1),
            ],
        )


# ---------------------------------------------------------------------------
# Calculation / result type compatibility
# ---------------------------------------------------------------------------


def test_irc_result_rejected_on_non_irc_calculation():
    with pytest.raises(ValueError, match="irc_result.*not allowed"):
        CalculationWithResultsPayload(
            type=CalculationType.sp,
            software_release=_SOFTWARE,
            level_of_theory=_LOT,
            irc_result=IRCResultPayload(
                direction=IRCDirection.forward,
                has_forward=True,
                has_reverse=False,
            ),
        )


def test_path_search_result_rejected_on_non_path_search_calculation():
    with pytest.raises(ValueError, match="path_search_result.*not allowed"):
        CalculationWithResultsPayload(
            type=CalculationType.opt,
            software_release=_SOFTWARE,
            level_of_theory=_LOT,
            path_search_result=PathSearchResultPayload(
                method=PathSearchMethod.neb,
                points=[PathSearchPointPayload(point_index=0)],
            ),
        )


def test_irc_calculation_accepts_irc_result():
    payload = CalculationWithResultsPayload(
        type=CalculationType.irc,
        software_release=_SOFTWARE,
        level_of_theory=_LOT,
        irc_result=IRCResultPayload(
            direction=IRCDirection.forward,
            has_forward=True,
            has_reverse=False,
        ),
    )
    assert payload.irc_result is not None
    assert payload.path_search_result is None


def test_path_search_calculation_accepts_neb_method():
    payload = CalculationWithResultsPayload(
        type=CalculationType.path_search,
        software_release=_SOFTWARE,
        level_of_theory=_LOT,
        path_search_result=PathSearchResultPayload(
            method=PathSearchMethod.neb,
            points=[
                PathSearchPointPayload(point_index=0),
                PathSearchPointPayload(point_index=1),
            ],
        ),
    )
    assert payload.path_search_result is not None
    assert payload.path_search_result.method is PathSearchMethod.neb
    assert len(payload.path_search_result.points) == 2


def test_path_search_calculation_accepts_gsm_method():
    payload = CalculationWithResultsPayload(
        type=CalculationType.path_search,
        software_release=_SOFTWARE,
        level_of_theory=_LOT,
        path_search_result=PathSearchResultPayload(
            method=PathSearchMethod.gsm,
            points=[
                PathSearchPointPayload(point_index=0),
                PathSearchPointPayload(point_index=1, is_ts_guess=True),
            ],
        ),
    )
    assert payload.path_search_result is not None
    assert payload.path_search_result.method is PathSearchMethod.gsm
