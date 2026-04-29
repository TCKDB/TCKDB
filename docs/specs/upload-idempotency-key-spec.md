# Upload idempotency key spec

## Goal

Add idempotency-key support to TCKDB write endpoints so retry-prone clients can safely repeat requests without creating duplicate scientific records.

This is especially important for:

- automated clients
- local/offline replay
- HPC post-processing scripts
- flaky network environments
- future workflow-tool adapters

The server-side contract should be generic. It must not be tied to any one producer tool.

## Background

TCKDB now supports authenticated uploads, local/private deployment, contribution bundles, hosted dry-run, and hosted bundle submit/import.

The next layer is safe retry behavior.

A client should be able to:

1. build a JSON payload
2. write it to disk
3. send it with an `Idempotency-Key`
4. retry the exact same request if the network or client process fails
5. avoid duplicate writes on the hosted/local TCKDB instance

Idempotency does not replace scientific deduplication.

Idempotency answers:

```text
Have I already processed this exact authenticated request?
```

Scientific deduplication answers:

```text
Does this species/reaction/provenance identity already exist?
```

Both are needed.

## Core policy

For supported write endpoints, clients may send:

```http
Idempotency-Key: <opaque-key>
```

TCKDB stores the canonical request payload hash and response for that authenticated user, HTTP method, endpoint, and key.

If the same authenticated user repeats the same request with the same key and same payload, TCKDB returns the stored response instead of executing the write again.

If the same authenticated user repeats the same key on the same endpoint with a different payload, TCKDB rejects the request with `409 idempotency_conflict`.

## Scope

In scope:

- idempotency table/model
- idempotency service
- middleware/dependency/helper for supported write routes
- support for upload endpoints
- support for bundle submit endpoint
- response replay for successful processed requests
- conflict response for key reuse with different payload
- tests for replay, conflict, scoping, and TTL behavior

Out of scope:

- idempotency for read-only endpoints
- idempotency for dry-run endpoints
- global idempotency across all users
- semantic parsing of key contents
- scientific deduplication changes
- background cleanup scheduler unless already trivial
- client package implementation
- producer/tool adapter implementation

## Header

Use the conventional header:

```http
Idempotency-Key: <opaque-key>
```

The server treats the key as an opaque string.

Clients may use structured keys such as:

```text
job:123:conformer:ethanol
notebook:2026-04-25:thermo:ethanol
```

but TCKDB must not parse meaning from the key.

## Key constraints

Validate keys on supported idempotent routes.

Recommended v0 constraints:

- minimum length: 16 characters
- maximum length: 200 characters
- allowed characters:
  - letters
  - digits
  - `.`
  - `_`
  - `-`
  - `:`

Regex:

```text
^[A-Za-z0-9._:-]{16,200}$
```

Invalid keys should return a stable client error.

Suggested response:

```json
{
  "detail": "Invalid Idempotency-Key header.",
  "code": "invalid_idempotency_key"
}
```

## Scope of uniqueness

Idempotency records are scoped by:

```text
authenticated_user_id
HTTP method
route path or canonical endpoint key
Idempotency-Key
```

Recommended unique constraint:

```text
(user_id, request_method, endpoint, idempotency_key)
```

Do not make keys globally unique.

The same key may be used by:

- different users
- different endpoints
- different HTTP methods

without conflict.

## Supported endpoints

For v0, support idempotency on mutation endpoints that create scientific or submission records.

At minimum:

```text
POST /api/v1/uploads/*
POST /api/v1/bundles/submit
```

Do not require idempotency on:

```text
POST /api/v1/bundles/dry-run
GET endpoints
DELETE endpoints
PATCH admin endpoints
auth/session endpoints
API-key creation endpoints
```

Dry-run does not mutate and does not need idempotency.

API-key creation should stay session-controlled and should not use replay semantics in this milestone.

## Optional vs required

For v0, `Idempotency-Key` is optional but recommended.

Behavior:

- supported write endpoint with no key: process normally
- supported write endpoint with valid key: idempotency behavior applies
- unsupported endpoint with key: ignore the key unless the route explicitly opts in

Do not reject unsupported endpoints merely because the header is present.

## TTL

Store idempotency records for 30 days.

Recommended field:

```text
expires_at = created_at + 30 days
```

Behavior:

- unexpired matching key participates in replay/conflict behavior
- expired records may be ignored for replay/conflict
- cleanup may be manual or future scheduled work

Do not implement a scheduler unless the repo already has one.

Add a service helper or documented command for cleanup if cheap.

Example cleanup logic:

```sql
DELETE FROM idempotency_record
WHERE expires_at < now();
```

## Payload hash

Use SHA-256 of canonical full request JSON body.

Canonicalization:

```python
json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
```

Then hash the UTF-8 bytes.

Important:

- include the full request body
- do not exclude client timestamps in v0
- do not normalize scientific fields beyond JSON canonicalization
- do not hash headers
- do not hash query params unless the endpoint uses query params as part of the write request

If the payload body changes, it is a different request.

Clients that need retry safety must resend the exact payload they wrote to disk.

## Stored response

For successful first execution with an idempotency key, store:

- status code
- response body JSON
- created_at
- expires_at
- payload_hash

Recommended table fields:

```text
id
user_id
request_method
endpoint
idempotency_key
payload_hash
status_code
response_body_json
created_at
expires_at
```

Optional fields:

```text
response_headers_json
```

Do not store raw API keys.

Do not store session cookies.

Do not store request authorization headers.

## Replay behavior

### First request with key

If no unexpired idempotency record exists for:

```text
user_id + method + endpoint + key
```

then:

1. process the request normally
2. store payload hash and successful response
3. return normal response

### Repeated request with same key and same payload

If an unexpired idempotency record exists and payload hash matches:

1. do not execute the write again
2. return stored response body
3. return stored status code if practical
4. include response header:

```http
Idempotency-Replayed: true
```

If preserving exact status code is hard in the existing FastAPI route structure, returning `200` with the stored response is acceptable for v0 only if documented and tested.

Preferred behavior is to replay the original status code.

### Repeated request with same key and different payload

If an unexpired record exists and payload hash differs:

Return:

```http
409 Conflict
```

Suggested body:

```json
{
  "detail": "Idempotency key was already used with a different request payload.",
  "code": "idempotency_conflict"
}
```

Do not include the previous payload.

Safe metadata may be included if useful:

```json
{
  "detail": "Idempotency key was already used with a different request payload.",
  "code": "idempotency_conflict",
  "created_at": "2026-04-25T12:00:00Z",
  "endpoint": "/api/v1/uploads/conformer"
}
```

## Failure behavior

Recommended v0 policy:

- store successful responses only
- do not store validation errors
- do not store authentication failures
- do not store server errors
- do not store responses if the transaction rolls back

Reason:

Failed requests should be safely retriable after the user fixes the issue.

A request should become idempotently replayable only after the write has committed successfully.

## Transaction behavior

The idempotency record and the write response must be committed atomically with the write.

If the upload/import transaction rolls back, no idempotency record should remain.

This avoids a dangerous state where the server replays a success response for a write that did not actually commit.

Implementation should use the same write transaction as the route where feasible.

## Race behavior

Concurrent identical requests with the same key should not create duplicate scientific records.

Recommended behavior:

- unique constraint on `(user_id, request_method, endpoint, idempotency_key)`
- on insert conflict, re-read the existing record
- if payload hash matches and response exists, replay it
- if payload hash differs, return 409
- if response is still in progress, return a stable conflict or retryable response

V0 acceptable behavior for in-progress duplicate:

```http
409 Conflict
```

with code:

```text
idempotency_in_progress
```

Only implement `idempotency_in_progress` if needed by the chosen approach.

Do not overbuild distributed locking in v0.

## Endpoint identity

Use a stable endpoint key.

Preferred:

```text
request.method + route path template
```

Examples:

```text
POST /api/v1/uploads/thermo
POST /api/v1/uploads/kinetics
POST /api/v1/bundles/submit
```

Avoid using raw full URL with host/query string unless necessary.

The same logical route should produce the same endpoint key across localhost, lab-server, and hosted deployments.

## Implementation approach

Prefer a route-level helper/dependency rather than generic global middleware if that fits the current FastAPI structure better.

Reason:

- only selected write routes need idempotency
- route handlers already know response body and status
- write transactions are easier to coordinate at route/service level
- fewer surprises for non-idempotent endpoints

Suggested service functions:

```python
def canonical_payload_hash(payload: Any) -> str:
    """Return SHA-256 hash for a canonical JSON request payload."""

def validate_idempotency_key(key: str) -> None:
    """Validate key shape or raise a stable client error."""

def get_existing_idempotency_record(...) -> IdempotencyRecord | None:
    """Return unexpired matching idempotency record if present."""

def record_idempotent_response(...) -> IdempotencyRecord:
    """Persist the successful response for future replay."""
```

A wrapper helper may be useful:

```python
def run_idempotent_write(..., operation: Callable[[], ResponsePayload]) -> ResponsePayload:
    ...
```

But do not force an abstraction that makes route code hard to read.

## Error response shape

Use existing API error conventions.

At minimum, support these stable codes:

```text
invalid_idempotency_key
idempotency_conflict
```

Optional:

```text
idempotency_in_progress
```

## Tests

Add focused tests for the idempotency service and supported routes.

Suggested files:

```text
tests/services/test_idempotency.py
tests/api/test_api_upload_idempotency.py
tests/api/test_api_bundle_submit_idempotency.py
```

Follow repo conventions.

## Required tests

### Test 1. Payload hash canonicalization

Assert equivalent JSON objects with different key order produce the same hash.

Assert changed payload values produce different hashes.

### Test 2. Invalid key rejected

For a supported write endpoint, invalid key returns stable client error.

Test too short, too long, and invalid characters.

### Test 3. First request stores response

Send a valid upload with an idempotency key.

Assert:

- request succeeds
- idempotency record exists
- payload hash stored
- response body stored

### Test 4. Replay returns stored response

Repeat the exact same request with the same key.

Assert:

- write is not executed twice
- same response body is returned
- replay header is present
- no duplicate scientific row is created

### Test 5. Same key different payload returns conflict

Send same user + endpoint + key with different payload.

Assert:

- 409
- code is `idempotency_conflict`
- no second write occurs

### Test 6. Scope by user

User A and User B can use the same idempotency key on the same endpoint without conflict.

### Test 7. Scope by endpoint

Same user can use the same idempotency key on different endpoints without conflict.

### Test 8. No key preserves existing behavior

Supported write endpoint without key still works normally.

### Test 9. Failed validation is not stored

Send invalid payload with idempotency key.

Assert:

- validation fails
- no idempotency record created

Then send valid payload with same key.

Assert:

- valid request can succeed

### Test 10. Rolled-back write does not store response

Force a workflow failure after idempotency key validation.

Assert:

- no idempotency record remains
- retry after fixing payload is possible

### Test 11. Expired key ignored

Create expired idempotency record.

Send same key.

Assert behavior follows new first request path.

### Test 12. Bundle submit replay

Submit a valid bundle with key.

Replay same request.

Assert:

- no duplicate submission
- no duplicate thermo/kinetics rows
- stored response replayed

## Documentation

Create or update:

```text
docs/specs/upload-idempotency-key-spec.md
```

If implementation lands in the same milestone, also document user-facing guidance in:

```text
docs/clients/generic-client-targeting.md
docs/contribution-bundles/manual-local-to-hosted-v0.md
```

Guidance should explain:

- when clients should send `Idempotency-Key`
- how to choose stable keys
- why payloads should be written to disk before upload
- what 409 idempotency conflict means

## Definition of done

Upload idempotency key support is complete when:

1. idempotency model/table exists
2. key validation exists
3. canonical payload hashing exists
4. supported write endpoints accept `Idempotency-Key`
5. successful keyed writes store response atomically
6. exact retries replay stored response
7. same key with different payload returns 409
8. no-key behavior remains unchanged
9. failed/rolled-back writes do not store success responses
10. tests cover user/endpoint scoping
11. bundle submit replay is covered
12. docs explain the contract

## Non-goals

- no generic idempotency for every route
- no idempotency for dry-run
- no background cleanup scheduler unless trivial
- no client package implementation
- no ARC/RMG/tool adapter implementation
- no scientific deduplication changes
- no OpenAPI-generated client work

## Policy note

Idempotency is a retry-safety contract.

It prevents duplicate processing of the same authenticated request; it does not replace scientific identity resolution or curation review.
