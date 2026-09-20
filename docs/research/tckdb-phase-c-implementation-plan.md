# Detailed phase C implementation plan

Companions: [Whole-programme plan](tckdb-implementation-programme.md),
[Phase A plan](tckdb-phase-a-implementation-plan.md),
[Phase B plan](tckdb-phase-b-implementation-plan.md),
[Phase B evidence](tckdb-phase-b-verification.md),
[Interchange standards note](tckdb-interchange-standards.md).

## Baseline, scope and decisions

Planning baseline is `0de7a173` (Phases A and B code landed and deployed).
Alembic head at planning time is `9b1c7e2d4a68`; re-verify before any
revision is authored. Phase C delivers two interchange profiles and the
contracts they need: a bounded QCSchema importer for calculation evidence, and
a narrow ThermoML pilot for experimental evidence, each ending in a
demonstration the paper can cite.

Three decisions taken 2026-09-19 bind this plan:

| Decision | Consequence |
| --- | --- |
| The ThermoML pilot property is ideal-gas heat capacity Cp(T) of pure compounds | Maps onto existing thermo points and NASA fits; the H/G reference-basis hold point stays untouched |
| Phase C is inside the first paper's scope | Each half ends in a publication demonstration; the programme's earlier sentence that the adapters follow the paper is superseded |
| The playground deployment may be read, read-only, as a development aid | It supplies the pilot species and the comparison record; it remains not the paper's corpus |

Rules that govern every work package: TCKDB is sovereign, so QCSchema and
ThermoML conform to TCKDB's contracts and no QCSchema- or ThermoML-shaped
table is added; validators and engines (QCElemental, lxml, Cantera) are pinned
external references; observations are stored as observed, never as
possibilities (DR-0007); cross-checks against external reference data live in
the review tier and nowhere earlier (ADR 0008); accepted science is corrected
by supersession (ADR 0003). No H/G reference basis, no covariance, no accuracy
threshold and no condensed-phase, mixture or reaction state is authorized here.

External facts verified 2026-09-19: QCElemental 0.51.2 (released 2026-09-16)
ships both model families as `qcelemental.models.v1` and `.v2`; the ThermoML
schema is version 4.0 at `https://trc.nist.gov/ThermoML.xsd`; the archive is
one NIST bulk file (`10.18434/mds2-2422`, v1.2.6, `ThermoML.v2020-09-30.tgz`)
of per-article XML and JSON, distributed "with permission of the journal
publishers" under the NIST open license with a citation request, and NIST
states the values "were checked for completeness and accuracy of representation
… but were not critically evaluated". Re-verify digests at implementation.

## Required impact analysis

Same discipline as Phases A and B. Targets to analyse before editing:
`MolecularPropertyObservation` (`backend/app/db/models/molecular_property_observation.py:66`)
and its Create schema; `_resolve_identity`
(`backend/app/services/cccbdb_molecular_property_import.py:180`, to be
extracted); `_assert_records_have_rights_basis`
(`backend/app/services/release/curation.py:568`); `DECLARING_MODULES`
(`backend/app/scientific_checks/declarations.py:1300`) and `_ACTIVE_RUBRICS`
(`backend/app/services/machine_review/recipe.py:46` — registering a rubric here
does not, by itself, restale anything; see the C4 review-round-2 correction
below for the mechanism that actually did, and the fix); the calculation read routes
(`backend/app/api/routes/calculations.py`); archive `INCLUDED_TABLES`
(`backend/app/services/archive/registry.py:18`).

## C0 — preconditions and the pilot species scan

Nothing lands before A and B are on `main` (done). Rebuild the code index.

WP0 is a read-only scan, not a guess: open the pinned ThermoML bulk file,
select single-component `PureOrMixtureData` blocks whose property is
"Molar heat capacity at constant pressure, J/K/mol", whose phase is "Ideal gas"
or "Gas", and which carry an experimental method; intersect their standard
InChIKeys with playground species that hold computed thermo (water, methane
and ethylene confirmed among 59). Methane by "Derived from speed of sound" was
the expected hit.

**WP0 result (2026-09-19, PR #503).** The scan of all 11,923 articles found
no single-component ideal-gas Cp for any playground species: methane, ethane
and propane occur only inside natural-gas mixture blocks; methanol only as
liquid or crystal; water once, as one real-gas point at 508 K. Every
calorimetric gas-phase Cp in the archive is tagged "Gas" at a stated pressure,
never "Ideal gas"; of the archive's 13 "Ideal gas" entries, 12 are
statistical-thermodynamics-derived and one is tagged "Predicted". The most-covered non-playground candidate
is **fluoroethane** (C2H5F, `UHCBBWUQDAVSMS-UHFFFAOYSA-N`), 38 flow-calorimetry
points at 315.33-365.75 K, DOI `10.1016/j.fluid.2016.07.034`, J. Fluid Phase
Equilib. 2016 — but its pressure is not the fixed 101.325 kPa first assumed
here. Re-measured directly from the block: pressure is a per-point `Variable`
("Pressure, kPa", `nVarNumber` 2), not a block-level `Constraint`, with 30
distinct values from 1020 to 3400 kPa across the 38 points (Cp spans
71.2-118.1 J/K/mol over that range). Fluoroethane's critical temperature is
375.31 K (NIST WebBook, `https://webbook.nist.gov/cgi/cbook.cgi?ID=353-36-6`,
citing Booth & Swinehart 1935 and Parthasarathy 1935); against that Tc, the
archive's 315.33-365.75 K span is a reduced temperature Tr = T/Tc of
0.84-0.97, and the measured 1020-3400 kPa is well above atmospheric pressure.
Fluoroethane's points are far from ideal-gas conditions, and every
other calorimetric gas-phase entry in the archive is either real-gas at high
pressure (four fluorocarbons near or above their critical temperature; water's
single point at 30 bar) or organometallic (ferrocene, nickelocene at 1 atm). **Decision (Calvin, 2026-09-20): the pilot is benzene**
(`UHOVQNZJYSORNB-UHFFFAOYSA-N`), from the archive's ideal-gas entries derived
by statistical thermodynamics: DOI `10.1016/j.jct.2013.08.022`, J. Chem.
Thermodyn. 2014, 12 values at 200-1000 K, phase "Ideal gas" at a 100 kPa
constraint, method recorded as the free-text `sMethodName` "statistical
thermodynamics", combined expanded uncertainty with a level of confidence and
no coverage factor; XML sha256
`e33222268a0f5eb10b8249e5b39253886cb4e87110cf8d5ef2b7d2cc368163c7`. The state
matches a computed ideal-gas record exactly, so the comparison is between an
externally evaluated ideal-gas Cp and TCKDB's computed statmech Cp, and the
paper says "evaluated", never "measured". It needs one computed thermo record
for benzene (an ARC B3LYP/def2-TZVP opt+freq, author-run). The scan script
`backend/scripts/validation/thermoml_cp_pilot_scan.py` and its report
`docs/validation/thermoml_cp_pilot_scan.md` are the evidence.

## C1 — QCSchema importer (no migration)

Measured state. `CalculationInBundle` and `ConformerUploadRequest`
(`schemas/python/tckdb-schemas/tckdb_schemas/workflows/conformer_upload.py:224`)
already carry everything a QCSchema result maps onto: geometry in Å,
single-point energy in hartree, a packed Hessian in hartree/bohr² with a
`HessianSource`, optimisation results, parameter observations, artifacts with
declared SHA-256. The computed-species bundle forces the primary calculation to
be an optimisation (`computed_species_upload.py:327`), so it is not the entry
point. No content-based duplicate detection exists for calculations;
request-level idempotency keys expire after thirty days
(`backend/app/services/idempotency.py:41`). QCElemental is absent everywhere.

Design.

- Location: a client-side package `clients/python/adapters/qcschema/`
  (`tckdb-qcschema`), mirroring `tckdb-chemkin`: depends on `tckdb-client`,
  `tckdb-schemas` and an exact `qcelemental==0.51.2`. Nothing enters the
  backend image. Modules: `reader.py` (family dispatch and validation),
  `molecule.py`, `hessian.py`, `mapping.py`, `uploader.py`, `exporter.py`,
  `cli.py` with `import`, `export` and `report`. CI gains a fourth step in
  `.github/workflows/python-client-ci.yml` and the gate-coverage expectations
  are updated.
- Family dispatch, the version trap: a document is family v2 iff it carries a
  top-level `input_data`; it is validated with that family's class only, and its
  `schema_name` and `schema_version` must then equal the family's declared pair
  or the document is refused with `schema_version_family_mismatch`. The integer
  alone never selects a family.
- Profile v1: `AtomicResult` with driver energy, gradient or hessian, and
  `OptimizationResult`; families v1 and v2; one fragment, every atom real,
  integer mass numbers, `success` true. One document becomes one
  `POST /uploads/conformers` followed by one `POST /calculations/{id}/artifacts`
  carrying the untouched bytes as `ArtifactKind.ancillary`, filename
  `<stem>.qcschema.json`, SHA-256 declared (allowed by
  `schemas/python/tckdb-schemas/tckdb_schemas/fragments/artifact.py:22`).
- Mapping table:

| QCSchema | TCKDB | Class |
| --- | --- | --- |
| `success=false` | refuse `job_failed`; nothing posted | rejected |
| driver energy, `return_result` | type `sp`, `electronic_energy_hartree`; a differing `properties.return_energy` refuses `energy_contradiction` | transformed |
| driver gradient | type `sp`, energy from `properties.return_energy` (absent refuses); gradient array retained only | transformed, retained |
| driver hessian, `return_result` 3N×3N | type `freq`, `HessianPayload` with `source=uploaded`, symmetry checked, packed row-major lower triangle in the order of `backend/app/services/hessian_parsing.py:417`; no `freq_result`, no modes, no `n_imag` | transformed |
| `OptimizationResult` | type `opt`, `converged=success`, `n_steps=len(trajectory)`, final energy, initial and final geometries with roles; per-step energies retained only | transformed, retained |
| `molecule.geometry` (bohr) | `xyz_text` in Å using `BOHR_TO_ANGSTROM` at `hessian_parsing.py:69` (CODATA 2014; qcelemental's 2018 value differs by 4.4e-10 relative, recorded, and export uses the same constant); ten decimals | transformed |
| `mass_numbers`, `masses` | isotopes only where non-standard; a mass that is not the tabulated value for its nuclide refuses `nonstandard_mass` | transformed or rejected |
| any `real` false; more than one fragment | refuse `ghost_atoms_unsupported`, `multi_fragment_unsupported` | rejected |
| charge, multiplicity | species identity payload (must be integers) | transformed |
| identity | `--smiles` on the CLI (recorded `depositor_declared`), else `identifiers.smiles`, else refuse `identity_unavailable`; no 3D perception | transformed or rejected |
| non-integer `molecular_charge` or `molecular_multiplicity` | refuse `non_integer_identity` (added by C-Q1, PR #504) | rejected |
| `model.method`, `model.basis` | `LevelOfTheoryRef` verbatim, case preserved (the level-of-theory hash at `backend/app/services/calculation_resolution.py:118` is byte-exact, so `b3lyp` and `B3LYP` are distinct rows; the demonstration selects by explicit level, not by hash) | transformed |
| `keywords.reference` | `spin_treatment` when recognised | transformed |
| `keywords` | parameter observations, section `qcschema.keywords`, no canonical key | transformed |
| `provenance.creator`, `.version` | `SoftwareReleaseRef` | transformed |
| routine, host, threads, memory, wall time | `parameters_json["tckdb_qcschema"]["provenance"]` | retained |
| `wavefunction`, `native_files`, stdout, stderr | live only in the raw artifact | unsupported, declared |
| raw bytes above the 50 MB artifact cap | refuse | rejected |

- Why a Hessian becomes a `freq` record with no spectrum: the document observed
  a matrix. Stored mode rows carry no source column, so a derived list would be
  indistinguishable from a parsed one to every reader; TCKDB already derives
  spectra at read time from stored Hessians. The Hessian-method inference will
  mark it assumed, which is correct because QCSchema does not say analytic or
  finite difference.
- Raw retention and mapping report: the compact report
  {transformed, retained_only, unsupported, rejected} lives in
  `parameters_json["tckdb_qcschema"]["mapping_report"]`; the full report is the
  CLI's JSON output. No new artifact kind in v1: it would cost an enum member
  in two packages, an allowlist entry and an `ALTER TYPE` revision with an
  asymmetric downgrade, for a discriminator the namespace already provides.
- Version pinning, recorded per calculation under
  `parameters_json["tckdb_qcschema"]`: adapter version, qcelemental version,
  model family, schema name and version, record kind, driver, raw artifact
  SHA-256, canonical document SHA-256, the bohr constant used, identity source;
  and `parameters_parser_version = "tckdb-qcschema/<v>;qcelemental/<v>"`.
- Idempotency: both request keys derive from the canonical-JSON SHA-256 of the
  parsed document (`qcschema:<sha[:32]>:conformers`, `…:artifact`), so a
  re-serialised copy replays; before posting, the artifact search route's
  `sha256` filter (`backend/app/api/routes/scientific/artifacts.py:93`) is
  queried and a hit refuses `already_imported` unless `--allow-duplicate`. A
  partial first run recovers on rerun. Stated limits: keys are per user and
  expire; two importers can race; server-side content dedupe is a policy TCKDB
  does not have and this plan does not add.
- Export, v1, `AtomicResult` v2 only: `sp` records as driver energy; `freq`
  records with a stored Hessian as driver hessian (full symmetric matrix
  unpacked); `opt` not exported; `provenance.creator="TCKDB"`, and the importer
  refuses TCKDB exports so an export cannot return as evidence. This needs one
  backend read, `GET /calculations/{id}/hessian` returning the stored matrix
  and its source, a client method, the OpenAPI golden, the client parity ledger
  and a client version bump. The round-trip test asserts exact energy and
  Hessian, geometry within the ten-decimal bound stated in the test, and the
  complete list of deliberately lost fields.
- Fixtures: Psi4 through qcengine in a separate pinned environment (lockfile
  committed beside the corpus) on water at a cheap level: energy, gradient,
  hessian, optimisation, a v2 copy of each, a genuinely failed job; hand-derived
  ghost-atom, two-fragment, non-integer-mass and family-drift documents with the
  exact edit recorded in a README. Each fixture has a sidecar naming the
  expected route or refusal code. The emitted payloads form a backend corpus
  under `backend/tests/fixtures/qcschema/<case>/` posted by a test mirroring
  `backend/tests/api/test_api_arc_run_fixtures.py`.
- Tests and the mutation each catches: the adapter's parametrized corpus test
  (fails on an empty corpus) against a changed unit constant, upper-triangle
  packing, `return_energy` used for driver energy, derived frequencies, the
  `real` check removed, family taken from the integer, `success` ignored, and
  identity fallback to perception; the backend corpus test against a dropped
  Hessian block, an added mode row, a changed artifact kind; the Hessian route
  (404 without, exact with); the uploader with a stub client (key stability
  across re-serialisation, pre-check refusal, partial-run recovery); the
  round trip.
- Demonstration: water at B3LYP/def2-TZVP, the species the playground holds
  from Gaussian, re-deposited to the publication instance under the Phase B
  machinery at freeze time. Export the Gaussian final geometry as a QCSchema
  molecule, run a Psi4 Hessian at that exact geometry, import it, export it
  back. Asserted: round-trip exactness; same composition, charge and
  multiplicity; Hessian symmetry; zero recovered imaginary modes; rigid-body
  residue within the Phase B curvature bound. Reported as measurements with no
  tolerance: the cross-program energy difference and the recovered-spectrum
  differences, because Gaussian and Psi4 define B3LYP with different
  correlation variants and grids and a printed-precision bound does not apply.
  The Hessian reanalysis on the Psi4 record reports `frequency_list_missing`,
  and that is shown. Deposited: Psi4 input and lockfile, raw JSON with digest,
  versions, mapping report, exported document, a database-read-only script
  `backend/scripts/validation/qcschema_interchange_report.py` registered in
  `backend/scripts/paper/registry.py:37`, `docs/validation/qcschema_interchange.md`,
  and a decision record for the profile.
- Non-goals: a QCArchive connector; minting thermo, statmech or frequencies
  from an energy or Hessian; a gradient table; scans, IRC, NEB or
  transition-state records; execution-environment manifests from QCSchema
  provenance; a new artifact kind; server-side dedupe; converting a document
  between families before storage.
- Risks: qcelemental and `tckdb-schemas` must coexist on one Pydantic major;
  `normalize_software_name("Psi4")` must resolve to the existing psi4 row;
  method case fragments level-of-theory identity across programs; a later
  `input`-kind artifact replaces `parameters_json` (a pre-existing hazard noted
  in DR-0026, not fixed here).

## C2 — observation, state, uncertainty and source-custody contracts (one migration)

Measured state. `molecular_property_observation` already has the four-role
shape of a result with provenance: nullable identity, origin, external-source
columns, raw payload, a nulls-not-distinct dedupe key, a dry-run persistence
service. It lacks a heat-capacity kind, a typed unit for it, pressure, a state
basis, any uncertainty meaning beyond a bare scalar, and a custody row for the
source document. `ThermoPoint` (`backend/app/db/models/thermo.py:236`) is keyed
by (thermo, temperature), so it cannot hold replicate measurements, and
`thermo` is the product a release selects. The only uncertainty-convention
enum in the schema belongs to kinetics. No state beyond `phase` exists.

Design.

- Contract: an experimental Cp(T) point is one `molecular_property_observation`
  row per compound, property and temperature value, with a new
  `MolecularPropertyKind.heat_capacity_cp`. Not `thermo` and not a third
  family.
- New enums in `backend/app/db/models/common.py`: `ObservedUncertaintyKind`
  {standard, expanded, combined_standard, combined_expanded}, one to one with
  ThermoML's per-value fields and with no "unspecified" member (absent is NULL);
  `ObservedUncertaintyAssessor` {source_author, source_evaluator};
  `ObservedStateBasis` {ideal_gas, real_gas}; `ExternalSourceRecordKind`
  {thermoml_article, cccbdb_page}; `SubmissionRecordType` gains
  `molecular_property_observation`.
- New columns on the observation: `pressure_bar` (CHECK positive),
  `state_basis`, `uncertainty_kind`, `uncertainty_coverage_factor` (CHECK at
  least 1), `uncertainty_level_of_confidence_pct` (CHECK in (0, 100]),
  `uncertainty_assessor`, `external_source_record_id`. CHECKs: the heat-capacity
  kind requires unit `J/mol/K` and a temperature; an uncertainty value and its
  kind are both present or both absent; a coverage factor only with an expanded
  kind. The dedupe key is unchanged; the per-value record key is
  `<DOI>#PureOrMixtureData[n]/Property[m]/NumValues[i]`, so replicates at one
  temperature are distinct rows and a re-import is a no-op.
- Source custody, implementing the CCCBDB spec's Gap 4 for new rows only:
  `external_source` (name, release, database DOI, citation text, terms URL,
  terms text; unique on name and release) and `external_source_record`
  (source, record kind, source URI, record key, retrieved at, HTTP status,
  content SHA-256, length, raw URI from `content_addressed_key`
  (`backend/app/services/artifact_storage.py:510`), container digest, schema
  id, schema valid, parser name, parser version, mapping version, mapping
  report JSON; unique on source, key, digest, parser version and mapping
  version so a changed parser appends rather than overwrites). Both join the
  archive `INCLUDED_TABLES`. Existing CCCBDB rows keep their flattened columns.
- Derivation: no new edge table. Lineage is raw bytes by digest, then the
  custody row, then the observation's FK, then the review finding. The
  normalisation step is the versioned mapping report plus each row's
  `raw_payload_json["mapping"]`. Typed derivation edges with parameters are
  deferred.
- Origin for a property that carries neither an `eMethodName` nor a
  `Prediction` element but a free-text `sMethodName` (the archive's own
  ideal-gas entries, including the benzene pilot): the importer holds an
  explicit allowlist of verbatim `sMethodName` strings observed in the pinned
  archive, each mapped to an origin and recorded as a mapping rule with the
  string ("statistical thermodynamics" maps to `computed`, with the string
  verbatim in `method_note`); a string not on the allowlist leaves the origin
  unresolved and the row is rejected with the string in the report. No keyword
  matching: the rule names observed strings only.
- Uncertainty precedence, versioned as `uncertainty.precedence.v1`: the typed
  columns hold the first present of combined expanded, combined standard,
  expanded, standard; every other uncertainty present is retained under
  `raw_payload_json["uncertainties"]`; repeatability, device specification,
  curve deviation and asymmetric forms are listed as unsupported; no inference
  between a coverage factor and a confidence level.
- State: "Ideal gas" maps to `ideal_gas`; "Gas" maps to `real_gas` with the
  pressure required from a "Pressure, kPa" constraint or variable (a real-gas
  row without a pressure is rejected); every other phase or standard state,
  any multi-component block and any reaction block is rejected with the source
  string in the report. No solvent, composition or activity fields. Since WP0
  showed the archive's calorimetric gas Cp is always real-gas at a stated
  pressure, real-gas rows are comparable in C4 with the non-ideality flagged,
  not excluded.
- Wire schema mirrored on `MolecularPropertyObservationBase`; `schema.dbml`
  regenerated; one revision from the verified head with both directions
  (removing an enum value on downgrade needs a type rebuild; follow the
  artifact-kind precedent).

## C3 — ThermoML Cp importer (backend, mirrors the CCCBDB importer)

- Package `backend/app/importers/thermoml/`. `__init__.py` pins the source
  name and release, the archive URL, SHA-256 and size, the XSD SHA-256, and
  parser and mapping versions. `archive.py` fetches the single allowlisted bulk
  URL, verifies the digest before reading any member, selects one article's XML
  and JSON by DOI, cross-checks the JSON's embedded MD5 against the XML, and
  snapshots raw bytes by SHA-256 with a manifest; any other URL raises, in the
  pattern of the CCCBDB URL guard. `schema/ThermoML.xsd` is committed with a
  checksum file; `validate.py` uses lxml's XSD validator with entity resolution
  and network disabled; a schema-invalid article is rejected, never parsed
  anyway. `models.py`, `parser.py` (namespace-aware; joins numeric rows to
  variables and constraints and per-value uncertainties to their definitions;
  unsupported content is recorded, never raised) and `mapping.py` (origin from
  the method element versus the prediction element, property label verbatim,
  unit `J/mol/K`, a literature fragment from DOI, title, year, journal and
  authors). Fixtures: the pilot article, a valid but unsupported liquid or
  mixture file, a schema-invalid file, a prediction-only file.
- Dependency: optional extra `thermoml = ["lxml>=5.2"]` in
  `backend/pyproject.toml` beside the CHEMKIN extra, and lxml pinned in
  `backend/environment.yml`, since the image installs from there.
- Persistence `backend/app/services/thermoml_cp_import.py`: dry-run by default,
  a savepoint per row, insert-or-ignore on the dedupe key, dispositions as in
  the CCCBDB service; writes the custody rows; opens one bulk-import submission
  and links the rows; records a `source_terms` rights attestation through
  `record_attestation` (`backend/app/services/rights.py:61`) carrying the NIST
  license URL, its disclaimer and the publisher-permission sentence verbatim.
  The SPDX identifier is the operator's choice and must equal the release's
  data license because the match is exact. Literature resolves through
  `resolve_or_create_literature` (`backend/app/services/literature_resolution.py:307`).
  The service imports the importer package's parser, mapper and validator
  (pure functions, no I/O) but never its archive fetch/select functions and never a
  per-family upload workflow module; the importer package never imports the
  service back, mirroring the existing layering test. CLI
  `backend/scripts/thermoml_cp_import.py --doi … [--commit]`.
- Identity: extract the CCCBDB resolver into
  `backend/app/services/external_observation_identity.py` and call it from both
  services. Resolve only on an exact single standard-InChIKey match to one
  species with one ground-state minimum entry; otherwise unresolved with every
  candidate retained; never create a species; never resolve by name, CAS or
  formula.
- Later attachment: a curator-only `attach_observation_identity` service and
  admin route that refuses a second assignment (correction is supersession).
  Optional for the demonstration, whose species resolves by InChIKey.
  Review round 2 (C-E5): unlike the automatic resolver above, the curator
  attach does not require the target species to have a *unique*
  ground-state minimum entry -- any ground-state minimum entry of its
  species is a legal target, because the curator naming one specific entry
  is itself the isomer disambiguation the automatic resolver refuses to
  guess at.

## C4 — the cross-check in the review tier and the paper surface

- The first member of `CheckTier.review` (`backend/app/scientific_checks/__init__.py:160`),
  declared in a new module listed in `DECLARING_MODULES`, with channel `none`
  because findings are private.
- Rubric `EvidenceRubric` (`backend/app/services/trust/models.py:260`) named
  `external_cp_comparison`, version 1, on thermo records, added to
  `_ACTIVE_RUBRICS`.

  **Review round 2 correction (2026-09-20).** The line above originally read
  "every stored machine review restales once and the plan says so" — backwards.
  Registering a rubric in `_ACTIVE_RUBRICS` restales nothing by itself: the
  only currency consumer, `get_record_machine_review_currency_for_record`
  (`backend/app/services/machine_review/query.py`), filters solely by
  `(record_type, record_id)` — it has never looked at rubric or provider. What
  actually restaled every thermo the runner touched was the runner itself:
  each `record_machine_review` row `run_and_record` appended (provider
  `tckdb.scientific_checks`) was newest by `reviewed_at` for that thermo, its
  currency key never matched the active *reviewer* (LLM) recipe, and the
  currency classifier — seeing it as the latest row, mismatched — reported
  `stale` and demoted the thermo's true current reviewer-family review to
  `historical`. HIGH-severity finding from that review; fixed by
  `MachineReviewRecordFamily` (`backend/app/services/machine_review/query.py`),
  which scopes every currency/latest-row read to one family (`reviewer` or
  `scientific_check`) at a time, `reviewer` being the default every existing
  consumer keeps. After the fix, neither registering the rubric nor running
  the check restales the reviewer family, and running the reviewer-family
  recipe never sees or restales a Cp comparison row. Runner
  `backend/app/services/external_comparison/cp.py`: for a
  computed thermo with a NASA-7, NASA-9 or point representation and
  same-entry heat-capacity observations with `state_basis=ideal_gas`, evaluate
  Cp at each observed temperature with Cantera (lazy import; an absent engine
  is a configuration error, never a finding); points compare only at an exactly
  matching temperature. Write one row through
  `create_record_machine_review_row` (`backend/app/services/machine_review/persistence.py:93`)
  with provider `tckdb.scientific_checks`, model `external_cp_comparison_v1`,
  a context hash from the real inputs, and per-observation findings: the
  observation and custody refs, temperature, observed Cp, its uncertainty with
  kind, coverage factor and assessor, computed Cp, residual, representation,
  in-range flag, the observation's `state_basis` and pressure, and for
  real-gas rows a `non_ideality: unquantified` flag stating that the residual
  includes the real-gas contribution at the observed pressure and that no
  virial or equation-of-state correction was applied (a correction would be a
  convention the hold points do not authorize); `not_comparable` only for
  temperatures outside the fit range. No threshold, no status change, no
  selection effect.
- Generators `experimental_cp_comparison` and `thermoml_source_provenance` in
  `backend/scripts/paper/generators.py`, registered in the registry, and a row
  added to the Phase B plan's manuscript correspondence table.

## C5 — read route and rights

- `GET /api/v1/scientific/species-entries/{id}/observations?property_kind=…`
  with the same disclosure envelope as other scientific reads, returning value,
  unit, temperature, pressure, state basis, typed uncertainty, method note,
  literature ref, external source (name, release, DOI, record key, digest) and
  identity status. Unresolved rows are unreachable by entry.
- Rights gap to close: the release gate never sees observation rows although
  the archive ships the table. Extend `_assert_records_have_rights_basis` to
  observation rows in archive scope, or exclude unattested observation rows
  from the archive projection. A curator must judge the journal-permission
  clause before an observation ships in a release; the plan flags this and does
  not decide it.

## Publication demonstration

The two halves can claim: one ThermoML 4.0 article validated against the
pinned XSD with every value, condition, uncertainty definition, method string,
identifier and citation preserved as raw bytes by digest, typed columns and a
mapping report; idempotent re-import; identity by exact InChIKey only; computed
Cp(T) for benzene evaluated independently with Cantera at the 12 temperatures
of the externally evaluated ideal-gas table, with residuals reported beside the
declared uncertainty meaning and the source's verbatim method string, and no
approval effect; and one Psi4 QCSchema bundle round-tripped exactly and compared with
the existing Gaussian record as a measurement.

They cannot claim ThermoML support beyond this profile; that the benzene
table is a measurement (it is a statistical-thermodynamics evaluation, and the
archive holds no calorimetric ideal-gas Cp for any small molecule); agreement
or accuracy;
anything about enthalpy, entropy, Gibbs energy, formation basis, mixtures,
condensed phases or covariance; that the residual isolates the computed model
from the real-gas contribution (no correction is applied); or QCSchema support
beyond the profile.

## Work packages

| WP | Scope | Migration | Depends on |
| --- | --- | --- | --- |
| C-Q1 adapter core | `tckdb-qcschema` package, corpus, CI step | none | none |
| C-Q2 backend acceptance and Hessian read | corpus test, `GET /calculations/{id}/hessian`, client method, golden, parity, client bump | none | Q1 payloads |
| C-Q3 export and round trip | `exporter.py`, CLI, pinned read fixtures | none | Q2 |
| C-Q4 QCSchema demonstration | validation script, registry entry, validation doc, decision record | none | Q3 and the author's Psi4 run |
| C-E1 schema | enums, observation columns and CHECKs, custody tables, registry, wire schema, revision | one | none |
| C-E2 importer | `importers/thermoml/`, XSD pin, fixtures, lxml extra | none | E1 |
| C-E3 persistence | service, identity extraction, submission and attestation, CLI | none | E1, E2 |
| C-E4 review check and generators | check, rubric, runner, two generators | none | E3 |
| C-E5 read route, rights, attach | route, gate extension, curator attach | none | E1, E3 |

The Q series and the E series run in parallel; E1 holds the only revision.
Every brief reproduces the known problem first, names files, reuse,
invariants and forbidden actions, lists each test with the mutation it
catches, runs the three gates locally before the pull request opens, passes
independent review, and ships through the ship skill.

## Verification

| Scenario | Required outcome |
| --- | --- |
| Family-drift document | Refused with `schema_version_family_mismatch` |
| Failed job; ghost atom; two fragments; non-tabulated mass | Refused with the named code; nothing posted |
| Hessian document | One `freq` calculation with the packed matrix, zero mode rows, raw artifact with matching digest |
| Re-serialised copy re-imported | Replays; no second calculation |
| Export then import | Energy and Hessian exact; geometry within the stated bound; loss list complete |
| `GET /calculations/{id}/hessian` | 404 without a Hessian; exact list with one |
| Bulk file with a wrong digest | Refused before any member is read |
| Schema-invalid article | Rejected; nothing parsed |
| Liquid or mixture article | No payloads; unsupported and rejected lists populated |
| Uncertainty precedence and coverage | Typed columns hold the highest-precedence kind; the rest retained |
| Prediction-only article | Non-experimental origin |
| Dry run | Nothing written |
| Second import | Every row `duplicate`; custody row unique per parser and mapping version |
| Ambiguous InChIKey | Unresolved; candidates retained |
| Attestation | Row carries the source terms verbatim |
| Review check | Findings with residuals and uncertainty meaning; no status or selection change; real-gas rows `not_comparable` |
| Generators | Byte-stable from a restored deposit |
| Release with an unattested observation | Refused |
| Second identity attachment | Refused |

Commands: the three gate scripts as in the Phase A plan, the client suite
with the new adapter step, `bash backend/scripts/update-openapi-golden.sh`,
DBML regeneration, the parity ledger generator, `detect_changes(scope="all")`.

## Completion gate

C completes when both profiles are implemented and tested as above, the
programme's C gate holds (versioned examples preserve identity, definitions,
conditions, provenance and uncertainty meaning; unsupported mappings are
reported; ingestion is idempotent), and both demonstrations reproduce from the
restored deposit. Corpus curation, deposit building and manuscript numbers
remain freeze-time steps, executed once after development is declared
finished.
