# Changelog

All notable changes to TCKDB are recorded here.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## Two things are versioned, and they are not the same

**The software** (`tckdb-backend`, `tckdb-client`, `tckdb-schemas`,
`tckdb-mcp`, `tckdb-chemkin`) is versioned per package and described in this
file.

**A curated scientific dataset** is versioned independently as a *dataset
release* — a tag such as `2026.07.0`, an immutable SHA-256-checksummed
manifest, an attributed selection ledger, and its own citation string. Dataset
releases are not listed here; they are discoverable at
`GET /api/v1/scientific/releases` and each carries its own
`changelog_entry`. Cite the dataset release when you use TCKDB's numbers; cite
the software (see [`CITATION.cff`](CITATION.cff)) when you use the code.

Conflating the two is the mistake this split exists to prevent: upgrading the
backend must never change what a published dataset says, and re-curating a
dataset must never require a code release.

## Maturity and version policy

TCKDB is **pre-1.0**. Until 1.0:

- **Minor** version bumps may contain breaking changes to HTTP contracts,
  wire schemas, and database schema. Read the entry before upgrading.
- **Patch** bumps are additive or corrective only.
- The **database schema** advances by Alembic revision, never by editing an
  applied revision. The revision a deployment is at is reported by
  `GET /api/v1/readyz`, and is bound into every dataset-release manifest.
- **Append-only tables stay append-only.** `release_selection`,
  `release_manifest`, `release_artifact`, `record_review_event`,
  `record_reproducibility_assessment` and the accepted-science tables are
  guarded by database triggers, not only by application code. No release will
  relax that.
- **A published dataset release is never rewritten.** Superseding a curated
  selection appends a row; retracting a release sets its status to
  `withdrawn` and keeps the row and manifest readable, so an outstanding
  citation never dangles.

Per-package maturity classifiers:

| Package | Status | Notes |
|---|---|---|
| `tckdb-backend` | Alpha | HTTP read contracts stabilising; schema still advancing |
| `tckdb-client` | Beta | Typed methods track the published OpenAPI |
| `tckdb-schemas` | Alpha | Upload wire contracts; version bumped on every change — **enforced in CI since 2026-08-17; five earlier versions are not unique, listed under Unreleased** |
| `tckdb-mcp` | Alpha | Agent integration surface |
| `tckdb-chemkin` | Alpha | Mechanism-export adapter |

The client is deliberately ahead of the backend: it is a thin, well-tested
wrapper over a contract that is itself still moving.

## Unreleased

### Fixed

- **Two pull requests could claim the same package version, and `git` would
  merge it silently.** Every change to a published package bumps its version.
  Two branches that start from the same base therefore bump to the *same next
  number*, and when the second merges, the version line on both sides is
  **byte-identical** — so `git` reports no conflict, the file does not appear
  in the merge diffstat, and one version number ends up describing two
  different packages. This was caught by hand on 2026-08-17, one line-diff
  before it shipped.

  A pull-request check now refuses it. It makes **two** comparisons against
  **two different refs**, because one ref cannot do both: *monotonicity*
  against the merge base (`git merge-base(base.sha, head.sha)` — not `main`'s
  tip, or a branch merely behind would be blamed for someone else's merge),
  and *novelty* against `origin/main` fetched at job runtime plus existing
  `<name>-v<version>` tags. The near-miss above passes the first check and is
  caught only by the second. Comparison uses a PEP 440 sort key, not string
  order, so `0.10.0` correctly exceeds `0.9.0`. Covers `tckdb-client`,
  `tckdb-schemas`, `tckdb-chemkin` and `tckdb-mcp`; a test fails if a new
  `pyproject.toml` appears in neither the covered nor the excluded list.

- **Five `tckdb-schemas` versions already carry more than one package, and
  this is the record of them.** Measured twice, by two independently written
  scripts that agreed, comparing *tree contents* per version rather than
  commit counts:

  | Version | Distinct states |
  |---|---|
  | `0.2.0` | 3 |
  | `0.8.0` | 2 |
  | `0.14.0` | 2 |
  | `0.30.0` | 2 |
  | `0.33.0` | 2 |

  `tckdb-mcp` `0.1.0` carries four states — it has never been bumped at all.
  **`tckdb-client` is clean** across its whole history.

  The difference is not cosmetic. Across the two `0.2.0` states, one lacks
  `HessianSource`, `TunnelingModel`, and the `lindemann` / `troe` / `sri` /
  `plog` / `chebyshev` entries entirely: **one state rejects payloads the
  other accepts.**

  **`0.8.0` is the one to know about.** The annotated tag
  `tckdb-schemas-v0.8.0` reads *"pinned for tckdb-adapters/tckdb_arc (Phase
  1)"*, and holds the **first** of its two states —
  `git merge-base --is-ancestor 7ad5cb99a tckdb-schemas-v0.8.0` returns false.
  Commit `7ad5cb99a` changed `tckdb_schemas/enums.py` and
  `tckdb_schemas/workflows/computed_reaction_upload.py` and left
  `version = "0.8.0"` untouched, so the two states **accept different upload
  fields**.

  **No consumer resolves a version number**, which is why nothing is being
  re-published to correct this. These packages were only ever installed from
  git, never from a package index, and ARC — the only known downstream —
  installs from a branch with no tag, SHA, or version constraint at all. So a
  duplicated version cannot mis-resolve for anybody today.

  **The `v0.8.0` tag is deliberately left where it is.** Moving or deleting a
  tag is its own hazard: anyone who has already fetched it holds the old
  target either way, and a moved tag makes two clones disagree about what a
  name means. It is recorded here instead, which is the honest fix for
  something that has already happened.

- **The `mypy` gate could not see the wire-contract package, and said
  "Success" anyway.** `tckdb-schemas` is a first-party package that lives in
  this repository and is installed *editable*. `mypy` does not read an
  editable install's import hook, so it could not find the package by name and
  reported `import-not-found` on all **38** of its imports from
  `backend/app/schemas` — which `ignore_missing_imports = true` then absorbed
  silently. The gate reported `Success: no issues found in 149 source files`
  while every type error *inside* the wire package, and every type error in
  backend code arising from how it uses the wire package, was invisible to it.

  Made resolvable and made a check target: `mypy_path` points at the package,
  the package is listed in `files`, and `ignore_missing_imports` is now
  **off**. All three are necessary and none substitutes for another.
  `follow_imports = "silent"` means a merely-*imported* module is analysed but
  its own errors are suppressed, so `mypy_path` alone would still have said
  nothing about a broken annotation inside the package; and
  `ignore_missing_imports` cannot distinguish "third-party package with no
  stubs" from "first-party package we failed to point mypy at", which is what
  made the original failure silent by construction. Measured at the time of
  the change: nothing in scope needed the setting — all 38 suppressed errors
  were `tckdb_schemas`. A stubless third-party dependency now needs a narrow
  per-module override, the way `rdkit` already has one.

  **12 findings** the gate had been missing, all in code that merged green.
  Eleven are fixed here; one is a documented suppression with its argument on
  the line. The one that was a live defect rather than an annotation
  infelicity: `EnergyTransferIn.model` on the pressure-dependent network
  upload is a **required** `str`, but its normalizer was
  `normalize_optional_text`, which collapses a blank string to `None`. A
  whitespace-only energy-transfer model name passes `min_length=1` and then
  left the field holding `None`. It is now `normalize_required_text`, which
  refuses the blank with a 422 — which is what `min_length=1` was already
  promising. The rest: two `float | None` comparisons in the NASA polynomial
  temperature-bound validator that were guarded by a `None`-count the checker
  could not read; two loop variables reused across loops over differently
  shaped payloads; and one dict annotated with the ORM `CalculationType` while
  holding the wire `CalculationType` — two distinct classes with identical
  members, whose every comparison worked only because both subclass `str`.

  Gate scope went from 149 to 183 source files. Proven by mutation, because a
  configuration change that silently still ignores the package looks identical
  to one that works: a deliberate `return 12345` from a `-> str` function
  inside the wire package, a deliberate misuse of a `tckdb_schemas` symbol
  from `backend/app`, and a deliberately unresolvable `mypy_path` each fail
  the gate now and each passed it green before.

- **A full artifact store is no longer reported as "retry later", and no
  longer reports itself as healthy.** Two halves of one defect.

  *The wrong answer.* `artifact_storage.py` special-cased exactly one
  condition — the store answering "no such key" — and every other error a
  botocore `ClientError` can carry collapsed into a single
  `503 artifact_storage_unavailable ... Retry later.` A **full** store landed
  in that residue, so a depositor uploading into a store with no disk left was
  told to do the one thing that cannot work: retrying a full disk fails until
  an *operator* frees space. Uploads into a store that has no room now answer
  **`507 artifact_storage_full`**, whose body says an operator must act.
  507 rather than a second code at 503 because the *status* then carries the
  advice: it is registered with exactly this meaning (RFC 4918), it is what
  MinIO itself answers, and it is absent from `tckdb-client`'s default retry
  set — so a pinned client and a non-Python caller both stop after one attempt
  without knowing the code exists. The `Replay` vocabulary is deliberately not
  extended: `never_succeeds` would be a false claim (an operator frees space
  and the identical request then succeeds) and any declaration at 507 is inert
  by `is_replay_futile`'s own rule.

  The error codes are **measured, not inferred**. MinIO
  `RELEASE.2025-09-07T16-13-09Z` was filled on a size-capped scratch volume
  and answered `XMinioStorageFull` at HTTP 507; with a hard bucket quota it
  answered `XMinioAdminBucketQuotaExceeded` at HTTP 400. `EntityTooLarge` is
  deliberately *not* treated as a capacity signal — it is a fact about one
  object, not about free space.

  *The silent health check.* `/status` probed artifact storage with a
  `head_bucket`, and a full store answers a `head_bucket` with **200**: every
  read succeeds, and — measured — even a 1-byte write succeeds on a store that
  refuses a 4 MiB one, because MinIO's threshold check is sized against the
  incoming object. So `/status` reported green while every artifact upload
  failed for want of space, and nobody was told, including whoever was on
  call. No read-only probe can detect this and no *cheap* write probe can
  either; the S3 API exposes no capacity query to ask instead. `/status` now
  reports what the real write path was told, as
  `artifact_storage.storage_full` with the observation timestamp, and degrades
  on it. Its limits are documented rather than papered over: `false` means "no
  write has been refused for room in this process", not "there is space"; it
  needs one upload attempt to fire; and it is per-process and cleared by a
  restart. See
  [`docs/deployment/troubleshooting.md`](docs/deployment/troubleshooting.md).

  Also fixed on the way: `artifact_persistence._store_and_record`'s broad
  `except Exception` caught the already-typed `ArtifactStorageUnavailable` and
  raised a *fresh* one, discarding every discriminator set upstream — so the
  pre-existing `missing` flag was being erased on the upload path too; and
  `store_artifact` let a raw `ClientError` from `create_bucket` escape past
  every `except ArtifactStorageUnavailable` downstream.

  Behaviour of `artifact_storage_unavailable` (503) and
  `artifact_object_missing` (502) is unchanged. **`tckdb-client`
  0.51.0 → 0.52.0** (documentation and one test; no code change — 507 was
  already outside the default retry set). No schema or migration impact.

- **A client no longer retries a lost artifact forever.**
  `GET /scientific/artifacts/{sha256}/download` reports two different storage
  failures: `503 artifact_storage_unavailable` when the object store did not
  answer, and `502 artifact_object_missing` when it answered and the bytes a
  still-published record points at are gone. Only the first can clear by
  waiting. Both statuses are in `tckdb-client`'s default retry set and the
  code that says which is which was excluded from `RejectionCode` by
  construction (the enum is `4xx` only, because a `5xx` refuses nothing the
  caller did) — so the server was honest and no client could act on it, and a
  custody break was replayed on a backoff schedule for a condition guaranteed
  never to clear.

  The code catalogue now carries a second, **declared** classification
  (`Replay`) beside the derived `is_client_facing`, and the generator emits it
  as **`NON_RETRYABLE_CODES`** — a `frozenset[str]`, not enum members, since
  these are not refusals. `RetryPolicy` reads the response body's `code` at the
  point it decides to retry (the whole response is in scope there) and stops
  after one attempt on a match. It is a **deny list**: an unrecognised code is
  retried exactly as before, so a pinned client never abandons a transient
  failure a newer server introduced. No HTTP status, code spelling or response
  body changed; no `RejectionCode` member was added or removed.
  **`tckdb-client` 0.49.0 → 0.50.0.** No schema or migration impact.

### Added

- **Imaginary modes get a determination, not just a threshold.**
  `GET /api/v1/scientific/calculations/{ref}?include=imaginary_mode_projections`
  projects each imaginary mode onto rigid-body motion and onto a dihedral
  rotation about each rotatable bond, as ADR 0012 asked for. The eigenvectors
  are recovered from the Hessian already stored in `calc_hessian`, so **nothing
  new is stored**: no table, no column, no migration, and the block is
  recomputed per request. The depositor's declared `imaginary_disposition` is
  reported *beside* the computed determination with the raw overlaps and the
  thresholds applied; a disagreement is surfaced as `agreement: conflicts` and
  never silently resolved. Where no Hessian is stored the block reads
  `hessian_not_stored` -- not determinable, which is a different answer from
  "no residue found". Detail-only and opt-in by name, like `include=trust`.
  `available_sections.has_hessian` is new on every calculation record.
  `backend/scripts/ops/project_imaginary_modes.py` runs the same projections
  over a whole corpus; run over all 18 live records carrying an imaginary mode
  and a Hessian, every one determines as `internal_vibration` with
  `rigid_body_overlap` 0.0000, and none carries a declared disposition. ADR 0013
  held that this was uncomputable because TCKDB stores no displacement vectors;
  that claim is corrected in place.

- **Curated vs exploratory read profiles.** Every `/api/v1/scientific/*`
  endpoint accepts `?profile=exploratory|curated`. `exploratory` is the default
  and is explicitly labelled as carrying **no TCKDB recommendation**; `curated`
  raises the review floor to `approved` (applied both to searches and to
  detail-by-ref reads) and reports `approved_floor_only` — it does not claim a
  curator selected those records. The release-backed endorsement
  (`tckdb_curated_release`) is emitted only by `/scientific/releases/*`, where
  records really are resolved through an attributed selection. The resolved
  profile is echoed in every scientific response and in every dataset manifest.
- **Attributed, append-only release selections.** New `curation_policy`,
  `dataset_release` and `release_selection` tables record which candidate a
  named curator chose for a subject, under which policy version, with what
  rationale, for which release. Selections never mutate the record they point
  at; superseding appends a new row.
- **Immutable, citable dataset manifests.** New `release_manifest` and
  `release_artifact` tables freeze the manifest document, each shipped file's
  bytes, and a SHA-256 over both, bound to the Alembic revision, backend and
  wire-schema package versions, curation-policy version and review-policy
  version. Publication is the only write: later uploads, review progressing, a
  DOI being attached or a withdrawal cannot change what a citation resolves to.
  Whether the live database still agrees is reported separately and
  non-fatally as `live_divergence`. A release ships its selections *and* the
  full candidate set and review history behind them, each line carrying
  chemical identity (SMILES/InChIKey) and level-of-theory/software provenance
  so a deposited file is interpretable offline.
- Selections may only name records at or above the `approved` review floor,
  checked on append and re-checked at publication; a release that selects
  nothing cannot be published.
- Public read surface: `GET /api/v1/scientific/releases`, `/{handle}`,
  `/{handle}/manifest` (with live re-verification), `/{handle}/selections`,
  `/{handle}/artifacts/{path}`. Curator-gated writes under `/api/v1/releases`.
- `CITATION.cff`, `SECURITY.md`, and this changelog.

### Changed

- **A calculation's citation is a paper on every upload root, not a row id on
  five of them.** `CalculationPayload.literature_id` is replaced by an inline
  `literature` fragment, resolved to a `literature` row by the workflow layer.
  The field was the calculation block of `/uploads/conformers`,
  `/uploads/transition-states`, `/uploads/statmech`, `/uploads/thermo` and
  `/uploads/transport` (and their `/jobs/*` twins). A depositor has a DOI, not
  our primary key: supplying one required having already queried this database.
  Resolution moves to `resolve_and_persist_calculation_with_results`, the one
  seam every upload root reaches, instead of being repeated in three
  workflows.

  **`tckdb-schemas` 0.32.0 → 0.33.0.** Breaking: `literature_id` no longer
  validates on those roots. `SchemaBase` is `extra="forbid"`, so an old payload
  gets a 422 naming the field rather than a 201 with the citation silently
  dropped. **No stored value changes** — `calculation.literature_id` is
  unaffected and no migration is involved.

  The same field had already been removed from the reaction bundle, the
  network-PDep route and `CalculationIn`, each time by hand, each time on one
  root. The reason it kept surviving is that the no-FK-ids invariant was
  asserted by exactly one test over exactly one upload root. That walker now
  runs over **every** upload root, discovered from the live route table so a
  new route is covered the moment it is registered
  (`backend/tests/schemas/test_upload_roots_expose_no_fk_ids.py`). Generalising
  it surfaced four further FK-shaped fields on depositor-facing surfaces
  (`SCFStabilityPayload.source_calculation_id` / `source_artifact_id`,
  `CalculationScanPointCreate.geometry_id`,
  `ReactionParticipantUpload.species_entry_id`); each is read by the server
  today, so each is frozen in a documented inventory with its reason rather
  than removed unreviewed.

- **Three codes a client could import but never receive are no longer
  exported.** `app/api/code_catalogue.py` gains a `Reach` field, so an entry
  can now record that no request produces it — the middle case of a three-way
  distinction that previously had no spelling: catalogued and client-facing (a
  caller can provoke it), catalogued and not client-facing (a real guard no
  request can trip), not catalogued at all (not a code). Before this, a guard
  could only be recorded by telling clients they might receive it, or by
  deleting the entry and leaving the next reader to rediscover the literal.
  `Reach` governs the client enum and nothing else; promotion
  (`MESSAGE_PREFIX_CODES`) deliberately does not consult it.

  **`tckdb-client` 0.39.0 → 0.40.0**, and three `RejectionCode` members are
  removed: `TRANSPORT_SOURCE_CALCULATION_OWNER_MISMATCH`,
  `APPLIED_ENERGY_CORRECTION_SOURCE_CALCULATION_OWNER_MISMATCH` and
  `IDEMPOTENCY_IN_PROGRESS`. Removing a member is breaking for an importer
  even when the code was unreachable, hence the minor bump. **No code changes
  value**, and all three keep a catalogue entry stating why no request reaches
  them. The two ownership guards stay in the code: against a bug five lines up
  they are a cheap tripwire, and in a database where a mis-attached
  calculation is a scientific error rather than a crash that is worth keeping.
  `idempotency_in_progress` is a contingency, not a milestone —
  `docs/specs/upload-idempotency-key-spec.md` lists it under *Optional* ("Only
  implement `idempotency_in_progress` if needed by the chosen approach") and
  the approach chosen does not need it; `app/api/idempotency.py` used to say
  "for v0", which does not distinguish a deferral from a decision, and now
  says which one it is.

- **A refusal that was recorded as unreachable is reachable, and is now
  tested.** `statmech_torsion_scan_calculation_owner_mismatch` was classified
  alongside the two above on the rule "an ownership guard is reachable exactly
  when the field it guards accepts a foreign row id". That rule is
  incomplete: a guard is also reachable when its key resolves in a namespace
  wider than the target's owner. `_persist_statmech_block` is shared by the
  species bundle, where the calc-key map is one species entry's own, and the
  PDep bundle, where it spans every species and transition state — and the
  PDep schema narrows a *species* statmech's keys to that species's own
  calculations but does not do the same for a *transition state*'s. A TS
  torsion naming a species-owned rotor scan therefore reaches the guard.
  Measured on the wire at `POST /api/v1/uploads/networks/pdep`; the code
  stays exported and `tests/api/test_api_network_pdep_ownership.py` provokes
  it, and the sibling `statmech_source_calculation_owner_mismatch`, through
  the route.

- **Two refusals stopped naming the function they were raised in.** The
  `detail` of the group-additivity missing-thermo guard and of the two keyset
  argument guards used to begin `create_applied_group_additivity: ` and
  `keyset_predicate: ` — the enclosing function in the position a client reads
  as a code. #164 stopped either being *promoted* into the `code` field; this
  reworks the messages themselves, so what a caller reads says what went wrong.
  Both entries are gone from `app/api/code_catalogue.py`, because a message
  with no token in the code position is not a code by any spelling.
  **No `RejectionCode` member is removed and `tckdb-client` is unchanged at
  0.38.0**: an `accidental_prefix` entry was never client-facing, so neither
  string was ever generated into the enum — checked with
  `generate_client_rejection_codes.py --check`, which reports the committed
  file up to date. Neither code was ever emitted, either: the runtime observer
  recorded 101 distinct `(status, code)` pairs across all three gates and
  neither appears. Both guards are reachable only by a direct programmatic
  call — `persist_thermo_upload` passes a row it has just flushed, so
  `session.get` cannot return `None` from any HTTP path — and the catalogue
  said otherwise, which is corrected.
- **The origin guard can now tell a code from a function of the same name.**
  `test_every_origin_still_defines_its_code` matched the code anywhere a
  double quote preceded it, so `"keyset_predicate"` in `__all__` satisfied the
  entry for `keyset_predicate` — the guard was blindest exactly where that
  class of defect lives, and #164 reworded that message with the guard staying
  green. Matching is now by syntactic position, shared with the catalogue's
  closure scan so the two cannot drift. That scan also reads `*_code=`
  arguments at any call, not only at a `raise`, which brings the six
  `*_handle_conflict` codes into static view (five of them are emitted by no
  test) and revealed one uncatalogued code, `database_error` — a dead
  `fallback_code` in the operational-error handler, now listed and annotated.
- `backend/pyproject.toml` now declares the repository's actual MIT license
  instead of `TBD — see repository root`.

### Notes

- **No DOI is minted.** The release machinery, manifest and checksums are
  implemented; depositing a release and recording its DOI is a documented
  manual step (`backend/docs/deployment/cutting_a_dataset_release.md`) to be
  run when a paper tag is cut. A DOI is not retractable and the corpus is not
  yet publishable.

## Earlier work

Before this file existed, changes were tracked only in the git history and in
`docs/decisions/`. Notable recent milestones, newest first:

- Stage 2 — scientific integrity blockers closed (#66).
- Execution-environment manifests recorded as provenance, not graded (#62, #64).
- Durable leases and heartbeats for async upload jobs.
- PDep scientific-integrity hardening: explicit pathway identity and solve
  inputs.
- Atom-resolved isotope identity for geometries and species entries.
- Raw artifact downloads gated behind authentication (ADR 0004, #48).
- Single-point energy and Cartesian Hessian extraction from uploaded ESS
  artifacts (#49, #51, #52).
- Lossless `tckdb.archive.v1` operator archive and restore path.
- Reproducibility assessments as an append-only curation projection.
