"""A caught coded exception must leave the handler still able to be named.

The defect this gate exists for
-------------------------------
A route catches a typed exception for a reason it genuinely has -- to pick
the right status, to roll back, to write a durable record -- and then
raises a plain ``HTTPException``. The typed exception's app-level handler
in :mod:`app.api.errors` never runs, so the ``code`` a client branches on
is replaced by ``http_<status>``. The condition did not change; only the
route that noticed it did.

Two instances were filed (#209, #212) and, measured, they were not the
only two. The sweep behind them found four:

* ``GET /admin/machine-review/curator-tasks/{id}`` answered ``http_404``
  where the four write routes on the same row answered
  ``curator_task_not_found``;
* ``GET /scientific/artifacts/{sha}/download`` answered ``http_502``
  where the upload path answered ``artifact_integrity_failed``;
* the sibling ``except ArtifactStorageUnavailable`` **in the same
  function**, ``http_503`` against ``artifact_storage_unavailable``,
  which no task had noticed;
* ``GET /scientific/artifacts/integrity`` answered ``http_422`` where
  every other scientific read answers ``client_sort_not_supported``.

Three of the four were found by sweeping rather than by being reported,
which is the reason this file is a gate and not a changelog entry.

Why a bare ``HTTPException`` is not always the defect
-----------------------------------------------------
Most of the sites this scan finds are correct, and a gate that failed on
all of them would be deleted within a month. TCKDB has a second, working
mechanism for carrying a code: :func:`app.api.error_contract.detail_code`
promotes a leading ``"<code>: <prose>"`` token when the catalogue lists
that code as arriving by ``message_prefix``. So

    except ReleaseCurationError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

publishes ``release_tag_taken``, not ``http_409``, while choosing a status
the generic ``ValueError`` handler could not have chosen. Passing the
message through is the *idiom*; replacing it with new prose is what drops
the code on the floor.

So the rule enforced here is narrow and mechanical: an ``except`` handler
in the API layer that raises ``HTTPException`` must pass the caught
exception's message through, or be listed below with a reason. It is
deliberately a shape test -- whether a specific code actually arrives is a
wire question, and the wire tests that ask it are named beside each entry.

Staleness, in both directions
-----------------------------
``docs/reviews/error_code_coverage_triage.md`` reasoned over call sites it
could not see, and #165 established the sharper form of the lesson: a set
of individually-correct behaviours can still have an unheld completeness
claim. A list that nothing re-checks becomes that. Both directions are
asserted:

* a **new** catch-and-replace site fails :meth:`test_every_site_that_
  replaces_the_message_is_accounted_for`, and the failure names it;
* a listed site that has been fixed, moved or deleted fails
  :meth:`test_no_entry_describes_a_site_that_is_gone`, so the list cannot
  outlive what it describes;
* and the scan itself is provoked against a site known to exist, so a
  scan that silently matches nothing cannot pass as a clean sweep.
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
API_ROOT = REPO_ROOT / "backend" / "app" / "api"

#: Sites that catch an exception and raise an ``HTTPException`` carrying a
#: *new* message, keyed ``"<path>:<except lineno>"``. Each value says why
#: that is not a lost code. Anything not here must pass ``str(exc)``
#: through so a ``message_prefix`` code survives.
#:
#: Keep this small. An entry is a standing claim that a client loses
#: nothing, and every one of them should name the test that checked it.
JUDGED_SITES: dict[str, str] = {
    "backend/app/api/routes/auth.py:256": (
        "Registration catches IntegrityError to roll back before answering, "
        "and this entry is the *completed* half of the one it replaces. "
        "That entry said letting the exception through would publish "
        "`unique_conflict` -- a real code that says less than the sentence "
        "it replaced, because a duplicate username and a duplicate email "
        "are the same SQLSTATE -- and that the honest repair was a code of "
        "its own, filed separately rather than smuggled in. #225 is that "
        "repair: the site now reads `exc.orig.diag.constraint_name` and "
        "raises `username_taken` or `email_taken`, so it mints a *stricter* "
        "code than the handler it bypasses rather than losing one. "
        "Still listed, and still a replaced message, because the scan's "
        "question is mechanical -- `detail=` is not built from `str(exc)` "
        "-- and answering it by inlining the call into the `raise` would "
        "reorder the classification after `session.rollback()` to satisfy a "
        "text match. What a client loses: nothing, and it gains the field "
        "name. Checked by test_api_auth.py::TestRegistrationConflictCodes "
        "on the wire, and by "
        "test_auth_registration_constraint_names.py against pg_constraint. "
        "An unrecognised constraint still answers `http_409`, which is the "
        "old behaviour kept deliberately for the case where naming a field "
        "would be a guess."
    ),
}

#: A site the scan must find, so a scan that matches nothing cannot pass.
#: It is the ``ReleaseCurationError`` handler on the release-publish route
#: -- the canonical *correct* shape, ``detail=str(exc)``.
_A_SITE_THAT_PASSES_THE_MESSAGE_THROUGH = "backend/app/api/routes/releases_admin.py"

#: Measured 2026-08-16, after the repairs: 13 handlers in
#: ``backend/app/api`` raise an ``HTTPException``, of which 12 pass the
#: caught message through and one is judged below. The floor is well under
#: 13 and exists only to catch a scan that has stopped seeing the tree,
#: not to police the count.
_MINIMUM_SITES_THE_SCAN_MUST_SEE = 8


def _raised_http_exceptions(node: ast.AST) -> list[ast.Call]:
    """Every ``raise HTTPException(...)`` lexically inside *node*."""
    found: list[ast.Call] = []
    for sub in ast.walk(node):
        if not isinstance(sub, ast.Raise) or not isinstance(sub.exc, ast.Call):
            continue
        func = sub.exc.func
        name = getattr(func, "id", None) or getattr(func, "attr", None)
        if name in {"HTTPException", "StarletteHTTPException"}:
            found.append(sub.exc)
    return found


def _mentions(node: ast.AST, name: str) -> bool:
    return any(
        isinstance(sub, ast.Name) and sub.id == name for sub in ast.walk(node)
    )


def _passes_the_message_through(call: ast.Call, bound: str | None) -> bool:
    """True when ``detail=`` is built from the caught exception.

    ``str(exc)`` and ``f"...{exc}"`` both count: either way the raiser's
    own sentence -- and therefore any code in its leading position --
    reaches :func:`app.api.error_contract.detail_code`.
    """
    if bound is None:
        return False
    for keyword in call.keywords:
        if keyword.arg == "detail" and _mentions(keyword.value, bound):
            return True
    for argument in call.args:
        if _mentions(argument, bound):
            return True
    return False


def _scan() -> dict[str, str]:
    """``"<path>:<lineno>" -> caught type(s)`` for catch-and-replace sites."""
    replaced: dict[str, str] = {}
    for path in sorted(API_ROOT.rglob("*.py")):
        tree = ast.parse(path.read_text())
        relative = str(path.relative_to(REPO_ROOT))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ExceptHandler):
                continue
            raises = _raised_http_exceptions(node)
            if not raises:
                continue
            if all(_passes_the_message_through(call, node.name) for call in raises):
                continue
            replaced[f"{relative}:{node.lineno}"] = ast.unparse(node.type or ast.Name("BaseException"))
    return replaced


def _all_sites() -> dict[str, str]:
    """Every ``except`` in the API layer that raises an ``HTTPException``."""
    sites: dict[str, str] = {}
    for path in sorted(API_ROOT.rglob("*.py")):
        tree = ast.parse(path.read_text())
        relative = str(path.relative_to(REPO_ROOT))
        for node in ast.walk(tree):
            if isinstance(node, ast.ExceptHandler) and _raised_http_exceptions(node):
                sites[f"{relative}:{node.lineno}"] = ast.unparse(
                    node.type or ast.Name("BaseException")
                )
    return sites


class TestTheScanSeesTheCodeItIsWrittenOver:
    """A survey that checked nothing passes; these say what it checked."""

    def test_it_finds_more_than_a_handful_of_handlers(self):
        sites = _all_sites()
        assert len(sites) >= _MINIMUM_SITES_THE_SCAN_MUST_SEE, (
            f"only {len(sites)} except-and-raise sites found under {API_ROOT}; "
            "the scan has probably stopped walking the tree"
        )

    def test_it_recognises_the_message_passthrough_idiom(self):
        """The correct shape must be *classified* correctly, not just seen."""
        sites = _all_sites()
        passthrough = {
            site
            for site in sites
            if site.startswith(_A_SITE_THAT_PASSES_THE_MESSAGE_THROUGH)
        }
        assert passthrough, (
            f"no handler found in {_A_SITE_THAT_PASSES_THE_MESSAGE_THROUGH}; "
            "the anchor for this assertion has moved"
        )
        assert not (passthrough & set(_scan())), (
            "`raise HTTPException(status, detail=str(exc))` was classified as "
            "replacing the message; the scan would then flag every correct "
            "site and this gate would be deleted rather than obeyed"
        )

    def test_it_would_notice_a_replaced_message(self):
        """Provoked against source, so the classifier is caught saying no."""
        tree = ast.parse(
            "try:\n"
            "    f()\n"
            "except SomeError as exc:\n"
            "    raise HTTPException(status_code=502, detail='new prose') from exc\n"
        )
        handler = next(
            node for node in ast.walk(tree) if isinstance(node, ast.ExceptHandler)
        )
        call = _raised_http_exceptions(handler)[0]
        assert not _passes_the_message_through(call, handler.name)


class TestTheSweepIsComplete:
    """Both directions, so neither a new site nor a stale entry survives."""

    def test_every_site_that_replaces_the_message_is_accounted_for(self):
        unlisted = {
            site: caught
            for site, caught in _scan().items()
            if site not in JUDGED_SITES
        }
        assert not unlisted, (
            "these handlers catch an exception and raise an HTTPException with "
            "a new message, so the caught exception's handler never mints its "
            f"code: {unlisted}. Either re-raise the exception (`raise`), pass "
            "its message through (`detail=str(exc)`), or add an entry to "
            "JUDGED_SITES saying what a client loses and why that is right."
        )

    def test_no_entry_describes_a_site_that_is_gone(self):
        """A list nothing re-checks is how a stale survey stays believed."""
        found = _scan()
        stale = sorted(set(JUDGED_SITES) - set(found))
        assert not stale, (
            f"JUDGED_SITES describes sites that no longer replace the caught "
            f"exception's message: {stale}. If they were repaired, delete the "
            "entry; if they moved, re-anchor it."
        )

    def test_the_artifact_routes_did_not_regrow_their_handlers(self):
        """Named, because this is the file where it had already happened.

        Anchored on file rather than line so a reformat does not fail it,
        and paired with the wire tests that assert the code each handler
        now publishes -- this is a shape test and cannot see a response
        body:

        * ``tests/api/test_api_untested_refusals_tier_de.py``
          ``::test_the_download_route_publishes_the_same_code_for_the_same_break``
          and ``::test_a_missing_object_names_the_storage_subsystem_too``;
        * ``tests/api/scientific/test_api_scientific_artifact_integrity.py``
          ``::test_client_sort_is_rejected_like_every_other_scientific_read``.

        ``routes/admin.py`` is deliberately **not** listed. #209 was not
        a catch-and-replace at all -- it was a private
        ``_get_curator_task_or_404`` duplicating a coded service helper,
        which this scan cannot see and should not pretend to. Asserting
        it here would be a guard covering a shape its target never had.
        What holds that one is
        ``test_every_curator_task_route_names_a_missing_task_the_same_way``,
        verified by mutation: reinstating the private guard turns it red.
        """
        replaced_by_file = {site.split(":")[0] for site in _scan()}
        path = "backend/app/api/routes/scientific/artifacts.py"
        assert path not in replaced_by_file, (
            f"{path} has regrown a handler that catches an exception and "
            "raises an HTTPException with a new message -- the exact shape "
            "#212 repaired, twice in one function"
        )
