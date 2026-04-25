"""Schema-level tests for IRC and NEB upload payloads."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.db.models.common import CalculationType, IRCDirection
from app.schemas.fragments.calculation import (
    CalculationWithResultsPayload,
    IRCPointPayload,
    IRCResultPayload,
    NEBImageResultPayload,
    NEBResultPayload,
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
# NEB
# ---------------------------------------------------------------------------


def test_neb_result_accepts_valid_payload():
    payload = NEBResultPayload(
        images=[
            NEBImageResultPayload(image_index=0),
            NEBImageResultPayload(image_index=1, is_climbing_image=True),
            NEBImageResultPayload(image_index=2),
        ],
    )
    assert len(payload.images) == 3


def test_neb_image_index_must_be_non_negative():
    with pytest.raises(ValidationError):
        NEBImageResultPayload(image_index=-1)


def test_neb_requires_at_least_one_image():
    with pytest.raises(ValidationError):
        NEBResultPayload(images=[])


def test_neb_duplicate_image_indices_rejected():
    with pytest.raises(ValueError, match="image_index values must be unique"):
        NEBResultPayload(
            images=[
                NEBImageResultPayload(image_index=0),
                NEBImageResultPayload(image_index=0),
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


def test_neb_result_rejected_on_non_neb_calculation():
    with pytest.raises(ValueError, match="neb_result.*not allowed"):
        CalculationWithResultsPayload(
            type=CalculationType.opt,
            software_release=_SOFTWARE,
            level_of_theory=_LOT,
            neb_result=NEBResultPayload(
                images=[NEBImageResultPayload(image_index=0)],
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
    assert payload.neb_result is None


def test_neb_calculation_accepts_neb_result():
    payload = CalculationWithResultsPayload(
        type=CalculationType.neb,
        software_release=_SOFTWARE,
        level_of_theory=_LOT,
        neb_result=NEBResultPayload(
            images=[
                NEBImageResultPayload(image_index=0),
                NEBImageResultPayload(image_index=1),
            ],
        ),
    )
    assert payload.neb_result is not None
    assert len(payload.neb_result.images) == 2
