# Codex prompt: Upload idempotency key support

Use `docs/specs/upload-idempotency-key-spec.md` as the source of truth for this task.

Implement upload idempotency-key support exactly as specified.

This milestone adds server-side retry safety for supported write endpoints.

Do not implement a client package, producer adapter, OpenAPI-generated client, service accounts, or scientific deduplication changes.

## Task

Add support for:

```http
Idempotency-Key: <opaque-key>
```

on supported TCKDB write endpoints.

Supported v0 endpoints should include at minimum:

```text
POST /api/v1/uploads/*
POST /api/v1/bundles/submit
```

A repeated request from the same authenticated user to the same endpoint with the same key and same payload should replay the stored response instead of executing the write again.

A repeated request with the same key but different payload should return `409 idempotency_conflict`.

## What to inspect first

- existing upload routes
- `app/api/routes/bundles.py`
- `app/api/deps.py`
- `get_write_db`
- auth/current-user dependencies
- existing integrity/error handling
- existing migration conventions
- existing model registration conventions
- existing API tests for uploads
- existing bundle submit tests
- `docs/specs/upload-idempotency-key-spec.md`

## Required decisions already made

Use these decisions; do not reopen them unless implementation reveals a hard blocker.

### Header

```http
Idempotency-Key
```

### Scope

Scope idempotency records by:

```text
authenticated_user_id + HTTP method + endpoint + Idempotency-Key
```

### Key constraints

Use:

```text
^[A-Za-z0-9._:-]{16,200}$
```

### TTL

Store records for 30 days.

### Payload hash

Use SHA-256 of canonical full request JSON body:

```python
json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
```

Hash UTF-8 bytes.

Do not exclude timestamps or other fields in v0.

### Failure storage policy

Store successful committed responses only.

Do not store:

- validation errors
- auth errors
- server errors
- rolled-back writes

### Replay behavior

Same user + method + endpoint + key + same payload hash:

- do not execute write again
- return stored response
- include `Idempotency-Replayed: true`

Same user + method + endpoint + key + different payload hash:

- return 409
- code: `idempotency_conflict`

## Required deliverables

### 1. Model/table

Add an idempotency model/table such as:

```text
idempotency_record
```

Recommended fields:

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

Recommended unique constraint:

```text
(user_id, request_method, endpoint, idempotency_key)
```

Follow repo migration rules.

### 2. Service/helper

Add an idempotency service such as:

```text
app/services/idempotency.py
```

Implement helpers for:

- key validation
- canonical payload hash
- existing record lookup
- conflict detection
- response recording
- optional expired-record cleanup helper

### 3. Route integration

Integrate idempotency into supported write routes.

At minimum:

- upload routes
- bundle submit route

Use route-level helper/dependency if simpler than middleware.

Do not apply idempotency globally to every endpoint.

Do not apply it to dry-run.

### 4. Error handling

Add stable errors for:

```text
invalid_idempotency_key
idempotency_conflict
```

Use existing error-response conventions.

### 5. Replay headers

On replayed response, include:

```http
Idempotency-Replayed: true
```

Preferred: replay original status code and response body.

### 6. Docs

Update user-facing docs if implementation lands here:

- `docs/clients/generic-client-targeting.md`
- `docs/contribution-bundles/manual-local-to-hosted-v0.md`

Explain:

- when to send `Idempotency-Key`
- how to choose a stable key
- why payload should be written before upload
- what 409 conflict means

## Implementation constraints

- successful idempotency record must commit atomically with the write
- rolled-back writes must not leave success records
- no-key behavior must remain unchanged
- failed validation should not create idempotency records
- authentication must still be required where already required
- do not store API keys or auth headers
- do not change scientific deduplication logic

## Tests to add

Add service and API tests covering:

1. canonical hash same for reordered JSON
2. canonical hash changes when payload changes
3. invalid key rejected
4. first keyed upload stores response
5. exact replay returns stored response and does not duplicate rows
6. same key different payload returns 409
7. same key allowed for different users
8. same key allowed for different endpoints
9. write without key still works
10. validation failure not stored
11. rollback failure not stored
12. expired key ignored
13. bundle submit replay does not duplicate submission or product rows

## Suggested test files

Create/update files such as:

```text
tests/services/test_idempotency.py
tests/api/test_api_upload_idempotency.py
tests/api/test_api_bundle_submit_idempotency.py
```

Follow existing test conventions.

## Validation commands

Run targeted idempotency tests, plus adjacent upload/bundle tests.

Suggested:

```bash
pytest tests/services/test_idempotency.py tests/api/test_api_upload_idempotency.py tests/api/test_api_bundle_submit_idempotency.py -v
```

Also run:

```bash
pytest tests/api/test_api_bundle_submit.py tests/api/test_api_bundle_dry_run.py tests/api/test_api_auth.py -v
```

Adjust file names to actual implementation.

## Expected output

After implementation, report:

1. what model/table was added
2. what migration changes were made
3. where key validation and payload hashing live
4. which endpoints support idempotency
5. how replay works
6. how conflict works
7. how atomicity with writes is guaranteed
8. what docs were updated
9. what tests were added
10. what tests were run
11. what was explicitly not implemented
12. whether any ambiguity remained
13. how concurrent in-flight duplicate requests are handled — explicitly state which of the following the implementation provides:
    - returns `idempotency_in_progress` for the in-flight duplicate, or
    - relies on the `(user_id, request_method, endpoint, idempotency_key)` unique constraint plus an insert-conflict re-read path, or
    - provides no in-flight protection beyond the unique constraint (acceptable for v0; must be stated)

## Acceptance criteria

The task is complete only if:

- idempotency records are stored for successful keyed writes
- exact keyed retries replay stored response
- conflicting keyed retries return 409
- no-key behavior remains unchanged
- validation failures are not stored
- rolled-back writes are not stored
- user and endpoint scoping are tested
- bundle submit replay is tested
- docs explain the contract
