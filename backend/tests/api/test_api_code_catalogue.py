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


def _scan_raise_sites() -> dict[str, str]:
    """Every code written as a literal where a refusal is raised.

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
        for node in ast.walk(tree):
            if not isinstance(node, ast.Raise) or not isinstance(node.exc, ast.Call):
                continue
            call = node.exc
            name = getattr(call.func, "id", None) or getattr(call.func, "attr", None)
            if name in _CODED_EXCEPTIONS and call.args:
                text = _leading_literal(call.args[0], constants)
                if text and _BARE_CODE.fullmatch(text):
                    found.setdefault(text, rel)
            for keyword in call.keywords:
                if keyword.arg == "code":
                    text = _leading_literal(keyword.value, constants)
                    if text and _BARE_CODE.fullmatch(text):
                        found.setdefault(text, rel)
            for argument in [*call.args, *(k.value for k in call.keywords)]:
                text = _leading_literal(argument, constants)
                if not text:
                    continue
                match = _CODE_POSITION.match(text)
                if match:
                    found.setdefault(match.group(1), rel)
    return found


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
    """
    scanned = _scan_raise_sites()
    assert len(scanned) > 80, (
        f"the scan found only {len(scanned)} codes at raise sites, which is "
        "far fewer than this backend has. The scan is broken, and a broken "
        "scan passes this test by finding nothing."
    )
    known = catalogued_codes()
    missing = {code: where for code, where in scanned.items() if code not in known}
    assert not missing, (
        "These codes are raised but not catalogued, so a client cannot "
        f"import them and nothing checks they still exist: {missing}"
    )


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
        if f'"{entry.code}"' not in path.read_text():
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
    """
    exported = {entry.code for entry in client_facing()}
    assert exported, "nothing is exported -- the client enum would be empty"

    for entry in CATALOGUE:
        if entry.surface in {Surface.generic_fallback, Surface.accidental_prefix}:
            assert entry.code not in exported, (
                f"{entry.code} has surface {entry.surface.value} and must not "
                "reach a client as an importable constant"
            )
        if entry.status >= 500:
            assert entry.code not in exported, (
                f"{entry.code} is reported at {entry.status}; a 5xx refuses "
                "nothing the caller did"
            )

    assert any(
        entry.surface is Surface.accidental_prefix for entry in CATALOGUE
    ), (
        "no accidental prefix is catalogued. Two are known to exist "
        "(create_applied_group_additivity, keyset_predicate); if they have "
        "been fixed, delete this assertion deliberately rather than letting "
        "the exclusion guard above run over an empty set."
    )


def test_the_status_fallback_family_is_not_exported() -> None:
    """``http_404`` and friends are the status line, spelled twice."""
    assert STATUS_FALLBACK_PATTERN.match("http_404")
    assert not STATUS_FALLBACK_PATTERN.match("handle_not_found")
    exported = {entry.code for entry in client_facing()}
    assert not [code for code in exported if STATUS_FALLBACK_PATTERN.match(code)]


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
