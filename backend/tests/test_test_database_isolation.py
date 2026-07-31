"""Safety contract for the destructive pytest database fixture."""

from __future__ import annotations

import conftest
import pytest


@pytest.mark.parametrize("name", ["tckdb_dev", "tckdb", 'tckdb_test"; DROP DATABASE tckdb;--'])
def test_test_database_name_refuses_non_isolated_targets(name):
    with pytest.raises(ValueError, match="DB_TEST_NAME"):
        conftest._validate_test_db_name(name)


def test_test_database_name_accepts_isolated_targets():
    assert conftest._validate_test_db_name("tckdb_test") == "tckdb_test"
    assert conftest._validate_test_db_name("tckdb_test_gw0") == "tckdb_test_gw0"
