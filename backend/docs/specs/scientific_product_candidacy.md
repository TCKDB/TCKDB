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
| `GET /api/v1/scientific/species-entries/{id}/thermo` | list (paginated); honors `collapse` |
| `GET /api/v1/scientific/species-entries/{id}/statmech` | list (paginated), all candidates |
| `GET /api/v1/scientific/species-entries/{id}/transport` | list (paginated), all candidates |
| `GET /api/v1/scientific/{thermo,statmech,transport}/search` | list (paginated) |
| `GET /api/v1/lookup/{thermo,statmech,transport}` | all matching records |
| `GET /api/v1/{thermo,statmech,transport}` (primitive) | list (paginated) |
| `GET /api/v1/{thermo,statmech,transport}/{id}` | single record by explicit id |

- **Default is non-canonical.** The thermo read request defaults to
  `collapse=all`.
- **The only single-record collapse is explicit and named.** Thermo
  `collapse=first` returns the top-ranked record under a documented,
  deterministic ranking (temperature coverage → extrapolation distance →
  review rank → evidence completeness → recency → id) and echoes the collapse
  mode in the response. `statmech` and `transport` have no collapse mode and
  always return the full candidate list.

Any "this is *the* species thermo" decision is a **read-time selection /
review** concern, not a property of the stored record.

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
