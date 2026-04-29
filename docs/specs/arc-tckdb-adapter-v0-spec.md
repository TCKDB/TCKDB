# ARC TCKDB adapter v0 spec

## Goal

Implement the first ARC-side adapter for exporting and optionally uploading ARC results to TCKDB.

This milestone should add a thin ARC integration layer that can:

1. construct a TCKDB conformer/calculation upload payload from ARC data
2. write the payload to disk before any network upload
3. write sidecar metadata for retry/debugging
4. optionally upload the payload through `tckdb-client`
5. preserve ARC job success even if TCKDB upload fails, unless strict mode is enabled

This milestone should not implement thermo or kinetics upload yet.

## Background

The TCKDB backend now supports:

- authenticated upload endpoints
- API-key auth
- idempotency keys
- local/private deployment
- hosted deployment
- generic `tckdb-client` v0

`tckdb-client` is generic and has no chemistry or ARC-specific logic.

ARC should own the mapping from ARC objects/files/results into TCKDB upload payloads.

The integration split is:

```text
ARC objects / ARC output files
        ↓
ARC TCKDB adapter
        ↓
TCKDB upload payload JSON
        ↓
tckdb-client
        ↓
TCKDB API
```

## Core decisions

### 1. Adapter lives in ARC

The chemistry/provenance mapping belongs in ARC or an ARC-side integration module.

Do not put ARC-specific code in `tckdb-client`.

### 2. First target is conformer/calculation upload

Do not start with thermo or kinetics.

Thermo and kinetics depend on species identity, conformer geometry, calculations, level of theory, software release, and workflow provenance.

The first adapter target should establish the foundation:

- species identity
- species entry identity
- conformer/geometry
- calculation provenance
- level of theory
- software release
- workflow tool release if available
- calculation results supported by the conformer upload schema

### 3. Always write payload before upload

The adapter must write the JSON payload to disk before attempting network upload.

Reason:

- network upload may fail
- ARC may run in fragile HPC/network environments
- post-processing replay needs the exact payload
- idempotency requires retrying the exact same payload
- debugging should not depend on reproducing ARC state

Flow:

```text
build payload
write payload JSON to disk
write sidecar metadata as pending
attempt upload if enabled
update sidecar with success or failure
```

### 4. Upload failure should not fail ARC by default

Default behavior:

- ARC job/result processing should continue if TCKDB upload fails
- log a warning
- leave payload and sidecar on disk for replay

Strict mode may be added:

- if strict mode is enabled, upload failure raises

### 5. Use idempotency keys

Each logical upload should have a stable idempotency key.

The idempotency key should be deterministic for the logical ARC output, not randomly generated per retry.

Recommended shape:

```text
arc:<project-or-run-id>:<job-or-species-id>:<payload-kind>:<stable-label-or-hash>
```

The exact parts may follow available ARC identifiers.

The key must satisfy the TCKDB server/client regex:

```text
^[A-Za-z0-9._:-]{16,200}$
```

Use `tckdb-client` helper functions where possible.

### 6. Configuration should be optional

ARC should not require TCKDB.

TCKDB upload should be disabled unless configured.

Configuration should support:

- enabled/disabled
- base URL
- API key environment variable
- payload output directory
- upload enabled
- strict upload mode
- timeout

Prefer reading the API key from an environment variable rather than storing it in ARC input files.

## Scope

In scope:

- ARC-side TCKDB adapter module
- TCKDB configuration object/parser support
- conformer/calculation payload construction
- payload JSON writer
- sidecar metadata writer
- optional upload through `tckdb-client`
- idempotency key generation
- retry-friendly metadata
- unit tests for adapter and writer
- docs for configuration and replay

Out of scope:

- thermo upload
- kinetics upload
- bundle export
- hosted submission
- TCKDB server changes
- `tckdb-client` changes unless a small bug is discovered
- ARC workflow redesign
- direct DB access
- background retry daemon
- UI/frontend work

## Package/module location

Use an ARC-consistent module location.

Suggested:

```text
arc/tckdb/
  __init__.py
  adapter.py
  client.py            # optional wrapper around tckdb-client if needed
  config.py
  idempotency.py
  payload_writer.py
  schemas.py           # optional typed dicts/dataclasses only if useful
```

Avoid naming collisions with the external `tckdb_client` package.

The ARC module may depend on:

- ARC internal objects/helpers
- `tckdb-client`
- standard library

Do not make `tckdb-client` depend on ARC.

## Configuration

Add an ARC-side config shape such as:

```yaml
tckdb:
  enabled: true
  base_url: "http://localhost:8000/api/v1"
  api_key_env: "TCKDB_API_KEY"
  payload_dir: "tckdb_payloads"
  upload: true
  strict: false
  timeout_seconds: 30
```

Field behavior:

- `enabled`: if false or missing, do nothing
- `base_url`: TCKDB API root
- `api_key_env`: environment variable containing API key
- `payload_dir`: where payload and sidecar files are written
- `upload`: if false, write payload only and do not contact TCKDB
- `strict`: if true, upload failure raises
- `timeout_seconds`: passed to `tckdb-client`

Do not store raw API keys in ARC project files by default.

## Payload output layout

Write payloads under the configured payload directory.

Suggested layout:

```text
tckdb_payloads/
  conformer_calculation/
    <safe-label>.payload.json
    <safe-label>.meta.json
```

The exact layout may follow ARC conventions.

Payload file:

```text
*.payload.json
```

Sidecar file:

```text
*.meta.json
```

## Sidecar metadata

Write sidecar metadata before upload and update it after upload.

Suggested shape:

```json
{
  "payload_file": "tckdb_payloads/conformer_calculation/example.payload.json",
  "endpoint": "/uploads/conformers",
  "idempotency_key": "arc:example:conformer:001",
  "payload_kind": "conformer_calculation",
  "created_at": "2026-04-26T12:00:00Z",
  "uploaded_at": null,
  "status": "pending",
  "response_status_code": null,
  "response_body": null,
  "last_error": null
}
```

Status values:

```text
pending
uploaded
failed
skipped
```

If upload succeeds:

- set `status = "uploaded"`
- set `uploaded_at`
- store response summary/body if not too large
- store whether response was idempotency replay if available

If upload fails:

- set `status = "failed"`
- set `last_error`
- keep payload file unchanged

## First upload endpoint

Use the existing TCKDB conformer upload endpoint.

Expected endpoint:

```text
/uploads/conformers
```

Confirm the actual endpoint from TCKDB docs or route definitions.

Do not hard-code an endpoint that does not exist.

## Payload construction

The adapter should construct a payload accepted by TCKDB’s conformer upload schema.

It should map available ARC data into:

- species identity
- species entry fields
- geometry / XYZ
- conformer metadata
- primary calculation
- calculation type
- calculation quality
- level of theory reference
- software release reference
- workflow tool release reference if available
- calculation result blocks supported by the schema
- calculation parameters if available

Exact field names must match the current TCKDB upload schema.

Use TCKDB upload schemas/docs as source of truth.

Do not expose raw TCKDB database IDs.

## Provenance mapping

Map ARC provenance carefully.

At minimum:

### Level of theory

Map ARC method/basis/solvent/etc. into TCKDB `LevelOfTheoryRef` or equivalent upload fragment.

### Software release

Map ESS name/version into TCKDB `SoftwareReleaseRef` or equivalent upload fragment.

Examples:

- Gaussian version
- ORCA version

### Workflow tool release

If ARC version/git commit is available, map it into `WorkflowToolReleaseRef`.

If unavailable, omit or mark unknown according to TCKDB schema.

### Calculation parameters

If ARC has parsed calculation parameters or route blocks, include them if the TCKDB conformer/calculation upload schema supports them.

Do not invent unsupported fields.

## Idempotency key generation

Generate one key per logical payload.

Recommended inputs:

- ARC project/run label if available
- species label
- conformer index or hash
- calculation job name/path
- payload kind

Use `make_idempotency_key` from `tckdb-client` where possible.

If needed, append a short hash to avoid collisions.

The key must be stable across retries for the same payload.

Do not include timestamps that change on retry.

## Upload behavior

If `upload = true`:

1. instantiate `TCKDBClient`
2. send payload to `/uploads/conformers`
3. include idempotency key
4. update sidecar on success/failure

If `upload = false`:

1. write payload
2. write sidecar with `status = "skipped"`
3. do not contact TCKDB

If upload fails and `strict = false`:

- log warning
- sidecar status `failed`
- continue ARC

If upload fails and `strict = true`:

- sidecar status `failed`
- raise

## Replay behavior

This milestone does not need a full retry daemon.

But the payload and sidecar must contain enough information for a future replay script to resend the request:

- endpoint
- payload file path
- idempotency key
- base URL is not required in sidecar, but may be recorded if useful

A future replay script can use:

```text
payload_file + endpoint + idempotency_key + configured base_url/api_key
```

## Logging

Log concise messages:

- payload written
- upload skipped
- upload succeeded
- upload replayed if response indicates idempotency replay
- upload failed

Do not log API keys.

Do not log full payloads by default.

## Tests

Add unit tests that do not require a live TCKDB server.

Use mocks/stubs for `tckdb-client`.

Suggested files:

```text
tests/test_tckdb_adapter.py
tests/test_tckdb_payload_writer.py
tests/test_tckdb_config.py
```

Follow ARC test conventions.

## Required tests

### Test 1. Disabled config does nothing

If TCKDB config is absent or disabled, adapter should not write or upload.

### Test 2. Payload is written before upload

Mock upload failure.

Assert payload file exists.

Assert sidecar exists and records failure.

### Test 3. Upload success updates sidecar

Mock `tckdb-client` success.

Assert sidecar status is `uploaded`.

### Test 4. Upload skipped mode

With `upload = false`, payload is written and sidecar status is `skipped`.

No network call.

### Test 5. Strict mode raises on upload failure

With `strict = true`, upload failure raises.

Payload and failed sidecar still exist.

### Test 6. Non-strict mode does not raise on upload failure

With `strict = false`, upload failure is logged/recorded but does not raise.

### Test 7. Idempotency key stable

Same logical ARC input produces same key.

Changing logical input produces different key.

### Test 8. API key read from environment

Adapter reads API key from configured env var.

Missing API key with `upload = true` produces clear error or failed sidecar.

### Test 9. Payload has no DB IDs

Generated payload should not include raw TCKDB database ID fields such as:

```text
species_id
species_entry_id
calculation_id
conformer_observation_id
literature_id
software_release_id
workflow_tool_release_id
```

unless the upload schema explicitly requires such fields, which it should not.

### Test 10. Payload validates against expected shape

If TCKDB schemas are available as test fixtures or JSON examples, validate shape as much as practical.

Do not require importing the TCKDB backend package into ARC unless that is already an accepted dependency.

### Test 11. Replay metadata complete

Sidecar contains endpoint, payload file, idempotency key, status, timestamps.

## Documentation

Create or update ARC docs such as:

```text
docs/tckdb-integration.md
```

or repo-consistent location.

Document:

- what the adapter does
- configuration
- environment variables
- local TCKDB example
- payload directory
- sidecar metadata
- upload failure behavior
- strict mode
- replay idea
- non-goals
- that thermo/kinetics are deferred

Include example config:

```yaml
tckdb:
  enabled: true
  base_url: "http://localhost:8000/api/v1"
  api_key_env: "TCKDB_API_KEY"
  payload_dir: "tckdb_payloads"
  upload: true
  strict: false
  timeout_seconds: 30
```

## Version coupling

Document that ARC adapter v0 targets a specific TCKDB API/client version.

Recommended dependency:

```text
tckdb-client>=0.1,<0.2
```

If packaging requires a different syntax, use repo conventions.

Do not implement OpenAPI-generated clients in v0.

## Definition of done

ARC TCKDB adapter v0 is complete when:

1. ARC-side TCKDB adapter module exists
2. TCKDB config is supported
3. conformer/calculation payload can be built
4. payload JSON is always written before upload
5. sidecar metadata is written and updated
6. optional upload uses `tckdb-client`
7. idempotency key is sent
8. upload failure does not fail ARC by default
9. strict mode can fail on upload error
10. unit tests pass without live TCKDB
11. docs exist
12. no thermo/kinetics adapters are added prematurely

## Non-goals

- no thermo upload
- no kinetics upload
- no bundle submission
- no hosted contribution flow
- no retry daemon
- no direct TCKDB database access
- no server-side TCKDB changes
- no chemistry logic in `tckdb-client`
- no OpenAPI-generated client
- no frontend/UI

## Policy note

ARC owns the chemistry mapping.

`tckdb-client` owns HTTP transport.

TCKDB owns validation and persistence.