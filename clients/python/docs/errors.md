# Errors and refusal codes

## Errors

Every HTTP failure raises a structured exception that carries the parsed
response body, status code, and headers:

| Status | Exception |
|--------|-----------|
| 401 | `TCKDBAuthenticationError` |
| 403 | `TCKDBForbiddenError` |
| 422 | `TCKDBValidationError` |
| 409 (`code=idempotency_conflict`) | `TCKDBIdempotencyConflictError` |
| 409 (other) | `TCKDBConflictError` |
| 4xx/5xx (other) | `TCKDBHTTPError` |
| 2xx whose body is not JSON (e.g. an HTML page) | `TCKDBUnexpectedResponseError` |
| network / timeout | `TCKDBConnectionError` |

`TCKDBUnexpectedResponseError` (a `TCKDBHTTPError`) almost always means
`base_url` names the web site rather than the API: use
`https://host/api/v1`, not `https://host`. The message names the URL that
was requested.

```python
from tckdb_client import TCKDBValidationError

try:
    client.upload("thermo", bad_payload)
except TCKDBValidationError as exc:
    print(exc.status_code, exc.detail)
```

### Telling one refusal from another

The status tells you who is at fault; it does not tell you what to do.
A `422` covers every refusal the server can make, and those want opposite
responses — a geometry that disagrees with its declared SMILES is worth
repairing and resending, while a reaction that does not balance means the
deposit is wrong and should not be retried at all.

`exc.code` names which one it was. `RejectionCode` is generated from the
server's code catalogue, so a code that is renamed breaks the import rather
than turning a branch into one that silently never matches. Every member is
a refusal a caller can actually receive: codes the catalogue records as
guards no request can trip are catalogued on the server and deliberately not
exported, because a branch for a code that never arrives is the same silent
non-event as a typo.

```python
from tckdb_client import RejectionCode, TCKDBHTTPError, rejection_code

try:
    client.upload("reactions", payload)
except TCKDBHTTPError as exc:
    match rejection_code(exc.code):
        case RejectionCode.SPECIES_GEOMETRY_COMPOSITION_MISMATCH:
            payload = repair_geometry(payload)  # recoverable
        case RejectionCode.REACTION_MASS_BALANCE_FAILED:
            raise                               # the deposit is wrong
        case None:
            raise                               # not a code this client knows
    print(exc.detail)                           # prose, for a human
    print(exc.response_json["context"])         # structured facts, or {}
```

`context` is not populated for every code, and `{}` is often the correct
answer rather than a gap. The server's rule: a code that names a
**relationship** — `*_conflict`, `*_mismatch`, anything asserting that
two things disagree — puts the *which* in `context` (a field path, a
public ref, a local key, a constraint name, a count; never a database row
id). A code that names a **thing** — `unknown_release`, `missing_filter`
— is already the whole message and has no second fact to report. Branch
on `code` first and enrich from `context` if it is there; never make a
non-empty `context` a precondition for handling a refusal.

Cap refusals (`limit_too_large`, `offset_too_large`, `geometry_too_large`,
`smiles_too_long`, `query_too_expensive`, the two `*_all_cap_exceeded`
codes and `composed_search_candidate_limit_exceeded`) are relationships
and carry **the limit** — read it and retry against it rather than
hardcoding a number, because these caps are deployment settings and a
self-hosted TCKDB may run different ones. They will never tell you how
much data is on the far side of the cap: no match count, no row count,
in `context` or `detail`. That is deliberate, not a gap to report — an
exact count off a cheap refusal would turn any filter into a census of
the server's holdings.

Use `rejection_code(exc.code)` rather than `RejectionCode(exc.code)`: a
server is routinely newer than the client pinned against it, and a code
added since must return `None` rather than raise inside your own error
handler.

`VALIDATION_REJECTION_CODES` and `CONFLICT_REJECTION_CODES` say which
status carries each code — `409` means a database-held rule refused the
write, `422` means nothing was written at all. A code can be in both: the
same scientific claim is sometimes enforced at the wire boundary and
again in the schema.

### Codes that are not refusals: `NON_RETRYABLE_CODES`

`RejectionCode` covers `4xx` only, because a `5xx` is not a refusal of
anything the caller did and there is no payload to repair. But a handful
of TCKDB's `5xx` conditions are *deterministic*, and a retry policy needs
to know which:

```python
from tckdb_client import NON_RETRYABLE_CODES
# frozenset of plain strings, not RejectionCode members
```

`GET /scientific/artifacts/{sha256}/download` reports two different
storage failures. A `503 artifact_storage_unavailable` means the object
store did not answer — wait and try again. A `502 artifact_object_missing`
means the store answered and the bytes a still-published record points at
are gone; only an operator restoring the object changes that, so replaying
the request is a backoff schedule with no exit. Both statuses are in
`DEFAULT_RETRY_STATUS_CODES`, so the status alone cannot tell them apart.

Artifact **uploads** add a third: `507 artifact_storage_full` means the
object store answered and refused the write because it has no room — out
of space, or over its quota. Nothing about the request is wrong and no
amount of waiting helps until an operator frees space, so do not retry it;
surface it to whoever runs the deployment. Unlike the pair above, this one
needs no deny list to get right — `507` is deliberately *not* in
`DEFAULT_RETRY_STATUS_CODES`, so any client, in any language, pinned to any
version, stops after one attempt on the status alone.

`RetryPolicy` consults this set automatically — if you pass a policy, you
get the behaviour for free. It is a **deny list**: a code the set does not
name is retried exactly as before, so a client pinned against an older
release never stops retrying a transient failure it has not heard of.

What each code asserts, why it refuses rather than warns, and how
legitimate chemistry it would otherwise reject can still be deposited, is
in [the scientific check register](https://github.com/TCKDB/TCKDB/blob/main/docs/guides/scientific_check_register.md).
