# tckdb-client v0 spec

## Goal

Create the first generic Python HTTP client package for the TCKDB API.

`tckdb-client` v0 should provide a small, boring, reusable client for scripts, notebooks, workflow tools, post-processing jobs, and future producer-specific adapters.

It must not contain chemistry-specific payload construction logic.

It must not depend on ARC, RMG, RDKit, or any scientific workflow package.

## Background

The server-side TCKDB API now supports:

- authenticated uploads using API keys
- generic client targeting by `base_url` + `api_key`
- contribution bundle dry-run
- contribution bundle submit/import
- upload idempotency keys for retry safety

The next layer is a generic client package that wraps HTTP mechanics:

- base URL normalization
- API-key header handling
- JSON POST helpers
- idempotency-key headers
- response/error handling
- bundle dry-run/submit helpers
- upload endpoint helpers

Producer-specific adapters are separate.

For example:

```text
ARC objects -> ARC adapter -> TCKDB payload dict -> tckdb-client
RMG objects -> RMG adapter -> TCKDB payload dict -> tckdb-client
Notebook dict -> tckdb-client
Payload JSON file -> tckdb-client
```

## Core decisions

### 1. `tckdb-client` is generic

The client must not import or depend on:

- ARC
- RMG
- RDKit
- Arkane
- ASE
- cclib
- quantum chemistry packages
- chemistry workflow packages

It only sends JSON-compatible payloads to the TCKDB HTTP API.

### 2. Payload construction belongs outside the client

`tckdb-client` accepts dictionaries or JSON-compatible objects.

It does not build species, conformer, thermo, kinetics, calculation, or bundle payloads from scientific objects.

Adapters in producer projects own that mapping.

### 3. Use `base_url + api_key`

Client configuration is:

```python
client = TCKDBClient(
    base_url="http://localhost:8000/api/v1",
    api_key="tck_...",
)
```

The same client should work against:

- local TCKDB
- lab/private TCKDB
- hosted/community TCKDB

### 4. Support idempotency keys

For retry-safe write operations, the client should support:

```http
Idempotency-Key: <opaque-key>
```

The client should not derive chemistry-specific keys in v0.

It may expose a helper for validating or building generic keys, but producer adapters should decide the logical key.

### 5. Use `httpx`

Use `httpx` for v0.

Reasons:

- good timeout support
- clean testing via `MockTransport`
- easy future async support
- modern Python API

Implement only the synchronous client in v0.

Async client is deferred.

## Package shape

Create a package such as:

```text
tckdb-client/
  pyproject.toml
  README.md
  src/
    tckdb_client/
      __init__.py
      client.py
      errors.py
      idempotency.py
      types.py
  tests/
    test_client.py
    test_errors.py
    test_idempotency.py
```

If this is created inside an existing monorepo, use a repo-consistent path such as:

```text
packages/tckdb-client/
```

or:

```text
clients/python/
```

Choose the path that best matches the repository conventions.

Do not put this inside ARC.

Do not put chemistry adapters in this package.

## Python version

Support the project’s current Python baseline.

Recommended:

```text
Python >=3.11
```

If the backend is already standardized on a stricter version such as Python 3.12, match that.

## Dependencies

Required runtime dependency:

```text
httpx
```

Recommended test dependencies:

```text
pytest
respx or httpx.MockTransport
```

Prefer `httpx.MockTransport` if it avoids extra dependencies.

Do not add heavy dependencies.

## Public API

### Main client

Expose:

```python
from tckdb_client import TCKDBClient
```

Constructor:

```python
class TCKDBClient:
    def __init__(
        self,
        base_url: str,
        api_key: str | None = None,
        timeout: float = 30.0,
    ) -> None:
        ...
```

Rules:

- normalize `base_url` by stripping trailing slashes
- do not require API key for health checks
- require API key for authenticated helpers unless explicitly skipped
- do not log API keys

### Core methods

Implement:

```python
def health(self) -> dict:
    """Return backend health response."""

def me(self) -> dict:
    """Return authenticated user context."""

def get_json(self, path: str) -> dict:
    """Send authenticated GET request and return JSON response."""

def post_json(
    self,
    path: str,
    payload: dict,
    *,
    idempotency_key: str | None = None,
) -> dict:
    """Send authenticated JSON POST request and return JSON response."""
```

### Upload helper

Implement a generic upload helper:

```python
def upload(
    self,
    endpoint: str,
    payload: dict,
    *,
    idempotency_key: str | None = None,
) -> dict:
    """POST a payload to an upload endpoint."""
```

This should accept either:

```text
"/uploads/conformers"
```

or a documented endpoint string.

Do not invent upload-kind mapping unless very small and explicit.

Acceptable optional mapping:

```python
UPLOAD_ENDPOINTS = {
    "conformer": "/uploads/conformers",
    "thermo": "/uploads/thermo",
    "kinetics": "/uploads/kinetics",
}
```

If implemented, keep it thin and documented.

### Bundle helpers

Implement:

```python
def bundle_dry_run(self, bundle: dict) -> dict:
    """POST a bundle to /bundles/dry-run."""

def bundle_submit(
    self,
    bundle: dict,
    *,
    idempotency_key: str | None = None,
) -> dict:
    """POST a bundle to /bundles/submit."""
```

Do not send idempotency keys for dry-run by default.

Support idempotency keys for submit.

## Headers

For authenticated requests, send:

```http
X-API-Key: <api_key>
```

If `idempotency_key` is provided, send:

```http
Idempotency-Key: <idempotency_key>
```

For JSON requests, send:

```http
Content-Type: application/json
Accept: application/json
```

Do not print or log headers containing secrets.

## Idempotency helper

Create:

```text
tckdb_client/idempotency.py
```

Provide:

```python
def validate_idempotency_key(key: str) -> str:
    """Validate and return an idempotency key."""

def make_idempotency_key(*parts: str) -> str:
    """Build a simple opaque idempotency key from safe string parts."""
```

Use the server-side key constraints:

```text
^[A-Za-z0-9._:-]{16,200}$
```

`make_idempotency_key` should be generic. It should not know chemistry semantics.

Example:

```python
key = make_idempotency_key("mytool", "job-123", "conformer", "ethanol")
```

Do not hash payloads client-side for server contract in v0. The server owns payload hashing.

## Error handling

Create structured exceptions in:

```text
tckdb_client/errors.py
```

Expose:

```python
class TCKDBError(Exception): ...

class TCKDBConnectionError(TCKDBError): ...

class TCKDBHTTPError(TCKDBError): ...

class TCKDBAuthenticationError(TCKDBHTTPError): ...

class TCKDBForbiddenError(TCKDBHTTPError): ...

class TCKDBValidationError(TCKDBHTTPError): ...

class TCKDBConflictError(TCKDBHTTPError): ...

class TCKDBIdempotencyConflictError(TCKDBConflictError): ...
```

Exception fields should include where applicable:

```python
status_code: int | None
code: str | None
detail: object | None
response_json: dict | list | None
response_text: str | None
headers: Mapping[str, str] | None
```

Map responses:

```text
401 -> TCKDBAuthenticationError
403 -> TCKDBForbiddenError
409 with code=idempotency_conflict -> TCKDBIdempotencyConflictError
409 other -> TCKDBConflictError
422 -> TCKDBValidationError
other 4xx/5xx -> TCKDBHTTPError
network/timeout -> TCKDBConnectionError
```

## Replay visibility

If a response includes:

```http
Idempotency-Replayed: true
```

the client should expose that somehow.

Acceptable v0 options:

1. return plain JSON and expose last response metadata on the client
2. return a lightweight response wrapper
3. include a helper method for advanced users

Recommended v0:

Use a lightweight response wrapper for lower-level methods, while convenience methods return JSON.

Example:

```python
@dataclass(frozen=True)
class TCKDBResponse:
    data: dict
    status_code: int
    headers: Mapping[str, str]

    @property
    def idempotency_replayed(self) -> bool:
        ...
```

Then:

```python
def request_json(...) -> TCKDBResponse:
    ...

def post_json(...) -> dict:
    return self.request_json(...).data
```

If this feels too much, use `client.last_response_headers`.

The spec prefers the response wrapper because idempotency replay is important for upload/retry logic.

## Timeout behavior

Default timeout:

```text
30 seconds
```

Allow override in constructor.

Use httpx timeout handling.

Convert timeout/network errors to `TCKDBConnectionError`.

## Path handling

Rules:

- `base_url` should not require trailing slash
- `path` may include or omit leading slash
- final URL must not duplicate slashes
- do not duplicate `/api/v1`

Examples:

```python
TCKDBClient("http://localhost:8000/api/v1").me()
TCKDBClient("http://localhost:8000/api/v1/").me()
client.post_json("/uploads/thermo", payload)
client.post_json("uploads/thermo", payload)
```

all should resolve correctly.

## README

Add `README.md` documenting:

- installation
- base URL + API key configuration
- health check
- authenticated `me`
- generic upload
- idempotency key usage
- bundle dry-run
- bundle submit
- error handling
- non-goals

## Example usage

Create examples such as:

```text
examples/basic_usage.py
examples/upload_json_file.py
examples/submit_bundle.py
```

Keep them generic.

No chemistry dependencies.

Example upload JSON file flow:

```bash
export TCKDB_BASE_URL="http://localhost:8000/api/v1"
export TCKDB_API_KEY="tck_replace_me"

python examples/upload_json_file.py \
  --endpoint /uploads/conformers \
  --payload ./payload.json \
  --idempotency-key "example:upload:conformer:001"
```

## Tests

Use `pytest`.

Use `httpx.MockTransport` or equivalent.

Do not require a live TCKDB server for unit tests.

## Required tests

### Test 1. Base URL normalization

Assert trailing slash handling works.

### Test 2. Path joining

Assert leading slash and no leading slash both work.

### Test 3. API key header

Authenticated requests include `X-API-Key`.

### Test 4. Idempotency header

Requests with idempotency key include `Idempotency-Key`.

### Test 5. Health without API key

`health()` works without API key.

### Test 6. Me requires API key

`me()` without API key raises a useful client-side or HTTP error.

### Test 7. post_json success

Successful response returns JSON.

### Test 8. request_json response wrapper

Wrapper exposes status code, headers, data, and `idempotency_replayed`.

### Test 9. 401 maps to authentication error

### Test 10. 403 maps to forbidden error

### Test 11. 422 maps to validation error

### Test 12. 409 idempotency_conflict maps to idempotency conflict error

### Test 13. 409 other maps to generic conflict error

### Test 14. 500 maps to HTTP error

### Test 15. timeout/network error maps to connection error

### Test 16. bundle_dry_run posts to `/bundles/dry-run`

### Test 17. bundle_submit posts to `/bundles/submit`

### Test 18. upload helper posts to selected endpoint

### Test 19. idempotency key validation

Valid/invalid key examples match server constraints.

### Test 20. examples compile

If examples are included, run `python -m py_compile`.

## Non-goals

- no chemistry payload adapters
- no ARC/RMG imports
- no RDKit dependency
- no schema generation from OpenAPI
- no async client
- no retry loop in v0 unless very small and explicit
- no automatic idempotency key derivation from payload
- no persistent payload sidecar
- no local export/import business logic
- no direct database access

## Definition of done

`tckdb-client` v0 is complete when:

1. package exists with minimal metadata
2. sync `TCKDBClient` exists
3. `base_url + api_key` auth works
4. `health`, `me`, `get_json`, `post_json`, `upload`, `bundle_dry_run`, and `bundle_submit` exist
5. idempotency key header support exists
6. structured exceptions exist
7. replay header visibility exists
8. examples exist
9. README exists
10. tests pass without live TCKDB
11. no chemistry dependencies are introduced

## Policy note

`tckdb-client` is the generic transport layer.

It sends already-formed TCKDB payloads; it does not know how to construct them.
