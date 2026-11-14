# CCCBDB importer

The importer reads experimental data from the NIST Computational Chemistry
Comparison and Benchmark Database (CCCBDB, Standard Reference Database 101,
Release 22, DOI `10.18434/T47C7Z`). Code is under `backend/app/importers/cccbdb/`;
usage, commands and flags are in `backend/app/importers/cccbdb/README.md`.
This document states what the importer covers and where its output goes.

## Scope

- Only experimental pages are handled; no computed (method/basis) pages are
  parsed.
- Fetching is limited to an explicit allowlist of pages
  (`crawl_plan.py`). A target is fetched live only when its
  `is_validated_url` flag is set or `--allow-unverified-urls` is passed.
  Tests run against checked-in HTML fixtures
  (`backend/app/importers/cccbdb/fixtures/`); live smoke tests run only
  with `TCKDB_CCCBDB_LIVE_TESTS=1`.
- Every value keeps its CCCBDB reference label verbatim.
- Source constants (`SOURCE_NAME`, `SOURCE_RELEASE`, `SOURCE_DATABASE_DOI`,
  `PARSER_NAME`) are defined in `app/importers/cccbdb/__init__.py`.

## Page kinds

A `CrawlTarget` carries one of four page kinds (`PageKind` in
`crawl_plan.py`). Each has its own parser under `parsers/`.

| `page_kind` | Parser | Output |
|---|---|---|
| `experimental_species` | `experimental_species.py` | One typed record per species (identity, thermo, statmech, geometry). The builders in `builders/` turn it into species-entry, thermo, statmech and geometry payload dicts. |
| `experimental_property_table` | `experimental_property_table.py` | Rows of one cross-species flat table, driven by a per-property `PropertyTableConfig` in `PROPERTY_CONFIGS`. The property-table builder turns rows into `MolecularPropertyObservationCreate` payloads. |
| `species_all_data` | `species_all_data.py` | Triage only (title, section headings, identifiers found in the text). The raw HTML is the archived artifact; no payloads are built. |
| `molecule_catalog_inchi_index` | `molecule_catalog.py` | Catalog entries (formula, name, InChI, InChIKey, SMILES, CAS). Used to propose identity hints for property rows; no payloads are built. |

Other parsers: `experimental_index.py` (the experimental discovery page,
used by the property-config audit), `form_result.py` (form-only result
pages; `atomization_energy` is the only supported target kind) and
`species_selection.py` (`choosex.asp` species-selection pages).

## Pipeline

```text
fetch / browser-saved HTML  ->  raw_html/   (content-addressed by SHA-256)
raw_html                    ->  parsed/     (parser output)
parsed                      ->  payloads/   (builder output)
payload JSON                ->  database    (molecular-property import only)
```

- **Snapshot archive** (`snapshot.py`, `scripts/cccbdb_snapshot.py`): fetches
  allowlisted pages into the archive and writes `manifest.json`. Pilots:
  `experimental`, `experimental-properties`, `catalog`,
  `species-alldata-cas`. `species_all_data` responses pass a classifier gate
  (`diagnostics/classifier.py`); only `molecule_data_page` responses are
  stored as data. Parsed JSON and payloads are regenerated from the stored
  HTML without re-fetching.
- **Browser-assisted import** (`browser_import.py`,
  `scripts/cccbdb_import_saved_species_page.py`): classifies and archives an
  HTML file saved from a browser, with no network access.
- **Form resolver** (`form_resolver.py`, `scripts/cccbdb_resolve_form_page.py`):
  reads a JSON queue of records and drives the entry-page GET and
  `getformx.asp` POST with one session, for form-only pages. Species-selection
  pages are rejected by default (`SelectionPolicy.REJECT_AMBIGUOUS`); with
  `EXACT_MATCH` one candidate is chosen only when the queue record's
  formula plus name, CAS or InChIKey matches, never on formula alone.
  `form_payload_builder.py` builds `atomization_energy` payloads from the
  archived form results.
- **Dry-run exporters** (`scripts/cccbdb_property_payload_dryrun.py`,
  `scripts/cccbdb_form_payload_dryrun.py`): write validated
  `MolecularPropertyObservationCreate` dicts to JSON files and never touch the
  database.
- **Diagnostics** (`diagnostics/`, `scripts/cccbdb_resolver_diagnostics.py`):
  an opt-in probe that classifies what CCCBDB returns for a few URLs and
  form submissions; it writes no snapshot.

## What is persisted

- Nothing in the importer persists the thermo, statmech or geometry payloads
  built from `experimental_species` pages. They exist as in-memory results
  and archived JSON. Values without a destination on the upload schemas
  (Hf(0 K), experimental vibrational frequencies, experimental ZPE, raw
  rotational-constant units, per-value reference labels) are kept in
  `result.external_source` with a matching warning.
- The database import
  (`app/services/cccbdb_molecular_property_import.py`,
  `scripts/cccbdb_import_molecular_property_payloads.py`) accepts only
  `MolecularPropertyObservationCreate` payloads, read from the property-table
  and form-result dry-run directories. It is a dry run unless `--commit` is
  passed, and requires `--license`.
- Each run that would insert at least one new row opens one
  `Submission(source_kind=bulk_import)` under the `--actor` user and links
  every inserted row to it. The rights basis is `depositor_agreement`; the
  repository does not capture CCCBDB's published terms of use, so no
  `source_terms` attestation is recorded.
- Rows are deduplicated by the `mpo_dedupe_key` unique constraint, with a
  read-before-write check that reports `duplicate` dispositions.

## Identity

- A payload's `species_entry_id` is left empty by the builders. Catalog
  enrichment (`enrichment.py`) writes an `identity_hint` into
  `raw_payload_json` only when the catalog match is unambiguous; ambiguous
  (isomer) candidates are kept under `catalog_candidates` instead.
- The DB import sets `species_entry_id` only when
  `identity_hint["inchikey"]` matches exactly one species and that species has
  exactly one entry with `kind=minimum` and a ground electronic state
  (`app/services/external_observation_identity.py`, shared with the ThermoML
  importer). Formula, formula plus name, name, and CAS hints are reported as
  warnings and never resolve an entry. A `species_entry_id` already present
  on the payload is kept.
- Rows left without `species_entry_id` stay self-describing through their
  `external_source_*` columns and `identity_hint`.

## Provenance

Every observation carries flattened `external_source_*` columns on
`molecular_property_observation` (name, release, DOI, URL, record key, page
kind, content SHA-256, parser name and version). The importer does not write
`external_source` or `external_source_record` rows.

Archived pages are not committed: `data/external/cccbdb/` is git-ignored.
