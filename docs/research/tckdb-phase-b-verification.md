# Phase B implementation evidence

Plan: [Phase B](tckdb-phase-b-implementation-plan.md). Programme:
[whole-programme plan](tckdb-implementation-programme.md). This records what
landed on `main` on 2026-09-19 for the work packages that are code, and what
remains for the ones that need the author. It is implementation evidence,
not a corpus, a deposit, or a reproduction.

## Method

Each work package ran as one worktree agent with a bounded brief, followed by
an independent reviewer on a different agent that read the diff, landed
deliberate mutations and reported which test each turned red, then the three
gate scripts run from the main session on the rebased branch, then CI, then a
squash merge and a mirror push. A review verdict of FAIL sent the branch
back; a fixed branch was re-reviewed before merge.

| PR | Work package | Review | Local gates (rest / api / scientific) |
| --- | --- | --- | --- |
| #495 | Phase A (prerequisite) | PASS, 12 mutations | 5,720 / 3,845 / 2,882 |
| #499 | Phase A rollout evidence | docs only | CI |
| #496 | B4a identity fixtures, CHEMKIN grid, selection replay | PASS, 5 findings fixed | CI (tests only) |
| #497 | B3 publication deposit | PASS, 6 findings fixed | 5,774 / 3,845 / 2,882 |
| #498 | B4b Hessian reanalysis, Arkane replay | FAIL then PASS on re-review | 5,829 / 3,845 / 2,882 |
| #500 | B1 rights attestation | PASS, 4 findings fixed | 5,918 / 3,870 / 2,882 |

Counts are the last full local run on each branch; the rebased final commit
of each branch also passed the CI gates before merge.

## What the reviews and gates caught

Every branch's targeted tests were green when handed back. The independent
review and the full gates found, and the branch then fixed:

- `tckdb_archive.py restore` had never worked against a freshly migrated
  database: two migrations write repair-ledger rows the restore guard treated
  as contamination. The in-process archive tests deleted those rows and never
  saw it. Fixed with a registry of migration-written tables (#497).
- The Arkane replay script could exit 0 having compared nothing: an
  unparseable Arkane output yielded an empty check set that counted as
  passing (#498, blocking).
- The Hessian bound's omega-squared term was a single-element estimate; the
  first-order per-mode sensitivity on the Gaussian fixture is 2.6 times wider.
  Replaced by a per-mode bound from each element's printed half-ULP (#498).
- The identity fixture README claimed a per-atom isotope check the service
  does not make; a misplaced-deuterium case is now pinned as a known
  limitation (#496).
- The exact-license rule had no test that a prefix match would fail; the
  bundle exporter invented a fragment for a partially attested selection;
  `REPRODUCE.md` step 0 printed three spaces and failed `sha256sum -c`
  (#500, #497).
- Full gates only: the error-catalogue closure guard (21 deposit codes), the
  generated API vocabulary document, the bundle-symmetry guard, the re-raise
  gate, the Alembic drift check, and the client parity ledger.

## Numbers measured, not claimed

- CHEMKIN grid over the RMG ammonia/methane fixture, Cantera 3.2.0, T in
  {300, 500, 800, 1000, 1500, 2000} K and P in {0.1, 1, 10} atm: 21 species
  with zero thermo deviation; 61 reaction groups over 64 rate terms compared,
  maximum forward-rate deviation 2.4e-5 relative at 0.60 of its derived
  bound; no unsupported forms and no export gaps in this fixture.
- Hessian reanalysis on `freq_g09.log`, rigid-body complement projection:
  30 modes recovered, maximum deviation 0.0067 cm^-1, worst mode at 39% of
  its omega-squared allowance. Without projection the 110 cm^-1 torsion is
  0.129 cm^-1 high. ORCA and Molpro fixtures exceed the bound by mass
  convention, as documented.
- Arkane ran end to end in `rmg_env` (rmgpy 4.0.0) against a scratch
  database seeded from test factories; no corpus-scale replay has run.
- Identity fixture set: 33 cases in six classes, all through the real
  resolution service.

## Rights gate, as landed

Every release selection and every publish now requires a standing
attestation on every submission linked to the record, with an exact
case-insensitive license match; the publish-time check also covers the
unselected candidates the release ships. The gate was proven live the honest
way: 82 existing release tests went red with `rights_basis_missing` until
their fixtures deposited and attested. Released NDJSON lines carry a `rights`
block; the manifest schema moved to v2 with v1 rows still reproducing their
recorded digests.

## Deployment

`main` at `a2c34447` (all of the above) was deployed to the self-hosted
instance with `tckdb_deploy.sh`: pre-deploy dump taken, revision
`a55cc983501a` upgraded to `9b1c7e2d4a68` (the attestation table and the
manifest's rights summary column; no backfill), container swapped, `status
ok`, `degraded []`. The arm64 image build hung once in the emulated smoke
test and succeeded on rerun, the behaviour the deployment notes already
record. This instance remains the author's playground, not the paper's
corpus.

## Remaining Phase B work

These need the author and are not started:

1. B2: build the publication instance, deposit the chosen ARC runs, SDF
   reactions and the RMG mechanism through the API, review, select, register
   a policy, publish, archive. Attest the historical playground submissions
   as `operator_own_data`.
2. B5: decide which `[DATA]` numbers the manuscript quotes from
   `corpus_counts`; the MCP tool count has no generator; add `doi:` to
   `CITATION.cff` only after the repository deposit.
3. The clean-container reproduction job, which needs a deposit built from a
   tagged commit.
4. `ReleaseManifest.git_commit` on top of manifest schema v2 (deferred from
   B3 until B1 merged).
5. Author accounts and ORCID/affiliation for `ACCOUNTS.md`.
