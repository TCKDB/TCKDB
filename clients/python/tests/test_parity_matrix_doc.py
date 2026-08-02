"""``docs/api_parity_matrix.md`` must match what the generator produces.

The doc is derived from ``tckdb_client._parity.COVERAGE``. Regenerating
it is one command; letting it rot is a silent lie about what the client
can reach. This test makes the rot impossible.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

from tckdb_client._parity import COVERAGE, coverage_counts

_PACKAGE_ROOT = Path(__file__).resolve().parent.parent
_SCRIPT = _PACKAGE_ROOT / "scripts" / "generate_parity_matrix.py"
_DOC = _PACKAGE_ROOT / "docs" / "api_parity_matrix.md"


@pytest.fixture(scope="module")
def generator():
    spec = importlib.util.spec_from_file_location(
        "_tckdb_parity_matrix_generator", _SCRIPT
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_generator_script_exists():
    assert _SCRIPT.is_file()


def test_checked_in_doc_matches_the_generator(generator):
    assert _DOC.is_file(), (
        "docs/api_parity_matrix.md is missing; run "
        "`python scripts/generate_parity_matrix.py`."
    )
    expected = generator.render_matrix()
    actual = _DOC.read_text(encoding="utf-8")
    assert actual == expected, (
        "docs/api_parity_matrix.md is out of date with "
        "tckdb_client._parity. Run "
        "`python scripts/generate_parity_matrix.py`."
    )


def test_check_mode_passes_for_the_checked_in_doc(generator):
    assert generator.main(["--check"]) == 0


def test_doc_declares_the_required_columns(generator):
    rendered = generator.render_matrix()
    header = "| Operation | raw HTTP | typed client | iterator | example | contract test |"
    assert header in rendered


def test_doc_lists_every_operation(generator):
    rendered = generator.render_matrix()
    for entry in COVERAGE:
        assert f"`{entry.operation}`" in rendered, entry.operation


def test_doc_reports_the_ledger_counts(generator):
    rendered = generator.render_matrix()
    counts = coverage_counts()
    assert f"| typed | {counts['typed']} |" in rendered
    assert f"| raw_only | {counts['raw_only']} |" in rendered
    assert f"| not_applicable | {counts['not_applicable']} |" in rendered
    assert f"| **total** | **{len(COVERAGE)}** |" in rendered


def test_every_untyped_reason_is_visible_in_the_doc(generator):
    rendered = generator.render_matrix()
    for entry in COVERAGE:
        if entry.status == "typed":
            continue
        assert entry.reason in rendered.replace("\n", " ") or entry.reason in rendered
