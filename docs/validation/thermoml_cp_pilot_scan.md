# ThermoML ideal-gas Cp pilot species scan (Phase C, WP0)

Measured 2026-09-19, re-measured 2026-09-20 after independent review (see
"Changes from the 2026-09-19 measurement" below), against the pinned NIST
ThermoML bulk archive (`ark:/88434/mds2-2422`, `ThermoML.v2020-09-30.tgz`,
v1.2.6). This is WP0 of the
[Phase C implementation plan](../research/tckdb-phase-c-implementation-plan.md)
("C0 -- preconditions and the pilot species scan"): a read-only scan to pick
the one article C2/C3 (the observation/uncertainty schema and the ThermoML
importer) are built and demonstrated against. Nothing in this document writes
to a database, touches the Pi, or changes any existing file.

Reproduce with:

```bash
python backend/scripts/validation/thermoml_cp_pilot_scan.py \
  --archive /path/to/ThermoML.v2020-09-30.tgz \
  --inchikeys docs/validation/thermoml_cp_pilot_scan_playground_inchikeys.txt \
  --out /path/to/thermoml_cp_pilot_scan.json
```

The playground InChIKey list used for every number in this document is
committed at
[`thermoml_cp_pilot_scan_playground_inchikeys.txt`](thermoml_cp_pilot_scan_playground_inchikeys.txt)
(26 species). The full JSON this scan produced is committed at
[`thermoml_cp_pilot_scan.json`](thermoml_cp_pilot_scan.json) (18.8 kB).

## Changes from the 2026-09-19 measurement

An independent review of the first version of this scan (PR #503) found eight
issues, all fixed in the script and re-measured against the same pinned
archive before this revision of the document was written:

1. **Plan text corrected.** The Phase C plan said fluoroethane's points were
   "at 101.325 kPa". They are not -- see "A finding that contradicts..."
   below and the plan's own C0 section, which now records the corrected
   pressure range and marks the pilot choice pending re-decision.
2. **Pressure is now read from a `Variable` as well as a `Constraint`.**
   `_pressure_constraints_kpa` previously read only the block-level fixed
   `Constraint`; most of the fluorinated-refrigerant records in this archive
   report pressure as a per-point `Variable` instead, and were silently
   reported as `n/a`. Water's single point is at 3000 kPa, not `n/a`.
3. **The test's excluding fixtures were untested.** The single-component and
   `Prediction` filters had no fixture exercising them; a two-component
   (mixture) block, a `Prediction` block, and an `sMethodName`-only block are
   now all in the synthetic archive, each asserted excluded from the strict
   candidate table AND counted in its own counter. See "Mutation check"
   below for the five named mutations this now catches.
4. **Skip counters and the `sMethodName`-only set now survive.** An article
   whose only qualifying blocks were `sMethodName`/`Prediction`/
   `CriticalEvaluation` previously made `_scan_document` return `None`,
   discarding that article's counters entirely; the three skip counters
   printed 0 on the real archive regardless of what was actually skipped.
   Fixed, and the `sMethodName`-only single-component "Ideal gas"/"Gas" Cp
   set is now a first-class report (15 compounds -- see below).
5. **The 26-species playground list is now committed** (see above), so "1 of
   26" is independently reproducible rather than asserted.
6. **Compounds with an InChI but no InChIKey are now counted and logged**
   (`compounds_without_inchikey`) instead of silently dropped. Zero were
   found in this archive for a qualifying block, but the count is now a
   real, checked zero rather than an absence nobody looked for.
7. **Exit code 3 is now "archive not found"**, distinct from exit code 2
   ("digest mismatch"). Both previously returned 2.
8. **Method precedence is now documented**: if a non-conformant document ever
   carried more than one of `eMethodName`/`sMethodName`/`Prediction`/
   `CriticalEvaluation` on the same property, `eMethodName` wins.

None of these fixes changed the headline result: the playground intersection
is still exactly `{water}`, under either the strict or the broadened reading,
and the archive-wide counts below are numerically identical to the
2026-09-19 run except where the fix specifically adds detail (pressure,
`sMethodName`-only table, `compounds_without_inchikey`).

## Archive and XSD digests, as observed

Both facts named in the WP0 brief were checked against the NIST records API
(`https://data.nist.gov/rmm/records?@id=ark:/88434/mds2-2422`) before any
download, and the archive's digest was independently recomputed after
download (both on 2026-09-19 and again on 2026-09-20 for this re-measurement).
All three checks agree:

| Fact | Pinned | Observed |
| --- | --- | --- |
| `ThermoML.v2020-09-30.tgz` size | 189,433,115 bytes | 189,433,115 bytes (records API and downloaded file, both runs) |
| `ThermoML.v2020-09-30.tgz` SHA-256 | `231161b5e443dc1ae0e5da8429d86a88474cb722016e5b790817bb31c58d7ec2` | same, in the records API `checksum.hash`, in the published `.tgz.sha256` sidecar, and recomputed on the downloaded file (both runs) |
| `https://trc.nist.gov/ThermoML.xsd` SHA-256 | `5c9945ce07c2a0c4d7bd249ba4d1f76b4a37eba0b872f1f1f6656b4df247ab89` | same, both in the records API's `ThermoML.xsd` component (mirrored at `data.nist.gov/od/ds/mds2-2422/ThermoML.xsd`) and recomputed on the file fetched directly from `trc.nist.gov` |

No mismatch. The archive's digest is enforced in code by
`thermoml_cp_pilot_scan.py`'s `PINNED_ARCHIVE_SHA256` constant, checked before
any tar member is read (exit `2` on mismatch, nothing parsed; exit `3` if
`--archive` does not point at an existing file at all -- these two failure
modes are now distinguished). The XSD is recorded here for the pin only --
WP0 does not validate documents against it; XSD validation is C3's
`validate.py`. The downloaded archive was deleted from the scratch working
directory after both scans; it is not retained in this repository.

## Method

The archive is 11,923 article pairs (`<doi-prefix>/<doi-suffix>.{xml,json}`),
189 MB, scanned directly from the `.tgz` with `tarfile` and
`xml.etree.ElementTree` -- nothing is extracted to disk. A member is parsed
only if its raw bytes contain the literal property string (a cheap
pre-filter); the full scan takes about 40-50 seconds.

A strict-table match requires, per the WP0 brief, all of:

- a `PureOrMixtureData` block with exactly one `Component` (single-component);
- a `Property` whose `Property-MethodID/PropertyGroup/HeatCapacityAndDerivedProp/ePropName`
  is exactly `"Molar heat capacity at constant pressure, J/K/mol"`;
- a phase of `"Ideal gas"` or `"Gas"`, read from that `Property`'s own
  `PropPhaseID/ePropPhase` where present, else the block's `PhaseID/ePhase`
  (the fallback path is now exercised by a dedicated test fixture);
- an **`eMethodName`** element under that property-method choice (the schema's
  enumerated experimental-method vocabulary -- vacuum adiabatic calorimetry,
  flow calorimetry, DSC variants, "Derived from speed of sound", etc.), as
  opposed to `sMethodName` (free-text), `CriticalEvaluation` or `Prediction`.
  If a non-conformant document ever carried more than one of these on the
  same property, `eMethodName` wins (fixed precedence, documented in the
  script's docstring).

Pressure is read two ways and reported as one of three kinds per candidate:
a block-level fixed **`Constraint`** (one value for every point in the
block), a per-point **`Variable`** (a `VariableValue` in each `NumValues`,
exactly like temperature), or **`none`** when neither is present. The report
gives the kind, minimum, maximum and distinct-value count.

Per-value uncertainty presence (`nStdUncertValue`, `nExpandUncertValue`,
`nCombStdUncertValue`, `nCombExpandUncertValue`) and the property-level
uncertainty definitions' coverage factor / level of confidence are recorded
per matched property, not per point (ThermoML ties per-value uncertainty
entries back to a property-level definition by assessment number, not by
repeating the coverage factor at every point).

## Archive-wide counts

| Metric | Value |
| --- | --- |
| Articles scanned | 11,923 |
| Articles with any single-component ideal-gas/gas Cp property (any compound, `eMethodName` only) | 6 |
| Distinct compounds hit (`eMethodName` only) | 7 |
| Mixture (multi-component) blocks skipped that otherwise matched the property | 480 |
| Single-component, target-phase blocks skipped for `sMethodName`-only method | 15 |
| Single-component, target-phase blocks skipped for `Prediction` | 0 |
| Single-component, target-phase blocks skipped for `CriticalEvaluation` | 0 |
| Compounds with an InChI but no InChIKey, referenced by a qualifying block | 0 |
| Playground species hit (of 26 candidate playground species, `eMethodName` only) | **1 of 26** (water) |

A second, deliberately broader measurement (below) also allows `sMethodName`
(free-text but still experimental, still excluding `CriticalEvaluation` and
`Prediction`) to check whether the strict `eMethodName`-only reading of the
brief was hiding playground hits. It was not: the playground intersection is
identical either way.

## The literal candidate: water, and why it is not usable

| Compound | InChIKey | DOI | Journal | Year | N | T range (K) | Pressure | ePhase | eMethodName | Uncertainty present | Coverage factor | Level of confidence | XML SHA-256 | JSON SHA-256 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| water | `XLYOFNOQVPJJNP-UHFFFAOYSA-N` | 10.1016/j.tca.2018.04.018 | Thermochim. Acta | 2018 | 1 | 508.4-508.4 | **3000 kPa** (`Variable`, 1 distinct value) | **Gas** | Flow calorimetry | `nCombExpandUncertValue` | false | true | `e0bee9dede8df2e874e1d3ee9201be396d1c2ba86be32221b7b8266765894ed7` | `6c666ea0ec5359b0070e6ab68f04dc1e7d23ca22a911b565dc4886175e08693a` |

(Corrected from the 2026-09-19 report, which showed `n/a` for pressure
because it only read block-level `Constraint` pressures; this point's
pressure is a per-point `Variable`, now read.)

This is the only playground species reached by any qualifying property in the
entire archive, under either the strict or the broadened reading. It is not a
usable pilot for the C2/C4 demonstration:

- **One point.** A single 508.4 K superheated-steam Cp value at 3000 kPa
  cannot exercise a Cp(T) importer, a dedupe key, or a multi-temperature
  Cantera comparison.
- **Phase is `"Gas"`, not `"Ideal gas"`, and the pressure confirms why.**
  3000 kPa (30 bar) is nowhere near the low-pressure limit "ideal gas"
  implies; at 508.4 K this is 0.786 of water's critical temperature
  (Tc = 647.096 K, the IAPWS-95 reference value) and well above atmospheric
  pressure. Per the C2 design
  (`docs/research/tckdb-phase-c-implementation-plan.md`, section C2), `"Gas"`
  maps to `state_basis=real_gas` and is explicitly "stored but reported not
  comparable" -- it is excluded from the C4 review-tier Cantera cross-check by
  design, which only evaluates `state_basis=ideal_gas` rows.
- The same article's other block (pentane + acetone, `nOrgNum` count 2) is a
  mixture and is correctly excluded.

**Measured conclusion: no playground species has usable ideal-gas Cp data in
this pinned archive.** Practically, this is the "intersection is empty"
branch the WP0 brief anticipates, even though one technically-matching row
exists.

## A finding that contradicts the plan's stated expectations

The plan text (`tckdb-phase-c-implementation-plan.md`, C0 section) named
methane ("Derived from speed of sound") as "the likely hit" and methanol,
ethane and propane as "the expected fallbacks." Both expectations are
measurably wrong for this specific archive snapshot:

- **Methane** appears in the archive with the target property string in only
  two files, both natural-gas mixture studies (`10.1021/je300762m.xml`,
  `10.1021/je4007019.xml`) alongside ethane and propane -- multi-component,
  excluded by the single-component requirement. Methane has no single-component
  ideal-gas or gas-phase Cp entry anywhere in this archive under this exact
  property string. `"Derived from speed of sound"` is a real `eMethodName`
  enum member (confirmed in the XSD), but it is not attached to any qualifying
  methane record here.
- **Ethane** appears in exactly one file (the same natural-gas mixture as
  methane) -- multi-component, excluded.
- **Propane** appears in two files, both the same natural-gas mixture context
  -- multi-component, excluded.
- **Ethylene** does not appear anywhere in the archive alongside the target
  property string.
- **Methanol** does appear with 20 qualifying articles and the target
  property string, but every single-component match is condensed-phase
  (`Liquid`, `Crystal`, or `Metastable liquid` -- calorimetric DSC/adiabatic
  work), never `Ideal gas` or `Gas`. Methanol's ideal-gas Cp is not in this
  archive under this property string at all.

None of the plan's four named species (methane, water, ethylene -- "confirmed
among 59" per the plan text -- and the methanol/ethane/propane fallback set)
supply a usable pilot record here. A second, related finding surfaced by this
re-measurement (finding 1 of the review, see "Changes" above): the plan's
originally-recorded fallback pick, fluoroethane, was also described with the
wrong pressure ("101.325 kPa") -- it is in fact 1020-3400 kPa, a per-point
`Variable`, not a fixed atmospheric `Constraint`. The plan's C0 section has
been corrected accordingly and the pilot choice is recorded there as pending
re-decision. This is recorded as a measured fact, not a guess, per the WP0
brief's "nothing lands before A and B" / read-only-scan discipline.

## Archive-wide picture: every qualifying single-component compound

Under the strict `eMethodName`-only reading (7 compounds, the same set the
counts above use), now with the corrected pressure kind/range for each:

| Compound | InChIKey | ePhase | eMethodName | N values | T range (K) | Pressure | Article |
| --- | --- | --- | --- | --- | --- | --- | --- |
| ferrocene | `DFRHTHSZMBROSH-UHFFFAOYSA-N` | Gas | Small sample (50 mg) DSC | 61 | 293-353 | 101.325 kPa (`Constraint`) | 10.1016/j.jct.2011.06.001 |
| nickelocene | `KZPXREABEBSAQM-UHFFFAOYSA-N` | Gas | Small sample (50 mg) DSC | 61 | 293-353 | 101.325 kPa (`Constraint`) | 10.1016/j.jct.2011.06.001 |
| fluoroethane | `UHCBBWUQDAVSMS-UHFFFAOYSA-N` | Gas | Flow calorimetry | 38 | 315.33-365.75 | 1020-3400 kPa, 30 distinct (`Variable`) | 10.1016/j.fluid.2016.07.034 |
| 2,3,3,3-tetrafluoro-1-propene | `FXRLMCRCYDHQFW-UHFFFAOYSA-N` | Gas | Flow calorimetry | 33 | 373.15-413.15 | 3498-10032 kPa, 30 distinct (`Variable`) | 10.1021/acs.jced.7b00946 |
| pentafluoroethane | `GTLACDSXYULKMZ-UHFFFAOYSA-N` | Gas | Flow calorimetry | 29 | 303.15-343.15 | 500-2400 kPa, 7 distinct (`Variable`) | 10.1021/acs.jced.8b00310 |
| 1,1,1-trifluoroethane | `UJPMYEOUBPIPHQ-UHFFFAOYSA-N` | Gas | Flow calorimetry | 10 | 311.15-343.15 | 1600-2400 kPa, 5 distinct (`Variable`) | 10.1007/s10765-007-0186-y |
| water | `XLYOFNOQVPJJNP-UHFFFAOYSA-N` | Gas | Flow calorimetry | 1 | 508.4-508.4 | 3000 kPa (`Variable`) | 10.1016/j.tca.2018.04.018 |

A structurally important pattern falls out of this table, sharper now that
pressure is read correctly: **every `eMethodName`-tagged (enumerated) Cp
match in the entire archive is phase `"Gas"`, never `"Ideal gas"`, and only
the two organometallics (ferrocene, nickelocene) are at a fixed near-
atmospheric pressure.** Every fluorinated-refrigerant record -- five of the
seven compounds -- is a per-point `Variable` pressure well above atmospheric,
in some cases (pentafluoroethane's 343.15 K point, and the entire
2,3,3,3-tetrafluoro-1-propene range) at or above the compound's own critical
temperature. `"Ideal gas"`-phase matches exist only under `sMethodName` (free
text), and are dominated by "Statistical thermodynamic calculations" /
"statistical thermodynamics" -- Cp(T) derived from spectroscopic constants
rather than measured calorimetrically. Broadening to include `sMethodName`
adds 15 more single-component compounds (22 total; 13 at `Ideal gas`, 9 at
`Gas`) -- but the playground overlap is still exactly `{water}`, unchanged
from the strict count.

## sMethodName-only single-component "Ideal gas"/"Gas" Cp compounds

Fixed by this revision (finding 4): these 15 blocks were previously counted
in a discarded counter that printed 0 regardless of what was skipped. All 15
are single-component, target-property, target-phase, but their only method
is a free-text `sMethodName` rather than an enumerated `eMethodName` -- the
"statistical-thermodynamics-derived ideal-gas" set an author may choose from
if the strict `eMethodName`-only reading is relaxed. 13 are at `Ideal gas`
phase and 2 are at `Gas` phase.

| Compound | InChIKey | DOI | Journal | Year | N | T range (K) | Pressure | ePhase | sMethodName | Uncertainty |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| cesium hydroxide iodide | `PSXWJXDCROVHEL-UHFFFAOYSA-L` | 10.1016/j.jct.2013.05.032 | J. Chem. Thermodyn. | 2013 | 29 | 298.15-3000 | 101.325 kPa (`Constraint`) | Ideal gas | statistical thermodynamics | `nCombExpandUncertValue` |
| .alpha.-D-glucose | `WQZGKKKJIJFFOK-DVKNGEFBSA-N` | 10.1016/j.jct.2012.11.031 | J. Chem. Thermodyn. | 2013 | 16 | 50-1000 | 101.325 kPa (`Constraint`) | Ideal gas | Statistical thermodynamic calculations | `nCombExpandUncertValue` |
| N-methylpyrrolidone | `SECXISVLQFMRJM-UHFFFAOYSA-N` | 10.1016/j.fluid.2015.06.045 | Fluid Phase Equilib. | 2015 | 14 | 100-1000 | 100 kPa (`Constraint`) | Ideal gas | STD | `nCombExpandUncertValue` |
| 2-aminoethan-1-ol | `HZAXFHJVJLSVMW-UHFFFAOYSA-N` | 10.1016/j.fluid.2015.06.045 | Fluid Phase Equilib. | 2015 | 14 | 100-1000 | 100 kPa (`Constraint`) | Ideal gas | STD | `nCombExpandUncertValue` |
| (S)-2-pinene | `GRWFGVWFFZKLTI-IUCAKERBSA-N` | 10.1016/j.jct.2013.01.009 | J. Chem. Thermodyn. | 2013 | 14 | 100-1000 | 100 kPa (`Constraint`) | Ideal gas | Statistical thermodynamic calculations | `nCombExpandUncertValue` |
| (1S,5S)-6,6-dimethyl-2-methylenebicyclo[3.1.1]heptane | `WTARULDDTDQWMU-IUCAKERBSA-N` | 10.1016/j.jct.2013.01.009 | J. Chem. Thermodyn. | 2013 | 14 | 100-1000 | 100 kPa (`Constraint`) | Ideal gas | Statistical thermodynamic calculations | `nCombExpandUncertValue` |
| (-)-cis-verbenol | `WONIGEXYPVIKFS-YIZRAAEISA-N` | 10.1016/j.jct.2013.01.009 | J. Chem. Thermodyn. | 2013 | 14 | 100-1000 | 100 kPa (`Constraint`) | Ideal gas | Statistical thermodynamic calculations | `nCombExpandUncertValue` |
| (-)-verbenone | `DCSCXTJOXBUFGB-JGVFFNPUSA-N` | 10.1016/j.jct.2013.01.009 | J. Chem. Thermodyn. | 2013 | 14 | 100-1000 | 100 kPa (`Constraint`) | Ideal gas | Statistical thermodynamic calculations | `nCombExpandUncertValue` |
| 5-(1-adamantyl)tetrazole | `OBWRBAQXRLLXFR-UHFFFAOYSA-N` | 10.1016/j.tca.2014.07.018 | Thermochim. Acta | 2014 | 14 | 100-1000 | 101.325 kPa (`Constraint`) | Ideal gas | Statistical thermodynamic calculations | `nCombExpandUncertValue` |
| benzene | `UHOVQNZJYSORNB-UHFFFAOYSA-N` | 10.1016/j.jct.2013.08.022 | J. Chem. Thermodyn. | 2014 | 12 | 200-1000 | 100 kPa (`Constraint`) | Ideal gas | statistical thermodynamics | `nCombExpandUncertValue` |
| phenoxazine | `TZMSYXZUNZXBOL-UHFFFAOYSA-N` | 10.1016/j.jct.2013.11.013 | J. Chem. Thermodyn. | 2014 | 10 | 200-600 | 101.325 kPa (`Constraint`) | Ideal gas | Statistical thermodynamics | `nCombExpandUncertValue` |
| 10H-phenothiazine | `WJFKNYWRSNBZNX-UHFFFAOYSA-N` | 10.1016/j.jct.2013.11.013 | J. Chem. Thermodyn. | 2014 | 10 | 200-600 | 101.325 kPa (`Constraint`) | Ideal gas | Statistical thermodynamics | `nCombExpandUncertValue` |
| decamethylcyclopentasiloxane | `XMSXQFUHVRWGNA-UHFFFAOYSA-N` | 10.1016/j.fluid.2007.04.028 | Fluid Phase Equilib. | 2007 | 9 | 298-1000 | 100 kPa (`Constraint`) | Ideal gas | Predicted | `nCombExpandUncertValue` |
| 2-methylpropane | `NNPPMTNAJDCUHE-UHFFFAOYSA-N` | 10.1016/j.fluid.2014.09.017 | Fluid Phase Equilib. | 2014 | 7 | 270-330 | 101.325 kPa (`Constraint`) | Gas | derived from presented speed of sound measurements | `nCombExpandUncertValue` |
| trans-1,3,3,3-tetrafluoropropene | `CDOOAUSHHFGWSA-OWOJBTEDSA-N` | 10.1021/je4004564 | J. Chem. Eng. Data | 2013 | 6 | 278.15-353.15 | 101.325 kPa (`Constraint`) | Gas | Derived with speed of sound | `nCombExpandUncertValue` |

Note: `decamethylcyclopentasiloxane`'s `sMethodName` is the literal string
`"Predicted"` -- a data-entry choice, not the schema's actual `Prediction`
element (which this scan does exclude, separately and correctly). It is
included here because its *method choice element* is `sMethodName`, exactly
as the scan's exclusion rule is defined; the string content of that field is
reported verbatim so a reader can judge it themselves. None of the 15 are
playground species.

## Candidates for re-decision

Three groups, as measured, with no recommendation made -- the choice among
them is the author's, per the plan's own C0 section.

### (a) Strict experimental gas-phase Cp candidates

The 7 compounds from "Archive-wide picture" above, now with reduced
temperature where a critical temperature is readily citable from NIST
WebBook or an equivalent peer-reviewed source (none currently hold TCKDB
computed thermo):

| Compound | InChIKey | N | T range (K) | Pressure | Tc (K), source | Tr = T/Tc range |
| --- | --- | --- | --- | --- | --- | --- |
| ferrocene | `DFRHTHSZMBROSH-UHFFFAOYSA-N` | 61 | 293-353 | 101.325 kPa (`Constraint`) | not readily citable (organometallic solid; no standard experimental critical point found) | -- |
| nickelocene | `KZPXREABEBSAQM-UHFFFAOYSA-N` | 61 | 293-353 | 101.325 kPa (`Constraint`) | not readily citable (as above) | -- |
| fluoroethane | `UHCBBWUQDAVSMS-UHFFFAOYSA-N` | 38 | 315.33-365.75 | 1020-3400 kPa (`Variable`, 30 distinct) | 375.31, NIST WebBook (CAS 353-36-6; Booth & Swinehart 1935 / Parthasarathy 1935) | 0.840-0.975 |
| 2,3,3,3-tetrafluoro-1-propene (R1234yf) | `FXRLMCRCYDHQFW-UHFFFAOYSA-N` | 33 | 373.15-413.15 | 3498-10032 kPa (`Variable`, 30 distinct) | 367.85, NIST/ACS (*J. Chem. Eng. Data* 2011/2012 vapor-pressure and p-rho-T measurements) | **1.014-1.123 (entire range supercritical)** |
| pentafluoroethane (R125) | `GTLACDSXYULKMZ-UHFFFAOYSA-N` | 29 | 303.15-343.15 | 500-2400 kPa (`Variable`, 7 distinct) | 339.16, NIST/ACS (Kuwabara et al. 1995, *J. Chem. Eng. Data*) | **0.894-1.012 (upper end supercritical)** |
| 1,1,1-trifluoroethane (R143a) | `UJPMYEOUBPIPHQ-UHFFFAOYSA-N` | 10 | 311.15-343.15 | 1600-2400 kPa (`Variable`, 5 distinct) | 345.86, NIST (*International Standard Formulation for ... R-143a*) | 0.900-0.992 |
| water | `XLYOFNOQVPJJNP-UHFFFAOYSA-N` | 1 | 508.4-508.4 | 3000 kPa (`Variable`) | 647.096, IAPWS-95 reference | 0.786 |

Every one of the five fluorinated-refrigerant records is at a pressure well
above atmospheric and, for two of them, at or above the compound's own
critical temperature over part or all of the measured range -- these are
compressed/supercritical-fluid Cp measurements, not ideal-gas ones, whatever
the ThermoML phase label says. Only ferrocene and nickelocene are at a fixed
near-atmospheric pressure, but their measurement context ("Small sample
(50 mg) DSC") and lack of a citable critical temperature make the
ideal-gas-limit assumption unverifiable from the archive alone.

### (b) sMethodName-only ideal-gas candidates

The 15 compounds from the table above (13 at `Ideal gas` phase, 2 at `Gas`),
all statistical-thermodynamics-derived rather than calorimetric, all at
either a fixed atmospheric-or-near-atmospheric pressure or a stated pressure
program (cesium hydroxide iodide's is the widest: 298.15-3000 K at
101.325 kPa held constant, not itself a pressure range -- the "3000" in that
row's T range is a temperature in K, not a pressure). None currently hold
TCKDB computed thermo.

### (c) Playground intersection

`{water}` -- exactly one species, under either the strict `eMethodName`-only
reading or the broadened reading that also allows `sMethodName`. Water does
not appear in group (b) (its only qualifying record uses `eMethodName`, not
`sMethodName`), so the three groups above do not overlap except where (a)
and (c) both list water.

## Mutation check

Five mutations named by the independent review, each applied by hand to a
throwaway copy of the fixed script, confirmed to turn a passing test red,
then reverted (none committed):

| # | Mutation | Test that goes red |
| --- | --- | --- |
| (b) | `len(components) != 1` weakened to `len(components) < 1` (mixture blocks no longer excluded) | `TestMixturePredictionAndFreeMethodAreCountedNotJustExcluded::test_counters_reflect_each_excluded_block` (`skipped_mixture_blocks` drops from 1 to 0) |
| (e) | A `Prediction` origin counted in `skipped_prediction` but then treated as `experimental_enum` (not excluded) | `TestFindsOnlyTheQualifyingRecords::test_exactly_the_three_qualifying_candidates_are_reported` (`playground_hit` rises from 3 to 4; the Prediction compound is registered in the playground fixture precisely so a leak is visible) |
| (f) | An `sMethodName` origin counted in `skipped_free_method` but not excluded from the strict table | Same test as (e), plus `TestFreeMethodIdealGasReport::test_smethod_compound_surfaces_in_the_dedicated_report` (the sMethodName compound now also appears in `candidates`) |
| (h) | The `nPropNumber` equality join in the `NumValues`/`PropertyValue` lookup mutated to always match (`if True:`) | `TestFindsOnlyTheQualifyingRecords::test_exactly_the_three_qualifying_candidates_are_reported` (the join-decoy candidate's `uncertainty_elements_present` becomes `["nStdUncertValue"]` instead of `[]`, picking up the decoy density property's uncertainty instead of the real Cp property's) |
| (i) | The block-level `PhaseID` fallback removed from `_property_phase` (returns `None` when `PropPhaseID` is absent) | Same test as (h) (the phase-fallback candidate, whose `Property` has no `PropPhaseID` at all, disappears from `candidates` entirely: `playground_hit` drops from 3 to 2) |

All five were confirmed red with the mutation applied and green again after
reverting; the diffs were never committed.

## Reproducibility

The script (`backend/scripts/validation/thermoml_cp_pilot_scan.py`) is
stdlib-only and takes the archive path, an InChIKey list (one playground
species per line, standard InChIKey first) and an output JSON path. Re-run
against the 26-species playground list committed alongside this document
(`thermoml_cp_pilot_scan_playground_inchikeys.txt`), or against the seven
fallback species in "Archive-wide picture" above, to reproduce every number
in this document. The downloaded archive itself is not retained anywhere in
this repository (it is 189 MB and NIST-hosted, not TCKDB's to redistribute);
re-download from
`https://data.nist.gov/od/ds/mds2-2422/ThermoML.v2020-09-30.tgz` and verify
the SHA-256 above before use. Exit codes: `0` at least one playground species
hit (this run's exit code, on account of water); `1` archive valid, no
playground hit; `2` digest mismatch; `3` `--archive` path does not exist.
