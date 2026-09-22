# Phase D verification and delivery record

Companions: [Implementation register](tckdb-phase-d-implementation-plan.md),
[Programme](tckdb-implementation-programme.md).

## Scope and baseline

Baseline `5988fbb890674eddb1819a164079612b0e79aff1`; migration head
`d2f4a7c1b8e6`; installed/locked Cantera 3.2.0. Implementation landed as
PR #518 and, after independent review round 2, is
merged to `main`; it has not been deployed to the hosted Pi. D0–D3 are
implemented; D7 verification results appear below. D4–D6 and normalized
experimental H/G remain held by the programme's reference-basis decision.
No first-paper claim or corpus freeze changes.

## What changed

D0/D2 repair the existing Cp runner's numerical and custody hashing, advance
its rubric to v2 without rewriting historical rows, preserve observation
public refs and uncertainty magnitude, and distinguish latest recorded from
live currency. Missing state/units/scalars, NASA gaps and nonfinite values
are explicit unavailable comparisons. No observations also produces an
unavailable finding. The existing representation preference and real-gas
`non_ideality: unquantified` behavior remain.

D1 compares every supplied NASA7/9 representation against exact Cp/S points,
s298, other stored representations and an explicitly named neighbour. D3
compares supplied opposite elementary rates with explicitly mapped thermo;
it retains every fit combination and cites inputs. Both use the existing
review persistence helper with informational findings and isolated recipes.

The new `backend/scripts/run_consistency_check.py` exposes explicit public-ref
invocation. Dry runs do not stage review rows. `--commit` appends one row;
service callers own their transaction. No API, UI, migration, background
trigger, preferred-record selection, trust or certification mutation exists.

## Independent review and actual mutations

Independent reviewer `/root/d_review` read the initial contracts, authored
`test_phase_d_numerical.py` and performed temporary mutations with restoration
in `try/finally`, checking production bytes after restoration. Expectations
use independent analytical formulas and SI constants, not the production
Cantera wrapper. All suites assert nonempty evaluated rows.

| Package | Actual mutation | Test that failed | Failures |
| --- | --- | --- | --- |
| D1 | Swap NASA7 low/high coefficient intervals | Analytical Cp/S/s298 case | 1 |
| D1 | Remove J/kmol/K → J/mol/K division | Full NASA7 and NASA9 polynomial cases | 2 |
| D1 | Give NASA9 shared boundary to lower interval | Boundary at 1000 K | 1 |
| D3 | Change m3/mol rate factor from 1000 to 1 | Independent equilibrium at three pressures | 3 |
| D1/D3 | Substitute 101325 Pa for supplied pressure | Reference-pressure preservation | 1 |
| D3 | Replace supplied reverse rate with thermo-derived reverse | Nonzero reverse disagreement | 1 |
| D0/D2 | Omit thermo numerical inputs from Cp hash | `test_every_material_cp_input_changes_live_currency` (NASA a1, a7, t_high) | 3 |
| D0/D2 | Omit custody parser version from snapshot | Same currency test, parser-version case | 1 |
| D0 | Remove runner filter from currency | `test_checks_and_rubrics_have_independent_currency` | 1 |
| D0 | Remove scientific-family filter from currency | Same isolation test | 1 |
| D0/D2 | Persist despite default dry run | `test_dry_run_and_engine_failure_never_stage_reviews` | 1 |

No mutation survived. The independent reviewer's final read-only pass found
no blocking issue in runnable D0–D3, including the nonfinite-input and
Cantera-inferred collider fixes. Reviewer execution logs for the second group were
written to `/tmp/phase_d_currency_mutation_evidence.txt`; this table retains
the reproducible changes and failing test names beyond that temporary file.
The parent also inspected all five failure logs and verified the restored files.

Review fixes: full hash inputs and scalar uncertainty; correct public
observation citations; no NASA9 gap extrapolation; preserved pressure;
isotope-bearing D3 records unavailable instead of falsely accepting elemental
balance; broken native-engine imports classified as configuration failures;
exact unavailable reasons retained; nonfinite inputs hash as explicit tokens
and never yield numerical residuals; direct Cp reads suppress autoflush.

A further parent probe showed Cantera can infer a third-body collider from
shared participants despite `is_third_body=False`. Such inferred reactions
are now unavailable. Independent third-order fixtures cover m6/mol2,
cm6/mol2 and cm6/molecule2 factors, in addition to the reviewer's first- and
second-order coverage.

## Verification runs

Commands run in `tckdb_env`, sequentially against the local development test
DB. The initial sandboxed DB run failed at connection setup (6 passed,
44 setup errors); this was not treated as scientific evidence. The required
resolver `backend/scripts/dev/ensure_test_db_port.py --apply` confirmed the
existing Docker database at port 5432; tests then ran with local socket access.

| Check | Result |
| --- | --- |
| Independent analytical suite, after six mutations restored | 35 passed |
| Cp/persistence/recipe before final review fixes | 50 passed |
| Cp/persistence/recipe plus independent numerical suite after fixes | 87 passed; 6 expected Cantera discontinuity warnings from synthetic persistence fixture |
| Restored persistence plus extra numerical cases | 31 passed; 6 synthetic-fixture Cantera warnings |
| First rest gate | 2 failed, 6188 passed, 31 skipped, 1 teardown error (241.80 s); see corrections below |
| Worker recovery isolated rerun | 1 passed (3.66 s), no worker-code change |
| `conda run -n tckdb_env bash backend/scripts/test-rest.sh` | 6190 passed, 31 skipped, 33 warnings (230.42 s) |
| `conda run -n tckdb_env bash backend/scripts/test-api.sh` | 3944 passed (343.10 s) |
| `conda run -n tckdb_env bash backend/scripts/test-scientific.sh` | 2905 passed (299.32 s) |
| Changed-file Ruff checks / `git diff --check` | Passed |
| Scientific check register generation | 39 entries |
| GitNexus `detect_changes(scope=all)` | 21 files, 18 touched symbols, zero reported processes, LOW; no error/partial/truncated flags |

Gate results are recorded as observed, including skips; no numerical test
with zero evaluated cases counts as acceptance. Numerical tolerances are
floating-point comparison tolerances, never chemical accuracy thresholds.

The first rest gate caught a paper-generator fixture that omitted phase and
reference pressure. Its inputs now explicitly record gas/1 bar; the existing
numerical and id-free assertions remain unchanged. A worker-recovery test
also raced a live worker and then failed its committed-row cleanup; it passed
unchanged in isolation and in the full rerun. Neither failure is silently
counted as a passing run.

Graph evidence uses the refreshed offline CLI because the MCP server kept an
older graph cached. The new shared encoder reports HIGH through its dependent
comparison modules. A name-only lookup for the paper test reported CRITICAL
without a concrete target; exact-identity lookup reported absent/UNKNOWN, and
source references confirmed pytest-only invocation. The index reports bounded
process enumeration, so zero reported affected processes is not proof that no
execution path exists. This analysis was completed before the implementing
implementing commit landed; the independent review round 2 that followed the
commit is recorded in the pull request, not re-run here.

## Limits and remaining holds

- D1 conservatively requires recorded gas phase for both Cp and entropy, but
  requires a recorded reference pressure only for entropy (decided
  2026-09-23, review round 2): a reference pressure cannot change a heat
  capacity, so Cp comparisons proceed without one while entropy comparisons
  still report it unavailable. Points are never interpolated or extrapolated.
- D2 keeps Phase C's NASA7 → NASA9 → point preference; use D1 to expose
  disagreement between fits.
- D3 requires explicit high-pressure-limit, direction and degeneracy
  conventions. Third-body, falloff, pressure-dependent, network-linked,
  isotope-specific and ambiguous effective rates are unavailable. It uses
  stored NASA standard-state coefficients, not reconstructed formation data.
- D3 evaluates at most 64 supplied fit combinations per invocation. No
  covariance or significance is inferred; supplied scalar uncertainty is
  reported separately from fit uncertainty, which remains absent.
- The optional package range still allows other Cantera versions; these
  runners enforce the lock's 3.2.0 at runtime and fail configuration checks
  before persistence when it is absent or different.
- Scientific records, trust, certification and selections are untouched.
  Machine-review status follows existing derivation; its `pass` token is
  not an accuracy or approval claim.
- D4 Gibbs, D5 Kirchhoff, D6 Hess and normalized experimental H/G comparisons
  remain held. Passing runnable packages does not complete all of Phase D.
