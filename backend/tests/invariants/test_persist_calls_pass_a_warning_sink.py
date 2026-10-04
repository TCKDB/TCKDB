"""No ``persist_*`` call on the bundle or worker path may drop its warnings (#647).

``/bundles/submit``, ``/bundles/dry-run`` and the background job worker each call
the persistence workflows that the direct ``/uploads/<kind>`` routes call. A
workflow that accepts a warnings sink and is not given one loses every warning
it would have reported, silently: nothing fails, the depositor just never sees
them. That is how #647 happened, and it is invisible to a behavioural test of any
kind that has not been written yet.

So the rule is structural. Every ``persist_*`` call in the two modules is found
by parsing them, resolved to the function it names, and checked against that
function's own signature: if the function takes ``warnings_out`` or
``warnings``, the call must pass it as a keyword and not as ``None``. A workflow
with no sink is allowed only if it is listed below with the reason, and the
list is checked both ways: an entry whose function has since gained a sink, or
that is no longer called, fails.
"""

from __future__ import annotations

import ast
import importlib
import inspect
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parents[2] / "app"
GUARDED_MODULES = ("workflows/contribution_bundle_submit.py", "workers/upload_worker.py")
SINK_PARAMETERS = ("warnings_out", "warnings")

#: Workflows that take no sink, and where their warnings come from instead.
NO_SINK_ALLOWLIST = {
    "persist_conformer_upload": (
        "reports its warnings on ``outcome.warnings``; the worker extends its result with them"
    ),
    "persist_computed_reaction_upload": (
        "returns its warnings in ``result['warnings']``; the worker merges them behind the request-level ones"
    ),
}


def _persist_calls(source_path: Path) -> list[tuple[str, ast.Call, str]]:
    """Every ``persist_*`` call as ``(name, call, module that defines it)``."""
    tree = ast.parse(source_path.read_text())
    origin: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            for alias in node.names:
                origin[alias.asname or alias.name] = node.module
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id.startswith("persist_"):
            assert node.func.id in origin, f"{source_path.name}: {node.func.id} is not imported by name"
            found.append((node.func.id, node, origin[node.func.id]))
    return found


def _all_calls() -> list[tuple[str, str, ast.Call, str]]:
    return [
        (relative, name, call, module)
        for relative in GUARDED_MODULES
        for name, call, module in _persist_calls(APP / relative)
    ]


def _sink_parameter(module: str, name: str) -> str | None:
    function = getattr(importlib.import_module(module), name)
    parameters = inspect.signature(function).parameters
    return next((p for p in SINK_PARAMETERS if p in parameters), None)


def _passes_sink(call: ast.Call, parameter: str) -> bool:
    for keyword in call.keywords:
        if keyword.arg == parameter:
            return not (isinstance(keyword.value, ast.Constant) and keyword.value.value is None)
    return False


def test_the_guard_finds_the_calls_it_guards() -> None:
    """Not vacuous: the bundle importer and every worker kind are in view."""
    calls = _all_calls()
    by_file = {relative: [name for r, name, _c, _m in calls if r == relative] for relative in GUARDED_MODULES}
    assert sorted(by_file["workflows/contribution_bundle_submit.py"]) == [
        "persist_kinetics_upload",
        "persist_thermo_upload",
    ]
    assert len(by_file["workers/upload_worker.py"]) >= 9, by_file["workers/upload_worker.py"]


@pytest.mark.parametrize(
    ("relative", "name", "call", "module"),
    [pytest.param(*row, id=f"{row[0]}:{row[1]}:{row[2].lineno}") for row in _all_calls()],
)
def test_a_persist_call_that_can_report_warnings_is_given_a_sink(relative, name, call, module) -> None:
    parameter = _sink_parameter(module, name)
    if parameter is None:
        assert name in NO_SINK_ALLOWLIST, f"{name} takes no warnings sink and is not allowlisted"
        return
    assert _passes_sink(call, parameter), (
        f"{relative}:{call.lineno} calls {name} without {parameter}=...; "
        "every warning it reports would be lost"
    )


def test_the_allowlist_is_exactly_the_calls_with_no_sink() -> None:
    calls = _all_calls()
    called = {name for _r, name, _c, _m in calls}
    sinkless = {name for _r, name, _c, module in calls if _sink_parameter(module, name) is None}
    assert sinkless == set(NO_SINK_ALLOWLIST), (
        "allowlist is stale or incomplete: "
        f"sinkless calls {sorted(sinkless)}, allowlisted {sorted(NO_SINK_ALLOWLIST)}"
    )
    assert set(NO_SINK_ALLOWLIST) <= called
