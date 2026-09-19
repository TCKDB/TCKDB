# Detailed phase A implementation plan

Companion: [Whole-programme plan](tckdb-implementation-programme.md).

## Baseline

The supplied audit and implementation baseline is `8539c927d6ca81758d28a67c0a0fbac31a13c9af`. Read-only probes reproduced rejection of valid 0 K-only enthalpy, acceptance of empty NASA7, temperature-only points and overlapping NASA9 intervals. Inspection found omitted state/0 K fields in scientific reads, selected NDJSON and contribution bundles, and invented CHEMKIN bounds/coefficients with unconditional gas phase. Frozen releases already serialize parent columns and declared children.

Acceptance of an inconsistent-looking H/S/G triple establishes no unconditional consistency rule: applicability and reference conventions remain unresolved. Schema acceptance does not prove persistence or publication. The planning audit did not inspect production or run the full backend suite.

## Required impact analysis

Bind `TCKDB` to `/home/calvin/code/TCKDB_v2`. The original index was at `ffe304ba38362bff9eb38bf52144b8975d0da6b9`, 186 commits behind; engine/storage versions 42/43 caused UNKNOWN results. Restore the runner/index before edits, run upstream analysis for changed functions/classes, inspect callers/processes and report HIGH/CRITICAL risks. UNKNOWN is unresolved; corroborate graph boundaries with source references. Vtrace is supplementary, not a complete replacement. Complete `detect_changes(scope="all")` is required before any commit; partial/truncated output does not pass.

## A1 — projections

Expose `phase`, `reference_pressure_bar`, `enthalpy_formation_0k_kj_mol` and `enthalpy_formation_0k_uncertainty_kj_mol` in `ThermoRecord`, `get_species_thermo`, `SelectedThermo.to_dict` and `_thermo_to_upload`. Include scalar uncertainties in selected exports. Return nullable facts directly, without include flags or invented defaults. Contribution replay must retain explicit null state. Update frontend response typing and generated OpenAPI/client contracts; composed search retains the expanded projection. Regression-test frozen releases and archives without redesigning their formats.

## A2 — creation contracts

Use shared fragments for reusable validation and backend services for ownership/provenance. A finite 0 K formation enthalpy, including zero, counts as content; uncertainty alone does not. Created points need a finite property. Created NASA7 needs all three ordered bounds and both seven-coefficient blocks; zeros are valid. Require finite supplied values, coefficients and bounds, positive temperatures and nonnegative uncertainties. Keep historical read models permissive and distinct from strict creates.

Order NASA9 validation by unique contiguous `interval_index` from 1. Reject reversed/overlapping intervals and gaps; exact adjoining bounds define the supported continuous upload profile. Gap rejection is an explicit profile restriction beyond the reproduced overlap defect. Allow a fit plus auxiliary points. Do not impose positive enthalpy, unconditional H/S/G consistency, polynomial continuity or accuracy rejection.

Apply content rules to standalone and both computed bundle schemas. Forward all four state/0 K fields through both workflows without extending bundle representation subsets to NASA9/Wilhoit. Omitted computed state defaults to gas/1 bar; explicit null remains null; experimental/estimated omissions remain unknown. Typed builders preserve omission versus null. No new database IDs in upload contracts.

## A3 — CHEMKIN eligibility

Reject with existing export gaps unless the selected record is complete NASA7, known gas phase, exactly 1 atm / 1.01325 bar. Report unknown state, other phases, incompatible pressure, incomplete fits and unsupported representations. Do not silently choose another candidate. Remove fabricated coefficients/bounds. Preserve strict mechanism validation so missing thermo cannot masquerade as an executable mechanism. Structured exports preserve supplied science; no pressure conversion in A. Document that existing computed defaults of 1 bar are ineligible.

## A4 — compatibility and claims

Provide a read-only legacy inventory with stable references and reasons. Do not rewrite historical rows, reinterpret nulls, delete incomplete evidence or regenerate frozen bytes. Preserve historical reads and archive recovery; fail incompatible upload-equivalent bundle export specifically. Before rollout, drain uploads with the old worker and explicitly inventory incompatible remaining jobs. Coordinate schema/client/backend versions, minimum compatibility and validation-tightening notes. Build a manuscript/audit claim-to-evidence checklist separating representation, execution, reproducibility and accuracy; remove universal-database and unsupported competitor claims.

No database migration, backfill or historical revision edits are planned: all four fields already exist. Later persistent semantics require a separate schema proposal and upgrade/downgrade compatibility tests.

## Verification

| Scenario | Required outcome |
| --- | --- |
| 0 K-only, including zero | Accepted, persisted and retained through reads/search, structured export, bundle replay and frozen release |
| Gas/liquid, 1 bar/1.01325 bar, null | Exact distinctions survive; replay never defaults explicit null |
| NASA7/NASA9/Wilhoit/scalar/points standalone | Supplied representations survive declared structured paths |
| Fit plus auxiliary points | Both retained |
| Empty/partial NASA7, empty points, nonfinite values | Deterministic error before persistence |
| NASA9 overlap/gap/reversal/duplicate or missing indices | Rejected; touching ordered intervals accepted |
| Both computed bundle workflows | Fields, defaults and validation match standalone policy |
| Historical incomplete rows | Reads and archives usable; bundle/CHEMKIN incompatibility explicit |
| Frozen release after uploads or upgrade | Bytes and SHA-256 unchanged |
| CHEMKIN state eligibility | Eligible cards load independently; ineligible cases produce gaps and fail executable-mechanism validation |

Custody checks require exact parsed values, enums, nulls, coefficient order, representation membership, archived/frozen bytes and SHA-256. No scientific tolerance applies. Use water and analytical constant-Cp NASA7 cases with pinned Cantera at bounds, 298.15 K where valid, interiors and both sides of the join. NASA9 cases include multiple intervals with nonzero inverse-temperature/integration terms and analytical constant Cp. Preserve every Wilhoit coefficient and optional integration constant; compare Cp using pinned RMG without claiming H/S reconstruction when constants are absent.

For unchanged coefficients and the same interpreter, reduced Cp/R, H/(RT), S/R must agree within `1e-10`, a floating-point allowance rather than chemical accuracy. CHEMKIN error bounds derive from each printed coefficient's half-unit rounding bound propagated through the NASA formulas, plus the numerical allowance; account for printed temperature rounding. No blanket percentage tolerance. State mismatch is categorical; no tolerance merges 1 bar with 1 atm. Preserve point H/S/G without general Gibbs claims. Record provenance, engine versions, units, grids, residuals and expected failures. Tests must detect swapped coefficient blocks and changed units.

Run targeted regressions, affected shared-schema/client/frontend tests, gate coverage and generated-contract drift checks. Confirm imports point to this checkout and run database suites sequentially:

```bash
conda run -n tckdb_env bash backend/scripts/test-rest.sh
conda run -n tckdb_env bash backend/scripts/test-api.sh
conda run -n tckdb_env bash backend/scripts/test-scientific.sh
```

## Completion gate

A requires recorded impact analysis, passing full-path preservation/validation tests, documented/tested CHEMKIN restrictions, intact legacy reads/archive recovery/frozen bytes, reviewed legacy inventory before rollout, required gates for the exact implementation commit, and a checklist distinguishing implemented, demonstrated, excluded and scientifically undecided claims. Deployment and public package publication require the rollout evidence; local implementation alone does not establish this gate. Corpus accuracy, rights, independent reproduction and paper submission readiness remain B gates.
