# Scientific artifact reads

Standalone metadata search plus integrity-verified download for
`calculation_artifact` rows.

## 1. Purpose

Calculation artifact metadata is already exposed through three indirect
surfaces:

- `GET /api/v1/scientific/calculations/{ref_or_id}?include=artifacts`
- `GET\|POST /api/v1/scientific/calculations/search?include=artifacts`
- `GET /api/v1/scientific/reaction-entries/{id}/full?include=artifacts`

All three reuse the same `CalculationArtifactSummary` projection so
clients have one artifact-metadata shape. What was missing — and what
this spec covers — was a *standalone* search surface that answers
artifact-shaped questions without first chaining a calculation search:

- which artifacts exist for *this species/TS entry / LoT / software /
  workflow / conformer observation*?
- which artifacts have a particular `sha256` or `filename`?
- which artifacts have/don't have content-addressed metadata
  (`has_sha256`, `has_bytes`)?

## 2. Endpoints

- `GET  /api/v1/scientific/artifacts/search`
- `POST /api/v1/scientific/artifacts/search`
- `GET  /api/v1/scientific/artifacts/{sha256}/download`

Standalone detail (`GET /api/v1/scientific/artifacts/{ref_or_id}`) is
**deferred**: `calculation_artifact` has no `public_ref` column today,
and the existing `CalculationArtifactSummary.artifact_ref` is
permanently `None` until the schema grows one. The grouped detail of an
artifact is already available via its owning calculation, so this slice
does not block on the schema change. When `calculation_artifact` grows
a `public_ref`, adding the detail endpoint is non-breaking.

## 3. Search is metadata-only

Search endpoints never expose artifact body bytes; the separately gated
digest-download endpoint does. Artifact search:

- never inlines artifact body bytes (no `body`, `content`, `data`,
  `xyz_text`, `atoms`, `coords` keys);
- never resolves the storage `uri` to a presigned download URL
  (no `presigned_url`, `download_url`, `signed_url`,
  `url_for_download`);
- exposes `uri` verbatim only because `CalculationArtifactSummary`
  already does in the calculation detail include — surface parity is
  the design rule.

Recursive forbidden-key tests guard the contract.

### Operational assumption: private storage bucket

The `uri` exposure is safe **only** under the operational assumption
that the underlying storage bucket is private. Specifically:

- Artifact storage buckets MUST be private (no anonymous read ACL, no
  public bucket policy).
- The `uri` returned by this endpoint is a name, not an access grant —
  but it loses that property if the bucket is also publicly listable
  or readable.
- No TCKDB endpoint accepts a caller-supplied artifact `uri` as input for
  download, presign, or proxy.

The download route instead resolves an exact lowercase SHA-256 only when at
least one owning calculation has an explicit `approved` review state. It reads
the content-addressed object and verifies both digest and persisted byte count
before returning it. Unknown, under-review, rejected, deprecated, and otherwise
non-approved digests all return the same 404 response so existence cannot be
probed. Storage failure returns 503; integrity failure returns 502.

Successful downloads require cache revalidation so a later review-state change
can take effect. They carry an ETag equal to the quoted SHA-256,
`X-Content-SHA256`, `X-Content-Type-Options: nosniff`, and a content-disposition
filename derived from the approved upload-event row.

The deployment-side restatement of this assumption lives in
[`docs/deployment/production_checklist.md`](../../../docs/deployment/production_checklist.md#artifact-storage-buckets-must-be-private).
Hosted deploys that cannot honor it must not expose the scientific
read API anonymously.

## 4. Response fragments

Per-record shape:

```python
class ScientificArtifactRecord(BaseModel):
    artifact: CalculationArtifactSummary
    calculation: ArtifactCalculationContext
    owner: CalculationOwnerSummary | None = None
    available_sections: AvailableArtifactSections
```

`artifact` reuses the exact `CalculationArtifactSummary` returned by
calculation detail's `include=artifacts`. The standalone search
response and the calculation include section return byte-identical
artifact projections for the same artifact row.

`calculation` is always populated and carries the owning calculation's
ref + minimal core (type/quality/created_at) plus the review badge.
LoT / software / workflow-tool summaries are populated when the
`calculation` include token is in effect (default expansion of `all`).

`owner` is populated only when `include=owner` was supplied — it is the
same discriminated `CalculationOwnerSummary` returned by calculation
detail (`kind: "species_entry" | "transition_state_entry"`).

Envelope:

```python
class ScientificArtifactSearchResponse(BaseModel):
    request: RequestEcho
    review_summary: ReviewStatusSummary
    records: list[ScientificArtifactRecord]
    pagination: Pagination
```

## 5. Search filters

| Filter | Semantics |
|---|---|
| `artifact_kind` | exact enum match (`output_log`, `input`, `checkpoint`, `formatted_checkpoint`, …) |
| `filename` | exact filename |
| `filename_contains` | case-insensitive substring match (`LIKE` over `lower(filename)`) |
| `sha256` | exact match |
| `has_sha256` | Compatibility filter: true matches integrity-complete rows; false is empty after the integrity migration |
| `has_bytes` | Compatibility filter: true matches integrity-complete rows; false is empty after the integrity migration |
| `bytes_min` / `bytes_max` | inclusive range over `bytes` |
| `calculation_ref` | exact owning calc |
| `calculation_type` | `Calculation.type` |
| `quality` | `Calculation.quality` |
| `method` / `basis` | join on `level_of_theory` |
| `software` / `software_version` | join on `software_release` + `software` |
| `workflow_tool` / `workflow_tool_version` | join on `workflow_tool_release` + `workflow_tool` |
| `species_entry_ref` | artifacts on calcs owned by that species entry |
| `transition_state_entry_ref` | artifacts on calcs owned by that TS entry |
| `conformer_observation_ref` | artifacts on calcs anchored to that observation |
| `created_after` / `created_before` | inclusive `>=` / exclusive `<` over `calculation_artifact.created_at` |
| `min_review_status`, `include_rejected`, `include_deprecated` | trust gate over owning calc's review state |

At least one filter from the meaningful-filter set above is required.
Pure pagination / include / review knobs are rejected with 422
`missing_filter`. Explicit boolean `False` values
(`has_sha256=false`) count as meaningful filters.

Indexing notes:

- Artifact metadata search has btree indexes on
  `calculation_artifact.calculation_id`, `kind`, and `sha256` (migration
  `f6a7b8c9d0e1_add_calculation_artifact_indexes`). These cover the
  owning-calc join, the `artifact_kind` filter, and exact-`sha256` /
  `has_sha256` lookups.
- `filename_contains` remains a sequential `LIKE` over
  `lower(filename)` — a `pg_trgm` GIN index or a generated lowercase
  column would be needed to accelerate it, and is deferred until a real
  consumer asks for substring search at scale.
- `bytes_min` / `bytes_max` and `created_after` / `created_before` are
  not individually indexed; they are expected to be combined with one of
  the indexed filters in practice. Revisit if a workload appears that
  uses them as the only predicate.

## 6. Include behavior

Legal tokens: `calculation`, `owner`, `review`, `internal_ids`, `all`.

- `calculation` — adds LoT / software / workflow-tool summaries on the
  always-present calculation context.
- `owner` — populates the species/TS entry owner block.
- `review` — reserved; accepted as a no-op. The owning calc's review
  badge is already on the calculation context by default.
- `internal_ids` — restores integer ids per the Phase D internal-IDs
  policy. Has no effect when `settings.allow_public_internal_ids` is
  false.
- `all` — expands to `calculation`, `owner`, `review`. Does not
  restore internal ids.

Unknown tokens return 422 `unknown_include_token`.

## 7. Review / trust behavior

Artifacts do not carry their own review state. Review gating is
anchored on the **owning calculation**:

- Default trust posture hides artifacts whose owning calc is
  rejected/deprecated.
- `include_rejected=true` / `include_deprecated=true` restores them.
- `min_review_status` narrows further (D7-shallow).
- Each artifact record carries the owning calc's `RecordReviewBadge`
  under `calculation.review`.
- `review_summary` counts owning-calc review states across the
  matching artifact records (artifact-row cardinality — one artifact
  row, one vote — not unique-calc cardinality).

Calc-quality follows a piggy-back rule: if the caller has not opted
into rejected review state and did not explicitly set `quality=…`,
`Calculation.quality = rejected` is filtered out.

## 8. Internal-ID behavior

`apply_internal_ids_visibility` strips integer ids by default. When
the deployment allows it and the caller passes `include=internal_ids`,
the following are restored:

- `artifact.artifact_id`
- `calculation.calculation_id`
- owner `*_id` fields (when `include=owner`).

`request.filter` is echoed verbatim and never recursed into — caller
input is preserved.

## 9. Ordering

- Default sort:
  `review_rank ASC, created_at DESC NULLS LAST, artifact_id DESC`.
- Client-supplied `sort=` is rejected with 422
  `client_sort_not_supported`.
- `request.sort` echoes `"review_rank,created_at,artifact_id"`.

## 10. Pagination

Standard scientific pagination envelope: `offset`, `limit`,
`returned`, `total`. `total` counts the post-filter, post-review-gate
artifact row count before pagination slicing.

## 11. GET / POST behavior

- GET accepts every field as a query parameter.
- POST accepts the same fields in the JSON body.
- POST with non-empty query string returns 422
  `post_search_fields_must_be_in_body`.
- Body and query-string interpretations of the same filter set return
  identical `records` and `pagination`.

## 12. Relationship to existing artifact surfaces

- Calculation detail (`include=artifacts`) — returns *every* artifact
  for one calc with the same per-row projection.
- Reaction-full (`include=artifacts`) — returns artifacts grouped by
  owning calculation, same per-row projection.
- Calculations search (`has_artifacts`, `artifact_kind`) — narrows
  *calculations* by artifact existence/kind; full artifact metadata
  comes via the `include=artifacts` heavy include on the same call.
- Standalone artifact search (this endpoint) — discovery surface that
  is artifact-shaped at the top level.

The artifact projection is byte-identical across all three surfaces
by design and verified by cross-endpoint equality tests.

## 13. Non-goals

- artifact body download
- presigned URL generation
- artifact upload changes
- artifact public_ref migration (deferred — covered in
  `scientific_calculation_reads.md` open question 1)
- full owner-record expansion inline (`species_entry`, `transition_state_entry`,
  conformer observation, statmech, transport, network) — clients can
  follow `species_entry_ref` / `transition_state_entry_ref` /
  `conformer_observation_ref` to the dedicated detail endpoints.
- bulk export, RDKit search, schema redesign.

## 14. Implementation status

- Schema: `app/schemas/reads/scientific_artifact_search.py`
- Service: `app/services/scientific_read/artifacts_search.py`
- Routes: `app/api/routes/scientific/artifacts.py` (wired into the
  scientific router)
- Tests: `tests/api/scientific/test_api_scientific_artifacts.py`
- No DB migration required (no schema change).
- No ARC / `tckdb-client` changes.

## 15. Test plan

The API tests cover:

- missing-filter rule (GET + POST)
- pure pagination/trust knobs do not satisfy the rule
- client sort rejected
- filter coverage: artifact_kind, filename, filename_contains, sha256,
  has_sha256 (true/false), has_bytes (true/false), bytes_min/max,
  calculation_ref, calculation_type, method/basis, software/version,
  workflow_tool/version, species_entry_ref,
  transition_state_entry_ref, conformer_observation_ref,
  created_after/created_before
- ref handle errors: wrong prefix → 422, malformed → 422,
  unknown well-formed ref → empty result
- default trust posture hides rejected owning-calc artifacts
- `include_rejected` restores rejected owning-calc artifacts
- pagination envelope shape
- deterministic ordering across repeated calls
- GET / POST parity
- POST rejects query-string filter keys
- include behavior for `calculation`, `owner`, `all`,
  unknown token rejection
- `include=all` does not restore internal ids
- internal-ids visibility (default hide; `internal_ids` opt-in)
- artifact summary parity with calculation detail
  `include=artifacts`
- recursive forbidden-payload key walk on default and `include=all`
  responses

## 16. Open questions

1. **Artifact `public_ref`.** When `calculation_artifact` grows a
   public ref column, swap `artifact_ref` from `None` to the column
   value and add `GET /api/v1/scientific/artifacts/{ref_or_id}`.
   Non-breaking.

2. **Owner expansion beyond calculation owner.** This slice exposes
   the calc-owner relationships (species entry / TS entry / conformer
   observation). Statmech / transport / network / kinetics ownership
   surfaces would arrive as follow-up filters keyed on
   `statmech_source_calculation` / `transport_source_calculation` /
   `network_solve_source_calculation` join paths if a downstream
   consumer asks for them.

3. **Review-history include.** `include=review` is reserved today as
   a no-op; calc-anchored per-event review history could be inlined
   under each artifact record once a use case appears. The badge is
   already on the calculation context, so the no-op token does not
   leave callers without data.
