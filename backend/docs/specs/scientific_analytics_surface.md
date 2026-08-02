# The bounded analytics surface

`GET /api/v1/scientific/analytics/{kinetics,thermo,statmech,calculations}`

Status: implemented (Stage 4).
Code: `backend/app/api/routes/scientific/analytics.py`,
`backend/app/services/scientific_read/analytics.py`,
`backend/app/schemas/reads/scientific_analytics.py`.
Tests: `backend/tests/api/scientific/test_api_scientific_analytics.py`.

---

## 1. Why four endpoints and not four hundred filters

Building a quantitative dataset from TCKDB means asking numeric questions:
*every Arrhenius fit valid at 900 K with Ea below 40 kJ/mol*, *every statmech
record with external symmetry 3 and at least two electronic levels*, *every
single-point whose T1 diagnostic exceeds 0.02*.

The obvious way to answer those is to add optional numeric filters to the ~40
existing transactional searches. That was considered and rejected. The
transactional searches answer **identity** questions ("which records belong to
this species / this reaction"), and the numeric filters would have been
optional everywhere, combinable in ways nobody enumerated, and therefore
impossible to index or document: the filters with a supporting index would be
indistinguishable, from the outside, from the filters without one.

So the numeric axes live on their own, on **four** endpoints with a finite set
of query shapes — written down here, and backed by measured index evidence
(§7). The transactional searches are unchanged.

---

## 2. What a record looks like

Analytics records are **flat projections**, not the nested detail records the
transactional searches return. A consumer assembling 60,000 rows into a matrix
wants scalars; the nested shape is right for a human inspecting one record and
wrong here. Each record carries:

* the record's public ref and its owner's public ref,
* every column the endpoint can filter on,
* the derived presence/count columns the endpoint can filter on,
* `review_status` and `created_at`.

Integer ids are present in the model and **stripped from public responses by
default**, exactly as everywhere else on `/scientific/*`; `include=internal_ids`
opts back in when the deployment allows it
(`docs/specs/internal_ids_visibility_policy.md`).

---

## 3. Filters

All filters AND-combine. All are optional — an unfiltered request is a
legitimate "give me the whole table, paged" query and is served, because
ordering and slicing happen in SQL and the page size is bounded.

Range filters are **inclusive on both ends**, independently optional, and never
match a NULL column: "Ea between 10 and 40" must not return records that never
stated an Ea. An inverted range (`*_min` above `*_max`) is a `422` (§6), not an
empty page.

### 3.1 `/analytics/kinetics`

| Filter | Type | Meaning |
|---|---|---|
| `scientific_origin` | enum | `computed` / `experimental` / `estimated` |
| `direction` | enum | stored direction of the fit (DR-0036): `forward` / `reverse` / `net` |
| `model_kind` | enum | `arrhenius`, `modified_arrhenius`, `multi_arrhenius`, `lindemann`, `troe`, `sri`, `plog`, `chebyshev` |
| `tunneling_model` | enum | `none` / `wigner` / `eckart` / `sct` / `other` |
| `pressure_context` | enum | `high_p_limit` / `apparent_at_pressure` / `pressure_dependent` |
| `degeneracy_min`, `degeneracy_max` | float | reaction-path degeneracy |
| `pressure_min_bar`, `pressure_max_bar` | float | `kinetics.pressure_bar` |
| `a_min`, `a_max` | float | Arrhenius A (in the record's own `a_units`; **not** unit-normalised — see the note below) |
| `n_min`, `n_max` | float | Arrhenius temperature exponent |
| `ea_min_kj_mol`, `ea_max_kj_mol` | float | activation energy (fixed unit) |
| `has_uncertainty` | bool | any of `a_uncertainty`, `n_uncertainty`, `ea_uncertainty_kj_mol` present |
| `ea_uncertainty_min_kj_mol`, `ea_uncertainty_max_kj_mol` | float | |
| `temperature_min_k`, `temperature_max_k` | float | **coverage**, see below |
| `has_literature` | bool | `literature_id` present |
| `workflow_tool` | string | workflow tool name via `workflow_tool_release` |
| `has_transition_state_provenance` | bool | a `kinetics_interpretation_assignment` names a `transition_state_entry` |
| `has_statmech_provenance` | bool | any `kinetics_interpretation_assignment` exists (its `statmech_id` is NOT NULL) |

**Temperature is a coverage filter, not an overlap filter.** A record matches
`temperature_min_k=900&temperature_max_k=1100` when its own window contains the
requested one (`tmin_k <= 900` **and** `tmax_k >= 1100`). A record with no
stated window does **not** match: unstated is not unbounded. This matches the
semantics the transactional kinetics reads already use.

**`a_min`/`a_max` are not unit-normalised.** `kinetics.a_units` genuinely varies
by molecularity (`per_s`, `cm3_mol_s`, `cm6_mol2_s`, …), and converting between
them at read time would require knowing the reaction order, which is not on the
row. Filtering A across mixed units is therefore the caller's responsibility;
combine `a_min`/`a_max` with a `model_kind` or `pressure_context` that pins the
order, and read `a_units` off each returned record. This is a documented
limitation, not an oversight.

### 3.2 `/analytics/thermo`

| Filter | Type | Meaning |
|---|---|---|
| `scientific_origin` | enum | |
| `phase` | enum | `gas` / `liquid` / `solid` / `aqueous` |
| `model_kind` | enum | `nasa7` / `nasa9` / `wilhoit` / `tabulated` / `scalar` |
| `reference_pressure_min_bar`, `reference_pressure_max_bar` | float | a range rather than equality, because the two conventions in the wild are 1 bar and 1.01325 bar and float equality on the latter is a trap |
| `h298_min_kj_mol`, `h298_max_kj_mol` | float | |
| `s298_min_j_mol_k`, `s298_max_j_mol_k` | float | |
| `enthalpy_formation_0k_min_kj_mol`, `enthalpy_formation_0k_max_kj_mol` | float | ΔfH°(0 K) |
| `has_uncertainty` | bool | any of the three uncertainty columns present |
| `h298_uncertainty_min_kj_mol`, `h298_uncertainty_max_kj_mol` | float | |
| `has_literature` | bool | |
| `workflow_tool` | string | |
| `has_statmech_provenance` | bool | `thermo.statmech_id` present |

### 3.3 `/analytics/statmech`

| Filter | Type | Meaning |
|---|---|---|
| `scientific_origin` | enum | |
| `external_symmetry` | int ≥ 1 | **exact match**, not a range — a symmetry number is a count with physical meaning, not a magnitude |
| `is_linear` | bool | |
| `point_group` | string | exact match on the stored symbol |
| `statmech_treatment` | enum | `rrho`, `rrho_1d`, `rrho_nd`, `rrho_1d_nd`, `rrho_ad`, `rrao` |
| `rigid_rotor_kind` | enum | `atom`, `linear`, `spherical_top`, `symmetric_top`, `asymmetric_top` |
| `optical_isomers` | int ≥ 1 | exact match, same reasoning as `external_symmetry` |
| `rotational_constant_{a,b,c}_{min,max}_cm1` | float | six filters over the three principal constants |
| `has_frequency_scale_factor` | bool | `frequency_scale_factor_id` present |
| `has_torsions` | bool | any `statmech_torsion` row |
| `has_electronic_levels` | bool | any `statmech_electronic_level` row |
| `electronic_level_count_min`, `electronic_level_count_max` | int ≥ 0 | correlated count |

A linear rotor reports only constant A, so filtering on `rotational_constant_c_*`
excludes it — that is the NULL rule of §3 doing its job, and it is the reading
you want when selecting asymmetric tops.

### 3.4 `/analytics/calculations`

| Filter | Type | Meaning |
|---|---|---|
| `calculation_type` | enum | `opt`, `freq`, `sp`, `irc`, `scan`, `path_search`, `conf` |
| `electronic_energy_min_hartree`, `electronic_energy_max_hartree` | float | `calc_sp_result.electronic_energy_hartree` |
| `zpe_min_hartree`, `zpe_max_hartree` | float | `calc_freq_result.zpe_hartree` |
| `n_imag` | int ≥ 0 | exact imaginary-mode count |
| `converged` | bool | `calc_opt_result.converged` |
| `t1_min`, `t1_max` | float | `calc_wavefunction_diagnostic.t1_diagnostic` |
| `d1_min`, `d1_max` | float | `calc_wavefunction_diagnostic.d1_diagnostic` |
| `s_squared_min`, `s_squared_max` | float | `calc_spin_diagnostic.s_squared` |
| `method`, `basis`, `lot_ref` | string | via `level_of_theory` |
| `software` | string | via `software_release` → `software` |

**Supplying one of these numeric axes is also a presence filter.** The values
live on per-type result and diagnostic tables, and the join added for a
requested axis is an *inner* join — so a calculation with no frequency result
cannot match `zpe_min_hartree`, and one whose ESS never printed T1 cannot match
`t1_max`. For dataset construction that is the intended reading ("give me the
rows that actually have this number"), and it is stated here rather than left
to be discovered.

### 3.5 Review knobs (all four endpoints)

`min_review_status`, `include_rejected`, `include_deprecated` — identical
semantics to the rest of `/scientific/*`, resolved through the shared
`visible_statuses()` helper.

---

## 4. The read profile

`?profile=curated` narrows the result set and is echoed back. Neither is
implemented per endpoint; both come from the two seams
`app/services/scientific_read/profile.py` documents:

1. **Narrowing.** The service calls
   `app.services.scientific_read.common.visible_statuses()`, which raises the
   floor to `approved` under the curated profile. There is no other place the
   analytics services decide what is visible, so the floor cannot be forgotten.
2. **Echoing.** Every route returns through
   `app.services.scientific_read.internal_ids.apply_internal_ids_visibility()`,
   which calls `stamp_read_profile()`. `AnalyticsRequestEcho` subclasses
   `ProfiledRequestEcho`, so `profile`, `profile_recommendation` and
   `profile_release_ref` are part of the published OpenAPI shape too.

`?release=` is rejected with `422 release_scoping_not_implemented` here as
everywhere else on the general read surface. See
`backend/docs/specs/dataset_release_and_profiles.md`.

---

## 5. Pagination

### 5.1 Sort

One sort, on all four endpoints, not client-selectable:

```
review_rank ASC, id DESC
```

`review_rank` is the SQL mirror of `REVIEW_RANK` (approved first). The trailing
key is the primary key, which makes the order **total** — that is what makes it
keyset-safe. `sort=` is rejected with `422 client_sort_not_supported`, per the
v0 sort policy.

### 5.2 Offset (default, unchanged)

`offset` / `limit` behave exactly as on the other scientific searches.
`pagination.total` is the post-filter, post-visibility count of the whole
matching set. `request.pagination_mode` reports `"offset"`.

### 5.3 Keyset (opt-in)

Every response carries `next_cursor`. It is non-null whenever the page came
back full (`returned == limit`); a short page means the traversal is complete.
Passing it as `?cursor=` returns the next page and reports
`request.pagination_mode == "cursor"`.

The contract:

* **A cursor is bound to its query.** It carries a signature over the endpoint,
  the filter echo, the review knobs, and the resolved read profile. Handing it
  to a different endpoint, the same endpoint with different filters, or the
  same query under a different profile is `422 cursor_query_mismatch` — not a
  quietly wrong page.
* **`cursor` and a non-zero `offset` cannot be combined**
  (`422 cursor_offset_conflict`). A cursor already encodes the position;
  accepting an offset alongside it and ignoring one of them is exactly the kind
  of silent wrongness keyset paging exists to remove.
* **`limit` may change between pages.** It is not part of the signature.
* Cursors are opaque, versioned tokens. An unparseable, truncated or
  wrong-version token is `422 invalid_cursor`.

### 5.4 The watermark — what it guarantees, and what it does not

A traversal takes a snapshot bound when it begins: the highest record id that
existed at that moment. Every page — including the first, and including the
`total` count — re-applies `id <= watermark`.

**Guaranteed.** Record ids are monotonic, so the watermark is a complete bound
on **insertions**. Uploading 10,000 kinetics records while a traversal is
running cannot inject a row into it, cannot shift the reader's position, and
cannot change `pagination.total` between pages. Combined with keyset ordering,
a traversal visits every matching record exactly once, in a stable order.

**Not guaranteed.** The watermark does **not** freeze curation. `record_review`
rows are mutable: a record approved or deprecated mid-traversal can still enter
or leave the visible set, and — because `review_rank` is the leading sort key —
can move relative to the reader's position. Freezing that would require reading
review state as-of a timestamp, which this path does not support.

`watermark.taken_at` is echoed for the reader's own provenance record.
`watermark.max_id` is deliberately **not** echoed: it is an internal record id,
hidden by the same policy that hides every other one, and the authoritative
copy travels inside the opaque cursor.

**If you need an immutable, citable set, do not use this.** Read a published
dataset release's frozen `selected_records` artifact, which Stage 3 guarantees
is byte-stable (`backend/docs/specs/dataset_release_and_profiles.md`). Keyset
traversal makes a *live* read far more reproducible than offset paging; it does
not make it immutable, and must not be cited as if it did.

### 5.5 `total` on keyset pages

Keyset pages still report the full filtered `total`, recomputed per page under
the same watermark (so it is stable across the traversal). That costs one
aggregate per page. It is kept because a consumer building a dataset needs to
know how much is left, and because dropping it would make the offset and cursor
contracts disagree about the same field.

---

## 6. Error contract

All errors follow the existing coded envelope
(`{"code": ..., "detail": ..., "context": {...}}`, `app/api/error_contract.py`).
Every one below is `422`.

| Code | Cause |
|---|---|
| `invalid_range` | a `*_min` above its `*_max`. `context` carries `min_filter`, `max_filter`, `min`, `max` — the two *actual* parameter names, not a derived stem, so the message never points at a parameter that does not exist. An empty page would read as "TCKDB has no such data"; a typo should not be able to say that. |
| `invalid_temperature_range` | `temperature_min_k` above `temperature_max_k` (shared helper) |
| `client_sort_not_supported` | any `sort=` value |
| `invalid_pagination` | `offset < 0`, `limit < 1`, or `limit` / `offset` above the hosted caps |
| `unknown_include_token` | any `include=` token other than `internal_ids` / `all` |
| `cursor_offset_conflict` | `cursor=` supplied together with a non-zero `offset=` |
| `invalid_cursor` | malformed, truncated, or wrong-version cursor |
| `cursor_query_mismatch` | cursor replayed against a different endpoint, filter set, or read profile |
| `release_scoping_not_implemented` | `?release=` supplied (router-level) |

---

## 7. Index evidence

**Rule applied: no speculative indexes.** Nine candidates were measured against
the Stage 4 benchmark corpus `tckdb_bench_s4` (50,000 species, 418,182
calculations, 70,000 statmech, 60,000 thermo, 13,636 kinetics, 386,514
`record_review`). Each was created, measured, and dropped in isolation. **Two
were kept. Seven were rejected** and are recorded below so nobody re-derives
that they do not help.

Method: for each query shape, the exact SQL the service issues was captured and
run under `EXPLAIN (ANALYZE, BUFFERS)`; the figure quoted is the **median
execution time of 7 runs**, after `ANALYZE`. Both the aggregate (`count`) and
the ordered page (`page`) statement were measured. The candidate indexes were
created under `cand_*` names during benchmarking; the plan excerpts below quote
those names verbatim.

### 7.1 Kept

#### `ix_record_review_record_lookup` — `record_review (record_type, record_id) INCLUDE (status)`

Every scientific read joins `record_review` on exactly this key and then reads
`status`. The existing `uq_record_review_record` unique constraint indexes the
key but not the payload, so the join visited the heap once per candidate.

*Before* (thermo `h298` range, 7,556 candidates — page statement):

```
Limit  (actual time=19.619..19.623 rows=50 loops=1)
  Buffers: shared hit=61310 read=292
  ->  Sort   Sort Method: top-N heapsort  Memory: 28kB
        ->  Merge Right Join  (actual time=3.387..18.976 rows=7098 loops=1)
              ->  Index Scan using uq_record_review_record on record_review
                    Index Cond: (record_type = 'thermo')
                    Buffers: shared hit=59899 read=292          <-- heap visits
              ->  Sort -> Seq Scan on thermo  (rows=7556)
                    Buffers: shared hit=1408
Execution Time: 19.676 ms
```

*After*:

```
Limit  (actual time=9.496..9.499 rows=50 loops=1)
  Buffers: shared hit=1711
  ->  Sort   Sort Method: top-N heapsort  Memory: 28kB
        ->  Merge Left Join  (actual time=3.375..8.887 rows=7098 loops=1)
              ->  Sort -> Seq Scan on thermo  (rows=7556)
                    Buffers: shared hit=1408
              ->  Index Only Scan using cand_rr_cover on record_review
                    Index Cond: (record_type = 'thermo')
                    Heap Fetches: 0
                    Buffers: shared hit=300                     <-- 200x fewer
Execution Time: 9.541 ms
```

**61,602 → 1,711 shared buffers, 19.6 ms → 9.5 ms, `Heap Fetches: 0`.**

The same effect on the calculation side: the `record_review` input to the
119,701-candidate page query changed from a 3,904-buffer `Parallel Seq Scan`
to an 805-buffer `Parallel Index Only Scan`.

#### `ix_calculation_type_id` — `calculation (type, id)`

The analytics filter pass selects only `calculation.id` for a given `type`, and
is always bounded by `id <= watermark`. Both columns are in the index, so the
scan is index-only.

*Before* (`calculation_type=sp`, 119,701 candidates — page statement; this is
the shape that used to return `503` under the pre-Stage-4 Python path):

```
Limit  (actual time=36.410..37.348 rows=50 loops=1)
  Buffers: shared hit=5652 read=4970
  ->  Gather Merge -> Sort (top-N heapsort)
        ->  Parallel Hash Right Join  (actual time=10.647..24.449 rows=38980 loops=3)
              ->  Parallel Seq Scan on record_review
                    Filter: (record_type = 'calculation')
                    Rows Removed by Filter: 74848
                    Buffers: shared hit=256 read=3648
              ->  Parallel Hash
                    ->  Parallel Seq Scan on calculation
                          Filter: ((id <= 418182) AND (type = 'sp'))
                          Rows Removed by Filter: 99494
                          Buffers: shared hit=5206 read=1322
Execution Time: 37.384 ms
```

*After* (both kept indexes present):

```
Limit  (actual time=28.638..28.716 rows=50 loops=1)
  Buffers: shared hit=1398 read=462
  ->  Gather Merge -> Sort (top-N heapsort)
        ->  Parallel Hash Right Join  (actual time=6.847..17.073 rows=38980 loops=3)
              ->  Parallel Index Only Scan using cand_rr_cover on record_review
                    Index Cond: (record_type = 'calculation')
                    Heap Fetches: 0
                    Buffers: shared hit=805
              ->  Parallel Hash
                    ->  Parallel Index Only Scan Backward using cand_calc_type_id on calculation
                          Index Cond: ((type = 'sp') AND (id <= 418182))
                          Heap Fetches: 0
                          Buffers: shared hit=463 read=462
Execution Time: 28.751 ms
```

**10,622 → 1,860 shared buffers, 37.4 ms → 28.8 ms.** Measured alone (without
the `record_review` index) it is a smaller but still real win: 35.4 → 32.1 ms
page, with the plan changing from `Parallel Seq Scan` to
`Parallel Index Only Scan`.

### 7.2 Full measurement table

Median of 7 `EXPLAIN (ANALYZE, BUFFERS)` runs, milliseconds. "After" = both
kept indexes present.

| Query shape (candidates) | count before | count after | page before | page after |
|---|---:|---:|---:|---:|
| `calculations?calculation_type=sp` (119,701) | 30.58 | **24.91** | 35.44 | **28.79** |
| `calculations?n_imag=1` (10,695) | 23.98 | 24.14 | 23.40 | 24.56 |
| `calculations?t1_min=..&t1_max=..` (20,717) | 28.63 | 28.98 | 31.99 | **28.81** |
| `thermo?h298_min..max` (7,556) | 18.37 | **9.88** | 19.65 | **9.68** |
| `thermo?phase=gas` (29,986) | 11.44 | *16.15* | 11.47 | **9.76** |
| `statmech?external_symmetry=3` (5,843) | 10.66 | **9.87** | 10.56 | 10.17 |
| `statmech?point_group=C2v` (7,619) | 10.25 | *10.94* | 10.62 | *11.74* |
| `kinetics?ea_min..max` (980) | 3.96 | **2.05** | 4.63 | **2.22** |
| `kinetics?scientific_origin=computed` (8,128) | 6.08 | **3.36** | 6.37 | **3.98** |

Two shapes regressed and are reported rather than hidden: `thermo?phase=gas`
count (11.4 → 16.1 ms) and `statmech?point_group=` (≈ +0.7 / +1.1 ms). Both
are planner *choices* — with the covering index available PostgreSQL switches
`thermo?phase=gas` to a merge join driven by `pk_thermo` — rather than index
defects, both are small in absolute terms, and both sit next to 10 ms and 6 ms
wins elsewhere. Isolated measurement confirmed the `statmech` regression comes
from the `record_review` index, not the `calculation` one.

### 7.3 Rejected candidates

Each was created alone, measured over 7 runs, and dropped. None is in the
migration.

| Candidate | Shape measured | Result | Verdict |
|---|---|---|---|
| `calc_freq_result (n_imag)` | `calculations?n_imag=1` | 23.98 → 24.75 count, 23.40 → 24.46 page | rejected — no gain; plan changed to a bitmap scan on an already-cheap 59,669-row table |
| `calc_wavefunction_diagnostic (t1_diagnostic)` | `calculations?t1_min..max` | 28.63 → 30.24 count, 31.99 → 30.57 page | rejected — within noise |
| `thermo (h298_kj_mol)` | `thermo?h298_min..max` | 18.37 → 18.32 count, 19.65 → 18.90 page | rejected — within noise; the cost was the review join, not the scan |
| `thermo (phase)` | `thermo?phase=gas` | 11.44 → 17.43 count | rejected — measurably worse |
| `statmech (external_symmetry)` | `statmech?external_symmetry=3` | 10.66 → 18.28 count, 10.56 → 20.04 page | rejected — much worse |
| `statmech (point_group)` | `statmech?point_group=C2v` | 10.25 → 11.57 count, 10.62 → 10.58 page | rejected — no gain |
| `kinetics (scientific_origin)` | `kinetics?scientific_origin=computed` | 6.08 → 5.88 count, 6.37 → 6.35 page | rejected — within noise |
| `kinetics (ea_kj_mol)` | `kinetics?ea_min..max` | 3.96 → 3.58 count, 4.63 → 3.43 page | rejected — a real but sub-millisecond gain on a 13,636-row table; the same shape gains 2 ms from the `record_review` index alone |

The pattern is consistent and worth stating: on this corpus the per-column
filter scans were never the bottleneck. The review join and the sort were.
That is why the one cross-cutting index (`record_review`) earns its keep while
seven per-column indexes do not.

### 7.4 Reproducing

```bash
PGPASSWORD=tckdb psql -h 127.0.0.1 -U tckdb -d tckdb_bench_s4
```

The measured statements are the exact SQL the services issue: the candidate
`SELECT count(*)` over the visible-candidate subquery, and the ordered/sliced
page `SELECT`. `tckdb_bench_s4` is disposable; indexes may be created and
dropped on it freely.

---

## 8. Why the query runs entirely in SQL

Filtering, review visibility, review ranking, ordering and slicing are all
expressed as SQL over one candidate subquery; only the page is materialized
(a second, bounded projection query over at most `limit` ids).

This is a correctness requirement, not a preference. The pattern it replaces —
load candidate ids, call `fetch_review_badges`, sort and slice in Python —
renders **one bind parameter per candidate id**, and PostgreSQL caps a
statement at 65,535 parameters. A broad analytics query would not have been
slow; it would have raised `OperationalError` and returned
`503 database_unavailable`. `calculation_type=sp` matches 119,701 rows on this
corpus and failed exactly that way.

The helpers that make this expressible are
`app/services/scientific_read/sql_review.py` (`join_review`,
`review_rank_expr`, `review_status_expr`, `visible_review_filter`). The
`review_summary` block is a SQL `GROUP BY` over the whole filtered set, not a
count of the page's badges — it describes what the filters matched, and
materializing the candidate set to count it would give back the property this
surface exists to have.

---

## 9. Deliberate omissions

* **No POST twin.** The chemistry-first searches carry one because
  reactant/product SMILES lists are awkward in a query string. Analytics filters
  are scalars and cursors are short opaque tokens, so a body form would add
  surface without answering a question.
* **No `collapse=`.** Collapse answers "show me one product for this species
  form"; a dataset query wants all of them.
* **No aggregate/group-by endpoint** (histograms, per-level-of-theory means).
  That is a different contract — it returns statistics rather than records, so
  review visibility, the profile echo and citation all mean something different
  — and it should be designed as such rather than bolted on here.
* **No unit normalisation of `a`** — see §3.1.
* **No cross-endpoint join** (e.g. "kinetics whose statmech has symmetry 3").
  Four independent endpoints keep the query shapes finite, which is the whole
  point; a join surface would reintroduce the combinatorics this design refuses.

---

## 10. Landing note

Adding four routes changes the published OpenAPI document, so the golden
snapshot at `backend/tests/api/golden/openapi.json` no longer matches and
`tests/api/test_openapi_snapshot.py::test_openapi_matches_golden` fails until
it is regenerated. That regeneration is a deliberate, reviewable step (it is
how a route addition becomes visible in review) and belongs to whoever lands
this change, not to the change itself.
