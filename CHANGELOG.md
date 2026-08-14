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
| `tckdb-schemas` | Alpha | Upload wire contracts; version bumped on every change |
| `tckdb-mcp` | Alpha | Agent integration surface |
| `tckdb-chemkin` | Alpha | Mechanism-export adapter |

The client is deliberately ahead of the backend: it is a thin, well-tested
wrapper over a contract that is itself still moving.

## Unreleased

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
