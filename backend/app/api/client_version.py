"""Compatibility check between TCKDB server and ``tckdb-client``.

Write/upload endpoints depend on :func:`require_supported_tckdb_client`
so a server that has dropped support for an older client surfaces a
clear ``426 Upgrade Required`` instead of failing later inside a
workflow with a confusing schema/validation error.

The check is intentionally narrow:

- Only requests that explicitly identify themselves as ``tckdb-client``
  via the ``X-TCKDB-Client-Name`` header are gated. Raw ``curl`` /
  arbitrary HTTP callers are passed through — they're either internal
  tooling or users following the API docs by hand.
- Read endpoints, auth, and health are exempt by *not* attaching this
  dependency, so an old client can still read data and re-authenticate
  while a user upgrades.
"""

from __future__ import annotations

from fastapi import Header, HTTPException, status
from packaging.version import InvalidVersion, Version

from app.api.config import settings

TCKDB_CLIENT_NAME = "tckdb-client"
CLIENT_NAME_HEADER = "X-TCKDB-Client-Name"
CLIENT_VERSION_HEADER = "X-TCKDB-Client-Version"


def _reject(code: str, client_version: str | None) -> None:
    minimum = settings.min_supported_tckdb_client_version
    raise HTTPException(
        status_code=status.HTTP_426_UPGRADE_REQUIRED,
        detail={
            "code": code,
            "message": f"This TCKDB server requires tckdb-client >= {minimum}.",
            "client_name": TCKDB_CLIENT_NAME,
            "client_version": client_version,
            "minimum_supported_version": minimum,
        },
    )


def require_supported_tckdb_client(
    x_tckdb_client_name: str | None = Header(
        default=None, alias=CLIENT_NAME_HEADER
    ),
    x_tckdb_client_version: str | None = Header(
        default=None, alias=CLIENT_VERSION_HEADER
    ),
) -> None:
    """Reject write requests from outdated ``tckdb-client`` versions.

    No-ops when enforcement is disabled, when the client-name header is
    absent, or when the client identifies as something other than
    ``tckdb-client``.
    """
    if not settings.enforce_tckdb_client_version_on_writes:
        return
    if not x_tckdb_client_name:
        return
    if x_tckdb_client_name != TCKDB_CLIENT_NAME:
        return

    if not x_tckdb_client_version:
        _reject("tckdb_client_version_missing", None)
    try:
        parsed = Version(x_tckdb_client_version)
    except InvalidVersion:
        _reject("tckdb_client_version_invalid", x_tckdb_client_version)
    minimum = Version(settings.min_supported_tckdb_client_version)
    if parsed < minimum:
        _reject("tckdb_client_version_unsupported", x_tckdb_client_version)
