"""Watch every error body the suite produces, and refuse an internal id.

Two identifiers, one argument. DR-0028 Requirement 2: a user-facing error
body must never contain a database primary key. Row ids go to the log,
where the operator is; the body names the field the depositor wrote,
because that is the identifier they can act on. A row id is not secret,
but it is not ours to give -- it does not survive a restore into a fresh
database, it does not agree between the hosted deployment and a lab
self-host, and no public surface is keyed on it. ``public_ref`` is.

The second is a **raw database constraint name** -- ``uq_software_name``,
``fk_species_entry_species_id_species`` -- and Calvin settled it on
2026-08-18 on exactly the reasoning above: meaningless to a depositor,
not stable across a migration that renames it, and a disclosure of schema
layout. It reaches the operator through the handler's log line. The
sanctioned route from an internal constraint name to a public contract is
the scientific check register: a constraint that matters enough declares
a rejection code, and the *code* is what crosses the wire. See
:func:`app.api.errors._integrity_error_handler`.

Before that ruling the repository held three positions at once -- one
test asserting the name reached the body for registered constraints, one
asserting it must not for unregistered ones, and a third silent about
foreign keys. Three positions is what a per-test rule produces; this
observer is the reason there can only be one from here on.

Why a runtime observer, when a static scan already exists
---------------------------------------------------------
``backend/tests/api/test_no_row_ids_in_user_facing_text.py`` reads the
AST of ``backend/app`` and fails an f-string that interpolates an
id-shaped name into a refusal. It is a good check and it stays. It also
says, in its own words, what it cannot see: a message bound to a local
before being raised, ``%``-formatting and ``str.format``, and anything
that is not a literal at the raise site.

It has a fourth blind spot it does not name, and that one is the reason
this file exists: **it only looks at text.** The envelope a client
receives is ``{"code", "detail", "context"}``, and ``context`` is the
half the envelope tells clients to read. It is assembled as a ``dict``
-- ``CodedValueError(..., context={...})`` -- so no f-string is involved
and there is nothing for an AST text scan to match. A row id put there
reaches every client and no check in the repository sees it.

Until now that half was covered the way the error codes were before
#185: per test, one body at a time, wherever an author remembered.
Twenty-one test files cite DR-0028 Requirement 2 between them, in 36
places; exactly one of them
(``test_api_untested_refusals_tier_bc.py::_refusal``) wrapped the check
in something reusable, and it is reused nowhere else. So this is the
same answer #185 gave for codes -- a passive, total observer that
inspects what the suite already produces, rather than a rule each new
test must opt into.

Every 4xx/5xx JSON body is recorded as it is built, and the test that
produced a leaking one fails -- attributing the leak to the request that
caused it rather than to a suite-wide tally nobody can debug. That is
:mod:`tests.error_code_observer`'s shape, deliberately.

What counts as a leak, and why not "an integer"
-----------------------------------------------
A bare integer in a body is not a row id. It is usually the point of the
refusal. Measured across all three gates on the commit this was written
against, ``context`` carries **29 distinct numeric-valued key names**:
``mode_count``, ``n_imag``, ``duplicate_mode_indices``, ``molecularity``,
``charge``, ``multiplicity``, ``offset``, ``retry_after_seconds``,
``min``/``max``, ``participant_index``, ``ts_atom_index`` and the rest.
``freq_mode_index_not_unique`` publishes ``{"duplicate_mode_indices":
[1], "mode_count": 2}`` and must keep doing so. "No integers" would
delete the useful half of the contract.

Three rules were considered. The measurement chose between them:

1. **Key name.** Refuse a ``context`` key named like a database key --
   ``id``, ``*_id``, ``*_ids``, ``*_pk`` -- carrying an integer. Cheap,
   and it is the shape the mistake has actually taken: somebody passes a
   row id under its natural name. **Measured: zero such keys exist
   today**, in any of the three gates, so the rule lands green and every
   future one is a real finding.
2. **Value correlation** -- compare body integers against primary keys
   created in the same transaction. Precise in principle. The
   measurement argues against it: the only id-shaped values in any body
   today are ``detail[].input.existing_calculation_id``, which is
   Pydantic echoing back *the caller's own* programmatic-chaining field.
   A correlator would flag it, and it is not a disclosure -- the client
   sent that number.
3. **Allowlist the legitimate numeric keys, refuse the rest.** Inverting
   the burden is the shape that has worked repeatedly in this codebase,
   and it was the front-runner. It is not tractable here, and not merely
   because 29 is a lot to maintain. Two of the 29 are ``C`` and ``H`` --
   element symbols, keys of the formula map in ``{"reactants": {"C": 2,
   "H": 6}}``. The key space is the periodic table crossed with whatever
   a depositor uploads, so no enumeration can be complete, and the
   remaining 27 grow with every new coded refusal that carries a count.
   An allowlist would turn "this refusal now says how many modes
   disagreed" into a red gate.

So: rule 1, over ``context``. The blind spot is honest and stated below.

The constraint-name rule is the opposite case, and easy
-------------------------------------------------------
Where a row id is an integer indistinguishable from the useful integers
beside it, a constraint name announces itself: ``NAMING_CONVENTION`` in
``app/db/base.py`` gives every one of them a two-letter kind and an
underscore, and nothing else in an error body is spelled that way. So
this one *is* rule 3 -- refuse the shape, no enumeration, no allowlist --
and it is applied to string **values** anywhere in ``context`` and to
plain-string ``detail``. See :data:`CONSTRAINT_NAME`.

Measured when it was added (2026-08-18). Exactly one body in the suite
carried such a name -- the registered-constraint 409's
``context["constraint"]`` -- and it was removed in the same change; with
that gone, a full ``pytest backend/tests`` produces no match at all. So
this rule lands green on a clean tree and every future match is a real
finding rather than a backlog. Turning it off and putting the name back
was run in both directions before it was believed: see the mutations
recorded in the pull request, and the synthetic pair in
``tests/api/test_error_body_id_gate.py``.

The id text rule, and why it is not the AST scan again
--------------------------------------------------------
``detail`` is also swept, but only where it is a plain string -- the
``NotFoundError`` / ``ValueError`` / ``HTTPException`` shape. It is
scanned for the *label* form, ``id=123`` or ``(pk: 4)``: prose that
announces an id and then discloses one. This is the same rule as
``ID_LABEL`` in the AST scan, applied at the other end of the pipeline,
and that is the point -- here it sees the rendered string, so it catches
the ``%``-format, the ``.format``, and the message-bound-to-a-local
cases the AST scan lists as out of reach.

A structured ``detail`` (Pydantic's list of failures) is **not** swept.
It is the caller's own request echoed back, ints and all -- including
the caller's own ``existing_calculation_id``. Sweeping it would refuse a
number the client itself sent.

What this cannot catch
----------------------
An id under a name that does not look like one -- ``{"winner": 12}`` --
and an id in prose that does not announce itself. Rule 2 is what would
catch those, and the measurement above is why it is not here. This is a
floor, like the AST scan is a floor: it makes the obvious way to write
the mistake fail, and the obvious way is the way it has been written
every time so far.

Non-vacuity
-----------
A sweep that inspects zero bodies passes trivially, which is the failure
mode this repository produces most often. Three separate things make
that impossible here, and none of them is ``> 0``:

* **A second, independent witness.** ``TestClient.request`` is patched
  as well as ``JSONResponse.__init__``. They sit at opposite ends of the
  request: one records what the application *built*, the other what a
  client *received*. If either patch dies, or the extractor is blinded
  at its entry, a test whose client received a JSON error while the
  sweep examined nothing fails -- immediately, and attributed. This
  needs no constant and holds under any selection, any ``-k``, any
  worker count.
* **A measured floor** on the two counters, per gate, in
  ``conftest.py``. It catches a blinding that leaves the entry intact
  but walks nothing.
* **A detector test** (``tests/api/test_error_body_id_gate.py``) that
  runs the extractor over a synthetic leaking body and a synthetic clean
  one, so a refactor that quietly stopped matching is red on its own.

Cost is one ``isinstance`` and a small dict walk per error response, and
nothing at all for 2xx.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

#: A ``context`` key named the way a database key is named. Deliberately
#: about the name: an id-valued expression called something else is out of
#: reach of a shape check, and pretending otherwise would oversell this.
ID_KEY = re.compile(r"^(?:id|ids|pk|pks)$|_id$|_ids$|_pk$|_pks$")

#: Prose that announces an id and then discloses one --
#: ``calculation_id=412``, ``(id: 7)``. This is
#: ``tests/api/test_no_row_ids_in_user_facing_text.py``'s ``ID_LABEL``
#: rule and its ``ID_NAME`` rule collapsed into one, because by the time
#: the body exists the label and the interpolated name are the same
#: characters.
#:
#: The optional prefix must end in ``_``, and a bare ``id`` must be
#: preceded by a non-alphanumeric. Together those keep out the words that
#: merely end in the letters: ``uuid=3``, ``grid=10``, ``valid=1``, and
#: -- measured, it really occurs in this suite's output --
#: Cantera's ``Tmid = 1000``.
ID_IN_PROSE = re.compile(
    r"(?:^|[^A-Za-z0-9])(?:[A-Za-z0-9]*_)?(?:id|ids|pk|pks|rowid)\s*[=:]\s*-?\d+",
    re.IGNORECASE,
)

#: A database constraint or index name, spelled the way
#: ``NAMING_CONVENTION`` in ``app/db/base.py`` spells every one of them:
#: a two-letter kind, an underscore, and the table it sits on. Those five
#: prefixes are the whole vocabulary, which is why this is a shape check
#: and not a list -- a constraint added tomorrow is caught without anyone
#: adding it here, and that is the only version of this rule worth having.
#:
#: Applied to *values* and to prose, not to key names. The mistake this
#: catches is publishing the name, and the name is the value; a
#: ``context`` key literally called ``constraint`` is fine when what it
#: carries is a code. Deliberately anchored on a non-word character so
#: ``...uq_x`` inside a longer identifier is not matched, and it requires
#: at least two characters after the prefix so a bare ``fk_`` in prose
#: about the convention itself does not fire.
#:
#: The one false positive it can have: a depositor who names a *local key*
#: ``fk_something`` and then triggers a refusal that echoes it back. That
#: is the caller's own string, so it would not be a disclosure -- the same
#: carve-out ``_integerish`` makes for ``existing_calculation_id``. It has
#: not happened, and if it does the fix is a carve-out here rather than a
#: weaker rule, because the alternative is an allowlist of every schema
#: object name in the database.
CONSTRAINT_NAME = re.compile(
    r"(?:^|[^A-Za-z0-9_])((?:ck|uq|fk|ix|pk)_[a-z][a-z0-9_]*[a-z0-9])"
)


@dataclass(frozen=True)
class Leak:
    """One id-shaped fact found in one error body."""

    status: int
    code: str
    where: str
    detail: str

    def explain(self) -> str:
        return f"HTTP {self.status} {self.code!r}: {self.where} -- {self.detail}"


@dataclass
class Observation:
    """What happened between two drains, i.e. during one test."""

    leaks: list[Leak] = field(default_factory=list)
    #: Error bodies the extractor entered.
    bodies: int = 0
    #: JSON 4xx/5xx responses a ``TestClient`` handed back.
    client_errors: int = 0


#: Cleared by :func:`drain`, so a failure names the test that caused it.
_PENDING = Observation()

#: Never cleared. The floor in ``conftest.py`` reads these.
_TOTAL_BODIES = 0
_TOTAL_FIELDS = 0
_TOTAL_CLIENT_ERRORS = 0

_INSTALLED = False


def _is_int(value: Any) -> bool:
    """An integer that is not a ``bool``.

    ``isinstance(True, int)`` is ``True`` in Python, and a flag named
    ``is_valid_id`` carrying ``True`` is not a row id.
    """
    return isinstance(value, int) and not isinstance(value, bool)


def _integerish(value: Any) -> str | None:
    """How *value* carries an integer, or ``None``.

    A row id put under an id-shaped key arrives three ways: bare, in a
    list (``{"calculation_ids": [3, 4]}``), or as the *values* of a map
    keyed by something the caller supplied (``{"resolved_ids": {"my_key":
    7}}``). The third is the shape ``test_local_key_resolution.py`` and
    ``test_api_pdep_undeclared_local_keys.py`` each assert against by
    hand; it is caught here whenever the map's own name is id-shaped.

    A *string* value is not flagged. ``{"species_entry_id": "spc_abc"}``
    is a public ref under a misleading name -- a naming problem, not a
    disclosure, and refusing it would push authors to rename the key
    rather than to stop leaking.
    """
    if _is_int(value):
        return repr(value)
    if isinstance(value, (list, tuple)) and any(_is_int(item) for item in value):
        return f"list containing {[i for i in value if _is_int(i)][:5]!r}"
    if isinstance(value, dict) and any(_is_int(item) for item in value.values()):
        return f"map whose values include {[i for i in value.values() if _is_int(i)][:5]!r}"
    return None


def _sweep_context(
    node: Any,
    path: str,
    found: list[tuple[str, str]],
    named: list[tuple[str, str]],
) -> int:
    """Walk *node*, appending to *found* and *named*.

    *found* collects ``(path, why)`` for each id-shaped key carrying an
    integer; *named* collects ``(path, name)`` for each string value that
    is spelled like a database constraint. Two lists rather than one
    because the two findings need different sentences and a caller
    reading a failure should not have to work out which rule fired.

    Returns the number of ``(key, value)`` pairs visited, which is what
    the floor counts: a blinded walk visits none of them.
    """
    visited = 0
    if isinstance(node, dict):
        for key, value in node.items():
            visited += 1
            here = f"{path}.{key}"
            if isinstance(key, str) and ID_KEY.search(key):
                why = _integerish(value)
                if why is not None:
                    found.append((here, why))
            visited += _sweep_context(value, here, found, named)
    elif isinstance(node, (list, tuple)):
        for index, value in enumerate(node):
            visited += _sweep_context(value, f"{path}[{index}]", found, named)
    elif isinstance(node, str):
        match = CONSTRAINT_NAME.search(node)
        if match is not None:
            named.append((path, match.group(1)))
    return visited


def inspect(status: int, content: dict[str, Any]) -> tuple[list[Leak], int]:
    """The leaks in one error envelope, and how many keys were examined.

    The public seam of this module: the detector test drives it directly
    with synthetic bodies, and blinding it is what the floor detects.
    """
    code = content.get("code")
    code = code if isinstance(code, str) and code else "<no code>"

    found: list[tuple[str, str]] = []
    named: list[tuple[str, str]] = []
    visited = _sweep_context(content.get("context"), "context", found, named)
    leaks = [
        Leak(status=status, code=code, where=where, detail=f"{why} under an id-shaped key")
        for where, why in found
    ]
    leaks.extend(
        Leak(
            status=status,
            code=code,
            where=where,
            detail=(
                f"database constraint name {name!r} -- an internal "
                "identifier; register the constraint for a code of its own "
                "and let the name go to the log"
            ),
        )
        for where, name in named
    )

    detail = content.get("detail")
    if isinstance(detail, str):
        visited += 1
        match = ID_IN_PROSE.search(detail)
        if match is not None:
            leaks.append(
                Leak(
                    status=status,
                    code=code,
                    where="detail",
                    detail=f"prose discloses {match.group(0)!r}",
                )
            )
        constraint = CONSTRAINT_NAME.search(detail)
        if constraint is not None:
            leaks.append(
                Leak(
                    status=status,
                    code=code,
                    where="detail",
                    detail=(
                        f"prose names database constraint "
                        f"{constraint.group(1)!r}"
                    ),
                )
            )
    return leaks, visited


def install() -> None:
    """Patch the two ends of a request: what was built, what was received.

    Idempotent, for the same reason :func:`tests.error_code_observer.install`
    is: pytest imports conftest once per process, but a plugin reload or a
    nested session must not stack wrappers.
    """
    global _INSTALLED
    if _INSTALLED:
        return

    from starlette.responses import JSONResponse
    from starlette.testclient import TestClient

    original_response = JSONResponse.__init__

    def recording_init(self, content=None, status_code=200, *args, **kwargs):  # type: ignore[no-untyped-def]
        if isinstance(status_code, int) and status_code >= 400 and isinstance(content, dict):
            global _TOTAL_BODIES, _TOTAL_FIELDS
            leaks, visited = inspect(status_code, content)
            _PENDING.bodies += 1
            _TOTAL_BODIES += 1
            _TOTAL_FIELDS += visited
            _PENDING.leaks.extend(leaks)
        return original_response(self, content, status_code, *args, **kwargs)

    original_request = TestClient.request

    def recording_request(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        response = original_request(self, *args, **kwargs)
        status = getattr(response, "status_code", 0)
        media = (response.headers.get("content-type") or "").split(";")[0].strip()
        if status >= 400 and media == "application/json":
            global _TOTAL_CLIENT_ERRORS
            _PENDING.client_errors += 1
            _TOTAL_CLIENT_ERRORS += 1
        return response

    JSONResponse.__init__ = recording_init  # type: ignore[method-assign]
    TestClient.request = recording_request  # type: ignore[method-assign]
    _INSTALLED = True


def drain() -> Observation:
    """Return, and forget, what this test produced.

    Draining rather than accumulating is what keeps a failure attached to
    the request that caused it: without it the first leaking body would
    fail every subsequent test in the process, and the culprit would be
    whichever test happened to run first.
    """
    global _PENDING
    observed = _PENDING
    _PENDING = Observation()
    return observed


def session_totals() -> tuple[int, int, int]:
    """``(bodies swept, body fields examined, client errors seen)`` so far.

    Cumulative and never reset -- this is what the floor in ``conftest.py``
    is asserted against, and under xdist what each worker hands back to the
    controller to be summed.
    """
    return _TOTAL_BODIES, _TOTAL_FIELDS, _TOTAL_CLIENT_ERRORS
