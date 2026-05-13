# TCKDB Schema Specification

This document is the human-readable companion to `schema.dbml`, which is generated from the live SQLAlchemy metadata in `app/db/models/`.
When this document and the DBML disagree, the ORM metadata and generated DBML are the source of truth.

## 1. Design Philosophy

The schema separates six concerns:

- Identity: stable chemical, reaction, bibliographic, software, and workflow objects
- Structure refinement: resolved species-entry, transition-state-entry, and conformer grouping layers
- Provenance: exact software release, workflow release, level of theory, literature, and calculation lineage
- Scientific products: thermo, transport, kinetics, statmech, network, and correction records
- Moderation and curation: reviews, submissions, selections, and audit events
- Operational support: upload jobs, parsed parameter vocabularies, and stored artifacts

The main structural choices are:

- `species` stores graph-level molecular identity, including `stereo_kind`, while `species_entry` stores resolved stereo/electronic/isotopic meaning
- `chem_reaction` stores graph-level stoichiometric identity, while `reaction_entry` stores one concrete curated/uploaded realization
- `transition_state` is reaction-entry-centered, and `transition_state_entry` stores candidate saddle-point structures
- `conformer_group` is basin identity, while `conformer_observation` and `conformer_selection` capture provenance and curation
- `calculation` is the hub for computational provenance and ownership
- direct ESS outputs live in dedicated result/link tables instead of overloading `calculation`
- submission/moderation state is modeled explicitly in `submission`, `submission_audit_event`, and `submission_record_link`
- network solving, phenomenological kinetics, and energy-correction data are first-class relational objects rather than JSON payloads

## 2. Units and Enum Conventions

Unit handling follows `docs/unit_policy.md`:

- fixed-unit columns use the unit in the column name, for example `electronic_energy_hartree`, `ea_kj_mol`, `temperature_k`
- enum-backed unit columns are used where the scientific representation varies, such as `ArrheniusAUnits`, `PressureUnit`, `TemperatureUnit`, `CoordinateUnit`, and `EnergyUnit`
- free-text unit storage is avoided for primary scientific values; the main exception is parsed calculation parameter capture in `calculation_parameter.unit`

Key enums reflected in the current DB include:

- identity and curation enums such as `MoleculeKind`, `StationaryPointKind`, `SpeciesEntryStateKind`, `TransitionStateEntryStatus`, `ConformerSelectionKind`, and `SpeciesEntryReviewRole`
- provenance enums such as `ScientificOriginKind`, `CalculationType`, `CalculationQuality`, `CalculationDependencyRole`, and `CalculationGeometryRole`
- scientific-model enums such as `KineticsModelKind`, `NetworkKineticsModelKind`, `RigidRotorKind`, `StatmechTreatmentKind`, `TorsionTreatmentKind`, and `EnergyCorrectionApplicationRole`
- moderation enums such as `SubmissionKind`, `SubmissionStatus`, `SubmissionSourceKind`, `SubmissionActorKind`, `SubmissionAuditEventKind`, and `SubmissionRecordType`

## 3. Core Identity and Reference Tables

### 3.1 Species

Fields:

- `id`
- `kind`
- `smiles`
- `inchi_key`
- `charge`
- `multiplicity`
- `stereo_kind`
- `created_at`

Notes:

- `inchi_key` is unique
- `multiplicity` must be at least 1
- `stereo_kind` is stored on the graph-level identity row, not on `species_entry`

### 3.2 Species Entry

Fields:

- `id`
- `species_id`
- `kind`
- `mol`
- `unmapped_smiles`
- `stereo_label`
- `electronic_state_kind`
- `electronic_state_label`
- `term_symbol_raw`
- `term_symbol`
- `isotopologue_label`
- `created_at`
- `created_by`

Notes:

- `species_entry` stores one resolved stereochemical, electronic-state, or isotopic form of a species
- `species_id` is indexed
- dedupe is enforced on `(species_id, stereo_label, electronic_state_kind, electronic_state_label, term_symbol, isotopologue_label)`

### 3.3 Geometry

`geometry` fields:

- `id`
- `natoms`
- `geom_hash`
- `xyz_text`
- `created_at`

`geometry_atom` fields:

- `geometry_id`
- `atom_index`
- `element`
- `x`
- `y`
- `z`

Notes:

- `geom_hash` is unique
- `natoms >= 1`
- `geometry_atom` uses a composite primary key on `(atom_index, geometry_id)`
- `geometry_atom.atom_index >= 1`

### 3.4 Literature and Authors

`literature` fields:

- `id`
- `kind`
- `title`
- `journal`
- `year`
- `volume`
- `issue`
- `pages`
- `doi`
- `isbn`
- `url`
- `publisher`
- `institution`
- `created_at`

`author` fields:

- `id`
- `given_name`
- `family_name`
- `full_name`
- `orcid`
- `created_at`

`literature_author` fields:

- `literature_id`
- `author_id`
- `author_order`

Notes:

- `author.orcid` is unique
- `literature` has normalized indexes for DOI and ISBN lookup
- `literature_author` uses a composite primary key on `(author_id, literature_id)`
- `literature_author(literature_id, author_order)` is also unique

### 3.5 Software, Workflow Tools, and Levels of Theory

`software` fields:

- `id`
- `name`
- `website`
- `description`
- `created_at`

`software_release` fields:

- `id`
- `software_id`
- `version`
- `revision`
- `build`
- `release_date`
- `notes`
- `created_at`

`workflow_tool` fields:

- `id`
- `name`
- `description`
- `created_at`

`workflow_tool_release` fields:

- `id`
- `workflow_tool_id`
- `version`
- `git_commit`
- `release_date`
- `notes`
- `created_at`

`level_of_theory` fields:

- `id`
- `method`
- `basis`
- `aux_basis`
- `cabs_basis`
- `dispersion`
- `solvent`
- `solvent_model`
- `keywords`
- `lot_hash`
- `created_at`

Notes:

- `software.name` and `workflow_tool.name` are unique
- software release dedupe is enforced on `(software_id, version, revision, build)`
- workflow-tool release dedupe is enforced on `(workflow_tool_id, version, git_commit)`
- `lot_hash` is unique

### 3.6 Application Users and Upload Jobs

`app_user` fields:

- `id`
- `username`
- `email`
- `full_name`
- `affiliation`
- `orcid`
- `api_key_hash`
- `role`
- `created_at`

`upload_job` fields:

- `id`
- `status`
- `kind`
- `payload`
- `created_by`
- `created_at`
- `started_at`
- `completed_at`
- `result`
- `error`
- `attempts`
- `max_attempts`

Notes:

- `username`, `email`, and `orcid` are unique when present
- `role` defaults to `user`
- `upload_job.id` is UUID-backed
- `upload_job` is an async queue table indexed by `(status, created_at)`

## 4. Reaction and Transition-State Identity

### 4.1 Reaction Family and Chem Reaction

`reaction_family` fields:

- `id`
- `name`
- `created_at`

`chem_reaction` fields:

- `id`
- `stoichiometry_hash`
- `reversible`
- `reaction_family_id`
- `reaction_family_raw`
- `reaction_family_source_note`
- `created_at`

Notes:

- `reaction_family.name` is unique
- `stoichiometry_hash` is unique when present
- if `reaction_family_raw` is set, `reaction_family_source_note` is required

### 4.2 Reaction Entry and Participants

`reaction_entry` fields:

- `id`
- `reaction_id`
- `created_at`
- `created_by`

`reaction_participant` fields:

- `reaction_id`
- `species_id`
- `role`
- `stoichiometry`

`reaction_entry_structure_participant` fields:

- `id`
- `reaction_entry_id`
- `species_entry_id`
- `role`
- `participant_index`
- `note`
- `created_at`
- `created_by`

Notes:

- `reaction_participant` is the compressed graph-level stoichiometric summary
- `reaction_participant` uses `(reaction_id, role, species_id)` as its composite primary key
- `reaction_participant.stoichiometry >= 1`
- `reaction_entry_structure_participant` is the ordered, entry-level species-entry assignment layer
- `reaction_entry_structure_participant` is unique on `(reaction_entry_id, role, participant_index)`
- `participant_index >= 1`

### 4.3 Transition State and Transition State Entry

`transition_state` fields:

- `id`
- `reaction_entry_id`
- `label`
- `note`
- `created_at`
- `created_by`

`transition_state_entry` fields:

- `id`
- `transition_state_id`
- `charge`
- `multiplicity`
- `mol`
- `unmapped_smiles`
- `status`
- `created_at`
- `created_by`

`transition_state_selection` fields:

- `id`
- `transition_state_id`
- `transition_state_entry_id`
- `selection_kind`
- `note`
- `created_at`
- `created_by`

Notes:

- `transition_state` is the reaction-channel-level TS concept
- `transition_state_entry` stores one candidate TS geometry family member
- `transition_state_entry.multiplicity >= 1`
- `transition_state_selection` is unique on `(transition_state_id, selection_kind)`
- a composite reference enforces that the selected entry belongs to the same `transition_state_id`

## 5. Conformer and Review Layer

### 5.1 Conformer Assignment Scheme

Fields:

- `id`
- `name`
- `version`
- `scope`
- `description`
- `parameters_json`
- `code_commit`
- `is_default`
- `created_at`
- `created_by`

Notes:

- dedupe is enforced on `(name, version)`
- the table stores versioned metadata for grouping or selection logic

### 5.2 Conformer Group, Observation, and Selection

`conformer_group` fields:

- `id`
- `species_entry_id`
- `label`
- `note`
- `representative_fingerprint_json`
- `representative_coords_json`
- `created_at`
- `created_by`

`conformer_observation` fields:

- `id`
- `conformer_group_id`
- `assignment_scheme_id`
- `scientific_origin`
- `note`
- `torsion_fingerprint_json`
- `created_at`
- `created_by`

`conformer_selection` fields:

- `id`
- `conformer_group_id`
- `assignment_scheme_id`
- `selection_kind`
- `note`
- `created_at`
- `created_by`

Notes:

- `conformer_group` is indexed by `species_entry_id`
- `conformer_group` is unique on `(species_entry_id, label)`
- `conformer_group` is the deduplicated conformational-basin identity for one `species_entry`
- `conformer_observation` is one provenance-bearing uploaded or imported observation assigned to a group; multiple observations per group are expected and valid
- matching an existing basin reuses the `conformer_group` only; distinct uploads must not be silently collapsed into one shared `conformer_observation` merely because they land in the same basin
- `conformer_observation` no longer stores `calculation_id`; `calculation.conformer_observation_id` carries the optional anchor to the specific observation the calculation came with
- `conformer_selection` is unique on `(conformer_group_id, assignment_scheme_id, selection_kind)`

### 5.3 Species Entry Review

Fields:

- `id`
- `species_entry_id`
- `user_id`
- `role`
- `note`
- `created_at`

Notes:

- review dedupe is enforced on `(species_entry_id, user_id, role)`

## 6. Submission and Moderation Layer

### 6.0 Two ingest planes

TCKDB has two distinct ingestion paths. They share the same scientific
schema and both populate `created_by` from the authenticated `app_user`,
but they differ in moderation lifecycle:

| Plane | Routes | Submission lifecycle | Intended actor |
|-------|--------|----------------------|----------------|
| Direct ingest | `/api/v1/uploads/*`, `/api/v1/calculations/{id}/artifacts` | None — `submission*` tables remain empty | Trusted workflow tools (e.g. ARC), curators, admins via API key |
| Moderated submission | `/api/v1/bundles/submit`, `/api/v1/submissions/*` | `submission` + `submission_audit_event` + `submission_record_link` rows are created | Public/community contributors |

Direct ingest writes scientific records that are immediately live and
attributed via `created_by`, with no review step. Moderated submission
wraps the same scientific workflows with a `submission` shell so a
curator can later approve, reject, or supersede the contribution; the
scientific rows are still attributed via `created_by` independently of
the submission's status.

`tckdb-client` currently only targets the direct-ingest plane. The
moderated plane is consumed by the contribution-bundle endpoint and the
new `/submissions/*` curator API exposed by
`backend/app/api/routes/submissions.py`.

### 6.1 Submission

Fields:

- `id`
- `created_by`
- `submission_kind`
- `source_kind`
- `upload_job_id`
- `status`
- `title`
- `summary`
- `submitted_at`
- `approved_at`
- `approved_by`
- `rejected_at`
- `rejected_by`
- `rejection_reason`
- `correction_due_at`
- `supersedes_submission_id`
- `llm_precheck_label`
- `llm_precheck_summary`
- `llm_precheck_model`
- `llm_precheck_at`
- `created_at`

Notes:

- `submission` represents one moderated user contribution event
- it links optionally to an `upload_job` — the column is reserved for a future
  async-moderated path; no current ingest path populates it
- indexes support lookup by creator, upload job, and `(status, created_at)`
- approving or rejecting your own submission is forbidden
- rejected submissions must include a `rejection_reason`
- the `llm_precheck_*` columns are reserved for future optional automated
  review; they are not part of the MVP and no current route or background
  process populates them. Current moderation is curator-driven.

### 6.2 Submission Audit Event

Fields:

- `id`
- `submission_id`
- `created_at`
- `actor_user_id`
- `actor_kind`
- `event_kind`
- `from_status`
- `to_status`
- `reason`
- `summary`
- `details_json`
- `related_submission_id`

Notes:

- `submission_audit_event` is append-only lifecycle history
- indexes exist on `submission_id` and `event_kind`

### 6.3 Submission Record Link

Fields:

- `id`
- `submission_id`
- `record_type`
- `record_id`
- `role`
- `created_at`

Notes:

- this table maps a submission to created or affected scientific records
- lookup is indexed both by submission and by `(record_type, record_id)`
- dedupe is enforced on `(submission_id, record_type, record_id, role)`

### 6.4 Record Review

Per-record consumer-facing trust state. Distinct from `submission.status`
(lifecycle of a contribution event) and from `species_entry_review`
(per-species attribution of who reviewed in what role).

Fields:

- `id`
- `record_type` — `submission_record_type` enum (reused from
  `submission_record_link` so the link table and review table share one
  vocabulary)
- `record_id`
- `status` — `record_review_status` enum:
  `not_reviewed | under_review | approved | rejected | deprecated`
- `submission_id` — nullable; populated for review rows attached to a
  moderated submission (lifecycle linkage)
- `reviewed_by` — nullable; set only for terminal statuses
- `reviewed_at` — nullable; set only for terminal statuses
- `note`
- `created_at`
- `created_by`

Notes:

- exactly one current-state row per `(record_type, record_id)` —
  enforced by `UNIQUE (record_type, record_id)`
- terminal statuses (`approved`, `rejected`, `deprecated`) require
  `reviewed_by` and `reviewed_at` (CHECK constraint)
- historical state changes are not persisted in this MVP — submission
  audit events remain the longitudinal record for moderated paths
- indexes exist on `(status, record_type)` and on `submission_id`

#### Three review/moderation concepts (do not conflate)

| Field | Meaning |
|-------|---------|
| `created_by` (on scientific rows) | Uploader/creator attribution |
| `submission.status` | Lifecycle of a moderated contribution event |
| `record_review.status` | Consumer-facing trust state of one scientific record |
| `species_entry_review` | Per-species attribution of who reviewed in what role |

#### Default statuses by ingest path

| Path | Initial `record_review.status` |
|------|--------------------------------|
| Direct `/api/v1/uploads/*` (trusted) | `not_reviewed` (no submission row) |
| Moderated `/api/v1/bundles/submit` | `under_review` (linked to the new submission) |

#### Status transitions on submission lifecycle

| Submission action | Effect on linked records' `record_review.status` |
|-------------------|--------------------------------------------------|
| `approve_submission` | linked records → `approved`; if approval is of a *superseding* submission, the prior submission's linked records → `deprecated` |
| `reject_submission` | linked records → `rejected` |
| `supersede_submission` | no review-state change — the prior submission's records keep their current status until the replacing submission is itself approved |

This deferred-deprecation policy avoids hiding good data when a
correction is uploaded but later rejected.

#### Allowed manual status transitions (curator/admin)

`set_record_review_status` (and the `PATCH /record-reviews/...` route
that wraps it) enforces a small allowed-transition set:

```
not_reviewed → under_review | approved | rejected | deprecated
under_review → approved | rejected | not_reviewed
approved     → under_review | deprecated
rejected     → under_review | deprecated
deprecated   → under_review | approved
```

Disallowed by default (route through `under_review` first):

```
approved → rejected
rejected → approved
deprecated → rejected
```

A curator/admin who is also `created_by` for a record cannot transition
that record to `approved` (self-approval guard). Other transitions are
allowed for the creator if they meet the role check —
self-deprecation and self-reopening are not the same trust problem.

#### Coverage

Direct uploads create review rows for the primary scientific records
they produce: `species_entry`, `reaction_entry`, `transition_state`,
`transition_state_entry`, `conformer_group`, `conformer_observation`,
`calculation`, `thermo`, `statmech`, `transport`, `kinetics`, `network`,
`network_solve`, and `applied_energy_correction`.

Calculation artifacts and pure normalized-expansion child tables
(`thermo_point`, `thermo_nasa`, `thermo_source_calculation`,
`statmech_source_calculation`, `statmech_torsion_definition`,
`kinetics_source_calculation`, `calc_scan_point`,
`calc_scan_point_coordinate_value`) inherit the trust of their parent
record and do not get their own review rows.

## 7. Calculation Layer

### 7.1 Calculation

Fields:

- `id`
- `type`
- `quality`
- `species_entry_id`
- `transition_state_entry_id`
- `software_release_id`
- `workflow_tool_release_id`
- `lot_id`
- `literature_id`
- `conformer_observation_id`
- `parameters_json`
- `parameters_parser_version`
- `parameters_extracted_at`
- `created_at`
- `created_by`

Notes:

- a calculation is owned by exactly one of `species_entry` or `transition_state_entry`
- `quality` defaults to `raw`
- the row can optionally point back to the specific conformer observation it supports
- `conformer_observation_id` is not unique; one observation may own many calculations, while each calculation has zero or one observation anchor

### 7.2 Geometry Link Tables

`calculation_input_geometry` fields:

- `calculation_id`
- `geometry_id`
- `input_order`

`calculation_output_geometry` fields:

- `calculation_id`
- `geometry_id`
- `output_order`
- `role`

Notes:

- input rows are keyed by `(calculation_id, input_order)` and unique on `(calculation_id, geometry_id)`
- output rows are keyed by `(calculation_id, output_order)` and unique on `(calculation_id, geometry_id)`
- `input_order >= 1` and `output_order >= 1`

### 7.3 Calculation Parameters, Constraints, Dependencies, and Artifacts

`calculation_parameter_vocab` fields:

- `canonical_key`
- `description`
- `expected_value_type`
- `affects_scientific_result`
- `affects_numerics`
- `affects_resources`
- `note`
- `created_at`

`calculation_parameter` fields:

- `id`
- `calculation_id`
- `raw_key`
- `canonical_key`
- `raw_value`
- `canonical_value`
- `section`
- `value_type`
- `unit`
- `parameter_index`
- `created_at`

`calculation_constraint` fields:

- `calculation_id`
- `constraint_index`
- `constraint_kind`
- `atom1_index`
- `atom2_index`
- `atom3_index`
- `atom4_index`
- `target_value`

`calculation_dependency` fields:

- `parent_calculation_id`
- `child_calculation_id`
- `dependency_role`

`calculation_artifact` fields:

- `id`
- `calculation_id`
- `kind`
- `uri`
- `sha256`
- `bytes`
- `created_at`

Notes:

- `calculation_parameter_vocab` is keyed directly by `canonical_key`
- `calculation_parameter` is an EAV-style parsed-parameter store with indexes on calculation, raw-key/section, and canonical key/value
- `parameter_index` must be null or non-negative
- One raw token may emit multiple canonical observations. Gaussian `IOp(5/13=1)` (instructs the SCF to continue when convergence fails) is stored as three rows: a generic `internal_option.iop` row plus two specialized canonicals — `scf.convergence_failure_ignored = true` and `scf.convergence_failure_action = continue`. This is a calculation **trust** flag, not SCF wavefunction stability evidence; it must not populate `calc_scf_stability`. Future high-severity trust flags may be promoted into a dedicated `calculation_diagnostic_flag` / read-warning surface.
- `calculation_constraint` uses `(calculation_id, constraint_index)` as its composite primary key
- constraint-arity checks enforce valid atom usage for cartesian, bond, angle, and dihedral/improper constraints
- `calculation_dependency` prevents self-edges
- selected dependency roles enforce one-parent-per-child semantics through filtered unique indexes in PostgreSQL; DBML can only show the named indexes, not their predicates

Ownership of geometric coordinate metadata across calculation tables:

- `calculation_constraint` is the canonical store for input coordinates **held fixed** during a calculation. It is generic across opt, TS, scan, IRC, path-search (NEB / GSM / string methods), and any other constrained run. A scan with one or more frozen coordinates writes both: the stepped coordinate(s) into `calc_scan_coordinate` and the held-fixed coordinate(s) into `calculation_constraint`. The two never duplicate the same coordinate.
- `calc_scan_coordinate` is the canonical store for the **active scan grid** (which coordinate is stepped, step size, start/end, symmetry hints). Only scan calculations write rows here.
- `calc_scan_point` plus `calc_scan_point_coordinate_value` are the canonical store for **scan output**: per-point energy/geometry and the observed coordinate values along the scanned grid. They are write-once results, not input metadata.
- `statmech_torsion` (with `statmech_torsion_definition`) is the canonical store for **thermochemical rotor interpretation** — the fitted treatment kind (hindered, free, rigid top), symmetry number, and dimension. It may reference its source scan via `source_scan_calculation_id` but does not replace scan-grid storage or constraint storage; it is a downstream interpretation, not a copy.

### 7.4 Direct Calculation Result Tables

`calc_sp_result` fields:

- `calculation_id`
- `electronic_energy_hartree`
- `electronic_energy_uncertainty_hartree`

`calc_opt_result` fields:

- `calculation_id`
- `converged`
- `n_steps`
- `final_energy_hartree`

`calc_freq_result` fields:

- `calculation_id`
- `n_imag`
- `imag_freq_cm1`
- `zpe_hartree`
- `zpe_uncertainty_hartree`

`calc_freq_mode` fields (per-mode vibrational data, optional sibling of
`calc_freq_result`):

- `calculation_id`
- `mode_index` (1-based, unique per calculation)
- `frequency_cm1` (negative for imaginary modes)
- `is_imaginary` (boolean; sign-consistent with `frequency_cm1` via DB CHECK)
- `reduced_mass_amu`
- `force_constant_mdyne_angstrom`
- `ir_intensity_km_mol`
- `raman_activity`
- `symmetry_label`
- `note`

Convention: imaginary modes are stored as **negative** `frequency_cm1`
together with `is_imaginary = true`. The two-way constraint
`(is_imaginary AND frequency_cm1 < 0) OR (NOT is_imaginary AND frequency_cm1 >= 0)`
is enforced at the DB. Producers that only have positive magnitudes
must flip the sign before upload; the Pydantic
`FrequencyModePayload` validator refuses inconsistent combinations.
When both `n_imag` and `modes` are supplied on a freq result, the
imaginary mode count must agree with `n_imag`. Mode rows are optional
— existing payloads without `modes` continue to validate and persist
exactly as before.

`calc_geometry_validation` fields:

- `calculation_id`
- `input_geometry_id`
- `output_geometry_id`
- `species_smiles`
- `is_isomorphic`
- `rmsd`
- `atom_mapping`
- `n_mappings`
- `validation_status`
- `validation_reason`
- `rmsd_warning_threshold`
- `created_at`

`calc_geometry_validation` is a *structure-consistency* check: does the
calculation's output geometry still represent the declared species
identity (graph isomorphism, with RMSD as a suspicion signal)? It exists
to catch optimizations that rearranged the molecule, broke or formed
bonds, dissociated, or transferred a proton.

A `validation_status=fail` row means "the automated identity validator
found a mismatch," **not** "the calculation is scientifically invalid."
Connectivity perception from XYZ is imperfect for weak complexes,
stretched or partially broken bonds, radicals, charged species, loose
conformers, and proton-transfer-like geometries — these can produce
false-positive `fail` rows even when the calculation is fine. These
rows are curator-attention signals, not inputs to automatic rejection
or quality gating. Phase-1 wiring records evidence; it never blocks an
upload.

Three closely related but distinct calculation-quality surfaces must not
be conflated:

- **Geometry validation** — molecular identity / connectivity preservation.
  Table: `calc_geometry_validation`.
- **SCF stability** — electronic wavefunction stability with respect to
  orbital rotations (Gaussian `Stable` / `Stable=Opt`, ORCA stability
  analysis). Table: `calc_scf_stability`.
- **Frequency validation** — nuclear Hessian / stationary-point character
  (number of imaginary modes, etc.). Lives on `calc_freq_result` and
  related fields, not in a dedicated validation table.

`calc_scf_stability` fields:

- `calculation_id`
- `status` (`scf_stability_status` enum: `stable`, `unstable`,
  `stabilized`, `inconclusive`; the read API also projects `not_checked`
  when no row exists)
- `lowest_eigenvalue`
- `instability_count`
- `instability_type`
- `reoptimized_wavefunction`
- `source_calculation_id`
- `source_artifact_id`
- `note`
- `created_at`, `created_by_id`

A row in `calc_scf_stability` exists only when a stability analysis was
actually attempted; absence of a row is the canonical encoding of
"not checked" and is projected as `status = "not_checked"` by the read
API. Ordinary SCF convergence is not stability evidence and must not
populate this table; `IOp(5/13=1)`-style trust flags (see §7.3) are also
not stability evidence.

`calc_irc_result` fields:

- `calculation_id`
- `direction`
- `has_forward`
- `has_reverse`
- `ts_point_index`
- `point_count`
- `zero_energy_reference_hartree`
- `note`

`calc_irc_point` fields:

- `calculation_id`
- `point_index`
- `direction`
- `is_ts`
- `reaction_coordinate`
- `electronic_energy_hartree`
- `relative_energy_kj_mol`
- `max_gradient`
- `rms_gradient`
- `geometry_id`
- `note`

`calc_scan_result` fields:

- `calculation_id`
- `dimension`
- `is_relaxed`
- `zero_energy_reference_hartree`
- `note`

`calc_scan_coordinate` fields:

- `calculation_id`
- `coordinate_index`
- `coordinate_kind`
- `atom1_index`
- `atom2_index`
- `atom3_index`
- `atom4_index`
- `step_count`
- `step_size`
- `start_value`
- `end_value`
- `value_unit`
- `resolution_degrees`
- `symmetry_number`

`calc_scan_point` fields:

- `calculation_id`
- `point_index`
- `electronic_energy_hartree`
- `relative_energy_kj_mol`
- `geometry_id`
- `note`

`calc_scan_point_coordinate_value` fields:

- `calculation_id`
- `point_index`
- `coordinate_index`
- `coordinate_value`
- `value_unit`

### Path-search calculations (NEB / GSM / string methods)

A path-search calculation explores a reaction path between or from
molecular endpoints to produce a TS guess. NEB and GSM (and growing/
freezing-string variants) are *methods* of a path-search calculation,
not separate top-level calculation provenance concepts. They share one
result table family, with the algorithm carried as data on
`calc_path_search_result.method`:

```text
calculation.type = path_search
calc_path_search_result.method ∈ {neb, gsm, growing_string, freezing_string, other}
```

A path-search calculation may serve as the parent of a TS optimization
through `calculation_dependency.role = optimized_from`:

```text
ts_guess(path_search) ──optimized_from──▶ ts_opt(opt)
```

Heuristic / template / user-supplied TS guesses remain geometry-only —
they are not modelled as path-search calculations unless a real
calculation was run.

`calc_path_search_result` fields:

- `calculation_id`
- `method`
- `is_double_ended`
- `converged`
- `n_points`
- `selected_ts_point_index`
- `climbing_image_index`
- `source_endpoint_count`
- `zero_energy_reference_hartree`
- `note`

`calc_path_search_point` fields:

- `calculation_id`
- `point_index`
- `electronic_energy_hartree`
- `relative_energy_kj_mol`
- `path_coordinate`
- `max_force`
- `rms_force`
- `max_gradient`
- `rms_gradient`
- `is_ts_guess`
- `is_climbing_image`
- `geometry_id`
- `note`

Notes:

- direct result tables use `calculation_id` as the primary key when the relationship is one-to-one
- point/image tables use composite keys to preserve ordering within one calculation
- scan-coordinate and general-constraint tables both enforce atom-index arity rules with check constraints
- `calc_scan_point_coordinate_value` has composite references back to both `calc_scan_coordinate` and `calc_scan_point`

## 8. Scientific Product Tables

### 8.1 Statmech

`statmech` fields:

- `id`
- `species_entry_id`
- `scientific_origin`
- `literature_id`
- `workflow_tool_release_id`
- `software_release_id`
- `external_symmetry`
- `point_group`
- `is_linear`
- `rigid_rotor_kind`
- `statmech_treatment`
- `frequency_scale_factor_id`
- `uses_projected_frequencies`
- `note`
- `created_at`
- `created_by`

`statmech_source_calculation` fields:

- `statmech_id`
- `calculation_id`
- `role`

`statmech_torsion` fields:

- `id`
- `statmech_id`
- `torsion_index`
- `symmetry_number`
- `treatment_kind`
- `dimension`
- `top_description`
- `invalidated_reason`
- `note`
- `source_scan_calculation_id`

`statmech_torsion_definition` fields:

- `torsion_id`
- `coordinate_index`
- `atom1_index`
- `atom2_index`
- `atom3_index`
- `atom4_index`

Notes:

- `external_symmetry >= 1` when present
- `statmech_torsion` is unique on `(statmech_id, torsion_index)`
- `torsion_index >= 1`, `dimension >= 1`, and `symmetry_number >= 1` when present

### 8.2 Thermo

`thermo` fields:

- `id`
- `species_entry_id`
- `scientific_origin`
- `literature_id`
- `workflow_tool_release_id`
- `software_release_id`
- `h298_kj_mol`
- `s298_j_mol_k`
- `h298_uncertainty_kj_mol`
- `s298_uncertainty_j_mol_k`
- `tmin_k`
- `tmax_k`
- `note`
- `created_at`
- `created_by`

`thermo_point` fields:

- `thermo_id`
- `temperature_k`
- `cp_j_mol_k`
- `h_kj_mol`
- `s_j_mol_k`
- `g_kj_mol`

`thermo_nasa` fields:

- `thermo_id`
- `t_low`
- `t_mid`
- `t_high`
- `a1` through `a7`
- `b1` through `b7`

`thermo_source_calculation` fields:

- `thermo_id`
- `calculation_id`
- `role`

Notes:

- thermo uncertainties must be non-negative when present
- `tmin_k` and `tmax_k` must be positive when present, with `tmin_k <= tmax_k`
- NASA temperature bounds must be all present or all absent
- if present, `t_low < t_mid < t_high`

### 8.3 Transport

Fields:

- `id`
- `species_entry_id`
- `scientific_origin`
- `literature_id`
- `software_release_id`
- `workflow_tool_release_id`
- `sigma_angstrom`
- `epsilon_over_k_k`
- `dipole_debye`
- `polarizability_angstrom3`
- `rotational_relaxation`
- `note`
- `created_at`
- `created_by`

Related table:

- `transport_source_calculation(transport_id, calculation_id, role)`

Notes:

- `sigma_angstrom` and `epsilon_over_k_k` must be both present or both absent
- `sigma_angstrom > 0` and `epsilon_over_k_k > 0` when present
- `rotational_relaxation >= 0` when present

### 8.4 Kinetics

Fields:

- `id`
- `reaction_entry_id`
- `scientific_origin`
- `model_kind`
- `literature_id`
- `workflow_tool_release_id`
- `software_release_id`
- `a`
- `a_units`
- `n`
- `ea_kj_mol`
- `a_uncertainty`
- `n_uncertainty`
- `ea_uncertainty_kj_mol`
- `tmin_k`
- `tmax_k`
- `degeneracy`
- `tunneling_model`
- `note`
- `created_at`
- `created_by`

Related table:

- `kinetics_source_calculation(kinetics_id, calculation_id, role)`

Notes:

- `model_kind` is enum-backed (`arrhenius` or `modified_arrhenius`)
- `a_units` uses the `ArrheniusAUnits` enum
- temperature bounds must be positive when present, with `tmin_k <= tmax_k`

## 9. Network and Pressure-Dependent Layer

### 9.1 Network Identity and Membership

`network` fields:

- `id`
- `name`
- `description`
- `literature_id`
- `software_release_id`
- `workflow_tool_release_id`
- `created_at`
- `created_by`

`network_reaction` fields:

- `network_id`
- `reaction_entry_id`

`network_species` fields:

- `network_id`
- `species_entry_id`
- `role`

`network_state` fields:

- `id`
- `network_id`
- `kind`
- `composition_hash`
- `label`

`network_state_participant` fields:

- `state_id`
- `species_entry_id`
- `stoichiometry`

Notes:

- `network_reaction` is keyed by `(network_id, reaction_entry_id)`
- `network_species` is keyed by `(network_id, role, species_entry_id)`
- `network_state` is unique on `(network_id, composition_hash)`
- `network_state_participant.stoichiometry >= 1`

### 9.2 Network Channels and Solves

`network_channel` fields:

- `id`
- `network_id`
- `source_state_id`
- `sink_state_id`
- `kind`

`network_solve` fields:

- `id`
- `network_id`
- `literature_id`
- `software_release_id`
- `workflow_tool_release_id`
- `me_method`
- `interpolation_model`
- `grain_size_cm_inv`
- `grain_count`
- `emax_kj_mol`
- `tmin_k`
- `tmax_k`
- `pmin_bar`
- `pmax_bar`
- `note`
- `created_at`
- `created_by`

`network_solve_bath_gas` fields:

- `solve_id`
- `species_entry_id`
- `mole_fraction`

`network_solve_energy_transfer` fields:

- `id`
- `solve_id`
- `model`
- `alpha0_cm_inv`
- `t_exponent`
- `t_ref_k`
- `note`

`network_solve_source_calculation` fields:

- `solve_id`
- `calculation_id`
- `role`

Notes:

- `network_channel` is unique on `(network_id, source_state_id, sink_state_id)`
- `network_channel` forbids `source_state_id = sink_state_id`
- solve temperature and pressure bounds must be positive when present, with `tmin_k <= tmax_k` and `pmin_bar <= pmax_bar`
- `grain_count >= 1` when present
- bath-gas mole fractions must satisfy `0 < mole_fraction <= 1`

### 9.3 Network Kinetics

`network_kinetics` fields:

- `id`
- `channel_id`
- `solve_id`
- `model_kind`
- `tmin_k`
- `tmax_k`
- `pmin_bar`
- `pmax_bar`
- `rate_units`
- `pressure_units`
- `temperature_units`
- `stores_log10_k`
- `note`
- `created_at`

`network_kinetics_chebyshev` fields:

- `network_kinetics_id`
- `n_temperature`
- `n_pressure`
- `coefficients`

`network_kinetics_plog` fields:

- `network_kinetics_id`
- `pressure_bar`
- `entry_index`
- `a`
- `a_units`
- `n`
- `ea_kj_mol`

`network_kinetics_point` fields:

- `network_kinetics_id`
- `temperature_k`
- `pressure_bar`
- `rate_value`

Notes:

- `network_kinetics` carries the shared metadata for one channel/solve fit
- temperature and pressure bounds must be positive when present and ordered
- Chebyshev dimensions must be at least 1
- PLOG pressure must be positive and `entry_index >= 1`
- tabulated points require positive `temperature_k` and `pressure_bar`

## 10. Energy-Correction Layer

### 10.1 Frequency Scale Factor

Fields:

- `id`
- `level_of_theory_id`
- `software_id`
- `scale_kind`
- `value`
- `source_literature_id`
- `workflow_tool_release_id`
- `note`
- `created_at`
- `created_by`

Notes:

- dedupe is enforced on `(level_of_theory_id, software_id, scale_kind, value, source_literature_id, workflow_tool_release_id)`
- `value > 0`

### 10.2 Energy Correction Scheme

`energy_correction_scheme` fields:

- `id`
- `kind`
- `name`
- `level_of_theory_id`
- `source_literature_id`
- `version`
- `units`
- `note`
- `created_at`
- `created_by`

Related parameter tables:

- `energy_correction_scheme_atom_param(scheme_id, element, value)`
- `energy_correction_scheme_bond_param(scheme_id, bond_key, value)`
- `energy_correction_scheme_component_param(scheme_id, component_kind, key, value)`

Notes:

- scheme dedupe is enforced on `(kind, name, level_of_theory_id, version)`
- the parameter tables normalize element-, bond-, and component-level correction coefficients

### 10.3 Applied Energy Correction

`applied_energy_correction` fields:

- `id`
- `target_species_entry_id`
- `target_reaction_entry_id`
- `source_conformer_observation_id`
- `source_calculation_id`
- `scheme_id`
- `frequency_scale_factor_id`
- `application_role`
- `value`
- `value_unit`
- `temperature_k`
- `note`
- `created_at`
- `created_by`

`applied_energy_correction_component` fields:

- `id`
- `applied_correction_id`
- `component_kind`
- `key`
- `multiplicity`
- `parameter_value`
- `contribution_value`

Notes:

- exactly one target must be set: species entry or reaction entry
- exactly one provenance source must be set: scheme or frequency scale factor
- the main table has a composite dedupe index spanning target, source, role, temperature, and provenance source
- `temperature_k > 0` when present
- `applied_energy_correction_component.multiplicity >= 1`

## 11. Important Integrity Rules

- `calculation` ownership is exclusive between species-entry and transition-state-entry paths
- `transition_state_selection` must point to an entry under the same transition state
- `species_entry` dedupe is enforced on the resolved identity tuple rather than raw provenance text
- `conformer_group` labels are unique within a species entry
- `conformer_selection` dedupes by group, scheme, and selection kind
- `submission` moderation forbids creator self-approval and creator self-rejection
- `submission_record_link` provides the normalized mapping from moderation objects to scientific records
- `reaction_participant` and `network_state_participant` both enforce stoichiometry positivity
- scan-coordinate and calculation-constraint tables both enforce atom-arity rules with database checks
- network solve and network kinetics tables enforce positive and ordered temperature/pressure ranges
- applied energy corrections enforce exactly one target and exactly one provenance source

## 12. Current Semantic Model

- `species` is graph identity; `species_entry` is resolved scientific meaning
- `chem_reaction` is graph identity; `reaction_entry` is a concrete curated/uploaded entry
- `transition_state` is reaction-entry-centered; `transition_state_entry` is one candidate structure
- `conformer_group` is basin identity; observations and selections add provenance and curation
- `calculation` stores provenance and ownership; result/link tables hold structured ESS outputs
- `statmech`, `thermo`, `transport`, `kinetics`, `network`, and `applied_energy_correction` are scientific product layers built on top of identity and provenance tables
- `submission`, `submission_audit_event`, and `submission_record_link` are the moderation/publication layer for all contributed records
