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
    # Ceiling raised 40 -> 45 (2026-09-27) for the Phase D advisory checks
    # D4-D6, each a separate review-tier claim the Phase D plan requires to be
    # declared; D4 filled slot 40, D6 is 41, D5 will be 42. The ceiling is
    # still a filter: anything past 45 needs the same explicit argument.
    #
    # Raised 45 -> 46 (2026-10-03) for #672 (ADR 0022): the review-tier
    # ``isotope-identity`` advisory check, for rows stored before the decision
    # that D/T means 2H/3H. They cannot be rewritten (trg_as_geometry_atom), so
    # they need a finding rather than a refusal, and it is a different tier
    # from every blocking entry. ``geometry_isotope_symbol_conflict`` is
    # deliberately NOT an entry: it refuses an input-format contradiction
    # ("D is 2H" is a definition) and lives in the code catalogue.
    #
    # Raised 46 -> 47 (2026-10-11) for #669: one entry for the table of calculation
    # types that may be the source of a stated energy, shared by the
    # transition-state and network rules. A referee could dispute which types
    # carry which energy kind, and the table had been two private ones that
    # disagreed. The new ``network_energy_zpe_source_absent`` warning is NOT a
    # second entry: it joins the existing "energy not comparable" entry.
    #
    # Raised 47 -> 48 (2026-10-27) for #812: the review-tier ``wide-basin`` advisory check. A conformer basin is the
    # connected component of its optimised structures, so a chain can be wider than the matching tolerance; TCKDB
    # reports that and never splits it (any automatic cut would put back an arbitrary threshold or an order
    # dependence), and a curator needs a finding to read. One entry, different tier from every blocking entry.
    #
    # Raised 48 -> 49 (2026-10-27) for DR-0038: ``reaction_entry_null_reaction``. With the species key the constitution,
    # a stereo isomerisation has one species on both sides, so "the sides differ" can no longer be read off the
    # species; the entry-level rule is the definitional one (a reaction that changes no participant is not a
    # reaction) and the only blocking claim about a reaction's entries. One entry, joining its conservation group.
    #
    # Raised 49 -> 50 (2026-10-31): 50, for the Hessian-method/deck contradiction refusal (#839). It is a
    # contract between two statements in one deposit (a recorded ``freq.hessian_method`` and the input deck
    # that ran), definitional rather than an expectation about a result, and no existing entry's claim covers
    # it: the tolerance entries judge a mode against a tau, this one decides which method tau is read from.
    #
    # Raised 50 -> 51 (2026-11-01) for #835: species_stereo_contradiction (#822, DR-0038). With the species key the
    # constitution, the entry is where a configuration is stated, read from three sources -- every linked geometry,
    # the SMILES marks, a supplied label -- and a disagreement about one element is refused. Reading R/S, E/Z and an
    # allene's axis from coordinates (torsion windows, alignment over automorphisms, an enumeration cap) is a
    # position a referee could dispute, which an input-format conflict is not (#672). Entries 8 and 9 compare a
    # geometry with its SMILES by element and isotope, never by configuration. Reviewed with the whole #835 sweep: of
    # 58 unregistered warning codes and 13 recent refusals this is the only new entry; four warnings widened 12, 22
    # and 25, and the stereo-label, channel-kind and bath-gas refusals stay in the code catalogue.
    #
    # Raised 51 -> 52 (2026-11-04) for the wavefunction reliability checks: one entry, three warning codes. They are the
    # first checks that read a stored wavefunction or spin diagnostic. (1) A closed-shell coupled-cluster T1 above 0.02
    # (Lee and Taylor 1989): a referee could dispute reporting a rule of thumb that the same literature shows is
    # mostly a false alarm, and could dispute leaving open-shell T1 and D1 unreported (their definitions differ between
    # programs and no primary threshold exists). (2) An <S^2> more than 0.1 from S(S+1): 0.1 is a convention of the
    # field with no primary paper recommending it, so the claim, the absolute-versus-relative form and the use of the
    # entry's declared multiplicity rather than a printed value are all positions a referee could dispute. (3) A
    # printed ideal <S^2> that is not S(S+1) of the declared multiplicity: a declaration contradicting what the program
    # ran, like entry 12's multiplicity_mismatch (and, like it, warn-tier here although ADR 0008 would put it at block).
    # It is in this entry, not entry 12, because the entry is organised by the data it reads (the stored spin and
    # wavefunction diagnostics). No existing entry covers any of them:
    # entry 12 compares a declared state with an output log, these judge a stored diagnostic against a criterion or
    # against the entry. Different tier from every blocking entry.
    assert 10 <= len(REGISTER) <= 52, (
        f"{len(REGISTER)} entries. Below ~10 the register is not describing "
        "the system; above ~52 the inclusion test ('could this check be wrong "
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


#: Register numbers as of origin/main before #839. Numbers are identifiers: they are stored on
#: ``deposit_warning.register_entry`` (append-only, deployed with #837) and cited from docs, so a
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
    # Registered by widening an existing entry (#835), so the number is the entry's own.
    "term_symbol_mismatch": 12,
    "electronic_state_contradicts_method": 12,
    "freq_list_incomplete_for_geometry": 22,
    "network_state_energy_sources_partial": 25,
    "scan_point_coordinate_disagrees_with_geometry": 49,
    "t1_diagnostic_above_closed_shell_threshold": 52,
    "spin_contamination_above_conventional_threshold": 52,
    "spin_expected_contradicts_multiplicity": 52,
}


#: The first words of every register entry, by number.
_OPENINGS: dict[int, str] = {
    1: "The reactant and product sides of a reaction conta",
    2: "The summed formal charge of a reaction's reactants",
    3: "A saddle point is made of exactly the atoms of the",
    4: "A saddle point carries the same total charge as th",
    5: "The saddle-point atoms an IRC mapping assigns to a",
    6: "A reaction entry whose reactants and products are",
    7: "A deposit's `molecule_kind` agrees with the kind a",
    8: "A conformer geometry deposited under a species ent",
    9: "The multiset of isotopic substitutions declared in",
    10: "Every geometry linked to a calculation is made of",
    11: "A species entry's declared charge equals the summe",
    12: "The charge and spin multiplicity a depositor decla",
    13: "An optimisation's output geometry still describes",
    14: "A transition state has at least one imaginary vibr",
    15: "A species entry declared a minimum has no imaginar",
    16: "A van der Waals complex is formally a minimum, so",
    17: "A transition state's imaginary modes other than th",
    18: "A transition state's reaction coordinate should ex",
    19: "A deposited saddle point should carry passing intr",
    20: "A frequency result that deposits both a scalar ima",
    21: "A frequency list carries no more modes than the ge",
    22: "A frequency list's mode count should match the sha",
    23: "The energies an energy-ordering record compares sh",
    24: "A stated energy should be the energy TCKDB stores",
    25: "Every energy an energy-ordering record or a networ",
    26: "A calculation cited as the source of a stated ener",
    27: "An atom does not change element on the way across",
    28: "One saddle-point atom is claimed by exactly one at",
    29: "Every atom index in a map is counted against a nam",
    30: "When a map covers every declared participant of an",
    31: "Where a deposit carries both an atom map and an IR",
    32: "A reaction that has a transition state should say",
    33: "A supplied atom map should cover every declared pa",
    34: "An atom map records whether a human asserted it or",
    35: "An Arrhenius pre-exponential factor carries units",
    36: "A partition function belongs to exactly one subjec",
    37: "A set of phenomenological k(T,P) declares whether",
    38: "A collisional energy-transfer model records whethe",
    39: "The bytes TCKDB serves for a stored artifact are t",
    40: "Whether a record's preserved evidence is sufficien",
    41: "Compare every supplied NASA Cp/entropy fit with ex",
    42: "Compare explicitly supplied opposite elementary ra",
    43: "Compare every stored Gibbs value of one thermo rec",
    44: "Compare a thermo record's supplied enthalpies (h29",
    45: "Compare a kinetics record's stated reaction energy",
    46: "Find, on a protium species entry, calculation geom",
    47: "Report a conformer group whose converged optimised",
    48: "A computed thermo record's heat capacity, evaluate",
    49: "A scan point's stored coordinate_value is the inte",
    50: "A calculation that records how its frequency job b",
    51: "A species entry states one stereo configuration: w",
    52: "A wavefunction reliability diagnostic that a calcu",
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
    # The entry added in #835 takes the next free number, in a group listed after every earlier one.
    assert number_of["species_stereo_contradiction"] == 51
    # Every entry by its opening words: a new check added to an earlier group shifts every later number (the
    # generator only protects against new *groups*), and this is the test that notices.
    headings = {
        int(m.group(1)): m.group(2)
        for m in re.finditer(r"^### (\d+)\. (.*)$", doc, flags=re.MULTILINE)
    }
    assert set(headings) == set(_OPENINGS) == set(range(1, len(_OPENINGS) + 1))
    for number, opening in _OPENINGS.items():
        assert headings[number].startswith(opening), number
    # Every code stored with a register number still points at the entry that declares it.
    for code, stored in REGISTER_ENTRY.items():
        assert number_of[code] == stored, code
