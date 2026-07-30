# TCKDB release and paper readiness ledger

**Authoritative roadmap:** `docs/reviews/tckdb_product_scientific_paper_readiness_2026-07-30.md`, “Followable implementation roadmap”.
**Baseline:** `00546f6`; Alembic head `a8b9c0d1e2f3`.

This ledger preserves the report’s stage names, order, deliverables, and exit
criteria. Stage 0 is an acceptance gate, not work moved to later stages.

| Stage | Deliverable | Baseline status | Acceptance gate / exit criterion |
|---|---|---|---|
| 0 — freeze the contract and baseline | Generated endpoint/client/ingestion/query/export parity matrices; supported/experimental labels; representative staging corpus; deployment inventory and restore-tested backup; manuscript claim-to-test/release evidence matrix | **PASS — versioned baseline** | **Every public claim and product journey has an owner, test, and versioned artifact.** The evidence is assembled below; these artifacts enter version control in this baseline change. Re-run and bind them when cutting a future release candidate. |
| 1 — ingestion reliability and security | Worker lease/heartbeat/retry/reclaim; job authorization/idempotency; async parity; submission pagination; test-DB cleanup | **PASS — versioned implementation** | Kill-after-claim recovery is exactly once; cross-user job reads fail; retried enqueue returns same job; no duplicate science. |
| 2 — scientific integrity blockers | Kinetics/statmech validation; PDep pathway/state identity; bath/energy-transfer normalization; rate interpretation, TS validation, isotope boundary | Open | Multi-well/multi-pathway records round-trip without ambiguity; incomplete records fail before persistence. |
| 3 — curated product and release semantics | Curated/exploratory profiles; attributed append-only selections; immutable manifest/checksums; version/license/citation metadata | Open | A user can cite and reproduce the exact selected dataset while retrieving candidates and review history. |
| 4 — query and client validation | Catalog benchmark/plans; bounded analytics or numeric filters; release-watermarked traversal; client parity and safe retries | Open | Published SLOs hold on representative corpus and every documented journey has a tested Python-client example. |
| 5 — production operations | Expected-head readiness; DB/object backup/restore; metrics/alerts; rate limits; immutable arm64 image smoke test | Open | Restore drill passes, worker crash self-heals, schema drift blocks readiness, Pi is reproducible from tagged release. |
| 6 — paper release | Corrected claims; primary-source comparison; SI; frozen code/data DOI; executable figures/tables; traceability evidence | Open | Every quantitative/comparative statement points to a cited source, public artifact, or executable test at paper tag. |

## Stage 0 acceptance evidence table

The evidence has passed review for this source baseline; these artifacts enter
version control in this baseline change. Re-run and bind them when cutting a
future release candidate.

| Requirement | Owner role | Evidence command / test | Artifact path | Status | Version/tag gate |
|---|---|---|---|---|---|
| Endpoint/client/ingestion/query/export parity | API/client maintainer | `conda run -n tckdb_env python backend/scripts/generate_stage0_endpoint_parity.py` then `git diff --exit-code -- docs/reviews/tckdb_stage0_endpoint_parity_2026-07-30.md docs/reviews/tckdb_stage0_endpoint_parity_2026-07-30.json` | `docs/reviews/tckdb_stage0_endpoint_parity_2026-07-30.{md,json}` | Review pass: 214 operations generated | Commit source `00546f6` artifacts; tag release candidate |
| Supported/experimental and claim/journey contract | Product/paper owner | `rg -n 'Owner role|Executable test|Artifact path|Version/tag gate' docs/reviews/tckdb_stage0_product_release_contract_2026-07-30.md` | `docs/reviews/tckdb_stage0_product_release_contract_2026-07-30.md` | Review pass | Commit artifacts; tag release candidate |
| Representative staging corpus | Data/release owner | Run the per-case commands in `tckdb_stage0_representative_corpus_2026-07-30.md` | `docs/reviews/tckdb_stage0_representative_corpus_2026-07-30.md` | Review pass: canonical fixture manifest; unsupported claims bounded | Commit manifest; tag release candidate |
| Deployment inventory | Operations owner | Read-only inventory recorded in contract; no production write | `docs/reviews/tckdb_stage0_product_release_contract_2026-07-30.md` | Review pass: source/Alembic/health/count inventory recorded | Commit artifact; bind release candidate to deployed revision |
| Restore-tested backup | Operations owner | Isolated DB restore with `ON_ERROR_STOP`; isolated MinIO boot + `mc` read check | dated backup paths and SHA-256 values below | Review pass: exact DB counts; object-store inventory read | Commit evidence; repeat at release-candidate tag |
| Claim-to-test/release evidence | Paper/release owner | Review expanded contract claim matrix and corpus mapping | contract and corpus manifest | Review pass: every statement has owner, command, artifact, and gate | Commit artifacts; future paper claims resolve at paper tag |

## Safe verification commands

```bash
# Source/CI OpenAPI contract, not a hosted production request
(cd backend && conda run -n tckdb_env pytest tests/api/test_openapi_snapshot.py -q)
(cd backend && conda run -n tckdb_env python scripts/generate_stage0_endpoint_parity.py)
git diff --check

# Static quality and migration-head evidence
(cd backend && conda run -n tckdb_env ruff check app tests)
(cd backend && conda run -n tckdb_env alembic heads)

# Non-mutating DBML comparison: generator writes backend/schema.dbml, so copy first.
cp backend/schema.dbml /tmp/tckdb-stage0-schema.dbml
(cd backend && conda run -n tckdb_env python -m scripts.generate_dbml)
diff -u /tmp/tckdb-stage0-schema.dbml backend/schema.dbml
git diff --exit-code -- backend/schema.dbml
```

Run the DBML check only in a clean worktree or review any generated-file
change deliberately; never redirect generator stdout because it writes its
fixed output path.

## Stage 1 implementation evidence recorded 2026-07-30

- Additive migration `b1c2d3e4f5a6` adds nullable `upload_job.lease_expires_at`
  and `heartbeat_at` plus the claim index; `alembic heads` reports it as the
  sole head. The focused pytest fixture rebuilt a fresh database and applied
  the complete migration chain before each stage test run.
- The worker claims either queued jobs or expired, nonterminal leases; a
  separate heartbeat session renews active work every 30 seconds. Lease expiry
  after the final attempt is terminally failed and its submission receives the
  failure audit event. Persistence and completion remain one transaction.
- `/jobs/*` now uses the standard `Idempotency-Key` contract and limits status
  reads to owner or curator/admin. The explicit product decision is documented
  in `backend/docs/specs/ingestion_submission_model.md`: the nine async kinds
  are experimental authenticated ingestion, not a public/Python-client
  feature-parity surface.
- Pagination (`offset`, `limit <= 200`) now bounds submission mine/review,
  submission audit-event, and submission record-link lists. The existing
  admin curator-task moderation list was audited and already had bounded
  pagination.
- `backend/tests/conftest.py` now drops the isolated per-run test database in
  session teardown after terminating only its own connections.
- Final verification passed: 91 full focused worker, job, submission,
  upload-submission, lease-migration, and test-database-isolation tests after
  the fence-order fix; all 3 source/CI OpenAPI snapshot tests; `ruff check`
  for touched code; `alembic heads`; and `git diff --check`. Claim now obtains
  its execution fence before mutating a job and scans a bounded 32-candidate
  ordered batch; the expired-final-attempt reaper uses the same advisory key
  through a transaction-scoped fence. Two PostgreSQL regressions prove claim
  and reaper leave a fenced job (and its submission) untouched until release.
  This includes a real subprocess claim/terminate/real thermo recovery test
  proving exactly-once completion, concurrent keyed enqueue yielding exactly
  one job/submission, prior-head migration-data recovery behavior, and DB-name
  safety coverage.
  Isolated `tckdb_stage1_schema_check_20260730` upgraded through the full
  migration chain to `b1c2d3e4f5a6 (head)`; the upload-job lease index is
  present in both ORM metadata and the migration. `alembic check` reports only
  the two known, pre-existing RDKit expression/GiST index removals
  (`ix_species_formula_lookup`, `ix_species_entry_mol_gist`), not Stage 1
  drift. Independent Sol review passed; Stage 1 is **PASS — versioned
  implementation**. Production/Pi deployment remains Stage 5 work.

## Exit rule

Stage 0 is **passed and versioned for source `00546f6`**. Re-run and re-bind
the evidence when cutting a future release candidate; no later stage can
substitute for this contract, corpus, restore, or claim baseline.

## Restore evidence recorded 2026-07-30

- PostgreSQL backup: `/home/calvin/tckdb_backups/tckdb_20260730_174102.sql.gz`;
  downloaded verification copy `/tmp/tckdb_stage0_db_20260730_174102.sql.gz`;
  SHA-256 `bbb001335ddd5d76f2807f264cd284ad93d76af099c4e05c81ccaedd159a3bbc`.
- MinIO backup: `/home/calvin/tckdb_backups/tckdb_minio_20260730_174101.tar.gz`;
  SHA-256 `2fa1d921f98a5cb3529863fd3857965ec403c9aa66a1b51b6578b427efabbc31`.
- The DB restore used `ON_ERROR_STOP` into isolated
  `tckdb_stage0_restore_20260730_174102`: Alembic `a8b9c0d1e2f3`, RDKit SQL
  extension `0.76.0`, species 55, calculation 460, calc_hessian 77,
  calculation_artifact 512, record_review 1,069, and upload_job 0 — exact
  live-baseline counts.
- The MinIO tar booted in isolated `tckdb-stage0-minio-restore`; authenticated
  `mc` access read `tckdb-artifacts` (26 MiB / 382 objects). Extraction held
  470 physical storage files / 27,571,238 bytes. The DB has 358 distinct
  artifact SHA-256 values / 25,919,479 distinct payload bytes: 24 objects are
  beyond DB references, recorded as likely orphans for follow-up, not restore
  failure. Production was not migrated/restarted and no science was written.
