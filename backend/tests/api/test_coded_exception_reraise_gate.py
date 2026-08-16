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

The second rule: a handler that can *absorb* a coded refusal
------------------------------------------------------------
The rule above asks what a handler **raises**. It cannot see the other
half, and the other half is where #212's last site actually lived:

    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

That **passes** the rule above -- the message goes through -- and it is
still a loss. ``CodedValidationError`` is a ``ValueError``, so this clause
catches every coded refusal raised anywhere beneath it; ``detail=str(exc)``
carries the code only where the catalogue lists it as ``message_prefix``,
and drops ``context`` unconditionally.

What is at stake depends on how broad the clause is, and the two cases are
worth keeping straight:

* ``except ValueError`` costs the ``code`` and the ``context``, but not
  the status -- ``_value_error_handler`` answers 422 and so does the
  ``HTTPException`` above.
* ``except Exception`` costs the status as well. ``NotFoundError``,
  ``DomainError`` and ``DataIntegrityError`` are **not** ``ValueError``
  subclasses (verified by ``issubclass``); they have their own handlers
  answering 404, 400 and 500. A broad clause that re-answers them as 422
  changes what the response *means*, not only what it is called.

The site that prompted this rule sat
on ``GET /scientific/artifacts/integrity`` wrapping
:func:`~app.services.scientific_read.artifact_integrity.search_artifact_integrity`,
and the #176 census verdict on it -- "cannot receive a coded error" -- was
**true**, established statically and empirically. It was true by accident:
the wrapped function sits beside functions that do raise coded refusals
(:func:`app.api.error_contract.reject_unsupported_filters` is one), so one
refactor routing a coded refusal through it would have flattened the code
with nothing failing.

Rather than pin that one call graph, this second rule covers the shape.
Every ``except`` in the API layer whose caught types can absorb a
``CodedValidationError`` -- ``ValueError``, ``Exception``, ``BaseException``
or a bare ``except`` -- must do one of three things:

1. **re-raise the original** (``raise``, or ``raise exc``), so the
   ``ValueError`` handler in :mod:`app.api.errors` still mints the code;
2. **intercept the coded case first**, with an earlier
   ``except CodedValidationError`` / ``except CodedValueError`` clause on
   the same ``try`` that re-raises it as itself -- this is the shape #165
   used on the kinetics wrapper, and it is the escape hatch for a handler
   that genuinely needs to translate the *uncoded* case; or
3. **be listed in** :data:`JUDGED_ABSORBING_HANDLERS` **with a reason**.

Why the exemption list is a list and not an inference
-----------------------------------------------------
Four of the sites it finds translate a coded refusal on purpose, and a
gate that broke them would be worse than no gate. Three are the lookup
API, whose whole contract is that an unresolvable species is a *200 with a
match block*, never a 422 -- the coded refusal is deliberately restated in
that envelope's own reason vocabulary. The fourth is a health probe that
must return rather than raise. None of that is derivable from the shape,
so it is written down, per site, in the dict below. An entry is a standing
claim; the staleness test makes it fail once it stops describing anything.

Deliberately **not** covered: the same shape anywhere below the API layer.
This is a scoping decision with a measured price. Running the identical
scan over all of ``backend/app`` (measured 2026-08-16) finds **104**
handlers that absorb without re-raising, against **8** in ``app/api`` --
so widening the rule would demand 96 further judgements, nearly all of
them chemistry parsers and service helpers restating one uncoded
``ValueError`` as another. Every one would have to be read and written up
before the gate could go green once, and a gate that opens with a hundred
exemptions is a gate that gets deleted.

The API layer is the right boundary on the merits, not only on cost: it is
where an exception stops being an exception and becomes a response body,
and a code that is never published is a code nobody could have branched
on. A refusal re-raised through ten service frames and dropped in the
eleventh is still caught here, because ``app/api`` is the last frame it
must cross.
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


# ---------------------------------------------------------------------------
# The second rule: a handler that can absorb a coded refusal
# ---------------------------------------------------------------------------

#: The caught types that swallow a ``CodedValidationError``. It subclasses
#: ``ValueError``, so these three -- plus a bare ``except:``, normalised to
#: ``BaseException`` by :func:`_caught_names` -- are exhaustive. Nothing
#: narrower can absorb one, and nothing wider exists.
ABSORBING_CATCHES = frozenset({"ValueError", "Exception", "BaseException"})

#: Clauses that take the coded case out of a broader clause's way. Both
#: spellings are live: wire-schema checks raise ``CodedValidationError``
#: because ``tckdb_schemas`` may not import ``app``, and the backend raises
#: the ``CodedValueError`` subclass.
CODED_CATCHES = frozenset({"CodedValidationError", "CodedValueError"})

#: Handlers in the API layer that absorb a coded refusal on purpose, keyed
#: ``"<path>::<enclosing qualname>"``. Anchored on the function rather than
#: a line number so that reformatting the module above them does not fail
#: this gate -- the lesson ``test_the_artifact_routes_did_not_regrow_their_
#: handlers`` records, applied to a list eight entries long instead of one.
#:
#: Every value must say what a client loses and why that is right. Adding
#: an entry is the expensive path on purpose: re-raising (``raise``) or an
#: earlier ``except CodedValueError`` clause needs no entry at all.
JUDGED_ABSORBING_HANDLERS: dict[str, str] = {
    # -- The four deliberate translations. ------------------------------
    # These are the ones a rule written carelessly would have broken, and
    # the reason the exemption mechanism is an explicit dict.
    "backend/app/api/routes/lookup.py::_resolve_species_list": (
        "The lookup contract answers 200 with a `match` block; an "
        "unresolvable reactant or product is a *result*, not a refusal, so "
        "there is no error body for a code to be lost from. "
        "`canonical_species_identity` can raise CodedValueError "
        "(`species_smiles_charge_mismatch`) and that is deliberately "
        "restated as the envelope's own `reactant_not_found` / "
        "`product_not_found` reason. The coded message is preserved "
        "verbatim inside the reason text (`f\"...: {e}\"`), so nothing a "
        "caller could read is dropped -- only the transport changes."
    ),
    "backend/app/api/routes/lookup.py::lookup_species": (
        "Same contract as above, one subject rather than a list: the coded "
        "refusal becomes `species_identity_none` in the match block, "
        "carrying `str(e)` as its reason. A 422 here would make the lookup "
        "API the only read route that refuses a query it simply cannot "
        "satisfy."
    ),
    "backend/app/api/routes/lookup.py::lookup_species_calculation": (
        "Same contract again, on the calculation lookup. Three separate "
        "entries rather than one wildcard on `lookup.py`: a wildcard would "
        "also exempt a fourth handler nobody had read."
    ),
    "backend/app/api/routes/health.py::_sanitized_endpoint": (
        "Formats an object-store URL for /status by stripping credentials "
        "from it. The only raiser under the try is `urlsplit`, which raises "
        "a bare ValueError; the handler returns the literal "
        "'(unparseable)'. No coded refusal is reachable, and the function "
        "returns a display string rather than a response body, so there is "
        "no `code` field for one to have reached."
    ),
    # -- Broad diagnostic handlers, none on a refusal path. --------------
    "backend/app/api/routes/auth.py::_migrate_password_hash": (
        "Opportunistic password-hash upgrade inside a nested transaction "
        "on an *already successful* login. Deliberately broad: the comment "
        "at the site says `never fail a valid login`. It logs and falls "
        "through, and the response it falls through to is a 200. Nothing "
        "under the try raises a coded refusal -- `hash_password` is pure "
        "and the nested begin raises SQLAlchemy errors."
    ),
    "backend/app/api/routes/health.py::_probe_bucket": (
        "`/status` must answer under every failure including ones this "
        "module has not anticipated, so the handler returns a health block "
        "rather than raising. A coded refusal cannot reach it: the try "
        "calls `head_bucket` on the storage client only."
    ),
    "backend/app/api/startup_checks.py::report_database_encoding_at_startup._probe": (
        "Boot diagnostic running on its own thread; a probe that raises "
        "must not become the fault it exists to report. It runs before any "
        "request exists, so there is no response body to lose a code from."
    ),
    "backend/app/api/startup_checks.py::report_artifact_storage_at_startup": (
        "Same reasoning at boot rather than on a thread: the storage probe "
        "raising must not take the process with it. Returns a diagnostic "
        "dict, not a response."
    ),
}

#: Measured 2026-08-16: nine handlers in ``backend/app/api`` can absorb a
#: coded refusal. One (``get_write_db``) needs no entry because it
#: re-raises; the other eight are judged above. The floor is well under
#: nine and exists only to catch a scan that has stopped walking the tree,
#: not to police the count.
_MINIMUM_ABSORBING_HANDLERS_THE_SCAN_MUST_SEE = 6

#: The handler that must be classified as *safe by re-raise*, so the
#: classifier is caught saying "no entry needed" about a real site rather
#: than only about a synthetic one. It is the write-session dependency:
#: every write request in the application passes through it, it catches
#: ``Exception`` to roll back and audit, and it ends with a bare ``raise``.
_A_HANDLER_THAT_RERAISES = "backend/app/api/deps.py::get_write_db"


def _caught_names(handler: ast.ExceptHandler) -> frozenset[str]:
    """The exception names a handler catches; bare ``except:`` is broadest."""
    node = handler.type
    if node is None:
        return frozenset({"BaseException"})
    parts = node.elts if isinstance(node, ast.Tuple) else [node]
    return frozenset(
        getattr(part, "id", None) or getattr(part, "attr", None) or ast.unparse(part)
        for part in parts
    )


def _reraises_the_original(handler: ast.ExceptHandler) -> bool:
    """True for a bare ``raise`` or ``raise <the bound name>``.

    Nested ``def``/``lambda`` bodies are **not** searched. A ``raise``
    inside a closure the handler merely defines does not re-raise anything
    when the handler runs, and counting it would make this classifier
    permissive in the one direction that matters: calling an unsafe
    handler safe is how a gate passes having checked nothing.
    """

    def search(node: ast.AST) -> bool:
        for child in ast.iter_child_nodes(node):
            if isinstance(
                child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)
            ):
                continue
            if isinstance(child, ast.Raise):
                if child.exc is None:
                    return True
                if (
                    handler.name
                    and isinstance(child.exc, ast.Name)
                    and child.exc.id == handler.name
                ):
                    return True
            if search(child):
                return True
        return False

    return search(handler)


def _qualnames(tree: ast.Module) -> dict[int, str]:
    """``id(node) -> "outer.inner"`` for every node, by enclosing scope."""
    resolved: dict[int, str] = {}

    def walk(node: ast.AST, prefix: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(
                child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
            ):
                inner = f"{prefix}.{child.name}" if prefix else child.name
                resolved[id(child)] = inner
                walk(child, inner)
            else:
                resolved[id(child)] = prefix
                walk(child, prefix)

    walk(tree, "")
    return resolved


def _absorbing_handlers() -> dict[str, dict]:
    """``"<path>::<qualname>" -> {caught, safe, lineno}`` for the API layer.

    ``safe`` is true when the handler re-raises the original, or when an
    earlier clause on the same ``try`` catches the coded type -- the two
    shapes that keep a code nameable without needing an exemption.
    """
    found: dict[str, dict] = {}
    for path in sorted(API_ROOT.rglob("*.py")):
        tree = ast.parse(path.read_text())
        relative = str(path.relative_to(REPO_ROOT))
        qualnames = _qualnames(tree)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Try):
                continue
            coded_taken_first = False
            for handler in node.handlers:
                caught = _caught_names(handler)
                if caught & CODED_CATCHES:
                    coded_taken_first = True
                    continue
                if not (caught & ABSORBING_CATCHES):
                    continue
                key = f"{relative}::{qualnames.get(id(node), '<module>')}"
                safe = coded_taken_first or _reraises_the_original(handler)
                previous = found.get(key)
                if previous is not None:
                    # Two absorbing handlers in one function collide on the
                    # key and one would vanish from the sweep. Keep the
                    # unsafe verdict so a merge can never hide a defect, and
                    # record the collision for the test that fails on it.
                    previous["collided"] = True
                    if previous["safe"] and not safe:
                        previous["safe"] = False
                        previous["lineno"] = handler.lineno
                    continue
                found[key] = {
                    "caught": "|".join(sorted(caught)),
                    "safe": safe,
                    "lineno": handler.lineno,
                    "collided": False,
                }
    return found


def _unjudged_absorbing_handlers() -> dict[str, dict]:
    """The sites the gate fails on: unsafe, and nobody has judged them."""
    return {
        key: info
        for key, info in _absorbing_handlers().items()
        if not info["safe"] and key not in JUDGED_ABSORBING_HANDLERS
    }


class TestTheAbsorbingScanSeesWhatItIsWrittenOver:
    """A sweep that matched nothing would pass every assertion below it."""

    def test_it_finds_the_handlers_that_can_absorb_a_code(self):
        handlers = _absorbing_handlers()
        assert len(handlers) >= _MINIMUM_ABSORBING_HANDLERS_THE_SCAN_MUST_SEE, (
            f"only {len(handlers)} handlers able to absorb a coded refusal "
            f"found under {API_ROOT}; the scan has probably stopped walking "
            "the tree, and every assertion below it would then pass "
            "vacuously"
        )

    def test_no_two_handlers_share_a_key(self):
        """Two in one function would merge, and one would leave the sweep."""
        collided = sorted(
            key for key, info in _absorbing_handlers().items() if info["collided"]
        )
        assert not collided, (
            "these functions hold more than one handler that can absorb a "
            "coded refusal, so the sweep cannot address them separately: "
            f"{collided}. Split the function, or key this scan on the "
            "handler's line number as well as its qualname."
        )

    def test_a_bare_reraise_is_classified_as_safe(self):
        """Caught saying 'no entry needed' about a real site, not a stub."""
        handlers = _absorbing_handlers()
        assert _A_HANDLER_THAT_RERAISES in handlers, (
            f"{_A_HANDLER_THAT_RERAISES} is no longer found by the scan; the "
            "anchor for this assertion has moved"
        )
        assert handlers[_A_HANDLER_THAT_RERAISES]["safe"], (
            "the write-session dependency catches Exception, rolls back, "
            "audits and then re-raises -- if the scan calls that unsafe it "
            "will call every correct handler unsafe and be deleted"
        )
        assert _A_HANDLER_THAT_RERAISES not in JUDGED_ABSORBING_HANDLERS, (
            "a re-raising handler must not need an exemption; an entry here "
            "would hide a later edit that stopped re-raising"
        )

    def test_it_would_notice_a_handler_that_swallows_a_coded_error(self):
        """Provoked against source, so the classifier is caught saying no."""
        tree = ast.parse(
            "try:\n"
            "    search_artifact_integrity()\n"
            "except ValueError as exc:\n"
            "    raise HTTPException(status_code=422, detail=str(exc)) from exc\n"
        )
        handler = next(
            node for node in ast.walk(tree) if isinstance(node, ast.ExceptHandler)
        )
        assert _caught_names(handler) & ABSORBING_CATCHES
        assert not _reraises_the_original(handler), (
            "the exact shape #212 removed from the artifact-integrity route "
            "was classified as safe; the gate would then permit its return"
        )

    def test_a_raise_inside_a_nested_def_is_not_a_reraise(self):
        """The permissive direction, provoked: calling unsafe handlers safe.

        A handler that defines a closure containing ``raise`` has not
        re-raised anything. Counting it would silently exempt the site.
        """
        tree = ast.parse(
            "try:\n"
            "    check()\n"
            "except ValueError as exc:\n"
            "    def _later():\n"
            "        raise\n"
            "    register(_later)\n"
        )
        handler = next(
            node for node in ast.walk(tree) if isinstance(node, ast.ExceptHandler)
        )
        assert not _reraises_the_original(handler), (
            "a `raise` inside a nested def was counted as the handler "
            "re-raising; every such handler would then be exempted without "
            "anybody writing a reason for it"
        )

    def test_a_real_reraise_beside_a_nested_def_still_counts(self):
        """The other direction, so the narrowing did not break the rule."""
        tree = ast.parse(
            "try:\n"
            "    check()\n"
            "except ValueError as exc:\n"
            "    def _later():\n"
            "        pass\n"
            "    log(exc)\n"
            "    raise\n"
        )
        handler = next(
            node for node in ast.walk(tree) if isinstance(node, ast.ExceptHandler)
        )
        assert _reraises_the_original(handler)

    def test_it_recognises_the_coded_first_escape_hatch(self):
        """#165's shape must pass, or the sanctioned repair fails the gate."""
        tree = ast.parse(
            "try:\n"
            "    check()\n"
            "except CodedValueError as exc:\n"
            "    raise CodedValueError(exc.code, f'{field}: {exc}') from exc\n"
            "except ValueError as exc:\n"
            "    raise ValueError(f'{field}: {exc}') from exc\n"
        )
        node = next(n for n in ast.walk(tree) if isinstance(n, ast.Try))
        coded_first = False
        verdicts = []
        for handler in node.handlers:
            caught = _caught_names(handler)
            if caught & CODED_CATCHES:
                coded_first = True
                continue
            if caught & ABSORBING_CATCHES:
                verdicts.append(coded_first or _reraises_the_original(handler))
        assert verdicts == [True], (
            "the broad clause behind an `except CodedValueError` that "
            "re-raises coded was not recognised as safe; option (a) would "
            "then be unavailable as a repair and this gate would force "
            "exemptions instead of fixes"
        )

    def test_the_order_of_the_clauses_matters(self):
        """A coded clause *after* the broad one never runs, so it is no fix."""
        tree = ast.parse(
            "try:\n"
            "    check()\n"
            "except ValueError as exc:\n"
            "    raise HTTPException(status_code=422, detail=str(exc)) from exc\n"
            "except CodedValueError as exc:\n"
            "    raise\n"
        )
        node = next(n for n in ast.walk(tree) if isinstance(n, ast.Try))
        coded_first = False
        verdicts = []
        for handler in node.handlers:
            caught = _caught_names(handler)
            if caught & CODED_CATCHES:
                coded_first = True
                continue
            if caught & ABSORBING_CATCHES:
                verdicts.append(coded_first or _reraises_the_original(handler))
        assert verdicts == [False], (
            "a CodedValueError clause written *below* the ValueError clause "
            "was treated as protecting it. Python never reaches it -- the "
            "broad clause matches first -- so accepting that ordering would "
            "bless a repair that does nothing"
        )


class TestTheAbsorbingSweepIsComplete:
    """Both directions, so neither a new site nor a stale entry survives."""

    def test_every_absorbing_handler_is_reraising_or_judged(self):
        unjudged = {
            key: info["caught"]
            for key, info in _unjudged_absorbing_handlers().items()
        }
        assert not unjudged, (
            "these handlers in the API layer catch a type that swallows a "
            "CodedValidationError, and neither re-raise it nor let an "
            "earlier `except CodedValueError` clause take it first: "
            f"{unjudged}. A coded refusal raised anywhere beneath them "
            "loses its `code` and its `context`. Either re-raise "
            "(`raise`), add an `except CodedValueError as exc: raise` "
            "clause above the broad one, or add an entry to "
            "JUDGED_ABSORBING_HANDLERS saying what a client loses and why "
            "that is right."
        )

    def test_no_entry_describes_a_handler_that_is_gone(self):
        """A list nothing re-checks is how a stale survey stays believed."""
        needs_judging = {
            key for key, info in _absorbing_handlers().items() if not info["safe"]
        }
        stale = sorted(set(JUDGED_ABSORBING_HANDLERS) - needs_judging)
        assert not stale, (
            "JUDGED_ABSORBING_HANDLERS describes handlers that no longer "
            f"absorb a coded refusal: {stale}. If they were repaired, "
            "delete the entry; if they moved or were renamed, re-anchor it."
        )

    def test_the_four_deliberate_translations_are_still_exactly_four(self):
        """The set a careless rule would have broken, pinned by name.

        Three lookup routes and one health probe. They are the reason the
        exemption mechanism is an explicit dict rather than an inference
        from shape: every one of them is, mechanically, a coded refusal
        being turned into something else, and every one of them is right.
        """
        deliberate = sorted(
            key
            for key in JUDGED_ABSORBING_HANDLERS
            if "lookup.py" in key or "::_sanitized_endpoint" in key
        )
        assert deliberate == [
            "backend/app/api/routes/health.py::_sanitized_endpoint",
            "backend/app/api/routes/lookup.py::_resolve_species_list",
            "backend/app/api/routes/lookup.py::lookup_species",
            "backend/app/api/routes/lookup.py::lookup_species_calculation",
        ], (
            "the four deliberate translations are no longer the four this "
            f"gate was written around: {deliberate}. If one was repaired, "
            "say so here; if a fifth was added, it needs the same argument "
            "the other four carry."
        )
        found = _absorbing_handlers()
        for key in deliberate:
            assert key in found, (
                f"{key} is exempted but the scan no longer finds it, so the "
                "exemption is protecting nothing and would hide a "
                "regression at the same site"
            )

    def test_the_integrity_route_has_not_regrown_its_value_error_handler(self):
        """Named, because this is the site the whole second rule is for.

        ``GET /scientific/artifacts/integrity`` carried
        ``except ValueError -> HTTPException(422, str(exc))`` until #212
        removed it. The wire test that asserts what it publishes instead is
        ``tests/api/scientific/test_api_scientific_artifact_integrity.py``
        ``::test_client_sort_is_rejected_like_every_other_scientific_read``;
        this is the shape half, which cannot see a response body.
        """
        path = "backend/app/api/routes/scientific/artifacts.py"
        regrown = sorted(
            key for key in _unjudged_absorbing_handlers() if key.startswith(path)
        )
        assert not regrown, (
            f"{path} has regrown a handler that absorbs a coded refusal "
            f"without re-raising it: {regrown}. `search_artifact_integrity` "
            "raises `client_sort_not_supported` and `offset_too_large`; "
            "wrapping it re-answers 422 as `http_422`"
        )
