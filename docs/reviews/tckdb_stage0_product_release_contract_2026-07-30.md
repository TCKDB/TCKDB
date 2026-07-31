# TCKDB Stage 0 product and release contract

**Baseline date:** 2026-07-30
**Source baseline:** `00546f604c4a3dc2e59d90802214990e3d949e3f` (`main`)
**Purpose:** freeze the vocabulary and evidence boundary for the next roadmap
stages. This is a documentation-only baseline, not a schema, API, deployment,
or manuscript change.

## Contract rules

1. “Supported” means an operation is mounted in the current source and
   OpenAPI snapshot, has a stated audience/auth contract, and is appropriate
   to describe as an implemented interface. It does **not** alone establish
   catalog-scale performance, a populated corpus, or a release-quality data
   product.
2. “Experimental / internal” means the interface may exist, but it must not be
   offered as a stable public product or paper-release mechanism until the
   stated gate is closed.
3. “Not a release claim” means no paper or product statement may imply a
   citable, immutable, independently reproducible dataset merely because the
   route, archive, or schema exists.
4. `backend/tests/api/golden/openapi.json` is the generated **source/CI**
   contract. Hosted production intentionally disables `/openapi.json` and
   `/docs`; their 404 responses are an intended safety posture, not drift.

## Source and OpenAPI inventory

The snapshot contains **214 HTTP operations** across **49 scientific paths**;
**65 operations** have the `scientific` tag. Counts were obtained from the
checked-in OpenAPI golden file, not from an assumed live deployment. Router
mounting is in `backend/app/api/router.py`; scientific composition is in
`backend/app/api/routes/scientific/__init__.py`.

The endpoint-level, reproducible source of truth is the generated
[parity matrix](tckdb_stage0_endpoint_parity_2026-07-30.md) and its
[JSON form](tckdb_stage0_endpoint_parity_2026-07-30.json). Regenerate from the
source/CI snapshot (never a hosted production request):

```bash
(cd backend && conda run -n tckdb_env python scripts/generate_stage0_endpoint_parity.py)
git diff --exit-code -- docs/reviews/tckdb_stage0_endpoint_parity_2026-07-30.md \
  docs/reviews/tckdb_stage0_endpoint_parity_2026-07-30.json
```

| Product area | Current evidence | Stage 0 label | Contract boundary |
|---|---|---|---|
| Anonymous scientific reads | `/api/v1/scientific/*` except protected operations; source mounts species, reactions, thermo, kinetics, calculations, TS, conformers, statmech, transport, networks/PDep, literature, corrections, and artifact metadata | **Supported API surface** | Public/ref-oriented query interface. Results are candidates unless a future release selection says otherwise. |
| Chemistry and provenance query | `backend/docs/specs/read_query_api_audit.md` §0 and scientific router; GET/POST search plus detail/composite reads | **Supported API surface** | Deterministic, bounded query behavior; not catalog-scale performance evidence. Important absent/deferred grains remain channel query/paginated network points, generic record provenance, and some standalone reads. |
| RDKit structure search | `GET|POST /scientific/species/structure-search`; audit §0.1 | **Supported API surface** | Species-entry substructure, similarity, and exact modes only; do not infer reaction-substructure support. |
| Legacy entity reads | `/api/v1/{species,reactions,thermo,...}` mounts use `require_auth_for_legacy_reads` | **Compatibility / authenticated surface** | Do not present as the public product contract; hosted behavior is deployment-configured. |
| Synchronous uploads | 11 `/uploads/*` POST routes; `UPLOAD_ENDPOINTS` in the Python client | **Supported authenticated ingestion** | Content-first, atomic, idempotency-aware upload path. Scientific completeness gaps for kinetics/statmech remain a release gate. |
| Contribution bundles v0 | `/bundles/dry-run` and `/bundles/submit`; client `bundle_dry_run`/`bundle_submit` | **Supported authenticated ingestion** | Validated transport for **thermo or kinetics only**; mixed bundles and other record families are out of scope. |
| Async upload jobs | Nine enqueue POST routes plus `GET /jobs/{job_id}`; no typed client lifecycle methods | **Experimental / not public-release supported** | No enqueue idempotency, incomplete type parity, crash recovery, or object-level status authorization. Do not direct external contributors to this path. |
| Python client | `clients/python/src/tckdb_client/client.py`: generic HTTP, upload/bundle, and typed search/detail/iterator helpers | **Supported subset** | Typed coverage exists for species/reactions, thermo/kinetics, calculations, networks, statmech, transport, artifacts, reaction-full, geometry. It is not OpenAPI-complete: no first-class async lifecycle, export/meta, many detail/search surfaces, artifact download, correction, TS/conformer, or path helpers. |
| HTTP NDJSON and CHEMKIN/ML export | `backend/app/api/routes/scientific/export.py` | **Curator/admin convenience export** | Protected scientific-tagged operations, not anonymous/public bulk APIs. NDJSON is explicitly non-lossless and non-reingestible. |
| Artifact byte download | `/scientific/artifacts/{sha256}/download` | **Authenticated scientific operation** | Metadata search is public; approved raw bytes require authentication and are not an anonymous scientific read. |
| Scientific archive v1 | `backend/scripts/tckdb_archive.py`; `backend/docs/specs/tckdb_archive_v1.md` | **Operator/admin recovery archive** | Deterministic, full-state recovery artifact; no public HTTP surface. It is not automatically an immutable published release or DOI artifact. |
| Candidate selection | `backend/docs/specs/scientific_product_candidacy.md` | **Implemented candidate storage; selection deferred** | Append-only candidate records and explicit read-time collapse exist. No persisted curator/product/release selection exists. |

### Supported query scope (precise wording)

The supported read contract can be described as: *TCKDB provides a public,
review-aware, public-reference scientific read API for exploration and
provenance discovery across species, reactions, computed records, and
pressure-dependent record representations.* The following stronger statements
are frozen out of Stage 0:

- “the TCKDB value” or a curator-selected canonical value;
- a comprehensive or catalog-scale query-performance claim;
- a public downloadable frozen dataset;
- a complete pressure-dependent microscopic-pathway corpus;
- reproducibility of a computed rate interpretation rather than retention of
  deposited rate-fit evidence.

## Claim-to-evidence matrix

This matrix is the only allowed source for paper-facing capability statements
until superseded by a dated release evidence package. “Evidence” identifies
repository facts; “gate” identifies what is still required before the claim
can be strengthened.

| Candidate paper/product statement | Status | Owner role | Test/evidence command | Artifact path | Version/tag gate |
|---|---|---|---|---|---|
| TCKDB is designed for gas-phase thermochemistry and kinetics products linked to calculation-level evidence. | Allowed, design claim | Scientific/product owner | OpenAPI snapshot + `scientific-smoke` corpus case | readiness review; corpus manifest | Commit baseline; demonstrate at release candidate |
| TCKDB retains multiple attributed candidate records under a resolved identity. | Allowed, scoped | Product/curation owner | `pytest tests -k thermo_invariants -q` | `scientific_product_candidacy.md` | Commit baseline; no selection claim |
| TCKDB separates evidence, review, and selection. | Partially allowed | Trust/curation owner | `pytest tests -k review -q` | trust/read specs; candidacy spec | Commit baseline; selection waits for Stage 3 |
| TCKDB supports public scientific query and provenance exploration. | Allowed, scoped | API maintainer | OpenAPI snapshot; `scientific-smoke` corpus case | golden OpenAPI; parity matrix | Commit baseline; rerun at release candidate |
| TCKDB accepts authenticated scientific contributions. | Allowed, scoped | Ingestion maintainer | `chemkin-roundtrip` and `bundle-v0` corpus cases | integration fixtures; bundle v0 spec | Commit baseline; Stage 1/2 before external-data release |
| TCKDB supports PDep/network records. | Allowed as representation | Scientific-read maintainer | `pdep-schema` corpus case | PDep schema test | Commit baseline; Stage 2 before pathway claim |
| TCKDB provides reproducible calculations/rates from deposited records. | Not allowed | Scientific-integrity owner | Stage 2 round-trip required | future integrity fixture/report | Only after Stage 2 exit |
| TCKDB is a public immutable citable scientific release/database. | Not allowed | Release owner | Stage 3 build/checksum verification required | future public artifact + DOI | Tagged public release |
| TCKDB uniquely combines capabilities absent from comparable systems. | Not allowed | Paper/comparison owner | Stage 6 multi-assessor audit required | future SI evidence appendix | Paper tag |
| TCKDB is ready for public/community deployment. | Not allowed | Release/operations owner | Stages 1–5 operations evidence required | future deployment/restore/benchmark artifacts | Tagged deployment release |

### Executable claim and journey evidence

This is the Stage 0 acceptance matrix. It extends the claim statements above
with ownership, an executable command, durable artifact, current status, and
the version/tag gate. Corpus case IDs refer to the canonical
[`representative staging-corpus manifest`](tckdb_stage0_representative_corpus_2026-07-30.md).

| Claim or journey | Status | Owner role | Corpus case | Executable test/evidence command | Artifact path | Version/tag gate |
|---|---|---|---|---|---|---|
| Explore public scientific records by reference | Allowed, scoped | API maintainer | `api-contract`, `scientific-smoke` | `(cd backend && conda run -n tckdb_env pytest tests/api/test_openapi_snapshot.py -q)` | `backend/tests/api/golden/openapi.json`; parity matrix | Source `00546f6`; regenerate at release candidate |
| Use typed client methods or a raw helper | Allowed, scoped | Python-client maintainer | `api-contract` | `(cd backend && conda run -n tckdb_env python scripts/generate_stage0_endpoint_parity.py)` | `docs/reviews/tckdb_stage0_endpoint_parity_2026-07-30.{md,json}` | Source `00546f6`; API/client version at release candidate |
| Submit synchronous scientific upload | Allowed, scoped | Ingestion maintainer | `chemkin-roundtrip`, `rmg-ammonia-methane` | `(cd backend && conda run -n tckdb_env pytest tests/integration/test_chemkin_round_trip.py -q)` | named fixture and integration test | Source commit; external-data release blocked by Stages 1–2 |
| Submit contribution bundle v0 | Allowed, narrow | Bundle maintainer | `bundle-v0` | `(cd backend && conda run -n tckdb_env pytest tests -k contribution_bundle -q)` | `docs/contribution-bundles/v0-format.md` | Thermo/kinetics v0 only |
| Query PDep/network representation | Allowed as representation | Scientific-read maintainer | `pdep-schema` | `(cd backend && conda run -n tckdb_env pytest tests/schemas/test_network_pdep_schema.py -q)` | `backend/tests/schemas/test_network_pdep_schema.py` | No microscopic-pathway claim before Stage 2 |
| Download approved artifact bytes | Allowed, protected | Artifact/security maintainer | `artifact-integrity` | `(cd backend && conda run -n tckdb_env pytest tests/db/test_calculation_artifact_integrity.py -q)` | artifact integrity test and ADR 0004 | Auth gate verified at release candidate |
| Restore scientific state | Allowed, operator-only | Operations owner | `archive-restore` | `(cd backend && conda run -n tckdb_env pytest tests/cli/test_tckdb_archive_script.py -q)` | archive spec, dated restore evidence in ledger | Repeat at release candidate |
| Treat async jobs as public lifecycle | Not allowed | Job-system owner | none; unsupported | Stage 1 crash/authorization/idempotency suite required | Stage 1 report | Only after Stage 1 exit |
| Treat a candidate as “the TCKDB value” | Not allowed | Product/curation owner | none; unsupported | Stage 3 selection/release test required | attributed release manifest | Only after Stage 3 exit |
| Cite immutable public scientific dataset | Not allowed | Release owner | none; unsupported | Stage 3 release build/checksum/manifest verification required | public artifact + DOI | Tagged public release |
| Claim reconstructed/reproducible computed rates | Not allowed | Scientific-integrity owner | none; unsupported | Stage 2 interpretation/pathway round-trip required | integrity fixture/report | Only after Stage 2 exit |
| Claim unique comparative superiority | Not allowed | Paper/comparison owner | none; unsupported | Stage 6 cited, dated, multi-assessor audit required | SI evidence appendix | Paper tag |

## Static and deployment baseline facts

### Repository facts checked on this baseline

| Fact | Value / evidence |
|---|---|
| Git baseline | `00546f6`, `main`, latest subject `Record execution environments; stop grading them (#64)` |
| Alembic source head | single head `a8b9c0d1e2f3` (`conda run -n tckdb_env alembic heads`) |
| Migration files | 42 `backend/alembic/versions/*.py` files |
| Tests | 291 backend `test_*.py` files; 5,827 test definitions across backend and Python-client tests (static count) |
| OpenAPI | checked-in golden snapshot: 214 operations, 49 scientific paths, 65 `scientific`-tagged operations |
| Client versioned surface | `clients/python` package includes synchronous generic HTTP, sync uploads, bundles, typed searches/details, and iterators; scope recorded above |

### Read-only production inventory supplied on 2026-07-30

This is operational evidence, separate from source evidence. No production
write was made for Stage 0.

| Fact | Observed state |
|---|---|
| Host/runtime | `raspberrypi`, `aarch64`; API service active since 13:57 IDT; PostgreSQL and MinIO containers healthy |
| Deployed revision | repository at `00546f6` on `main == origin/main`; Alembic `a8b9c0d1e2f3`; RDKit 4.7.0 |
| Public safety probes | HTTPS `/health` 200, `/readyz` 200, `/docs` 404, `/openapi.json` 404; anonymous scientific species search 200; anonymous legacy species list 401 |
| Data snapshot | 55 species, 460 calculations, 77 calculation-Hessian rows, 1,069 review rows (all `under_review`), and zero upload-job rows |
| Capacity and backup | 469 GB disk / 425 GB free; executable backup script, daily 03:30 timer, latest DB dumps 1.8 MB on 2026-07-30 |
| Restore drill | Fresh DB backup `tckdb_20260730_174102.sql.gz`, SHA-256 `bbb001335ddd5d76f2807f264cd284ad93d76af099c4e05c81ccaedd159a3bbc`; isolated restore matched revision and all baseline counts. Fresh MinIO tar SHA-256 `2fa1d921f98a5cb3529863fd3857965ec403c9aa66a1b51b6578b427efabbc31`; isolated bucket read succeeded (26 MiB / 382 objects). See ledger for exact paths and orphan follow-up. |

These facts do **not** prove dataset quality, public-release readiness,
performance SLOs, DOI publication, or independent scientific reruns. The
restore drill is passed, with 24 object-store objects beyond DB references
recorded as a follow-up rather than a restore failure.

## Source anchors and repeatable inventory commands

Run from repository root unless noted.

```bash
# Contract route composition and current generated contract
sed -n '1,260p' backend/app/api/router.py
sed -n '1,180p' backend/app/api/routes/scientific/__init__.py
jq '[.paths | to_entries[] | .value | to_entries[] |
  select((.key == "get") or (.key == "post") or (.key == "put") or
         (.key == "patch") or (.key == "delete"))] | length' \
  backend/tests/api/golden/openapi.json

# Ingestion, export, and client evidence
rg -n '^@router\.post' backend/app/api/routes/uploads.py backend/app/api/routes/bundles.py
rg -n '^@router\.(get|post)' backend/app/api/routes/jobs.py backend/app/api/routes/scientific/export.py
rg -n 'def (search_|get_|iter_|upload|bundle_)' clients/python/src/tckdb_client/client.py

# Static contract verification
(cd backend && conda run -n tckdb_env pytest tests/api/test_openapi_snapshot.py -q)
(cd backend && conda run -n tckdb_env alembic heads)
```

## Stage 0 ownership and change control

- Stage 0 changes only durable documentation and baselines. It must not change
  production behavior, database schema, migrations, or manuscript prose.
- The `paper/` tree and the two dated review reports were pre-existing
  untracked user material when this baseline was made; preserve them.
- Any future change to a capability label must update this contract, the stage
  ledger, source/OpenAPI evidence, and its verification result in the same
  reviewable change.
