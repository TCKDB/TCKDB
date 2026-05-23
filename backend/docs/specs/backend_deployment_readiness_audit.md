# TCKDB Backend Deployment Readiness Audit

**Date:** 2026-05-22
**Scope:** `backend/` — public scientific reads, auth, rate limits, DB/migrations, observability, tests, OpenAPI/client contract
**Goal:** Identify what is production-ready, what is risky, what needs hardening before public deployment, and what should be implemented next.

This audit is read-only. No code, schemas, or tests were modified.

---

## 1. Executive summary

The backend is **deployment-ready for a closed / shared-private deployment** today, and **close to deployment-ready for a public read deployment** with a small set of focused fixes. Core posture is strong: authentication uses PBKDF2 with timing-safe comparison, API keys are SHA-256 hashed and session-bootstrapped, sessions are server-side with fixed expiry, structured pagination is hard-capped, internal IDs are dual-gated, rejected/deprecated records are filtered by default, GET/POST search parity is enforced and tested, and the error envelope intentionally never leaks DB row IDs.

The blockers are mostly **operational, not architectural**: open-registration defaults to `True` (must be flipped in prod), there is no `/readyz` endpoint, no request-ID correlation in logs, no Sentry/OTel integration, no FK indexes on the most filtered-on columns of `calculation`, the RDKit structure search runs `mol_from_smiles(sp.smiles)` inline per row (no stored/indexed mol column yet), and the **`.claude/rules/migration-rules.md` single-migration policy has already been silently relaxed** for Phase 4a without updating the rule file or shipping a deployed-DB migration playbook.

Test posture is excellent (3,923 tests, zero `skip`, two `xfail`, per-test transaction rollback). Documentation around deployment scenarios is unusually mature for a project at this stage.

**Headline call:** ship to a shared-private (lab-internal) deployment now; do P0/P1 work before broad public exposure.

---

## 2. Current public read surface

Public, anonymous-readable routes under `/api/v1/scientific/*` cover:

- **Species** — list + detail + structure search (exact / substructure / similarity)
- **Reactions** — search + full + path expansion
- **Transition states** — search + detail + path expansion
- **Calculations** — search + detail + path includes + dependency / artifact / geometry / scan / IRC / path-search expansions
- **Conformers** — group + observation reads
- **Statmech** — search + detail
- **Thermo / Kinetics / Transport** — search + detail
- **Network / PDep** — search + detail + (gated) `points` expansion
- **Literature** — inverse reads (records by literature)
- **Energy-correction / reference reads**
- **Artifact search** — metadata-only (URIs are storage keys, not signed download URLs)

Legacy entity routes (`/api/v1/{thermo,kinetics,geometries,...}`) are guarded by `LEGACY_READS_REQUIRE_AUTH` (default permissive locally, must be set in production — see §5).

---

## 3. Deployment readiness scorecard

| Area | Score | Notes |
|---|---|---|
| Authentication & password storage | A | PBKDF2-SHA256/200k, salted, timing-safe compare |
| API-key model | A− | SHA-256 hashed, session-bootstrapped, no per-key scope |
| Authorization (admin / curator / review) | A | Role enum + dependency guards consistently applied |
| Public read safety (pagination, internal IDs, status filters) | A | Hard cap 200; `include=all` fan-out guarded; `internal_ids` dual-gated |
| OpenAPI surface | A- | Tags + response models present; full schema frozen via golden snapshot (`tests/api/test_openapi_snapshot.py`); sparse field-level docs remain a P2 |
| Response/error envelope consistency | A | Unified shape across search endpoints; no PK/FK leaks in `detail` |
| Rate limiting | B | Per-bucket + IP + path classification, but **in-process store** — partitions per worker |
| DB schema invariants & constraint coverage | B+ | Cascades sometimes app-only; a few should-be-NOT-NULL columns on `network` |
| Indexing for read filters | C+ | **No indexes** on `calculation.lot_id`, `calculation.software_release_id`, `calculation.conformer_observation_id` |
| Migration strategy for deployed DB | C | Single-migration policy already relaxed; no documented prod migration playbook |
| Structure search performance | A- | Stored `species_entry.mol` cartridge column with GiST index (migration `d4e5f6a7b8c9`); service reads directly from `se.mol`. Fingerprint cache for similarity remains deferred. |
| Health / readiness / liveness | C+ | `/health` exists and pings DB; no `/readyz`, no startup grace, no socket-hang guard |
| Structured logging / request IDs | C | Plain-text logs; no correlation IDs; no JSON output |
| External error/perf monitoring | D | No Sentry / OpenTelemetry / APM integration |
| CORS / CSRF | A | Default-empty origin allow-list; cookie SameSite=Lax + HttpOnly + Secure |
| Tests | A | 3,923 tests, 0 skips, per-test rollback, factories deterministic |
| Backup / restore documentation | B | Documented manually in `docs/deployment/shared-private-deployment.md`; no shipped script |

---

## 4. High-priority blockers

The following must be addressed before broad public exposure. None block a closed deployment.

### P0 — blockers before public deployment

| # | Finding | Where | Fix |
|---|---|---|---|
| P0-1 | `auth_allow_open_registration` defaults to `True` | [backend/app/api/config.py:24](backend/app/api/config.py#L24) | Set `AUTH_ALLOW_OPEN_REGISTRATION=false` in prod env. Add deploy checklist. |
| P0-2 | Migration policy drift — `.claude/rules/migration-rules.md` says "single migration only" but 4 migrations exist (`60b67e360daf`, `d861dfd60891`, `a1b2c3d4e5f6`, `b2c3d4e5f6a7`) | [backend/alembic/versions/](backend/alembic/versions/), [.claude/rules/migration-rules.md](.claude/rules/migration-rules.md) | Update the rule to reflect Phase 4a relaxation; document the deployed-DB migration flow (init vs upgrade). |
| P0-3 | No prod migration / bootstrap playbook | (missing) | Add `docs/deployment/migrations.md`: empty DB → `alembic upgrade head`; existing DB upgrade flow; public_ref backfill triggers. |
| P0-4 | No `/readyz` (readiness) endpoint; `/health` lacks socket-hang timeout | [backend/app/api/routes/health.py:14](backend/app/api/routes/health.py#L14) | Add `/readyz` checking DB, schema rev, and a guarded SELECT under timeout. |
| P0-5 | `EXPOSE_API_DOCS` is convenient but unset enforcement | [backend/app/api/config.py:96](backend/app/api/config.py#L96) | Confirm prod deploy sets `EXPOSE_API_DOCS=false` and add to checklist. |

### P1 — high priority before broader use

| # | Finding | Where | Fix |
|---|---|---|---|
| P1-1 | Rate-limit store is in-process — multi-worker deployments partition the budget per worker | [backend/app/api/rate_limit.py:34](backend/app/api/rate_limit.py#L34) | Add Redis-backed store before scaling past one worker. Budget is otherwise honest. |
| P1-2 | No FK indexes on hot Calculation filter columns: `lot_id`, `software_release_id`, `conformer_observation_id`, `species_entry_id`, `transition_state_entry_id`, `literature_id` | [backend/app/db/models/calculation.py:71-107](backend/app/db/models/calculation.py#L71-L107) | **Addressed (2026-05-23).** Indexes added via additive migration `c3d4e5f6a7b8_add_calculation_fk_indexes` and `index=True` on the model columns. |
| P1-3 ✅ addressed | Structure search runs `mol_from_smiles(sp.smiles)` inline — O(N rows) per query, no GiST index | [backend/app/services/scientific_read/structure_search.py](backend/app/services/scientific_read/structure_search.py) | **Done.** Migration `d4e5f6a7b8c9` backfills `species_entry.mol` and creates `ix_species_entry_mol_gist`. The service now reads `se.mol` directly; substructure/similarity queries hit the GiST index. A guard test (`test_structure_search_uses_stored_mol_column_not_inline_conversion`) fails if the inline-conversion pattern reappears. |
| P1-4 | No request-ID / correlation ID in logs | [backend/app/api/errors.py](backend/app/api/errors.py) | Add middleware that emits `X-Request-ID` and injects it into logging context. |
| P1-5 | No JSON / structured logs | (config) | Switch to a JSON log handler for the hosted profile. |
| P1-6 | `Network.name` is nullable; no unique constraint on provenance tuple | [backend/app/db/models/network.py:28](backend/app/db/models/network.py#L28) | Make name NOT NULL or add `(literature_id, name)` unique constraint to prevent silent dupes. |
| P1-7 | FK `ON DELETE` defaults to `RESTRICT` at DB level even where ORM cascades `delete-orphan`; deletion paths must orphan-delete carefully | [backend/alembic/versions/d861dfd60891_create_intial_schema.py](backend/alembic/versions/d861dfd60891_create_intial_schema.py) | Audit code paths that delete `Species` / `Calculation` / `ConformerObservation`; align DB-level ON DELETE rules with intended ORM semantics. |
| P1-8 ✅ addressed | OpenAPI has no frozen golden snapshot — only path-presence tests | [backend/tests/api/scientific/test_api_openapi.py](backend/tests/api/scientific/test_api_openapi.py) | **Done.** [backend/tests/api/test_openapi_snapshot.py](backend/tests/api/test_openapi_snapshot.py) freezes the full normalized `/openapi.json` in [backend/tests/api/golden/openapi.json](backend/tests/api/golden/openapi.json). Regenerate intentionally with `UPDATE_OPENAPI_GOLDEN=1 pytest tests/api/test_openapi_snapshot.py`. Review the diff. |
| P1-9 ✅ addressed | Chebyshev / PLOG coefficient payload has no explicit truncation cap | [backend/app/services/scientific_read/network_kinetics.py](backend/app/services/scientific_read/network_kinetics.py) | **Done.** Chebyshev `include=coefficients` flattens in `(temperature_order, pressure_order)` order and is capped at `settings.public_max_limit`; payload exposes `coefficient_count_total` + `coefficients_truncated`. PLOG `include=plog` is now a wrapper with `entries` capped at the same limit and surfaces `plog_entry_count_total` + `plog_entries_truncated`. `include=all` still excludes `points`. |

---

## 5. Security and authorization findings

**Strong**:
- PBKDF2-SHA256 with 200k iterations and unique 16-byte salt — `app/services/auth.py:65-82`.
- Constant-time password / API-key compare — `auth.py:85-103`, `auth.py:208-221`.
- API keys stored only as SHA-256 hash; plain key returned exactly once; `tck_` prefix; 32-byte URL-safe random — `db/models/api_key.py:28-29`, `services/auth.py:115-117,189-205`.
- Session model is server-side with role-based TTL (user=7d, curator=3d, admin=12h) and no sliding refresh — `services/auth.py:44-48,151-170`.
- Cookies: `HttpOnly`, `SameSite=Lax`, `Secure` in hosted profile — `api/config.py:113-115`.
- CORS middleware only mounted if `CORS_ALLOW_ORIGINS` is non-empty — `api/app.py:55-62`.
- Admin role escalation requires `require_admin` (only admins can promote) — `api/routes/admin.py:30-42`.
- Review writes are curator/admin-gated; reads any-authenticated — `api/routes/record_reviews.py:38-113`.
- No dev-only login bypass found.

**Risky**:
- **P0-1**: `auth_allow_open_registration: bool = True` default. Combined with no email verification (`auth.py:43-47`) and no CAPTCHA, this is the single largest pre-public-deploy hazard.
- **P2**: `/auth/me`, `/auth/register`, `/admin/users/{user_id}/role` and API-key endpoints expose `id: int` — DB PKs on the auth surface. `ALLOW_PUBLIC_INTERNAL_IDS` only governs the scientific surface. Low-impact but inconsistent with the policy elsewhere. (`auth.py:55-62`, `admin.py:25-27`.)
- **P3**: No per-API-key permission scope — keys inherit owner role; revocation is immediate.

---

## 6. Rate-limit and abuse-control findings

**Architecture** ([rate_limit.py:271-340](backend/app/api/rate_limit.py#L271-L340)):

- Six buckets by `(route_class, credential_presence)`: `login` (10/min IP), `register` (10/hour IP), `anon_read` (60/min), `auth_read` (300/min), `auth_write` (30/min), `anon_other` (20/min).
- IP resolution honors `trusted_proxy_header` and falls back to ASGI transport peer — spoof-resistant.
- 429 responses include `Retry-After` plus a machine-readable `retry_after_seconds`.
- Health endpoints bypass the limiter.

**Gaps**:

- **P1-1**: In-process fixed-window store partitions limits per worker. For Gunicorn/Uvicorn with N workers behind one proxy IP, callers get N× the budget. Acceptable for single-worker MVP; **must move to Redis (or memcached) before scaling**.
- **P2**: No explicit cost-weighted budget for known-expensive endpoints (`structure-search`, `network/PDep`, `calculation/search?include=all`). Currently they share `anon_read` (60/min). Recommend separate buckets with stricter budgets for these endpoints.
- **P2**: `register` is per-IP only. A single attacker behind one IP gets 10 / hour; a botnet beats this trivially. Mitigation is upstream (Cloudflare / WAF), not in-app.

---

## 7. Database and migration findings

**Migration drift** (P0-2):
- 4 Alembic revisions present: `60b67e360daf` (RDKit ext), `d861dfd60891` (initial schema), `a1b2c3d4e5f6` (molecular property observation), `b2c3d4e5f6a7` (MPO timestamps).
- `a1b2c3d4e5f6` docstring relaxes the "single-migration" rule for Phase 4a onward.
- `.claude/rules/migration-rules.md` and the `CLAUDE.md` block both still say "Never create new Alembic migration files." **This is now wrong and silently misleading.**

**Public refs** ([app/services/public_refs.py](backend/app/services/public_refs.py)):
- 18 entities currently mint public refs via `PublicRefMixin`. Length: `String(40)`, longest prefix observed = `nsolve` (6) → 33-char ceiling, 7 chars headroom.
- **Gap**: `MolecularPropertyObservation` is ref-bearing but missing from the prefix map — P2.

**Indexing**:
- Hot filter columns lack FK indexes (P1-2 above). PostgreSQL does **not** auto-index FK source columns, so this is a real query-time scan risk once data grows.
- `calculation_parameter` is indexed on `(calculation_id)` and `(canonical_key)` — fine.
- `species_entry.species_id` and `conformer_observation.conformer_group_id` are indexed.

**Constraints & nullability**:
- `network.name` nullable; consider NOT NULL or unique on `(literature_id, name)` (P1-6).
- FKs use `deferrable=True initially="IMMEDIATE"` with implicit `ON DELETE RESTRICT`. Some ORM-side `cascade="all, delete-orphan"` won't be backed by DB-level rules — audit deletion paths (P1-7).

**RDKit `mol` column**:
- `types.py:1-13` defines `RDKitMol`.
- `SpeciesEntry.mol` exists but is **not populated by the upload path or used by the structure search** — search runs `mol_from_smiles(sp.smiles)` inline. The variable name `_STORED_MOL_EXPR` in `structure_search.py:382` is misleading: it's a per-row conversion expression, not a stored-column reference.

**Cascade rules**:
- `Calculation` correctly cascades to `*_result`, geometries, dependencies, scan/IRC/path points, artifacts, parameters — all `delete-orphan`.
- Species / reaction / conformer paths use `save-update, merge` only — safe.

**JSONB**:
- `calculation.parameters_json`, MPO `vector_json`/`tensor_json`, `submission`/`upload_job`/`idempotency` payloads. All justified; no current pressure to promote.

**Enums**: All centralized in `app/db/models/common.py` — 47 enums, well-organized.

---

## 8. Performance and indexing findings

| Hot path | Risk | Classification |
|---|---|---|
| Structure search (`se.mol @> qmol`) | GiST-indexed substructure / Tanimoto similarity | ✅ P1-3 done — see migration `d4e5f6a7b8c9` |
| `calculation/search?include=all` | Heavy includes; no `selectinload()` of nested artifacts/geometries observed | P2 — benchmark first; add `selectinload` if N+1 measured |
| `network/PDep` `include=points` | Capped at 200 rows; truncation flag exposed | Safe for v0 |
| `network/PDep` Chebyshev / PLOG coefficient matrices | No size cap | P2 — add a sanity cap |
| Reaction full / path expansion | Bounded by participant count | Safe for v0 |
| Artifact search owner filters | Joins ok; capped pagination | Safe for v0 |
| Review badge bulk loading | Bulk-loaded in batches | Safe for v0 |
| Literature inverse records | Bounded by literature.id filter | Safe for v0 |

Classification scheme:

- **Safe for v0**: pagination bounded; no relationship fan-out.
- **Needs index**: P1-2 (Calculation FKs).
- ~~**Needs cap**: P1-9 (Chebyshev / PLOG coefficient size).~~ ✅ Addressed — both payloads now capped at `settings.public_max_limit` with truncation metadata.
- **Needs async / export endpoint**: not currently required; revisit if/when bulk export is requested.
- **Needs benchmark**: `calculation/search?include=all` at realistic catalog sizes.

---

## 9. Payload safety findings

**Strong** ([app/services/scientific_read/](backend/app/services/scientific_read/)):

- `MAX_LIMIT = 200`, `public_max_offset = 10_000`, dual-enforced at route + service.
- `include=all` excludes high-cost tokens (`network_kinetics.py:89-92` explicitly excludes `points` and `internal_ids`).
- `internal_ids` requires both caller opt-in *and* `allow_public_internal_ids=True` (default `False`) — dual gate.
- `default_visible_statuses()` filters out `rejected` and `deprecated` for anonymous callers; explicit opt-in via `include_rejected` / `include_deprecated` query params.
- No `email`, `password`, `hash`, `secret`, or `token` fields found in any `app/schemas/reads/scientific_*.py` response model.
- User FK columns (`reviewed_by`, `created_by`, `approved_by`) are in the internal-ID stripping deny-list.

**Artifact URIs**: Storage keys (e.g., `s3://bucket/key`) are exposed verbatim. This is **by design** — they are storage paths, not signed URLs, and the artifact-service layer resolves them. Documented in `scientific_artifact_reads.md`. P0-acceptable.

**Gap closed**: P1-9 above — Chebyshev / PLOG payloads are now capped at `settings.public_max_limit` with truncation metadata (`coefficient_count_total` / `coefficients_truncated`, `plog_entry_count_total` / `plog_entries_truncated`).

---

## 10. Observability and operations findings

**Present**:
- `/health` endpoint pings DB ([health.py:14-18](backend/app/api/routes/health.py#L14)).
- Comprehensive exception handlers ([errors.py:229-241](backend/app/api/errors.py#L229-L241)) cover `IntegrityError → 409` with SQLSTATE, `OperationalError → 503`, custom `query_timeout → 503`, `ValueError → 422`, `NotFoundError → 404`, `IdempotencyConflict → 409`.
- DB statement timeout enforced at app level: `db_statement_timeout_ms = 30000` ([deps.py:31-62](backend/app/api/deps.py#L31-L62)).
- Docker Compose rotates container logs (10MB × 5 files).
- Deployment docs comprehensive: `docs/deployment/{local-v0,shared-private-deployment,self_hosted_single_node,native-advanced,client-access-from-hpc,deployment_modes,troubleshooting,api_containerization_notes,admin_auth_quickstart}.md` plus a `README.md` scenario matrix.
- Backup / restore steps documented (manual `pg_dump`, restore order) in `shared-private-deployment.md`.

**Missing**:
- `/readyz` endpoint (P0-4).
- Request-ID / correlation-ID middleware (P1-4).
- JSON logging (P1-5).
- Sentry / OpenTelemetry integration (P2).
- Shipped backup cron script (P2 — currently operators must script it).
- Explicit DB pool sizing — SQLAlchemy defaults to pool_size=5, which is fine for a single worker but easy to forget on scale-up (P2).
- Belt-and-braces role-level `statement_timeout` (recommended in addition to the app-level setting).

---

## 11. Test reliability findings

**Strong** ([backend/tests/conftest.py](backend/tests/conftest.py)):
- 3,923 tests across 205 files.
- Per-test transaction with savepoint mode (`conftest.py:222-233`) and rollback (line 248).
- Rate-limit middleware disabled in tests via autouse fixture (`conftest.py:25-42`).
- Session-scoped shared test user + function-scoped curator/admin avoid bootstrap-test contamination.
- Zero `@pytest.mark.skip`, two `xfail`.

**Acceptable with note**:
- Test factories use a process-global `_INCHI_COUNTER` ([_factories.py:98-106](backend/tests/services/scientific_read/_factories.py#L98-L106)). Never reset between sessions. Tests partition the key space with distinct prefixes (`AKR`, `MR1`, …), but parallelization (`pytest-xdist`) would need a worker-aware counter — flag for CI.

**Missing**:
- ~~No frozen OpenAPI golden snapshot (P1-8).~~ Addressed — see `tests/api/test_openapi_snapshot.py` + `tests/api/golden/openapi.json`.
- No explicit response-level test for `apply_internal_ids_visibility` end-to-end (P3 — covered indirectly via integration).

---

## 12. OpenAPI/client-contract findings

**Strong**:
- Unified search-response envelope across endpoints: `{request, review_summary, records, pagination}` — see `scientific_kinetics_search.py:140-147` and identical shapes in species/artifact/network/reaction search.
- Error envelope is flat: `{detail, code?}`. No DB row IDs in `detail`. Frozen by `test_error_envelope_shape.py`.
- GET/POST parity: both routes delegate to the same service function; POST rejects query-string parameters (`_POST_ALLOWED_QS_KEYS: set[str] = set()`, raises 422 `post_search_fields_must_be_in_body`).
- `parse_include()` + `validate_includes()` reject unknown include tokens with 422 — tested.
- `RequestEcho` returns the resolved filter/sort/collapse/include so clients can re-construct the executed query.
- Public-ref column type is `String(40)` with stable prefix scheme — safe for client parsing.

**Gaps**:
- **P1-8** ✅ addressed: Golden `/openapi.json` snapshot frozen at `tests/api/golden/openapi.json`; drift is caught by `tests/api/test_openapi_snapshot.py`. Update intentionally with `UPDATE_OPENAPI_GOLDEN=1`.
- **P2**: Search request schemas don't set `model_config = ConfigDict(extra="forbid")`. Unknown POST body fields are silently ignored (Pydantic v2 default). Route-level query-string guard catches the common confusion, but defense in depth would harden this.
- **P2**: Sparse field-level `Field(description=...)` / `examples=` on response schemas — Swagger UI experience is functional but not informative.

---

## 13. Recommended next implementation plan

Ordered by impact-per-effort, drawing from the P0/P1 list:

1. **Migration policy & deployed-DB playbook (P0-2, P0-3)** — half-day. Fix `.claude/rules/migration-rules.md` to reflect Phase 4a relaxation; write `docs/deployment/migrations.md` describing empty-DB bootstrap, deployed-DB upgrade, and any required backfill triggers. Currently the most dangerous documentation drift.
2. **Production env hygiene (P0-1, P0-5)** — quick. Add a deployment checklist (env vars: `AUTH_ALLOW_OPEN_REGISTRATION=false`, `EXPOSE_API_DOCS=false`, `LEGACY_READS_REQUIRE_AUTH=true`, `SESSION_COOKIE_SECURE=true`, `ALLOW_PUBLIC_INTERNAL_IDS=false`). Optionally add a startup assertion that refuses to boot in `hosted` profile when these are misconfigured.
3. **Hot-path FK indexes on `calculation` (P1-2)** — half-day. Add `index=True` to `lot_id`, `software_release_id`, `conformer_observation_id`, `species_entry_id`, `transition_state_entry_id`, `literature_id`. Fold into the right migration (depends on outcome of #1).
4. **Readiness + observability minimum (P0-4, P1-4, P1-5)** — one day. `/readyz`, request-ID middleware, JSON logs. These are the smallest single change that makes the deployment actually operable under traffic.
5. ~~**Stored mol column + GiST index for structure search (P1-3)** — one to two days. Add a populated `mol` column on `species_entry`, GiST index, backfill, and migrate `structure_search.py` to use the indexed column.~~ ✅ Done — migration `d4e5f6a7b8c9`; service uses `se.mol` directly.
6. ~~**OpenAPI golden snapshot (P1-8)** — quick. Prevents accidental client-breaking schema drift.~~ ✅ Done — see `tests/api/test_openapi_snapshot.py`.
7. **Rate-limit Redis backend (P1-1)** — only when scaling past one worker. Until then, the in-process store is honest.
8. **Sentry integration (P2)** — defer until first real user traffic; integrate before public launch.

### Evaluation of the audit-prompt's suggested next-task list

| Suggested task | Verdict |
|---|---|
| Materialized/indexed RDKit mol column for structure search | ✅ Done — P1-3, migration `d4e5f6a7b8c9`. |
| Standalone bulk export endpoint | **Defer** — no real consumer demand yet; pagination + client looping suffices. Add only when a downstream pipeline asks. |
| Artifact URI exposure policy | **Already correct** — documented design; URIs are storage keys, not signed URLs. Reaffirm in deploy docs; no code change. |
| Migration strategy cleanup for deployed DB | **Yes** — P0-2/P0-3, top priority. |
| OpenAPI contract freeze | ✅ Done — P1-8, see `tests/api/test_openapi_snapshot.py`. |
| Rate-limit budget review | **Partial** — cost-weighted buckets for structure-search / `include=all` would help; full Redis migration only at scale (P1-1). |
| Backup/restore deployment docs | **Already mostly there** — extend with a shipped `pg_dump` cron template. |
| Health/readiness endpoint hardening | **Yes** — P0-4, immediate. |

---

## 14. Appendix: files inspected

**Auth & authorization**:
- `backend/app/api/routes/auth.py`
- `backend/app/api/routes/admin.py`
- `backend/app/api/routes/record_reviews.py`
- `backend/app/api/router.py`
- `backend/app/api/deps.py`
- `backend/app/api/app.py`
- `backend/app/api/config.py`
- `backend/app/services/auth.py`
- `backend/app/db/models/api_key.py`
- `backend/app/db/models/user_session.py`
- `backend/app/db/models/common.py`
- `backend/scripts/bootstrap_admin.py`
- `backend/tests/api/test_api_legacy_route_auth.py`

**Public scientific reads**:
- `backend/app/api/routes/scientific/*.py`
- `backend/app/services/scientific_read/*.py`
- `backend/app/schemas/reads/scientific_*.py`
- `backend/app/api/routes/_pagination.py`
- `backend/docs/specs/scientific_*.md`
- `backend/tests/api/scientific/*.py`

**Rate limits, observability, error handling**:
- `backend/main.py`
- `backend/app/api/app.py`
- `backend/app/api/rate_limit.py`
- `backend/app/api/errors.py`
- `backend/app/api/routes/health.py`
- `backend/app/api/deps.py`
- `docker-compose.yml`

**DB & migrations**:
- `backend/alembic/versions/60b67e360daf_enable_rdkit_extension.py`
- `backend/alembic/versions/d861dfd60891_create_intial_schema.py`
- `backend/alembic/versions/a1b2c3d4e5f6_add_molecular_property_observation.py`
- `backend/alembic/versions/b2c3d4e5f6a7_mpo_timestamps_server_default.py`
- `backend/app/db/base.py`
- `backend/app/db/types.py`
- `backend/app/db/models/calculation.py`
- `backend/app/db/models/species.py`
- `backend/app/db/models/network.py`
- `backend/app/db/models/network_pdep.py`
- `backend/app/db/models/molecular_property_observation.py`
- `backend/app/db/models/statmech.py`
- `backend/app/db/models/transport.py`
- `backend/app/services/public_refs.py`

**Performance / structure search**:
- `backend/app/services/scientific_read/structure_search.py`
- `backend/app/services/scientific_read/calculations.py`
- `backend/app/services/scientific_read/network_kinetics.py`
- `backend/app/services/scientific_read/calculations_search.py`

**Tests & contract**:
- `backend/tests/conftest.py`
- `backend/tests/api/scientific/test_api_openapi.py`
- `backend/tests/api/scientific/test_error_envelope_shape.py`
- `backend/tests/services/scientific_read/_factories.py`
- `backend/pytest.ini`

**Deployment docs**:
- `docs/deployment/README.md`
- `docs/deployment/deployment_modes.md`
- `docs/deployment/local-v0.md`
- `docs/deployment/shared-private-deployment.md`
- `docs/deployment/self_hosted_single_node.md`
- `docs/deployment/native-advanced.md`
- `docs/deployment/client-access-from-hpc.md`
- `docs/deployment/troubleshooting.md`
- `docs/deployment/api_containerization_notes.md`
- `docs/deployment/admin_auth_quickstart.md`
- `CLAUDE.md`
- `.claude/rules/migration-rules.md`
- `.claude/rules/schema-rules.md`
