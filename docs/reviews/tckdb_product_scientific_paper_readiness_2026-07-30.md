# TCKDB product, scientific, paper, and deployment readiness review

**Date:** 2026-07-30

**Repository:** `TCKDB_v2` at `00546f6` (`main`)

**Scope:** product design, API, ingestion, querying, Python client, SQLAlchemy
schema, migrations, computational/quantum chemistry fitness, manuscript
claims, and self-hosted Raspberry Pi deployment.

## Executive verdict

TCKDB is already a substantial and unusually thoughtful scientific data
platform. The identity/provenance/result/curation separation is real in the
implementation; the scientific read API is broad; uploads are generally
content-first and audited; the calculation model preserves much more evidence
than a conventional thermo/kinetics value store; and the repository has strong
tests, migration discipline, deployment guards, and documentation.

It is suitable for an invite-only pilot after the ingestion reliability and
job-authorization findings below are fixed. It is **not yet ready to be
presented as a fully reproducible public/community kinetics database** or as a
frozen paper dataset. The remaining blockers are concentrated rather than
system-wide:

1. async jobs can be stranded permanently after a worker crash;
2. async job status is not owner-authorized;
3. pressure-dependent channels lose microscopic reaction and transition-state
   identity, and master-equation inputs are not assigned to specific states or
   pathways;
4. important kinetics and statmech upload paths can accept scientifically
   incomplete records;
5. there is no immutable, public, citable scientific release with checksums and
   a DOI;
6. several manuscript claims are stronger than the implementation or available
   evidence.

The right strategy is not a broad rewrite. Preserve the architecture, close the
small number of correctness/reliability gaps, validate queries against a
representative corpus, publish a versioned release, and revise the paper around
evidence that can be independently reproduced.

## Review method and evidence

Three independent review tracks were run:

- product/API/ingestion/querying and client coverage;
- computational and quantum-chemistry schema fitness;
- manuscript claims checked against the implementation and primary-source
  competitor material.

Repository navigation used Vtrace and GitNexus context before targeted source
inspection. No application code, schema, migration, or manuscript text was
changed by this review.

Current repository facts:

- 110 ORM tables and 42 Alembic revisions;
- 166 API route decorators, including 65 scientific-route operations;
- 11 synchronous upload routes;
- 5,067 test functions across 291 test files;
- 111,834 lines of backend application Python and 122,001 lines of test Python;
- a single Alembic head: `a8b9c0d1e2f3`.

Validation performed:

| Check | Result |
|---|---|
| Ruff over `app` and `tests` | pass |
| Scoped mypy | pass, 140 source files |
| OpenAPI golden snapshot | pass, 3 tests |
| ORM-derived DBML versus committed `backend/schema.dbml` | exact match |
| Alembic graph | one linear head |
| Local Compose Postgres and MinIO | healthy |
| Scientific API/service tier | not executed successfully: local host credentials could not authenticate to the long-running Compose DB |

The local development DB is at `b8e3f1a9c2d4`, four revisions behind current
head. It must not be used as evidence for the current schema until backed up and
upgraded. This is the local workstation DB, not evidence about the Raspberry Pi
deployment.

The Pi itself was not inspected: no hostname, public base URL, or SSH target was
provided. Its current migration revision, backup state, TLS configuration,
worker health, and real query performance therefore remain unverified.

## Priority findings

### P0 — fix before public deployment or accepting important external data

#### 1. Async upload jobs have no crash recovery

The worker commits a job as `processing` before performing the work, while the
claim query only selects `queued` rows. Tests explicitly require already
processing rows to be skipped. There is no lease, heartbeat, attempt counter, or
stale-processing reaper.

Evidence:

- [`backend/app/workers/upload_worker.py`](../../backend/app/workers/upload_worker.py#L45)
- [`backend/app/workers/upload_worker.py`](../../backend/app/workers/upload_worker.py#L265)
- [`backend/tests/workers/test_upload_worker.py`](../../backend/tests/workers/test_upload_worker.py#L101)

Any worker or host failure after the claim commit can strand an upload forever.
Add a lease (`lease_expires_at`), heartbeat, attempts, and an atomic reclaim
path, or adopt an external durable queue. Test termination immediately after
claim and successful recovery by a fresh worker.

#### 2. Job-status reads lack object-level authorization

`GET /jobs/{job_id}` requires authentication but does not check that the caller
owns the job or has curator/admin privileges. The response exposes result,
error, and timing data. An unguessable UUID reduces discovery probability but
is not authorization.

Evidence:

- [`backend/app/api/routes/jobs.py`](../../backend/app/api/routes/jobs.py#L166)
- [`backend/app/schemas/jobs.py`](../../backend/app/schemas/jobs.py#L21)
- the policy to mirror is in
  [`backend/app/api/routes/submissions.py`](../../backend/app/api/routes/submissions.py#L62)

Add owner-or-curator/admin authorization and explicit cross-user tests.

#### 3. Pressure-dependent channels lose microscopic pathway identity

`NetworkChannel` is identified by network, source state, sink state, and kind,
with a uniqueness rule on source/sink. It has no reaction-entry or
transition-state link. The network-to-reaction many-to-many association does
not identify which reaction belongs to which channel. Upload validation rejects
parallel source/sink channels.

Evidence:

- [`backend/app/db/models/network_pdep.py`](../../backend/app/db/models/network_pdep.py#L116)
- [`backend/app/db/models/network.py`](../../backend/app/db/models/network.py#L79)
- [`backend/app/schemas/workflows/network_pdep_upload.py`](../../backend/app/schemas/workflows/network_pdep_upload.py#L325)
- [`backend/app/schemas/workflows/network_pdep_upload.py`](../../backend/app/schemas/workflows/network_pdep_upload.py#L916)

Two elementary pathways or conformational TSs between the same wells cannot be
represented independently. Add a stable channel/pathway identity and an
explicit channel-to-microreaction/TS association. Remove source/sink uniqueness
as the pathway identity.

#### 4. Master-equation source inputs are not assigned to states/pathways

`NetworkSolveSourceCalculation` records only solve, calculation, and a coarse
role. A `well_energy` or `barrier_energy` role does not identify which well or
barrier it parameterizes. Network states contain composition, and channels have
no barrier/TS assignment.

Evidence:

- [`backend/app/db/models/network_pdep.py`](../../backend/app/db/models/network_pdep.py#L46)
- [`backend/app/db/models/network_pdep.py`](../../backend/app/db/models/network_pdep.py#L290)
- [`backend/app/db/models/network_pdep.py`](../../backend/app/db/models/network_pdep.py#L311)

For a network with multiple wells or barriers, the solve cannot be reconstructed
unambiguously. Model state-specific energy/statmech/transport inputs and
pathway-specific TS/barrier/tunneling inputs, including the energy-zero and
correction convention.

#### 5. Kinetics and statmech ingestion need strict content invariants

Thermo uploads correctly require scientific content and enforce representation
consistency. Equivalent general validation is missing for kinetics and
statmech. Examples include an Arrhenius model without complete coefficients, a
PLOG model without PLOG rows, a Chebyshev model without its child payload, or a
statmech record with no scientific content.

Evidence:

- [`backend/app/schemas/workflows/kinetics_upload.py`](../../backend/app/schemas/workflows/kinetics_upload.py#L227)
- [`backend/app/schemas/workflows/kinetics_upload.py`](../../backend/app/schemas/workflows/kinetics_upload.py#L317)
- [`backend/app/schemas/workflows/statmech_upload.py`](../../backend/app/schemas/workflows/statmech_upload.py#L149)
- the stronger pattern is in
  [`backend/app/schemas/workflows/thermo_upload.py`](../../backend/app/schemas/workflows/thermo_upload.py#L299)

Add shared model-kind/payload-shape validation, `has_scientific_content`
validation, and persistence-level/DB invariants where feasible.

### P1 — complete before a paper dataset freeze

#### 6. Async ingestion is neither idempotent nor feature-complete

Synchronous uploads and bundle submission have idempotency support, but async
enqueue does not. A client retry can create duplicate jobs. The queue also
omits computed-species and statmech job types, and the Python client has no
first-class enqueue/poll/wait/cancel surface.

Evidence:

- [`backend/app/api/routes/jobs.py`](../../backend/app/api/routes/jobs.py#L47)
- [`backend/app/api/routes/jobs.py`](../../backend/app/api/routes/jobs.py#L81)
- [`backend/app/api/routes/uploads.py`](../../backend/app/api/routes/uploads.py#L178)
- [`clients/python/src/tckdb_client/client.py`](../../clients/python/src/tckdb_client/client.py#L107)

Decide explicitly whether async is a supported public product surface. If yes,
add enqueue idempotency, close type parity, and specify cancel/retry/terminal
status semantics. If no, label it experimental/internal.

#### 7. The product needs an audited scientific selection/release layer

The absence of `is_best` flags is a strength: raw candidate records remain
honest and append-only. However, the current `collapse=first` style selection is
a deterministic read heuristic, not an expert-curated database recommendation.
There is no persisted, versioned benchmark/release selection for a community
user asking for “the TCKDB value.”

Evidence:

- [`backend/docs/specs/scientific_product_candidacy.md`](../../backend/docs/specs/scientific_product_candidacy.md#L56)
- [`backend/docs/specs/scientific_product_candidacy.md`](../../backend/docs/specs/scientific_product_candidacy.md#L86)

Add a separate, append-only, attributed selection or benchmark-release layer.
It should name the policy/reviewer, version, candidate, rationale, and release,
without mutating the underlying scientific record.

#### 8. There is no public, immutable, citable dataset release

The HTTP NDJSON and CHEMKIN/ML exports are curator-only projections. The
scientific NDJSON endpoint explicitly does not claim lossless re-ingestion. A
lossless archive exists as an operator/admin recovery format, which is a
different contract.

Evidence:

- [`backend/app/api/routes/scientific/export.py`](../../backend/app/api/routes/scientific/export.py#L58)
- [`backend/app/services/scientific_read/export.py`](../../backend/app/services/scientific_read/export.py#L88)
- [`backend/docs/specs/tckdb_archive_v1.md`](../../backend/docs/specs/tckdb_archive_v1.md#L1)

Publish immutable release artifacts containing:

- selected scientific records and full candidate/provenance data;
- manifest and SHA-256 checksums;
- schema/Alembic revision and software versions;
- selection/review policy version;
- data and code licenses;
- changelog and citation metadata;
- a Zenodo or equivalent DOI.

Keep this distinct from disaster-recovery backup and convenience projections.

#### 9. Isotopologue identity is not atom-resolved

`SpeciesEntry.isotopologue_label` is free text, while geometry atoms contain an
element and coordinates but no isotope mass number. Isotope-specific
frequencies, rotational constants, Hessian reuse, ZPE, and kinetic isotope
effects cannot be reconstructed unambiguously.

Evidence:

- [`backend/app/db/models/species.py`](../../backend/app/db/models/species.py#L139)
- [`backend/app/db/models/geometry.py`](../../backend/app/db/models/geometry.py#L42)

Either add per-atom isotope identity and make hashing/canonicalization
isotope-aware, or remove atom-resolved isotopologue support from the paper's
claims.

#### 10. Computed rate provenance stops short of the interpretation

Kinetics source links identify calculations and coarse energy roles, and the
kinetics row stores a tunneling enum. They do not bind the exact reactant,
product, and TS statmech records; conformer ensemble/selection; standard state;
or the parameters used by Eckart/SCT/other tunneling treatments.

Evidence:

- [`backend/app/db/models/kinetics.py`](../../backend/app/db/models/kinetics.py#L134)
- [`backend/app/db/models/kinetics.py`](../../backend/app/db/models/kinetics.py#L226)

Add kinetics-to-statmech/conformer-selection associations and typed tunneling
application/result records. Until then, describe these records as deposited
rate fits with rich source evidence, not guaranteed reproducible
first-principles derivations.

#### 11. Conformer ensembles are not first-class scientific inputs

Statmech is attached at species-entry grain and can cite calculations, but
there is no explicit ensemble membership, conformer degeneracy, weight,
relative-energy reference, Boltzmann convention, or multi-structural method.

Evidence:

- [`backend/app/db/models/statmech.py`](../../backend/app/db/models/statmech.py#L57)
- [`backend/app/db/models/statmech.py`](../../backend/app/db/models/statmech.py#L134)

Add ensemble membership and selection/weighting provenance, or require and
label single-conformer statmech explicitly.

#### 12. TS/NMD/IRC validation evidence is incomplete

Frequency-mode scalars are stored, but normal-mode displacement vectors are
not. IRC points contain path, energy, and geometry but not a structured endpoint
assignment/pass-fail result against reaction participants. TS geometry
validation is deferred, while a TS entry can carry a validated status.

Evidence:

- [`backend/app/db/models/calculation.py`](../../backend/app/db/models/calculation.py#L504)
- [`backend/app/db/models/calculation.py`](../../backend/app/db/models/calculation.py#L887)
- [`backend/app/db/models/calculation.py`](../../backend/app/db/models/calculation.py#L1237)
- [`backend/app/db/models/transition_state.py`](../../backend/app/db/models/transition_state.py#L80)

Add structured NMD and IRC endpoint validation records linked to participants.
Store displacement vectors or require an integrity-checked artifact/Hessian
that supports their reconstruction.

#### 13. Uncertainty is stored but not statistically defined or propagated

Several result tables have optional scalar uncertainties, but there is no
common distribution, confidence/coverage, method, covariance, or correlation
model. Table thermo points, fitted-coefficient covariance, and network
kinetics uncertainty are incomplete.

Treat present values as reported magnitudes of unspecified semantics. Do not
claim uncertainty-aware inference. A future common uncertainty/ensemble model
should attach provenance and support covariance where derived values share
inputs.

## API and query assessment

### What is strong

- Scientific responses have consistent request echoes, deterministic ordering,
  review/trust summaries, coded errors, and bounded pagination.
- Stable public refs are the default external identity; hosted deployments hide
  internal integer IDs.
- Chemistry-first species, reaction, thermo, kinetics, calculation, conformer,
  TS, statmech, transport, network, correction, literature, artifact, and
  composite provenance reads exist.
- Structure search uses a stored RDKit molecule plus a GiST index and performs
  bounded matching in SQL.
- Calculation search has strong provenance and presence filters across
  calculation type, owner chemistry, level of theory, software/workflow,
  geometry/artifact/result presence, validation, dependencies, and canonical
  parameters.
- Search caps, statement timeouts, rate limiting, hosted startup guards,
  request IDs, and structured logging are implemented.
- Content-addressed artifact download is approval-gated and revalidates digest
  and length.
- Sync uploads are atomic, submission/audit wrapped, and have mature
  idempotency behavior.

### Query semantics that need a product decision

Most scientific reads default to a visibility set broader than approved-only,
while exports default to approved. This is transparent for exploration but easy
for a downstream user to mistake for a curated database answer.

Provide two explicit profiles:

- **curated/release:** approved records and an attributed release selection;
- **exploratory/archive:** all visible candidates with explicit review/trust
  state and no implication of recommendation.

The chosen profile must be echoed in every response and dataset manifest.

### Query performance is not yet demonstrated at catalog scale

The index direction is sensible: RDKit GiST, formula expression index, and
several hot FK indexes exist. However, broad species and reaction searches can
materialize candidate rows, badges, availability, and Python-side sorting
before slicing. Offset pagination is deterministic but not snapshot-stable:
concurrent insertions or deprecations can duplicate or skip records.

Before adding speculative indexes:

1. construct a representative corpus with realistic species, calculations,
   conformers, reactions, artifacts, and review cardinalities;
2. define latency and memory SLOs for each supported query shape;
3. run `EXPLAIN (ANALYZE, BUFFERS)` for every filter/sort/include profile;
4. record plans and timings as release evidence;
5. add evidence-backed composite/partial indexes or materialized projections;
6. add a dataset/release watermark and keyset pagination for reproducible
   traversal.

### Important missing quantitative filters

The public API is strong for provenance discovery but weaker for constructing
quantitative scientific datasets.

- Kinetics needs filters for origin, stored direction, degeneracy/tunneling,
  pressure context, coefficient/rate ranges, uncertainty, literature, workflow,
  and TS/statmech provenance.
- Thermo needs origin, phase/reference pressure, numeric H/S/Cp/G ranges,
  uncertainty, literature/workflow, correction scheme, and statmech provenance.
- Statmech needs symmetry, linearity, point group, scale/treatment, rotor, and
  electronic-level filters.
- Calculation needs energy/frequency ranges, imaginary-mode count, convergence,
  and T1/D1/spin-diagnostic filters.
- Network search cannot answer which microreaction/TS produced a channel until
  the schema represents that relation.

Prefer a small number of documented, indexed analytics endpoints or immutable
release tables over adding dozens of expensive optional filters to every
transactional search.

### Python client parity

The client is useful but does not cover the full scientific API. Missing or
partial convenience coverage includes structure search, conformer and TS
search/detail, corrections and frequency scale factors, literature, calculation
path/scan/IRC reads, artifact download, exports/meta, and async job lifecycle.

Create a generated OpenAPI-to-client parity test and an explicit matrix:

| Operation | raw HTTP supported | typed client | iterator | example | contract test |
|---|---:|---:|---:|---:|---:|

Add opt-in retry/backoff only for GETs and idempotency-keyed POSTs. Preserve
`Retry-After`.

## Additional production findings

### Readiness does not verify that the DB is at code head

`/readyz` reports whatever revision is installed but does not compare it with
the code's expected head. A behind-schema deployment can still return 200.

Evidence:

- [`backend/app/api/routes/health.py`](../../backend/app/api/routes/health.py#L44)

Expose both installed and expected revision, and return 503 when they differ.
Also consider making `/health` process-only liveness and leaving DB checks to
`/readyz`, so an unavailable DB does not trigger pointless process restart
loops.

### Test databases are never dropped at session teardown

The session fixture creates a PID-specific database and disposes the engine,
but does not drop the DB after the test run. The local Postgres instance
currently contains hundreds of `tckdb_test_*` databases.

Evidence:

- [`backend/tests/conftest.py`](../../backend/tests/conftest.py#L91)
- [`backend/tests/conftest.py`](../../backend/tests/conftest.py#L138)

Drop ephemeral PID/worker databases in `finally`, while preserving explicitly
named DBs only behind a deliberate debugging flag.

### Backup automation covers Postgres but not MinIO

The example systemd backup service runs `pg_dump`; documentation correctly says
that DB-only backup is insufficient because artifacts live in object storage.
There is no paired MinIO backup service/timer in the example units.

Evidence:

- [`examples/deployment/systemd/tckdb-backup.service`](../../examples/deployment/systemd/tckdb-backup.service#L18)
- [`docs/deployment/shared-private-deployment.md`](../deployment/shared-private-deployment.md#L350)

Ship a paired object-store backup example, an inventory/checksum manifest, and a
restore-drill script that validates DB-to-artifact referential completeness.

### Multi-worker and observability limits remain

Rate-limit state is in-process, so multiple API workers multiply budgets.
Production needs shared enforcement at Redis/proxy/WAF level before horizontal
scaling. Add metrics for request latency/error rate, DB pool saturation,
statement timeouts, worker heartbeat, queue age, stranded/retried jobs, artifact
failures, and backup age. Add external error reporting and alerting.

### Release and packaging metadata need cleanup

The repository has an MIT license, but `backend/pyproject.toml` still says
`TBD — see repository root`; the backend is classified Alpha while the client
is Beta. There is no `CITATION.cff` or visible release changelog.

Evidence:

- [`LICENSE`](../../LICENSE)
- [`backend/pyproject.toml`](../../backend/pyproject.toml#L33)
- [`clients/python/pyproject.toml`](../../clients/python/pyproject.toml#L5)

Align license metadata, maturity/version policy, changelog, security/contact
policy, citation metadata, package publishing, and DOI release.

## Manuscript claims audit

The companion
[`tckdb_external_landscape_research_2026-07-30.md`](tckdb_external_landscape_research_2026-07-30.md)
checks the competitor/novelty framing against official documentation and
primary sources. Its central conclusion is that TCKDB has a plausible
product-level distinction, but the current “only TCKDB combines all eight”
comparison is not supported: AiiDA, NOMAD, QCArchive, ioChem-BD, NIST, ATcT,
RMG, and PrIMe each provide more provenance, contribution, immutability,
disagreement, or re-use capability than the draft table currently credits.

### Claims that must be corrected

| Claim | Problem | Required wording/action |
|---|---|---|
| “every table plays exactly one of four roles” | The model inventory also has Platform tables (`app_user`, sessions, API keys, jobs, idempotency). Even the model README says “four” and then lists five buckets. | Limit the four-role taxonomy to scientific-domain tables and describe Platform as an orthogonal operational layer; fix the README inconsistency too. |
| curation/review is an append-only or immutable overlay | The authoritative current review state is mutable; the separate review-event history is append-only. Scientific-record immutability starts after first approval and applies to an explicit supported-record registry, not every row from insertion. | Describe current state, event history, and post-approval scientific immutability separately. |
| “with a hosted community instance” | No public URL or deployment evidence is present in the repo, and this review did not inspect the Pi. | Provide independently checkable URL/health/revision/uptime evidence, or say “designed for a future hosted community instance.” |
| “lossless NDJSON export verified by re-ingestion” | The public scientific NDJSON projection explicitly disclaims lossless/re-ingestible semantics. The operator archive is lossless, but it is a different surface. | Distinguish the lossless archive round-trip from scientific NDJSON projection; do not call the latter lossless. |
| “re-computable from inside the database” | Provenance links can be nullable; kinetics lacks exact interpretation/statmech/tunneling assignments; PDep solve inputs are ambiguous. | Say “supports end-to-end traceability when the required evidence is deposited,” then quantify completeness on the frozen dataset. |
| raw/byte-exact artifacts are directly retrievable | Artifact metadata and content hashes may be public, but raw content access is separately authorization/approval gated and producers do not necessarily deposit every raw-file kind. | Say artifacts are content-addressed and retrievable subject to deposition and access policy; quantify artifact completeness in the released example. |
| every value is labelled with a level of theory | Experimental, literature, imported, and estimated products need not have a LoT. Computed products may be traced to LoT-labelled source calculations when those links are present. | Scope the claim to computed records with deposited source-calculation provenance. |
| isotopologues “remain resolvable” | Identity is a free-text label with no per-atom isotope assignment. | Remove or qualify until atom-resolved isotope identity exists. |
| geometry hash is a cross-source chemical geometry identity | The hash is over normalized XYZ text; it is not generally invariant to rotation, translation, or atom permutation. | Call it content/canonical-text addressing under the documented normalization, and validate any stronger identity claim separately. |
| conformer fingerprint is generally symmetry-aware | Current handling is narrower, with methyl folding, a fixed angular threshold, and RMSD fallback; broad molecular automorphisms and coupled torsions are not demonstrated. | Publish a benchmark over rings, non-methyl symmetric rotors, automorphisms, coupled torsions, and threshold sensitivity. |
| “the schema comprises 30 relational models” | There are 110 mapped tables; approximately 30 is the model-module count, not relational-model count. | Report modules and mapped tables separately, generated at release time. |
| public-reference examples such as `species_…` and `reaction_entry_…` | Actual registered prefixes include compact forms such as `spc_`, `spe_`, `rxn_`, and `rxe_`. | Generate examples from the public-ref registry/OpenAPI rather than spelling them by hand. |
| representative selection is an “explicit, attributed curation-overlay selection” | Current candidate collapse is a deterministic heuristic; no persisted curator/release selection exists for thermo/statmech/transport. | Call it a named read-time heuristic, or implement the audited selection layer. |
| PLOG/Chebyshev are never stored as ordinary reaction-entry kinetics | The ORM and real CHEMKIN integration test support standalone reaction-level PLOG and Chebyshev rows as well as network-level products. | Correct Methods to describe both representations and the scientific distinction between deposited phenomenological fits and master-equation network products. |
| real mechanism has “64 reactions” | The fixture has 64 rate rows representing 61 reaction identities. | Report both numbers precisely. Publish the fixture and output. |
| no stored value can be traced/re-run in existing resources | This is too categorical and is contradicted by calculation repositories/workflow systems. | Narrow to the specific combined product-level contribution and cite primary sources. |
| all remaining limitations require no schema redesign | PDep pathway identity, isotope identity, ensembles, and statistically defined UQ do require meaningful schema work. | Remove “none … architectural”; state which extensions are additive and which change identity/relationships. |
| “live, growing” ML/data alternative | Current corpus is modest seed data and no immutable public release is cited. | Say “designed to support a growing corpus” until a citable release exists. |

Primary locations:

- [`paper/18__TCKDB/0_abstract.tex`](../../paper/18__TCKDB/0_abstract.tex#L1)
- [`paper/18__TCKDB/1_intro.tex`](../../paper/18__TCKDB/1_intro.tex#L6)
- [`paper/18__TCKDB/3_results.tex`](../../paper/18__TCKDB/3_results.tex#L4)
- [`paper/18__TCKDB/3_results.tex`](../../paper/18__TCKDB/3_results.tex#L12)
- [`paper/18__TCKDB/3_results.tex`](../../paper/18__TCKDB/3_results.tex#L19)
- [`paper/18__TCKDB/3_results.tex`](../../paper/18__TCKDB/3_results.tex#L26)
- [`paper/18__TCKDB/4_conclusions.tex`](../../paper/18__TCKDB/4_conclusions.tex#L4)
- [`backend/app/db/models/README.md`](../../backend/app/db/models/README.md#L4)
- [`backend/tests/integration/test_chemkin_round_trip_real.py`](../../backend/tests/integration/test_chemkin_round_trip_real.py#L1)

### Claims that are strong and defensible

- four-role separation of identity, provenance, result, and curation;
- append-only candidates rather than mutable product preference flags;
- content-first ingestion with server-side identity/reference resolution;
- calculation hub with typed results, geometries, dependencies, parameters,
  artifacts, diagnostics, Hessians, and software/workflow provenance;
- explicit thermo representations, corrections, frequency scale factors,
  falloff/PLOG/Chebyshev/multi-Arrhenius kinetics;
- stable public references and deployment-gated internal IDs;
- tested CHEMKIN store/export/downstream-load round-trip, if the exact fixture,
  code revision, and result artifacts are published; the Cantera assertion is
  dependency-gated with `importorskip`, so the paper release must run it in an
  environment where Cantera is installed and record the non-skipped result;
- candid statement that the machine-review providers are disabled/stubbed and
  do not contribute to public trust;
- self-hostability and documented arm64/Raspberry Pi deployment path.

### Evidence the paper still needs

1. A frozen code release and citable data snapshot with DOI.
2. Exact generated schema/API/test counts at that tag.
3. Public manifest and checksums for every result/figure dataset.
4. CHEMKIN fixture, exported output, Cantera version, validation command, and
   machine-readable comparison results.
5. A traceability table for the ethylene example with public refs and artifact
   digests, plus a script that reproduces the walk.
6. A genuine disagreement example across methods; seven identical values are a
   useful repeatability demonstration but not evidence of disagreement handling
   in practice.
7. Query-performance evaluation at realistic cardinality, including hardware,
   PostgreSQL/RDKit versions, plans, latency percentiles, and memory.
8. Upload round-trip validation for at least ARC/computed species and reaction,
   statmech, thermo, kinetics, TS/IRC, and PDep if PDep remains a headline.
9. A competitor table whose cells are sourced to primary literature or official
   documentation and use narrow, testable capability definitions.
10. Non-empty Supporting Information: endpoint inventory, schema views,
    conformer algorithm, selection policy, validation protocols, and release
    manifest.
11. Re-run and publish the existing Arkane statmechanics round-trip against the
    current API, including the formation-enthalpy correction/reference chain,
    optical-isomer handling, rotor membership, and per-mode frequency access.

The current test-count statement (“over 4,500”) remains directionally true, but
the release should generate rather than hand-maintain metrics. At this review
the counts are 5,067 test functions and 291 test files.

## Raspberry Pi deployment assessment and runbook

The repository is deployment-shaped for arm64:

- Compose uses an RDKit/Postgres image documented as multi-arch;
- the API Docker build targets `linux/amd64,linux/arm64`;
- services bind to loopback;
- hosted startup guards cover registration, docs, legacy IDs, cookies, rate
  limiting, CORS, and internal IDs;
- Cloudflare/reverse-proxy and systemd paths are documented.

However, the Dockerfile still calls itself a scaffold pending an actual
multi-arch validation, and a GitHub workflow declaration is not proof that the
published arm64 image exists and boots. Verify the image manifest and run an
arm64 smoke test.

For the existing Pi, use this order:

1. **Inventory without changing state**
   - record git SHA/image digest, OS/architecture, Compose version, free disk,
     DB size, MinIO size, current Alembic revision, RDKit/Postgres versions,
     API `/health` and `/readyz`, worker process, queue counts, and backup age;
   - save the output as a dated deployment record.
2. **Back up both data planes**
   - create a compressed `pg_dump`;
   - mirror MinIO/S3 objects to a physically separate target;
   - create checksums and an inventory mapping artifact rows to objects.
3. **Restore drill off the Pi**
   - restore DB and objects into an empty staging instance;
   - run migrations only on the staging copy;
   - run artifact-integrity, representative read, upload dry-run, and export
     checks.
4. **Fix release blockers in code**
   - worker leases/recovery, job authorization, enqueue idempotency;
   - strict kinetics/statmech validators;
   - readiness head comparison.
5. **Choose the scientific release scope**
   - either redesign PDep pathway/solve-input identity before including PDep in
     the paper, or explicitly label it an incomplete schema prototype and
     exclude it from the citable dataset claims.
6. **Build and test immutable images**
   - publish SHA-tagged multi-arch images;
   - boot the exact arm64 image in staging;
   - record image digest and dependency manifest.
7. **Upgrade the Pi**
   - stop writes/worker;
   - take fresh DB and object backups;
   - follow
     [`backend/docs/deployment/migrations.md`](../../backend/docs/deployment/migrations.md);
   - run `alembic current` and compare with `alembic heads`;
   - start one API worker and the durable upload worker;
   - run
     [`backend/scripts/check_selfhosted_deployment.sh`](../../backend/scripts/check_selfhosted_deployment.sh).
8. **Verify from outside the host**
   - TLS and proxy headers;
   - anonymous curated reads;
   - authenticated upload dry-run and one idempotent job;
   - owner isolation for jobs/submissions;
   - rate-limit behavior;
   - artifact download integrity;
   - backup age and alerting.
9. **Freeze release evidence**
   - export the citable snapshot and manifest;
   - run paper scripts/tests against that snapshot;
   - mint DOI and add `CITATION.cff`;
   - only then write “deployed” and quantitative corpus claims.

## Skills and what belongs on the Pi

The listed Codex skills are development/review workflows, not TCKDB runtime
dependencies. Do **not** copy the whole personal `~/.codex/skills` tree to the
deployed Pi.

| Skill group | Where it belongs |
|---|---|
| `alembic-initial-schema-sync` | Not applicable to the current layered deployed-migration policy; the expected metadata-initial checker is absent. Use migration integration tests and `alembic check` against a current staging DB. |
| `sqlalchemy-models-to-dbml` | Developer/CI workstation. The repo-local generator is current and the committed DBML exactly matches ORM metadata. |
| ARC/ARCbench/Arkane/TS-energy/scheduler skills | ARC integration/developer machine or Zeus workflow environment, not the TCKDB production host. |
| `g09-status-report` | Gaussian job-management workstation only. |
| `git-rebase` | Developer workstation when rebasing; never needed by the running service. |

The Pi should receive only versioned application images/source, environment
configuration, migrations, operator scripts, service units, and backup/restore
tooling.

## Followable implementation roadmap

### Stage 0 — freeze the contract and baseline

Deliver:

- endpoint/client/ingestion/query/export parity matrices generated from OpenAPI
  and route registries;
- supported versus experimental labels;
- a representative staging corpus;
- deployment inventory and restore-tested backup;
- manuscript claim-to-test/release evidence matrix.

Exit criteria: every public claim and product journey has an owner, test, and
versioned artifact.

### Stage 1 — ingestion reliability and security

Implement:

- worker lease, heartbeat, retry attempts, stale reclaim, terminal error policy;
- job owner/curator/admin authorization;
- enqueue idempotency;
- async feature-parity decision;
- pagination for submission/moderation lists;
- ephemeral test-DB cleanup.

Exit criteria: kill-after-claim integration test recovers exactly once; cross-user
job reads fail; a retried enqueue returns the same job; no scientific duplicate
is created.

### Stage 2 — scientific integrity blockers

Implement:

- strict kinetics/statmech content and model-shape validators;
- PDep channel-to-reaction/TS identity and per-state/per-path solve inputs;
- normalized bath composition and state/collider-specific energy transfer;
- explicit kinetics statmech/conformer/tunneling assignments;
- structured TS NMD/IRC validation;
- atom-resolved isotope support, or remove isotopologue scope.

Exit criteria: realistic multi-well/multi-pathway examples round-trip without
ambiguity; intentionally incomplete records fail before persistence.

### Stage 3 — curated product and release semantics

Implement:

- curated versus exploratory query profiles;
- attributed, append-only release selections;
- immutable dataset manifest/checksums;
- schema/review/policy/software version binding;
- license, changelog, security/contact, and citation metadata.

Exit criteria: a user can cite and reproduce the exact selected dataset while
still retrieving all underlying candidates and review history.

### Stage 4 — query and client validation

Implement:

- catalog-scale benchmark and recorded query plans;
- numeric scientific filters or a bounded analytics layer;
- keyset/release-watermarked traversal;
- OpenAPI-driven client parity tests and missing typed methods;
- safe retries for GET/idempotent POST only.

Exit criteria: published SLOs hold on the representative corpus and every
documented journey has a tested Python-client example.

### Stage 5 — production operations

Implement:

- readiness comparison to expected Alembic head;
- DB plus object-store backup automation and restore verification;
- queue/worker/request/DB/artifact/backup metrics and alerts;
- shared rate limiting before multi-worker scaling;
- immutable arm64 image build and smoke test.

Exit criteria: restore drill passes, a worker crash self-heals, schema drift
blocks readiness, and the Pi deployment is reproducible from a tagged release.

### Stage 6 — paper release

Deliver:

- corrected manuscript claims;
- primary-source comparison table;
- complete SI;
- frozen code/data DOI;
- executable figure/table generation;
- traceability, disagreement, CHEMKIN, ingestion, and query-performance
  evidence.

Exit criteria: every quantitative or comparative statement points to a cited
source, public release artifact, or executable test at the paper tag.

## Bottom line

TCKDB is on the right architectural track. Most of the July science gaps
(spin-aware identity, Hessians, falloff/PLOG/Chebyshev, optical isomers,
electronic levels, spin treatment, reference-state metadata, Wilhoit, and
diagnostics) have already been closed in a disciplined migration sequence. The
remaining work is narrower but consequential: durable ingestion, authorization,
microscopic PDep provenance, strict scientific validity, curated release
semantics, evidence-backed query scaling, and claims calibration.

Completing those stages would make the project credible both as a real deployed
database and as a paper whose strongest claims survive expert scrutiny.
