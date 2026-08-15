"""Every ownership refusal a request can provoke is provoked by a route test.

Why this file exists
--------------------
``app.services.calculation_ownership`` is one comparison behind eight
codes and fifteen call sites. Three times now somebody has read that
module and reached a *different* answer about which of its refusals a
depositor can actually receive: ``statmech_torsion_scan_calculation_owner_mismatch``
was recorded as unreachable, then as reachable by exactly one route, then
by two; ``applied_energy_correction_source_calculation_owner_mismatch``
sat at ``Reach.guard`` on the same misreading until #195 measured it.
Each correction came from a person re-reading the module, and nothing in
the suite would have gone red had the wrong reading survived.

The four files that provoke these refusals on the wire
(``test_api_bundle_ownership_codes``, ``test_api_bundle_torsion_scan_ownership``,
``test_api_network_pdep_ownership``, ``test_api_statmech_citation_ownership``,
plus the standalone-route cases in ``test_api_uploads`` and
``test_api_upload_key_and_role_contracts``) each prove *one* refusal.
None of them, and nothing else, asserts that the set is **complete** —
that no code in the module is left with its behaviour asserted only at
the function level, which is precisely the defect #115 found for
``scf_stability`` and #146 for supersession. A new ``W_*`` constant, or a
route test rewritten to assert a different code, changes what a depositor
can receive and leaves the suite green today.

So this file checks the completeness claim rather than any one refusal.

Why it is a source scan and not more provocations
-------------------------------------------------
The obvious alternative — re-post every payload here and tally the codes
— was rejected for the reason ``tests/error_code_observer.py`` gives for
refusing a tally with a floor: it becomes a second thing to keep in step
with the suite, and it goes green for the wrong reason the day a fixture
moves. It would also duplicate eight payloads that already exist, so a
single fixture drifting would turn two files red and tell a reader
nothing new.

What is missing is not another provocation. It is the statement that the
provocations cover the codes, held against the source of both. That is
the shape ``tests/scripts/test_read_spec_names_resolve.py`` already uses
for prose that names symbols, and it is cheap: no database, no requests.

The branch dimension, which is the one that was actually unwatched
--------------------------------------------------------------------
``assert_owned_by`` has two refusing branches, not one. It compares
against a **species entry** or against a **transition-state entry**, and
which one fired is reported to the client as ``context["owner_kind"]`` —
a value a client may branch on, and therefore contract. A code being
provoked somewhere says nothing about which of the two branches produced
it, and the transition-state branch is reachable on only three routes
(``/uploads/networks/pdep``, ``/uploads/computed-reaction``,
``/uploads/kinetics``) out of the seven that reach the rule at all.
``test_both_owner_kinds_are_asserted_on_the_wire`` is the assertion that
neither branch has quietly lost every route that exercises it.

Not covered here, deliberately
------------------------------
* ``transport_source_calculation_owner_mismatch`` is catalogued
  ``Reach.guard`` — no write path can produce the condition, so no route
  test can exist. It is excluded by reading the catalogue, not by name,
  so promoting it to reachable makes this file demand a provocation.
* The ``ValueError`` ``assert_owned_by`` raises when given neither owner
  is a programming-error guard with no code and no route; it is pinned in
  ``tests/invariants/test_structure_invariants.py``.
"""

from __future__ import annotations

import ast
from pathlib import Path

from app.api.code_catalogue import CATALOGUE, Reach
from app.services import calculation_ownership

#: This module's own literals must never satisfy its own checks.
_SELF = Path(__file__).name

_API_TESTS = Path(__file__).parent


def _ownership_codes() -> frozenset[str]:
    """Every code the ownership module defines, read off the module.

    Derived rather than listed: a ``W_*`` constant added tomorrow joins
    the expected set without anyone remembering to edit this file, which
    is the whole point of the check.
    """
    return frozenset(
        value
        for name, value in vars(calculation_ownership).items()
        if name.startswith("W_") and isinstance(value, str)
    )


def _guard_only(codes: frozenset[str]) -> frozenset[str]:
    """The subset the catalogue says no request can produce."""
    return frozenset(
        entry.code
        for entry in CATALOGUE
        if entry.code in codes and entry.reach is Reach.guard
    )


def _docstring_nodes(tree: ast.AST) -> set[int]:
    """``id()`` of every string constant that is a docstring.

    A code mentioned in prose is not an assertion about it. Excluding
    docstrings is what stops this file from being satisfied by the very
    thing it exists to distrust -- a claim written down and never run.
    """
    ids: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(
            node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
        ):
            continue
        body = getattr(node, "body", None)
        if not body:
            continue
        first = body[0]
        if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
            if isinstance(first.value.value, str):
                ids.add(id(first.value))
    return ids


def executable_string_literals(source: str) -> frozenset[str]:
    """String literals in *source* that are not docstrings.

    Comments never reach the AST at all, so they are excluded for free.
    Public because :func:`test_the_scan_ignores_prose` calls it on a
    synthetic module -- a scan nobody has tried to fool is a scan whose
    result means nothing.
    """
    tree = ast.parse(source)
    docstrings = _docstring_nodes(tree)
    return frozenset(
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in docstrings
    )


def _posting_test_modules() -> dict[str, frozenset[str]]:
    """Route-test module name → its non-docstring string literals.

    Restricted to modules that actually issue a request. A file that
    names a code without posting anything is asserting something about
    the workflow, not about what a depositor receives, and this file
    exists because those two are not the same claim.
    """
    modules: dict[str, frozenset[str]] = {}
    for path in sorted(_API_TESTS.glob("test_*.py")):
        if path.name == _SELF:
            continue
        source = path.read_text()
        tree = ast.parse(source)
        posts = any(
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "post"
            for node in ast.walk(tree)
        )
        if posts:
            modules[path.name] = executable_string_literals(source)
    return modules


def test_every_reachable_ownership_code_is_asserted_by_a_route_test() -> None:
    """A refusal a depositor can receive is a refusal a route test provokes.

    What this protects: adding a ``W_*`` code, or making an existing one
    reachable, without also proving on the wire that a client receives
    it. That gap is invisible today -- the guard is exercised by unit
    tests, the catalogue lists the code, and no request in the suite has
    ever produced it.
    """
    codes = _ownership_codes()
    assert codes, "the ownership module defines no W_* codes -- the scan is broken"

    reachable = codes - _guard_only(codes)
    # A floor, not a non-empty check (#186): the assertion below is an
    # *absence* -- it passes perfectly against an empty ``reachable``, so
    # a catalogue edit marking every code ``Reach.guard`` would silence
    # it rather than fail it. Measured 2026-08-16: 8 codes, 1 guard.
    assert len(reachable) >= 7, (
        f"only {len(reachable)} ownership codes are reachable by a request; "
        "7 were when this floor was measured. If a code genuinely became "
        "unreachable, lower the floor in the same change that says why."
    )

    modules = _posting_test_modules()
    assert modules, "no route test module posts anything -- the scan is broken"

    seen = frozenset().union(*modules.values())
    missing = sorted(reachable - seen)
    assert not missing, (
        "these ownership codes are reachable by a request but no route test "
        f"under tests/api/ asserts them: {missing}. Either provoke each one "
        "through the route a depositor would use, or -- if no write path can "
        "produce it -- record that in app/api/code_catalogue.py as Reach.guard "
        "with the reason."
    )


def test_both_owner_kinds_are_asserted_on_the_wire() -> None:
    """Each refusing branch of the shared guard is provoked by some route.

    ``assert_owned_by`` refuses against a species entry or against a
    transition-state entry, and tells the client which in
    ``context['owner_kind']``. A code being covered says nothing about
    which branch produced it: every reachable code is provoked
    species-side, and only four call sites can reach the
    transition-state branch at all.

    Deleting that branch is caught today by five route tests across
    three routes -- but all five live in files whose own docstrings
    anticipate being rewritten (the PDep pair explicitly, if a TS-side
    key is ever narrowed at the schema layer). This is what would notice
    the rewrite dropping the branch on the way past.
    """
    codes = _ownership_codes()
    modules = _posting_test_modules()

    for owner_kind in ("species_entry", "transition_state_entry"):
        provokers = sorted(
            name
            for name, literals in modules.items()
            if owner_kind in literals and literals & codes
        )
        assert provokers, (
            f"no route test under tests/api/ asserts owner_kind="
            f"{owner_kind!r} alongside an ownership code, so the "
            f"corresponding branch of assert_owned_by is exercised only "
            f"at the function level. Provoke it through a route: a "
            f"depositor receives context['owner_kind'], so it is contract."
        )


def test_the_scan_ignores_prose() -> None:
    """The scanner must not count a code that is only *written about*.

    Without this, both tests above would pass on a suite that had deleted
    every provocation and kept the docstrings explaining them -- the
    exact failure mode they exist to catch, and the one this project
    keeps rediscovering. So the scan is run against a module built to
    fool it.
    """
    fooling_source = (
        '"""A docstring naming fake_owner_mismatch, which is not asserted."""\n'
        "\n"
        "# A comment naming commented_owner_mismatch too.\n"
        "\n"
        "def helper():\n"
        '    """Prose naming nested_owner_mismatch."""\n'
        '    return "real_owner_mismatch"\n'
    )
    literals = executable_string_literals(fooling_source)

    assert "real_owner_mismatch" in literals, (
        "the scan cannot see an ordinary string literal, so a green result "
        "from it means nothing"
    )
    assert "fake_owner_mismatch" not in literals
    assert "commented_owner_mismatch" not in literals
    assert "nested_owner_mismatch" not in literals


def test_the_transport_guard_is_the_only_unreachable_ownership_code() -> None:
    """The exemption is one named code, not an open category.

    ``test_every_reachable_ownership_code_is_asserted_by_a_route_test``
    excuses whatever the catalogue marks ``Reach.guard``, which means a
    code could be excused from needing a route test by editing the
    catalogue alone. This pins the exemption list so that widening it is
    a deliberate, reviewable act rather than a way to make the check
    above go quiet.
    """
    codes = _ownership_codes()
    assert _guard_only(codes) == frozenset(
        {"transport_source_calculation_owner_mismatch"}
    ), (
        "the set of ownership codes no request can produce has changed. If a "
        "code became unreachable, say why in its catalogue note; if one became "
        "reachable, it now needs a route test that provokes it."
    )
