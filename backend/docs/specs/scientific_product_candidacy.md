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

Product-level curated selection is now implemented — but **as an overlay, not
as a column**. A curator's pick lives in `release_selection`, an append-only
curation table that names the policy version, the curator, the candidate, the
rationale and the release, and never writes to `thermo` / `statmech` /
`transport` / `kinetics`. See
[`dataset_release_and_profiles.md`](dataset_release_and_profiles.md).

Clients must still not assume one product record is authoritative *from the
record alone*. Authority is a property of a published dataset release, is
addressed through `/api/v1/scientific/releases/*` or `?profile=curated`, and is
always accompanied by the full candidate set it was chosen from.

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
  pre-collapse candidate count stays in `pagination.total`, while
  `pagination.post_collapse_total` reports the count after selection collapse
  and before offset/limit slicing.

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

## Resolved: product-level curated selection (Stage 3)

The open question above — whether to add an explicit product-level
curated-selection mechanism — is **resolved**. The driver arrived: a community
user needs to be able to cite "the TCKDB value", and a deterministic read-time
heuristic is not an expert recommendation.

The shape chosen is *not* the `species_product_selection` table sketched here.
Keying a selection on `(species_entry_id, product_type, product_id, ...)` with
mutable rows would have reintroduced preferred/selected semantics as editable
state. Instead:

- selections live in `release_selection`, scoped to a **named, versioned
  dataset release**, so "the TCKDB value" is always "the TCKDB value *as of
  release 2026.07.0*" — a citable, unchanging claim rather than a moving one;
- rows are **append-only** (database trigger + UNIQUE supersession chain);
  revising a decision inserts a row and the previous rationale stays readable;
- the target is addressed by the same loose `(record_type, record_id)` pointer
  `record_review` uses, so no product table gains a column;
- every release publishes the candidates *and* the review history behind its
  selections, so the recommendation can be checked and disputed without
  privileged access.

The read-time `SelectionPolicy` enum is unchanged and still persists nothing.
`benchmark_reference` / `curator_pick` remain absent from it, for the same
reason as before: they are stored curator decisions, and they are now expressed
as release selections rather than as read knobs.

Full contract: [`dataset_release_and_profiles.md`](dataset_release_and_profiles.md).

## Read profiles

The per-species read contract now also depends on `?profile=`:

- `profile=exploratory` (**default**) — unchanged from everything above: all
  candidates, no TCKDB recommendation, explicitly labelled as such in the
  response's `request.profile_recommendation`.
- `profile=curated` — the review floor rises to `approved`, and the response
  reports the dataset release backing it (or honestly reports
  `recommendation: none` when no release has been published yet).

The resolved profile is echoed in every scientific response and every dataset
manifest.
