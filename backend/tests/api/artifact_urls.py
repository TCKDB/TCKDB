"""The artifact-upload URL for a calculation known by its row id.

``POST /api/v1/calculations/{calculation_ref}/artifacts`` takes the ``calc_``
ref only (#578). Tests that create a calculation through a fixture hold its row
id, so this resolves the ref through the same session the test's client uses.
"""

from __future__ import annotations

from app.api.deps import get_db
from app.db.models.calculation import Calculation


def artifacts_url(client, calculation_id: int) -> str:
    """``/api/v1/calculations/<calc_ref>/artifacts`` for the calculation with this row id."""
    session = client.app.dependency_overrides[get_db]()
    calculation = session.get(Calculation, calculation_id)
    assert calculation is not None, f"no calculation with row id {calculation_id}"
    return f"/api/v1/calculations/{calculation.public_ref}/artifacts"
