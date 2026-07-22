"""Finite-positive validation for entity-level kinetics write schemas."""

from __future__ import annotations

import math

import pytest
from pydantic import ValidationError

from app.schemas.entities.kinetics import KineticsCreate, KineticsUpdate


def _create(degeneracy):
    return KineticsCreate(
        reaction_entry_id=1,
        scientific_origin="computed",
        degeneracy=degeneracy,
    )


def _update(degeneracy):
    return KineticsUpdate(degeneracy=degeneracy)


@pytest.mark.parametrize("schema_factory", [_create, _update])
@pytest.mark.parametrize("value", [None, 1.0e-12, 1, 2.5])
def test_entity_write_degeneracy_accepts_none_or_finite_positive(
    schema_factory,
    value,
) -> None:
    assert schema_factory(value).degeneracy == value


@pytest.mark.parametrize("schema_factory", [_create, _update])
@pytest.mark.parametrize(
    ("value", "error_type"),
    [
        (0, "greater_than"),
        (-1.0, "greater_than"),
        (math.nan, "finite_number"),
        (math.inf, "finite_number"),
        (-math.inf, "finite_number"),
    ],
)
def test_entity_write_degeneracy_rejects_non_positive_or_nonfinite(
    schema_factory,
    value,
    error_type,
) -> None:
    with pytest.raises(ValidationError) as exc_info:
        schema_factory(value)

    assert [(error["loc"], error["type"]) for error in exc_info.value.errors()] == [
        (("degeneracy",), error_type)
    ]
