# Calculation artifact upload endpoint spec

## Goal

Add a primitive-entity endpoint `POST /api/v1/calculations/{calculation_id}/artifacts` that accepts one or more artifact files (ESS logs, input decks, checkpoints) and persists them through the existing artifact-storage infrastructure.

> **Invariant for the implementer.** Artifact upload is a second-phase, calculation-targeted, append-only operation. Batches must validate completely before any storage writes occur, and idempotency must be scoped to the concrete target calculation. Every implementation choice in this spec serves one of those three properties.

This is the canonical artifact upload path. Artifacts attach to calculations, so the upload URL targets calculations directly. Workflow uploads (`/uploads/conformers`, etc.) **do not** carry artifacts in their payloads — they create chemistry; artifact upload is a separate two-step request.

The hierarchy this respects:

```text
conformer_observation
   ├─ calculation (opt)        ← artifacts attach here
   ├─ calculation (freq)       ← artifacts attach here
   └─ calculation (sp)         ← artifacts attach here
        ├─ artifact (input)
        ├─ artifact (output_log)
        └─ artifact (checkpoint)
```

This unblocks the ARC adapter on branch `tckdb-imp` from including raw Gaussian/ORCA logs alongside conformer uploads, so calculations can be replayed, audited, and re-parsed server-side. It also resolves a downstream nuisance: with logs present, fields the adapter can't fill cleanly today (e.g. `software_release.version`) become recoverable from the log.

## Non-goals

- **No new artifact storage backends** — reuse existing MinIO/S3 via [backend/app/services/artifact_storage.py](../../backend/app/services/artifact_storage.py).
- **No DB schema changes** — the `calculation_artifact` table already exists. **No Alembic migration of any kind.**
- **No changes to `CalculationArtifact` model fields.**
- **No changes to existing inline-artifact workflows** — `network_pdep_upload` and `computed_reaction` keep their current `artifacts` field on the workflow payload as a workflow-bundle convenience. They are *not* refactored to use the new endpoint. Both patterns coexist; the new endpoint becomes the canonical primitive, the inline pattern stays as a bundle convenience. (This matches the project's two-layer-API guidance: bundle endpoints AND primitive endpoints.)
- **No artifact upload extension to other workflow uploads** (statmech, thermo, transport, etc.) — those workflows do not currently accept artifacts and this spec does not add the field to them. They get the new primitive endpoint for free, just like conformer uploads do.
- **No streaming/multipart upload** — inline base64 only, matching the existing `ArtifactIn` contract.

## Background — what's already built

TCKDB has a complete artifact pipeline already in production for two workflows. The conformer workflow does not use it, and there is no per-calculation endpoint despite the GET symmetry already existing.

| Component | Location | Status |
|---|---|---|
| `CalculationArtifact` table | [backend/app/db/models/calculation.py:839](../../backend/app/db/models/calculation.py#L839) | ✅ in schema; columns: `id`, `calculation_id`, `kind`, `uri`, `sha256`, `bytes` |
| `ArtifactKind` enum | [backend/app/db/models/common.py:147](../../backend/app/db/models/common.py#L147) | ✅ values: `input`, `output_log`, `checkpoint`, `formatted_checkpoint`, `ancillary` |
| `ArtifactIn` upload schema | [backend/app/schemas/workflows/network_pdep_upload.py:64](../../backend/app/schemas/workflows/network_pdep_upload.py#L64) | ✅ base64 inline + optional declared sha256 / bytes |
| Storage service | [backend/app/services/artifact_storage.py](../../backend/app/services/artifact_storage.py) | ✅ `validate_artifact()`, `store_artifact()`, `validate_total_upload_size()`. Content-addressed S3 paths derived from sha256. ESS-log signature check inside `validate_artifact` for `kind=output_log`. |
| Working precedent helper | `_persist_artifact` in [backend/app/workflows/computed_reaction.py](../../backend/app/workflows/computed_reaction.py) (~lines 74-96) | ✅ decode → validate → store → row creation |
| `GET /api/v1/calculations/{id}/artifacts` | [backend/app/api/routes/calculations.py:471](../../backend/app/api/routes/calculations.py#L471) | ✅ list endpoint already exists |
| `POST /api/v1/calculations/{id}/artifacts` | — | ❌ does not exist |
| `ConformerUploadResult` exposes calc IDs | [backend/app/api/routes/uploads.py:63](../../backend/app/api/routes/uploads.py#L63) | ❌ returns observation/species_entry/conformer_group IDs only — clients have no way to target artifact uploads at specific calculations |

## Required changes

### Change 1 — Promote `ArtifactIn` to fragments

`ArtifactIn` is consumed by multiple workflows and (after this spec) the new route. Per the project layout convention in `CLAUDE.md` ("Schemas — `entities/` for read/write, `workflows/` for upload payloads, `fragments/` for reusable pieces"), `ArtifactIn` belongs in `fragments/`.

**Files:**

- New: `backend/app/schemas/fragments/artifact.py` — contains `ArtifactIn` (verbatim move, no behavioural change).
- Update: `backend/app/schemas/workflows/network_pdep_upload.py` — import `ArtifactIn` from fragments and re-export at the same name to preserve backwards compatibility for any external callers.
- Update: `backend/app/workflows/computed_reaction.py` — import from fragments.

**Behavioural impact:** none. Pure refactor.

### Change 2 — Promote artifact persistence to a shared service with a batch helper

The helper at `_persist_artifact` in [backend/app/workflows/computed_reaction.py](../../backend/app/workflows/computed_reaction.py) is reusable. Move it to a shared service so the new route can call the same code without going through the workflow module. **Plus** add a batch helper that does two-pass validation — see *Batch atomicity* below for why this is non-optional.

**New file:** `backend/app/services/artifact_persistence.py`

**Public functions:**

```python
def persist_artifact(
    session: Session,
    *,
    calculation_id: int,
    artifact_in: ArtifactIn,
) -> CalculationArtifact:
    """Decode, validate, store, and record one artifact.

    Single-artifact path — used by the existing inline-artifact workflows
    (computed_reaction, network_pdep_upload) where the workflow's overall
    transaction boundary already covers atomicity. NOT for the new batch
    endpoint; use persist_artifact_batch() there.

    1. base64-decode content.
    2. validate (ESS-log signature for kind=output_log, declared sha256 / bytes integrity).
    3. write to content-addressed object store.
    4. create CalculationArtifact row pointing at the resulting URI.
    """


@dataclass(frozen=True)
class _DecodedArtifact:
    """Pass-1 output: decoded + fully validated, not yet stored."""
    artifact_in: ArtifactIn
    content: bytes
    computed_sha256: str


def validate_and_decode_all_artifacts(
    artifacts: list[ArtifactIn],
) -> list[_DecodedArtifact]:
    """Pass 1 (in-memory only, no I/O):

    For each ArtifactIn, base64-decode, run validate_artifact(), and
    capture the computed sha256 + decoded bytes. Raises on the FIRST
    failure — caller maps to 422. No object-store writes happen here.
    """


def persist_artifact_batch(
    session: Session,
    *,
    calculation_id: int,
    artifacts: list[ArtifactIn],
) -> list[CalculationArtifact]:
    """Two-pass: validate-all-then-store-all.

    1. Call validate_and_decode_all_artifacts(artifacts). If any single
       artifact fails validation, the whole batch raises and NOTHING is
       written to S3 or to the DB. (See *Batch atomicity* for rationale.)
    2. For each decoded artifact: store_artifact() to S3, then create
       CalculationArtifact row. Returns the rows in input order.

    The caller is responsible for transaction boundaries — this function
    creates rows via session.add() but does not commit.
    """
```

**Why a batch helper is non-optional:** S3 / MinIO writes are not part of the SQL transaction. Looping `persist_artifact()` per artifact means artifact #1 lands in S3 before artifact #3 fails ESS-signature validation. The DB rows can roll back; the bytes already in S3 cannot. Two-pass `validate_and_decode_all_artifacts` first guarantees that storage writes are only attempted for batches that have already passed every per-artifact gate.

**Failure mode this does NOT cover (acceptable for MVP, future hardening):** if pass-2 succeeds at S3 writes for artifacts 1-3 but the DB commit fails afterward (e.g. session rollback at a higher layer), the bytes are in S3 with no row pointing at them. Bytes leak. This is a content-addressed-storage leak (the SHA-derived key is unreachable but takes up space) — not a correctness leak (no orphaned-but-active provenance). MVP accepts this; a future spec can add compensating S3 deletes on commit failure if storage cost becomes a concern.

**Update:** `backend/app/workflows/computed_reaction.py` to import and call `persist_artifact()` (the single-artifact path) instead of the in-file `_persist_artifact`. Delete the in-file helper. The workflow's batch processing loops `persist_artifact()` because the workflow's outer transaction covers that atomicity claim — only the new route uses `persist_artifact_batch()`.

**Behavioural impact:** none for `computed_reaction`. Equivalence test: existing artifact tests must pass without modification.

### Change 3 — New endpoint `POST /api/v1/calculations/{calc_id}/artifacts`

**File:** [backend/app/api/routes/calculations.py](../../backend/app/api/routes/calculations.py)

Add a sibling route to the existing `GET /{calc_id}/artifacts`:

```python
class ArtifactsUploadRequest(BaseModel):
    artifacts: list[ArtifactIn] = Field(..., min_length=1)


class ArtifactsUploadResult(BaseModel):
    calculation_id: int
    artifacts: list[CalculationArtifactRead]   # existing read schema
    warnings: list[UploadWarning] = []


@router.post(
    "/{calculation_id}/artifacts",
    response_model=ArtifactsUploadResult,
    status_code=201,
)
def upload_calculation_artifacts(
    calculation_id: int,
    request: ArtifactsUploadRequest,
    session: Session = Depends(get_write_db),
    current_user: AppUser = Depends(get_current_user),
    idem: IdempotencyContext = Depends(idempotency_dependency),
):
    if (replay := idem.maybe_replay()) is not None:
        return replay

    # 404 if calculation does not exist
    calculation = session.get(Calculation, calculation_id)
    if calculation is None:
        raise HTTPException(status_code=404, detail="Calculation not found.")

    # 403 if the caller cannot modify this calculation's artifacts
    if not can_modify_calculation_artifacts(session, calculation, current_user):
        raise HTTPException(
            status_code=403,
            detail="You are not authorized to attach artifacts to this calculation.",
        )

    # Fail fast on aggregate size before any per-artifact validation work
    declared_or_actual_bytes = [
        a.bytes if a.bytes is not None else len(base64.b64decode(a.content_base64))
        for a in request.artifacts
    ]
    validate_total_upload_size(declared_or_actual_bytes)

    # Two-pass: validate all → store all + create rows. The batch helper
    # raises on first per-artifact failure with no S3 writes attempted.
    rows = persist_artifact_batch(
        session,
        calculation_id=calculation_id,
        artifacts=request.artifacts,
    )
    session.flush()

    result = ArtifactsUploadResult(
        calculation_id=calculation_id,
        artifacts=[CalculationArtifactRead.model_validate(r) for r in rows],
    )
    idem.record(session, status_code=201, body=result.model_dump(mode="json"))
    return result
```

**Idempotency:** route uses the same `idempotency_dependency` pattern as other POSTs. Replays return the original response; key conflicts return 409 (existing behaviour from idempotency middleware). See *Retry semantics and idempotency domain* below for the full two-phase retry model.

**Status codes:**
- `201` — artifacts persisted (one or more rows created).
- `401` — unauthenticated.
- `403` — authenticated but not authorized to attach artifacts to this calculation (see *Authorization*).
- `404` — `calculation_id` does not exist.
- `409` — idempotency conflict (existing middleware behaviour).
- `413` or `422` — total size exceeds cap from `validate_total_upload_size`.
- `422` — per-artifact validation failure (sha256 mismatch, missing ESS signature for `output_log`, malformed kind, etc.).

#### Authorization

**Artifact uploads are a first-class contributor operation, not a curator-only operation.** Ordinary authenticated contributors must be able to upload artifacts to calculations they created or to calculations belonging to a submission they own. Curator/admin role exists as an *override* for repair, migration, and cross-contributor curation workflows — not as the primary gate.

The risk this section guards against is *not* "community users uploading to the system" — that is the intended use. The risk is one contributor attaching misleading logs/checkpoints to *another contributor's* calculation. The ownership boundary is what makes uploaded artifacts trustworthy as provenance.

**Rule:** A request to `POST /calculations/{calc_id}/artifacts` is authorized iff at least one of the following holds. The endpoint must check them in order and accept on the first hit:

1. **Direct creation** — `calculation.created_by == current_user.id`. `Calculation` mixes in `CreatedByMixin` ([backend/app/db/models/calculation.py:49](../../backend/app/db/models/calculation.py#L49)) so this column is always populated and FK'd to `app_user.id`. This is the path that ARC uploads land on today (calculations created via `POST /uploads/conformers` get the caller's `app_user.id` as `created_by`).
2. **Submission ownership** — there exists a `submission_record_link` row with `record_type='calculation'` and `record_id=calc.id`, joined to a `submission` whose `created_by == current_user.id`. The submission tables already exist ([backend/app/db/models/submission.py](../../backend/app/db/models/submission.py), [backend/app/db/models/upload_job.py](../../backend/app/db/models/upload_job.py)); `SubmissionRecordType` enum already includes `calculation`. This path covers calculations created via the contribution-bundle submit flow ([backend/app/api/routes/bundles.py](../../backend/app/api/routes/bundles.py)) where the bundle, not the individual calc, is the unit of authorship.
3. **Curator/admin override** — `current_user.role in {curator, admin}` (using the existing [backend/app/api/deps.py:101](../../backend/app/api/deps.py#L101) `_CURATION_ROLES` set). This path exists explicitly for repair / migration / cross-contributor curation. It is the override clause, not the default.

The route does **not** use `require_curator_or_admin` as a route-level dependency — that would block contributors from uploading artifacts to their own work, which is the wrong semantics. The role check is one branch of the disjunction, evaluated inside the handler.

If none of the three branches accept, return 403 with detail like `"You are not authorized to attach artifacts to this calculation."` Do not leak the FK value of `created_by`, the submission_id, or any other internal identifier in the 403 detail.

##### Authorization helper

The three-branch check is non-trivial enough that it should live in a single helper function so the same logic can be reused (e.g. by a future `DELETE /calculations/{id}/artifacts/{artifact_id}` endpoint, by curation tooling, etc.). Suggested location: `backend/app/api/deps.py` alongside `require_curator_or_admin`:

```python
def can_modify_calculation_artifacts(
    session: Session,
    calculation: Calculation,
    user: AppUser,
) -> bool:
    """True if `user` may attach/modify artifacts on `calculation`.

    Three accept paths: direct creation, submission ownership, or
    curator/admin role. Caller is responsible for raising 403 if False.
    """
```

The route handler calls this and raises `HTTPException(403, ...)` on False.

#### Per-artifact validation

The existing `ArtifactIn` model already enforces some shape constraints. The spec makes them explicit so the implementer doesn't accidentally weaken them when promoting to fragments:

| Field | Constraint |
|---|---|
| `kind` | Must be a member of `ArtifactKind` (`input`, `output_log`, `checkpoint`, `formatted_checkpoint`, `ancillary`). Pydantic enum validation gives this. |
| `filename` | Required, `min_length=1`. Provenance metadata only — the server does **not** use it to derive the storage path. |
| `content_base64` | Required, `min_length=1`. Server base64-decodes; decode failures → 422. |
| `sha256` | Optional. If provided: must match `^[0-9a-f]{64}$` — 64 lowercase canonical hex characters. **Tighten** the existing `ArtifactIn` Field constraint from a length-only check (`min_length=64, max_length=64`) to a regex pattern (`pattern=r"^[0-9a-f]{64}$"`). Clients must normalize uppercase hex before sending. If provided and does not match the SHA-256 of the decoded bytes → 422. |
| `bytes` | Optional, `ge=0`. **Tighten to `gt=0`** — a zero-byte artifact has no information value and is almost certainly a client bug. If provided and does not match `len(decoded_bytes)` → 422. |
| `uri` | **NOT a client-supplied field.** `ArtifactIn` does not expose `uri` and must continue not to expose it. The `uri` on `CalculationArtifact` is always backend-generated by `store_artifact()`. Registering a remote URI without uploading bytes is a future, separate, trusted/admin-only path. |

ESS-log signature validation (`_validate_output_log_signature`) continues to fire inside `validate_artifact` for `kind=output_log`. The endpoint surfaces these as 422.

#### Retry semantics and idempotency domain

The conformer upload and artifact upload are **not** in the same retry domain. Once the conformer upload returns 201, its calc IDs are durable; subsequent artifact upload attempts retry against those IDs without re-creating chemistry rows. This is by design — it preserves work done in phase 1 even when phase 2 partially fails.

Concretely, ARC's expected pattern:

```text
1. POST /uploads/conformers           idempotency-key: <chem-key>
   → 201 { primary_calculation: {...}, additional_calculations: [...] }
2. for each calculation_id in the response:
     POST /calculations/{id}/artifacts  idempotency-key: <artifact-key-N>
     → 201 (or partial failure)
3. on retry: re-issue ONLY the missing/failed artifact uploads with the SAME
   per-artifact idempotency keys. Phase 1 is not re-attempted.
```

**Server requirements:**

- The artifact endpoint must accept idempotency keys distinct from the conformer upload's key. Same key, same payload → cached response. Different key, same content → new upload event (and new `CalculationArtifact` row — see *Artifact row uniqueness* below).
- The endpoint must tolerate the case where some artifacts for a calculation have already been uploaded and others have not. The current proposal — "request body contains only the artifacts the client wants to add this time" — naturally supports this: ARC sends the missing ones, gets 201, done.
- The endpoint should **not** enforce "all artifacts for this calculation must be uploaded in one request." The two-phase retry model relies on partial completion being a valid intermediate state.

##### Idempotency identity must include the concrete calculation target

The idempotency middleware ([backend/app/api/idempotency.py](../../backend/app/api/idempotency.py)) caches `(user, route, key, body) → response`. For routes whose paths have **no path parameters** (every existing TCKDB upload endpoint today), "route" is unambiguous: the route template equals the concrete URL. For this new route the URL has a `{calculation_id}` parameter, and that creates a real correctness hazard:

```text
POST /api/v1/calculations/10/artifacts  Idempotency-Key: abc  body: {...}   → 201 (calc 10 result)
POST /api/v1/calculations/20/artifacts  Idempotency-Key: abc  body: {...}   → ❌ replays calc-10 result if route key is template-only
```

This is both a correctness bug (wrong response) and a provenance bug (the second request looks successful but creates no row for calc 20).

**Requirement for the implementer:** the idempotency identity for `POST /calculations/{calculation_id}/artifacts` must include the concrete `calculation_id`. Two acceptable implementations:

1. **Narrower** (preferred unless reading `idempotency.py` reveals a broader bug): include `request.path_params` in the canonical idempotency domain for this endpoint specifically. Existing endpoints stay behaviour-unchanged because they have no path params.
2. **Broader**: change the middleware's notion of "route" from template to concrete path. This affects every endpoint; only do this if it's clearly the correct global fix.

The spec does **not** mandate the broader change. The implementer reads `idempotency.py` first, decides scope, and either (a) lands the narrow fix as part of this PR or (b) opens a separate PR for the middleware-level fix and pins this PR behind it.

**Client requirements (documented for ARC adapter, but worth pinning here so the server side doesn't accidentally encode contrary assumptions):**

- Per-artifact idempotency key = stable function of `(arc_project, species_label, calculation_id, artifact_kind, artifact_sha256)`. Stable across retries, distinct across logically-different artifacts.
- The sidecar tracks per-artifact status, not per-calculation. A `pending` artifact retry doesn't depend on the conformer-upload sidecar.

#### Artifact row uniqueness

**Decision:** no row-level dedup. The `calculation_artifact` table is treated as append-only event provenance, matching the project's identity-vs-result principle: identity tables dedupe, while result and provenance tables are append-only. Same bytes uploaded twice → two `calculation_artifact` rows pointing at the same content-addressed URI.

Rationale:

- Content-addressed S3 storage already dedups *bytes* (same SHA → same S3 object). Storage cost is bounded.
- `CalculationArtifact` does not have a uniqueness constraint on `(calculation_id, sha256, kind)` and this spec does not add one. Adding one would silently collapse legitimate re-upload events (e.g. a curator re-uploading an artifact that an automated client also sent earlier) without preserving the provenance trail.
- If duplicate rows ever become a real annoyance, add an *application-level* idempotency-key-scoped dedup in a follow-up — not a DB uniqueness constraint.

This means: `Idempotency-Key` controls retry-replay (same key + same payload → cached response, no new row). It does **not** control content-replay (different key + same bytes → two rows, one S3 object). Those are different operations.

### Change 4 — Extend `ConformerUploadResult` to expose calc IDs

Without this, ARC has no way to target artifact uploads at the calculations it just created.

**File:** [backend/app/api/routes/uploads.py:63](../../backend/app/api/routes/uploads.py#L63)

Use a richer reference object rather than a plain ID list. The list shape "additional_calculation_ids[i] corresponds to request.additional_calculations[i]" is correct but fragile — clients that drift in calc-array ordering get silent breakage. The richer shape pins the correspondence explicitly via `request_index` and lets clients filter/route by `type` without re-reading the original request.

```python
class CalculationUploadRef(BaseModel):
    """A handle to a calculation created by a workflow upload, returned in
    the upload result so clients can target follow-up requests (e.g. POST
    /calculations/{id}/artifacts) at specific calculations.

    request_index pins the correspondence to the original request's
    additional_calculations[] ordering so reordering on either side is
    safely detectable. Always set on additional calculation refs; null
    on the primary calculation ref.
    """

    request_index: int | None = None
    calculation_id: int
    type: CalculationType


class ConformerUploadResult(BaseModel):
    id: int
    type: str = "conformer_observation"
    species_entry_id: int
    conformer_group_id: int
    primary_calculation: CalculationUploadRef
    additional_calculations: list[CalculationUploadRef] = []
    warnings: list[UploadWarning] = []
```

`CalculationUploadRef` lives in `app/schemas/entities/calculation.py` (or wherever the read-shaped calculation refs already live — defer to existing conventions). The same model can be reused by other workflow upload result schemas (`ReactionUploadResult`, `KineticsUploadResult`, etc.) once they need this; defining it once here pays forward.

**Workflow update:** `persist_conformer_upload` in [backend/app/workflows/conformer.py](../../backend/app/workflows/conformer.py) currently returns only the `ConformerObservation`. It must also expose calc IDs and types so the route handler can populate the response. Cleanest shape: return a small dataclass:

```python
@dataclass
class ConformerUploadOutcome:
    observation: ConformerObservation
    primary_calculation_id: int
    primary_calculation_type: CalculationType
    additional_calculation_ids: list[int]      # same order as request.additional_calculations
    additional_calculation_types: list[CalculationType]
```

The route handler ([backend/app/api/routes/uploads.py:166](../../backend/app/api/routes/uploads.py#L166)) reads from this struct and zips the additional lists with `range(len(...))` to populate `request_index` on each ref.

**Behavioural impact:** existing clients receive additional fields in the response and a richer shape than they had before. The previously-documented `id`, `species_entry_id`, `conformer_group_id` fields are unchanged. Existing tests that assert on the response need extending, not rewriting.

**Apply the same treatment to other workflows that create calculations** (e.g. `ReactionUploadResult`, `KineticsUploadResult`, etc.) — IFF that's needed to make their artifacts uploadable. Recommend the implementer scope this to `ConformerUploadResult` only for this PR; extend other result schemas in a separate PR as the corresponding ARC adapter methods land.

#### Caller audit for the return-type change

`persist_conformer_upload` changes its return type from `ConformerObservation` to `ConformerUploadOutcome`. Every caller must be audited and updated. The implementer must verify and update at least:

- `backend/app/api/routes/uploads.py` — synchronous upload route handler. Maps `ConformerUploadOutcome` → `ConformerUploadResult` with the new `CalculationUploadRef` shape.
- `backend/app/workers/upload_worker.py` — asynchronous upload worker. **Critical:** the worker stores its result in `upload_job.result` (JSONB column on the `upload_job` table). That JSON must include the same `primary_calculation` and `additional_calculations` ref shape as the synchronous response, otherwise async-upload clients (offline ARC, batch contributors) cannot get the calculation IDs they need for second-phase artifact upload. **The feature working in sync mode but not async mode is not acceptable.**
- `backend/tests/workflows/test_conformer_upload.py` — direct-call workflow tests. Update assertions to read from the new outcome shape.
- Any test under `backend/tests/workers/` that asserts on `upload_job.result` for conformer-kind jobs.
- Any test that asserts on `ConformerUploadResult` response shape.

**Verification step before coding starts:** the implementer should `grep -rn "persist_conformer_upload\|ConformerUploadResult" backend/` to enumerate every caller and assertion. The list above is necessary but possibly not sufficient.

## Test plan

All tests should run via `conda run -n tckdb_env pytest backend/tests/...`.

- `backend/tests/api/test_api_calculation_artifacts.py` (new):
  - `POST /calculations/{id}/artifacts` with one `output_log` artifact (minimal Gaussian/ORCA header bytes) by the user who created the calculation: 201; row exists in `calculation_artifact`; `uri`, `sha256`, `bytes` populated; `GET /calculations/{id}/artifacts` lists it.
  - `POST` with multiple artifacts in one request succeeds atomically; if any one fails validation the whole batch is rejected (no rows created, no S3 writes leaked — assertable by checking row count and bucket contents).
  - **Authorization** — exercise all three accept paths and the reject path:
    - Unauthenticated request → 401.
    - Authenticated `user`-role caller who is `created_by` of the calculation → 201 (direct-creation path).
    - Authenticated `user`-role caller who did not create the calculation but owns a submission that links to it (`submission_record_link` row, `submission.created_by == caller`) → 201 (submission-ownership path).
    - Authenticated `curator`-role caller, no ownership relationship → 201 (override path).
    - Authenticated `admin`-role caller, no ownership relationship → 201 (override path).
    - Authenticated `user`-role caller with no direct-creation and no submission-ownership relationship to the calculation → 403.
    - 403 error detail must not leak `calculation.created_by`, the submission_id, or any other internal identifier.
    - Test the helper function `can_modify_calculation_artifacts` directly with synthetic inputs covering all four input combinations (created_by match Y/N × submission match Y/N × role override Y/N), plus the curator/admin permutations.
  - **Per-artifact validation**:
    - `kind` outside the enum → 422.
    - `bytes=0` declared → 422.
    - Missing `filename` → 422.
    - `sha256` declared but not 64 lowercase hex → 422.
    - `sha256` declared but does not match content → 422.
    - `bytes` declared but does not match decoded length → 422.
    - `kind=output_log` with content lacking ESS signature → 422.
  - `POST` to a non-existent `calculation_id` → 404.
  - `POST` exceeding `validate_total_upload_size`'s cap → fails before any storage write fires.
  - **Retry semantics / idempotency domain**:
    - Same idempotency key + identical payload → cached response, no new row, no new S3 object write attempt.
    - Same content (same SHA), different idempotency key → two `calculation_artifact` rows, one S3 object (asserts the append-only-rows + dedup-bytes property).
    - Partial-success retry: upload artifacts A and B; artifact B fails validation; retry with only B (after fixing it) — A's row from the first attempt is unaffected, B lands.
    - **Cross-calc-id isolation** (critical): same idempotency key + same body sent to `/calculations/{A}/artifacts` and then to `/calculations/{B}/artifacts` — calc B gets a fresh 201 with rows attached to calc B, not a replay of calc A's response. This test fails if the path-param-scoping fix is missing or wrong.
  - **Batch atomicity** — exercise the two-pass behaviour explicitly:
    - Send a batch of 3 artifacts where artifact #2 has a SHA mismatch. Assert: 422, zero rows in `calculation_artifact`, zero S3 writes attempted (mock the storage client and assert call count).
    - Send a batch of 3 artifacts where artifact #3 has an invalid ESS signature. Same assertions.
    - Send a batch where the *aggregate* size exceeds the cap. Assert: 422, zero rows, zero S3 writes.
- `backend/tests/api/test_api_upload_conformer.py` (extend existing or create):
  - `POST /uploads/conformers` returns `primary_calculation` and `additional_calculations` with the new shape. Each ref has `calculation_id` and `type`; refs in `additional_calculations` have `request_index` matching their position in the original request. Use those IDs to make a follow-up `POST /calculations/{id}/artifacts` and verify the artifact lands.
- Equivalence regression: existing `computed_reaction` and `network_pdep_upload` artifact tests must continue passing after `ArtifactIn` and `_persist_artifact` are moved.
- No new tests for `artifact_storage` itself — the service is unchanged.

## Open questions for the implementer

1. **`CalculationArtifactRead` schema** — does a read-shaped artifact response model already exist? If yes, reuse it. If not, create a small one in `app/schemas/entities/calculation.py` (or wherever the existing artifact-read structure for `GET /artifacts` lives). It should expose `id`, `calculation_id`, `kind`, `uri`, `sha256`, `bytes`, `created_at`. Do not expose any internal-only fields.
2. **Batch atomicity** — recommend all-or-nothing within a single request (one `artifacts: [...]` POST). If artifact #3 fails validation, roll back artifacts #1 and #2. Easier to reason about for clients. Confirm with the implementer that the SQLAlchemy session boundaries support this — the existing test fixture rolls back per test, which is consistent.
3. **Should `conformer_observation_id` be a query filter on `GET /calculations`?** — probably yes (so clients can find calc IDs without going through the upload response if they need to retry artifact uploads later), but it's out of scope for this spec. Track separately if you want it.
4. **Total upload size cap configurability** — `validate_total_upload_size` today is a single global value. If conformer-upload artifact bundles need a different cap than network/pdep upload artifact bundles, this might need to become a parameter. Default position: keep it global; revisit only if integration testing surfaces a real problem.

## Implementation order

The order intentionally puts pure refactors first (steps 1-2 leave existing tests passing without modification), then a return-type change with caller audit (step 3), then the new behaviour (steps 4-6). No Alembic migration at any step.

1. **Refactor `ArtifactIn` to fragments**, no behaviour change.
   - Create `backend/app/schemas/fragments/artifact.py`.
   - Move `ArtifactIn` and tighten the `sha256` constraint to `pattern=r"^[0-9a-f]{64}$"` and `bytes` to `gt=0` (not `ge=0`).
   - Re-export from `network_pdep_upload.py` for back-compat.
   - Update internal imports in `computed_reaction.py`.
   - Run existing tests — should pass unchanged.

2. **Refactor artifact persistence into a shared service** with the new batch helper.
   - Create `backend/app/services/artifact_persistence.py` with `persist_artifact()` (single), `validate_and_decode_all_artifacts()` (pass 1), and `persist_artifact_batch()` (two-pass).
   - Update `computed_reaction.py` to call `persist_artifact()` and delete its in-file `_persist_artifact`.
   - Run existing `computed_reaction` and `network_pdep_upload` artifact tests — should pass unchanged.

3. **Add `ConformerUploadOutcome` and `CalculationUploadRef`; update all callers including the async worker.**
   - Add `CalculationUploadRef` in `app/schemas/entities/calculation.py`.
   - Add `ConformerUploadOutcome` dataclass in `app/workflows/conformer.py`; change `persist_conformer_upload` return type.
   - Update `app/api/routes/uploads.py` to map outcome → `ConformerUploadResult` with the richer ref shape.
   - **Update `app/workers/upload_worker.py` so `upload_job.result` JSON exposes the same `primary_calculation` / `additional_calculations` shape.** Async clients depend on this for second-phase artifact upload.
   - Run `grep -rn "persist_conformer_upload\|ConformerUploadResult" backend/` and update every assertion site found.

4. **Add the authorization helper and unit-test the matrix.**
   - Add `can_modify_calculation_artifacts(session, calculation, user) -> bool` in `app/api/deps.py`.
   - Three-branch disjunction: direct creation, submission ownership, curator/admin override.
   - Unit-test all four input combinations (created_by Y/N × submission Y/N × role override Y/N) plus the curator/admin permutations against synthetic inputs.

5. **Add `POST /api/v1/calculations/{calculation_id}/artifacts`** with the two-pass batch flow.
   - Route handler in `app/api/routes/calculations.py`, sibling to the existing `GET /{id}/artifacts`.
   - Order: idempotency replay → load calc or 404 → authz helper or 403 → aggregate size cap → `persist_artifact_batch()` → flush → record idempotency response.
   - Add full happy-path, authz, validation, and batch-atomicity tests.

6. **Add idempotency tests proving cross-calc-id isolation** and land the path-param fix the implementer chose (narrow or broad — see *Idempotency identity must include the concrete calculation target* above).
   - Test: same key + same body to calc A then calc B → calc B gets a fresh 201 with calc-B's response, not a replay of calc-A's.
   - If the narrow fix was chosen, this test exercises the new path-params-in-canonical-domain behaviour for this endpoint specifically.
   - If the broader middleware fix was chosen, also re-run regression tests against existing upload endpoints to confirm nothing regressed.

7. **Run the full test suite.** The targeted suite first, then the project-wide one:
   ```bash
   conda run -n tckdb_env pytest backend/tests/api/test_api_calculation_artifacts.py backend/tests/api/test_api_uploads.py -v
   conda run -n tckdb_env pytest backend/tests/ -v
   ```

## ARC-side follow-up (separate, out of scope here)

After this spec ships and is merged, the ARC adapter on branch `tckdb-imp` will be updated to:

- Add `tckdb.artifacts.upload: bool`, `tckdb.artifacts.kinds: list[str]`, `tckdb.artifacts.max_size_mb: int` knobs to `arc/tckdb/config.py`.
- After a successful `submit_from_output(...)`, read the response's `primary_calculation.calculation_id` and each entry of `additional_calculations[].calculation_id`. Use the `type` on each ref to route the right log to the right calculation (opt log → opt calc, freq log → freq calc, sp log → sp calc). Read `record["opt_log"]` / `record["freq_log"]` / `record["sp_log"]` from disk (paths resolved against the ARC project directory), base64-encode + sha256, and `POST /calculations/{calc_id}/artifacts` for each.
- Each artifact upload gets its own idempotency key, derived from `(arc_project, species_label, calc_id, artifact_kind, artifact_sha256)` so retries are safe.
- Adapter-side strict mode: artifact upload failures default to logging + sidecar record, not raising — same policy as conformer upload failures.

That ARC-side work is mechanical once the TCKDB-side endpoint exists. Tracked on the ARC repo, not here.
