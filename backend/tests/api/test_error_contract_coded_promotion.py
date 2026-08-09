"""The seam that turns a raised code into the envelope's ``code`` field.

The end-to-end proofs in ``test_api_scientific_rejection_codes.py`` show
that twenty specific codes arrive. These pin the *rules* they arrive by,
including the two that are decisions rather than plumbing: a refusal may
attach a code without moving its message, and a failure carrying two
different codes reports neither.
"""

from __future__ import annotations

from pydantic import BaseModel, ValidationError, model_validator
from tckdb_schemas.coded_error import CodedValidationError

from app.api.error_contract import (
    CodedValueError,
    error_envelope,
    validation_detail_code,
    validation_detail_context,
)


class TestTheMessageDoesNotMove:
    def test_message_prefix_false_leaves_the_prose_exactly_as_written(self):
        """The additive guarantee, stated once where it is implemented.

        Attaching a code to an existing refusal must not change a byte of
        what a client already receives — that is what lets ``code`` be
        introduced without a deprecation for anyone matching prose.
        """
        prose = "Reaction is not element-balanced (reaction_mass_balance_failed)."
        exc = CodedValueError(
            "reaction_mass_balance_failed", prose, message_prefix=False
        )
        assert str(exc) == prose
        assert error_envelope(str(exc), code=exc.code, fallback_code="x") == {
            "code": "reaction_mass_balance_failed",
            "detail": prose,
            "context": {},
        }

    def test_the_default_still_prefixes(self):
        """The read API's published details carry ``"code: message"``.

        ``unsupported_filter: filter(s) ['inchi'] are not supported by ...``
        is a shape client tests already pin, so the default must not move
        either.
        """
        exc = CodedValueError("unsupported_filter", "filter is unavailable")
        assert str(exc) == "unsupported_filter: filter is unavailable"


class _Coded(BaseModel):
    value: int

    @model_validator(mode="after")
    def check(self):
        if self.value < 0:
            raise CodedValidationError(
                "negative_value",
                "value must not be negative.",
                context={"value": self.value},
                message_prefix=False,
            )
        return self


def _errors(value: int) -> list[dict]:
    try:
        _Coded(value=value)
    except ValidationError as exc:
        return exc.errors()
    raise AssertionError("expected a ValidationError")


class TestPromotionIsTypedNotTextual:
    def test_a_code_is_read_from_the_exception_not_the_message(self):
        """Nothing about the sentence is parsed.

        The message here contains no code at all, in any spelling. If the
        promotion were textual this would fall back to the generic code,
        which is exactly what every chemistry refusal used to do.
        """
        details = _errors(-1)
        assert "negative_value" not in str(details[0]["msg"])
        assert validation_detail_code(details, fallback="validation_error") == (
            "negative_value"
        )

    def test_the_structured_context_comes_with_it(self):
        assert validation_detail_context(_errors(-3)) == {"value": -3}

    def test_a_plain_value_error_still_falls_back(self):
        class _Plain(BaseModel):
            value: int

            @model_validator(mode="after")
            def check(self):
                raise ValueError("something went wrong.")

        try:
            _Plain(value=1)
        except ValidationError as exc:
            details = exc.errors()
        assert validation_detail_code(details, fallback="validation_error") == (
            "validation_error"
        )
        assert validation_detail_context(details) == {}


def _failure(code: str, field: str) -> dict:
    """One entry of a Pydantic ``errors()`` list, carrying a coded cause."""
    return {
        "type": "value_error",
        "loc": ["body", field],
        "msg": f"Value error, {field} is wrong.",
        "ctx": {
            "error": CodedValidationError(
                code, f"{field} is wrong.", context={"field": field}
            )
        },
    }


class TestTwoCodesReportNeither:
    def test_two_failures_fall_back_rather_than_pick_one(self):
        """Naming one of two contradictions would deny the other.

        A payload that is wrong in two ways is two things to fix. The
        envelope says so by declining to name either; the ``detail`` list
        still carries both, so nothing is hidden — only the single-code
        summary is withheld, because there is no single code.
        """
        failures = [_failure("a_code", "alpha"), _failure("b_code", "beta")]
        assert (
            validation_detail_code(failures, fallback="request_validation_error")
            == "request_validation_error"
        )
        assert validation_detail_context(failures) == {}

    def test_one_failure_is_promoted(self):
        """Guard the guard: the case above must fail for the right reason."""
        failures = [_failure("a_code", "alpha")]
        assert (
            validation_detail_code(failures, fallback="request_validation_error")
            == "a_code"
        )
        assert validation_detail_context(failures) == {"field": "alpha"}
