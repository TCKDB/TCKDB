# Read-only TCKDB MCP integration

**Status:** implemented — all nine MVP tools shipped at [`integrations/mcp/`](../../integrations/mcp/); 535 tests passing  
**Scope:** read-only MCP server wrapping `/api/v1/scientific/*`  
**Location:** `integrations/mcp/` (sibling to `backend/` and `clients/`)  
**Out of scope:** any write, auth, admin, moderation, bulk-export, or artifact-download surface

---

## 1. Context & goal

TCKDB exposes a public-read scientific HTTP API under `/api/v1/scientific/*` covering search ([species](../../backend/app/api/routes/scientific/species.py), [reactions](../../backend/app/api/routes/scientific/reactions.py), [thermo](../../backend/app/api/routes/scientific/thermo_search.py), [kinetics](../../backend/app/api/routes/scientific/kinetics_search.py), [species-calculations](../../backend/app/api/routes/scientific/species_calculations_search.py)), get-by-ref ([species-entry thermo](../../backend/app/api/routes/scientific/thermo.py), [reaction-entry kinetics](../../backend/app/api/routes/scientific/kinetics.py), [geometry](../../backend/app/api/routes/scientific/geometries.py)), and composite reads ([reaction-entry full](../../backend/app/api/routes/scientific/provenance.py)). All endpoints emit a uniform envelope and a stable error schema, and use Phase-D public refs (`spe_*`, `rxe_*`, `geom_*`, …) as agent-facing handles.

Agentic consumers — Claude in the IDE, ARC's planner ([memory](../../CLAUDE.md): `project_arc_tckdb_client_architecture`), and future research copilots — need to query this surface without:

- inventing ad-hoc HTTP wrappers per agent harness,
- learning the include-token vocabulary by trial and error,
- ever seeing raw SQL or DB integer IDs,
- ever being given a path to mutate the database.

This spec defines a Model Context Protocol (MCP) server that wraps the scientific API as a closed set of read-only tools. The MCP is the **single sanctioned agent entrypoint** to TCKDB; any future capability (artifact download, basin browsing) lands as a new named tool, never a generic escape hatch.

The MCP is **design-only at this point** — no code is shipped by this spec.

---

## 2. Non-goals

- ❌ No raw SQL or query-string passthrough.
- ❌ No `/api/v1/uploads/*` or `/api/v1/submissions/*` (write surface).
- ❌ No `/auth/*` — no login, register, or API-key management tools.
- ❌ No admin / moderation / curation / review-action tools.
- ❌ No bulk export.
- ❌ No artifact / output-file download (deferred — would require an explicit re-scoping PR).
- ❌ No `include=internal_ids` — DB integer IDs never reach the agent.
- ❌ No integer-ID inputs — all entity handles are public refs.
- ❌ No background jobs, no caching layer, no rate-limit overrides.
- ❌ No backend Python imports — MCP speaks HTTP only.

---

## 3. Repo location & rationale

### Where

```
integrations/
  mcp/
    pyproject.toml         # own version, deps = [tckdb-client, mcp]
    README.md              # invocation + env vars + claude_desktop_config example
    src/tckdb_mcp/
      __init__.py
      server.py            # MCP server entrypoint (stdio transport)
      config.py            # TCKDB_BASE_URL, TCKDB_API_KEY, defaults
      errors.py            # tckdb-client exception → MCP error mapping
      tools/
        __init__.py
        health.py
        species.py
        reactions.py
        thermo.py
        kinetics.py
        geometry.py
    tests/
```

Sibling to [`backend/`](../../backend/) and [`clients/`](../../clients/). Not nested under either.

### Why in-repo for now

1. **Schema is volatile.** Single-migration policy ([feedback](../../.claude/rules/migration-rules.md)) means scientific schemas and the include-token vocabulary can change between commits. The MCP tool catalogue must move in lockstep with the API surface; cross-repo PRs would lag.
2. **One reviewer sees both sides of a contract change.** When [_LEGAL_INCLUDE_TOKENS](../../backend/app/services/scientific_read/thermo.py) gains a new token or a route adds a filter, the same PR can extend the MCP tool schema.
3. **Public-ref policy is mid-rollout** (Phase D, [docs/specs/public_identifier_policy.md](public_identifier_policy.md), [docs/specs/internal_ids_visibility_policy.md](internal_ids_visibility_policy.md)). Living in-repo lets the MCP track ref prefix additions without versioning gymnastics.

### Why isolated under `integrations/`

1. **No backend imports.** MCP depends only on `tckdb-client` ([clients/python/](../../clients/python/)) and the upstream `mcp` SDK. SQLAlchemy 2, psycopg, RDKit, Alembic stay out of the install closure.
2. **Independent versioning.** `integrations/mcp/pyproject.toml` carries its own version; tckdb-client bumps and backend changes don't force MCP version churn (and vice versa, per [tckdb_client_version_bump](../../CLAUDE.md) discipline).
3. **Move-out safety.** When extraction is right, `git mv integrations/mcp …` is mechanical — no import surgery, no shared `app.*` modules.
4. **Establishes the `integrations/` convention** for future adjacent surfaces (LSP, Slack bot, CI lints, schema-doc bot). None of those belong under `clients/` (they aren't libraries) or `backend/` (they aren't the API).

### When to split into its own repo

Any one of these triggers consideration:

- MCP gains **independent release cadence** — more than monthly cuts disjoint from backend changes.
- **External contributors** begin maintaining it (community MCP server registry, vendor pickup).
- It accumulates **per-deployment configuration** the monorepo shouldn't carry (multi-tenant routing, per-agent auth flows, telemetry vendors).
- The scientific API hits **v1 hosted GA** and the include-token / public-ref vocabularies stabilize. At that point a separate repo with a pinned `tckdb-client>=X` dependency is the natural home.

Until any of these is true, in-repo wins.

---

## 4. Why HTTP, not DB or services

| Concern | Imported backend services | Direct DB access | HTTP via `tckdb-client` |
|---|---|---|---|
| Visibility & review-status policy | Re-implement | Re-implement | **Server enforces** |
| `internal_ids` gating per [ALLOW_PUBLIC_INTERNAL_IDS](internal_ids_visibility_policy.md) | Re-implement | Re-implement | **Server enforces** |
| Timeout & query-cancel | Re-implement | Re-implement | **Server enforces** |
| Install closure | SQLAlchemy + psycopg + RDKit (optional) | + DB driver | **httpx + mcp** |
| Deployment portability | Same-host only | Same-host only | **Any base_url** |
| Failure-domain isolation | Coupled | Coupled | **Independent** |

The HTTP boundary is exactly the visibility/auth contract the API team designed; re-implementing it in the MCP guarantees divergence. The MCP is a thin shape-shifter on top of [tckdb-client](../../clients/python/src/tckdb_client/client.py), not a parallel access path.

This also matches the [docs/clients/generic-client-targeting.md](../clients/generic-client-targeting.md) model: every TCKDB client targets a deployment by `(base_url, api_key)` and changing `base_url` swaps between local, lab-server, and hosted with no other config.

---

## 5. Architecture

```
┌────────────┐  MCP stdio   ┌──────────────────┐  HTTP   ┌──────────────────┐
│ Agent host │ ───────────► │ tckdb-mcp server │ ──────► │ TCKDB backend    │
│ (Claude,   │              │ (integrations/   │  X-API- │ /api/v1/         │
│  ARC, …)   │ ◄─────────── │  mcp/)           │  Key    │  scientific/*    │
└────────────┘  JSON tools  └──────────────────┘         └──────────────────┘
                                     │
                                     │ depends on
                                     ▼
                            ┌──────────────────┐
                            │ tckdb-client     │
                            │ (clients/python/ │
                            │  tckdb-client)   │
                            └──────────────────┘
```

The MCP server is a **stateless** process. Each tool call resolves to one HTTP request (or two, for tools that pre-resolve a ref). No caching, no session state, no background work.

---

## 6. Configuration

Read once at startup; never re-read (matches [tckdb-client v0 spec](tckdb-client-v0-spec.md)):

| Env var | Required | Default | Purpose |
|---|---|---|---|
| `TCKDB_BASE_URL` | no | `http://127.0.0.1:8010/api/v1` | API root including `/api/vN` |
| `TCKDB_API_KEY` | no | unset | `X-API-Key` header value when present; never logged |
| `TCKDB_MCP_DEFAULT_LIMIT` | no | `25` | default `limit` on search tools before cap |
| `TCKDB_MCP_MAX_LIMIT` | no | `50` | upper bound on per-tool `limit` (server allows 200) |
| `TCKDB_MCP_TIMEOUT_SECONDS` | no | `30` | per-request httpx timeout |

CLI overrides:

```
tckdb-mcp --base-url URL --api-key-env VAR
```

No config files. Two values fully determine the target.

---

## 7. Public ref policy

The MCP exposes only **public refs** as entity handles. Prefixes accepted by MCP tools:

| Prefix | Entity | Source |
|---|---|---|
| `spe_*` | species_entry | path-handle resolver, [handles.py](../../backend/app/services/scientific_read/handles.py) |
| `rxe_*` | reaction_entry | path-handle resolver |
| `geom_*` | geometry | path-handle resolver |
| `spc_*` | species (parent) | search results |
| `rxn_*` | chemical_reaction (parent) | search results |
| `lot_*` | level_of_theory | search results, filter inputs |

Rules:

- **Tool inputs that take a handle** must start with the expected prefix. Other prefixes or integer-shaped strings are rejected client-side with a clear `invalid_input` error before the HTTP call.
- **Tool outputs** propagate every `*_ref` field verbatim from the server. Agents chain refs across tool calls; the MCP never rewrites or invents them.
- **`include=internal_ids` is never sent.** Even on permissive deployments (`ALLOW_PUBLIC_INTERNAL_IDS=true`) integer IDs serve no agent purpose and would pollute context.

---

## 8. Include / collapse policy

The server documents per-endpoint legal `include=` tokens in [`_LEGAL_INCLUDE_TOKENS`](../../backend/app/services/scientific_read/) and enforces them via [`validate_includes`](../../backend/app/services/scientific_read/common.py#L86) (422 `unknown_include_token` on mismatch). The MCP mirrors this vocabulary in tool schemas as enums, plus:

- **Defaults are conservative.** Search tools default to `[]` (no expansions). Get-entry tools default to `["provenance"]` only.
- **`collapse=all`** is the default for search tools; tools may set `collapse="first"` to return at most one record.
- **`internal_ids` is removed from every tool's enum.** Even though the server lists it, the MCP cannot request it.
- **`all` is exposed** as a convenience expansion for the public-token superset (server behavior: [validate_includes](../../backend/app/services/scientific_read/common.py#L108) expands `all` minus `internal_tokens`).
- **No client-side preset wrapper in v0.** Earlier draft proposed `preset: "minimal"|"default"|"full"`; deferred — adds an indirection the agent doesn't need when explicit token arrays already work. Reconsider if usage shows token-array fatigue.

The per-tool token list shown in §10 is taken verbatim from the source.

---

## 9. Error policy

`tckdb-client` raises a structured exception hierarchy ([clients/python/src/tckdb_client/errors.py](../../clients/python/src/tckdb_client/errors.py)) carrying `status_code`, `code`, `detail`, `response_json`, `headers`. The MCP maps these into a uniform tool-error envelope:

```json
{
  "code": "<stable_token>",
  "detail": "<human-readable, safe>",
  "http_status": 422
}
```

Stable codes surfaced by the MCP:

| Code | Trigger | HTTP |
|---|---|---|
| `not_found` | server 404 (unknown ref, no records) | 404 |
| `invalid_input` | server 422 (unknown include token, bad pagination, handle prefix mismatch, sort=) | 422 |
| `auth_required` | server 401 (when a hosted policy requires a key) | 401 |
| `forbidden` | server 403 | 403 |
| `conflict` | server 409 (shouldn't fire on read; surfaced for completeness) | 409 |
| `service_unavailable` | server 503 (DB timeout, transient infra) | 503 |
| `network_error` | httpx transport failure | — |
| `timeout` | per-request timeout exceeded | — |
| `internal_error` | unmapped 5xx | 500 |

Hardening rules:

- **`detail` is scanned for DB-ID-shaped substrings** (defensive — the server already enforces this, per [integrity-error-response-hardening-spec.md](../integrity-error-response-hardening-spec.md)) and replaced with `"<id>"` if found. Belt-and-braces.
- **Validation errors include the offending token** in `detail` so the agent can self-correct on retry.
- **`httpx` exceptions are never re-raised raw** to the agent; they're always wrapped.
- **API keys are never echoed** into any error payload.

---

## 10. MVP tool catalogue

Nine tools. Each is a closed contract; the MCP exposes no generic "call any endpoint" tool.

For each tool, all input fields are optional unless marked **required**. Output fields are passthrough from the corresponding [scientific read schema](../../backend/app/schemas/reads/) unless stated otherwise.

### 10.1 `tckdb_health`

- **Purpose:** verify the configured `TCKDB_BASE_URL` is reachable and the DB is up.
- **Endpoint:** `GET /health`
- **Auth:** none.
- **Input:** `{}` (no parameters)
- **Output:** `{"status": "ok"}` or an error envelope (§9).
- **Example:**
  ```json
  // call
  {}
  // result
  {"status": "ok"}
  ```

### 10.2 `tckdb_search_species`

- **Purpose:** discover species_entries by chemistry or by ref.
- **Endpoint:** `POST /scientific/species/search` ([route](../../backend/app/api/routes/scientific/species.py))
- **Legal `include` tokens (MCP exposes):** `thermo`, `statmech`, `transport`, `conformers`, `review`, `all`. *(server also accepts `internal_ids`; MCP omits it)*
- **Default `include`:** `[]`. **Default `collapse`:** `"all"`.
- **Input:**
  ```
  {
    smiles?: string,
    inchi?: string,
    inchi_key?: string,
    formula?: string,
    charge?: integer,
    multiplicity?: integer,
    electronic_state_kind?: string,
    species_entry_kind?: string,
    species_ref?: string,           // must start with "spc_"
    species_entry_ref?: string,     // must start with "spe_"
    min_review_status?: string,
    include_rejected?: boolean,     // default false
    include_deprecated?: boolean,   // default false
    offset?: integer (>=0, default 0),
    limit?: integer (1..50, default 50),
    include?: string[] (subset of legal tokens, default []),
    collapse?: "all" | "first" (default "all")
  }
  ```
- **Output:** server search envelope unchanged: `{request, pagination, records, review_summary}`. Each record carries `species_entry_ref`, `species_ref`, `availability`, `review`, and optional expansion blocks per `include`.
- **Errors:** `invalid_input` for unknown include tokens, bad pagination, prefix mismatch; `service_unavailable` for query timeouts.
- **Example:**
  ```json
  {"smiles": "CCO", "include": ["thermo"], "limit": 5}
  ```

### 10.3 `tckdb_search_reactions`

- **Purpose:** find reactions by reactant/product chemistry, family, or ref.
- **Endpoint:** `POST /scientific/reactions/search` ([route](../../backend/app/api/routes/scientific/reactions.py))
- **Legal `include`:** `kinetics`, `transition_states`, `species`, `review`, `all`.
- **Default `include`:** `[]`. **Default `collapse`:** `"all"`.
- **Input:**
  ```
  {
    reactants?: string[],            // SMILES list (AND semantics on multiset)
    products?: string[],             // SMILES list
    direction?: "forward" | "reverse" | "either",  // default "either"
    family?: string,
    reaction_ref?: string,           // must start with "rxn_"
    reaction_entry_ref?: string,     // must start with "rxe_"
    min_review_status?: string,
    include_rejected?: boolean,
    include_deprecated?: boolean,
    offset?, limit?, include?, collapse?     // as §10.2
  }
  ```
- **Output:** search envelope; each record carries `reaction_entry_ref`, `reaction_ref`, optional `kinetics[]`, `transition_states[]`, `species[]`.
- **Example:**
  ```json
  {"reactants": ["[OH]", "CC"], "products": ["O", "[CH2]C"], "include": ["kinetics"]}
  ```

### 10.4 `tckdb_search_thermo`

- **Purpose:** chemistry-first thermo lookup.
- **Endpoint:** `POST /scientific/thermo/search` ([route](../../backend/app/api/routes/scientific/thermo_search.py))
- **Legal `include`:** `provenance`, `calculations`, `artifacts`, `review`, `all`.
- **Default `include`:** `[]`. **Default `collapse`:** `"all"`.
- **Input:**
  ```
  {
    // species identity (any subset)
    smiles?, inchi?, inchi_key?, formula?, charge?, multiplicity?,
    species_ref?, species_entry_ref?,
    // thermo filters
    temperature_min?: number,
    temperature_max?: number,
    model_kind?: string,
    level_of_theory_id?: integer,    // MCP rejects; use level_of_theory_ref
    level_of_theory_ref?: string,    // must start with "lot_"
    software?: string,
    min_review_status?, include_rejected?, include_deprecated?,
    offset?, limit?, include?, collapse?
  }
  ```
  *MCP convention:* `level_of_theory_id` is dropped from the MCP schema (integer-ID rejection). Agents pass `level_of_theory_ref`.
- **Output:** search envelope; records include thermo coefficients and optional expansion blocks.
- **Example:**
  ```json
  {"smiles": "CC=O", "temperature_min": 298, "temperature_max": 1500, "include": ["provenance"]}
  ```

### 10.5 `tckdb_search_kinetics`

- **Purpose:** chemistry-first kinetics lookup.
- **Endpoint:** `POST /scientific/kinetics/search` ([route](../../backend/app/api/routes/scientific/kinetics_search.py))
- **Legal `include`:** `provenance`, `calculations`, `artifacts`, `review`, `species`, `transition_states`, `path_search`, `irc`, `all`.
- **Default `include`:** `[]`. **Default `collapse`:** `"all"`.
- **Input:**
  ```
  {
    reactants?: string[], products?: string[], direction?, family?,
    reaction_ref?, reaction_entry_ref?,
    temperature_min?, temperature_max?, pressure?,
    model_kind?, level_of_theory_ref?, software?,
    min_review_status?, include_rejected?, include_deprecated?,
    offset?, limit?, include?, collapse?
  }
  ```
- **Output:** search envelope; records carry kinetics models + optional expansions.
- **Example:**
  ```json
  {"reactants": ["[OH]", "CC"], "temperature_min": 300, "temperature_max": 2000,
   "include": ["transition_states", "path_search"]}
  ```

### 10.6 `tckdb_get_species_entry_thermo`

- **Purpose:** retrieve all thermo records attached to a specific species_entry.
- **Endpoint:** `GET /scientific/species-entries/{species_entry_ref}/thermo` ([route](../../backend/app/api/routes/scientific/thermo.py))
- **Legal `include`:** `provenance`, `calculations`, `statmech`, `review`, `artifacts`, `all`.
- **Default `include`:** `["provenance"]`. (Get-by-ref is cheap; provenance is usually the next agent question.)
- **Input:**
  ```
  {
    species_entry_ref: string (REQUIRED, must start with "spe_"),
    temperature_min?, temperature_max?, model_kind?,
    level_of_theory_ref?, software?,
    min_review_status?, include_rejected?, include_deprecated?,
    offset?, limit?, include?
  }
  ```
- **Output:** `{request, pagination, records, review_summary}`; `records[]` are full thermo records.
- **Errors:** `invalid_input` on non-`spe_` ref; `not_found` if species_entry doesn't exist.
- **Example:**
  ```json
  {"species_entry_ref": "spe_01HZX4G…", "include": ["provenance", "statmech"]}
  ```

### 10.7 `tckdb_get_reaction_entry_kinetics`

- **Purpose:** retrieve all kinetics attached to a specific reaction_entry.
- **Endpoint:** `GET /scientific/reaction-entries/{reaction_entry_ref}/kinetics` ([route](../../backend/app/api/routes/scientific/kinetics.py))
- **Legal `include`:** `provenance`, `calculations`, `transition_states`, `path_search`, `irc`, `review`, `artifacts`, `all`.
- **Default `include`:** `["provenance"]`.
- **Input:**
  ```
  {
    reaction_entry_ref: string (REQUIRED, must start with "rxe_"),
    temperature_min?, temperature_max?, pressure?,
    model_kind?, level_of_theory_ref?, software?,
    min_review_status?, include_rejected?, include_deprecated?,
    offset?, limit?, include?
  }
  ```
- **Output:** envelope; `records[]` are kinetics records with the parent reaction implicit.
- **Example:**
  ```json
  {"reaction_entry_ref": "rxe_01HZY…", "include": ["transition_states", "irc"]}
  ```

### 10.8 `tckdb_get_reaction_entry_full`

- **Purpose:** one-shot composite read: species + kinetics + transition states + provenance for an entire reaction_entry. The "show me everything about this reaction" tool.
- **Endpoint:** `GET /scientific/reaction-entries/{reaction_entry_ref}/full` ([route](../../backend/app/api/routes/scientific/provenance.py))
- **Legal `include`:** `species`, `kinetics`, `transition_states`, `calculations`, `path_search`, `irc`, `scans`, `conformers`, `artifacts`, `review`, `all`.
- **Default `include`:** `["species", "kinetics", "transition_states"]`.
- **Input:**
  ```
  {
    reaction_entry_ref: string (REQUIRED, must start with "rxe_"),
    include?: string[] (subset of legal tokens, default ["species","kinetics","transition_states"])
  }
  ```
- **Output:** composite record (single object, not a list) — see [ReactionEntryFullResponse](../../backend/app/schemas/reads/) for shape.
- **Errors:** `invalid_input` on prefix mismatch or unknown token; `not_found` on missing entry.
- **Example:**
  ```json
  {"reaction_entry_ref": "rxe_01HZY…", "include": ["species", "kinetics", "transition_states", "irc"]}
  ```

### 10.9 `tckdb_get_geometry`

- **Purpose:** fetch a 3D geometry payload by ref. Geometries are discovered through `tckdb_search_species` / `tckdb_search_reactions` (via `include=["transition_states"]`) or through species-calculations search (out of MVP scope, see §12); this tool resolves the ref to coordinates.
- **Endpoint:** `GET /scientific/geometries/{geometry_ref}` ([route](../../backend/app/api/routes/scientific/geometries.py))
- **Legal `include`:** `review`, `provenance`, `all`.
- **Default `include`:** `[]`.
- **Input:**
  ```
  {
    geometry_ref: string (REQUIRED, must start with "geom_"),
    include?: string[]
  }
  ```
- **Output:** geometry record (coordinates + optional metadata).
- **Errors:** `invalid_input` on non-`geom_` ref; `not_found` on missing geometry.
- **Example:**
  ```json
  {"geometry_ref": "geom_01HZZ…", "include": ["provenance"]}
  ```

### MCP-imposed caps (above what the server allows)

| Cap | Server allows | MCP caps to |
|---|---|---|
| `limit` | 200 | **50** (configurable via `TCKDB_MCP_MAX_LIMIT`) |
| `include=internal_ids` | gated by `ALLOW_PUBLIC_INTERNAL_IDS` | **never sent** |
| Integer-ID inputs | accepted on path params | **rejected client-side** |
| `level_of_theory_id` filter | accepted | **dropped from MCP schema; use `level_of_theory_ref`** |

The `limit` cap is a context-window decision: agents pay for payload size in tokens, not bytes.

---

## 11. Security constraints (closed-set matrix)

The MCP exposes **exactly** the nine tools in §10. No tool dispatches into a generic "call endpoint X" helper. No tool accepts a SQL string, a URL fragment, or a method name. If a future capability is needed, it lands as a new named tool with explicit schema review.

| Surface | Exposed? |
|---|---|
| `/api/v1/scientific/*` GET reads | ✅ via named tools only |
| `/api/v1/scientific/*` POST searches | ✅ via named tools only |
| `/api/v1/uploads/*` | ❌ |
| `/api/v1/submissions/*` | ❌ |
| `/auth/*` | ❌ |
| Admin / moderation routes | ❌ |
| Artifact download | ❌ (deferred) |
| Bulk export | ❌ |
| `include=internal_ids` | ❌ |
| Integer-ID inputs | ❌ |
| Raw SQL / query-string passthrough | ❌ |

The API key, when present, is held in process memory only — never echoed in error payloads, logs, or tool outputs.

---

## 12. Future extensions

Triggered by demand, not date ([feedback](../../CLAUDE.md): `no_calendar_followups`). Candidates and the criteria that would unlock them:

- **`tckdb_search_species_calculations`** wrapping `/scientific/species-calculations/search`. Add when an agent flow needs calc-centric discovery (ranking, quality filtering) — likely once ARC's planner uses TCKDB for calc reuse.
- **Artifact download tool.** Requires explicit re-scoping — current MCP does not surface output files, ESS logs, or raw geometry strings beyond what's in scientific response payloads. A future tool would need: (a) a sandboxed file-handle protocol, (b) size caps, (c) a clear story for the agent host's filesystem.
- **Streaming / paged results.** Out of scope for v0; `limit=50` cap mitigates context pressure.
- **Read-only OpenAPI introspection tool.** Useful for agents that want to inventory available filters — but only after the API stabilizes.
- **Per-tool include presets.** If usage shows agents repeatedly typing the same token lists, add `preset` parameters mapping to documented sets.

Each new tool ships in a separate PR with an updated section in this spec.

---

## 13. References

- **API routes:** [backend/app/api/routes/scientific/](../../backend/app/api/routes/scientific/) and [router registration](../../backend/app/api/router.py)
- **Include-token validation:** [backend/app/services/scientific_read/common.py](../../backend/app/services/scientific_read/common.py) (`validate_includes`, `validate_pagination`, `reject_client_sort`)
- **Per-endpoint legal include sets:** `_LEGAL_INCLUDE_TOKENS` in [species.py](../../backend/app/services/scientific_read/species.py), [reactions.py](../../backend/app/services/scientific_read/reactions.py), [thermo_search.py](../../backend/app/services/scientific_read/thermo_search.py), [kinetics_search.py](../../backend/app/services/scientific_read/kinetics_search.py), [thermo.py](../../backend/app/services/scientific_read/thermo.py), [kinetics.py](../../backend/app/services/scientific_read/kinetics.py), [geometry.py](../../backend/app/services/scientific_read/geometry.py), [provenance.py](../../backend/app/services/scientific_read/provenance.py)
- **Error envelope:** [backend/app/api/errors.py](../../backend/app/api/errors.py)
- **Public ref policy:** [docs/specs/public_identifier_policy.md](public_identifier_policy.md), [docs/specs/internal_ids_visibility_policy.md](internal_ids_visibility_policy.md)
- **Transport layer the MCP reuses:** [clients/python/src/tckdb_client/client.py](../../clients/python/src/tckdb_client/client.py), errors at [errors.py](../../clients/python/src/tckdb_client/errors.py)
- **Deployment targeting model:** [docs/clients/generic-client-targeting.md](../clients/generic-client-targeting.md)
- **Abuse controls (server-side):** [docs/specs/public_read_abuse_controls.md](public_read_abuse_controls.md)
- **Read API MVP context:** [docs/specs/read_api_mvp.md](read_api_mvp.md)
- **Implemented package:** [integrations/mcp/](../../integrations/mcp/) — README, tools, tests
- **Implemented tool sources:** [`tools/`](../../integrations/mcp/src/tckdb_mcp/tools/) (one module per tool); shared path-handle helper at [`_path_handles.py`](../../integrations/mcp/src/tckdb_mcp/tools/_path_handles.py)

---

## 14. Completion note (audit, post-implementation)

All nine MVP tools in §10 are implemented and registered in [`server.py`](../../integrations/mcp/src/tckdb_mcp/server.py); 535 tests pass (`pytest integrations/mcp/tests`).

Cross-cutting invariants verified by audit:

- Every per-tool `LEGAL_INCLUDE_TOKENS` exactly mirrors the corresponding backend `_LEGAL_INCLUDE_TOKENS` **minus** `internal_ids`.
- `internal_ids` appears in the source only inside rejection paths, comments, and documentation describing its absence — never in an accepted-tokens set.
- Every tool that takes a domain handle has a non-empty `_REJECTED_INTEGER_FIELDS` set with a teaching error pointing at the corresponding `*_ref` handle.
- Public-ref prefix validation is consistent: path-handle tools delegate to [`validate_path_handle`](../../integrations/mcp/src/tckdb_mcp/tools/_path_handles.py); POST search tools use simple `startswith` prefix checks consistent with their siblings.
- Default `include` matches §10 per tool — search-tools and entry-tools default to `["provenance"]`, `tckdb_get_reaction_entry_full` defaults to `["species", "kinetics", "transition_states"]`, `tckdb_get_geometry` defaults to `[]`.
- README and spec catalogue list exactly the same nine tools.
- No personal/deployment values leak into the package or the spec.

The read-only MVP is complete. Subsequent work (artifact download, streaming, OpenAPI introspection, additional read tools) lands as named tools per the §12 future-extensions criteria.
