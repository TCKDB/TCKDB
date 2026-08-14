"""What holds :mod:`app.api.code_catalogue` to being complete and honest.

The catalogue's whole value is the claim that it lists every code the API
can put in an error body. A list that merely *asserts* that would be the
recurring defect in this repository -- a check that cannot fail -- so the
claim is attacked from three sides here and a fourth in
``backend/tests/error_code_observer.py``:

* a source scan finds every code written as a literal at a raise site and
  demands a catalogue entry for it;
* every entry's ``origin`` must still contain the literal, so a rename
  fails before review does;
* the scientific check register must be a strict subset, so the two
  cannot quietly become one list again -- which is the design this work
  exists to avoid;
* and the runtime observer fails the test that produces a code nobody
  listed, which is the only one of the four that can see a code minted
  from a variable at request time.

Each assertion prints what the checker actually saw. An assertion over an
empty set is how a guard passes while proving nothing, and three of these
would be trivially satisfiable by a scan that found nothing.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

from app.api.code_catalogue import (
    CATALOGUE,
    STATUS_FALLBACK_PATTERN,
    Surface,
    catalogued_codes,
    client_facing,
)
from app.scientific_checks import CodeChannel
from app.scientific_checks.declarations import constraint_rejections, register

REPO_ROOT = Path(__file__).resolve().parents[3]

#: Where a code can be written. ``app/importers`` is excluded because its
#: prefixed strings are dry-run warnings appended to a report object --
#: they are never raised and no HTTP response carries them.
SCANNED_ROOTS = (
    REPO_ROOT / "backend" / "app",
    REPO_ROOT / "schemas" / "python" / "tckdb-schemas" / "tckdb_schemas",
)
SKIPPED = ("/tests/", "/importers/")

_CODE_POSITION = re.compile(r"^([a-z][a-z0-9_]*_[a-z0-9_]+): ")
_BARE_CODE = re.compile(r"^[a-z][a-z0-9_]*$")
_CODED_EXCEPTIONS = {"CodedValueError", "CodedValidationError"}

#: The positions :func:`_code_positions` distinguishes. A *position* is
#: where a literal sits in the syntax, not what it looks like -- which is
#: the whole point: ``"keyset_predicate"`` looks identical in ``__all__``
#: and in a ``raise``, and only one of them writes a code.
_AT_A_REFUSAL = "at_a_refusal"      #: leading ``"code: "`` among a raise's arguments
_CODED_FIRST_ARG = "coded_first_arg"  #: first argument of a ``Coded*Error`` raise
_CODE_KEYWORD = "code_keyword"      #: a ``code=`` / ``*_code=`` argument
_CODE_VALUE = "code_value"          #: a bare code used as a value
_MESSAGE_TEXT = "message_text"      #: a ``"code: "`` message written anywhere

#: The positions that mean *this module refuses with this code*. The
#: closure scan uses only these, because a module that stores a code in a
#: mapping is not thereby a raise site.
_WRITTEN_AS_A_REFUSAL = frozenset({_AT_A_REFUSAL, _CODED_FIRST_ARG, _CODE_KEYWORD})


def _module_constants(trees: list[ast.Module]) -> dict[str, str]:
    """``NAME = "literal"`` across the scanned tree.

    Needed because the house style is to name a code once as a module
    constant (``W_REACTION_MASS_BALANCE_FAILED``) and raise with the name.
    Without resolving those, the scan would see no code at most of the
    sites that matter and pass over an almost-empty set.
    """
    constants: dict[str, str] = {}
    for tree in trees:
        for node in ast.walk(tree):
            if not isinstance(node, ast.Assign) or len(node.targets) != 1:
                continue
            target = node.targets[0]
            if not isinstance(target, ast.Name) or not target.id.isupper():
                continue
            if isinstance(node.value, ast.Constant) and isinstance(
                node.value.value, str
            ):
                constants[target.id] = node.value.value
    return constants


def _leading_literal(node: ast.AST, constants: dict[str, str]) -> str | None:
    """The literal text an expression starts with, or ``None``.

    Handles the four shapes a refusal's first argument actually takes: a
    plain string, an f-string, a module constant, and a string built by
    ``+`` concatenation.
    """
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr) and node.values:
        head = node.values[0]
        if isinstance(head, ast.Constant) and isinstance(head.value, str):
            return head.value
    if isinstance(node, ast.Name):
        return constants.get(node.id)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return _leading_literal(node.left, constants)
    return None


def _whole_literal(node: ast.AST, constants: dict[str, str]) -> str | None:
    """The *entire* string an expression is, or ``None``.

    A bare code is a whole string. ``_leading_literal`` would answer
    ``"http_"`` for ``f"http_{exc.status_code}"`` -- a prefix of a token,
    not a token -- and reading that as a code invented one.
    """
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Name):
        return constants.get(node.id)
    return None


def _is_code_name(name: str) -> bool:
    """Does *name* name a code? ``code``, ``conflict_code``, ``fallback_code``."""
    lowered = name.lower()
    return lowered == "code" or lowered.endswith("_code")


def _held_strings(node: ast.AST):
    """Every string a *value* expression holds, through the containers used.

    ``{"23505": ("unique_conflict", "...")}`` and
    ``"idempotency_in_progress" if exc.in_progress else "idempotency_conflict"``
    are both a code written as a value; the literal is simply nested.
    """
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        yield node.value
    elif isinstance(node, (ast.Tuple, ast.List)):
        for element in node.elts:
            yield from _held_strings(element)
    elif isinstance(node, ast.Dict):
        for value in node.values:
            yield from _held_strings(value)
    elif isinstance(node, ast.IfExp):
        yield from _held_strings(node.body)
        yield from _held_strings(node.orelse)


def _documentation_strings(tree: ast.Module) -> set[int]:
    """Node ids of every string that is a *statement* -- a docstring.

    Prose is where a code gets *mentioned*, and mentioning is what this
    module must not accept. A string that is its own statement does
    nothing but document, so it is excluded before anything else looks at
    it. This is why a docstring quoting ``"missing_filter: ..."`` cannot
    stand in for the refusal that writes it.
    """
    return {
        id(node.value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Expr)
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
    }


def _code_positions(
    tree: ast.Module, constants: dict[str, str]
) -> dict[str, set[str]]:
    """Every code written as a code in one module, and *how* it is written.

    One walker, two callers -- :func:`_scan_code_sites` filters it to the
    positions that raise and :func:`_defines_code` accepts all of them --
    because two matchers drift apart, and the drift is invisible: both
    stay green.

    The distinction it exists to make is between a code and a *token that
    happens to look like one*. ``keyset_predicate`` is written in
    ``keyset.py`` twice over: once as the name of the function in
    ``__all__``, and once (until #178) as the leading token of a refusal.
    The regex this replaced could not tell those apart, because both are
    a double quote followed by the token. So it accepted the export --
    which meant rewording the refusal, the thing the guard exists to
    catch, left it green. The guard was blindest exactly where the defect
    was, and #164 reworded a message under it without noticing.

    Positions are therefore about *syntax*, not spelling. A bare code
    counts where it is used as a value -- passed to a call, stored in a
    mapping, assigned to ``code`` or to an upper-case module constant --
    and nowhere else. A name list, an import and a docstring all mention
    the token without writing a code, and none of them is a value.

    :returns: code -> the set of positions it was written in.
    """
    found: dict[str, set[str]] = {}
    documentation = _documentation_strings(tree)

    def record(code: str, position: str) -> None:
        found.setdefault(code, set()).add(position)

    for node in ast.walk(tree):
        if isinstance(node, ast.Raise) and isinstance(node.exc, ast.Call):
            call = node.exc
            name = getattr(call.func, "id", None) or getattr(call.func, "attr", None)
            if name in _CODED_EXCEPTIONS and call.args:
                text = _whole_literal(call.args[0], constants)
                if text and _BARE_CODE.fullmatch(text):
                    record(text, _CODED_FIRST_ARG)
            for keyword in call.keywords:
                if keyword.arg == "code":
                    text = _whole_literal(keyword.value, constants)
                    if text and _BARE_CODE.fullmatch(text):
                        record(text, _CODE_KEYWORD)
            for argument in [*call.args, *(k.value for k in call.keywords)]:
                text = _leading_literal(argument, constants)
                if text:
                    match = _CODE_POSITION.match(text)
                    if match:
                        record(match.group(1), _AT_A_REFUSAL)

        if isinstance(node, ast.Call):
            # ``code=`` at a raise was already visible. ``conflict_code=``
            # was not, because ``reconcile_id_ref`` is *called*, not
            # raised -- so the six ``*_handle_conflict`` codes were
            # invisible to this scan and were found by the runtime
            # observer alone. Only one of the six is emitted anywhere in
            # the three gates, so the observer cannot be what covers the
            # other five.
            #
            # A *qualified* ``*_code=`` only, away from a raise. Bare
            # ``code=`` is also how ``UploadWarning``, the dry run's
            # messages and the rubric's findings are built, and those
            # arrive with an *accepted* deposit -- 33 of them, none of
            # which belongs in a catalogue of what refused a request. A
            # name like ``conflict_code`` or ``fallback_code`` says which
            # contract the string is for; a bare ``code`` does not.
            for keyword in node.keywords:
                if keyword.arg and keyword.arg != "code" and _is_code_name(keyword.arg):
                    text = _whole_literal(keyword.value, constants)
                    if text and _BARE_CODE.fullmatch(text):
                        record(text, _CODE_KEYWORD)
            for argument in node.args:
                for text in _held_strings(argument):
                    if _BARE_CODE.fullmatch(text):
                        record(text, _CODE_VALUE)

        if isinstance(node, ast.Dict):
            for value in node.values:
                for text in _held_strings(value):
                    if _BARE_CODE.fullmatch(text):
                        record(text, _CODE_VALUE)

        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
            if isinstance(target, ast.Name) and (
                target.id.isupper() or _is_code_name(target.id)
            ):
                for text in _held_strings(node.value):
                    if _BARE_CODE.fullmatch(text):
                        record(text, _CODE_VALUE)

        if (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and id(node) not in documentation
        ):
            match = _CODE_POSITION.match(node.value)
            if match:
                record(match.group(1), _MESSAGE_TEXT)

    for value in constants.values():
        match = _CODE_POSITION.match(value)
        if match:
            record(match.group(1), _MESSAGE_TEXT)

    return found


def _scan_code_sites() -> dict[str, str]:
    """Every code written as a literal where a refusal is raised or named.

    :returns: code -> ``path`` of the first site that writes it.
    """
    sources: list[tuple[Path, ast.Module]] = []
    for root in SCANNED_ROOTS:
        for path in sorted(root.rglob("*.py")):
            if any(skip in str(path) for skip in SKIPPED):
                continue
            sources.append((path, ast.parse(path.read_text())))

    constants = _module_constants([tree for _path, tree in sources])
    found: dict[str, str] = {}

    for path, tree in sources:
        rel = str(path.relative_to(REPO_ROOT))
        for code, positions in _code_positions(tree, constants).items():
            if positions & _WRITTEN_AS_A_REFUSAL:
                found.setdefault(code, rel)
    return found


def _defines_code(source: str, code: str) -> bool:
    """Is *code* written as a code in *source*?

    Delegates to :func:`_code_positions`, so "written as a code" means
    the same thing here as it does to the closure scan. Any position
    counts, because an origin is the module that *holds the literal* --
    ``app/api/errors.py`` writes several of its codes into a response
    body rather than raising them, and the ownership guard names its
    codes as module constants for callers to pass in.

    Source that does not parse defines nothing: a matcher that cannot
    read the module must not report success for it.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return False
    return bool(_code_positions(tree, _module_constants([tree])).get(code))


def test_a_code_is_listed_once_per_status_it_arrives_at() -> None:
    """One ``(code, status)``, one entry -- but a code may have two.

    Two entries for one code at one status would let the generated enum
    and the drift guards disagree about it. Two entries at *different*
    statuses is a fact about the system, not a mistake: the atom map's
    claims are stated once at the wire boundary and again as a check
    constraint, so the same code arrives as a 422 or a 409 depending on
    the write path. Collapsing those to one entry silently dropped both
    from the client's ``CONFLICT_REJECTION_CODES`` while this was being
    written, which is how the distinction was found.
    """
    pairs = [(entry.code, entry.status) for entry in CATALOGUE]
    duplicates = sorted({pair for pair in pairs if pairs.count(pair) > 1})
    assert not duplicates, f"catalogued more than once: {duplicates}"
    assert len(pairs) > 50, (
        f"only {len(pairs)} entries. The API emits far more than that, so a "
        "catalogue this small has stopped being an enumeration."
    )


def test_every_raise_site_code_is_catalogued() -> None:
    """A route cannot mint a code without the catalogue learning about it.

    This is the closure guard, and it is the reason the catalogue can
    claim completeness for the literal-written majority rather than
    hoping for it.

    It reads ``*_code=`` arguments at every call and not only at a
    ``raise``, which is what makes the six ``*_handle_conflict`` codes
    visible: ``reconcile_id_ref`` is *called* with ``conflict_code=`` and
    raises inside, so a scan of raise sites saw nothing.

    Only one of the six -- ``level_of_theory_handle_conflict`` -- is
    emitted anywhere in the three gates (measured against the runtime
    observer's record of 101 distinct ``(status, code)`` pairs), so the
    other five have no runtime coverage at all. ``test_error_contract_
    catalogue_gate.py`` reaches them by a second static route, following
    the parameter rather than the keyword; this makes the catalogue's own
    closure guard see them directly, which is where a reader looks first.
    """
    scanned = _scan_code_sites()
    assert len(scanned) > 80, (
        f"the scan found only {len(scanned)} codes at raise sites and code "
        "arguments, which is "
        "far fewer than this backend has. The scan is broken, and a broken "
        "scan passes this test by finding nothing."
    )
    known = catalogued_codes()
    missing = {code: where for code, where in scanned.items() if code not in known}
    assert not missing, (
        "These codes are raised but not catalogued, so a client cannot "
        f"import them and nothing checks they still exist: {missing}"
    )


def test_the_origin_detector_can_say_no() -> None:
    """The drift guard is only worth its runtime if it can fail.

    Found by mutation: replacing :func:`_defines_code` with ``return
    True`` leaves ``test_every_origin_still_defines_its_code`` green, and
    its ``checked > 50`` floor does not help -- that floor counts entries
    whose *path* resolved, which is a different thing. So the detector is
    exercised directly, on the spellings it must accept and the
    near-misses it must reject.

    The ``__all__`` rejection is the one this test was reopened for. The
    regex this detector replaced answered *yes* for a module that merely
    exports a function of that name, so ``keyset_predicate`` was
    "defined" by ``__all__`` -- and #164 reworded its refusal with the
    guard staying green. A code and an identifier are the same shape;
    only position tells them apart.
    """
    assert _defines_code('raise ValueError("missing_filter: at least one")', "missing_filter")
    assert _defines_code('raise NotFoundError(code="handle_not_found")', "handle_not_found")
    assert _defines_code('reconcile(conflict_code="species_handle_conflict")', "species_handle_conflict")
    assert _defines_code('JSONResponse(content={"code": "query_timeout"})', "query_timeout")
    assert _defines_code('W_OWNER_MISMATCH = "thermo_source_calculation_owner_mismatch"',
                         "thermo_source_calculation_owner_mismatch")
    assert not _defines_code('"""a docstring mentioning missing_filter: prose."""',
                             "missing_filter")
    assert not _defines_code('"invalid_handle_prefix"', "invalid_handle")
    assert not _defines_code(
        'def keyset_predicate(keys, values): ...\n__all__ = ["keyset_predicate"]',
        "keyset_predicate",
    )
    assert not _defines_code("this is not python(", "some_code")


def test_every_origin_still_defines_its_code() -> None:
    """The pointer must point at something, and at the right something.

    Mirrors :attr:`app.scientific_checks.PythonCheck.location`: hold the
    real thing, not a string describing it, so a rename fails CI instead
    of surviving as a stale reference. A path rather than a line number,
    for the reason recorded there -- a gate that fires on a moved comment
    teaches everyone to regenerate without reading.
    """
    checked = 0
    problems: list[str] = []
    for entry in CATALOGUE:
        if not entry.origin:
            continue
        path = REPO_ROOT / entry.origin
        if not path.exists():
            problems.append(f"{entry.code}: {entry.origin} does not exist")
            continue
        if not _defines_code(path.read_text(), entry.code):
            problems.append(
                f"{entry.code}: not written as a literal in {entry.origin}"
            )
        checked += 1
    assert checked > 50, f"only {checked} origins checked; the guard is hollow"
    assert not problems, "\n".join(problems)


def test_the_register_is_a_strict_subset_of_the_catalogue() -> None:
    """Every scientific refusal code is a code the API can emit.

    The containment is the relationship this work establishes: the
    register annotates a *subset* of the catalogue rather than being the
    source it is generated from. A register code absent here would be a
    scientific claim about a refusal no consumer can receive.
    """
    scientific = {
        code
        for check in register()
        if check.channel is CodeChannel.error_envelope
        for code in check.codes
    }
    scientific |= {code for code, _detail in constraint_rejections().values()}
    assert scientific, "no scientific refusal codes -- the guard would be vacuous"

    missing = sorted(scientific - catalogued_codes())
    assert not missing, (
        f"the register declares these codes but the catalogue does not list "
        f"them: {missing}"
    )


def test_the_catalogue_is_not_the_register_wearing_a_different_name() -> None:
    """The two lists must stay different sizes, and by a wide margin.

    ``test_register_is_non_empty_and_proportionate`` keeps the register
    small so that membership keeps meaning something. This is the
    complementary guard: if the catalogue ever shrank to the register's
    size, the separation would have collapsed and the client enum would be
    back to publishing only chemistry -- the exact defect this module was
    written for.
    """
    scientific = {
        code
        for check in register()
        if check.channel is CodeChannel.error_envelope
        for code in check.codes
    }
    scientific |= {code for code, _detail in constraint_rejections().values()}
    catalogued = catalogued_codes()

    assert len(catalogued) > 2 * len(scientific), (
        f"{len(catalogued)} catalogued codes against {len(scientific)} "
        "scientific ones. The catalogue is meant to enumerate everything the "
        "API can return, which is several times the set of positions about "
        "chemistry; these numbers being close means one of the two lists has "
        "stopped doing its job."
    )
    non_scientific = catalogued - scientific
    assert len(non_scientific) > 50, (
        f"only {len(non_scientific)} codes are catalogued but not scientific"
    )


def test_client_facing_excludes_what_a_client_cannot_use() -> None:
    """A generic fallback and an accidental prefix are not contracts.

    Exporting ``validation_error`` would invite a branch that tells the
    caller nothing the 422 did not. Exporting a function name that landed
    in the code position would publish a fabricated code as though it were
    real, which is worse than an honestly generic one -- the finding #159
    was opened for.

    No entry carries ``accidental_prefix`` since #178 reworded the last
    two messages, so that half of the rule is now held by the count below
    and by the two names pinned out of the catalogue: an exclusion loop
    over an empty set is the failure this file exists to avoid, and a
    surface with no members would have made it one silently.
    """
    exported = {entry.code for entry in client_facing()}
    assert exported, "nothing is exported -- the client enum would be empty"

    excluded = 0
    for entry in CATALOGUE:
        if entry.surface in {Surface.generic_fallback, Surface.accidental_prefix}:
            assert entry.code not in exported, (
                f"{entry.code} has surface {entry.surface.value} and must not "
                "reach a client as an importable constant"
            )
            excluded += 1
        if entry.status >= 500:
            assert entry.code not in exported, (
                f"{entry.code} is reported at {entry.status}; a 5xx refuses "
                "nothing the caller did"
            )
            excluded += 1

    assert excluded > 5, (
        f"only {excluded} entries were excluded, so the loop above proved "
        "almost nothing about the exclusion rule"
    )

    for gone in ("create_applied_group_additivity", "keyset_predicate"):
        assert gone not in {entry.code for entry in CATALOGUE}, (
            f"{gone} is catalogued again. It is a function name, not a code: "
            "#178 reworded both refusals so neither token reaches the code "
            "position, and an entry for one means a message has regressed to "
            "the house style with the enclosing function as its context."
        )


def test_the_status_fallback_family_is_not_exported() -> None:
    """``http_404`` and friends are the status line, spelled twice."""
    assert STATUS_FALLBACK_PATTERN.match("http_404")
    assert not STATUS_FALLBACK_PATTERN.match("handle_not_found")
    exported = {entry.code for entry in client_facing()}
    assert not [code for code in exported if STATUS_FALLBACK_PATTERN.match(code)]


def test_the_runtime_observer_is_actually_watching() -> None:
    """A guard that is not installed is a guard that cannot fail.

    Deleting ``error_code_observer.install()`` from ``conftest.py`` turns
    the completeness check into a no-op and nothing else in the suite
    notices -- the teardown hook keeps running, over a set that is empty
    because nothing is being recorded. That is the exact failure shape
    this repository keeps finding, so it gets its own assertion: emit a
    code the catalogue cannot possibly contain, and require the observer
    to have seen it.

    Draining here is also what stops the synthetic code reaching the
    teardown hook and failing this test with its own bait.
    """
    from starlette.responses import JSONResponse

    from tests import error_code_observer

    JSONResponse(content={"code": "zzz_synthetic_probe_bait"}, status_code=499)
    unlisted = error_code_observer.drain_unlisted()
    assert (499, "zzz_synthetic_probe_bait") in unlisted, (
        "the JSONResponse patch is not installed, so every error code the "
        "suite produces is going unwatched and app/api/code_catalogue.py's "
        "completeness claim is unchecked at runtime"
    )


def test_a_refusal_that_is_not_science_is_importable_by_a_client() -> None:
    """The point of the work, stated as a test.

    ``thermo_source_role_type_mismatch`` is one of the seven refusals #159
    declared. It is correctly absent from the scientific check register --
    a role/type mismatch cannot be wrong in an interesting way -- and
    before the catalogue existed that also meant no client could name it.
    ``missing_filter`` is one of the twenty-four read-API codes in the
    same position.
    """
    exported = {entry.code for entry in client_facing()}
    scientific = {
        code
        for check in register()
        if check.channel is CodeChannel.error_envelope
        for code in check.codes
    }

    for code in ("thermo_source_role_type_mismatch", "missing_filter"):
        assert code in exported, f"{code} is not exported to a client"
        assert code not in scientific, (
            f"{code} has been added to the scientific check register. That is "
            "the decision #164 refused; if it has been revisited, this test "
            "should be changed deliberately and not merely to go green."
        )
