# Transition-State Trust Rubric — `computed_transition_state_v1`

**Status:** implemented — `computed_transition_state_v1` ships in
`app/services/trust/rubrics.py` and is wired into the standalone
transition-state-entry detail read and propagated into the composite
`reaction-entries/{id}/full` read (updated 2026-07-13; original design
below is retained as rationale). See
[trust_read_api_current.md](trust_read_api_current.md) for the current,
maintained endpoint contract.
**Date:** 2026-05-28 (original design)
**Companion to:** [automated_trust_layer.md](automated_trust_layer.md),
[scientific_transition_state_reads.md](scientific_transition_state_reads.md),
[trust_read_api_current.md](trust_read_api_current.md)
**Scope (at design time):** Backend only. No code, no migrations, no
read-API changes, no LLM/RAG integration. No ARC or `tckdb-client`
changes. Does not introduce or change transition-state curation UI. (This
scope note describes the state when the spec was written; the feature is
now implemented — see Status above.)

---

## 1. Why this exists

The deterministic trust layer ships rubrics for the following record
families today:

```text
computed_calculation_v1
computed_kinetics_v1
computed_thermo_v1
computed_statmech_v1
computed_transport_v1
```

The reaction-entry composite read

```text
GET /api/v1/scientific/reaction-entries/{id}/full?include=trust
```

propagates trust into embedded kinetics records and embedded calculation
summaries, but **not** into the transition-state sections. That is
correct given the current state — no rubric exists for transition
states, so there is nothing honest to attach.

This spec closes that gap by defining `computed_transition_state_v1`. It
answers exactly one question:

> How much **structured evidence** supports this transition-state entry?

It deliberately does not answer:

> Is this transition state scientifically guaranteed correct?

Per the [core principles](automated_trust_layer.md#3-core-principles)
the metric is **never** called a quality score, certified score, or
truth score. The output is an evidence-completeness label plus an
explainable per-check breakdown.

---

## 2. Non-goals

- **No implementation.** Spec only — no Python, no SQLAlchemy, no
  serializers, no tests in this PR.
- **No migrations.** This rubric reads existing rows; nothing new is
  persisted.
- **No read API changes.** The future wiring into
  `/scientific/transition-state-entries/{ref}?include=trust` and
  `/reaction-entries/{id}/full?include=trust` is described in §10 but
  not delivered here.
- **No top-level reaction-entry trust.** The rubric evaluates a TS
  entry, not its parent reaction entry; no aggregation up to
  reaction-entry trust is introduced.
- **No reaction-level rollup.** TS trust does not roll up into kinetics
  trust either. Kinetics already has its own rubric
  (`computed_kinetics_v1`) with its own TS evidence checks.
- **No LLM / RAG involvement.** Per
  [§3 principle 4](automated_trust_layer.md#3-core-principles), LLM
  output remains submission-scoped and advisory; it cannot influence
  this rubric's pass/fail decisions, completeness ratio, or label.
- **No ARC or `tckdb-client` changes.** TS evidence is whatever ends up
  in the database after a normal submission flow; the rubric does not
  ask ARC for additional data.
- **No transition-state curation UI.** Curator review state is read
  through the existing `record_review` row, exactly the same way the
  other rubrics already do.

---

## 3. Rubric target

`computed_transition_state_v1` evaluates a **`transition_state_entry`**.

Rationale:

- A `transition_state_entry` is the concrete candidate saddle-point
  structure that carries charge, multiplicity, status, and the
  attachment point for source calculations
  (`calculation.transition_state_entry_id`).
- The parent `transition_state` is the identity concept (one per
  reaction channel); it does not own evidence directly.
- Per the existing scientific TS read surface
  ([scientific_transition_state_reads.md §2](scientific_transition_state_reads.md#2-endpoint-list)),
  the search grain is already **TS-entry**, so wiring trust at the same
  grain matches what clients already iterate over.

The rubric **inspects** the following parent context as supporting
identity facts (without changing grain):

- `transition_state` (parent TS concept).
- `reaction_entry` and `chem_reaction` (reaction context).

Inspection of the parent context is read-only and contributes only to
identity checks (e.g. `transition_state_parent_present`,
`reaction_entry_present`). The numerator and denominator of the
completeness ratio are entirely owned by `transition_state_entry` and
its directly attached source calculations.

### 3.1 Rubric selection

Per
[§5.2 of the trust layer spec](automated_trust_layer.md#52-evidencerubric),
each record matches **at most one** rubric per evaluator call.

```text
record_type == transition_state_entry
  → computed_transition_state_v1   (the only TS rubric defined)
```

There is no `experimental_transition_state_v1` and no plan to add one;
experimental transition states are not a concept TCKDB stores.

---

## 4. Checks

Naming follows the existing trust layer convention
(`<noun_or_evidence_phrase>_present` / `_passed` / `_not_failed`),
matching the style of `computed_kinetics_v1` and
`computed_calculation_v1`.

Kinds match
[§5.1](automated_trust_layer.md#51-evidencecheck): **R** = required,
**O** = optional, **W** = warning. `applies_when` predicates are
spelled out where non-obvious.

### 4.1 Identity and entry context (R-heavy)

| Check | Kind | Notes |
|---|---|---|
| `transition_state_entry_present` | R | The record under evaluation is loaded. Trivially passes inside the evaluator (a guard against being called with `None`). |
| `transition_state_parent_present` | R | `transition_state_entry.transition_state_id` resolves to a real `transition_state` row. |
| `reaction_entry_present` | R | The parent TS resolves to a `reaction_entry`. Without this the entry has no chemical context. |
| `chem_reaction_present` | O | The parent reaction entry resolves to a `chem_reaction`. NULL FKs are already prevented by the schema, so this is a defensive optional check rather than a load-bearing one. |
| `ts_status_recorded` | R | `transition_state_entry.status` (`TransitionStateEntryStatus`) is set. Schema NOT NULL guarantees this in practice; the check exists so that `ts_status_not_rejected` has a well-defined predicate. |
| `ts_status_not_rejected` | R | `status != rejected`. If rejected → hard fail (see §6). |
| `charge_present` | R | `charge` is set. Schema NOT NULL guarantees this in practice. |
| `multiplicity_present` | R | `multiplicity` is set. Schema NOT NULL guarantees this in practice. |
| `multiplicity_valid` | R | `multiplicity >= 1`. Already enforced by the `multiplicity_ge_1` CheckConstraint; the rubric mirrors the constraint so a violation surfaces as a hard fail rather than a 500. |
| `ts_graph_or_smiles_present` | O | At least one of: a non-null `unmapped_smiles`, a non-null `mol` blob, or a mapped reaction graph attached via the reaction entry. Marked **optional** because not all TS entries are guaranteed to carry SMILES at upload time (see [scientific_transition_state_reads.md §3](scientific_transition_state_reads.md#3-response-fragments) — `unmapped_smiles` is the only public-readable surface and is itself nullable). |

### 4.2 Supporting calculation evidence

The rubric assembles a small object — call it the **source calculation
set** — covering every `calculation` row whose
`transition_state_entry_id` equals the entry under evaluation, plus
calculations linked via `calculation_dependency` chains rooted at any
of those rows. Discovery rules are spelled out in §5.

| Check | Kind | Notes |
|---|---|---|
| `supporting_calculations_present` | R | At least one `calculation` row is attached to this TS entry (either directly via `calculation.transition_state_entry_id`, or as the upstream of a `calculation_dependency` chain reachable from such a row). Weight 2 (load-bearing). |
| `ts_optimization_present` | O | At least one calc with `calculation.type == opt` is in the source set. The expected representative is `opt` owned by the TS entry. |
| `ts_frequency_present` | O | At least one calc with `calculation.type == freq` is in the source set, ideally linked to an opt via `calculation_dependency.role == freq_on`. |
| `ts_single_point_present` | O | At least one calc with `calculation.type == sp` is in the source set, ideally linked via `single_point_on`. Missing SP is **not** a hard fail (see §6). |
| `irc_evidence_present` | O | At least one calc with `calculation.type == irc` is in the source set, linked to the TS opt via `irc_start` or `irc_followup` dependency. Missing IRC is **not** a hard fail. |
| `path_search_evidence_present` | O | At least one calc with `calculation.type == path_search` is reachable from the TS entry's source set (typically via the TS opt's `optimized_from` chain to a `path_search` parent), OR a `calculation.type == scan` calc participating as a scan parent (`scan_parent` dependency role). Missing path-search is **not** a hard fail. See §7. |
| `calculation_dependencies_present` | O | At least one `calculation_dependency` row exists among the source set's calculations. Documents the DAG explicitly even when individual roles fail to pass their own optional checks. Aligns with the existing `feedback_dag_edges_opportunistic` posture — DAG edges enrich, they do not gate. |

### 4.3 Source-calculation provenance roll-up

These checks **summarize** facts from `computed_calculation_v1`
applied to the source set. They do not duplicate every calculation-level
check — they exist so a TS-entry consumer can answer "do my
supporting calcs carry enough provenance?" without crawling the
embedded calculation trust fragments.

For each summary check, the predicate is "at least one calc in the
source set passes the equivalent calculation-level check," unless
otherwise stated.

| Check | Kind | Notes |
|---|---|---|
| `source_calculation_lot_present` | R | Every calc in the source set resolves to a `level_of_theory`. (All; not "at least one" — LoT-less source calcs are meaningless for TS evidence.) |
| `source_calculation_software_present` | O | Every calc in the source set resolves to a `software_release`. (All-source semantics same as above.) |
| `source_calculation_workflow_tool_present` | O | At least one calc in the source set carries a `workflow_tool_release`. (At-least-one — workflow tool is optional per-calc, common at TS-set level.) |
| `source_calculation_artifacts_present` | O | At least one calc in the source set has ≥1 `calculation_artifact`. |
| `source_calculation_has_non_hard_failed_evidence` | R | At least one calc in the source set evaluates under `computed_calculation_v1` to a non-`hard_failed` label. If every supporting calc is itself hard-failed, the TS entry inherits a hard fail (see §6). Weight 2. |

### 4.4 Geometry validation

`calc_geometry_validation` is the existing per-calculation automatic
geometry-validation row (`ValidationStatus ∈ {passed, warning, fail}`),
already used by `computed_calculation_v1`.

| Check | Kind | Notes |
|---|---|---|
| `geometry_validation_present_for_source_calculations` | O | At least one calc in the source set whose `type ∈ {opt, irc, path_search}` has a `calc_geometry_validation` row. SP and freq calcs have no geometry of their own and are excluded from the predicate. |
| `geometry_validation_not_failed_for_source_calculations` | W | If any geometry-validation row exists in the source set with `status == fail`, this surfaces as a **hard fail** (see §6) rather than a warning. The W-kind check fires only when at least one row exists with `status == warning` and none with `status == fail`. |

### 4.5 Frequency / imaginary-mode evidence

Frequency evidence is the most TS-specific part of the rubric and is
the only place where the spec recommends conservative hard-fail
behavior. See §8 for the policy rationale.

For frequency evidence, the rubric inspects the **most-recent**
`calc_freq_result` row associated with a freq calc in the source set
(ordered by `calculation.created_at desc`, with `calculation.id desc`
tie-break for determinism). When no such row exists, all four checks
below are `not_applicable` and `ts_frequency_present` carries the
missing signal on its own.

| Check | Kind | Notes |
|---|---|---|
| `imaginary_frequency_count_recorded` | O | `calc_freq_result.n_imag IS NOT NULL`. |
| `single_imaginary_frequency_for_ts` | R | `calc_freq_result.n_imag == 1`. See §8 for what happens when `n_imag == 0` or `n_imag > 1`. |
| `imaginary_frequency_value_present` | O | `calc_freq_result.imag_freq_cm1 IS NOT NULL`. |
| `frequency_source_consistent_with_ts_status` | W | Conditional warning. Fires when `transition_state_entry.status ∈ {optimized, validated}` and the most-recent freq result is **not** strictly compatible with a TS (`n_imag != 1`). The hard-fail branch in §8 overrides this when the violation is severe enough. |

### 4.6 Curator review state

| Check | Kind | Notes |
|---|---|---|
| `review_not_rejected_or_deprecated_if_applicable` | R | `applies_when` a `record_review` row exists for this TS entry. Passes when `record_review.status ∉ {rejected, deprecated}`. If the row exists with `rejected` or `deprecated`, the trust status collapses to that value per the existing trust-layer derivation (see [automated_trust_layer.md §7](automated_trust_layer.md#7-trust-status-vocabulary)) — the rubric label remains computed but the public `trust_status` becomes `rejected` / `deprecated`. |

Curator review on a TS entry follows the same pass-through semantics
already in use for kinetics, thermo, and calculations: the rubric
**reads** `record_review.status` but never mutates it.

---

## 5. Source-calculation set construction

The TS-entry source set is built deterministically from existing
rows, using only fields documented in
[scientific_transition_state_reads.md](scientific_transition_state_reads.md)
and the calculation model.

### 5.1 Direct attachment

Every `calculation` row where
`calculation.transition_state_entry_id == ts_entry.id` is in the source
set. Today these are typically the TS `opt` calc and, more rarely, a
TS-owned `freq`, `irc`, or `path_search` calc that records the TS
entry as its anchor.

### 5.2 Dependency traversal (one hop)

From each directly-attached calc, follow `calculation_dependency`
edges **in both directions** for the following roles:

| From role | Adds calcs of type | Rationale |
|---|---|---|
| `optimized_from` (upstream) | `path_search`, `scan` | TS opt was produced from a path-search or scan. |
| `freq_on` (downstream child where parent is the TS opt) | `freq` | TS freq computed on the TS-optimized geometry. |
| `single_point_on` (downstream child where parent is the TS opt) | `sp` | TS SP computed on the TS-optimized geometry. |
| `irc_start`, `irc_followup` (downstream child where parent is the TS opt) | `irc` | IRC initiated from the TS optimized geometry. |
| `scan_parent` (upstream where the TS opt is the downstream child) | `scan` | TS guess came from a scan. |

A **single** dependency hop is sufficient — the rubric is not a graph
walker. If a project records additional intermediate calcs that the
evaluator should reach, they should either be attached directly via
`transition_state_entry_id` or surfaced through additional
`calculation_dependency` rows; spec-time expansion of the traversal is
left to a future rubric version (`v2`).

### 5.3 De-duplication

The source set is keyed by `calculation.id`. The same calc reached by
both direct attachment and dependency traversal is counted once.

### 5.4 Reuse of `computed_calculation_v1`

Each calc in the source set is independently evaluable under
`computed_calculation_v1`. The TS rubric **does not** re-implement
calculation-level checks; it summarizes the source-set's
calculation-level evaluations into the four checks under §4.3.

When the rubric is wired into `/full?include=trust` (see §10), the
calculation summaries embedded in the reaction-full payload already
carry their own `computed_calculation_v1` trust fragments. The TS-entry
trust fragment under that response refers to the same evaluations
implicitly — clients can inspect per-calc detail by following the
existing calculation refs already documented in
[scientific_transition_state_reads.md §3](scientific_transition_state_reads.md#3-response-fragments).

---

## 6. Hard-fail policy

A hard fail is a discrete, evidenced structural failure. Per
[automated_trust_layer.md §8](automated_trust_layer.md#8-hard-fails)
low completeness alone is **never** a hard fail.

### 6.1 Hard-fail signals (TS rubric)

| Signal | Source row(s) | Notes |
|---|---|---|
| `transition_state_entry_missing` | Evaluator called with no entry. | Should not normally happen on the read path; defensive check. |
| `transition_state_parent_missing` | `transition_state_entry.transition_state_id` does not resolve. | Schema NOT NULL guarantees against this, but the rubric mirrors the constraint so any future relaxation surfaces as a hard fail rather than silent garbage. |
| `reaction_entry_missing` | Parent TS does not resolve to a `reaction_entry`. | Same defensive rationale. |
| `ts_entry_status_rejected` | `transition_state_entry.status == rejected`. | Already excluded by default by the existing trust-layer status mapping; the rubric records the hard-fail explicitly so the trust fragment under `/full?include=trust&include_rejected=true` is honest. |
| `multiplicity_invalid` | `multiplicity < 1`. | Mirrors the CheckConstraint. |
| `supporting_required_calculation_hard_failed` | At least one calc in the source set is required to support a passed required check (e.g. it is the only source for `source_calculation_lot_present`) AND it evaluates to `hard_failed` under `computed_calculation_v1`. | Avoid the "everything else is bad, but we pass anyway" trap. |
| `geometry_validation_failed_for_required_source` | Any calc in the source set has `calc_geometry_validation.status == fail`. | If even one supporting calc has failed geometry validation, the TS evidence is structurally compromised. |
| `frequency_source_has_zero_imaginary_modes_for_validated_ts` | `transition_state_entry.status ∈ {optimized, validated}` AND the most-recent `calc_freq_result.n_imag == 0`. | Stationary point that is not a TS. See §8. |
| `frequency_source_has_multiple_imaginary_modes_for_validated_ts` | `transition_state_entry.status ∈ {optimized, validated}` AND the most-recent `calc_freq_result.n_imag > 1`. | Higher-order saddle point. See §8. |

### 6.2 Explicit non-hard-fails

The following missing signals **must not** hard-fail the rubric, even
though they all lower completeness:

- Missing IRC calc.
- Missing path-search / NEB / GSM calc.
- Missing single-point calc (e.g. the user composes thermo/kinetics
  from a composite method directly on the opt).
- Missing calculation artifacts.
- Missing curator review (default state for new submissions).
- `ts_graph_or_smiles_present == false` (TS entries without
  SMILES/mol attachment are still useful identity records).
- `ts_status == guess` (a guess-stage TS legitimately has no opt/freq
  yet; it should land in `partial` or `sparse`, not `hard_failed`).

This list is exhaustive on purpose. If a future evolution wants to
hard-fail one of these signals, that is a **rubric version bump**
(`v2`), not an in-place change.

---

## 7. IRC / path-search policy

IRC and path-search evidence are the strongest *positive* signals that
a saddle point really is the saddle point connecting the claimed
reactants and products. The rubric treats them as such — they raise
completeness when present — but does **not** require either of them.

### 7.1 Treatment

- `irc_evidence_present` (O): passes when an `irc` calc is reachable
  from the TS opt via `irc_start` or `irc_followup`.
- `path_search_evidence_present` (O): passes when a `path_search` or
  scan-parent calc is reachable from the TS opt via `optimized_from` /
  `scan_parent`.

Both checks are independent. A TS entry with only IRC, only
path-search, or both, all raise the completeness ratio. Neither is
needed to reach `well_supported` if the rest of the evidence is full;
both missing is acceptable for a `partial` or `mostly_supported` label
depending on the other optional checks.

### 7.2 Rationale

- Many ARC / RMG flows produce TS entries from a scan walk-up + opt +
  freq + sp pipeline, and only run IRC for a subset (cost, time).
  Hard-failing those entries would silently exclude a large fraction
  of currently-good submissions.
- Conversely, some IRC-only flows (TS entries that came from a
  reference geometry plus a confirming IRC, no path-search) are
  legitimate and should not be punished for lack of path-search.
- The `n_imag == 1` check (§4.5) already captures the **necessary**
  topological-saddle-point evidence; IRC and path-search are
  **additional** evidence and the rubric treats them that way.

### 7.3 Future tightening

If a future TS rubric version (`computed_transition_state_v2`) wants
to require IRC for `well_supported`, the path is:

- Bump the rubric name to `v2` (do **not** mutate `v1`).
- Move `irc_evidence_present` from `O` to `R`.
- Document the migration in the rubric file's docstring.

`v1` stays parseable for as long as existing records carry its
historical label — no retroactive re-evaluation under stricter rules.

---

## 8. Frequency evidence policy

Frequency-based confirmation that a structure is a first-order saddle
point (`n_imag == 1`) is the most direct mechanical signal of TS-ness
that the database holds. The policy is:

### 8.1 When status is `guess`

```text
n_imag == 1           → single_imaginary_frequency_for_ts: passed
n_imag is null        → all frequency checks: missing
n_imag == 0           → missing (warning), NOT hard fail
                        (the entry is at the guess stage; not having
                        proven TS-ness yet is expected)
n_imag > 1            → warning (frequency_source_consistent_with_ts_status),
                        NOT hard fail
                        (still an early-stage candidate)
```

### 8.2 When status is `optimized` or `validated`

```text
n_imag == 1           → single_imaginary_frequency_for_ts: passed
n_imag is null        → single_imaginary_frequency_for_ts: missing
                        (the entry claims to be optimized/validated;
                        absence of n_imag is a serious provenance gap
                        but it is a missing-check, not a hard fail,
                        because the freq calc itself may not yet be
                        attached. Lower the completeness, do not
                        exclude.)
n_imag == 0           → HARD FAIL
                        (frequency_source_has_zero_imaginary_modes_for_validated_ts)
n_imag > 1            → HARD FAIL
                        (frequency_source_has_multiple_imaginary_modes_for_validated_ts)
```

### 8.3 When status is `rejected`

The entry is already excluded by default; the trust-status collapses
to `rejected` regardless of frequency evidence. The frequency checks
still run for completeness but their results do not change the public
default exclusion.

### 8.4 Rationale

- `n_imag == 0` and `n_imag > 1` for a status-`optimized`-or-better
  TS entry is a **contradiction between two stored facts** in the
  database. Surfacing it as a hard fail is honest: either the freq
  source is attached to the wrong entry, or the status is wrong.
- `n_imag is null` is silence, not contradiction. Silence should lower
  the label, not exclude the record.
- `guess` status leaves room for an evolving record; freq evidence
  not yet established is normal and should not be punished beyond
  reduced completeness.

### 8.5 Selecting "the" freq source

When multiple freq calcs exist in the source set, the rubric picks
**one** representative deterministically:

1. Latest `calculation.created_at`.
2. Tie-broken by `calculation.id DESC`.

Multiple freq calcs are normal — for example a coarse freq before
an SP, a finer freq for thermo. The picked representative drives
§4.5's pass/fail. Future versions may make this configurable per
project; v1 stays prescriptive to keep the rubric deterministic.

---

## 9. Relationship to `computed_calculation_v1`

The TS rubric **composes** `computed_calculation_v1` rather than
duplicating it.

### 9.1 What the TS rubric reuses

Every calc in the source set (§5) is independently evaluable under
`computed_calculation_v1`. The TS rubric summarizes that evaluation
into:

```text
source_calculation_lot_present
source_calculation_software_present
source_calculation_workflow_tool_present
source_calculation_artifacts_present
source_calculation_has_non_hard_failed_evidence
```

This avoids re-implementing per-calculation checks and keeps the two
rubric layers independent — a bump to `computed_calculation_v1`
automatically improves what the TS rubric sees, without a TS rubric
version bump.

### 9.2 What the TS rubric does NOT reuse

- It does **not** flatten every calculation-level missing check into
  the TS missing list. That would make the TS missing list
  unreadable and would conflate per-calc gaps with TS-level gaps.
- It does **not** sum calculation-level completeness ratios into the
  TS ratio. Aggregation rubrics across heterogeneous records are
  out of scope; see §11 Q3.

### 9.3 Loaded-vs-id evaluator wrapper

The evaluator entrypoint mirrors the pattern already used by the
other rubrics
([§12](automated_trust_layer.md#12-evaluator-service-shape-informative)):

```text
evaluate_loaded_transition_state_entry(ts_entry, *, source_calcs, freq_results, ...)
    → EvaluationResult

evaluate_transition_state_entry(session, ts_entry_id)
    → EvaluationResult
```

The `loaded` variant assumes the read serializer has already loaded
the necessary children (parent TS, reaction entry, source calcs,
freq results, validation rows, review row). The id-wrapper is
provided for tests and ad-hoc evaluation; it loads the graph itself.
Per the existing evaluator rule, the `loaded` variant must not issue
its own queries.

---

## 10. Future read-API integration

This spec describes the rubric only. The read API wiring is a
separate slice; this section documents the intended endpoints so the
rubric design lines up with where it will eventually be consumed.

### 10.1 Standalone TS-entry detail

When implemented, the existing endpoint

```text
GET /api/v1/scientific/transition-state-entries/{ts_entry_ref_or_id}
```

gains the same opt-in pattern documented in
[trust_read_api_current.md](trust_read_api_current.md):

```text
GET /api/v1/scientific/transition-state-entries/{ref}?include=trust
  → attaches the standard trust fragment under record.trust,
    using computed_transition_state_v1
```

Behavior to mirror the existing trust-enabled endpoints:

- `include=trust` is opt-in. The default response omits `trust`.
- `include=all` does **not** expand to `trust`.
- `trust.evidence.record_id` follows the existing internal-id policy
  (hidden by default; restored only when `include=internal_ids` is
  requested and policy permits).
- `trust.llm_precheck` ships disabled (`{enabled: false, label:
  "not_run"}`).

### 10.2 TS-concept detail

```text
GET /api/v1/scientific/transition-states/{ts_ref_or_id}?include=trust
```

The TS-concept endpoint already embeds its child TS entries. Under
`include=trust`, **each embedded entry** carries its own
`computed_transition_state_v1` trust fragment. **No top-level
TS-concept trust is emitted** — the concept does not own evidence;
the entries do. This mirrors the reaction-entry `/full` posture
(no top-level reaction-entry trust).

### 10.3 Reaction-entry `/full` propagation

```text
GET /api/v1/scientific/reaction-entries/{id}/full?include=trust
```

Today this read propagates trust to:

- Embedded kinetics records (`computed_kinetics_v1`).
- Embedded calculation summaries (`computed_calculation_v1`).

Once `computed_transition_state_v1` lands and the loaded-variant
evaluator exists, the same composite endpoint will additionally
propagate trust to:

- Each embedded `transition_state_entry` under the TS section
  (`computed_transition_state_v1`).

The propagation stays additive — top-level reaction-entry trust is
still **not** emitted, and the existing kinetics / calculation trust
blocks are unchanged.

### 10.4 Search-grain trust

`/scientific/transition-states/search` (the existing TS-entry-grain
search; see
[scientific_transition_state_reads.md §2](scientific_transition_state_reads.md#2-endpoint-list))
remains free of trust fragments in this slice, following the existing
search-grain policy
([trust_read_api_current.md](trust_read_api_current.md)):

> Search/list endpoints do not expose trust fragments. Trust is
> currently a detail/read-surface feature.

When the project promotes trust to search grain globally (a separate
spec; not in scope here), TS search will follow.

---

## 11. Relationship to AI Review Assistant / LLM

Per [automated_trust_layer.md §3 principle 4](automated_trust_layer.md#3-core-principles)
and
[ai_review_assistant_admin_consumption.md](ai_review_assistant_admin_consumption.md):

- The AI Review Assistant produces advisory, submission-scoped output.
- AI Review Assistant audit events
  (`llm_precheck_*` `SubmissionAuditEventKind` values) do not
  influence `computed_transition_state_v1`:
  - They do not flip any check pass/fail/missing state.
  - They do not change the completeness ratio.
  - They do not change the rubric label or `trust_status`.
- The public scientific `trust.llm_precheck` fragment continues to
  ship disabled on TS-entry responses, just like on every other
  trust-enabled endpoint.

If a future spec proposes a record-level mapping from AI Review
Assistant audit events into public scientific trust fragments, that
mapping is governed by a separate design — it does not enter this
rubric.

---

## 12. Test plan (for the future implementation slice)

These tests are for the eventual implementation; they are not
deliverables of this spec.

| Test | What it proves |
|---|---|
| `tests/trust/test_ts_missing_entry_hard_fails.py` | Evaluator called with no TS entry → `hard_failed`, reason `transition_state_entry_missing`. |
| `tests/trust/test_ts_rejected_entry_hard_fails.py` | TS entry with `status == rejected` → `hard_failed`, reason `ts_entry_status_rejected`; rubric checks still run and surface in `missing_checks` honestly. |
| `tests/trust/test_ts_sparse_entry_low_completeness.py` | Guess-stage TS entry with only identity facts → label `sparse` or `unsupported`; no hard fail. |
| `tests/trust/test_ts_with_opt_freq_passes.py` | TS entry with opt + freq (`n_imag == 1`) + LoT + software → label at least `mostly_supported`. |
| `tests/trust/test_ts_freq_nimag_one_passes.py` | `single_imaginary_frequency_for_ts` passes when `n_imag == 1`. |
| `tests/trust/test_ts_freq_nimag_zero_validated_hard_fails.py` | `status == validated` and `n_imag == 0` → `hard_failed`, reason `frequency_source_has_zero_imaginary_modes_for_validated_ts`. |
| `tests/trust/test_ts_freq_nimag_zero_guess_warns.py` | `status == guess` and `n_imag == 0` → warning, NOT hard fail (per §8.1). |
| `tests/trust/test_ts_freq_nimag_multi_validated_hard_fails.py` | `status ∈ {optimized, validated}` and `n_imag > 1` → `hard_failed`, reason `frequency_source_has_multiple_imaginary_modes_for_validated_ts`. |
| `tests/trust/test_ts_freq_nimag_multi_guess_warns.py` | `status == guess` and `n_imag > 1` → warning, NOT hard fail. |
| `tests/trust/test_ts_irc_raises_completeness.py` | Adding an IRC calc to an otherwise-identical entry strictly raises the completeness ratio and does not change pass→fail on any check. |
| `tests/trust/test_ts_path_search_raises_completeness.py` | Same, for path-search. |
| `tests/trust/test_ts_no_irc_not_hard_fail.py` | A TS entry with full opt + freq + LoT + software but no IRC and no path-search reaches at least `partial`; no hard fail. |
| `tests/trust/test_ts_geometry_validation_failed_hard_fails.py` | Any source calc with `calc_geometry_validation.status == fail` → `hard_failed`, reason `geometry_validation_failed_for_required_source`. |
| `tests/trust/test_ts_geometry_validation_warning_warns.py` | `status == warning` on a source-calc validation → warning, no hard fail. |
| `tests/trust/test_ts_loaded_matches_session_evaluator.py` | `evaluate_loaded_transition_state_entry` and `evaluate_transition_state_entry` produce byte-identical fragments for the same record. |
| `tests/trust/test_ts_fragment_shape.py` | The TS trust fragment matches the standard envelope in [trust_read_api_current.md §Common Trust Fragment Shape](trust_read_api_current.md#common-trust-fragment-shape) exactly. |
| `tests/trust/test_ts_full_include_trust_embeds_ts.py` | `/scientific/reaction-entries/{id}/full?include=trust` attaches `computed_transition_state_v1` to each embedded TS entry once the wiring lands. |
| `tests/trust/test_ts_detail_include_trust.py` | Standalone TS-entry detail respects `include=trust` opt-in (default omits; explicit include attaches; `include=all` does not include trust). |
| `tests/trust/test_ts_llm_precheck_disabled.py` | `trust.llm_precheck` ships `{enabled: false, label: "not_run", summary: null}` regardless of any AI Review Assistant audit events on the originating submission. |
| `tests/trust/test_ts_rubric_version_stability.py` | Re-running `computed_transition_state_v1` on a frozen fixture produces byte-identical output. (Pins the public contract.) |

---

## 13. Open design questions

1. **Source-set traversal depth.** §5 deliberately caps dependency
   traversal at one hop. If real projects start chaining
   path-search → preopt → opt → freq → sp → composite-sp, the rubric
   may miss the deepest evidence. Resolution path: bump to
   `computed_transition_state_v2` with explicit deeper traversal
   rules. Do not relax silently.

2. **Tie-breaking the freq source.** §8.5 picks "latest" deterministically.
   Real projects may want "the freq attached to the same opt as the
   SP that feeds thermo." Adding a `preferred_freq_calculation_id`
   on `transition_state_entry` is one option; deriving it from review
   state is another. Same as the equivalent open question in
   [scientific_transition_state_reads.md §12](scientific_transition_state_reads.md#12-open-questions).
   Out of scope here; revisit when a downstream consumer asks.

3. **TS aggregation rubric.** Should a future rubric aggregate
   multiple TS entries on the same parent TS concept into a single
   TS-concept-level label? Not in scope here. The current design
   keeps the grain at TS-entry, exactly as the read surface does.

4. **TS rubric in kinetics rollup.** `computed_kinetics_v1` already
   has its own TS evidence checks
   (`ts_energy_source_present`, `frequency_source_present`). Should
   kinetics trust *read* the TS trust fragment instead of computing
   its own TS checks independently? This is the cross-rubric
   aggregation question; deliberately deferred. Both rubrics
   evaluate independently for now; clients can compose them by
   reading both fragments in the `/full` response.

5. **Curator escalation.** The rubric treats `record_review.status`
   as authoritative. If curators want a TS-entry-specific elevation
   path (e.g. "benchmark TS"), that is the same
   `benchmark_reference` open question already noted in
   [automated_trust_layer.md §15 Q2](automated_trust_layer.md#15-open-design-questions);
   no TS-specific decision needed here.

6. **Hard-fail when source set is empty.** Today
   `supporting_calculations_present` is R but its **absence** is a
   missing check, not a hard fail (the entry might be a fresh
   guess). Should we promote it to a hard fail when
   `transition_state_entry.status ∈ {optimized, validated}`? This
   would parallel the §8 status-aware hard fails. Tentative: yes,
   but as a `v2` change once we see real records. v1 keeps the
   conservative posture: missing supporting calcs → low
   completeness, not exclusion.

---

## 14. Recommended next implementation slice

A small, self-contained slice that follows
[automated_trust_layer.md §14](automated_trust_layer.md#14-recommended-implementation-order):

1. **Add `computed_transition_state_v1` rubric class** in
   `app/services/trust/rubrics/computed_transition_state_v1.py`,
   with the §4 checks. No read-API wiring yet.
2. **Add the loaded-variant evaluator entrypoint** that takes
   pre-loaded TS entry + source calcs + freq results + validation
   rows + parent TS + reaction entry + review row.
3. **Add the id-wrapper evaluator** for tests and ad-hoc calls.
4. **Land the test fixtures** that the test plan in §12 needs:
   - Minimal-evidence TS entry (identity only).
   - Full-evidence TS entry (opt + freq `n_imag == 1` + sp + IRC +
     path-search + artifacts + LoT + software + workflow tool).
   - TS entry with `n_imag == 0` and `optimized` status (hard-fail
     fixture).
   - TS entry with `n_imag > 1` and `validated` status (hard-fail
     fixture).
   - TS entry with `rejected` status.
   - TS entry with `guess` status and `n_imag == 0` (warning, not
     hard fail).
5. **Wire trust into the standalone TS-entry detail endpoint** under
   `include=trust`, mirroring the existing trust-enabled endpoints
   in [trust_read_api_current.md](trust_read_api_current.md). Land
   with its detail tests.
6. **Wire propagation into `/reaction-entries/{id}/full?include=trust`**
   so embedded TS entries carry their fragment. No top-level
   reaction-entry trust.
7. **Update `trust_read_api_current.md`** to list
   `computed_transition_state_v1` in the rubric table and the TS-entry
   detail and `/full` propagation rows.

Each step ships in isolation, with tests, behind an additive read
contract; no read-API behavior changes for callers that do not
request `include=trust`.

---

## 15. Final summary

| Item | Outcome |
|---|---|
| Spec file | `backend/docs/specs/transition_state_trust_rubric.md` (this file) |
| Rubric target | `transition_state_entry` (parent TS / reaction entry inspected as identity context only) |
| Checks defined | §4: identity, supporting calcs, source-calc provenance roll-up, geometry validation, frequency / imaginary modes, curator review |
| Hard-fail policy | §6: structural identity, rejection, multiplicity validity, required supporting calc hard fail, source-calc geometry-validation fail, status-aware imaginary-mode contradiction |
| IRC / path-search policy | §7: both raise completeness when present; both missing is **not** a hard fail; future tightening goes through a `v2` bump |
| Frequency-evidence policy | §8: `n_imag == 1` passes; `n_imag == 0` / `> 1` is a hard fail **only** for `optimized` / `validated` entries; for `guess` it is a warning |
| Future read integration | §10: `/scientific/transition-state-entries/{ref}?include=trust`; embedded entries under TS-concept detail and `/full`; no top-level reaction-entry trust |
| LLM relationship | §11: AI Review Assistant is advisory, submission-scoped, and never influences this rubric |
| Open questions | §13: traversal depth, freq tie-break, TS aggregation, cross-rubric kinetics linkage, curator escalation, status-aware support-calc hard fail |
| Recommended next slice | §14: rubric class + loaded evaluator + tests + standalone TS-entry detail wiring + `/full` propagation + trust_read_api_current.md update |
