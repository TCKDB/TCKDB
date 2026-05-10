# TCKDB MVP Read/Query API — Endpoint Spec

**Status:** Draft (Phase 2)
**Owners:** Calvin
**Date:** 2026-05-09
**Parent roadmap:** [docs/roadmaps/read_api_phases.md](../roadmaps/read_api_phases.md)
**Sister specs (still authoritative for their slice):**
[lookup-expansion-spec.md](../lookup-expansion-spec.md),
[conformer_read_schema_and_endpoints.md](../conformer_read_schema_and_endpoints.md),
[network_read_api_spec_harness.md](../network_read_api_spec_harness.md),
[workflow-tool-read-api.md](../workflow-tool-read-api.md),
[DR-0016](../decisions/0016-chemistry-first-lookup-api.md).

---

## Purpose

Define the v0 contracts for `/api/v1/scientific/*` — the composite scientific read layer that answers user-facing chemistry questions with built-in trust and provenance summaries.

This spec is the **canonical source** for:

- response envelopes for the five MVP endpoints
- common response fragments (review/provenance/evidence)
- filter/sort/collapse/include grammar
- pagination, error model, default behavior

Anything in this spec that contradicts an existing per-area read spec wins for `/scientific/*` only; it does not modify `/lookup/*` or direct entity routes.

---

## Scope

In scope:

- `GET /api/v1/scientific/species/search`
- `GET|POST /api/v1/scientific/reactions/search`
- `GET /api/v1/scientific/reaction-entries/{id}/kinetics`
- `GET /api/v1/scientific/species-entries/{id}/thermo`
- `GET /api/v1/scientific/reaction-entries/{id}/full`
- common fragments and conventions used by all five

Out of scope (per roadmap §Non-goals):

- replacing or modifying `/lookup/*` or direct entity routes
- any frontend behavior
- any write/ingestion behavior
- LLM summarization, recommendation, ranking beyond L1–L3
- subjective selectors of any kind
- changes to ARC or `tckdb-client`

---

## Common conventions

### URL prefix

All endpoints in this spec live under `/api/v1/scientific/`.

### Filter / sort / collapse axes (D4)

Three orthogonal query-parameter axes:

| Axis | Purpose | Examples |
|---|---|---|
| **filter** | exclude records | `min_review_status=approved`, `temperature_min=300`, `level_of_theory_id=12`, `software=gaussian` |
| **sort** | deterministic ranking | per-endpoint default only — see *Sort vocabulary (v0)* below |
| **collapse** | reduce N matches to fewer | `collapse=all` (default), `collapse=first` — see *Collapse semantics* below |

Order of application is fixed: **filter → sort → collapse**. Pagination is applied last, after collapse.

### Sort vocabulary (v0)

Client-supplied `sort=` is **not accepted** in v0. Each endpoint uses its documented default sort (per L3). If a client sends a `sort=` query parameter, the endpoint returns:

```
422 Unprocessable Entity
{ "error": { "code": "client_sort_not_supported", "message": "sort= is not accepted in v0; the per-endpoint default sort applies." } }
```

The default sort for each endpoint is documented in its **Sorting** subsection. Per-key ASC/DESC direction is fixed by the default and is not configurable. A future v1 may open a constrained per-endpoint `sort=` vocabulary; v0 keeps the surface closed.

### Collapse semantics

```
collapse=all (default):
  return all eligible records after filter → sort → pagination.

collapse=first:
  return at most one record (the first record after filter and sort).
  The records array shape is preserved; it contains either zero items
  (no matches survived filtering) or exactly one item.
```

Collapse interacts with pagination and review summary as follows:

- `pagination.total` reflects the **pre-collapse, post-filter** match count. With `collapse=first`, `total` may be larger than `returned` (which is 0 or 1).
- `review_summary` counts the **pre-collapse, post-filter** candidate set. It reports the trust posture of the candidates the endpoint considered, not only the one record returned.
- `pagination.offset` and `pagination.limit` are still echoed as supplied; with `collapse=first` the response always returns 0 or 1 items regardless of `limit`.

### Default trust posture (D5, D7)

```
include approved, under_review, not_reviewed
exclude rejected     (override: include_rejected=true)
exclude deprecated   (override: include_deprecated=true)
```

`min_review_status` applies **shallow** (D7) — only on the primary record returned by the endpoint:

```
/scientific/reaction-entries/{id}/kinetics    → kinetics.review_status
/scientific/species-entries/{id}/thermo       → thermo.review_status
/scientific/species/search                    → species_entry.review_status (when an entry is involved)
/scientific/reactions/search                  → reaction_entry review badge
/scientific/reaction-entries/{id}/full        → applied per joined section, not chained transitively
```

A future `provenance_min_review_status` filter (deferred) will walk the chain. Not in v0.

### `review_rank` mapping (L2)

Lower wins:

```
approved     = 0
under_review = 1
not_reviewed = 2
deprecated   = 3
rejected     = 4
```

Used internally by the sort layer. Not exposed in responses (responses carry `review_status` strings).

### `include=` tokens (L4)

The full legal vocabulary across all endpoints:

```
provenance, calculations, artifacts, review, species, thermo, kinetics,
statmech, transport, transition_states, path_search, irc, scans, conformers, all
```

Per-endpoint subsets are listed in each endpoint section. Unknown tokens → **422 Unprocessable Entity** with a list of legal tokens for that endpoint. `include=all` expands to every legal token for the endpoint it's used on.

### Review include naming (`include=review` vs `include_review=full`)

Two distinct request flags govern review data; they are **not synonyms**:

| Flag | What it does | Where it lives |
|---|---|---|
| `include=review` | Adds per-record `RecordReviewBadge` objects to records that don't already carry one by default. | Member of the `include=` token vocabulary (L4). |
| `include_review=full` | Adds the `review_records` audit-style array to the response (full review history per record). Currently supported only on `/scientific/reaction-entries/{id}/full`. | Standalone boolean-style query parameter; values: `summary` (default) or `full`. |

For endpoints other than `/full`, `include_review=full` is rejected with 422 (`unsupported_review_detail`). The default for `include_review` is always `summary`.

### Pagination (L5)

Search-style and result-list endpoints (everything except `/full`) accept:

```
offset   (default 0, min 0)
limit    (default 50, min 1, max 200)
```

Responses include:

```json
{
  "pagination": {
    "offset": 0,
    "limit": 50,
    "returned": 12,
    "total": 12
  }
}
```

**`pagination.total` semantics:** count after filters and **before** sort, collapse, and pagination. With `collapse=first`, `total` may be greater than `returned`. With `offset > 0`, `total` is the same on every page (it does not change with offset). Endpoints must not return a different `total` for the same filter inputs across calls.

`/scientific/reaction-entries/{id}/full` is not paginated — it returns one composite document. Sub-arrays inside it are returned in full unless suppressed by `include=`.

### Error model

| Code | Meaning |
|---|---|
| 200 | Success — possibly with empty `records` array |
| 400 | Malformed request (bad JSON, missing required path param) |
| 404 | Path-param resource does not exist (e.g., unknown `reaction_entry_id`) |
| 422 | Validation error — bad enum value, unknown `include=` token, conflicting filters |

Empty result sets are **not** 404. A search with zero matches returns:

```json
{ "request": {...}, "review_summary": {"approved": 0, ...}, "records": [], "pagination": {...} }
```

Error body:

```json
{
  "error": {
    "code": "unknown_include_token",
    "message": "Token 'banana' is not a legal include= value for /scientific/species/search.",
    "legal_values": ["thermo", "review", "all"]
  }
}
```

---

## Common response fragments

Defined here once, referenced by every endpoint. Each fragment has a stable JSON shape.

### `ReviewStatusSummary`

Counts per status across the relevant joined record set.

```json
{
  "approved": 4,
  "under_review": 0,
  "not_reviewed": 2,
  "deprecated": 0,
  "rejected": 0,
  "total": 6
}
```

### `RecordReviewBadge`

Single record's direct review state. No chain traversal (per D7).

```json
{
  "status": "approved",
  "reviewed_at": "2026-04-12T10:33:00Z",
  "reviewer_kind": "human"
}
```

`reviewer_kind` is one of `human | automated | system`. Fields beyond `status` are omitted when unknown.

### `LevelOfTheorySummary`

```json
{
  "level_of_theory_id": 12,
  "method": "wb97xd",
  "basis": "def2tzvp",
  "dispersion": null,
  "solvent": null,
  "label": "wb97xd/def2tzvp"
}
```

### `SoftwareReleaseSummary`

```json
{
  "software_release_id": 5,
  "software": "Gaussian",
  "version": "16.C.01"
}
```

### `CalculationEvidenceSummary`

```json
{
  "calculation_id": 110,
  "calculation_type": "opt",
  "converged": true,
  "geometry_validation_status": "passed",
  "scf_stability_status": "not_present",
  "level_of_theory": { "...LevelOfTheorySummary": "" },
  "software": { "...SoftwareReleaseSummary": "" }
}
```

**`geometry_validation_status`** values match the underlying `CalculationGeometryValidation.validation_status` column (`ValidationStatus` enum) plus `not_present`:

```
passed       — validation row exists with status=passed
warning      — validation row exists with status=warning (advisory issues, not invalidating)
fail         — validation row exists with status=fail
not_present  — no CalculationGeometryValidation row exists for this calculation
```

**`scf_stability_status`** values match the underlying `CalculationSCFStability.status` column (`SCFStabilityStatus` enum) plus `not_present`:

```
stable        — stability row exists with status=stable
unstable      — stability row exists with status=unstable
stabilized    — stability row exists with status=stabilized (was unstable, then converged after correction)
inconclusive  — stability row exists with status=inconclusive
not_present   — no CalculationSCFStability row exists for this calculation
```

`not_present` is an API-level value indicating absence of the validation row. The underlying database does not store a `not_present` value — it represents the missing row.

### `ProvenanceSummary`

```json
{
  "primary_calculation": { "...CalculationEvidenceSummary": "" },
  "level_of_theory": { "...LevelOfTheorySummary": "" },
  "software": { "...SoftwareReleaseSummary": "" },
  "supporting_calculation_ids": [60, 61, 62, 63],
  "submission_id": 41
}
```

### `PathSearchSummary`

```json
{
  "calculation_id": 61,
  "method": "gsm",
  "converged": true
}
```

### `ValidationSummary` and `SCFStabilitySummary`

Both fragments share the same outer shape — a status string plus the calculation ID it describes.

**`ValidationSummary`** (geometry validation outcome for a calculation):

```json
{ "status": "passed | warning | fail | not_present", "calculation_id": 60 }
```

**`SCFStabilitySummary`** (SCF wavefunction stability outcome for a calculation):

```json
{ "status": "stable | unstable | stabilized | inconclusive | not_present", "calculation_id": 60 }
```

`not_present` indicates that no row exists in the corresponding validation/stability table for this calculation. It is an API-level value, not stored in the database.

### `TemperatureCoverage` (D8 verbatim)

```json
{
  "requested_min_k": 300.0,
  "requested_max_k": 2000.0,
  "record_min_k": 300.0,
  "record_max_k": 1500.0,
  "covers_requested_range": false,
  "overlap_fraction": 0.7059,
  "extrapolation_distance_k": 500.0
}
```

`overlap_fraction` is a diagnostic field. **It is never used as the primary `temperature_coverage` sort score (D8).**

### `EvidenceCompletenessBreakdown` (L1)

`EvidenceCompletenessBreakdown` has a **stable outer shape** (`score`, `max`, `checklist`) but **endpoint-specific `checklist` keys**. The integer `score` equals the count of `true` predicates in the `checklist`; `max` is the count of total predicates for that endpoint flavor.

**Kinetics flavor** (returned by `/scientific/reaction-entries/{id}/kinetics`):

```json
{
  "score": 5,
  "max": 9,
  "checklist": {
    "has_source_calculations": true,
    "has_transition_state_entry": true,
    "has_ts_opt_evidence": true,
    "has_ts_freq_evidence": true,
    "has_ts_sp_evidence": false,
    "has_path_search_or_irc_evidence": false,
    "has_uncertainty": true,
    "has_geometry_validation": true,
    "has_scf_stability": false
  }
}
```

**Thermo flavor** (returned by `/scientific/species-entries/{id}/thermo`):

```json
{
  "score": 4,
  "max": 8,
  "checklist": {
    "has_source_calculations": true,
    "has_statmech_source": false,
    "has_frequency_evidence": true,
    "has_sp_or_energy_evidence": true,
    "has_temperature_dependent_model": true,
    "has_uncertainty": false,
    "has_geometry_validation": false,
    "has_scf_stability": false
  }
}
```

Predicate definitions for each flavor live in the relevant endpoint's *Evidence completeness predicates* subsection.

The species/reaction search endpoints do **not** return `EvidenceCompletenessBreakdown` (per L1 — they use boolean availability flags instead).

---

# Endpoints

The five MVP endpoints, in implementation order.

---

## 1. `GET /api/v1/scientific/species/search`

### Purpose

Identity-resolved species discovery. Find candidate species by chemical identifier, with review-state and "what's available" summaries on each entry, in one round trip.

### User story

> *I have a SMILES. Show me what species TCKDB knows about, which entries are approved, and what data exists for each — without me chaining `/lookup/species` and 4 follow-ups.*

### Distinction from `/lookup/species`

`/lookup/species` returns the {query, match, results} envelope with structural match status and pointer-style results. `/scientific/species/search` returns answer-shaped records with per-entry review badge, availability flags, and an aggregate `review_summary`. Use lookup for "did I find it"; use scientific for "tell me about it."

### Query parameters

**filter:**
| Param | Type | Default | Notes |
|---|---|---|---|
| `smiles` | string | — | At least one of {smiles, inchi, inchi_key, formula} required. |
| `inchi` | string | — | |
| `inchi_key` | string | — | |
| `formula` | string | — | |
| `charge` | int | — | |
| `multiplicity` | int | — | |
| `electronic_state_kind` | enum | — | `SpeciesEntryStateKind`: `ground | excited` |
| `species_entry_kind` | enum | — | `StationaryPointKind`: `minimum | vdw_complex` |
| `min_review_status` | enum | — | shallow per D7 |
| `include_rejected` | bool | `false` | |
| `include_deprecated` | bool | `false` | |

**Multiple identifier filters** (`smiles`, `inchi`, `inchi_key`, `formula`) combine with **AND** semantics. If the supplied identifiers are mutually inconsistent (e.g., `inchi_key=A...` and `formula=B...` that don't agree), the endpoint returns **zero matches** in a 200 response — not a 422 validation error.

**sort:** v0 uses the documented default sort only; client-supplied `sort=` is rejected (see *Sort vocabulary*). Default: `review_rank ASC, has_entries DESC, created_at DESC, id DESC`.

**collapse:** `all` (default), `first`. See *Collapse semantics*.

**include:** subset of `{thermo, statmech, transport, conformers, review, all}`. `evidence_completeness` is not used here (per L1).

**pagination:** `offset`, `limit`.

**path scope:** this endpoint has no path parameter.

### Default behavior

- Returns one row per `species`, with all matching `species_entry` rows nested.
- `rejected` and `deprecated` entries excluded.
- Default sort emphasizes review status, then "has any entries with data."

### Response shape

```json
{
  "request": {
    "filter": { "smiles": "C[CH2]" },
    "sort": "review_rank,has_entries,created_at,id",
    "collapse": "all",
    "include": []
  },
  "review_summary": { "approved": 0, "under_review": 0, "not_reviewed": 1, "deprecated": 0, "rejected": 0, "total": 1 },
  "records": [
    {
      "species_id": 12,
      "canonical_smiles": "C[CH2]",
      "inchi_key": "ZGEGCLOFRBLKSE-UHFFFAOYSA-N",
      "formula": "C2H5",
      "charge": 0,
      "multiplicity": 2,
      "entries": [
        {
          "species_entry_id": 31,
          "species_entry_kind": "minimum",
          "electronic_state_kind": "ground",
          "review": { "...RecordReviewBadge": "" },
          "availability": {
            "has_thermo": true,
            "has_statmech": true,
            "has_transport": false,
            "has_conformers": true,
            "calculation_count": 4
          }
        }
      ]
    }
  ],
  "pagination": { "offset": 0, "limit": 50, "returned": 1, "total": 1 }
}
```

### Sorting (L3)

Default (and only): `review_rank ASC, has_entries DESC, created_at DESC, id DESC`. Client-supplied `sort=` → 422.

`has_entries` is a derived bool: true if the species has at least one entry surviving filters.

### Test matrix

1. Lookup by exact SMILES returns the canonical species.
2. Lookup by inchi_key matches.
3. Default behavior excludes rejected and deprecated entries.
4. `include_deprecated=true` surfaces deprecated entries.
5. `min_review_status=approved` returns only species with at least one approved entry.
6. `include=thermo` adds a `thermo_summary` block per entry.
7. Empty result returns `records: []`, not 404.
8. Unknown `include=` token → 422 with legal-token list.
9. Pagination: `limit=1` and `offset=1` return non-overlapping pages.
10. Sort is deterministic across two calls with identical params.
11. `sort=anything` → 422 (`client_sort_not_supported`).
12. Two consistent identifiers (`smiles=X&inchi_key=Y` for the same species) AND-combine and return that species.
13. Two inconsistent identifiers return 200 with empty `records`.

---

## 2. `GET|POST /api/v1/scientific/reactions/search`

### Purpose

Reaction-entry-resolved lookup by reactants and products, with per-entry kinetics/TS availability and review summary.

### User story

> *I have reactants and products. What reaction entries does TCKDB know? Which have kinetics? Which have a TS? Are any approved?*

### Distinction from `/lookup/reaction` and `/lookup/reaction-kinetics`

`/lookup/reaction` returns reaction-identity matches with the lookup envelope. `/lookup/reaction-kinetics` is a primitive composed lookup. `/scientific/reactions/search` is answer-shaped: review summary, availability flags, default sort favoring kinetics availability.

### Why both GET and POST

Reactant/product lists encode poorly in URLs once SMILES contain query characters. `GET` accepts repeated `reactants=` / `products=` params for simple cases; `POST` accepts a body for the rest:

```json
{
  "reactants": ["[CH3]", "c1ccccc1"],
  "products": ["CH4", "[c]1ccccc1"],
  "direction": "either",
  "min_review_status": "approved",
  "include_deprecated": false,
  "include_rejected": false,
  "include": ["kinetics"],
  "collapse": "all",
  "offset": 0,
  "limit": 50
}
```

`POST` has no side effects; it's a query-by-body convenience.

**POST body rule:** all search fields, filters, `include` flags, `collapse`, `offset`, and `limit` are supplied in the JSON body. Query-string parameters are not accepted on the POST form except for any infrastructure-level params already used app-wide. The body must not contain a `sort` key (client-supplied sort is rejected for v0 — see *Sort vocabulary*); supplying it returns 422.

### Query parameters

**filter:**
| Param | Type | Default | Notes |
|---|---|---|---|
| `reactants` | string[] | — | Repeated on GET, or in POST body. |
| `products` | string[] | — | |
| `direction` | enum | `either` | `forward | reverse | either`. See *Direction semantics* below. |
| `family` | string | — | `ReactionFamily.name` (string match against the family table). |
| `min_review_status` | enum | — | shallow per D7 |
| `include_rejected` | bool | `false` | |
| `include_deprecated` | bool | `false` | |

**Direction semantics.** The schema does not store a per-entry direction; `ChemReaction.reversible` is the only related field. The `direction` query parameter is **match-time semantics**, not a filter against a stored column:

- `forward` — query reactants must match stored reactants and query products must match stored products.
- `reverse` — query reactants must match stored products and query products must match stored reactants.
- `either` (default) — match in either orientation; for non-reversible reactions, `reverse` matches still pass (the API does not gate on `reversible`).

`direction=exact` is **not supported in v0** and returns 422 if supplied. Strict-orientation matching may be added in v1.

**sort:** v0 uses the documented default sort only; client-supplied `sort=` is rejected. Default: `review_rank ASC, has_kinetics DESC, has_transition_state DESC, created_at DESC, id DESC`.

**collapse:** `all` (default), `first`. See *Collapse semantics*.

**include:** subset of `{kinetics, transition_states, species, review, all}`. `evidence_completeness` not used here (per L1).

**pagination:** `offset`, `limit`.

**path scope:** this endpoint has no path parameter.

### Default behavior

- One row per `reaction_entry`.
- `direction=either` treats forward and reverse as equivalent matches.
- Reactant/product matching is by canonical structure of resolved species_entries (consistent with `/lookup/reaction`).

### Response shape

```json
{
  "request": {
    "filter": { "reactants": ["[CH3]", "c1ccccc1"], "products": ["CH4", "[c]1ccccc1"], "direction": "either" },
    "sort": "review_rank,has_kinetics,has_transition_state,created_at,id",
    "collapse": "all",
    "include": []
  },
  "review_summary": { "approved": 1, "under_review": 0, "not_reviewed": 3, "deprecated": 0, "rejected": 0, "total": 4 },
  "records": [
    {
      "reaction_id": 44,
      "reaction_entry_id": 51,
      "equation": "[CH3] + c1ccccc1 <=> CH4 + [c]1ccccc1",
      "matched_direction": "forward",
      "reversible": true,
      "family": "h_abstraction",
      "review": { "...RecordReviewBadge": "" },
      "reactants": [
        { "species_entry_id": 31, "smiles": "[CH3]", "participant_index": 0 },
        { "species_entry_id": 27, "smiles": "c1ccccc1", "participant_index": 1 }
      ],
      "products": [
        { "species_entry_id": 32, "smiles": "CH4", "participant_index": 0 },
        { "species_entry_id": 33, "smiles": "[c]1ccccc1", "participant_index": 1 }
      ],
      "availability": {
        "has_kinetics": true,
        "has_transition_state": true,
        "has_path_search": true,
        "kinetics_count": 2
      }
    }
  ],
  "pagination": { "offset": 0, "limit": 50, "returned": 1, "total": 1 }
}
```

### Test matrix

1. GET with two reactants and two products matches a known reaction entry.
2. POST with the same payload (in body) yields the identical response body.
3. `direction=forward` excludes reverse matches; `direction=either` includes both; `matched_direction` echoed per record.
4. Deprecated/rejected reaction entries excluded by default.
5. `min_review_status=approved` filter on the entry-level review badge.
6. `include=kinetics` adds a `kinetics_summary` array per entry.
7. Empty result → 200 with empty `records`.
8. `direction=exact` → 422 (`unsupported_direction`); other invalid values → 422 (`invalid_enum`).
9. Sort is deterministic.
10. POST with `sort` in body → 422 (`client_sort_not_supported`).
11. POST with `reactants` in query string → 422 (`post_search_fields_must_be_in_body`).

---

## 3. `GET /api/v1/scientific/reaction-entries/{id}/kinetics`

### Purpose

Return kinetics records attached to a reaction entry, sorted by trust + temperature coverage, with provenance summaries.

### User story

> *Give me Arrhenius parameters for reaction X over 300–2000 K. Which records cover the range? What level of theory? Was the TS validated? Is anything approved?*

### Distinction from `/lookup/kinetics` and `/lookup/reaction-kinetics`

`/lookup/kinetics` and `/lookup/reaction-kinetics` return the lookup envelope and do not embed `temperature_coverage` scoring or `evidence_completeness`. `/scientific/.../kinetics` is the answer endpoint: D8 sort, evidence breakdown, review badge per record, provenance summary embedded.

### Path parameters

- `id` — `reaction_entry.id` only. 404 if unknown. **Does not accept `chem_reaction.id`.** Callers needing chem_reaction-level lookup must use `/scientific/reactions/search` first to enumerate entries.

### Query parameters

**filter:**
| Param | Type | Default | Notes |
|---|---|---|---|
| `temperature_min` | float | — | Kelvin. Used by D8 sort. |
| `temperature_max` | float | — | Kelvin. Used by D8 sort. |
| `model_kind` | enum | — | `KineticsModelKind`: `arrhenius | modified_arrhenius`. Network-level pressure-dependent forms (Chebyshev, PLOG, tabulated) live on `Network` in the current schema and are out of scope for this endpoint in v0. |
| `level_of_theory_id` | int | — | See *LoT filter target* below. |
| `software` | string | — | software name |
| `min_review_status` | enum | — | shallow per D7 |
| `include_rejected` | bool | `false` | |
| `include_deprecated` | bool | `false` | |

**LoT filter target.** `level_of_theory_id` filters against the **primary level of theory of the kinetics record's source-calculation chain** — specifically the level of theory of the calculation linked via `KineticsSourceCalculation` with role `ts_energy` if present, falling back to `fit_source`, then `freq`, then any source calculation, in that priority order. It does **not** filter against every nested chain calculation. Deep provenance LoT filtering is deferred to v1. If no source calculation is attached, the kinetics record is treated as having no LoT and is excluded when `level_of_theory_id` is specified.

**sort:** v0 uses the documented default sort only; client-supplied `sort=` is rejected. Default = D9 verbatim:
```
covers_requested_range DESC,
extrapolation_distance_k ASC,
review_rank ASC,
evidence_completeness DESC,
created_at DESC,
id DESC
```
When neither `temperature_min` nor `temperature_max` is provided, `covers_requested_range` is treated as neutral (ties on `true` for all records) and `extrapolation_distance_k` is `0` for all records, so the effective sort becomes `review_rank ASC, evidence_completeness DESC, created_at DESC, id DESC`.

**collapse:** `all` (default), `first`. See *Collapse semantics*.

**include:** subset of `{provenance, calculations, transition_states, path_search, irc, review, artifacts, all}`. `provenance` is included by default and need not be requested. `include_review=full` is **not supported** on this endpoint (422 `unsupported_review_detail`).

**pagination:** `offset`, `limit`.

### v0 `parameters` shape per `model_kind`

Both supported model kinds expose the same Arrhenius-family columns from the schema. `parameters` shape:

```json
{
  "model_kind": "modified_arrhenius",
  "parameters": {
    "A": 1.2e-12,
    "A_units": "cm3_molecule_s",
    "n": 2.1,
    "Ea_kj_mol": 15.4
  }
}
```

`model_kind` values: `arrhenius | modified_arrhenius` (per `KineticsModelKind`).
`A_units` values: per `ArrheniusAUnits` — `per_s | cm3_mol_s | cm3_molecule_s | m3_mol_s | cm6_mol2_s | cm6_molecule2_s | m6_mol2_s`.
For `arrhenius`, `n` may be omitted or null; for `modified_arrhenius`, `n` is expected to be present (no API-side validation — reflects what the schema stores).

### v0 `uncertainty` shape

Always returned (may have all-null entries):

```json
{
  "uncertainty": {
    "A_uncertainty": 1.5,
    "A_uncertainty_kind": "multiplicative",
    "n_uncertainty": null,
    "Ea_uncertainty_kj_mol": 0.4
  }
}
```

`A_uncertainty_kind` values: per `KineticsUncertaintyKind` — `additive | multiplicative` (omitted/null when `A_uncertainty` is null).

### v0 `tunneling` shape

`tunneling_model` is returned as a **nullable string** (matches the underlying `Kinetics.tunneling_model: Optional[str]` column). Structured tunneling submodels are deferred:

```json
{ "tunneling_model": "eckart" }
```

or:

```json
{ "tunneling_model": null }
```

### Kinetics provenance shape (TS-backed and non-TS-backed)

**Transition-state provenance is optional.** A kinetics record may or may not be backed by a transition-state calculation chain. The endpoint must not imply otherwise.

Kinetics records may originate from any of:

```
TST/computational  — backed by a TS opt/freq/SP chain (and optionally path-search/IRC)
experimental       — measured rate, often paired with a literature reference
estimated          — rule-based, group-additivity, or analogy estimate
imported           — re-published from another database
fitted             — empirical fit, possibly with no underlying calc chain
network-derived    — fit from a master-equation solve on a network
literature-derived — extracted from a published table or correlation
```

`Kinetics.scientific_origin` (`ScientificOriginKind`: `computed | experimental | estimated`) is returned at the top level of every kinetics record so the consumer can interpret the rest of provenance correctly.

**Provenance fields by record category:**

| Field | TS-backed computational | Non-TS-backed (experimental / estimated / imported / fitted / network / literature) |
|---|---|---|
| `transition_state_entry_id` | int | `null` |
| `ts_opt_calculation_id` | int | `null` |
| `ts_freq_calculation_id` | int | `null` |
| `ts_sp_calculation_id` | int | `null` |
| `path_search` | `PathSearchSummary` or `null` | `null` |
| `irc` | object or `null` | `null` |
| `primary_level_of_theory` | `LevelOfTheorySummary` if any source calc has one | `null` if none |
| `primary_software` | `SoftwareReleaseSummary` if any source calc has one | `null` if none |
| `geometry_validation` | `ValidationSummary` from TS opt chain | `null` |
| `scf_stability` | `SCFStabilitySummary` from TS opt/SP chain | `null` |
| `source_calculations` | array of `CalculationEvidenceSummary` (when `include=calculations`) | array, possibly empty if no calculations support this record |
| `literature` | object (id, title, year) when `Kinetics.literature_id` is set | object when set; commonly populated for experimental and literature-derived records |
| `software_release` | `SoftwareReleaseSummary` when `Kinetics.software_release_id` is set | object when set |
| `workflow_tool_release` | object when `Kinetics.workflow_tool_release_id` is set | object when set |

**Response-shape convention** (aligned with `/full` empty-section policy):

- In the **default summary** `provenance` block on each kinetics record, every documented provenance key is **always present**; unavailable values are returned as `null` (not omitted). This lets clients depend on a stable Pydantic model shape across record categories.
- In **expanded include sections** (e.g. `include=calculations`, `include=path_search`):
  - Collection sections are returned as `[]` when empty.
  - Object sections are returned as `null` when unavailable.
  - Sections not requested via `include=` are omitted entirely.

### Evidence completeness predicates (kinetics)

Each predicate is a deterministic boolean computed from the kinetics record and its joined provenance. Max score = 9.

| Predicate | True when |
|---|---|
| `has_source_calculations` | At least one `KineticsSourceCalculation` row exists for this kinetics record. |
| `has_transition_state_entry` | The reaction entry has at least one `TransitionState` row (any status). |
| `has_ts_opt_evidence` | A source calculation with `KineticsCalculationRole.ts_energy` exists, OR a `Calculation.calculation_type=opt` exists on the TS entry chain. |
| `has_ts_freq_evidence` | A source calculation with role `freq` exists, OR a `Calculation.calculation_type=freq` is reachable from the TS entry. |
| `has_ts_sp_evidence` | A source calculation with role `ts_energy` (interpreted as final SP energy) exists, OR a `Calculation.calculation_type=sp` is reachable from the TS entry. |
| `has_path_search_or_irc_evidence` | A source calculation with role `irc` exists, OR a `Calculation.calculation_type` in {`path_search`, `irc`} is reachable from the TS entry. |
| `has_uncertainty` | Any of `Kinetics.a_uncertainty`, `Kinetics.n_uncertainty`, `Kinetics.ea_uncertainty_kj_mol` is non-null. |
| `has_geometry_validation` | A `CalculationGeometryValidation` row exists for the TS opt calculation with `validation_status` in {`passed`, `warning`}. (`fail` does not contribute — failed validation is not positive evidence.) |
| `has_scf_stability` | A `CalculationSCFStability` row exists for the TS opt or SP calculation with `status` in {`stable`, `stabilized`}. (`unstable` and `inconclusive` do not contribute.) |

If a predicate cannot be computed in v0 because the supporting read path is not yet implemented, it must be returned as **`false` with the predicate key still present**, so the score is auditable. Phase 3 implementation must not silently omit checklist keys.

**Non-TS-backed kinetics and TS-related predicates.** For non-TS-backed kinetics records (experimental, estimated, imported, fitted, network-derived, literature-derived), TS-related predicates (`has_transition_state_entry`, `has_ts_opt_evidence`, `has_ts_freq_evidence`, `has_ts_sp_evidence`, `has_path_search_or_irc_evidence`, `has_geometry_validation`, `has_scf_stability`) are **`false`, not missing**. Their checklist keys remain present.

This lowers the generic computational-evidence score for non-TS-backed records, but it **does not mean the record is invalid or untrustworthy**. Consumers must interpret `evidence_completeness` together with `scientific_origin`:

- A `scientific_origin=computed` record with low `evidence_completeness` is genuinely under-evidenced for a TST workflow.
- A `scientific_origin=experimental` record with low `evidence_completeness` simply means the computational checklist does not apply to it; the trustworthiness signal is `review_status` plus `literature` plus `uncertainty`, not the TS checklist.

The L1 formula and the predicate set are unchanged.

**Sorting implication.** Per D9, `evidence_completeness` is the **fourth-priority** sort key — strictly behind `covers_requested_range`, `extrapolation_distance_k`, and `review_rank`. Because of this ordering, non-TS-backed records are **not** automatically demoted below TS-backed records of comparable temperature coverage and review status. They are not hidden. Clients that want only TS-backed computational kinetics should use scientific-origin-aware filtering once it ships; this spec does not add a new required filter for v0. (A future enhancement may add a `scientific_origin=` filter and/or a `requires_ts_chain=true` filter; not in v0.)

### Response shape

The response is the same envelope for every record category. Two examples follow: a TS-backed computational kinetics record and a non-TS-backed (here, experimental) kinetics record. Both shapes are valid in the same response.

**Example 1 — TS-backed computational kinetics:**

```json
{
  "request": {
    "filter": { "temperature_min": 300.0, "temperature_max": 2000.0 },
    "sort": "covers_requested_range,extrapolation_distance_k,review_rank,evidence_completeness,created_at,id",
    "collapse": "all",
    "include": ["provenance"]
  },
  "reaction_entry_id": 51,
  "review_summary": { "approved": 0, "under_review": 0, "not_reviewed": 2, "deprecated": 0, "rejected": 0, "total": 2 },
  "records": [
    {
      "kinetics_id": 101,
      "scientific_origin": "computed",
      "model_kind": "modified_arrhenius",
      "review": { "...RecordReviewBadge": "" },
      "parameters": {
        "A": 1.2e-12,
        "A_units": "cm3_molecule_s",
        "n": 2.1,
        "Ea_kj_mol": 15.4
      },
      "tunneling_model": "eckart",
      "uncertainty": {
        "A_uncertainty": 1.5,
        "A_uncertainty_kind": "multiplicative",
        "n_uncertainty": null,
        "Ea_uncertainty_kj_mol": 0.4
      },
      "temperature_coverage": { "...TemperatureCoverage": "" },
      "evidence_completeness": { "...EvidenceCompletenessBreakdown": "" },
      "provenance": {
        "transition_state_entry_id": 9,
        "ts_opt_calculation_id": 60,
        "ts_freq_calculation_id": 62,
        "ts_sp_calculation_id": 63,
        "path_search": { "...PathSearchSummary": "" },
        "irc": null,
        "primary_level_of_theory": { "...LevelOfTheorySummary": "" },
        "primary_software": { "...SoftwareReleaseSummary": "" },
        "geometry_validation": { "...ValidationSummary": "" },
        "scf_stability": { "...SCFStabilitySummary": "" },
        "literature": null,
        "software_release": { "...SoftwareReleaseSummary": "" },
        "workflow_tool_release": null
      }
    }
  ],
  "pagination": { "offset": 0, "limit": 50, "returned": 1, "total": 2 }
}
```

**Example 2 — Non-TS-backed kinetics (experimental, with literature):**

```json
{
  "kinetics_id": 202,
  "scientific_origin": "experimental",
  "model_kind": "modified_arrhenius",
  "review": { "...RecordReviewBadge": "" },
  "parameters": {
    "A": 1.0e-12,
    "A_units": "cm3_molecule_s",
    "n": 0.0,
    "Ea_kj_mol": 12.3
  },
  "tunneling_model": null,
  "uncertainty": {
    "A_uncertainty": 1.3,
    "A_uncertainty_kind": "multiplicative",
    "n_uncertainty": null,
    "Ea_uncertainty_kj_mol": 0.6
  },
  "temperature_coverage": { "...TemperatureCoverage": "" },
  "evidence_completeness": { "...EvidenceCompletenessBreakdown": "" },
  "provenance": {
    "transition_state_entry_id": null,
    "ts_opt_calculation_id": null,
    "ts_freq_calculation_id": null,
    "ts_sp_calculation_id": null,
    "path_search": null,
    "irc": null,
    "primary_level_of_theory": null,
    "primary_software": null,
    "geometry_validation": null,
    "scf_stability": null,
    "literature": {
      "id": 77,
      "title": "Example kinetic study",
      "year": 1999
    },
    "software_release": null,
    "workflow_tool_release": null
  }
}
```

Both records may appear together in `records[]` for the same query. The endpoint must not synthesize, fabricate, or guess TS-chain fields for non-TS-backed records — they are returned as `null`.

### Test matrix

1. Unknown `reaction_entry_id` → 404.
2. With `temperature_min=300&temperature_max=2000`, a record covering exactly that range sorts above one covering 300–1500 K.
3. With same temperature range, two records covering it tie on coverage; the one with `extrapolation_distance_k=0` and lower `review_rank` wins.
4. `min_review_status=approved` filters by direct kinetics review only (D7) — verify by including a kinetics record with approved status whose source TS calc is `not_reviewed`; record must still appear.
5. Without `temperature_min/max`, temperature keys are neutral; sort falls through to `(review_rank, evidence_completeness, latest)`.
6. `evidence_completeness` is computed per the L1 kinetics checklist; breakdown is auditable.
7. `include=calculations` embeds full `CalculationEvidenceSummary` array.
8. Empty result (entry exists, no matching kinetics) → 200, empty `records`.
9. Sort is deterministic across two identical calls.
10. Unknown `include=` token → 422.
11. **TS-backed kinetics returns populated TS provenance.** A `scientific_origin=computed` record with TS opt/freq/SP source calculations returns non-null `transition_state_entry_id`, `ts_opt_calculation_id`, `ts_freq_calculation_id`, `ts_sp_calculation_id`, `primary_level_of_theory`, and `primary_software`.
12. **Non-TS-backed kinetics returns null TS provenance.** A `scientific_origin=experimental` record (or `estimated`/`imported` with no TS source calcs) returns `null` for every TS-chain field, but still returns `scientific_origin`, `parameters`, `uncertainty`, `review`, and any populated `literature`/`software_release`/`workflow_tool_release`. The TS-chain keys are present (not omitted).
13. **Non-TS-backed kinetics is not rejected by `min_review_status` solely on TS evidence.** A `scientific_origin=experimental, review=approved` record with all TS-related evidence checklist keys `false` is still returned by `min_review_status=approved`.
14. **TS and non-TS records co-exist in one response.** A query against an entry with both kinds of kinetics returns both in `records[]`, sorted per D9; `evidence_completeness` differs but neither is hidden.
15. **Provenance keys are stable.** For both TS-backed and non-TS-backed records, every key listed in the *Kinetics provenance shape* table is present in `provenance` (with the appropriate value or `null`); no key is silently omitted.

---

## 4. `GET /api/v1/scientific/species-entries/{id}/thermo`

### Purpose

Return thermo records attached to a species entry, sorted by temperature coverage + trust + evidence, with provenance summaries.

### User story

> *Give me thermo for species entry 31, ideally NASA polynomials covering 300–3000 K. Which records cover the range? Which are approved?*

### Distinction from `/lookup/thermo`

`/lookup/thermo` returns the lookup envelope. `/scientific/.../thermo` returns answer-shaped records with `temperature_coverage`, `evidence_completeness` (thermo variant per L1), and embedded `ProvenanceSummary`.

### Path parameters

- `id` — `species_entry.id` only. 404 if unknown. **Does not accept `species.id`.** Callers needing species-level lookup must use `/scientific/species/search` first to enumerate entries.

### Query parameters

**filter:**
| Param | Type | Default | Notes |
|---|---|---|---|
| `temperature_min` | float | — | Kelvin. Used by sort. |
| `temperature_max` | float | — | Kelvin. Used by sort. |
| `model_kind` | enum | — | `nasa | points | scalar`. Wilhoit is not represented in the v0 schema and is out of scope. `nasa` matches records with a `ThermoNASA` row; `points` matches records with `ThermoPoint` rows; `scalar` matches records with only `h298`/`s298`-style scalar columns and no NASA/point structure. |
| `level_of_theory_id` | int | — | See *LoT filter target* below. |
| `software` | string | — | |
| `min_review_status` | enum | — | shallow per D7 |
| `include_rejected` | bool | `false` | |
| `include_deprecated` | bool | `false` | |

**LoT filter target.** `level_of_theory_id` filters against the **primary level of theory of the thermo record's source-calculation chain** — specifically the level of theory of the `ThermoSourceCalculation` row with role `sp` if present, falling back to `composite`, then `freq`, then `opt`, then any source calculation, in that priority order. It does **not** filter against every nested chain calculation. Deep provenance LoT filtering is deferred to v1. If no source calculation is attached, the thermo record is treated as having no LoT and is excluded when `level_of_theory_id` is specified.

**sort:** v0 uses the documented default sort only; client-supplied `sort=` is rejected. Default:
```
covers_requested_temperature_range DESC,
extrapolation_distance_k ASC,
review_rank ASC,
evidence_completeness DESC,
created_at DESC,
id DESC
```

**collapse:** `all` (default), `first`. See *Collapse semantics*.

**include:** subset of `{provenance, calculations, statmech, review, artifacts, all}`. `provenance` included by default. `include_review=full` is **not supported** on this endpoint (422 `unsupported_review_detail`).

**pagination:** `offset`, `limit`.

### v0 `model` shape per `model_kind`

A thermo record carries scalar h298/s298 fields **plus** zero or one of {NASA polynomial, points list}. The response always includes the scalar block; it conditionally includes a `nasa` block or a `points` block depending on what the record stores.

**Scalar block (always present):**

```json
{
  "h298_kj_mol": -12.3,
  "s298_j_mol_k": 250.1,
  "cp_units": "J/mol/K"
}
```

**NASA block** (when `model_kind=nasa`; matches `ThermoNASA` columns):

```json
{
  "nasa": {
    "t_low": 200.0,
    "t_mid": 1000.0,
    "t_high": 6000.0,
    "low_temperature_coefficients":  [a1, a2, a3, a4, a5, a6, a7],
    "high_temperature_coefficients": [b1, b2, b3, b4, b5, b6, b7]
  }
}
```

`a1..a7` map to `ThermoNASA.a1..a7` (low-temperature segment); `b1..b7` map to `ThermoNASA.b1..b7` (high-temperature segment). The temperature bounds may all be null (per the `temperature_bounds_all_or_none` constraint); coefficient values may be null.

**Points block** (when `model_kind=points`; matches `ThermoPoint` rows):

```json
{
  "points": [
    { "temperature_k": 300.0, "cp_j_mol_k": null, "h_kj_mol": null, "s_j_mol_k": null, "g_kj_mol": null }
  ]
}
```

Per-point fields are nullable; only the temperature is required.

**Scalar-only records** (`model_kind=scalar`): only the scalar block is returned, with no `nasa` or `points` key.

`temperature_coverage` is computed against the record's effective temperature range. For NASA records, the range is `[t_low, t_high]`; if either bound is null, that side does not participate in the coverage check (and `record_min_k` / `record_max_k` is returned as null in the fragment). For points records, the range is `[min(temperature_k), max(temperature_k)]` over the points present. For scalar records, the range is undefined and `covers_requested_temperature_range` is `false` whenever the client supplied a temperature bound; the record sorts last on the temperature keys.

### Evidence completeness predicates (thermo)

Each predicate is a deterministic boolean. Max score = 8.

| Predicate | True when |
|---|---|
| `has_source_calculations` | At least one `ThermoSourceCalculation` row exists. |
| `has_statmech_source` | A `Statmech` row links to this thermo or shares a source calculation. |
| `has_frequency_evidence` | A source calculation with role `freq` (`ThermoCalculationRole.freq`) exists. |
| `has_sp_or_energy_evidence` | A source calculation with role `sp` or `composite` exists, OR any source calc has a non-null electronic energy field. |
| `has_temperature_dependent_model` | A `ThermoNASA` row exists OR ≥2 `ThermoPoint` rows exist. |
| `has_uncertainty` | The thermo record has any non-null uncertainty field. **Predicate is `false` in v0** if the schema does not yet expose thermo uncertainty columns; key still returned. |
| `has_geometry_validation` | A `CalculationGeometryValidation` row exists for the opt source calculation with `validation_status` in {`passed`, `warning`}. |
| `has_scf_stability` | A `CalculationSCFStability` row exists for the sp source calculation with `status` in {`stable`, `stabilized`}. |

If a predicate cannot be computed because its supporting read path does not exist in v0, return `false` with the key present. Do not omit the key.

### Response shape

```json
{
  "request": {
    "filter": { "temperature_min": 300.0, "temperature_max": 3000.0, "model_kind": "nasa" },
    "sort": "covers_requested_temperature_range,extrapolation_distance_k,review_rank,evidence_completeness,created_at,id",
    "collapse": "all",
    "include": ["provenance"]
  },
  "species_entry_id": 31,
  "review_summary": { "approved": 1, "under_review": 0, "not_reviewed": 0, "deprecated": 0, "rejected": 0, "total": 1 },
  "records": [
    {
      "thermo_id": 88,
      "model_kind": "nasa",
      "review": { "...RecordReviewBadge": "" },
      "h298_kj_mol": -12.3,
      "s298_j_mol_k": 250.1,
      "cp_units": "J/mol/K",
      "nasa": {
        "t_low": 200.0,
        "t_mid": 1000.0,
        "t_high": 6000.0,
        "low_temperature_coefficients":  [3.50, 0.0001, 0.0, 0.0, 0.0, -1000.0, 4.0],
        "high_temperature_coefficients": [3.20, 0.0002, 0.0, 0.0, 0.0,  -950.0, 5.0]
      },
      "temperature_coverage": { "...TemperatureCoverage": "" },
      "evidence_completeness": { "...EvidenceCompletenessBreakdown": "" },
      "provenance": {
        "primary_calculation": { "...CalculationEvidenceSummary": "" },
        "level_of_theory": { "...LevelOfTheorySummary": "" },
        "software": { "...SoftwareReleaseSummary": "" },
        "statmech_id": 200,
        "freq_calculation_id": 62,
        "sp_calculation_id": 63
      }
    }
  ],
  "pagination": { "offset": 0, "limit": 50, "returned": 1, "total": 1 }
}
```

### Test matrix

1. Unknown `species_entry_id` → 404.
2. NASA record covering 300–3000 K sorts above one covering 300–1500 K when query is 300–3000 K.
3. `model_kind=nasa` filter hides records that have only scalar h298/s298 (no NASA row) and records with only points.
4. `min_review_status=approved` filters direct thermo review only (D7).
5. `evidence_completeness` is computed per L1 thermo checklist; breakdown is auditable.
6. `include=statmech` embeds statmech summary on each record.
7. Empty result → 200, empty `records`.
8. Sort is deterministic.
9. Unknown `include=` token → 422.

---

## 5. `GET /api/v1/scientific/reaction-entries/{id}/full`

### Purpose

One composite document supporting a reaction entry — species, kinetics, transition states, calculations, dependencies, review summary. Inspection/debugging/curation surface.

### User story

> *Show me everything supporting reaction entry 51. I'm investigating whether to trust the kinetics and need the full provenance graph.*

### Distinction from other endpoints

- `/scientific/reactions/search` returns lightweight reaction entries.
- `/scientific/reaction-entries/{id}/kinetics` returns sorted kinetics records.
- `/scientific/reaction-entries/{id}/full` returns all of it joined into one document, with deterministic nested ordering and no scientific ranking applied at the top level.

This endpoint is **not paginated** and not collapsed. Sub-arrays inside it are returned in full unless suppressed by `include=`.

### Path parameters

- `id` — `reaction_entry.id` only. 404 if unknown. **Does not accept `chem_reaction.id`.** Callers needing chem_reaction-level lookup must use `/scientific/reactions/search` first to enumerate entries.

### Query parameters

**filter (top-level):**

| Param | Type | Default | Notes |
|---|---|---|---|
| `min_review_status` | enum | — | Applied independently to each joined sub-array's primary records; see *Filter behavior on /full* below. |
| `include_rejected` | bool | `false` | Same scope as above. |
| `include_deprecated` | bool | `false` | Same scope as above. |

**Filter behavior on /full.** Top-level filters apply **per joined sub-array** to that section's primary records (e.g., to each kinetics record, each transition state, each species entry). They do **not** chain transitively into the supporting provenance graph (per D7). They do **not** remove the parent `reaction_entry` object itself — even if every sub-array filters down to empty, the response still returns `reaction_entry` and `review_summary`, with empty sub-arrays per the *Empty-section policy* below.

No other top-level filters are accepted in v0 (e.g., no `temperature_min` here — use the kinetics endpoint for that).

**sort:** **fixed**. Per L3:
- All sub-arrays sort by `review_rank ASC, created_at DESC, id DESC`.
- No client-overridable sort. Client-supplied `sort=` → 422. Composite documents must be reproducible call-to-call.

**collapse:** not applicable.

**include:** controls which sub-sections are populated. Subset of `{species, kinetics, transition_states, calculations, path_search, irc, scans, conformers, artifacts, review, all}`. Default: `species, kinetics, transition_states` (lightweight). To get full graph: `include=all`.

**`include_review` parameter:** `summary` (default) or `full`. `full` adds the top-level `review_records` audit array (see *Response shape* below). This endpoint is the **only** endpoint that supports `include_review=full`; other endpoints reject it with 422 (`unsupported_review_detail`).

**default behavior:**
- Always returns: `reaction_entry`, `review_summary`, `species` summary, `kinetics` summary array.
- Default omits: `calculations`, `artifacts`, `path_search`, `irc`, `scans`, `conformers`. The `review_records` array is omitted unless `include_review=full`.

**Empty-section policy.** Whenever an `include=` token is in the request set (explicitly or via `all`), that section's key is **always present** in the response, even when the underlying data set is empty:

- Collection sections (e.g. `kinetics`, `calculations`, `artifacts`, `irc`) are returned as empty JSON arrays (`[]`).
- Object-shaped sections (e.g. a future single object section) are returned as `null` if unavailable.

When an `include=` token is **not** in the request set, that section's key is **omitted** from the response entirely. Clients can therefore distinguish "I asked and got nothing" (`"foo": []` or `"foo": null`) from "I didn't ask" (key absent).

### Kinetics in `/full` and non-TS-backed records

The `kinetics` sub-array contains **all kinetics records** attached to the reaction entry that survive the top-level review filters (per *Filter behavior on /full* above), regardless of whether they have a transition-state backing.

For each kinetics record, its `provenance` block follows the **Kinetics provenance shape** documented in §Endpoint 3 — TS-chain fields are populated for TS-backed computational records and are `null` for non-TS-backed records (experimental, estimated, imported, fitted, network-derived, literature-derived). The endpoint **must not synthesize, fabricate, or infer** transition-state links for non-TS-backed records.

The `transition_states` sub-array is **independent** of the `kinetics` sub-array. It contains only the `TransitionState` rows actually associated with the `reaction_entry` — never invented from kinetics records that lack a TS chain. A reaction entry with only experimental kinetics will return an empty `transition_states: []` (when `include=transition_states` is requested) and still return the experimental kinetics in `kinetics[]`.

### Response shape (with `include=all`)

```json
{
  "request": {
    "include": ["species", "kinetics", "transition_states", "calculations", "path_search", "irc", "scans", "conformers", "artifacts", "review"]
  },
  "reaction_entry": {
    "id": 51,
    "reaction_id": 44,
    "equation": "[CH3] + c1ccccc1 <=> CH4 + [c]1ccccc1",
    "reversible": true,
    "family": "h_abstraction",
    "review": { "...RecordReviewBadge": "" }
  },
  "review_summary": { "approved": 2, "under_review": 0, "not_reviewed": 4, "deprecated": 0, "rejected": 0, "total": 6 },
  "species": {
    "reactants": [
      { "species_entry_id": 31, "smiles": "[CH3]", "participant_index": 0, "review": { "...RecordReviewBadge": "" } }
    ],
    "products": [
      { "species_entry_id": 32, "smiles": "CH4",   "participant_index": 0, "review": { "...RecordReviewBadge": "" } }
    ]
  },
  "kinetics": [
    {
      "kinetics_id": 101,
      "scientific_origin": "computed",
      "model_kind": "modified_arrhenius",
      "review": { "...RecordReviewBadge": "" },
      "parameters": { "A": 1.2e-12, "A_units": "cm3_molecule_s", "n": 2.1, "Ea_kj_mol": 15.4 },
      "tunneling_model": "eckart",
      "uncertainty": { "A_uncertainty": null, "A_uncertainty_kind": null, "n_uncertainty": null, "Ea_uncertainty_kj_mol": null },
      "evidence_completeness": { "...EvidenceCompletenessBreakdown": "" },
      "provenance": {
        "transition_state_entry_id": 9,
        "ts_opt_calculation_id": 60,
        "ts_freq_calculation_id": 62,
        "ts_sp_calculation_id": 63,
        "path_search": { "...PathSearchSummary": "" },
        "irc": null,
        "primary_level_of_theory": { "...LevelOfTheorySummary": "" },
        "primary_software": { "...SoftwareReleaseSummary": "" },
        "geometry_validation": { "...ValidationSummary": "" },
        "scf_stability": null,
        "literature": null,
        "software_release": { "...SoftwareReleaseSummary": "" },
        "workflow_tool_release": null
      }
    },
    {
      "kinetics_id": 202,
      "scientific_origin": "experimental",
      "model_kind": "modified_arrhenius",
      "review": { "...RecordReviewBadge": "" },
      "parameters": { "A": 1.0e-12, "A_units": "cm3_molecule_s", "n": 0.0, "Ea_kj_mol": 12.3 },
      "tunneling_model": null,
      "uncertainty": { "A_uncertainty": 1.3, "A_uncertainty_kind": "multiplicative", "n_uncertainty": null, "Ea_uncertainty_kj_mol": 0.6 },
      "evidence_completeness": { "...EvidenceCompletenessBreakdown": "" },
      "provenance": {
        "transition_state_entry_id": null,
        "ts_opt_calculation_id": null,
        "ts_freq_calculation_id": null,
        "ts_sp_calculation_id": null,
        "path_search": null,
        "irc": null,
        "primary_level_of_theory": null,
        "primary_software": null,
        "geometry_validation": null,
        "scf_stability": null,
        "literature": { "id": 77, "title": "Example kinetic study", "year": 1999 },
        "software_release": null,
        "workflow_tool_release": null
      }
    }
  ],
  "transition_states": [
    {
      "transition_state_entry_id": 9,
      "review": { "...RecordReviewBadge": "" },
      "calculations": {
        "ts_opt": { "calculation_id": 60, "type": "opt" },
        "ts_guess": { "calculation_id": 61, "type": "path_search", "method": "gsm" },
        "ts_freq": { "calculation_id": 62, "type": "freq" },
        "ts_sp":   { "calculation_id": 63, "type": "sp" }
      },
      "dependencies": [
        { "parent_calculation_id": 61, "child_calculation_id": 60, "role": "optimized_from" },
        { "parent_calculation_id": 60, "child_calculation_id": 62, "role": "freq_on" },
        { "parent_calculation_id": 60, "child_calculation_id": 63, "role": "single_point_on" }
      ]
    }
  ],
  "calculations": [
    { "...CalculationEvidenceSummary": "" }
  ],
  "path_search": [ { "...PathSearchSummary": "" } ],
  "irc": [],
  "scans": [],
  "conformers": [],
  "artifacts": [],
  "review_records": [
    { "record_type": "kinetics", "record_id": 101, "status": "approved", "reviewed_at": "..." }
  ]
}
```

### Test matrix

1. Unknown `reaction_entry_id` → 404. Passing a `chem_reaction.id` that doesn't match a reaction_entry → 404 (not silent fallthrough).
2. Default response (no `include=`) returns `reaction_entry`, `species`, `kinetics`, `transition_states`, `review_summary` only — no `calculations`, `path_search`, `artifacts`, etc. keys at all.
3. `include=all` populates every legal section, including the empty ones as `[]` (per *Empty-section policy*).
4. `include=calculations` adds the `calculations` key (as `[]` if empty) without adding `path_search` or `artifacts` keys.
5. `review_summary` correctly counts joined records across visible sections.
6. Sub-arrays are deterministically ordered per `(review_rank, created_at DESC, id DESC)`.
7. Unknown `include=` token → 422 with legal-token list.
8. Two identical calls return byte-equal bodies.
9. `include_review=full` adds the top-level `review_records` array; `include_review=summary` (default) omits it.
10. `include_review=full` is rejected (422) on every other endpoint (`unsupported_review_detail`).
11. `min_review_status=approved` removes non-approved records from each populated sub-array but still returns the parent `reaction_entry` and `review_summary`.
12. Client-supplied `sort=` → 422 (`client_sort_not_supported`).
13. **`/full` includes non-TS-backed kinetics without fabricating TS links.** A reaction entry with both a TS-backed computational kinetics record and an experimental kinetics record returns both in `kinetics[]`; the experimental record's TS-chain provenance fields are all `null`; `transition_states[]` contains only the TS rows actually associated with the reaction entry, never invented from the experimental record.
14. **`/full` returns experimental-only kinetics when no TS exists.** A reaction entry whose only kinetics is `scientific_origin=experimental` returns that record in `kinetics[]` and an empty `transition_states: []` (when `include=transition_states` is requested).

---

# Test plan summary

Each endpoint section's test matrix becomes the API-test requirement for that endpoint in Phase 4. Cross-cutting tests:

- `review_summary` consistency across endpoints when querying overlapping data.
- Pagination invariants (`returned <= limit`, `total` stable across pages).
- Default trust posture (rejected/deprecated excluded) honored uniformly.
- Sort determinism for every default sort.
- `include=all` is a no-op idempotent (calling twice returns the same shape).

---

# Open implementation questions (Phase 3)

These are not blockers for spec acceptance — they are notes for the service-layer implementation:

- **OQ1.** Where does `app/services/scientific_read/` live, exactly? Confirm against the existing `app/services/` convention during Phase 3 kickoff.
- **OQ2.** `evidence_completeness.checklist` derivation — some checklist items (e.g., "has SP/energy evidence" for thermo) require joins through `statmech_source` or `calc_dependency`. Phase 3 needs to decide: compute in SQL, in Python, or hybrid. The L1 formulas are stable regardless.
- **OQ3.** `temperature_coverage` for thermo records that use NASA piecewise polynomials — the record's `tmin_k`/`tmax_k` is the union of the piecewise ranges. Confirm this is what the existing schema exposes; if not, document the surface in Phase 3.
- **OQ4.** Caching. None for v0. Identify hot endpoints during Phase 4 testing and revisit.

---

# Appendix: per-endpoint `include=` legal sets

| Endpoint | Legal `include=` tokens | `all` expands to |
|---|---|---|
| `species/search` | `thermo, statmech, transport, conformers, review, all` | all five |
| `reactions/search` | `kinetics, transition_states, species, review, all` | all four |
| `reaction-entries/{id}/kinetics` | `provenance, calculations, transition_states, path_search, irc, review, artifacts, all` | all seven |
| `species-entries/{id}/thermo` | `provenance, calculations, statmech, review, artifacts, all` | all five |
| `reaction-entries/{id}/full` | `species, kinetics, transition_states, calculations, path_search, irc, scans, conformers, artifacts, review, all` | all ten |

Tokens not listed for an endpoint → 422.
