# Public hosted querying

## Purpose

TCKDB can be queried as a hosted scientific information system.

Users query by chemistry and scientific constraints. TCKDB returns
thermochemistry, kinetics, calculations, geometries, provenance, review
state, and stable public refs for follow-up reads.

This guide is the entry point for that public surface. It is
deliberately framing-neutral: TCKDB is not an ARC cache, not an RMG
helper, and not workflow-tool-specific. Workflow tools and producer
adapters are downstream consumers of the same public API documented
here.

Companion documents:

- [scientific_query_cookbook.md](scientific_query_cookbook.md) —
  recipe-style examples.
- [workflow_tool_scientific_reads.md](workflow_tool_scientific_reads.md) —
  integration patterns for workflow tools.
- [internal_ids_visibility_policy.md](../specs/internal_ids_visibility_policy.md) —
  policy for integer-ID exposure.
- [deployment_modes.md](../deployment/deployment_modes.md) —
  the three TCKDB deployment modes (local/offline, self-hosted
  online, hosted/community) and where this public surface fits.

## What is publicly queryable

| Surface | Endpoint |
|---|---|
| species search | `GET /scientific/species/search` |
| reaction search | `GET\|POST /scientific/reactions/search` |
| thermo search | `GET\|POST /scientific/thermo/search` |
| kinetics search | `GET\|POST /scientific/kinetics/search` |
| species-calculation search | `GET\|POST /scientific/species-calculations/search` |
| geometry detail | `GET /scientific/geometries/{geometry_handle}` |
| species-entry thermo | `GET /scientific/species-entries/{species_entry_handle}/thermo` |
| reaction-entry kinetics | `GET /scientific/reaction-entries/{reaction_entry_handle}/kinetics` |
| reaction-entry full provenance | `GET /scientific/reaction-entries/{reaction_entry_handle}/full` |

All paths are relative to the deployment's `/api/v1` base URL.

## API key behavior

- An API key is **optional** for public scientific reads when the
  deployment allows anonymous reads.
- An API key is **required** for uploads, writes, admin, and moderation
  endpoints. The Python client raises `TCKDBAuthenticationError`
  client-side before sending if a key is not configured.
- If an API key is provided for reads, the client forwards it as the
  `X-API-Key` header. Hosted deployments may use authenticated reads
  for higher quotas, attribution, or per-user analytics.

Anonymous CLI call:

```bash
python clients/python/tckdb-client/examples/query_cookbook.py \
  --base-url http://127.0.0.1:8010/api/v1 \
  --smiles "O" \
  --recipe thermo
```

Authenticated CLI call (same recipe, attributed):

```bash
python clients/python/tckdb-client/examples/query_cookbook.py \
  --base-url http://127.0.0.1:8010/api/v1 \
  --api-key "$TCKDB_API_KEY" \
  --smiles "O" \
  --recipe thermo
```

## Public refs and follow-up reads

`*_ref` fields are the **public handles** for hosted querying:

- `spc_...` — species
- `spe_...` — species entry (charge/multiplicity/electronic-state realization)
- `rxn_...` — chemical reaction
- `rxe_...` — reaction entry
- `geom_...` — geometry
- `calc_...` — calculation
- `lot_...` — level of theory
- `thm_...` — thermo record
- `kin_...` — kinetics record

Integer `*_id` fields are **internal/debug compatibility fields**. They
are hidden by default and must not appear in client code, scripts, or
public examples. See the [Internal IDs](#internal-ids) section below.

Refs chain naturally:

```python
thermo = client.search_thermo(smiles="O", collapse="first")
species_entry_ref = thermo["records"][0]["species"]["species_entry_ref"]

detail = client.get_species_thermo(
    species_entry_ref,                      # ref accepted as the handle
    temperature_min=300,
    temperature_max=2000,
)
```

The same pattern works with `get_reaction_kinetics(reaction_entry_ref, …)`,
`get_reaction_full(reaction_entry_ref)`, and
`get_geometry(geometry_ref)`.

## Common query patterns

Thermo by species:

```python
client.search_thermo(
    smiles="O",
    temperature_min=300,
    temperature_max=2000,
    collapse="first",
    include=["provenance", "review"],
)
```

Kinetics by reactants/products:

```python
client.search_kinetics(
    reactants=["C", "[OH]"],
    products=["[CH3]", "O"],
    temperature_min=500,
    temperature_max=2000,
    include=["provenance", "review"],
)
```

Lowest single-point energy for a species:

```python
client.search_species_calculations(
    smiles="O",
    calculation_type="sp",
    sort="electronic_energy_hartree",
    limit=1,
    include=["provenance"],
)
```

Optimized geometry for a species:

```python
calcs = client.search_species_calculations(
    smiles="O",
    calculation_type="opt",
    include=["geometry", "provenance"],
)
geom_ref = calcs["records"][0]["primary_output_geometry_ref"]
```

Geometry coordinates by `geom_ref`:

```python
client.get_geometry(geom_ref, include=["provenance"])
```

## Python client quick start

```python
from tckdb_client import TCKDBClient

# Anonymous — public scientific reads only.
client = TCKDBClient(base_url="http://127.0.0.1:8010/api/v1")

thermo = client.search_thermo(
    smiles="O",
    temperature_min=300,
    temperature_max=2000,
    collapse="first",
    include=["provenance", "review"],
)
```

When the deployment requires authentication, or when you want
attributed reads, pass an `api_key`:

```python
client = TCKDBClient(
    base_url="http://127.0.0.1:8010/api/v1",
    api_key=api_key,
)
```

Uploads, moderation, and admin endpoints always require an API key;
see [`clients/python/tckdb-client/README.md`](../../clients/python/tckdb-client/README.md)
for the full matrix.

## CLI examples

Two ready-to-run example scripts ship with the client:

- [`scientific_reads.py`](../../clients/python/tckdb-client/examples/scientific_reads.py) —
  one-shot section-filterable read demo.
- [`query_cookbook.py`](../../clients/python/tckdb-client/examples/query_cookbook.py) —
  recipe-driven walkthrough that mirrors
  [`scientific_query_cookbook.md`](scientific_query_cookbook.md).

Thermo only:

```bash
python clients/python/tckdb-client/examples/scientific_reads.py \
  --base-url http://127.0.0.1:8010/api/v1 \
  --smiles "O" \
  --only thermo \
  --json
```

OPT calculations plus geometry coordinates:

```bash
python clients/python/tckdb-client/examples/scientific_reads.py \
  --base-url http://127.0.0.1:8010/api/v1 \
  --smiles "O" \
  --only calculations,geometry \
  --calculation-type opt \
  --json
```

Both scripts accept `--api-key` but neither requires it for scientific
reads.

## Geometry coordinates

Geometry coordinates are not embedded in calculation-search responses.
That keeps search payloads small and lets the geometry detail endpoint
serve coordinates with their own provenance block.

The contract:

- `species-calculations/search` returns `geometry_ref` handles
  (`input_geometry_ref`, `primary_output_geometry_ref`,
  `output_geometry_refs`, …).
- `GET /scientific/geometries/{geometry_handle}` returns `symbols`,
  `coords`, `xyz_text`, plus a `provenance` block listing the
  calculations that produced or consumed the geometry.

Where to find the ref:

- **SP calculations** typically expose an `input_geometry_ref` — they
  consume a geometry rather than produce one.
- **OPT calculations** expose `primary_output_geometry_ref` when the
  optimized geometry was persisted; `input_geometry_ref` is the
  starting structure.
- **Freq calculations** point at the geometry whose Hessian was
  evaluated.

## `include=` parameter

`include=` accepts a **comma-separated token list** on every
scientific read endpoint. That is the documented public form:

```
GET /scientific/species/search?smiles=O&include=provenance,review
```

The Python client takes `include=["provenance", "review"]` and
serializes it into the same comma-separated query value. Repeated
`include=` query parameters are still parsed for HTTP client
compatibility (`?include=a&include=b`) but the comma-separated form
is canonical — that is what OpenAPI, the docs, and the client emit.
Tokens are trimmed of surrounding whitespace, empty entries are
dropped, and duplicates are deduplicated server-side.

`include=all` expands to every legal token for the endpoint **except
`internal_ids`**. Unknown tokens — or tokens known but illegal for
that endpoint — return 422.

## Internal IDs

Internal integer IDs (`species_id`, `species_entry_id`, `geom_id`, etc.)
are hidden by default. They are compatibility/debug fields, not part of
the public hosted querying contract.

To opt in to integer IDs:

- The client must request `include=["internal_ids"]` (or
  `include=["internal_ids", …]` alongside other tokens). Both
  `include=all,internal_ids` and `include=all&include=internal_ids`
  produce the same resolved set.
- The deployment must set `ALLOW_PUBLIC_INTERNAL_IDS=true`. Public
  deployments leave this `false`.
- `include=["all"]` does **not** include `internal_ids`. The opt-in is
  intentional.

Public documentation, examples, and integrations should always use
`*_ref` handles. See the
[internal-ids visibility policy](../specs/internal_ids_visibility_policy.md)
for the full contract.

## Abuse-control expectations

Hosted deployments enforce application-level rate limits and query
caps. The full policy lives in
[public_read_abuse_controls.md](../specs/public_read_abuse_controls.md).
In short:

- Anonymous reads are rate-limited per client IP; authenticated
  reads have a larger, separate budget keyed by API-key or session
  fingerprint. A rejected request returns HTTP 429 with the stable
  code `rate_limit_exceeded`.
- `limit` is clamped to `PUBLIC_MAX_LIMIT` (default 200), `offset`
  to `PUBLIC_MAX_OFFSET` (default 10 000). Exceeding values get
  HTTP 422 with `limit_too_large` / `offset_too_large` in the
  `detail` field.
- `/scientific/geometries/{handle}` refuses geometries with more
  than `MAX_GEOMETRY_ATOMS_PUBLIC` atoms (default 500;
  `geometry_too_large`).
- `/scientific/reaction-entries/{handle}/full` caps expanded
  sub-arrays at `MAX_FULL_CALCULATIONS_PUBLIC` etc. (default 100
  each; `query_too_expensive`).
- `/scientific/reactions/search` requires at least one chemistry or
  ref filter (`missing_reaction_search_filter`).
- CORS is **not** permissive by default; deployments must set
  `CORS_ALLOW_ORIGINS` to an explicit list to enable browser
  cross-origin calls.
- Login and register are throttled per IP with their own buckets so
  credential stuffing and account spam are rejected with 429 before
  saturating the DB.

Anonymous-friendly client reads are **not** themselves an
abuse-control mechanism. The client only decides whether it
requires a key before sending; limits and shaping are server-side.
The reverse-proxy / CDN should still layer in coarser per-IP rate
limits, body-size caps, and statement timeouts as described in the
spec.

Deployments behind a reverse proxy must configure
`TRUSTED_PROXY_HEADER` correctly (see the "Trusted-proxy header"
section of the abuse-control spec) — otherwise per-IP buckets either
collapse onto a shared transport peer or, worse, become trivially
spoofable.

The public scientific API is `/api/v1/scientific/*`. Legacy
non-scientific read routes (`/api/v1/thermo`, `/api/v1/kinetics`,
`/api/v1/species`, ...) require authentication by default in hosted
deployments via `LEGACY_READS_REQUIRE_AUTH`. Use the scientific
surface for anonymous public reads.

## Known non-blocking follow-ups

Surfaced by the real-data usability audit
([docs/audits/real_data_query_usability_audit.md](../audits/real_data_query_usability_audit.md))
and tracked here so consumers know what to expect. None block public
hosted querying.

- **Software release version normalization** — ORCA records currently
  report `software_release.version = "ORCA 6.0.0"` (software name
  prefix included). Upload-side polish; reads return what was stored.
- **NASA temperature-bound review** — some NASA polynomial records use
  `t_low = 10 K`, which is unusually wide. Producers should review fit
  ranges; consumers should check `t_low`/`t_high` against their
  intended use before extrapolating.
- **Upload-side TS-opt provenance link persistence** — some
  TS-backed kinetics records have `ts_opt_calculation_ref = None`
  because the upload pipeline did not persist the TS-opt → calculation
  link. The read API surfaces what was uploaded; the fix is upload-side
  denormalization.

## Related documentation

- [scientific_query_cookbook.md](scientific_query_cookbook.md) — recipe walkthrough.
- [workflow_tool_scientific_reads.md](workflow_tool_scientific_reads.md) — workflow-tool integration patterns.
- [scientific_read_demo_data.md](scientific_read_demo_data.md) — demo data setup.
- [`clients/python/tckdb-client/README.md`](../../clients/python/tckdb-client/README.md) — client install, configure, auth matrix.
- [internal_ids_visibility_policy.md](../specs/internal_ids_visibility_policy.md) — internal-IDs contract.
- [real_data_query_usability_audit.md](../audits/real_data_query_usability_audit.md) — usability audit and known follow-ups.
