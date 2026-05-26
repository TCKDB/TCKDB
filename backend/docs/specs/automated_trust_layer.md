# Automated Trust / Evidence Layer

**Status:** draft spec — design only, no code yet
**Date:** 2026-05-26
**Scope:** Backend only. No changes to ARC, `tckdb-client`, upload schemas,
ingest pipelines, or curator workflow. No LLM dependency introduced. No
frontend or public SQL/GraphQL surface defined.
**Audience:** TCKDB backend maintainers, future trust-evaluator implementers,
read-API authors.

---

## 1. Why this exists

TCKDB has matured past the point where manual curator review can gate
public usefulness. Submissions are arriving faster than humans can sign
them off, but most submissions are *not* wrong — they are simply
**unreviewed**. The current read surface treats unreviewed records as a
second-class state that needs a curator before it becomes useful. That is
the wrong default.

The premise of this spec:

> **TCKDB should not be curator-gated. TCKDB should be trust-stratified.**

A valid, schema-validated, provenance-rich computational record is useful
**now**, with an honest evidence label attached, even if no human has
signed off on it. A record that fails hard validation, has been
rejected, or has been deprecated should remain excluded by default.

This document specifies the automated layer that produces that evidence
metadata deterministically, so the read API can:

1. Show users *which* checks passed and *which* are missing on every
   record.
2. Exclude rejected / deprecated / hard-failed records by default.
3. Surface curator review as an **elevation path**, not a gate.
4. Keep the LLM strictly out of the trust score (LLM stays a
   precheck-only advisor — see `SubmissionPrecheckLabel`,
   `SubmissionAuditEventKind.llm_precheck_*`).

---

## 2. Non-goals

The following are explicitly out of scope for this spec:

- LLM implementation, RAG, or LLM-derived scoring.
- Frontend implementation (badge rendering, filters UI).
- Manual curation UI / workflow changes.
- New upload schemas or ingest pipeline changes.
- ARC-specific behavior or ARC-side adapters.
- Public SQL, GraphQL, or any new external surface beyond the read API
  fragment defined in §10.
- Scientific *correctness* certification. The system labels evidence,
  not truth.
- Persisted trust tables (see §11 — possibly added later, not in MVP).

---

## 3. Core principles

These are load-bearing. If a future change violates one of these, it is
no longer this design.

1. **Evidence completeness is not scientific correctness.** A "well
   supported" label means "the upload carries the metadata one would
   expect for this kind of record", not "this result is right."
2. **The metric is not called a quality score.** Prefer
   `evidence_completeness` / `provenance_completeness`. Never
   `quality_score`, `truth_score`, `confidence_score`,
   `certified_score`.
3. **Scoring is deterministic and checklist-derived.** Given the same
   record at the same rubric version, two evaluators must produce the
   same passed/missing/warning sets.
4. **The LLM never assigns the score.** It may attach an advisory
   precheck label (see `SubmissionPrecheckLabel`) but does not pass,
   fail, weight, or veto any check in this spec.
5. **Expert review is optional elevation, not required visibility.**
   Records without curator review remain public by default.
6. **Public reads expose trust metadata by default.** Every scientific
   read of a reviewable record carries the trust fragment defined in
   §10.
7. **Rejected / deprecated / hard-failed records are excluded by
   default.** They are still queryable behind explicit opt-in filters.
8. **All checks are explainable.** Every passed, missing, or warning
   item resolves to a concrete record-level fact a reader can verify.
9. **Workflow-tool agnostic.** A check may reference *any* registered
   `workflow_tool` family, but no rubric is conditional on the
   submission having come from a specific tool (ARC, RMG,
   ChemPyMechWriter, …).
10. **Versioned rubrics.** Each rubric has an explicit
    `rubric_name@vN` identifier and is implemented in code (not data),
    so historical labels remain reproducible.

---

## 4. Relationship to existing models

This layer **composes with** the existing moderation model. It does not
replace any of it.

| Existing concept | What it owns | What the trust layer adds |
|---|---|---|
| `submission` (`SubmissionStatus`) | Contribution-event lifecycle (`pending` → `approved` / `rejected` / `superseded`). | Nothing. The trust layer reads the *records produced by* a submission, not the submission itself. |
| `submission_audit_event` | Append-only audit log of moderation actions. | The trust layer is a *reader* of audit events (e.g. to know whether LLM precheck ran), never a writer. |
| `record_review` (`RecordReviewStatus`: `not_reviewed`, `under_review`, `approved`, `rejected`, `deprecated`) | Per-record curator state, one row per `(record_type, record_id)`. | The trust layer reads `record_review.status` as **input** to `trust_status`. It never mutates this table. |
| `submission_record_link` | Traceability index from a submission to the records it created. | Used by the evaluator to recover the originating submission for advisory data (LLM precheck label, audit-event-derived warnings). |
| `calc_geometry_validation` (`ValidationStatus`: `passed`, `warning`, `fail`) | Per-calculation automatic geometry validation. | Read as **input** to several rubric checks. A `fail` here propagates into the `hard_failed` family (see §7). |
| `calculation_dependency` | DAG of calculations (opt → freq → sp, etc.). | Read as **input** to "is there an opt source / freq source / sp source?" checks. |
| `calculation_artifact` | Stored input/output files. | Read as **input** to "are calculation artifacts retained?" checks. |
| `calculation_parameter` | Parsed ESS execution parameters. | Read as **input** to a small set of "is SCF stability declared?", "is symmetry behaviour recorded?" checks where applicable. |
| `thermo_source_calculation`, `kinetics_source_calculation`, `statmech_source_calculation`, `transport_source_calculation` | Per-scientific-product source-calculation linkage rows. | Read as **input** to "is there a source calc with role X?" checks. |
| `SubmissionPrecheckLabel` | Advisory LLM label on the originating submission. | Surfaced under `trust.llm_precheck` as advisory metadata only. |

**The trust layer does not introduce a new persisted status enum in
MVP.** See §11 for the optional-cache table that may follow.

---

## 5. Concepts

### 5.1 `EvidenceCheck`

A single, deterministic, boolean (or `passed`/`warning`/`not_applicable`)
inspection over a record. Each check has:

- `name`: stable identifier (e.g. `irc_evidence_present`).
- `rubric`: which rubric(s) this check belongs to.
- `weight`: integer (typically 1; 2 for load-bearing items like
  `source_calculations_present`).
- `kind`: one of:
  - `required` — must pass to reach `well_supported`.
  - `optional` — contributes to completeness but absence does not
    block any label.
  - `warning` — its absence (or its `warning` result on a
    tri-state source like `ValidationStatus`) is surfaced under
    `warnings[]`.
- `applies_when`: predicate over the record (e.g.
  `kinetics.model_kind == arrhenius`). When the predicate is false the
  check is `not_applicable` and contributes to **neither** numerator
  nor denominator.
- `explain`: short human string surfaced under
  `missing_checks` / `warnings`.

### 5.2 `EvidenceRubric`

A versioned bundle of checks. Identified as
`<name>@v<n>` (e.g. `computed_kinetics_v1`). Each rubric defines:

- `applies_to`: a record-type discriminator (e.g.
  `kinetics` with `scientific_origin == computed`).
- `checks`: ordered list of `EvidenceCheck`.
- `label_thresholds`: explicit mapping from
  `passed_weight / possible_weight` ratio to a label (see §6).
- `requires_no_hard_fail`: list of hard-fail signals that, if present,
  force the rubric output into the `hard_failed` family regardless of
  completeness ratio.

A record may match **at most one** rubric per evaluator call. Selection
is by record type plus the `applies_to` discriminator. Ambiguity is a
bug; the evaluator must raise rather than guess.

### 5.3 `EvidenceCompleteness`

The numeric output of evaluating a rubric against a record:

```text
passed_weight / possible_weight
```

where `possible_weight` excludes `not_applicable` checks. Stored
internally as a ratio; never exposed as a percentage in the read
fragment (see §10).

### 5.4 `EvidenceBadge`

The human-facing summary derived from the completeness ratio and the
hard-fail check (see §6 for thresholds). One of:

- `well_supported`
- `mostly_supported`
- `partial`
- `sparse`
- `unsupported`
- `hard_failed`

### 5.5 `EvidenceWarning`

A check result of kind `warning`, surfaced as a string in
`evidence.warnings[]`. Warnings do **not** lower the completeness ratio
(by definition warning checks contribute zero weight). They are
informational signals only.

### 5.6 `TrustStatus`

A **computed** label that fuses `record_review.status`,
`EvidenceBadge`, and any `hard_failed` signal into a single value the
read API can filter on. See §7 for the full vocabulary and the
derivation table. **Not stored in MVP** — recomputed on read.

### 5.7 `ReviewStatus`

A pass-through of `RecordReviewStatus` from the existing `record_review`
row. The trust fragment surfaces it as `trust.review_status` so clients
do not have to fetch `record_review` separately.

### 5.8 `TrustFragment`

The JSON shape returned under `trust:` on every scientific read. See
§10 for the schema.

---

## 6. Evidence completeness model

The numeric formula is intentionally simple:

```text
evidence_completeness = passed_weight / possible_weight
```

with `possible_weight` excluding `not_applicable` checks.

The human-facing display **never leads with the number.** It leads with
the label, then the passed / missing / warning sets. The number is
available under `evidence.completeness_ratio` for clients that want it,
but the spec deliberately does not expose it as a percentage to avoid
the "quality score" framing.

### 6.1 Default label thresholds

| Ratio range | Label |
|---|---|
| ≥ 0.90 *and* every `required` check passed | `well_supported` |
| ≥ 0.75 | `mostly_supported` |
| ≥ 0.50 | `partial` |
| ≥ 0.25 | `sparse` |
| < 0.25 | `unsupported` |
| any `hard_fail` signal present | `hard_failed` (overrides all of the above) |

Rubrics may override these thresholds via `label_thresholds`. The
default is conservative on purpose: `well_supported` requires both a
strong ratio **and** zero failed required-checks.

### 6.2 Example output

```json
{
  "evidence": {
    "rubric": "computed_kinetics_v1",
    "label": "well_supported",
    "passed_checks": 8,
    "possible_checks": 11,
    "missing_checks": [
      "irc_evidence",
      "uncertainty",
      "scf_stability"
    ],
    "warnings": [
      "geometry_validation_not_available"
    ],
    "is_certified": false
  }
}
```

The `is_certified` field is **always** the result of curator action,
never of the automated layer. Automated evaluation can never set it to
`true`.

---

## 7. Trust status vocabulary

The proposed vocabulary, with how each value is derived from existing
state. **In MVP, none of these are persisted** — the evaluator computes
them on read. See §11 for the persistence question.

| `trust_status` | Derivation | Visible by default? |
|---|---|---|
| `raw_uploaded` | Originating submission still `pending`; record exists but has not yet passed schema validation. | No (still moderation-internal) |
| `schema_validated` | Submission reached `precheck_passed`; record schema-valid, but rubric not yet runnable (e.g. orchestrator still attaching children). Transient. | No |
| `auto_validated` | `record_review.status ∈ {not_reviewed, under_review}` *and* evidence label ∈ `{well_supported, mostly_supported}` *and* no hard fail. | **Yes** |
| `auto_validated_with_warnings` | Same as `auto_validated` but `evidence.warnings` is non-empty. | **Yes** |
| `needs_attention` | `record_review.status ∈ {not_reviewed, under_review}` *and* evidence label ∈ `{partial, sparse, unsupported}` *and* no hard fail. | **Yes** (visible with the label that explains why) |
| `expert_reviewed` | `record_review.status == approved`. | **Yes** |
| `benchmark_reference` | Curator explicitly elevates an `approved` record to benchmark status (curator action, future enum value or `record_review` flag — out of scope here, reserved). | **Yes** |
| `deprecated` | `record_review.status == deprecated`. | No (opt-in via `include_deprecated=true`) |
| `rejected` | `record_review.status == rejected`. | No (opt-in via `include_rejected=true`) |
| `hard_failed` | Any of: `calc_geometry_validation.status == fail` on a load-bearing source calc; failing required structural check (e.g. reactant species missing on a `reaction_entry`); explicit `submission` rejection that has not yet propagated to `record_review`. | No (opt-in via dedicated debug filter; not part of public read) |

### 7.1 Conflict with existing enums

This vocabulary intentionally **overlaps** with `RecordReviewStatus`
(`approved`, `rejected`, `deprecated`). That overlap is deliberate:
the overlapping values are the same concept and the computed
`trust_status` collapses to the `record_review` value directly. The
*new* values (`auto_validated`, `auto_validated_with_warnings`,
`needs_attention`, `raw_uploaded`, `schema_validated`,
`benchmark_reference`, `hard_failed`) cover what `record_review`
deliberately does not — the un-reviewed-but-evaluated middle ground.

**Recommendation:** keep `trust_status` as a **computed read-layer
value**, not a stored column. Rationale:

- `RecordReviewStatus` is already the durable per-record state and
  the system of record for human curator decisions.
- `SubmissionStatus` is already the durable per-contribution state.
- The automated values are derivations over (`record_review.status`,
  evidence label, hard-fail flags). Persisting them creates a third
  source of truth that must be kept in sync.
- The completeness label *does* benefit from caching once rubrics
  stabilize — see §11 for the optional `trust_evaluation` table that
  caches the evaluation result, not the status. The status is then
  still derived at read time from
  `(record_review.status, cached_evaluation.label, hard_fail)`.

---

## 8. Hard fails

A hard fail is **never** the result of low evidence completeness. Low
completeness produces `unsupported`, not `hard_failed`. Hard fails are
discrete structural failures recorded elsewhere in the schema:

| Signal | Source |
|---|---|
| `geometry_validation_failed` | `calc_geometry_validation.status == fail` on a load-bearing source calc for the record's rubric. |
| `missing_required_identity` | `reaction_entry` with no resolved reactants/products; `species_entry` with no `species`; etc. The schema's NOT NULL constraints catch most of this; the rubric catches the corner cases (e.g. cardinality). |
| `submission_rejected_pending_propagation` | Originating submission is `rejected` but `record_review` row has not been updated. (Edge case during moderation transitions.) |

Hard-failed records are **excluded by default** from public reads.
They remain visible to admin / curator surfaces and to the originating
submitter.

---

## 9. Rubrics (MVP)

Each rubric below lists its purpose, the records it applies to, and
its check set. Checks are grouped as `required` (R), `optional` (O),
or `warning` (W). `applies_when` predicates are spelled out only where
non-obvious.

The check set is the **MVP shape**; weights and exact predicate
wording will be tuned during implementation and bumped to `vN+1`
rather than mutated in place.

### 9.1 `computed_kinetics_v1`

**Purpose:** evidence checklist for a computed `kinetics` record
(`scientific_origin == computed`).

**Applies to:** `kinetics` rows whose `reaction_entry` exists and whose
upstream calculations are linked via `kinetics_source_calculation`.

| Check | Kind | Notes |
|---|---|---|
| `reaction_entry_present` | R | FK present. |
| `kinetics_model_present` | R | `KineticsModelKind` set. |
| `arrhenius_parameters_complete` | R | `applies_when` model is `arrhenius`-family; requires A, n, Ea. |
| `temperature_range_present` | O | `t_min` and `t_max` both set. |
| `temperature_range_valid` | O | `applies_when` range is present; `t_min < t_max`, both in physical range. |
| `source_calculations_present` | R | At least one row in `kinetics_source_calculation`. |
| `ts_energy_source_present` | O | `KineticsCalculationRole.ts_energy` row present. |
| `reactant_energy_sources_present` | O | One per reactant (cardinality from `reaction_entry`). |
| `product_energy_sources_present` | O | One per product. |
| `frequency_source_present` | O | `KineticsCalculationRole.frequency` row present (TS and reactants/products as applicable). |
| `level_of_theory_present` | R | All source calcs resolve to a `level_of_theory`. |
| `software_release_present` | O | All source calcs resolve to a `software_release`. |
| `workflow_tool_release_present` | O | At least one source calc carries `workflow_tool_release`. |
| `calculation_artifacts_present` | O | At least one source calc has ≥1 `calculation_artifact`. |
| `geometry_validation_present` | O | TS geom has a `calc_geometry_validation` row. |
| `geometry_validation_passed_or_warning` | W | `applies_when` row exists; `warning` if `status == warning`; `fail` is a hard-fail signal, not a warning. |
| `irc_evidence_present` | O | TS calc has an IRC follow-up (`calculation_dependency` with role implying IRC, or an explicit `calc_irc_result`). |
| `path_search_evidence_present` | O | TS has a `calc_path_search_result` chain. |
| `uncertainty_present` | O | `KineticsUncertaintyKind` set and uncertainty fields populated. |
| `tunneling_metadata_present_if_claimed` | O | `applies_when` kinetics declares tunneling; requires a tunneling model identifier. |

**Hard-fail signals:** `geometry_validation_failed` on the TS source
calc; `missing_required_identity` (reaction_entry without
reactants/products).

**Example output:** see §6.2.

### 9.2 `computed_thermo_v1`

**Purpose:** evidence checklist for a computed `thermo` record.

**Applies to:** `thermo` rows whose `species_entry` exists and whose
upstream calculations are linked via `thermo_source_calculation`.

| Check | Kind | Notes |
|---|---|---|
| `species_entry_present` | R | |
| `thermo_model_present` | R | NASA / Wilhoit / scalar / table. |
| `scalar_or_nasa_or_points_present` | R | At least one representation populated. |
| `temperature_range_present_if_applicable` | O | `applies_when` model is range-bearing (NASA, Wilhoit). |
| `temperature_range_valid` | O | `applies_when` range is present. |
| `source_calculations_present` | R | |
| `opt_source_present` | O | `ThermoCalculationRole.opt`. |
| `freq_source_present` | O | `ThermoCalculationRole.freq`. |
| `sp_or_composite_source_present_if_applicable` | O | `applies_when` thermo derives from an SP-style energy (not pure empirical). |
| `statmech_present` | O | Linked `statmech` row if model is computed-from-statmech. |
| `frequency_scale_factor_present_if_applicable` | O | `applies_when` frequencies entered the pipeline. |
| `level_of_theory_present` | R | |
| `software_release_present` | O | |
| `workflow_tool_release_present` | O | |
| `calculation_artifacts_present` | O | |
| `geometry_validation_present` | O | |
| `geometry_validation_passed_or_warning` | W | |
| `uncertainty_present` | O | |

**Hard-fail signals:** `geometry_validation_failed` on the species opt
geom; missing `species_entry`.

### 9.3 `computed_statmech_v1`

**Purpose:** evidence checklist for `statmech` (partition-function /
mode treatment record).

| Check | Kind | Notes |
|---|---|---|
| `species_entry_present` | R | |
| `rigid_rotor_kind_present` | R | `RigidRotorKind`. |
| `treatment_kind_present` | R | `StatmechTreatmentKind`. |
| `frequencies_present` | R | At least one mode row. |
| `torsion_treatment_recorded_if_applicable` | O | `applies_when` molecule has internal rotors. |
| `frequency_scale_factor_present` | O | |
| `source_calculations_present` | R | |
| `freq_source_present` | O | `StatmechCalculationRole.freq`. |
| `opt_source_present` | O | |
| `level_of_theory_present` | R | |
| `software_release_present` | O | |
| `geometry_validation_present` | O | |
| `geometry_validation_passed_or_warning` | W | |

**Hard-fail signals:** `missing_required_identity`.

### 9.4 `computed_transport_v1`

**Purpose:** evidence checklist for a computed `transport` record.

| Check | Kind | Notes |
|---|---|---|
| `species_entry_present` | R | |
| `transport_model_present` | R | LJ parameters (sigma, epsilon) or equivalent. |
| `source_calculations_present` | O | `applies_when` transport is computational rather than empirical. |
| `level_of_theory_present` | O | `applies_when` computational. |
| `software_release_present` | O | |
| `workflow_tool_release_present` | O | |
| `uncertainty_present` | O | |
| `temperature_range_present_if_applicable` | O | |

**Hard-fail signals:** missing `species_entry`.

### 9.5 `computed_calculation_v1`

**Purpose:** evidence checklist for an individual `calculation` row
viewed as a primary record (e.g. via `/scientific/calculations/...`).

| Check | Kind | Notes |
|---|---|---|
| `calculation_type_present` | R | `CalculationType`. |
| `level_of_theory_present` | R | |
| `software_release_present` | O | |
| `workflow_tool_release_present` | O | |
| `input_geometry_present` | R | At least one input geometry linked. |
| `output_geometry_present` | O | `applies_when` calc type produces a geometry (opt, ts, irc). |
| `result_block_present` | R | The appropriate `calc_*_result` for the calculation type. |
| `quality_recorded` | O | `CalculationQuality` set. |
| `geometry_validation_present` | O | `applies_when` calc type is opt or ts. |
| `geometry_validation_passed_or_warning` | W | |
| `scf_stability_present_if_claimed` | O | `applies_when` calc claims SCF stability checked in parameters. |
| `artifacts_present` | O | At least one `calculation_artifact`. |
| `parameters_parsed` | O | At least one `calculation_parameter` row. |

**Hard-fail signals:** `geometry_validation_failed` on the calc's own
output geometry; `result_block` absent for a calc declared as
successful.

### 9.6 `experimental_kinetics_v1`

**Purpose:** evidence checklist for an **experimental** `kinetics`
record (`scientific_origin == experimental`).

| Check | Kind | Notes |
|---|---|---|
| `reaction_entry_present` | R | |
| `kinetics_model_present` | R | |
| `arrhenius_parameters_complete` | R | If model is Arrhenius-family. |
| `temperature_range_present` | R | Experimental kinetics without a measured range is suspect. |
| `temperature_range_valid` | O | |
| `literature_source_present` | R | At least one `literature` row attached. |
| `literature_doi_or_isbn_resolved` | O | `applies_when` literature row exists; DOI/ISBN populated and validated per `docs/literature_policy.md`. |
| `uncertainty_present` | O | |
| `pressure_dependence_recorded_if_applicable` | O | `applies_when` model declares P-dependence. |

**Hard-fail signals:** `missing_required_identity` (reaction_entry
without reactants/products); zero literature sources.

### 9.7 `experimental_thermo_v1`

**Purpose:** evidence checklist for an experimental `thermo` record.

| Check | Kind | Notes |
|---|---|---|
| `species_entry_present` | R | |
| `thermo_model_present` | R | |
| `scalar_or_nasa_or_points_present` | R | |
| `temperature_range_present_if_applicable` | O | |
| `literature_source_present` | R | |
| `literature_doi_or_isbn_resolved` | O | |
| `uncertainty_present` | O | |

**Hard-fail signals:** zero literature sources; missing species.

---

## 10. Read API: `trust` fragment

Every scientific read of a reviewable record includes a `trust` block.
Existing endpoints already carry the smaller `RecordReviewBadge` —
this block is its superset and replaces the badge field name with a
fully expanded fragment.

### 10.1 Shape

```json
{
  "trust": {
    "review_status": "not_reviewed",
    "trust_status": "auto_validated_with_warnings",
    "evidence": {
      "rubric": "computed_kinetics_v1",
      "label": "well_supported",
      "completeness_ratio": 0.73,
      "passed_checks": 8,
      "possible_checks": 11,
      "missing_checks": [
        "irc_evidence",
        "uncertainty"
      ],
      "warnings": [
        "scf_stability_not_checked"
      ]
    },
    "llm_precheck": {
      "enabled": false,
      "label": "not_run"
    },
    "is_certified": false
  }
}
```

### 10.2 Field semantics

| Field | Source | Notes |
|---|---|---|
| `trust.review_status` | `record_review.status` (default `not_reviewed` when row absent) | Always present. |
| `trust.trust_status` | Computed per §7. | Always present. |
| `trust.evidence.rubric` | Selected rubric name + version. | Omitted only if no rubric applies (very rare; e.g. provenance-only entities). |
| `trust.evidence.label` | Per §6.1. | |
| `trust.evidence.completeness_ratio` | `passed_weight / possible_weight`, rounded to 2 dp. | Not exposed as a percentage; reader formats as it likes. |
| `trust.evidence.passed_checks` | Count of `kind in {required, optional}` checks that passed. | |
| `trust.evidence.possible_checks` | Same denominator, excluding `not_applicable`. | |
| `trust.evidence.missing_checks[]` | Names of checks that did not pass and are not `not_applicable`. | |
| `trust.evidence.warnings[]` | Names of warning checks that fired. | |
| `trust.llm_precheck.enabled` | `true` iff the originating submission had an LLM precheck step (per `SubmissionAuditEventKind.llm_precheck_*`). | |
| `trust.llm_precheck.label` | `passed` / `flagged` (from `SubmissionPrecheckLabel`) or `not_run`. | Advisory only. Does not influence `trust_status`. |
| `trust.is_certified` | `true` iff `record_review.status == approved` *and* curator explicitly marked certification. | Reserved; default `false`. |

### 10.3 Default filtering on list endpoints

Every `/scientific/*/search` endpoint applies these defaults:

```text
include:
  trust_status in {
    auto_validated,
    auto_validated_with_warnings,
    needs_attention,
    expert_reviewed,
    benchmark_reference
  }

exclude:
  trust_status in {
    rejected,
    deprecated,
    hard_failed,
    raw_uploaded,
    schema_validated
  }
```

Explicit opt-in filters:

| Query parameter | Effect |
|---|---|
| `include_unreviewed=false` | Drop `auto_validated*` and `needs_attention`; keep only `expert_reviewed` / `benchmark_reference`. |
| `include_warnings=false` | Drop `auto_validated_with_warnings`. |
| `include_deprecated=true` | Include `deprecated`. |
| `include_rejected=true` | Include `rejected`. |
| `review_status=approved_only` | Equivalent to `include_unreviewed=false`. |
| `trust_status=expert_reviewed` | Strict whitelist filter. |
| `min_evidence_label=mostly_supported` | Filter to records whose evidence label is at least this strong (ordering per §6.1). |

**Ordering tie-breaker:** when two records compare equal on the
client's chosen sort key, fall back to
`(trust_status_priority, completeness_ratio desc, created_at desc,
record_id asc)`. The priority order is the table in §7 from
`expert_reviewed` down to `auto_validated`. Deterministic ordering is
required so pagination is stable across requests.

---

## 11. Storage strategy

### 11.1 Option A — fully computed on read (recommended for MVP)

- No new tables.
- The evaluator is a pure function over existing rows.
- Trust fragments are built inside the existing read serializers.

**Pros:**

- Zero migration cost.
- Single source of truth: `record_review` for curator state, raw
  domain rows for evidence, plus rubric code.
- Rubric bumps (`v1 → v2`) take effect immediately on next read.
- Cannot drift.

**Cons:**

- Recomputed on every read. For records with deep dependency graphs
  (e.g. kinetics with many source calcs and artifacts), this can
  amount to non-trivial work.
- Cannot easily filter or sort on evaluator output at the database
  level without re-running the evaluator per row.

### 11.2 Option B — persisted, versioned trust evaluations

Add a new table `trust_evaluation` (separate Alembic revision; do NOT
edit the initial migration):

```text
trust_evaluation
  id                 BIGINT PK
  record_type        SubmissionRecordType    -- reuses existing enum
  record_id          BIGINT
  rubric_name        TEXT                    -- e.g. computed_kinetics_v1
  rubric_version     INT
  label              EvidenceBadge           -- new enum (computed values only)
  completeness_ratio NUMERIC(5,4)
  passed_checks      INT
  possible_checks    INT
  missing_checks     JSONB                   -- array of strings
  warnings           JSONB                   -- array of strings
  hard_fail_reason   TEXT NULL
  evaluated_at       TIMESTAMPTZ
  evaluator_version  TEXT                    -- git sha or semver of evaluator
  UNIQUE (record_type, record_id, rubric_name, rubric_version)
```

Notes:

- `trust_status` remains **computed**, even with this table — it is
  derived from `(record_review.status, label, hard_fail_reason)` at
  read time. We do not need to persist a third overlapping status
  enum.
- Recomputation is triggered by:
  - Domain mutations that touch the record or its source calcs
    (worker job; out of scope here).
  - Rubric version bumps (backfill job).
  - Manual re-evaluation request from curators.
- The `UNIQUE` constraint deliberately includes `rubric_version` so
  historical evaluations are preserved. Reads pick the latest
  version for the record type.

**Pros:**

- Filterable / sortable at the database level.
- Decouples read latency from rubric complexity.
- Audit trail of what each rubric version said about each record.

**Cons:**

- Requires a refresh pipeline. Stale rows are worse than no rows.
- New migration, new code path for invalidation, new failure mode.

### 11.3 Recommendation

**Start with Option A (computed). Add Option B only when there is a
real pressure point** — typically one of:

- Search endpoints want to filter or sort on `evidence.label` and
  cannot afford to recompute per row.
- A real interactive UI on top of TCKDB is fielding enough traffic
  to make recomputation cost visible.
- Curators want to query "how many records would a rubric v2 promote
  from `needs_attention` to `auto_validated`?" — this needs
  persisted historical evaluations.

Until then, the evaluator's output is fast enough to build inside the
read serializer for record-detail endpoints, and search endpoints can
filter by `record_review.status` alone (which is already a column).
**If Option B is later adopted, it requires a new Alembic revision per
`.claude/rules/migration-rules.md`; the initial migration must not be
edited.**

---

## 12. Evaluator service shape (informative)

This section is descriptive, not normative — the implementation may
shape itself differently. It exists to make the contract concrete.

```text
app/services/trust/
  __init__.py
  rubrics/
    __init__.py
    base.py                # EvidenceCheck, EvidenceRubric, registry
    computed_kinetics_v1.py
    computed_thermo_v1.py
    computed_statmech_v1.py
    computed_transport_v1.py
    computed_calculation_v1.py
    experimental_kinetics_v1.py
    experimental_thermo_v1.py
  evaluator.py             # evaluate(record) -> EvaluationResult
  fragment.py              # build_trust_fragment(record, evaluation) -> TrustFragment
```

The evaluator entrypoint:

```text
def evaluate(record: ReviewableRecord) -> EvaluationResult:
    rubric = select_rubric(record)
    if rubric is None:
        return EvaluationResult.not_applicable(record)
    checks = [run_check(c, record) for c in rubric.checks]
    return EvaluationResult.from_checks(rubric, checks)
```

Each check is a small pure function taking the record (and its
already-loaded children via the existing service-layer loaders) and
returning one of `passed`, `failed`, `warning`, `not_applicable`.
No check is allowed to issue its own database query — the evaluator
receives the loaded record graph from the read serializer. This keeps
the evaluator deterministic and avoids N+1 surprises.

---

## 13. Test plan

The trust layer is testable as a pure function over fixture records,
which makes the test surface unusually tractable.

Required tests (paths illustrative):

| Test | What it proves |
|---|---|
| `tests/trust/test_kinetics_complete.py` | Computed kinetics with full provenance → `well_supported`, `passed_checks == possible_checks`, empty `missing_checks`. |
| `tests/trust/test_kinetics_missing_irc.py` | Same record minus IRC evidence → label drops by at most one step; `missing_checks` contains `irc_evidence_present`; no spurious warnings. |
| `tests/trust/test_kinetics_no_sources.py` | Kinetics with zero `kinetics_source_calculation` → `source_calculations_present` fails; label `sparse` or below; required-check failure recorded. |
| `tests/trust/test_thermo_nasa.py` | Thermo with NASA polynomials → all NASA-applicable checks live; scalar-only checks `not_applicable`. |
| `tests/trust/test_thermo_scalar_only.py` | Thermo with scalar-only data → range checks `not_applicable`; completeness ratio reflects reduced denominator, not failure. |
| `tests/trust/test_experimental_kinetics.py` | Experimental kinetics with literature, no calcs → still reaches `mostly_supported`; `source_calculations_present` is `not_applicable` under the experimental rubric. |
| `tests/trust/test_exclusion_defaults.py` | Records in `rejected` / `deprecated` / `hard_failed` are absent from default list responses; appear with explicit opt-in. |
| `tests/trust/test_deterministic_order.py` | Two records with identical `completeness_ratio` sort by the §10.3 tie-breaker stably across runs. |
| `tests/trust/test_fragment_shape.py` | Generated fragment matches the §10.1 schema exactly (no extra keys, no missing required keys). |
| `tests/trust/test_rubric_version_stability.py` | Re-running `computed_kinetics_v1` on a frozen fixture produces byte-identical output. (Pins the contract.) |
| `tests/trust/test_geometry_validation_hard_fail.py` | A record whose TS geom has `ValidationStatus.fail` returns `trust_status == hard_failed`, regardless of how many other checks pass. |
| `tests/trust/test_llm_precheck_advisory.py` | Toggling LLM precheck `passed` ↔ `flagged` on the originating submission **does not** change `trust_status`, `label`, `passed_checks`, or `missing_checks`. |
| `tests/trust/test_no_llm_dependency.py` | Evaluator runs and produces a complete trust fragment with the LLM provider disabled / unconfigured. |
| `tests/trust/test_workflow_tool_agnostic.py` | A record whose `workflow_tool` is unknown (no ARC, no RMG) still evaluates against the rubric without raising. |

---

## 14. Recommended implementation order

1. **Land the rubric base classes** (`EvidenceCheck`, `EvidenceRubric`,
   registry) with `computed_calculation_v1` only. Smallest scope.
2. **Wire `trust` fragment into the existing calculation read endpoint
   under a flag** (e.g. `include=trust`). Existing `RecordReviewBadge`
   is left intact during the transition.
3. **Add `computed_kinetics_v1` and `computed_thermo_v1`.** These are
   the highest-value rubrics for downstream consumers.
4. **Add `computed_statmech_v1`, `computed_transport_v1`.**
5. **Add the experimental rubrics** (`experimental_kinetics_v1`,
   `experimental_thermo_v1`). These need the literature-policy
   helpers to be settled.
6. **Promote `trust` fragment to always-on** on detail endpoints.
   Remove the include flag.
7. **Add the default exclusion filters** on `/scientific/*/search`
   endpoints. Document the explicit opt-ins (§10.3).
8. **(Optional, later)** Add the `trust_evaluation` table and an
   invalidation worker. New Alembic revision per
   `.claude/rules/migration-rules.md`.

Each step ships in isolation, with tests, behind an additive read
contract.

---

## 15. Open design questions

These are intentionally left for implementation review.

1. **Weighting.** All checks default to weight 1 in this spec. Some
   checks (e.g. `source_calculations_present`) are clearly more
   load-bearing than others. Should weights be in the rubric or
   should `kind=required` be enough? Tentative answer: kinds are
   enough for MVP; revisit only if labels feel mis-calibrated after
   real records run through.

2. **`benchmark_reference` mechanism.** This spec reserves the value
   but does not define how a curator promotes a record into it.
   Options: a flag column on `record_review`, a new
   `RecordReviewStatus` value, or a separate curator-action table.
   Out of scope here; resolve when benchmark sets are actually being
   curated.

3. **Rubric override per `submission_kind`.** Some kinetics records
   come in as `computed_reaction` submissions and some as standalone
   `kinetics`. Are these the same rubric? Tentative answer: yes —
   the rubric selects on the *resulting record type*, not the
   submission kind. Submission kind is a routing concern.

4. **PDep / network records.** No rubric defined here. These tables
   have no production data yet (table-scoped exception in
   `.claude/rules/migration-rules.md`); the rubric can be added
   alongside the first real network records.

5. **Re-evaluation cadence under Option B.** If we eventually
   persist evaluations, who triggers refresh? Domain mutation hook?
   Periodic backfill? Hybrid? This is moot under Option A.

6. **Calibration loop.** Once real records flow through, we will
   want a diagnostic endpoint (admin-only) that reports, per rubric,
   the histogram of completeness ratios and the most common
   `missing_checks`. This is a tool for tuning rubric versions, not
   a public-facing thing. Out of scope for v1 of this spec.

---

## 16. Glossary

- **Hard fail** — a discrete, evidenced failure that disqualifies a
  record from default public visibility, *independent* of the
  evidence completeness ratio.
- **Evidence completeness** — `passed_weight / possible_weight`. Not
  a quality score. Not a correctness claim.
- **Rubric** — a versioned, code-defined bundle of checks tied to a
  record type.
- **Trust status** — the computed read-time label fusing review
  state, evidence label, and hard-fail signals.
- **Certified** — strictly a curator-action outcome. Automated
  evaluation never produces `is_certified=true`.
- **Advisory** — describes any signal (LLM precheck, third-party
  agent label) that surfaces to readers but does not influence the
  evidence completeness ratio or the trust status.
