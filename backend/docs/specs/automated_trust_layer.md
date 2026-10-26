# Automated Trust / Evidence Layer

**Status:** implemented (design doc, retained for rationale) — the
deterministic evaluator, rubrics, and read-API wiring described below have
shipped (`app/services/trust/{evaluator,rubrics,fragment,models}.py`).
This document is no longer "design only"; treat
[`trust_read_api_current.md`](trust_read_api_current.md) as the
authoritative, up-to-date statement of which endpoints and rubrics are
actually wired — this file documents the design rationale and may lag
behind the shipped surface in specific details (see the "Current wiring"
note before §10).
**Date:** 2026-05-26 (original design); status line updated 2026-07-13.
**Scope:** Backend only. No changes to ARC, `tckdb-client`, upload schemas,
ingest pipelines, or curator workflow. No LLM dependency introduced. No
frontend or public SQL/GraphQL surface defined.
**Audience:** TCKDB backend maintainers, future trust-evaluator implementers,
read-API authors.

---

## Trust contract: when a hard-fail badge is an honest judgement

Two rules in the deterministic rubrics share one question: when is a hard-fail badge
an honest judgement?

- **An automated geometry-validation `fail` is advisory, not a hard fail.** `calc_geometry_validation` documents its own `fail`
  as a curator-attention signal: connectivity perceived from XYZ is unreliable for weak complexes, stretched bonds, radicals
  and proton-transfer-like geometries. The evaluator no longer emits `geometry_validation_failed` or
  `geometry_validation_failed_for_source_calculation`, and so no longer emits
  `source_calculation_hard_failed_for_required_role` or `all_source_calculations_hard_failed` *because of* such a row. The
  failure stays visible: the geometry checks report `warning` (zero weight) for `fail` as well as for `warning`. Rejected
  calculation quality and a recorded artifact-integrity break still hard-fail exactly as before. A *confirmed* identity failure is
  a structure-determination finding judged by structure selection in its own role, not by this badge.
- **The transition state badge issues no frequency verdict.** Judging the representative (latest) result, or every attached
  result, is not sufficient: a saddle claim is about a *declared target* (the geometry it
  evaluates, the role bundle supporting it, a usable source, the owner's persisted treatment of extra modes), and the badge has none of
  those. "Latest decides" lets a bad rerun on another geometry condemn a coherent older bundle; "every result must contradict" lets a
  rejected or unrelated older one-mode result rescue a disproved bundle and ignores curvature stored on optimisation and composite jobs.
  So the badge no longer produces `frequency_source_has_zero_imaginary_modes_for_validated_ts` or
  `frequency_source_reaction_coordinate_not_designated_for_validated_ts` (the members stay so stored results parse). It also certifies
  nothing. What it adds is visibility: the warning `no_frequency_result_lacks_an_imaginary_mode` fires when any attached result with a
  recorded count reports zero imaginary modes (alone, older, newer or in disagreement; a null count is unknown and neither rescues nor
  condemns), and `reaction_coordinate_designated_for_ts` is `missing` for several modes without a designation. The scoped judgement is
  the structure assessment (`POST /scientific/transition-state-entries/{ref}/evidence/select`): it pins the geometry, ignores
  unusable and other-geometry results, reads opt/composite characterisation on its output geometry, leaves a same-target
  disagreement unresolved (`curvature_witnesses_disagree`), reports zero modes on the evaluated geometry as
  `curvature_contradicts_claim`, reports an undesignated extra mode as `reaction_coordinate_not_established`, and applies authorized
  adjudications. Reason mapping for the retired hard fails: zero modes becomes the warning above plus `curvature_contradicts_claim`;
  undesignated extra modes becomes the missing designation check plus `reaction_coordinate_not_established`.
  The trust projection of a TS entry carries that assessment: `trust.structure_assessment` holds its
  answer to one fixed evidence-only question (echoed as `task`), the zero-weight warning `declared_saddle_target_qualifies` restates
  it, and the frequency-presence checks read the result a declared target pins. The assessment runs on a single-entry detail read only; lists
  carry the status `not_assessed_on_lists` but read the declared pins in one statement per page, so the counts agree everywhere. It never moves the label, the completeness counts or `is_certified`.
- **Undesignated imaginary modes.** A second zero-weight warning,
  `no_frequency_result_has_undesignated_imaginary_modes`, fires when any attached frequency result with a recorded count reports
  two or more imaginary modes and does not designate the reaction coordinate. It reads the same source set as the zero-mode warning
  and applies the same rule to a null count (unknown: it neither rescues nor condemns), so an older or newer non-representative
  result stays visible even when the representative result is clean. It means that attached result needs investigation, not that the
  transition state is disproved, and it certifies nothing. `reaction_coordinate_designated_for_ts` is unchanged. The transition
  state page lists every warning in words, each ending "needs investigation".
- **A confirmed finding is applied where it is consumed.** The badge stays narrow (rejected quality, artifact custody). H298 and
  kinetics selection read live, supported `StructureEvidenceFinding` rows for the record's own source calculations in the roles it
  uses them for (`structure_selection.source_findings`): calculation-scope findings, geometry-scope findings on the geometry the role
  reads, and determination-scope findings only through a source role the product uses on the geometry that determination evaluates. A
  `role_invalidation` bites only its role; a curvature contradiction bites the frequency role and never a separately valid recorded
  energy; only an authorized same-subject adjudication settles a finding (history is kept); unresolved is unresolved, not blocked;
  an unreadable kind or version is disclosed, not promoted to a failure. A heuristic geometry `fail` is not read here at all.

**Versioning.** Every TCKDB-owned rubric is identified by a stable name plus a separate integer version, and every version is
currently 1: `computed_calculation`, `computed_kinetics`, `computed_thermo`, `computed_statmech`, `computed_ts_statmech`,
`computed_transport` and `computed_transition_state`. The reviewer-family machine-review recipe stamps the rubric name and
version, so a review made under a different rubric version, or whose context changed (the machine-review context carries the check
sets and the hard-fail reason), compares **stale** and a re-review is planned. `rubric_versions_mismatch` and
`context_hash_mismatch` are the reasons; the stored review row is never rewritten and keeps its own recipe and verdict as history.
The scientific-check families and the external-Cp runner keep their own keys. The `HardFailReason` members are kept so stored
results and reviews still parse; the two retired causes are simply no longer produced.

**Live consumers.** H298 and kinetics selection block on `evidence.hard_fail_reason`, so a record whose only flaw was an automated
geometry fail is now eligible, and fresh answers can differ from earlier ones. Each assessment carries
`evidence_rubric:<name>` and `evidence_rubric_version:<N>` entries in its advisory list, and each manifest records a structured
`assessment_semantics` block (`name` and `version`, `evidence_rubric` as `{"name": "computed_thermo", "version": 1}`, and
`source_findings` as `{"name": "structure_source_findings", "version": 1}`), every versioned thing a name beside an integer
version. Every selection manifest also opens with a root `format` block (`{"name": "thermo_selection_manifest", "version": 1}`),
which replaces the bare `manifest_format_version` integer. The selection `policy` version covers only the decision procedure; assessment
semantics are versioned separately (`ASSESSMENT_SEMANTICS_VERSION`, `1`). A manifest with no block, or a malformed or foreign
one, is refused. Replay reads the recorded eligibility and re-runs the ordering, never reassesses, and refuses semantics it does
not carry. See
`tests/services/thermo_selection/test_trust_correction_h298.py` for the exact probes.

## Recording a structure-evidence finding (curators and admins)

A finding is a curator's recorded judgment about one geometry, one calculation or one structure determination: that it is the
wrong identity, state or path, that a role it fills is invalid, that its characterization contradicts itself, or (as an
adjudication) that an earlier finding no longer stands. It is the only way the rule above can fire on a real deployment.

**Who has standing.** Only a curator or an admin. The route is `POST /api/v1/curation/structure-evidence-findings`, gated by the same
role check as the review-queue and supersession routes, so every finding written here has `authority = authorized_adjudication` and is
attributed (`created_by`). The request carries no authority and no author. A depositor's assertion about their own record
(`producer_assertion`) and a challenge by a third party are separate policy decisions; this route writes neither, and the request schema
has no field through which either could arrive.

**What a request names.** The subject by public ref (`geom_`, `calc_` or `sdet_`) with a matching `scope`; a `kind`; a `verdict`
(`invalidates`, `does_not_invalidate`, `unresolved`); a required `rationale`; a `role` exactly when the kind is `role_invalidation`;
optionally a supporting calculation and a paper by public ref. A cited paper must carry a DOI or an ISBN, as everywhere else a source
has to be identifiable. No request carries a row id and no refusal does either; ids are logged server-side.

**Append-only.** A finding is never edited or deleted: the API has no method for it and the database refuses both
(`trg_structure_evidence_finding_append_only`). A correction is an *adjudication* naming the earlier ruling and stating the
replacement verdict: `does_not_invalidate` (withdraw it), `unresolved` (reopen it) or `invalidates` (reinstate it, which is how an
earlier withdrawal is overturned). The route requires the named ruling to exist, to be about the same subject and to be still in
force. Corrections can themselves be corrected. A ruling is replaced once: the row is locked before the in-force check, so two
curators correcting the same ruling cannot both succeed. An ordinary finding that rules `does_not_invalidate` and names nothing is
refused (`structure_finding_dismissal_needs_target`): it would change nothing, and withdrawing a ruling is an adjudication.

**Effect.** Each chain of corrections is read at its head, the newest ruling in force, and a head that continues a `role_invalidation` or a
`contradictory_characterization` keeps that claim's role and scope (an adjudication has none of its own). An `invalidates` head excludes the evidence from H298, kinetics and structure
selection, scoped by role, exactly as described above. A `does_not_invalidate` head has no effect. An `unresolved` head leaves the
product unresolved, not excluded, as an ordinary `unresolved` finding does. Replacing one role's ruling leaves the other roles' rulings
standing, and a single append changes the effect, so there is no moment in which a record is wrongly selectable. History stays: a
replaced ruling reads `in_force: false` and names the adjudication that replaced it. A finding that no correction names reads as it always did.

**Reads.** `GET /api/v1/curation/structure-evidence-findings?subject_ref=...` (curator or admin) lists the findings about one subject with
whether each is still in force and what settled it, and for each ruling in force the kind and role selections apply (`applied_kind`,
`applied_role`), which for a correction continuing a role invalidation differ from the kind and role it was recorded with. It is a curator surface: curators adjudicate records the curated profile hides, so it is
not profile-filtered. The public surface is unchanged: findings reach readers through the selection documents, which already withhold a
hidden record's refs (the finding of a hidden subject is not loaded under the curated profile, so neither it nor the subject is named).

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
    stable `rubric_name` plus a separate integer `rubric_version` and is implemented in code (not data),
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
- `explain`: short human string explaining a `missing` or `warning`
  outcome.

### 5.2 `EvidenceRubric`

A versioned bundle of checks. Identified by a
stable name and a separate integer version (e.g. `computed_kinetics`, version 1). Each rubric defines:

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
    "rubric": "computed_kinetics",
    "rubric_version": 1,
    "label": "well_supported",
    "checks": {
      "reaction_entry_present": "passed",
      "kinetics_model_present": "passed",
      "irc_evidence_present": "missing",
      "uncertainty_present": "missing",
      "geometry_validation_not_available": "warning"
    },
    "passed_count": 8,
    "possible_count": 11,
    "evidence_completeness": 0.73,
    "is_certified": false
  }
}
```

As shipped, `checks` is an ordered map from check name to outcome, and
`passed_count` / `possible_count` are the numeric counterparts — the same
shape as the canonical read-fragment example in §10.1
(`EvidenceEvaluation` in `app/services/trust/models.py`). An earlier
draft of this example conflated the two (`passed_checks`/`possible_checks`
as bare integers); that shape was never implemented.

**Why a map and not four name lists.** `checks` replaced four parallel
arrays — `passed_checks`, `missing_checks`, `warning_checks`,
`not_applicable_checks`. Most check names are assertions ending in
`_present`, so a name inside a bucket named for its truth value read as a
double negative: `missing_checks: ["irc_evidence_present"]` meant *there
is no IRC evidence*, and every reader had to negate the name against the
bucket. Putting the outcome in the value removes that step
(`"irc_evidence_present": "missing"`) and makes *"did check X pass?"* one
lookup rather than a scan of up to four arrays. The migration is
mechanical: `x in missing_checks` becomes `checks.get(x) == "missing"`,
and `passed_checks` becomes
`[k for k, v in checks.items() if v == "passed"]`.

Two properties the map is required to keep:

- **The value is one of four outcomes, never a boolean.** `missing`
  counts against the record and is inside `possible_count`;
  `not_applicable` means the check could not be asked and is outside
  *both* numerator and denominator. Collapsing them to `false` would
  penalise a record for an unanswerable question — for instance,
  `geometry_validation_not_failed_for_source_calculations` is
  `not_applicable` precisely when
  `geometry_validation_present_for_source_calculations` is `missing`.
- **Key order is the rubric's declared check order and is stable.** Two
  evaluations of the same rubric serialise their keys identically, so a
  diff between two records lines up check for check. Consumers may rely
  on it.

Membership matches the arrays it replaced exactly: a check appears with
the outcome whose bucket it used to occupy, and only then. A warning-kind
check that did not fire is absent, as it was absent from all four arrays
— it carries zero weight, so counting it as `passed` would make the
number of `passed` entries disagree with `passed_count`.

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
wording will be tuned during implementation and given a new rubric version
rather than mutated in place.

### 9.1 `computed_kinetics`

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

### 9.2 `computed_thermo`

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

### 9.3 `computed_statmech`

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

### 9.3.1 `computed_ts_statmech` (owner-scoped)

A statmech row is owned by exactly one of a species entry or a transition-state entry (the
`statmech_exactly_one_subject` CHECK). `computed_statmech` asks for a species entry first, so a
transition-state-owned record could never pass its first required check and was badged `hard_failed` /
`species_entry_missing` however complete it was. A hard fail is a discrete structural signal (section 8), and that record
was internally consistent and validly owned, so the badge was a defect.

**The rubric.** `computed_ts_statmech` is `computed_statmech` with one check swapped: `transition_state_entry_present`
(required, "Statmech must be attached to a transition_state_entry.") replaces `species_entry_present`. Every other check is the
*same spec object*, so the two cannot drift. It adds no transition-state-specific check: no imaginary-mode count, no
reaction-coordinate designation, no IRC requirement.

**Selection.** `statmech_rubric_for(statmech)` returns the *name* of the TS rubric (`computed_ts_statmech`; resolve it with `RUBRICS.get(name)`) iff `species_entry_id is None and transition_state_entry_id is
not None`, else `computed_statmech` (a row with neither or both owners, impossible under the CHECK, keeps the species rubric).
`RUBRIC_NAME_FOR_RECORD_TYPE["statmech"]` stays `computed_statmech`: it is the record-type default. The TS rubric is listed in `OWNER_SCOPED_RUBRIC_NAMES`, which the
API vocabulary also reads. The structural hard-fail check differs only in its first line (`transition_state_entry_missing`, an
existing `HardFailReason`); external symmetry, torsion dimension and required-role source calculations are shared code.

**What does not change.**

- *Species-owned records.* Rubric, version, check map, badge and completeness are byte-identical, and so are their stored machine
  reviews' currency. The species rubric version is not touched by the TS rubric, because a shared bump would restale every species review
  while nothing those records claim changed.
- *Completeness is not certification.* `is_certified` is false on every evaluation.
- *A geometry-validation fail stays advisory.* The shared warning-kind check and the shared hard-fail detector are reused unchanged.
- *The badge describes evidence completeness, not validity.* A `well_supported` TS statmech badge says the record's provenance
  fields are populated. Whether the transverse model suits a task is judged by `POST /scientific/statmech/select`.
- *Task assessment does not inherit the badge's required-role checks.* `app.services.statmech_selection` never imports
  `app.services.trust` (pinned by a test).
- *The reproducibility rubric* has the same species-only assumption (`scientific_context`); it is a separate tier with a
  separate decision (issue #760), now made: a transition-state-owned statmech is graded under
  `tckdb_reproducibility_ts_statmech` (version 1). See `reproducibility_assessments.md`.

**Machine-review recipe.** A reviewer-family review is stamped with the rubric that actually evaluated the record
(`active_rubric_for_fragment`), not with the record type's default: `computed_ts_statmech` (version 1) for a
TS-owned statmech, `computed_statmech` (version 1) for a species-owned one. A review stores exactly one rubric
(`rubric_name`, `rubric_version`). `active_rubric_for_record_type` is not
owner-aware and remains the species default for statmech. A rubric absent from the active table raises (HTTP 500) instead of
falling back.

**Effect on stored reviews, and the rollout.**

- No `record_machine_review` row is updated, deleted or appended by the change or by its deploy. Staleness is derived at read
  time. After deploy, the latest reviewer-family review of each TS-owned statmech reads `stale`
  (`context_hash_mismatch` and `rubric_versions_mismatch`), because its context was hashed under the species rubric. Species-owned
  reviews stay `current`.
- There is no automatic or bulk re-review. Re-review stays the existing per-record admin action; a fresh pass from the fake
  producer would carry no information.
- Checks that guard species badges. There is no operator report script: the routing is pinned by
  `tests/services/test_trust_ts_statmech_owner_rubric.py` (the owner router and the stale classification of a review stored under
  the other rubric), and species badges are pinned by the frozen-literal test `tests/services/test_trust_statmech_species_frozen.py`.
  After a deploy, the check to read on a live database is the machine-review currency of each species-owned statmech record: a
  species review stored by earlier code reads `current`, and a moved species badge would turn it stale.


### 9.4 `computed_transport`

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

### 9.5 `computed_calculation`

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
| `quality_recorded` | O | An independent, *approved* `record_review` row exists for this calculation. Decoupled from `CalculationQuality` (2026-09): a depositor's own `quality=curated` no longer earns this point on its own — see `_check_quality_recorded` in `app/services/trust/rubrics.py`. |
| `geometry_validation_present` | O | `applies_when` calc type is opt or ts. |
| `geometry_validation_passed_or_warning` | W | |
| `scf_stability_present_if_claimed` | O | `applies_when` calc claims SCF stability checked in parameters. |
| `artifacts_present` | O | At least one `calculation_artifact`. |
| `parameters_parsed` | O | At least one `calculation_parameter` row. |

**Hard-fail signals:** `geometry_validation_failed` on the calc's own
output geometry; `result_block` absent for a calc declared as
successful.

### 9.6 `experimental_kinetics`

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

### 9.7 `computed_transition_state`

Defined in its own spec file:
[transition_state_trust_rubric.md](transition_state_trust_rubric.md).

**Purpose:** evidence checklist for a `transition_state_entry`. The
rubric evaluates the entry as the concrete saddle-point candidate and
inspects its parent `transition_state` and `reaction_entry` as
identity context only. The full check set, hard-fail policy,
IRC / path-search treatment, and frequency / imaginary-mode policy
are documented in the companion spec.

**Hard-fail signals (summary):** `transition_state_entry_missing`,
`transition_state_parent_missing`, `reaction_entry_missing`,
`ts_entry_status_rejected`, `multiplicity_invalid`,
`supporting_required_calculation_hard_failed`,
`geometry_validation_failed_for_required_source`,
`frequency_source_has_zero_imaginary_modes_for_validated_ts`,
`frequency_source_has_multiple_imaginary_modes_for_validated_ts`.

**Status (updated 2026-07-13):** implemented and wired. The rubric
ships as `COMPUTED_TRANSITION_STATE_V1` in
`app/services/trust/rubrics.py` and is attached on the standalone
`GET /scientific/transition-state-entries/{ref}?include=trust` read. It
is also propagated into the composite
`GET /scientific/reaction-entries/{id}/full?include=trust` read under
`transition_states[*].trust`. It is also available on the entry-grained
`GET|POST /scientific/transition-states/search` under the same token, at
`records[*].trust`, where the page query eager-loads the evidence graph
once for the whole page. It is **not** wired into the parent TS-concept
detail read (`/scientific/transition-states/{ref}`), which still rejects
the token: a concept is a collection of entries evaluated at different
levels of theory, and a single verdict for the collection would be an
aggregation rather than a reading.
See `trust_read_api_current.md` for the exact contract per endpoint.

### 9.8 `experimental_thermo`

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

## Current wiring note (2026-07-13)

The three bullets below summarize where this spec stands relative to the
shipped code, verified against `app/services/trust/` and
`app/api/routes/scientific/` on 2026-07-13:

- **The trust fragment is opt-in (`include=trust`) on detail/read
  endpoints only**, never on search/list endpoints and never expanded by
  `include=all`. The endpoint list has grown past the three shown in §10
  below — calculation, kinetics, and thermo were first, but statmech,
  transport, standalone transition-state-entry, and the composite
  `reaction-entries/{id}/full` read (which propagates trust into embedded
  kinetics / calculation / transition-state-entry sections) also carry it
  now. `trust_read_api_current.md` is the maintained, exhaustive endpoint
  table — §10 here is illustrative, not exhaustive.
- **Search endpoints filter on `record_review.status` (via
  `min_review_status` / `include_rejected` / `include_deprecated`
  parameters), not on any trust label.** The `trust_status`-based default
  filtering and opt-in query parameters sketched in §10.3
  (`include_unreviewed`, `min_evidence_label`, `trust_status=`, etc.) were
  never implemented; they remain a design sketch only.
- **The TS rubric is implemented, not spec-only.**
  `computed_transition_state` ships in `app/services/trust/rubrics.py`
  and is wired into the standalone transition-state-entry detail read and
  propagated into `reaction-entries/{id}/full`. §9.7 below is updated
  accordingly; `transition_state_trust_rubric.md` is a companion
  reference, not an open design gap.

## 10. Read API: `trust` fragment

The trust fragment is opt-in on detail/read endpoints; see the "Current
wiring note" immediately above and `trust_read_api_current.md` for the
exhaustive, maintained endpoint list. The endpoints below were the
original MVP set (calculation, kinetics, thermo detail reads):

- `GET /api/v1/scientific/calculations/{calculation_ref_or_id}?include=trust`
- `GET /api/v1/scientific/reaction-entries/{reaction_entry_id}/kinetics?include=trust`
- `GET /api/v1/scientific/species-entries/{species_entry_id}/thermo?include=trust`

Default responses omit `trust`, and `include=all` intentionally does
not expand to `trust`; callers must request `include=trust` explicitly.
Search/list endpoints do not include trust fragments yet. Existing
reads still carry their smaller `RecordReviewBadge` fields where
already implemented.

Trust means deterministic evidence completeness for attached
provenance. It is not a scientific correctness judgment and must not
be presented as a quality score.

### 10.1 Shape

```json
{
  "trust": {
    "review_status": "not_reviewed",
    "trust_status": "mostly_supported",
    "evidence": {
      "rubric": "computed_kinetics",
      "rubric_version": 1,
      "label": "well_supported",
      "evidence_completeness": 0.73,
      "passed_count": 8,
      "possible_count": 11,
      "checks": {
        "reaction_entry_present": "passed",
        "irc_evidence": "missing",
        "uncertainty": "missing",
        "scf_stability_not_checked": "warning"
      }
    },
    "llm_precheck": {
      "enabled": false,
      "label": "not_run",
      "summary": null
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
| `trust.evidence.rubric` | Bare public rubric name (`computed_calculation`, `computed_kinetics`, `computed_thermo`). | Always present for shipped trust reads. |
| `trust.evidence.rubric_version` | Integer rubric version (currently 1 for every rubric). | Separate from the name; retained for machine checks and future migrations. |
| `trust.evidence.label` | Per §6.1. | |
| `trust.evidence.evidence_completeness` | `passed_weight / possible_weight`, rounded by the evaluator. | Not exposed as a percentage; reader formats as it likes. |
| `trust.evidence.passed_count` | Count of `kind in {required, optional}` checks that passed. | |
| `trust.evidence.possible_count` | Same denominator, excluding `not_applicable`. | |
| `trust.evidence.checks{}` | Ordered map of check name → outcome (`passed` / `missing` / `warning` / `not_applicable`). | Key order is the rubric's declared check order and is stable. `missing` counts against the record; `not_applicable` is excluded from both numerator and denominator. Replaced the former `passed_checks` / `missing_checks` / `warning_checks` / `not_applicable_checks` arrays — see §6.2. |
| `trust.llm_precheck.enabled` | Disabled in the current MVP. | Always `false` until an explicit precheck integration lands. |
| `trust.llm_precheck.label` | Advisory LLM label. | Currently always `not_run`; does not influence `trust_status`. |
| `trust.llm_precheck.summary` | Advisory LLM summary. | Currently always `null`. |
| `trust.is_certified` | Reserved curator flag. | Current automated trust reads always emit `false`. |

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
- Rubric version bumps (e.g. 1 to 2) take effect immediately on next read.
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
  rubric_name        TEXT                    -- e.g. computed_kinetics
  rubric_version     INT
  label              EvidenceBadge           -- new enum (computed values only)
  completeness_ratio NUMERIC(5,4)
  passed_count       INT
  possible_count     INT
  checks             JSONB                   -- {check name: outcome}
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
- Curators want to query "how many records would a rubric version 2 promote
  from `needs_attention` to `auto_validated`?" — this needs
  persisted historical evaluations.

Until then, the evaluator's output is fast enough to build inside the
read serializer for record-detail endpoints, and search endpoints can
filter by `record_review.status` alone (which is already a column).
**If Option B is later adopted, it requires a new Alembic revision per
`.claude/rules/migration-rules.md`; the initial migration must not be
edited.**

---

## 12. Evaluator service shape

This section originally sketched a `rubrics/` sub-package split one file
per rubric. **As shipped (updated 2026-07-13), the rubrics live together
in a single `rubrics.py` module** rather than the per-rubric package
below; the rest of the shape (models/evaluator/fragment split) matches:

```text
app/services/trust/
  __init__.py
  models.py     # EvidenceCheckSpec, EvidenceCheckResult, EvidenceEvaluation,
                 # EvidenceRubric, TrustFragment, TrustLLMPrecheck, label_from_completeness
  rubrics.py    # all EvidenceRubric definitions (computed_calculation,
                 # computed_kinetics, computed_thermo, computed_statmech,
                 # computed_transport, computed_transition_state) + the
                 # record_type -> rubric registry
  evaluator.py  # select_rubric(record_type), per-record hard-fail detection,
                 # evaluate/aggregate helpers -> EvidenceEvaluation
  fragment.py   # build_trust_fragment(evaluation, review_status=...) -> TrustFragment
```

The experimental rubrics (`experimental_kinetics`,
`experimental_thermo`) sketched in §9.6/§9.8 are not present in
`rubrics.py` as of this update; treat them as still design-only.

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
| `tests/trust/test_kinetics_complete.py` | Computed kinetics with full provenance → `well_supported`, `passed_count == possible_count`, no `missing` outcome in `checks`. |
| `tests/trust/test_kinetics_missing_irc.py` | Same record minus IRC evidence → label drops by at most one step; `checks["irc_evidence_present"] == "missing"`; no spurious warnings. |
| `tests/trust/test_kinetics_no_sources.py` | Kinetics with zero `kinetics_source_calculation` → `source_calculations_present` fails; label `sparse` or below; required-check failure recorded. |
| `tests/trust/test_thermo_nasa.py` | Thermo with NASA polynomials → all NASA-applicable checks live; scalar-only checks `not_applicable`. |
| `tests/trust/test_thermo_scalar_only.py` | Thermo with scalar-only data → range checks `not_applicable`; completeness ratio reflects reduced denominator, not failure. |
| `tests/trust/test_experimental_kinetics.py` | Experimental kinetics with literature, no calcs → still reaches `mostly_supported`; `source_calculations_present` is `not_applicable` under the experimental rubric. |
| `tests/trust/test_exclusion_defaults.py` | Records in `rejected` / `deprecated` / `hard_failed` are absent from default list responses; appear with explicit opt-in. |
| `tests/trust/test_deterministic_order.py` | Two records with identical `completeness_ratio` sort by the §10.3 tie-breaker stably across runs. |
| `tests/trust/test_fragment_shape.py` | Generated fragment matches the §10.1 schema exactly (no extra keys, no missing required keys). |
| `tests/trust/test_rubric_version_stability.py` | Re-running `computed_kinetics` on a frozen fixture produces byte-identical output. (Pins the contract.) |
| `tests/trust/test_geometry_validation_hard_fail.py` | A record whose TS geom has `ValidationStatus.fail` returns `trust_status == hard_failed`, regardless of how many other checks pass. |
| `tests/trust/test_llm_precheck_advisory.py` | Toggling LLM precheck `passed` ↔ `flagged` on the originating submission **does not** change `trust_status`, `label`, or any entry in `checks`. |
| `tests/trust/test_no_llm_dependency.py` | Evaluator runs and produces a complete trust fragment with the LLM provider disabled / unconfigured. |
| `tests/trust/test_workflow_tool_agnostic.py` | A record whose `workflow_tool` is unknown (no ARC, no RMG) still evaluates against the rubric without raising. |

---

## 14. Recommended implementation order

1. **Land the rubric base classes** (`EvidenceCheck`, `EvidenceRubric`,
   registry) with `computed_calculation` only. Smallest scope.
2. **Wire `trust` fragment into the existing calculation read endpoint
   under a flag** (e.g. `include=trust`). Existing `RecordReviewBadge`
   is left intact during the transition.
3. **Add `computed_kinetics` and `computed_thermo`.** These are
   the highest-value rubrics for downstream consumers.
4. **Add `computed_statmech`, `computed_transport`.**
5. **Add the experimental rubrics** (`experimental_kinetics`,
   `experimental_thermo`). These need the literature-policy
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
   the histogram of completeness ratios and the checks most often
   `missing`. This is a tool for tuning rubric versions, not
   a public-facing thing. Out of scope for this spec.

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


## 17. Made under

**Made under.** Every assessment a page shows names the rubric that judged it and its integer version. The wire shape is `MadeUnder`
(`app/schemas/reads/scientific_assessment.py`): `rubric`, `assessed_version`, `current_rubric`, `current_version`, `state`, `stale_reason`.

- `current`: made under the version in force, and the inputs were compared with today's and match.
- `inputs_unchecked`: made under the version in force, but the route does not compare the inputs, so nothing is claimed about them. A list or
  search read says this and never `current`.
- `stale`: an assessment exists but was made under another version (`stale_reason: rubric_version`) or, with the version unchanged, before its inputs changed
  (`inputs_changed`: the inputs the assessment was computed from differ now, which can be new evidence or a changed rule under the same version). A page
  says it has not been re-assessed under the current version; nothing schedules a re-assessment, only a curator action runs one.
- `unassessed`: no assessment exists, so no rubric is named. Never a kind of stale.
- Version 0 means "stamped before the 2026-10 versioning reset"; no registry holds 0, so it is never current. Pages word it that way, never as "v0".
- The deterministic trust badge is computed at read time under the rubric version in force, so it is `current` by construction. Only stored assessments
  (reproducibility) can be stale. Evidence-completeness checklists on thermo and kinetics are not registered rubrics and carry no version yet.
- The line never ranks a record: it names the rule that judged it.
- `inputs_checked`: whether the assessment's inputs were compared with today's. Re-evaluating them costs tens of statements per record, so **the route decides,
  never the response size**: statmech and transport by-ref reads always compare them; the thermo and kinetics entry-scoped lists compare them only on an explicit
  `check_inputs=true` with `include=assessments` and `limit` of at most 10 (422 `assessment_input_check_limit` otherwise; echoed as `request.check_inputs`);
  every other route compares the rubric name and version only and reports `inputs_unchecked`. `null` when there is nothing to compare (`unassessed`).
  The comparison is the same under `curated` and `exploratory` (ADR 0023, step 6): a stored stamp was computed from the full set of inputs with no read profile, so
  whether it is current is a property of the record and its stamp, not of the reader. The recomputation reads no profile; a changed input, whatever its review
  status, makes the stamp `stale` (`inputs_changed`), and the answer is a state and a reason, never a ref. `check_inputs` sent
  to a route that does not support it is refused (422 `check_inputs_not_supported`), not ignored; a bad handle is a 404 before `check_inputs` is judged.
- The admin curator-task reads carry `made_under` for the stored machine review the task came from (the row whose `source_audit_event_id` is the task's; the
  record's latest reviewer-family review when the task has none), read by the currency rule (`currency.stamp_mismatches`): a different prompt or rubric name or
  version, or a stored version 0, is `stale` (`stale_reason` `rubric_version` or `prompt_version`); otherwise `inputs_unchecked`, because the evidence hash is
  not compared for a page of tasks. A task whose source review is not stored has none. The page words it as a review ("Reviewed under ...").
- Procedures without a version (the evidence-completeness checklists, geometry validation, SCF stability) carry no `made_under`: a version is not invented
  for them.

## 18. The rubric-change guard, and bumping a rubric

A stored assessment names the rubric version it was made under, and a reader decides *stale* by comparing that number with the current one. That only works
if the number moves when the rules move. `app/services/trust/rubric_pins.json` holds, for each current `(name, version)`, a fingerprint of what the rubric
decides; `tests/scripts/test_rubric_pins.py` and `backend/scripts/generate_rubric_pins.py --check` fail when the two disagree. The test runs in the complement
gate, like the other generator checks.

**What is pinned.** Every trust rubric that declares checks, and both reproducibility rubrics (`tckdb_reproducibility`, `tckdb_reproducibility_ts_statmech`).
Rubrics declared with `checks=()` (the review-tier ones) are not pinned: their rules live in the services that run them, a pin of an empty list would pass
whatever those did, and `unguarded_rubric_names()` lists them.

**The fingerprint.** For a trust rubric: its record type; each check's name, kind and weight and the code its runner executes; and the code of the evaluator
entrypoints in `GRADING_ENTRYPOINTS` (hard-fail detectors, completeness aggregation, label thresholds, and for the transition-state rubric the scoped context
builders). For a reproducibility rubric: its name and the code of `evaluate_reproducibility` and `reproducibility_rubric_for`. `explain` wording is excluded.
"Code" is the AST without docstrings or comments, followed across `app` and `tckdb_schemas` through every function named or called (decorators unwrapped,
`staticmethod`, `property`, `partial`, lambdas), every callable inside a constant container, every module constant read, and every in-scope class referenced as
a whole (an enum's methods and properties count; an SQLAlchemy model contributes its methods, not its columns).

**It fails closed.** It raises rather than skips: a constant it cannot canonicalise, a callable without readable source, `getattr` with a computed name
(unless the site is listed in `DYNAMIC_ATTRIBUTE_READS` with the reason it reads a data attribute and never chooses a function), `eval`/`exec`/`globals`, and any read of
the environment or of `settings` (declare a genuine one in `DECLARED_INPUTS`). The pins record the Python minor version, because `ast.dump` differs between minors.

**Also followed.** Imports inside a function body; the enum columns and relationship targets of every ORM model it reaches (an instance of a model carries
the behaviour of its enums, e.g. `ArtifactIntegrityFinding.is_break`); ORM `hybrid_property` accessors and `column_property` expressions; module-level SQL
expressions (as the text they compile to); and the runtime statmech rubric chooser (`evaluate_loaded_statmech`, `statmech_rubric_for`). A test checks that every
`evaluate_*` the read services call is reached by a pinned rubric. Also refused: `sys.modules` and `__dict__[...]` lookups, and a module constant read from the
environment at import unless it is listed in `DECLARED_ENVIRONMENT_CONSTANTS` as configuration (the storage endpoint and credentials; they differ between CI and a
laptop). `DYNAMIC_ATTRIBUTE_READS` keys the exact call, so a second computed `getattr` in an allowed function still fails.

**Left out of the fingerprint, on purpose.** Pydantic `Field` descriptions, titles and examples, and the plain-text message of a `raise` (its `code=` and every
other argument still count). Docstrings and comments are not in the AST. A renamed local variable or an added type annotation does move it. **Out of scope:**
DB-side functions and triggers, server defaults and migrations.

**To bump a rubric.** (1) Change the checks. (2) Raise `version` on its `EvidenceRubric` in `app/services/trust/rubrics.py` (or on `SHARED` / `TS_STATMECH` in
`reproducibility_rubric.py`). (3) Run `conda run -n tckdb_env python backend/scripts/generate_rubric_pins.py` and commit the pin. A new rubric with checks also
needs an entry in `GRADING_ENTRYPOINTS`. The script refuses to re-pin a changed rubric whose version did not move. `--allow-unbumped NAME --reason TEXT`
overrides for a change that decides nothing new and records `{name, version, reason}` in `unbumped_overrides` (the test surfaces them);
`--repin-for-python` re-pins everything after a Python minor upgrade and refuses unless only the Python version moved. A change to the fingerprinting itself
bumps `ALGORITHM` and re-pins everything, recorded as `algorithm_repin` in `unbumped_overrides`, in a commit that changes nothing else. Pre-release, pins are kept for current versions only.

**Not guarded, candidates:** selection procedures, `RENDERERS`, `PAYLOAD_FORMATS`, `PARSERS`, and the review-tier rubrics above.
