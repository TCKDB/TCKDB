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
- `execution_environment_manifest` is an immutable, SHA-256 content-addressed closure shared by calculations; its optional FK preserves legacy uploads while allowing a calculation-level rerun claim when inputs, parameters, dependencies, and the closed runtime all exist.
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
- `kind` (`molecule_kind` enum) is `molecule`, `pseudo`, or `electron`.
  `pseudo` is a lumped or phenomenological construct whose composition and
  charge are not atom-resolved facts, so `validate_reaction_elemental_balance`
  and `validate_reaction_charge_conservation` decline to judge a reaction
  containing one. `electron` is a free electron: it has no SMILES (the row
  carries the reserved token `[e-]`) and no InChI (the row carries a sentinel
  that is deliberately not InChIKey-shaped), and it exists so that associative
  detachment, dissociative attachment, photoionization and photodetachment can
  be deposited as balanced reactions. An electron exempts a reaction from
  **nothing** — it contributes zero atoms and charge −1, and both conservation
  checks still have to pass. See revision `b8f3d6a1c9e4`.

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
- `isotope_key`
- `isotopologue_label` (deprecated)
- `created_at`
- `created_by`

Notes:

- `species_entry` stores one resolved stereochemical, electronic-state, or isotopic form of a species
- `species_id` is indexed
- dedupe is enforced on `(species_id, stereo_label, electronic_state_kind, electronic_state_label, term_symbol, isotope_key)`
- `isotope_key` is the canonical SMILES of the isotope-labelled identity
  molecule (e.g. `[2H]C([2H])([2H])O`), derived server-side from the isotope
  labels in the uploaded SMILES. `NULL` means every atom is at its most
  abundant natural isotope, and is the stable all-standard key
- `isotopologue_label` is deprecated: it is no longer part of identity, is not
  accepted on any upload/Create/Update schema, and is never written by the
  application. It is retained only so deployments that already stored
  annotations there do not lose them

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
- `isotope_mass_number`

Notes:

- `geom_hash` is unique
- `natoms >= 1`
- `geometry_atom` uses a composite primary key on `(atom_index, geometry_id)`
- `geometry_atom.atom_index >= 1`
- `geometry_atom.isotope_mass_number` is the nuclide for that atom. `NULL`
  means the element's most abundant natural isotope — not "unknown" — so no
  backfill of pre-existing geometries is required. It is the per-atom mass a
  downstream normal-mode analysis needs
- `geom_hash` covers the isotope labelling: the hashed canonical text gains an
  `ISOTOPES` suffix only when a substitution is present, so hashes of ordinary
  geometries are unchanged while two identically-positioned but differently
  labelled geometries do not dedupe onto one another

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
- `spin_treatment` (nullable)
- `core_treatment` (nullable: `frozen_core | all_electron`)
- `lot_hash`
- `created_at`

Notes:

- `software.name` and `workflow_tool.name` are unique
- software release dedupe is enforced on `(software_id, version, revision, build)`
- workflow-tool release dedupe is enforced on `(workflow_tool_id, version, git_commit)`
- `lot_hash` is unique
- `lot_hash` hashes each basis name (`basis`, `aux_basis`, `cabs_basis`) by its identity key, not verbatim: lower case, with the family hyphen in `def2-` and `cc-p` restored (`app/chemistry/basis_set_names.py`, #574). `def2tzvp` and `def2-TZVP` are one level of theory; `6-31G*` and `6-31G**` stay two. The row stores the first spelling it was uploaded with.
- `core_treatment` (ADR 0021) joins the `lot_hash` payload **only when it is set**. A NULL adds no key, so every level that did not state it keeps the hash it had before the column existed; no row was re-keyed. `frozen_core` and `all_electron` are two levels, and "not stated" is a third that is neither. A partial core treatment (an energy window, Gaussian `FC=1`) has no value; it stays NULL and is described in `keywords`. The column is in `snapshot_defaults.UNCHANGED_DEFAULTS` so a whole-row digest of a level does not go stale for a NULL. `scripts/ops/merge_duplicate_levels_of_theory.py` regroups by the recomputed hash and lists the column, so it never folds one treatment into the other.
- A level of theory's `public_ref` is minted from `lot_hash` **once, at insert**, and never recomputed. Revision `38b06819f099` re-keyed every row whose basis spelling differs from its identity key and left `public_ref` alone, so for those rows `public_ref` is no longer what their content would mint on a fresh instance. A LOT ref identifies a row; it is not re-derivable from content after a re-key.
- `lot_hash` values of re-keyed rows changed in `38b06819f099`. Anything holding an old value (a `lot_hash=` query, an ML export row, a stored consistency-check snapshot) no longer matches.

`level_of_theory_merge` fields:

- `merged_lot_id` (PK, FK `level_of_theory.id`)
- `into_lot_id` (FK `level_of_theory.id`)
- `created_at`

Notes:

- Written only by `backend/scripts/ops/merge_duplicate_levels_of_theory.py` (#574). A merged row is kept with its `public_ref`, because a published release freezes `level_of_theory_ref` per cited calculation; every read that accepts a LOT ref resolves a merged row's ref to `into_lot_id`.
- No calculation points at a merged row, and `into_lot_id` is never itself merged (one hop).
- A table rather than a column on `level_of_theory`, so whole-row snapshots of a level of theory (consistency-check inputs, reproducibility context hashes) do not change for every row.

Composite schemes ([ADR 0021](../docs/adr/0021-composite-levels-of-theory-are-a-recipe-bound-to-a-level-not-a-weighted-level.md), revision `d7a3f1b9c284`). A composite energy is a recipe; the recipe is identity, and a side table binds a level of theory to it.

`composite_scheme` fields (identity; deduplicated by `definition_hash`, never updated in place):

- `id`, `public_ref` (`csch_`, content-derived from `definition_hash`)
- `kind` (`named_method | extrapolation | additive`), `name`
- `definition_hash` (`CHAR(64)`, unique): for a named method, `sha256` of the canonical JSON `{"kind":"named_method","method":<catalogue key>}`
- `geometry_level_of_theory_id`, `frequency_level_of_theory_id` (nullable FK `level_of_theory.id`): the levels the recipe runs internally; NULL = not stated
- `recipe_zpe_scale_factor` (nullable): NULL unless a source is cited; never read as 1.0
- `source_literature_id` (nullable FK `literature.id`), `note`, `created_at`

`composite_scheme_term` fields: `id`, `scheme_id`, `position` (unique per scheme), `operation` (`base | extrapolation | difference | value | empirical`), `energy_component` (`EnergyComponentKind`: `total | reference | correlation | triples | dboc | scalar_relativistic`), `formula` (nullable; only on an extrapolation term), `exponent` (nullable; only with a formula).

`composite_scheme_term_input` fields: `id`, `term_id`, `slot` (`value | high | low | cardinal`), `level_of_theory_id`, `cardinal_number` (nullable; required on a `cardinal` slot). Unique on `(term_id, slot, cardinal_number)` with NULLs not distinct.

`level_of_theory_composite` fields: `level_of_theory_id` (PK, FK), `scheme_id` (FK), `binding_source` (`named_method_catalogue | declared`), `created_at`.

Notes:

- A named-method scheme and its binding are created when a level of theory whose method is in `app/chemistry/composite_methods.py` is resolved (`app/services/composite_scheme_resolution.py`), and by the revision's backfill for existing levels. The scheme states only what the catalogue states and has no terms. `lot_hash` and the level's `public_ref` are untouched.
- A term input is never a composite level (bound, or whose method is catalogued): enforced by `assert_ordinary_input_level`, which every writer of `composite_scheme_term_input` goes through. A merged level is checked and stored as the level it was merged into.
- A merged level is never bound; the merge script moves the duplicate's binding to the holder.

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

### 6.0 Ingest planes

**Every accepted upload creates a `submission`** — the audit wrapper for an
upload event that produced (or attempted to produce) scientific records. The
planes share the same scientific schema and all attribute records via
`created_by`; they differ by payload shape and review default, not by whether
they are auditable. See `docs/specs/ingestion_submission_model.md` for the full
model.

| Plane | Routes | Submission / review on success | Notes |
|-------|--------|--------------------------------|-------|
| Synchronous direct | `/api/v1/uploads/*` | `submission` (`pending`) + `submission_created` + `ingestion_succeeded` + `submission_record_link` rows + `record_review` `under_review` | One submission per upload; full review-target set linked, plus artifact evidence links |
| Async job | `/api/v1/jobs/*` → worker | submission created at enqueue (`pending`, `upload_job_id` set); worker links/records on success, or marks `failed` on terminal failure | Durable failure audit |
| Moderated bundle | `/api/v1/bundles/submit`, `/api/v1/submissions/*` | `submission` (`pending`) + audit + curated `submission_record_link` + `record_review` `under_review` | Multi-record community contribution |
| Artifact attach | `/api/v1/calculations/{id}/artifacts` | none of its own | Second-phase artifact upload against an existing calculation |

Successful ingestion is **not** scientific approval: a successful submission
sits at `status = pending` with records `under_review`; a curator later
approves/rejects/supersedes via `/submissions/*`. Failed uploads are recorded
durably with `status = failed` and an `ingestion_failed` audit event, and never
leave partial scientific records.

`tckdb-client` targets the synchronous direct plane.

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

- `submission` represents one upload/import event (every accepted upload
  creates one; see §6.0)
- `status` — `submission_status` enum:
  `pending | precheck_passed | auto_flagged | approved | rejected | superseded | failed`.
  `failed` is a system-set terminal state for an upload whose ingestion failed
  (async job out of retries, or a sync upload that raised during persistence);
  it is distinct from curator `rejected` (which requires a reviewer and reason)
  and is never curator-approvable or public.
- `upload_job_id` links to the `upload_job` for async (`/jobs/*`) uploads; it is
  set at enqueue time and null for synchronous uploads
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
- `record_type` is the `submission_record_type` enum; it includes scientific
  identity/result types plus `artifact` for uploaded evidence files
- uploaded calculation artifacts are linked with `role = "artifact"` as
  contribution evidence; unlike scientific records they get **no**
  `record_review` row
- `role = NULL` rows are the review-target links; role-bearing rows are the
  bundle's curated links and artifact evidence links
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

**What `coordinate_value` holds.** It is the value of the internal coordinate
at that sampled point, in that coordinate's own unit — degrees for `angle`,
`dihedral` and `improper`, Ångström for `bond`. It is not a displacement, not an
offset, and not relative to anything. `start_value` and `end_value` are the
requested extent of the scan grid, input metadata beside `step_count` and
`step_size`; neither is an anchor.

A periodic coordinate may continue past 360° where that keeps a sweep monotone,
and a reader takes `mod 360` for the physical angle. 419.867° and 59.867° are
the same value of the same coordinate; storing the continuation preserves which
turn of a path-dependent relaxed scan a point belongs to, and makes visible that
the first and last points of a full sweep are one geometry deposited twice.

A producer whose program prints a relative sweep converts before depositing. It
necessarily holds the anchor, having computed the sweep from it. TCKDB does not
adopt a producer's internal representation; see
`docs/adr/0020-a-scan-coordinate-value-is-the-coordinate-itself.md` for why the
relative form does not generalise (a bond angle reflects at 180° rather than
wrapping, a bond length has no branch cut, and an improper dihedral has no
convention shared across codes).

**Conformance is checked, not assumed.** A stored value is compared against the
coordinate recomputed from the sampled point's own geometry. The check needs no
anchor and no producer-specific knowledge. It **warns** rather than blocking: a
mis-stated axis and a mis-attached geometry present identically, so a
disagreement says something is wrong without saying which. Its tolerance is
derived from deposit precision (six decimal places on the current corpus, a
floor of ~3.6e-5° on any recomputed dihedral) and the `1/(r sin θ)` conditioning
of the quartet, never fixed by hand; where a quartet is near-collinear the
dihedral is not a usable coordinate and the check reports **not checkable**,
which is neither a pass nor a failure.

**Legacy.** The 46 dihedral scan series deposited before 2026-08-31 were stored
on a relative axis, with the absolute dihedral of the starting geometry held in
`start_value`. They are corrected in place — exactly and invertibly, since
`start_value` is retained — by a migration that recomputes each point's dihedral
from its own geometry and converts a row only where that recomputation confirms
the result. Rows it cannot confirm are left as deposited and reported. That
rewrite of append-only result rows is a narrow, one-time exception, taken
because scan calculations carry no stored artifact (`opt` 232, `freq` 165, `sp`
164, `scan` 0, measured 2026-08-31) and so cannot be re-derived. A future
non-conforming deposit is corrected by re-depositing, not by migrating.

### 7.4 Direct Calculation Result Tables

`calc_sp_result` fields:

- `calculation_id`
- `electronic_energy_hartree`
- `electronic_energy_uncertainty_hartree`

`calc_composite_result` fields (1:1 with a `calculation` of type `composite`; ADR 0021):

- `calculation_id` (PK, FK)
- `assembly` (`composite_assembly`: `program_run | assembled`; `assembled` is arithmetic over other deposited calculations and needs a user-built scheme, see `calc_composite_input`)
- `electronic_energy_hartree` (nullable; ZPE-free, every term of the recipe included)
- `e0_hartree` (nullable; 0 K, including the recipe's scaled zero-point energy)
- `recipe_zpe_hartree` (nullable; at least 0)

`NULL` means not stated, never zero. TCKDB never stores a total it computed itself:
`e0_hartree = electronic_energy_hartree + recipe_zpe_hartree` and
`sum(terms) = electronic_energy_hartree` are checked (blocking, to printed precision: `max(1e-6, 5e-7 * n)` hartree for `n` rounded quantities, 3 for e0 and `len(terms) + 1` for the terms) when
the numbers they relate are all present
(`composite_e0_inconsistent`, `composite_terms_do_not_sum`). All three energies are
finite-checked at the database. The level of theory of a composite calculation
must be bound to a composite scheme (`composite_level_not_scheme_bound`): a
catalogued named method or a user-built scheme sent inline. All composite result tables carry the
accepted-science immutability guard `calc_sp_result` has (revision `f3b7d2a9c514`).

`calc_composite_term` fields (the optional breakdown of the ZPE-free energy):

- `calculation_id` (PK part, FK)
- `term_position` (PK part; at least 0; a `composite_scheme_term.position` of the calculation's scheme where the scheme has terms, otherwise the producer's own ordering)
- `value_hartree`

`calc_composite_input` fields (ADR 0021, P5; one row per slot an `assembled` composite filled):

- `calculation_id` (PK part, FK: the composite)
- `term_position` (PK part; at least 0; the term's place in the scheme's `terms`; the depositor's `term_key` is not stored)
- `slot` (PK part; `composite_input_slot`: `value | high | low | cardinal`)
- `input_calculation_id` (PK part, FK: the single point or optimisation that fills the slot; not the composite itself; indexed)
- `cardinal_number` (nullable; set exactly on a `cardinal` slot, at least 1)

Unique `(calculation_id, term_position, slot, cardinal_number)` with NULLs compared equal. Every row is mirrored by a
`calculation_dependency` edge with the new role `composite_input` (parent = the input, child = the composite; the
server writes both, and `depends_on` cannot declare the role). Checked when the inputs are written, after every
calculation of the request exists: every slot of the scheme has exactly one input
(`composite_input_missing` / `_slot_unknown` / `_duplicate`), the input is an `sp` or `opt`
(`composite_input_type_invalid`), belongs to the composite's species or transition-state entry
(`composite_input_owner_mismatch`), ran at the slot's level of theory with merges followed
(`composite_input_level_mismatch`), and ran at one geometry when more than one declares it
(`composite_input_geometry_mismatch`; an undeclared geometry warns, `composite_input_geometry_undeclared`).
The deposited `electronic_energy_hartree` is then recomputed from the inputs' stored energies with the scheme's
formulas and compared (`composite_total_mismatch`, tolerance `max(1e-6, 5e-7 * n)` hartree; `composite_total_unverifiable`
warns when a needed energy or component is not stated). The recomputed value is never stored. A `correlation` term
reads the whole correlation energy: `correlation` where it includes (T) (ORCA), `correlation + triples` where triples are
separate (Molpro), whichever sum equals the stored energy. Guarded like `calc_composite_result` on `calculation_id`
only: the cited calculation is a citation, not an owner (revision `b4d8e2f6a1c9`).

`calc_composite_log_check` fields (ADR 0021, P7a; what comparing a program-run composite with one attached output log concluded):

- `calculation_id` (PK part, FK: the composite)
- `artifact_sha256` (PK part; 64 lowercase hex digits: the log's digest, not a foreign key because the content-addressed object may be shared)
- `parser_version` (PK part; at least 1: the composite-log parser version that drew the conclusion)
- `outcome` (`composite_log_outcome`: `confirmed | mismatch | method_mismatch | available | unverifiable | absent`)
- `created_at`

A *conclusion*, never a number: nothing the log stated is stored and the deposited result is untouched. Written once per
`(calculation, log digest, parser version)` by the upload hook that already compares the log
(`composite_energy_log_mismatch` and its siblings), so a read can report `composite_energy_verification` without parsing a log;
the same bytes uploaded twice under one parser version conclude the same thing about an immutable result and the second observation is dropped, while an upload after a parser fix records a fresh conclusion and a read prefers the newest version per log. A calculation
whose log was uploaded before revision `a9c3e7b1d5f2` has no row and reads as `program_reported` until the log is deposited again.
Guarded like `calc_composite_result` on `calculation_id` (accepted-science freeze, TRUNCATE refused).

`composite_energy_verification` (read-time, never stored; `recomputed | recompute_mismatch | log_reconciled | program_reported |
unverifiable`) is derived from stored rows on every read: an `assembled` composite is recomputed from its inputs' stored energies with
the same arithmetic as `composite_total_mismatch` (so an input filled or changed later shows), a `program_run` is read from this table.

The `calculation.type` enum (`calc_type`) gains `composite`. A `composite` calculation
may be a conformer's primary calculation (it ran the optimisation that produced the
geometry); a `freq`, `sp` or `scan` may depend on it (`freq_on`, `single_point_on`,
`scan_parent`) when it has an output geometry.

`calc_sp_energy_component` fields (ADR 0021; a child of a single-point calculation):

- `calculation_id`
- `component` (`EnergyComponentKind`: `total | reference | correlation | triples | dboc | scalar_relativistic`)
- `value_hartree` (finite)

Primary key `(calculation_id, component)`: one value per component. Single-point calculations only (refused on any other type by the wire models and again at the write). The value is what the depositor sent. Components are refused without the energy (`sp_energy_components_require_energy`). At deposit, `reference + correlation` must equal `calc_sp_result.electronic_energy_hartree` within 1e-6 Eh, or, when a `triples` component is also sent, `reference + correlation + triples` may match instead (ORCA's correlation includes (T); Molpro prints CCSD and (T) separately); a `total` must equal the energy. On F12 methods `reference` must include the CABS-singles correction if the program's total does. TCKDB compares and never stores a sum it formed. Guarded as an ownership child of `calculation` like `calc_sp_result` (accepted-science freeze, TRUNCATE refused).

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

`calc_geometry_validation` compares the calculation's output geometry
against the declared species identity, with RMSD as a suspicion signal.

**What it compares is the molecular formula, not the molecular graph.**
The column is named `is_isomorphic` and the check was written intending
graph isomorphism, but `resolve_atom_mapping` falls back to
`_find_matches_using_smiles_graph` whenever bond perception from XYZ
fails or finds no substructure match — the common case for radicals,
ions and stretched geometries — and that fallback rejects a candidate
only on a per-element atom-count mismatch. Verified by direct call:
ethanol declared with dimethyl ether deposited (constitutional isomers,
both C2H6O) **passes**, and methane with one hydrogen pulled to 5 Å
**passes**. The rearrangement, bond-breaking, dissociation and
proton-transfer cases this table was introduced for are therefore *not*
caught, and connectivity validation is not implemented. Doing it
properly needs bond perception that is trustworthy on exactly the
strained, radical and stretched structures where it would matter, which
`rdDetermineBonds` is not.

A `validation_status=fail` row means "the atom counts disagree," **not**
"the calculation is scientifically invalid." These rows are
curator-attention signals, not inputs to automatic rejection or quality
gating. Phase-1 wiring records evidence; it never blocks an upload.

Per ADR 0008 §9 the blocking tier owns a rule and the others cite it:
formula agreement between a structure and the identity it is deposited
under is owned by
`app.services.species_resolution.assert_geometry_composition_matches_identity`,
which refuses the upload — but only for **conformer** geometries. A
calculation's input and output geometries reach no blocking composition
check on any path, so for those this advisory row is the only place the
comparison happens at all. For an *output* geometry that is deliberate:
an optimisation that drifted is science to record, not a payload to
refuse.

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

`coordinate_value` is the internal coordinate itself, in that coordinate's own
unit — not a displacement and not relative to `start_value`. See "Ownership of
geometric coordinate metadata across calculation tables" in §7.3 for the
declared contract, how conformance is checked, and the legacy correction.

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

### 8.0 PDep scientific-integrity additions

`network_channel.channel_key` is the stable pathway identity within a network.
Endpoint pairs are deliberately not unique: two distinct mechanisms can connect
the same macroscopic source and sink. `network_channel_microreaction` records
the elementary reaction evidence for each channel and may name its specific
`transition_state_entry`.

Each `network_solve_state_energy` row gives one state energy in fixed
`energy_kj_mol`, with mandatory `energy_zero_convention` and
`correction_convention`, and may cite its source calculation. A complete
uploaded solve supplies exactly one such row for every network state.
A state with several species has an energy that is a sum, so the single
`source_calculation_id` can only be one summand of several. Since #678 the
`network_solve_state_energy_source` child table holds one calculation per
participant, keyed `(solve_id, state_id, species_entry_id)` and tied by composite
foreign keys to the state energy and to a participant of that very state
(stoichiometry stays on `network_state_participant`, so `2A` is one row, not a
copy index). The upload holds the stated energy against the sum of the stored
energies, weighted by stoichiometry, and records the outcome on the state
energy as `source_sum_comparison` (`agrees` / `not_compared`) with
`source_sum_not_compared_reason`; a contradiction refuses the upload and no
computed total is stored. NULL outcome columns mean the row predates the check.
A legacy single source on a multi-species state reads back as `partial_sources`.
`network_solve_bath_gas` is a normalized composition (mole fractions sum to
one), while `network_solve_energy_transfer` declares its `scope`: a `per_well`
row names both a state and a collider species, and a `network_wide` row names
neither because the producer determined one ⟨ΔE⟩down for the entire network
(the usual Arkane/RMG/MESS form). The token is what prevents a single generic
collision model from being mistaken for a state-specific parameterization —
and, equally, prevents the database forcing one value to be duplicated per
well in order to be storable at all. See ADR 0009.

Atom-resolved isotope identity **is** represented. Producers express isotopic
substitution with standard SMILES isotope notation (`[2H]`, `[13C]`, `[18O]`)
in the species-entry identity, and with `geometry.isotopes` (a 1-based XYZ atom
index → mass number map) for the per-atom nuclides. The server derives
`species_entry.isotope_key` — the canonical SMILES of the isotope-labelled
molecule — and stores the per-atom nuclides in
`geometry_atom.isotope_mass_number`. The key is atom-resolved rather than
formula-level, so isotopomers (`[2H]CO` vs `[2H]OC`) are distinct identities,
as their frequencies, rotational constants and ZPE require. Isotopologues share
one `species` row: they share a molecular graph and a potential energy surface,
and differ only in nuclear mass. When both a geometry and an identity are
deposited together, their isotope substitutions are cross-checked and a
mismatch is rejected rather than reconciled by guessing.

The former free-text `isotopologue_label` is deprecated and no longer
participates in identity.

### 8.1 Statmech

`statmech` fields:

- `id`
- `species_entry_id`
- `scientific_origin`
- `literature_id`
- `workflow_tool_release_id`
- `software_release_id`
- `energy_level_of_theory_id` (the level the depositor declared for the energy; NULL when none was declared, never back-filled)
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
- `energy_level_of_theory_id` (the level the depositor declared for the energy; NULL when none was declared, never back-filled)
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
- `t0_k`
- `ea_kj_mol`
- `a_uncertainty`
- `n_uncertainty`
- `ea_uncertainty_kj_mol`
- `tmin_k`
- `tmax_k`
- `degeneracy`
- `degeneracy_convention`
- `tunneling_model`
- `determination_id`, `representation_role` (see "Kinetics determination, applicability and protocol declarations")
- `applicability_declaration`, `protocol_declaration` (JSONB, versioned)
- `energy_level_of_theory_id` (the level the depositor declared for the record's energies; NULL when none was declared, never back-filled)
- `note`
- `created_at`
- `created_by`

Related tables:

- `kinetics_source_calculation(kinetics_id, calculation_id, role)`
- `kinetics_determination` (identity; see below)

Notes:

- `model_kind` is enum-backed (`arrhenius` or `modified_arrhenius`)
- `a_units` uses the `ArrheniusAUnits` enum
- `t0_k` is the reference temperature of the scalar rate,
  `k = A (T/T0)^n exp(-Ea/RT)`: NOT NULL, default 1 K (the plain `A T^n` form,
  which is what every row stored before the column existed meant), greater
  than zero and at most 10000 K. It applies to this row's own `a`, `n` and
  `ea_kj_mol` of a modified-Arrhenius rate; falloff, PLOG, sum-of-Arrhenius and
  Chebyshev records are always at 1 K (upload refuses another value)
- temperature bounds must be positive when present, with `tmin_k <= tmax_k`
- `degeneracy` is either null or a finite value greater than zero
- `degeneracy_convention` is enum-backed (`already_applied`, `not_applied`, or
  `unknown`); legacy rows are backfilled as `unknown`, and the convention is
  never inferred from the numeric degeneracy

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
- `scope` (`per_well` | `network_wide`)
- `state_id` (NULL iff `scope = 'network_wide'`)
- `collider_species_entry_id` (NULL iff `scope = 'network_wide'`)
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

### 9.4 Network solve declarations and fit determinations (2026-10-04)

One new identity table and six nullable columns let a depositor state what a network solve's
outputs are outputs *of*, and which fits are alternate representations of one determination. All
are **attributed claims**: stored as made, never inferred, never defaulted, never backfilled.
Every solve and fit deposited before the revision reads `NULL` for all of them, and `NULL` means
"not stated": not "valid everywhere", not "independent". A selector built on these reads absence
as *unresolved*.

- `network_solve.target_declaration`, `protocol_declaration`, `validation_declaration` (JSONB).
  The database checks only that each is an object with a numeric `version`
  (`ck_network_solve_*_declaration_versioned_object`); their shape is owned by
  `tckdb_schemas.network_declarations` (`extra="forbid"`, only version `1`). Stored in resolved
  form: states by composition hash (a content locator within the network, backed by the
  network's own state rows), determinations by public ref, product sets pinned with a
  `membership_version` and `content_hash`. A `validation` entry is stored as *declared*, never as
  verified.
- `network_kinetics.determination_id`, `representation_role` (`network_representation_role`:
  `complete` | `additive_component` | `overlapping_contribution`) and `representation_declaration`
  (JSONB, versioned, with the fit's own `key`), set together or not at all
  (`ck_network_kinetics_determination_iff_role`, `..._iff_representation`). The declared key is
  unique within a determination (`uq_network_kinetics_representation_key`), which is what lets a
  determination carry several same-kind alternates.
- `network_kinetics_determination` (public ref prefix `nkdet`): identity of one channel's coefficient
  within one solve, unique on `(solve_id, determination_key)` and on `identity_hash`. Carries the
  declared observable (`observable_declaration`). **Immutable from creation**
  (`trg_network_kinetics_determination_immutable`) and an ownership child of `network_solve`
  guarded on `solve_id`, so nothing can be added under an accepted solve.
- A fit's determination is of the fit's own solve and channel: the composite foreign key
  `fk_network_kinetics_determination_scope` `(determination_id, solve_id, channel_id)` makes that a database fact
  (not applied while `determination_id` is NULL).
- Not enforced by the database (a CHECK cannot state them): the determination's channel belongs to
  the solve's network; a target names the network's own states and channels. The write path
  (`app.services.network_declaration_resolution`) refuses them as `network_declaration_invalid`.
  Only the determination row is immutable in the database; a fit's grouping and a solve's product sets are
  protected by the upload path and, once the solve is accepted, by the ordinary accepted-science guards.
- Product sets record membership (determinations and the fits they hold), pinned by content hash; the
  required output identities live in the solve's output catalog (`outputs[].required`), not in the set.
- Existing columns stay authoritative: the bath gas, state energies, grain settings and rate units
  already say things about a solve, and a declaration that contradicts one is refused.

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
- `level_of_theory_id` (the energy level of an `energy//frequency` key)
- `frequency_level_of_theory_id` (the frequency level of an `energy//frequency` key; part of identity; NULL when the scheme is keyed on one level)
- `source_literature_id`
- `software_release_id`
- `workflow_tool_release_id`
- `data_revision` (revision of the data holding the tables, e.g. the RMG-database commit; NULL when not stated)
- `atom_params_applied_as` (`subtracted` | `added`; how `atom_params` enter the energy; NULL when not stated)
- `units`
- `note`
- `created_at`
- `created_by`

Related parameter tables:

- `energy_correction_scheme_atom_param(scheme_id, element, value)`
- `energy_correction_scheme_bond_param(scheme_id, bond_key, value)`
- `energy_correction_scheme_component_param(scheme_id, component_kind, key, value)`

Notes:

- scheme identity has two forms, each a partial unique index. With `data_revision` NULL it is `(kind, name, level_of_theory_id, source_literature_id, software_release_id, workflow_tool_release_id)`. With a `data_revision` it is `(kind, name, level_of_theory_id, source_literature_id, software_release_id, data_revision)`: the workflow-tool build is then provenance, recorded from the first deposit, and two builds of one revision are one scheme. A revised scheme never matches an unrevised one
- `atom_param.value` is in the scheme's `units`; for `kind=atom_energy` it is the level's atomic energy of the element, applied as `atom_params_applied_as` says (Arkane subtracts `atom_energy`, adds `atom_hf` and subtracts `atom_thermal`, the net per-atom term being `atom_hf - atom_thermal`)
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

### 10.4 Reproducibility Assessment

`record_reproducibility_assessment` fields:

- `id`
- `public_ref` — opaque, unique `rpa_` handle for the exact immutable assessment row
- `record_type`
- `record_id`
- `grade`
- `rubric_name`
- `rubric_version`
- `context_hash`
- `context_json`
- `passed_json`
- `missing_json`
- `warnings_json`
- `assessor_kind`
- `assessor_user_id`
- `source_submission_id`
- `assessed_at`
- `created_at`

Notes:

- grades are `described`, `auditable`, and `rerunnable`
- the service validates that the polymorphic target exists, canonicalizes the
  mandatory context object, and computes `context_hash` server-side
- assessment history is append-only; a database trigger rejects updates and
  deletes
- the latest assessment is derived by `(assessed_at DESC, id DESC)`, not a
  mutable current flag
- this axis is independent of human record-review status and trust badges

## 11. Curated Release Layer

Five tables that answer "what is *the* TCKDB value?" without writing to the
science. See `docs/specs/dataset_release_and_profiles.md` and
`docs/adr/0007-curated-selections-are-a-release-overlay-not-a-column.md`.

### 11.1 `curation_policy`

Identity table. A named, versioned expert selection rubric, deduped on
`(name, version)`.

- `name`
- `version`
- `description`
- `criteria_json`
- `public_ref` (`cpol_...`)
- `created_by`
- `created_at`

Notes:

- a policy revision is a **new row**; re-registering an existing
  `(name, version)` with different content is rejected, because a published
  release states which policy version governed it
- distinct from the read-time `SelectionPolicy` enum, which ranks candidates
  from record data alone and persists nothing

### 11.2 `dataset_release`

Curation table. The citable unit.

- `tag` (unique, e.g. `2026.07.0`)
- `title`, `description`
- `status` (`draft` | `published` | `withdrawn`)
- `curation_policy_id`
- `data_license`, `code_license`
- `citation_text`, `contact`, `changelog_entry`
- `doi` (nullable; recorded after a deposit, never minted)
- `published_at`, `withdrawn_at`, `withdrawn_reason`
- `public_ref` (`rel_...`)

Notes:

- the data license (the scientific corpus) and the code license are separate
  fields and normally differ
- a withdrawn release keeps its row and manifest so an outstanding citation
  never dangles

### 11.3 `release_selection`

Curation table, **append-only**. One attributed decision.

- `dataset_release_id`, `curation_policy_id`
- `record_type` / `record_id` — the selected candidate
- `subject_type` / `subject_id` — what it was selected *for*
- `action` (`select` | `supersede` | `withdraw`)
- `supersedes_selection_id` (unique)
- `rationale` (non-blank)
- `selected_by`, `created_at`
- `public_ref` (`rsel_...`)

Notes:

- selectable record types are `thermo`, `statmech`, `transport`, `kinetics`,
  `network_solve`, `transition_state_entry` (CHECK-constrained)
- a database trigger rejects `UPDATE` and `DELETE`; revising a decision inserts
  a row naming the one it replaces
- `supersedes_selection_id` is unique, so supersession chains stay linear
- there is deliberately **no** `is_current` column; what stands is computed as
  head-of-chain-and-not-withdrawn

### 11.4 `release_manifest`

Result table, immutable. Exactly one per release.

- `dataset_release_id` (unique)
- `manifest_schema` (`tckdb.dataset_release.v1`), `profile` (always `curated`)
- `alembic_revision`, `backend_version`, `schemas_package_version`,
  `review_policy_version`, `curation_policy_id`, `recovery_archive_schema`
- `data_license`, `code_license`, `citation_text` (snapshotted)
- `content_sha256`
- `selected_record_count`, `candidate_record_count`
- `generated_at`, `created_by`
- `public_ref` (`rman_...`)

Notes:

- `content_sha256` covers the canonical serialization of a manifest document
  that is **rendered** from these rows rather than stored, so re-rendering
  detects drift
- a database trigger rejects `UPDATE` and `DELETE`

### 11.5 `release_artifact`

Result table, immutable. One checksummed file per manifest.

- `release_manifest_id`, `path` (unique together)
- `kind` (`selected_records` | `candidate_records` | `review_history` |
  `selection_ledger`)
- `media_type`, `sha256`, `byte_count`, `record_count`

Notes:

- a release ships its selections **and** the candidates and review history they
  were chosen from
- content is regenerated deterministically and re-checked against `sha256` on
  every download; a mismatch is a 409, never a silent serve

## 12. Important Integrity Rules

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
- reproducibility assessments preserve their hashed context and cannot be updated or deleted
- `release_selection`, `release_manifest` and `release_artifact` are append-only at the database level; superseding a selection inserts a row rather than editing one
- a `release_selection` must name a candidate that actually belongs to the subject it claims to be about

## 13. Current Semantic Model

- `species` is graph identity; `species_entry` is resolved scientific meaning
- `chem_reaction` is graph identity; `reaction_entry` is a concrete curated/uploaded entry
- `transition_state` is reaction-entry-centered; `transition_state_entry` is one candidate structure
- `conformer_group` is basin identity; observations and selections add provenance and curation
- `calculation` stores provenance and ownership; result/link tables hold structured ESS outputs
- `statmech`, `thermo`, `transport`, `kinetics`, `network`, and `applied_energy_correction` are scientific product layers built on top of identity and provenance tables
- `submission`, `submission_audit_event`, and `submission_record_link` are the moderation/publication layer for all contributed records
- `record_reproducibility_assessment` is an append-only curation projection of reproducibility evidence, separate from approval and trust
- `curation_policy`, `dataset_release`, `release_selection`, `release_manifest` and `release_artifact` are the curated-release overlay: an attributed, append-only selection among coexisting candidates plus the immutable checksummed manifest that makes it citable — none of them writes to a scientific product table

## Enthalpy reference declaration (2026-09-23)

`thermo.enthalpy_reference_kind = formation_298k` declares
standard enthalpy of formation at 298.15 K: one mole of the species formed
from elements in their reference forms, whose formation enthalpies are zero.
At another temperature, H(T) is that formation energy plus the species' own
enthalpy increment from 298.15 K. The elemental term remains pinned at
298.15 K; it is not recomputed against the elements at T.

The declaration covers `h298_kj_mol`, `thermo_point.h_kj_mol`,
`thermo_point.g_kj_mol`, Wilhoit `h0_kj_mol`, and the NASA-7/NASA-9 enthalpy
integration constants. A point's
`g_kj_mol` means H(T) - T*S(T), on the same reference zero, with entropy
converted to kJ/(mol*K). It is not a formation Gibbs energy recomputed
against elemental entropies. `enthalpy_formation_0k_kj_mol` retains its
separate, existing meaning.

The two-layer rule deliberately is not a scalar iff constraint:

- The database rule is `h298_kj_mol IS NULL OR enthalpy_reference_kind IS NOT NULL`,
  enforced by `trg_guard_thermo_enthalpy_reference` rather than a CHECK. The
  trigger fires only when an insert or update actually writes
  `h298_kj_mol` or `enthalpy_reference_kind` (compared against the
  pre-update row with `IS DISTINCT FROM`), so a write to any other column
  on a legacy undeclared row is unaffected. A CHECK, even `NOT VALID`, was
  tried first and rejected: Postgres revalidates a `NOT VALID` CHECK on
  every subsequent update regardless of which columns it touches, which
  would have frozen every legacy undeclared row against unrelated writes
  such as the public-ref backfill.
- Every deposit workflow requires the declaration for any h298 scalar, point
  enthalpy, point Gibbs energy, Wilhoit h0, or NASA-7/NASA-9 block, and
  refuses a declaration when none of that content exists. A point G counts
  because it sits on the same reference zero as H, even on a point with no
  H. Cp/entropy-only deposits leave it null. Point H and G live in the child
  `thermo_point` table, so this part is workflow-only: no database trigger
  or constraint inspects them.
- Declared fit-only and point-only records are valid without h298. No scalar
  is evaluated from a fit and stored as though the depositor supplied it.

Absence is absence: null means the source did not declare the reference.
`EnthalpyReferenceKind` has exactly one member and no `unspecified` member.
No default depends on origin, software, magnitude, or another row. Legacy
rows are not backfilled, including approved immutable rows, and the trigger
does not freeze them: any column other than the two it guards remains
writable, and a legacy row may later gain a declaration through an update
that sets `enthalpy_reference_kind` (which the trigger then validates
against the row's current `h298_kj_mol`). It must not later be validated by
inferring references or by using the accepted-science repair mechanism.

Sensible increments such as H(T)-H(0) and absolute quantum-chemistry
enthalpies belong in `molecular_property_observation`, with their stated
property label, state, temperature, pressure and uncertainty meaning.
CCCBDB's explicitly labelled H(298.15)-H(0) is routed to an observation
payload with its source datum and identity hint intact. ARC requires an
explicit adapter configuration; its output does not establish a basis.

## Thermodynamic target and protocol declarations (2026-10-03)

Three nullable columns on `thermo` let a depositor state two things the record
could not state before. Both are **attributed claims**: stored as made, never
inferred, never defaulted, never backfilled. Every record deposited before
this revision reads `NULL` for all three, and `NULL` means "not stated" --
not "equilibrium", not "standard".

- `thermodynamic_target_kind` (`thermo_target_kind`: `equilibrium_ensemble` |
  `single_conformer`) -- what the values are claimed to describe.
- `target_conformer_group_id` (FK to `conformer_group`) -- the one group a
  `single_conformer` target names.
- `protocol_declaration` (JSONB) -- a versioned declaration of how the values
  were produced.

### Target rules

- A `single_conformer` target **requires** a group and the group must belong to
  the **same species entry** as the thermo row. An `equilibrium_ensemble`
  target names no group, and a request that gives one is **refused**
  (`thermo_target_group_not_allowed`), not ignored: a claim carrying a group
  the record does not mean would otherwise be stored as if it were meant.
- "A group is named exactly when the kind is `single_conformer`" is a CHECK
  (`ck_thermo_target_group_iff_single_conformer`, written with `IS NOT DISTINCT
  FROM` so that a group on a row with no kind is refused too). That the group
  belongs to the row's species entry is a cross-table fact no CHECK can state,
  and making it a trigger or composite foreign key would alter
  `conformer_group`, a deployed identity table. It is enforced where the row is
  written, in three layers that do not rely on each other: the request schema
  (self-contradiction), the workflow (`resolve_thermo_declarations`, which
  re-derives every check from the declaration itself so a `model_construct`
  payload is judged the same), and `persist_thermo` (the resolved columns).
- The target is never inferred from `statmech_id`, a conformer selection, or
  anything else attached to the record.
- Wire spelling: `conformer_group_ref` (public ref, standalone
  `/uploads/thermo` and contribution bundles) or `conformer_key` (a conformer
  the same computed-species / computed-reaction bundle declares; a reaction
  bundle's namespace is the species' own conformers). Never a database id.

### Protocol declaration (version 1)

Owned by `tckdb_schemas.thermo_declarations`. Typed Pydantic models with
`extra="forbid"` and an explicit required `version`; only the versions in
`THERMO_PROTOCOL_VERSIONS` (`{1}`) are accepted. The database checks only that
the value is a JSON object with a numeric `version`
(`ck_thermo_protocol_declaration_versioned_object`). What is stored is the
validated form, with supporting calculations as public refs.

| Field | Meaning |
| --- | --- |
| `recipe.name` | `g3`, `g4`, `g4mp2`, `g4_complete`, or `other` (then `recipe.other_name` is required, and may not spell a listed recipe in any case or punctuation: `thermo_recipe_name_listed`). The four named recipes are distinct values because a method-aware comparison must tell them apart. |
| `recipe.recipe_version` | Free text naming the recipe's version or reference. Stored as written. |
| `formation_reference.derivation` | `atomization`, `isodesmic`, or `working_reaction` (any other balanced working reaction). A preference established for one derivation does not transfer to another. |
| `formation_reference.reference_data_source` / `reference_data_detail` | Where the reference formation enthalpies came from: `atct`, `nist_janaf`, `codata`, or `other` (then the detail is required); the detail is free text such as `ATcT 1.122`. |
| `thermal_approximation.ensemble_representation` | What stands in for the target ensemble: `lowest_conformer` or `boltzmann_conformers`. **Separate from the target**: the target says what ensemble is meant, this says what represents it. |
| `thermal_approximation.internal_motion` | `harmonic`, `hindered_rotors` or `anharmonic`. |
| `departures` | Stated departures from the standard form of the declared recipe: a list of `{component, description}` (`geometry`, `frequencies`, `zero_point_energy`, `electronic_energy`, `empirical_correction`, `other`). **Three states**: omitted means not stated; `[]` means the depositor states there are none; a list names them. "Standard" can only be established by `[]`. |
| `supporting_calculations` | Calculations the declaration rests on, by local key (`calculation_key`, bundle uploads only: `/uploads/kinetics` carries no calculations, so a key there is refused as `calculation_key_undeclared`) or public ref (`calculation_ref`, standalone and contribution-bundle routes only). Must belong to the record's own species entry. Stored as public refs. |

The vocabulary is deliberately small: it stores only what a job states, and a
value is added when a real deposit needs it (adding an enum member or an
optional field is additive within version 1). It is a claim, not a verification:
nothing here checks a recipe against the linked calculations or statmech, and
a method name alone satisfies no later evidence requirement.

### Lifecycle

- **Immutability.** `trg_as_root_thermo` refuses any UPDATE of an accepted
  thermo row, whichever column it touches, so the three columns are frozen with
  the rest of the row. An approved declaration is corrected only by
  supersession, like any other scientific content. No accepted-science repair
  declaration lists them, so no repair can change one.
- **Digests.** Follows #619/#633. No consistency check reads a declaration, so the
  three columns are in `THERMO_HASH_EXCLUDED_COLUMNS`: stating or changing one never
  restales a stored consistency review. They are in the reproducibility-assessment
  snapshot, registered in `UNCHANGED_DEFAULTS` with value `NULL`: an undeclared row's
  snapshot is exactly what it was before the revision, and a row that states a target
  or protocol snapshots differently, because the declaration is part of what it claims.
- **Reads.** `ThermoRecord.thermodynamic_target` (`kind`, `conformer_group_ref`)
  and `ThermoRecord.protocol` (stored form). Both `null` for a legacy row. A stored
  protocol that no longer validates (written outside the upload path) is served as
  `protocol: null, protocol_unreadable: true` and logged; it never fails the listing.
- **Contribution bundles.** Export carries an equilibrium target and the
  protocol's recipe, formation reference, thermal approximation and departures.
  It leaves out, and reports as a `declaration_pruned` omission, what names a
  row of the exporting database and so cannot travel: a `single_conformer`
  target's group and the protocol's supporting calculations. Left out means
  absent, never replaced.

## Kinetics determination, applicability and protocol declarations (2026-10-04)

One new identity table and four nullable columns on `kinetics` let a depositor state three
things a rate record could not state before. All are **attributed claims**: stored as made,
never inferred, never defaulted, never backfilled. Every record deposited before this revision
reads `NULL` for all four, and `NULL` means "not stated": not "a standalone rate", not
"universally valid", not "standard". A selector built on these reads absence as *unresolved*.

- `kinetics.determination_id` (FK to `kinetics_determination`) and `kinetics.representation_role`
  (`kinetics_representation_role`: `complete` | `additive_component`), set together or not at all
  (`ck_kinetics_determination_iff_role`).
- `kinetics.applicability_declaration` and `kinetics.protocol_declaration` (JSONB). The database
  checks only that each is an object with a numeric `version`
  (`ck_kinetics_applicability_declaration_versioned_object`,
  `ck_kinetics_protocol_declaration_versioned_object`); their shape is owned by
  `tckdb_schemas.kinetics_declarations` (`extra="forbid"`, only version `1`).

### The determination (`kinetics_determination`, public ref prefix `kdet`)

One complete determination of a rate: a measurement set, or one computed rate. Several fitted
representations of it (an Arrhenius fit and a Chebyshev fit of the same data, a multi-Arrhenius or
PLOG parent with all its children) share it and are **not** independent support for one another;
separate calculations or measurements are separate determinations. A separately uploaded additive
component is declared `additive_component`: it is not a total-rate candidate on its own.

- **Identity is content**: the reaction entry, the direction, the declared target
  (`target_kind` `whole_reaction`, or `resolved_channel` naming exactly one of a
  `transition_state_entry_ref` or a `network_ref` with `channel_key`), the source attribution
  (literature, workflow-tool release) and a source-scoped `key`. `identity_hash` is the unique
  SHA-256 of that content, so the same content resolves to one row. The record's fitting software is
  deliberately not part of it: two fits of one determination may come from different tools.
- **Immutable from creation**, including while shared: `trg_kinetics_determination_immutable`
  refuses every UPDATE. A record that needs a different determination joins another row.
- **Why the reaction entry is in the identity, and what that means for sharing.** Every upload
  mints its own `reaction_entry`, and a determination is of one entry, so two separate uploads
  never share a determination by restating its content. A record joins an existing determination
  by citing `determination_ref`, which also **anchors the record to that determination's reaction
  entry** (the submitted reaction content must be exactly that entry's, as for a transition-state
  ref). Within one reaction bundle the fits share the bundle's entry, and within one contribution
  bundle import, uploads that state the same determination content (same key, direction,
  whole-reaction target, source and reaction) are anchored to one entry as the export grouped them.
  A resolved-channel determination is anchored by its transition state, as a rate's tunneling or
  interpretation evidence is.
- **What a record must state to join**: its own `direction` (never inferred; the bundle route
  accepts `forward` or `net` only, because a bundle fit is stored under an entry oriented as its own
  keys, so a reverse fit swaps them) and a source (`literature` or `workflow_tool_release`).
  Citing a ref requires the same reaction entry (by anchoring), direction and source attribution
  (`kinetics_determination_mismatch`, `context.reason`).
- A channel target must belong to the record's reaction: a transition state of that entry
  (`"entry"` scope on the standalone route, any entry of the same reaction and structures on the
  bundle route) or a network channel linked to it, either as the record's own
  `network_kinetics` channel or through a channel micro-reaction of the same reaction.
- Replacement of an accepted record also checks the declared target: where both records state a
  determination, the two must be of the same `target_kind` and the same transition state or channel
  (the determination key and source are not compared: a re-measurement is another determination of
  the same target). Where either states none, nothing can be compared and the replacement is allowed;
  the record's read keeps saying `determination: null`.

### Applicability declaration (version 1)

What the stored coefficient is a coefficient *of*. Every statement is optional (omitted = unknown);
`claim_origin` (`source_publication` | `depositor_interpretation`) is required.

| Field | Meaning |
| --- | --- |
| `phase` | `gas` (the vocabulary starts there). |
| `observable` | `rate_coefficient`, `rate_of_progress`, `effective_global_law`. The last two can be stated so they are reported as what they are; a rate-coefficient selector treats them as unsupported. |
| `coefficient_basis` | `elementary_coefficient`, `third_body_kernel` (a simple `+M` coefficient still to be multiplied by an effective collider concentration), `composition_effective_coefficient` (already evaluated for one declared mixture). |
| `scope` | `whole_reaction` or `resolved_channel`; must agree with the determination's `target_kind`. |
| `reaction_order` | Concentration order of the coefficient (reactants, plus one for a simple third-body kernel). |
| `rate_progress_convention` | `reaction_progress` or `reactant_loss` (the factor of two for `2 A -> products`). |
| `pressure_dependence` | `independent` (established; a null pressure context is not that), `high_pressure_limit`, `fixed_pressure`, `pressure_dependent`; with `pressure_domain_min_bar`/`pressure_domain_max_bar` (both or neither) for a pressure-dependent model's stated validity domain. |
| `collider_kind` | `not_dependent`, `specified_collider` (one collider, no mole fraction), `fixed_mixture` (at least two, each with a mole fraction summing to one within an absolute 1e-9, never renormalised, none repeated), `composition_dependent` (through the record's own efficiencies, with the source's `default_third_body_efficiency` if it states one). Colliders are species content on the wire and species public refs once stored. |

**The existing columns stay authoritative.** A declaration that contradicts one is refused
(`kinetics_declaration_contradicts_record`), never reconciled: a pressure claim against
`pressure_context`/`pressure_bar`/`model_kind`/a network link (a fixed pressure needs the record's own
`apparent_at_pressure` and `pressure_bar`; a PLOG, Chebyshev, falloff or network-linked record cannot
be declared pressure independent, limiting or fixed), the coefficient basis against `is_third_body`,
the collider kind against the record's third-body or falloff treatment, the order against the
reaction's reactant count, and the scope against the determination's target. A column that is not
stated is not a contradiction. Temperature bounds, units and degeneracy keep their own columns and
are not restated here.

### Protocol declaration (version 1)

How the rate was produced; an attributed claim, **not verified** against the linked calculations (a
later selection step reads what the links show and reports a claim as declared, verified or
contradicted). At least one statement is required; all are optional.

| Field | Meaning |
| --- | --- |
| `method_kind` | `experimental`, `saddle_point_tst`, `variational_tst`, `barrierless_capture`, `master_equation`, `other` (then `method_other_name` is required). Must agree with the record's `scientific_origin` (`kinetics_declaration_contradicts_record`). |
| `barrier_basis` | `classical_electronic` or `zpe_corrected`. |
| `zero_point_treatment` | `harmonic_unscaled`, `harmonic_scaled`, `anharmonic`, `none`. |
| `geometry_relation` | `optimized_at_energy_level` or `optimized_at_other_level`. |
| `rotor_treatment` / `conformer_treatment` / `path_treatment` | `rigid_rotor_harmonic_oscillator`/`hindered_rotor`/`anharmonic`; `single_conformer`/`boltzmann_ensemble`/`multistructural`; `single_path`/`multipath_truncated`/`multipath_full`. |
| `departures` | Three states: omitted = not stated; `[]` = none; a list of parts changed from the standard form of the method (`geometry`, `frequencies`, `zero_point_energy`, `electronic_energy`, `empirical_correction`, `tunneling`, `other`). Only `[]` says "standard". |
| `supporting_calculations` | `{calculation_key | calculation_ref, purpose}` (`geometry`, `frequency`, `electronic_energy`, `zero_point_energy`, `irc`); each calculation must belong to a participant of the record's reaction or to its transition state (`kinetics_protocol_calculation_owner_mismatch`). Stored as public refs. |

The tunneling model stays in its existing column (`tunneling_model`, with
`kinetics_tunneling_application`); the energy and statistical-mechanics interpretation links stay in
`kinetics_source_calculation` and `kinetics_interpretation_assignment`. The protocol adds only what no
column or link states.

### Where each rule is enforced

Three layers, none removable because another exists: the request schema (`kinetics_declaration_error`,
shared with the client builder), `resolve_kinetics_declarations` (re-derives every check from the
declaration, so a `model_construct` payload is judged the same) and
`assert_kinetics_declaration_columns`, the last stop in `persist_kinetics` and in the reaction bundle
workflow, which build the `kinetics` row.

### Lifecycle

- **Immutability.** `trg_as_root_kinetics` refuses any UPDATE of an accepted kinetics row, whichever
  column it touches, so the four columns are frozen with the row; the determination is immutable on
  its own. No accepted-science repair declaration lists them.
- **Digests.** The four columns are registered in `UNCHANGED_DEFAULTS` with value `NULL`: a legacy row's
  consistency-input hash and reproducibility snapshot are what they were before the revision, and a
  record that states one hashes differently. Unlike the thermo declarations they are **not** kept out of
  the consistency hash: the declared meaning is part of what a stored advisory finding rests on. The linked
  determination's content is added to both snapshots only when a record has one.
- **Reads.** `KineticsRecord.determination` (`determination_ref`, `key`, `direction`, `target`,
  `representation_role`), `applicability`, `protocol` (stored forms); all `null` for a legacy record. A
  stored declaration that no longer validates is served as `null` with `declaration_unreadable: true` and
  logged; it never fails the listing.
- **Contribution bundles.** Export carries the record's `direction`, `is_third_body`, `pressure_context`
  and `pressure_bar` (the columns a declaration is checked against), a whole-reaction determination
  (key, target, role), the applicability declaration with its colliders as species content, and the
  protocol without its supporting calculations. It leaves out, and reports as a `declaration_pruned`
  omission, what names a row of the exporting database: a resolved-channel determination and the
  supporting calculations. Left out means absent, never replaced.
- **Release and archive.** A released kinetics record ships its determination embedded (without the
  identity hash, which digests this database's ids); the archive carries the table.

### `reaction.reversible` on the kinetics route (#598)

`chem_reaction.reversible` is part of a graph reaction's identity (the stoichiometry hash), and that is
unchanged. On `/uploads/kinetics`, and in the reactions of `/uploads/networks`, the field is optional, because
a rate does not need it: an omitted value is *not stated*, and is never guessed (the transition-state and
computed-reaction routes state `reversible`, defaulting it in their own schema; a bare rate has no such context).
A deposit that does not state it takes the value of the one stored reaction with its participants, or of the
reaction its transition state or cited determination anchors it to. When nothing can be inherited (none is
stored, or both twins are), it is refused with `reaction_reversible_required` and the producer states
`reversible: true` or `false`. There is no nullable column and no default. A transition-state-anchored rate
that states the opposite of the anchored reaction is still refused. Separately, every route that can create a
reaction (reactions, kinetics, computed reactions, bundles, networks and pressure-dependent networks, and their
job results) reports `reaction_reversible_twin` when the reaction it attached to has a twin (same participants,
opposite `reversible`), naming the twin by public ref.

### Determinations: roles, deletion, and order

- One determination holds either `complete` representations or `additive_component` records, never both:
  selection reads the role to decide whether a record is a total rate. A record of the other role is refused
  (`kinetics_determination_mismatch`, `context.reason` `role`).
- A cited determination cannot be deleted (the foreign key from `kinetics.determination_id`); the immutability
  trigger refuses UPDATE only. A determination no record cites may be deleted: it states nothing for anyone.
- `applicability.reaction_order` is the order of the side the record's direction names: the reactant count for
  a forward coefficient, the product count for a reverse one (plus one for a simple third-body reaction). A net
  rate, or one whose direction is not stated, is not checked. The separate `a_units` molecularity check on the
  standalone route still reads the reactant count only, whatever the direction (pre-existing, unchanged).

## Actual protocols and structure determinations (2026-10-05)

Revision `d3a8f6c1b952`. These are the claims a task-aware energy selection (lowest recorded energy, validated
minimum or saddle, protocol preference) needs and the upload contract could not state. They are declarations only:
this revision selects nothing and changes no ordering. Every one is an attributed claim, stored as made, never
inferred and never backfilled; a record deposited without one reads "not stated".

### `calculation.actual_protocol_declaration` (version 1)

Nullable JSONB, a versioned object whose shape `tckdb_schemas.structure_declarations.ActualProtocolDeclaration`
owns (the database checks only that it is an object with a numeric `version`). It carries the facts a level-of-theory
label cannot: electronic state and root, reference spin treatment, relativistic treatment, effective core potential,
core correlation, auxiliary basis, dispersion, solvation, constraints, material numerical approximations and the
corrections the stored number includes, with the declaration's source (producer, producer and parser versions).

- Each single fact is `known` (with a value), `unknown` or `not_applicable`. A fact left out is *not stated*. A list
  fact is omitted (not stated) or a list, where an empty list is the claim "none".
- A declaration that restates a level-of-theory field is *compared* with it when a selection reads the record; a
  disagreement is a finding, and neither side wins by upload order.
- Frozen with the rest of the row when the calculation is accepted (`ADD COLUMN` fires no UPDATE trigger, so the
  upgrade touches no approved row). The column stays out of the consistency and reproducibility digests while it is
  NULL (`snapshot_defaults`), so no stored review or assessment of an existing calculation goes stale.

### `structure_determination` (public ref prefix `sdet`)

One source-attributed claim about a defined geometry, conformer basin or saddle, and the quantity it supplies.

- **Owner:** exactly one of `species_entry_id` or `transition_state_entry_id` (`ck_..._one_owner`). A
  `conformer_basin` names a species entry and its `conformer_observation_id`; a `saddle_point` names a transition
  state entry and no observation; a `geometry` names no observation (`ck_..._target_matches_owner`).
- **Quantity:** `electronic_energy`, `zero_kelvin_energy` (a supplied E0) or NULL (evidence only). The
  `energy_convention` (how the zero-point energy inside an E0 was obtained, which corrections it includes) is stated
  exactly when the quantity is `zero_kelvin_energy` (`ck_..._convention_iff_zero_kelvin`). Neither energy is ever
  derived from the other.
- **Identity:** the owner, the observation (for a basin only), the source attribution (`literature_id` or
  `workflow_tool_release_id`, one required) and the source-scoped `determination_key` are the identifier, unique
  (`uq_structure_determination_key`, NULLS NOT DISTINCT; `identity_hash` is its digest). Stating the key again with the
  same content resolves to the existing row, with or without an Idempotency-Key, so a repeat is never an additional
  determination. `content_hash` digests everything else (target kind, quantity, convention, recipe, evaluated geometry,
  pinned calculations); stating the key with different content is refused (`structure_determination_mismatch`,
  `context.reason` `content`), never merged. A basin is per observation and each conformer upload creates a new
  observation, so a basin claim cannot be restated across uploads; a geometry or saddle claim can be, over calculations
  already deposited.
- **Immutable from creation:** `trg_structure_determination_immutable` refuses every UPDATE.

### `structure_determination_source`

One calculation pinned to one role (`energy`, `geometry_optimization`, `curvature`, `correction`, `connectivity`,
`alternative_characterization`) of a determination; one calculation can fill several roles, each its own row, and
alternative bundles are separate determinations (no Cartesian combination of an owner's attachments is ever built).
The owner columns repeat the determination's, and six composite foreign keys make "this source is a calculation of
the determination's own owner, and for a basin of its observation" a database fact (whichever owner column is set, its
keys are checked; the three `uq_calculation_scope_*` constraints on `calculation` exist only as their targets). A source
is pinned in the transaction that creates its determination and never added or removed afterwards: the database refuses
an INSERT that is not under the creation marker (`tckdb.structure_determination_writing`, a transaction-local setting the
write path sets; a tripwire against accidental or scripted edits, not access control) and every DELETE. The determination
is immutable and, with its sources, frozen under an accepted owner: the shared accepted-science guards
(`tckdb_guard_accepted_child`, `tckdb_guard_accepted_via_child`) cover the transition state entry and the conformer
observation, and TRUNCATE is refused on all three tables. These owner columns are nullable by design (one of two owners),
so this revision registers them in its own tuples and `tests/db/test_structure_determination_migration.py` checks them
against the model and `pg_trigger`; the shared registry's NOT NULL rule does not apply. `geometry_id` is the one geometry
the role's result describes, recorded only where the calculation's type makes it unambiguous (an optimization's one
output geometry, a single point's or frequency job's one input geometry) and NULL otherwise.

### `structure_evidence_finding` (public ref prefix `sfnd`)

Append-only events (`trg_..._append_only` refuses UPDATE and DELETE): a confirmed identity, state or path
incompatibility, a role-specific invalidation, a contradictory characterization, or an adjudication. A finding is
pinned to the geometry, calculation or determination its `scope` names (`ck_..._subject_matches_scope`), with its
author, `authority` (a producer's own assertion is distinct from an authorized adjudication), verdict, rationale,
semantic version and any finding it supersedes. An adjudication supersedes an earlier finding and needs
`authorized_adjudication` authority. The heuristic geometry-validation rows stay what they were (observations); this
table is where a confirmed interpretation is recorded without rewriting them. Nothing writes a finding yet.

### Where each rule is enforced

A basin's observation belongs to the determination's species entry (a trigger on INSERT), and every source of a basin is
anchored to that observation (composite keys). That the evaluated geometry is one of a source calculation's own
geometries (the geometry is shared, content-addressed content, so the database cannot check its owner), that the
evaluating calculation is one of the pinned sources and that the pinned calculations exist are cross-table facts the write
path enforces (`app.services.structure_determination_resolution`; codes `structure_determination_mismatch`,
`calculation_key_undeclared`, `unknown_calculation_ref`, `structure_determination_invalid`).

### Lifecycle

Released transition-state entries ship their determinations, sources and the findings pinned to them
(`RECORD_VALUE_TABLES`; the two digests are omitted because they are over this database's row ids). The three tables
are in the archive registry. Not yet released as selectable types: standalone calculations and conformer groups
(unchanged). Downgrade drops the tables, the constraints, the column and the enums, and prints what it forgets.
