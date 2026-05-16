"""Server compatibility / metadata endpoint.

Exposes just enough information for clients to know whether they
need to upgrade before attempting a write. Deliberately does NOT
expose deployment details (DB host, CORS config, etc.) — only the
public contract every client needs.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.config import settings

router = APIRouter()


@router.get("/meta")
def get_meta() -> dict:
    """Return server identity and ``tckdb-client`` compatibility info."""
    return {
        "server": "tckdb",
        "minimum_supported_tckdb_client_version": (
            settings.min_supported_tckdb_client_version
        ),
        "enforce_tckdb_client_version_on_writes": (
            settings.enforce_tckdb_client_version_on_writes
        ),
    }
