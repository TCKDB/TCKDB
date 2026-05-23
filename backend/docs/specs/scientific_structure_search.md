# Scientific Structure Search (RDKit Cartridge)

**Original spec date:** 2026-05-22
**Status:** Implemented (v0)
**Scope:** Public chemistry-structure search over species entries,
backed by the PostgreSQL RDKit cartridge.

This is the first read endpoint to lean on the RDKit cartridge for
matching, rather than equality / range filters over scalar columns.
It is deliberately discovery-oriented: callers find species entries by
structural query and chain to the existing per-entry detail endpoints
for the heavy scientific payloads.

---

## Purpose

Let API consumers ask three structural questions about species in
TCKDB:

1. **Substructure** — "Which species entries contain this fragment?"
2. **Similarity** — "Which species entries are chemically similar to
   this query?"
3. **Exact** — "Which species entries are this exact molecule?"

The endpoint does not duplicate the identity-search surface on
`/scientific/species/search` (which AND-combines equality filters over
SMILES / InChIKey / formula / charge / multiplicity). Use this endpoint
when the query is a structural pattern, not a literal identity tuple.

---

## Endpoint list

| Method | Path | Notes |
|---|---|---|
| GET  | `/api/v1/scientific/species/structure-search` | Query-string form |
| POST | `/api/v1/scientific/species/structure-search` | JSON-body form |

The endpoint lives under the `/species` prefix so it sits beside
`/species/search` as a discovery surface over the same grain. Both
forms share the same service implementation (`search_species_by_structure`)
and return the same response envelope.

---

## Search grain

Records are returned at **`species_entry`** grain.

Rationale:

- `species_entry` is the curation/provenance-bearing record surfaced
  throughout the rest of the scientific read API. Returning entries (not
  bare `species`) lets callers see review state and chain into the
  existing entry-grain detail endpoints.
- Matching is computed on `species.smiles` (the parent species' identity
  SMILES) since that is the column populated for every row in the
  database today. The result still surfaces at `species_entry` grain so
  each entry's per-record review badge is included.

For substructure / similarity queries, every entry under a matching
parent species is returned. Callers who want one-record-per-species
should consume `/species/search` instead.

---

## Query input

The request accepts exactly one of these query fields:

| Field | Substructure | Similarity | Exact |
|---|---|---|---|
| `query_smiles`     | yes | yes | yes |
| `query_smarts`     | yes | no  | no  |
| `query_inchi`      | no  | yes | yes |
| `query_inchi_key`  | no  | no  | yes |

If zero query fields are supplied, the response is `422
missing_structure_query`. If more than one is supplied, the response is
`422 multiple_structure_queries`. A mode / query-field mismatch (e.g.
`mode=similarity` with `query_smarts`) is `422 invalid_structure_query`.

RDKit parses every structure query before it reaches the cartridge:

- `query_smiles` and `query_inchi` are canonicalized to canonical
  SMILES via `Chem.MolToSmiles(Chem.MolFromSmiles(...))` /
  `Chem.MolFromInchi(...)`.
- `query_smarts` is parsed with `Chem.MolFromSmarts` to surface a 422
  before the SQL runs; the original text is passed to
  `qmol_from_smarts()` server-side.
- `query_inchi_key` is taken verbatim for the exact-match lookup. For
  exact mode supplied as SMILES or InChI, the InChIKey is computed
  client-side via `rdkit.Chem.inchi.MolToInchiKey`.

A parse failure at any step is `422 invalid_structure_query`.

### Mode-specific knobs

- `mode` defaults to `substructure`.
- `similarity_threshold` is accepted only in similarity mode; the
  default (and echoed value when omitted) is **0.5**. Range
  `[0.0, 1.0]` enforced by the request schema.
- Sort vocabulary is v0-frozen; supplying `sort=` yields `422
  client_sort_not_supported`. The per-mode default deterministic sort
  always applies.

---

## Substructure semantics

For substructure mode the SQL is shaped as:

```sql
SELECT se.id, sp.id
FROM species_entry se
JOIN species sp ON sp.id = se.species_id
WHERE mol_from_smiles(sp.smiles) IS NOT NULL
  AND mol_from_smiles(sp.smiles) @> <query_mol>
```

`<query_mol>` is `qmol_from_smarts(:q)` for a SMARTS query or
`mol_from_smiles(:q)` for a SMILES query (after canonicalization on the
Python side). All matching is computed database-side — there is no
Python-side iteration over species.

The `IS NOT NULL` guard ensures species rows whose SMILES happens to be
unparseable by the cartridge are skipped silently rather than raising.

---

## Similarity semantics

Similarity uses the cartridge's Tanimoto coefficient over Morgan-bit
fingerprints:

```sql
tanimoto_sml(
  morganbv_fp(mol_from_smiles(sp.smiles)),
  morganbv_fp(mol_from_smiles(:q))
) AS similarity_score
```

The threshold filter is applied database-side as `>= :threshold`. The
returned `similarity_score` is a `float`, surfaced on every similarity
record so callers can rank or filter further client-side.

Default sort:

1. `similarity_score DESC`
2. `review_rank ASC`
3. `species_entry_id DESC`

---

## Exact-match semantics

Exact mode normalizes the query to a canonical InChIKey on the Python
side (via RDKit) and looks up the indexed `species.inchi_key` column
directly:

```sql
WHERE sp.inchi_key = :computed_inchi_key
```

Why InChIKey:

- `species.inchi_key` is uniquely constrained and indexed, so this is
  the fastest exact-match available without a cartridge call.
- It is the single canonical identity that survives stereochemistry and
  SMILES-aliasing ambiguity, so "exact" has an unambiguous meaning even
  when callers supply a SMILES variant.

SMARTS is rejected for exact mode (`422 invalid_structure_query`); a
SMARTS pattern is by definition not a literal identity.

---

## Response shape

```json
{
  "request": { "filter": {...}, "mode": "...", "sort": "...", "include": [...] },
  "review_summary": { "approved": 0, ... },
  "records": [
    {
      "species_ref": "spec_...",
      "species_id": 17,
      "species_entry_ref": "se_...",
      "species_entry_id": 42,
      "smiles": "CCO",
      "inchi_key": "LFQSCWFLJHTTHZ-UHFFFAOYSA-N",
      "charge": 0,
      "multiplicity": 1,
      "species_entry_kind": "minimum",
      "electronic_state_kind": "ground",
      "match": {
        "mode": "similarity",
        "similarity_score": 0.83,
        "matched_query": "CCO",
        "matched_query_kind": "smiles"
      },
      "review": { "status": "not_reviewed", ... },
      "endpoint": "/api/v1/scientific/species-entries/se_..."
    }
  ],
  "pagination": { "offset": 0, "limit": 50, "returned": 1, "total": 1 }
}
```

`endpoint` is a convenience deep-link to the matching species entry's
canonical detail URL — useful for UI consumers building structure-search
result lists.

---

## include behavior

Legal `include=` tokens (v0):

- `review`
- `internal_ids`
- `all`

`include=all` expands to `review` only — it never auto-includes
`internal_ids`. The internal-ID opt-in must be supplied explicitly
(`include=all,internal_ids`) **and** the deployment must allow it
(`settings.allow_public_internal_ids`).

The structure-search endpoint is deliberately discovery-shaped: heavy
scientific projections (thermo / kinetics / statmech / transport /
conformers) are out of scope here. Callers chain via
`species_entry_ref` to the existing per-entry detail endpoints when
they want those payloads. This keeps response sizes bounded for the
search use case, where dozens of hits per query are typical.

---

## Review / trust behavior

The default-trust posture matches the rest of the scientific read API:

- `rejected` and `deprecated` entries are hidden by default.
- `include_rejected=true` / `include_deprecated=true` restore them.
- Each record carries a compact `review` badge.
- `review_summary` counts the candidate set **before** pagination.

Restored rejected / deprecated entries sort after their better-reviewed
peers because `review_rank` is the first (substructure / exact) or
second (similarity) tie-breaker in the default sort.

The opt-in flags are **not** auth-gated — anonymous callers can flip
them. This is an intentional transparency policy for scientific
reproducibility, not an authorization boundary; the operational
implication ("do not store private data in rejected/deprecated
records") is documented in
[`docs/deployment/production_checklist.md`](../../../docs/deployment/production_checklist.md#public-read-policy-assumptions).

---

## Internal-ID behavior

Internal integer IDs (`species_id`, `species_entry_id`) are stripped
from the response by default. The Phase D internal-ID policy applies
unchanged:

- Refs (`species_ref`, `species_entry_ref`) are always visible.
- IDs return only when `include=internal_ids` is set **and** the
  deployment's `allow_public_internal_ids` setting is on.

---

## Performance / index requirements

Substructure and similarity queries read from the materialized
`species_entry.mol` cartridge column. The write path
(`app/services/species_resolution.py`) canonicalizes the parent
species's SMILES into `mol` at insert time, and Alembic migration
`d4e5f6a7b8c9_add_species_entry_mol_gist_index` adds a GiST index on
the column and backfills any pre-existing NULL rows:

```sql
CREATE INDEX ix_species_entry_mol_gist
ON species_entry USING gist (mol);
```

The service issues:

```text
… WHERE se.mol IS NOT NULL AND se.mol @> mol_from_smiles(:query_text)
```

so the GiST index drives the scan. Rows whose `mol` is NULL (for
example, SMILES the cartridge could not parse) are excluded from
match results — adding a per-row `mol_from_smiles(sp.smiles)`
fallback would force a seq-scan and silently defeat the index. A
guard test (`test_structure_search_uses_stored_mol_column_not_inline_conversion`)
fails if the inline-conversion pattern reappears in the cartridge
SQL builders.

Exact mode keeps the indexed `species.inchi_key` lookup path — it
does not touch the cartridge.

### SQL-side pagination bound

Review-status visibility filtering, deterministic ordering, and
`LIMIT/OFFSET` pagination are all pushed into SQL. A broad query
(e.g. a wildcard SMARTS that matches most of the catalog) returns at
most `limit` rows to Python; the candidate set is never materialized
in application memory.

Each per-mode builder issues two queries against the same
`species_entry JOIN species LEFT JOIN record_review` shape:

- An aggregate `GROUP BY review_status` query whose wire result is
  bounded by the number of `RecordReviewStatus` values. Used to derive
  the exact post-filter `pagination.total` and the `review_summary`.
- An ordered, `LIMIT`/`OFFSET`-bounded row query that returns only the
  visible page. Sort keys live in SQL (`similarity_score DESC`,
  `review_rank ASC` via a `CASE`, `species_entry_id DESC`).

This is enforced by a source-inspection guard test
(`test_structure_search_pushes_limit_into_sql`) and a behavioral
test that walks a multi-page query against more rows than fit in one
page.

**Deferred follow-ups:**

- Pre-computed Morgan fingerprint column for similarity, plus its
  own GiST index. The query currently materializes
  `morganbv_fp(se.mol)` per row; for a busy workload, storing the
  fingerprint and indexing it would be the next refinement.
- Bulk reindex / `VACUUM ANALYZE` runbook for the
  `ix_species_entry_mol_gist` index once query volume warrants
  re-profiling.

---

## Payload safety

The response intentionally exposes only identity-level fields. Forbidden
keys that would betray a heavier payload — `mol`, `molblock`,
`rdkit_binary`, `geometry`, `coordinates`, `coords`, `xyz_text`,
`atoms`, `body`, `content`, `data` — are absent at every level of the
response tree, verified by a recursive payload-safety test
(`test_payload_does_not_leak_forbidden_keys`).

Artifact bodies, geometry coordinates, and per-conformer details are
all unavailable through this endpoint — chain to the appropriate
detail / artifact / geometry endpoint if those are required.

---

## Non-goals (v0)

- **Reaction substructure search.** Reactions are matched by
  participant species, which would require a separate aggregation pass.
- **Retrosynthesis / reaction-template search.**
- **Drawn-molecule / image-based search.**
- **3D / geometry similarity** (RMSD, shape descriptors).
- **Conformer shape similarity.**
- **Bulk export.**
- **Artifact body download.**
- **Schema redesign beyond the shipped GIST-index migration.**
- **ARC changes.**
- **`tckdb-client` changes.**

---

## Implementation status

Implemented and shipped on `main` as of 2026-05-22.

Files:

- `backend/app/api/routes/scientific/structure.py` — GET / POST routes
- `backend/app/services/scientific_read/structure_search.py` — service
- `backend/app/schemas/reads/scientific_structure_search.py` — schemas
- `backend/tests/api/scientific/test_api_scientific_structure_search.py`
  — API tests (31 cases)

The router is registered before the species identity-search router in
`backend/app/api/routes/scientific/__init__.py` so that future generic
catch-all routes on the `/species/{handle}` segment cannot shadow
`/species/structure-search`.

---

## Test plan

Covered by the API test module:

- Input validation: missing query, multiple queries, invalid SMILES,
  invalid SMARTS, mode-specific rejection of incompatible query fields,
  client sort rejection, unknown include token.
- Substructure: SMARTS match, SMILES match, non-match.
- Similarity: self-match score, threshold filter, score-ordering, default
  threshold echoed.
- Exact: InChIKey match, SMILES match.
- Trust: default hides rejected, `include_rejected` restores them at the
  end of the result.
- Pagination envelope and deterministic ordering.
- GET / POST parity; POST rejects query-string filters.
- Include: `review`, `all`, `all` + `internal_ids` policy gate (off and on).
- Payload safety: recursive walk for forbidden keys.

---

## Open questions

- **Reaction substructure search.** Likely a v1 ask — design hinges on
  how participant-level structural matches roll up to a reaction-level
  hit. Out of scope here.
- **Pre-computed fingerprint column for similarity.** The substructure
  GiST index is shipped (P1-3); a separate fingerprint-cache column
  with its own GiST opclass is the natural next step once similarity
  query volume justifies the extra storage. Defer until observed.
