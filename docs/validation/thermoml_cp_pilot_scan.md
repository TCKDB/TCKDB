# ThermoML ideal-gas Cp pilot species scan (Phase C, WP0)

Measured 2026-09-19 against the pinned NIST ThermoML bulk archive
(`ark:/88434/mds2-2422`, `ThermoML.v2020-09-30.tgz`, v1.2.6). This is WP0 of the
[Phase C implementation plan](../research/tckdb-phase-c-implementation-plan.md)
("C0 -- preconditions and the pilot species scan"): a read-only scan to pick the
one article C2/C3 (the observation/uncertainty schema and the ThermoML
importer) are built and demonstrated against. Nothing in this document writes
to a database, touches the Pi, or changes any existing file.

Reproduce with:

```bash
python backend/scripts/validation/thermoml_cp_pilot_scan.py \
  --archive /path/to/ThermoML.v2020-09-30.tgz \
  --inchikeys /path/to/playground_inchikeys.txt \
  --out /path/to/thermoml_cp_pilot_scan.json
```

## Archive and XSD digests, as observed

Both facts named in the WP0 brief were checked against the NIST records API
(`https://data.nist.gov/rmm/records?@id=ark:/88434/mds2-2422`) before any
download, and the archive's digest was independently recomputed after
download. All three checks agree:

| Fact | Pinned | Observed |
| --- | --- | --- |
| `ThermoML.v2020-09-30.tgz` size | 189,433,115 bytes | 189,433,115 bytes (records API and downloaded file) |
| `ThermoML.v2020-09-30.tgz` SHA-256 | `231161b5e443dc1ae0e5da8429d86a88474cb722016e5b790817bb31c58d7ec2` | same, in the records API `checksum.hash`, in the published `.tgz.sha256` sidecar, and recomputed on the downloaded file |
| `https://trc.nist.gov/ThermoML.xsd` SHA-256 | `5c9945ce07c2a0c4d7bd249ba4d1f76b4a37eba0b872f1f1f6656b4df247ab89` | same, both in the records API's `ThermoML.xsd` component (mirrored at `data.nist.gov/od/ds/mds2-2422/ThermoML.xsd`) and recomputed on the file fetched directly from `trc.nist.gov` |

No mismatch. The archive's digest is enforced in code by
`thermoml_cp_pilot_scan.py`'s `PINNED_ARCHIVE_SHA256` constant, checked before
any tar member is read (exit `2` on mismatch, nothing parsed). The XSD is
recorded here for the pin only -- WP0 does not validate documents against it;
XSD validation is C3's `validate.py`.

## Method

The archive is 11,923 article pairs (`<doi-prefix>/<doi-suffix>.{xml,json}`),
189 MB, scanned directly from the `.tgz` with `tarfile` and
`xml.etree.ElementTree` -- nothing is extracted to disk. A member is parsed
only if its raw bytes contain the literal property string (a cheap
pre-filter); the full scan takes about 15 seconds.

A match requires, per the WP0 brief, all of:

- a `PureOrMixtureData` block with exactly one `Component` (single-component);
- a `Property` whose `Property-MethodID/PropertyGroup/HeatCapacityAndDerivedProp/ePropName`
  is exactly `"Molar heat capacity at constant pressure, J/K/mol"`;
- a phase of `"Ideal gas"` or `"Gas"`, read from that `Property`'s own
  `PropPhaseID/ePropPhase` where present, else the block's `PhaseID/ePhase`;
- an **`eMethodName`** element under that property-method choice (the schema's
  enumerated experimental-method vocabulary -- vacuum adiabatic calorimetry,
  flow calorimetry, DSC variants, "Derived from speed of sound", etc.), as
  opposed to `sMethodName` (free-text), `CriticalEvaluation` or `Prediction`.

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
| Playground species hit (of 26 candidate playground species, `eMethodName` only) | **1 of 26** (water) |

A second, deliberately broader measurement (below) also allows `sMethodName`
(free-text but still experimental, still excluding `CriticalEvaluation` and
`Prediction`) to check whether the strict `eMethodName`-only reading of the
brief was hiding playground hits. It was not: the playground intersection is
identical either way.

## The literal candidate: water, and why it is not usable

| Compound | InChIKey | DOI | Journal | Year | N | T range (K) | P (kPa) | ePhase | eMethodName | Uncertainty present | Coverage factor | Level of confidence | XML member | XML SHA-256 | JSON SHA-256 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| water | `XLYOFNOQVPJJNP-UHFFFAOYSA-N` | 10.1016/j.tca.2018.04.018 | Thermochim. Acta | 2018 | 1 | 508.4-508.4 | n/a | **Gas** | Flow calorimetry | `nCombExpandUncertValue` | false | true | `10.1016/j.tca.2018.04.018.xml` | `e0bee9dede8df2e874e1d3ee9201be396d1c2ba86be32221b7b8266765894ed7` | `6c666ea0ec5359b0070e6ab68f04dc1e7d23ca22a911b565dc4886175e08693a` |

This is the only playground species reached by any qualifying property in the
entire archive, under either the strict or the broadened reading. It is not a
usable pilot for the C2/C4 demonstration:

- **One point.** A single 508.4 K superheated-steam Cp value cannot exercise a
  Cp(T) importer, a dedupe key, or a multi-temperature Cantera comparison.
- **Phase is `"Gas"`, not `"Ideal gas"`.** Per the C2 design
  (`docs/research/tckdb-phase-c-implementation-plan.md`, section C2), `"Gas"`
  maps to `state_basis=real_gas` and is explicitly "stored but reported not
  comparable" -- it is excluded from the C4 review-tier Cantera cross-check by
  design, which only evaluates `state_basis=ideal_gas` rows. The one row this
  archive offers for a playground species cannot feed the demonstration the
  plan is built around, independent of its single-point problem.
- The same article's other block (pentane + acetone, `nOrgNum` count 2) is a
  mixture and is correctly excluded.

**Measured conclusion: no playground species has usable ideal-gas Cp data in
this pinned archive.** Practically, this is the "intersection is empty"
branch the WP0 brief anticipates, even though one technically-matching row
exists.

## A finding that contradicts the plan's stated expectations

The plan text (`tckdb-phase-c-implementation-plan.md:67-69`) names methane
("Derived from speed of sound") as "the likely hit" and methanol, ethane and
propane as "the expected fallbacks." Both expectations are measurably wrong
for this specific archive snapshot:

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
supply a usable pilot record here. This is recorded as a measured fact, not a
guess, per the WP0 brief's "nothing lands before A and B" / read-only-scan
discipline: the plan's expectation was evidently written before this exact
archive snapshot was scanned structurally, and this scan is what corrects it.

## Archive-wide picture: every qualifying single-component compound

Under the strict `eMethodName`-only reading (7 compounds, the same set the
counts above use):

| Compound | InChIKey | ePhase | eMethodName | N values | Article |
| --- | --- | --- | --- | --- | --- |
| ferrocene | `DFRHTHSZMBROSH-UHFFFAOYSA-N` | Gas | Small sample (50 mg) DSC | 61 | 10.1016/j.jct.2011.06.001 |
| nickelocene | `KZPXREABEBSAQM-UHFFFAOYSA-N` | Gas | Small sample (50 mg) DSC | 61 | 10.1016/j.jct.2011.06.001 |
| fluoroethane | `UHCBBWUQDAVSMS-UHFFFAOYSA-N` | Gas | Flow calorimetry | 38 | 10.1016/j.fluid.2016.07.034 |
| 2,3,3,3-tetrafluoro-1-propene | `FXRLMCRCYDHQFW-UHFFFAOYSA-N` | Gas | Flow calorimetry | 33 | 10.1021/acs.jced.7b00946 |
| pentafluoroethane | `GTLACDSXYULKMZ-UHFFFAOYSA-N` | Gas | Flow calorimetry | 29 | 10.1021/acs.jced.8b00310 |
| 1,1,1-trifluoroethane | `UJPMYEOUBPIPHQ-UHFFFAOYSA-N` | Gas | Flow calorimetry | 10 | 10.1007/s10765-007-0186-y |
| water | `XLYOFNOQVPJJNP-UHFFFAOYSA-N` | Gas | Flow calorimetry | 1 | 10.1016/j.tca.2018.04.018 |

A structurally important pattern falls out of this table and is worth
recording: **every `eMethodName`-tagged (enumerated) Cp match in the entire
archive is phase `"Gas"`, never `"Ideal gas"`.** `"Ideal gas"`-phase matches
exist only under `sMethodName` (free text), and are dominated by "Statistical
thermodynamic calculations" / "statistical thermodynamics" -- Cp(T) derived
from spectroscopic constants rather than measured calorimetrically. Broadening
to include `sMethodName` adds 15 more single-component compounds (22 total;
13 at `Ideal gas`, 9 at `Gas`) -- benzene, .alpha.-D-glucose, (S)-2-pinene,
(-)-cis-verbenol, (-)-verbenone, phenoxazine, 10H-phenothiazine,
5-(1-adamantyl)tetrazole, N-methylpyrrolidone, 2-aminoethan-1-ol, cesium
hydroxide iodide, decamethylcyclopentasiloxane (whose `sMethodName` is the
literal string `"Predicted"`, a data-entry choice that does not use the
schema's actual `Prediction` element), 2-methylpropane and
trans-1,3,3,3-tetrafluoropropene -- but the playground overlap is still
exactly `{water}`, unchanged from the strict count.

## Recommendation

**No playground species can be the WP0 pilot article as specified.** This is
the fallback branch the brief anticipates, with the specific fallback set the
plan named (methanol, ethane, propane) also measurably absent from this
archive for this property. The three most-covered qualifying compounds in the
archive generally, reported with the same detail as a playground candidate
would get:

1. **Ferrocene** (`DFRHTHSZMBROSH-UHFFFAOYSA-N`) and **nickelocene**
   (`KZPXREABEBSAQM-UHFFFAOYSA-N`) tie at 61 values each, 293-353 K, 101.325
   kPa, DOI 10.1016/j.jct.2011.06.001, `J. Chem. Thermodyn.` 2011, "Small
   sample (50 mg) DSC", combined expanded uncertainty with a level of
   confidence recorded (no coverage factor recorded). Same article, same
   method, same conditions -- two organometallic sandwich compounds measured
   side by side.
2. **Fluoroethane** (`UHCBBWUQDAVSMS-UHFFFAOYSA-N`), 38 values, 315.33-365.75
   K, DOI 10.1016/j.fluid.2016.07.034, `Fluid Phase Equilib.` 2016, "Flow
   calorimetry", combined expanded uncertainty with a level of confidence
   recorded.

This is an author decision, not one this scan makes: none of the three are
"small molecules" in the sense the plan meant (simple organics already
familiar to the playground) -- two are organometallic sandwich compounds, one
is a fluorinated refrigerant, and none currently hold TCKDB computed thermo.
Picking any of them as the pilot means computing thermo for a new species
before C2/C3/C4 can be demonstrated end to end, which the plan's C0 section
explicitly allows ("the fallback... is an author decision recorded here") but
does not pre-decide. The alternative -- accepting water's single `"Gas"`
point and treating the demonstration as `not_comparable` by design -- defeats
the purpose of the residual-reporting exhibit C4 is built around and is not
recommended.

No runner-up is named among playground species because there is exactly one
non-viable candidate, not two viable ones to rank.

## Reproducibility

The script (`backend/scripts/validation/thermoml_cp_pilot_scan.py`) is
stdlib-only and takes the archive path, an InChIKey list (one playground
species per line, standard InChIKey first) and an output JSON path. Re-run
against the 26-species playground list used for this scan, or against the
three fallback species above, to reproduce every number in this document. The
downloaded archive itself is not retained anywhere in this repository (it is
189 MB and NIST-hosted, not TCKDB's to redistribute); re-download from
`https://data.nist.gov/od/ds/mds2-2422/ThermoML.v2020-09-30.tgz` and verify
the SHA-256 above before use.
