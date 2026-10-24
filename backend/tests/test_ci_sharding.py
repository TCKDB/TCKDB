"""The slice-selection function: a pure function of the path, and total."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from tests import ci_sharding

PLAN = {
    "g": {"slices": 3, "files": {"tests/a.py": 1, "tests/b.py": 2, "tests/c.py": 3}},
}


@pytest.mark.parametrize(
    "spec",
    ["", "api", "api:0/3", "api:4/3", "api:1/0", "API:1/3", "api:1-3", "api:a/3", " :1/3"],
)
def test_a_malformed_spec_is_an_error_not_a_full_run(spec: str) -> None:
    if spec == "":
        assert ci_sharding.from_environment({"TCKDB_TEST_SHARD": spec}) is None
        return
    with pytest.raises(ci_sharding.ShardError):
        ci_sharding.parse(spec)


def test_unset_means_no_sharding() -> None:
    assert ci_sharding.from_environment({}) is None


def test_parse_round_trips() -> None:
    shard = ci_sharding.parse("rest:2/5")
    assert (shard.gate, shard.index, shard.count) == ("rest", 2, 5)
    assert str(shard) == "rest:2/5"


def test_a_planned_file_goes_where_the_plan_says() -> None:
    assert [ci_sharding.shard_of(path, "g", 3, PLAN) for path in ("tests/a.py", "tests/b.py", "tests/c.py")] == [1, 2, 3]


def test_an_unplanned_file_is_still_somebodys_and_always_the_same_slice() -> None:
    first = ci_sharding.shard_of("tests/new/test_x.py", "g", 3, PLAN)
    assert 1 <= first <= 3
    assert all(ci_sharding.shard_of("tests/new/test_x.py", "g", 3, PLAN) == first for _ in range(5))


def test_a_plan_for_another_slice_count_is_refused_not_misapplied() -> None:
    with pytest.raises(ci_sharding.ShardError, match="built for 3 slices"):
        ci_sharding.shard_of("tests/a.py", "g", 2, PLAN)


def test_an_unknown_gate_is_refused() -> None:
    with pytest.raises(ci_sharding.ShardError, match="no gate"):
        ci_sharding.shard_of("tests/a.py", "nope", 3, PLAN)


def test_a_plan_entry_outside_the_slices_is_refused() -> None:
    bad = {"g": {"slices": 2, "files": {"tests/a.py": 3}}}
    with pytest.raises(ci_sharding.ShardError):
        ci_sharding.shard_of("tests/a.py", "g", 2, bad)


def test_select_partitions_the_items_exactly() -> None:
    items = [
        SimpleNamespace(nodeid=f"{path}::test_{n}")
        for path in ("tests/a.py", "tests/b.py", "tests/c.py", "tests/new/test_x.py")
        for n in range(4)
    ]
    kept_ids = []
    for index in (1, 2, 3):
        kept, dropped = ci_sharding.select(items, ci_sharding.Shard("g", index, 3), PLAN)
        assert len(kept) + len(dropped) == len(items)
        kept_ids.extend(item.nodeid for item in kept)
    assert sorted(kept_ids) == sorted(item.nodeid for item in items), "every item is kept by exactly one slice"


def test_the_committed_plan_covers_the_gates_it_names_with_valid_slices() -> None:
    plan = ci_sharding.load_plan()
    assert plan, "the committed plan is empty"
    for gate, entry in plan.items():
        assert entry["slices"] >= 1
        assert entry["files"], f"{gate} has no planned files"
        assert set(entry["files"].values()) <= set(range(1, entry["slices"] + 1))
        assert set(entry["files"].values()) == set(range(1, entry["slices"] + 1)), f"{gate} leaves a slice unplanned"
