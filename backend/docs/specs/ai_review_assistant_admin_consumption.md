# AI Review Assistant Admin Consumption

**Status:** draft spec - backend/admin UX design only
**Date:** 2026-05-27
**Scope:** TCKDB backend/admin tooling design. No frontend implementation,
real LLM providers, RAG, upload wiring, public trust mapping, migrations,
ARC changes, or `tckdb-client` changes.
**Audience:** TCKDB backend maintainers, admin tooling authors, curator UX
implementers.

**See also:** `provisional_machine_review.md` §0 is the authoritative map of
the full machine-review layering (deterministic trust → submission events →
submission summary → admin inspection → future public `machine_review` →
human `review_status`). This file covers the admin/curator consumption of the
submission-scoped layers (2 and 3) and points at the admin inspection layer
(4, documented in `admin_machine_review_inspection.md`).

---

## 1. Current Behavior

AI Review Assistant is optional and off by default
(`AI_REVIEW_ASSISTANT_MODE=off`). The fake/disabled providers can generate
structured advisory results, and those results are persisted as
`submission_audit_event` rows with:

```text
event_kind=llm_precheck_recorded
actor_kind=llm
```

There are now three read surfaces over this data, at two distinct auth tiers:

```text
GET /api/v1/submissions/{submission_id}/audit-events                  -- submission visibility
GET /api/v1/submissions/{submission_id}/ai-review-summary             -- submission visibility
GET /api/v1/admin/submissions/{submission_id}/machine-review-inspection -- admin only
```

- `/audit-events` — full audit timeline, including `details_json` (the
  full-detail source of truth; §5).
- `/ai-review-summary` — compact latest-result card derived from the newest
  `llm_precheck_recorded` event, or `null` if none (§4).
- `/admin/.../machine-review-inspection` — **admin-only** raw diagnostic view
  projecting the submission's precheck events onto its linked records, with
  unmapped findings and mapping/parse warnings. Curators are **not** granted
  access. It is a private debugging surface, **not** public trust and **not**
  a curator workflow. Full contract in `admin_machine_review_inspection.md`.

The first two endpoints follow the existing submission visibility policy; the
inspection endpoint requires admin. Public scientific trust fragments still
show the AI review state as disabled/`not_run`, and there is **no** public
`trust.machine_review` (§8).

Recent related commits:

```text
0087c35 Add optional LLM precheck plumbing
c8e2999 Persist LLM precheck audit events
2c1bc0e Test LLM precheck audit read surface
2b0f8d4 Add admin-only submission machine-review inspection endpoint
```

---

## 2. Core Principles

These constraints are load-bearing:

- AI Review Assistant output is advisory only.
- It does not approve submissions.
- It does not reject submissions.
- It does not change `submission.status`.
- It does not mutate scientific records.
- It does not change deterministic evidence completeness.
- It is initially displayed only in submission/admin moderation surfaces.

The assistant may help curators notice provenance gaps, suspicious
inconsistencies, unit issues, or missing evidence. It is never a
certification system and never a moderation decision engine.

---

## 3. Recommended Admin Surfaces

Admin and curator tooling should consume AI Review Assistant output through
submission-scoped moderation surfaces first.

Recommended MVP surfaces:

1. Submission detail latest AI Review Assistant card.
2. Submission audit timeline event.
3. Optional future list/filter view.

No new database table is required for the MVP. Both the latest card and
the audit timeline can be derived from `llm_precheck_recorded` audit
events already exposed by the submission audit endpoint.

---

## 4. Latest AI Review Assistant Card

The submission detail page should show a compact latest-result card when a
submission has at least one `llm_precheck_recorded` audit event. The card
is derived from the newest matching event for that submission.

Newest means highest `created_at`, with audit event primary key as the
tie-breaker when timestamps are equal.

Suggested card shape:

```json
{
  "label": "warning",
  "summary": "The upload has core provenance but is missing IRC evidence.",
  "model": "fake/test",
  "used_rag": false,
  "created_at": "...",
  "finding_counts": {
    "info": 1,
    "warning": 2,
    "critical": 0
  }
}
```

Field mapping:

| Card field | Source |
|---|---|
| `label` | `details_json.label` |
| `summary` | `details_json.summary`, falling back to audit event `summary` |
| `model` | `details_json.model` |
| `used_rag` | `details_json.used_rag` |
| `created_at` | audit event `created_at` |
| `finding_counts` | deterministic count of `details_json.findings[].severity` |

If no `llm_precheck_recorded` event exists, the submission detail page may
omit the card or render an unobtrusive "not run" state. Absence of an audit
event must not be interpreted as a pass.

Labels are advisory states, not workflow states:

| Label | Admin display meaning |
|---|---|
| `not_run` | Assistant did not review the submission. |
| `pass` | No notable advisory concerns were reported. This is not approval. |
| `warning` | Curator-visible advisory issues were reported. |
| `needs_attention` | Strong advisory signal that curator inspection is recommended. |
| `failed_to_review` | The assistant could not complete review. This is not upload failure. |

---

## 5. Audit Timeline Event

Admin tooling should render existing audit events in chronological order
from:

```text
GET /api/v1/submissions/{submission_id}/audit-events
```

For:

```text
event_kind=llm_precheck_recorded
```

UI should show:

```text
AI Review Assistant recorded a precheck
label
summary
provider/model
finding count
expandable details_json
```

Recommended interpretation:

| Display item | Source |
|---|---|
| Event title | Literal: `AI Review Assistant recorded a precheck` |
| Label | `details_json.label` |
| Summary | `details_json.summary`, falling back to audit event `summary` |
| Provider/model | `details_json.provider` and `details_json.model` |
| Finding count | length of `details_json.findings` |
| Details | expandable pretty-printed `details_json` |

The full `details_json` should remain available in the timeline because it
is the canonical MVP payload for structured findings. The timeline should
not hide failed reviews; `failed_to_review` is useful operational signal.

---

## 6. Finding Display

Findings should be grouped first by severity, then by category. Within each
group, preserve provider order unless a future backend endpoint supplies a
stable sort.

Supported severity values:

```text
info
warning
critical
```

Supported category values:

```text
provenance
units
geometry
kinetics
thermo
statmech
calculation_parameters
consistency
```

Each finding should display:

| Field | Display behavior |
|---|---|
| `severity` | Badge or grouped heading. |
| `category` | Secondary grouping or compact label. |
| `record_type` + `record_id` | Record reference if visible under the current user's permissions. |
| `message` | Primary curator-facing text. |
| `evidence_keys` | Compact technical references, usually shown in expanded details. |

Record references are advisory navigation hints. If the current user cannot
see the referenced record or internal id, the UI should still show the
finding message and category without exposing hidden identifiers.

---

## 7. Visibility and Authorization

Use the existing submission and audit visibility policy:

- Creator can read own audit events.
- Curator/admin can read according to the existing policy.
- Other users cannot read private submission audit events.

Do not introduce a separate AI Review Assistant visibility policy for the
MVP. The AI review result is submission-scoped audit data, so it should
inherit the same access rules as other submission audit events.

---

## 8. Public Trust Fragment Boundary

Do not map submission-level AI Review Assistant results into public
calculation/kinetics/thermo `trust.llm_precheck` yet.

Reasons:

- One submission may affect multiple records.
- One record may have multiple submission histories.
- Latest submission precheck is not necessarily latest record-level
  assessment.
- Advisory submission review is not record certification.

The admin machine-review inspection endpoint does **not** change this: it is
a read-only projection for maintainers and emits its own admin-only schema,
not a public `TrustFragment`. There is **no** public `trust.machine_review`
fragment yet, and the existence of the inspection endpoint must not be read as
one. Public `trust.llm_precheck` stays disabled/`not_run`.

Public scientific trust fragments should remain unchanged for now. In
particular, AI Review Assistant output must not affect:

- deterministic evidence completeness
- `passed_checks`
- `missing_checks`
- `warning_checks`
- `not_applicable_checks`
- `hard_fail_reason`
- `trust_status`
- public record visibility

This boundary keeps curator moderation assistance separate from public
scientific evidence labeling.

---

## 9. Optional Future List/Filter View

A future admin list view may filter submissions by latest AI Review
Assistant state. This is not required for the MVP.

Useful future filters:

- latest precheck label
- highest finding severity
- has critical findings
- has failed_to_review
- provider/model
- used_rag
- precheck created_at range

Until a dedicated endpoint or table exists, broad list filtering by audit
event payload may be inefficient. The first production UI should prefer
the submission detail card and audit timeline.

---

## 10. Future Options

Future work may include:

- Latest AI precheck summary endpoint.
- Admin filters by label or severity.
- Dedicated `submission_llm_precheck` table.
- Record-level advisory summaries.
- RAG-cited findings.
- Real Cloud/Local provider integration.

These are future options only. They should not be implemented as part of
the current admin consumption spec.

---

## 11. Tests Required Later

If a latest-card endpoint, admin list filter, or frontend card is
implemented later, require tests proving:

- Latest card chooses the newest `llm_precheck_recorded` event.
- Finding counts are deterministic.
- Audit timeline still exposes the full `details_json`.
- Visibility follows existing submission audit policy.
- Public scientific reads remain unchanged.

Useful additional coverage:

- `failed_to_review` is displayed as an advisory/operational result, not
  an upload failure.
- Missing `details_json.findings` behaves like an empty finding list.
- Unknown future fields in `details_json` remain visible in expanded
  details and do not break compact rendering.

---

## 12. Non-Goals

- No frontend implementation.
- No real LLM provider.
- No RAG.
- No upload workflow wiring.
- No public trust fragment mapping.
- No migrations.
- No schema changes.
- No ARC/client changes.

---

## 13. Recommended Implementation Slice

When implementation begins, the smallest useful slice is:

1. Add a backend helper or response adapter that selects the latest
   `llm_precheck_recorded` event for one submission.
2. Derive the compact latest-card shape from that audit event without
   adding a table.
3. Keep the existing audit endpoint as the full-detail source of truth.
4. Add endpoint/card tests for deterministic latest selection, finding
   counts, visibility, and unchanged public scientific reads.

This slice preserves the advisory boundary while giving curators a clear
submission-scoped signal.
