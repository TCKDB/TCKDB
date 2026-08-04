# TCKDB release and paper readiness ledger

**Authoritative roadmap:** `docs/reviews/tckdb_product_scientific_paper_readiness_2026-07-30.md`, “Followable implementation roadmap”.
**Original baseline:** `00546f6`; Alembic head `a8b9c0d1e2f3`.
**Current as of 2026-08-04:** `216ee83`; Alembic head `f9b2e6c4a1d7`; deployed Pi at `f8f4f58` / `c4d8f1b2a9e6` (one revision behind).

This ledger preserves the report’s stage names, order, deliverables, and exit
criteria. Stage 0 is an acceptance gate, not work moved to later stages.

| Stage | Deliverable | Baseline status | Acceptance gate / exit criterion |
|---|---|---|---|
| 0 — freeze the contract and baseline | Generated endpoint/client/ingestion/query/export parity matrices; supported/experimental labels; representative staging corpus; deployment inventory and restore-tested backup; manuscript claim-to-test/release evidence matrix | **PASS — versioned baseline** | **Every public claim and product journey has an owner, test, and versioned artifact.** The evidence is assembled below; these artifacts enter version control in this baseline change. Re-run and bind them when cutting a future release candidate. |
| 1 — ingestion reliability and security | Worker lease/heartbeat/retry/reclaim; job authorization/idempotency; async parity; submission pagination; test-DB cleanup | **PASS — versioned implementation** | Kill-after-claim recovery is exactly once; cross-user job reads fail; retried enqueue returns same job; no duplicate science. |
| 2 — scientific integrity blockers | Kinetics/statmech validation; PDep pathway/state identity; bath/energy-transfer normalization; rate interpretation, TS validation, isotope boundary | **PASS — versioned implementation** | Multi-well/multi-pathway records round-trip without ambiguity; incomplete records fail before persistence. |
| 3 — curated product and release semantics | Curated/exploratory profiles; attributed append-only selections; immutable manifest/checksums; version/license/citation metadata | **PASS — versioned implementation** | A user can cite and reproduce the exact selected dataset while retrieving candidates and review history. |
| 4 — query and client validation | Catalog benchmark/plans; bounded analytics or numeric filters; release-watermarked traversal; client parity and safe retries | **PASS — versioned implementation** | Published SLOs hold on representative corpus and every documented journey has a tested Python-client example. |
| 5 — production operations | Expected-head readiness; DB/object backup/restore; metrics/alerts; rate limits; immutable arm64 image smoke test | Open — partial | Restore drill passes, worker crash self-heals, schema drift blocks readiness, Pi is reproducible from tagged release. |
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

## Stage 2 implementation evidence recorded 2026-07-31

Commit `e8981a5`, PR #66, branch `agent/stage-2-scientific-integrity`.
Migrations `c1d2e3f4a5b6` (PDep scientific integrity) then `d2e3f4a5b6c7`
(atom-resolved isotope identity); `alembic heads` reports `d2e3f4a5b6c7` as the
sole head. Stage 2 **failed independent review twice** before passing on the
third round; the failures are recorded here because they shaped the design.

- **PDep pathway identity.** Channels are keyed by a producer-visible
  `channel_key` rather than the endpoint triple, so parallel elementary paths
  between the same two wells no longer collide, and several transition states
  may share one micro reaction. The previous schema rule forced producers to
  mint duplicate `reaction_entry` identity rows to express parallel paths,
  contradicting the identity-tables-dedupe invariant.
- **Per-path solve inputs.** Forward/reverse barriers per path, energies per
  state, and energy transfer scoped to `(state, collider)`, each under an
  enum-backed energy-zero and correction convention. Coverage is exact set
  equality over the declared states, saddle-point paths, and
  well x bath-collider pairs.
- **Barrierless and submerged-barrier channels are first class.** These were
  previously unrepresentable, and the repository's own reference payload
  modelled `C2H5 + O2 -> C2H5OO` — a barrierless association — with an invented
  15.0 kJ/mol barrier to satisfy a `> 0` constraint in both Pydantic and the
  database. The fixture is now a genuine 3-state / 3-channel network whose
  association and dissociation paths carry no transition state and no barrier,
  plus a real saddle-point channel (concerted HO2 elimination) carrying two
  transition states on one micro reaction.
- **Statmech subjects and rate interpretation.** A statmech record describes
  exactly one subject (species entry XOR transition-state entry), enforced in
  the ORM, the database, and at the workflow seam. Each reactant slot, product
  slot, and the TS binds to its exact statmech record; substitution across
  subjects is rejected before persistence, verified in all three directions.
- **Requirements attach to claims, not to record types.** Statmech source
  calculations are warned at deposit and required at the point a computed rate
  is built from them, so experimental, imported, and monatomic deposits stay
  accessible while computed-TST reproducibility is enforced. IRC evidence is
  optional and recommended, surfaced by an `UploadWarning` and an
  always-present typed read descriptor. Normal-mode-displacement evidence was
  removed entirely as an upstream (ARC) concern, not a TCKDB one.
- **Two validator over-reaches were caught and corrected before merge.**
  `modified_arrhenius` had been made to forbid `third_body_efficiencies`,
  which breaks `H + O2 + M <=> HO2 + M` and the CHEMKIN round-trip; the
  allowlist was re-derived and settled empirically against Cantera 3.2.0
  rather than from code. A blanket statmech source-calculation requirement had
  broken the repository's stored real ARC producer payloads and the Python
  client builder contract.
- **Atom-resolved isotope identity.** Per-atom mass numbers on geometry atoms,
  isotopes carried in isotopic SMILES, and a derived canonical isotope key
  replacing free-text `isotopologue_label` in species-entry identity.
  Isotopomers are distinguished, not merged. `NULL` means the most abundant
  natural isotope, so no backfill runs; the migration was verified to leave the
  identity tuple of a 56-row production-shaped database byte-identical.
- **Deployment is gated, not guessed.** The migration refuses to run against
  pre-contract network data (the deployed database holds 2 networks, 42
  channels, 2 unscoped energy-transfer rows) and raises before any DDL, leaving
  `alembic_version` untouched. The operator export/delete/re-upload runbook is
  in `backend/docs/deployment/migrations.md`. Four downgrade guards refuse
  rollback while TS-owned statmech rows, parallel channels, or isotope-labelled
  data exist.
- **Archive completeness.** The six new tables are classified in the lossless
  operator archive registry; without this they would have been silently absent
  from every backup and restore, invalidating the Stage 0 restore evidence.
- **Verification:** 835 non-scientific API tests; 1,391 scientific API tests;
  2,612 service/schema/importer/client-contract/integration/db tests; 731
  workflow/invariant/parser/worker/CLI/example/script tests; `ruff check` clean;
  `alembic check` showing only the two known pre-existing RDKit index artifacts;
  and a fresh disposable-database upgrade/downgrade/re-upgrade cycle. The
  hydrazine/MRCI Arkane round-trip is green at 22 passed and the CHEMKIN
  round-trip passes Cantera validation. One failure remains,
  `tests/db/test_identifier_lengths.py`, on two over-long
  `execution_environment_manifest` foreign-key names that predate this branch
  and were confirmed byte-identical against baseline.

The reviewer's non-blocking findings were closed in the same branch rather than
deferred: `is_third_body` is rejected on PLOG/Chebyshev (it had silently shifted
the required A-unit order, rejecting correctly-unitted entries and accepting
incorrect ones); every conformer's geometry is isotope-checked, not only the
first; a `conformer_selection` on a transition-state role is a 422 instead of an
`IntegrityError` at flush; network Chebyshev kinetics require finite T/P bounds;
`network_channel.channel_key` is `NOT NULL`; structured IRC evidence is
depositable through the standalone transition-state upload and the
computed-reaction bundle rather than only the PDep bundle; and a canonical-TST
rate that omits the TS partition function emits
`missing_kinetics_transition_state_interpretation`.

Two gaps remain genuinely open and are documented in
`backend/app/services/species_resolution.py` and
`backend/docs/specs/pdep_upload_contract_v2.md` rather than left implicit. The
geometry/identity isotope check compares only the multiset of substitutions, so
an isotopomer position mismatch is accepted; a real fix needs SMILES-to-XYZ atom
correspondence, which requires 3D bond perception that fails silently on hard
cases. And the always-present typed evidence descriptor covers IRC but not
tunneling or interpretation assignments, so a default kinetics read cannot
distinguish absent tunneling evidence from an unrequested include.

## Stage 3 implementation evidence recorded 2026-08-01

Commit `d3a99ec`, PR #68, migration `e3f4a5b6c7d8` (five brand-new tables,
nothing pre-existing altered). Failed independent review once, on five blocking
findings, then passed with the reviewer re-running each of its own repros.

- **Selection is an overlay that never touches the science.** No `is_best`,
  `is_selected`, `is_current` or `preferred` column exists anywhere; what stands
  is head-of-chain-and-not-withdrawn, computed from an append-only ledger. A
  full write audit found no write to any scientific table, and triggers reject
  `UPDATE`, `DELETE`, direct `TRUNCATE` and parent `TRUNCATE ... CASCADE` on the
  ledger and on frozen manifests and artifacts.
- **Only approved records may be selected**, re-checked at publication. This is
  what makes the pre-existing accepted-science immutability trigger actually
  cover a released value; before the fix a release could recommend a
  never-reviewed record whose value was then editable after publication.
- **Releases are frozen at publication.** The first implementation rendered
  artifacts from the live database at read time, so a single ordinary upload
  turned every citable download into a 409 and the manifest into
  `verified: false`. On a corpus that is entirely under review, that window was
  effectively zero. Artifact bytes and the manifest document are now stored once
  and served forever; whether the live corpus still agrees is a separate,
  explicitly non-fatal divergence report.
- **The manifest is rebuildable from the manifest row alone.** Attaching the
  DOI -- the release runbook's own final step -- previously broke the digest
  permanently, so the integrity signal read `true` only for releases nobody had
  deposited. Release and policy claims are now snapshotted at freeze.
- **Artifacts are interpretable offline**: selected records, the candidates that
  were *not* selected, the review history and the selection ledger, carrying
  SMILES, InChIKey, charge, multiplicity, level of theory and software. The
  first implementation stripped every foreign key without substitution, so a
  Zenodo deposit would have stated heats of formation for an opaque public ref.
- **Endorsement is only claimed where it can be backed.** Release endpoints
  report release backing; the general curated surface reports `approved_floor_only`
  and nothing more. Release-scoped filtering of the ~40 general searches is
  deliberately not implemented -- a filter behaving differently per endpoint
  would be worse than none -- so `?release=` is refused with a 422 naming the
  endpoint that does answer the question, rather than accepted and ignored.
- **Verification:** 862 non-scientific API tests; 1,433 scientific API tests;
  2,691 service/schema/importer/client-contract/integration/db tests; 746
  workflow and tooling tests; 84 release tests; `ruff check` clean;
  `alembic check` showing only the two known pre-existing RDKit index artifacts;
  a fresh disposable-database upgrade/downgrade/re-upgrade cycle; and CI green
  on the first attempt. The only failure remains the pre-existing
  `execution_environment_manifest` identifier-length case.

Recorded operational boundaries, in `backend/docs/specs/dataset_release_and_profiles.md`
§7a with revisit triggers: frozen release bytes are inlined base64 in the
recovery archive rather than using the `blobs/` sidecar (~4/3 inflation); and
`ALTER TABLE ... DISABLE TRIGGER` remains available to an owning role, a gap
that exists only where the prescribed three-role deployment split has not been
applied.

## Stage 4 implementation evidence recorded 2026-08-04

Commits `8802ada` (#84), `45ff0f2` (#87), `27351d0` (#90), with `e3f300e` (#85)
and `b19b309` (#86) alongside. Migration `a7c2e4f8b6d9` adds two measured
indexes and nothing else.

- **All four deliverables are present and verified in the tree**, not inferred
  from commit titles: the benchmark catalogue (`backend/docs/benchmarks/`,
  regenerated from measurement JSON by `scripts/bench/report.py` and never
  hand-written), four bounded analytics routes, keyset traversal with a
  snapshot watermark that pins to a release when one is named, and the client's
  `retry.py` / `_parity.py` with `clients/python/docs/api_parity_matrix.md`.
  All 91 typed operations resolve; `test_openapi_parity.py` passes in full.
- **The headline SLO finding was a correctness bug, not a latency one.**
  `calculation_search_by_lot` could not succeed at all on the 50,000-species
  corpus: `fetch_review_badges` rendered one bind parameter per candidate id,
  and 116,940 matches needed 119,702 against PostgreSQL's 65,535 wire-protocol
  cap, so the endpoint returned `503`. Moving the review filter and sort into
  SQL took it to p50 281 ms with a maximum of 51 parameters. The threshold was
  established by measurement (65,534 succeed, 65,535 fail), not assumed.
- **The `build_record` N+1 is closed**: 1,060 → 310 statements for a 50-record
  page, p50 346.6 → 215.0 ms, marginal cost per record 21 → 6, with all 17
  other query shapes byte-identical in statement count. An independent
  re-measurement on a separate 50k corpus reproduced 1,059 → 310.
- **Generated artefacts stopped under-reporting the schema.** `generate_dbml.py`
  suppressed a `UniqueConstraint` whenever *any* index covered the same
  columns, so a non-unique lookup index silently erased `uq_record_review_record`
  from `schema.dbml` while the constraint remained enforced in the database.
  Suppression now requires the covering index to be unique.
- **Verification:** scientific gate 1,954–1,962 passed; API gate 2,395–2,404
  passed; client 1,337 passed; `ruff check app tests` clean; `alembic check`
  showing only the two known pre-existing RDKit index artifacts.

**Not covered by this stage, recorded rather than implied:** the benchmark
corpus was rebuilt rather than reused between the before and after runs, so
published latencies for the 17 untouched shapes drifted −14% to +55% with no
code change — statement counts are deterministic and are the number to read.
Two measured defects remain open with reasons stated in
`backend/docs/benchmarks/README.md`: the composed `thermo_search_broad` /
`kinetics_search` shapes (cost proportional to matches, not to the page), and
`structure_search_substructure`, which has no plan-backed diagnosis and so gets
no guessed fix.

## Stage 5 partial progress recorded 2026-08-04

Not a pass. Two of the four exit clauses have evidence; two do not.

- **"Schema drift blocks readiness"** is *not* yet satisfiable as a gate.
  `alembic check` can never return clean: `ix_species_formula_lookup` (an
  expression index) and `ix_species_entry_mol_gist` (an RDKit GiST index over a
  `mol` column autogenerate cannot type) are reported as removals on every run,
  identically on `origin/main`. Until those two are excluded or taught to
  round-trip, drift detection cannot be wired to readiness without a permanent
  false positive.
- **"Restore drill passes"** has fresh evidence beyond the Stage 0 drill: the
  archive path was exercised end to end during the `f9b2e6c4a1d7` review —
  `write_archive` then `restore_archive` into a separately migrated database
  followed by a real commit, 384 rows, one computed solve. Worth noting that
  every existing archive test rolls back, so `SET CONSTRAINTS ALL DEFERRED` at
  `app/services/archive/core.py:706` had never met a deferred trigger before
  that run.
- Worker crash self-heal and reproducible-arm64-image remain untouched.

## Deferred scope recorded 2026-08-04

**CCCBDB import is deferred, not abandoned.** `backend/scripts/cccbdb_*.py` and
`backend/app/importers/cccbdb/` implement extracting molecular property data
from an online third-party source to populate the database. The code is live
and its wrappers resolve, but the direction is parked: whether TCKDB ingests
from external databases at all is a scope question for after the paper, not a
Stage 4–6 deliverable. Recorded here so the modules are not later mistaken for
dead code — a reference-count audit on 2026-08-04 flagged them as unreferenced
precisely because they are hand-run entry points.

## Process finding recorded 2026-08-04

A `git checkout origin/main -- .` run to read one file's contents destroyed
every uncommitted tracked-file edit in the working tree. The Stage 4 read/query
surface was uncommitted at the time: its new modules survived, because that
command does not touch untracked files, but every edit that wired them in was
lost and had to be rewritten from the surviving tests and `_parity.py`.

Two things follow, and neither is "be careful". First, `git show <ref>:<path>`
reads another ref's version of a file without touching the working tree, and is
what should have been used. Second, and more usefully: the work was
recoverable only because the *tests* survived as untracked files and specified
the contract precisely enough to rebuild against. Uncommitted work in a single
working tree has no recovery path — the branch is now pushed to origin, which
is the actual fix.

## Process finding recorded 2026-07-31

Stages 0 and 1 had been committed to a local `main` and never pushed, so neither
had ever run CI despite both being recorded above as review-passed and
"versioned". The first CI run over that code failed immediately: Stage 1 added a
guard permitting destructive test-fixture setup only for databases matching
`^tckdb_test(?:_[A-Za-z0-9_]+)?$`, but `.github/workflows/backend-ci.yml` named
its per-gate databases `tckdb_<gate>_<run>_<attempt>`, which does not match, so
every backend gate aborted during fixture setup. The nightly workflow already
conformed. The fix renames the CI databases into the guarded pattern rather than
widening the guard, since the guard is what prevents a misconfigured run from
dropping a development or production database.

The lesson generalizes: a stage is not verified until it has run in an
environment nobody in the implementation loop controls. Local suites and
independent review both passed over code that could not execute in CI at all.

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
