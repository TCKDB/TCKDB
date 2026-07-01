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

Uploads are gated by `require_supported_tckdb_client` so stale clients
can't write malformed payloads. See
[`docs/guides/system_flow.md`](../../../docs/guides/system_flow.md) §2
for the full request lifecycle and `docs/specs/read_api_mvp.md` for the
read-API envelope contract.
