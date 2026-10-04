from __future__ import annotations

import pytest

from tests.services.network_selection._world import build_world


@pytest.fixture
def world(db_session):
    return build_world(db_session)
