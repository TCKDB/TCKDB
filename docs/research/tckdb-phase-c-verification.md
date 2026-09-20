# Phase C implementation evidence

Plan: [Phase C](tckdb-phase-c-implementation-plan.md). Programme:
[whole-programme plan](tckdb-implementation-programme.md). This records what
landed on `main` between 2026-09-19 and 2026-09-20 for the nine work
packages that are code, and what remains for the two halves' demonstrations
and freeze-time steps. It is implementation evidence, not a corpus, a
deposit, or a reproduction.

## Method

Each work package ran as one worktree agent with a bounded brief, followed
by an independent reviewer on a different agent that read the diff, landed
deliberate mutations and reported which test each turned red. A review
verdict of FAIL, or a finding surfaced after an initial PASS, sent fixes
back onto the same branch for re-review. Five of the nine work packages
needed a second independent-review round before merge, and one (C-E5)
needed a third. Every merged commit passed CI (`Required gates`, `CI gate
coverage`, and the language/backend/client suites) before a single-parent
(squash) merge onto `main`.

| PR | Work package | Review | Local gates (rest / api / scientific) |
| --- | --- | --- | --- |
| #502 | Phase C plan (docs) | docs only | CI |
| #503 | C0 pilot species scan | FAIL, 8 findings (headline: fluoroethane's archived pressure is a per-point `Variable` at 1020-3400 kPa, not the fixed 101.325 kPa the plan first stated); fixed | n/a — script + its own pytest suite, 11 passed; no backend DB |
| #504 | C-Q1 QCSchema adapter core | FAIL, 6 findings (1 blocking: wall-clock `parameters_extracted_at` broke idempotency-key stability); fixed | n/a — client-side package; adapter suite 81 passed (up from 63) |
| #505 | C-E1 observation schema (revision `0b4a3afabfd3`) | no independent-review round recorded in the PR body | schema-only WP; targeted + full services/schemas/importers sweep, 4,357 passed; the three gate scripts not run |
| #508 | C-Q2 backend acceptance + Hessian read | no independent-review round recorded in the PR body | targeted + regression sweep 230 passed, client suite 1,511 passed; the three gate scripts not run in-PR — the main session records them green afterward except issue #506 |
| #507 | C-E2 ThermoML importer | FAIL, 1 blocking (`Component`/`Compound` joined by `nCompIndex` only; the real archive keys by `RegNum`/`nOrgNum`) + 10 more findings; fixed | main session's full local run: 6,034 / 3,870 / 2,882 (the 1 REST failure is issue #506) |
| #511 | C-Q3 export + round trip | first review FAIL (isotopes always exported as the standard nuclide, undetected by two mutations); PASS; a third pass found an uncoded crash on a D/T atom symbol, fixed | n/a — client-side package; adapter suite 113 passed (86 -> 102 -> 113 across the rounds) |
| #509 | C-E3 persistence | PASS; a second review tightened one finding (a dry run had uploaded the article to the object store anyway) | main session's full local run: 6,044 / 3,885 / 2,882 |
| #510 | C-E4 review check + generators | first independent review FAIL, HIGH (recording a Cp comparison restaled the thermo's current machine review); fixed; PASS; a third pass closed three LOW items | main session's full local run: 6,071 / 3,883 / 2,882 (plus two full-gate-only guards fixed after) |
| #513 | C-E5 read, rights, attach (revision `d2f4a7c1b8e6`) | first review PASS with 7 test-coverage gaps and 2 design changes; second review FAIL (CCCBDB-imported rows had no submission link, locking every one of them out of the new curator attach route); fixed; third review PASS | PR's own initial run: scientific 2,903 / API 3,901 / REST 5,964, 14 skipped (2 real failures caught and fixed before a clean re-run); main session's full run after the final round: 6,097 / 3,923 / 2,905 |

## What the reviews and gates caught

- WP0's pilot scan (#503) first reported fluoroethane at a fixed
  101.325 kPa; re-measured directly from the ThermoML block, its pressure
  is a per-point `Variable` with 30 distinct values from 1020 to 3400 kPa.
  The same review round restored three skip counters an early return had
  zeroed (an article whose only qualifying blocks were `sMethodName` had
  discarded all its counters), and the 26-species playground InChIKey list
  was committed (`docs/validation/thermoml_cp_pilot_scan_playground_inchikeys.txt`)
  so "1 of 26" is independently reproducible.
- C-Q1's mapper (#504) stamped `parameters_extracted_at` from
  `datetime.now(timezone.utc)`; because the backend hashes the whole
  canonical request body per idempotency key
  (`backend/app/api/idempotency.py:304-309`), building the same document's
  payload twice produced two different bodies and a rerun never replayed.
  The same review found `OptimizationResult`'s `provenance.creator` (the
  optimizer, geomeTRIC) had been recorded as the ESS software release, and
  `provenance.username` retained verbatim into `parameters_json`.
- C-E2's importer (#507) joined `Component`/`Compound` by `nCompIndex`
  only; the real ThermoML archive keys every Cp block by `RegNum`/`nOrgNum`
  — confirmed by downloading and inspecting the pinned archive directly.
  Ten more findings followed: ambiguous block-level `PhaseID` fallback,
  `eStandardState` not rejected, an empty per-value uncertainty crashing
  the schema's own CHECK instead of being rejected, an inexact kPa-to-bar
  division (`* 0.01` vs `/ 100.0`), and a shared mutable rule accumulator
  leaking one row's mapping rules into another's.
- C-E3's persistence service (#509) uploaded a dry run's article bytes to
  the object store even though nothing landed in the database — reproduced
  against local MinIO, confirming `head_artifact_object` returned a real
  object after a `commit=False` call.
- C-E4's Cp-comparison runner (#510) restaled a thermo's current machine
  review: `get_record_machine_review_currency_for_record` read every
  `record_machine_review` row for `(record_type, record_id)` with no
  `provider` filter, so the newer Cp-comparison row (always newest by
  `reviewed_at`) was classified as latest and read as `stale`, demoting the
  true reviewer review to historical. Fixed by
  `MachineReviewRecordFamily` (`backend/app/services/machine_review/query.py:63`),
  which scopes every currency/latest-row read to one family; `reviewer` is
  the default every existing call site keeps unchanged.
- C-Q3's exporter (#511) always exported `mass_numbers` as each element's
  standard tabulated nuclide, even for a recorded deuterium — undetected by
  two mutations against the round-1 suite (emitting `2` for every H,
  emitting a literal wrong array both left 102 of 102 tests passing). A
  third pass found the fix itself crashed uncoded
  (`qcelemental.exceptions.NotAnElementError`) on a `D`/`T` atom symbol.
- C-E5's curator attach (#513) refused every CCCBDB-imported observation:
  the CCCBDB importer never opened a `Submission`, so the whole CCCBDB
  backlog had no `submission_record_link` for the attach route's
  precondition to find. Fixed by giving the CCCBDB importer the same
  submission-wiring the ThermoML importer already had
  (`backend/app/services/cccbdb_molecular_property_import.py`).
- Recurring lesson, held on every PR this phase: targeted tests were green
  while a full-gate-only guard was red. #513's initial full-gate run alone
  caught two real failures a targeted run had missed
  (`test_client_rejection_codes_generated.py` and
  `tests/services/test_record_refs.py`, both fixed before a clean re-run).
  A refusal message built as `"observation_identity_hint_conflict: this
  observation's own ..."` (`backend/app/services/observation_identity_attach.py:191`)
  is exactly the shape — a message beginning with a `snake_case: ` token —
  the error-catalogue closure guard treats as a declared code. Across the
  phase, the row-id-in-user-text guard, the runtime-ASCII audit, the
  version-bump `PACKAGES` list, the OpenAPI golden, the client parity
  ledger, and the API vocabulary doc each caught something a module-scoped
  pytest run could not.

## What landed

### C0 — pilot species scan (#503)

A read-only scan of NIST's pinned ThermoML bulk archive
(`ThermoML.v2020-09-30.tgz`, SHA-256
`231161b5e443dc1ae0e5da8429d86a88474cb722016e5b790817bb31c58d7ec2`,
189,433,115 bytes) found zero of the 26 playground species with
single-component, ideal-gas-phase Cp data among 11,923 articles; methane's
only archive appearance is inside multi-component natural-gas mixtures,
excluded by the single-component requirement. A broadened check found 13
archive compounds at "Ideal gas" phase and 9 at "Gas", all free-text
`sMethodName` entries (never `eMethodName`); fluoroethane was the
best-covered real "Gas"-phase candidate (38 points, 315.33-365.75 K,
DOI `10.1016/j.fluid.2016.07.034`), but its pressure, re-measured directly
from the block, is a per-point `Variable` of 30 distinct values from 1020
to 3400 kPa — reduced temperature 0.84-0.97 against its 375.31 K critical
temperature, i.e. real-gas, not ideal. The plan's C0 section records the
pilot as re-decided (Calvin, 2026-09-20) to **benzene**
(`UHOVQNZJYSORNB-UHFFFAOYSA-N`), an "Ideal gas" entry evaluated by
statistical thermodynamics (DOI `10.1016/j.jct.2013.08.022`, 12 values,
200-1000 K, 100 kPa constraint). Evidence:
`backend/scripts/validation/thermoml_cp_pilot_scan.py`,
`docs/validation/thermoml_cp_pilot_scan.md`, and the committed
26-species playground InChIKey list.

### C-Q1 — QCSchema adapter core (#504)

New client-side package `clients/python/adapters/qcschema/`
(`tckdb-qcschema`, pinned `qcelemental==0.51.2`, version 0.1.0 -> 0.2.0
across the review fixes), mirroring `tckdb-chemkin`: `reader.py` (family
dispatch — a document is v2 iff it carries a top-level `input_data`,
checked against its own `schema_name`/`schema_version` pair, never the
version integer alone), `molecule.py`, `hessian.py` (packs the Hessian's
lower triangle in the row-major order of
`backend/app/services/hessian_parsing.py:417`), `mapping.py`, `uploader.py`,
`cli.py`. 81 tests pass after the review fixes (up from 63); fixtures
include real Psi4/qcengine jobs on water at HF/STO-3G (energy, gradient,
Hessian, optimization, each in both wire families) run in a separate
pinned `tckdb_qcschema_psi4` conda environment, plus hand-derived
ghost-atom, two-fragment, non-standard-mass and family-drift cases. CI
gained a fourth step in `.github/workflows/python-client-ci.yml:105`
(`python -m pytest adapters/qcschema/tests`).

### C-Q2 — backend acceptance and Hessian read (#508)

Confirms the adapter's real mapper output is accepted by the live upload
routes: `backend/tests/api/test_api_qcschema_fixtures.py` posts all 8 route
fixtures (energy/gradient/Hessian/optimization, families v1 and v2) and
checks the stored rows, provenance, and idempotent replay (10 passed).
Adds `GET /calculations/{id}/hessian`
(`backend/app/api/routes/calculations.py:271-272`, 404 `hessian_not_found`
without a stored matrix), a client method (`tckdb-client` 0.85.0 ->
0.86.0), the OpenAPI golden, and the parity ledger.
`normalize_software_name("Psi4")`/`("geomeTRIC")` were confirmed
empirically to need no alias-table change — uploading all 8 corpus
documents in one transaction produces exactly one `Software` row named
`Psi4` and one `WorkflowTool` row named `geomeTRIC`.

### C-Q3 — export and round trip (#511)

`exporter.py` turns a stored `sp` or `freq`-with-Hessian calculation back
into a v2 `AtomicResult` (`export_calculation`, `exporter.py:535`); `opt`
is refused (`export_unsupported_type`) because TCKDB never stores the
optimization trajectory. The importer now refuses to re-import its own
export (`tckdb_export_reimport_refused`,
`clients/python/adapters/qcschema/tckdb_qcschema/reader.py:68`), so an
export can never read back as independent evidence of a second job. The
stated geometry bound is derived, not guessed: 0.5e-10 Å from the import
side's `"%.10f"` formatting, carried through the same `BOHR_TO_ANGSTROM`
constant to ~9.4486e-11 bohr on export; a third, unbudgeted rounding —
qcelemental's own `Molecule` constructor defaults to 8-decimal-place
geometry noise on construction — is suppressed with an explicit
`geometry_noise=14`. The complete, asserted loss set is `provenance`,
`keywords`, `extras`, `id`, `wavefunction`, `native_files`, `stdout`,
`stderr`. Two review rounds fixed: isotopes always exported as the
standard nuclide even when the stored geometry recorded a non-standard one
(now read from the legacy `GET /geometries` surface and cross-checked
element-for-element against the scientific-read atoms —
`_HYDROGEN_ISOTOPE_SYMBOLS = {"D": 2, "T": 3}` at `exporter.py:211`,
mirroring `backend/app/chemistry/isotopes.py`), and an uncoded crash on a
`D`/`T` atom symbol. Adapter suite: 113 passed (86 before the isotope
fix, 102 after it); version 0.2.0 -> 0.3.0 -> 0.4.0.

### C-E1 — observation schema (#505)

New Alembic revision `0b4a3afabfd3` (down from `9b1c7e2d4a68`), both
directions implemented. New columns on `molecular_property_observation`
(`backend/app/db/models/molecular_property_observation.py:151`
`pressure_bar`, `:152` `state_basis`, `:134` `uncertainty_kind`, `:224`
`external_source_record_id`), all nullable, no backfill; seven new CHECK
constraints; new tables `external_source`/`external_source_record` for
source custody, both added to the archive's `INCLUDED_TABLES`.
`molecular_property_kind` gains `heat_capacity_cp`; `submission_record_type`
gains `molecular_property_observation` (reviewable, not
release-selectable, not supersession-eligible). One same-transaction
Postgres restriction — a CHECK referencing the new enum value immediately
after `ALTER TYPE ... ADD VALUE` — is handled inside
`op.get_context().autocommit_block()`. Targeted tests plus a full
services/schemas/importers sweep: 4,357 passed.

### C-E2 — ThermoML importer (#507)

New read-only package `backend/app/importers/thermoml/` (pinned archive
URL, SHA-256, size, and XSD SHA-256 in
`backend/app/importers/thermoml/__init__.py:45,57,62,80`; `archive.py`,
`validate.py` using lxml with entity resolution and network disabled,
`parser.py`, `mapping.py`). Produces `MolecularPropertyObservationCreate`
payloads and a mapping report; writes nothing to the database. Real-archive
CLI runs against four DOIs are recorded in the PR: benzene 12 payloads,
fluoroethane 38, ferrocene+nickelocene 122 (61 each), water 1, each with
pressure source/range, uncertainty kind, and origin. An `sMethodName`
allowlist (rule `origin.smethodname_allowlist.v1`) maps exactly two
observed free-text strings ("statistical thermodynamics",
"Statistical thermodynamic calculations") to `computed`; every other
free-text method string is rejected, not guessed. 76 tests total: 59
fixture-based, 17 opt-in real-archive smoke tests gated behind
`TCKDB_THERMOML_ARCHIVE` (skipped, not silently passed, when unset).

### C-E3 — ThermoML persistence (#509)

`backend/app/services/thermoml_cp_import.py::import_thermoml_cp_article`
(`:407`) validates, parses, maps, gets-or-creates the custody rows,
resolves literature and identity, opens one
`Submission(source_kind=bulk_import)` with a `source_terms` rights
attestation carrying NIST/TRC's terms text verbatim
(`_open_submission_with_source_terms_attestation`, `:557`) only when at
least one row will newly insert, then inserts with a savepoint per row and
`ON CONFLICT (mpo_dedupe_key) DO NOTHING`. The CCCBDB importer's identity
resolver was extracted to a shared
`backend/app/services/external_observation_identity.py`
(`ground_state_minimum_entries_for_species` at `:60`, `resolve_identity`
at `:91`) so both importers share one resolution rule instead of two
copies that can drift. A second review round found the dry-run path still
wrote the article's raw bytes to the object store; fixed to compute the
content-addressed key locally without contacting the store, surfaced to
the caller as a `would_store` warning.

### C-E4 — review check and generators (#510)

`backend/app/services/external_comparison/cp.py` is the first check
declared in `CheckTier.review`
(`backend/app/scientific_checks/external_comparison.py:33`): for a
computed thermo with a NASA-7, NASA-9 or point representation and
same-entry `state_basis=ideal_gas` observations, it evaluates Cp at each
observed temperature with Cantera (lazy import) and writes one
`record_machine_review` row (`run_and_record`, `cp.py:514`) carrying the
residual, the observation's typed uncertainty, and — for real-gas rows —
an explicit `non_ideality: unquantified` flag; no threshold, no status
change, no selection effect. The independent review's HIGH finding —
recording a comparison silently restaled the thermo's true current
reviewer review — is fixed by `MachineReviewRecordFamily`
(`backend/app/services/machine_review/query.py:63`, provider constant
`SCIENTIFIC_CHECK_PROVIDER` at `:60`), which scopes every currency/
latest-row read to one family; `reviewer` is the default every existing
call site keeps unchanged with no code edit. Two generators
(`experimental_cp_comparison`, `thermoml_source_provenance`) feed the
manuscript, byte-stable and id-free.

### C-E5 — read route, rights, attach (#513)

`GET /api/v1/scientific/species-entries/{id}/observations`
(`backend/app/api/routes/scientific/species_subresources.py:203`, service
`backend/app/services/scientific_read/observations.py`) is the first
public route to read `molecular_property_observation` at all; identity is
resolved by construction, so an identity-unresolved row (nullable
`species_entry_id`) is structurally unreachable. New Alembic revision
`d2f4a7c1b8e6` (down from `0b4a3afabfd3`; confirmed the current head at
`origin/main`) adds a `mpo_`-prefixed public ref. The rights gate the plan
pointed at (`_assert_records_have_rights_basis`) turned out not to be the
real gap — observations are not release-selectable and never enter a
release's candidate set — so the actual fix is
`_assert_observations_have_rights_basis`
(`backend/app/services/deposit/build.py:610`, called from `write_deposit`
at `:763`), scoped to observations whose `species_entry_id` is one of the
release's own subject entries. A curator-only
`POST /api/v1/admin/observations/{ref}/identity`
(`backend/app/services/observation_identity_attach.py:221`) may name any
ground-state-minimum entry of the observation's resolved species once; a
second attach is refused (supersession, ADR 0003); a hint/target InChIKey
connectivity mismatch is refused
(`_assert_no_identity_hint_conflict`, `:165`); an observation with no
submission link is refused (`observation_identity_attach_requires_submission`).
The phase's one blocking finding in its final review round was that the
CCCBDB importer never opened a `Submission`, so every CCCBDB-imported row
failed that last precondition; fixed by giving CCCBDB the same
submission-wiring the ThermoML importer already had. `tckdb-client`
reached 0.87.1 across this PR's rounds (a typed `get_species_observations()`
method, then one new rejection code for the hint-conflict refusal).

## Decisions taken

- The pilot species is re-decided from fluoroethane (real-gas, high
  pressure, close to its critical temperature) to **benzene** (an
  ideal-gas entry evaluated by statistical thermodynamics), decided by
  Calvin on 2026-09-20 and recorded in the plan's C0 section.
- A curator attach may name **any** ground-state-minimum entry of the
  observation's resolved species, not only a unique one — the curator's
  choice of which entry is itself the isomer disambiguation the automatic
  resolver refuses to guess at (#513, finding F4).
- The curation fact from an attach is recorded as a `SubmissionAuditEvent`
  (`observation_identity_attached`), never by mutating
  `raw_payload_json` — under the four-role model, provenance rows are
  append-only and a later curation act must not rewrite them (#513,
  finding F5).
- A hint-connectivity conflict refuses the attach: if the observation's
  own recorded identity hint names an InChIKey connectivity block
  different from the target species's own, the attach is refused
  (`observation_identity_hint_conflict`); only the connectivity block is
  compared, so a stereochemistry-only difference is still accepted, since
  resolving that is what the tool is for (#513, Probe C).

## Deviations from the plan

- The plan's C0 section stated fluoroethane's points were at a fixed
  101.325 kPa; measured directly from the archive, they are a per-point
  `Variable` at 1020-3400 kPa, real-gas and near the compound's own
  critical temperature. The pilot moved to benzene as a result.
- The plan's C4 section originally read "every stored machine review
  restales once and the plan says so" — the independent review found this
  backwards: registering a rubric in `_ACTIVE_RUBRICS` restales nothing by
  itself; the runner's own appended row, read by a currency check with no
  family filter, was the actual mechanism. The plan text was corrected and
  dated as a review-round-2 correction rather than silently rewritten.
- The plan's C3 section said the persistence service imports "no
  parser/fetcher" from the importer package; #509's review corrected this
  to what is actually true — the service does import the parser, mapper
  and validator (pure functions, no I/O); it only avoids the archive
  fetcher and any per-family upload workflow module.
- The plan pointed the curator-attach ambiguity rule at
  `external_observation_identity.py` as an existing precedent; no such
  module existed on `main` when #513 (C-E5) started, since the CCCBDB
  resolver had not yet been extracted there. #513 defined the rule
  directly and reconciled it once #509 (C-E3) landed the real extraction,
  mid-review, via a rebase.
- The plan's C-E5 brief pointed the rights gap at
  `_assert_records_have_rights_basis` (the release-selection gate); the
  actual gap was in the deposit builder (`write_deposit`), because
  observations are not release-selectable and that gate structurally
  cannot see one.
- The CCCBDB importer's own submission-wiring (opening a `Submission` per
  run, linking every inserted row) was not named in any Q or E work
  package; it was added inside #513 as the fix for the phase's one
  blocking finding in its final review round, and left a backfill gap for
  rows written before the change (issue #514).
- The plan's mapping table named no refusal code for a non-integer
  QCSchema `molecular_charge`/`molecular_multiplicity`; #504 introduced
  `non_integer_identity` and flagged it in the PR for confirmation before
  C-Q2/C-Q3 built on the same convention.

## What needs the author

1. The Psi4 run for the C-Q4 demonstration (water at the Gaussian
   geometry) — C-Q4 itself (validation script, registry entry, validation
   doc, decision record) has not started; it depends on C-Q3 (landed) and
   this run.
2. A computed benzene thermo record (an ARC B3LYP/def2-TZVP opt+freq,
   author-run) for the Cp comparison demonstration — the C0 pilot decision
   names it as needed and none exists yet.
3. The curator judgement on the ThermoML journal-permission clause
   ("available here with permission of the journal publishers") before any
   ThermoML-derived observation ships in a citable release — C-E3 (#509)
   records the terms text verbatim on the attestation but does not make
   this judgement.
4. The Pi row count for issue #514 (legacy CCCBDB observation rows written
   before submission linking, now refusing a curator identity-attach) —
   the main session's attempt to run that count was blocked by the
   permission classifier.
5. Corpus curation, deposit building and the manuscript's `[DATA]` numbers
   remain freeze-time steps, executed once after development is declared
   finished — stated here once, not carried forward as a next step.

## Rollout

<!-- ROLLOUT: filled by the main session after the Pi deploy of 2f7f23b2 (revisions 0b4a3afabfd3 and d2f4a7c1b8e6) -->

| Step | Evidence |
| --- | --- |
| Pre-deploy dump | |
| Migration upgrade to `d2f4a7c1b8e6` | |
| Container swap | |
| `status` | |
| `degraded` | |
