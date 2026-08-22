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
:data:`~app.api.code_catalogue.Surface.message_prefix`. The two above were
catalogued as :data:`~app.api.code_catalogue.Surface.accidental_prefix` --
the catalogue's own word for "not a code" -- so the honest record of the
defect is what stopped it reaching a client.

#178 then fixed the messages themselves. Both now say what went wrong
instead of naming the function it went wrong in, so neither token occupies
the code position any more and neither is catalogued at all: a message with
no leading token is not an accidental prefix, it is just a message. The two
raises above are kept in this docstring as the shape to recognise, and the
tests below are still provoked from the real functions -- because the gate
is what stops the *next* one, and the next one will be written the same
way.

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

#192 audited that claim rather than trusting it, and it was not true of
four assertions here: three asserted an *absence* over a set nothing
required to be populated, and ``_assert_no_token_in_the_code_position``
ran a pattern nothing required to match. Each now states its floor where
it makes its claim, rather than relying on a neighbour that happens to
share a constant. The floors are measured, and the measurements are in
the messages so the next reader can tell a real shrinkage from a broken
scan.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from app.api.code_catalogue import CATALOGUE, Surface, catalogued_codes
from app.api.error_contract import (
    _NESTED_CODE_PATTERN,
    MESSAGE_PREFIX_CODES,
    detail_code,
    validation_detail_code,
)
from app.services.scientific_read.keyset import keyset_predicate

REPO_ROOT = Path(__file__).resolve().parents[3]
SCANNED_ROOTS = (
    REPO_ROOT / "backend" / "app",
    REPO_ROOT / "schemas" / "python" / "tckdb-schemas" / "tckdb_schemas",
)
SKIPPED = ("/tests/", "/importers/")
_BARE_CODE = re.compile(r"^[a-z][a-z0-9_]*_[a-z0-9_]+$")
_CODE_POSITION = re.compile(r"^[a-z][a-z0-9_]*_[a-z0-9_]+: ")


def _promoted(message: str) -> str:
    return validation_detail_code(message, fallback="validation_error")


#: A message that must be read as opening with a token in the code
#: position. It is the exact shape #178 reworded out of ``keyset.py`` --
#: see this module's docstring -- and it exists so that
#: :func:`_opens_with_a_token` can be caught saying no to it.
_A_REFUSAL_THAT_DOES_OPEN_WITH_A_TOKEN = (
    "keyset_predicate: at least one sort key is required."
)

#: The shortest a real refusal gets. Measured against the two this file
#: provokes, which are 11 and 17 words; the floor is well under both and
#: exists only to reject the degenerate input, not to police wording.
_MINIMUM_WORDS_IN_A_REFUSAL = 5


def _opens_with_a_token(message: str) -> bool:
    """Does *message* put a token in the code position?

    One expression, used for both the positive control and the assertion
    below, so that blinding the detector cannot blind only the half that
    says no. Two spellings of the same question drift apart, and the
    drift is invisible: both stay green.
    """
    return bool(_CODE_POSITION.match(message))


def _assert_no_token_in_the_code_position(message: str) -> None:
    """The message must not open with ``some_token: ``.

    Asserting only that a message *falls back* would be satisfied by the
    gate alone: re-adding ``keyset_predicate: `` in front of the prose
    would still degrade to ``validation_error``, because the token is
    uncatalogued, and the test that was meant to hold the wording would
    stay green. #177 is the same lesson one layer down -- a guard that
    passes for a second reason stops guarding the first.

    #192: this helper used to be that same failure one layer down again.
    It matched a pattern and asserted the match was empty, so a blinded
    pattern made it report success having read nothing -- measured, both
    callers green with the detector replaced by one that matches nothing.
    The file did go red, but in ``TestOneMessageDeclaresOneCode``, whose
    floor happens to share this module constant; that coupling is an
    accident of spelling, not a guard, and it is how the defect surfaced
    at all. So the detector is now run against a message it must accept
    before it is trusted to reject one, and the message has to be a
    sentence -- ``""`` opens with no token either.
    """
    assert _opens_with_a_token(_A_REFUSAL_THAT_DOES_OPEN_WITH_A_TOKEN), (
        "the code-position detector no longer recognises "
        f"{_A_REFUSAL_THAT_DOES_OPEN_WITH_A_TOKEN!r}, which is the exact "
        "shape it exists to reject. Every assertion it makes below is "
        "therefore vacuous: it would pass over a refusal that had gone "
        "back to naming the function it was raised in."
    )
    assert len(message.split()) >= _MINIMUM_WORDS_IN_A_REFUSAL, (
        f"the refusal is {message!r}, which is too short to be the prose "
        "this guard is written over. A message with no words opens with no "
        "token, so the assertion below would pass without examining a "
        "refusal at all."
    )
    assert not _opens_with_a_token(message), (
        f"the refusal opens with a token in the code position: {message!r}. "
        "A message says what went wrong; it does not name the function it "
        "went wrong in, and a leading token is read as a code by anyone "
        "who has seen the read API's convention."
    )


def _sources() -> list[tuple[str, ast.Module]]:
    parsed: list[tuple[str, ast.Module]] = []
    for root in SCANNED_ROOTS:
        for path in sorted(root.rglob("*.py")):
            if any(skip in str(path) for skip in SKIPPED):
                continue
            parsed.append(
                (str(path.relative_to(REPO_ROOT)), ast.parse(path.read_text()))
            )
    return parsed


def _helpers_that_raise_a_parameter_as_the_code(
    sources: list[tuple[str, ast.Module]],
) -> dict[str, str]:
    """``function name -> parameter`` for ``raise X(f"{param}: ...")``.

    The shape the catalogue's raise-site scan cannot read, because the code
    is not a literal anywhere near the ``raise``.
    """
    helpers: dict[str, str] = {}
    for _path, tree in sources:
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            parameters = {
                argument.arg
                for argument in [
                    *node.args.args,
                    *node.args.kwonlyargs,
                    *node.args.posonlyargs,
                ]
            }
            for inner in ast.walk(node):
                if not isinstance(inner, ast.Raise) or not isinstance(
                    inner.exc, ast.Call
                ):
                    continue
                for argument in inner.exc.args:
                    if not isinstance(argument, ast.JoinedStr) or len(
                        argument.values
                    ) < 2:
                        continue
                    head, following = argument.values[0], argument.values[1]
                    if (
                        isinstance(head, ast.FormattedValue)
                        and isinstance(head.value, ast.Name)
                        and head.value.id in parameters
                        and isinstance(following, ast.Constant)
                        and isinstance(following.value, str)
                        and following.value.startswith(": ")
                    ):
                        helpers[node.name] = head.value.id
    return helpers


def _codes_passed_to(helpers: dict[str, str]) -> dict[str, str]:
    """``code -> call site`` for every literal handed to such a parameter."""
    found: dict[str, str] = {}
    for path, tree in _sources():
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
            parameter = helpers.get(name or "")
            if parameter is None:
                continue
            for keyword in node.keywords:
                if keyword.arg != parameter:
                    continue
                value = keyword.value
                if isinstance(value, ast.Constant) and isinstance(value.value, str):
                    if _BARE_CODE.fullmatch(value.value):
                        found.setdefault(value.value, path)
    return found


class TestTheGateIsTheCatalogue:
    """The set consulted is derived, not a second hand-written list."""

    def test_it_is_exactly_the_catalogue_message_prefix_codes(self):
        """Both sides are read from ``CATALOGUE``, so both empty is equal.

        #192: an equality between two derivations of one source is
        satisfied when that source is empty, and this is the assertion
        that says the gate *is* the catalogue. The floor is repeated here
        rather than left to ``test_it_is_large_enough_to_be_the_read_api``
        below, in the same style as the ``>= 6`` floor that appears in
        both of ``TestTheShapeTheRaiseSiteScanCannotRead``'s assertions:
        a test that can only fail because of its neighbour is a test that
        stops working when its neighbour moves.
        """
        derived = {
            entry.code
            for entry in CATALOGUE
            if entry.surface is Surface.message_prefix
        }
        assert len(derived) > 50, (
            f"only {len(derived)} codes carry Surface.message_prefix, so this "
            "equality is comparing two nearly-empty sets and would hold "
            "however badly the derivation had broken"
        )
        assert MESSAGE_PREFIX_CODES == derived

    def test_it_is_large_enough_to_be_the_read_api(self):
        """A gate over a nearly-empty set would degrade the whole read API.

        Most read-API refusals declare their code the legacy way, as the
        leading ``"code: "`` of their own message, so this set has to stay
        the size of that surface. If it collapsed, every one of those codes
        would arrive as ``validation_error`` -- the failure #159 named.
        """
        assert len(MESSAGE_PREFIX_CODES) > 50, sorted(MESSAGE_PREFIX_CODES)

    def test_it_excludes_every_code_that_arrives_some_other_way(self):
        """Promotion is for the message-prefix surface and no other.

        Written over *every* other surface rather than over
        ``accidental_prefix`` alone, which is what it used to say. #178
        reworded the two accidental prefixes out of existence, so that
        set is now empty and the assertion it satisfied would have gone
        on passing while checking nothing -- the failure shape this file
        is otherwise careful about. The surfaces below hold 70-odd
        entries between them, and the count says so.
        """
        other = {
            entry.code
            for entry in CATALOGUE
            if entry.surface is not Surface.message_prefix
        }
        assert len(other) > 50, sorted(other)
        assert not (other & MESSAGE_PREFIX_CODES), sorted(other & MESSAGE_PREFIX_CODES)

    def test_a_function_name_is_in_no_surface_at_all(self):
        """The two #178 reworded are not catalogued under any surface.

        Not ``accidental_prefix`` either: a message that no longer leads
        with the token has no token in the code position, so there is
        nothing to classify. An entry reappearing means a refusal has
        gone back to naming the function it was raised in.
        """
        catalogued = {entry.code for entry in CATALOGUE}
        assert len(catalogued) > 100, (
            f"only {len(catalogued)} codes are catalogued at all (measured: "
            "149). Absence from a set this small says nothing about whether "
            "these two names came back -- the assertions below would pass "
            "over an empty catalogue."
        )
        for name in ("create_applied_group_additivity", "keyset_predicate"):
            assert name not in catalogued
            assert name not in MESSAGE_PREFIX_CODES


class TestNoCataloguedCodeIsLost:
    """The regression that matters: promotion must keep every real code."""

    def test_every_catalogued_message_prefix_code_is_still_promoted(self):
        """Written as a loop, not a ``parametrize``, and that is a finding.

        Parametrising over ``MESSAGE_PREFIX_CODES`` reads better and names
        the lost code in the test id -- but pytest turns an empty parameter
        set into a *skip*, so the mutation that empties the derivation
        (measured: ``frozenset()``) made the one test that checks nothing is
        lost report ``1 skipped`` instead of failing. A vacuous pass dressed
        as a skip is the exact shape this repository keeps finding, so the
        loop form is used and the count is asserted.

        The prose deliberately carries no second ``token: ``: a message with
        two tokens has always fallen back, and that is a different test.
        """
        lost = [
            code
            for code in sorted(MESSAGE_PREFIX_CODES)
            if _promoted(f"{code}: the request was refused.") != code
        ]
        assert not lost, f"these codes stopped being promoted: {lost}"
        assert len(MESSAGE_PREFIX_CODES) > 50, (
            f"only {len(MESSAGE_PREFIX_CODES)} codes were checked, so this "
            "test passed without exercising the read API's code surface"
        )

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
        cannot make this test pass for the wrong reason. Two things have
        to be true for it now: the message no longer leads with the
        function's name (#178), *and* a leading token would not be
        promoted even if it did (#164). Either alone would leave the
        other free to regress.
        """
        with pytest.raises(ValueError) as raised:
            keyset_predicate([], [])
        message = str(raised.value)
        assert _promoted(message) == "validation_error"
        _assert_no_token_in_the_code_position(message)

    def test_the_length_guard_is_the_same_shape(self):
        with pytest.raises(ValueError) as raised:
            keyset_predicate([(object(), "asc")], [1, 2])
        message = str(raised.value)
        assert _promoted(message) == "validation_error"
        _assert_no_token_in_the_code_position(message)

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


class TestTheShapeTheRaiseSiteScanCannotRead:
    """The net this change would otherwise have removed.

    Before this change, an uncatalogued code in the code position was
    *promoted*, so the runtime observer saw it in the body and failed the
    test that produced it. That was the only net covering a code
    interpolated into the code position from a parameter, because the
    catalogue's static scan reads literals at ``raise`` sites and this shape
    has no literal there.

    Gating promotion closes that net: the token now degrades to
    ``validation_error``, the body carries no unknown code, and the observer
    has nothing to see. Measured, not reasoned about -- renaming
    ``conflict_code="level_of_theory_handle_conflict"`` to ``..._clash``
    leaves both the raise-site scan and the observer green, where before
    this change the observer failed the test that emitted it.

    So the net is restored here, statically and in the same shape: find the
    helpers that raise a *parameter* as the code, then require every literal
    handed to that parameter to be catalogued. ``reconcile_id_ref`` is the
    one such helper today and the six ``*_handle_conflict`` codes are its
    arguments; five of the six are never emitted by any test, so the
    observer could not have covered them either.
    """

    def test_the_scan_finds_the_helper_it_is_written_for(self):
        """An empty scan would make the next assertion prove nothing."""
        helpers = _helpers_that_raise_a_parameter_as_the_code(_sources())
        assert "reconcile_id_ref" in helpers, sorted(helpers)
        assert helpers["reconcile_id_ref"] == "conflict_code"

    def test_every_code_minted_from_a_parameter_is_catalogued(self):
        """Otherwise the gate would silently degrade a real code.

        This is the failure #159 refused to risk. It is checked rather than
        argued about, and the check names what it saw.
        """
        helpers = _helpers_that_raise_a_parameter_as_the_code(_sources())
        codes = _codes_passed_to(helpers)
        assert len(codes) >= 6, f"the scan found only {sorted(codes)}"
        known = catalogued_codes()
        missing = {code: where for code, where in codes.items() if code not in known}
        assert not missing, (
            "these codes reach the code position but no catalogue entry "
            f"claims them, so promotion will drop them: {missing}"
        )

    def test_they_are_catalogued_as_arriving_by_this_mechanism(self):
        """Catalogued is not enough -- the surface has to be right too.

        A code listed under any other surface would still be dropped by the
        gate, so the containment that matters is against the promotable set,
        not against the catalogue as a whole.
        """
        helpers = _helpers_that_raise_a_parameter_as_the_code(_sources())
        codes = _codes_passed_to(helpers)
        assert len(codes) >= 6, f"the scan found only {sorted(codes)}"
        not_promotable = sorted(set(codes) - MESSAGE_PREFIX_CODES)
        assert not not_promotable, (
            "these codes are written in the code position but are not "
            f"catalogued as message_prefix, so they will not be promoted: "
            f"{not_promotable}"
        )


def _static_message(node: ast.AST) -> str | None:
    """The static text of a string expression, interpolations blanked.

    An f-string's ``{...}`` holes are replaced by ``\\ufffd`` rather than
    dropped: dropping them would let the text on either side join and
    invent a token that the runtime string never contains, and
    ``\\ufffd`` matches neither half of the code pattern, so it cannot
    create one or bridge across one. What it cannot do is see a token a
    *value* supplies at runtime -- see the class docstring for why that
    is a known and bounded gap rather than an oversight.
    """
    if isinstance(node, ast.Constant):
        return node.value if isinstance(node.value, str) else None
    if isinstance(node, ast.JoinedStr):
        parts: list[str] = []
        for value in node.values:
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                parts.append(value.value)
            else:
                parts.append("�")
        return "".join(parts)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = _static_message(node.left)
        right = _static_message(node.right)
        return None if left is None or right is None else left + right
    return None


def _messages_opening_with_a_promotable_code() -> list[tuple[str, int, str]]:
    """``(path, line, text)`` for every message that declares a code.

    Docstrings are excluded: this module and its neighbours quote real
    refusal messages to explain them, and a quotation is not a message a
    client can receive.
    """
    longest: dict[tuple[str, int], str] = {}
    for path, tree in _sources():
        documentation = {
            id(node.value)
            for node in ast.walk(tree)
            if isinstance(node, ast.Expr)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        }
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Constant, ast.JoinedStr, ast.BinOp)):
                continue
            if id(node) in documentation:
                continue
            text = _static_message(node)
            if text is None:
                continue
            match = _CODE_POSITION.match(text)
            if match is None:
                continue
            token = text.split(":", 1)[0]
            if token not in MESSAGE_PREFIX_CODES:
                continue
            # ``ast.walk`` yields a concatenation *and* its first operand,
            # both of which open with the code. Keeping the longest text
            # per site reports the message the raiser actually built,
            # rather than the same defect twice with half of it missing.
            key = (path, node.lineno)
            if len(text) > len(longest.get(key, "")):
                longest[key] = text
    return [(path, line, text) for (path, line), text in sorted(longest.items())]


class TestOneMessageDeclaresOneCode:
    """A message that names two codes cannot honestly report either.

    ``validation_detail_code`` judges ambiguity on the *unfiltered*
    candidate set, deliberately: a message carrying two ``token: ``
    tokens fell back to ``validation_error`` before the position rule
    existed and must keep falling back, so that tightening the rule can
    never *add* a code to a published response. That is the right rule.

    Its consequence is that a message which opens with a real code and
    then spells a second one mid-sentence silently publishes neither.
    Two sites in the tree did exactly that, both in
    ``validate_pagination``::

        "invalid_pagination: limit_too_large: limit must be <= 200 (got …)"

    Both were catalogued, both were exported to the client enum, and
    neither could ever reach a ``code`` field. The repair was to say what
    went wrong once -- the second token was the honest name all along, so
    it became the code -- and this is the gate that keeps the class
    closed. It lands green because the two sites it was written for are
    fixed in the same change; a gate that lands red gets disabled rather
    than satisfied.

    What it cannot see, stated so it is not mistaken for coverage: a
    token supplied by an interpolated *value* at runtime (a
    ``resource_name`` that happens to be ``foo_bar`` immediately before a
    colon), and a message assembled across statements. The runtime
    observer does not cover those either -- a degraded body carries a
    perfectly catalogued ``validation_error`` -- so the residual is real.
    It is bounded by the fact that every such message is built here, from
    literals, in one function per refusal.
    """

    def test_the_scan_sees_the_messages_it_is_written_over(self):
        """An empty scan would make the next assertion prove nothing.

        The floor named ``limit_too_large`` and ``offset_too_large``
        until 2026-08-18, when the cap family became
        ``Shape.relationship`` and both moved to
        ``Surface.coded_exception`` so they could carry their cap in
        ``context``. They are no longer in ``MESSAGE_PREFIX_CODES``, so
        this scan -- which reads only that surface -- correctly stops
        seeing them, and naming them here would pin a spelling the tree
        no longer has.

        Re-pointed rather than dropped: the floor still names three
        concrete codes that must be visible, and two of them are still
        raised from ``validate_pagination``'s own module, so a scan that
        stops reading ``scientific_read/common.py`` still fails here.
        """
        messages = _messages_opening_with_a_promotable_code()
        assert len(messages) > 40, (
            f"only {len(messages)} coded messages found; the scan has stopped "
            "reading the tree and the assertion below is vacuous"
        )
        codes = {text.split(":", 1)[0] for _path, _line, text in messages}
        for expected in (
            "invalid_pagination",
            "client_sort_not_supported",
            "composed_search_pagination_changed",
        ):
            assert expected in codes, sorted(codes)

    def test_no_coded_message_names_a_second_code(self):
        """The floor is repeated here, not borrowed from the test above.

        #192: this is an absence asserted over a scan, and the scan's own
        floor lives in a different test. Carrying it here too costs a line
        and means the assertion cannot be made vacuous by an edit that
        leaves its neighbour untouched.
        """
        messages = _messages_opening_with_a_promotable_code()
        assert len(messages) > 40, (
            f"only {len(messages)} coded messages found (measured: 125), so "
            "there is nothing to be ambiguous and this test proves nothing"
        )
        ambiguous = [
            (path, line, text)
            for path, line, text in messages
            if len(set(_NESTED_CODE_PATTERN.findall(text))) > 1
        ]
        assert not ambiguous, (
            "these messages open with a catalogued code and then name "
            "another one, so validation_detail_code refuses to promote "
            "either and the client receives validation_error: "
            + "; ".join(f"{path}:{line} {text!r}" for path, line, text in ambiguous)
        )

    def test_the_scan_would_notice_a_second_token(self):
        """The assertion above passes over a set that is empty today.

        So the detector is provoked directly: the exact shape that was in
        the tree must be classified as ambiguous, and must actually
        degrade when the envelope reads it. Without this the gate could be
        broken -- a pattern that matches nothing -- and stay green.

        The historical shape is still asserted, because it is still the
        shape the gate exists for. What moved is the *repaired* half: as
        of 2026-08-18 ``limit_too_large`` is a ``coded_exception``
        carrying its cap in ``context``, so it is no longer promotable
        out of a sentence at all and ``_promoted`` of it is now
        ``validation_error`` by design rather than by defect. Asserting
        the old value would be asserting the gate's opposite. A code that
        *is* still ``message_prefix`` is used for the positive half, so
        the detector is still shown saying yes as well as no -- and the
        historical pair keeps its own assertion, one line below, so this
        change tightens the test rather than trading one case for
        another.
        """
        regressed = "invalid_pagination: limit_too_large: limit must be <= 200 (got 999)"
        assert len(set(_NESTED_CODE_PATTERN.findall(regressed))) > 1
        assert _promoted(regressed) == "validation_error"

        # A code that left MESSAGE_PREFIX_CODES cannot be promoted from
        # prose even when it is the only token: that is the whole point
        # of declaring it on the exception instead.
        assert _promoted("limit_too_large: limit must be <= 200 (got 999)") == (
            "validation_error"
        )

        still_ambiguous = (
            "invalid_pagination: composed_search_invalid_page: page is wrong"
        )
        assert len(set(_NESTED_CODE_PATTERN.findall(still_ambiguous))) > 1
        assert _promoted(still_ambiguous) == "validation_error"

        repaired = "composed_search_invalid_page: page is wrong"
        assert len(set(_NESTED_CODE_PATTERN.findall(repaired))) == 1
        assert _promoted(repaired) == "composed_search_invalid_page"


class TestTheLegacyDetailPathIsGatedToo:
    """``detail_code`` reads English as well, and read it more loosely.

    Its character test does not even require an underscore, so before this
    change ``raise HTTPException(422, "manifest: ...")`` would have
    published ``code="manifest"``. The four bare prefixes written in the
    tree (``manifest``, ``readyz``, ``startup``, ``status``) are one
    refactor away from an error body, and the gate below is what stops
    them arriving as codes. A fifth, ``geometry_validation``, was reworded
    rather than gated -- it was the only one shaped like a code to the
    *narrowed* pattern as well, and is now held by
    :class:`TestNoLoggerFormatStringSitsInTheCodePosition`.
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


# ---------------------------------------------------------------------------
# Logger format strings: the code position, in a place with no response body
# ---------------------------------------------------------------------------

#: ``logger.<level>(...)`` calls whose first argument is a string literal.
#: ``.log(level, msg)`` is deliberately absent -- its first argument is the
#: level, not the message -- and no site in the tree uses it with a prefix.
_LOG_LEVEL_METHODS = frozenset(
    {"debug", "info", "warning", "warn", "error", "exception", "critical"}
)

#: Any leading ``token: `` at all, code-shaped or not. Looser than
#: :data:`_CODE_POSITION` on purpose: it is the *population* the gate is
#: measured against, so that "nothing matched" and "nothing is wrong" cannot
#: be confused.
_ANY_LEADING_TOKEN = re.compile(r"^([A-Za-z][A-Za-z0-9_]*): ")

#: The bare logger prefixes written in the tree that are **not** code-shaped,
#: because none contains an underscore. Named so the scan has a positive
#: control: a sweep that found none of these has stopped reading files, and
#: every "no code-shaped prefix found" below it would then be vacuous.
_PREFIXES_THAT_ARE_NOT_CODE_SHAPED = frozenset(
    {"manifest", "readyz", "startup", "status"}
)


def _logger_format_strings() -> list[tuple[str, int, str]]:
    """``(path, lineno, message)`` for every logger call with a literal.

    ``/importers/`` is scanned here although :data:`SKIPPED` excludes it
    from the raise-site sweep. That exclusion is about *raises*, which an
    offline importer cannot turn into a response; a log line is a
    readability hazard wherever it is written, and ``manifest:`` -- one of
    the five -- lives there.
    """
    found: list[tuple[str, int, str]] = []
    for root in SCANNED_ROOTS:
        for path in sorted(root.rglob("*.py")):
            if "/tests/" in str(path):
                continue
            tree = ast.parse(path.read_text())
            relative = str(path.relative_to(REPO_ROOT))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                func = node.func
                if (
                    not isinstance(func, ast.Attribute)
                    or func.attr not in _LOG_LEVEL_METHODS
                    or not node.args
                ):
                    continue
                first = node.args[0]
                if isinstance(first, ast.Constant) and isinstance(first.value, str):
                    found.append((relative, node.lineno, first.value))
                elif isinstance(first, ast.JoinedStr) and first.values:
                    head = first.values[0]
                    if isinstance(head, ast.Constant) and isinstance(head.value, str):
                        found.append((relative, node.lineno, head.value))
    return found


def _logger_prefixes() -> dict[str, list[str]]:
    """``token -> ["path:lineno", ...]`` for logger messages opening with one."""
    prefixes: dict[str, list[str]] = {}
    for relative, lineno, message in _logger_format_strings():
        match = _ANY_LEADING_TOKEN.match(message)
        if match:
            prefixes.setdefault(match.group(1), []).append(f"{relative}:{lineno}")
    return prefixes


class TestNoLoggerFormatStringSitsInTheCodePosition:
    """A log line is not a response, and must not read like a declaration.

    ``geometry_validation: chemistry layer raised for calculation_id=%s``
    reached no response body, so nothing was ever advertised wrongly. It
    was still worth rewording, and the reason is the one #161, #173 and
    #178 each cost a PR to learn: in this codebase a snake_case token in
    front of a colon *is* how a code is declared. A string that carries
    that shape without being a code is read as one -- by a person, and by
    the next scan somebody widens.

    ``geometry_validation`` was the only one of the five bare logger
    prefixes that could be, because it was the only one containing an
    underscore, and :data:`_CODE_POSITION` requires one. ``manifest``,
    ``readyz``, ``startup`` and ``status`` cannot match it and are left
    alone -- reworded strings nobody needed to rewrite are their own
    kind of noise.

    Note what this does **not** claim. ``detail_code``'s legacy character
    test does not require an underscore, so all five would be code-shaped
    to *it*; what stops them reaching a body is that promotion is gated on
    the catalogue, asserted directly by
    :meth:`TestTheLegacyDetailPathIsGatedToo.
    test_an_uncatalogued_prefix_falls_back_to_the_status`. This gate is
    about the shape a reader sees, and it is the durable answer to "a
    string in the code position that is not a code", which is the shape
    that has now produced four tasks.
    """

    def test_the_scan_reads_the_logger_calls_in_the_tree(self):
        """Assert the population, so an empty sweep cannot pass as clean.

        Measured 2026-08-16: 96 logger calls with a literal first argument,
        16 of which open with a bare ``token: ``. The floor is well under
        96 and exists to catch a scan that has stopped walking the tree,
        not to police the count -- the same reasoning as
        ``_MINIMUM_SITES_THE_SCAN_MUST_SEE`` in the re-raise gate.
        """
        messages = _logger_format_strings()
        assert len(messages) >= 60, (
            f"only {len(messages)} logger calls with a literal format string "
            f"found under {[str(r) for r in SCANNED_ROOTS]}; the scan has "
            "stopped reading files and every assertion below is vacuous"
        )

    def test_it_finds_the_bare_prefixes_that_are_written(self):
        """The positive control: known non-code-shaped prefixes are seen."""
        prefixes = _logger_prefixes()
        missing = sorted(_PREFIXES_THAT_ARE_NOT_CODE_SHAPED - set(prefixes))
        assert not missing, (
            f"the scan no longer sees these logger prefixes: {missing}. They "
            "are the evidence that it can see a prefix at all; without them "
            "'no code-shaped prefix found' means nothing"
        )

    def test_no_logger_format_string_opens_in_the_code_position(self):
        offenders = {
            token: sites
            for token, sites in _logger_prefixes().items()
            if _CODE_POSITION.match(f"{token}: ")
        }
        assert not offenders, (
            "these logger format strings open with a snake_case token in "
            "front of a colon, which is how this codebase declares an error "
            f"code: {offenders}. A log line declares nothing. Reword it -- "
            "'geometry validation: ...' rather than 'geometry_validation: "
            "...' -- so a reader, and the next scan somebody widens, cannot "
            "mistake it for a code."
        )

    def test_the_remaining_prefixes_are_not_code_shaped(self):
        """Says *why* four were left alone, rather than leaving it implied."""
        for token in sorted(_PREFIXES_THAT_ARE_NOT_CODE_SHAPED):
            assert "_" not in token
            assert not _CODE_POSITION.match(f"{token}: "), (
                f"{token!r} is code-shaped after all, so leaving it unchanged "
                "was wrong and this gate should be failing on it"
            )

    def test_the_scan_would_notice_one(self):
        """Provoked on source, so the detector is caught saying yes."""
        tree = ast.parse('logger.warning("some_new_code: it went wrong")\n')
        node = next(n for n in ast.walk(tree) if isinstance(n, ast.Call))
        assert isinstance(node.func, ast.Attribute)
        assert node.func.attr in _LOG_LEVEL_METHODS
        message = node.args[0].value
        assert _ANY_LEADING_TOKEN.match(message)
        assert _CODE_POSITION.match(message), (
            "the detector did not recognise a snake_case logger prefix; the "
            "gate above would then pass over a real one"
        )
