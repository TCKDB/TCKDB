"""Drift guards for the scientific check register.

The register's whole value is that it cannot quietly stop describing the
code. These tests are what makes that true:

* a registered Python check must resolve to a real callable;
* a registered database constraint or trigger must exist in the live
  PostgreSQL schema, queried from ``pg_constraint`` / ``pg_trigger``
  rather than trusted as a string;
* a declared code must appear verbatim in the source of the module
  defining the enforcing function — so renaming the code string fails
  here even though the declaration sits next to it;
* every file constructing a ``ScientificCheck`` must be a declared
  module, so forgetting to register one is caught rather than silently
  omitted;
* the generated document must be in sync with the declarations.
"""

from __future__ import annotations

import ast
import inspect
import re
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import text

from app.scientific_checks import (
    CheckTier,
    CodeChannel,
    ConstantThreshold,
    DatabaseConstraint,
    DesignPosition,
    ProvenanceThreshold,
    PythonCheck,
    ScientificCheck,
)
from app.scientific_checks.declarations import (
    DECLARING_MODULES,
    constraint_rejections,
    register,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
BACKEND_ROOT = REPO_ROOT / "backend"
GENERATOR = BACKEND_ROOT / "scripts" / "generate_scientific_check_register.py"
REGISTER_DOC = REPO_ROOT / "docs" / "guides" / "scientific_check_register.md"

#: The end-to-end proof that an ``error_envelope`` code actually arrives in
#: an HTTP response body. Named here so the guard below can require every
#: such code to appear in it.
ENVELOPE_PROOF = (
    BACKEND_ROOT / "tests" / "api" / "test_api_scientific_rejection_codes.py"
)

#: The equivalent proof for the positions PostgreSQL holds: it violates
#: each named constraint for real and asserts the declared code reaches
#: the 409 body.
CONSTRAINT_PROOF = (
    BACKEND_ROOT / "tests" / "api" / "test_api_database_constraint_codes.py"
)

#: The exception types that carry a code out of a check as an attribute.
CODED_ERROR_TYPES = frozenset({"CodedValueError", "CodedValidationError"})

REGISTER = register()
REJECTIONS = constraint_rejections()


def test_register_is_non_empty_and_proportionate() -> None:
    """The register must stay a filter, not a dump of every validator.

    The backend holds roughly 326 Pydantic validators and 225 check
    constraints. If this count approaches that order the inclusion test
    has stopped being applied and every entry's claim is diluted.
    """
    assert 10 <= len(REGISTER) <= 40, (
        f"{len(REGISTER)} entries. Below ~10 the register is not describing "
        "the system; above ~40 the inclusion test ('could this check be wrong "
        "in an interesting way?') has stopped being applied."
    )


def test_every_python_check_resolves_to_real_code() -> None:
    """Each ``PythonCheck`` names a live callable with locatable source."""
    for check in REGISTER:
        for site in check.enforced_by:
            if not isinstance(site, PythonCheck):
                continue
            assert callable(site.func), f"{check.asserts!r}: {site.func!r} is not callable"
            source_file = inspect.getsourcefile(site.func)
            assert source_file is not None, f"{check.asserts!r}: no source file"
            assert Path(source_file).exists()
            # Resolving the location is the guard: a renamed or deleted
            # function cannot be imported by the declaring module at all,
            # so this test never even runs — which is the point.
            assert ":" in site.location


def _source_without_declarations(module) -> str:
    """Module source with every ``ScientificCheck(...)`` literal blanked out.

    Without this the code check is tautological: a declaration sitting in
    the same module as its check would satisfy the assertion by quoting
    itself, and renaming the real code string would go unnoticed.
    """
    source = inspect.getsource(module)
    lines = source.splitlines()
    tree = ast.parse(source)
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        value = node.value
        if not isinstance(value, ast.Call):
            continue
        callee = value.func
        name = callee.id if isinstance(callee, ast.Name) else getattr(callee, "attr", "")
        if name != "ScientificCheck":
            continue
        end = node.end_lineno or node.lineno
        for index in range(node.lineno - 1, end):
            lines[index] = ""
    return "\n".join(lines)


def test_every_declared_code_appears_in_the_enforcing_module() -> None:
    """A declared code must be a string the enforcing module actually holds.

    Checked against the module that defines the *function*, with the
    register declarations themselves stripped, so the assertion can only
    be satisfied by the code string the check really uses.
    """
    for check in REGISTER:
        if not check.emitted or not check.codes:
            continue
        python_sites = [s for s in check.enforced_by if isinstance(s, PythonCheck)]
        if not python_sites:
            continue
        sources = []
        for site in python_sites:
            module = inspect.getmodule(site.func)
            assert module is not None
            sources.append(_source_without_declarations(module))
        for code in check.codes:
            # Bare substring: codes are routinely embedded mid-message
            # ("... (species_geometry_composition_mismatch). A deposited
            # structure must ..."), so a quote-delimited match would miss
            # them. The tokens are long and distinctive enough that an
            # accidental match is not a real risk.
            assert any(code in src for src in sources), (
                f"Code {code!r} is declared as emitted for {check.asserts!r}, "
                "but no module defining its enforcing function contains that "
                "literal string outside the register declaration itself. "
                "Either the code was renamed and the register not updated, or "
                "the entry should set emitted=False."
            )


def _coded_error_codes(module) -> tuple[set[str], bool]:
    """Codes this module raises through the typed path, and whether any is dynamic.

    Reads the first positional argument of every ``CodedValueError`` /
    ``CodedValidationError`` construction, resolving a bare name against
    the module's own top-level string constants. A first argument that is
    neither a literal nor a resolvable constant — ``codes.pop()`` in
    ``raise_for_blocking_findings``, which reports whichever code the
    findings agreed on — is reported as *dynamic* rather than guessed at.

    Register declarations are blanked out first, for the same reason the
    code-literal guard blanks them: a declaration must not be able to
    satisfy an assertion about the check.
    """
    source = _source_without_declarations(module)
    tree = ast.parse(source)

    constants: dict[str, str] = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if not isinstance(node.value, ast.Constant):
            continue
        if not isinstance(node.value.value, str):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name):
                constants[target.id] = node.value.value

    resolved: set[str] = set()
    dynamic = False
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        callee = node.func
        name = callee.id if isinstance(callee, ast.Name) else getattr(callee, "attr", "")
        if name not in CODED_ERROR_TYPES:
            continue
        if not node.args:
            dynamic = True
            continue
        first = node.args[0]
        if isinstance(first, ast.Constant) and isinstance(first.value, str):
            resolved.add(first.value)
        elif isinstance(first, ast.Name) and first.id in constants:
            resolved.add(constants[first.id])
        elif isinstance(first, ast.Attribute) and first.attr in constants:
            resolved.add(constants[first.attr])
        else:
            dynamic = True
    return resolved, dynamic


def test_every_entry_declares_where_its_code_reaches_a_client() -> None:
    """A code and a channel imply each other, and blocking checks name one.

    The gap this exists to catch was found by reading, not by CI: eleven
    entries declared no code at all, and every entry that *did* declare
    one spelled it inside an English sentence that the response envelope
    never promoted — so the register read as though a client could branch
    on elemental balance when it could only branch on
    ``validation_error``. The construction-time invariant in
    :class:`ScientificCheck` covers the first half; this covers the half
    that matters for a refusal, which is that a check able to refuse an
    upload must say something a program can act on.
    """
    for check in REGISTER:
        assert bool(check.codes) == (check.channel is not CodeChannel.none), (
            f"{check.asserts!r} declares {len(check.codes)} code(s) with "
            f"channel={check.channel.value}"
        )
        if check.tier is not CheckTier.block:
            continue
        if not any(isinstance(s, PythonCheck) for s in check.enforced_by):
            continue
        assert check.channel is CodeChannel.error_envelope, (
            f"{check.asserts!r} refuses payloads from Python and would reach a "
            f"client as channel={check.channel.value}. A blocking check whose "
            "refusal carries no code in the error envelope leaves a client "
            "string-matching English to find out what it did wrong — which is "
            "the exact condition this field was added to make impossible."
        )


def test_error_envelope_codes_are_raised_through_the_typed_path() -> None:
    """An ``error_envelope`` code must be an argument, never only a substring.

    The parenthesised convention — ``"... (reaction_mass_balance_failed)."``
    — satisfied the older "the literal appears in the module" guard while
    reaching no client at all, because the envelope promoter's pattern
    requires a colon. Requiring the code to be the *first argument* of a
    coded-error construction is the difference between a code the module
    mentions and a code the module reports.
    """
    for check in REGISTER:
        if check.channel is not CodeChannel.error_envelope:
            continue
        python_sites = [s for s in check.enforced_by if isinstance(s, PythonCheck)]
        assert python_sites, (
            f"{check.asserts!r} claims to reach the error envelope but names no "
            "Python enforcement site, so nothing can raise it."
        )
        resolved: set[str] = set()
        dynamic = False
        for site in python_sites:
            module = inspect.getmodule(site.func)
            assert module is not None
            site_resolved, site_dynamic = _coded_error_codes(module)
            resolved |= site_resolved
            dynamic |= site_dynamic
        assert resolved or dynamic, (
            f"{check.asserts!r} is declared to reach the error envelope, but no "
            "module defining its enforcing function constructs a "
            f"{' or '.join(sorted(CODED_ERROR_TYPES))} at all — so it raises a "
            "bare ValueError and the response body will say validation_error."
        )
        for code in check.codes:
            assert code in resolved or dynamic, (
                f"Code {code!r} is declared to reach the error envelope, but no "
                "enforcing module passes it to a coded error. Embedding a code "
                "in a message is not reporting it: only the exception's own "
                "attribute survives into the response body's code field."
            )


def test_every_error_envelope_code_is_proved_end_to_end() -> None:
    """Each envelope code must be asserted against a real HTTP response body.

    The two guards above are about the raise site. Neither can tell you
    whether the code survives Pydantic wrapping, the exception handler and
    the JSON encoder — and that whole path is where the codes were being
    lost. So the register's claim is anchored to a test that reads
    ``response.json()["code"]``, and a new envelope code cannot be
    declared without one.
    """
    assert ENVELOPE_PROOF.exists(), f"{ENVELOPE_PROOF} is missing"
    proof = ENVELOPE_PROOF.read_text()
    declared = sorted(
        {
            code
            for check in REGISTER
            if check.channel is CodeChannel.error_envelope
            for code in check.codes
        }
    )
    assert declared, "no error_envelope codes — this guard would pass vacuously"
    missing = [code for code in declared if code not in proof]
    assert not missing, (
        f"These codes are declared to reach the 422 body but no end-to-end test "
        f"in {ENVELOPE_PROOF.name} names them: {missing}. Add one that asserts "
        "response.json()['code'] == the code — not that the message contains "
        "it, which is the assertion that let the gap survive."
    )


def test_constraint_rejections_exist_to_verify() -> None:
    """Guard the guard: an empty mapping makes the three below vacuous."""
    assert REJECTIONS, (
        "No DatabaseConstraint declares a rejection_code, so every "
        "database-enforced scientific position is back to arriving as a "
        "generic 409 and the guards below pass by describing nothing."
    )


def test_constraint_rejections_are_usable_runtime_strings() -> None:
    """A 409's code and sentence must be ASCII, and the sentence a sentence.

    ASCII because these are runtime strings on the wire, and the register
    prose around them is not -- the ``asserts`` field is written with em
    dashes for the document. Copying one of those into a response body is
    the easy mistake, so it is checked rather than remembered.
    """
    for name, (code, detail) in sorted(REJECTIONS.items()):
        assert code.isascii() and detail.isascii(), (
            f"{name}: non-ASCII in the 409 payload ({code!r} / {detail!r}). "
            "The register's prose fields may use typographic punctuation; a "
            "runtime string may not."
        )
        assert re.fullmatch(r"[a-z][a-z0-9_]*", code), (
            f"{name}: {code!r} is not a matchable token. Clients branch on "
            "this string, and the generated client enum derives its member "
            "name from it."
        )
        assert len(detail) > 40 and detail.endswith("."), (
            f"{name}: {detail!r} is not a sentence a depositor can act on. "
            "The point of naming the constraint is to say which scientific "
            "rule refused the write, not to emit a second token."
        )


def test_a_constraint_that_names_a_code_is_not_declared_as_carrying_none() -> None:
    """``channel=none`` and a named constraint cannot both be true.

    ``CodeChannel.none`` is the register's way of saying *no
    machine-readable code reaches anybody*. The moment a constraint
    declares a rejection code that claim is false, and the entry that
    still says ``none`` is the one a reader would trust.
    """
    for check in REGISTER:
        named = [
            site
            for site in check.enforced_by
            if isinstance(site, DatabaseConstraint) and site.rejection_code
        ]
        if not named:
            continue
        assert check.channel is not CodeChannel.none, (
            f"{check.asserts!r} declares channel=none while "
            f"{[site.name for site in named]} name codes a client now receives."
        )


def test_every_constraint_rejection_code_is_provoked_against_postgres() -> None:
    """Each named constraint must be violated for real by the proof file.

    The weak version of this guard would assert the mapping is non-empty
    and stop. That would not catch the failure that actually matters:
    a constraint whose *name* in the register no longer matches what
    PostgreSQL reports, because a migration renamed it or because the
    violation trips a different constraint first. Neither is visible from
    Python -- only from provoking the violation and reading
    ``diag.constraint_name`` -- so the register's claim is anchored to a
    test that does exactly that, and a new rejection code cannot be
    declared without one.
    """
    assert CONSTRAINT_PROOF.exists(), f"{CONSTRAINT_PROOF} is missing"
    from tests.api.test_api_database_constraint_codes import PROVOCATIONS

    missing = sorted(set(REJECTIONS) - set(PROVOCATIONS))
    assert not missing, (
        f"These constraints declare a rejection code but {CONSTRAINT_PROOF.name} "
        f"never provokes them: {missing}. Add an entry to PROVOCATIONS -- the "
        "code is otherwise a claim that the mapping key matches what PostgreSQL "
        "reports, which nothing has checked."
    )
    stale = sorted(set(PROVOCATIONS) - set(REJECTIONS))
    assert not stale, (
        f"{CONSTRAINT_PROOF.name} provokes constraints the register no longer "
        f"names: {stale}. Drop the provocation or restore the declaration."
    )


def test_the_handler_reads_the_register_rather_than_its_own_table() -> None:
    """The API's mapping must *be* the register's, not a copy of it.

    A hand-kept table in ``app/api/errors.py`` would pass every other
    guard here while drifting from the schema at its own pace. Comparing
    the objects is what makes "derived, never written twice" enforced.
    """
    from app.api.errors import _constraint_rejections

    assert _constraint_rejections() == REJECTIONS


def test_declaring_modules_covers_every_file_that_declares_a_check() -> None:
    """No file may construct a ``ScientificCheck`` without being collected."""
    declared_files = {
        Path(inspect.getsourcefile(module)).resolve()  # type: ignore[arg-type]
        for module in DECLARING_MODULES
    }
    found = subprocess.run(
        [
            "git",
            "grep",
            "-l",
            # --untracked matters: a declaration added in a brand-new file
            # is exactly the case this guard exists for, and plain
            # ``git grep`` only searches tracked files.
            "--untracked",
            "-E",
            r"^[A-Z_]+ = ScientificCheck\(",
            "--",
            "*.py",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    constructing = {
        (REPO_ROOT / line).resolve()
        for line in found.stdout.splitlines()
        if line.strip()
    }
    missing = constructing - declared_files
    assert not missing, (
        "These files construct a ScientificCheck but are not in "
        "DECLARING_MODULES, so their entries are silently absent from the "
        f"register: {sorted(str(p.relative_to(REPO_ROOT)) for p in missing)}"
    )


def test_entries_are_uniquely_addressable() -> None:
    """No two entries share a (group, sort_key) slot, and codes stay local.

    A code may legitimately appear in more than one entry — ADR 0008 has
    ``n_imag_contradicts_minimum`` naming the *finding* while the declared
    stationary-point kind decides the tier — but a code scattered across
    unrelated groups would mean two different claims share one label.
    """
    slots = [(check.group, check.sort_key) for check in REGISTER]
    assert len(slots) == len(set(slots)), "duplicate (group, sort_key) slot"

    groups_by_code: dict[str, set[str]] = {}
    for check in REGISTER:
        for code in check.codes:
            groups_by_code.setdefault(code, set()).add(check.group)
    scattered = {code: groups for code, groups in groups_by_code.items() if len(groups) > 1}
    assert not scattered, f"codes shared across unrelated groups: {scattered}"


def test_every_entry_states_its_tier_rationale_and_enforcement() -> None:
    """Membership is the claim, so no entry may be a stub."""
    for check in REGISTER:
        assert check.enforced_by, f"{check.asserts!r} names no enforcement site"
        assert len(check.asserts) > 30, f"{check.asserts!r} is not a sentence"
        assert len(check.tier_rationale) > 60, (
            f"{check.asserts!r} has no real ADR 0008 tier justification"
        )
        assert check.adr, f"{check.asserts!r} cites no ADR"
        if check.tier is CheckTier.block:
            assert check.escape_hatch is not None, (
                f"{check.asserts!r} blocks but records no escape hatch. If "
                "there genuinely is none, say so explicitly — a blocking "
                "check with no documented door is exactly what a referee will "
                "probe."
            )


def test_anchors_name_the_function_rather_than_a_line() -> None:
    """No anchor may carry a line number, in the declarations or the document.

    This is what keeps :func:`test_generated_document_is_in_sync` a *semantic*
    gate. With line numbers the register is a build artifact of every line
    above every declared check: #109 added one line to
    ``app/services/geometry_validation.py``, shifting a check from 120 to 121,
    and turned the tier red on main -- the entire drift being that digit.

    A gate that fires on cosmetic movement gets regenerated reflexively
    without anyone reading the diff, which is how a real drift eventually
    lands unnoticed. So the anchor names the function, and the sync test keeps
    its teeth for the things that matter: a renamed check, a removed code, a
    vocab key that no longer resolves.

    Asserted on both sides -- the live objects and the committed document --
    because either one alone could regress without the other noticing.
    """
    line_anchor = re.compile(r"\.py:\d+")

    offenders = [
        (check.asserts, site.location)
        for check in REGISTER
        for site in check.enforced_by
        if isinstance(site, PythonCheck) and line_anchor.search(site.location)
    ]
    offenders += [
        (check.asserts, threshold.location)
        for check in REGISTER
        for threshold in check.thresholds
        if isinstance(threshold, ProvenanceThreshold)
        and line_anchor.search(threshold.location)
    ]
    assert not offenders, (
        "These anchors carry a line number, which makes the register move "
        "whenever the code above them does:\n"
        + "\n".join(f"  {asserts!r}: {location}" for asserts, location in offenders)
    )

    document_offenders = sorted(set(line_anchor.findall(REGISTER_DOC.read_text())))
    assert not document_offenders, (
        f"{REGISTER_DOC} contains line-number anchors: {document_offenders}. "
        "Regenerate it; if they persist, a `location` property has regressed "
        "to `inspect.getsourcelines`."
    )


def test_generated_document_is_in_sync() -> None:
    """The committed register must match what the declarations render."""
    assert REGISTER_DOC.exists(), f"{REGISTER_DOC} has not been generated"
    result = subprocess.run(
        [sys.executable, str(GENERATOR), "--check"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, (
        f"{REGISTER_DOC} is out of date with the declarations.\n"
        f"{result.stdout}\n{result.stderr}"
    )


# ---------------------------------------------------------------------------
# Database-level entries, verified against live schema metadata
# ---------------------------------------------------------------------------


def _database_constraints() -> list[tuple[ScientificCheck, DatabaseConstraint]]:
    return [
        (check, site)
        for check in REGISTER
        for site in check.enforced_by
        if isinstance(site, DatabaseConstraint)
    ]


def test_there_are_database_entries_to_verify() -> None:
    """Guard the guard: an empty list would make the next test vacuous."""
    assert _database_constraints(), (
        "No DatabaseConstraint entries. Either the register lost its "
        "database-level checks or the collection broke; either way the "
        "existence check below would pass vacuously."
    )


@pytest.mark.parametrize(
    ("check", "site"),
    _database_constraints(),
    ids=lambda value: value.name if isinstance(value, DatabaseConstraint) else "",
)
def test_database_object_exists_in_live_schema(
    check: ScientificCheck, site: DatabaseConstraint, db_session
) -> None:
    """A registered constraint or trigger must exist in PostgreSQL.

    Queried from the catalog, not from ``__table_args__``: the ORM holds
    the short name that ``NAMING_CONVENTION`` expands, so only the
    database knows what the object is really called.
    """
    if site.kind == "trigger":
        found = db_session.execute(
            text(
                "SELECT count(*) FROM pg_trigger t "
                "JOIN pg_class c ON c.oid = t.tgrelid "
                "WHERE NOT t.tgisinternal AND t.tgname = :name AND c.relname = :table"
            ),
            {"name": site.name, "table": site.table},
        ).scalar_one()
        assert found == 1, (
            f"Trigger {site.name!r} on {site.table!r} is registered as "
            f"enforcing {check.asserts!r}, but no such trigger exists in the "
            "live schema."
        )
        return

    found = db_session.execute(
        text(
            "SELECT count(*) FROM pg_constraint con "
            "JOIN pg_class c ON c.oid = con.conrelid "
            "WHERE con.conname = :name AND c.relname = :table"
        ),
        {"name": site.name, "table": site.table},
    ).scalar_one()
    assert found == 1, (
        f"Constraint {site.name!r} on {site.table!r} is registered as "
        f"enforcing {check.asserts!r}, but no such constraint exists in the "
        "live schema. Note the register must carry the *expanded* name "
        "PostgreSQL holds, not the short form in __table_args__."
    )


def _provenance_thresholds() -> list[tuple[ScientificCheck, ProvenanceThreshold]]:
    return [
        (check, threshold)
        for check in REGISTER
        for threshold in check.thresholds
        if isinstance(threshold, ProvenanceThreshold)
    ]


def test_there_are_provenance_thresholds_to_verify() -> None:
    """Guard the guard: an empty list makes the vocab check below vacuous."""
    assert _provenance_thresholds(), (
        "No ProvenanceThreshold entries. Either ADR 0012's tau lost its "
        "declaration or collection broke; either way the parameter-key "
        "existence check would pass vacuously."
    )


@pytest.mark.parametrize(
    ("check", "threshold"),
    _provenance_thresholds(),
    ids=lambda value: value.name if isinstance(value, ProvenanceThreshold) else "",
)
def test_provenance_threshold_keys_exist_in_the_parameter_vocabulary(
    check: ScientificCheck, threshold: ProvenanceThreshold, db_session
) -> None:
    """Every key a provenance threshold reads must be a real vocab key.

    This is the guard that makes a provenance-derived threshold as
    checkable as a constant. ``calculation_parameter.canonical_key`` is
    FK-constrained against ``calculation_parameter_vocab``, so a key that
    was renamed, or never seeded, can never be written — and the
    threshold would then resolve to its fallback forever while the
    register went on claiming it keys on that provenance. Queried from
    the live table rather than from the parser's mapping, because only
    the database decides what a parameter row may reference.
    """
    for key in threshold.parameter_keys:
        found = db_session.execute(
            text(
                "SELECT count(*) FROM calculation_parameter_vocab "
                "WHERE canonical_key = :key"
            ),
            {"key": key},
        ).scalar_one()
        assert found == 1, (
            f"{check.asserts!r} declares that its {threshold.name!r} threshold "
            f"is resolved from the recorded parameter {key!r}, but no such "
            f"canonical key exists in calculation_parameter_vocab. No "
            f"calculation_parameter row can reference it, so the threshold "
            f"would silently resolve to its fallback on every record."
        )


def test_provenance_thresholds_resolve_and_state_their_fallback() -> None:
    """A provenance threshold must be live code with a documented fallback."""
    for check, threshold in _provenance_thresholds():
        assert callable(threshold.resolver), (
            f"{check.asserts!r}: {threshold.resolver!r} is not callable"
        )
        assert ":" in threshold.location
        assert threshold.parameter_keys, (
            f"{check.asserts!r}: a provenance threshold that reads no recorded "
            "parameter is a constant with extra steps"
        )
        assert threshold.values, (
            f"{check.asserts!r}: no per-protocol values, so a reader cannot "
            "see what the threshold ever resolves to"
        )
        assert len(threshold.fallback) > 60, (
            f"{check.asserts!r}: a provenance-derived threshold must say what "
            "happens when the provenance is missing. That is the case a "
            "referee will probe, and silence there is worse than a constant."
        )


def test_constant_thresholds_justify_their_number() -> None:
    """A constant in the register must say why it is that number."""
    for check in REGISTER:
        for threshold in check.thresholds:
            if not isinstance(threshold, ConstantThreshold):
                continue
            assert threshold.unit, f"{check.asserts!r}: {threshold.name} has no unit"
            assert len(threshold.rationale) > 40, (
                f"{check.asserts!r}: {threshold.name} states a number with no "
                "justification, which is the shape that invites a referee to "
                "ask where it came from."
            )


def test_design_positions_are_prose_not_placeholders() -> None:
    """A ``DesignPosition`` cannot be drift-guarded, so it must be specific."""
    for check in REGISTER:
        for site in check.enforced_by:
            if isinstance(site, DesignPosition):
                assert len(site.where) > 40, (
                    f"{check.asserts!r} names a design position too vague to "
                    "be checkable by a reader: " + site.where
                )
