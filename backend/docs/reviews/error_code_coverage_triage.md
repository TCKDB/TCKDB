# The 53 catalogued codes no test has ever produced

`backend/app/api/code_catalogue.py` enumerates every code the API can put in
the `code` field of an error body. Instrumenting all three PR gates shows that
**91 of its 144 distinct codes are produced by the suite and 53 are not.**

A code no test reaches is one of four things, and the catalogue cannot tell
them apart: a refusal nobody has verified, a guard that only fires on a
backend bug, a branch that can no longer fire at all, or something nobody has
established. They need opposite fixes. This document classifies all 53.

It is an *analysis*. No production file is changed by the PR that carries it.

---

## Lead: four things that change the picture

These matter more than the classification, and three of them are about codes
the suite *does* exercise.

### 1. `invalid_pagination` never reaches a client on two of its four paths, and four tests are named after a code they cannot produce

> **Resolved in #170.** The two messages now carry one token each, and the
> second token became the code: `limit_too_large` and `offset_too_large` are
> catalogued and exported, `invalid_pagination` keeps the two malformed
> cases. The four tests provoke the service and assert the code. The
> ambiguity scan below landed with them, green. One correction to the
> account: three of the four tests were named `test_rejects_invalid_pagination`;
> the fourth (`test_api_species_search.py`) was
> `test_get_invalid_pagination_limit_rejected`, and its comment already said
> the framework caught the request first.

`backend/app/services/scientific_read/common.py` raises it four ways. Two
carry a second `snake_case: ` token inside the message:

```
invalid_pagination: limit_too_large: limit must be <= 200 (got 100000)
invalid_pagination: offset_too_large: offset must be <= 10000 (got 999999)
```

`validation_detail_code` judges ambiguity on the *unfiltered* candidate set
(`app/api/error_contract.py:383`), so two tokens means no promotion and the
client receives `code="validation_error"`. Measured on the wire, all four:

| site | raised for | wire `code` |
|---|---|---|
| `common.py:75` | `offset < 0` | `invalid_pagination` |
| `common.py:77` | `limit < 1` | `invalid_pagination` |
| `common.py:81` | `limit > cap` | **`validation_error`** |
| `common.py:86` | `offset > cap` | **`validation_error`** |

Now the part that hides it. Four tests are called
`test_rejects_invalid_pagination` — `tests/api/scientific/test_api_species_thermo.py:74`,
`…_species_search.py:124`, `…_species_transport.py:89`,
`…_species_statmech.py:89` — and every one of them issues `GET …?limit=999`.
The GET routes declare `limit: int = Query(50, ge=1, le=200)`
(`api/routes/scientific/thermo.py:58` and eight siblings), so FastAPI rejects
the request before the service is called:

```
GET …/thermo?limit=999    -> 422 code=request_validation_error   (le=200, framework)
GET …/thermo?offset=999999 -> 422 code=validation_error          (service, degraded)
POST …/search {"limit": 100000} -> 422 code=validation_error     (service, degraded)
```

So the four tests named after the code exercise the framework's constraint,
not the code; they assert only `status_code == 422`, which is true either way.
`offset` carries no `le`, so the degraded path is reachable on an ordinary
GET.

`backend/tests/api/test_error_contract_coded_promotion.py:194` pins the
two-token fall-back as correct behaviour — correctly, as a rule about
ambiguity — without noticing that the catalogue lists `invalid_pagination` as
a `message_prefix` code and `clients/python/src/tckdb_client/rejection_codes.py`
exports it for clients to branch on.

A static scan finds the whole class and it is exactly two sites, both this
code (scan in "Method" below). The repair is a message reword
(`limit_too_large: ` → `limit too large, `), not a rule change.

### 2. Three of the five ownership codes are guards that check a value they just set

> **Two of three, corrected in #184.** The rule below is incomplete, and
> `statmech_torsion_scan_calculation_owner_mismatch` is the code it gets
> wrong. A guard is reachable when the field it guards can name a
> calculation the enclosing block did not itself scope to the target —
> which a foreign row id achieves, and so does a **key resolved in a
> namespace wider than the target's owner**. The second clause is the one
> missing here, and it is not hypothetical: this analysis read call
> *sites* rather than the callers of the function containing them, and
> `_persist_statmech_block` has two production callers in
> `workflows/network_pdep.py` besides the one in `computed_species.py`.
> Both hand it a map spanning every species and every transition state in
> the bundle. The PDep payload schema narrows a *species* statmech's
> source and torsion keys to that species's own calculations first; it
> does not do the same for a *transition state*'s. So a TS torsion naming
> a species-owned scan calculation reaches the guard, and
> `POST /api/v1/uploads/networks/pdep` returns
> `422 statmech_torsion_scan_calculation_owner_mismatch` — measured, and
> pinned by `tests/api/test_api_network_pdep_ownership.py`. That code
> stays in the client enum. The other two were removed from it in #184
> and keep annotated catalogue entries.
>
> Two further consequences of applying the corrected rule, neither fixed
> in #184 (both live in files that change hands elsewhere): the reaction
> bundle enforces the same ownership rule with four inline comparisons
> that raise a bare `ValueError`
> (`workflows/computed_reaction.py:807`, `:847`, `:1095`) instead of
> calling the shared guard, so the same mistake there answers
> `validation_error`; and a species torsion's `source_scan_calculation_key`
> in that bundle (`workflows/computed_reaction.py:1128`) is checked for
> neither ownership nor anything else, so a torsion may cite a sibling
> species's or the TS's scan calculation and be persisted silently.
>
> **Both resolved.** #193 routed the torsion's scan key through the shared
> guard; #195 did the same for the inline comparisons (three, not four —
> the count above was measured before #174 and #177 moved the file, and
> re-deriving the anchors on `main` found three). Two of the three now
> report `applied_energy_correction_source_calculation_owner_mismatch`,
> which is what took that entry out of `Reach.guard` and back into the
> client enum, and the third reports the
> `statmech_source_calculation_owner_mismatch` its three sibling write
> paths already did. Provoked on the wire in
> `tests/api/test_api_bundle_ownership_codes.py`.

`assert_calculation_owned_by` takes its code as a parameter and has five.
Two are emitted by the suite; three are not — and the three are unreachable by
construction, for one reason:

> An ownership guard is reachable exactly when the field it guards accepts a
> **foreign row id**.

`thermo_source_calculation_owner_mismatch` and
`statmech_source_calculation_owner_mismatch` are reachable because
`source_calculations[].existing_calculation_id` lets a depositor cite an
arbitrary calculation (`workflows/thermo.py:159`, `services/statmech_resolution.py:169`).
The other three have no such field. Every one of their call sites reads a
calculation out of a map built moments earlier by
`resolve_and_persist_calculation_with_results(..., species_entry_id=X)` and
then asserts it is owned by `X`:

```python
# backend/app/workflows/transport.py:66-78
calc_row = resolve_and_persist_calculation_with_results(
    session, calc_in.calculation, species_entry_id=species_entry.id, ...)
assert_calculation_owned_by(
    calc_row, code=W_TRANSPORT_SOURCE_CALCULATION_OWNER_MISMATCH,
    species_entry_id=species_entry.id, ...)
```

The comments say so in as many words ("owner-consistency is enforced by
construction but also double-checked below to guard any future path that
reuses existing calculations"). That is a legitimate forward guard and I am
not proposing removal — but all three are exported to the client's
`RejectionCode` enum as refusals a caller can branch on, and the API cannot
currently produce any of them. This is the #165 shape with a different cause:
not "checked but never routed", but "checked against a value the same function
assigned".

### 3. `idempotency_in_progress` is a dead ternary branch, and the code says why

> **Half-corrected in #186.** The branch is dead, as stated. But
> `app/api/idempotency.py` did *not* say why in the sense that matters: it
> said "for v0", which reads as a milestone not yet reached, and the
> distinction between a deferral and a decision is the one a client author
> needs. The spec settles it — `idempotency_in_progress` is listed under
> *Optional*, "Only implement `idempotency_in_progress` if needed by the
> chosen approach", and the approach chosen does not need it. It is a
> contingency. The comment now says so, the catalogue entry is
> `Reach.guard`, and the member is gone from the client enum; the ternary
> and the `in_progress` parameter stay, because they are what make the
> state sayable if an advisory lock is ever added.

`app/api/errors.py:444` picks the code off `exc.in_progress`.
`IdempotencyConflict.__init__` defaults it to `False`
(`services/idempotency.py:77`) and **the one construction site never passes
it** (`services/idempotency.py:196`). `app/api/idempotency.py:12` documents the
reason: concurrent in-flight duplicates are deliberately not protected in v0.
So the branch cannot fire, the catalogue lists the code as one the API can
emit, and `clients/python/src/tckdb_client/rejection_codes.py:108` exports it.

### 4. Two catalogue entries have the wrong HTTP status, and nothing can catch that

> **Resolved in #170.** Both entries are 409, the observer compares
> `(status, code)`, and both codes are now provoked on the wire — which was
> the necessary third part: measured across the three gates, the 91 codes the
> suite emits agreed with the catalogue at every status, so widening the
> comparison alone turned nothing red. Both wrong entries were among the 52
> codes no test produced.

Measured on the wire:

| code | catalogue says | actually arrives at |
|---|---|---|
| `curation_policy_version_conflict` | 422 | **409** (`routes/releases_admin.py:152`) |
| `release_tag_taken` | 422 | **409** (`routes/releases_admin.py:200`) |

The runtime observer only checks that an emitted *code* is catalogued; it
never compares the status. `ApiCode.status` is documented as the thing a
client reads retry advice off, and for these two it is wrong. Both are in the
unobserved 53, which is why nobody noticed — but the same blind spot would
hide a wrong status on an *observed* code too.

---

## Counts

| classification | count |
|---|---|
| **UNTESTED REFUSAL** — reachable through a real request, nobody has provoked it | **33** |
| **BACKEND-BUG GUARD** — only fires if backend code is wrong; legitimate to leave untested | **10** |
| **UNREACHABLE** — no request can produce it | **10** |
| **UNCLEAR** | **0** |
| total | **53** |

Twenty-two of the 33 untested refusals were **provoked on the wire** by
throwaway probes during this triage (marked *provoked* below), so those
verdicts are measurements, not readings.

---

## Method

Four passes, three of them instrumented, deliberately able to disagree.

**1. Runtime census (instrumented).** A pytest plugin patches
`starlette.responses.JSONResponse.__init__` and records every `(status, code)`
of every 4xx/5xx body, with the nodeid that produced it — the shipped
`backend/tests/error_code_observer.py` records only codes the catalogue
*omits*, and drains per test, so it cannot answer "which catalogued codes were
never seen". Run against all three PR gates as CI runs them:

```
tests/api (minus scientific, minus openapi snapshot)   1133 passed
tests/api/scientific + tests/services/scientific_read  2049 passed
complement (everything else)                           4293 passed
```

All green; 91 catalogued codes observed, 53 not.

**How the catalogue is counted.** At `2ea4eeb8` it holds **146 entries** and
**144 distinct codes** — `atom_map_element_not_conserved` and
`atom_map_not_a_bijection` each have two entries, at 422 and at 409, which is
deliberate and documented. The suite also emits the `http_<status>` fallback
family (`http_401`, `http_403`, `http_404`, `http_409`), which the catalogue
enumerates as a pattern rather than as entries, plus the observer's own
synthetic `zzz_synthetic_probe_bait`. Counting those alongside catalogued
codes gives 96; counting only catalogued codes gives 91. Either way the
unobserved set is the same 53, because the fallback family and the probe are
not catalogue members.

**2. Static call-site reachability.** For each of the 53, read from the raise
site outward: which functions call it, with what arguments, and can a route
supply an input that reaches the branch. Backed by an AST call-graph over
`backend/app` + `tckdb_schemas` rooted at the 234 route handlers and 336
Pydantic validators (used only as a strong-negative filter — name-based
resolution over-approximates, so *unreachable* is evidence and *reachable* is
not).

**3. Raise-line coverage (instrumented, independent).** The census answers
"did a client ever receive this code?". Coverage answers a different question:
"did the branch that mints it ever *run*?". They come apart in exactly the
interesting case — a refusal exercised at service or schema level whose code
never reaches a wire body, which is the #115 shape. The full suite was re-run
under `coverage.py`, each code AST-resolved to its `raise` statements
(constants and f-strings resolved), and the statement line checked:

| | of the 53 |
|---|---|
| `raise` **executed** by the suite at one or more sites | **23** |
| `raise` **never executed** anywhere, at any level | **16** |
| not attributable by this method | 14 |

The 14 are the method's blind spots, stated so they are not read as evidence:
six codes share one `raise` that takes the code as a parameter
(`assert_calculation_owned_by`, `reconcile_id_ref`), two are written as
`raise HTTPException(detail="code: …")` where the message is a keyword rather
than a positional argument, and the rest are handler literals or a ternary
rather than a raise. All fourteen were settled by probes or by call-site
reading instead.

**The 23 are the finding.** A quarter of the whole catalogue's untested set is
not untested logic at all — it is logic with a unit test whose *published
code* nobody has ever checked. `atom_map_geometry_unparseable` fires at all
three of its sites in the wire package; `release_tag_taken`,
`selection_already_stands`, `selection_already_superseded`,
`selection_no_longer_approved`, `record_subject_mismatch`,
`curation_policy_version_conflict`, `non_finite_value`,
`unsupported_reaction_molecularity`, `unsafe_lowest_energy_comparison`,
`ml_export_seed_unresolved`, `ml_export_lot_unresolved` and
`export_seed_unresolved` all have service-level tests. That is why the tests
for them are cheap: the behaviour is pinned, only the contract is not.

**4. Wire probes (instrumented).** For every verdict where reading could
plausibly be wrong, a throwaway probe issues the request through `TestClient`
and prints what the wire returned. This is what turned 22 readings into
measurements and is what caught findings 1 and 4 above. The probes are
**not** part of this PR — see "Instrumentation" at the end.

> **Coverage caveat, recorded because it nearly produced a wrong answer.**
> `tckdb_schemas` is an editable install pointing at the *main* checkout, so
> in a worktree `import tckdb_schemas` loads `/home/calvin/code/TCKDB_v2/schemas/…`
> and coverage records that path, not the worktree's. Keyed on the worktree
> path, all three `atom_map_*` codes looked unexecuted; keyed on the real path
> (files verified byte-identical at this commit) all three are executed. Any
> future coverage work in a worktree has to resolve the wire package the same
> way.

### Where the methods disagreed

Five times, and every disagreement was a finding:

1. **`invalid_pagination`** — static said "reachable, four sites, and the
   suite tests it". The probe showed two of the four sites do not put the code
   on the wire at all, and that the four tests named after it never reach the
   service. Finding 1. Static reading of the service module could not have
   found this: the cause is split between an exception-handler rule in
   `error_contract.py` and a `Query(le=200)` in a route signature.
2. **`species_handle_conflict`** — static said reachable via
   `species_id` + `species_ref`; the first probe returned
   `handle_type_mismatch` instead, because the probe used the ref prefix
   `sp_` and the real prefix is `spc_`. Re-probed with `spc_`: reachable,
   confirmed. Static was right; the probe was wrong first. Worth recording
   because it is the failure mode of probes.
3. **`withdraw_reason_required` / `rationale_required`** — static said
   reachable with an empty reason. On the wire an empty string is rejected by
   Pydantic `min_length=1` as `request_validation_error`, and
   `record_ref` is resolved *before* the rationale is checked. Both are
   reachable, but only with a **whitespace-only** reason and a **real**
   record ref. A test written from the static reading alone would have
   asserted the wrong code.
4. **`non_finite_value`** — static said "needs a stored NaN, probably
   impossible". The probe found Postgres accepts `'NaN'::float8` into
   `thermo.h298_kj_mol`, that a trigger makes the row immutable only *after*
   acceptance, and that publishing a release over such a row returns
   `non_finite_value` with the JSON path. This is the one item I would have
   filed UNCLEAR on reading alone.
5. **`manifest_already_frozen`** — the one place a *method* was overturned
   rather than sharpened. Static reading and a wire probe both said
   unreachable: `publish` calls `publish_release` before `freeze_manifest`, so
   publishing twice raises `release_not_draft` first, which the probe
   confirmed. Coverage then showed the `raise` at
   `services/release/manifest.py:295` **does execute** — a service-level test
   calls `freeze_manifest` directly. So the branch is *live* and the code is
   unreachable *through any route*. Two different verdicts with two different
   consequences: do not delete the branch, do stop telling clients they can
   receive the code. Reading alone would have recommended deletion.

Everywhere else the two agreed, including every remaining UNREACHABLE verdict.
The five whose `raise` coverage could confirm — `unsupported_direction`,
`include_not_implemented_yet`, `record_type_not_selectable`,
`subject_type_mismatch`, `unsupported_release_record_type` — were all recorded
as never executed, matching the static verdict exactly, and
`calculation_handle_conflict`'s wrapper *call* line never executed either,
which is a stronger statement than the static one (the wrapper is not merely
unable to conflict; it is never invoked).

### The ambiguity scan (finding 1, generalised)

For every `raise` whose message starts with a catalogued `message_prefix`
code, apply `_NESTED_CODE_PATTERN` to the static message text and count
candidates. Two sites in the whole tree carry more than one, both
`invalid_pagination` (the `raise` statements at `common.py:80` and
`common.py:85`, whose messages are on the following lines). The class is
closed and small.

> **Landed in #170** as
> `tests/api/test_error_contract_catalogue_gate.py::TestOneMessageDeclaresOneCode`,
> in the same change that fixed the two sites, so it went green immediately.
> Two details the sketch above did not anticipate: it reads every string
> expression rather than only `raise` arguments (a message bound to a local
> is the one shape neither this scan nor the observer could otherwise see),
> and it excludes docstrings, because this repository quotes real refusal
> messages in prose. It reads 125 messages across 68 codes.

---

## UNTESTED REFUSAL (33)

Reachable through a real request; nobody has provoked it. Ordered by how easy
the test is to write.

### Tier A — one request, no fixtures (11)

| code | anchor | provoke with |
|---|---|---|
| `export_seed_unresolved` *provoked* | `services/scientific_read/export.py:564`, `:593` | `GET /scientific/export/ndjson?reaction_ref=rxe_nope` as curator |
| `ml_export_seed_unresolved` *provoked* | `services/scientific_read/ml_dataset.py:467` | `GET /scientific/export/ml/species.ndjson?species_ref=spe_nope` |
| `ml_export_lot_unresolved` | `services/scientific_read/ml_dataset.py:1387` | same route, `lot_ref=lot_nope` |
| `unsafe_lowest_energy_comparison` *provoked* | `services/scientific_read/species_calculations_search.py:389` | `POST /scientific/species-calculations/search` `{"calculation_type":"sp","ranking":"lowest_energy"}` |
| `invalid_pagination` (offset<0, limit<1 only) *provoked* | `services/scientific_read/common.py:75`, `:77` | any composed-search POST with `{"offset": -1}` or `{"limit": 0}` |
| `species_handle_conflict` *provoked* | `services/scientific_read/handles.py:581` | `POST /scientific/species-calculations/search` `{"species_id":1,"species_ref":"spc_nope"}` |
| `species_entry_handle_conflict` *provoked* | `services/scientific_read/handles.py:595` | same, with `species_entry_id` + `species_entry_ref` |
| `curator_task_not_found` *provoked* | `services/machine_review/curator_task_lifecycle.py:69` | `POST /admin/machine-review/curator-tasks/99999999/assign` as admin |
| `unknown_curation_policy` *provoked* | `api/routes/releases_admin.py:181` | `POST /releases` citing a policy version that was never registered |
| `unknown_record` *provoked* | `services/release/curation.py:357` | `POST /releases/{tag}/selections` `{"record_ref":"thm_nope"}` (the sibling site `curation.py:275`, inside `_assert_record_exists`, is unreachable from `add_selection` — the id was just read out of the same table) |
| `record_has_no_subject` *provoked* | `services/release/curation.py:366` | select a `statmech` owned by a **transition-state entry** — `Statmech.species_entry_id` is nullable (`db/models/statmech.py:62`), so the subject lookup returns `None` |

### Tier B — needs a release/selection fixture (9, all provoked)

All reachable through `/api/v1/releases/*`; each was produced on the wire
during triage.

> **All nine are now provoked by a test.** The first two landed with #170;
> the other seven are in
> `backend/tests/api/test_api_untested_refusals_tier_bc.py`, which asserts the
> `(status, code)` pair through the route and, for each, that a neighbouring
> valid request is still accepted. Every pair agreed with the catalogue on
> first run — checked by deliberately mis-stating one status and confirming
> the runtime observer fails the test, so the agreement is measured rather
> than assumed. Two corrections to the rows below: the first two rows'
> "arrives at 409, not 422" note describes the catalogue *before* #170 and no
> longer holds — both entries say 409 — and `rationale_required`'s three
> sites need three different routes (select, supersede, withdraw-a-selection),
> not three payloads on one.

| code | anchor | provoke with |
|---|---|---|
| `release_tag_taken` | `services/release/curation.py:136` | `POST /releases` twice with the same `tag` — **arrives at 409**, not 422 |
| `curation_policy_version_conflict` | `services/release/curation.py:86` | re-register `(name, version)` with a different `description` — **arrives at 409** |
| `release_not_published` | `services/release/curation.py:225`, `:250` | withdraw, or attach a DOI to, a draft release |
| `release_not_draft` | `services/release/curation.py:170`, `:625` | publish twice, or append a selection after publishing |
| `withdraw_reason_required` | `services/release/curation.py:229` | withdraw a **published** release with `reason="  "` (whitespace — `""` is caught by Pydantic first) |
| `rationale_required` | `services/release/curation.py:441`, `:508`, `:564` | select a **real** record ref with `rationale="   "` |
| `selection_already_stands` | `services/release/curation.py:464` | two selections for the same subject in one release |
| `supersedes_same_record` | `services/release/curation.py:511` | supersede a selection with the record it already names |
| `selection_already_superseded` | `services/release/curation.py:640` | supersede the same selection twice |

### Tier C — needs a second record or a state change (4; all but the last provoked)

> **All four are now provoked by a test**, in the same file — including
> `release_artifact_corrupt`, which the last row calls "not payload-provokable".
> That is true and is not the same as untestable: the test publishes, suspends
> `trg_release_artifact_immutable` with `session_replication_role = replica`,
> rewrites the stored bytes, and downloads. Two corrections: the
> `selection_no_longer_approved` row says "demote its `RecordReview`", and
> `approved → rejected` is **not** an allowed review transition
> (`services/record_review.py:75`) — it must route through `under_review`.
> `approved → deprecated` is allowed, is below the same curated floor, and is
> what the test uses. And `non_finite_value` raises *after* `publish_release`
> has flushed `status = published` — `freeze_manifest` is the failing step —
> so the release is left published in the session. In production
> `get_write_db` rolls that back (`api/deps.py:143`) and the release stays a
> draft; under the test harness, whose session is dependency-overridden and
> has no such rollback, it does not, so the test's accepted neighbour is a
> *different* release published first.

| code | anchor | provoke with |
|---|---|---|
| `record_subject_mismatch` | `services/release/curation.py:304` | supersede a selection with a record belonging to a **different** species entry. Reachable only through `supersede`, never through `add` — `add_selection` derives both the record and the subject from the same lookup. (See "worth a second look" below: `supersede_selection` uses `superseded.record_type` with the *new* ref's `record_id`.) |
| `selection_no_longer_approved` | `services/release/curation.py:207` | select an approved record, demote its `RecordReview`, publish |
| `non_finite_value` | `services/release/artifacts.py:133` | store a non-finite double **before** the record is accepted (the immutability trigger only guards accepted rows), select it, publish. Confirmed: `UPDATE thermo SET h298_kj_mol='NaN'::float8` then publish → `non_finite_value … (at $.record.h298_kj_mol)` |
| `release_artifact_corrupt` | `api/routes/scientific/releases.py:196` | freeze a manifest, mutate `release_artifact.content`, `GET .../artifacts/{path}`. Not payload-provokable — it guards stored bytes, not a request |

### Tier D — upload payloads (8)

Reachable from `POST /uploads/*`; none provoked (each needs a full upload
fixture, which is the next round's work).

| code | anchor | provoke with |
|---|---|---|
| `atom_map_without_transition_state` | `tckdb_schemas/fragments/reaction_atom_map.py:436` | reaction upload carrying `atom_map` with no transition state declared |
| `atom_map_participant_not_declared` | same file `:331`, `:479`, `:491`, `:514`, `:535` | map names a side/index the reaction does not declare, names the wrong `species_key`, or names one twice |
| `atom_map_geometry_unparseable` | same file `:142`, `:150`, `:157` | a participant geometry whose XYZ header count disagrees with its coordinate lines |
| `unsupported_reaction_molecularity` | `app/chemistry/units.py:74` | kinetics upload with **four** reactants and an `a_units`. `reactants` is `Field(min_length=1)` with no upper bound (`schemas/workflows/kinetics_upload.py:112`), so molecularity 4 reaches `_A_UNITS_BY_ORDER.get(4) is None` |
| `composed_search_candidate_limit_exceeded` | `services/scientific_read/common.py:118`, `:147` | a composed search (thermo/kinetics/species-calculations) matching more than `public_max_offset + page_size` rows; cheapest as a lowered `settings.public_max_offset` |
| `composed_search_pagination_changed` | `services/scientific_read/common.py:124` | commit a row between two pages of a composed traversal. Genuinely reachable in production — the read session sets no isolation level, so it runs READ COMMITTED — but not provokable by a single payload; a test must inject the row |
| `export_all_cap_exceeded`, `ml_export_all_cap_exceeded` | `services/scientific_read/export.py:582`, `ml_dataset.py:477` | `all=true` with more rows than `all_cap`; cheapest by passing a small `all_cap` |

*(Tier D lists 8 codes across 7 rows — `export_all_cap_exceeded` and
`ml_export_all_cap_exceeded` share a row.)*

### Tier E — stored-state corruption (1)

| code | anchor | provoke with |
|---|---|---|
| `artifact_integrity_failed` | handler `api/errors.py:359`; detected at `services/artifact_storage.py:474`, `:484` | corrupt the stored object so its sha256 no longer matches, then download it. 502, deliberately not 503 |

---

## BACKEND-BUG GUARD (10)

Legitimate to leave untested. Stating that explicitly is the point — none of
these should be counted as missing coverage, and a blanket "every catalogued
code must be tested" gate would demand tests for all ten.

| code | anchor | why it cannot fire on a correct backend |
|---|---|---|
| `create_applied_group_additivity` | `services/group_additivity_resolution.py:129` | `persist_thermo` flushes before the id is used, so `session.get(Thermo, thermo_id)` cannot return `None`. Additionally the code can never reach a body at all — see "cannot reach the wire" below |
| `keyset_predicate` | `services/scientific_read/keyset.py:214`, `:218` | argument-shape invariant between `keys` and `last_values`; both are built by the caller. Same wire caveat |
| `owner_missing` | `services/scientific_read/calculations.py:516` | the `one_owner` CHECK constraint forbids a calculation with neither owner; the comment says so |
| `statmech_calculation_key_undeclared` | `services/statmech_resolution.py:109` (constant at `:56`) | "The schema layer of every upload path already refuses a key it cannot match, so reaching this error means a workflow built an incomplete map" — the docstring's own words |
| `stored_species_smiles_unparseable` | `services/reaction_resolution.py:120` (constant at `:53`) | a stored SMILES that RDKit cannot parse; every stored SMILES was parsed on the way in |
| `composed_search_invalid_page` | `services/scientific_read/common.py:131` | `pagination.returned != len(records)` — an internal invariant of the sibling search service |
| `composed_search_pagination_stalled` | `services/scientific_read/common.py:140` | an empty page before the reported total; requires rows to vanish mid-traversal, and these tables are append-only |
| `transport_source_calculation_owner_mismatch` | raise at `services/calculation_ownership.py:143`; call site `workflows/transport.py:72` | checks a `species_entry_id` the same block just assigned; no `existing_calculation_id` path exists for transport |
| `applied_energy_correction_source_calculation_owner_mismatch` | call sites `workflows/thermo.py:323`, `workflows/computed_species.py:781`, `:832` | all three read from a key map built for the target's own owner; applied corrections have no `existing_calculation_id` field |
| `statmech_torsion_scan_calculation_owner_mismatch` | call site `workflows/computed_species.py:979` | same; `source_scan_calculation_key` has no existing-id variant |

The last three are the finding-2 cluster. They are deliberate forward guards,
so the recommendation is **not** deletion — it is that they stop being
exported to the client enum as branchable refusals while the API cannot
produce them.

> **Two of the three have since become reachable, and the row above is
> right about why it could not see it.** Both rows list only the call
> sites that *use the shared guard*, because that is what a scan of
> `assert_calculation_owned_by` finds. The reaction bundle enforced the
> same rule with its own inline comparison, so its call sites are absent
> from both rows and the "why it cannot fire" column was reasoning over
> an incomplete list. #193 (`statmech_torsion_scan_...`) and #195
> (`applied_energy_correction_source_...`) routed those sites through the
> guard; both codes are now `Reach.request` and exported.
> `transport_source_calculation_owner_mismatch` is the one that stands:
> no write path anywhere produces the condition.

---

## UNREACHABLE (10)

No request can produce them. **Reported, not deleted** — every one needs
review, two are deliberate, and one (`manifest_already_frozen`) has a live
branch that a service test still executes.

Ordered by how conclusive the evidence is.

| code | anchor | why it cannot fire |
|---|---|---|
| `calculation_handle_conflict` | `services/scientific_read/handles.py:651` | `reconcile_calculation_pair` has **zero callers** outside `handles.py`. The wrapper is dead |
| `reaction_handle_conflict` | `services/scientific_read/handles.py:609` | its only caller passes `id_value=None` (`services/scientific_read/reactions.py:104`), and `reconcile_id_ref` returns before the conflict branch whenever `id_value is None`. *Probed: the same request returns 200* |
| `reaction_entry_handle_conflict` | `services/scientific_read/handles.py:623` | identical, `reactions.py:107` |
| `unsupported_direction` | `services/scientific_read/reactions.py:400` | `ReactionDirectionQuery` has exactly `forward`/`reverse`/`either` (`schemas/reads/scientific_reactions.py:44-46`); the branch is already marked `# pragma: no cover`. The enum docstring at `:41` still claims "the service rejects it with a deterministic 422", which is stale. *Probed: `direction=exact` → `request_validation_error`* |
| `include_not_implemented_yet` | `services/scientific_read/calculations.py:257`, `calculations_search.py:208` | `_NOT_IMPLEMENTED_INCLUDE_TOKENS = frozenset()` (`calculations.py:131`). Intersecting with the empty set is always empty. The comment says the guard is kept for a future token — a defensible choice, but the code is exported to clients today. *Probed* |
| `idempotency_in_progress` | `api/errors.py:444` | `in_progress` is never set — finding 3 |
| `record_type_not_selectable` | `services/release/curation.py:437` | the only route into `add_selection` resolves `record_type` from `SELECTABLE_REF_PREFIXES`, whose value set equals `SELECTABLE_RECORD_TYPES` exactly (verified by set difference, both directions empty) |
| `subject_type_mismatch` | `services/release/curation.py:296` | both call sites compare a `subject_type` that came from `CANDIDATE_SOURCES[record_type]` against `CANDIDATE_SOURCES[record_type][2]` — the same lookup |
| `unsupported_release_record_type` | `services/release/records.py:500`, `:793` | both callers (`services/release/artifacts.py:316`, `:435`) derive `record_type` from `ReleaseSelection` rows, which `add_selection` already restricted to `SELECTABLE_RECORD_TYPES` |
| `manifest_already_frozen` | `services/release/manifest.py:296` | unreachable **through any route** — `publish` calls `publish_release` first, which raises `release_not_draft`. *Probed: publishing twice returns `release_not_draft`.* **The branch is not dead**: coverage shows the `raise` executes, from a service-level test that calls `freeze_manifest` directly. Do not delete it; the wrong claim is the client-facing one |

### Also unreachable, but by a different mechanism: the two accidental prefixes

`create_applied_group_additivity` and `keyset_predicate` are catalogued as
`Surface.accidental_prefix`. Since #164, promotion requires membership in
`MESSAGE_PREFIX_CODES`, which is built from `message_prefix` entries only —
so **neither can ever appear in a `code` field**. Verified directly:

```
detail_code("create_applied_group_additivity: …")          -> "validation_error"
validation_detail_code("keyset_predicate: …")              -> "validation_error"
```

That is the design working. But the catalogue's own header says it enumerates
"every code the API can put in the `code` field", and the note on
`create_applied_group_additivity` (`code_catalogue.py:275-279`) says "it lands
in the code position and the envelope reports it. Reachable from POST
/uploads/thermo." The refusal is reachable; the reported code is not. That
sentence predates the gate that now blocks it.

---

## The six `*_handle_conflict` codes

Only `level_of_theory_handle_conflict` is emitted. Of the other five:

| code | verdict |
|---|---|
| `species_handle_conflict` | **reachable** — `species_calculations_search.py:202` passes a real `request.species_id`; provoked on the wire |
| `species_entry_handle_conflict` | **reachable** — `species_calculations_search.py:207`; provoked |
| `reaction_handle_conflict` | **unreachable** — only caller hard-codes `id_value=None` |
| `reaction_entry_handle_conflict` | **unreachable** — same |
| `calculation_handle_conflict` | **unreachable** — wrapper has no callers at all |

So: two untested refusals, three dead. The rule that separates them is
whether the calling search request exposes the sibling `*_id` field. It does
on `/scientific/species-calculations/search`
(`schemas/reads/scientific_species_calculations.py:112-116`) and it does not
on `/scientific/species/search` or `/scientific/reactions/search`, which pass
`id_value=None` on purpose.

Worth noting for the next round: `reconcile_calculation_pair` and the two
reaction wrappers are the only three, and all three would become reachable the
day someone adds the sibling `*_id` filter to those endpoints. That is an
argument for leaving them and testing `reconcile_id_ref` directly at the unit
level, not for deleting them — but it should be a decision, not an accident.

---

## Does the nightly cover any of them?

**No — zero of the 53.** Measured, not argued: the full suite was run the way
the nightly runs it — `backend/scripts/test-full.sh` with an unpinned seed
(drew `--randomly-seed=235974281`), 7478 passed, 14 skipped, 20 minutes — with
the same recorder attached. It observed **the same 91 codes**, and no
`(status, code)` pair that the catalogue does not already list.

This is structural, not luck. The three PR gates partition `backend/tests/`
by construction: `test-rest.sh` is defined as the complement of the other two
(`backend/scripts/test-rest.sh:72`), so gates ∪ = full suite minus
`tests/api/test_openapi_snapshot.py`, which produces no error bodies. The
nightly runs the same tests in a different order with an unpinned seed
(`backend/scripts/test-full.sh`, `TCKDB_TEST_SEED=random`). Order does not
create requests.

The number is therefore not smaller than it looks. There is no set of tests
anywhere in the repository that produces any of these 53 codes.

---

## Worth a second look, outside this triage

Three things noticed while reading that are not about coverage:

1. **`supersede_selection` mixes record types.** The route resolves
   `body.record_ref` to a `ResolvedCandidate` but then passes only
   `candidate.record_id` to `supersede_selection`, which uses
   `superseded.record_type` (`api/routes/releases_admin.py:265-269`,
   `services/release/curation.py:515-527`). Superseding a kinetics selection
   with a `thm_…` ref therefore looks up that integer id in the *thermo*
   table. It fails safe today — `_assert_record_exists` raises `unknown_record`
   when the id is absent, and `_assert_subject_matches` catches the rest —
   but the two ids colliding would attach the wrong record silently.
2. **Unknown curator task answers with two different codes.** `POST
   .../curator-tasks/{id}/assign` returns `curator_task_not_found`;
   `GET .../curator-tasks/{id}` returns bare `http_404`, because
   `api/routes/admin.py:385` has its own `_get_curator_task_or_404` that
   raises `HTTPException(404, "Curator task not found.")` instead of using the
   service helper. Same condition, two contracts.
3. **`_operational_error_handler` passes `fallback_code="database_error"`**
   (`api/errors.py:433`), a code the catalogue does not list. Harmless today —
   `code=` is always supplied explicitly on that path — but it is a string
   one edit away from being emitted.

---

## What the next round should and should not do

**Should not**: add a gate requiring every catalogued code to be tested.
Twenty of the 53 (the ten backend-bug guards and the ten unreachable ones)
would fail it on day one and could not be made to pass, and a gate that starts
red gets disabled.

**Should**, roughly in value order:

1. ~~Fix `invalid_pagination`'s two messages so the code survives promotion;
   point the four `test_rejects_invalid_pagination` tests at a path that
   actually reaches the service (`?offset=999999`, or a POST body); and make
   them assert `code`, not just `422`.~~ **Done in #170**, with the two
   second tokens promoted to codes rather than reworded away — see finding 1.
2. ~~Correct the two wrong statuses in the catalogue, and decide whether the
   observer should check `(status, code)` rather than `code` — the check is
   one tuple wider and would have caught both.~~ **Done in #170.** The
   parenthetical was the one claim in this document that measurement
   contradicted: the wider check would *not* have caught either on its own,
   because neither code was emitted by any test. It catches them now because
   the same change provokes both.
3. Write the Tier A and Tier B tests — 20 codes, 19 of them already provoked
   on the wire, so the payloads are known and 12 of them already have
   service-level tests to hang the assertion next to.
4. Decide, per unreachable code, whether to delete the branch or make it
   reachable. Three of them (`include_not_implemented_yet`, the two accidental
   prefixes) are deliberate and should stay with a note; the rest need a call.
5. Consider a catalogue field distinguishing "a caller can provoke this" from
   "this guards an internal invariant". That is the distinction this document
   had to reconstruct by hand, and it is the one that makes a coverage gate
   possible later.

---

## Instrumentation

**Nothing beyond this document is proposed for the PR.**

Three throwaway tools were written and are deliberately not landed:

* the recording plugin (a `JSONResponse` patch that logs every code with its
  nodeid) — it answers a census question once; the shipped
  `backend/tests/error_code_observer.py` already covers the standing need,
  and a second permanent patch on the same method is a hazard.
* four probe modules under `backend/tests/` — these are drafts of the tests
  step 3 above will write properly, and landing half-written versions would
  pre-empt that work with worse code.
* the ambiguity scan — this is the one with lasting value: "a `message_prefix`
  code's own message must contain exactly one candidate token" is a real
  invariant, statically checkable, and it is what finding 1 violates. It is
  not proposed **now** because it would land red on two sites. It should land
  in the same change that fixes those two messages, where it goes green
  immediately and stays green. The scan is nine lines over
  `_CODE_POSITION_PATTERN` and `_NESTED_CODE_PATTERN`; reproducing it is not
  the cost.
