"""Generated parity check between the backend OpenAPI doc and the client.

This is the test that stops the client silently falling behind the API.
It reads the backend's checked-in OpenAPI snapshot
(``backend/tests/api/golden/openapi.json``, read-only — this suite never
regenerates it), enumerates every operation, and requires each one to be
classified in ``tckdb_client._parity``.

An untriaged operation is a **failure**, not a warning. The fix is to add
an entry to ``_parity.py``: either a new typed method, or an explicit
``raw_only`` / ``not_applicable`` decision with a reason.

No network access and no live server are involved — the OpenAPI document
is a file on disk.
"""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from tckdb_client import TCKDBClient
from tckdb_client._parity import (
    COVERAGE,
    COVERAGE_BY_OPERATION,
    OperationCoverage,
    coverage_counts,
    diff_against_spec,
    entries_with_status,
    find_openapi_golden,
    load_openapi,
    openapi_operations,
    typed_client_methods,
    typed_iterators,
)

_PACKAGE_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def golden_path() -> Path:
    path = find_openapi_golden()
    assert path is not None, (
        "backend/tests/api/golden/openapi.json was not found from this "
        "checkout. The parity test must never pass vacuously."
    )
    return path


@pytest.fixture(scope="module")
def spec(golden_path: Path) -> dict:
    return dict(load_openapi(golden_path))


class TestLedgerIntegrity:
    def test_every_entry_is_uniquely_keyed(self):
        keys = [entry.key for entry in COVERAGE]
        assert len(keys) == len(set(keys))
        assert len(COVERAGE_BY_OPERATION) == len(COVERAGE)

    def test_status_invariants_hold_for_every_entry(self):
        for entry in COVERAGE:
            if entry.status == "typed":
                assert entry.client_method, entry.operation
                assert entry.contract_test, entry.operation
                assert entry.reason is None, entry.operation
            else:
                assert entry.reason, entry.operation
                assert entry.client_method is None, entry.operation
                assert entry.iterator is None, entry.operation

    def test_reasons_are_substantive(self):
        """A one-word reason is not triage."""
        for entry in COVERAGE:
            if entry.status == "typed":
                continue
            assert len(entry.reason or "") >= 40, entry.operation

    def test_typed_entries_cannot_be_declared_without_a_method(self):
        with pytest.raises(ValueError, match="must name a client method"):
            OperationCoverage(
                method="GET", path="/api/v1/nowhere", status="typed"
            )

    def test_untyped_entries_cannot_be_declared_without_a_reason(self):
        with pytest.raises(ValueError, match="must give a reason"):
            OperationCoverage(
                method="GET", path="/api/v1/nowhere", status="raw_only"
            )


class TestBackendParity:
    def test_every_backend_operation_is_classified(self, spec):
        diff = diff_against_spec(spec)
        assert diff.ok, "\n" + diff.describe()

    def test_ledger_covers_exactly_the_documented_operations(self, spec):
        assert set(COVERAGE_BY_OPERATION) == openapi_operations(spec)

    def test_counts_add_up(self, spec):
        counts = coverage_counts()
        assert sum(counts.values()) == len(openapi_operations(spec))

    def test_a_new_backend_operation_fails_the_check(self, spec):
        """The guard itself must actually trip — proven, not assumed."""
        mutated = {
            "paths": {
                **spec["paths"],
                "/api/v1/brand-new-endpoint": {"get": {"operationId": "x"}},
            }
        }
        diff = diff_against_spec(mutated)
        assert not diff.ok
        assert ("GET", "/api/v1/brand-new-endpoint") in diff.untriaged
        assert "brand-new-endpoint" in diff.describe()

    def test_a_removed_backend_operation_fails_the_check(self, spec):
        surviving = dict(spec["paths"])
        surviving.pop("/api/v1/health")
        diff = diff_against_spec({"paths": surviving})
        assert not diff.ok
        assert ("GET", "/api/v1/health") in diff.stale


class TestTypedEntriesResolve:
    def test_every_named_client_method_exists(self):
        for name in typed_client_methods():
            assert hasattr(TCKDBClient, name), (
                f"_parity names client method {name!r} but TCKDBClient has no "
                "such attribute."
            )
            assert callable(getattr(TCKDBClient, name))

    def test_every_named_iterator_exists_and_is_a_generator_factory(self):
        for name in typed_iterators():
            assert hasattr(TCKDBClient, name), name
            method = getattr(TCKDBClient, name)
            assert callable(method)
            annotation = inspect.signature(method).return_annotation
            assert "Iterator" in str(annotation), (
                f"{name} is declared as an iterator in _parity but its return "
                f"annotation is {annotation!r}."
            )

    def test_every_named_contract_test_file_exists(self):
        for entry in entries_with_status("typed"):
            target = _PACKAGE_ROOT / (entry.contract_test or "")
            assert target.is_file(), (
                f"{entry.operation} names contract test {entry.contract_test!r}, "
                "which does not exist."
            )

    def test_every_named_contract_test_mentions_its_client_method(self):
        cache: dict[str, str] = {}
        for entry in entries_with_status("typed"):
            path = entry.contract_test or ""
            if path not in cache:
                cache[path] = (_PACKAGE_ROOT / path).read_text(encoding="utf-8")
            assert entry.client_method in cache[path], (
                f"{entry.operation} claims coverage in {path} but that module "
                f"never references {entry.client_method!r}."
            )

    def test_every_named_example_file_exists(self):
        for entry in COVERAGE:
            if not entry.example:
                continue
            assert (_PACKAGE_ROOT / entry.example).is_file(), (
                f"{entry.operation} names example {entry.example!r}, which "
                "does not exist."
            )
