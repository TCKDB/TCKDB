"""No internal row id may be interpolated into text a user receives.

The rule -- never put a database primary or foreign key in user-facing
detail; log it server-side instead -- has been written down for a long
time. It was violated in roughly fifty places. That is the whole
argument for this file: writing a rule down does not enforce it, and a
one-time sweep regresses on the next pull request, because the next
author will reach for ``f"{kind} not found (id={row.id})"`` exactly as
every previous author did. It is the obvious thing to write.

Why the ids matter
------------------
A row id is not secret, but it is not *ours to give*. It is an
implementation detail of a specific database instance: it does not
survive a restore into a fresh database, it does not mean the same thing
on the hosted deployment and on a lab self-host, and it is not what any
public surface is keyed on -- ``public_ref`` is. A client that learns to
key on ids builds against something TCKDB has never promised to keep
stable, and a 404 that echoes the id it was asked about also confirms,
by omission or presence, what exists.

The one genuinely bad shape is a *disclosure*: a message that hands back
an id the caller did not already have. ``handles.reconcile_id_ref`` had
one -- supply a public ref and any wrong id, and the 422 told you the
real row id the ref resolves to, which is precisely the mapping
``internal_ids`` exists to hide.

What this catches
-----------------
An f-string interpolating an id-shaped expression into a call that
constructs a user-facing refusal, or into a field of a successful
response. Detection is on the AST, not on the text, so it does not care
about wording, and it cannot be satisfied by rephrasing.

What this cannot catch, honestly
--------------------------------
* A message built into a local variable first, then raised. The check
  looks at the argument expression, not at what flowed into it.
* ``%``-formatting and ``str.format``.
* An id-valued expression whose *name* does not look like an id
  (``row.pk``, ``winner.identifier``, a bare integer returned from a
  helper).
* An id stored in a row that is served later -- ``__init__``'s
  ``record_ref = str(record_id)`` family. Those are inventoried in the
  candidate list rather than caught here, because catching them needs
  taint analysis rather than a shape check.

So this is a floor, not a proof. It makes the *obvious* way to write the
mistake fail in CI, which is the shape the mistake has actually taken
every time so far.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[2] / "app"

#: Callables whose string argument becomes a response body. The exception
#: types are registered in ``app/api/errors.py``, which puts ``str(exc)``
#: verbatim into the envelope; ``MatchDetail`` strings go out in a
#: successful 200.
USER_FACING_CALLS = frozenset(
    {
        "CodedValidationError",
        "CodedValueError",
        "DomainError",
        "HTTPException",
        "NotFoundError",
        "ValueError",
    }
)

#: Keyword arguments of the above (and of response-model constructors)
#: whose value is rendered to a user.
USER_FACING_KEYWORDS = frozenset({"detail", "message", "resource_name"})

#: Method calls that are user-facing sinks only in a particular module.
#: ``add`` is far too common a name to treat as a sink everywhere, but
#: ``_MatchBuilder.add`` in the lookup routes writes free text straight
#: into the ``details`` list of a **200** body, which is a worse place for
#: an id than any refusal: nothing went wrong, and the id is handed over
#: on the success path.
USER_FACING_METHODS: dict[str, frozenset[str]] = {
    "app/api/routes/lookup.py": frozenset({"add"}),
}

#: An expression named like a database key. Deliberately about the
#: *name*: an id-valued expression called something else is out of reach
#: of a shape check, and pretending otherwise would oversell the guard.
ID_NAME = re.compile(r"^id$|^ids$|^id_|_id$|_ids$")

#: Literal text immediately before an interpolation, which labels
#: whatever follows as an id whatever that expression is called. This is
#: the only text-shaped rule here and it earns its place: the single
#: worst leak found -- ``f"(ref -> id={resolved})"`` in
#: ``handles.reconcile_id_ref`` -- interpolated a name the ID_NAME rule
#: cannot see, while announcing in the surrounding prose exactly what it
#: was about to disclose.
ID_LABEL = re.compile(r"(^|[^a-z0-9_])(id|ids|row_id|pk)\s*[=:]?\s*$", re.IGNORECASE)

#: Sites that interpolate an id-shaped name into user-facing text and are
#: nonetheless correct, each with the reason. Deliberately not a wildcard
#: and deliberately not silent: adding one means writing down why, in a
#: diff a reviewer sees. An empty allowance is the goal, not a failure.
ALLOWED: dict[tuple[str, str], str] = {}


def _is_id_expression(node: ast.expr) -> str | None:
    """The id-shaped name in *node*, or ``None``.

    Only the expression's own head is inspected -- a ``Name``, or the
    attribute at the end of a dotted chain, or a constant subscript key.
    It deliberately does not descend into a ``Call``, because ``len(ids)``
    is a count and ``str(record_id)`` is a conversion whose *result* is
    the id; the second is caught by the ``Name`` inside it only if it is
    the head, which is the conservative reading.
    """
    if isinstance(node, ast.Name):
        return node.id if ID_NAME.search(node.id) else None
    if isinstance(node, ast.Attribute):
        return node.attr if ID_NAME.search(node.attr) else None
    if isinstance(node, ast.Subscript):
        key = node.slice
        if isinstance(key, ast.Constant) and isinstance(key.value, str):
            return key.value if ID_NAME.search(key.value) else None
    return None


def _labelled_as_id(joined: ast.JoinedStr, index: int) -> bool:
    """Whether the literal text just before part *index* announces an id."""
    if index == 0:
        return False
    previous = joined.values[index - 1]
    if not isinstance(previous, ast.Constant) or not isinstance(previous.value, str):
        return False
    return bool(ID_LABEL.search(previous.value))


def _interpolated_ids(node: ast.expr) -> list[str]:
    """Every id-shaped expression interpolated into *node*, if it is text."""
    found: list[str] = []
    for child in ast.walk(node):
        if not isinstance(child, ast.JoinedStr):
            continue
        for index, part in enumerate(child.values):
            if not isinstance(part, ast.FormattedValue):
                continue
            name = _is_id_expression(part.value)
            if name is not None:
                found.append(name)
            elif _labelled_as_id(child, index):
                found.append(ast.unparse(part.value))
    return found


def _offenders_in(path: Path, relative: str) -> list[tuple[int, str, str]]:
    tree = ast.parse(path.read_text())
    methods = USER_FACING_METHODS.get(relative, frozenset())
    offenders: list[tuple[int, str, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        callee = node.func
        name = callee.id if isinstance(callee, ast.Name) else getattr(callee, "attr", "")

        candidates: list[ast.expr] = []
        if name in USER_FACING_CALLS or name in methods:
            candidates.extend(node.args)
        candidates.extend(
            keyword.value
            for keyword in node.keywords
            if keyword.arg in USER_FACING_KEYWORDS
        )
        for argument in candidates:
            for leaked in _interpolated_ids(argument):
                offenders.append((argument.lineno, name or "<call>", leaked))
    return offenders


def _sweep() -> list[tuple[str, int, str, str]]:
    results: list[tuple[str, int, str, str]] = []
    for path in sorted(APP_ROOT.rglob("*.py")):
        relative = str(path.relative_to(APP_ROOT.parent))
        for lineno, callee, leaked in _offenders_in(path, relative):
            if (relative, leaked) in ALLOWED:
                continue
            results.append((relative, lineno, callee, leaked))
    return results


def test_no_row_id_is_interpolated_into_user_facing_text() -> None:
    offenders = _sweep()
    assert not offenders, (
        "These sites put a database row id into text a user receives. Log the "
        "id server-side and name the resource instead -- a public ref if the "
        "caller supplied one, otherwise just the kind:\n"
        + "\n".join(
            f"  {path}:{lineno}  {callee}(...)  interpolates {leaked!r}"
            for path, lineno, callee, leaked in offenders
        )
    )


def test_the_scan_actually_reaches_the_code_it_claims_to() -> None:
    """Guard the guard: a broken path or parse would pass vacuously."""
    files = list(APP_ROOT.rglob("*.py"))
    assert len(files) > 200, f"only {len(files)} modules scanned"
    assert (APP_ROOT / "services" / "scientific_read" / "handles.py") in files


def test_the_detector_recognises_the_shape_it_exists_to_catch() -> None:
    """The detector must fail on the mistake and pass on the fix.

    Without this, a refactor that quietly stopped matching anything would
    leave every assertion above passing on an empty sweep -- the failure
    mode of every lint written as a search.
    """
    def leaks(source: str) -> list[str]:
        return [
            leaked
            for node in ast.walk(ast.parse(source))
            if isinstance(node, ast.Call)
            for argument in node.args
            for leaked in _interpolated_ids(argument)
        ]

    assert leaks(
        'raise NotFoundError(f"calculation not found (calculation_id={calc_id})")'
    ) == ["calc_id"]
    # The label rule, which is the only thing that can see this one: the
    # expression is called ``resolved``, and the prose around it is what
    # announces that a row id is about to be disclosed.
    assert leaks('raise ValueError(f"ref resolves elsewhere (ref -> id={resolved})")')

    for clean in (
        'raise NotFoundError("calculation not found")',
        'raise NotFoundError(f"calculation not found (calculation_ref={ref!r})")',
        'raise ValueError(f"matched {count} records, narrow the query")',
        'raise ValueError(f"{len(ids)} records is too many")',
        'raise ValueError(f"invalid handle {handle!r}")',
    ):
        assert not leaks(clean), clean
