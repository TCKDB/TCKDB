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
import dataclasses
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
from app.scientific_checks.numbering import numbered_register

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
    # The ceiling is a filter: a new entry has to pass the inclusion test.
    assert 10 <= len(REGISTER) <= 83, (
        f"{len(REGISTER)} entries. Below ~10 the register is not describing "
        "the system; above ~83 the inclusion test ('could this check be wrong "
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
    a constraint whose *name* in the register does not match what
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

    A code may legitimately appear in more than one entry (``n_imag_contradicts_minimum``
    names the finding while the declared stationary-point kind decides the tier), but a
    code scattered across unrelated groups would mean two claims share one label.
    """
    slots = [(check.group, check.sort_key) for check in REGISTER]
    assert len(slots) == len(set(slots)), "duplicate (group, sort_key) slot"

    groups_by_code: dict[str, set[str]] = {}
    for check in REGISTER:
        for code in check.codes:
            groups_by_code.setdefault(code, set()).add(check.group)
    scattered = {code: groups for code, groups in groups_by_code.items() if len(groups) > 1}
    assert not scattered, f"codes shared across unrelated groups: {scattered}"


def test_every_entry_states_its_rule_and_enforcement() -> None:
    """No entry may be a stub, and a blocking entry records its escape hatch."""
    for check in REGISTER:
        assert check.enforced_by, f"{check.asserts!r} names no enforcement site"
        assert len(check.asserts) > 30, f"{check.asserts!r} is not a sentence"
        if check.tier is CheckTier.block:
            assert check.escape_hatch is not None, (
                f"{check.asserts!r} blocks but records no escape hatch; write "
                "'None.' if there is none."
            )


def test_declarations_carry_no_removed_fields() -> None:
    """The register holds rule, tier, enforcement and repair, not justification."""
    fields = {field.name for field in dataclasses.fields(ScientificCheck)}
    assert not fields & {"adr", "tier_rationale"}
    assert "note" not in {field.name for field in dataclasses.fields(PythonCheck)}
    assert "note" not in {field.name for field in dataclasses.fields(DatabaseConstraint)}


_DECISION_REFERENCE = re.compile(r"ADR \d|DR-\d|docs/adr")
_SECOND_SENTENCE = re.compile(r"[.!?]\s+[A-Z`]")


def _declaration_texts() -> list[tuple[str, str]]:
    """Every prose field a declaration carries, with where it came from."""
    texts: list[tuple[str, str]] = []
    for check in REGISTER:
        where = check.codes[0] if check.codes else check.asserts[:40]
        texts.append((f"{where}: asserts", check.asserts))
        for label, value in (("escape_hatch", check.escape_hatch), ("divergence", check.divergence)):
            if value:
                texts.append((f"{where}: {label}", value))
        for threshold in check.thresholds:
            texts.append((f"{where}: {threshold.name} rationale", threshold.rationale))
            if isinstance(threshold, ProvenanceThreshold):
                texts.append((f"{where}: {threshold.name} fallback", threshold.fallback))
        for site in check.enforced_by:
            if isinstance(site, DesignPosition):
                texts.append((f"{where}: design position", site.where))
            if isinstance(site, DatabaseConstraint) and site.rejection_detail:
                texts.append((f"{where}: rejection detail", site.rejection_detail))
    return texts


def test_no_declaration_cites_a_decision_record() -> None:
    """A declaration says what the check requires, never which ADR or DR it came from."""
    assert _declaration_texts(), "no declaration text collected, so the scan would pass vacuously"
    offenders = [(where, m.group(0)) for where, text_ in _declaration_texts() if (m := _DECISION_REFERENCE.search(text_))]
    assert not offenders, offenders


def test_scientific_check_sources_cite_no_decision_record() -> None:
    """The scientific_checks package and the generator carry no ADR or DR pointer either."""
    scanned = set((BACKEND_ROOT / "app" / "scientific_checks").glob("*.py")) | {GENERATOR}
    assert len(scanned) >= 5
    offenders = [
        (path.name, m.group(0))
        for path in sorted(scanned)
        for line in path.read_text().splitlines()
        if (m := _DECISION_REFERENCE.search(line))
    ]
    assert not offenders, offenders


def test_register_document_cites_no_decision_record() -> None:
    assert not _DECISION_REFERENCE.search(REGISTER_DOC.read_text())


def test_prose_fields_are_one_sentence() -> None:
    """``asserts``, ``escape_hatch``, ``divergence`` and threshold rationales are one sentence each."""
    for where, value in _declaration_texts():
        if where.endswith(("asserts", "escape_hatch", "divergence", "rationale")):
            assert not _SECOND_SENTENCE.search(value), f"{where} is more than one sentence: {value!r}"


def test_anchors_name_the_function_rather_than_a_line() -> None:
    """No anchor may carry a line number, in the declarations or the document.

    This is what keeps :func:`test_generated_document_is_in_sync` a *semantic*
    gate. With line numbers the register is a build artifact of every line
    above every declared check: adding one line to
    ``app/services/geometry_validation.py``, shifting a check from 120 to 121,
    and turned the tier red on main -- the entire drift being that digit.

    A gate that fires on cosmetic movement gets regenerated reflexively
    without anyone reading the diff, which is how a real drift eventually
    lands unnoticed. So the anchor names the function, and the sync test keeps
    its teeth for the things that matter: a renamed check, a removed code, a
    vocab key that does not resolve.

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
        "No ProvenanceThreshold entries: the tau declaration was lost or "
        "collection broke, so the parameter-key check would pass vacuously."
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
            "happens when the provenance is missing."
        )


def test_constant_thresholds_have_a_unit_and_a_rationale() -> None:
    """A constant in the register states its unit and what the number means."""
    for check in REGISTER:
        for threshold in check.thresholds:
            if not isinstance(threshold, ConstantThreshold):
                continue
            assert threshold.unit, f"{check.asserts!r}: {threshold.name} has no unit"
            assert threshold.rationale, f"{check.asserts!r}: {threshold.name} has no rationale"


def test_design_positions_are_prose_not_placeholders() -> None:
    """A ``DesignPosition`` cannot be drift-guarded, so it must be specific."""
    for check in REGISTER:
        for site in check.enforced_by:
            if isinstance(site, DesignPosition):
                assert len(site.where) > 40, (
                    f"{check.asserts!r} names a design position too vague to "
                    "be checkable by a reader: " + site.where
                )


#: Register numbers on origin/main. Numbers are identifiers: they are stored on
#: ``deposit_warning.register_entry`` (append-only) and cited from docs, so a
#: new entry takes the next free number and never shifts one. Entries #15 to #18 are the
#: stationary-point entries every other document cites.
_PINNED_NUMBERS: dict[str, int] = {
    "n_imag_contradicts_minimum": 16,
    "minimum_imaginary_modes_below_tau": 17,
    "transition_state_extra_imaginary_modes_below_tau": 17,
    "transition_state_extra_imaginary_mode_above_tau": 17,
    "transition_state_extra_imaginary_modes_not_assessable": 17,
    "transition_state_imaginary_frequency_too_small": 18,
    "reaction_atom_map_absent": 32,
    "reaction_atom_map_participants_incomplete": 33,
    "reaction_atom_map_atoms_incomplete": 33,
    "reported_network_solve": 37,
    "network_wide_energy_transfer_scope": 38,
    # Registered by widening an existing entry, so the number is the entry's own.
    "term_symbol_mismatch": 12,
    "electronic_state_contradicts_method": 12,
    "freq_list_incomplete_for_geometry": 22,
    "network_state_energy_sources_partial": 25,
    "scan_point_coordinate_disagrees_with_geometry": 49,
    "t1_diagnostic_above_closed_shell_threshold": 52,
    "spin_contamination_above_conventional_threshold": 52,
    "spin_expected_contradicts_multiplicity": 52,
}


#: The declaration behind every register entry, by published number.
_ENTRY_DECLARATIONS: dict[int, str] = {
    1: "CHECK_REACTION_ELEMENTAL_BALANCE",
    2: "CHECK_REACTION_CHARGE_CONSERVATION",
    3: "CHECK_TRANSITION_STATE_COMPOSITION",
    4: "CHECK_TRANSITION_STATE_CHARGE",
    5: "CHECK_TS_IRC_MAPPING_ELEMENTS",
    6: "CHECK_REACTION_ENTRY_NULL_REACTION",
    7: "CHECK_SPECIES_KIND_MATCHES_STORED_IDENTITY",
    8: "CHECK_GEOMETRY_COMPOSITION_MATCHES_IDENTITY",
    9: "CHECK_GEOMETRY_ISOTOPES_MATCH_IDENTITY",
    10: "CHECK_CALCULATION_GEOMETRY_COMPOSITION",
    11: "CHECK_SMILES_CHARGE_MATCHES_DECLARED",
    12: "CHECK_CHARGE_MULTIPLICITY_VS_LOG",
    13: "CHECK_OPT_GEOMETRY_MATCHES_DECLARED_SPECIES",
    14: "CHECK_TRANSITION_STATE_REACTION_COORDINATE",
    15: "CHECK_MINIMUM_ZERO_IMAGINARY_MODES",
    16: "CHECK_VDW_COMPLEX_IMAGINARY_MODE",
    17: "CHECK_TRANSITION_STATE_EXTRA_IMAGINARY_MODES",
    18: "CHECK_TRANSITION_STATE_SOFT_REACTION_COORDINATE",
    19: "CHECK_TS_IRC_EVIDENCE",
    20: "CHECK_FREQ_N_IMAG_AGREES_WITH_ITS_MODES",
    21: "CHECK_FREQ_LIST_WITHIN_GEOMETRY_DEGREES_OF_FREEDOM",
    22: "CHECK_FREQ_LIST_MODE_COUNT_MATCHES_GEOMETRY_SHAPE",
    23: "CHECK_TS_ENERGY_ORDERING_LEVELS",
    24: "CHECK_TS_ENERGY_ORDERING_STATED_MISMATCH",
    25: "CHECK_TS_ENERGY_ORDERING_NOT_COMPARED",
    26: "CHECK_ENERGY_SOURCE_TYPE",
    27: "CHECK_ATOM_MAP_ELEMENT_CONSERVED",
    28: "CHECK_ATOM_MAP_IS_A_BIJECTION",
    29: "CHECK_ATOM_MAP_INDICES_ARE_GEOMETRY_RELATIVE",
    30: "CHECK_ATOM_MAP_ACCOUNTS_FOR_EVERY_ATOM",
    31: "CHECK_ATOM_MAP_AGREES_WITH_IRC_MAPPING",
    32: "CHECK_ATOM_MAP_ABSENT",
    33: "CHECK_ATOM_MAP_INCOMPLETE",
    34: "CHECK_ATOM_MAP_PROVENANCE_IS_DECLARED",
    35: "CHECK_ARRHENIUS_A_UNITS_MATCH_MOLECULARITY",
    36: "CHECK_STATMECH_HAS_EXACTLY_ONE_SUBJECT",
    37: "CHECK_NETWORK_SOLVE_KIND_EVIDENCE",
    38: "CHECK_ENERGY_TRANSFER_SCOPE",
    39: "CHECK_STORED_ARTIFACT_BYTES_MATCH_THEIR_DIGEST",
    40: "CHECK_REPRODUCIBILITY_IS_ITS_OWN_JUDGEMENT",
    41: "CHECK_THERMO_CONSISTENCY",
    42: "CHECK_THERMO_KINETICS_CONSISTENCY",
    43: "CHECK_GIBBS_SELF_CONSISTENCY",
    44: "CHECK_KIRCHHOFF_CONSISTENCY",
    45: "CHECK_HESS_CONSISTENCY",
    46: "CHECK_ISOTOPE_IDENTITY_CONSISTENCY",
    47: "CHECK_WIDE_BASIN_CONSISTENCY",
    48: "CHECK_EXTERNAL_CP_COMPARISON",
    49: "CHECK_SCAN_COORDINATE_VALUE_MATCHES_GEOMETRY",
    50: "CHECK_RECORDED_HESSIAN_METHOD_AGREES_WITH_THE_INPUT_DECK",
    51: "CHECK_SPECIES_ENTRY_STEREO_AGREES",
    52: "CHECK_WAVEFUNCTION_RELIABILITY_DIAGNOSTICS",
    53: "CHECK_KINETICS_BATH_GAS_IS_A_COMPOSITION",
    54: "CHECK_NETWORK_CHANNEL_KIND_FOLLOWS_ITS_STATES",
    55: "CHECK_NETWORK_STATES_ARE_DISTINCT_COMPOSITIONS",
    56: "CHECK_DECLARED_ENERGY_LEVEL_IS_HONEST",
    57: "CHECK_ENERGIES_AND_OPTIMISATIONS_PAIR_ONE_TO_ONE_BY_GEOMETRY",
    58: "CHECK_CALCULATION_GEOMETRY_STATEMENTS_AGREE",
    59: "CHECK_COMPOSITE_STATED_NUMBERS_AGREE",
    60: "CHECK_COMPOSITE_INPUTS_ARE_AT_THE_SLOT_LEVEL_AND_ONE_GEOMETRY",
    61: "CHECK_SP_ENERGY_COMPONENTS_ADD_UP",
    62: "CHECK_COMPOSITE_FREQUENCY_IS_AT_THE_RECIPE_LEVEL",
    63: "CHECK_NAMED_COMPOSITE_IS_A_COMPOSITE_CALCULATION",
    64: "CHECK_COMPOSITE_INPUTS_AND_TOTAL_CAN_BE_CHECKED",
    65: "CHECK_A_CITED_THING_IS_OF_THE_RECORDS_OWN_SUBJECT",
    66: "CHECK_KINETICS_DECLARATION_DOES_NOT_CONTRADICT_THE_RECORD",
    67: "CHECK_DECLARATION_IS_ABOUT_THE_RECORD_THAT_CARRIES_IT",
    68: "CHECK_OBSERVATION_IS_ATTACHED_TO_ITS_OWN_GROUND_STATE",
    69: "CHECK_A_BOND_ADDITIVITY_TOTAL_HAS_ITS_COMPONENTS",
    70: "CHECK_A_FREQUENCY_SCALE_FACTOR_IS_OF_THE_PROGRAM_THAT_RAN",
    71: "CHECK_ENTHALPY_STATES_ITS_REFERENCE",
    72: "CHECK_CALCULATION_DEPENDENCIES_ARE_ACYCLIC",
    73: "CHECK_STABILITY_VERDICTS_DO_NOT_CITE_EACH_OTHER_IN_A_RING",
    74: "CHECK_ENERGIES_AT_DIFFERENT_LEVELS_ARE_NOT_COMPARED",
    75: "CHECK_A_CONVERGED_OPTIMISATION_HAS_AN_ENERGY_BEHIND_IT",
    76: "CHECK_TYPED_SETTINGS_AGREE_WITH_THE_ARTIFACTS",
    77: "CHECK_STATED_ENERGY_AGREES_WITH_THE_LOG",
    78: "CHECK_HESSIAN_METHOD_OF_A_LATE_ARTIFACT_AGREES_WITH_THE_STORED_BASIS",
    79: "CHECK_ATOMIC_ELECTRONIC_LEVELS_AGREE_WITH_THE_GROUND_TERM",
    80: "CHECK_SCF_STABILITY_SOURCE_IS_COMPARABLE",
    81: "CHECK_A_FORMAT_DEFINED_THERMO_DECLARATION_DESCRIBES_WHAT_THE_FORMAT_DEFINES",
    82: "CHECK_A_RECORDED_HESSIAN_METHOD_IS_REFINED_WHEN_IT_CANNOT_HOLD",
    83: "CHECK_A_ONE_ELECTRON_HARTREE_FOCK_ENERGY_STANDS_FOR_THE_CORRELATED_LEVEL",
}


def test_existing_entries_keep_their_numbers() -> None:
    """Adding an entry must not renumber the ones already published."""
    from app.services.deposit_warnings import REGISTER_ENTRY

    doc = (BACKEND_ROOT.parent / "docs" / "guides" / "scientific_check_register.md").read_text()
    number_of: dict[str, int] = {}
    for block in re.split(r"\n(?=### \d+\. )", doc)[1:]:
        number = int(re.match(r"### (\d+)\.", block).group(1))
        channel = re.search(r"reaches a client via\*\* \| (.*?) \|", block).group(1)
        code_line = re.search(r"\*\*Code\*\* \| (.*?) \|", block).group(1)
        for code in re.findall(r"`([a-z_0-9]+)`", code_line):
            # A code can be declared twice (a refusal and a warning); the stored
            # number is the warning entry's.
            if "UploadWarning" in channel or code not in number_of:
                number_of[code] = number
    assert len(_PINNED_NUMBERS) >= 10, "the pin lost its content"
    for code, expected in _PINNED_NUMBERS.items():
        assert number_of[code] == expected, f"{code}: entry {number_of[code]}, was {expected} on main"
        if code in REGISTER_ENTRY:
            assert REGISTER_ENTRY[code] == expected, code
    assert number_of["hessian_method_contradicts_input_deck"] == 50
    # A newly added entry takes the next free number, in a group listed after every earlier one.
    assert number_of["species_stereo_contradiction"] == 51
    # Every entry by its declaration: a new check added to an earlier group shifts every later number (the
    # generator only protects against new *groups*), and this is the test that notices.
    declared_name = {
        id(value): name
        for module in DECLARING_MODULES
        for name, value in vars(module).items()
        if isinstance(value, ScientificCheck)
    }
    published = {number: declared_name[id(check)] for number, check in numbered_register()}
    assert published == _ENTRY_DECLARATIONS
    headings = {int(m.group(1)) for m in re.finditer(r"^### (\d+)\. ", doc, flags=re.MULTILINE)}
    assert headings == set(_ENTRY_DECLARATIONS) == set(range(1, len(_ENTRY_DECLARATIONS) + 1))
    # Every code stored with a register number still points at the entry that declares it.
    for code, stored in REGISTER_ENTRY.items():
        assert number_of[code] == stored, code


def _register_codes() -> set[str]:
    """Every code a register entry reports, including the codes its database constraints name."""
    codes: set[str] = set()
    for check in REGISTER:
        codes.update(check.codes)
        for site in check.enforced_by:
            if isinstance(site, DatabaseConstraint) and site.rejection_code:
                codes.add(site.rejection_code)
    return codes


def test_every_catalogue_and_warning_code_is_in_exactly_one_of_the_register_and_not_scientific() -> None:
    """Every emitted code is in the register or in NOT_SCIENTIFIC, never both, and nothing stale is listed."""
    from tckdb_schemas.deposit_warning_messages import RENDERED

    from app.api.code_catalogue import CATALOGUE
    from app.scientific_checks.not_scientific import NOT_SCIENTIFIC

    emitted = {entry.code for entry in CATALOGUE} | set(RENDERED)
    registered = _register_codes()
    assert len(emitted) > 300 and len(NOT_SCIENTIFIC) > 100, "an empty side would make this pass vacuously"
    unclassified = sorted(emitted - registered - set(NOT_SCIENTIFIC))
    assert not unclassified, (
        f"{len(unclassified)} codes are in neither the register nor NOT_SCIENTIFIC: {unclassified}. "
        "Declare a register entry for a scientific one, or add it to NOT_SCIENTIFIC with the reason it is not."
    )
    both = sorted(registered & set(NOT_SCIENTIFIC))
    assert not both, f"codes claimed by the register and also listed as not scientific: {both}"
    stale = sorted(set(NOT_SCIENTIFIC) - emitted)
    assert not stale, f"NOT_SCIENTIFIC names codes nothing emits any more: {stale}"
    blank = sorted(code for code, reason in NOT_SCIENTIFIC.items() if len(reason.strip()) < 15)
    assert not blank, f"NOT_SCIENTIFIC entries with no real reason: {blank}"
