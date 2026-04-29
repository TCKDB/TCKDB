"""Schema-level tests for ``parameters_json["tckdb_origin"]`` validation.

The validation is opt-in: ``parameters_json`` is a generic JSONB blob,
and the ``tckdb_origin`` block is checked only when present. Absence
is the common case (most calculations are independently executed and
omit the block entirely).

The convention is documented in DR-0026.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.db.models.common import CalculationType
from app.schemas.fragments.calculation import CalculationWithResultsPayload
from app.schemas.fragments.calculation_origin import (
    CalculationOriginKind,
    CalculationOriginMetadata,
    ReusedFromCalculationRef,
)
from app.schemas.fragments.refs import (
    LevelOfTheoryRef,
    SoftwareReleaseRef,
)


def _base_calc_kwargs(**overrides) -> dict:
    """Minimum-required kwargs to construct a CalculationWithResultsPayload."""
    base = {
        "type": CalculationType.sp,
        "level_of_theory": LevelOfTheoryRef(method="wb97xd", basis="def2-tzvp"),
        "software_release": SoftwareReleaseRef(name="Gaussian"),
    }
    base.update(overrides)
    return base


def _reused_result_block(**overrides) -> dict:
    """A well-formed reused_result tckdb_origin block."""
    block = {
        "origin_kind": "reused_result",
        "reused_from": {"calculation_type": "opt"},
        "reason": "sp_level equals opt_level",
        "independent_ess_job": False,
        "producer": "ARC",
    }
    block.update(overrides)
    return block


# ---------------------------------------------------------------------------
# Test 1: parameters_json absent → valid
# ---------------------------------------------------------------------------


def test_parameters_json_absent_is_valid() -> None:
    """Most calculations omit parameters_json entirely; that's the
    common case and must not be rejected."""
    payload = CalculationWithResultsPayload(**_base_calc_kwargs())
    assert payload.parameters_json is None


# ---------------------------------------------------------------------------
# Test 2: parameters_json without tckdb_origin → valid
# ---------------------------------------------------------------------------


def test_parameters_json_without_tckdb_origin_is_valid() -> None:
    """parameters_json is a generic JSONB blob — producers are free to
    stash unrelated parser-output content there. Validation only fires
    when tckdb_origin is present."""
    payload = CalculationWithResultsPayload(
        **_base_calc_kwargs(
            parameters_json={
                "scf_iterations": 18,
                "scf_converged": True,
                "ess_route_line": "# wb97xd/def2-tzvp opt freq",
            }
        )
    )
    assert payload.parameters_json["scf_iterations"] == 18


# ---------------------------------------------------------------------------
# Test 3: valid reused_result block → valid and round-trips
# ---------------------------------------------------------------------------


def test_valid_reused_result_block_round_trips() -> None:
    """A well-formed reused_result block validates and the payload's
    parameters_json round-trips it byte-for-byte. This is the property
    the conformer-upload workflow relies on to persist origin metadata
    without modification."""
    block = _reused_result_block()
    payload = CalculationWithResultsPayload(
        **_base_calc_kwargs(parameters_json={"tckdb_origin": block})
    )
    assert payload.parameters_json["tckdb_origin"] == block
    # Direct fragment validation also works for producer-side checks.
    parsed = CalculationOriginMetadata.model_validate(block)
    assert parsed.origin_kind is CalculationOriginKind.reused_result
    assert parsed.reused_from == ReusedFromCalculationRef(
        calculation_type=CalculationType.opt
    )
    assert parsed.independent_ess_job is False
    assert parsed.producer == "ARC"


# ---------------------------------------------------------------------------
# Test 4: reused_result without reused_from → 422
# ---------------------------------------------------------------------------


def test_reused_result_without_reused_from_is_rejected() -> None:
    """The reused_result origin_kind requires reused_from to identify
    the source calculation type. Cross-field validation lives in
    CalculationOriginMetadata's model_validator."""
    block = _reused_result_block()
    del block["reused_from"]
    with pytest.raises(ValidationError) as exc_info:
        CalculationWithResultsPayload(
            **_base_calc_kwargs(parameters_json={"tckdb_origin": block})
        )
    assert "reused_from" in str(exc_info.value).lower()


# ---------------------------------------------------------------------------
# Test 5: reused_result with invalid calculation_type → 422
# ---------------------------------------------------------------------------


def test_reused_result_with_invalid_calculation_type_is_rejected() -> None:
    """ReusedFromCalculationRef.calculation_type is a CalculationType
    enum; a non-enum string must fail enum validation."""
    block = _reused_result_block(reused_from={"calculation_type": "not_a_real_type"})
    with pytest.raises(ValidationError):
        CalculationWithResultsPayload(
            **_base_calc_kwargs(parameters_json={"tckdb_origin": block})
        )


# ---------------------------------------------------------------------------
# Test 6: malformed origin_kind → 422
# ---------------------------------------------------------------------------


def test_malformed_origin_kind_is_rejected() -> None:
    """origin_kind is a strict enum with four values; anything else must
    be rejected so a typo can't silently slip into the database."""
    block = {
        "origin_kind": "reused",  # close but not in enum
        "reused_from": {"calculation_type": "opt"},
    }
    with pytest.raises(ValidationError):
        CalculationWithResultsPayload(
            **_base_calc_kwargs(parameters_json={"tckdb_origin": block})
        )


# ---------------------------------------------------------------------------
# Test 7: independent executed block is allowed if provided
# ---------------------------------------------------------------------------


def test_explicit_executed_block_is_allowed() -> None:
    """Producers may emit origin_kind: 'executed' explicitly for clarity;
    absence and explicit emission must be equally valid. No reused_from
    is required for executed."""
    block = {
        "origin_kind": "executed",
        "independent_ess_job": True,
        "producer": "ARC",
    }
    payload = CalculationWithResultsPayload(
        **_base_calc_kwargs(parameters_json={"tckdb_origin": block})
    )
    parsed = CalculationOriginMetadata.model_validate(
        payload.parameters_json["tckdb_origin"]
    )
    assert parsed.origin_kind is CalculationOriginKind.executed
    assert parsed.reused_from is None


# ---------------------------------------------------------------------------
# Cross-field constraint: reused_result + independent_ess_job=True → 422
# ---------------------------------------------------------------------------


def test_reused_result_with_independent_ess_job_true_is_rejected() -> None:
    """A reused result by definition did not run an independent ESS job;
    asserting both is internally inconsistent and must be rejected.
    This guard exists so producers cannot accidentally claim an
    independent execution while marking the row as reused."""
    block = _reused_result_block(independent_ess_job=True)
    with pytest.raises(ValidationError) as exc_info:
        CalculationWithResultsPayload(
            **_base_calc_kwargs(parameters_json={"tckdb_origin": block})
        )
    assert "independent_ess_job" in str(exc_info.value).lower()


# ---------------------------------------------------------------------------
# Other origin_kinds are accepted with minimal shape (forward compat)
# ---------------------------------------------------------------------------


def test_imported_origin_minimal_shape_is_allowed() -> None:
    """imported and derived origins do not (yet) impose cross-field
    constraints. They accept the bare minimum (origin_kind only) so
    producers can adopt them as the vocabulary matures."""
    for kind in ("imported", "derived"):
        payload = CalculationWithResultsPayload(
            **_base_calc_kwargs(
                parameters_json={"tckdb_origin": {"origin_kind": kind}}
            )
        )
        parsed = CalculationOriginMetadata.model_validate(
            payload.parameters_json["tckdb_origin"]
        )
        assert parsed.origin_kind.value == kind
