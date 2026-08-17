# Generic client targeting

Any TCKDB API client — a one-off Python script, a notebook, a lab automation
job, a workflow tool, a CI pipeline, or a future CLI — talks to TCKDB the
same way: over HTTP, against a chosen instance, authenticated with an API
key issued by that instance.

This document is the canonical reference for that model.

> Specific tools (ARC, RMG, ad-hoc lab pipelines, future CLI clients) are
> mentioned only as examples. The targeting model is generic: changing the
> client tool does not change the configuration shape.

---

## The model

A TCKDB client is configured by exactly two values:

- `base_url` — the API root of the target instance.
- `api_key` — an API key minted on **that same instance**.

```yaml
tckdb:
  base_url: "http://localhost:8010/api/v1"
  api_key: "tck_replace_me"
```

That is the entire configuration surface. Everything else (payload shape,
endpoints, auth header, response format) is identical across deployments.

### What this means in practice

- TCKDB clients use the **HTTP API**, not direct database access. There is
  no supported way for a client to read or write the database directly.
- The **target instance** is selected by `base_url`. Pointing a client at a
  different `base_url` points it at a different deployment — full stop.
- Authentication uses the **`X-API-Key` header**.
- API keys belong to **users on the target instance**. A key minted on the
  local instance does not authenticate to a hosted instance, and vice
  versa.
- Changing `base_url` changes which instance the client talks to. It does
  **not** synchronize, copy, or migrate data between instances. See
  [Targeting is not syncing](#targeting-is-not-syncing) below.

---

## Targets

The same client code works against all three deployment shapes by changing
only `base_url` (and the matching `api_key`).

### Local TCKDB

A local instance — typically `http://localhost:8010` — set up via
[Local v0 deployment](../deployment/local-v0.md). Useful for single-user
workstations, laptops, and offline work.

```yaml
# examples/clients/tckdb.local.yml
tckdb:
  base_url: "http://localhost:8010/api/v1"
  api_key: "tck_local_replace_me"
```

### Lab-server TCKDB

A shared instance reachable on a lab network, typically behind a
hostname like `https://tckdb.lab.example.org`. Shared private
deployments usually run with `AUTH_ALLOW_OPEN_REGISTRATION=false`; ask
the deployment's administrator to seed your account before minting a
key. Operator-side guidance lives in
[Shared private deployment](../deployment/shared-private-deployment.md).
HPC/batch usage is covered in
[Client access from HPC](../deployment/client-access-from-hpc.md).

```yaml
# examples/clients/tckdb.lab-server.yml
tckdb:
  base_url: "http://lab-tckdb.internal:8010/api/v1"
  api_key: "tck_lab_replace_me"
```

### Hosted TCKDB

The public/community instance, reached over HTTPS at its operator-published
URL.

```yaml
# examples/clients/tckdb.hosted.yml
tckdb:
  base_url: "https://tckdb.example.org/api/v1"
  api_key: "tck_hosted_replace_me"
```

The hosted URL above is illustrative; substitute the actual operator URL.

---

## API keys

API keys are per-user, per-instance credentials.

### Creating a key

Keys can only be minted by a logged-in user — never by another API key.
This means the bootstrapping path is always: log in (with a password +
session cookie), then mint a key.

```bash
# 1. Log in (saves the session cookie to cookies.txt)
curl -sf -c cookies.txt \
  -H "Content-Type: application/json" \
  -X POST "$TCKDB_BASE_URL/auth/login" \
  -d '{"username": "alice", "password": "..."}'

# 2. Mint a key (uses the session cookie)
curl -sf -b cookies.txt \
  -H "Content-Type: application/json" \
  -X POST "$TCKDB_BASE_URL/auth/api-keys" \
  -d '{"label": "my-laptop"}'
```

The response contains a one-time `key` field (e.g. `tck_...`). **Save it
immediately** — the plaintext value is never shown again. Only the hash is
stored server-side.

The full key-management surface lives at:

- `POST /auth/api-keys` — mint a key (session-only)
- `GET  /auth/api-keys` — list keys (session-only)
- `DELETE /auth/api-keys/{id}` — revoke a key (session-only)

See DR-0022 — Auth and Roles v1
for the full auth model.

### Sending a key

Every authenticated client request sends the API key in the `X-API-Key`
header:

```
X-API-Key: tck_...
```

There is no other supported way to authenticate a client. (Sessions exist
only for the human/browser flow that mints keys.)

### Attribution

A request authenticated with `X-API-Key` is attributed to the **user that
owns the key** on that instance. Records created by the request carry that
user's `app_user.id` in their `created_by` column. Different keys belonging
to the same user produce the same attribution; keys belonging to different
users produce different attributions.

---

## Environment-variable pattern

Hard-coding a `base_url` and `api_key` into a config file is convenient for
notebooks and one-off scripts, but secrets in repo-tracked files are a
foot-gun. The recommended pattern for scripts, notebooks, automation, and
CI is to read both values from the environment:

```bash
export TCKDB_BASE_URL="http://localhost:8010/api/v1"
export TCKDB_API_KEY="tck_replace_me"
```

Switching targets becomes a one-line change:

```bash
# point the same script at hosted instead of local
export TCKDB_BASE_URL="https://tckdb.example.org/api/v1"
export TCKDB_API_KEY="tck_hosted_replace_me"
```

Most TCKDB clients should accept both forms (config file and env vars) and
let env vars override config-file values.

---

## Examples

### `curl`

Verify auth against the target instance:

```bash
curl -sf "$TCKDB_BASE_URL/auth/me" \
  -H "X-API-Key: $TCKDB_API_KEY"
```

A `200` with the authenticated user's profile means the `base_url` and
`api_key` are mutually consistent and the user is active.

A representative upload-shaped request (the exact endpoint depends on the
upload workflow; see the API documentation for the full set):

```bash
curl -X POST "$TCKDB_BASE_URL/uploads/thermo" \
  -H "X-API-Key: $TCKDB_API_KEY" \
  -H "Content-Type: application/json" \
  --data @payload.json
```

### Python

A minimal generic client lives at
[`examples/clients/simple_upload.py`](../../examples/clients/simple_upload.py).
It reads `TCKDB_BASE_URL` and `TCKDB_API_KEY` from the environment and
issues an authenticated `GET /auth/me`. Deliberately dependency-light
(`requests` only) and not coupled to any workflow tool.

Inline equivalent:

```python
import os
import requests

base_url = os.environ["TCKDB_BASE_URL"].rstrip("/")
api_key = os.environ["TCKDB_API_KEY"]

resp = requests.get(
    f"{base_url}/auth/me",
    headers={"X-API-Key": api_key},
    timeout=30,
)
resp.raise_for_status()
print(resp.json())
```

The same shape works for every other endpoint — only the path, method, and
body change.

---

## Retry safety with `Idempotency-Key`

TCKDB supports the conventional `Idempotency-Key` HTTP header on
mutation endpoints (`POST /api/v1/uploads/*` and
`POST /api/v1/bundles/submit`). Sending it makes a write *retry-safe*:
an exact retry of the same request returns the stored response without
re-executing the write.

Use it whenever a client may retry a request after a network glitch,
process restart, queued HPC job re-submission, or operator-driven
replay of a saved payload. The behavior is contractual — see
DR-0024 — Upload Idempotency Keys.

### Sending the header

```bash
curl -X POST "$TCKDB_BASE_URL/uploads/thermo" \
  -H "X-API-Key: $TCKDB_API_KEY" \
  -H "Content-Type: application/json" \
  -H "Idempotency-Key: arc:job-12345:thermo:ethanol" \
  --data @payload.json
```

Inline Python:

```python
resp = requests.post(
    f"{base_url}/uploads/thermo",
    headers={
        "X-API-Key": api_key,
        "Idempotency-Key": "arc:job-12345:thermo:ethanol",
    },
    json=payload,
    timeout=30,
)
```

### Choosing a stable key

The server treats keys as opaque (16–200 chars, `[A-Za-z0-9._:-]`) and
never parses them, but a good client key is:

- **Stable** — the same logical upload, retried, must produce the
  same key. Random per-attempt keys defeat the whole feature.
- **Unique per logical request** — derive from identifiers the client
  already has (e.g. `<tool>:<job-id>:<output-kind>:<species-or-reaction-label>`).
- **Scoped to the producer** — different ARC jobs, notebooks, or runs
  should produce different keys; reusing a key across distinct logical
  requests will cause `409 idempotency_conflict` rather than overwrite.

Idempotency records are scoped server-side by
`(authenticated_user_id, HTTP_method, endpoint, idempotency_key)` —
the same key may safely be used by different users or on different
endpoints without colliding.

### Write the payload to disk *before* sending

The retry contract requires resending the **exact same payload bytes**
(after JSON canonicalization) under the same key. The recommended
pattern is:

1. Build the upload payload.
2. Write it to disk (`tckdb_payloads/<id>.payload.json`).
3. Write a sidecar with status `pending` plus the chosen
   `Idempotency-Key`.
4. POST the on-disk payload.
5. On success, mark the sidecar `uploaded`. On failure, leave it for
   later retry.

This pattern is what makes Track-B-style HPC post-processing replay
safe: a separate process can re-read the on-disk payload and re-POST
it under the original key, and the server will replay the original
response if the first attempt did succeed but the response was lost.

### Response semantics

- First request with a key: processed normally; the server stores the
  response under the key for 30 days.
- Exact retry (same key, same payload): server replays the stored
  response. Look for the `Idempotency-Replayed: true` response header
  to distinguish replay from re-execution.
- Same key with a different payload: `409 Conflict` with body
  `{"code": "idempotency_conflict", ...}`. Either pick a new key or
  resend the original payload bytes.
- Invalid key shape: `400 Bad Request` with body
  `{"code": "invalid_idempotency_key", ...}`.
- Validation/auth/server failures and rolled-back writes: nothing is
  stored. The same key may be reused once the underlying problem is
  fixed.
- Records older than 30 days are treated as if the key had never been
  used.

---

## API-key safety

API keys are bearer credentials. Anyone holding a valid key can act as the
owning user on the issuing instance until the key is revoked. Treat them
accordingly.

- **Do not commit keys to a repository.** Use environment variables or a
  gitignored local config file (e.g. `~/.config/tckdb/config.yml`,
  `.env.local`).
- **Do not paste keys into shared notebooks, chat, or screenshots.**
- **Rotate proactively.** Mint a new key, deploy it, then revoke the old
  one — the API supports multiple concurrent keys per user specifically so
  rotation does not require downtime.
- **Revoke immediately if a key is leaked or suspected leaked.** Use
  `DELETE /auth/api-keys/{id}` on the issuing instance. Revocation takes
  effect on the next request; no other credentials are disturbed.
- **Local, lab-server, and hosted keys are independent.** A key minted on
  one instance does not authenticate against another. Mint a separate key
  per instance and store them under distinct names
  (`TCKDB_API_KEY_LOCAL`, `TCKDB_API_KEY_HOSTED`, …) if a single
  workstation needs to talk to several.
- **API keys inherit the owning user's role.** A key minted by a curator
  has curator privileges; a key minted by a regular user has user-level
  privileges. Mint keys from the lowest-privilege account that satisfies
  the use case.

---

## Targeting is not syncing

This is the most common conceptual mistake when first setting up a TCKDB
client.

Changing `base_url` from local to hosted (or vice versa) **does not**:

- copy any records between instances,
- replicate the database in either direction,
- mirror users, API keys, or roles,
- promote local-only data to the hosted community instance.

Each instance is a fully independent deployment of the same backend with
its own users, its own data, its own moderation state, and its own
identity space. Pointing a client at a different `base_url` simply makes
the client talk to a different database.

Local-to-hosted contribution — the explicit, validated, moderated path
from a private local instance to the hosted community database — will be
delivered later through **contribution bundles**: a portable export of
selected scientific records that hosted imports through its existing
validation, deduplication, and submission/moderation pipeline. Bundles are
not raw database dumps and not a sync mechanism. See
DR-0023 — Local/Offline and Hosted Submission Model
for the full rationale.

Until contribution bundles ship, the only way to put data on a given
instance is to upload it to that instance directly via its API.

---

## Troubleshooting

- **401 when calling an upload endpoint.** The request is not authenticating
  as a valid user. Check that you are sending `X-API-Key`, not
  `Authorization: Bearer`, and that the key was created on the same TCKDB
  instance as `TCKDB_BASE_URL`.

- **401 when creating an API key.** API keys cannot mint more API keys.
  Log in with `POST /auth/login` first, keep the session cookie, then call
  `POST /auth/api-keys` with that cookie.

- **403 on registration.** Public registration is disabled for this
  deployment. Use the configured bootstrap/admin account or ask an admin
  to create/promote the account.

- **Key worked yesterday but now gives 401.** The key may have been
  revoked, the owning user may have been deactivated, or you may be
  pointing at a different instance. Confirm the target URL first, then
  create a new key from a browser/session login if needed.

- **404 or doubled path such as `/api/v1/api/v1/...`.** The `base_url` is
  shaped incorrectly. Configure it once as the API root, for example
  `http://localhost:8010/api/v1`, and have clients append endpoint paths
  like `/auth/me`.

- **Connection works locally but not from an HPC job or lab machine.** The
  backend may be bound to `127.0.0.1`, hidden behind VPN/firewall rules,
  or reachable only from a login/service node. Use a lab-server URL that
  the job can actually reach.

- **API key works directly but fails through a reverse proxy.** The proxy
  may be stripping custom headers. Ensure `X-API-Key` is forwarded to the
  backend.

- **422 on upload even though authentication works.** The request reached
  TCKDB, but the payload failed schema/scientific validation. Check
  `Content-Type: application/json`, read the response `code` (see below),
  and verify you are using the upload schema for the target endpoint.

---

## Telling one 422 from another

A refusal body is `{"code": ..., "detail": ..., "context": {...}}`. **Branch
on `code`, never on `detail`.** `detail` is a paragraph of advice written for
a human, and it will be reworded; `code` is the contract.

`request_validation_error` means the payload did not match the schema — a
missing field, a wrong type — and the `detail` list names the offending
path. Anything else is a *scientific* refusal: the payload was well-formed
and the chemistry in it contradicted itself. Those codes are enumerated,
one per claim, in
[the scientific check register](../guides/scientific_check_register.md);
every entry whose "code reaches a client via" row says `error_envelope`
appears in a 422 body exactly as written there.

The distinction worth building on is what a client should *do*:

| Code | What it means | What to do |
| --- | --- | --- |
| `species_geometry_composition_mismatch`, `species_geometry_isotope_mismatch`, `species_smiles_charge_mismatch` | the structure you deposited and the label you gave it describe different molecules | fix the payload and retry — one of the two is a typo |
| `reaction_mass_balance_failed`, `reaction_charge_not_conserved`, `transition_state_composition_mismatch`, `transition_state_charge_mismatch` | the reaction as written is not a reaction | stop and investigate; retrying the same thing cannot help |
| `atom_map_*`, `transition_state_irc_mapping_element_mismatch` | the mechanism you described contradicts itself, or an index counts against the wrong geometry | correct the mapping; note that indices are geometry-relative (ADR 0011) |
| `transition_state_no_imaginary_mode`, `transition_state_reaction_coordinate_*`, `n_imag_contradicts_minimum` | the frequency evidence contradicts the kind of stationary point declared | re-declare the entry as what it is, or deposit no frequency evidence |
| `arrhenius_a_units_molecularity_mismatch` | `a_units` do not have the dimensionality the reaction order requires | fix the unit token |
| `validation_error` | a refusal that carries no more specific code | read `detail`; and please report it, because a scientific refusal reaching you as this is a gap |

### What `context` carries, and when it is empty

`context` is where a refusal's facts go in structured form — the two
element counts that disagreed, the two charges, the disputed atom indices
— so a client can render a useful message without parsing the sentence.

It is **not populated for every code**, and the rule for which is worth
knowing before you write a branch that reads it:

- A code that names a **relationship** — `state_conflict`,
  `handle_type_mismatch`, `atom_map_element_not_conserved`, anything
  spelled `*_conflict` or `*_mismatch` — asserts that two or more things
  disagree and names none of them. There `context` is where the *which*
  lives: a field path, a public ref, a local key you declared, the
  database constraint that refused, a count. Never a database row id —
  TCKDB does not put row ids in error bodies, because a row id is not
  stable across a restore or between two deployments and no public
  surface is keyed on one.
- A code that names a **thing** — `unknown_release`, `missing_filter`,
  `limit_too_large` — is already the whole message. `context` may be `{}`
  and that is the honest answer, not a gap: there is no second fact to
  report.

So: read `context` where the code names a relationship, and treat `{}`
as normal everywhere else. Do not make a populated `context` a
precondition for handling a refusal — branch on `code` first, then
enrich from `context` if it is there. Some relationship codes have not
been populated yet; that work is in progress, and the server's own
catalogue (`backend/app/api/code_catalogue.py`) records the
classification per code if you need to know where things stand.

One special case that looks like an exception and is not: the `426`
client-version refusals (`tckdb_client_version_unsupported` and
siblings) put their facts — the version you sent, the minimum this
server accepts — in `detail`, which for these three is a JSON object
rather than a sentence. "Never parse `detail`" still holds: parsing is
what a sentence needs, and these are already structured.

A warning is not a refusal: an accepted upload returns `201` with a
`warnings` list whose entries carry their own `code`. The register's
`upload_warning` rows are those.

---

## References

- [Deployment guide overview](../deployment/README.md) — one-app
  taxonomy of deployment scenarios, client environments, and
  infrastructure strategies.
- [Single-machine private deployment](../deployment/local-v0.md) —
  how to bring up a TCKDB instance for the `localhost` target above.
- [Shared private deployment](../deployment/shared-private-deployment.md) —
  operator-side guide for the lab/group target shape.
- [Client access from HPC](../deployment/client-access-from-hpc.md) —
  using TCKDB from batch jobs and HPC clusters as an API client.
- [Native advanced install](../deployment/native-advanced.md) —
  fallback infrastructure strategy when Docker and Apptainer are
  unavailable.
- DR-0022 — Auth and Roles v1 —
  authoritative description of the dual-mode auth (sessions for humans,
  API keys for clients) referenced throughout this document.
- DR-0023 — Local/Offline and Hosted Submission Model
  — the architectural commitment that local, lab-server, and hosted
  instances run the same backend and that contribution flows through
  bundles, not sync.
- Example configs:
  [`tckdb.local.yml`](../../examples/clients/tckdb.local.yml),
  [`tckdb.lab-server.yml`](../../examples/clients/tckdb.lab-server.yml),
  [`tckdb.hosted.yml`](../../examples/clients/tckdb.hosted.yml).
- Example client:
  [`simple_upload.py`](../../examples/clients/simple_upload.py).
