"""The non-ASCII check has to fire on emitted text and nowhere else.

Both halves are load-bearing and the second is the harder one. A checker
that also flags the em dash in a docstring gets switched off within a
week, and once it is off it is not protecting the error messages either
-- so the false-positive tests here are not politeness, they are what
keeps the rule alive.

The population they defend is real: ``app/scientific_checks/`` and the
resolution services carry several thousand words of deliberate prose in
``rationale=`` / ``escape_hatch=`` fields, which exist to be rendered
into a Markdown register, and ``app/importers/cccbdb/`` matches literal
non-ASCII tokens scraped out of HTML.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts import check_runtime_ascii as checker


def findings(source: str):
    return checker.check_source(source, Path("sample.py"))


# ---------------------------------------------------------------------------
# Fires: strings that reach a database, a client or a log
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "source",
    [
        pytest.param('raise ValueError("bad thing — fix it")', id="raise"),
        pytest.param(
            'raise ValueError(f"{x} → {y} is not allowed")', id="raise-fstring"
        ),
        pytest.param('logger.warning("job failed — retrying")', id="logger"),
        pytest.param(
            'self.logger.error("nope …")', id="attribute-logger"
        ),
        pytest.param(
            'logging.getLogger(__name__).info("hi — there")', id="getLogger"
        ),
        pytest.param(
            'UploadWarning(code="x", message="a — b")', id="message-kwarg"
        ),
        pytest.param(
            'HTTPException(status_code=422, detail="a … b")', id="detail-kwarg"
        ),
        pytest.param('block(reason="unreachable — dns")', id="reason-kwarg"),
    ],
)
def test_emission_sites_are_reported(source):
    assert len(findings(source)) == 1


def test_an_accumulated_warning_is_an_emission_site():
    """``warnings.append`` was missed once, and then miscategorised.

    ``app/importers/cccbdb/builders/statmech_payload.py`` appends to a
    local named ``warnings`` that becomes ``PayloadBundle.warnings`` and
    travels out through the upload path. The first version of this
    checker did not see it, and the omission was written up as "a parser
    token matched against HTML" -- which it is not, and which would have
    left a real emission site permanently excused.
    """
    source = (
        "warnings.append(\n"
        '    "converted GHz→cm⁻¹; raw values preserved"\n'
        ")\n"
    )
    found = findings(source)
    assert len(found) == 1
    assert found[0].kind == "accumulated warning"


def test_an_ordinary_list_append_is_not_an_emission_site():
    """The receiver name is what distinguishes an accumulator."""
    assert findings('rows.append("a — b")') == []


def test_attribute_accumulators_count_too():
    assert len(findings('result.warnings.append("a — b")')) == 1


def test_the_upload_warning_case_that_caused_the_incident():
    """An em dash in an UploadWarning message rolled back a whole upload."""
    source = (
        "warnings.append(\n"
        "    UploadWarning(\n"
        '        field="solve.kind",\n'
        '        code=W_REPORTED,\n'
        '        message="the inputs behind them — barriers — are absent",\n'
        "    )\n"
        ")\n"
    )
    found = findings(source)
    assert len(found) == 1
    assert found[0].kind == "message= argument"
    assert found[0].characters == "—"


def test_every_offending_character_is_named_once():
    found = findings('raise ValueError("— and … and —")')
    assert found[0].characters == "—…"


# ---------------------------------------------------------------------------
# Does not fire: prose, which is the whole reason this can be left switched on
# ---------------------------------------------------------------------------


def test_module_docstrings_are_left_alone():
    assert findings('"""A module docstring — with typography."""\n') == []


def test_function_docstrings_are_left_alone():
    source = 'def f():\n    """Explains something — at length."""\n    return 1\n'
    assert findings(source) == []


def test_comments_are_left_alone():
    assert findings("x = 1  # a comment — with an em dash\n") == []


def test_documentation_as_data_is_left_alone():
    """The declaration registry is prose that renders to Markdown.

    ``rationale`` / ``escape_hatch`` / ``fallback`` / ``note`` fields on
    ``ScientificCheck`` are read by
    ``scripts/generate_scientific_check_register.py`` and by nothing
    else. They are reST-marked, multi-paragraph, and typography is
    correct in them.
    """
    source = (
        "ScientificCheck(\n"
        '    rationale="The noise floor is flat — so tau matters.",\n'
        '    escape_hatch="Declare ``molecule_kind: pseudo`` — it exempts.",\n'
        '    note="Called from both seams — the bundle and the upload.",\n'
        ")\n"
    )
    assert findings(source) == []


def test_parser_input_tokens_are_left_alone():
    """CCCBDB parsers match literal non-ASCII scraped from HTML.

    ``"Å"`` there is not typography, it is the token being matched,
    and rewriting it would break the parser.
    """
    source = 'UNITS = {"Å": "angstrom", "µ⁻¹": "per micron"}\n'
    assert findings(source) == []


def test_argparse_help_and_field_descriptions_are_left_alone():
    source = (
        'parser.add_argument("--force", help="Override the guardrail — dangerous")\n'
        'x = Field(description="required / optional — controls weight")\n'
    )
    assert findings(source) == []


def test_ascii_at_an_emission_site_is_fine():
    assert findings('raise ValueError("bad thing -- fix it")') == []


# ---------------------------------------------------------------------------
# Escape hatch
# ---------------------------------------------------------------------------


def test_marker_suppresses_a_finding():
    source = (
        'raise ValueError("the unit is Å")  # tckdb: allow-non-ascii\n'
    )
    assert findings(source) == []


def test_marker_is_accepted_at_either_end_of_a_multiline_literal():
    head = (
        "raise ValueError(  # tckdb: allow-non-ascii\n"
        '    "reported in Å "\n'
        '    "by the parser"\n'
        ")\n"
    )
    assert findings(head) == []

    tail = (
        "raise ValueError(\n"
        '    "reported in Å "\n'
        '    "by the parser"  # tckdb: allow-non-ascii\n'
        ")\n"
    )
    assert findings(tail) == []


def test_marker_does_not_suppress_a_different_line():
    source = (
        'raise ValueError("first — here")\n'
        'raise ValueError("second is fine")  # tckdb: allow-non-ascii\n'
    )
    assert len(findings(source)) == 1


# ---------------------------------------------------------------------------
# Shell scripts
#
# The rule here is cruder than the Python one -- any non-ASCII outside a
# whole-line comment -- and that is the right trade for a language with no
# AST to walk: a shell script's code is nearly all echo, printf and heredocs,
# so "code versus comment" is very nearly "emitted versus prose".
#
# It was missing entirely until now, and the gap was concrete:
# ``tckdb_auth.sh``'s ``mask_key`` built its output with U+2026 and nothing
# had ever looked at it, because the checker only walked ``*.py``.
# ---------------------------------------------------------------------------


def shell_findings(source: str):
    return checker.check_shell_source(source, Path("sample.sh"))


@pytest.mark.parametrize(
    "source",
    [
        pytest.param("""printf '%s…%s' "${k:0:6}" "${k: -4}\"""", id="mask-ellipsis"),
        pytest.param('echo "deploy failed — see the log"', id="echo-em-dash"),
        pytest.param('hint "install curl → then retry"', id="printf-helper-arrow"),
        pytest.param('die "no key — refusing"', id="die"),
    ],
)
def test_shell_fires_on_emitted_text(source: str):
    assert len(shell_findings(source)) == 1


@pytest.mark.parametrize(
    "source",
    [
        pytest.param("# tckdb_auth.sh — small auth helper.", id="header-comment"),
        pytest.param("    # best-effort parsing — a WARN here is fine.", id="indented"),
        pytest.param("#!/usr/bin/env bash", id="shebang"),
        pytest.param('echo "plain ascii only"', id="clean-code"),
        pytest.param("", id="blank"),
    ],
)
def test_shell_leaves_prose_and_clean_code_alone(source: str):
    """Whole-line comments are prose.

    This repo's shell headers are hundreds of lines of explanation written
    with em dashes. A checker that fires on those is a checker somebody
    deletes, and then it is not guarding the ``echo`` two lines down either.
    """
    assert shell_findings(source) == []


def test_shell_honours_the_allow_marker():
    source = 'echo "µ is the reduced mass"  # tckdb: allow-non-ascii'
    assert shell_findings(source) == []


def test_shell_reports_the_line_number_and_the_characters():
    source = 'echo "ok"\necho "bad — worse …"\necho "ok"'
    found = shell_findings(source)
    assert len(found) == 1
    assert found[0].line == 2
    assert set(found[0].characters) == {"—", "…"}


def test_shell_scripts_are_actually_collected():
    """Guard the guard: the walk must reach ``*.sh``, not only ``*.py``.

    Without this, every assertion above tests a function nothing calls.
    """
    scripts = checker.BACKEND_ROOT / "scripts"
    walked = {*scripts.rglob("*.py"), *scripts.rglob("*.sh")}
    assert any(p.suffix == ".sh" for p in walked)
    # The real entry point, on the real tree, one file at a time.
    for path in sorted(p for p in walked if p.suffix == ".sh"):
        assert checker.check_file(path) == [], path


# ---------------------------------------------------------------------------
# The tree it actually guards
# ---------------------------------------------------------------------------


def test_the_packaged_sources_are_clean():
    """Runs the real check over every default target, as CI does."""
    assert checker.main([]) == 0


def test_tests_are_out_of_scope_by_construction():
    """Test data is the one place non-ASCII is the point.

    ``tests/schemas/test_artifact_in_schema.py`` uploads ``café.log``
    and ``tests/services/test_idempotency.py`` hashes an accented string,
    both to prove the system handles them. Linting those would mean
    annotating the tests that prove the encoding works in order to guard
    against an encoding problem -- and nothing under ``tests`` executes
    in a deployment.
    """
    assert "tests" not in checker.DEFAULT_TARGETS


# ---------------------------------------------------------------------------
# What it is pointed at
#
# The rule above is only worth what its target list covers, and for a long
# time that list was two directories chosen when the script was written. The
# wire package -- which raises the validation errors a client reads and builds
# the ``message=`` strings written to ``upload_warning.message`` -- was never
# on it, and had nine violations at emission sites the day it was first
# scanned. Nothing asserted the coverage, so nothing noticed.
#
# Everything below fails if the target list shrinks, if the wire package stops
# resolving, or if CI stops scanning what a local run scans.
# ---------------------------------------------------------------------------

#: The trees that must stay covered. Each entry is here because losing it
#: would lose a real emission site, not because it happens to be listed
#: today: ``app`` is the deployed code, ``scripts`` writes to real databases,
#: and the wire package produces the persisted warning text.
REQUIRED_TARGETS = frozenset({"app", "scripts", checker.WIRE_PACKAGE_TARGET})

WIRE_PACKAGE = (checker.BACKEND_ROOT / checker.WIRE_PACKAGE_TARGET).resolve()


def test_the_target_list_does_not_shrink():
    """A deleted target is a silent loss of coverage.

    This is the assertion that did not exist. Removing the wire package
    from ``DEFAULT_TARGETS`` would leave every other test in this file
    green while the check stopped reading the package that caused
    2026-08-04's class of failure.
    """
    assert REQUIRED_TARGETS <= set(checker.DEFAULT_TARGETS)


def test_every_default_target_resolves_to_real_files():
    """Guard the guard: a target that expands to nothing passes vacuously.

    ``main`` reports zero findings for a directory it cannot read and for
    a directory that is empty, and the two are indistinguishable in a CI
    log. Assert the expansion instead of the exit code.
    """
    for target in checker.default_target_paths():
        assert target.exists(), target
        walked = checker.walk(target)
        assert walked, target
        assert any(p.suffix == ".py" for p in walked), target


def test_the_wire_package_target_reaches_the_modules_that_emit():
    """Named modules, not a count.

    A path that resolved one directory too high (the distribution root)
    or one too low would still expand to files and still satisfy the test
    above. These two modules are where the nine violations were.
    """
    walked = set(checker.walk(WIRE_PACKAGE))
    assert WIRE_PACKAGE / "stationary_point.py" in walked
    assert WIRE_PACKAGE / "fragments" / "calculation.py" in walked


def test_the_wire_package_is_covered_by_the_defaults():
    """The modules above are reached by ``main([])``, not only directly."""
    covered: set[Path] = set()
    for target in checker.default_target_paths():
        covered.update(checker.walk(target))
    assert WIRE_PACKAGE / "stationary_point.py" in covered


def test_the_wire_packages_own_tests_stay_out_of_scope():
    """Same reasoning as ``backend/tests``: test data is where non-ASCII lives.

    The target is the package directory, so ``tckdb-schemas/tests`` sits
    outside it. If the target ever moved up to the distribution root this
    would fail, which is the point.
    """
    wire_tests = WIRE_PACKAGE.parent / "tests"
    assert wire_tests.is_dir(), "expected the wire package to ship tests"
    covered: set[Path] = set()
    for target in checker.default_target_paths():
        covered.update(checker.walk(target))
    assert not any(wire_tests in p.parents for p in covered)


def test_the_wire_package_is_clean():
    assert checker.check_path(WIRE_PACKAGE) == []


def test_a_reintroduced_em_dash_in_a_wire_message_is_caught():
    """The live rule against the live file, not a hand-written sample.

    Reads the real ``stationary_point.py``, puts an em dash back into a
    ``message=`` literal that had one, and checks the modified source.
    Proves the checker fires on this package's actual shape -- deeply
    nested ``message=(...)`` f-string concatenations inside a
    ``StationaryPointFinding(...)`` call -- rather than on the flat
    one-liners the parametrized cases above use.
    """
    path = WIRE_PACKAGE / "stationary_point.py"
    source = path.read_text(encoding="utf-8")
    clean = "correct chemistry -- a transition "
    assert clean in source, "the message this mutation targets has moved"
    assert checker.check_source(source, path) == []

    mutated = source.replace(clean, "correct chemistry — a transition ")
    found = checker.check_source(mutated, path)
    assert len(found) == 1
    assert found[0].characters == "—"


def test_the_defaults_do_not_depend_on_the_current_directory(tmp_path, monkeypatch):
    """CI runs from ``backend/``; a developer runs from anywhere.

    The wire package is outside ``backend``, so it is reached by a
    ``..`` segment. Resolved against the current directory instead of the
    script's own location, that would point somewhere else -- or nowhere
    -- depending on who ran it.
    """
    from_here = checker.default_target_paths()
    monkeypatch.chdir(tmp_path)
    assert checker.default_target_paths() == from_here
    monkeypatch.chdir(checker.REPO_ROOT)
    assert checker.default_target_paths() == from_here
    assert all(p.is_absolute() for p in from_here)


def test_an_empty_target_is_an_error_not_a_pass(tmp_path, capsys):
    """A moved or mistyped directory must fail loudly.

    Exit 0 on a directory with nothing in it is how a check stops
    checking without anyone finding out.
    """
    (tmp_path / "notes.txt").write_text("no sources here", encoding="utf-8")
    assert checker.main([str(tmp_path)]) == 2
    assert "no *.py or *.sh" in capsys.readouterr().err


def test_a_missing_target_is_an_error_not_a_pass(tmp_path, capsys):
    assert checker.main([str(tmp_path / "gone")]) == 2
    assert "no such path" in capsys.readouterr().err


def test_a_green_run_says_what_it_read(capsys):
    """The CI log has to show the coverage, not just the verdict.

    "The gate is green" is worth exactly as much as the tree it looked
    at, and for as long as this check existed the tree silently excluded
    the package that produces the strings it protects. A success that
    prints nothing cannot be told apart from a success over nothing.
    """
    assert checker.main([]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == len(checker.DEFAULT_TARGETS)
    assert any(
        line.startswith("checked ")
        and checker.WIRE_PACKAGE_TARGET.removeprefix("../") in line
        and " 0 file(s) " not in line
        for line in lines
    ), lines


# ---------------------------------------------------------------------------
# CI parity
# ---------------------------------------------------------------------------

WORKFLOW = checker.REPO_ROOT / ".github" / "workflows" / "backend-ci.yml"
STEP_NAME = "Lint (non-ASCII in runtime strings)"


def _ascii_lint_step() -> dict:
    import yaml

    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    for job in workflow["jobs"].values():
        for step in job.get("steps", []):
            if step.get("name") == STEP_NAME:
                return step
    raise AssertionError(f"no step named {STEP_NAME!r} in {WORKFLOW}")


def test_ci_invokes_the_check_with_no_arguments():
    """The target list has to live in one place.

    If CI passed its own paths, a local run and a CI run would scan
    different trees and the divergence would only show up as a green PR
    followed by a red merge -- or, worse, the other way round.
    """
    step = _ascii_lint_step()
    command = step["run"].strip()
    assert command.endswith("scripts/check_runtime_ascii.py"), command
    assert step["working-directory"] == "backend"
