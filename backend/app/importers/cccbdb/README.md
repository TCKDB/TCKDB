# CCCBDB importer (Phase 1)

Fixture-driven parser prototype for NIST CCCBDB experimental species
pages.

## What Phase 1 does

- Parses sanitized HTML fixtures of CCCBDB experimental species pages
  with the Python stdlib `html.parser`.
- Normalizes parsed values into TCKDB-aligned canonical units (kJ/mol,
  J/mol/K, cm⁻¹, GHz, ångström).
- Produces in-memory, typed Pydantic v2 records:
  `CCCBDBExperimentalSpeciesRecord` with sub-records for identity,
  thermo, statmech (point group / rotational constants / vibrational
  frequencies), and geometry.
- Stamps each record with full database-level and fetch-level
  provenance (source release, DOI, URL, content SHA256, parser
  version) and preserves value-level reference labels verbatim
  (`Gurvich`, `TRC`, `Pedley`, …).
- Tolerates missing optional sections by emitting structured
  `warnings` instead of raising.

## What Phase 1 deliberately does **not** do

- No database writes. No upload routes. No TCKDB workflow integration.
- No live CCCBDB crawling in tests. Live smoke tests exist but are
  gated behind `TCKDB_CCCBDB_LIVE_TESTS=1` and are not part of CI.
- No RDKit-based structural canonicalization. SMILES / InChI strings
  are whitespace-stripped only; structural normalization is a Phase 2
  builder concern.
- No mode-level experimental frequency persistence model. Mode-level
  frequencies are produced by the parser but their final destination
  depends on Schema Gap 2 in
  `backend/docs/specs/cccbdb_importer.md`.
- No computed method/basis page parsing — Phase 3.

## How to run the tests

From the repo root:

```bash
conda run -n tckdb_env pytest backend/tests/importers/cccbdb -v
```

The default test set is fully offline and runs against the bundled
HTML fixtures in `backend/app/importers/cccbdb/fixtures/`.

To opt into the (optional) live smoke tests against the public
CCCBDB site:

```bash
TCKDB_CCCBDB_LIVE_TESTS=1 conda run -n tckdb_env \
    pytest backend/tests/importers/cccbdb/test_live_cccbdb_smoke.py -v
```

These live tests use a clear User-Agent, short timeouts, no retries
past one conservative attempt, do not write downloaded HTML back into
the repo, and assert only broad parser invariants. They exist for
local parser-drift checking, not for reproducible CI validation.

## Why fixtures instead of live requests

1. **Determinism.** CI must not depend on `cccbdb.nist.gov` being
   reachable, fast, or unchanged.
2. **Politeness.** The pilot deliberately limits CCCBDB access; live
   testing in CI would mean hundreds of repeat fetches per week.
3. **Coverage.** The fixtures cover combinations the live site does
   not always serve together (e.g. unit conversions, missing
   uncertainty cells), which would otherwise require fragile online
   selection.
4. **Reproducibility.** A fixture's SHA256 is a stable identity. A
   live page's identity drifts with NIST's release schedule.

## Phase 2a — payload builders

Phase 2a adds [builders/](builders/) that map Phase 1 records onto
existing TCKDB upload schemas. Builders are pure: no DB writes, no
network, no ORM dependencies.

Top-level entry point:

```python
from app.importers.cccbdb.parsers import parse_experimental_species_page
from app.importers.cccbdb.builders import build_experimental_species_payload

record = parse_experimental_species_page(html, source_url=url)
result = build_experimental_species_payload(record)
# result.species_entry_payload     -> SpeciesEntryIdentityPayload dict (or None / partial)
# result.thermo_payload            -> ThermoUploadRequest dict (or None)
# result.statmech_payload          -> StatmechUploadRequest dict (or None)
# result.geometry_payload          -> GeometryPayload dict (or None)
# result.external_source           -> CCCBDB-level provenance + per-value refs + unparsed
# result.warnings                  -> human-readable notes about unmapped values
```

### What maps to first-class TCKDB fields

| Phase 1 value             | First-class TCKDB field                            |
|---------------------------|----------------------------------------------------|
| `identity.smiles`         | `SpeciesEntryIdentityPayload.smiles`               |
| `identity.charge`         | `SpeciesEntryIdentityPayload.charge`               |
| `identity.multiplicity`   | `SpeciesEntryIdentityPayload.multiplicity`         |
| `identity.state_label`    | `SpeciesEntryIdentityPayload.term_symbol_raw`      |
| `thermo.hf_298`           | `ThermoUploadRequest.h298_kj_mol`                  |
| `thermo.hf_298.uncertainty` | `ThermoUploadRequest.h298_uncertainty_kj_mol`    |
| `thermo.s_298`            | `ThermoUploadRequest.s298_j_mol_k`                 |
| `thermo.s_298.uncertainty`| `ThermoUploadRequest.s298_uncertainty_j_mol_k`     |
| `thermo.cp_298`           | `ThermoPointCreate.cp_j_mol_k` (at T=298.15 K)     |
| `thermo.h_298_minus_h_0`  | `ThermoPointCreate.h_kj_mol` (at T=298.15 K)       |
| `statmech.point_group`    | `StatmechUploadRequest.point_group`                |
| `statmech.symmetry_number`| `StatmechUploadRequest.external_symmetry`          |
| `geometry.atoms`          | `GeometryPayload.xyz_text` (formatted)             |

Every product payload is stamped with `scientific_origin = "experimental"`.

### What remains as metadata/warnings

These Phase 1 values have no clean first-class home on the existing
upload schemas. The builder preserves each in
`result.external_source.unparsed` and emits a corresponding
`result.warnings` entry pointing at the side-channel key:

- `thermo.hf_0` — `ThermoUploadRequest` has no Hf(0 K) field.
- `statmech.frequencies` — experimental vibrational modes have no
  TCKDB destination yet. They are **not** routed into
  `calc_freq_mode`: that table is calculation-scoped, and creating a
  placeholder `Calculation` row just to host experimental data would
  violate the spec's "no fake calculations" rule.
- `statmech.rotational_constants` — no first-class A/B/C fields on
  `statmech` (see Schema Gap 3 in
  `backend/docs/specs/cccbdb_importer.md`).
- `statmech.zpe_kj_mol` — no experimental ZPE field.

Per-value reference labels (`Gurvich`, `TRC`, `Pedley`, …) also have
no per-value home on `ThermoUploadRequest`/`ThermoPointCreate`, so
they are kept in `result.external_source.per_value_references`,
keyed by the Phase 1 `property_kind` token.

### Why no DB writes yet

The schema gaps above need to be closed before some imported values
have first-class destinations. Until they are, the builders produce
*validated* dicts for the parts that fit cleanly and *structured
side-channel* data for the rest. A future upload-workflow step can
consume the dicts; the side-channel data informs the schema-gap
migrations.

### How to run builder tests

```bash
conda run -n tckdb_env pytest backend/tests/importers/cccbdb -v
```

The builder tests include schema-validation checks that instantiate
the real `ThermoUploadRequest`, `StatmechUploadRequest`, and
`SpeciesEntryIdentityPayload` against the built dicts. If the
upload schemas ever drift away from what the importer produces,
those tests fail loudly.

## Phase 2b — snapshot archive

The hand-authored fixtures under [fixtures/](fixtures/) are great for
unit tests but they are *our* tables, not NIST's. The
[snapshot.py](snapshot.py) command captures the actual CCCBDB pages
into a durable, content-addressed archive so payloads can be
regenerated later even if the website changes or disappears.

### Why raw snapshots are the durable source of truth

```
CCCBDB page  ─→  raw_html/*.html    ←── content-addressed by SHA256;
                                        the immutable artifact NIST
                                        actually served us.

raw_html  ─→  parsed/*.json         ←── Phase 1 parser output;
                                        regeneratable from raw_html.

parsed    ─→  payloads/*.json       ←── Phase 2a builder output;
                                        regeneratable from parsed.

payloads  ─→  TCKDB DB rows         ←── Phase 3+ upload workflow
                                        (does not exist yet).
```

Each downstream artifact is **regeneratable** from the previous one.
Only the raw HTML must be archived; everything else is a function of
it plus a versioned parser/builder. A future schema change or parser
fix can be applied by re-running the snapshot's `--write-payloads`
step without re-fetching CCCBDB.

### How to run a 3-page snapshot

```bash
conda run -n tckdb_env python -m scripts.cccbdb_snapshot \
    --output-dir data/external/cccbdb \
    --pilot experimental \
    --write-payloads \
    --sleep-seconds 2
```

This fetches H2, H2O, and benzene (with a 2-second pause between
requests), writes `data/external/cccbdb/raw_html/`,
`data/external/cccbdb/parsed/`, and `data/external/cccbdb/payloads/`,
and emits a deterministic `data/external/cccbdb/manifest.json`.

Useful flags:

| Flag | Effect |
|---|---|
| `--dry-run` | Compute everything in memory; write nothing. |
| `--force-refresh` | Re-fetch raw HTML even when cached. |
| `--max-pages N` | Cap the snapshot to the first `N` targets. |
| `--sleep-seconds S` | Polite gap between fetches (default 2.0). |
| `--timeout-seconds S` | Per-request timeout (default 20.0). |
| `--strict` | Exit nonzero if any target had a parser/builder error. |

### How to regenerate payloads from saved snapshots

Re-running the command without `--force-refresh` reuses cached
`raw_html/*.html` for every species already on disk:

```bash
# Re-build payloads with an updated parser/builder, no re-fetch:
conda run -n tckdb_env python -m scripts.cccbdb_snapshot \
    --output-dir data/external/cccbdb \
    --pilot experimental \
    --write-payloads
```

The runner re-hashes the cached HTML, re-runs the Phase 1 parser and
Phase 2a builder, and rewrites `parsed/` and `payloads/` (and the
manifest) with the latest parser/builder versions.

### Manifest shape

`manifest.json` records, per target:

```text
species_key, page_kind, source_url, source_record_key,
retrieved_at, http_status, content_sha256,
raw_html_path, parsed_json_path, payload_json_path,
parser_warnings, builder_warnings, fetch_warnings,
parser_error, builder_error, cache_hit
```

Plus top-level fields: `source`, `source_release`,
`source_database_doi`, `snapshot_version`, `created_at`,
`parser_version`, `builder_version`. All paths are relative to the
archive root, so an archive can be moved or zipped without rewriting.

### Failure handling

The runner is deliberately tolerant:

- A **fetch failure** records `fetch_warnings` and skips parser/builder
  steps for that target.
- A **parser failure** still saves raw HTML and records `parser_error`
  in the manifest, so the bad page can be diagnosed offline later.
- A **builder failure** still saves raw HTML and parsed JSON.
- The runner exits nonzero only when every target failed, or when
  `--strict` is passed and any record had an error.

### URL contract and the unverified-URL guard

CCCBDB does **not** expose stable per-species GET URLs for the
single-molecule data flow. ``exp1x.asp`` is a POST form whose results
are served via server-side session state, not a query string:

* `exp1x.asp?formula=H2O` returns the form, not data.
* `exp1x.asp?casno=...` is unrecognized; Cloudflare returns 1015
  ("rate limited" — but really "URL pattern not served") even for a
  single request.

So every per-species ``CrawlTarget`` in [crawl_plan.py](crawl_plan.py)
carries an explicit ``is_validated_url`` flag, defaulting to
``False``. The snapshot CLI refuses to live-fetch any unvalidated URL
unless ``--allow-unverified-urls`` is passed, returning exit code 2
with a precise error listing each offending target. Tests with
injected ``FixtureFetcher`` bypass the guard.

The ``experimental-properties`` pilot (Phase 3a, below) sidesteps this
problem entirely by using cross-species flat-table URLs that don't
need session state, so its targets are marked
``is_validated_url=True`` and run unguarded.

## Phase 3a — cross-species property tables

[crawl_plan.py](crawl_plan.py) exposes a second pilot,
``experimental-properties``, that targets CCCBDB's flat property-table
pages (one URL = one wide table = many species rows for one
property). These URLs are empirically verified single-GET resources
(WebFetch survey, May 2026):

| URL | property_kind | Units | Columns |
|---|---|---|---|
| `hf0kx.asp` | `hf_0` | kJ/mol | Species, Name, Hfg 0K, Reference, DOI |
| `goodlistx.asp` | `hf_0_with_uncertainty` | kJ/mol | Element, Species, Enthalpy 0K, unc |
| `diplistx.asp` | `dipole` | Debye | Molecule, name, state, x, y, z, tot, squib, comment |
| `expdiatomicsx.asp` | `diatomic_spectroscopic` | cm⁻¹ | Species, name, ωe, ωexe, ωeye, Be, De, αe, reference |

> `inchix.asp` is **molecule catalog only**. Its links are not trusted
> as property-table URLs — every property-table target above is
> explicitly allowlisted with a confirmed URL pattern.

Run the property pilot with:

```bash
conda run -n tckdb_env python -m scripts.cccbdb_snapshot \
    --output-dir data/external/cccbdb \
    --pilot experimental-properties
```

Files in the archive use the ``property_<key>_<sha12>.html`` /
``property_<key>_<sha12>.json`` prefix so they coexist with the
``experimental_<species>_…`` per-species files without collision.

### Parser architecture

[parsers/experimental_property_table.py](parsers/experimental_property_table.py)
ships **one** generic flat-table parser. It locates the largest
``<table>`` on the page, extracts the header + rows into a generic
``(column_names, rows-as-strings)`` structure, and a per-property
:class:`PropertyTableConfig` maps the raw columns onto
:class:`CCCBDBExperimentalPropertyRow` fields. Adding a new property
table is a two-line change: a config entry keyed by ``property_kind``
plus a ``CrawlTarget`` in [crawl_plan.py](crawl_plan.py). No new
parser code.

Per-row references and any non-first-class columns (x/y/z dipole
components, ωexe / Be / De / αe for diatomics, the DOI column on
`hf0kx.asp`, …) survive in ``CCCBDBExperimentalPropertyRow.raw_row``
and ``.reference`` so downstream code can lift them later without
re-fetching.

### Why property-table payloads aren't built yet

Phase 2a payload builders target per-species `Thermo` / `Statmech` /
`Geometry` upload schemas. Cross-species property rows need a
different upload destination — a ``molecular_property_observation``
table that's still a Schema Gap (see [Phase 0 spec §7](../../../docs/specs/cccbdb_importer.md#schema-gaps)).
Until that lands, the property-table snapshot **stops at parsed JSON**:
running with ``--write-payloads`` skips builder generation for these
targets and records a ``builder_warnings`` entry pointing at the gap.

### Why session-aware POST is deferred

Reaching CCCBDB's per-species data would require a session-aware
fetcher (POST the formula form, follow `Set-Cookie` headers, GET the
data page). That's significant work and the property tables already
give us most of the high-value experimental data we need without it.
Per-species POST fetching is intentionally deferred to a later phase.

## Phase 3b — molecule catalog (identity universe)

`inchix.asp` lists every molecule CCCBDB knows about with
formula / name / InChI / InChIKey / SMILES / CAS identifiers. It is
the **identity universe** for the catalog — what CCCBDB *can*
describe, independent of which property pages actually serve data
for any given molecule.

Run the catalog snapshot with:

```bash
conda run -n tckdb_env python -m scripts.cccbdb_snapshot \
    --output-dir data/external/cccbdb \
    --pilot catalog
```

Files land under `raw_html/catalog_inchix_<sha12>.html` and
`parsed/catalog_inchix_<sha12>.json`. No payload files — catalog
entries are not TCKDB upload payloads. Manifest records
`page_kind="molecule_catalog_inchi_index"`.

### `inchix.asp` is catalog-only — its links are not data URLs

**Important policy.** The hyperlinks inside `inchix.asp` rows are
**preserved as raw audit metadata only**. Each
`CCCBDBCatalogEntry.raw_href` keeps whatever the row pointed at —
but the importer never trusts that href as a data URL.

- `trusted_property_url` and `trusted_species_url` on
  `CCCBDBCatalogEntry` are **always `None`** in Phase 3b. They are
  reserved for a future search/form resolver.
- The Phase 2b confirmation that `exp1x.asp?casno=…` URL patterns
  don't resolve still stands. Constructing a property URL from a
  catalog href would just produce another Cloudflare 1015.
- Actual data retrieval continues to use either (a) the cross-species
  property-table URLs from Phase 3a, or (b) a future search/form
  resolver (placeholder in `parsers/molecule_catalog.py` —
  `resolve_species_data_page_from_search` deliberately raises
  `NotImplementedError`).

If an href looks like an absolute URL or a non-CCCBDB target, the
parser emits an audit warning on the entry. The warning is informational
only — the parser never fetches the href.

### Catalog-based identity enrichment for property rows

[enrichment.py](enrichment.py) exposes
`propose_catalog_matches(property_row, catalog)` which returns a
list of `CCCBDBCatalogMatch` candidates. It is **candidate proposal,
not identity resolution**:

| Property-row signal | Score | Unambiguous? |
|---|---|---|
| formula + name both match | `high` | iff only candidate |
| formula match + name alias/substring | `medium` | iff only candidate |
| formula match only, unique catalog formula | `low` | yes |
| formula match only, isomers in catalog | `low` | **no** (warning) |
| name match only, unique | `medium` | yes |
| name match only, non-unique | `low` | no |
| conflicting formula | not returned | n/a |

Rules baked into the helper:

- **Formula-only matches with multiple isomers in the catalog are
  always ambiguous, regardless of score.** C2H6O is ethanol *or*
  dimethyl ether; C3H6 is propene *or* cyclopropane; C4H10 is
  n-butane *or* isobutane. Silently picking one would be a
  correctness bug.
- The original property row is never mutated.
- Ambiguous candidates are never dropped — callers receive them all
  with a warning and decide whether to trust any of them.
- `is_unambiguous=True` only when the candidate is the single match
  or the single high-confidence match in the proposal set.

### Why no payloads for catalog entries

Catalog entries are identifiers, not science. They have no
`Thermo` / `Statmech` / `Geometry` upload destination. They feed
identity-resolution decisions for *other* records that do produce
upload payloads.

## Phase 4a — `molecular_property_observation` + property-table builder

Phase 4a closes CCCBDB **Schema Gap 1**. The new
`molecular_property_observation` table is the first-class home for
scalar / vector / tensor molecular properties — dipoles, IE/EA/PA,
HOMO/LUMO, atomization energies, enthalpies of formation,
spectroscopic constants, etc. These do **not** belong on
`transport` (which is Lennard-Jones / collision data) and do **not**
belong on `thermo` / `statmech` either.

### Schema

The model lives at
[app/db/models/molecular_property_observation.py](../../db/models/molecular_property_observation.py).
The Alembic migration is
[alembic/versions/a1b2c3d4e5f6_add_molecular_property_observation.py](../../../alembic/versions/a1b2c3d4e5f6_add_molecular_property_observation.py)
— an **additive** revision on top of `d861dfd60891`. This is a
deliberate policy departure from the historical
"single initial migration" rule: from Phase 4a onward, additive
schema changes ship as discrete revisions rather than folding into
the initial migration. The historical rule still applies to changes
that would modify existing tables; only purely additive changes
qualify for the new path.

Key design choices:

- `species_entry_id` is **nullable**. CCCBDB property rows only have
  formula+name; catalog enrichment is often ambiguous (isomers).
  Forcing a non-null FK would push the importer into fabricating
  species entries, which is worse than carrying an
  identity-unresolved row.
- Scalars get first-class columns. Vectors/tensors live in JSONB —
  pragmatic given current CCCBDB inputs, forward-compatible for SI
  promotion later.
- External-source provenance has dedicated columns
  (`external_source_*`) rather than a side-table, so an observation
  is self-describing for replay from a wiped archive.
- The dedupe `UniqueConstraint` uses
  `postgresql_nulls_not_distinct=True` so unresolved rows still
  dedupe by content + source + reference.

### Property-kind mapping

| Phase 3a `property_kind` | `MolecularPropertyKind` |
|---|---|
| `hf_0` | `enthalpy_of_formation` ⚠ NOT `atomization_energy` |
| `hf_0_with_uncertainty` | `enthalpy_of_formation` |
| `dipole` | `dipole_moment` |
| `diatomic_spectroscopic` | `spectroscopic_constant` |

`hf_0` is **enthalpy of formation at 0 K** — confusing it with
atomization energy would be a real scientific bug. Unknown
`property_kind` tokens fall through to
`MolecularPropertyKind.other` with the raw token preserved as
`property_label`.

### Identity enrichment policy

`build_molecular_property_payloads_from_property_table(table, catalog=...)`
calls the Phase 3b enrichment helper. Identity gets surfaced *only*
when the catalog produces an unambiguous match:

- **Unambiguous match**: identity hint (formula/name/InChI/InChIKey/
  SMILES) is attached to `raw_payload_json["identity_hint"]`.
  `species_entry_id` is **still left unset** — translating a catalog
  identifier into a real `species_entry_id` is the workflow layer's
  job, gated on dedup against existing rows.
- **Ambiguous match** (isomers): no identity hint surfaces. The
  candidate list is preserved in
  `raw_payload_json["catalog_candidates"]` with per-candidate
  scores and a row-level warning naming each isomer.
- **No match**: identity stays at `formula` / `name` in the raw
  payload; identity hint is `None`.

`raw_href` from catalog entries is **never** used. Phase 3b's policy
applies all the way through.

### Why no upload route yet

Phase 4a ships the schema, the Pydantic model, and the
property-table → payload builder. It does **not** add an upload
route or persist anything. The builder returns
`CCCBDBMolecularPropertyBuildResult.payload` (a
`MolecularPropertyObservationCreate` ready for the workflow layer)
plus identity hints and warnings; an upload service is a separate
Phase 4b decision.

## Phase 5a — direct-CAS per-species snapshots

The resolver diagnostic (previous section) found that
`alldata2x.asp?casno=<digits>` returns a real `molecule_data_page`
for **4 of 5** pilot molecules, while form-POST strategies all hit
Cloudflare. The Phase 5a snapshot mode exploits that finding —
carefully.

Run with:

```bash
conda run -n tckdb_env python -m scripts.cccbdb_snapshot \
    --output-dir data/external/cccbdb \
    --pilot species-alldata-cas \
    --sleep-seconds 5
```

### What "carefully" means: the classification gate

`alldata2x.asp?casno=...` has a silent-failure mode. For some CAS
numbers (1 in 5 in the live diagnostic) it returns HTTP 200 with
the formula-entry form served from `exp1x.asp` after a redirect.
The response *looks* fine until you read the body.

So the runner does not blindly save HTML for `species_all_data`
targets. Every response is passed through
[`classify_html`](diagnostics/classifier.py) and **only**
`molecule_data_page` responses are accepted as data:

| Classification | `accepted_as_data` | `raw_html` written? |
|---|---|---|
| `molecule_data_page` | `True` | yes — `raw_html/species_alldata_<key>_<sha>.html` |
| `redirect_landing_page` | `False` | no (optionally `rejected_html/` with `--save-rejected-html`) |
| `formula_entry_page` | `False` | no |
| `rate_limit_or_error_page` | `False` | no |
| `unknown` | `False` | no |
| (any other page kind) | `None` (gate not invoked) | yes |

Rejected responses still produce a manifest record with
`classification`, `classification_reason`, `final_url`, and a
`resolver_warnings` entry — so the failure is forensic, not
silent.

### Archive layout

```
data/external/cccbdb/
├── manifest.json
├── raw_html/
│   └── species_alldata_<species_key>_<sha12>.html      (accepted only)
├── rejected_html/                                       (--save-rejected-html only)
│   └── species_alldata_<species_key>_<sha12>.html
└── parsed/
    └── species_alldata_<species_key>_<sha12>.json
```

The `species_alldata_` prefix keeps these files distinct from
`experimental_*`, `property_*`, and `catalog_*`. Cache lookups are
page-kind-aware; tests pin the non-collision invariant in all three
directions.

### Parser scope

The Phase 5a parser is **triage-only**: title, ordered section
headings, and any InChI / InChIKey / SMILES / formula / molecule
name the regex sniffs can find in the body text. Full thermochemistry
/ geometry / vibrational extraction is deferred. The durable artifact
is the raw HTML; the parsed JSON is a fast-lookup convenience and a
record of what the parser could see.

### Policy contract

- `inchix.asp` hyperlinks remain catalog-only and untrusted
  (Phase 3b).
- Session-aware form POST stays deferred: the live diagnostic showed
  `exp1x_form_post` triggering rate-limit responses on every pilot
  molecule.
- The direct-CAS URL is only used when an explicit `cas_number` is
  supplied on the `CrawlTarget`.
- Catalog `raw_href` values are never used to construct
  `species_all_data` URLs.
- `species_all_data` snapshots are raw archival inputs, not TCKDB
  upload payloads. No `MolecularPropertyObservationCreate` (or
  any other upload schema) is built from them in this phase.

## Browser-assisted species-page import

Scripted CCCBDB fetches don't reliably reach per-species data:

- `alldata2x.asp?casno=...` may serve the formula-entry form
  (the bug the Phase 5b classifier hardening protects against).
- `exp1x.asp` / `alldata1x.asp` are themselves the form pages.
- `getformx.asp` is the real form workflow but Python `requests`
  triggers Cloudflare rate-limit / bot-detection behavior.

A human in a browser, however, gets a real per-species page. The
**browser-assisted importer** lets a maintainer save the rendered
HTML from their browser and import that local file into the standard
CCCBDB archive — same `raw_html/` layout, same classifier gate,
same parsed JSON output. No network at all.

### How to manually save a page

1. Open the per-species page in your browser (e.g. find H2O via
   CCCBDB's normal navigation flow).
2. **Right-click → Save Page As → HTML, complete** (or the
   browser-specific equivalent).
3. Note the final URL from the browser address bar — that's the
   `--source-url` you'll pass.

### How to import the saved file

```bash
conda run -n tckdb_env python -m scripts.cccbdb_import_saved_species_page \
    --input-html /tmp/h2o_saved.html \
    --output-dir data/external/cccbdb \
    --species-key h2o \
    --source-url "https://cccbdb.nist.gov/<page url from browser>" \
    --cas-number 7732-18-5 \
    --note "Saved via Firefox manual lookup"
```

What happens:

1. The HTML is classified using the same hardened classifier the
   regular snapshot uses. **No file is written to `raw_html/`**
   if the HTML is a formula-entry / rate-limit / redirect-landing
   page — the gate still applies.
2. Accepted pages are copied to
   `raw_html/species_alldata_<key>_<sha12>.html`, parsed into
   `parsed/species_alldata_<key>_<sha12>.json`, and a manifest
   record is appended with `resolver_strategy="browser_saved_html"`.
3. Re-importing the exact same content is **idempotent** — dedupe
   key is `(species_key, page_kind, content_sha256)`. A fresh save
   of the same species (different SHA) appends a new record
   alongside.
4. Rejected pages produce a manifest record but no `raw_html/`
   entry. With `--save-rejected-html` the body is copied to
   `rejected_html/` for forensic inspection.

### Optional flags

| Flag | Effect |
|---|---|
| `--allow-unknown` | Accept `Classification.unknown` responses as data — escape hatch for pages that lack strict identifier patterns. |
| `--save-rejected-html` | Copy rejected bodies to `rejected_html/` (off by default). |
| `--note "..."` | Free-text note recorded on the manifest record. |
| `--resolver-strategy ...` | Override `browser_saved_html` (e.g. if you used a session-aware curl recipe). |

### Why this avoids the legacy-link and Cloudflare problems

Scripted fetches give CCCBDB three things that may flag them as
bot traffic: the User-Agent, the absence of browser fingerprint
state, and the lack of session cookies from the form submission
flow. The browser already supplies all three. Importing the
already-rendered HTML keeps the archive's classifier gate +
provenance discipline while sidestepping the request layer
entirely.

### Classifier-hardening note (Phase 5b)

CCCBDB's formula-entry page carries a deceptively-similar title:

```
<TITLE>CCCBDB All data for one molecule</TITLE>
<H1>All data (experiment and calculated) for one species</H1>
<FORM ACTION = "getformx.asp" METHOD="post">
```

An earlier Phase 5a classifier accepted this page as
`molecule_data_page` because the heading + the bare word "CAS"
appearing in menu/form labels was enough. That bug is fixed:

- Formula-entry signals (any of `select a species by entering a
  chemical formula`, `getformx.asp`, `name="formula"`, `rules for
  chemical formula`) **outrank** molecule-data signals.
- `molecule_data_page` now requires evidence of a **populated
  identifier value** in the body — a real `InChI=…` string, a real
  InChIKey (14-10-1 caps), or a real CAS-number pattern
  (`\d{2,7}-\d{2}-\d`). The bare label "CAS" no longer fires.

#### Finding bad archived pages

If a snapshot was run before the hardening landed, contaminated
`raw_html/` pages may exist. Manual sweep:

```bash
# Find raw species_alldata pages that are actually formula-entry forms
grep -RIl "Select a species by entering a chemical formula" \
  data/external/cccbdb/raw_html/species_alldata_*.html

# Also worth checking:
grep -RIl "getformx.asp" \
  data/external/cccbdb/raw_html/species_alldata_*.html
```

Any match is a bad archive — delete or move out, then re-run the
snapshot. The fixed classifier will route it to `rejected_html/`
(or drop it entirely without `--save-rejected-html`).


## Resolver diagnostics

CCCBDB's per-species data flow is not what its URL patterns suggest:

- `inchix.asp` hyperlinks are catalog metadata only — Phase 3b
  preserves them as `raw_href` for audit and forbids using them as
  data URLs.
- `alldata2x.asp?casno=…` URLs look like direct per-species GETs but
  empirically redirect to the formula-entry form (`exp1x.asp`); the
  `casno` query parameter is not honored.
- A manual browser lookup works — which means the real path is a
  form/session workflow (POST + ASP cookie), not a query-string GET.

[`app/importers/cccbdb/diagnostics/`](diagnostics/) is a small,
opt-in debugging tool that characterizes what CCCBDB actually
returns for a handful of probe URLs and form submissions, **before**
anyone implements a session-aware resolver. It is deliberately not
production crawling and never writes a snapshot.

Run with:

```bash
conda run -n tckdb_env python -m scripts.cccbdb_resolver_diagnostics \
    --output-json /tmp/cccbdb_resolver_diagnostics.json \
    --sleep-seconds 2
```

What it does:

1. Fetches `exp1x.asp` once and discovers its `<form>` (action,
   method, hidden fields, named inputs, select options).
2. For each of the 5 hardcoded probe molecules (H2O, H2, CH4,
   benzene, ethanol), runs up to four strategies, sharing one
   `requests.Session` so cookies persist:
   - `direct_alldata2x_casno` — GET `alldata2x.asp?casno=...`
   - `exp1x_get_with_formula` — GET `exp1x.asp?formula=...`
   - `exp1x_form_post` — POST the discovered form with the formula
     in the discovered text input + every hidden field's default
   - `exp1x_form_post_with_name` — same, with the molecule name
3. Classifies each response into one of:
   `formula_entry_page`, `molecule_data_page`, `property_table_page`,
   `rate_limit_or_error_page`, `redirect_landing_page`, `unknown`.
4. Writes a JSON report with attempted URL, final URL after
   redirects, classification, content SHA256, page title, and a
   short diagnostic reason.

Live runs hit `cccbdb.nist.gov`; the script is **never** run in CI.
Offline tests under
[tests/importers/cccbdb/test_resolver_diagnostics.py](../../../tests/importers/cccbdb/test_resolver_diagnostics.py)
exercise the classifier, form discovery, and runner orchestration
with hand-rolled synthetic HTML and a fake transport.


### Why downloaded archives are ignored by git

`data/external/cccbdb/` is in the repository `.gitignore`. Hand-authored
sanitized fixtures under [fixtures/](fixtures/) remain tracked because
they back the unit tests and have a stable license story. Live NIST
HTML, by contrast, may carry implicit terms-of-use considerations and
churns with each CCCBDB release — checking it in would tie the repo's
state to NIST's release schedule and could mislead reviewers about
whose code is whose. The archive lives next to the repo, not inside it.

## Phase 2c — disk-payload round-trip validation

Phase 2c adds round-trip integration tests at
[backend/tests/importers/cccbdb/test_snapshot_payload_roundtrip.py](../../../tests/importers/cccbdb/test_snapshot_payload_roundtrip.py)
that run the snapshot end-to-end with `write_payloads=True`, read the
manifest from the temporary archive, open each `payload_json_path`
from disk, and validate the sub-payloads against the real upload
models:

| Sub-payload | Real model |
|---|---|
| `payload["species_entry_payload"]` | `tckdb_schemas.fragments.identity.SpeciesEntryIdentityPayload` |
| `payload["thermo_payload"]`        | `app.schemas.workflows.thermo_upload.ThermoUploadRequest`     |
| `payload["statmech_payload"]`      | `app.schemas.workflows.statmech_upload.StatmechUploadRequest` |
| `payload["geometry_payload"]`      | `tckdb_schemas.fragments.geometry.GeometryPayload`            |

This makes the archive **replayable after a DB wipe**: every payload
artifact on disk is an honest workflow-ready upload request (or
explicitly flagged otherwise — see below). The tests do not write to
the database and do not fetch CCCBDB.

### Validity gates for partial inputs

When a CCCBDB experimental page omits required identity fields
(e.g. H2 has no SMILES), the builder still emits the scientific
content (h298 / s298 / point group / per-value refs), but flips
explicit validity gates so the round-trip test knows to skip
strict validation:

```text
species_entry_payload_is_valid   # gates SpeciesEntryIdentityPayload
thermo_payload_is_valid          # gates ThermoUploadRequest (also needs species_entry)
statmech_payload_is_valid        # gates StatmechUploadRequest
```

When `*_is_valid=False`, the sub-payload is preserved on disk for
inspection but is not promised to satisfy the upload schema. A future
identity-resolution layer (InChIKey-based matching, manual SMILES
backfill, …) can re-build the payload from the parsed JSON without
re-fetching.

### Drift caught by the round-trip

The round-trip already caught two real issues during its first run:

1. The geometry builder shipped a `natoms` extra field that
   `GeometryPayload` (`extra="forbid"`) rejects. Fixed: atom count
   now lives only on line 0 of the XYZ block.
2. The thermo/statmech builders embedded a partial `species_entry`
   for species without SMILES, which produced a `ThermoUploadRequest`
   that fails validation. Fixed by adding `thermo_payload_is_valid`
   / `statmech_payload_is_valid` flags so the contract is explicit.

Both bugs were invisible to the Phase 2a in-memory tests because
those tests indexed into the built dicts directly. The on-disk
boundary catches what in-memory access skips.

### Where future builder/upload code goes

Phase 3 may add:

```text
backend/app/importers/cccbdb/builders/
    calculation_payload.py    # computed method/basis -> calculation upload
    property_payload.py       # dipole/IE/EA/PA/... (needs Schema Gap 1)
```

See `backend/docs/specs/cccbdb_importer.md` §9 for the full target
layout, and Schema Gaps §7 for the tables that need to land before
some builders can produce final payloads.
