# Phase D verification and delivery record

Companions: [Implementation register](tckdb-phase-d-implementation-plan.md),
[Programme](tckdb-implementation-programme.md).

## Scope and baseline

D0–D3: baseline `5988fbb890674eddb1819a164079612b0e79aff1`; migration head
`d2f4a7c1b8e6`; installed/locked Cantera 3.2.0. Implementation landed as
PR #518 and, after review round 2, is merged to `main`.

D4–D6: the shared foundation (#549), D4 Gibbs self-consistency (#551), D6
Hess (#550) and D5 Kirchhoff (#552) are merged to `main` at `15079bbc`. The
record below was verified against `main` at `2a32f694`, which adds #555
and leaves the consistency code unchanged. Migration head is `e7b1c9d4a632`
and Cantera is 3.2.0. The checks compare only records that declare
`enthalpy_reference_kind`. Since #555 the thermo upload requires that
declaration for every enthalpy, a stored Gibbs energy included.

D0–D6 are implemented. The true Gibbs energy of formation (ΔfG) check,
formation-increment Kirchhoff and normalized experimental H/G comparisons
remain held on element reference data (see [Limits and remaining
holds](#limits-and-remaining-holds)). No first-paper claim or corpus
freeze changes.

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

## Independent review and actual mutations (D0–D3)

An independent review read the initial contracts, authored
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

No mutation survived. The final read-only review found no blocking issue in
runnable D0–D3, including the nonfinite-input and Cantera-inferred collider
fixes. This table records the reproducible changes and failing test names.

Review fixes: full hash inputs and scalar uncertainty; correct public
observation citations; no NASA9 gap extrapolation; preserved pressure;
isotope-bearing D3 records unavailable instead of falsely accepting elemental
balance; broken native-engine imports classified as configuration failures;
exact unavailable reasons retained; nonfinite inputs hash as explicit tokens
and never yield numerical residuals; direct Cp reads suppress autoflush.

A further probe showed Cantera can infer a third-body collider from
shared participants despite `is_third_body=False`. Such inferred reactions
are now unavailable. Independent third-order fixtures cover m6/mol2,
cm6/mol2 and cm6/molecule2 factors, in addition to the review's first- and
second-order coverage.

## Verification runs (D0–D3)

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
commit landed; the independent review round 2 that followed the
commit is recorded in the pull request, not re-run here.

## D4–D6: independent mutations (2026-09-27)

D4 Gibbs self-consistency (#551), D5 Kirchhoff (#552) and D6 Hess (#550)
rest on the shared foundation (#549). Their pull requests list the
mutations run while they were built. Those lists are not repeated here.
This record covers a separate pass that chose its own mutations, mostly
ones the pull requests did not list, and ran each one.

Each mutation is one textual change to production code. The change was
applied alone, the nine Phase D modules (`backend/tests/services/test_phase_d_*.py`)
were run, and the file was restored and its SHA-256 compared with the
original. A mutation is killed when at least one test fails. At `15079bbc`
the suite had 333 tests before the additions below and 343 after. After
rebasing onto `2a32f694` it has 344, and every mutation that first
survived, plus V1.1, was run again there and failed the same tests. Line
numbers refer to `15079bbc`, except the #554 guard, which is new.

| ID | Change (file:line) | Failing tests | Result |
| --- | --- | --- | --- |
| V4.1 | `gibbs.py:168` every stored G read from the first eligible point | 1 (D4-4) | killed |
| V4.2 | `gibbs.py:101` point source reads h and s from the record's first point | 1 (D4-4) | killed |
| V4.3 | `gibbs.py:178` float-precision bound omits T·\|s\|/1000 | 0, then 1 (D4-1) | survived; test fixed |
| V4.4 | `gibbs.py:88` scalars read for a G within 1 K of 298.15 K | 0, then 1 | survived; test added |
| V4.5 | `gibbs.py:123-128` basis checked before species scope | 0, then 1 | survived; test added |
| V4.6 | `gibbs.py:190` species entry dropped from the input hash | 0, then 2 | survived; tests added |
| V4.7 | `gibbs.py:171` non-finite stored G guard removed | 2 | killed |
| V5.1 | `kirchhoff.py:348` integral taken from T to T, so T is used for both ends | 8 | killed |
| V5.2 | `kirchhoff.py:204` T0 is the lowest common grid temperature, not 298.15 K | 1 | killed |
| V5.3 | `kirchhoff.py:106` pair gate reads only the left record's own reasons | 0, then 1 | survived; test added |
| V5.4 | `kirchhoff.py:243` boundary-jump sign reversed | 4 | killed |
| V5.5 | `kirchhoff.py:289` a fit compared with its own integral | 12 | killed |
| V5.6 | `kirchhoff.py:367` requested temperature grid dropped from the input hash | 0, then 1 | survived; test added |
| V5.7 | `kirchhoff.py:317` same-record anchors labelled `assumed` | 1 | killed |
| V6.1 | `hess.py:288` shared-basis check skipped whenever a participant has a NASA fit | 0, then 1 | survived; test added |
| V6.2 | `hess.py:320` stoichiometric coefficient sign reversed | 13 | killed |
| V6.3 | `hess.py:339` participant slots dropped from the input hash | 1 | killed |
| V6.4 | `hess.py:303` combination cap refuses a count equal to it | 0, then 1 | survived; test added |
| V6.5 | `hess.py:207` h298 uncertainty lent to fit and point terms | 1 | killed |
| V6.6 | `hess.py:271` participant species-scope gate removed | 3 | killed |
| V6.7 | `hess.py:125` transition-state-on-another-reaction-entry gate removed | 1 | killed |
| VF.1 | `engine.py:292` h298 accepted within 1 K (#549 M-h298-inexact) | 2 | killed |
| VF.2 | `stoichiometry.py:114` products counted as unique species, not slots (#549 MS.1a) | 59 | killed |
| V1.1 | `thermo.py:15-16` the #554 self-neighbour guard removed | 2 | killed |

Eight mutations survived the first run. None of them exposed a production
defect: the code already did the right thing, but no test would have
noticed if it stopped. Each now fails a test:

- **V4.3.** D4-1 compared the reported bound with
  `pytest.approx(..., rel=1e-12)`. That form still applies the default
  absolute tolerance of 1e-12, and the bound is itself about 1e-12, so the
  assertion could not fail. It now passes `abs=0`.
- **V4.4.** A G at 298.0 K next to 298.15 K scalars gets its point row and
  one record-level scalar row. It gets no scalar row at 298.0 K.
- **V4.5.** An undeclared ion with no recorded phase is reported as out of
  scope. An undeclared neutral species is reported as
  `enthalpy_reference_unrecorded`, and once declared, as `phase_not_recorded`.
- **V4.6.** Changing an entry's `isotope_key` or a species' charge makes a
  stored D4 review stale.
- **V5.3.** A same-entry neighbour whose phase was never recorded makes
  every anchor `phase_not_recorded`, in either order. Tabulated points pass
  through no engine gate, so only the pair gate can catch this.
- **V5.6.** A D5 review recorded on one `--temperature` grid is stale for a
  request on another.
- **V6.1.** An undeclared participant that has only a NASA fit is refused as
  `enthalpy_reference_unrecorded`.
- **V6.4.** A record holds at most one fit, so four participants give 16 or
  81 combinations and never exactly 64. The test lowers the cap to 16:
  16 combinations are evaluated, and a cap of 15 refuses them.

## D4–D6: end-to-end runs through the CLI

A scratch database (`tckdb_test_wpv_smoke`, `alembic upgrade head` to
`e7b1c9d4a632`) was filled by the workflow functions behind the upload
routes: `persist_thermo_upload`, `persist_transition_state_upload` and
`persist_kinetics_upload`. It holds:

- a declared CH4 record with h298, s298, a NASA-7 fit (GRI-Mech 3.0 shape
  with ΔfH re-anchored to −74.6 kJ/mol) and points at 298.15 K and 1000 K;
  the 1000 K point's h is 0.5 kJ/mol above the fit and its G 0.2 kJ/mol
  above its own H − TS;
- a C2H6 record whose only point carries S and G, deposited declared and
  then cleared of its declaration in the database, because the upload now
  refuses an undeclared G (#555); this is how a row from before the rule
  reads;
- a declared OH⁻ record;
- CH4 + OH → CH3 + H2O, forward, with a tunneling row (ΔE = −54.6 kJ/mol,
  `thermal_enthalpy_298k`, separated reactants) and four declared thermo
  records (D6-1), plus the same reaction deposited with direction `reverse`.

`backend/scripts/run_consistency_check.py` ran against it, with trimmed
output below. Each eligible case ran as a dry run and then with `--commit`.
Both gave the same `context_hash` and findings. A refused case exits 0: a
refusal is a finding, not an error.

```text
--check gibbs-self --target-ref <CH4>                  5 findings
  point     298.15  residual 0.0     within_float_precision true
  nasa7     298.15  residual 0.0209
  scalar298 298.15  residual 0.0
  point     1000.0  residual 0.2000  within_float_precision false
  nasa7     1000.0  residual 1.4788
--check gibbs-self --target-ref <C2H6, undeclared>     1 finding
  point     500.0   reason enthalpy_reference_unrecorded
--check gibbs-self --target-ref <OH-, declared>        2 findings
  point     500.0   reason charged_species_out_of_scope
  scalar298 298.15  reason charged_species_out_of_scope
--check kirchhoff --target-ref <CH4>                   9 findings
  anchor h298-point   298.15 residual 0.0 ; 1000.0 reason no_exact_h298_value
  anchor h298-nasa7   298.15 residual 0.0 ; 1000.0 reason no_exact_h298_value
  anchor point-nasa7  298.15 residual 0.0 ; 1000.0 residual 0.5000
  increment point vs I(nasa7) 298.15 -> 1000.0 residual 0.5000
  boundary_jump nasa7 t_mid 1000.0 residual 7.1e-15
--check kirchhoff --target-ref <OH-, declared>         4 findings
  anchor h298-point 298.15, 500.0 reason charged_species_out_of_scope
  increment reason no_comparison_pairs_or_temperatures
--check hess --target-ref <kin, forward> --thermo spe=thm x4        6 findings
  {"delta_e_claim_kj_mol": -54.6, "delta_h_formation_kj_mol": -57.40000000000003,
   "residual_kj_mol": 2.8000000000000256, "temperature_k": 298.15,
   "terms": [[-1,"h298",-74.6,0.1],[-1,"h298",37.4,0.2],[1,"h298",147.2,0.3],[1,"h298",-241.8,0.04]],
   "element_reference_compilation": "not_recorded; cancellation across terms assumed, unverifiable",
   "endpoint_identity": "not_recorded; separated species assumed", "uncertainty_propagation": null}
--check hess --target-ref <kin, reverse> --thermo spe=thm x4        6 findings
  reason tunneling_orientation_not_declared
```

The residuals match their hand values: the seeded 0.2 and 0.5 kJ/mol, D6-1's
+2.8 kJ/mol, and zero where the fit, scalars and point were anchored to the
same number. The D4 NASA-7 rows differ from the point rows because the fit's
entropy is not the point's.

**No scientific table is written.** Before and after each of the ten runs,
every table in the public schema (131) was read for its row count and an MD5
digest of its full row text. Dry runs and refused cases changed nothing.
Each `--commit` changed exactly one table, `record_machine_review`, by one
row (0 → 1 → 2 → 3). No other table's count or digest moved.

## D4–D6: #554 and gates

D1's `compare_thermo` now refuses a record as its own neighbour, with the
message D5 uses: "a thermo record cannot be its own neighbour". This covers
the same object and a second object with the same public ref. Tests cover
the direct call and the service path, which stages nothing. The existing
D1 tests are unchanged and pass. Eight D1 scenarios ran through the
function at `15079bbc` and through the fixed one, and all eight gave
byte-identical findings and input hashes: single record, explicit grid,
NASA-7/NASA-9/Wilhoit, same-entry neighbour, differing and NaN pressures,
a different entry, and unrecorded phase. The self-neighbour case was
accepted before the fix (10 findings) and is refused after it.

The gates ran one at a time on the branch after rebasing onto `2a32f694`.
The object store was a throwaway SeaweedFS started with CI's image, flags
and environment, so the live-store tests ran rather than skipped:

| Check | Result |
| --- | --- |
| `conda run -n tckdb_env bash backend/scripts/test-rest.sh` | 6652 passed, 31 skipped |
| `conda run -n tckdb_env bash backend/scripts/test-api.sh` | 4021 passed |
| `conda run -n tckdb_env bash backend/scripts/test-scientific.sh` | 2936 passed |
| Phase D modules (`tests/services/test_phase_d_*.py`) | 344 passed |
| `ruff check app tests scripts`, `check_runtime_ascii.py`, scoped `mypy` (196 files) | clean |
| `generate_scientific_check_register.py --check`, `test_gate_coverage.py` (31) | up to date, passed |
| `mkdocs build --strict` | built without warnings |

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
- D4–D6 compare only records that declare `enthalpy_reference_kind`. An
  undeclared record is `enthalpy_reference_unrecorded` and no basis is
  inferred. Since #555 such a record cannot be newly deposited, so only
  rows from before the rule are excluded this way. On the playground instance, measured read-only on 2026-09-27,
  no record is eligible yet: none of its 65 thermo rows declares its
  enthalpy reference, and none of its 17 kinetics records carries a
  tunneling row with reaction energies. The checks apply to new declared
  deposits.
- D4's G is the record's own H(T) − T·S(T) on the `formation_298k` zero, not
  a Gibbs energy of formation. Only the same-row point source is an
  identity with a float-precision flag. Fit and scalar sources report
  residuals only and state that one standard pressure is assumed across
  representations. Wilhoit is not evaluated.
- D5 covers one species: one record, or two records of one species entry.
  It never bridges a NASA-9 gap and never compares a record with itself.
  Reaction-level Kirchhoff is not implemented.
- D6 needs a forward kinetics record with a traceable, unsolvated tunneling
  row on a separated-species zero, one explicit thermo record per
  participant, and a balanced reaction. It never searches for a cycle,
  closes a barrier, combines uncertainties or recommends a value. Every
  finding states that element-reference cancellation and separated
  endpoints are assumed.
- **Held: the true Gibbs energy of formation (ΔfG) check**, together with
  formation-increment Kirchhoff and ΔfH(0 K) ↔ ΔfH(298.15 K) conversion.
  These are unblocked by element reference data: a cited, versioned
  element-reference compilation giving element entropies and enthalpy
  increments with temperature, phase transitions and its stated standard
  pressure; a per-record declaration of which compilation a value uses;
  decisions on deuterium/tritium and ion conventions; and a custody home
  for the compilation. Normalized experimental H/G comparisons stay held on
  the same data. D0–D6 being implemented does not complete Phase D while
  this holds.
