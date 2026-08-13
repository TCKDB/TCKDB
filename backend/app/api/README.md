# `app/api/` — FastAPI routes

Thin HTTP layer. Routes parse/authorize, then delegate to workflows
(writes) or `services/scientific_read/` (reads). No business logic here.

- `app.py` — app factory + middleware stack (`RequestID → RateLimit → CORS`).
- `router.py` — composes one `api_router` mounted at `/api/v1`.
- `routes/` — one module per resource.

## Route groups

| Prefix | Auth | Modules |
|---|---|---|
| `/scientific` | public | the read surface (delegates to `services/scientific_read/`) |
| `/uploads`, `/bundles` | API key + client version gate | `uploads.py`, `bundles.py` → workflows |
| `/submissions`, `/record-reviews`, `/admin` | per-route / curator | `submissions.py`, `record_reviews.py`, `admin.py` |
| `/jobs` | API key | `jobs.py` — async upload job status |
| `/species`, `/reactions`, `/kinetics`, … | auth-gated (legacy) | per-entity read modules |

## Error codes

`code_catalogue.py` enumerates every code the API can put in the `code`
field of an error body, with the mechanism that puts each one there. It
is the source the client's `RejectionCode` enum is
generated from, and it is **not** `app/scientific_checks/`: that register
enumerates positions about chemistry, which is a smaller and differently
motivated set, and generating the client enum from it meant a refusal
that was correctly excluded from the register was also a refusal no
client could name. Adding a catalogue entry is cheap because it claims
only that the code exists; adding a register entry stays expensive
because it claims a position a referee could argue with.

Uploads are gated by `require_supported_tckdb_client` so stale clients
can't write malformed payloads. See
[`docs/guides/system_flow.md`](../../../docs/guides/system_flow.md) §2
for the full request lifecycle and `docs/specs/read_api_mvp.md` for the
read-API envelope contract.
