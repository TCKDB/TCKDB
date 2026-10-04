"""Fixtures shared by the API tests that are not in the root conftest."""

from __future__ import annotations

# The kinetics selection tests' one reaction entry (H + CH4 -> H2 + CH3), re-exported for the API tests.
from tests.services.kinetics_selection.conftest import world

__all__ = ["world"]
