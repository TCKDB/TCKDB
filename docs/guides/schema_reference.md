# Schema reference

This document is generated from the SQLAlchemy models in `app/db/models/` by
[`backend/scripts/generate_schema_reference.py`](../../backend/scripts/generate_schema_reference.py). The models are
the source of truth; hand edits to this file are overwritten the next time it
is regenerated. Regenerate with:

```bash
conda run -n tckdb_env python scripts/generate_schema_reference.py
```

run from `backend/`, and verify with `--check` (no arguments changes nothing
and exits non-zero if this file is stale).

Tables are grouped by the role their own model class docstring states --
**identity** (deduped -- what a thing is), **provenance** (append-only -- how
it was produced), **result** (append-only -- the number), or **curation**
(overlay -- how much to trust it); see
[`core_concepts.md`](core_concepts.md). A table whose class docstring does not
say which of the four it is prints as "role not stated on the model" rather
than a guess. Each column's "Meaning" cell is pulled only from the column's
own database comment (this codebase currently sets none) or from a structured
entry in the model's own docstring that names the column; a column with
neither prints "not documented". Those markers are deliberate: they are
honest about where the schema is under-documented, rather than papering over
the gap.

## Curation

### `conformer_selection`

**Role:** curation

**Purpose:** Store explicit workflow, curation, or UI selections for conformer groups.

| Column | Type | Nullable | Default | Foreign key | Enum values | Meaning |
|---|---|---|---|---|---|---|
| `id` | BIGINT | no | — | — | — | not documented |
| `conformer_group_id` | BIGINT | no | — | conformer_group.id | — | not documented |
| `assignment_scheme_id` | BIGINT | yes | — | conformer_assignment_scheme.id | — | not documented |
| `selection_kind` | ConformerSelectionKind (enum) | no | — | — | `display_default`, `curator_pick`, `lowest_energy`, `benchmark_reference`, `preferred_for_thermo`, `preferred_for_kinetics`, `representative_geometry` | not documented |
| `note` | TEXT | yes | — | — | — | not documented |
| `created_at` | TIMESTAMP WITHOUT TIME ZONE | no | now() | — | — | not documented |
| `created_by` | BIGINT | yes | — | app_user.id | — | not documented |

### `release_manifest`

**Role:** curation

**Purpose:** The frozen, checksummed description of one published dataset release.

| Column | Type | Nullable | Default | Foreign key | Enum values | Meaning |
|---|---|---|---|---|---|---|
| `id` | BIGINT | no | — | — | — | not documented |
| `dataset_release_id` | BIGINT | no | — | dataset_release.id | — | not documented |
| `manifest_schema` | VARCHAR(64) | no | — | — | — | not documented |
| `profile` | ReadProfile (enum) | no | curated | — | `exploratory`, `curated` | not documented |
| `alembic_revision` | VARCHAR(64) | no | — | — | — | not documented |
| `backend_version` | VARCHAR(64) | no | — | — | — | not documented |
| `schemas_package_version` | VARCHAR(64) | no | — | — | — | not documented |
| `review_policy_version` | VARCHAR(64) | no | — | — | — | not documented |
| `curation_policy_id` | BIGINT | no | — | curation_policy.id | — | not documented |
| `recovery_archive_schema` | VARCHAR(64) | no | — | — | — | not documented |
| `release_public_ref` | VARCHAR(40) | no | — | — | — | not documented |
| `release_published_at` | TIMESTAMP WITHOUT TIME ZONE | yes | — | — | — | not documented |
| `release_tag` | VARCHAR(64) | no | — | — | — | not documented |
| `release_title` | TEXT | no | — | — | — | not documented |
| `release_description` | TEXT | yes | — | — | — | not documented |
| `release_contact` | TEXT | no | — | — | — | not documented |
| `release_changelog_entry` | TEXT | yes | — | — | — | not documented |
| `release_status_at_publication` | DatasetReleaseStatus (enum) | no | — | — | `draft`, `published`, `withdrawn` | not documented |
| `release_doi_at_publication` | VARCHAR(128) | yes | — | — | — | not documented |
| `data_license` | VARCHAR(64) | no | — | — | — | not documented |
| `code_license` | VARCHAR(64) | no | — | — | — | not documented |
| `citation_text` | TEXT | no | — | — | — | not documented |
| `curation_policy_ref` | VARCHAR(40) | no | — | — | — | not documented |
| `curation_policy_name` | VARCHAR(128) | no | — | — | — | not documented |
| `curation_policy_version` | VARCHAR(64) | no | — | — | — | not documented |
| `curation_policy_description` | TEXT | no | — | — | — | not documented |
| `curation_policy_criteria_json` | JSONB | no | {}'::jsonb | — | — | not documented |
| `contract_json` | JSONB | no | — | — | — | not documented |
| `rights_summary_json` | JSONB | yes | — | — | — | not documented |
| `document_json` | JSONB | no | — | — | — | not documented |
| `content_sha256` | VARCHAR(64) | no | — | — | — | not documented |
| `selected_record_count` | BIGINT | no | — | — | — | not documented |
| `candidate_record_count` | BIGINT | no | — | — | — | not documented |
| `generated_at` | TIMESTAMP WITHOUT TIME ZONE | no | now() | — | — | not documented |
| `created_by` | BIGINT | yes | — | app_user.id | — | not documented |
| `public_ref` | VARCHAR(40) | no | — | — | — | not documented |

**Check constraints:**

- `ck_release_manifest_alembic_revision_nonblank`: `length(btrim(alembic_revision)) > 0`
- `ck_release_manifest_candidate_count_nonneg`: `candidate_record_count >= 0`
- `ck_release_manifest_content_sha256_hex`: `content_sha256 ~ '^[0-9a-f]{64}$'`
- `ck_release_manifest_selected_count_nonneg`: `selected_record_count >= 0`

### `species_entry_review`

**Role:** curation

**Purpose:** Store explicit human review or curation events for species entries.

| Column | Type | Nullable | Default | Foreign key | Enum values | Meaning |
|---|---|---|---|---|---|---|
| `id` | BIGINT | no | — | — | — | not documented |
| `species_entry_id` | BIGINT | no | — | species_entry.id | — | not documented |
| `user_id` | BIGINT | no | — | app_user.id | — | not documented |
| `role` | SpeciesEntryReviewRole (enum) | no | — | — | `curator`, `reviewer`, `validator`, `linker` | not documented |
| `note` | TEXT | yes | — | — | — | not documented |
| `created_at` | TIMESTAMP WITHOUT TIME ZONE | no | now() | — | — | not documented |

### `transition_state_selection`

**Role:** curation

**Purpose:** Store explicit workflow, curation, or UI selections for transition states.

| Column | Type | Nullable | Default | Foreign key | Enum values | Meaning |
|---|---|---|---|---|---|---|
| `id` | BIGINT | no | — | — | — | not documented |
| `transition_state_id` | BIGINT | no | — | transition_state.id | — | not documented |
| `selection_kind` | TransitionStateSelectionKind (enum) | no | — | — | `display_default`, `curator_pick`, `lowest_barrier`, `benchmark_reference`, `preferred_for_kinetics`, `representative_geometry` | not documented |
| `note` | TEXT | yes | — | — | — | not documented |
| `created_at` | TIMESTAMP WITHOUT TIME ZONE | no | now() | — | — | not documented |
| `created_by` | BIGINT | yes | — | app_user.id | — | not documented |

## Identity

### `calculation_parameter`

**Role:** identity

**Purpose:** EAV-style parsed parameter from an ESS calculation.

| Column | Type | Nullable | Default | Foreign key | Enum values | Meaning |
|---|---|---|---|---|---|---|
| `id` | BIGINT | no | — | — | — | not documented |
| `calculation_id` | BIGINT | no | — | calculation.id | — | not documented |
| `raw_key` | TEXT | no | — | — | — | not documented |
| `canonical_key` | TEXT | yes | — | calculation_parameter_vocab.canonical_key | — | not documented |
| `raw_value` | TEXT | no | — | — | — | not documented |
| `canonical_value` | TEXT | yes | — | — | — | not documented |
| `section` | TEXT | yes | — | — | — | not documented |
| `value_type` | TEXT | yes | — | — | — | not documented |
| `unit` | TEXT | yes | — | — | — | not documented |
| `parameter_index` | INTEGER | yes | — | — | — | not documented |
| `source` | ParameterSource (enum) | no | upload | — | `parser`, `upload`, `curated` | not documented |
| `parser_version` | TEXT | yes | — | — | — | not documented |
| `created_at` | TIMESTAMP WITHOUT TIME ZONE | no | now() | — | — | not documented |

**Check constraints:**

- `ck_calculation_parameter_parameter_index_ge_0`: `parameter_index IS NULL OR parameter_index >= 0`

### `conformer_group`

**Role:** identity

**Purpose:** Store one deduplicated conformational-basin identity for a species entry.

| Column | Type | Nullable | Default | Foreign key | Enum values | Meaning |
|---|---|---|---|---|---|---|
| `id` | BIGINT | no | — | — | — | not documented |
| `species_entry_id` | BIGINT | no | — | species_entry.id | — | not documented |
| `label` | VARCHAR(64) | yes | — | — | — | not documented |
| `note` | TEXT | yes | — | — | — | not documented |
| `representative_fingerprint_json` | JSONB | yes | — | — | — | not documented |
| `representative_coords_json` | JSONB | yes | — | — | — | not documented |
| `created_at` | TIMESTAMP WITHOUT TIME ZONE | no | now() | — | — | not documented |
| `created_by` | BIGINT | yes | — | app_user.id | — | not documented |
| `public_ref` | VARCHAR(40) | no | — | — | — | not documented |

### `curation_policy`

**Role:** identity

**Purpose:** A named, versioned expert policy for choosing among candidate records.

| Column | Type | Nullable | Default | Foreign key | Enum values | Meaning |
|---|---|---|---|---|---|---|
| `id` | BIGINT | no | — | — | — | not documented |
| `name` | VARCHAR(128) | no | — | — | — | not documented |
| `version` | VARCHAR(64) | no | — | — | — | not documented |
| `description` | TEXT | no | — | — | — | not documented |
| `criteria_json` | JSONB | no | {}'::jsonb | — | — | not documented |
| `created_at` | TIMESTAMP WITHOUT TIME ZONE | no | now() | — | — | not documented |
| `created_by` | BIGINT | yes | — | app_user.id | — | not documented |
| `public_ref` | VARCHAR(40) | no | — | — | — | not documented |

**Check constraints:**

- `ck_curation_policy_description_nonblank`: `length(btrim(description)) > 0`
- `ck_curation_policy_name_nonblank`: `length(btrim(name)) > 0`
- `ck_curation_policy_version_nonblank`: `length(btrim(version)) > 0`

### `kinetics_third_body_efficiency`

**Role:** identity

**Purpose:** Per-collider third-body efficiency for a falloff/third-body reaction.

| Column | Type | Nullable | Default | Foreign key | Enum values | Meaning |
|---|---|---|---|---|---|---|
| `id` | BIGINT | no | — | — | — | not documented |
| `kinetics_id` | BIGINT | no | — | kinetics.id | — | not documented |
| `collider_species_id` | BIGINT | no | — | species.id | — | not documented |
| `efficiency` | DOUBLE PRECISION | no | — | — | — | not documented |

**Check constraints:**

- `ck_kinetics_third_body_efficiency_efficiency_ge_0`: `efficiency >= 0`

### `network_channel_microreaction`

**Role:** identity

**Purpose:** Mechanistic evidence explicitly associating a channel with an elementary step/TS.

| Column | Type | Nullable | Default | Foreign key | Enum values | Meaning |
|---|---|---|---|---|---|---|
| `id` | BIGINT | no | — | — | — | not documented |
| `channel_id` | BIGINT | no | — | network_channel.id | — | not documented |
| `reaction_entry_id` | BIGINT | no | — | reaction_entry.id | — | not documented |
| `transition_state_entry_id` | BIGINT | yes | — | transition_state_entry.id | — | not documented |

### `reaction_participant`

**Role:** identity

**Purpose:** Compressed stoichiometric summary for a reaction graph identity.

| Column | Type | Nullable | Default | Foreign key | Enum values | Meaning |
|---|---|---|---|---|---|---|
| `reaction_id` | BIGINT | no | — | chem_reaction.id | — | not documented |
| `species_id` | BIGINT | no | — | species.id | — | not documented |
| `role` | ReactionRole (enum) | no | — | — | `reactant`, `product` | not documented |
| `stoichiometry` | SMALLINT | no | — | — | — | not documented |

**Check constraints:**

- `ck_reaction_participant_stoichiometry_ge_1`: `stoichiometry >= 1`

### `software`

**Role:** identity

**Purpose:** Stable identity of a software package.

| Column | Type | Nullable | Default | Foreign key | Enum values | Meaning |
|---|---|---|---|---|---|---|
| `id` | BIGINT | no | — | — | — | not documented |
| `name` | TEXT | no | — | — | — | not documented |
| `website` | TEXT | yes | — | — | — | not documented |
| `description` | TEXT | yes | — | — | — | not documented |
| `created_at` | TIMESTAMP WITHOUT TIME ZONE | no | now() | — | — | not documented |
| `public_ref` | VARCHAR(40) | no | — | — | — | not documented |

### `species`

**Role:** identity

**Purpose:** Store graph-defined species identities without resolved stereo or 3D conformers.

| Column | Type | Nullable | Default | Foreign key | Enum values | Meaning |
|---|---|---|---|---|---|---|
| `id` | BIGINT | no | — | — | — | not documented |
| `kind` | MoleculeKind (enum) | no | — | — | `molecule`, `pseudo`, `electron` | not documented |
| `smiles` | TEXT | no | — | — | — | not documented |
| `inchi_key` | CHAR(27) | no | — | — | — | not documented |
| `charge` | SMALLINT | no | — | — | — | not documented |
| `multiplicity` | SMALLINT | no | — | — | — | not documented |
| `stereo_kind` | StereoKind (enum) | no | — | — | `unspecified`, `achiral`, `enantiomer`, `diastereomer`, `ez_isomer` | not documented |
| `created_at` | TIMESTAMP WITHOUT TIME ZONE | no | now() | — | — | not documented |
| `public_ref` | VARCHAR(40) | no | — | — | — | not documented |

**Check constraints:**

- `ck_species_multiplicity_ge_1`: `multiplicity >= 1`

### `species_entry`

**Role:** identity

**Purpose:** Store one stereochemically, electronically, or isotopically resolved species form.

| Column | Type | Nullable | Default | Foreign key | Enum values | Meaning |
|---|---|---|---|---|---|---|
| `id` | BIGINT | no | — | — | — | not documented |
| `species_id` | BIGINT | no | — | species.id | — | not documented |
| `kind` | StationaryPointKind (enum) | no | minimum | — | `minimum`, `vdw_complex` | not documented |
| `mol` | mol (RDKit cartridge structure) | yes | — | — | — | not documented |
| `unmapped_smiles` | TEXT | yes | — | — | — | not documented |
| `stereo_label` | VARCHAR(64) | yes | — | — | — | not documented |
| `electronic_state_kind` | SpeciesEntryStateKind (enum) | no | ground | — | `ground`, `excited` | not documented |
| `electronic_state_label` | VARCHAR(8) | yes | — | — | — | not documented |
| `term_symbol_raw` | VARCHAR(64) | yes | — | — | — | not documented |
| `term_symbol` | VARCHAR(64) | yes | — | — | — | not documented |
| `isotope_key` | TEXT | yes | — | — | — | not documented |
| `isotopologue_label` | VARCHAR(64) | yes | — | — | — | not documented |
| `created_at` | TIMESTAMP WITHOUT TIME ZONE | no | now() | — | — | not documented |
| `created_by` | BIGINT | yes | — | app_user.id | — | not documented |
| `public_ref` | VARCHAR(40) | no | — | — | — | not documented |

### `workflow_tool`

**Role:** identity

**Purpose:** Stable identity for a workflow or orchestration tool.

| Column | Type | Nullable | Default | Foreign key | Enum values | Meaning |
|---|---|---|---|---|---|---|
| `id` | BIGINT | no | — | — | — | not documented |
| `name` | TEXT | no | — | — | — | not documented |
| `description` | TEXT | yes | — | — | — | not documented |
| `created_at` | TIMESTAMP WITHOUT TIME ZONE | no | now() | — | — | not documented |
| `public_ref` | VARCHAR(40) | no | — | — | — | not documented |

## Provenance

### `conformer_assignment_scheme`

**Role:** provenance

**Purpose:** Store versioned metadata about conformer-assignment or selection logic.

| Column | Type | Nullable | Default | Foreign key | Enum values | Meaning |
|---|---|---|---|---|---|---|
| `id` | BIGINT | no | — | — | — | not documented |
| `name` | VARCHAR(128) | no | — | — | — | not documented |
| `version` | VARCHAR(64) | no | — | — | — | not documented |
| `scope` | ConformerAssignmentScopeKind (enum) | no | canonical | — | `canonical`, `imported`, `experimental`, `custom` | not documented |
| `description` | TEXT | yes | — | — | — | not documented |
| `parameters_json` | JSONB | yes | — | — | — | not documented |
| `code_commit` | VARCHAR(64) | yes | — | — | — | not documented |
| `is_default` | BOOLEAN | no | false | — | — | not documented |
| `created_at` | TIMESTAMP WITHOUT TIME ZONE | no | now() | — | — | not documented |
| `created_by` | BIGINT | yes | — | app_user.id | — | not documented |
| `public_ref` | VARCHAR(40) | no | — | — | — | not documented |

### `conformer_observation`

**Role:** provenance

**Purpose:** Store one provenance-bearing conformer observation assigned to a basin.

| Column | Type | Nullable | Default | Foreign key | Enum values | Meaning |
|---|---|---|---|---|---|---|
| `id` | BIGINT | no | — | — | — | not documented |
| `conformer_group_id` | BIGINT | no | — | conformer_group.id | — | not documented |
| `assignment_scheme_id` | BIGINT | yes | — | conformer_assignment_scheme.id | — | not documented |
| `scientific_origin` | ScientificOriginKind (enum) | no | computed | — | `computed`, `experimental`, `estimated` | not documented |
| `note` | TEXT | yes | — | — | — | not documented |
| `torsion_fingerprint_json` | JSONB | yes | — | — | — | not documented |
| `created_at` | TIMESTAMP WITHOUT TIME ZONE | no | now() | — | — | not documented |
| `created_by` | BIGINT | yes | — | app_user.id | — | not documented |
| `public_ref` | VARCHAR(40) | no | — | — | — | not documented |

### `external_source_record`

**Role:** provenance

**Purpose:** One immutable snapshot of one document fetched from an :class:`ExternalSource`, with the parser/mapping versions that produced typed rows from it.

| Column | Type | Nullable | Default | Foreign key | Enum values | Meaning |
|---|---|---|---|---|---|---|
| `id` | BIGINT | no | — | — | — | not documented |
| `external_source_id` | BIGINT | no | — | external_source.id | — | not documented |
| `record_kind` | ExternalSourceRecordKind (enum) | no | — | — | `thermoml_article`, `cccbdb_page` | not documented |
| `source_uri` | TEXT | no | — | — | — | not documented |
| `source_record_key` | TEXT | no | — | — | — | not documented |
| `retrieved_at` | TIMESTAMP WITHOUT TIME ZONE | no | — | — | — | not documented |
| `http_status` | INTEGER | yes | — | — | — | not documented |
| `content_sha256` | TEXT | no | — | — | — | not documented |
| `content_length` | BIGINT | no | — | — | — | not documented |
| `raw_uri` | TEXT | no | — | — | — | not documented |
| `container_digest` | TEXT | yes | — | — | — | not documented |
| `schema_id` | TEXT | yes | — | — | — | not documented |
| `schema_valid` | BOOLEAN | yes | — | — | — | not documented |
| `parser_name` | TEXT | no | — | — | — | not documented |
| `parser_version` | TEXT | no | — | — | — | not documented |
| `mapping_version` | TEXT | no | — | — | — | not documented |
| `mapping_report_json` | JSONB | yes | — | — | — | not documented |
| `created_at` | TIMESTAMP WITHOUT TIME ZONE | no | now() | — | — | not documented |

**Check constraints:**

- `ck_external_source_record_content_length_ge_0`: `content_length >= 0`
- `ck_external_source_record_content_sha256_hex`: `content_sha256 ~ '^[0-9a-f]{64}$'`
- `ck_external_source_record_http_status_range`: `http_status IS NULL OR http_status BETWEEN 100 AND 599`

### `level_of_theory`

**Role:** provenance

**Purpose:** Method/basis provenance used by calculations.

| Column | Type | Nullable | Default | Foreign key | Enum values | Meaning |
|---|---|---|---|---|---|---|
| `id` | BIGINT | no | — | — | — | not documented |
| `method` | TEXT | no | — | — | — | not documented |
| `basis` | TEXT | yes | — | — | — | not documented |
| `aux_basis` | TEXT | yes | — | — | — | not documented |
| `cabs_basis` | TEXT | yes | — | — | — | not documented |
| `dispersion` | TEXT | yes | — | — | — | not documented |
| `solvent` | TEXT | yes | — | — | — | not documented |
| `solvent_model` | TEXT | yes | — | — | — | not documented |
| `keywords` | TEXT | yes | — | — | — | not documented |
| `spin_treatment` | SpinTreatment (enum) | yes | — | — | `restricted`, `unrestricted`, `restricted_open`, `unknown` | not documented |
| `lot_hash` | CHAR(64) | no | — | — | — | not documented |
| `created_at` | TIMESTAMP WITHOUT TIME ZONE | no | now() | — | — | not documented |
| `public_ref` | VARCHAR(40) | no | — | — | — | not documented |

### `molecular_property_observation`

**Role:** provenance

**Purpose:** One molecular-property observation with full external provenance.

| Column | Type | Nullable | Default | Foreign key | Enum values | Meaning |
|---|---|---|---|---|---|---|
| `id` | BIGINT | no | — | — | — | not documented |
| `species_entry_id` | BIGINT | yes | — | species_entry.id | — | ``species_entry_id`` is **nullable**. CCCBDB property tables ship raw rows where identity is at most ``formula`` + ``name`` — the catalog enrichment helper (:func:`app.importers.cccbdb.enrichment. propose_catalog_matches`) is often *ambiguous* (isomers). Forcing a non-null FK would push the importer into fabricating species entries, which would be a worse outcome than carrying an identity-unresolved observation with its CCCBDB provenance intact. Once an unambiguous match becomes available (manual curation or a future resolver), a row's ``species_entry_id`` can be populated via an UPDATE. |
| `scientific_origin` | ScientificOriginKind (enum) | no | — | — | `computed`, `experimental`, `estimated` | not documented |
| `property_kind` | MolecularPropertyKind (enum) | no | — | — | `dipole_moment`, `quadrupole_moment`, `polarizability`, `polarizability_iso`, `ionization_energy`, `electron_affinity`, `proton_affinity`, `enthalpy_of_formation`, `atomization_energy`, `homo_energy`, `lumo_energy`, `homo_lumo_gap`, `rotational_constant`, `spectroscopic_constant`, `heat_capacity_cp`, `other` | not documented |
| `property_label` | TEXT | yes | — | — | — | not documented |
| `scalar_value` | DOUBLE PRECISION | yes | — | — | — | not documented |
| `scalar_unit` | TEXT | yes | — | — | — | not documented |
| `scalar_uncertainty` | DOUBLE PRECISION | yes | — | — | — | not documented |
| `vector_json` | JSONB | yes | — | — | — | not documented |
| `tensor_json` | JSONB | yes | — | — | — | not documented |
| `uncertainty_kind` | ObservedUncertaintyKind (enum) | yes | — | — | `standard`, `expanded`, `combined_standard`, `combined_expanded` | not documented |
| `uncertainty_coverage_factor` | DOUBLE PRECISION | yes | — | — | — | not documented |
| `uncertainty_level_of_confidence_pct` | DOUBLE PRECISION | yes | — | — | — | not documented |
| `uncertainty_assessor` | ObservedUncertaintyAssessor (enum) | yes | — | — | `source_author`, `source_evaluator` | not documented |
| `temperature_k` | DOUBLE PRECISION | yes | — | — | — | not documented |
| `pressure_bar` | DOUBLE PRECISION | yes | — | — | — | not documented |
| `state_basis` | ObservedStateBasis (enum) | yes | — | — | `ideal_gas`, `real_gas` | not documented |
| `wavelength_nm` | DOUBLE PRECISION | yes | — | — | — | not documented |
| `method_note` | TEXT | yes | — | — | — | not documented |
| `state_label_raw` | TEXT | yes | — | — | — | not documented |
| `literature_id` | BIGINT | yes | — | literature.id | — | not documented |
| `software_release_id` | BIGINT | yes | — | software_release.id | — | not documented |
| `workflow_tool_release_id` | BIGINT | yes | — | workflow_tool_release.id | — | not documented |
| `source_calculation_id` | BIGINT | yes | — | calculation.id | — | not documented |
| `external_source_name` | TEXT | yes | — | — | — | not documented |
| `external_source_release` | TEXT | yes | — | — | — | not documented |
| `external_source_doi` | TEXT | yes | — | — | — | not documented |
| `external_source_url` | TEXT | yes | — | — | — | not documented |
| `external_source_record_key` | TEXT | yes | — | — | — | not documented |
| `external_source_page_kind` | TEXT | yes | — | — | — | not documented |
| `external_source_content_sha256` | TEXT | yes | — | — | — | not documented |
| `external_source_parser_version` | TEXT | yes | — | — | — | not documented |
| `external_source_record_id` | BIGINT | yes | — | external_source_record.id | — | not documented |
| `reference_label` | TEXT | yes | — | — | — | not documented |
| `reference_comment` | TEXT | yes | — | — | — | not documented |
| `raw_reference_text` | TEXT | yes | — | — | — | not documented |
| `raw_payload_json` | JSONB | yes | — | — | — | not documented |
| `note` | TEXT | yes | — | — | — | not documented |
| `created_at` | TIMESTAMP WITHOUT TIME ZONE | no | now() | — | — | not documented |
| `created_by` | BIGINT | yes | — | app_user.id | — | not documented |
| `public_ref` | VARCHAR(40) | no | — | — | — | not documented |

**Check constraints:**

- `ck_molecular_property_observation_mpo_scalar_uncertainty_ge_0`: `scalar_uncertainty IS NULL OR scalar_uncertainty >= 0`
- `ck_molecular_property_observation_mpo_scalar_value_has_unit`: `scalar_value IS NULL OR scalar_unit IS NOT NULL`
- `ck_molecular_property_observation_mpo_temperature_k_gt_0`: `temperature_k IS NULL OR temperature_k > 0`
- `ck_molecular_property_observation_mpo_value_at_least_one`: `scalar_value IS NOT NULL OR vector_json IS NOT NULL OR tensor_json IS NOT NULL`
- `ck_molecular_property_observation_mpo_wavelength_nm_gt_0`: `wavelength_nm IS NULL OR wavelength_nm > 0`
- `ck_mpo_coverage_factor_only_expanded`: `uncertainty_coverage_factor IS NULL OR uncertainty_kind IN ('expanded', 'combined_expanded')`
- `ck_mpo_heat_capacity_cp_requires_temperature`: `property_kind <> 'heat_capacity_cp' OR temperature_k IS NOT NULL`
- `ck_mpo_heat_capacity_cp_unit_j_mol_k`: `property_kind <> 'heat_capacity_cp' OR scalar_unit = 'J/mol/K'`
- `ck_mpo_pressure_bar_gt_0`: `pressure_bar IS NULL OR pressure_bar > 0`
- `ck_mpo_uncertainty_confidence_pct_range`: `uncertainty_level_of_confidence_pct IS NULL OR (uncertainty_level_of_confidence_pct > 0 AND uncertainty_level_of_confidence_pct <= 100)`
- `ck_mpo_uncertainty_coverage_factor_ge_1`: `uncertainty_coverage_factor IS NULL OR uncertainty_coverage_factor >= 1`
- `ck_mpo_uncertainty_kind_iff_value`: `property_kind <> 'heat_capacity_cp' OR (scalar_uncertainty IS NULL) = (uncertainty_kind IS NULL)`

### `network_solve`

**Role:** provenance

**Purpose:** The provenance envelope for one coherent set of k(T,P) on a network.

| Column | Type | Nullable | Default | Foreign key | Enum values | Meaning |
|---|---|---|---|---|---|---|
| `id` | BIGINT | no | — | — | — | not documented |
| `network_id` | BIGINT | no | — | network.id | — | not documented |
| `kind` | NetworkSolveKind (enum) | no | computed | — | `computed`, `reported` | not documented |
| `literature_id` | BIGINT | yes | — | literature.id | — | not documented |
| `software_release_id` | BIGINT | yes | — | software_release.id | — | not documented |
| `workflow_tool_release_id` | BIGINT | yes | — | workflow_tool_release.id | — | not documented |
| `me_method` | TEXT | yes | — | — | — | not documented |
| `interpolation_model` | TEXT | yes | — | — | — | not documented |
| `grain_size_cm_inv` | DOUBLE PRECISION | yes | — | — | — | not documented |
| `grain_count` | INTEGER | yes | — | — | — | not documented |
| `emax_kj_mol` | DOUBLE PRECISION | yes | — | — | — | not documented |
| `tmin_k` | DOUBLE PRECISION | yes | — | — | — | not documented |
| `tmax_k` | DOUBLE PRECISION | yes | — | — | — | not documented |
| `pmin_bar` | DOUBLE PRECISION | yes | — | — | — | not documented |
| `pmax_bar` | DOUBLE PRECISION | yes | — | — | — | not documented |
| `note` | TEXT | yes | — | — | — | not documented |
| `created_at` | TIMESTAMP WITHOUT TIME ZONE | no | now() | — | — | not documented |
| `created_by` | BIGINT | yes | — | app_user.id | — | not documented |
| `public_ref` | VARCHAR(40) | no | — | — | — | not documented |

**Check constraints:**

- `ck_network_solve_grain_count_ge_1`: `grain_count IS NULL OR grain_count >= 1`
- `ck_network_solve_pmax_bar_gt_0`: `pmax_bar IS NULL OR pmax_bar > 0`
- `ck_network_solve_pmin_bar_gt_0`: `pmin_bar IS NULL OR pmin_bar > 0`
- `ck_network_solve_pmin_le_pmax`: `pmin_bar IS NULL OR pmax_bar IS NULL OR pmin_bar <= pmax_bar`
- `ck_network_solve_reported_requires_literature`: `kind <> 'reported' OR literature_id IS NOT NULL`
- `ck_network_solve_tmax_k_gt_0`: `tmax_k IS NULL OR tmax_k > 0`
- `ck_network_solve_tmin_k_gt_0`: `tmin_k IS NULL OR tmin_k > 0`
- `ck_network_solve_tmin_le_tmax`: `tmin_k IS NULL OR tmax_k IS NULL OR tmin_k <= tmax_k`

### `workflow_tool_release`

**Role:** provenance

**Purpose:** Exact provenance for a workflow tool code state.

| Column | Type | Nullable | Default | Foreign key | Enum values | Meaning |
|---|---|---|---|---|---|---|
| `id` | BIGINT | no | — | — | — | not documented |
| `workflow_tool_id` | BIGINT | no | — | workflow_tool.id | — | not documented |
| `version` | TEXT | yes | — | — | — | not documented |
| `git_commit` | CHAR(40) | yes | — | — | — | not documented |
| `release_date` | DATE | yes | — | — | — | not documented |
| `notes` | TEXT | yes | — | — | — | not documented |
| `created_at` | TIMESTAMP WITHOUT TIME ZONE | no | now() | — | — | not documented |
| `public_ref` | VARCHAR(40) | no | — | — | — | not documented |

## Result

### `applied_group_additivity`

**Role:** result

**Purpose:** One group-additivity estimation attached to a ``thermo`` record.

| Column | Type | Nullable | Default | Foreign key | Enum values | Meaning |
|---|---|---|---|---|---|---|
| `id` | BIGINT | no | — | — | — | not documented |
| `thermo_id` | BIGINT | no | — | thermo.id | — | not documented |
| `scheme_id` | BIGINT | no | — | group_additivity_scheme.id | — | not documented |
| `note` | TEXT | yes | — | — | — | not documented |
| `created_at` | TIMESTAMP WITHOUT TIME ZONE | no | now() | — | — | not documented |
| `created_by` | BIGINT | yes | — | app_user.id | — | not documented |

### `calc_scf_stability`

**Role:** result

**Purpose:** SCF wavefunction stability evidence for a calculation.

| Column | Type | Nullable | Default | Foreign key | Enum values | Meaning |
|---|---|---|---|---|---|---|
| `calculation_id` | BIGINT | no | — | calculation.id | — | not documented |
| `status` | SCFStabilityStatus (enum) | no | — | — | `stable`, `unstable`, `stabilized`, `inconclusive` | not documented |
| `lowest_eigenvalue` | FLOAT | yes | — | — | — | not documented |
| `instability_count` | INTEGER | yes | — | — | — | not documented |
| `instability_type` | TEXT | yes | — | — | — | not documented |
| `reoptimized_wavefunction` | BOOLEAN | yes | — | — | — | not documented |
| `source_calculation_id` | BIGINT | yes | — | calculation.id | — | not documented |
| `source_artifact_id` | BIGINT | yes | — | calculation_artifact.id | — | not documented |
| `note` | TEXT | yes | — | — | — | not documented |
| `created_at` | TIMESTAMP WITHOUT TIME ZONE | no | now() | — | — | not documented |
| `created_by` | BIGINT | yes | — | app_user.id | — | not documented |

**Check constraints:**

- `ck_calc_scf_stability_instability_count_ge_0`: `instability_count IS NULL OR instability_count >= 0`
- `ck_calc_scf_stability_stabilized_has_instability`: `NOT (status = 'stabilized' AND instability_count = 0)`
- `ck_calc_scf_stability_stable_no_reopt`: `NOT (status = 'stable' AND reoptimized_wavefunction IS TRUE)`

### `calc_spin_diagnostic`

**Role:** result

**Purpose:** Parsed spin-contamination ``<S^2>`` evidence for a calculation.

| Column | Type | Nullable | Default | Foreign key | Enum values | Meaning |
|---|---|---|---|---|---|---|
| `calculation_id` | BIGINT | no | — | calculation.id | — | not documented |
| `s_squared` | FLOAT | no | — | — | — | not documented |
| `s_squared_expected` | FLOAT | yes | — | — | — | not documented |
| `s_squared_annihilated` | FLOAT | yes | — | — | — | not documented |
| `note` | TEXT | yes | — | — | — | not documented |
| `created_at` | TIMESTAMP WITHOUT TIME ZONE | no | now() | — | — | not documented |
| `created_by` | BIGINT | yes | — | app_user.id | — | not documented |

**Check constraints:**

- `ck_calc_spin_diagnostic_s_squared_annihilated_ge_0`: `s_squared_annihilated IS NULL OR s_squared_annihilated >= 0`
- `ck_calc_spin_diagnostic_s_squared_expected_ge_0`: `s_squared_expected IS NULL OR s_squared_expected >= 0`
- `ck_calc_spin_diagnostic_s_squared_ge_0`: `s_squared >= 0`

### `calc_wavefunction_diagnostic`

**Role:** result

**Purpose:** Parsed coupled-cluster / multireference diagnostics for a calculation.

| Column | Type | Nullable | Default | Foreign key | Enum values | Meaning |
|---|---|---|---|---|---|---|
| `calculation_id` | BIGINT | no | — | calculation.id | — | not documented |
| `t1_diagnostic` | FLOAT | yes | — | — | — | not documented |
| `d1_diagnostic` | FLOAT | yes | — | — | — | not documented |
| `t1_norm` | FLOAT | yes | — | — | — | not documented |
| `largest_t2_amplitude` | FLOAT | yes | — | — | — | not documented |
| `note` | TEXT | yes | — | — | — | not documented |
| `created_at` | TIMESTAMP WITHOUT TIME ZONE | no | now() | — | — | not documented |
| `created_by` | BIGINT | yes | — | app_user.id | — | not documented |

**Check constraints:**

- `ck_calc_wavefunction_diagnostic_d1_diagnostic_ge_0`: `d1_diagnostic IS NULL OR d1_diagnostic >= 0`
- `ck_calc_wavefunction_diagnostic_largest_t2_amplitude_ge_0`: `largest_t2_amplitude IS NULL OR largest_t2_amplitude >= 0`
- `ck_calc_wavefunction_diagnostic_t1_diagnostic_ge_0`: `t1_diagnostic IS NULL OR t1_diagnostic >= 0`
- `ck_calc_wavefunction_diagnostic_t1_norm_ge_0`: `t1_norm IS NULL OR t1_norm >= 0`

### `transition_state_validation_evidence`

**Role:** result

**Purpose:** Structured IRC validation result for one TS candidate.

| Column | Type | Nullable | Default | Foreign key | Enum values | Meaning |
|---|---|---|---|---|---|---|
| `id` | BIGINT | no | — | — | — | not documented |
| `transition_state_entry_id` | BIGINT | no | — | transition_state_entry.id | — | not documented |
| `kind` | TEXT | no | — | — | — | not documented |
| `passed` | BOOLEAN | no | — | — | — | not documented |
| `rationale` | TEXT | no | — | — | — | not documented |
| `reconstruction_calculation_id` | BIGINT | no | — | calculation.id | — | not documented |
| `reactant_participant_mapping` | JSONB | yes | — | — | — | not documented |
| `product_participant_mapping` | JSONB | yes | — | — | — | not documented |
| `transition_state_geometry_id` | BIGINT | yes | — | geometry.id | — | not documented |
| `created_at` | TIMESTAMP WITHOUT TIME ZONE | no | now() | — | — | not documented |
| `created_by` | BIGINT | yes | — | app_user.id | — | not documented |

**Check constraints:**

- `ck_transition_state_validation_evidence_mapping_names_geometry`: `(coalesce(jsonb_typeof(reactant_participant_mapping), 'null') = 'null' AND coalesce(jsonb_typeof(product_participant_mapping), 'null') = 'null') OR transition_state_geometry_id IS NOT NULL`
- `ck_transition_state_validation_evidence_ts_validation_kind`: `kind IN ('irc')`

## Role not stated on the model

### `accepted_science_repair`

**Role:** role not stated on the model

**Purpose:** One declaration: which table, which columns, which revision, and why.

| Column | Type | Nullable | Default | Foreign key | Enum values | Meaning |
|---|---|---|---|---|---|---|
| `id` | BIGINT | no | — | — | — | not documented |
| `target_schema` | TEXT | no | public | — | — | not documented |
| `target_table` | TEXT | no | — | — | — | not documented |
| `declared_columns` | TEXT[] | no | — | — | — | not documented |
| `alembic_revision` | TEXT | no | — | — | — | not documented |
| `reason` | TEXT | no | — | — | — | not documented |
| `declared_by_role` | TEXT | no | CURRENT_USER | — | — | not documented |
| `declared_by_login` | TEXT | no | SESSION_USER | — | — | not documented |
| `xact_id` | BIGINT | no | ((pg_current_xact_id())::text)::bigint | — | — | not documented |
| `created_at` | TIMESTAMP WITH TIME ZONE | no | clock_timestamp() | — | — | not documented |
| `expires_at` | TIMESTAMP WITH TIME ZONE | no | clock_timestamp() + interval '1 hour | — | — | not documented |

**Check constraints:**

- `ck_accepted_science_repair_columns_declared`: `cardinality(declared_columns) > 0 AND array_position(declared_columns, NULL) IS NULL`
- `ck_accepted_science_repair_expiry_window`: `expires_at > created_at AND expires_at <= created_at + interval '24 hours'`
- `ck_accepted_science_repair_reason_nonblank`: `length(btrim(reason)) > 0`
- `ck_accepted_science_repair_revision_nonblank`: `length(btrim(alembic_revision)) > 0`
- `ck_accepted_science_repair_target_nonblank`: `length(btrim(target_schema)) > 0 AND length(btrim(target_table)) > 0`

### `accepted_science_repair_change`

**Role:** role not stated on the model

**Purpose:** One accepted record's view of one row a repair rewrote.

| Column | Type | Nullable | Default | Foreign key | Enum values | Meaning |
|---|---|---|---|---|---|---|
| `id` | BIGINT | no | — | — | — | not documented |
| `repair_id` | BIGINT | no | — | accepted_science_repair.id | — | not documented |
| `record_type` | SubmissionRecordType (enum) | no | — | — | `species`, `species_entry`, `conformer_group`, `conformer_observation`, `reaction`, `reaction_entry`, `transition_state`, `transition_state_entry`, `calculation`, `statmech`, `thermo`, `kinetics`, `transport`, `network`, `network_solve`, `applied_energy_correction`, `artifact`, `molecular_property_observation` | not documented |
| `record_id` | BIGINT | no | — | — | — | not documented |
| `target_schema` | TEXT | no | — | — | — | not documented |
| `target_table` | TEXT | no | — | — | — | not documented |
| `row_identity` | JSONB | no | — | — | — | not documented |
| `changed_columns` | TEXT[] | no | — | — | — | not documented |
| `before_json` | JSONB | no | — | — | — | not documented |
| `after_json` | JSONB | no | — | — | — | not documented |
| `recorded_at` | TIMESTAMP WITH TIME ZONE | no | clock_timestamp() | — | — | not documented |

**Check constraints:**

- `ck_accepted_science_repair_change_columns_changed`: `cardinality(changed_columns) > 0`

### `api_key`

**Role:** role not stated on the model

**Purpose:** API keys owned by an :class:`AppUser`.

| Column | Type | Nullable | Default | Foreign key | Enum values | Meaning |
|---|---|---|---|---|---|---|
| `id` | BIGINT | no | — | — | — | not documented |
| `user_id` | BIGINT | no | — | app_user.id | — | not documented |
| `key_hash` | CHAR(64) | no | — | — | — | not documented |
| `label` | TEXT | yes | — | — | — | not documented |
| `last_used_at` | TIMESTAMP WITHOUT TIME ZONE | yes | — | — | — | not documented |
| `revoked_at` | TIMESTAMP WITHOUT TIME ZONE | yes | — | — | — | not documented |
| `created_at` | TIMESTAMP WITHOUT TIME ZONE | no | now() | — | — | not documented |

### `app_user`

**Role:** role not stated on the model

**Purpose:** Application user identity used for curation provenance and auth.

| Column | Type | Nullable | Default | Foreign key | Enum values | Meaning |
|---|---|---|---|---|---|---|
| `id` | BIGINT | no | — | — | — | not documented |
| `username` | TEXT | no | — | — | — | not documented |
| `email` | TEXT | yes | — | — | — | not documented |
| `full_name` | TEXT | yes | — | — | — | not documented |
| `affiliation` | TEXT | yes | — | — | — | not documented |
| `orcid` | CHAR(19) | yes | — | — | — | not documented |
| `password_hash` | TEXT | yes | — | — | — | not documented |
| `is_active` | BOOLEAN | no | true | — | — | not documented |
| `role` | AppUserRole (enum) | no | user | — | `user`, `curator`, `admin` | not documented |
| `created_at` | TIMESTAMP WITHOUT TIME ZONE | no | now() | — | — | not documented |

### `applied_energy_correction`

**Role:** role not stated on the model

**Purpose:** One energy correction applied to a specific entry.

| Column | Type | Nullable | Default | Foreign key | Enum values | Meaning |
|---|---|---|---|---|---|---|
| `id` | BIGINT | no | — | — | — | not documented |
| `target_species_entry_id` | BIGINT | yes | — | species_entry.id | — | not documented |
| `target_reaction_entry_id` | BIGINT | yes | — | reaction_entry.id | — | not documented |
| `target_transition_state_entry_id` | BIGINT | yes | — | transition_state_entry.id | — | not documented |
| `source_conformer_observation_id` | BIGINT | yes | — | conformer_observation.id | — | not documented |
| `source_calculation_id` | BIGINT | yes | — | calculation.id | — | not documented |
| `scheme_id` | BIGINT | yes | — | energy_correction_scheme.id | — | not documented |
| `frequency_scale_factor_id` | BIGINT | yes | — | frequency_scale_factor.id | — | not documented |
| `application_role` | EnergyCorrectionApplicationRole (enum) | no | — | — | `zpe`, `thermal_correction_energy`, `thermal_correction_enthalpy`, `thermal_correction_gibbs`, `entropy_contribution`, `bac_total`, `aec_total`, `soc_total`, `atomization_reference_adjustment`, `composite_delta`, `custom` | not documented |
| `value` | DOUBLE PRECISION | no | — | — | — | not documented |
| `value_unit` | EnergyUnit (enum) | no | — | — | `hartree`, `kj_mol`, `kcal_mol` | not documented |
| `temperature_k` | DOUBLE PRECISION | yes | — | — | — | not documented |
| `note` | TEXT | yes | — | — | — | not documented |
| `created_at` | TIMESTAMP WITHOUT TIME ZONE | no | now() | — | — | not documented |
| `created_by` | BIGINT | yes | — | app_user.id | — | not documented |

**Check constraints:**

- `ck_applied_energy_correction_exactly_one_provenance_source`: `num_nonnulls(scheme_id, frequency_scale_factor_id) = 1`
- `ck_applied_energy_correction_exactly_one_target`: `num_nonnulls(target_species_entry_id, target_reaction_entry_id, target_transition_state_entry_id) = 1`
- `ck_applied_energy_correction_temperature_k_gt_0`: `temperature_k IS NULL OR temperature_k > 0`

### `applied_energy_correction_component`

**Role:** role not stated on the model

**Purpose:** Per-component breakdown of an applied energy correction.

| Column | Type | Nullable | Default | Foreign key | Enum values | Meaning |
|---|---|---|---|---|---|---|
| `id` | BIGINT | no | — | — | — | not documented |
| `applied_correction_id` | BIGINT | no | — | applied_energy_correction.id | — | not documented |
| `component_kind` | AppliedCorrectionComponentKind (enum) | no | — | — | `atom`, `bond`, `molecular`, `zpe_scale`, `soc`, `other` | not documented |
| `key` | TEXT | no | — | — | — | not documented |
| `multiplicity` | INTEGER | no | 1 | — | — | not documented |
| `parameter_value` | DOUBLE PRECISION | no | — | — | — | not documented |
| `contribution_value` | DOUBLE PRECISION | no | — | — | — | not documented |

**Check constraints:**

- `ck_applied_energy_correction_component_multiplicity_ge_1`: `multiplicity >= 1`

### `applied_group_additivity_component`

**Role:** role not stated on the model

**Purpose:** Per-Benson-group contribution within one applied GA estimation.

| Column | Type | Nullable | Default | Foreign key | Enum values | Meaning |
|---|---|---|---|---|---|---|
| `id` | BIGINT | no | — | — | — | not documented |
| `applied_group_additivity_id` | BIGINT | no | — | applied_group_additivity.id | — | not documented |
| `component_kind` | GroupAdditivityComponentKind (enum) | no | — | — | `group`, `ring_correction`, `gauche_correction`, `cis_correction`, `symmetry_correction`, `other` | not documented |
| `group_label` | TEXT | no | — | — | — | not documented |
| `count` | INTEGER | no | 1 | — | — | not documented |
| `h298_contribution_kj_mol` | DOUBLE PRECISION | yes | — | — | — | not documented |
| `s298_contribution_j_mol_k` | DOUBLE PRECISION | yes | — | — | — | not documented |
| `cp298_contribution_j_mol_k` | DOUBLE PRECISION | yes | — | — | — | not documented |

**Check constraints:**

- `ck_applied_group_additivity_component_count_ge_1`: `count >= 1`

### `artifact_integrity_event`

**Role:** role not stated on the model

**Purpose:** One observation about TCKDB's custody of a stored artifact.

| Column | Type | Nullable | Default | Foreign key | Enum values | Meaning |
|---|---|---|---|---|---|---|
| `id` | BIGINT | no | — | — | — | not documented |
| `sha256` | CHAR(64) | no | — | — | — | not documented |
| `artifact_id` | BIGINT | yes | — | calculation_artifact.id | — | not documented |
| `finding` | ArtifactIntegrityFinding (enum) | no | — | — | `digest_mismatch`, `size_mismatch`, `object_missing`, `verified` | not documented |
| `detected_during` | ArtifactIntegrityDetectionContext (enum) | no | — | — | `download`, `verification_sweep`, `store_dedup_verification`, `parameter_extraction`, `archive`, `reproducibility_verification`, `reclaim_restore`, `input_geometry_extraction` | not documented |
| `expected_bytes` | BIGINT | yes | — | — | — | not documented |
| `observed_sha256` | CHAR(64) | yes | — | — | — | not documented |
| `observed_bytes` | BIGINT | yes | — | — | — | not documented |
| `object_last_modified_at` | TIMESTAMP WITHOUT TIME ZONE | yes | — | — | — | not documented |
| `object_etag` | TEXT | yes | — | — | — | not documented |
| `object_content_length` | BIGINT | yes | — | — | — | not documented |
| `artifact_recorded_at` | TIMESTAMP WITHOUT TIME ZONE | yes | — | — | — | not documented |
| `detail` | TEXT | yes | — | — | — | not documented |
| `created_at` | TIMESTAMP WITHOUT TIME ZONE | no | now() | — | — | not documented |
| `created_by` | BIGINT | yes | — | app_user.id | — | not documented |
| `public_ref` | VARCHAR(40) | no | — | — | — | not documented |

**Check constraints:**

- `ck_artifact_integrity_event_observed_digest_present_iff_read`: `(finding = 'object_missing' AND observed_sha256 IS NULL) OR (finding <> 'object_missing' AND observed_sha256 IS NOT NULL)`
- `ck_artifact_integrity_event_observed_sha256_lower_hex`: `observed_sha256 IS NULL OR observed_sha256 ~ '^[0-9a-f]{64}$'`
- `ck_artifact_integrity_event_sha256_lower_hex`: `sha256 ~ '^[0-9a-f]{64}$'`
- `ck_artifact_integrity_event_verified_requires_matching_digest`: `finding <> 'verified' OR observed_sha256 = sha256`

### `artifact_storage_capacity_event`

**Role:** role not stated on the model

**Purpose:** One observation about the object store's capacity to accept a write.

| Column | Type | Nullable | Default | Foreign key | Enum values | Meaning |
|---|---|---|---|---|---|---|
| `id` | BIGINT | no | — | — | — | not documented |
| `observation` | ArtifactStorageCapacityObservation (enum) | no | — | — | `refused`, `accepted`, `capacity_report`, `operator_clear` | not documented |
| `observed_bytes` | BIGINT | yes | — | — | — | not documented |
| `s3_code` | TEXT | yes | — | — | — | not documented |
| `detail` | TEXT | yes | — | — | — | not documented |
| `created_at` | TIMESTAMP WITHOUT TIME ZONE | no | now() | — | — | not documented |
| `created_by` | BIGINT | yes | — | app_user.id | — | not documented |

**Check constraints:**

- `ck_artifact_storage_capacity_event_bytes_match_observation`: `(observation <> 'operator_clear' OR observed_bytes IS NULL) AND (observation <> 'capacity_report' OR observed_bytes IS NOT NULL) AND (observation <> 'accepted' OR observed_bytes IS NOT NULL)`
- `ck_artifact_storage_capacity_event_s3_code_only_on_refused`: `observation = 'refused' OR s3_code IS NULL`

### `author`

**Role:** role not stated on the model

**Purpose:** not documented

| Column | Type | Nullable | Default | Foreign key | Enum values | Meaning |
|---|---|---|---|---|---|---|
| `id` | BIGINT | no | — | — | — | not documented |
| `given_name` | TEXT | yes | — | — | — | not documented |
| `family_name` | TEXT | no | — | — | — | not documented |
| `full_name` | TEXT | no | — | — | — | not documented |
| `orcid` | CHAR(19) | yes | — | — | — | not documented |
| `created_at` | TIMESTAMP WITHOUT TIME ZONE | no | now() | — | — | not documented |

### `calc_freq_mode`

**Role:** role not stated on the model

**Purpose:** One vibrational mode parsed from a frequency calculation.

| Column | Type | Nullable | Default | Foreign key | Enum values | Meaning |
|---|---|---|---|---|---|---|
| `calculation_id` | BIGINT | no | — | calculation.id | — | not documented |
| `mode_index` | INTEGER | no | — | — | — | not documented |
| `frequency_cm1` | FLOAT | no | — | — | — | not documented |
| `is_imaginary` | BOOLEAN | no | — | — | — | not documented |
| `reduced_mass_amu` | FLOAT | yes | — | — | — | not documented |
| `force_constant_mdyne_angstrom` | FLOAT | yes | — | — | — | not documented |
| `ir_intensity_km_mol` | FLOAT | yes | — | — | — | not documented |
| `raman_activity` | FLOAT | yes | — | — | — | not documented |
| `symmetry_label` | TEXT | yes | — | — | — | not documented |
| `imaginary_disposition` | ImaginaryModeDisposition (enum) | yes | — | — | `rigid_body_residue`, `torsion`, `ring_pucker`, `intermolecular`, `symmetry_breaking`, `unassigned` | not documented |
| `note` | TEXT | yes | — | — | — | not documented |

**Check constraints:**

- `ck_calc_freq_mode_frequency_sign_matches_is_imaginary`: `(is_imaginary AND frequency_cm1 < 0) OR (NOT is_imaginary AND frequency_cm1 >= 0)`
- `ck_calc_freq_mode_imaginary_disposition_requires_imaginary_mode`: `imaginary_disposition IS NULL OR is_imaginary`
- `ck_calc_freq_mode_ir_intensity_km_mol_ge_0`: `ir_intensity_km_mol IS NULL OR ir_intensity_km_mol >= 0`
- `ck_calc_freq_mode_mode_index_ge_1`: `mode_index >= 1`
- `ck_calc_freq_mode_reduced_mass_amu_gt_0`: `reduced_mass_amu IS NULL OR reduced_mass_amu > 0`

### `calc_freq_result`

**Role:** role not stated on the model

**Purpose:** not documented

| Column | Type | Nullable | Default | Foreign key | Enum values | Meaning |
|---|---|---|---|---|---|---|
| `calculation_id` | BIGINT | no | — | calculation.id | — | not documented |
| `n_imag` | INTEGER | yes | — | — | — | not documented |
| `imag_freq_cm1` | FLOAT | yes | — | — | — | not documented |
| `zpe_hartree` | FLOAT | yes | — | — | — | not documented |
| `zpe_uncertainty_hartree` | FLOAT | yes | — | — | — | not documented |
| `reaction_coordinate_mode_index` | INTEGER | yes | — | — | — | not documented |
| `imaginary_mode_tau_cm1` | FLOAT | yes | — | — | — | not documented |
| `imaginary_mode_tau_basis` | TEXT | yes | — | — | — | not documented |
| `imaginary_mode_structural_flag` | BOOLEAN | yes | — | — | — | not documented |

**Check constraints:**

- `ck_calc_freq_result_imaginary_mode_tau_basis_known`: `imaginary_mode_tau_basis IS NULL OR imaginary_mode_tau_basis IN ('analytic_tight', 'analytic_default', 'finite_difference_gradient', 'finite_difference_energy', 'protocol_not_recorded', 'assumed_analytic_default', 'assumed_finite_difference_gradient', 'assumed_finite_difference_energy')`

### `calc_geometry_validation`

**Role:** role not stated on the model

**Purpose:** Evidence that a calculation's output geometry preserves the intended molecular identity.

| Column | Type | Nullable | Default | Foreign key | Enum values | Meaning |
|---|---|---|---|---|---|---|
| `calculation_id` | BIGINT | no | — | calculation.id | — | not documented |
| `input_geometry_id` | BIGINT | yes | — | geometry.id | — | not documented |
| `output_geometry_id` | BIGINT | yes | — | geometry.id | — | not documented |
| `species_smiles` | TEXT | no | — | — | — | not documented |
| `is_isomorphic` | BOOLEAN | no | — | — | — | not documented |
| `rmsd` | FLOAT | yes | — | — | — | not documented |
| `atom_mapping` | JSONB | yes | — | — | — | not documented |
| `n_mappings` | INTEGER | yes | — | — | — | not documented |
| `validation_status` | ValidationStatus (enum) | no | — | — | `passed`, `warning`, `fail` | not documented |
| `validation_reason` | TEXT | yes | — | — | — | not documented |
| `rmsd_warning_threshold` | FLOAT | yes | — | — | — | not documented |
| `created_at` | TIMESTAMP WITHOUT TIME ZONE | no | now() | — | — | not documented |

### `calc_hessian`

**Role:** role not stated on the model

**Purpose:** Cartesian second-derivative (Hessian) matrix for a calculation.

| Column | Type | Nullable | Default | Foreign key | Enum values | Meaning |
|---|---|---|---|---|---|---|
| `calculation_id` | BIGINT | no | — | calculation.id | — | not documented |
| `geometry_id` | BIGINT | no | — | geometry.id | — | not documented |
| `natoms` | INTEGER | no | — | — | — | not documented |
| `lower_triangle_hartree_bohr2` | FLOAT[] | no | — | — | — | not documented |
| `source` | HessianSource (enum) | no | — | — | `parsed_fchk`, `parsed_hess`, `parsed_log`, `uploaded`, `derived` | not documented |
| `parser_version` | TEXT | yes | — | — | — | not documented |
| `note` | TEXT | yes | — | — | — | not documented |
| `created_at` | TIMESTAMP WITHOUT TIME ZONE | no | now() | — | — | not documented |
| `created_by` | BIGINT | yes | — | app_user.id | — | not documented |

**Check constraints:**

- `ck_calc_hessian_hessian_lower_triangle_cardinality`: `cardinality(lower_triangle_hartree_bohr2) = (3 * natoms) * (3 * natoms + 1) / 2`
- `ck_calc_hessian_hessian_natoms_ge_1`: `natoms >= 1`

### `calc_irc_point`

**Role:** role not stated on the model

**Purpose:** One sampled point on an IRC path.

| Column | Type | Nullable | Default | Foreign key | Enum values | Meaning |
|---|---|---|---|---|---|---|
| `calculation_id` | BIGINT | no | — | calculation.id | — | not documented |
| `point_index` | INTEGER | no | — | — | — | not documented |
| `direction` | IRCDirection (enum) | yes | — | — | `forward`, `reverse`, `both` | not documented |
| `is_ts` | BOOLEAN | no | False | — | — | not documented |
| `reaction_coordinate` | FLOAT | yes | — | — | — | not documented |
| `electronic_energy_hartree` | FLOAT | yes | — | — | — | not documented |
| `relative_energy_kj_mol` | FLOAT | yes | — | — | — | not documented |
| `max_gradient` | FLOAT | yes | — | — | — | not documented |
| `rms_gradient` | FLOAT | yes | — | — | — | not documented |
| `geometry_id` | BIGINT | yes | — | geometry.id | — | not documented |
| `note` | TEXT | yes | — | — | — | not documented |

**Check constraints:**

- `ck_calc_irc_point_point_index_ge_0`: `point_index >= 0`

### `calc_irc_result`

**Role:** role not stated on the model

**Purpose:** IRC-level metadata for an IRC calculation.

| Column | Type | Nullable | Default | Foreign key | Enum values | Meaning |
|---|---|---|---|---|---|---|
| `calculation_id` | BIGINT | no | — | calculation.id | — | not documented |
| `direction` | IRCDirection (enum) | no | — | — | `forward`, `reverse`, `both` | not documented |
| `has_forward` | BOOLEAN | no | False | — | — | not documented |
| `has_reverse` | BOOLEAN | no | False | — | — | not documented |
| `ts_point_index` | INTEGER | yes | — | — | — | not documented |
| `point_count` | INTEGER | yes | — | — | — | not documented |
| `zero_energy_reference_hartree` | FLOAT | yes | — | — | — | not documented |
| `note` | TEXT | yes | — | — | — | not documented |

**Check constraints:**

- `ck_calc_irc_result_point_count_ge_0`: `point_count IS NULL OR point_count >= 0`

### `calc_opt_result`

**Role:** role not stated on the model

**Purpose:** not documented

| Column | Type | Nullable | Default | Foreign key | Enum values | Meaning |
|---|---|---|---|---|---|---|
| `calculation_id` | BIGINT | no | — | calculation.id | — | not documented |
| `converged` | BOOLEAN | yes | — | — | — | not documented |
| `n_steps` | INTEGER | yes | — | — | — | not documented |
| `final_energy_hartree` | FLOAT | yes | — | — | — | not documented |

**Check constraints:**

- `ck_calc_opt_result_n_steps_ge_0`: `n_steps IS NULL OR n_steps >= 0`

### `calc_path_search_point`

**Role:** role not stated on the model

**Purpose:** One sampled point on a path-search calculation's reaction path.

| Column | Type | Nullable | Default | Foreign key | Enum values | Meaning |
|---|---|---|---|---|---|---|
| `calculation_id` | BIGINT | no | — | calculation.id | — | not documented |
| `point_index` | INTEGER | no | — | — | — | not documented |
| `electronic_energy_hartree` | FLOAT | yes | — | — | — | not documented |
| `relative_energy_kj_mol` | FLOAT | yes | — | — | — | not documented |
| `path_coordinate` | FLOAT | yes | — | — | — | not documented |
| `max_force` | FLOAT | yes | — | — | — | not documented |
| `rms_force` | FLOAT | yes | — | — | — | not documented |
| `max_gradient` | FLOAT | yes | — | — | — | not documented |
| `rms_gradient` | FLOAT | yes | — | — | — | not documented |
| `is_ts_guess` | BOOLEAN | no | False | — | — | not documented |
| `is_climbing_image` | BOOLEAN | no | False | — | — | not documented |
| `geometry_id` | BIGINT | yes | — | geometry.id | — | not documented |
| `note` | TEXT | yes | — | — | — | not documented |

**Check constraints:**

- `ck_calc_path_search_point_point_index_ge_0`: `point_index >= 0`

### `calc_path_search_result`

**Role:** role not stated on the model

**Purpose:** Path-search-level metadata for a calculation that explored a reaction path between or from molecular endpoints.

| Column | Type | Nullable | Default | Foreign key | Enum values | Meaning |
|---|---|---|---|---|---|---|
| `calculation_id` | BIGINT | no | — | calculation.id | — | not documented |
| `method` | PathSearchMethod (enum) | no | — | — | `neb`, `gsm`, `growing_string`, `freezing_string`, `other` | not documented |
| `is_double_ended` | BOOLEAN | yes | — | — | — | not documented |
| `converged` | BOOLEAN | yes | — | — | — | not documented |
| `n_points` | INTEGER | yes | — | — | — | not documented |
| `selected_ts_point_index` | INTEGER | yes | — | — | — | not documented |
| `climbing_image_index` | INTEGER | yes | — | — | — | not documented |
| `source_endpoint_count` | INTEGER | yes | — | — | — | not documented |
| `zero_energy_reference_hartree` | FLOAT | yes | — | — | — | not documented |
| `note` | TEXT | yes | — | — | — | not documented |

**Check constraints:**

- `ck_calc_path_search_result_climbing_image_index_ge_0`: `climbing_image_index IS NULL OR climbing_image_index >= 0`
- `ck_calc_path_search_result_n_points_ge_1`: `n_points IS NULL OR n_points >= 1`
- `ck_calc_path_search_result_selected_ts_point_index_ge_0`: `selected_ts_point_index IS NULL OR selected_ts_point_index >= 0`
- `ck_calc_path_search_result_source_endpoint_count_ge_1`: `source_endpoint_count IS NULL OR source_endpoint_count >= 1`

### `calc_scan_coordinate`

**Role:** role not stated on the model

**Purpose:** Definition of one scanned internal coordinate.

| Column | Type | Nullable | Default | Foreign key | Enum values | Meaning |
|---|---|---|---|---|---|---|
| `calculation_id` | BIGINT | no | — | calculation.id | — | not documented |
| `coordinate_index` | INTEGER | no | — | — | — | not documented |
| `coordinate_kind` | ScanCoordinateKind (enum) | no | — | — | `bond`, `angle`, `dihedral`, `improper` | not documented |
| `atom1_index` | INTEGER | no | — | — | — | not documented |
| `atom2_index` | INTEGER | no | — | — | — | not documented |
| `atom3_index` | INTEGER | yes | — | — | — | not documented |
| `atom4_index` | INTEGER | yes | — | — | — | not documented |
| `step_count` | INTEGER | yes | — | — | — | not documented |
| `step_size` | FLOAT | yes | — | — | — | not documented |
| `start_value` | FLOAT | yes | — | — | — | not documented |
| `end_value` | FLOAT | yes | — | — | — | not documented |
| `value_unit` | CoordinateUnit (enum) | yes | — | — | `angstrom`, `degree` | not documented |
| `resolution_degrees` | INTEGER | yes | — | — | — | not documented |
| `symmetry_number` | SMALLINT | yes | — | — | — | not documented |

**Check constraints:**

- `ck_calc_scan_coordinate_atom1_index_ge_1`: `atom1_index >= 1`
- `ck_calc_scan_coordinate_atom2_index_ge_1`: `atom2_index >= 1`
- `ck_calc_scan_coordinate_atom3_index_ge_1`: `atom3_index IS NULL OR atom3_index >= 1`
- `ck_calc_scan_coordinate_atom4_index_ge_1`: `atom4_index IS NULL OR atom4_index >= 1`
- `ck_calc_scan_coordinate_coordinate_arity_matches_kind`: `
            CASE coordinate_kind
                WHEN 'bond' THEN atom3_index IS NULL AND atom4_index IS NULL
                WHEN 'angle' THEN atom3_index IS NOT NULL AND atom4_index IS NULL
                ELSE atom3_index IS NOT NULL AND atom4_index IS NOT NULL
            END
            `
- `ck_calc_scan_coordinate_coordinate_index_ge_1`: `coordinate_index >= 1`
- `ck_calc_scan_coordinate_resolution_degrees_ge_1`: `resolution_degrees IS NULL OR resolution_degrees >= 1`
- `ck_calc_scan_coordinate_step_count_ge_1`: `step_count IS NULL OR step_count >= 1`
- `ck_calc_scan_coordinate_symmetry_number_ge_1`: `symmetry_number IS NULL OR symmetry_number >= 1`

### `calc_scan_point`

**Role:** role not stated on the model

**Purpose:** One sampled point on a scan surface.

| Column | Type | Nullable | Default | Foreign key | Enum values | Meaning |
|---|---|---|---|---|---|---|
| `calculation_id` | BIGINT | no | — | calculation.id | — | not documented |
| `point_index` | INTEGER | no | — | — | — | not documented |
| `electronic_energy_hartree` | FLOAT | yes | — | — | — | not documented |
| `relative_energy_kj_mol` | FLOAT | yes | — | — | — | not documented |
| `geometry_id` | BIGINT | yes | — | geometry.id | — | not documented |
| `note` | TEXT | yes | — | — | — | not documented |

**Check constraints:**

- `ck_calc_scan_point_point_index_ge_1`: `point_index >= 1`

### `calc_scan_point_coordinate_value`

**Role:** role not stated on the model

**Purpose:** Coordinate values for one sampled scan point.

| Column | Type | Nullable | Default | Foreign key | Enum values | Meaning |
|---|---|---|---|---|---|---|
| `calculation_id` | BIGINT | no | — | calc_scan_coordinate.calculation_id | — | not documented |
| `point_index` | INTEGER | no | — | calc_scan_point.point_index | — | not documented |
| `coordinate_index` | INTEGER | no | — | calc_scan_coordinate.coordinate_index | — | not documented |
| `coordinate_value` | FLOAT | no | — | — | — | not documented |
| `value_unit` | CoordinateUnit (enum) | yes | — | — | `angstrom`, `degree` | not documented |

**Check constraints:**

- `ck_calc_scan_point_coordinate_value_coordinate_index_ge_1`: `coordinate_index >= 1`
- `ck_calc_scan_point_coordinate_value_point_index_ge_1`: `point_index >= 1`

### `calc_scan_result`

**Role:** role not stated on the model

**Purpose:** Scan-level metadata for a scan calculation.

| Column | Type | Nullable | Default | Foreign key | Enum values | Meaning |
|---|---|---|---|---|---|---|
| `calculation_id` | BIGINT | no | — | calculation.id | — | not documented |
| `dimension` | INTEGER | no | — | — | — | not documented |
| `is_relaxed` | BOOLEAN | yes | — | — | — | not documented |
| `zero_energy_reference_hartree` | FLOAT | yes | — | — | — | not documented |
| `note` | TEXT | yes | — | — | — | not documented |

**Check constraints:**

- `ck_calc_scan_result_dimension_ge_1`: `dimension >= 1`

### `calc_sp_result`

**Role:** role not stated on the model

**Purpose:** not documented

| Column | Type | Nullable | Default | Foreign key | Enum values | Meaning |
|---|---|---|---|---|---|---|
| `calculation_id` | BIGINT | no | — | calculation.id | — | not documented |
| `electronic_energy_hartree` | FLOAT | yes | — | — | — | not documented |
| `electronic_energy_uncertainty_hartree` | FLOAT | yes | — | — | — | not documented |

### `calculation`

**Role:** role not stated on the model

**Purpose:** Computational record with one scientific owner and an optional observation anchor.

| Column | Type | Nullable | Default | Foreign key | Enum values | Meaning |
|---|---|---|---|---|---|---|
| `id` | BIGINT | no | — | — | — | not documented |
| `type` | CalculationType (enum) | no | — | — | `opt`, `freq`, `sp`, `irc`, `scan`, `path_search`, `conf` | not documented |
| `quality` | CalculationQuality (enum) | no | raw | — | `raw`, `curated`, `rejected` | not documented |
| `species_entry_id` | BIGINT | yes | — | species_entry.id | — | not documented |
| `transition_state_entry_id` | BIGINT | yes | — | transition_state_entry.id | — | not documented |
| `software_release_id` | BIGINT | yes | — | software_release.id | — | not documented |
| `workflow_tool_release_id` | BIGINT | yes | — | workflow_tool_release.id | — | not documented |
| `lot_id` | BIGINT | yes | — | level_of_theory.id | — | not documented |
| `literature_id` | BIGINT | yes | — | literature.id | — | not documented |
| `execution_environment_manifest_id` | BIGINT | yes | — | execution_environment_manifest.id | — | not documented |
| `conformer_observation_id` | BIGINT | yes | — | conformer_observation.id | — | not documented |
| `parameters_json` | JSONB | yes | — | — | — | not documented |
| `parameters_parser_version` | TEXT | yes | — | — | — | not documented |
| `parameters_extracted_at` | TIMESTAMP WITHOUT TIME ZONE | yes | — | — | — | not documented |
| `software_reconciliation_status` | SoftwareReconciliationStatus (enum) | yes | — | — | `matched`, `enriched`, `mismatch`, `declared_only`, `parsed_only` | not documented |
| `observed_software_banner` | TEXT | yes | — | — | — | not documented |
| `declared_software_banner` | TEXT | yes | — | — | — | not documented |
| `created_at` | TIMESTAMP WITHOUT TIME ZONE | no | now() | — | — | not documented |
| `created_by` | BIGINT | yes | — | app_user.id | — | not documented |
| `public_ref` | VARCHAR(40) | no | — | — | — | not documented |

**Check constraints:**

- `ck_calculation_one_owner`: `
                (
                    transition_state_entry_id IS NOT NULL
                    AND species_entry_id IS NULL
                )
                OR
                (
                    transition_state_entry_id IS NULL
                    AND species_entry_id IS NOT NULL
                )
                `

### `calculation_artifact`

**Role:** role not stated on the model

**Purpose:** Append-only artifact metadata: bytes-on-S3 plus minimal upload context.

| Column | Type | Nullable | Default | Foreign key | Enum values | Meaning |
|---|---|---|---|---|---|---|
| `id` | BIGINT | no | — | — | — | not documented |
| `calculation_id` | BIGINT | no | — | calculation.id | — | not documented |
| `kind` | ArtifactKind (enum) | no | — | — | `input`, `output_log`, `checkpoint`, `formatted_checkpoint`, `hessian`, `ancillary` | not documented |
| `uri` | TEXT | no | — | — | — | not documented |
| `sha256` | CHAR(64) | no | — | — | — | not documented |
| `bytes` | BIGINT | no | — | — | — | not documented |
| `filename` | TEXT | no | — | — | — | not documented |
| `note` | TEXT | yes | — | — | — | not documented |
| `created_at` | TIMESTAMP WITHOUT TIME ZONE | no | now() | — | — | not documented |
| `created_by` | BIGINT | yes | — | app_user.id | — | not documented |
| `public_ref` | VARCHAR(40) | no | — | — | — | not documented |

**Check constraints:**

- `ck_calculation_artifact_bytes_gt_0`: `bytes > 0`
- `ck_calculation_artifact_sha256_lower_hex`: `sha256 ~ '^[0-9a-f]{64}$'`

### `calculation_constraint`

**Role:** role not stated on the model

**Purpose:** Geometric constraint applied to a calculation.

| Column | Type | Nullable | Default | Foreign key | Enum values | Meaning |
|---|---|---|---|---|---|---|
| `calculation_id` | BIGINT | no | — | calculation.id | — | not documented |
| `constraint_index` | INTEGER | no | — | — | — | not documented |
| `constraint_kind` | ConstraintKind (enum) | no | — | — | `cartesian_atom`, `bond`, `angle`, `dihedral`, `improper` | not documented |
| `atom1_index` | INTEGER | no | — | — | — | not documented |
| `atom2_index` | INTEGER | yes | — | — | — | not documented |
| `atom3_index` | INTEGER | yes | — | — | — | not documented |
| `atom4_index` | INTEGER | yes | — | — | — | not documented |
| `target_value` | FLOAT | yes | — | — | — | not documented |

**Check constraints:**

- `ck_calculation_constraint_atom1_index_ge_1`: `atom1_index >= 1`
- `ck_calculation_constraint_atom2_index_ge_1`: `atom2_index IS NULL OR atom2_index >= 1`
- `ck_calculation_constraint_atom3_index_ge_1`: `atom3_index IS NULL OR atom3_index >= 1`
- `ck_calculation_constraint_atom4_index_ge_1`: `atom4_index IS NULL OR atom4_index >= 1`
- `ck_calculation_constraint_constraint_arity_matches_kind`: `
            CASE constraint_kind
                WHEN 'cartesian_atom' THEN atom2_index IS NULL AND atom3_index IS NULL AND atom4_index IS NULL
                WHEN 'bond' THEN atom2_index IS NOT NULL AND atom3_index IS NULL AND atom4_index IS NULL
                WHEN 'angle' THEN atom2_index IS NOT NULL AND atom3_index IS NOT NULL AND atom4_index IS NULL
                ELSE atom2_index IS NOT NULL AND atom3_index IS NOT NULL AND atom4_index IS NOT NULL
            END
            `
- `ck_calculation_constraint_constraint_index_ge_1`: `constraint_index >= 1`

### `calculation_dependency`

**Role:** role not stated on the model

**Purpose:** Directed dependency edge between two calculations.

| Column | Type | Nullable | Default | Foreign key | Enum values | Meaning |
|---|---|---|---|---|---|---|
| `parent_calculation_id` | BIGINT | no | — | calculation.id | — | not documented |
| `child_calculation_id` | BIGINT | no | — | calculation.id | — | not documented |
| `dependency_role` | CalculationDependencyRole (enum) | no | — | — | `optimized_from`, `freq_on`, `single_point_on`, `arkane_source`, `irc_start`, `irc_followup`, `scan_parent` | not documented |

**Check constraints:**

- `ck_calculation_dependency_not_self`: `parent_calculation_id <> child_calculation_id`

### `calculation_input_geometry`

**Role:** role not stated on the model

**Purpose:** Ordered input-geometry link table for a calculation.

| Column | Type | Nullable | Default | Foreign key | Enum values | Meaning |
|---|---|---|---|---|---|---|
| `calculation_id` | BIGINT | no | — | calculation.id | — | not documented |
| `geometry_id` | BIGINT | no | — | geometry.id | — | not documented |
| `input_order` | INTEGER | no | 1 | — | — | not documented |
| `source` | CalculationInputGeometrySource (enum) | no | deposited | — | `deposited`, `extracted_from_artifact` | not documented |

**Check constraints:**

- `ck_calculation_input_geometry_input_order_ge_1`: `input_order >= 1`

### `calculation_output_geometry`

**Role:** role not stated on the model

**Purpose:** not documented

| Column | Type | Nullable | Default | Foreign key | Enum values | Meaning |
|---|---|---|---|---|---|---|
| `calculation_id` | BIGINT | no | — | calculation.id | — | not documented |
| `geometry_id` | BIGINT | no | — | geometry.id | — | not documented |
| `output_order` | INTEGER | no | 1 | — | — | not documented |
| `role` | CalculationGeometryRole (enum) | yes | — | — | `final`, `initial`, `scan_point`, `irc_forward`, `irc_reverse`, `path_search_point` | not documented |

**Check constraints:**

- `ck_calculation_output_geometry_output_order_ge_1`: `output_order >= 1`

### `calculation_parameter_vocab`

**Role:** role not stated on the model

**Purpose:** Ontology seed for canonical parameter keys.

| Column | Type | Nullable | Default | Foreign key | Enum values | Meaning |
|---|---|---|---|---|---|---|
| `canonical_key` | TEXT | no | — | — | — | not documented |
| `description` | TEXT | yes | — | — | — | not documented |
| `expected_value_type` | TEXT | yes | — | — | — | not documented |
| `affects_scientific_result` | BOOLEAN | yes | — | — | — | not documented |
| `affects_numerics` | BOOLEAN | yes | — | — | — | not documented |
| `affects_resources` | BOOLEAN | yes | — | — | — | not documented |
| `note` | TEXT | yes | — | — | — | not documented |
| `created_at` | TIMESTAMP WITHOUT TIME ZONE | no | now() | — | — | not documented |

### `chem_reaction`

**Role:** role not stated on the model

**Purpose:** not documented

| Column | Type | Nullable | Default | Foreign key | Enum values | Meaning |
|---|---|---|---|---|---|---|
| `id` | BIGINT | no | — | — | — | not documented |
| `stoichiometry_hash` | CHAR(64) | yes | — | — | — | not documented |
| `reversible` | BOOLEAN | no | — | — | — | not documented |
| `reaction_family_id` | BIGINT | yes | — | reaction_family.id | — | not documented |
| `reaction_family_raw` | TEXT | yes | — | — | — | not documented |
| `reaction_family_source_note` | TEXT | yes | — | — | — | not documented |
| `created_at` | TIMESTAMP WITHOUT TIME ZONE | no | now() | — | — | not documented |
| `public_ref` | VARCHAR(40) | no | — | — | — | not documented |

**Check constraints:**

- `ck_chem_reaction_reaction_family_raw_requires_source_note`: `reaction_family_raw IS NULL OR reaction_family_source_note IS NOT NULL`

### `dataset_release`

**Role:** role not stated on the model

**Purpose:** A citable dataset release.

| Column | Type | Nullable | Default | Foreign key | Enum values | Meaning |
|---|---|---|---|---|---|---|
| `id` | BIGINT | no | — | — | — | not documented |
| `tag` | VARCHAR(64) | no | — | — | — | not documented |
| `title` | TEXT | no | — | — | — | not documented |
| `description` | TEXT | yes | — | — | — | not documented |
| `status` | DatasetReleaseStatus (enum) | no | draft | — | `draft`, `published`, `withdrawn` | not documented |
| `curation_policy_id` | BIGINT | no | — | curation_policy.id | — | not documented |
| `data_license` | VARCHAR(64) | no | — | — | — | not documented |
| `code_license` | VARCHAR(64) | no | — | — | — | not documented |
| `citation_text` | TEXT | no | — | — | — | not documented |
| `doi` | VARCHAR(128) | yes | — | — | — | not documented |
| `contact` | TEXT | no | — | — | — | not documented |
| `changelog_entry` | TEXT | yes | — | — | — | not documented |
| `published_at` | TIMESTAMP WITHOUT TIME ZONE | yes | — | — | — | not documented |
| `withdrawn_at` | TIMESTAMP WITHOUT TIME ZONE | yes | — | — | — | not documented |
| `withdrawn_reason` | TEXT | yes | — | — | — | not documented |
| `created_at` | TIMESTAMP WITHOUT TIME ZONE | no | now() | — | — | not documented |
| `created_by` | BIGINT | yes | — | app_user.id | — | not documented |
| `public_ref` | VARCHAR(40) | no | — | — | — | not documented |

**Check constraints:**

- `ck_dataset_release_citation_nonblank`: `length(btrim(citation_text)) > 0`
- `ck_dataset_release_code_license_nonblank`: `length(btrim(code_license)) > 0`
- `ck_dataset_release_contact_nonblank`: `length(btrim(contact)) > 0`
- `ck_dataset_release_data_license_nonblank`: `length(btrim(data_license)) > 0`
- `ck_dataset_release_published_at_matches_status`: `(status = 'draft' AND published_at IS NULL) OR (status IN ('published', 'withdrawn') AND published_at IS NOT NULL)`
- `ck_dataset_release_tag_nonblank`: `length(btrim(tag)) > 0`
- `ck_dataset_release_title_nonblank`: `length(btrim(title)) > 0`
- `ck_dataset_release_withdrawn_has_reason`: `(status <> 'withdrawn') OR (withdrawn_at IS NOT NULL AND length(btrim(coalesce(withdrawn_reason, ''))) > 0)`

### `energy_correction_scheme`

**Role:** role not stated on the model

**Purpose:** Reusable energy-correction parameter set.

| Column | Type | Nullable | Default | Foreign key | Enum values | Meaning |
|---|---|---|---|---|---|---|
| `id` | BIGINT | no | — | — | — | not documented |
| `kind` | EnergyCorrectionSchemeKind (enum) | no | — | — | `atom_energy`, `atom_hf`, `atom_thermal`, `soc`, `bac_petersson`, `bac_melius`, `isodesmic`, `other` | not documented |
| `name` | TEXT | no | — | — | — | not documented |
| `level_of_theory_id` | BIGINT | yes | — | level_of_theory.id | — | not documented |
| `source_literature_id` | BIGINT | yes | — | literature.id | — | not documented |
| `software_release_id` | BIGINT | yes | — | software_release.id | — | not documented |
| `workflow_tool_release_id` | BIGINT | yes | — | workflow_tool_release.id | — | not documented |
| `units` | EnergyUnit (enum) | yes | — | — | `hartree`, `kj_mol`, `kcal_mol` | not documented |
| `note` | TEXT | yes | — | — | — | not documented |
| `created_at` | TIMESTAMP WITHOUT TIME ZONE | no | now() | — | — | not documented |
| `created_by` | BIGINT | yes | — | app_user.id | — | not documented |
| `public_ref` | VARCHAR(40) | no | — | — | — | not documented |

### `energy_correction_scheme_atom_param`

**Role:** role not stated on the model

**Purpose:** Element-keyed scalar parameter within a correction scheme.

| Column | Type | Nullable | Default | Foreign key | Enum values | Meaning |
|---|---|---|---|---|---|---|
| `scheme_id` | BIGINT | no | — | energy_correction_scheme.id | — | not documented |
| `element` | TEXT | no | — | — | — | not documented |
| `value` | DOUBLE PRECISION | no | — | — | — | not documented |

### `energy_correction_scheme_bond_param`

**Role:** role not stated on the model

**Purpose:** Bond-type keyed scalar parameter within a correction scheme.

| Column | Type | Nullable | Default | Foreign key | Enum values | Meaning |
|---|---|---|---|---|---|---|
| `scheme_id` | BIGINT | no | — | energy_correction_scheme.id | — | not documented |
| `bond_key` | TEXT | no | — | — | — | not documented |
| `value` | DOUBLE PRECISION | no | — | — | — | not documented |

### `energy_correction_scheme_component_param`

**Role:** role not stated on the model

**Purpose:** Multi-component parameter within a correction scheme.

| Column | Type | Nullable | Default | Foreign key | Enum values | Meaning |
|---|---|---|---|---|---|---|
| `scheme_id` | BIGINT | no | — | energy_correction_scheme.id | — | not documented |
| `component_kind` | MeliusBacComponentKind (enum) | no | — | — | `atom_corr`, `bond_corr_length`, `bond_corr_neighbor`, `mol_corr` | not documented |
| `key` | TEXT | no | — | — | — | not documented |
| `value` | DOUBLE PRECISION | no | — | — | — | not documented |

### `execution_environment_manifest`

**Role:** role not stated on the model

**Purpose:** A normalized, immutable manifest whose SHA-256 identifies its exact closure.

| Column | Type | Nullable | Default | Foreign key | Enum values | Meaning |
|---|---|---|---|---|---|---|
| `id` | BIGINT | no | — | — | — | not documented |
| `schema_version` | VARCHAR(32) | no | — | — | — | not documented |
| `content_digest` | VARCHAR(71) | no | — | — | — | not documented |
| `runtime_kind` | VARCHAR(32) | no | — | — | — | not documented |
| `runtime_locator` | TEXT | no | — | — | — | not documented |
| `executable_locator` | TEXT | no | — | — | — | not documented |
| `software_release_id` | BIGINT | no | — | software_release.id | — | not documented |
| `workflow_tool_release_id` | BIGINT | yes | — | workflow_tool_release.id | — | not documented |
| `closure_json` | JSONB | no | — | — | — | not documented |
| `canonical_json` | JSONB | no | — | — | — | not documented |
| `created_at` | TIMESTAMP WITHOUT TIME ZONE | no | now() | — | — | not documented |

**Check constraints:**

- `ck_execution_environment_manifest_canonical_json_object`: `jsonb_typeof(canonical_json) = 'object'`
- `ck_execution_environment_manifest_closure_json_array`: `jsonb_typeof(closure_json) = 'array'`
- `ck_execution_environment_manifest_content_digest_sha256`: `content_digest ~ '^sha256:[0-9a-f]{64}$'`

### `external_source`

**Role:** role not stated on the model

**Purpose:** One external database/collection a custody record can cite.

| Column | Type | Nullable | Default | Foreign key | Enum values | Meaning |
|---|---|---|---|---|---|---|
| `id` | BIGINT | no | — | — | — | not documented |
| `source_name` | TEXT | no | — | — | — | not documented |
| `source_release` | TEXT | no | — | — | — | not documented |
| `source_database_doi` | TEXT | yes | — | — | — | not documented |
| `citation_text` | TEXT | yes | — | — | — | not documented |
| `terms_url` | TEXT | yes | — | — | — | not documented |
| `terms_text` | TEXT | yes | — | — | — | not documented |
| `created_at` | TIMESTAMP WITHOUT TIME ZONE | no | now() | — | — | not documented |

### `frequency_scale_factor`

**Role:** role not stated on the model

**Purpose:** Immutable registry row for one frequency scale factor definition.

| Column | Type | Nullable | Default | Foreign key | Enum values | Meaning |
|---|---|---|---|---|---|---|
| `id` | BIGINT | no | — | — | — | not documented |
| `level_of_theory_id` | BIGINT | no | — | level_of_theory.id | — | not documented |
| `software_release_id` | BIGINT | yes | — | software_release.id | — | not documented |
| `scale_kind` | FrequencyScaleKind (enum) | no | — | — | `fundamental`, `zpe`, `enthalpy`, `entropy`, `heat_capacity` | not documented |
| `value` | DOUBLE PRECISION | no | — | — | — | not documented |
| `source_literature_id` | BIGINT | yes | — | literature.id | — | not documented |
| `workflow_tool_release_id` | BIGINT | yes | — | workflow_tool_release.id | — | not documented |
| `note` | TEXT | yes | — | — | — | not documented |
| `created_at` | TIMESTAMP WITHOUT TIME ZONE | no | now() | — | — | not documented |
| `created_by` | BIGINT | yes | — | app_user.id | — | not documented |
| `public_ref` | VARCHAR(40) | no | — | — | — | not documented |

**Check constraints:**

- `ck_frequency_scale_factor_value_gt_0`: `value > 0`

### `geometry`

**Role:** role not stated on the model

**Purpose:** Stores a reusable molecular geometry and its serialized XYZ form.

| Column | Type | Nullable | Default | Foreign key | Enum values | Meaning |
|---|---|---|---|---|---|---|
| `id` | BIGINT | no | — | — | — | not documented |
| `natoms` | INTEGER | no | — | — | — | not documented |
| `geom_hash` | CHAR(64) | no | — | — | — | not documented |
| `xyz_text` | TEXT | yes | — | — | — | not documented |
| `created_at` | TIMESTAMP WITHOUT TIME ZONE | no | now() | — | — | not documented |
| `public_ref` | VARCHAR(40) | no | — | — | — | not documented |

**Check constraints:**

- `ck_geometry_natoms_ge_1`: `natoms >= 1`

### `geometry_atom`

**Role:** role not stated on the model

**Purpose:** Stores per-atom coordinates for a geometry row.

| Column | Type | Nullable | Default | Foreign key | Enum values | Meaning |
|---|---|---|---|---|---|---|
| `geometry_id` | BIGINT | no | — | geometry.id | — | not documented |
| `atom_index` | INTEGER | no | — | — | — | not documented |
| `element` | CHAR(2) | no | — | — | — | not documented |
| `x` | FLOAT | no | — | — | — | not documented |
| `y` | FLOAT | no | — | — | — | not documented |
| `z` | FLOAT | no | — | — | — | not documented |
| `isotope_mass_number` | SMALLINT | yes | — | — | — | not documented |

**Check constraints:**

- `ck_geometry_atom_atom_index_ge_1`: `atom_index >= 1`
- `ck_geometry_atom_element_canonical`: `btrim(element) ~ '^[A-Z][a-z]?$'`
- `ck_geometry_atom_isotope_mass_number_ge_1`: `isotope_mass_number IS NULL OR isotope_mass_number >= 1`

### `group_additivity_scheme`

**Role:** role not stated on the model

**Purpose:** Reusable description of a group-additivity library / estimator.

| Column | Type | Nullable | Default | Foreign key | Enum values | Meaning |
|---|---|---|---|---|---|---|
| `id` | BIGINT | no | — | — | — | not documented |
| `name` | TEXT | no | — | — | — | not documented |
| `version` | TEXT | yes | — | — | — | not documented |
| `description` | TEXT | yes | — | — | — | not documented |
| `source_literature_id` | BIGINT | yes | — | literature.id | — | not documented |
| `code_commit` | TEXT | yes | — | — | — | not documented |
| `note` | TEXT | yes | — | — | — | not documented |
| `created_at` | TIMESTAMP WITHOUT TIME ZONE | no | now() | — | — | not documented |
| `created_by` | BIGINT | yes | — | app_user.id | — | not documented |
| `public_ref` | VARCHAR(40) | no | — | — | — | not documented |

### `idempotency_record`

**Role:** role not stated on the model

**Purpose:** Server-side retry-safety record for a successful keyed write.

| Column | Type | Nullable | Default | Foreign key | Enum values | Meaning |
|---|---|---|---|---|---|---|
| `id` | BIGINT | no | — | — | — | not documented |
| `user_id` | BIGINT | no | — | app_user.id | — | not documented |
| `request_method` | VARCHAR(8) | no | — | — | — | not documented |
| `endpoint` | TEXT | no | — | — | — | not documented |
| `idempotency_key` | VARCHAR(200) | no | — | — | — | not documented |
| `payload_hash` | CHAR(64) | no | — | — | — | not documented |
| `status_code` | INTEGER | no | — | — | — | not documented |
| `response_body_json` | JSONB | no | — | — | — | not documented |
| `expires_at` | TIMESTAMP WITHOUT TIME ZONE | no | — | — | — | not documented |
| `created_at` | TIMESTAMP WITHOUT TIME ZONE | no | now() | — | — | not documented |

### `kinetics`

**Role:** role not stated on the model

**Purpose:** Kinetics records attached to a reaction entry.

| Column | Type | Nullable | Default | Foreign key | Enum values | Meaning |
|---|---|---|---|---|---|---|
| `id` | BIGINT | no | — | — | — | not documented |
| `reaction_entry_id` | BIGINT | no | — | reaction_entry.id | — | not documented |
| `scientific_origin` | ScientificOriginKind (enum) | no | — | — | `computed`, `experimental`, `estimated` | not documented |
| `model_kind` | KineticsModelKind (enum) | no | modified_arrhenius | — | `arrhenius`, `modified_arrhenius`, `multi_arrhenius`, `lindemann`, `troe`, `sri`, `plog`, `chebyshev` | not documented |
| `direction` | KineticsDirection (enum) | yes | — | — | `forward`, `reverse`, `net` | not documented |
| `is_third_body` | BOOLEAN | no | false | — | — | not documented |
| `literature_id` | BIGINT | yes | — | literature.id | — | not documented |
| `workflow_tool_release_id` | BIGINT | yes | — | workflow_tool_release.id | — | not documented |
| `software_release_id` | BIGINT | yes | — | software_release.id | — | not documented |
| `network_kinetics_id` | BIGINT | yes | — | network_kinetics.id | — | not documented |
| `a` | DOUBLE PRECISION | yes | — | — | — | not documented |
| `a_units` | ArrheniusAUnits (enum) | yes | — | — | `per_s`, `cm3_mol_s`, `cm3_molecule_s`, `m3_mol_s`, `cm6_mol2_s`, `cm6_molecule2_s`, `m6_mol2_s` | not documented |
| `n` | DOUBLE PRECISION | yes | — | — | — | not documented |
| `ea_kj_mol` | DOUBLE PRECISION | yes | — | — | — | not documented |
| `a_uncertainty` | DOUBLE PRECISION | yes | — | — | — | not documented |
| `a_uncertainty_kind` | KineticsUncertaintyKind (enum) | yes | — | — | `additive`, `multiplicative` | not documented |
| `n_uncertainty` | DOUBLE PRECISION | yes | — | — | — | not documented |
| `ea_uncertainty_kj_mol` | DOUBLE PRECISION | yes | — | — | — | not documented |
| `tmin_k` | DOUBLE PRECISION | yes | — | — | — | not documented |
| `tmax_k` | DOUBLE PRECISION | yes | — | — | — | not documented |
| `degeneracy` | DOUBLE PRECISION | yes | — | — | — | not documented |
| `degeneracy_convention` | KineticsDegeneracyConvention (enum) | no | unknown | — | `already_applied`, `not_applied`, `unknown` | not documented |
| `tunneling_model` | TunnelingModel (enum) | yes | — | — | `none`, `wigner`, `eckart`, `sct`, `other` | not documented |
| `pressure_context` | PressureContext (enum) | yes | — | — | `high_p_limit`, `apparent_at_pressure`, `pressure_dependent` | not documented |
| `pressure_bar` | DOUBLE PRECISION | yes | — | — | — | not documented |
| `note` | TEXT | yes | — | — | — | not documented |
| `created_at` | TIMESTAMP WITHOUT TIME ZONE | no | now() | — | — | not documented |
| `created_by` | BIGINT | yes | — | app_user.id | — | not documented |
| `public_ref` | VARCHAR(40) | no | — | — | — | not documented |

**Check constraints:**

- `ck_kinetics_a_uncertainty_kind_required_with_value`: `(a_uncertainty IS NULL) = (a_uncertainty_kind IS NULL)`
- `ck_kinetics_a_uncertainty_multiplicative_ge_1`: `a_uncertainty_kind <> 'multiplicative' OR a_uncertainty >= 1.0`
- `ck_kinetics_apparent_pressure_requires_pressure_bar`: `pressure_context <> 'apparent_at_pressure' OR pressure_bar IS NOT NULL`
- `ck_kinetics_degeneracy_finite_positive`: `degeneracy IS NULL OR (degeneracy > 0 AND degeneracy < 'Infinity'::double precision)`
- `ck_kinetics_pressure_bar_gt_0`: `pressure_bar IS NULL OR pressure_bar > 0`
- `ck_kinetics_tmax_k_gt_0`: `tmax_k IS NULL OR tmax_k > 0`
- `ck_kinetics_tmin_k_gt_0`: `tmin_k IS NULL OR tmin_k > 0`
- `ck_kinetics_tmin_le_tmax`: `tmin_k IS NULL OR tmax_k IS NULL OR tmin_k <= tmax_k`

### `kinetics_arrhenius_entry`

**Role:** role not stated on the model

**Purpose:** One modified-Arrhenius term of a sum-of-Arrhenius rate (DR-0036).

| Column | Type | Nullable | Default | Foreign key | Enum values | Meaning |
|---|---|---|---|---|---|---|
| `id` | BIGINT | no | — | — | — | not documented |
| `kinetics_id` | BIGINT | no | — | kinetics.id | — | not documented |
| `entry_index` | INTEGER | no | — | — | — | not documented |
| `a` | DOUBLE PRECISION | no | — | — | — | not documented |
| `a_units` | ArrheniusAUnits (enum) | yes | — | — | `per_s`, `cm3_mol_s`, `cm3_molecule_s`, `m3_mol_s`, `cm6_mol2_s`, `cm6_molecule2_s`, `m6_mol2_s` | not documented |
| `n` | DOUBLE PRECISION | yes | — | — | — | not documented |
| `ea_kj_mol` | DOUBLE PRECISION | yes | — | — | — | not documented |

**Check constraints:**

- `ck_kinetics_arrhenius_entry_arrhenius_entry_index_ge_1`: `entry_index >= 1`

### `kinetics_chebyshev`

**Role:** role not stated on the model

**Purpose:** Chebyshev-polynomial k(T,P) surface for a kinetics record.

| Column | Type | Nullable | Default | Foreign key | Enum values | Meaning |
|---|---|---|---|---|---|---|
| `kinetics_id` | BIGINT | no | — | kinetics.id | — | not documented |
| `n_temperature` | SMALLINT | no | — | — | — | not documented |
| `n_pressure` | SMALLINT | no | — | — | — | not documented |
| `tmin_k` | DOUBLE PRECISION | yes | — | — | — | not documented |
| `tmax_k` | DOUBLE PRECISION | yes | — | — | — | not documented |
| `pmin_bar` | DOUBLE PRECISION | yes | — | — | — | not documented |
| `pmax_bar` | DOUBLE PRECISION | yes | — | — | — | not documented |
| `coefficients` | JSONB | no | — | — | — | not documented |

**Check constraints:**

- `ck_kinetics_chebyshev_cheb_n_pressure_ge_1`: `n_pressure >= 1`
- `ck_kinetics_chebyshev_cheb_n_temperature_ge_1`: `n_temperature >= 1`

### `kinetics_falloff`

**Role:** role not stated on the model

**Purpose:** Pressure-dependent falloff parameters for a kinetics record (DR-0032).

| Column | Type | Nullable | Default | Foreign key | Enum values | Meaning |
|---|---|---|---|---|---|---|
| `kinetics_id` | BIGINT | no | — | kinetics.id | — | not documented |
| `low_a` | DOUBLE PRECISION | no | — | — | — | not documented |
| `low_a_units` | ArrheniusAUnits (enum) | yes | — | — | `per_s`, `cm3_mol_s`, `cm3_molecule_s`, `m3_mol_s`, `cm6_mol2_s`, `cm6_molecule2_s`, `m6_mol2_s` | not documented |
| `low_n` | DOUBLE PRECISION | yes | — | — | — | not documented |
| `low_ea_kj_mol` | DOUBLE PRECISION | yes | — | — | — | not documented |
| `troe_alpha` | DOUBLE PRECISION | yes | — | — | — | not documented |
| `troe_t3` | DOUBLE PRECISION | yes | — | — | — | not documented |
| `troe_t1` | DOUBLE PRECISION | yes | — | — | — | not documented |
| `troe_t2` | DOUBLE PRECISION | yes | — | — | — | not documented |
| `sri_a` | DOUBLE PRECISION | yes | — | — | — | not documented |
| `sri_b` | DOUBLE PRECISION | yes | — | — | — | not documented |
| `sri_c` | DOUBLE PRECISION | yes | — | — | — | not documented |
| `sri_d` | DOUBLE PRECISION | yes | — | — | — | not documented |
| `sri_e` | DOUBLE PRECISION | yes | — | — | — | not documented |
| `note` | TEXT | yes | — | — | — | not documented |

### `kinetics_interpretation_assignment`

**Role:** role not stated on the model

**Purpose:** Exact statistical-mechanics/conformer interpretation used for a rate role.

| Column | Type | Nullable | Default | Foreign key | Enum values | Meaning |
|---|---|---|---|---|---|---|
| `kinetics_id` | BIGINT | no | — | kinetics.id | — | not documented |
| `subject_key` | TEXT | no | — | — | — | not documented |
| `role` | TEXT | no | — | — | — | not documented |
| `statmech_id` | BIGINT | no | — | statmech.id | — | not documented |
| `conformer_selection_id` | BIGINT | yes | — | conformer_selection.id | — | not documented |
| `transition_state_entry_id` | BIGINT | yes | — | transition_state_entry.id | — | not documented |
| `ensemble_policy` | KineticsEnsemblePolicy (enum) | no | — | — | `single_structure`, `lowest_energy_conformer`, `boltzmann_weighted_conformers`, `multi_structural_torsional`, `other` | not documented |
| `standard_state_convention` | KineticsStandardStateConvention (enum) | no | — | — | `ideal_gas_1_bar`, `ideal_gas_1_atm`, `concentration_1_mol_cm3`, `concentration_1_mol_l`, `other` | not documented |
| `degeneracy_interpretation` | KineticsDegeneracyInterpretation (enum) | no | — | — | `external_symmetry_number`, `reaction_path_degeneracy`, `symmetry_number_and_path_degeneracy`, `no_symmetry_treatment`, `other` | not documented |
| `convention_note` | TEXT | yes | — | — | — | not documented |

**Check constraints:**

- `ck_kinetics_interpretation_assignment_other_note`: `(ensemble_policy <> 'other' AND standard_state_convention <> 'other' AND degeneracy_interpretation <> 'other') OR convention_note IS NOT NULL`
- `ck_kinetics_interpretation_assignment_subject_shape`: `(role = 'transition_state' AND subject_key = 'transition_state' AND transition_state_entry_id IS NOT NULL AND conformer_selection_id IS NULL) OR (role IN ('reactant', 'product') AND subject_key ~ ('^' || role || ':[0-9]+$') AND transition_state_entry_id IS NULL)`

### `kinetics_plog`

**Role:** role not stated on the model

**Purpose:** A single pressure entry of a PLOG (logarithmic-interpolation) rate.

| Column | Type | Nullable | Default | Foreign key | Enum values | Meaning |
|---|---|---|---|---|---|---|
| `id` | BIGINT | no | — | — | — | not documented |
| `kinetics_id` | BIGINT | no | — | kinetics.id | — | not documented |
| `entry_index` | INTEGER | no | — | — | — | not documented |
| `pressure_bar` | DOUBLE PRECISION | no | — | — | — | not documented |
| `a` | DOUBLE PRECISION | no | — | — | — | not documented |
| `a_units` | ArrheniusAUnits (enum) | yes | — | — | `per_s`, `cm3_mol_s`, `cm3_molecule_s`, `m3_mol_s`, `cm6_mol2_s`, `cm6_molecule2_s`, `m6_mol2_s` | not documented |
| `n` | DOUBLE PRECISION | yes | — | — | — | not documented |
| `ea_kj_mol` | DOUBLE PRECISION | yes | — | — | — | not documented |

**Check constraints:**

- `ck_kinetics_plog_plog_entry_index_ge_1`: `entry_index >= 1`
- `ck_kinetics_plog_plog_pressure_bar_gt_0`: `pressure_bar > 0`

### `kinetics_source_calculation`

**Role:** role not stated on the model

**Purpose:** Links kinetics records to supporting calculations by role.

| Column | Type | Nullable | Default | Foreign key | Enum values | Meaning |
|---|---|---|---|---|---|---|
| `kinetics_id` | BIGINT | no | — | kinetics.id | — | not documented |
| `calculation_id` | BIGINT | no | — | calculation.id | — | not documented |
| `role` | KineticsCalculationRole (enum) | no | — | — | `reactant_energy`, `product_energy`, `ts_energy`, `freq`, `irc`, `master_equation`, `fit_source` | not documented |

### `kinetics_tunneling_application`

**Role:** role not stated on the model

**Purpose:** Typed reproducibility evidence for a tunneling correction applied to a rate.

| Column | Type | Nullable | Default | Foreign key | Enum values | Meaning |
|---|---|---|---|---|---|---|
| `kinetics_id` | BIGINT | no | — | kinetics.id | — | not documented |
| `model` | TEXT | no | — | — | — | not documented |
| `model_identifier` | TEXT | yes | — | — | — | not documented |
| `transition_state_entry_id` | BIGINT | no | — | transition_state_entry.id | — | not documented |
| `source_calculation_id` | BIGINT | yes | — | calculation.id | — | not documented |
| `imaginary_frequency_cm1` | DOUBLE PRECISION | yes | — | — | — | not documented |
| `frequency_sign_convention` | TEXT | no | negative_imaginary_cm1 | — | — | not documented |
| `reactant_energy_kj_mol` | DOUBLE PRECISION | yes | — | — | — | not documented |
| `product_energy_kj_mol` | DOUBLE PRECISION | yes | — | — | — | not documented |
| `forward_barrier_kj_mol` | DOUBLE PRECISION | yes | — | — | — | not documented |
| `reverse_barrier_kj_mol` | DOUBLE PRECISION | yes | — | — | — | not documented |
| `energy_zero_convention` | EnergyZeroConvention (enum) | yes | — | — | `lowest_state`, `entrance_channel`, `separated_reactants`, `absolute`, `other` | not documented |
| `energy_correction_convention` | EnergyCorrectionConvention (enum) | yes | — | — | `electronic_only`, `electronic_plus_zpe`, `atom_and_bond_corrected`, `thermal_enthalpy_298k`, `other` | not documented |
| `convention_note` | TEXT | yes | — | — | — | not documented |
| `result_artifact_id` | BIGINT | yes | — | calculation_artifact.id | — | not documented |
| `sct_path_integral_artifact_id` | BIGINT | yes | — | calculation_artifact.id | — | not documented |

**Check constraints:**

- `ck_kinetics_tunneling_application_imaginary_negative`: `imaginary_frequency_cm1 IS NULL OR imaginary_frequency_cm1 < 0`
- `ck_kinetics_tunneling_application_model_enum`: `model IN ('none', 'wigner', 'eckart', 'sct', 'other')`
- `ck_kinetics_tunneling_application_other_note`: `(energy_zero_convention IS DISTINCT FROM 'other' AND energy_correction_convention IS DISTINCT FROM 'other') OR convention_note IS NOT NULL`
- `ck_kinetics_tunneling_application_other_replayable`: `model <> 'other' OR (model_identifier IS NOT NULL AND result_artifact_id IS NOT NULL)`

### `literature`

**Role:** role not stated on the model

**Purpose:** not documented

| Column | Type | Nullable | Default | Foreign key | Enum values | Meaning |
|---|---|---|---|---|---|---|
| `id` | BIGINT | no | — | — | — | not documented |
| `kind` | LiteratureKind (enum) | no | — | — | `article`, `book`, `thesis`, `report`, `dataset`, `webpage` | not documented |
| `title` | TEXT | no | — | — | — | not documented |
| `journal` | TEXT | yes | — | — | — | not documented |
| `year` | INTEGER | yes | — | — | — | not documented |
| `volume` | TEXT | yes | — | — | — | not documented |
| `issue` | TEXT | yes | — | — | — | not documented |
| `pages` | TEXT | yes | — | — | — | not documented |
| `doi` | TEXT | yes | — | — | — | not documented |
| `isbn` | TEXT | yes | — | — | — | not documented |
| `url` | TEXT | yes | — | — | — | not documented |
| `publisher` | TEXT | yes | — | — | — | not documented |
| `institution` | TEXT | yes | — | — | — | not documented |
| `created_at` | TIMESTAMP WITHOUT TIME ZONE | no | now() | — | — | not documented |
| `public_ref` | VARCHAR(40) | no | — | — | — | not documented |

### `literature_author`

**Role:** role not stated on the model

**Purpose:** not documented

| Column | Type | Nullable | Default | Foreign key | Enum values | Meaning |
|---|---|---|---|---|---|---|
| `literature_id` | BIGINT | no | — | literature.id | — | not documented |
| `author_id` | BIGINT | no | — | author.id | — | not documented |
| `author_order` | INTEGER | no | — | — | — | not documented |

### `machine_review_curator_task`

**Role:** role not stated on the model

**Purpose:** One human to-do about one finding on one record in one submission.

| Column | Type | Nullable | Default | Foreign key | Enum values | Meaning |
|---|---|---|---|---|---|---|
| `id` | BIGINT | no | — | — | — | not documented |
| `submission_id` | BIGINT | no | — | submission.id | — | not documented |
| `record_type` | SubmissionRecordType (enum) | no | — | — | `species`, `species_entry`, `conformer_group`, `conformer_observation`, `reaction`, `reaction_entry`, `transition_state`, `transition_state_entry`, `calculation`, `statmech`, `thermo`, `kinetics`, `transport`, `network`, `network_solve`, `applied_energy_correction`, `artifact`, `molecular_property_observation` | ``record_type`` + ``record_id`` use the same generic record-addressing as :class:`~app.db.models.submission.SubmissionRecordLink` and :class:`~app.db.models.record_review.RecordReview`. ``record_id`` is the raw internal id; acceptable because this table is admin/curator-private and is never serialised onto a public response (spec §3). |
| `record_id` | BIGINT | no | — | — | — | ``record_type`` + ``record_id`` use the same generic record-addressing as :class:`~app.db.models.submission.SubmissionRecordLink` and :class:`~app.db.models.record_review.RecordReview`. ``record_id`` is the raw internal id; acceptable because this table is admin/curator-private and is never serialised onto a public response (spec §3). |
| `finding_fingerprint` | VARCHAR(64) | no | — | — | — | not documented |
| `workflow_state` | MachineReviewCuratorTaskState (enum) | no | untriaged | — | `untriaged`, `needs_curator_review`, `in_curator_review`, `resolved_no_action`, `resolved_human_reviewed`, `dismissed_machine_finding` | not documented |
| `machine_review_status` | MachineReviewStatus (enum) | no | — | — | `not_run`, `machine_screened_pass`, `machine_screened_warning`, `machine_screened_needs_attention`, `machine_review_failed` | ``machine_review_status`` / ``highest_severity`` / ``findings_count`` are a **denormalised advisory snapshot** for cheap queue ranking/filtering — never authoritative (spec §3). |
| `highest_severity` | MachineReviewSeverity (enum) | no | — | — | `info`, `warning`, `critical` | ``machine_review_status`` / ``highest_severity`` / ``findings_count`` are a **denormalised advisory snapshot** for cheap queue ranking/filtering — never authoritative (spec §3). |
| `findings_count` | INTEGER | no | 1 | — | — | ``machine_review_status`` / ``highest_severity`` / ``findings_count`` are a **denormalised advisory snapshot** for cheap queue ranking/filtering — never authoritative (spec §3). |
| `source_audit_event_id` | BIGINT | yes | — | submission_audit_event.id | — | not documented |
| `assigned_to` | BIGINT | yes | — | app_user.id | — | not documented |
| `updated_at` | TIMESTAMP WITHOUT TIME ZONE | no | now() | — | — | not documented |
| `resolved_at` | TIMESTAMP WITHOUT TIME ZONE | yes | — | — | — | not documented |
| `resolved_by` | BIGINT | yes | — | app_user.id | — | not documented |
| `resolution_note` | TEXT | yes | — | — | — | not documented |
| `created_at` | TIMESTAMP WITHOUT TIME ZONE | no | now() | — | — | not documented |

**Check constraints:**

- `ck_machine_review_curator_task_findings_count_positive`: `findings_count >= 1`
- `ck_machine_review_curator_task_resolution_consistency`: `(workflow_state IN ('resolved_no_action', 'resolved_human_reviewed', 'dismissed_machine_finding')) = (resolved_at IS NOT NULL AND resolved_by IS NOT NULL AND resolution_note IS NOT NULL)`

### `network`

**Role:** role not stated on the model

**Purpose:** Reaction network metadata.

| Column | Type | Nullable | Default | Foreign key | Enum values | Meaning |
|---|---|---|---|---|---|---|
| `id` | BIGINT | no | — | — | — | not documented |
| `name` | TEXT | yes | — | — | — | not documented |
| `description` | TEXT | yes | — | — | — | not documented |
| `literature_id` | BIGINT | yes | — | literature.id | — | not documented |
| `software_release_id` | BIGINT | yes | — | software_release.id | — | not documented |
| `workflow_tool_release_id` | BIGINT | yes | — | workflow_tool_release.id | — | not documented |
| `created_at` | TIMESTAMP WITHOUT TIME ZONE | no | now() | — | — | not documented |
| `created_by` | BIGINT | yes | — | app_user.id | — | not documented |
| `public_ref` | VARCHAR(40) | no | — | — | — | not documented |

### `network_channel`

**Role:** role not stated on the model

**Purpose:** A directed phenomenological channel between two network states.

| Column | Type | Nullable | Default | Foreign key | Enum values | Meaning |
|---|---|---|---|---|---|---|
| `id` | BIGINT | no | — | — | — | not documented |
| `network_id` | BIGINT | no | — | network.id | — | not documented |
| `source_state_id` | BIGINT | no | — | network_state.id | — | not documented |
| `sink_state_id` | BIGINT | no | — | network_state.id | — | not documented |
| `kind` | NetworkChannelKind (enum) | no | — | — | `isomerization`, `association`, `dissociation`, `stabilization`, `exchange` | not documented |
| `mechanism` | NetworkChannelMechanism (enum) | no | elementary | — | `elementary`, `well_skipping` | not documented |
| `channel_key` | TEXT | no | — | — | — | not documented |

**Check constraints:**

- `ck_network_channel_source_ne_sink`: `source_state_id <> sink_state_id`

### `network_kinetics`

**Role:** role not stated on the model

**Purpose:** One fitted phenomenological k(T,P) for a channel from a specific solve.

| Column | Type | Nullable | Default | Foreign key | Enum values | Meaning |
|---|---|---|---|---|---|---|
| `id` | BIGINT | no | — | — | — | not documented |
| `channel_id` | BIGINT | no | — | network_channel.id | — | not documented |
| `solve_id` | BIGINT | no | — | network_solve.id | — | not documented |
| `model_kind` | NetworkKineticsModelKind (enum) | no | — | — | `chebyshev`, `plog`, `tabulated` | not documented |
| `tmin_k` | DOUBLE PRECISION | yes | — | — | — | not documented |
| `tmax_k` | DOUBLE PRECISION | yes | — | — | — | not documented |
| `pmin_bar` | DOUBLE PRECISION | yes | — | — | — | not documented |
| `pmax_bar` | DOUBLE PRECISION | yes | — | — | — | not documented |
| `rate_units` | ArrheniusAUnits (enum) | yes | — | — | `per_s`, `cm3_mol_s`, `cm3_molecule_s`, `m3_mol_s`, `cm6_mol2_s`, `cm6_molecule2_s`, `m6_mol2_s` | not documented |
| `pressure_units` | PressureUnit (enum) | yes | — | — | `bar`, `atm` | not documented |
| `temperature_units` | TemperatureUnit (enum) | yes | — | — | `kelvin` | not documented |
| `stores_log10_k` | BOOLEAN | yes | — | — | — | not documented |
| `note` | TEXT | yes | — | — | — | not documented |
| `created_at` | TIMESTAMP WITHOUT TIME ZONE | no | now() | — | — | not documented |
| `public_ref` | VARCHAR(40) | no | — | — | — | not documented |

**Check constraints:**

- `ck_network_kinetics_pmax_bar_gt_0`: `pmax_bar IS NULL OR pmax_bar > 0`
- `ck_network_kinetics_pmin_bar_gt_0`: `pmin_bar IS NULL OR pmin_bar > 0`
- `ck_network_kinetics_pmin_le_pmax`: `pmin_bar IS NULL OR pmax_bar IS NULL OR pmin_bar <= pmax_bar`
- `ck_network_kinetics_tmax_k_gt_0`: `tmax_k IS NULL OR tmax_k > 0`
- `ck_network_kinetics_tmin_k_gt_0`: `tmin_k IS NULL OR tmin_k > 0`
- `ck_network_kinetics_tmin_le_tmax`: `tmin_k IS NULL OR tmax_k IS NULL OR tmin_k <= tmax_k`

### `network_kinetics_chebyshev`

**Role:** role not stated on the model

**Purpose:** Chebyshev polynomial coefficients for a network kinetics record.

| Column | Type | Nullable | Default | Foreign key | Enum values | Meaning |
|---|---|---|---|---|---|---|
| `network_kinetics_id` | BIGINT | no | — | network_kinetics.id | — | not documented |
| `n_temperature` | SMALLINT | no | — | — | — | not documented |
| `n_pressure` | SMALLINT | no | — | — | — | not documented |
| `coefficients` | JSONB | no | — | — | — | not documented |

**Check constraints:**

- `ck_network_kinetics_chebyshev_n_pressure_ge_1`: `n_pressure >= 1`
- `ck_network_kinetics_chebyshev_n_temperature_ge_1`: `n_temperature >= 1`

### `network_kinetics_plog`

**Role:** role not stated on the model

**Purpose:** One PLOG entry: Arrhenius parameters at a discrete pressure.

| Column | Type | Nullable | Default | Foreign key | Enum values | Meaning |
|---|---|---|---|---|---|---|
| `network_kinetics_id` | BIGINT | no | — | network_kinetics.id | — | not documented |
| `pressure_bar` | DOUBLE PRECISION | no | — | — | — | not documented |
| `entry_index` | SMALLINT | no | 1 | — | — | not documented |
| `a` | DOUBLE PRECISION | no | — | — | — | not documented |
| `a_units` | ArrheniusAUnits (enum) | yes | — | — | `per_s`, `cm3_mol_s`, `cm3_molecule_s`, `m3_mol_s`, `cm6_mol2_s`, `cm6_molecule2_s`, `m6_mol2_s` | not documented |
| `n` | DOUBLE PRECISION | no | — | — | — | not documented |
| `ea_kj_mol` | DOUBLE PRECISION | no | — | — | — | not documented |

**Check constraints:**

- `ck_network_kinetics_plog_entry_index_ge_1`: `entry_index >= 1`
- `ck_network_kinetics_plog_pressure_bar_gt_0`: `pressure_bar > 0`

### `network_kinetics_point`

**Role:** role not stated on the model

**Purpose:** One tabulated k(T,P) data point.

| Column | Type | Nullable | Default | Foreign key | Enum values | Meaning |
|---|---|---|---|---|---|---|
| `network_kinetics_id` | BIGINT | no | — | network_kinetics.id | — | not documented |
| `temperature_k` | DOUBLE PRECISION | no | — | — | — | not documented |
| `pressure_bar` | DOUBLE PRECISION | no | — | — | — | not documented |
| `rate_value` | DOUBLE PRECISION | no | — | — | — | not documented |

**Check constraints:**

- `ck_network_kinetics_point_pressure_bar_gt_0`: `pressure_bar > 0`
- `ck_network_kinetics_point_temperature_k_gt_0`: `temperature_k > 0`

### `network_reaction`

**Role:** role not stated on the model

**Purpose:** Links a network to a reaction entry.

| Column | Type | Nullable | Default | Foreign key | Enum values | Meaning |
|---|---|---|---|---|---|---|
| `network_id` | BIGINT | no | — | network.id | — | not documented |
| `reaction_entry_id` | BIGINT | no | — | reaction_entry.id | — | not documented |

### `network_solve_bath_gas`

**Role:** role not stated on the model

**Purpose:** Bath gas composition for one network solve.

| Column | Type | Nullable | Default | Foreign key | Enum values | Meaning |
|---|---|---|---|---|---|---|
| `solve_id` | BIGINT | no | — | network_solve.id | — | not documented |
| `species_entry_id` | BIGINT | no | — | species_entry.id | — | not documented |
| `mole_fraction` | DOUBLE PRECISION | no | — | — | — | not documented |

**Check constraints:**

- `ck_network_solve_bath_gas_mole_fraction_range`: `mole_fraction > 0 AND mole_fraction <= 1`

### `network_solve_channel_barrier`

**Role:** role not stated on the model

**Purpose:** Solve input barrier for one explicitly identified microscopic path.

| Column | Type | Nullable | Default | Foreign key | Enum values | Meaning |
|---|---|---|---|---|---|---|
| `solve_id` | BIGINT | no | — | network_solve.id | — | not documented |
| `channel_id` | BIGINT | no | — | network_channel.id | — | not documented |
| `reaction_entry_id` | BIGINT | no | — | reaction_entry.id | — | not documented |
| `transition_state_entry_id` | BIGINT | no | — | transition_state_entry.id | — | not documented |
| `forward_barrier_kj_mol` | DOUBLE PRECISION | no | — | — | — | not documented |
| `reverse_barrier_kj_mol` | DOUBLE PRECISION | no | — | — | — | not documented |
| `energy_zero_convention` | EnergyZeroConvention (enum) | no | — | — | `lowest_state`, `entrance_channel`, `separated_reactants`, `absolute`, `other` | not documented |
| `correction_convention` | EnergyCorrectionConvention (enum) | no | — | — | `electronic_only`, `electronic_plus_zpe`, `atom_and_bond_corrected`, `thermal_enthalpy_298k`, `other` | not documented |
| `convention_note` | TEXT | yes | — | — | — | not documented |
| `source_calculation_id` | BIGINT | yes | — | calculation.id | — | not documented |

**Check constraints:**

- `ck_network_solve_channel_barrier_barriers_finite`: `forward_barrier_kj_mol <> 'NaN'::double precision AND reverse_barrier_kj_mol <> 'NaN'::double precision`
- `ck_network_solve_channel_barrier_other_note`: `(energy_zero_convention <> 'other' AND correction_convention <> 'other') OR convention_note IS NOT NULL`

### `network_solve_energy_transfer`

**Role:** role not stated on the model

**Purpose:** Energy transfer model parameters for one network solve.

| Column | Type | Nullable | Default | Foreign key | Enum values | Meaning |
|---|---|---|---|---|---|---|
| `id` | BIGINT | no | — | — | — | not documented |
| `solve_id` | BIGINT | no | — | network_solve.id | — | not documented |
| `scope` | NetworkEnergyTransferScope (enum) | no | per_well | — | `per_well`, `network_wide` | not documented |
| `state_id` | BIGINT | yes | — | network_state.id | — | not documented |
| `collider_species_entry_id` | BIGINT | yes | — | species_entry.id | — | not documented |
| `model` | TEXT | yes | — | — | — | not documented |
| `alpha0_cm_inv` | DOUBLE PRECISION | yes | — | — | — | not documented |
| `t_exponent` | DOUBLE PRECISION | yes | — | — | — | not documented |
| `t_ref_k` | DOUBLE PRECISION | yes | — | — | — | not documented |
| `note` | TEXT | yes | — | — | — | not documented |

**Check constraints:**

- `ck_network_solve_energy_transfer_scope_columns_agree`: `(scope = 'per_well' AND state_id IS NOT NULL AND collider_species_entry_id IS NOT NULL) OR (scope = 'network_wide' AND state_id IS NULL AND collider_species_entry_id IS NULL)`

### `network_solve_source_calculation`

**Role:** role not stated on the model

**Purpose:** Links a network solve to supporting calculations by role.

| Column | Type | Nullable | Default | Foreign key | Enum values | Meaning |
|---|---|---|---|---|---|---|
| `solve_id` | BIGINT | no | — | network_solve.id | — | not documented |
| `calculation_id` | BIGINT | no | — | calculation.id | — | not documented |
| `role` | NetworkSolveCalculationRole (enum) | no | — | — | `well_energy`, `barrier_energy`, `well_freq`, `barrier_freq`, `master_equation_run`, `fit_source` | not documented |

### `network_solve_state_energy`

**Role:** role not stated on the model

**Purpose:** Solve-specific state energy with an explicit zero/correction convention.

| Column | Type | Nullable | Default | Foreign key | Enum values | Meaning |
|---|---|---|---|---|---|---|
| `solve_id` | BIGINT | no | — | network_solve.id | — | not documented |
| `state_id` | BIGINT | no | — | network_state.id | — | not documented |
| `energy_kj_mol` | DOUBLE PRECISION | no | — | — | — | not documented |
| `energy_zero_convention` | EnergyZeroConvention (enum) | no | — | — | `lowest_state`, `entrance_channel`, `separated_reactants`, `absolute`, `other` | not documented |
| `correction_convention` | EnergyCorrectionConvention (enum) | no | — | — | `electronic_only`, `electronic_plus_zpe`, `atom_and_bond_corrected`, `thermal_enthalpy_298k`, `other` | not documented |
| `convention_note` | TEXT | yes | — | — | — | not documented |
| `source_calculation_id` | BIGINT | yes | — | calculation.id | — | not documented |

**Check constraints:**

- `ck_network_solve_state_energy_other_note`: `(energy_zero_convention <> 'other' AND correction_convention <> 'other') OR convention_note IS NOT NULL`

### `network_species`

**Role:** role not stated on the model

**Purpose:** Links a network to a species entry with a role.

| Column | Type | Nullable | Default | Foreign key | Enum values | Meaning |
|---|---|---|---|---|---|---|
| `network_id` | BIGINT | no | — | network.id | — | not documented |
| `species_entry_id` | BIGINT | no | — | species_entry.id | — | not documented |
| `role` | NetworkSpeciesRole (enum) | no | — | — | `well`, `reactant`, `product`, `bath_gas` | not documented |

### `network_state`

**Role:** role not stated on the model

**Purpose:** A macroscopic state in a reaction network (well or bimolecular channel).

| Column | Type | Nullable | Default | Foreign key | Enum values | Meaning |
|---|---|---|---|---|---|---|
| `id` | BIGINT | no | — | — | — | not documented |
| `network_id` | BIGINT | no | — | network.id | — | not documented |
| `kind` | NetworkStateKind (enum) | no | — | — | `well`, `bimolecular`, `termolecular` | not documented |
| `composition_hash` | CHAR(64) | no | — | — | — | not documented |
| `label` | TEXT | yes | — | — | — | not documented |

### `network_state_participant`

**Role:** role not stated on the model

**Purpose:** Species composition of a network state.

| Column | Type | Nullable | Default | Foreign key | Enum values | Meaning |
|---|---|---|---|---|---|---|
| `state_id` | BIGINT | no | — | network_state.id | — | not documented |
| `species_entry_id` | BIGINT | no | — | species_entry.id | — | not documented |
| `stoichiometry` | SMALLINT | no | 1 | — | — | not documented |

**Check constraints:**

- `ck_network_state_participant_stoichiometry_ge_1`: `stoichiometry >= 1`

### `reaction_atom_map`

**Role:** role not stated on the model

**Purpose:** One depositor-supplied atom correspondence for one micro reaction.

| Column | Type | Nullable | Default | Foreign key | Enum values | Meaning |
|---|---|---|---|---|---|---|
| `id` | BIGINT | no | — | — | — | not documented |
| `reaction_entry_id` | BIGINT | no | — | reaction_entry.id | — | not documented |
| `transition_state_entry_id` | BIGINT | no | — | transition_state_entry.id | — | not documented |
| `transition_state_geometry_id` | BIGINT | no | — | geometry.id | — | not documented |
| `source` | AtomMapSource (enum) | no | — | — | `declared`, `inferred` | not documented |
| `equivalent_map_count` | INTEGER | yes | — | — | — | not documented |
| `note` | TEXT | yes | — | — | — | not documented |
| `created_at` | TIMESTAMP WITHOUT TIME ZONE | no | now() | — | — | not documented |
| `created_by` | BIGINT | yes | — | app_user.id | — | not documented |

**Check constraints:**

- `ck_reaction_atom_map_equivalent_map_count_ge_1`: `equivalent_map_count IS NULL OR equivalent_map_count >= 1`
- `ck_reaction_atom_map_inferred_requires_note`: `source <> 'inferred' OR (note IS NOT NULL AND btrim(note) <> '')`

### `reaction_atom_map_pair`

**Role:** role not stated on the model

**Purpose:** One atom of one participant identified with one transition-state atom.

| Column | Type | Nullable | Default | Foreign key | Enum values | Meaning |
|---|---|---|---|---|---|---|
| `id` | BIGINT | no | — | — | — | not documented |
| `atom_map_id` | BIGINT | no | — | reaction_atom_map.id | — | not documented |
| `side` | ReactionRole (enum) | no | — | reaction_entry_structure_participant.role | `reactant`, `product` | not documented |
| `structure_participant_id` | BIGINT | no | — | reaction_entry_structure_participant.id | — | not documented |
| `geometry_id` | BIGINT | no | — | geometry_atom.geometry_id | — | not documented |
| `atom_index` | INTEGER | no | — | geometry_atom.atom_index | — | not documented |
| `transition_state_geometry_id` | BIGINT | no | — | geometry_atom.geometry_id | — | not documented |
| `ts_atom_index` | INTEGER | no | — | geometry_atom.atom_index | — | not documented |
| `element` | CHAR(2) | no | — | geometry_atom.element | — | not documented |
| `ts_element` | CHAR(2) | no | — | geometry_atom.element | — | not documented |

**Check constraints:**

- `ck_reaction_atom_map_pair_atom_index_ge_1`: `atom_index >= 1`
- `ck_reaction_atom_map_pair_element_canonical`: `btrim(element) ~ '^[A-Z][a-z]?$'`
- `ck_reaction_atom_map_pair_element_matches`: `element = ts_element`
- `ck_reaction_atom_map_pair_ts_atom_index_ge_1`: `ts_atom_index >= 1`
- `ck_reaction_atom_map_pair_ts_element_canonical`: `btrim(ts_element) ~ '^[A-Z][a-z]?$'`

### `reaction_entry`

**Role:** role not stated on the model

**Purpose:** not documented

| Column | Type | Nullable | Default | Foreign key | Enum values | Meaning |
|---|---|---|---|---|---|---|
| `id` | BIGINT | no | — | — | — | not documented |
| `reaction_id` | BIGINT | no | — | chem_reaction.id | — | not documented |
| `created_at` | TIMESTAMP WITHOUT TIME ZONE | no | now() | — | — | not documented |
| `created_by` | BIGINT | yes | — | app_user.id | — | not documented |
| `public_ref` | VARCHAR(40) | no | — | — | — | not documented |

### `reaction_entry_structure_participant`

**Role:** role not stated on the model

**Purpose:** not documented

| Column | Type | Nullable | Default | Foreign key | Enum values | Meaning |
|---|---|---|---|---|---|---|
| `id` | BIGINT | no | — | — | — | not documented |
| `reaction_entry_id` | BIGINT | no | — | reaction_entry.id | — | not documented |
| `species_entry_id` | BIGINT | no | — | species_entry.id | — | not documented |
| `role` | ReactionRole (enum) | no | — | — | `reactant`, `product` | not documented |
| `participant_index` | INTEGER | no | — | — | — | not documented |
| `note` | TEXT | yes | — | — | — | not documented |
| `created_at` | TIMESTAMP WITHOUT TIME ZONE | no | now() | — | — | not documented |
| `created_by` | BIGINT | yes | — | app_user.id | — | not documented |

**Check constraints:**

- `ck_reaction_entry_structure_participant_participant_index_ge_1`: `participant_index >= 1`

### `reaction_family`

**Role:** role not stated on the model

**Purpose:** not documented

| Column | Type | Nullable | Default | Foreign key | Enum values | Meaning |
|---|---|---|---|---|---|---|
| `id` | BIGINT | no | — | — | — | not documented |
| `name` | TEXT | no | — | — | — | not documented |
| `created_at` | TIMESTAMP WITHOUT TIME ZONE | no | now() | — | — | not documented |

### `record_machine_review`

**Role:** role not stated on the model

**Purpose:** One persisted machine-review pass over one scientific record (append-only).

| Column | Type | Nullable | Default | Foreign key | Enum values | Meaning |
|---|---|---|---|---|---|---|
| `id` | BIGINT | no | — | — | — | not documented |
| `record_type` | SubmissionRecordType (enum) | no | — | — | `species`, `species_entry`, `conformer_group`, `conformer_observation`, `reaction`, `reaction_entry`, `transition_state`, `transition_state_entry`, `calculation`, `statmech`, `thermo`, `kinetics`, `transport`, `network`, `network_solve`, `applied_energy_correction`, `artifact`, `molecular_property_observation` | not documented |
| `record_id` | BIGINT | no | — | — | — | not documented |
| `status` | MachineReviewStatus (enum) | no | — | — | `not_run`, `machine_screened_pass`, `machine_screened_warning`, `machine_screened_needs_attention`, `machine_review_failed` | not documented |
| `curator_priority` | VARCHAR(16) | yes | — | — | — | not documented |
| `summary` | TEXT | yes | — | — | — | not documented |
| `findings_json` | JSONB | no | []'::jsonb | — | — | not documented |
| `model` | VARCHAR(128) | yes | — | — | — | not documented |
| `provider` | VARCHAR(128) | yes | — | — | — | not documented |
| `context_hash` | VARCHAR(64) | no | — | — | — | not documented |
| `context_schema_version` | VARCHAR(32) | no | — | — | — | not documented |
| `prompt_version` | VARCHAR(64) | no | — | — | — | not documented |
| `rubric_versions_json` | JSONB | no | — | — | — | not documented |
| `source_submission_id` | BIGINT | yes | — | submission.id | — | not documented |
| `source_audit_event_id` | BIGINT | yes | — | submission_audit_event.id | — | not documented |
| `reviewed_at` | TIMESTAMP WITHOUT TIME ZONE | no | — | — | — | not documented |
| `created_at` | TIMESTAMP WITHOUT TIME ZONE | no | now() | — | — | not documented |

**Check constraints:**

- `ck_record_machine_review_context_hash_len`: `char_length(context_hash) = 64`
- `ck_record_machine_review_findings_json_is_array`: `jsonb_typeof(findings_json) = 'array'`

### `record_reproducibility_assessment`

**Role:** role not stated on the model

**Purpose:** One immutable, versioned assessor claim about reproducibility.

| Column | Type | Nullable | Default | Foreign key | Enum values | Meaning |
|---|---|---|---|---|---|---|
| `id` | BIGINT | no | — | — | — | not documented |
| `record_type` | SubmissionRecordType (enum) | no | — | — | `species`, `species_entry`, `conformer_group`, `conformer_observation`, `reaction`, `reaction_entry`, `transition_state`, `transition_state_entry`, `calculation`, `statmech`, `thermo`, `kinetics`, `transport`, `network`, `network_solve`, `applied_energy_correction`, `artifact`, `molecular_property_observation` | not documented |
| `record_id` | BIGINT | no | — | — | — | not documented |
| `grade` | ReproducibilityGrade (enum) | no | — | — | `insufficient`, `described`, `auditable`, `rerunnable` | not documented |
| `rubric_name` | VARCHAR(128) | no | — | — | — | not documented |
| `rubric_version` | VARCHAR(64) | no | — | — | — | not documented |
| `context_hash` | VARCHAR(64) | no | — | — | — | not documented |
| `context_json` | JSONB | no | — | — | — | not documented |
| `passed_json` | JSONB | no | []'::jsonb | — | — | not documented |
| `missing_json` | JSONB | no | []'::jsonb | — | — | not documented |
| `warnings_json` | JSONB | no | []'::jsonb | — | — | not documented |
| `assessor_kind` | ReproducibilityAssessorKind (enum) | no | — | — | `system`, `curator` | not documented |
| `assessor_user_id` | BIGINT | yes | — | app_user.id | — | not documented |
| `source_submission_id` | BIGINT | yes | — | submission.id | — | not documented |
| `assessed_at` | TIMESTAMP WITHOUT TIME ZONE | no | now() | — | — | not documented |
| `created_at` | TIMESTAMP WITHOUT TIME ZONE | no | now() | — | — | not documented |
| `public_ref` | VARCHAR(40) | no | — | — | — | not documented |

**Check constraints:**

- `ck_record_reproducibility_assessment_assessor_user_consistent`: `(assessor_kind = 'system' AND assessor_user_id IS NULL) OR (assessor_kind = 'curator' AND assessor_user_id IS NOT NULL)`
- `ck_record_reproducibility_assessment_context_hash_lower_hex`: `context_hash ~ '^[0-9a-f]{64}$'`
- `ck_record_reproducibility_assessment_context_json_is_object`: `jsonb_typeof(context_json) = 'object'`
- `ck_record_reproducibility_assessment_missing_json_is_array`: `jsonb_typeof(missing_json) = 'array'`
- `ck_record_reproducibility_assessment_passed_json_is_array`: `jsonb_typeof(passed_json) = 'array'`
- `ck_record_reproducibility_assessment_warnings_json_is_array`: `jsonb_typeof(warnings_json) = 'array'`

### `record_review`

**Role:** role not stated on the model

**Purpose:** Current review/trust state for one scientific record.

| Column | Type | Nullable | Default | Foreign key | Enum values | Meaning |
|---|---|---|---|---|---|---|
| `id` | BIGINT | no | — | — | — | not documented |
| `record_type` | SubmissionRecordType (enum) | no | — | — | `species`, `species_entry`, `conformer_group`, `conformer_observation`, `reaction`, `reaction_entry`, `transition_state`, `transition_state_entry`, `calculation`, `statmech`, `thermo`, `kinetics`, `transport`, `network`, `network_solve`, `applied_energy_correction`, `artifact`, `molecular_property_observation` | not documented |
| `record_id` | BIGINT | no | — | — | — | not documented |
| `status` | RecordReviewStatus (enum) | no | not_reviewed | — | `not_reviewed`, `under_review`, `approved`, `rejected`, `deprecated` | not documented |
| `submission_id` | BIGINT | yes | — | submission.id | — | not documented |
| `reviewed_by` | BIGINT | yes | — | app_user.id | — | not documented |
| `reviewed_at` | TIMESTAMP WITHOUT TIME ZONE | yes | — | — | — | not documented |
| `first_approved_at` | TIMESTAMP WITHOUT TIME ZONE | yes | — | — | — | not documented |
| `note` | TEXT | yes | — | — | — | not documented |
| `created_at` | TIMESTAMP WITHOUT TIME ZONE | no | now() | — | — | not documented |
| `created_by` | BIGINT | yes | — | app_user.id | — | not documented |

**Check constraints:**

- `ck_record_review_record_review_approved_has_first_approval`: `status <> 'approved' OR first_approved_at IS NOT NULL`
- `ck_record_review_record_review_terminal_requires_reviewer`: `(status NOT IN ('approved', 'rejected', 'deprecated')) OR (reviewed_by IS NOT NULL AND reviewed_at IS NOT NULL)`

### `record_review_event`

**Role:** role not stated on the model

**Purpose:** Append-only history event for one :class:`RecordReview` row.

| Column | Type | Nullable | Default | Foreign key | Enum values | Meaning |
|---|---|---|---|---|---|---|
| `id` | BIGINT | no | — | — | — | not documented |
| `record_review_id` | BIGINT | no | — | record_review.id | — | not documented |
| `event_kind` | RecordReviewEventKind (enum) | no | — | — | `created`, `status_change` | not documented |
| `from_status` | RecordReviewStatus (enum) | yes | — | — | `not_reviewed`, `under_review`, `approved`, `rejected`, `deprecated` | not documented |
| `to_status` | RecordReviewStatus (enum) | yes | — | — | `not_reviewed`, `under_review`, `approved`, `rejected`, `deprecated` | not documented |
| `actor_user_id` | BIGINT | yes | — | app_user.id | — | not documented |
| `reason` | TEXT | yes | — | — | — | not documented |
| `details_json` | JSONB | yes | — | — | — | not documented |
| `created_at` | TIMESTAMP WITHOUT TIME ZONE | no | now() | — | — | not documented |

### `release_artifact`

**Role:** role not stated on the model

**Purpose:** One checksummed file belonging to a :class:`ReleaseManifest`.

| Column | Type | Nullable | Default | Foreign key | Enum values | Meaning |
|---|---|---|---|---|---|---|
| `id` | BIGINT | no | — | — | — | not documented |
| `release_manifest_id` | BIGINT | no | — | release_manifest.id | — | not documented |
| `kind` | ReleaseArtifactKind (enum) | no | — | — | `selected_records`, `candidate_records`, `review_history`, `selection_ledger` | not documented |
| `path` | VARCHAR(255) | no | — | — | — | not documented |
| `media_type` | VARCHAR(128) | no | — | — | — | not documented |
| `content` | BYTEA | no | — | — | — | not documented |
| `sha256` | VARCHAR(64) | no | — | — | — | not documented |
| `byte_count` | BIGINT | no | — | — | — | not documented |
| `record_count` | BIGINT | no | — | — | — | not documented |

**Check constraints:**

- `ck_release_artifact_byte_count_nonneg`: `byte_count >= 0`
- `ck_release_artifact_path_nonblank`: `length(btrim(path)) > 0`
- `ck_release_artifact_record_count_nonneg`: `record_count >= 0`
- `ck_release_artifact_sha256_hex`: `sha256 ~ '^[0-9a-f]{64}$'`

### `release_selection`

**Role:** role not stated on the model

**Purpose:** One attributed, append-only selection of a candidate record.

| Column | Type | Nullable | Default | Foreign key | Enum values | Meaning |
|---|---|---|---|---|---|---|
| `id` | BIGINT | no | — | — | — | not documented |
| `dataset_release_id` | BIGINT | no | — | dataset_release.id | — | not documented |
| `curation_policy_id` | BIGINT | no | — | curation_policy.id | — | not documented |
| `record_type` | SubmissionRecordType (enum) | no | — | — | `species`, `species_entry`, `conformer_group`, `conformer_observation`, `reaction`, `reaction_entry`, `transition_state`, `transition_state_entry`, `calculation`, `statmech`, `thermo`, `kinetics`, `transport`, `network`, `network_solve`, `applied_energy_correction`, `artifact`, `molecular_property_observation` | not documented |
| `record_id` | BIGINT | no | — | — | — | not documented |
| `subject_type` | SubmissionRecordType (enum) | no | — | — | `species`, `species_entry`, `conformer_group`, `conformer_observation`, `reaction`, `reaction_entry`, `transition_state`, `transition_state_entry`, `calculation`, `statmech`, `thermo`, `kinetics`, `transport`, `network`, `network_solve`, `applied_energy_correction`, `artifact`, `molecular_property_observation` | not documented |
| `subject_id` | BIGINT | no | — | — | — | not documented |
| `action` | ReleaseSelectionAction (enum) | no | select | — | `select`, `supersede`, `withdraw` | not documented |
| `supersedes_selection_id` | BIGINT | yes | — | release_selection.id | — | ``supersedes_selection_id`` is ``UNIQUE``, so a selection can be replaced at most once and the supersession chain stays linear and auditable, exactly as ``scientific_record_supersession`` does. |
| `rationale` | TEXT | no | — | — | — | not documented |
| `selected_by` | BIGINT | no | — | app_user.id | — | not documented |
| `created_at` | TIMESTAMP WITHOUT TIME ZONE | no | now() | — | — | not documented |
| `public_ref` | VARCHAR(40) | no | — | — | — | not documented |

**Check constraints:**

- `ck_release_selection_no_self_supersession`: `supersedes_selection_id IS NULL OR supersedes_selection_id <> id`
- `ck_release_selection_rationale_nonblank`: `length(btrim(rationale)) > 0`
- `ck_release_selection_selectable_record_type`: `record_type IN ('kinetics', 'network_solve', 'statmech', 'thermo', 'transition_state_entry', 'transport')`
- `ck_release_selection_supersession_matches_action`: `(action = 'select' AND supersedes_selection_id IS NULL) OR (action IN ('supersede', 'withdraw') AND supersedes_selection_id IS NOT NULL)`

### `scientific_record_supersession`

**Role:** role not stated on the model

**Purpose:** One immutable, one-to-one edge from an accepted record to its replacement.

| Column | Type | Nullable | Default | Foreign key | Enum values | Meaning |
|---|---|---|---|---|---|---|
| `id` | BIGINT | no | — | — | — | not documented |
| `record_type` | SubmissionRecordType (enum) | no | — | — | `species`, `species_entry`, `conformer_group`, `conformer_observation`, `reaction`, `reaction_entry`, `transition_state`, `transition_state_entry`, `calculation`, `statmech`, `thermo`, `kinetics`, `transport`, `network`, `network_solve`, `applied_energy_correction`, `artifact`, `molecular_property_observation` | not documented |
| `superseded_record_id` | BIGINT | no | — | — | — | not documented |
| `superseding_record_id` | BIGINT | no | — | — | — | not documented |
| `reason` | TEXT | no | — | — | — | not documented |
| `created_by` | BIGINT | no | — | app_user.id | — | not documented |
| `created_at` | TIMESTAMP WITHOUT TIME ZONE | no | now() | — | — | not documented |

**Check constraints:**

- `ck_scientific_record_supersession_distinct_records`: `superseded_record_id <> superseding_record_id`
- `ck_scientific_record_supersession_reason_nonblank`: `length(btrim(reason)) > 0`
- `ck_scientific_record_supersession_supported_type`: `record_type IN ('calculation', 'thermo', 'statmech', 'kinetics', 'transport', 'network', 'network_solve', 'applied_energy_correction', 'transition_state_entry', 'conformer_observation')`

### `software_release`

**Role:** role not stated on the model

**Purpose:** Exact release metadata for a software package.

| Column | Type | Nullable | Default | Foreign key | Enum values | Meaning |
|---|---|---|---|---|---|---|
| `id` | BIGINT | no | — | — | — | not documented |
| `software_id` | BIGINT | no | — | software.id | — | not documented |
| `version` | TEXT | yes | — | — | — | not documented |
| `revision` | TEXT | yes | — | — | — | not documented |
| `build` | TEXT | yes | — | — | — | not documented |
| `release_date` | DATE | yes | — | — | — | not documented |
| `notes` | TEXT | yes | — | — | — | not documented |
| `created_at` | TIMESTAMP WITHOUT TIME ZONE | no | now() | — | — | not documented |
| `public_ref` | VARCHAR(40) | no | — | — | — | not documented |

### `statmech`

**Role:** role not stated on the model

**Purpose:** Statistical mechanics interpretation layer for a species entry.

| Column | Type | Nullable | Default | Foreign key | Enum values | Meaning |
|---|---|---|---|---|---|---|
| `id` | BIGINT | no | — | — | — | not documented |
| `species_entry_id` | BIGINT | yes | — | species_entry.id | — | not documented |
| `transition_state_entry_id` | BIGINT | yes | — | transition_state_entry.id | — | not documented |
| `scientific_origin` | ScientificOriginKind (enum) | no | — | — | `computed`, `experimental`, `estimated` | not documented |
| `literature_id` | BIGINT | yes | — | literature.id | — | not documented |
| `workflow_tool_release_id` | BIGINT | yes | — | workflow_tool_release.id | — | not documented |
| `software_release_id` | BIGINT | yes | — | software_release.id | — | not documented |
| `external_symmetry` | SMALLINT | yes | — | — | — | not documented |
| `point_group` | TEXT | yes | — | — | — | not documented |
| `is_linear` | BOOLEAN | yes | — | — | — | not documented |
| `rigid_rotor_kind` | RigidRotorKind (enum) | yes | — | — | `atom`, `linear`, `spherical_top`, `symmetric_top`, `asymmetric_top` | not documented |
| `rotational_constant_a_cm1` | DOUBLE PRECISION | yes | — | — | — | not documented |
| `rotational_constant_b_cm1` | DOUBLE PRECISION | yes | — | — | — | not documented |
| `rotational_constant_c_cm1` | DOUBLE PRECISION | yes | — | — | — | not documented |
| `statmech_treatment` | StatmechTreatmentKind (enum) | yes | — | — | `rrho`, `rrho_1d`, `rrho_nd`, `rrho_1d_nd`, `rrho_ad`, `rrao` | not documented |
| `frequency_scale_factor_id` | BIGINT | yes | — | frequency_scale_factor.id | — | not documented |
| `uses_projected_frequencies` | BOOLEAN | yes | — | — | — | not documented |
| `optical_isomers` | SMALLINT | yes | — | — | — | not documented |
| `note` | TEXT | yes | — | — | — | not documented |
| `created_at` | TIMESTAMP WITHOUT TIME ZONE | no | now() | — | — | not documented |
| `created_by` | BIGINT | yes | — | app_user.id | — | not documented |
| `public_ref` | VARCHAR(40) | no | — | — | — | not documented |

**Check constraints:**

- `ck_statmech_external_symmetry_ge_1`: `external_symmetry IS NULL OR external_symmetry >= 1`
- `ck_statmech_optical_isomers_ge_1`: `optical_isomers IS NULL OR optical_isomers >= 1`
- `ck_statmech_rotational_constant_a_cm1_positive`: `rotational_constant_a_cm1 IS NULL OR rotational_constant_a_cm1 > 0`
- `ck_statmech_rotational_constant_b_cm1_positive`: `rotational_constant_b_cm1 IS NULL OR rotational_constant_b_cm1 > 0`
- `ck_statmech_rotational_constant_c_cm1_positive`: `rotational_constant_c_cm1 IS NULL OR rotational_constant_c_cm1 > 0`
- `ck_statmech_statmech_exactly_one_subject`: `(species_entry_id IS NULL) <> (transition_state_entry_id IS NULL)`

### `statmech_electronic_level`

**Role:** role not stated on the model

**Purpose:** One electronic energy level for the electronic partition function.

| Column | Type | Nullable | Default | Foreign key | Enum values | Meaning |
|---|---|---|---|---|---|---|
| `id` | BIGINT | no | — | — | — | not documented |
| `statmech_id` | BIGINT | no | — | statmech.id | — | not documented |
| `level_index` | INTEGER | no | — | — | — | not documented |
| `energy_cm1` | DOUBLE PRECISION | no | — | — | — | not documented |
| `degeneracy` | INTEGER | no | — | — | — | not documented |

**Check constraints:**

- `ck_statmech_electronic_level_degeneracy_ge_1`: `degeneracy >= 1`
- `ck_statmech_electronic_level_energy_cm1_ge_0`: `energy_cm1 >= 0`
- `ck_statmech_electronic_level_level_index_ge_1`: `level_index >= 1`

### `statmech_source_calculation`

**Role:** role not stated on the model

**Purpose:** Links statmech records to source calculations by role.

| Column | Type | Nullable | Default | Foreign key | Enum values | Meaning |
|---|---|---|---|---|---|---|
| `statmech_id` | BIGINT | no | — | statmech.id | — | not documented |
| `calculation_id` | BIGINT | no | — | calculation.id | — | not documented |
| `role` | StatmechCalculationRole (enum) | no | — | — | `opt`, `freq`, `sp`, `scan`, `composite`, `imported` | not documented |

### `statmech_torsion`

**Role:** role not stated on the model

**Purpose:** Stores one torsion associated with a statmech record.

| Column | Type | Nullable | Default | Foreign key | Enum values | Meaning |
|---|---|---|---|---|---|---|
| `id` | BIGINT | no | — | — | — | not documented |
| `statmech_id` | BIGINT | no | — | statmech.id | — | not documented |
| `torsion_index` | INTEGER | no | — | — | — | not documented |
| `symmetry_number` | SMALLINT | yes | — | — | — | not documented |
| `treatment_kind` | TorsionTreatmentKind (enum) | yes | — | — | `hindered_rotor`, `free_rotor`, `rigid_top`, `hindered_rotor_dos` | not documented |
| `dimension` | INTEGER | no | 1 | — | — | not documented |
| `top_description` | TEXT | yes | — | — | — | not documented |
| `invalidated_reason` | TEXT | yes | — | — | — | not documented |
| `note` | TEXT | yes | — | — | — | not documented |
| `source_scan_calculation_id` | BIGINT | yes | — | calculation.id | — | not documented |

**Check constraints:**

- `ck_statmech_torsion_dimension_ge_1`: `dimension >= 1`
- `ck_statmech_torsion_symmetry_number_ge_1`: `symmetry_number IS NULL OR symmetry_number >= 1`
- `ck_statmech_torsion_torsion_index_ge_1`: `torsion_index >= 1`

### `statmech_torsion_definition`

**Role:** role not stated on the model

**Purpose:** Atom indices for a torsional coordinate.

| Column | Type | Nullable | Default | Foreign key | Enum values | Meaning |
|---|---|---|---|---|---|---|
| `torsion_id` | BIGINT | no | — | statmech_torsion.id | — | not documented |
| `coordinate_index` | INTEGER | no | — | — | — | not documented |
| `atom1_index` | INTEGER | no | — | — | — | not documented |
| `atom2_index` | INTEGER | no | — | — | — | not documented |
| `atom3_index` | INTEGER | no | — | — | — | not documented |
| `atom4_index` | INTEGER | no | — | — | — | not documented |

**Check constraints:**

- `ck_statmech_torsion_definition_atom1_index_ge_1`: `atom1_index >= 1`
- `ck_statmech_torsion_definition_atom2_index_ge_1`: `atom2_index >= 1`
- `ck_statmech_torsion_definition_atom3_index_ge_1`: `atom3_index >= 1`
- `ck_statmech_torsion_definition_atom4_index_ge_1`: `atom4_index >= 1`
- `ck_statmech_torsion_definition_coordinate_index_ge_1`: `coordinate_index >= 1`

### `submission`

**Role:** role not stated on the model

**Purpose:** One user contribution event tracked for moderation and publication.

| Column | Type | Nullable | Default | Foreign key | Enum values | Meaning |
|---|---|---|---|---|---|---|
| `id` | BIGINT | no | — | — | — | not documented |
| `created_by` | BIGINT | no | — | app_user.id | — | not documented |
| `submission_kind` | SubmissionKind (enum) | no | — | — | `computed_reaction`, `computed_species`, `conformer`, `reaction`, `kinetics`, `network`, `network_pdep`, `statmech`, `thermo`, `transition_state`, `transport`, `other` | not documented |
| `source_kind` | SubmissionSourceKind (enum) | no | api | — | `api`, `web`, `bulk_import`, `system`, `migration` | not documented |
| `upload_job_id` | UUID | yes | — | upload_job.id | — | not documented |
| `status` | SubmissionStatus (enum) | no | pending | — | `pending`, `precheck_passed`, `auto_flagged`, `approved`, `rejected`, `superseded`, `failed` | not documented |
| `title` | VARCHAR(200) | yes | — | — | — | not documented |
| `summary` | TEXT | yes | — | — | — | not documented |
| `submitted_at` | TIMESTAMP WITHOUT TIME ZONE | no | now() | — | — | not documented |
| `approved_at` | TIMESTAMP WITHOUT TIME ZONE | yes | — | — | — | not documented |
| `approved_by` | BIGINT | yes | — | app_user.id | — | not documented |
| `rejected_at` | TIMESTAMP WITHOUT TIME ZONE | yes | — | — | — | not documented |
| `rejected_by` | BIGINT | yes | — | app_user.id | — | not documented |
| `rejection_reason` | TEXT | yes | — | — | — | not documented |
| `correction_due_at` | TIMESTAMP WITHOUT TIME ZONE | yes | — | — | — | not documented |
| `supersedes_submission_id` | BIGINT | yes | — | submission.id | — | ``supersedes_submission_id`` is stored on the *replacing* submission and points at the one it replaces. The inverse direction is exposed as a relationship, not a second column, to keep a single source of truth. |
| `llm_precheck_label` | SubmissionPrecheckLabel (enum) | yes | — | — | `passed`, `flagged` | not documented |
| `llm_precheck_summary` | TEXT | yes | — | — | — | not documented |
| `llm_precheck_model` | VARCHAR(128) | yes | — | — | — | not documented |
| `llm_precheck_at` | TIMESTAMP WITHOUT TIME ZONE | yes | — | — | — | not documented |
| `created_at` | TIMESTAMP WITHOUT TIME ZONE | no | now() | — | — | not documented |
| `public_ref` | VARCHAR(40) | no | — | — | — | not documented |

**Check constraints:**

- `ck_submission_submission_approver_not_creator`: `(status <> 'approved') OR (approved_by IS NOT NULL AND approved_by <> created_by)`
- `ck_submission_submission_rejected_requires_reason`: `(status <> 'rejected') OR (rejection_reason IS NOT NULL)`
- `ck_submission_submission_rejecter_not_creator`: `(status <> 'rejected') OR (rejected_by IS NOT NULL AND rejected_by <> created_by)`

### `submission_audit_event`

**Role:** role not stated on the model

**Purpose:** Append-only moderation/lifecycle event for a submission.

| Column | Type | Nullable | Default | Foreign key | Enum values | Meaning |
|---|---|---|---|---|---|---|
| `id` | BIGINT | no | — | — | — | not documented |
| `submission_id` | BIGINT | no | — | submission.id | — | not documented |
| `created_at` | TIMESTAMP WITHOUT TIME ZONE | no | now() | — | — | not documented |
| `actor_user_id` | BIGINT | yes | — | app_user.id | — | not documented |
| `actor_kind` | SubmissionActorKind (enum) | no | — | — | `user`, `curator`, `admin`, `llm`, `system` | not documented |
| `event_kind` | SubmissionAuditEventKind (enum) | no | — | — | `submission_created`, `ingestion_succeeded`, `ingestion_failed`, `llm_precheck_passed`, `llm_precheck_flagged`, `llm_precheck_recorded`, `curator_approved`, `curator_rejected`, `correction_window_opened`, `correction_uploaded`, `submission_superseded`, `status_changed`, `public_visibility_changed`, `observation_identity_attached` | not documented |
| `from_status` | SubmissionStatus (enum) | yes | — | — | `pending`, `precheck_passed`, `auto_flagged`, `approved`, `rejected`, `superseded`, `failed` | not documented |
| `to_status` | SubmissionStatus (enum) | yes | — | — | `pending`, `precheck_passed`, `auto_flagged`, `approved`, `rejected`, `superseded`, `failed` | not documented |
| `reason` | TEXT | yes | — | — | — | not documented |
| `summary` | TEXT | yes | — | — | — | not documented |
| `details_json` | JSONB | yes | — | — | — | not documented |
| `related_submission_id` | BIGINT | yes | — | submission.id | — | not documented |

### `submission_record_link`

**Role:** role not stated on the model

**Purpose:** Lightweight mapping from a submission to one scientific record it produced.

| Column | Type | Nullable | Default | Foreign key | Enum values | Meaning |
|---|---|---|---|---|---|---|
| `id` | BIGINT | no | — | — | — | not documented |
| `submission_id` | BIGINT | no | — | submission.id | — | not documented |
| `record_type` | SubmissionRecordType (enum) | no | — | — | `species`, `species_entry`, `conformer_group`, `conformer_observation`, `reaction`, `reaction_entry`, `transition_state`, `transition_state_entry`, `calculation`, `statmech`, `thermo`, `kinetics`, `transport`, `network`, `network_solve`, `applied_energy_correction`, `artifact`, `molecular_property_observation` | not documented |
| `record_id` | BIGINT | no | — | — | — | not documented |
| `role` | VARCHAR(64) | yes | — | — | — | not documented |
| `created_at` | TIMESTAMP WITHOUT TIME ZONE | no | now() | — | — | not documented |

### `submission_rights_attestation`

**Role:** role not stated on the model

**Purpose:** One append-only statement that a submission may be licensed as stated.

| Column | Type | Nullable | Default | Foreign key | Enum values | Meaning |
|---|---|---|---|---|---|---|
| `id` | BIGINT | no | — | — | — | not documented |
| `submission_id` | BIGINT | no | — | submission.id | — | not documented |
| `license_id` | VARCHAR(64) | no | — | — | — | not documented |
| `basis` | RightsBasisKind (enum) | no | — | — | `depositor_agreement`, `operator_own_data`, `historical_review`, `source_terms` | not documented |
| `attested_by` | BIGINT | no | — | app_user.id | — | not documented |
| `actor_kind` | SubmissionActorKind (enum) | no | — | — | `user`, `curator`, `admin`, `llm`, `system` | not documented |
| `attested_at` | TIMESTAMP WITHOUT TIME ZONE | no | now() | — | — | not documented |
| `source_terms` | TEXT | yes | — | — | — | not documented |
| `note` | TEXT | yes | — | — | — | not documented |
| `supersedes_attestation_id` | BIGINT | yes | — | submission_rights_attestation.id | — | not documented |
| `created_at` | TIMESTAMP WITHOUT TIME ZONE | no | now() | — | — | not documented |
| `public_ref` | VARCHAR(40) | no | — | — | — | not documented |

**Check constraints:**

- `ck_submission_rights_attestation_license_id_nonblank`: `length(btrim(license_id)) > 0`
- `ck_submission_rights_attestation_source_terms_required`: `(basis <> 'source_terms') OR (source_terms IS NOT NULL AND length(btrim(source_terms)) > 0)`

### `thermo`

**Role:** role not stated on the model

**Purpose:** Thermochemistry records for a species entry.

| Column | Type | Nullable | Default | Foreign key | Enum values | Meaning |
|---|---|---|---|---|---|---|
| `id` | BIGINT | no | — | — | — | not documented |
| `species_entry_id` | BIGINT | no | — | species_entry.id | — | not documented |
| `scientific_origin` | ScientificOriginKind (enum) | no | — | — | `computed`, `experimental`, `estimated` | not documented |
| `model_kind` | ThermoModelKind (enum) | yes | — | — | `nasa7`, `nasa9`, `wilhoit`, `tabulated`, `scalar` | not documented |
| `literature_id` | BIGINT | yes | — | literature.id | — | not documented |
| `workflow_tool_release_id` | BIGINT | yes | — | workflow_tool_release.id | — | not documented |
| `software_release_id` | BIGINT | yes | — | software_release.id | — | not documented |
| `enthalpy_reference_kind` | EnthalpyReferenceKind (enum) | yes | — | — | `formation_298k` | ``enthalpy_reference_kind`` declares which reference every enthalpy on this record uses. ``formation_298k`` is the standard enthalpy of formation at 298.15 K; an enthalpy at any other temperature is that value plus the species' own enthalpy increment from 298.15 K, with the elemental term not reevaluated. ``NULL`` means the reference was never recorded -- it is never inferred from a value, a producer or a neighbouring row, and never backfilled. |
| `h298_kj_mol` | DOUBLE PRECISION | yes | — | — | — | ``h298_kj_mol`` / ``s298_j_mol_k`` are the standard enthalpy of formation and standard entropy at 298.15 K. |
| `s298_j_mol_k` | DOUBLE PRECISION | yes | — | — | — | ``h298_kj_mol`` / ``s298_j_mol_k`` are the standard enthalpy of formation and standard entropy at 298.15 K. |
| `h298_uncertainty_kj_mol` | DOUBLE PRECISION | yes | — | — | — | not documented |
| `s298_uncertainty_j_mol_k` | DOUBLE PRECISION | yes | — | — | — | not documented |
| `enthalpy_formation_0k_kj_mol` | DOUBLE PRECISION | yes | — | — | — | ``enthalpy_formation_0k_kj_mol`` is the 0 K standard formation enthalpy (ΔfH°(0 K)), the quantity computational thermochemistry derives directly from atomization/composite energies. It relates to ``h298_kj_mol`` through the species and element thermal enthalpy increments, ΔfH°(298) = ΔfH°(0) + [H°(298) − H°(0)]_species − Σ [H°(298) − H°(0)]_elements; the two are stored side by side rather than derived from one another because either may be the primary reported value. |
| `enthalpy_formation_0k_uncertainty_kj_mol` | DOUBLE PRECISION | yes | — | — | — | not documented |
| `reference_pressure_bar` | DOUBLE PRECISION | yes | — | — | — | ``reference_pressure_bar`` is the standard-state pressure the H/S and NASA/tabulated values are referenced to (IUPAC 1 bar; legacy data using the older 1 atm convention should record 1.01325). ``NULL`` means the reference pressure was not specified. There is no origin default (decided 2026-09-24, issue #529): a QC computed upload that omits it stays ``NULL`` rather than being stamped ``1.0``, because the dominant computed producer (ARC) computes entropy at 1 atm and records no pressure anywhere in its output -- the same class of invisible, plausible-looking error the enthalpy-reference decision above exists to prevent. Rows written before this decision still carry the invented ``1.0``; correcting them is a separate, undecided question. |
| `phase` | PhaseKind (enum) | yes | — | — | `gas`, `liquid`, `solid`, `aqueous` | ``phase`` records the physical phase (gas by default for computed species). ``NULL`` means unspecified. |
| `tmin_k` | DOUBLE PRECISION | yes | — | — | — | not documented |
| `tmax_k` | DOUBLE PRECISION | yes | — | — | — | not documented |
| `statmech_id` | BIGINT | yes | — | statmech.id | — | ``statmech_id`` links a *computed* thermo row to the ``statmech`` record it was derived from. ``NULL`` for experimental, literature, or group-additivity thermo that has no statmech basis. |
| `note` | TEXT | yes | — | — | — | not documented |
| `created_at` | TIMESTAMP WITHOUT TIME ZONE | no | now() | — | — | not documented |
| `created_by` | BIGINT | yes | — | app_user.id | — | not documented |
| `public_ref` | VARCHAR(40) | no | — | — | — | not documented |

**Check constraints:**

- `ck_thermo_enthalpy_formation_0k_uncertainty_ge_0`: `enthalpy_formation_0k_uncertainty_kj_mol IS NULL OR enthalpy_formation_0k_uncertainty_kj_mol >= 0`
- `ck_thermo_h298_uncertainty_ge_0`: `h298_uncertainty_kj_mol IS NULL OR h298_uncertainty_kj_mol >= 0`
- `ck_thermo_s298_uncertainty_ge_0`: `s298_uncertainty_j_mol_k IS NULL OR s298_uncertainty_j_mol_k >= 0`
- `ck_thermo_tmax_k_gt_0`: `tmax_k IS NULL OR tmax_k > 0`
- `ck_thermo_tmin_k_gt_0`: `tmin_k IS NULL OR tmin_k > 0`
- `ck_thermo_tmin_le_tmax`: `tmin_k IS NULL OR tmax_k IS NULL OR tmin_k <= tmax_k`

### `thermo_nasa`

**Role:** role not stated on the model

**Purpose:** NASA polynomial coefficients for a thermo record.

| Column | Type | Nullable | Default | Foreign key | Enum values | Meaning |
|---|---|---|---|---|---|---|
| `thermo_id` | BIGINT | no | — | thermo.id | — | not documented |
| `t_low` | DOUBLE PRECISION | yes | — | — | — | not documented |
| `t_mid` | DOUBLE PRECISION | yes | — | — | — | not documented |
| `t_high` | DOUBLE PRECISION | yes | — | — | — | not documented |
| `a1` | DOUBLE PRECISION | yes | — | — | — | not documented |
| `a2` | DOUBLE PRECISION | yes | — | — | — | not documented |
| `a3` | DOUBLE PRECISION | yes | — | — | — | not documented |
| `a4` | DOUBLE PRECISION | yes | — | — | — | not documented |
| `a5` | DOUBLE PRECISION | yes | — | — | — | not documented |
| `a6` | DOUBLE PRECISION | yes | — | — | — | not documented |
| `a7` | DOUBLE PRECISION | yes | — | — | — | not documented |
| `b1` | DOUBLE PRECISION | yes | — | — | — | not documented |
| `b2` | DOUBLE PRECISION | yes | — | — | — | not documented |
| `b3` | DOUBLE PRECISION | yes | — | — | — | not documented |
| `b4` | DOUBLE PRECISION | yes | — | — | — | not documented |
| `b5` | DOUBLE PRECISION | yes | — | — | — | not documented |
| `b6` | DOUBLE PRECISION | yes | — | — | — | not documented |
| `b7` | DOUBLE PRECISION | yes | — | — | — | not documented |

**Check constraints:**

- `ck_thermo_nasa_t_high_gt_t_mid`: `t_mid IS NULL OR t_high IS NULL OR t_high > t_mid`
- `ck_thermo_nasa_t_low_gt_0`: `t_low IS NULL OR t_low > 0`
- `ck_thermo_nasa_t_mid_gt_t_low`: `t_low IS NULL OR t_mid IS NULL OR t_mid > t_low`
- `ck_thermo_nasa_temperature_bounds_all_or_none`: `
            (
                t_low IS NULL
                AND t_mid IS NULL
                AND t_high IS NULL
            )
            OR
            (
                t_low IS NOT NULL
                AND t_mid IS NOT NULL
                AND t_high IS NOT NULL
            )
            `

### `thermo_nasa9_interval`

**Role:** role not stated on the model

**Purpose:** One temperature interval of a NASA-9 (Glenn/NASA-9) polynomial.

| Column | Type | Nullable | Default | Foreign key | Enum values | Meaning |
|---|---|---|---|---|---|---|
| `id` | BIGINT | no | — | — | — | not documented |
| `thermo_id` | BIGINT | no | — | thermo.id | — | not documented |
| `interval_index` | INTEGER | no | — | — | — | not documented |
| `t_min_k` | DOUBLE PRECISION | no | — | — | — | not documented |
| `t_max_k` | DOUBLE PRECISION | no | — | — | — | not documented |
| `a1` | DOUBLE PRECISION | no | — | — | — | not documented |
| `a2` | DOUBLE PRECISION | no | — | — | — | not documented |
| `a3` | DOUBLE PRECISION | no | — | — | — | not documented |
| `a4` | DOUBLE PRECISION | no | — | — | — | not documented |
| `a5` | DOUBLE PRECISION | no | — | — | — | not documented |
| `a6` | DOUBLE PRECISION | no | — | — | — | not documented |
| `a7` | DOUBLE PRECISION | no | — | — | — | not documented |
| `a8` | DOUBLE PRECISION | no | — | — | — | not documented |
| `a9` | DOUBLE PRECISION | no | — | — | — | not documented |

**Check constraints:**

- `ck_thermo_nasa9_interval_interval_index_ge_1`: `interval_index >= 1`
- `ck_thermo_nasa9_interval_t_max_k_gt_t_min_k`: `t_max_k > t_min_k`
- `ck_thermo_nasa9_interval_t_min_k_gt_0`: `t_min_k > 0`

### `thermo_point`

**Role:** role not stated on the model

**Purpose:** Tabulated standard-state thermo values at a specific temperature.

| Column | Type | Nullable | Default | Foreign key | Enum values | Meaning |
|---|---|---|---|---|---|---|
| `thermo_id` | BIGINT | no | — | thermo.id | — | not documented |
| `temperature_k` | DOUBLE PRECISION | no | — | — | — | not documented |
| `cp_j_mol_k` | DOUBLE PRECISION | yes | — | — | — | not documented |
| `h_kj_mol` | DOUBLE PRECISION | yes | — | — | — | ``h_kj_mol`` is the tabulated enthalpy at ``temperature_k``, on the same reference zero the parent ``thermo`` row's ``enthalpy_reference_kind`` declares: for ``formation_298k``, the standard formation enthalpy at 298.15 K plus the species' own enthalpy increment from 298.15 K to ``temperature_k``, with the elemental term pinned at 298.15 K and not reevaluated at T -- so this is not ``H(T) - H(0)``. A parent record whose reference is undeclared (legacy, predating the declaration rule) leaves this value's reference zero unestablished; ``h_kj_mol`` carries no declaration of its own, it is governed entirely by the parent's. |
| `s_j_mol_k` | DOUBLE PRECISION | yes | — | — | — | not documented |
| `g_kj_mol` | DOUBLE PRECISION | yes | — | — | — | ``g_kj_mol`` is the Gibbs free energy at ``temperature_k`` on that same zero, ``H(T) - T*S(T)`` (entropy converted from J/(mol*K) to kJ/(mol*K)); it inherits the same parent-governed, possibly-undeclared reference zero as ``h_kj_mol``. |

### `thermo_source_calculation`

**Role:** role not stated on the model

**Purpose:** Links thermo records to supporting calculations by role.

| Column | Type | Nullable | Default | Foreign key | Enum values | Meaning |
|---|---|---|---|---|---|---|
| `thermo_id` | BIGINT | no | — | thermo.id | — | not documented |
| `calculation_id` | BIGINT | no | — | calculation.id | — | not documented |
| `role` | ThermoCalculationRole (enum) | no | — | — | `opt`, `freq`, `sp`, `composite`, `imported` | not documented |

### `thermo_wilhoit`

**Role:** role not stated on the model

**Purpose:** Wilhoit heat-capacity form for a thermo record (1:1).

| Column | Type | Nullable | Default | Foreign key | Enum values | Meaning |
|---|---|---|---|---|---|---|
| `thermo_id` | BIGINT | no | — | thermo.id | — | not documented |
| `cp0_j_mol_k` | DOUBLE PRECISION | no | — | — | — | not documented |
| `cp_inf_j_mol_k` | DOUBLE PRECISION | no | — | — | — | not documented |
| `b_k` | DOUBLE PRECISION | no | — | — | — | not documented |
| `a0` | DOUBLE PRECISION | no | — | — | — | not documented |
| `a1` | DOUBLE PRECISION | no | — | — | — | not documented |
| `a2` | DOUBLE PRECISION | no | — | — | — | not documented |
| `a3` | DOUBLE PRECISION | no | — | — | — | not documented |
| `h0_kj_mol` | DOUBLE PRECISION | yes | — | — | — | not documented |
| `s0_j_mol_k` | DOUBLE PRECISION | yes | — | — | — | not documented |

**Check constraints:**

- `ck_thermo_wilhoit_b_k_gt_0`: `b_k > 0`
- `ck_thermo_wilhoit_cp0_ge_0`: `cp0_j_mol_k >= 0`
- `ck_thermo_wilhoit_cp_inf_ge_0`: `cp_inf_j_mol_k >= 0`

### `transition_state`

**Role:** role not stated on the model

**Purpose:** Reaction-channel-level transition-state concept.

| Column | Type | Nullable | Default | Foreign key | Enum values | Meaning |
|---|---|---|---|---|---|---|
| `id` | BIGINT | no | — | — | — | not documented |
| `reaction_entry_id` | BIGINT | no | — | reaction_entry.id | — | not documented |
| `label` | TEXT | yes | — | — | — | not documented |
| `note` | TEXT | yes | — | — | — | not documented |
| `created_at` | TIMESTAMP WITHOUT TIME ZONE | no | now() | — | — | not documented |
| `created_by` | BIGINT | yes | — | app_user.id | — | not documented |
| `public_ref` | VARCHAR(40) | no | — | — | — | not documented |

### `transition_state_entry`

**Role:** role not stated on the model

**Purpose:** One candidate transition-state geometry family member under a TS concept.

| Column | Type | Nullable | Default | Foreign key | Enum values | Meaning |
|---|---|---|---|---|---|---|
| `id` | BIGINT | no | — | — | — | not documented |
| `transition_state_id` | BIGINT | no | — | transition_state.id | — | not documented |
| `charge` | SMALLINT | no | — | — | — | not documented |
| `multiplicity` | SMALLINT | no | — | — | — | not documented |
| `mol` | mol (RDKit cartridge structure) | yes | — | — | — | not documented |
| `unmapped_smiles` | TEXT | yes | — | — | — | not documented |
| `status` | TransitionStateEntryStatus (enum) | no | optimized | — | `guess`, `optimized`, `validated`, `rejected` | not documented |
| `created_at` | TIMESTAMP WITHOUT TIME ZONE | no | now() | — | — | not documented |
| `created_by` | BIGINT | yes | — | app_user.id | — | not documented |
| `public_ref` | VARCHAR(40) | no | — | — | — | not documented |

**Check constraints:**

- `ck_transition_state_entry_multiplicity_ge_1`: `multiplicity >= 1`

### `transport`

**Role:** role not stated on the model

**Purpose:** Transport properties attached to a species entry.

| Column | Type | Nullable | Default | Foreign key | Enum values | Meaning |
|---|---|---|---|---|---|---|
| `id` | BIGINT | no | — | — | — | not documented |
| `species_entry_id` | BIGINT | no | — | species_entry.id | — | not documented |
| `scientific_origin` | ScientificOriginKind (enum) | no | — | — | `computed`, `experimental`, `estimated` | not documented |
| `literature_id` | BIGINT | yes | — | literature.id | — | not documented |
| `software_release_id` | BIGINT | yes | — | software_release.id | — | not documented |
| `workflow_tool_release_id` | BIGINT | yes | — | workflow_tool_release.id | — | not documented |
| `sigma_angstrom` | DOUBLE PRECISION | yes | — | — | — | not documented |
| `epsilon_over_k_k` | DOUBLE PRECISION | yes | — | — | — | not documented |
| `dipole_debye` | DOUBLE PRECISION | yes | — | — | — | not documented |
| `polarizability_angstrom3` | DOUBLE PRECISION | yes | — | — | — | not documented |
| `rotational_relaxation` | DOUBLE PRECISION | yes | — | — | — | not documented |
| `note` | TEXT | yes | — | — | — | not documented |
| `created_at` | TIMESTAMP WITHOUT TIME ZONE | no | now() | — | — | not documented |
| `created_by` | BIGINT | yes | — | app_user.id | — | not documented |
| `public_ref` | VARCHAR(40) | no | — | — | — | not documented |

**Check constraints:**

- `ck_transport_epsilon_over_k_k_gt_0`: `epsilon_over_k_k IS NULL OR epsilon_over_k_k > 0`
- `ck_transport_lj_pair_both_or_neither`: `(sigma_angstrom IS NULL AND epsilon_over_k_k IS NULL) OR (sigma_angstrom IS NOT NULL AND epsilon_over_k_k IS NOT NULL)`
- `ck_transport_rotational_relaxation_ge_0`: `rotational_relaxation IS NULL OR rotational_relaxation >= 0`
- `ck_transport_sigma_angstrom_gt_0`: `sigma_angstrom IS NULL OR sigma_angstrom > 0`

### `transport_source_calculation`

**Role:** role not stated on the model

**Purpose:** Links transport records to supporting calculations by role.

| Column | Type | Nullable | Default | Foreign key | Enum values | Meaning |
|---|---|---|---|---|---|---|
| `transport_id` | BIGINT | no | — | transport.id | — | not documented |
| `calculation_id` | BIGINT | no | — | calculation.id | — | not documented |
| `role` | TransportCalculationRole (enum) | no | — | — | `full_transport`, `dipole`, `polarizability`, `supporting_geometry` | not documented |

### `upload_job`

**Role:** role not stated on the model

**Purpose:** Async upload job queue backed by PostgreSQL.

| Column | Type | Nullable | Default | Foreign key | Enum values | Meaning |
|---|---|---|---|---|---|---|
| `id` | UUID | no | gen_random_uuid() | — | — | not documented |
| `status` | UploadJobStatus (enum) | no | queued | — | `queued`, `processing`, `complete`, `failed` | not documented |
| `kind` | UploadJobKind (enum) | no | — | — | `computed_reaction`, `conformer`, `reaction`, `kinetics`, `network`, `network_pdep`, `thermo`, `transition_state`, `transport` | not documented |
| `payload` | JSONB | no | — | — | — | not documented |
| `created_by` | BIGINT | yes | — | app_user.id | — | not documented |
| `created_at` | TIMESTAMP WITHOUT TIME ZONE | no | now() | — | — | not documented |
| `started_at` | TIMESTAMP WITHOUT TIME ZONE | yes | — | — | — | not documented |
| `completed_at` | TIMESTAMP WITHOUT TIME ZONE | yes | — | — | — | not documented |
| `lease_expires_at` | TIMESTAMP WITHOUT TIME ZONE | yes | — | — | — | not documented |
| `heartbeat_at` | TIMESTAMP WITHOUT TIME ZONE | yes | — | — | — | not documented |
| `result` | JSONB | yes | — | — | — | not documented |
| `error` | TEXT | yes | — | — | — | not documented |
| `attempts` | INTEGER | no | 0 | — | — | not documented |
| `max_attempts` | INTEGER | no | 3 | — | — | not documented |

### `user_session`

**Role:** role not stated on the model

**Purpose:** Server-side session record for human browser authentication.

| Column | Type | Nullable | Default | Foreign key | Enum values | Meaning |
|---|---|---|---|---|---|---|
| `id` | BIGINT | no | — | — | — | not documented |
| `user_id` | BIGINT | no | — | app_user.id | — | not documented |
| `token_hash` | CHAR(64) | no | — | — | — | not documented |
| `expires_at` | TIMESTAMP WITHOUT TIME ZONE | no | — | — | — | not documented |
| `revoked_at` | TIMESTAMP WITHOUT TIME ZONE | yes | — | — | — | not documented |
| `created_at` | TIMESTAMP WITHOUT TIME ZONE | no | now() | — | — | not documented |
