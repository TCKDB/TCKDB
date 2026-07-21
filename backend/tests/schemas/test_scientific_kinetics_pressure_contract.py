"""Pressure validation shared by direct and composed kinetics queries."""

from __future__ import annotations

import math

import pytest
from pydantic import ValidationError

from app.schemas.reads.scientific_kinetics import KineticsReadRequest
from app.schemas.reads.scientific_kinetics_search import KineticsSearchRequest


@pytest.mark.parametrize("request_type", [KineticsReadRequest, KineticsSearchRequest])
@pytest.mark.parametrize("field", ["pressure_bar", "pressure"])
@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf])
def test_pressure_requires_a_finite_positive_value(request_type, field, value):
    with pytest.raises(ValidationError):
        request_type(**{field: value})


@pytest.mark.parametrize("request_type", [KineticsReadRequest, KineticsSearchRequest])
def test_pressure_aliases_compare_exactly_after_numeric_parsing(request_type):
    assert request_type(pressure_bar=1, pressure=1.0).pressure_bar == 1.0

    with pytest.raises(ValidationError, match="pressure_alias_conflict"):
        request_type(pressure_bar=1.0, pressure=1.0000000000005)
