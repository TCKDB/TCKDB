# Detailed phase B implementation plan

Companions: [Whole-programme plan](tckdb-implementation-programme.md),
[Phase A plan](tckdb-phase-a-implementation-plan.md),
[Phase A evidence](tckdb-phase-a-verification.md),
[Claim-to-evidence checklist](tckdb-publication-evidence-checklist.md).

## Baseline and dependencies

Planning baseline is `8539c927d6ca81758d28a67c0a0fbac31a13c9af` plus the
uncommitted Phase A working tree. Alembic head at planning time is
`c24ce2d9c198`; re-verify before any revision is authored. Phase B cannot
freeze anything until Phase A has landed on `main`, its three gates have run
on the merged commit, and rollout hold points 1–5 of the Phase A evidence
record are recorded. Rights capture and corpus selection may begin alongside.

Phase B is evidence, packaging and rights. It authorizes no scientific
convention: no H/G reference basis, no uncertainty coverage convention, no
accuracy threshold by family. Those remain the programme's hold points. Every
result reported from B is representation, execution or reproducibility
evidence; none is chemical accuracy.

Four decisions were taken 2026-09-19 and bind this plan:

| Decision | Consequence |
| --- | --- |
| The citable corpus is built on a fresh publication instance, not the playground deployment | No archive redaction mode; the deposit refuses any account outside an author allowlist |
| The three depositor accounts on the playground are all the operator's | Historical rights are recorded as `operator_own_data` attestations with an actor; nothing external must be obtained for that corpus |
| The paper claims self-hosting with documented deployment modes, not a durable hosted service | Paired database/object-store recovery and readiness-versus-head checks are not B gates |
| No independent human reproducer is available yet | A clean container run by a script nobody in the loop edited is the first-pass gate; a human report slot stays open |

## Required impact analysis

Same discipline as Phase A: bind the index to this checkout, rebuild if stale,
run upstream `impact` on every function named below before editing, treat
UNKNOWN as unresolved, corroborate with source references, and complete
`detect_changes(scope="all")` without `partial` or `truncated` before any
commit. Targets expected to report HIGH or CRITICAL and to be announced before
edits: `open_upload_submission` and `open_job_submission`
(`backend/app/services/upload_submission.py:144,107`; twelve call sites in
`backend/app/api/routes/uploads.py` and one in `routes/jobs.py:66`);
`add_selection`, `supersede_selection`, `publish_release`
(`backend/app/services/release/curation.py:486,557,228`); `freeze_manifest`
and `snapshot_from_manifest` (`backend/app/services/release/manifest.py:281,432`);
`write_archive` (`backend/app/services/archive/core.py:417`); every upload
request class under `backend/app/schemas/workflows/`.

## B0 — preconditions

Land Phase A through a worktree branch, the three gate scripts, an independent
review and a merge; then execute the A rollout steps. Rebuild the code index at
the landed commit. Confirm `validate_registry`
(`backend/app/services/archive/registry.py:243`) is green: every table B adds
must be classified for the archive or the registry test fails, which is the
intended sensitivity.

## B1 — rights basis for every released record

Measured state. The only rights field in the schema is the per-release
`dataset_release.data_license` / `code_license` pair
(`backend/app/db/models/dataset_release.py:194-203`). No rights predicate
exists in `curation.py`: `add_selection` and `supersede_selection` check only
the approval floor through `_assert_record_is_approved` (`:449`), and
`publish_release` rechecks only approval through
`_assert_standing_selections_still_approved` (`:252`). `candidate_records.ndjson`
ships every unselected candidate for a covered subject
(`backend/app/services/release/records.py:787`), so a gate on selections alone
still publishes unattested bytes. `LICENSE-DATA:105-121` and the ingestion and
release specs state that deposit-time agreement must exist before a second
contributor; the tests enforce that sentence as text, not as behaviour
(`backend/tests/test_release_metadata.py:111`). Records loaded by
`backend/scripts/bulk_load_arc.py:268` call the workflow directly and open no
submission, so a submission-keyed attestation cannot reach them.

Design.

- New append-only table `submission_rights_attestation` in a new module
  `backend/app/db/models/submission_rights.py`, imported in
  `app/db/models/__init__.py` and added to the archive `INCLUDED_TABLES`.
  Enum `RightsBasisKind` in `common.py`: `depositor_agreement`,
  `operator_own_data`, `historical_review`, `source_terms`. Columns:
  public ref, `submission_id`, `license_id` (SPDX identifier, nonblank),
  `basis`, `attested_by` (app_user), `actor_kind`, `attested_at`,
  `source_terms` (CHECK: required when basis is `source_terms`), `note`,
  `supersedes_attestation_id` self-reference. A correction is an insert. Add
  the database-level UPDATE/DELETE refusal trigger used for
  `release_manifest`. No audit-event enum value is added in v1: the row is the
  audit record, and enum additions cost a non-trivial downgrade.
- Wire fragment `DepositRights` in `tckdb-schemas` (new `rights.py`, package
  version bump): `license`, `depositor_attests_right_to_license: Literal[True]`
  so a false value fails validation, `source_terms | None`. No identifiers.
  Optional `rights` field on every upload request class and on
  `BundleSubmissionMetadata`
  (`backend/app/schemas/workflows/contribution_bundle.py:119`). Optional in v1
  so existing clients and the playground continue to work; the release gate,
  not the upload, is where absence bites.
- Choke point: new `backend/app/services/rights.py` with `attest_from_deposit`
  and `record_attestation`, called from `open_upload_submission`,
  `open_job_submission` (new keyword without a default so a forgotten route
  fails loudly) and `submit_contribution_bundle` immediately after
  `create_submission` (`backend/app/workflows/contribution_bundle_submit.py:321`).
  A `depositor_agreement` attestation always carries the submission creator
  as actor.
- Endpoints `POST` and `GET
  /api/v1/submissions/{submission_id}/rights-attestations` in
  `backend/app/api/routes/submissions.py`. `depositor_agreement` only by the
  creator; the other kinds only by curator or admin. Responses use public refs.
- Release predicate `_assert_record_has_rights_basis` and a batch form in
  `curation.py`. Join record → `SubmissionRecordLink` → `Submission` →
  standing attestation (latest not superseded). Every linked submission must
  be attested; fail closed. Compatibility in v1 is an exact, case-insensitive
  match with `release.data_license`; no license lattice. Error codes
  `rights_basis_missing`, `rights_basis_incompatible`,
  `candidate_rights_basis_missing`, `candidate_rights_basis_incompatible`,
  registered in `backend/app/api/code_catalogue.py`. Called from
  `add_selection` and `supersede_selection` after the approval check, and from
  a new `_assert_standing_selections_still_releasable` in `publish_release`
  that also covers the candidate set via `candidate_ids_for_subjects`. Refuse,
  never filter: a filtered candidate list would falsify "every candidate is
  retrievable".
- Artifacts: a `rights` block on every line of `selected_records.ndjson` and
  `candidate_records.ndjson` (license, basis, attestation ref, attested_at,
  actor label as already emitted for curators; never a user primary key).
  `rights_summary_json` on `ReleaseManifest` and on the snapshot so the
  document remains rebuildable from manifest columns. Bump `MANIFEST_SCHEMA`
  (`backend/app/services/release/versions.py:23`) to `tckdb.dataset_release.v2`
  once, shared with B3; the renderer branches on the stored schema so every
  v1 row reproduces its recorded digest.
- Bundle export (`backend/scripts/export_contribution_bundle.py:166`) emits the
  fragment from the source submission's standing attestation;
  `docs/contribution-bundles/v0-format.md` documents it.
- Migration: one revision from the verified head, upgrade and downgrade, no
  backfill. Historical coverage is a curator action with an actor: a read-only
  depositor inventory first, then attestations; for records with no link,
  create a `Submission(source_kind=migration)` via `create_submission`
  (`backend/app/services/submission.py:112`) and `link_records` (`:732`).
- Documentation moves from "not built" to a description of the mechanism in
  `LICENSE-DATA`, `backend/docs/specs/ingestion_submission_model.md`,
  `backend/docs/specs/dataset_release_and_profiles.md` §7b and the runbook's
  §0 bullet, keeping `test_release_metadata.py:111` in step.

Out of scope and named as such: a license compatibility lattice, single-curator
publish governance, and a refuse-at-upload switch.

## B2 — corpus curation on a publication instance

Recipe: fresh `db` and `minio` volumes from `docker-compose.yml`, `alembic
upgrade head` at the landed commit, author accounts only. Deposit through the
API or bundle routes so every record has a submission and an attestation:
the ARC runs chosen for the paper (the bulk loader at `bulk_load_arc.py:268`
opens no submission and must not be used for the citable corpus), the SDF
reaction set (`backend/scripts/bulk_load_reactions.py`, same caveat), and the
RMG ammonia/methane mechanism through the CHEMKIN adapter (fixture
`backend/tests/integration/fixtures/rmg_ammonia_methane/`, whose
`PROVENANCE.md` names it an internal emulation of García-Ruiz et al. 2024, cited
as such).

Required contents, each with a stated reason for inclusion:

- identity challenge cases: a tautomer pair, E/Z and chiral pairs,
  isotopologues, singlet and triplet carbene, a charged species;
- one complete chain (geometry, frequencies, Hessian, artifacts, statmech,
  thermo, review, selection) and one deliberately incomplete chain (the manual
  ORCA/Molpro hydrazine set: no workflow tool, no Hessian);
- one genuine disagreement with compatible definitions (candidate: a hydrazine
  transition state at MRCI+Q and at B3LYP); the seven ethylene deposits are
  deposit-history evidence for the submission layer, not independence, and are
  reported as such;
- thermo and kinetics cases that satisfy the Phase A CHEMKIN eligibility rule
  and cases that deliberately do not, so exported gaps are exercised.

A coverage generator (B3 framework) reports families, charge/spin/isotopes,
methods, temperature and pressure domains, missing evidence and usable
uncertainty, with unknown uncertainty kept distinct from zero.

On that instance a curator approves the cited records (today nothing is
approved anywhere, so no release can be cut), registers a curation policy,
appends selections with rationale and publishes through the runbook. After
the archive is written, the instance is read-only; the deposit builder
refuses a dirty tree or a revision mismatch.

## B3 — digest-bound publication deposit

Measured state. A release freezes four NDJSON artifacts and a manifest; its
contract block (`manifest.py:147`) states that raw artifact bytes, geometries
and calculation payloads are omitted. The version block (`versions.py:40-66`)
records no git commit and may say `"unknown"` for the backend version. The
archive `tckdb.archive.v1` carries rows and byte-exact blobs but restores only
into an empty database at the identical revision, and its restore is tested
in-process only. No script produces any manuscript number. The runbook's
description of verification at `backend/docs/deployment/cutting_a_dataset_release.md:234`
contradicts the code and its own later section.

Design of `tckdb.deposit.v1`:

- A directory, optionally tarred, with `MANIFEST.json` in canonical JSON
  (`backend/app/services/release/artifacts.py:116`): `schema`; `release`
  (tag, ref, manifest digest); `source` (git commit, git tag, backend version,
  schemas version, Alembic head); `members` as path, SHA-256, byte count and
  role. Roles: release manifest, the four release artifacts, evidence archive,
  source pins (`commit.json`, `environment.yml`, `uv.lock`, `Dockerfile`,
  `CITATION.cff`, `LICENSE`, `LICENSE-DATA`), result generators, expected
  outputs, `REPRODUCE.md`, `ACCOUNTS.md`.
- Service `backend/app/services/deposit/build.py` (collect members, source
  binding, publishability assertions, write, verify) and a thin script
  `backend/scripts/ops/build_publication_deposit.py build|verify`. The release
  is read from the database through `load_manifest` (`manifest.py:425`) and
  `ReleaseArtifact.content`, never HTTP, and only if `verify_release`
  (`manifest.py:497`) reports ok. `write_archive` runs in the same session;
  the archived `release_artifact` digests must equal the emitted members.
  Refusals: dirty tree, no exact tag, any `"unknown"` version, database
  revision different from the script head.
- Privacy: an `--author-account` allowlist; every actor reference
  (`created_by`, `selected_by`, `reviewed_by`, `attested_by`) must resolve to
  it or the build refuses naming usernames only. `ACCOUNTS.md` lists username,
  ORCID and affiliation, never email, and states the residual risk that ESS
  output blobs contain the authors' cluster paths. No redaction mode: the
  archive's byte-exact, fail-closed contract is kept.
- `ReleaseManifest.git_commit` (nullable, 40 characters) and
  `versions.git_commit()` reading `TCKDB_GIT_COMMIT` baked into the image, with
  `git rev-parse` as fallback and `"unknown"` last. Emitted under manifest
  schema v2. The deposit builder asserts it equals `HEAD`.
- Generators under `backend/scripts/paper/`: a registry of name → callable
  over a session, and `generate_expected_outputs.py --output-dir`. They read
  the restored database through the ORM and read services, because that is
  what a reproducer has and what the paper describes. Determinism rules:
  order by public ref, no wall-clock values, canonical JSON, decimals as
  strings. One generator per `[DATA]` claim in `paper/19__TCKDB_skeleton/`:

| Skeleton line | Generator |
| --- | --- |
| `4_limitations.md:7` corpus counts, depositors, tools, ESS releases, levels of theory, review states | `corpus_counts` |
| `3_results.md:16` mechanism species, rate expressions, forms | `mechanism_roundtrip_counts` |
| `3_results.md:33` ethylene selected thermo values | `selected_thermo_by_species` |
| `3_results.md:35`, `SI.md:33` seven ethylene candidates: submission refs, timestamps, ARC commits, artifact digests | `candidate_lineage` |
| `3_results.md:43` transition-state entries with an imaginary mode, with a Hessian | `transition_state_evidence` |
| `3_results.md:44`, `SI.md:29` spectrum-from-Hessian agreement table | `hessian_reanalysis` (B4) |
| `SI.md:25` fixture mechanism provenance and Cantera version | `mechanism_fixture_provenance` |
| (Phase C demonstration, not a Phase B skeleton line) computed-vs-observed Cp(T) per thermo, with every review-tier finding field | `experimental_cp_comparison` (Phase C-E4) |
| (Phase C demonstration, not a Phase B skeleton line) ThermoML article custody: source, digest, parser/mapping versions | `thermoml_source_provenance` (Phase C-E4) |

The two ethylene rows are served by the generalised generators (every species
with a standing selection; every species entry with more than one thermo
candidate), which the paper filters to ethylene at write-up time; the shipped
registry (`backend/scripts/paper/registry.py`, PR #497) carries no
ethylene-specific generator.

- `REPRODUCE.md`: check out the pinned commit; start `db` and `minio`;
  `alembic upgrade head`; `backend/scripts/tckdb_archive.py restore` with the
  environment names read by `Settings.database_url`
  (`backend/app/api/config.py:242`) documented; run the generators; byte-diff
  against `expected_outputs/`; `build_publication_deposit.py verify --db`;
  `backend/scripts/ops/verify_artifact_integrity.py --release <tag>`. Add
  `tckdb_archive.py verify <tar>` for an offline member-hash check.
- Integration test `backend/tests/integration/test_deposit_round_trip.py`:
  build from a small corpus, migrate a scratch database, restore through a
  subprocess, run the generators, compare bytes. This is the first
  out-of-process restore test.
- Runbook: correct line 234, add a "build the publication deposit" section, keep
  the curl procedure.
- Clean-environment gate: a throwaway container job, defined once and not
  edited by anyone in the implementation loop, runs `REPRODUCE.md` end to end
  from the deposit alone; its log is the first-pass reproduction evidence. The
  verification record keeps a slot for a human reproduction report.

## B4 — experiments with predeclared tolerances

- C1 identity. A frozen fixture set under `backend/tests/fixtures/identity/`
  (expected merges, expected splits, expected exclusions) run through the
  resolution services by one parametrized test that fails on an empty set.
  Seed it from the cases in `backend/tests/services/test_species_identity.py`,
  `test_species_isotope_identity.py` and `test_stereo_label_from_3d.py`. Report
  false merges and splits by class. The absence of a stereo backfill for
  pre-fix deposits is stated, not hidden.
- C2 evidence replay. (a) A deposited Hessian reanalysis script that reads
  `calc_hessian`, geometry and masses from the restored database, projects via
  `backend/app/chemistry/normal_modes.py`, compares to stored `calc_freq_mode`
  values, and reports residual and refusal category per case
  (`hessian_not_stored`, `masses_unresolved`, rigid-body curvature). Cases
  without a Hessian stay in the denominator. Reuse the sweep in
  `backend/scripts/ops/project_imaginary_modes.py`. (b) The Arkane statmech
  replay (`backend/scripts/validation/arkane_statmech_roundtrip.py`) is made to
  read from a database session instead of curl and SSH, with RMG pinned and
  kept out of process. Its two approximations (dropping the R lowest
  frequencies in place of Hessian projection; no atom or bond corrections)
  and the two API gaps (per-mode frequencies, optical isomers) are reported as
  limitations. Fixing them is not B work.
- C3 mechanism interoperability. Extend
  `backend/tests/integration/test_chemkin_round_trip_real.py` with a numerical
  grid: Cp, H and S for every species under the Phase A printed-precision
  bounds plus `1e-10`, and k(T) or k(T,P) for every supported rate form at
  declared points. Rate tolerance is derived from the printed precision of
  each parameter, stated in the test before any comparison. Unsupported forms
  and export gaps are listed in the output, never dropped.
- C4 review and selection. On the restored database, re-running selection
  under the frozen policy reproduces the `selected_records` digest; an
  explicit refusal is produced when no eligible candidate exists.
- C5 disagreement. One case, both candidates preserved, definitions checked
  compatible, curation or abstention explicit with rationale; independence is
  counted by method and group, never by upload id.
- Performance. Only if the manuscript quotes a timing: rerun
  `backend/scripts/bench/run_benchmark.py` at the paper commit on the
  workstation with the recorded caveats. Otherwise exclude performance and
  say so.

## B5 — manuscript correspondence

Every `[DATA]` line maps to a generator in the table above. Apply the
corrections D1–D11 and the rewordings of
`paper/18__TCKDB/DIVERGENCE_2026-09-13.md`. Withdraw the eight-column
"only TCKDB" comparison until the SI matrix has a primary source and a precise
definition per cell. `CITATION.cff` gains `doi:` only after the repository
deposit, with `test_release_metadata.py:159` relaxed in the same change, and
cites the dataset release tag and manifest digest. The claim checklist
statuses are updated from the deposit, not from intent.

## Explicitly deferred

- Paired database and object-store recovery drill, readiness-versus-head
  check and alert evidence: not a B gate because the paper does not claim a
  hosted service; tracked as operations work.
- Frontend release journey and typed client release methods: platform work.
- QCSchema, ThermoML, covariance and reconciliation: phases C–E.

## Verification

| Scenario | Required outcome |
| --- | --- |
| Linked but unattested record | Refused at selection and at publish with `rights_basis_missing` |
| Attested under a different license | Refused with `rights_basis_incompatible`; exact match accepted |
| Record with no submission link, or two links with one unattested | Refused |
| Curator records a historical attestation | Previously refused record becomes selectable |
| Attestation superseded, or unattested candidate added, after selection | Publish refuses |
| Release artifacts | Every line carries the rights block; no user primary key anywhere |
| Deposit build on dirty tree, unknown version, revision mismatch, non-allowlisted account | Refused, naming the cause |
| Subprocess restore into an empty database | Release bytes and SHA-256 identical; generator outputs byte-identical to `expected_outputs/` |
| Hessian reanalysis | Residual per case within the declared bound; refusals counted, not dropped |
| Identity fixtures | Zero false merges; splits and exclusions as expected; empty set fails |
| CHEMKIN grid | Within predeclared bounds; gaps listed |
| Selection replay | Digest-identical; refusal when no eligible candidate |
| Container reproduction | Log captured from the deposit alone |
| Manuscript | Every `[DATA]` number equals a generator output |

Commands: the three gate scripts as in the Phase A plan, the new integration
test, `bash backend/scripts/update-openapi-golden.sh`, DBML regeneration, the
shared-schema and client suites, and `detect_changes(scope="all")`.

## Completion gate

B completes when the rights predicate is live and tested; the publication
instance is curated, reviewed, released and archived; `tckdb.deposit.v1` is
built at an exact tagged commit with every member digest-bound; the container
reproduction passes from the deposit alone; every manuscript `[DATA]` number
and figure comes from a deposited generator; the claim checklist is updated
from the deposit; and each work package has an independent reviewer PASS.
Minting the DOI stays a runbook step the author triggers. A human
independent reproduction is recorded when available; the paper's wording does
not present the container run as one.

## Delivery

One worktree and one pull request per work package. B1, the B3 framework and
the B4 fixtures and scripts can proceed in parallel after B0; B2 needs B1;
the freeze needs everything. Each brief records its impact analysis, runs the
gates from the main session, and passes independent review before merge. No
deployment to the playground instance is required by B.
