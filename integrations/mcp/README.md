# tckdb-mcp

Read-only Model Context Protocol (MCP) server that wraps the TCKDB
scientific HTTP API. Lets agents query species, reactions, calculations,
transition states, conformers, statmech, transport, network/PDep data,
literature links, correction references, and artifact metadata without
inventing ad-hoc HTTP wrappers, ever seeing raw SQL, or having any path
to mutate the database.

> **Read-only.** This integration deliberately exposes no upload, auth,
> admin, moderation, bulk-export, or artifact-download tools. Every
> capability is a named tool with an explicit schema; there is no
> generic endpoint-passthrough escape hatch.

See `docs/specs/mcp_readonly_integration.md`
for the full design.

## Status

**Current scientific read/query tools implemented.** The MCP tracks the
committed backend OpenAPI golden snapshot at
[`backend/tests/api/golden/openapi.json`](../../backend/tests/api/golden/openapi.json).

| Tool | Endpoint | Purpose |
|---|---|---|
| `tckdb_health` | `GET /health` | Reachability probe |
| `tckdb_search_species` | `GET /api/v1/scientific/species/search` | Species discovery |
| `tckdb_species_structure_search` | `POST /api/v1/scientific/species/structure-search` | RDKit-backed species structure search |
| `tckdb_search_reactions` | `POST /api/v1/scientific/reactions/search` | Reaction-entry discovery |
| `tckdb_search_thermo` | `POST /api/v1/scientific/thermo/search` | Chemistry-first thermo |
| `tckdb_search_kinetics` | `POST /api/v1/scientific/kinetics/search` | Chemistry-first kinetics |
| `tckdb_get_reaction_entry_kinetics` | `GET /api/v1/scientific/reaction-entries/{rxe_ref}/kinetics` | Entry-scoped kinetics |
| `tckdb_get_species_entry_thermo` | `GET /api/v1/scientific/species-entries/{spe_ref}/thermo` | Entry-scoped thermo |
| `tckdb_get_geometry` | `GET /api/v1/scientific/geometries/{geom_ref}` | Geometry detail |
| `tckdb_get_reaction_entry_full` | `GET /api/v1/scientific/reaction-entries/{rxe_ref}/full` | Composite reaction record |
| `tckdb_calculation_search` | `POST /api/v1/scientific/calculations/search` | Calculation search |
| `tckdb_calculation_detail` | `GET /api/v1/scientific/calculations/{calc_ref}` | Calculation detail |
| `tckdb_transition_state_search` | `POST /api/v1/scientific/transition-states/search` | Transition-state search |
| `tckdb_transition_state_detail` | `GET /api/v1/scientific/transition-states/{ts_ref}` | Transition-state detail |
| `tckdb_transition_state_entry_detail` | `GET /api/v1/scientific/transition-state-entries/{tse_ref}` | Transition-state entry detail |
| `tckdb_conformer_search` | `POST /api/v1/scientific/conformers/search` | Conformer search |
| `tckdb_conformer_group_detail` | `GET /api/v1/scientific/conformer-groups/{cg_ref}` | Conformer group detail |
| `tckdb_conformer_observation_detail` | `GET /api/v1/scientific/conformer-observations/{co_ref}` | Conformer observation detail |
| `tckdb_statmech_search` | `POST /api/v1/scientific/statmech/search` | Statmech search |
| `tckdb_statmech_detail` | `GET /api/v1/scientific/statmech/{sm_ref}` | Statmech detail |
| `tckdb_transport_search` | `POST /api/v1/scientific/transport/search` | Transport search |
| `tckdb_transport_detail` | `GET /api/v1/scientific/transport/{trn_ref}` | Transport detail |
| `tckdb_network_search` | `POST /api/v1/scientific/networks/search` | Network/PDep search |
| `tckdb_network_detail` | `GET /api/v1/scientific/networks/{net_ref}` | Network/PDep detail |
| `tckdb_network_solve_search` | `POST /api/v1/scientific/network-solves/search` | Network-solve search |
| `tckdb_network_solve_detail` | `GET /api/v1/scientific/network-solves/{nsolve_ref}` | Network-solve detail |
| `tckdb_network_kinetics_search` | `POST /api/v1/scientific/network-kinetics/search` | Network-kinetics search |
| `tckdb_network_kinetics_detail` | `GET /api/v1/scientific/network-kinetics/{nkin_ref}` | Network-kinetics detail |
| `tckdb_literature_records` | `GET /api/v1/scientific/literature/{lit_ref}/records` | Literature inverse records |
| `tckdb_artifact_search` | `POST /api/v1/scientific/artifacts/search` | Artifact metadata search |
| `tckdb_frequency_scale_factor_search` | `POST /api/v1/scientific/frequency-scale-factors/search` | Frequency-scale-factor references |
| `tckdb_energy_correction_scheme_search` | `POST /api/v1/scientific/energy-correction-schemes/search` | Energy-correction-scheme references |

## Deployment model — who runs what

The MCP is a small local process you run *next to* your agent host. It
translates tool calls into HTTP requests against an already-running
TCKDB API somewhere — local, lab-private, or hosted.

```
┌────────────────────────────────────┐
│ Agent host (Claude Desktop /       │
│   Claude Code / Codex / …)         │
└──────────────┬─────────────────────┘
               │  MCP stdio
               ▼
┌────────────────────────────────────┐
│ tckdb-mcp (local Python process)   │
│   • reads TCKDB_BASE_URL           │
│   • reads optional TCKDB_API_KEY   │
└──────────────┬─────────────────────┘
               │  HTTP(S) + X-API-Key
               ▼
┌────────────────────────────────────┐
│ TCKDB API (FastAPI server)         │
│   • run by the deployment hoster   │
│   • exposes /api/v1/scientific/*   │
└────────────────────────────────────┘
```

| Role | What they do |
|---|---|
| **TCKDB hoster** (the deployment operator — could be you, your lab, or a shared server) | Runs the FastAPI backend on a reachable host:port. May issue API keys. Does **not** need to run the MCP — the MCP is a client. |
| **User / agent operator** | Installs `tckdb-mcp` locally, sets `TCKDB_BASE_URL` to point at a TCKDB API, optionally sets `TCKDB_API_KEY`, registers the server in their MCP-compatible agent host's config. |
| **Agent** (Claude / etc.) | Invokes tools by name. Never sees raw SQL, never gets DB integer IDs, never has a path to mutate the database. |
| **`tckdb-mcp`** (this package) | Validates tool inputs locally, makes a single HTTP call per tool invocation, normalizes errors into a stable envelope. |

Implications:

- For normal local use, **the TCKDB API hoster does not need to deploy the MCP** — each user runs their own copy alongside their agent host.
- The MCP has no persistent state, no caching, no database connection. Restarting it is free.
- A future hosted/remote MCP (e.g. served over SSE) is *not* part of the read-only MVP.

## Configuration

All configuration is via environment variables. No config files.

| Variable | Required | Default | Purpose |
|---|---|---|---|
| `TCKDB_BASE_URL` | no | `http://127.0.0.1:8010/api/v1` | API root including `/api/v1` |
| `TCKDB_API_KEY` | no | unset | Sent as `X-API-Key` when set; never logged |
| `TCKDB_MCP_DEFAULT_LIMIT` | no | `25` | Default for `limit` on search tools |
| `TCKDB_MCP_MAX_LIMIT` | no | `50` | Cap applied to every `limit` |
| `TCKDB_MCP_TIMEOUT_SECONDS` | no | `30` | Per-request httpx timeout |

URL resolution rules:

- `GET /health` is at server root, not under `/api/v1`. With the default
  `TCKDB_BASE_URL` it resolves to `http://127.0.0.1:8010/health`.
- Scientific endpoints sit under `/api/v1`. Species search resolves to
  `http://127.0.0.1:8010/api/v1/scientific/species/search`.

## Running locally

### Install

From the TCKDB repo root, install in editable mode. Python 3.11+ is required.

```bash
# Runtime only: pulls httpx and the `mcp` SDK.
python -m pip install -e integrations/mcp

# To also run the test suite:
python -m pip install -e "integrations/mcp[test]"
```

The package exposes a single console script — `tckdb-mcp` — which starts
the stdio MCP server. It is defined in
[`pyproject.toml`](pyproject.toml) as
`tckdb-mcp = "tckdb_mcp.server:main"`.

### Configure and run

```bash
# Point at any running TCKDB API. The default is the local dev compose port.
export TCKDB_BASE_URL="http://127.0.0.1:8010/api/v1"

# Optional. Scientific reads are public on every deployment; set this
# only when the deployment requires an API key for reads (or when you
# want your traffic attributed to your account).
# export TCKDB_API_KEY="tck_..."

# Start the stdio MCP server. It reads from stdin and writes to stdout.
tckdb-mcp
```

Running `tckdb-mcp` in a shell on its own is rarely useful — the stdio
transport waits for an MCP client to drive it. Wire it into your agent
host instead (next section).

### Register with an MCP-compatible agent host

Most MCP hosts (Claude Desktop, Claude Code, Codex, and other MCP
clients) accept a JSON config of the shape below. The exact location
of that config varies by host — consult your host's documentation for
the file path.

**Console-script form** (recommended once the package is installed in
the agent host's Python environment):

```json
{
  "mcpServers": {
    "tckdb": {
      "command": "tckdb-mcp",
      "env": {
        "TCKDB_BASE_URL": "http://127.0.0.1:8010/api/v1"
      }
    }
  }
}
```

**Python-module form** (useful when the host can't find the console
script on PATH — `python` resolves more reliably than entry points
across virtualenvs):

```json
{
  "mcpServers": {
    "tckdb": {
      "command": "python",
      "args": ["-m", "tckdb_mcp.server"],
      "env": {
        "TCKDB_BASE_URL": "http://127.0.0.1:8010/api/v1"
      }
    }
  }
}
```

With an API key:

```json
{
  "mcpServers": {
    "tckdb": {
      "command": "tckdb-mcp",
      "env": {
        "TCKDB_BASE_URL": "https://your-tckdb-host.example/api/v1",
        "TCKDB_API_KEY": "tck_replace_me"
      }
    }
  }
}
```

Restart the agent host after editing its config so it re-spawns the
MCP subprocess.

## Smoke test

A two-minute end-to-end check that the MCP, configuration, and your
TCKDB API are all wired up correctly.

1. **Start (or point to) a running TCKDB API.** For a local development
   deployment the project's `docker compose up -d` brings up Postgres
   and the FastAPI server on port 8010.
2. **Set `TCKDB_BASE_URL`** in the agent host's MCP config to that
   instance.
3. **Restart the agent host** so it spawns `tckdb-mcp`.
4. **Ask the agent to call `tckdb_health`** — for example:
   > Use the TCKDB MCP to check server health.

   Expected result: `{"status": "ok"}` (the response may include extra
   fields the backend chooses to publish; the MCP forwards them
   through).
5. **Try a simple search**:
   > Use the TCKDB MCP to search for species matching SMILES `CCO`.

If the database is empty, `tckdb_health` will still succeed but
searches will return zero records. That's expected; it confirms the
plumbing is correct.

More example prompts (chemistry plausible, but no claim your DB
contains them):

> Use the TCKDB MCP to search for reactions with reactants `["[CH3]", "C"]` and products `["CH4", "[CH3]"]`.
>
> Use the TCKDB MCP to search for thermo records for SMILES `CCO` between 298 K and 1500 K.
>
> Use the TCKDB MCP to search for kinetics for reactants `["[OH]", "CC"]` and products `["O", "[CH2]C"]`.
>
> Use the TCKDB MCP to fetch the full record for reaction entry `rxe_…` (replace with a real ref from a prior search).

## Public refs

TCKDB exposes stable, opaque public handles for the entities the MCP
returns. Every input that takes a handle uses one of these prefixes:

| Prefix | Entity |
|---|---|
| `spc_*` | species (the chemistry-only identity) |
| `spe_*` | species entry (one observation/record of a species) |
| `rxn_*` | reaction (the chemistry-only identity) |
| `rxe_*` | reaction entry (one observation/record of a reaction) |
| `geom_*` | geometry (3D coordinates payload) |
| `lot_*` | level of theory |
| `calc_*` | calculation |
| `ts_*` | transition-state concept |
| `tse_*` | transition-state entry |
| `cg_*` | conformer group |
| `co_*` | conformer observation |
| `sm_*` | statmech record |
| `trn_*` | transport record |
| `net_*` | network/PDep model |
| `nsolve_*` | network solve |
| `nkin_*` | network kinetics |
| `lit_*` | literature reference |
| `fsf_*` | frequency scale factor |
| `ecs_*` | energy correction scheme |

Rules the MCP enforces locally, before any HTTP call:

- Refs must start with the right prefix for the field they're supplied
  to — `species_ref` must be `spc_…`, `reaction_entry_ref` must be
  `rxe_…`, etc.
- **Integer IDs are rejected outright** even when the underlying backend
  route could accept them. Fields like `species_id`, `reaction_entry_id`,
  `geometry_id`, `level_of_theory_id`, etc. raise `invalid_input` with a
  teaching message pointing to the corresponding `*_ref` handle.
- The MCP never requests `include=internal_ids` — DB integer IDs do not
  reach the agent.
- `include=all` is allowed only where the backend defines safe bounded
  semantics; it does not expose `internal_ids`. For network kinetics,
  tabulated `points` are explicit-only: request `include: ["points"]`
  when point data is actually needed.
- Rejected/deprecated records are hidden by default by backend defaults
  (`include_rejected=false`, `include_deprecated=false`). Tools expose
  those flags for scientific transparency when a caller explicitly asks.
- Artifact search is metadata-only. The MCP does not expose artifact
  bodies, presigned URLs, download URLs, or raw content. A raw storage
  `uri` may appear if the backend returns one; storage remains private.

Why: agents should query against stable handles that survive renames,
re-imports, and ID-space changes. DB primary keys are an implementation
detail; refs are the public surface.

## Tool workflow

The tools group naturally by what an agent is trying to do:

```
Health
  └─ tckdb_health

Chemistry discovery
  ├─ tckdb_search_species
  ├─ tckdb_species_structure_search
  ├─ tckdb_search_reactions
  ├─ tckdb_search_thermo
  └─ tckdb_search_kinetics

Scientific record search/detail
  ├─ tckdb_calculation_search / tckdb_calculation_detail
  ├─ tckdb_transition_state_search / tckdb_transition_state_detail
  ├─ tckdb_conformer_search / conformer detail tools
  ├─ tckdb_statmech_search / tckdb_statmech_detail
  ├─ tckdb_transport_search / tckdb_transport_detail
  └─ tckdb_network_* and tckdb_network_kinetics_*

References and metadata
  ├─ tckdb_literature_records
  ├─ tckdb_artifact_search
  ├─ tckdb_frequency_scale_factor_search
  └─ tckdb_energy_correction_scheme_search
```

Typical chain: `tckdb_search_reactions` → pick an `rxe_*` ref →
`tckdb_get_reaction_entry_full` to expand species, kinetics, and
transition states in a single follow-up.

## Current Examples

Species structure search:

```json
{
  "query_smarts": "[OH]",
  "mode": "substructure",
  "include": ["review"],
  "limit": 10
}
```

Reaction search:

```json
{
  "reactants": ["[OH]", "CC"],
  "products": ["O", "[CH2]C"],
  "include": ["species", "kinetics", "review"],
  "limit": 10
}
```

Network kinetics detail with explicit points:

```json
{
  "network_kinetics_ref": "nkin_...",
  "include": ["coefficients", "plog", "points", "review"]
}
```

Literature inverse records:

```json
{
  "literature_ref": "lit_...",
  "record_type": "calculation",
  "limit": 25
}
```

## Tools

### `tckdb_health`

Reachability probe. No arguments.

```json
// call
{}
// result
{"status": "ok"}
```

### `tckdb_search_species`

Search species and species_entries by public chemistry identifiers.

Inputs (all optional individually, but **at least one identity field is
required**: `smiles`, `inchi`, `inchi_key`, `formula`, `species_ref`,
or `species_entry_ref`):

```text
smiles?: string
inchi?: string
inchi_key?: string
formula?: string
charge?: integer
multiplicity?: integer
species_ref?: string         # must start with "spc_"
species_entry_ref?: string   # must start with "spe_"
electronic_state_kind?: string
species_entry_kind?: string
min_review_status?: string
include_rejected?: boolean
include_deprecated?: boolean
offset?: integer (>=0, default 0)
limit?: integer (>=1, capped at TCKDB_MCP_MAX_LIMIT)
include?: string[]           # subset of {thermo, statmech, transport,
                             #            conformers, review, all}
collapse?: "all" | "first"   # default "all"
```

Example:

```json
{"smiles": "CCO", "include": ["thermo"], "limit": 5}
```

Output: the server search envelope (`request`, `pagination`, `records`,
`review_summary`) propagated unchanged.

### `tckdb_search_reactions`

Search reactions and reaction_entries by reactant/product SMILES, family,
direction, or public ref.

At least one discriminator is required: `reactants`, `products`,
`reaction_ref`, `reaction_entry_ref`, or `family`. Modifiers alone
(`direction`, `min_review_status`) do not constitute a search.

```text
reactants?: string[]            # non-empty list of non-empty SMILES
products?: string[]             # non-empty list of non-empty SMILES
direction?: "forward" | "reverse" | "either"   # default "either"
family?: string
reaction_ref?: string           # must start with "rxn_"
reaction_entry_ref?: string     # must start with "rxe_"
min_review_status?: string
include_rejected?: boolean
include_deprecated?: boolean
offset?: integer (>=0, default 0)
limit?: integer (>=1, capped at TCKDB_MCP_MAX_LIMIT)
include?: string[]              # subset of {species, kinetics,
                                #            transition_states, review, all}
collapse?: "all" | "first"      # default "all"
```

Example:

```json
{
  "reactants": ["[OH]", "CC"],
  "products": ["O", "[CH2]C"],
  "include": ["kinetics", "transition_states"],
  "limit": 10
}
```

Output: the server search envelope (`request`, `pagination`, `records`,
`review_summary`) propagated unchanged.

### `tckdb_get_reaction_entry_kinetics`

Fetch kinetics records scoped to a single reaction entry by its public
`rxe_*` ref. First path-handle tool — the ref is injected into the URL
after both prefix and path-safety validation.

```text
reaction_entry_ref: string         # REQUIRED, must start with "rxe_"
temperature_min?: number
temperature_max?: number
pressure?: number
model_kind?: string                # validated server-side
level_of_theory_ref?: string       # must start with "lot_"
software?: string
min_review_status?: string
include_rejected?: boolean
include_deprecated?: boolean
offset?: integer (>=0, default 0)
limit?: integer (>=1, capped at TCKDB_MCP_MAX_LIMIT)
include?: string[]                 # subset of {provenance, calculations,
                                   #            transition_states, path_search,
                                   #            irc, review, artifacts, all};
                                   # defaults to ["provenance"]
collapse?: "all" | "first"         # default "all"
```

Example:

```json
{
  "reaction_entry_ref": "rxe_01HZY3K9X2",
  "temperature_min": 300,
  "temperature_max": 2000,
  "include": ["provenance", "transition_states"]
}
```

Output: the server kinetics envelope (`request`, `pagination`, `records`,
`review_summary`) propagated unchanged.

### `tckdb_get_species_entry_thermo`

Fetch thermo records scoped to a single species entry by its public
`spe_*` ref.

```text
species_entry_ref: string         # REQUIRED, must start with "spe_"
temperature_min?: number
temperature_max?: number
model_kind?: string                # validated server-side
level_of_theory_ref?: string       # must start with "lot_"
software?: string
min_review_status?: string
include_rejected?: boolean
include_deprecated?: boolean
offset?: integer (>=0, default 0)
limit?: integer (>=1, capped at TCKDB_MCP_MAX_LIMIT)
include?: string[]                 # subset of {provenance, calculations,
                                   #            statmech, review, artifacts,
                                   #            all}; defaults to ["provenance"]
collapse?: "all" | "first"         # default "all"
```

Example:

```json
{
  "species_entry_ref": "spe_01HZ5K9X2A",
  "temperature_min": 298,
  "temperature_max": 1500,
  "include": ["provenance", "statmech"]
}
```

Output: the server thermo envelope (`request`, `pagination`, `records`,
`review_summary`) propagated unchanged.

### `tckdb_get_geometry`

Fetch a molecular geometry payload by its public `geom_*` ref. The
simplest path-handle tool — one required ref and one optional include
list, no filters, no pagination.

```text
geometry_ref: string         # REQUIRED, must start with "geom_"
include?: string[]           # subset of {review, provenance, all}; default []
```

Example:

```json
{
  "geometry_ref": "geom_01HZ7AAA",
  "include": ["provenance"]
}
```

Output: the server geometry payload (atomic symbols + Cartesian
coords; optional review/provenance metadata when requested) propagated
unchanged.

### `tckdb_get_reaction_entry_full`

Composite scientific read for a single reaction entry — species,
kinetics, transition states, and optional calculations / path_search /
IRC / scans / conformers / artifacts / review joined into one document.
No pagination, no collapse, no temperature filters — agents either get
the resource or they don't.

```text
reaction_entry_ref: string         # REQUIRED, must start with "rxe_"
min_review_status?: string
include_rejected?: boolean
include_deprecated?: boolean
include?: string[]                 # subset of {species, kinetics,
                                   #            transition_states, calculations,
                                   #            path_search, irc, scans,
                                   #            conformers, artifacts, review,
                                   #            all};
                                   # defaults to ["species", "kinetics",
                                   #              "transition_states"]
include_review?: "summary" | "full" # default "summary"
```

Example:

```json
{
  "reaction_entry_ref": "rxe_01HZY3K9X2",
  "include": ["species", "kinetics", "transition_states", "calculations", "irc"]
}
```

Output: the server full-response envelope propagated unchanged.

### `tckdb_search_thermo`

Chemistry-first thermo search. Find thermo records starting from
SMILES / InChI / formula or a public species ref, without first running
`tckdb_search_species` to get a `spe_*` handle. Complements
`tckdb_get_species_entry_thermo`.

At least one identity discriminator is **required**: `smiles`, `inchi`,
`inchi_key`, `formula`, `species_ref`, or `species_entry_ref`.
Modifier-only requests (temperature/model/review filters alone) are
rejected to avoid unbounded scans.

```text
smiles?: string
inchi?: string
inchi_key?: string
formula?: string
charge?: integer
multiplicity?: integer
electronic_state_kind?: string
species_entry_kind?: string
species_ref?: string                 # must start with "spc_"
species_entry_ref?: string           # must start with "spe_"
temperature_min?: number (> 0 K)
temperature_max?: number (> 0 K)     # min <= max if both provided
model_kind?: "nasa" | "points" | "scalar"
level_of_theory_ref?: string         # must start with "lot_"
software?: string
min_review_status?: string
include_rejected?: boolean
include_deprecated?: boolean
offset?: integer (>= 0, default 0)
limit?: integer (>= 1, capped at TCKDB_MCP_MAX_LIMIT)
include?: string[]                   # subset of {provenance, calculations,
                                     #            artifacts, review, all};
                                     # defaults to ["provenance"]
collapse?: "all" | "first"           # default "all"
```

Example by SMILES:

```json
{"smiles": "CCO", "temperature_min": 298, "temperature_max": 1500}
```

Example by InChIKey:

```json
{"inchi_key": "LFQSCWFLJHTTHZ-UHFFFAOYSA-N", "include": ["provenance", "calculations"]}
```

Example by `spe_` ref:

```json
{"species_entry_ref": "spe_01HZ5K9X2A", "model_kind": "nasa"}
```

Integer-ID fields (`species_id`, `species_entry_id`, `thermo_id`,
`level_of_theory_id`, `calculation_id`) and `include=internal_ids` are
not accepted — use the corresponding `*_ref` handles.

Output: the server search envelope (`request`, `pagination`, `records`,
`review_summary`) propagated unchanged.

### `tckdb_search_kinetics`

Chemistry-first kinetics search. Find kinetics records starting from
reactant/product SMILES, a reaction family, or a public reaction ref —
without first running `tckdb_search_reactions` to get an `rxe_*` handle.
Complements `tckdb_get_reaction_entry_kinetics`.

At least one identity discriminator is **required**: `reactants`,
`products`, `reaction_ref`, `reaction_entry_ref`, or `family`. Modifiers
alone (`direction`, temperature/pressure, model_kind, review filters)
are rejected to avoid unbounded scans.

```text
reactants?: string[]                 # non-empty SMILES list
products?: string[]                  # non-empty SMILES list
direction?: "forward" | "reverse" | "either"   # modifier; default "either"
family?: string
reaction_ref?: string                # must start with "rxn_"
reaction_entry_ref?: string          # must start with "rxe_"
temperature_min?: number (> 0)
temperature_max?: number (> 0)       # min <= max if both provided
pressure?: number (> 0)
model_kind?: "arrhenius" | "modified_arrhenius"
level_of_theory_ref?: string         # must start with "lot_"
software?: string
min_review_status?: string
include_rejected?: boolean
include_deprecated?: boolean
offset?: integer (>= 0, default 0)
limit?: integer (>= 1, capped at TCKDB_MCP_MAX_LIMIT)
include?: string[]                   # subset of {provenance, calculations,
                                     #            artifacts, review, species,
                                     #            transition_states, path_search,
                                     #            irc, all};
                                     # defaults to ["provenance"]
collapse?: "all" | "first"           # default "all"
```

Example by reactants/products:

```json
{
  "reactants": ["[OH]", "CC"],
  "products": ["O", "[CH2]C"],
  "temperature_min": 300,
  "temperature_max": 2000,
  "include": ["provenance", "transition_states"]
}
```

Example by `rxe_` ref:

```json
{"reaction_entry_ref": "rxe_01HZY3K9X2", "model_kind": "arrhenius"}
```

Example by family:

```json
{"family": "H_Abstraction", "include": ["species", "transition_states"]}
```

Integer-ID fields (`reaction_id`, `reaction_entry_id`, `species_id`,
`species_entry_id`, `kinetics_id`, `level_of_theory_id`, `calculation_id`)
and `include=internal_ids` are not accepted — use the corresponding
`*_ref` handles.

Output: the server search envelope (`request`, `pagination`, `records`,
`review_summary`) propagated unchanged.

## What this package does NOT expose

By design — adding any of these requires an explicit spec amendment, not
a config flag.

- ❌ Raw SQL, query-string passthrough, or generic endpoint dispatch
- ❌ `/api/v1/uploads/*` and `/api/v1/submissions/*` (write surface)
- ❌ `/auth/*` (login, register, API-key management)
- ❌ Admin, moderation, curation, or review-action tools
- ❌ Bulk export
- ❌ Artifact / output-file download
- ❌ `include=internal_ids` (DB integer IDs)
- ❌ Integer-ID inputs — agents pass `*_ref` handles only

## Architecture

The MCP server talks **HTTP** to the TCKDB FastAPI backend. It does not
query Postgres directly, does not import backend services or ORM
models, and contains no chemistry logic. The HTTP boundary enforces
visibility, auth, and review-status policy server-side; the MCP is a
thin agent-facing wrapper.

```
Agent host (Claude, ARC, …)
        │  MCP stdio
        ▼
tckdb_mcp.server
        │  validates input, dispatches tools
        ▼
tckdb_mcp.http_client (httpx)
        │  HTTP + X-API-Key
        ▼
TCKDB FastAPI  ──►  Postgres
```

### Why not `tckdb-client` yet?

The first slice uses an internal ~80-line `httpx` wrapper rather than
the existing [`clients/python`](../../clients/python)
package. Two reasons:

1. `tckdb-client.health()` calls `{base_url}/health`. With the
   documented base URL of `http://host/api/v1` this resolves to
   `http://host/api/v1/health` — but health is root-mounted at
   `http://host/health`.
2. The MCP now covers many current scientific read/search endpoints.
   Keeping the wrapper local avoids coupling this integration to
   broader client generation until those surfaces settle.

The long-term plan is still to depend on `tckdb-client`. Revisit when
it supports root-relative health checks and the full scientific read
surface used here.

## Troubleshooting

Common setup issues and the first things to check.

### `tckdb_health` fails with `network_error` or `timeout`

The MCP can't reach the TCKDB API at all.

- Is the API actually running? Hit it directly:
  `curl http://127.0.0.1:8010/health` (substitute your `TCKDB_BASE_URL`'s
  host/port).
- Is `TCKDB_BASE_URL` correct? Health is mounted at server root, but
  the env var must still include the `/api/vN` suffix because scientific
  endpoints expect it. The MCP strips the suffix only for `/health`.
- If the API is remote, check the network path: firewall, VPN, SSH
  tunnel, port forward, TLS cert validity.

### `tckdb_health` works but every search returns 0 records

The wiring is fine; the data isn't there.

- The database may simply be empty (fresh local dev DB, freshly migrated
  test instance).
- Your query may not match: try fewer constraints. For chemistry
  identifiers, try a SMILES that's definitely in the dataset.
- Confirm you're pointing at the right deployment — `TCKDB_BASE_URL`
  may be pointing at a different instance than you expect.

### Server returns `auth_required` (401) or `forbidden` (403)

The deployment requires authentication for the call you made.

- Set `TCKDB_API_KEY` to a valid API key from the deployment operator
  and restart the agent host so it re-spawns the MCP with the new env.
- The MCP forwards the key as `X-API-Key`. Scientific reads are public
  on local dev; hosted deployments may gate them.
- Check the key hasn't been revoked or expired.

### MCP returns `invalid_input` before reaching the server

The MCP validated something client-side. The error `detail` says
which field. Common causes:

- You passed an integer ID (e.g. `species_id: 42`). Use the public
  `*_ref` form instead (`species_ref: "spc_…"`).
- You passed an `include` token that's legal for a sibling endpoint
  but not this one (`statmech` on `tckdb_search_thermo`,
  `transition_states` on `tckdb_get_species_entry_thermo`, etc.). The
  error lists the legal tokens for this tool.
- A search tool was called with only modifiers (e.g. `temperature_min`
  alone) and no identity discriminator. Add at least one of
  `smiles`/`inchi`/`inchi_key`/`formula`/`species_ref`/`species_entry_ref`
  (species and thermo searches) or
  `reactants`/`products`/`reaction_ref`/`reaction_entry_ref`/`family`
  (reaction and kinetics searches).
- A ref started with the wrong prefix or contained a path-unsafe
  character (`/`, `?`, `#`, `&`, whitespace).

### The MCP starts but the agent host doesn't list the tools

The host can't find or spawn the MCP binary.

- Confirm `tckdb-mcp` is on the PATH used by the host. The console
  script lives in the same Python environment's `bin/` (or `Scripts/`
  on Windows) directory where you ran `pip install`.
- If the host runs in a different shell/env than you installed into,
  prefer the Python-module config form: `"command": "python", "args":
  ["-m", "tckdb_mcp.server"]`, with the full path to the right
  `python` interpreter if needed.
- Check the host's MCP logs (most hosts surface MCP subprocess
  stderr); the MCP itself logs startup to stderr.
- Restart the host process after any config edit.

### Tests fail with `ModuleNotFoundError: tckdb_mcp`

The test bootstrap puts `src/` on `sys.path` via `conftest.py`. If
that's not enough — for example, you're running pytest from outside
the `integrations/mcp/` directory and the conftest didn't fire — do
the editable install: `pip install -e "integrations/mcp[test]"`.

## Tests

```bash
pytest integrations/mcp/tests
```

Tests mock the HTTP transport via `httpx.MockTransport` — no live
backend is required.

## Error envelope

Every tool error surfaces as:

```json
{
  "code": "<stable_token>",
  "detail": "<human-readable, safe>",
  "http_status": 422
}
```

Stable codes: `invalid_input`, `auth_required`, `forbidden`,
`not_found`, `conflict`, `rate_limited`, `service_unavailable`,
`timeout`, `network_error`, `internal_error`. Raw `httpx` exceptions
and DB integer IDs are never surfaced to the agent.
