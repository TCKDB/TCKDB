"""The seam that turns a raised code into the envelope's ``code`` field.

The end-to-end proofs in ``test_api_scientific_rejection_codes.py`` show
that twenty specific codes arrive. These pin the *rules* they arrive by,
including the two that are decisions rather than plumbing: a refusal may
attach a code without moving its message, and a request that failed more
than once reports no single code — whether the failures carry two
different codes or the same one twice. ``context`` follows that decision
rather than making its own, because for a while it made its own and the
two disagreed (#236).
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


class TestOnlyTheCodePositionDeclaresACode:
    """#161: a field path that ends in a field name is not a code.

    The house style for a workflow refusal is
    ``raise ValueError(f"{context}: <prose>")`` with ``context`` a field
    path. Before this rule, a path ending in ``existing_calculation_id``
    put that field name in front of a colon and the envelope advertised it
    as ``code`` — a string in no register, in no generated client
    constant, that changes when somebody renames a field. That is worse
    than an uncoded error: ``validation_error`` is honestly generic, a
    fabricated code looks real.
    """

    def test_a_field_path_is_not_promoted(self):
        message = (
            "source_calculations[0].existing_calculation_id: refers to a "
            "calculation owned by a different species entry."
        )
        assert (
            validation_detail_code(message, fallback="validation_error")
            == "validation_error"
        )

    def test_a_token_the_sentence_merely_mentions_is_not_promoted(self):
        """The same defect, elsewhere: prose that names a column.

        ``app/services/reaction_resolution.py`` refuses a reaction whose
        family disagrees with the resolved one, and the sentence names the
        column. That is prose about a field, not a declaration of a code.
        """
        message = (
            "Resolved reaction already has a different reaction_family: "
            "H_Abstraction != Disproportionation."
        )
        assert (
            validation_detail_code(message, fallback="validation_error")
            == "validation_error"
        )

    def test_a_code_written_in_the_code_position_is_still_promoted(self):
        """The regression guard for the narrowing.

        Every read-API refusal declares its code the legacy way, as the
        leading ``"code: "`` of its own message. Twenty-four distinct codes
        reach clients only through this path; tightening the rule must not
        cost a single one of them.
        """
        message = "invalid_handle: 'not-a-handle' is not a <prefix>_<body> public ref"
        assert (
            validation_detail_code(message, fallback="validation_error")
            == "invalid_handle"
        )

    def test_a_code_survives_pydantic_wrapping_its_sentence(self):
        """A validator's ``ValueError`` reaches ``errors()`` as
        ``"Value error, <message>"``. The code is still the first thing
        the *raiser* wrote, so it is still in the code position.

        Deliberately built without ``ctx``, and that is the whole point of
        the case. Pydantic v2 *also* preserves the raw exception in
        ``ctx["error"]``, whose ``str()`` carries no wrapper, so every live
        route recovers its code from there and would keep working if the
        prefix list were emptied — measured, not assumed (mutation M2b).
        The prefix stripping is what stops the whole read API's twenty-four
        codes depending on Pydantic continuing to hand over an exception
        object: strip the context anywhere in front of this function and
        ``msg`` is all that is left.
        """
        details = [
            {
                "type": "value_error",
                "loc": ["body"],
                "msg": (
                    "Value error, pressure_alias_conflict: pressure_bar and "
                    "deprecated pressure must agree."
                ),
            }
        ]
        assert (
            validation_detail_code(details, fallback="request_validation_error")
            == "pressure_alias_conflict"
        )

    def test_two_tokens_still_fall_back_exactly_as_before(self):
        """The narrowing is one-way, and this is what pins that.

        ``invalid_pagination: limit_too_large: …`` carries two tokens and
        has always fallen back. Judging ambiguity *after* the position
        filter would leave one survivor and promote it — turning a
        published ``validation_error`` into a new code. Ambiguity is
        therefore judged on the unfiltered set: the rule can only remove a
        code, never add one.

        The message below is now a synthetic example rather than a
        quotation: ``validate_pagination`` was the one place that wrote
        two tokens, and it no longer does — the second token was the
        honest name for what went wrong, so it became the code and
        ``invalid_pagination`` kept only the malformed cases. The *rule*
        is unchanged and is what this pins. That no real message carries
        two tokens is a separate, statically checked claim; see
        ``test_error_contract_catalogue_gate.py::TestOneMessageDeclaresOneCode``.
        """
        message = "invalid_pagination: limit_too_large: limit must be <= 200 (got 500)"
        assert (
            validation_detail_code(message, fallback="validation_error")
            == "validation_error"
        )


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


class TestTwoFailuresWithOneCodeReportNeither:
    """#236: the two functions used to ask *different* questions.

    ``validation_detail_code`` falls back on more than one **failure**;
    ``validation_detail_context`` used to fall back only on more than one
    distinct **code**. Two failures carrying the same code slipped
    between them: the envelope reported the generic
    ``request_validation_error`` beside a populated ``context``
    describing one of the failures it had just declined to name — and
    ``merged.update()`` meant that for any key both errors set, the last
    one silently won and the first fact was gone with nothing said.

    ``TestTwoCodesReportNeither`` above never caught it because its two
    failures carry *different* codes, which trips the old condition too.
    The distinguishing case is the same code twice, and it is only rare
    until #219 moves ~24 refusals into ``model_validator(mode="after")``,
    which accumulates failures rather than stopping at the first.
    """

    def test_two_failures_sharing_a_code_report_no_context(self):
        """The code falls back, so the facts must go with it.

        Asserting emptiness alone would be satisfied by a
        ``validation_detail_context`` that always returned ``{}``, so
        every assertion here is paired with
        :meth:`test_a_single_failure_still_carries_its_facts` below,
        which uses the same ``_failure`` builder and the same code.
        """
        failures = [_failure("a_code", "alpha"), _failure("a_code", "beta")]
        assert {
            error["ctx"]["error"].code for error in failures
        } == {"a_code"}, "the premise: one distinct code across two failures"
        assert (
            validation_detail_code(failures, fallback="request_validation_error")
            == "request_validation_error"
        )
        assert validation_detail_context(failures) == {}

    def test_a_single_failure_still_carries_its_facts(self):
        """The other direction, with the identical code and builder.

        This is what makes the assertion above a statement about *two
        failures* rather than about ``a_code`` or about the function
        having been emptied out.
        """
        failures = [_failure("a_code", "alpha")]
        assert (
            validation_detail_code(failures, fallback="request_validation_error")
            == "a_code"
        )
        assert validation_detail_context(failures) == {"field": "alpha"}

    def test_the_later_failures_facts_are_not_smuggled_through(self):
        """Named separately, because the old behaviour was *specifically*
        this: ``merged.update()`` left the second failure's ``field`` in
        place of the first's. A reinstated merge fails here reporting the
        value it kept, rather than only failing an ``== {}`` whose
        message says nothing about which fact leaked.

        Only the merge can produce a non-empty result in this shape --
        the code falls back before any context is looked at -- so this
        does not also guard "picks a winner".
        ``TestOneCodeCarriedByTwoDeclaredErrors`` below does, on the one
        shape where picking a winner is reachable.
        """
        failures = [_failure("a_code", "alpha"), _failure("a_code", "beta")]
        context = validation_detail_context(failures)
        assert context.get("field") != "beta", (
            "the later failure's facts reached the envelope under a code "
            "naming neither failure — this is the #236 defect"
        )

    def test_the_envelope_a_client_reads_is_consistent(self):
        """Assembled the way ``errors.py`` assembles it.

        The two functions are only ever read together, through
        ``error_envelope``. Pinning the pair at the seam catches a future
        change that keeps each function defensible alone while making the
        body they build disagree with itself.
        """
        failures = [_failure("a_code", "alpha"), _failure("a_code", "beta")]
        envelope = error_envelope(
            failures,
            code=validation_detail_code(
                failures, fallback="request_validation_error"
            ),
            context=validation_detail_context(failures),
            fallback_code="request_validation_error",
        )
        assert envelope["code"] == "request_validation_error"
        assert envelope["context"] == {}
        assert len(envelope["detail"]) == 2, (
            "both failures must still be reported in full; withholding the "
            "summary is not the same as hiding a failure"
        )


class TestOneCodeCarriedByTwoDeclaredErrors:
    """A code *is* promoted, and two declared errors claim it.

    This is the only shape in which the no-merge decision is reachable,
    and it is deliberately contrived: Pydantic puts one exception in one
    ``ctx["error"]``, so through ``ValidationError.errors()`` the number
    of declared errors equals the number of failures, and two failures
    make :func:`validation_detail_code` fall back before any context is
    consulted. Nesting the two under a single outer entry is what gets
    past the fallback.

    It is tested rather than deleted because the branch is what stops a
    silent winner from reappearing. #219 changes how failures accumulate,
    and a defensive branch nothing exercises is a branch that quietly
    stops working — which is the same defect one layer down as the one
    this file is about.
    """

    @staticmethod
    def _two_errors_under_one_entry() -> list:
        return [[_failure("a_code", "alpha"), _failure("a_code", "beta")]]

    def test_the_code_is_promoted_here(self):
        """The premise. Without it the context assertion proves nothing.

        If this ever falls back, the test below passes for the wrong
        reason -- the early ``{}`` return -- and stops guarding the
        merge at all.
        """
        assert (
            validation_detail_code(
                self._two_errors_under_one_entry(),
                fallback="request_validation_error",
            )
            == "a_code"
        )

    def test_two_contexts_for_one_code_report_neither(self):
        """No merge, and no winner picked from the two."""
        context = validation_detail_context(self._two_errors_under_one_entry())
        assert context == {}, (
            "one of two declared errors' facts was reported as though it "
            f"were the whole story: {context}"
        )

    def test_one_context_for_one_code_is_still_reported(self):
        """The other direction, in the same nested shape.

        Same nesting, same code, one declared error instead of two — so
        the assertion above is about the *count*, not about the nesting
        defeating :func:`_declared_errors`.
        """
        nested = [[_failure("a_code", "alpha")]]
        assert (
            validation_detail_code(nested, fallback="request_validation_error")
            == "a_code"
        )
        assert validation_detail_context(nested) == {"field": "alpha"}
