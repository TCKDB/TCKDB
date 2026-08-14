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

import ast
import re
from pathlib import Path

import pytest

from app.api.code_catalogue import CATALOGUE, Surface, catalogued_codes
from app.api.error_contract import (
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


def _promoted(message: str) -> str:
    return validation_detail_code(message, fallback="validation_error")


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
