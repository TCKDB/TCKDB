# TCKDB platform and interoperability readiness audit

Date: 2026-09-18. Source revision: `8539c927d6ca81758d28a67c0a0fbac31a13c9af`.

TCKDB has a credible foundation for a unified molecular thermochemistry and kinetics service: scientific uploads, evidence-preserving records, curation, stable public references, executable mechanism exports, and frozen releases already exist. Its next level requires connecting these capabilities into independently reproducible, rights-cleared community datasets, then adding source adapters and scientific domains deliberately. The current implementation does not substantiate a claim that all thermochemical databases have already been unified.

This audit reads the [landscape report](deep-research-report.md) as the desired destination and checks current source against the [July readiness review](../reviews/tckdb_product_scientific_paper_readiness_2026-07-30.md). Many July deficiencies have been addressed. Findings below distinguish implemented machinery, recorded historical measurements, and unverified deployment or publication outcomes.

## Method and limits

Vtrace `get_code_context` was called before source exploration and located computed-species/reaction uploads and provenance fragments. GitNexus was bound to `TCKDB`; its index reported 186 commits behind HEAD and graph queries failed because the database storage version was 43 while the installed engine supported 42. The coordinating agent refreshed Vtrace; GitNexus repair was blocked by runner/network availability. Targeted source inspection therefore provides the evidence here; no empty graph result is treated as proof of absence.

No application code was changed, no production DB was accessed, and no uploads, releases, migrations, or deployments were performed. A small offline client check was executed:

```bash
cd clients/python
conda run -n tckdb_env pytest tests/test_openapi_parity.py tests/test_parity_matrix_doc.py tests/test_retry.py -q
```

Result: **104 passed in 0.14 s**. This verifies client coverage bookkeeping, its generated documentation, and retry behavior; it does not establish that the whole backend suite passes or that the public service is healthy.

Negative capability findings are scoped to searches of `backend/app`, `clients/python`, `schemas/python`, `frontend/src`, deployment scripts, and their corresponding tests/docs. External adapters maintained in other repositories could exist; they were not audited here.

## Capabilities worth preserving

| Capability | Verified evidence | Scientific value and boundary |
|---|---|---|
| Durable, attributable ingestion | [Job submission and idempotency](../../backend/app/api/routes/jobs.py#L50), [worker claim/recovery](../../backend/app/workers/upload_worker.py#L49), [job ownership authorization](../../backend/app/api/routes/jobs.py#L193), [crash-recovery integration test](../../backend/tests/workers/test_upload_worker.py#L806) | A failed worker need not strand scientific deposits; retries and contribution records have explicit semantics. These are implemented fixes to July findings, not remaining missing features. |
| Content-first computed uploads | [Computed reaction schema](../../schemas/python/tckdb-schemas/tckdb_schemas/workflows/computed_reaction_upload.py#L1257), [computed species schema](../../schemas/python/tckdb-schemas/tckdb_schemas/workflows/computed_species_upload.py#L637) | Local payload keys, structural content, and validated references support automated producers without forcing users to know backend FK IDs. Deposited completeness still depends on the producer and payload. |
| External-source pilot with retained evidence | [CCCBDB snapshot](../../backend/app/importers/cccbdb/snapshot.py#L314), [property-table ingestion](../../backend/app/importers/cccbdb/property_table_ingest.py#L1), [persistence service](../../backend/app/services/cccbdb_molecular_property_import.py#L1), [external provenance fields](../../backend/app/db/models/molecular_property_observation.py#L151) | Source release, URL, source record, parser version, content hash, references, and raw payload support source audit. Ambiguous identity remains unresolved rather than being guessed. This is real import infrastructure, although not a general source federation. |
| Attributed recommendation separate from candidates | [Curation policy and release selection](../../backend/app/db/models/dataset_release.py#L118), [release artifacts](../../backend/app/services/release/artifacts.py#L1) | The release records who selected which candidate under which policy and retains alternatives and review history. That answers a community need absent from a simple table of preferred numbers. |
| Immutable scientific release artifacts | [Frozen manifest](../../backend/app/services/release/manifest.py#L1), [artifact download](../../backend/app/api/routes/scientific/releases.py#L143), [growth/DOI regression tests](../../backend/tests/services/release/test_release_manifest.py#L264) | Stored bytes and checksums remain stable when the live corpus grows or a DOI is attached. Having this machinery does not prove a release has actually been deposited publicly. |
| Distinct scientific archive | [Archive implementation](../../backend/app/services/archive/core.py#L1), [archive tests](../../backend/tests/services/archive/test_archive.py) | Portable scientific-state archive preserves its declared scientific/provenance/curation rows and byte-exact calculation artifacts while excluding credentials and ephemeral state. It must not be described as a full operational PostgreSQL backup. |
| Mechanism and ML delivery | [Export endpoints](../../backend/app/api/routes/scientific/export.py#L58), [CHEMKIN export](../../backend/app/api/routes/scientific/export.py#L127), [ML species export](../../backend/app/api/routes/scientific/export.py#L187), [Cantera validation helper](../../backend/app/services/scientific_read/chemkin_serialize.py#L723) | CHEMKIN and selected NDJSON/ML projections enable practical reuse. The Cantera validation helper checks conversion and loading; it is not evidence of a native public Cantera-YAML API or of lossless export. |
| Explicit public read contract | [Profile dependency](../../backend/app/api/routes/scientific/_profile.py#L32), [analytics cursor contract](../../backend/app/api/routes/scientific/analytics.py#L63) | Exploratory reads carry no recommendation; curated reads enforce an approval floor; release recommendations are separately attributed. Analytics supports bounded numeric querying and keyset traversal, with limitations stated. |
| Client coverage and safe retries | [Parity matrix](../../clients/python/docs/api_parity_matrix.md#L12), [coverage test](../../clients/python/tests/test_openapi_parity.py#L94), [retry tests](../../clients/python/tests/test_retry.py#L1) | Current checked-in matrix classifies 250 operations: 104 typed, 106 raw-only, 40 not applicable. A guard catches unclassified API changes. Retry tests distinguish safe reads/idempotent writes from unsafe write replay. |
| Scientific browsing interface | [Route registry](../../frontend/src/App.tsx#L29), [species page](../../frontend/src/pages/SpeciesEntryPage.tsx), [reaction page](../../frontend/src/pages/ReactionEntryPage.tsx), [network page](../../frontend/src/pages/NetworkEntryPage.tsx) | A substantial archive browser presents chemistry, methods, geometry, reactions, and networks; TCKDB is more than a backend schema. Route existence and component tests do not establish user-task success or accessibility. |
| Measured query engineering and CI | [Benchmark harness](../../backend/scripts/bench/run_benchmark.py#L1), [measured results](../../backend/docs/benchmarks/README.md#L164), [three backend CI gates](../../.github/workflows/backend-ci.yml#L40) | The project records end-to-end timings, SQL count, plans, environment, empty-result warnings, and test coverage gates. This is materially stronger than unmeasured performance claims. |

## What remains partial or missing

### 1. Deposit-level rights and contributor consent are a community-launch prerequisite

**Status: explicit unfinished contract.** Release-level data/code licenses exist and are tested. `LICENSE-DATA` [explicitly states](../../LICENSE-DATA#L102) that deposit-time license agreement and recording must be implemented before accepting second-contributor deposits. The [Submission model](../../backend/app/db/models/submission.py#L58) records creator, source, moderation, and supersession but has no corresponding structured license/rights agreement. A scoped search for license, redistribution, attribution, source-version, and retrieval fields in release services and ORM models found release licenses and source metadata, not a general deposit/source entitlement contract.

The single-depositor statement in that notice must be reconciled with any corpus inventory reporting several depositor accounts. Account count alone does not establish distinct rights holders, missing permission, or a violation. It establishes a **rights-evidence question that the paper dataset must resolve**.

Required acceptance criteria:

- Versioned contribution terms/rights attestation are recorded with actor, time, source, license identifier or explicit custom terms, and permitted uses at ingestion.
- Every record selected into an open release has a traceable rights basis; imported material retains source attribution and source terms rather than inheriting a house license automatically.
- Release validation rejects selections with unresolved rights or incompatible redistribution terms; tests cover mixed sources and missing evidence.
- Existing corpus deposits receive a documented rights review before inclusion in the paper dataset. A software default is not accepted as proof of agreement.

This is a platform-contract finding, not legal advice about what a particular external database permits.

### 2. A citable release is not yet a self-contained scientific reproduction package

**Status: strong primitives, incomplete packaging contract.** The current release has four frozen NDJSON artifacts: selected records, candidates, review history, selection ledger ([artifact registry](../../backend/app/services/release/artifacts.py#L77)). Its manifest explicitly [omits](../../backend/app/services/release/manifest.py#L167) raw artifact bytes, calculation result payloads beyond cited method/software, and geometries. The DOI instructions deposit [the manifest plus those four files](../../backend/docs/deployment/cutting_a_dataset_release.md#L300).

The separate `tckdb.archive.v1` writer supplies much richer scientific state and byte-exact artifacts. A contribution bundle has a different purpose again: [thermo/kinetics upload-equivalent payloads](../../backend/app/services/contribution_bundle_export.py#L1), with artifact packaging outside that service's scope. These distinctions are sound; the missing piece is an overall deposited package that binds the appropriate pieces by digest and scope.

Required acceptance criteria:

- A paper deposition has a top-level manifest binding the frozen scientific release, a scoped reproducible archive/evidence package, software revision/environment, and all analysis scripts by SHA-256.
- An independent clean installation reconstructs the selected dataset and reruns every published numerical table/figure without privileged access to the live server.
- The report distinguishes replaying parsing/analysis from rerunning licensed quantum-chemistry software; missing or access-restricted raw evidence is quantified.
- A real persistent identifier and successful independent download/checksum verification are captured. `CITATION.cff` currently [intentionally omits a DOI](../../CITATION.cff#L11); this audit did not query production or establish whether any separate DOI exists.

There is also documentation drift: the release procedure around [line 234](../../backend/docs/deployment/cutting_a_dataset_release.md#L234) describes re-deriving live artifacts, while current code verifies frozen bytes and reports live divergence separately. Align documentation before quoting the contract in Methods.

### 3. Source federation is a pilot, not a universal interoperability layer

**Status: partial, with a useful implemented CCCBDB precedent.** The CHEMKIN parser handles NASA7 and multiple kinetic forms ([parser](../../clients/python/adapters/chemkin/tckdb_chemkin/parser.py#L196)); CCCBDB now has an idempotent persistence workflow despite its README retaining early phase language. Neither proves broad import/export coverage.

Searches for `ThermoML`, `OPTIMADE`, `Parquet`, `JSON-LD`, `CALPHAD`, and `Cantera` in runtime/schema/client directories found Cantera/CHEMKIN references and validation, but no native implementations of the other named interchange surfaces. This is a scoped implementation finding, not a claim that conversion is impossible or that external adapters do not exist.

Required acceptance criteria:

- Publish a capability matrix for every adapter: exact source/version, scientific fields represented, units/reference-state handling, uncertainty handling, identity failures, licensing, retained raw evidence, rejected fields, and round-trip fidelity.
- Use a small multi-source molecular pilot to exercise disagreements and duplicate original literature, not just successful imports from one source.
- Add experimental ThermoML import first where the scientific model can represent it; unsupported state/uncertainty structures must be preserved or rejected explicitly, never silently flattened.
- Add analysis-ready Parquet/Arrow with release binding and provenance identifiers; retain canonical evidence separately.
- Provide a tested direct Cantera-YAML delivery path if demanded by users. Treat CALPHAD, fluids/EOS, materials/OPTIMADE, and biochemical/geochemical conventions as additional scientific domains with separate contracts, not file extensions appended to a gas-phase model.

The practical meaning of “one database” should be one place to discover, compare, select, and retrieve interoperable evidence. Some source values will remain remotely accessed or entitlement-controlled; that boundary must be visible in the product.

### 4. Release browsing, download access, and recommendations need one coherent user journey

**Status: scientific browser exists; community release workflow remains fragmented.** The [frontend route inventory](../../frontend/src/App.tsx#L29) contains detailed scientific browsing and machine-review inspection but no dedicated public release catalog/detail/download route. Release APIs exist. Typed Python release methods remain intentionally [raw-only](../../clients/python/docs/api_parity_matrix.md#L255). Convenience bulk exports require curator/admin authorization ([export routes](../../backend/app/api/routes/scientific/export.py#L58)); frozen release downloads use a separate route.

`profile=curated` currently means approved-floor filtering, not “the TCKDB recommendation.” The general `release=` parameter is [explicitly rejected as unimplemented](../../backend/app/api/routes/scientific/_profile.py#L41). Analytics keysets bound insertions but [do not freeze changing curation](../../backend/app/api/routes/scientific/analytics.py#L63). These are honest contracts, but a user should not need to learn several backend distinctions to cite or train on the right values.

Required acceptance criteria:

- A public user completes “find chemistry → see competing values → understand the selected value and rationale → download the frozen dataset → copy its citation” in one documented browser and Python workflow.
- Every download clearly identifies live/exploratory, approval-filtered, or release-selected scope.
- An approved open release is downloadable without curator privileges; expensive live exports have documented limits and access rationale.
- Add typed release manifest/artifact retrieval and checksum verification; either implement release-scoped queries or provide a clear frozen-download equivalent.
- Measure task completion, failure points, and time with researchers outside the development group; test keyboard and screen-reader access for the published workflows.

### 5. Production evidence is stronger than July, but not complete

**Status: partly addressed; several verifiable gaps remain.** Lease recovery, worker heartbeat/fencing, attempts, and job ownership are now present. Health reports worker status and queue lag ([health service](../../backend/app/api/routes/health.py#L63)). A Postgres backup script now restores a scratch database and checks encoding/data equivalence ([backup script](../../backend/scripts/ops/tckdb_backup.sh#L22)). These are advances, not absent capabilities.

However, `/readyz` still returns success when it finds any installed revision, without comparing it to code head ([source](../../backend/app/api/routes/health.py#L577)). The checked-in backup shell script and example systemd service cover PostgreSQL; searches of deployment/ops scripts found no paired object-store backup and complete restore orchestration. Artifact integrity checking exists, but checking bytes is not preserving recoverable copies.

Required acceptance criteria:

- Expose installed and expected schema revisions; fail readiness for an incompatible schema and test the behind-head case.
- Back up both scientific database state and object-store artifacts, bind their inventories/hashes, and demonstrate restoration onto an isolated replacement host.
- Publish measured recovery objectives, actual restore time, artifact completeness, backup freshness, worker recovery, and alert delivery for the deployment supporting the paper.
- Record live deployment revision and dated health/availability evidence. Do not infer operational readiness from CI files or a nominal public URL.

### 6. Performance claims must preserve the existing benchmark's caveats

**Status: genuine benchmark evidence, unresolved broad-search cost.** The repository already contains a catalog-scale corpus and query plans. Its measured table reports some zero-match queries and explicitly excludes those timings as evidence of useful performance. It also records different sample sizes/background load and warns against precise before/after comparisons ([benchmark notes](../../backend/docs/benchmarks/README.md#L172)).

The recorded broad-thermo query uses approximately **1,331 SQL statements and one second for a 50-record page** on the historical workstation corpus. Current [thermo-search source](../../backend/app/services/scientific_read/thermo_search.py#L166) still collects species candidates and calls thermo reads per entry. The exact historical timing is not a new measurement of this HEAD. The benchmark intentionally does not extrapolate workstation numbers to the Raspberry Pi ([harness](../../backend/scripts/bench/run_benchmark.py#L105)).

Required acceptance criteria:

- Define separate SLOs for interactive identity lookup, broad discovery, numeric analytics, release downloads, and uploads.
- Rerun nonempty representative queries on the deployed hardware and tagged paper revision with sufficient repetitions, concurrent users, cold/warm cache labels, result correctness, memory, and SQL counts.
- Direct large quantitative queries to the existing bounded analytics surface; either fix composed discovery costs or document measured limits.
- Publish raw timings and plans. Ensure tests assert relevant scaling behavior rather than only successful status codes.

## Prioritized delivery plan

| Priority | Deliverable | Completion evidence |
|---|---|---|
| P0 before a public multi-contributor launch | Deposit/source rights contract and current-corpus rights review | Machine-readable rights for every released record; tests refuse unresolved release selections; documented review of existing depositors/sources. |
| P0 before submitting a reproducibility claim | One frozen, openly retrievable research package | DOI or equivalent repository deposit, checksums, release plus full required evidence, exact software revision, successful independent reconstruction and figure reproduction. |
| P0 before relying on the hosted service for irreplaceable deposits | Paired DB/object-store recovery and schema compatibility | Recorded isolated restore and artifact audit; incompatible-schema readiness test; observed worker/alert behavior. |
| P1 for a convincing first database paper | End-to-end release user journey and measured utility | External-user task study, typed release retrieval, documented public download policy, quantitative coverage and workflow demonstrations. |
| P1 for a credible unification claim | Two or more complementary open-source adapters with reconciliation | Published mapping/fidelity reports, source versions and terms, raw snapshots, disagreements retained, original-evidence deduplication evaluated. |
| P1 for scale claims | Refreshed workload and deployment benchmark | Tagged code/corpus, nonempty representative results, distributions under load, plans and limits; no unsupported workstation-to-Pi extrapolation. |
| P2 after a molecular core is proven | Experimental interchange and analysis distributions | ThermoML semantic round trips where supported; Parquet/Arrow release exports; explicit unrepresentable-field reports. |
| P2/P3 as separate scientific expansion | Fluids/mixtures, solid phases/CALPHAD, materials, aqueous/biochemical domain adapters | Domain-specific state/model contracts, expert validation and preservation of source entitlements. |

## Defensible article framing

The supported platform contribution is a unified, provenance-rich molecular thermochemistry/kinetics archive with content-first ingestion, attributed curation, explicit candidate disagreement, frozen release artifacts, and mechanism/analysis delivery. The stronger research contribution must be demonstrated on a released corpus: how much evidence can be reconstructed, what ambiguity is prevented, how source disagreement changes selection, and which downstream scientific task becomes more reliable.

Do not count API endpoints, schema tables, or tests as scientific validation. Report them as engineering scope. For the article, publish deposition completeness by product type, import fidelity/error rate, independent reanalysis success, source disagreement and duplicate-evidence cases, user task success, and measured operating limits. Those results would justify a claim that TCKDB reduces fragmentation within its demonstrated domain while providing a foundation for broader federation.
