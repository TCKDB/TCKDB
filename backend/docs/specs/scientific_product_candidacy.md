# Scientific Product Candidacy & Selection Semantics

Status: current behavior (audit-confirmed). Scope: `thermo`, `statmech`,
`transport` attached to `species_entry`.

## Summary

`thermo`, `statmech`, and `transport` are **candidate interpreted products for
a chemically resolved molecular form** (`species_entry`). They are not facts
owned by a single conformer, and no single record is canonical without an
explicit, named selection/review policy applied at read time.

## Where products attach, and why

- **Ownership: `species_entry`.** A species-level property — H°(298), S°,
  Cp(T), the molecular partition function, Lennard-Jones parameters — is an
  *ensemble* result produced by statistical mechanics over the conformational
  ensemble. It therefore lives at the resolved-form level, above the
  conformers, where it is scientifically meaningful.
- **Evidence: `conformer_observation`.** The conformer-specific calculations
  that supported a product are preserved through the product's
  `*_source_calculation` links:

  ```text
  thermo    → thermo_source_calculation    → calculation → conformer_observation
  statmech  → statmech_source_calculation  → calculation → conformer_observation
  transport → transport_source_calculation → calculation → conformer_observation
  ```

  Conformer provenance is a backward link, never ownership. There is
  deliberately no `conformer_observation_id` column on the product tables.

## Multiple candidate products may coexist

Product tables are **append-only result tables**. There is no uniqueness
constraint collapsing them and no silent overwrite. Two uploads for the same
`species_entry` produce two distinct rows — whether they come from two
near-identical conformers (e.g. torsions differing by a degree), two levels of
theory, or computed vs. experimental sources.

The axis of multiplicity is **provenance**, not "one row per conformer":

- `scientific_origin` (computed / experimental / estimated)
- level of theory (via source calculations)
- `software_release` / `workflow_tool_release`
- `literature`
- uploader (`created_by`)

Protected by:

- `backend/tests/invariants/test_thermo_invariants.py::test_repeated_thermo_uploads_for_same_species_append_not_overwrite`
- `backend/tests/services/test_model_constraints.py::test_thermo_allows_multiple_records_per_species_entry`
- `backend/tests/workflows/test_thermo_upload.py::test_repeated_thermo_uploads_are_append_only`
- `backend/tests/api/test_api_lookup_expansion.py` (multiple statmech records all surface)

## No stored canonical flag

There is **no `is_preferred` / `is_selected` / `is_canonical` / `is_default`
column** on `thermo`, `statmech`, or `transport`. This follows the result-table
rule: identity tables dedupe; result tables stay append-only and carry no
preferred/selected semantics inline.

Selection that exists today is **conformer-level only** — `ConformerSelection`
(`ConformerSelectionKind`, including `preferred_for_thermo` /
`preferred_for_kinetics`) elects *which conformer* is preferred for deriving a
product. It does **not** elect one product record over another.

Product-level curated selection (a curator pinning a benchmark or
display-default product among coexisting candidates) is **not implemented**.
Clients must not assume one product record is authoritative.

## Read contract

Read paths return candidates, and collapse to one only under explicit policy:

| Path | Returns |
|------|---------|
| `GET /api/v1/scientific/species-entries/{id}/thermo` | list (paginated); honors `collapse` + `selection_policy` |
| `GET /api/v1/scientific/species-entries/{id}/statmech` | list (paginated); honors `collapse` + `selection_policy` |
| `GET /api/v1/scientific/species-entries/{id}/transport` | list (paginated); honors `collapse` + `selection_policy` |
| `GET /api/v1/scientific/{thermo,statmech,transport}/search` | list (paginated); broad search returns all candidates |
| `GET /api/v1/lookup/{thermo,statmech,transport}` | all matching records |
| `GET /api/v1/{thermo,statmech,transport}` (primitive) | list (paginated) |
| `GET /api/v1/{thermo,statmech,transport}/{id}` | single record by explicit id |

- **Default is non-canonical.** All three per-species reads default to
  `collapse=all` + `selection_policy=default`, returning every candidate.
- **Single-record collapse is explicit and named.** `collapse=first` returns
  exactly one record, chosen by an explicit, named `selection_policy`. The
  chosen `collapse` and `selection_policy` are echoed in the response; the
  pre-collapse candidate count stays in `pagination.total`.

### Named selection policies

`selection_policy` (enum, defined in
`app/schemas/reads/scientific_common.py::SelectionPolicy`) governs *only* which
single record `collapse=first` returns — it never reorders the full
`collapse=all` candidate list, and it never persists a choice:

| Policy | Selects |
|--------|---------|
| `default` | the endpoint's standard ranking — for thermo: temperature coverage → extrapolation distance → review rank → evidence completeness → recency → id; for statmech/transport: review rank → recency → id |
| `latest` | the most recently created candidate (recency → id) |
| `most_reviewed` | best review status first, then recency → id |

Policies that would require a *stored* curator decision (e.g.
`benchmark_reference`, `curator_pick`) are intentionally **absent**: they cannot
be evaluated from record data alone and need the deferred product-selection
persistence layer, not a read knob.

Any "this is *the* species thermo" decision is therefore a **read-time
selection** concern (an explicit, named, non-persisted policy), not a property
of the stored record.

## Implementation note: deterministic provenance fallback

When a `thermo` record declares no `ThermoSourceCalculation` rows of its own,
the read service borrows source calcs from a statmech on the same
`species_entry` to populate provenance/evidence display (freq / SP / LoT /
software). When several statmech records coexist, this borrow now picks the
**lowest statmech id deterministically** (previously `next(iter(set))`, which
depended on set-iteration order). This is a reproducibility guarantee for the
provenance *display* only — it does not designate a canonical statmech or
thermo. See `backend/app/services/scientific_read/thermo.py`
(`get_species_thermo`, `_build_provenance`) and
`backend/tests/services/scientific_read/test_get_species_thermo.py::test_statmech_fallback_pick_is_deterministic_with_multiple_statmech`.

## Open design question

Whether to add an explicit **product-level** curated-selection mechanism (e.g. a
`species_product_selection` table keyed by `(species_entry_id, product_type,
product_id, selection_kind, selection_policy)`, or read-time-only named
policies) is deferred pending a concrete consumer (curation UI / review
workflow). It is intentionally not built here, to avoid introducing
preferred/selected semantics into the deployed schema without a driver.
