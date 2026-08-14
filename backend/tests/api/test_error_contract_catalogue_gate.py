"""Position says a token *means* to be a code; the catalogue says it is one.

#159 narrowed code promotion to the code position -- the start of a
message, optionally behind a framework wrapper -- because position is how a
raiser declares a code. That killed the ``existing_calculation_id``
fabrication, where a field path ended a sentence. It could not kill the
other half of the problem, because a code and a function name are the same
shape and both get written first:

    raise ValueError("create_applied_group_additivity: thermo_id does not ...")
    raise ValueError("keyset_predicate: at least one sort key is required.")

Both are the house style ``f"{context}: <prose>"`` with the enclosing
function as ``context``, and both were published as though a client could
branch on them.

So promotion now also consults :mod:`app.api.code_catalogue`, which records
per code *by which mechanism* it reaches the body. A leading token is
promoted only where the catalogue lists that code as
:data:`~app.api.code_catalogue.Surface.message_prefix`. The two above are
catalogued as :data:`~app.api.code_catalogue.Surface.accidental_prefix` --
the catalogue's own word for "not a code" -- so the honest record of the
defect is what stops it reaching a client.

What this file exists to prevent
--------------------------------
#159 refused to gate promotion on a list, because forgetting to register a
new code would silently degrade it to ``validation_error``. Three things
answer that, and only the third lives here:

* ``test_api_code_catalogue.py::test_every_raise_site_code_is_catalogued``
  statically scans every ``raise`` and demands an entry (verified by
  mutation: a new ``raise ValueError("some_new_code: ...")`` fails it as a
  plain literal, as an f-string, and when raised inside a helper);
* the runtime observer fails the test that *emits* an uncatalogued code,
  which is what catches a code interpolated into the code position from a
  variable -- the six ``*_handle_conflict`` codes are exactly that shape and
  were found by the observer and by nothing else;
* and these tests pin the dependency itself, so the gate cannot be widened
  back to "any leading token" or narrowed to nothing without a red test.

Every assertion below runs over something non-empty and is stated in both
directions, because a gate that can only say yes -- or only say no -- passes
while proving nothing.
"""

from __future__ import annotations

import pytest

from app.api.code_catalogue import CATALOGUE, Surface
from app.api.error_contract import (
    MESSAGE_PREFIX_CODES,
    detail_code,
    validation_detail_code,
)
from app.services.scientific_read.keyset import keyset_predicate


def _promoted(message: str) -> str:
    return validation_detail_code(message, fallback="validation_error")


class TestTheGateIsTheCatalogue:
    """The set consulted is derived, not a second hand-written list."""

    def test_it_is_exactly_the_catalogue_message_prefix_codes(self):
        assert MESSAGE_PREFIX_CODES == {
            entry.code
            for entry in CATALOGUE
            if entry.surface is Surface.message_prefix
        }

    def test_it_is_large_enough_to_be_the_read_api(self):
        """A gate over a nearly-empty set would degrade the whole read API.

        Most read-API refusals declare their code the legacy way, as the
        leading ``"code: "`` of their own message, so this set has to stay
        the size of that surface. If it collapsed, every one of those codes
        would arrive as ``validation_error`` -- the failure #159 named.
        """
        assert len(MESSAGE_PREFIX_CODES) > 50, sorted(MESSAGE_PREFIX_CODES)

    def test_it_excludes_what_the_catalogue_calls_not_a_code(self):
        accidental = {
            entry.code
            for entry in CATALOGUE
            if entry.surface is Surface.accidental_prefix
        }
        assert not (accidental & MESSAGE_PREFIX_CODES), sorted(
            accidental & MESSAGE_PREFIX_CODES
        )


class TestNoCataloguedCodeIsLost:
    """The regression that matters: promotion must keep every real code."""

    @pytest.mark.parametrize("code", sorted(MESSAGE_PREFIX_CODES))
    def test_every_catalogued_message_prefix_code_is_still_promoted(self, code):
        """One case per code, so a loss names the code that was lost.

        The prose deliberately contains no second ``token: ``, because a
        message carrying two tokens has always fallen back and this is not
        the test for that.
        """
        assert _promoted(f"{code}: the request was refused.") == code

    def test_a_code_still_survives_pydantic_wrapping_its_sentence(self):
        """The framework-wrapper path is gated the same way, not bypassed."""
        assert (
            validation_detail_code(
                [
                    {
                        "type": "value_error",
                        "loc": ["body"],
                        "msg": (
                            "Value error, pressure_alias_conflict: pressure and "
                            "the deprecated alias must agree."
                        ),
                    }
                ],
                fallback="request_validation_error",
            )
            == "pressure_alias_conflict"
        )


class TestAFunctionNameIsNotACode:
    """The defect, provoked from the real refusals rather than a copy."""

    def test_the_keyset_guard_no_longer_advertises_its_own_name(self):
        """``keyset_predicate`` guards an argument invariant, so it fires
        only on a backend bug -- and used to answer with a ``code`` naming
        the function that had the bug.

        Provoked from the real function so that rewording its message
        cannot make this test pass for the wrong reason.
        """
        with pytest.raises(ValueError) as raised:
            keyset_predicate([], [])
        message = str(raised.value)
        assert _promoted(message) == "validation_error"

    def test_the_length_guard_is_the_same_shape(self):
        with pytest.raises(ValueError) as raised:
            keyset_predicate([(object(), "asc")], [1, 2])
        assert _promoted(str(raised.value)) == "validation_error"

    def test_an_uncatalogued_token_in_the_code_position_is_not_promoted(self):
        """The general rule, on a token no catalogue entry can ever claim."""
        token = "zzz_uncatalogued_token"
        assert token not in MESSAGE_PREFIX_CODES
        assert _promoted(f"{token}: something went wrong.") == "validation_error"

    def test_a_field_path_is_still_not_promoted(self):
        """#159's case, still refused -- by position, before the catalogue."""
        assert (
            _promoted(
                "source_calculations[0].existing_calculation_id: refers to a "
                "calculation owned by a different species entry."
            )
            == "validation_error"
        )


class TestTheLegacyDetailPathIsGatedToo:
    """``detail_code`` reads English as well, and read it more loosely.

    Its character test does not even require an underscore, so before this
    change ``raise HTTPException(422, "manifest: ...")`` would have
    published ``code="manifest"``. Two of the five bare prefixes written in
    the tree (``manifest``, ``readyz``, ``startup``, ``status``,
    ``geometry_validation``) are one refactor away from an error body.
    """

    def test_a_catalogued_prefix_is_still_lifted(self):
        assert (
            detail_code("unknown_release: no such release.", fallback="http_404")
            == "unknown_release"
        )

    def test_an_uncatalogued_prefix_falls_back_to_the_status(self):
        assert (
            detail_code(
                "manifest: stored document hashes differently.",
                fallback="http_500",
            )
            == "http_500"
        )

    def test_a_declared_code_in_a_dict_detail_is_not_second_guessed(self):
        """A dict's ``code`` key is a declaration, not English.

        The client-version handshake names its code that way, and it is
        catalogued as ``detail_object`` rather than ``message_prefix``.
        Passing the dict branch through the same gate would drop it, which
        is why only the string branch is gated.
        """
        assert (
            detail_code(
                {"code": "tckdb_client_version_unsupported", "detail": "too old"},
                fallback="http_426",
            )
            == "tckdb_client_version_unsupported"
        )
        assert "tckdb_client_version_unsupported" not in MESSAGE_PREFIX_CODES
