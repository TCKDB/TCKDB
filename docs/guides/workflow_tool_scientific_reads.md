# Workflow-tool scientific reads from TCKDB

> For the framing-neutral hosted-query entry point (anonymous reads,
> refs, abuse-control expectations), see
> [public_hosted_querying.md](public_hosted_querying.md). This guide
> picks up where that one leaves off and focuses on workflow-tool
> integration patterns.

## Purpose

This guide shows developers how to integrate a workflow tool with TCKDB's
scientific read/query API using the official Python `tckdb-client`. It
covers identity discovery, retrieval of thermo and kinetics, provenance
inspection, and how to interpret review/trust state — without depending
on a frontend or a tool-specific adapter.

The goal is a stable, generic integration boundary: workflow tools talk
to TCKDB through the same documented endpoints regardless of which tool
is asking, and TCKDB stays the source of truth for filtering, ranking,
and provenance summaries.

## What this guide covers

- searching for species and reactions by chemical identity
- retrieving thermo for a `species_entry` via its public ref
- retrieving kinetics for a `reaction_entry` via its public ref
- inspecting full provenance for a reaction
- reading and interpreting the review/trust state
- using the documented filter / sort / collapse vocabulary
- handling kinetics records that are not transition-state-backed
- handling empty results and HTTP errors
- where workflow-specific reuse policy belongs (hint: not in `tckdb-client`)

> **Refs are the normal hosted handles. Integer IDs are internal/debug
> compatibility fields.**
>
> Phase D: scientific read responses hide integer `*_id` fields and
> bare integer-id arrays by default. Their public `*_ref` siblings
> (and the ref-bearing object arrays like `input_geometries`,
> `supporting_calculations`) remain visible. Workflow tools should
> query by chemistry, then use the returned `*_ref` values (e.g.
> `spe_...`, `rxe_...`, `lot_...`) for follow-up reads.
>
> To request the legacy id-bearing shape (compatibility, debugging),
> add the `internal_ids` token to the include list — e.g.
> `include=["provenance", "review", "internal_ids"]`. The opt-in is
> only effective when the deployment sets
> `ALLOW_PUBLIC_INTERNAL_IDS=true`; otherwise it is silently dropped
> and the response stays refs-only.
>
> `include=all` does **not** include `internal_ids`. Combine
> explicitly: `include=["all", "internal_ids"]`.
>
> Route inputs are unchanged — integer path handles and `*_id` query
> filters still work as inputs. See
> [`docs/specs/internal_ids_visibility_policy.md`](../specs/internal_ids_visibility_policy.md)
> and
> [`docs/specs/public_identifier_policy.md`](../specs/public_identifier_policy.md)
> for the full contract.

## What this guide does not cover

- writing data to TCKDB (uploads, contribution bundles)
- defining what "best" means for a specific workflow — that is an adapter
  decision, not a TCKDB or `tckdb-client` decision
- ARC-, RMG-, AutoTST-, KinBot-, or Arkane-specific mappings — those
  belong in tool-specific adapters
- the frontend / web UI
- direct SQL or ORM access to the database

## Architecture

```
+-----------------------+       +----------------+      +----------+
| TCKDB backend         | <---  | tckdb-client   | <--- | workflow |
| /api/v1/scientific/*  |  HTTP | (thin Python)  |      | adapter  |
+-----------------------+       +----------------+      +----------+
       ^                              ^                      ^
       |                              |                      |
   scientific reads,             query/body                tool-specific
   filtering, ranking,           serialization,            mapping, reuse
   review/trust state,           response parsing,         decisions, job
   provenance summaries          error mapping             launch policy
```

The boundary is intentional:

- **TCKDB backend** owns scientific read/query semantics, filtering,
  deterministic ranking, provenance summaries, and review/trust state.
  Documented in [`docs/specs/read_api_mvp.md`](../specs/read_api_mvp.md).
- **`tckdb-client`** is a thin Python HTTP client. It serializes
  parameters, calls the backend, and returns parsed JSON. It does not
  rank, select, or interpret responses.
- **Workflow-tool adapter** (lives in the tool's own repository) owns
  tool-specific mapping, reuse decisions, and job-launch policy. It
  consumes `tckdb-client` and applies whatever scientific judgment the
  workflow requires.

Things `tckdb-client` deliberately does **not** decide:

- whether a workflow should reuse a TCKDB result instead of running a
  fresh job
- whether a calculation is "good enough" for a specific workflow
- what "best" means for a specific workflow context — there is no such
  selector in v0

Those decisions belong outside `tckdb-client`.

## Required setup

```python
from tckdb_client import TCKDBClient

client = TCKDBClient(
    base_url="http://127.0.0.1:8000/api/v1",   # include /api/v1
    api_key=None,                               # optional for public scientific reads
)
```

- `base_url` should already include the `/api/v1` prefix; the client
  joins paths cleanly and never duplicates it.
- `api_key` is optional for public scientific reads. Use it only when
  the deployment requires authentication or when you need authenticated
  quotas/features. When set, the client forwards it as the `X-API-Key`
  header on every request — including reads — so authenticated
  deployments still see a billable identity.
- Use the client as a context manager when convenient
  (`with TCKDBClient(...) as client:`) to release the underlying HTTP
  connection at scope exit.

> Public reads being anonymous-friendly in the client is not an
> abuse-control mechanism. Hosted deployments should enforce abuse
> limits server-side through rate limits, pagination caps, query
> timeouts, and monitoring.

## Read/query workflow overview

For most hosted workflow-tool use, **prefer the chemistry-first search
methods** — they take chemical identifiers and return fully-shaped
thermo/kinetics records with the resolved entry-id identity already
attached:

```python
client.search_thermo(smiles="...", temperature_min=..., temperature_max=...)
client.search_kinetics(reactants=[...], products=[...], direction="...")
```

Entry-id detail methods stay useful for follow-up reads, provenance
inspection, curation, and stable references. The path parameter accepts
either the integer PK or a public ref of the matching prefix, so the
ref-first flow is the natural one:

```python
# Follow-up reads keyed off the *_ref returned by search_*:
client.get_species_thermo(species_entry_id="spe_...")   # ref accepted
client.get_reaction_kinetics(reaction_entry_id="rxe_...")
client.get_reaction_full(reaction_entry_id="rxe_...")

# Integer ids still work for code that already holds them:
client.get_species_thermo(species_entry_id=31)
```

A normal scientific read has four stages:

1. **Chemistry-first search.** Call `search_thermo` or `search_kinetics`
   with chemical identifiers; get back records that already include the
   resolved `species_entry_ref` / `reaction_entry_ref` (and the matching
   integer ids).
2. **Trust inspection.** Inspect `review` on each returned record;
   inspect `provenance` to see what calculations support it.
3. **Optional follow-up.** If you need the full provenance graph for
   inspection or curation, use `get_reaction_full(reaction_entry_id=<ref>)`
   with the public ref you got back from the search.
4. **Workflow decision.** The adapter (not `tckdb-client`) decides
   whether to reuse the TCKDB record or proceed with its own calculation.

`tckdb-client` returns parsed JSON dictionaries that match the response
models in `backend/app/schemas/reads/scientific_*.py`. The keys named
below come from those models.

## Chemistry-first thermo search (recommended for hosted workflow tools)

When a workflow tool wants thermo for a species and only knows the
chemical identifier, use `search_thermo` directly:

```python
thermo = client.search_thermo(
    smiles="C[CH2]",
    temperature_min=300,
    temperature_max=3000,
    collapse="first",
    include=["provenance", "review"],
)
```

The response carries both the resolved species/species-entry identity
**and** the thermo record itself in one envelope:

```python
{
    "request": {...},
    "review_summary": {...},
    "records": [
        {
            "species": {
                "species_id": 12,
                "canonical_smiles": "C[CH2]",
                "inchi_key": "...",
                "charge": 0,
                "multiplicity": 2,
                "species_entry_id": 31,
                "species_entry_kind": "minimum",
                "electronic_state_kind": "ground",
                "species_entry_review": {"status": "not_reviewed"},
            },
            "thermo": {
                "thermo_id": 88,
                "scientific_origin": "computed",
                "model_kind": "nasa",
                "review": {"status": "approved"},
                "h298_kj_mol": -12.3,
                "s298_j_mol_k": 250.1,
                "nasa": {...},
                "temperature_coverage": {...},
                "evidence_completeness": {"score": 5, "max": 8, "checklist": {...}},
                "provenance": {...},
            },
        },
    ],
    "pagination": {...},
}
```

The `species` block contains the same identity context you would get
from `search_species`; the `thermo` block is the same `ThermoRecord`
you would get from `get_species_thermo`. So a workflow tool can rely on
a single thermo schema regardless of which discovery path it took.

`search_thermo` defaults to **POST** because species filters often
include identifiers with characters that don't survive query strings
cleanly (e.g. `inchi`). Pass `method="GET"` to force a query-string
form.

## Chemistry-first kinetics search (recommended for hosted workflow tools)

Same pattern for kinetics — supply reactants and products, get back
kinetics records with the resolved reaction/reaction-entry identity:

```python
kinetics = client.search_kinetics(
    reactants=["[CH3]", "c1ccccc1"],
    products=["CH4", "[c]1ccccc1"],
    direction="either",
    temperature_min=300,
    temperature_max=2000,
    collapse="first",
    include=["provenance", "review"],
)
```

Each record carries a `reaction` block (`reaction_id`,
`reaction_entry_id`, `equation`, `reversible`, `family`,
`matched_direction`, `reactants`, `products`, `reaction_entry_review`)
and a `kinetics` block (the same `KineticsRecord` shape as
`get_reaction_kinetics`).

`search_kinetics` defaults to **POST**; pass `method="GET"` for the
repeated-query-param form (`?reactants=A&reactants=B&products=C`).

`direction="exact"` is **not** supported in v0 — the backend rejects it
with 422.

**Returned entry ids are handles, not prerequisites.** A workflow
adapter should inspect `review` and `provenance` on each record before
deciding to reuse it. Importantly:

- `species_entry_id` alone does not mean a thermo record is reusable —
  the workflow's reuse policy decides that.
- Conformer / statmech / source-calculation evidence should be
  interpreted through `provenance` and `evidence_completeness`, not
  inferred from the entry id.
- Non-TS-backed kinetics surface here too, with null TS-chain
  provenance fields (see *Handling non-TS-backed kinetics* below).

## Species lookup (discovery-only)

> Use `search_thermo` directly when you actually want thermo. Reach for
> `search_species` only when you specifically want the identity layer
> without the thermo records (e.g. a UI that lists candidate species
> first, or a workflow that wants conformer or transport info instead).

Discover candidate species by chemical identity. Multiple identifier
arguments combine with AND semantics; mutually inconsistent identifiers
return an empty result set rather than a validation error.

```python
species = client.search_species(
    smiles="C[CH2]",
    include=["review"],
    collapse="all",
)
```

The response is a search envelope:

```python
{
    "request": {"filter": {"smiles": "C[CH2]"}, "sort": "...", "collapse": "all", "include": ["review"]},
    "review_summary": {"approved": 0, "under_review": 0, "not_reviewed": 1, "deprecated": 0, "rejected": 0, "total": 1},
    "records": [
        {
            "species_id": 12,
            "canonical_smiles": "C[CH2]",
            "inchi_key": "...",
            "charge": 0,
            "multiplicity": 2,
            "entries": [
                {
                    "species_entry_id": 31,
                    "species_entry_kind": "minimum",
                    "electronic_state_kind": "ground",
                    "review": {"status": "not_reviewed"},
                    "availability": {
                        "has_thermo": True,
                        "has_statmech": False,
                        "has_transport": False,
                        "has_conformers": False,
                        "calculation_count": 4,
                    },
                },
            ],
        },
    ],
    "pagination": {"offset": 0, "limit": 50, "returned": 1, "total": 1},
}
```

This call returns **candidate species/species-entry records**, not full
thermo or kinetics. Each entry carries both `species_entry_id` and
`species_entry_ref`; pass either one (the ref is preferred) into
subsequent thermo or conformer reads.

## Species thermo retrieval (entry-id detail / follow-up)

> For most hosted workflow-tool use, prefer `search_thermo` (above).
> Use this entry handle form for follow-up reads, curation tooling, or
> when you already have a stable `species_entry_ref` (or
> `species_entry_id`) from a prior call.

Once a `species_entry` handle is known, retrieve thermo with explicit
temperature bounds when relevant. The path parameter accepts either
the public ref (preferred) or the integer id:

```python
# Ref-first follow-up (preferred):
thermo = client.get_species_thermo(
    species_entry_id="spe_...",   # public ref accepted as the handle
    temperature_min=300,
    temperature_max=3000,
    collapse="first",
    include=["provenance", "review"],
)

# Integer-id form (compatibility):
thermo = client.get_species_thermo(
    species_entry_id=31,
    temperature_min=300,
    temperature_max=3000,
    collapse="first",
)
```

Each record carries:

- `model_kind` — one of `nasa`, `points`, `scalar`
- the relevant model block (`nasa` polynomial coefficients, or a
  `points` array, or only the scalar `h298_kj_mol` / `s298_j_mol_k`)
- `temperature_coverage` — full-range coverage flag plus extrapolation
  distance
- `evidence_completeness` — score and per-predicate checklist
- `provenance` — primary calculation, level of theory, software, source
  calculation references
- `review` — direct review badge for the thermo record

`collapse="first"` returns at most one record using TCKDB's documented
deterministic backend ordering. The client does not define "best
thermo" — it only returns what the backend ranked first.

## Reaction lookup (discovery-only)

> Use `search_kinetics` directly when you actually want kinetics. Reach
> for `search_reactions` only when you specifically want the reaction
> identity layer without the kinetics records (e.g. listing matched
> reactions in a UI, or chaining into the full-provenance endpoint).

Discover reaction entries by reactants and products. The client defaults
to **POST** because SMILES strings are easier and safer in JSON bodies
than in URL query strings.

```python
rxns = client.search_reactions(
    reactants=["[CH3]", "c1ccccc1"],
    products=["CH4", "[c]1ccccc1"],
    direction="either",
    include=["review"],
    collapse="all",
)
```

Each record carries:

- `reaction_id` / `reaction_ref`, `reaction_entry_id` / `reaction_entry_ref`
- `equation`, `reversible`, `family`
- `matched_direction` — which orientation matched the query
- `reactants`, `products` — participant lists with `species_entry_id`,
  `species_entry_ref`, `smiles`, `participant_index`
- `availability` — boolean flags + counts (kinetics, transition state,
  path search) for the entry
- `review` — direct review badge for the reaction entry

`direction` accepts `forward`, `reverse`, or `either`. `direction=exact`
is **not** supported in v0 and the backend rejects it with 422.

If you prefer the GET form (e.g. for a quick interactive lookup with
plain identifiers), pass `method="GET"`:

```python
client.search_reactions(reactants=["A"], products=["B"], method="GET")
```

## Reaction kinetics retrieval (entry-id detail / follow-up)

> For most hosted workflow-tool use, prefer `search_kinetics` (above).
> Use this entry handle form for follow-up reads, curation, or when you
> already have a stable `reaction_entry_ref` (or `reaction_entry_id`).

Use a `reaction_entry_ref` from `search_kinetics` or `search_reactions`
(or the integer id, for compatibility) to fetch kinetics:

```python
# Ref-first follow-up (preferred):
kinetics = client.get_reaction_kinetics(
    reaction_entry_id="rxe_...",   # public ref accepted as the handle
    temperature_min=300,
    temperature_max=2000,
    collapse="first",
    include=["provenance", "review"],
)

# Integer-id form (compatibility):
kinetics = client.get_reaction_kinetics(
    reaction_entry_id=51,
    temperature_min=300,
    temperature_max=2000,
    collapse="first",
)
```

Each record carries:

- `kinetics_id`, `scientific_origin`, `model_kind`
- `parameters` — Arrhenius `A`, `A_units`, `n`, `Ea_kj_mol`
- `tunneling_model`, `uncertainty`
- `temperature_coverage` — full-range coverage + extrapolation distance
  against the requested range
- `evidence_completeness` — score + per-predicate checklist
- `review` — direct review badge for the kinetics record
- `provenance` — TS-chain references plus literature/software/workflow
  metadata; **TS-chain fields are nullable** (see *Handling non-TS-backed
  kinetics* below)

Temperature coverage, review rank, evidence completeness, and
tie-breaking are all computed by TCKDB. The client only serializes the
request and returns the response — it never re-ranks records on the
client side.

## Full provenance inspection

The composite endpoint returns a single document joining species,
kinetics, transition states, calculations, and review summary. It is
intended for **inspection, debugging, and curation**, not for quick
search:

```python
# Ref-first follow-up (preferred): chain from search_kinetics' returned ref.
full = client.get_reaction_full(
    reaction_entry_id="rxe_...",   # public ref accepted as the handle
    include=[
        "species",
        "kinetics",
        "transition_states",
        "calculations",
        "path_search",
        "irc",
        "review",
    ],
    include_review="full",
)
```

`include_review="full"` adds an audit-style `review_records` array. This
is the only endpoint that supports `include_review="full"`; the others
return 422 if asked.

Sub-arrays you did not ask for are **omitted** from the response.
Sub-arrays you did ask for are **always present**, possibly empty. So a
caller can distinguish "I asked and got nothing" (`"foo": []` or
`"foo": null`) from "I didn't ask" (key absent).

## Geometry detail reads

`species-calculations/search` returns `geometry_ref` handles, not
inline coordinates. Use the detail endpoint to retrieve the full
coordinate payload when needed:

```python
# Refs returned by search:
geom_block = calcs["records"][0]["geometry"]
geometry_handle = (
    geom_block["primary_output_geometry_ref"]
    or geom_block["input_geometries"][0]["geometry_ref"]
)

# Detail follow-up — also accepts an integer id for compatibility:
geometry = client.get_geometry(geometry_handle)
# {"geometry_ref": "geom_…", "natoms": 3, "format": "cartesian",
#  "coordinate_units": "angstrom",
#  "symbols": ["O", "H", "H"], "coords": [...], "provenance": {...}}
```

Expectations by calculation type:

- **SP**: `input_geometries` populated; `primary_output_geometry_ref`
  is `null` by design (the upload layer only auto-attaches an output
  geometry for `opt`).
- **OPT**: `primary_output_geometry_ref` is set when the optimized
  geometry was persisted; the role is typically `final`.
- **freq / scan / irc / path_search**: producer-declared only —
  output geometry only present when explicitly uploaded.

The detail endpoint's response also includes a small `provenance`
summary listing every calculation that produced (with `role`) or
consumed the geometry — useful for tracing why a geometry appears in
the database.

> **Picking only the sections you want.** The example script bundled
> with `tckdb-client` (`examples/scientific_reads.py`) is a multi-call
> demo. Narrow it with `--only species`, `--only calculations,geometry`,
> `--no-followups`, etc. Override the hard-coded
> `calculation_type=sp` / `ranking=lowest_energy` with
> `--calculation-type opt --ranking latest` (or whatever the backend
> accepts). The backend API itself is unchanged — this is only the
> example's filtering surface.

## Trust and review state

Every scientific record carries a `review` badge with one of:

```
approved | under_review | not_reviewed | deprecated | rejected
```

Default behavior:

- `approved`, `under_review`, `not_reviewed` are returned by default.
- `deprecated` and `rejected` are excluded by default. Override with
  `include_deprecated=True` or `include_rejected=True`.

`min_review_status` filters the **primary record** returned by the
endpoint. It does **not** require every supporting provenance record
(source calculations, transition states, validation rows, etc.) to be
approved. This is "shallow" semantics.

```python
approved_kinetics = client.get_reaction_kinetics(
    reaction_entry_id=51,
    min_review_status="approved",
    collapse="all",
)
```

For deeper inspection, request `include=["provenance"]` (or use the
full-provenance endpoint) and apply workflow-specific judgment in the
adapter. A future server-side `provenance_min_review_status` filter is
deferred — v0 keeps trust filtering shallow and honest.

## Filter / sort / collapse behavior

There are three orthogonal axes:

```
filter:    controls eligibility (review, temperature range,
           level_of_theory_id, software, model_kind, ...)

sort:      backend default deterministic ordering only (v0)

collapse:  whether to return all records ("all") or only the first
           backend-ranked record ("first")
```

Important rules:

- **Client-supplied `sort=` is not supported in v0.** The methods
  intentionally have no `sort` argument; the backend rejects any
  client-supplied sort value with 422 (`client_sort_not_supported`).
- **`collapse="first"` is not a "best" selector.** It returns the first
  record under TCKDB's documented deterministic backend ordering. The
  ordering rules are spelled out in the spec (e.g. for kinetics, the
  D9 chain orders by temperature coverage first, then extrapolation
  distance, then review rank, then evidence completeness, then
  `created_at`, then `id`).
- `pagination.total` is the **pre-collapse, post-filter** match count.
  When `collapse="first"`, `total` may be larger than `returned`
  (returned is then 0 or 1).
- `review_summary` counts the **pre-collapse filtered candidate set**,
  so callers see the trust posture of every candidate, not only the one
  record returned.

## Handling non-TS-backed kinetics

Not every kinetics record is backed by a transition-state calculation.
Records with `scientific_origin` of `experimental`, `estimated`,
`imported`, fitted, network-derived, or literature-derived may have
**null** TS-chain provenance fields. This is valid — those records are
not "broken" or untrustworthy, they just don't have a TS chain.

The provenance keys are always present in the JSON (Phase 2.2 contract):

```python
record = kinetics["records"][0]
provenance = record.get("provenance", {})

if provenance.get("transition_state_entry_id") is None:
    # Non-TS-backed: experimental, estimated, imported, fitted, etc.
    # Look at literature, scientific_origin, software_release instead.
    print("Kinetics record is not TS-backed.")
    print("Origin:", record.get("scientific_origin"))
    print("Literature:", provenance.get("literature"))
```

Workflow tools must not assume `transition_state_entry_id` is always
present. They must not synthesize TS links from non-TS-backed records.
And they should interpret the `evidence_completeness` checklist together
with `scientific_origin`: a low score on a non-TS-backed record means
the *computational* checklist does not apply, not that the record is
invalid.

## Handling empty results

An empty result set is **not** a 404. The endpoint returns a 200
response with `records: []` and a populated `pagination.total: 0`:

```python
rxns = client.search_reactions(
    reactants=["DOES_NOT_EXIST"],
    products=["NEITHER_DOES_THIS"],
)
assert rxns["records"] == []
assert rxns["pagination"]["total"] == 0
```

Detail-read endpoints (`/scientific/reaction-entries/{id}/kinetics`,
`/scientific/species-entries/{id}/thermo`,
`/scientific/reaction-entries/{id}/full`) return 404 only when the path
parameter is unknown. If the entry exists but has no matching kinetics
or thermo, you get a 200 with empty `records`.

## Error handling

The client surfaces backend errors as structured exceptions:

| Backend status | Client exception | Common cause |
|---|---|---|
| 401 | `TCKDBAuthenticationError` | API key missing/invalid (auth-required deployment) |
| 403 | `TCKDBForbiddenError` | API key valid but lacks permission |
| 404 | `TCKDBHTTPError` (status_code=404) | unknown `species_entry_id` / `reaction_entry_id`; passing a `species.id` or `chem_reaction.id` instead |
| 422 | `TCKDBValidationError` | invalid filter, unknown `include` token, unsupported `direction`, invalid temperature range, client-supplied `sort=` |
| other 4xx/5xx | `TCKDBHTTPError` | server error or unexpected status |
| network / timeout | `TCKDBConnectionError` | transport-level failure |

Each exception carries `status_code`, `code` (when the response provides
one), `detail`, the parsed `response_json`, and `headers`. Surface these
to the caller — do not swallow scientific validation errors, since the
backend's `code` and `detail` are how a workflow tool learns *which*
constraint failed.

```python
from tckdb_client import TCKDBHTTPError, TCKDBValidationError

try:
    kinetics = client.get_reaction_kinetics(reaction_entry_id=999_999)
except TCKDBHTTPError as exc:
    if exc.status_code == 404:
        # No reaction_entry with this id. Adapter decides what to do.
        ...
    else:
        raise

try:
    rxns = client.search_reactions(
        reactants=["A"], products=["B"], direction="exact",  # not supported in v0
    )
except TCKDBValidationError as exc:
    # exc.detail is a structured backend message; log and surface to user.
    ...
```

## Recommended integration boundary

A typical workflow integration has three layers:

1. A **`tckdb-client` instance** configured with the deployment's
   `base_url` and (optionally) an API key. Stateless and reusable.
2. A small **adapter module inside the workflow tool** that knows how
   to translate the workflow's chemical concepts (species objects,
   reaction templates, requested temperature ranges) into client method
   calls, and how to map TCKDB responses back into workflow concepts.
3. The **workflow's policy code**, which decides whether to reuse a
   TCKDB record vs run a fresh calculation. This is where statements
   like *"reuse if approved"*, *"reuse if temperature coverage is
   exact"*, or *"never reuse experimental kinetics for high-pressure
   regime"* live — and where they belong, because the right answer
   depends on the workflow's context, not on TCKDB's contract.

The `tckdb-client` itself stays free of all of this. The adapter does
the mapping. The policy lives in the workflow.

## Minimal end-to-end example

The chemistry-first form — one call, no manual id chaining:

```python
from tckdb_client import TCKDBClient

client = TCKDBClient(base_url="http://127.0.0.1:8000/api/v1")

kinetics = client.search_kinetics(
    reactants=["[CH3]", "c1ccccc1"],
    products=["CH4", "[c]1ccccc1"],
    direction="either",
    temperature_min=300,
    temperature_max=2000,
    collapse="first",
    include=["provenance", "review"],
)

records = kinetics.get("records", [])
if not records:
    print("No matching kinetics found in TCKDB.")
else:
    rec = records[0]
    print("reaction_entry_ref:", rec["reaction"]["reaction_entry_ref"])
    print("reaction_entry_id :", rec["reaction"]["reaction_entry_id"])
    print("matched_direction :", rec["reaction"]["matched_direction"])
    print("kinetics_ref      :", rec["kinetics"]["kinetics_ref"])
    print("kinetics_id       :", rec["kinetics"]["kinetics_id"])
    print("scientific_origin :", rec["kinetics"]["scientific_origin"])
    print("review status     :", rec["kinetics"]["review"]["status"])
    print("temperature cov.  :", rec["kinetics"]["temperature_coverage"])
    print("evidence score    :", rec["kinetics"]["evidence_completeness"]["score"])
```

The two-call form (use only when you specifically need a separate
identity-only step — e.g. listing matched reactions in a UI before
fetching kinetics):

```python
rxns = client.search_reactions(
    reactants=["[CH3]", "c1ccccc1"],
    products=["CH4", "[c]1ccccc1"],
    direction="either",
    collapse="first",
)

records = rxns.get("records", [])
if records:
    # Prefer the public ref; fall back to the integer id if needed.
    reaction_entry_handle = (
        records[0].get("reaction_entry_ref")
        or records[0]["reaction_entry_id"]
    )
    kinetics = client.get_reaction_kinetics(
        reaction_entry_id=reaction_entry_handle,
        temperature_min=300,
        temperature_max=2000,
        collapse="first",
        include=["provenance", "review"],
    )
```

The example deliberately stops short of a reuse decision. That decision
belongs in the workflow tool — not here.

## Adapter-specific policy belongs outside `tckdb-client`

When a workflow says things like:

```
- reuse only if review_status is approved
- reuse only if temperature coverage is exact (not extrapolated)
- reuse only if evidence_completeness >= 7
- never reuse experimental kinetics for ab-initio refinement
- always re-run TS opt at our preferred level of theory
```

…those are workflow policies, not TCKDB facts. They belong in the
workflow tool's adapter or job-launch policy module, where they can be
versioned and changed without touching TCKDB or `tckdb-client`.

The advantage of keeping policy outside the client is that two
different workflows can consume the same TCKDB instance with completely
different reuse rules — neither has to fork the client, and neither
constrains the other.

## Coming next: chemistry-first species calculation/conformer search

> **Status: Phase 7 design / pending implementation.** See
> [docs/specs/species_calculation_search_api.md](../specs/species_calculation_search_api.md).

A future endpoint will let workflow tools ask calculation-and-conformer
questions chemistry-first — i.e. *"give me the lowest-SP-energy
calculation for this species at this LoT, and tell me which conformer
it belongs to"* — without manually chaining
`search_species` → `get_species_calculations` themselves:

```python
# Pending Phase 7 implementation; method name and signature are draft.
calcs = client.search_species_calculations(
    smiles="CCO",
    calculation_type="sp",
    level_of_theory_id=8,
    ranking="lowest_energy",
    collapse="first",
    include=["provenance", "conformers", "review"],
)
```

This is **calculation-centered**, not thermo-centered: the response
records expose the calculation row, its energy, geometry IDs, conformer
context (when present), validation/SCF status, level of theory, and
software release — so workflow adapters can decide whether the
calculation is reusable for a given workflow, with no hidden policy in
the client. Until the endpoint ships, fall back to chaining
`search_species` → existing entry-id calculation reads (or wait for the
implementation phase).

## Known v0 limitations

- No frontend / web UI is required for workflow-tool reads — the API is
  the contract.
- No client-side "best" selection. The methods deliberately omit
  subjective selectors (`best`, `preferred`, `tckdb_default`,
  `highest_lot`).
- No deep provenance review filtering. `min_review_status` is shallow
  in v0 — it filters the primary record only, not the whole supporting
  chain.
- No cursor pagination. Use `offset` + `limit` (default 50, max 200).
- No ARC/RMG adapter logic in `tckdb-client`. Adapters live in the
  consuming workflow's repository.
- No automatic job-reuse decision. The client returns data; the
  workflow decides.
- Some rich nested sections may currently be summaries (e.g. the
  per-section `*_summary` fields under species-search entries return
  `{ids: [...]}` ID lists rather than fully embedded records). Future
  phases may enrich these without breaking the outer envelope.

## Checklist for workflow-tool integrations

1. Configure `base_url` to include `/api/v1`.
2. Configure an `api_key` if the deployment requires authentication.
3. **For thermo or kinetics, prefer the chemistry-first methods**
   (`search_thermo`, `search_kinetics`) — they take chemical identifiers
   and return entry-id handles in the response.
4. Use the discovery-only methods (`search_species`, `search_reactions`)
   only when you specifically want the identity layer alone.
5. When using the entry-id detail methods, those ids are strictly
   `species_entry.id` / `reaction_entry.id` — never `species.id` or
   `chem_reaction.id`.
6. Request thermo / kinetics with explicit `temperature_min` /
   `temperature_max` when the workflow has a target range.
7. Use `collapse="first"` only when the documented backend default
   ordering is acceptable. Otherwise use `collapse="all"` and let the
   adapter pick.
8. Inspect `review` (per-record badge) and `provenance` (per-record
   summary) before reusing a record.
9. Treat non-TS-backed kinetics as **valid but different** — do not
   discard them just because TS-related checklist keys are `false`.
10. Keep workflow-specific reuse policy **outside** `tckdb-client`,
    ideally in a small adapter module in the workflow's own repository.
11. Log the TCKDB record ids (`species_entry_id`, `reaction_entry_id`,
    `kinetics_id`, `thermo_id`) that the workflow chose to reuse — those
    ids are the audit trail back to TCKDB provenance.
