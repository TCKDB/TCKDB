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
    RELATIONSHIP_WORDS,
    REPLAYABLE_STATUSES,
    STATUS_FALLBACK_PATTERN,
    ApiCode,
    Reach,
    Replay,
    Shape,
    Surface,
    catalogued_codes,
    client_facing,
    never_retryable,
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
        if entry.reach is Reach.guard:
            assert entry.code not in exported, (
                f"{entry.code} is classified as a guard no request can trip, "
                "so a client branch written for it would never run -- the "
                "silent non-event this enum is generated to remove"
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
    """``http_404`` and friends are the status line, spelled twice.

    The pattern is exercised in both directions before it is trusted, and
    #192 added the floor under ``exported``: the final assertion is an
    absence over a derived set, and an empty ``client_facing()`` would
    satisfy it while the client enum published nothing at all.
    """
    assert STATUS_FALLBACK_PATTERN.match("http_404")
    assert not STATUS_FALLBACK_PATTERN.match("handle_not_found")
    exported = {entry.code for entry in client_facing()}
    assert len(exported) > 50, (
        f"only {len(exported)} codes are exported (measured: 136); an "
        "exclusion asserted over a set this small has stopped saying that "
        "the status-fallback family in particular is kept out"
    )
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


def test_the_observer_watches_the_status_and_not_only_the_code() -> None:
    """A catalogued code at an uncatalogued status must still fail.

    The observer recorded ``(status, code)`` from the day it was written
    and compared only the code, so a wrong status was invisible to it.
    Two entries were wrong -- ``curation_policy_version_conflict`` and
    ``release_tag_taken`` are both 409 and were both catalogued at 422 --
    and nothing in the suite could say so. Status is not decoration: the
    client's ``REJECTION_STATUSES`` is generated from this field, and 409
    versus 422 is the difference between "the write reached the database"
    and "nothing was written, resend a corrected payload".

    Stated in both directions, because half of it passes vacuously: the
    right status must be accepted, or the widened check would be
    satisfied by rejecting everything.
    """
    from starlette.responses import JSONResponse

    from tests import error_code_observer

    sample = CATALOGUE[0]
    wrong_status = 409 if sample.status != 409 else 422
    assert wrong_status not in {
        entry.status for entry in CATALOGUE if entry.code == sample.code
    }

    JSONResponse(content={"code": sample.code}, status_code=sample.status)
    assert (sample.status, sample.code) not in error_code_observer.drain_unlisted(), (
        f"{sample.code} at its own catalogued status {sample.status} is "
        "being reported as unlisted, so the observer now refuses correct "
        "pairs and its failures say nothing"
    )

    JSONResponse(content={"code": sample.code}, status_code=wrong_status)
    unlisted = error_code_observer.drain_unlisted()
    assert (wrong_status, sample.code) in unlisted, (
        f"{sample.code} arrived at {wrong_status} and the catalogue records "
        f"it only at {sample.status}, but the observer let it through -- it "
        "is comparing codes again, not (status, code) pairs"
    )
    assert str(sample.status) in error_code_observer.explain(
        wrong_status, sample.code
    ), "the failure must name the status the catalogue does record"
    assert "no catalogue entry at all" in error_code_observer.explain(
        999, "zzz_synthetic_probe_bait"
    ), "a code with no entry must not be reported as a status disagreement"


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


#: The codes #184 and #186 classified as guards. Named here rather than
#: derived, because the value of the classification is that flipping one
#: back is a deliberate act with a test to change, not a silent widening
#: of the client contract.
#:
#: It was three until #195, and the removal is the mechanism working.
#: ``applied_energy_correction_source_calculation_owner_mismatch`` was
#: classified a guard because its three call sites all read a calculation
#: the enclosing block had scoped to the target -- and its note named the
#: fourth and fifth, in ``/uploads/computed-reaction``, which resolve the
#: same key across the whole bundle and refused with a bare ``ValueError``.
#: Routing those through the shared guard made the code reachable, so it
#: left this tuple, gained ``Reach.request``, and is now exported.
_GUARD_CODES = (
    "idempotency_in_progress",
    "transport_source_calculation_owner_mismatch",
)

#: Codes that were ``Reach.guard`` and are not any more. Kept so the
#: reclassification cannot silently reverse: a code that goes back to
#: being a guard has stopped being reachable, which means a write path
#: was removed, and that deserves the same deliberate edit the promotion
#: did.
_PROMOTED_FROM_GUARD = ("applied_energy_correction_source_calculation_owner_mismatch",)


def test_the_reach_rule_can_say_no() -> None:
    """``Reach`` must change an answer, or it is a comment with a type.

    The same shape as ``test_the_origin_detector_can_say_no`` above, and
    for the same reason: a field whose only evidence is that the codes
    carrying it happen not to be exported proves nothing, because they
    could be excluded by one of the three older rules instead. So it is
    exercised on two entries that differ in nothing else.
    """
    origin = "backend/app/services/calculation_ownership.py"
    reachable = ApiCode("probe_code", 422, Surface.coded_exception, origin)
    guarded = ApiCode(
        "probe_code", 422, Surface.coded_exception, origin,
        reach=Reach.guard, note="probe",
    )

    assert reachable.reach is Reach.request, "Reach.request must be the default"
    assert reachable.is_client_facing
    assert not guarded.is_client_facing


def test_every_guard_entry_is_catalogued_annotated_and_unexported() -> None:
    """The three-way distinction, asserted as three separate properties.

    Catalogued, because the literal exists and a reader who greps it
    deserves an answer. Annotated, because "no request can produce this"
    is a claim and an unexplained claim is the kind that rots -- the note
    has to say which property of the code makes it true. Not exported,
    because a caller cannot receive it.
    """
    guards = [entry for entry in CATALOGUE if entry.reach is Reach.guard]
    assert guards, (
        "no entry is classified as a guard, so every assertion below runs "
        "over an empty set -- the failure this file exists to avoid"
    )
    assert len(guards) * 8 < len(CATALOGUE), (
        f"{len(guards)} of {len(CATALOGUE)} entries are classified as guards. "
        "The class is meant to be the rare case a caller can never reach; at "
        "this proportion it has become a place to put codes nobody has "
        "tested, which is a different thing and hides real refusals from "
        "clients."
    )

    catalogued = catalogued_codes()
    exported = {entry.code for entry in client_facing()}
    for entry in guards:
        assert entry.code in catalogued
        assert entry.code not in exported
        assert entry.note, (
            f"{entry.code} is classified Reach.guard with no note. The note "
            "is where the claim is justified; without it the classification "
            "is unfalsifiable."
        )

    named = {entry.code for entry in guards}
    for code in _GUARD_CODES:
        assert code in named, (
            f"{code} is no longer classified as a guard. If a write path now "
            "reaches it, that is good news and this list should shrink "
            "deliberately -- together with the client version bump that "
            "re-exports it."
        )
    for code in _PROMOTED_FROM_GUARD:
        assert code not in named, (
            f"{code} is classified as a guard again. It was promoted in #195 "
            "because /uploads/computed-reaction resolves the key it guards "
            "in a bundle-wide namespace; going back means that write path is "
            "gone, which is a deliberate change, not a reclassification."
        )
        assert code in exported, (
            f"{code} is reachable but not exported, so a client still cannot "
            "branch on a refusal it can receive"
        )


#: The codes declared deterministic, pinned so a silent reclassification
#: fails rather than restoring the forever-retry bug. Both are 502s whose
#: own refusal sentence says "retrying will not clear it".
_NEVER_SUCCEEDS_CODES = ("artifact_integrity_failed", "artifact_object_missing")


def test_the_replay_rule_can_say_no() -> None:
    """``Replay`` must change an answer, or it is a comment with a type.

    The same shape as ``test_the_reach_rule_can_say_no`` above, and for the
    same reason: two probes differing in nothing but the field under test,
    so the negative cannot be produced by one of the other rules. Also
    pins the default, which is the fail-safe direction — an unclassified
    code must stay retryable, because giving up on a real outage is worse
    than the wasted attempts this field prevents.
    """
    origin = "backend/app/api/errors.py"
    transient = ApiCode("probe_code", 503, Surface.response_literal, origin)
    deterministic = ApiCode(
        "probe_code", 503, Surface.response_literal, origin,
        replay=Replay.never_succeeds, note="probe",
    )

    assert transient.replay is Replay.may_succeed, (
        "Replay.may_succeed must be the default: an entry nobody classified "
        "has to keep being retried"
    )
    assert not transient.is_replay_futile
    assert deterministic.is_replay_futile


def test_a_never_succeeds_declaration_at_an_unreplayed_status_is_inert() -> None:
    """A true classification that changes nothing must not be exported.

    No client retries a 422, so declaring one deterministic is correct and
    useless — and publishing it would grow ``NON_RETRYABLE_CODES`` with
    entries whose effect can never be observed, which is how a set stops
    being read. Same for a guard no request can produce: nothing will ever
    hand that code to a retry layer.
    """
    origin = "backend/app/api/errors.py"
    at_422 = ApiCode(
        "probe_code", 422, Surface.coded_exception, origin,
        replay=Replay.never_succeeds, note="probe",
    )
    unreachable = ApiCode(
        "probe_code", 503, Surface.response_literal, origin,
        reach=Reach.guard, replay=Replay.never_succeeds, note="probe",
    )

    assert at_422.replay is Replay.never_succeeds
    assert not at_422.is_replay_futile, (
        "a 422 declared never_succeeds was exported; 422 is not in "
        f"REPLAYABLE_STATUSES ({sorted(REPLAYABLE_STATUSES)}) so the "
        "declaration cannot change any client's behaviour"
    )
    assert not unreachable.is_replay_futile


def test_every_never_succeeds_entry_is_annotated_and_effective() -> None:
    """The declaration's obligations, asserted as separate properties.

    Non-empty, because every other assertion here runs over the set and an
    empty one would satisfy them all while the client retried a lost
    artifact forever. Annotated, for the reason ``Reach.guard`` requires a
    note: this claim tells a client to stop trying, and an unexplained
    claim is the kind that rots. Effective, because a declaration at a
    status nobody replays is inert. And rare, because the safe default is
    "keep retrying" and a set that grows to cover most 5xx conditions has
    become a place to put codes nobody wanted to think about.
    """
    declared = [
        entry for entry in CATALOGUE if entry.replay is Replay.never_succeeds
    ]
    assert declared, (
        "no entry is declared Replay.never_succeeds, so the client's "
        "NON_RETRYABLE_CODES is empty and every deterministic 5xx is "
        "replayed on a backoff schedule again -- the bug #234 fixed"
    )
    assert len(declared) * 10 < len(CATALOGUE), (
        f"{len(declared)} of {len(CATALOGUE)} entries are declared "
        "never_succeeds. The class is meant to be the rare condition that "
        "provably cannot clear; at this proportion it has become the "
        "default, and the default has to be 'keep retrying'."
    )

    for entry in declared:
        assert entry.note, (
            f"{entry.code} is declared Replay.never_succeeds with no note. "
            "The note is where the claim is justified; without it the "
            "classification is unfalsifiable."
        )
        assert entry.status in REPLAYABLE_STATUSES, (
            f"{entry.code} is declared never_succeeds at {entry.status}, "
            "which no client replays anyway. The declaration is true and "
            "inert; delete it or fix the status."
        )
        assert entry.reach is Reach.request, (
            f"{entry.code} is declared never_succeeds and also unreachable, "
            "so no retry layer will ever be handed it"
        )

    assert {entry.code for entry in never_retryable()} == set(_NEVER_SUCCEEDS_CODES), (
        "the exported deny list changed. That is a published client "
        "contract: adding to it stops clients retrying something, removing "
        f"from it starts them again. Expected {sorted(_NEVER_SUCCEEDS_CODES)}, "
        f"got {sorted(entry.code for entry in never_retryable())}."
    )


def test_the_storage_pair_still_disagrees_about_replay() -> None:
    """The pair that forced the field to exist, asserted from both ends.

    ``artifact_storage_unavailable`` and ``artifact_object_missing`` leave
    one handler, from one exception type, about one subsystem — and they
    need opposite retry advice. A test that only pinned the 502 would pass
    against a catalogue that had marked *both* deterministic, which would
    make the client abandon every transient object-store blip. So the
    negative is pinned too, and it is the entry that makes the guard above
    non-vacuous.

    Also asserted: neither reaches the ``RejectionCode`` enum. That is not
    a defect this work leaves behind, it is the point — the caller did
    nothing wrong, so there is no branch for a depositor to write, and the
    retry advice travels by the second export instead.
    """
    by_code: dict[str, ApiCode] = {}
    for entry in CATALOGUE:
        if entry.status >= 500:
            by_code[entry.code] = entry

    missing = by_code.get("artifact_object_missing")
    unavailable = by_code.get("artifact_storage_unavailable")
    integrity = by_code.get("artifact_integrity_failed")
    assert missing and unavailable and integrity, (
        "one of the three artifact-storage codes is gone from the "
        f"catalogue: {sorted(by_code)}"
    )

    assert missing.replay is Replay.never_succeeds
    assert integrity.replay is Replay.never_succeeds
    assert unavailable.replay is Replay.may_succeed, (
        "artifact_storage_unavailable is declared never_succeeds. It means "
        "the object store did not answer, which is exactly the case where "
        "waiting is the right response -- a client marking it deterministic "
        "would abandon every transient outage."
    )

    exported = {entry.code for entry in client_facing()}
    for entry in (missing, unavailable, integrity):
        assert entry.code not in exported, (
            f"{entry.code} reached the RejectionCode enum. It is a 5xx: the "
            "caller did nothing wrong, so there is no refusal to branch on. "
            "The retry advice is carried by never_retryable(), which is why "
            "is_client_facing did not have to widen."
        )


def test_the_ownership_guards_are_guards_because_of_a_schema_shape() -> None:
    """The classification rests on live schemas, so it is checked against them.

    An ownership guard can fire only when the field it guards can name a
    record the enclosing block did not itself scope to the target: a
    foreign row id, **or** a key resolved in a namespace wider than the
    target's owner. Both clauses matter, and the second one is what #173
    added after the first misclassified a guard that turned out to be
    measurable on the wire.

    ``transport_source_calculation_owner_mismatch`` is the last ownership
    code where neither clause holds: its payload carries no
    ``existing_*_id``, and its one write path reads a calculation the same
    loop persisted against the transport target's own species entry. That
    first half is a property of a live model, so it is asserted against
    the model rather than described -- the day somebody adds the field,
    this fails and names the entry to reclassify.

    Two ownership codes with the same *shape* are deliberately not here.
    ``statmech_torsion_scan_calculation_owner_mismatch`` is reachable on
    two bundle routes (``tests/api/test_api_network_pdep_ownership.py``,
    ``tests/api/test_api_bundle_torsion_scan_ownership.py``), and #195
    moved ``applied_energy_correction_source_calculation_owner_mismatch``
    into the same company: its payload still carries no foreign id, so
    the first clause still fails, and it is reachable anyway because
    ``/uploads/computed-reaction`` resolves ``source_calculation_key``
    across every species and the transition state. That is asserted below
    as the field's continued existence, and measured on the wire in
    ``tests/api/test_api_bundle_ownership_codes.py``.
    """
    from tckdb_schemas.energy_correction import (
        AppliedEnergyCorrectionUploadPayload,
    )

    from app.schemas.workflows.transport_upload import (
        TransportSourceCalculationIn,
    )

    def _foreign_row_fields(model) -> list[str]:
        return sorted(
            name
            for name in model.model_fields
            if name.endswith("_id") or name.startswith("existing_")
        )

    assert not _foreign_row_fields(TransportSourceCalculationIn), (
        f"TransportSourceCalculationIn now carries "
        f"{_foreign_row_fields(TransportSourceCalculationIn)}. A field that "
        "accepts a row this request did not create is exactly what makes an "
        "ownership guard reachable, so transport_source_calculation_owner_"
        "mismatch is no longer Reach.guard and the client enum must "
        "re-export it."
    )

    # The second clause, for the code that is reachable by it alone. The
    # first clause is still false here -- the payload has no foreign id --
    # so asserting only that would say the opposite of the truth.
    assert not _foreign_row_fields(AppliedEnergyCorrectionUploadPayload), (
        "AppliedEnergyCorrectionUploadPayload has gained a foreign row "
        "field. That is a wider change than the wide-namespace "
        "reachability this entry rests on, and the note on "
        "applied_energy_correction_source_calculation_owner_mismatch "
        "needs rewriting to say so."
    )
    assert "source_calculation_key" in AppliedEnergyCorrectionUploadPayload.model_fields, (
        "the field whose bundle-wide resolution makes "
        "applied_energy_correction_source_calculation_owner_mismatch "
        "reachable is gone; the entry is a guard again"
    )
    exported = {entry.code for entry in client_facing()}
    assert "applied_energy_correction_source_calculation_owner_mismatch" in exported
    assert "transport_source_calculation_owner_mismatch" not in exported


# ---------------------------------------------------------------------------
# Shape: does the code owe a client structured facts?
# ---------------------------------------------------------------------------
#
# ``Shape`` classifies every entry by Calvin's rule (2026-08-17): a code
# naming a relationship -- conflict, mismatch, ambiguity -- owes a client a
# populated ``context``, because it asserts something about two or more
# things and names none of them; a code naming a thing does not, because the
# name is the whole message.
#
# What is deliberately *not* asserted here, and must stay that way: that
# any entry's ``context`` is empty. Pinning the empty object would make
# every future improvement cost a red test, so the gap is left countable
# and unfrozen. The obligation in the other direction -- that a populated
# ``context`` keeps its keys -- is asserted per code on the wire, where a
# response body can be looked at, not here.


#: Entries whose ``context`` this repository has already populated *and*
#: which a wire test pins by key. Pinned here so that unpopulating one is a
#: deliberate edit in two places rather than a silent regression in one --
#: and pinned as a *floor*, never as the whole set, so the tranche that
#: populates the next relationship code does not have to edit this list.
_POPULATED_RELATIONSHIP_CODES = (
    # Not populated by this pass -- it arrived already carrying its
    # context, from ``Tell "no such selection" from "which selection?"``,
    # and ``test_api_conformer_selection_locator_codes.py`` pins its keys
    # on the wire. Listed for the same reason as the rest: a later edit
    # moving it to ``Shape.thing`` while those assertions stand would be a
    # contradiction, and this is what says so.
    "ambiguous_conformer_selection_locator",
    "handle_type_mismatch",
    "invalid_temperature_range",
    "multiple_structure_queries",
    "transition_state_irc_mapping_element_mismatch",
)


def test_the_shape_rule_can_say_no() -> None:
    """``Shape`` must change an answer, or it is a comment with a type.

    The same shape as ``test_the_reach_rule_can_say_no`` and
    ``test_the_replay_rule_can_say_no``: two probes differing in nothing
    but the field under test, so the negative cannot be produced by one of
    the other rules. Also pins the default, and the default is the one that
    needs pinning -- an unclassified code reads as a thing and therefore
    excuses itself from the obligation, which is the failure the word guard
    below exists to close.
    """
    origin = "backend/app/api/errors.py"
    named = ApiCode("probe_code", 409, Surface.sqlstate_category, origin)
    relating = ApiCode(
        "probe_code", 409, Surface.sqlstate_category, origin,
        shape=Shape.relationship,
    )

    assert named.shape is Shape.thing, "Shape.thing must be the default"
    assert not named.owes_context
    assert relating.owes_context


def test_the_classification_splits_the_catalogue_both_ways() -> None:
    """Both classes are populated, and neither swallowed the catalogue.

    A classification where every entry landed on one side is a field that
    was added and not applied -- it would satisfy every other assertion
    below while saying nothing. The bounds are deliberately loose: the
    point is that both answers are given, not that the ratio is any
    particular number.
    """
    relationships = [entry for entry in CATALOGUE if entry.shape is Shape.relationship]
    things = [entry for entry in CATALOGUE if entry.shape is Shape.thing]

    assert len(relationships) + len(things) == len(CATALOGUE), (
        "an entry carries neither shape, which the enum should have made "
        "impossible"
    )
    assert len(relationships) > 20, (
        f"only {len(relationships)} of {len(CATALOGUE)} entries are "
        "classified as relationships. The catalogue is full of *_mismatch "
        "and *_conflict codes; a number this low means the field was added "
        "and not applied."
    )
    assert len(things) > 20, (
        f"only {len(things)} of {len(CATALOGUE)} entries are classified as "
        "things. Most refusals name a thing -- unknown_X, missing_Y -- so a "
        "number this low means the rule was read as 'populate everything', "
        "which is the answer it exists to refuse."
    )
    assert {entry.code for entry in CATALOGUE if entry.owes_context} == {
        entry.code for entry in relationships
    }, "ApiCode.owes_context has drifted from Shape.relationship"


def test_no_entry_named_like_a_relationship_is_classified_as_a_thing() -> None:
    """The word guard, and proof it has something to guard.

    ``Shape`` defaults to ``thing``, so the classification a new code gets
    for free is the one that excuses it from the obligation. This is what
    stops that being silent for the spellings the codes here actually use:
    a name containing ``mismatch``, ``conflict``, ``contradicts``,
    ``disagree``, ``ambiguous`` or ``not_conserved`` cannot describe one
    thing, so it must be declared.

    The converse is not checked and cannot be -- ``atom_map_not_a_bijection``
    and ``selection_already_stands`` are relationships spelled without any
    of those words -- so a ``Shape.relationship`` declaration on a code
    with none of them is a judgement, not an error.
    """
    matched = [
        entry
        for entry in CATALOGUE
        if any(word in entry.code for word in RELATIONSHIP_WORDS)
    ]
    assert len(matched) > 25, (
        f"only {len(matched)} entries carry a relationship word, so this "
        "guard is nearly vacuous. Either RELATIONSHIP_WORDS lost a word or "
        "the catalogue was renamed out from under it."
    )
    misclassified = [
        entry.code for entry in matched if entry.shape is not Shape.relationship
    ]
    assert not misclassified, (
        f"{sorted(set(misclassified))} are named for a relation between two "
        "things but classified Shape.thing. A code whose name contains "
        f"{list(RELATIONSHIP_WORDS)} asserts something about two or more "
        "things and names neither, so it owes a client a populated context "
        "-- see Shape in app/api/code_catalogue.py. If one of these really "
        "is complete under its own name, rename the code; do not reclassify "
        "it."
    )


def test_the_word_guard_would_notice_a_misclassification() -> None:
    """The detector above must be able to fail, not merely to pass.

    Measured, not reasoned about: the real catalogue satisfies the guard,
    so a bug that made the word test match nothing would be invisible. A
    synthetic entry named for a relation and classified as a thing has to
    be caught by the same expression the assertion uses.
    """
    origin = "backend/app/api/errors.py"
    bait = ApiCode("probe_owner_mismatch", 422, Surface.coded_exception, origin)
    honest = ApiCode(
        "probe_owner_mismatch", 422, Surface.coded_exception, origin,
        shape=Shape.relationship,
    )
    clean = ApiCode("probe_unknown_thing", 404, Surface.coded_exception, origin)

    def caught(entry: ApiCode) -> bool:
        return (
            any(word in entry.code for word in RELATIONSHIP_WORDS)
            and entry.shape is not Shape.relationship
        )

    assert caught(bait), "the word guard cannot see a misclassified *_mismatch"
    assert not caught(honest)
    assert not caught(clean), (
        "the word guard fires on a code that names no relation, so it would "
        "force a classification by accident of vocabulary"
    )


def test_every_populated_relationship_code_is_still_a_relationship() -> None:
    """The codes with wire tests over their ``context`` keep the claim.

    One direction only. A code moving *out* of ``Shape.relationship`` while
    a wire test still pins its context keys is a contradiction: the entry
    would say the code names a thing while the response names two. The
    other direction -- a relationship entry whose context is still empty --
    is the open work and is deliberately not asserted anywhere, because
    pinning it either way costs a red test for the next improvement.
    """
    by_code = {entry.code: entry for entry in CATALOGUE}
    for code in _POPULATED_RELATIONSHIP_CODES:
        entry = by_code.get(code)
        assert entry is not None, f"{code} left the catalogue"
        assert entry.shape is Shape.relationship, (
            f"{code} is classified Shape.thing, but a wire test asserts its "
            "response context names the two things that disagreed. One of "
            "the two is wrong."
        )
        assert entry.owes_context
