# Depositor text safety — implementation plan

Status: draft for owner ruling. Base: `main` at `1643d4d1`. Measured 2026-09-09 against the live archive (`https://tckdb.homecalvin.com`, anonymous, read-only GET) and the `main` worktree checkout.

## 1. The gap, restated precisely

Depositor-authored free text (a `network.description`, a `.note`, a curation-policy blurb) publishes to public read endpoints the instant it is uploaded, with no screening and no review gate — because nothing in the review/precheck stack runs automatically today (§2). The owner asked for something to close this, or a package that already does.

**Non-goal:** this plan does not touch scientific values, structures, or FK-resolved provenance. It is scoped to free-text columns a depositor typed by hand.

## 2. Measured facts

### 2.1 Deposits are authenticated, not anonymous

Every upload route requires `Depends(get_current_user)` (`backend/app/api/routes/uploads.py:227` and ten further routes in the same file; `backend/app/api/routes/submissions.py:117` and three more). `AppUserRole` is `user` / `curator` / `admin` (`backend/app/db/models/common.py:848`). There is no anonymous submission form — this is an authenticated-contributor archive, which changes the threat model from "public web form abuse" to "an account-holder wrote something a screener should catch before a stranger reads it."

### 2.2 Nothing runs automatically at upload time — corrected from the brief

The brief's framing ("a real curator review pipeline exists... flag at deposit into the existing review pipeline") overstates how live that pipeline is. Measured:

- `backend/app/services/machine_review/` is real, tested code, but its own docstrings say so directly: `admin_trigger.py` is "a maintainer/debug surface... nothing here is wired into uploads or any public read"; `orchestration.py` calls **only** a `FakeMachineReviewProducer` or a caller-supplied review, never a real provider; `machine_review_curator_task.py` states "No automatic task creation, assignment, or resolution behaviour exists yet — this is model + migration only."
- `backend/app/services/llm_precheck/` (the AI Review Assistant) is further along — it has disabled/fake/real-shaped providers and persists `submission_audit_event` rows via `record_llm_precheck_audit_event` (`backend/app/services/llm_precheck/service.py:48`) — but `backend/docs/specs/optional_llm_precheck.md` states plainly: "It has no upload workflow wiring" and defaults to `AI Review Assistant: Off`. `run_llm_precheck_for_submission` (`service.py:58`) exists but nothing calls it from an upload path today.
- `provenance_warnings.py`'s `UploadWarning` mechanism **is** live — it is computed and returned inline during upload reconciliation (thermo/statmech/kinetics/transport workflows already call it). It is the one piece of this stack that is actually wired.

**Consequence for the plan:** "flag at deposit into the existing pipeline" is not a small hook-up — it is new wiring at the same boundary `provenance_warnings.py` already occupies, modeled on the *shape* `llm_precheck` already defined (a `SubmissionAuditEventKind` value + a `details_json` payload) rather than a call into a system that already fires.

### 2.3 Public reads default to showing everything, and the archive already has a proven analogue for this exact problem

`visible_statuses()` (`backend/app/services/scientific_read/common.py:376`) is the single seam every scientific read service calls. It is governed by a `?profile=` dependency (`backend/app/api/routes/scientific/_profile.py`, `backend/app/services/scientific_read/profile.py`) with two states:

- `exploratory` (default): every visible candidate, review status shown, no floor.
- `curated` (opt-in): floor raised to `approved`.

`profile.py`'s own docstring gives the exact reasoning this plan needs: *"Every record on the deployed database is currently `not_reviewed`. A curated default would return empty result sets and read as a broken database, so `exploratory` is the default and `curated` is opt-in."*

This is the crux made concrete by the codebase's own prior decision on a structurally identical problem (gate visibility on review state vs. keep the archive usable). It also tells me the obvious move — key the new gate on `RecordReviewStatus`/`approved` the way `curated` does — has already been tried, for a broader case, and rejected as a *default* for exactly the reason the brief anticipates. I do not reuse `curated` directly: it gates whole records, and gating `network.description` off because `network.name = "hydrazine"`'s owning record is `not_reviewed` would delete the one example the brief itself says must survive.

### 2.4 Free-text columns that reach a page (grepped, not asserted)

`grep -n "Mapped\[Optional\[str\]\] = mapped_column(Text" backend/app/db/models/*.py` plus the required-`Text` name columns, cross-checked against `backend/app/schemas/reads/*.py` for which reach a response:

| Table.column | Kind | Confirmed reaching a read schema |
|---|---|---|
| `network.name`, `network.description` | short id / long prose | Yes — live-fetched `net_o6bt63kjeyvhvxx26w6kdi433a`: `name="hydrazine"`, `description` is 500+ chars of depositor prose (run names, file paths, a normalization note) |
| `energy_correction_scheme.name` | short id | Yes (`scientific_energy_correction_scheme_search.py:55`) |
| `energy_correction_scheme.note`, `.convention_note` ×2 more | long prose | present on the model (`energy_correction.py:77,250,371`); not observed reaching a read schema in this pass — stated as unconfirmed, not absent |
| `group_additivity.name`, `.description`, `.note` (×2, group + component) | short id / long prose | `.note` confirmed at `scientific_thermo.py:234`; `.name`/`.description` on the model (`group_additivity.py:60,62,74,129`), not traced further |
| `dataset_release.name`, `.description`, `.release_description`, `.curation_policy_name`, `.curation_policy_description` | short id / long prose | `name`/`description` confirmed (`scientific_release.py:44,46,87`) |
| `software.name`, `.description`; `workflow.name` (workflow_tool), `.description` | short id / long prose | model only, not traced further |
| `reaction_family.name`, `reaction.reaction_family_source_note`, `reaction.note` | short id / long prose | model only |
| `species.note` (×3 subtypes), `transition_state.note` (×2), `kinetics.note`/`.convention_note` (×2), `thermo.note`, `transport.note`, `statmech.note`/`.top_description`, `calculation.*.note` (7 subtypes), `molecular_property_observation.method_note`/`.external_source_name`/`.reference_comment`/`.note` | long prose, mostly per-record | model only, high count, lower per-record visibility than the identity-level fields above |
| `author.given_name`/`.family_name`/`.full_name` | short id, often auto-resolved from DOI/ISBN metadata | out of scope for the same reason `species_entry_label` is out of scope — largely server/metadata-sourced, not open depositor prose |
| `machine_review_curator_task.resolution_note`, `record_review.note` | long prose, but **curator/admin-authored**, not depositor | explicitly excluded — the gap is depositor text, not reviewer text |

**Takeaway for scoping:** the surface splits cleanly into (a) short identifying `name` fields — few, high value, and per the network example, exactly the thing that must not disappear — and (b) long free-prose `description`/`note`/`comment` fields, dozens of columns, low navigational value, the actual place harmful content would go. This distinction is load-bearing for the design in §3.

### 2.5 The curator-task queue is generic enough to host a new finding type without a schema change to itself

`MachineReviewCuratorTask` (`backend/app/db/models/machine_review_curator_task.py`) addresses `record_type` + `record_id` (whole record, not field-level) with a `finding_fingerprint` string, a denormalized `machine_review_status`/`highest_severity`/`findings_count` snapshot, and an optional `source_audit_event_id` FK to `submission_audit_event`. `MachineReviewCategory` (`backend/app/services/machine_review/schemas.py`) — the finding taxonomy (`provenance`, `units`, `geometry`, ... `schema_gap`) — is **not** mirrored as a Postgres enum in `app/db/models/common.py` (only `MachineReviewStatus`/`MachineReviewSeverity` are). Adding a new category value there is a pure-Python change, no migration.

`SubmissionAuditEvent.details_json` (`backend/app/db/models/submission.py`, `JSONB`, nullable) is the existing place to park "which field, which rule matched" without adding a column to the curator-task table. `SubmissionAuditEventKind` (`backend/app/db/models/common.py:1226`) **is** a Postgres enum and already has `llm_precheck_flagged`/`llm_precheck_recorded` as the precedent for "a screening pass happened, here's the audit trail" — a new value (e.g. `content_screening_flagged`) needs a real Alembic revision (`ALTER TYPE ... ADD VALUE`, per the already-deployed-table rule) since `submission_audit_event` holds real data.

### 2.6 Dependency-addition has a real process, and it favors the pure-Python option

`backend/environment.yml` is version-pinned exact-per-package and is "the single source of runtime deps" per `backend/Dockerfile`'s own comment ("If a runtime import fails for a missing dependency, add it to `backend/environment.yml`... rather than to a pip line here"). It already carries a `pip:` sub-block for one conda-forge-absent, pure-Python package (`isbnlib2==3.11.9`), which is the exact precedent a pure-Python screening library would follow — no new Dockerfile logic, no compiled-wheel risk on `linux-aarch64`. `backend/pyproject.toml`'s `dependencies` list is the second place the same version must land (it is the "source of truth for the package itself").

## 3. The crux: does the gate apply, and keyed on what?

**Decision: the gate is per-field, not per-record, and it keys on an explicit unresolved flag — never on `RecordReviewStatus`.**

Reasoning, from §2.3 directly: keying on `approved` (or any review-status floor) reproduces the exact failure `curated` was built to avoid, at a scope the archive cannot yet afford — every record is `not_reviewed`, so a review-status gate on free text hides `network.description="hydrazine..."` today, with no depositor or curator action able to fix it until reviewing actually starts. A gate nobody can satisfy is the gate the brief warned would get removed.

Instead, the gate keys on a **new, narrow, per-field state**: *flagged and unresolved*. Default state for every field, on every record, forever, is "not flagged" — i.e., visible, exactly as today. A field only moves to "flagged" when the layer-3 screener (§5) or a curator raises a finding against it at deposit or on manual review; it moves back to "visible" (permanently — no re-flag on the same fingerprint) the moment a curator resolves the linked `machine_review_curator_task`. This means:

- Ordinary depositor text — "hydrazine", every existing record — is never touched. The archive today is one giant, correctly-permissive `not-flagged` state, not because a gate was skipped, but because no field has ever been flagged.
- The gate applies to **long free-prose fields only** (`description`/`note`/`comment`/`*_note` family), not short `name` fields. Rationale from §2.4: names are the load-bearing identifiers the brief's own example turns on, single/few-word tokens have negligible harmful-content surface, and a manual curator override still exists for the rare bad-faith `name` (nothing stops a human from opening a task against a `name` column through the same generic `record_type`/`record_id` addressing — the automated screener in §5 simply doesn't scan them by default).
- The gate is **on by default** for the fields it covers, but only bites when something is actually flagged — so "on by default" costs nothing today and does not need a configurable off-switch to avoid breaking the archive (contrast with `curated`, which *would* break it and is deliberately opt-in). What is configurable is whether the automated screener (§5) runs at all — see the owner questions in §8.
- **Render contract**, extending the reaction-entry-page precedent's absent/null/populated three-state discipline to a fourth state:
  - Field never populated → existing "not recorded" wording (unchanged).
  - Field populated, not flagged → the text, unchanged (this is ~100% of the archive today).
  - Field populated, flagged, task open → **withheld**, rendered as an explicit statement naming the reason and the queue, e.g. *"Text withheld pending review."* with a stable, non-identifying pointer (a `finding_fingerprint`-derived opaque token, never the raw curator-task id per the no-ids-in-error-detail convention) — never blank, never an omitted key, never reading as "this field has no value."
  - Field populated, flagged, task resolved-as-clean → text renders normally again, permanently (no re-flagging on the same fingerprint).
  - Field populated, flagged, task resolved-as-upheld → curator's resolution decides the field's fate; this plan does not invent a redaction/edit mechanism (append-only identity data has no edit path today) — the realistic curator action is *reject the submission* or *contact the depositor*, which are existing submission-moderation tools, not something this plan adds.

This design directly answers the "trades a real loss of usefulness against a threat that has not materialised" tension: it trades *nothing* against a threat that has not materialized, and starts paying only once something materializes.

## 4. Layer 2: flag at deposit — design

New wiring at the same call site `provenance_warnings.py` occupies inside the persist workflows (e.g. `backend/app/workflows/computed_reaction.py:320` region, `_collect_bundle_provenance_warnings`, and the thermo/statmech/kinetics/transport upload workflows that already call `provenance_warnings`). For each in-scope free-prose field being written on this upload:

1. Run the screener (§5) over the field value.
2. On a hit: append an `UploadWarning(field="network.description", code="possible_flagged_language", message=...)` to the response the depositor already receives (non-blocking, per the owner's ruling — §7) — same shape as `provenance_warnings.py`'s existing warnings, so no new client-side contract.
3. Write one `submission_audit_event` row, `event_kind=SubmissionAuditEventKind.content_screening_flagged` (new value), `details_json={"field": "...", "matched_rule": "...", "record_type": "...", "record_ref": "..."}`. Whether `details_json` snapshots the matched substring or only the rule/category id is an open question (§8) — leaning toward category-only, since the live field can be re-read at review time and a snapshot would duplicate the very text being screened into a second, less-guarded row.
4. Create one `machine_review_curator_task` row (`workflow_state=untriaged`, `machine_review_status=machine_screened_needs_attention`, `highest_severity` from the rule, `source_audit_event_id` = the row from step 3, `finding_fingerprint` = a stable hash of `(record_type, record_id, field, rule_id)` so a re-upload of the same text does not spam duplicate tasks — the table's own `UniqueConstraint` on `(submission_id, record_type, record_id, finding_fingerprint)` enforces this).
5. Never reject the submission. Never mutate the field, the record, `submission.status`, or `RecordReviewStatus` — this task creation follows the curator-task model's own documented "non-interference policy" verbatim.

`MachineReviewCategory` gains one new value, e.g. `content_screening` (pure-Python enum edit, §2.5 — no migration). `SubmissionAuditEventKind` gains `content_screening_flagged` (real Alembic revision, already-deployed table).

## 5. Layer 3: the screening library

### Candidates evaluated

**`detoxify`** — torch-based. Disqualified outright; the API is an arm64 Pi container and the repo already treats "no torch" as settled (per the task brief). Not evaluated further.

**`alt-profanity-check`** — a linear SVM (scikit-learn `LinearSVC` + `CountVectorizer` bag-of-words) trained on two social-media/comment datasets (`t-davidson/hate-speech-and-offensive-language`, the Kaggle Jigsaw Toxic Comment Challenge). Read its README and training description (no install, no run — a bag-of-words linear model's weights are opaque without loading the shipped `.joblib`, and simulating a trained classifier by hand is not a faithful measurement, so I report characterization, not a fabricated accuracy number). Two concrete concerns, not conjecture:
  - Its own README states the caveat directly: *"it has a hard time picking up on less common variants... Never treat any prediction from this library as unquestionable truth."*
  - Its accuracy table (95% test accuracy, 86% precision on its own benchmark) is measured against Twitter/Wikipedia-comment register — casual, first/second-person, slang-heavy. TCKDB's free text is third-person, formal, chemistry-jargon-dense (§2.4's live example: "Chebyshev fits from the Arkane run..."). A bag-of-words model trained on social-media toxicity has no chemistry vocabulary in its training distribution at all; whether it over- or under-fires on words like "explosive," "toxic," "hazard," "kill" (all ordinary in hazard/safety notes) cannot be answered without running it, and I did not run it, so I report this as an open risk rather than a number.
  - Dependency cost: scikit-learn + joblib + the shipped model file. `scikit-learn` ships arm64/`linux-aarch64` builds on conda-forge, so it is not disqualified the way torch is — but it is a materially heavier, opaque addition next to a wordlist.

**`better-profanity`** — pure Python, MIT, no runtime dependency beyond the stdlib. **Read its actual matching source** (`better_profanity.py`, `utils.py`, `varying_string.py`, fetched from the upstream GitHub repo) rather than assuming behavior from the name:
  - It tokenizes on non-alphanumeric boundaries and requires a token to **exactly equal** a wordlist entry (with a bounded leetspeak character-substitution map, `a→a/@/*/4` etc.) via `VaryingString.__eq__`, which rejects any candidate whose length falls outside `[min_len, max_len]` for the pattern before doing the substitution walk. It is **not** a substring/`in` check. Multi-word phrases in the wordlist ("hand job") are matched by joining adjacent *whole tokens*, never by matching inside one token.
  - This directly refutes the brief's stated danger *for this specific library* — "analysis"/"analyte"/"canal"/"assay"/"class"/"mass"/"passivation"/"cumene" cannot match a shorter wordlist entry like "anal"/"ass"/"cum" under whole-token equality, because their lengths don't fit the pattern.

**Measured false-positive rate.** Downloaded the library's actual `profanity_wordlist.txt` (835 entries) from its GitHub repo and reimplemented its whole-word-exact-match rule (case-insensitive tokenize on `[A-Za-z]+`, membership test — the leetspeak layer only *adds* candidate matches, so this undercounts hits if anything, making the measured FP rate conservative-high, not conservative-low) against:
  - The real `network.description` pulled live from `net_o6bt63kjeyvhvxx26w6kdi433a` (§2.4) plus `network.name="hydrazine"`.
  - A 72-word hand-built list of routine chemistry/technique vocabulary chosen specifically to include Scunthorpe-trap substrings: `analyte, analysis, analogue, analog, canal, class, mass, assay, assess, passivation, arsenic, arsine, cumene, cyclopentadienyl, hexose, isodiazene, diazene, triazene, tetrazene, methylhydrazine, chebyshev, arrhenius, eckart, tunnelling, cummingtonite, ...` (full list and script left in this PR's branch history via the commit below, not shipped as a repo file).

  **Result: 0 / 72 false positives, 0 hits in the live corpus.** Script and downloaded wordlist were scratch artifacts, deleted after this measurement — not part of the deliverable.

**Recommendation: `better-profanity`, gated as advisory-only per §3/§4, never blocking.** It is cheap (pure Python, `pip:` sub-block in `environment.yml` next to the `isbnlib2` precedent, §2.6), auditable (the wordlist is a file you can read, not a black box), and measured — not asserted — to not trip on this archive's actual vocabulary.

**Explicit scope limit, stated rather than implied:** a profanity wordlist catches profanity and slurs. It does not catch PII, doxxing, harassment phrased without a banned word, phishing links, or other "harmful content" in the broader sense the owner's phrasing gestures at. This plan does not propose those; it proposes the one grounded, measured intervention. If the owner wants that broader net, it is a separate, harder problem (likely closer to the `llm_precheck`/AI-Review-Assistant path in §2.2, which is heavier, optional, and explicitly off by default) and should be scoped separately rather than bolted onto this PR.

## 6. Slices

### Slice 1 — `MachineReviewCategory.content_screening` + `SubmissionAuditEventKind.content_screening_flagged`
Files: `backend/app/services/machine_review/schemas.py` (new enum value), `backend/app/db/models/common.py` (new `SubmissionAuditEventKind` member), new Alembic revision (already-deployed table — new revision, both `upgrade()`/`downgrade()`, `ALTER TYPE submission_audit_event_kind ADD VALUE`).
**Acceptance:** `alembic upgrade head` on an empty test DB round-trips; `downgrade()` documented (Postgres cannot drop an enum value cheaply — state the accepted no-op/rebuild-type tradeoff in the revision docstring, as the migration rules require both functions implemented, not that downgrade be lossless); existing `machine_review` and `submission` test suites unaffected (mutate the new value into an unrelated test and watch nothing break).

### Slice 2 — the screener
New module, e.g. `backend/app/services/content_screening/screener.py`: wraps `better-profanity` behind a narrow interface (`screen(text: str) -> tuple[Finding, ...]`), loaded once at import, whitelist configurable (empty by default), disabled entirely if the library import fails (mirrors the `llm_precheck` disabled-provider pattern — screening absence must never break an upload). `better-profanity==<pinned>` added to `backend/environment.yml`'s `pip:` block and `backend/pyproject.toml`'s `dependencies`.
**Acceptance:** unit tests reproduce the §5 measured result inside the real test suite (not just this plan's scratch script) — assert zero findings on the chemistry-vocab fixture and the live `network.description` string (fixture it verbatim), assert a finding on an injected test string containing a wordlist entry as its own token; mutate the wordlist-membership check to `in` (substring) and watch the chemistry-vocab test go red, proving the test would actually catch a regression to naive matching.

### Slice 3 — deposit-time wiring (layer 2)
Files: the persist workflows that already call `provenance_warnings.py` (`backend/app/workflows/computed_reaction.py` and the thermo/statmech/kinetics/transport/network upload workflows — enumerate exactly which workflows touch which in-scope free-prose fields before writing code, since §2.4's field list spans many workflows), `backend/app/services/submission.py` (new `record_content_screening_audit_event`, alongside `record_llm_precheck_audit_event`), curator-task creation reusing the existing model with no schema change to it (§2.5).
**Acceptance:** an upload carrying a flagged `network.description` still returns 2xx with the record created, plus one `UploadWarning`; exactly one `submission_audit_event` and one `machine_review_curator_task` row are written; a second upload with the *same* flagged text on the *same* record does not create a second task (fingerprint dedupe); a curator-task API listing (existing `admin_machine_review_curator_task_api.md` surface) shows the new task with `category=content_screening`; mutate the "never reject" invariant (make it raise) and watch a dedicated test fail.

### Slice 4 — read-time withholding (layer 1)
Files: the read services for the in-scope tables (`scientific_read/networks_search.py` and siblings for each table in §2.4's "long prose" rows), a shared helper (e.g. `apply_content_screening_visibility`, modeled on `apply_internal_ids_visibility`'s single-seam pattern) that checks for an open `content_screening` task against `(record_type, record_id, field)` and substitutes the withheld-placeholder text from §3.
**Acceptance:** the live-shaped `network.description` fixture renders unchanged when no task is open; a fixture with an open task on `network.description` renders the placeholder, not the text, not an omitted key, and `network.name` on the *same* record is unaffected (field-level, not record-level); resolving the task (existing curator-task resolution endpoint) flips the same fixture back to showing text on the next read, no cache to invalidate since this is computed per-request; mutate the helper to check `record_id` only (ignore `field`) and watch a test fail that proves two different flagged fields on one record don't cross-contaminate.

### Slice 5 — frontend rendering of the withheld state
Files: wherever `network.description`/`.note`/etc. currently render as plain prose (e.g. record pages using these fields — trace the actual components before writing code, this plan does not enumerate them since it is backend-first). Render the withheld placeholder distinctly from both "not recorded" (null) and normal text — likely reusing the site's existing `Disclosure`/absence-note visual language rather than inventing new UI.
**Acceptance:** screenshot comparison (per the project's headless-Chrome verification habit) of the three states — populated, null, withheld — on one page, confirming none look alike.

## 7. Owner's ruling already given, quoted verbatim

> "we need to build something for the harmful content gap or find a py package that looks for such thing."

> Layer 1: "render depositor free text only once a record is approved; publish the scientific data ... as normal meanwhile."

> Layer 2: "an `UploadWarning` plus a curator task. **Never auto-reject.**"

> Layer 3: "a screening library, only to flag for a human, **never to block or redact**."

The plan in §3–§6 satisfies "never auto-reject" and "never block or redact" exactly as stated. It deliberately **does not** implement layer 1 as literally specified ("once a record is approved") — §2.3/§3 explain why a record-level approval gate was rejected in favor of a field-level flag gate, and that substitution is the one place this plan diverges from the brief's literal wording. That divergence is flagged here for the owner to overrule if the literal reading was intended over the reasoned one.

## 8. Open questions for the owner

1. **Confirm or overrule §3's substitution** of "gate on approval" with "gate on an explicit unresolved flag." If the owner wants the literal approval-gate despite §2.3/§2.4's finding that it hides `network.name="hydrazine"` today, say so explicitly — that is a one-line change to the render-contract's condition, not a redesign.
2. **Which fields are in scope for the automated screener** (§3's "long free-prose fields only" list) — is the §2.4 table's split (identifying `name` excluded, `description`/`note`/`comment` included) the right line, or should specific high-traffic fields (e.g. `network.description`, since it is the one demonstrably public, human-written paragraph in the live archive) be prioritized for slice 3/4 and the rest deferred?
3. **Snapshot vs. re-read** for `details_json` (§4 step 3) — store the matched excerpt for curator triage, or only the rule/category id and let the curator re-read the live field? Leaning toward id-only; the owner may weigh curator ergonomics differently.
4. **Is `better-profanity` (§5) worth the dependency at all**, given its scope is limited to profanity/slurs and not the broader "harmful content" the brief's phrasing gestures at? The measured 0/72 false-positive rate says it is *safe* to add; it does not by itself say it is *worth* adding if the owner's real concern is broader (PII, harassment, doxxing) — in which case layers 1+2 without layer 3 (manual curator flagging only, no automated screener) is a legitimate, smaller-scope answer, and slice 2 becomes optional.
5. **Retroactive scan of existing content** — this plan only screens on new uploads (slice 3). Should a one-time backfill scan run `better-profanity` over the already-live archive's free-text columns to seed the curator queue with anything already published? Not scoped here; flag for a follow-up if wanted.
