"""The cross-slice check must refuse every way a slice can go missing.

Each case builds a set of slice reports that is fine, breaks exactly one
thing, and asserts the check names it. A check that cannot fail is the
defect this file exists to prevent, so the good case is asserted too.
"""

from __future__ import annotations

import copy
import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "check_gate_shards.py"
spec = importlib.util.spec_from_file_location("check_gate_shards_under_test", SCRIPT)
assert spec and spec.loader
check_gate_shards = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check_gate_shards)

KEY = ("tests/api", "!tests/api/scientific")
FLOORS = {KEY: (10, 20)}
PLAN = {"api": {"slices": 3, "files": {}}}


def _report(index: int, *, selected: int = 10, ran: int | None = None, total: int = 30, bodies: int = 6, fields: int = 9):
    return {
        "gate": "api",
        "index": index,
        "count": 3,
        "total": total,
        "selected": selected,
        "ran": selected if ran is None else ran,
        "failed": 0,
        "collection_errors": 0,
        "body_sweep": {"bodies": bodies, "fields": fields},
        "selection_key": list(KEY),
    }


def _good() -> list[dict]:
    return [_report(1), _report(2), _report(3)]


def test_slices_that_add_up_pass() -> None:
    problems, summary = check_gate_shards.check(_good(), PLAN, FLOORS)
    assert problems == []
    assert "30 tests = 30 selected over 3 slices" in summary[0]


def test_a_missing_slice_is_named() -> None:
    problems, _ = check_gate_shards.check(_good()[:2], PLAN, FLOORS)
    assert problems and "expected slices 1..3, found [1, 2]" in problems[0]


def test_a_gate_with_no_reports_at_all_is_named() -> None:
    problems, _ = check_gate_shards.check([], PLAN, FLOORS)
    assert problems and "found none" in problems[0]


def test_a_report_for_a_gate_the_plan_does_not_know_is_named() -> None:
    stray = copy.deepcopy(_good())
    stray[0]["gate"] = "mystery"
    problems, _ = check_gate_shards.check(stray, PLAN, FLOORS)
    assert any("mystery" in problem and "not in ci_shard_plan.json" in problem for problem in problems)


def test_slices_that_collected_different_sets_are_refused() -> None:
    reports = _good()
    reports[1]["total"] = 31
    problems, _ = check_gate_shards.check(reports, PLAN, FLOORS)
    assert problems and "different test sets" in problems[0]


def test_shares_that_do_not_add_up_to_the_total_are_refused() -> None:
    reports = _good()
    reports[2]["selected"] = 9  # one test is in no slice
    reports[2]["ran"] = 9
    problems, _ = check_gate_shards.check(reports, PLAN, FLOORS)
    assert any("selected 29 tests between them but the gate collects 30" in problem for problem in problems)


def test_a_test_in_two_slices_is_refused() -> None:
    reports = _good()
    reports[2]["selected"] = 11
    reports[2]["ran"] = 11
    problems, _ = check_gate_shards.check(reports, PLAN, FLOORS)
    assert any("selected 31 tests" in problem for problem in problems)


def test_selected_tests_that_never_reported_an_outcome_are_refused() -> None:
    reports = _good()
    reports[0]["ran"] = 4  # the slice stopped early
    problems, _ = check_gate_shards.check(reports, PLAN, FLOORS)
    assert any("24 reported an outcome (some never ran)" in problem for problem in problems)


def test_a_test_file_that_cannot_be_imported_is_refused() -> None:
    """The shape of a broken test file: it contributes no tests, so every count still adds up."""
    reports = _good()
    reports[1]["collection_errors"] = 1
    problems, _ = check_gate_shards.check(reports, PLAN, FLOORS)
    assert any("collection errors" in problem for problem in problems)


def test_a_report_that_does_not_say_how_many_collection_errors_is_refused() -> None:
    reports = _good()
    del reports[0]["collection_errors"]
    problems, _ = check_gate_shards.check(reports, PLAN, FLOORS)
    assert any("collection errors" in problem for problem in problems)


def test_a_failed_test_is_refused_even_if_the_job_was_somehow_green() -> None:
    reports = _good()
    reports[2]["failed"] = 1
    problems, _ = check_gate_shards.check(reports, PLAN, FLOORS)
    assert any("failed or errored" in problem for problem in problems)


def test_an_empty_slice_is_refused() -> None:
    reports = _good()
    reports[0].update(selected=0, ran=0)
    reports[1].update(selected=20, ran=20)
    problems, _ = check_gate_shards.check(reports, PLAN, FLOORS)
    assert any("selected no tests" in problem for problem in problems)


def test_more_outcomes_than_selected_tests_is_worded_as_such() -> None:
    reports = _good()
    reports[0]["ran"] = 12
    problems, _ = check_gate_shards.check(reports, PLAN, FLOORS)
    assert any("more outcomes than tests selected" in problem for problem in problems)


def test_the_body_sweep_floor_is_held_to_the_sum_not_the_slice() -> None:
    # Each slice sees 6 bodies, below the gate's floor of 10; the sum is 18.
    problems, _ = check_gate_shards.check(_good(), PLAN, FLOORS)
    assert problems == []

    starved = [_report(index, bodies=2, fields=3) for index in (1, 2, 3)]
    problems, _ = check_gate_shards.check(starved, PLAN, FLOORS)
    assert any("fell below its floor" in problem for problem in problems)


def test_a_gate_with_no_floor_row_is_refused_rather_than_unchecked() -> None:
    problems, _ = check_gate_shards.check(_good(), PLAN, {})
    assert any("no BODY_SWEEP_FLOORS row" in problem for problem in problems)


def test_the_floors_in_conftest_are_readable_and_cover_every_planned_gate() -> None:
    """The script reads conftest.py with ``ast``; this proves it still can."""
    import json

    table = check_gate_shards.floors()
    assert table and all(len(value) == 2 for value in table.values())
    plan = json.loads(check_gate_shards.PLAN.read_text(encoding="utf-8"))
    assert set(plan) == {"api", "scientific", "rest"}
    # The three selections CI runs, as conftest._selection_key spells them.
    assert ("tests/api", "!tests/api/scientific", "!tests/api/test_openapi_snapshot.py") in table
    assert ("tests/api/scientific", "tests/services/scientific_read") in table
    assert ("tests", "!tests/api", "!tests/services/scientific_read") in table


@pytest.mark.parametrize("field", ["gate", "index", "count", "total", "selected", "ran", "collection_errors", "body_sweep", "selection_key"])
def test_the_report_the_conftest_writes_has_every_field_the_check_reads(field: str) -> None:
    import ast

    conftest = (Path(__file__).resolve().parents[1] / "conftest.py").read_text(encoding="utf-8")
    ast.parse(conftest)
    assert f'"{field}"' in conftest, f"conftest._write_shard_report no longer writes {field!r}"
