# Detailed phase D implementation plan

Companions: [Programme](tckdb-implementation-programme.md),
[Phase B register](tckdb-phase-b-implementation-plan.md),
[Phase C register](tckdb-phase-c-implementation-plan.md),
[Phase C evidence](tckdb-phase-c-verification.md),
[Phase D verification](tckdb-phase-d-verification.md).

## Authority, baseline and measured state

The rewritten programme (2026-09-20) supersedes older D–G descriptions.
This register implements the supplied Phase D plan: bounded Cp/entropy and
thermo–kinetics checks, explicit CLI/service invocation, dry-run by default,
no public API, UI, migration, production deployment or expanded paper claim.
Gibbs, Kirchhoff, Hess and normalized experimental H/G comparisons remain held.

Baseline: `5988fbb890674eddb1819a164079612b0e79aff1` on `main`.
Measured Alembic head: `d2f4a7c1b8e6`. Installed Cantera and `backend/uv.lock`
both specify 3.2.0; the existing optional dependency range is `>=3.0`, so the
runner additionally refuses an installed engine other than 3.2.0.
No migration is needed. Existing untracked `.agents/` and `paper/` are outside
this implementation.

Reference reading order: rewritten programme; Phase B register; Phase C
register and evidence; existing Cp runner and recipe; model contracts;
Cantera's [thermo interface](https://www.cantera.org/stable/python/thermo.html)
and [kinetics interface](https://www.cantera.org/stable/python/kinetics.html).
The declarations path is `backend/app/scientific_checks/declarations.py`.

Measured Cp defects before implementation: its hash omitted fit coefficients,
points, state, units, uncertainty, engine and custody versions; the finding
omitted uncertainty magnitude; NASA9 allowed gaps; NASA reference pressure was
hard-coded to one atmosphere; observation citations predated `mpo_` refs.
Its family/runner-scoped latest read already existed, while its wording
incorrectly implied currency. The legacy representation preference is NASA7,
then NASA9, then exact points. D2 retains that profile; D1 exposes each fit.

## Common contract and dependencies

Pure comparisons return informational findings with residuals or unavailable
reasons. The persistence wrapper appends one `record_machine_review` using
`create_record_machine_review_row`. Existing status derivation stays intact;
scientific status, trust, certification and selections have no write path here.
Declarations use `CheckTier.review`, `CodeChannel.none`, no code or threshold.
Each runner uses `SCIENTIFIC_CHECK_PROVIDER` and its own versioned rubric from
the existing recipe, outside the trust rubric registry. Cp keeps its stable
runner identity and advances to rubric v2; v1 rows remain historical evidence.

Latest and currency queries require both scientific-check family and runner.
Currency resolves current live inputs; latest recorded alone makes no currency
claim. Hashes include the records, representations, state, uncertainty,
provenance/custody, requested grid and engine version. Set-like record and grid
collections are canonicalized; coefficient/JSON array order is preserved.
Timestamps and actor metadata do not participate. Uncertainty stays as supplied:
no inferred coverage, covariance, independence or significance.

Cantera owns NASA and modified-Arrhenius evaluation. Bar converts explicitly to
Pa; NASA Cp/S converts J/kmol/K to J/mol/K; rate inputs convert to Cantera's
kmol/m/s basis and activation energy to J/kmol. Missing/wrong engine is a
configuration error before persistence, never a scientific finding.

## Work-package register and bounded briefs

| Package | Implementation and acceptance boundary | Dependency |
| --- | --- | --- |
| D0 — baseline and isolation | Measure baseline/pin; repair Cp input custody and currency; isolate reviewer family and check runners; version recipes without rewriting rows | First |
| D1 — Cp and entropy | Every NASA7/9 fit versus exact Cp/S points and s298 at 298.15 K; explicit same-entry neighbour and grid; expose each representation pair | D0 |
| D2 — computed versus observed | Existing Cp profile, complete input hashes and public observation refs; supplied uncertainty magnitude and meaning; real-gas non-ideality remains unquantified | D0 |
| D3 — thermo versus kinetics | Explicit forward and supplied reverse elementary fits plus complete thermo mapping; balanced participants and compatible gas reference pressure; both rates, thermo reverse and residuals | D0 |
| D4 — Gibbs | Held until H/G basis and quantity definitions are recorded, with independent analytical fixtures | Reference-basis decision |
| D5 — Kirchhoff | Held; distinguish species thermal increments from formation increments and supply elemental references | Reference-basis decision |
| D6 — Hess | Held; compatible quantities, cycle orientation/stoichiometry and reference provenance; no cycle search/reconciliation/recommended values | Reference-basis decision |
| D7 — verification/delivery | Independent analytical and mutation-backed review, exact gates, limitations and programme link | Each runnable package |

D0/D2 brief: change the existing runner's evidence capture and applicability,
keep its callable persistence interface, and add check-specific live currency.
Regression coverage must prove changes in coefficients, observations, state,
uncertainty or custody stale the result, while reordering and clocks do not.
Reviewer currency and a second check must survive appending this check.

D1 brief: default grid is stored points plus 298.15 K if s298 exists. Explicit
neighbours require temperatures. No interpolation or extrapolation. NASA7 uses
low coefficients through its midpoint; NASA9 uses the upper interval at a shared
boundary, and never evaluates a gap. Every pair remains visible, including
unsupported representations and missing points. The implementation conservatively
requires recorded gas phase and matching pressure for Cp as well as entropy.
Neither s298 nor points carry inferred fit covariance or uncertainty coverage.

D3 brief: require the same reaction entry and explicit forward/reverse direction.
Count structure-participant slots for stoichiometry and balance elements and
charge. Require complete entry-to-thermo mapping, gas phase, matching recorded
reference pressure, NASA domains and explicit high-pressure-limit elementary
rate semantics. Exclude third body, falloff, PLOG, Chebyshev, network-linked and
ambiguous effective rates. Rate units must match molecularity; degeneracy must
be explicitly already applied or have a positive unapplied factor. Evaluate all
supplied NASA combinations, bounded to 64. Do not synthesize the supplied reverse
fit. Use stored NASA coefficients directly without reconstructing formation
values or elemental references. Unavailable cases remain findings.

## Invocation

`backend/scripts/run_consistency_check.py` accepts `--check thermo|external-cp|thermo-kinetics`,
`--target-ref`, optional `--comparison-thermo-ref`, repeated `--temperature`, and
`--commit`. D3 additionally requires `--reverse-kinetics-ref` and repeated
`--thermo spe_...=thm_...`. References resolve in the service, never by preferred
record selection. External Cp uses observation temperatures. No writes occur in
a dry run. Service `invoke(commit=True)` stages one row; the caller controls the
transaction; the CLI commits it only after successful evaluation.

## Verification and completion gates

Every numerical test must assert nonempty evaluated cases and use analytical
expectations independent of the production evaluator. Include nonzero Cp/S and
rate disagreement, NASA7 ordering, NASA9 boundaries/gaps, exact points, units and
reference pressure. Include unavailable/missing state, uncertainty, unsupported
representations and out-of-domain temperatures. Include material-input currency,
order stability, recipe isolation, dry-run/configuration failure, single append
and unchanged scientific/selection state. Large residuals stay informational;
pytest tolerances express numerical precision, never scientific thresholds.

Independent review records actual mutations and failing tests. Fix surviving
mutations and re-review. Before implementation delivery run sequentially:

```bash
conda run -n tckdb_env bash backend/scripts/test-rest.sh
conda run -n tckdb_env bash backend/scripts/test-api.sh
conda run -n tckdb_env bash backend/scripts/test-scientific.sh
```

GitNexus refresh and upstream impact are prerequisites. Refresh succeeded at the
baseline after resolving registry access and a collision between retry processes.
The fresh CLI is authoritative because the MCP server retains stale index metadata.
Cp functions report LOW; the declarations module is HIGH because shared catalogue
consumers depend on it. Constant-symbol UNKNOWN results were corroborated by
source references in recipe, runner and register. Complete, untruncated graph
change analysis is required before any commit.

Runnable packages may finish while D4–D6 remain held. D7 records actual results;
this document does not assert that all Phase D is complete, authorize corpus
freeze, expand first-paper claims or authorize production rollout.

Implementation review refinement: D3 also excludes isotope-bearing species
until nuclide-specific engine composition is supported, and refuses a third
body inferred by Cantera from shared reaction participants. These are explicit
unavailable findings, not synthesized conventions. All eleven independent
mutations were killed; the verification record lists their failing tests.
