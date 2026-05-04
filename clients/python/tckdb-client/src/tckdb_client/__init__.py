"""Generic synchronous Python HTTP client for the TCKDB API."""

from tckdb_client.client import TCKDBClient, TCKDBResponse, UPLOAD_ENDPOINTS
from tckdb_client.errors import (
    TCKDBAuthenticationError,
    TCKDBConflictError,
    TCKDBConnectionError,
    TCKDBError,
    TCKDBForbiddenError,
    TCKDBHTTPError,
    TCKDBIdempotencyConflictError,
    TCKDBValidationError,
)
from tckdb_client.idempotency import make_idempotency_key, validate_idempotency_key
from tckdb_client.replay import (
    ClientFactory,
    ReplayFailure,
    ReplaySummary,
    replay_bundle,
)

__all__ = [
    "TCKDBClient",
    "TCKDBResponse",
    "UPLOAD_ENDPOINTS",
    "TCKDBError",
    "TCKDBConnectionError",
    "TCKDBHTTPError",
    "TCKDBAuthenticationError",
    "TCKDBForbiddenError",
    "TCKDBValidationError",
    "TCKDBConflictError",
    "TCKDBIdempotencyConflictError",
    "validate_idempotency_key",
    "make_idempotency_key",
    "ClientFactory",
    "ReplayFailure",
    "ReplaySummary",
    "replay_bundle",
]

__version__ = "0.4.0"
