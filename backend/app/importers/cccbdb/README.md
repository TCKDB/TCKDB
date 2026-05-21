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

## Dry-run payload exporter

The Phase 5c façade (`PropertyTableFetcher` + `PropertyTableParser` +
`PropertyTableIngestor`) gives you a path from raw HTML to
`MolecularPropertyObservationCreate` payloads, but it doesn't write
them anywhere. **Phase 5d adds a dry-run exporter** that runs the
pipeline end-to-end and dumps the payloads as JSON for offline
inspection:

```bash
conda run -n tckdb_env python -m scripts.cccbdb_property_payload_dryrun \
    --archive-dir data/external/cccbdb \
    --output-dir data/external/cccbdb/payloads_dryrun \
    --use-cache-only
```

Output layout (one file per property kind + an aggregate summary):

```
data/external/cccbdb/payloads_dryrun/
├── summary.json
├── hf_0.json
├── hf_0_with_uncertainty.json
├── dipole.json
├── diatomic_spectroscopic.json
└── polarizability_iso.json
```

Each per-target file has the shape:

```json
{
  "property_kind": "dipole",
  "source_url": "https://cccbdb.nist.gov/diplistx.asp",
  "detected_headers": ["Molecule", "name", "state", ...],
  "parsed_row_count": 4,
  "payload_count": 4,
  "invalid_payload_count": 0,
  "warning_count": 0,
  "skipped_missing_cache": false,
  "warnings": [],
  "payloads": [/* MolecularPropertyObservationCreate dicts */]
}
```

`summary.json` aggregates counts plus a `per_target` array and a
`warning_summary` map for at-a-glance inspection of which property
kinds need attention.

### How this differs from DB persistence

The dry-run **never writes to TCKDB**. Every emitted payload is a
`MolecularPropertyObservationCreate` dict that has round-tripped
through `model_dump(mode="json")` → `model_validate(...)`. A future
upload service can consume these dicts; persistence is a separate
concern. If a row's payload fails validation, the dry-run records
the row in `invalid_payload_count` + a warning and continues — it
never crashes the whole run.

### Why `species_entry_id` is always null

The ingestor never invents database IDs. When the catalog has an
unambiguous match for a row's `(formula, name)`, the InChI / InChIKey
/ SMILES go into `raw_payload_json["identity_hint"]`; when the match
is ambiguous (isomers like C2H6O), the candidates land in
`raw_payload_json["catalog_candidates"]` with per-row warnings. The
workflow layer that eventually persists these payloads is
responsible for translating an InChIKey hint into a real
`species_entry_id` (find an existing `species_entry` row by
InChIKey) — or leaving the row as identity-unresolved.

### How `identity_hint` is consumed later

A future upload service should:

1. Read `raw_payload_json["identity_hint"]["inchikey"]` from the
   payload.
2. Look up the matching `species_entry` row.
3. If found, set `species_entry_id` on the
   `MolecularPropertyObservationCreate` before persisting.
4. If not found, persist with `species_entry_id=None` and a
   `species_unresolved` warning — a maintainer can resolve later.

### Cache-only flag

`--use-cache-only` makes the exporter refuse to touch the network:

```bash
# Run against an already-warmed archive, no fetches
python -m scripts.cccbdb_property_payload_dryrun \
    --archive-dir data/external/cccbdb \
    --output-dir /tmp/dryrun-only \
    --use-cache-only
```

Targets whose raw HTML isn't already in `archive_dir/raw_html/` are
recorded as `skipped_missing_cache=True` with a warning. The run
still exits 0 — the goal is to inspect what's currently archived,
not to drive a fresh fetch.

### Inspecting the output

Useful one-liners (no live network):

```bash
# Which property kinds produced any payloads?
jq -r '.per_target[] | "\(.property_kind): \(.payload_count) payloads"' \
    data/external/cccbdb/payloads_dryrun/summary.json

# What detected headers does polarizability_iso surface?
# (catches column drift on pollistx.asp before it bites)
jq '.detected_headers' \
    data/external/cccbdb/payloads_dryrun/polarizability_iso.json

# Which rows had catalog-ambiguity warnings?
jq -r '.warnings[]' \
    data/external/cccbdb/payloads_dryrun/dipole.json | grep ambiguity
```

## Class-shaped property-table ingestion façade

Phase 3a shipped the cross-species property-table parser. Phase 4a
shipped the `molecular_property_observation` schema + the row → upload
builder. Phase 5c adds a small **façade module** that exposes the
three steps as composable classes — easier to plug into a pipeline,
easier to swap one step in a test:

```python
from app.importers.cccbdb.property_table_ingest import (
    PropertyTableFetcher,
    PropertyTableParser,
    PropertyTableIngestor,
    ingest_property_pilot,
)
```

| Class | Wraps | Job |
|---|---|---|
| `PropertyTableFetcher` | `run_snapshot` | Polite cache-first fetching for one or more property-table targets. Honors the existing rate-limit / dry-run / Cloudflare-aware policies. Default `sleep_seconds=5.0`. |
| `PropertyTableParser` | `parse_experimental_property_table_page` | Parses one already-fetched table into a `CCCBDBExperimentalPropertyTable`. Validates the target's `property_kind` is registered. |
| `PropertyTableIngestor` | `build_molecular_property_payloads_from_property_table` | Converts parsed rows into `MolecularPropertyObservationCreate` payloads. Carries an optional `catalog` for identity-hint enrichment. |
| `ingest_property_pilot()` | All three | One-shot end-to-end runner that fetches, parses, and ingests the full `EXPERIMENTAL_PROPERTIES_PILOT`. Returns aggregate `PilotIngestionResult` with `manifest`, `per_target`, `workflow_ready_count()`, `warning_summary()`. |

### Property-kind inventory

The allowlist now covers **6 cross-species experimental tables**:

| `property_kind` | URL | Units | `MolecularPropertyKind` | `workflow_ready` |
|---|---|---|---|---|
| `hf_0` | `hf0kx.asp` | kJ/mol | `enthalpy_of_formation` | true |
| `hf_0_with_uncertainty` | `goodlistx.asp` | kJ/mol (+ unc) | `enthalpy_of_formation` | true |
| `dipole` | `diplistx.asp` | Debye | `dipole_moment` | true |
| `diatomic_spectroscopic` | `expdiatomicsx.asp` | cm⁻¹ | `spectroscopic_constant` | true |
| `polarizability_iso` | `pollistx.asp` *(live-verified in 5d)* | Bohr³ | `polarizability_iso` | true |
| `quadrupole_moment` | `quadlistx.asp` *(new in 5e)* | Debye·Å | `quadrupole_moment` | **false** (parsed-only) |

#### Parsed-only (tensor-only) targets

`quadrupole_moment` is the project's first ``workflow_ready=False``
target. The page (`quadlistx.asp`) publishes only the diagonal
traceless tensor (`xx | yy | zz`) — there is no isotropic / "main"
scalar column. Collapsing the three components into a single number
would silently misrepresent the physics, so the builder emits NO
``MolecularPropertyObservationCreate`` payload. The parser still
captures every row in ``raw_row`` (xx/yy/zz/squib/comment), and the
dry-run reports `health="quarantined"` instead of `"unhealthy"`.

The full tensor channel for quadrupole observations is deferred —
either via a future tensor field on ``molecular_property_observation``
or via a dedicated quadrupole table.

#### Deferred experimental pages (CCCBDB form-only)

These pages exist on CCCBDB's Experimental index but cannot be
ingested by the property-table importer because they are FORM
pages — they post to ``getformx.asp`` and require session state to
return data, never a flat single-GET table:

| Page | Title | Reason deferred |
|---|---|---|
| `exprot1x.asp` | Experimental Rotational Constants | form-only |
| `expvibs1x.asp` | Experimental Vibrational Frequencies | form-only |
| `ea1x.asp` | Atomization Energies | form-only |
| `expgeom1x.asp` | Experimental Geometries | form-only |
| `expbondlengths1x.asp` | Internal Coordinates by type | form-only |
| `expangle1x.asp` | Bond angles | form-only |
| `exppg1x.asp` | Experimental Point Groups | form-only |
| `exptriatomicsx.asp` | Triatomics (vibrations) | form-only |
| `exprotbarx.asp` | Internal Rotation barriers | form-only |
| `exp1x.asp` / `xpx.asp` | One molecule / property browser | form-only |

The audit module reports these explicitly under
``form_only_deferred_links`` so the maintainer's "what's left" view
distinguishes "doable today" from "needs a session-aware POST
resolver first." See ``parsers/experimental_index.FORM_ONLY_HREFS``.

#### Additional flat-table candidates surfaced by the audit

Verified-flat but not yet configured (future work):

| URL | Title | Notes |
|---|---|---|
| `refstatex.asp` | Reference States | flat 26-row table; ``Element`` / ``Reference State`` / ``H(298)-H(0)`` / ``unc`` |
| `elecspinx.asp` | Electronic Spin Splitting Corrections | flat 86-row table; multi-unit row (cm⁻¹ / hartree / kJ mol⁻¹) needs unit-policy work |
| `diatomicexpbondx.asp` | Diatomic bond lengths | **matrix** layout (row = X element, col = Y element); not a flat row-per-observation shape — needs a separate parser |

The live `pollistx.asp` page header is
`Molecule | name | State | Conformation | alpha | squib | commment`
— the earlier Phase 5c inferred shape (`xx | yy | zz | iso | …`) was
extrapolated from the `diplistx.asp` sibling and was wrong. The
configured `value_column` is now `alpha`. If a live fetch reveals a
different column header again, update
[`PROPERTY_CONFIGS["polarizability_iso"]`](parsers/experimental_property_table.py)
— the parser itself is column-name-driven and doesn't need to change.

### `hf_0` vs `hf_0_with_uncertainty`

These are intentionally **both** kept in the pilot and are NOT
duplicates:

| Target | Source | Coverage | Uncertainty |
|---|---|---|---|
| `hf_0` | `hf0kx.asp` | all species CCCBDB has at 0 K (~450 rows) | none |
| `hf_0_with_uncertainty` | `goodlistx.asp` | "well-known" curated subset (~31 rows) | yes |

The goodlist is a curated subset of `hf_0`, not a superset — dropping
either target loses information. Both feed
`molecular_property_observation` as `enthalpy_of_formation` payloads;
the goodlist subset is distinguishable downstream by a non-null
`scalar_uncertainty`. (This decision is pinned by tests in
[`tests/importers/cccbdb/test_property_payload_dryrun.py::TestHfZeroOutcome`](../../../tests/importers/cccbdb/test_property_payload_dryrun.py).)

A May 2026 cache-lookup bug caused `hf_0` to silently parse against
the goodlist's HTML (the glob `property_hf_0_*.html` matched
`property_hf_0_with_uncertainty_*.html`); see the regex anchor in
[`snapshot._find_cached_raw_html`](snapshot.py).

## Experimental index discovery (`exp2x.asp` / `exp1x.asp`)

`https://cccbdb.nist.gov/exp2x.asp` (redirects to `exp1x.asp`) is
CCCBDB's canonical "Experimental" sub-menu. It is **not** a data
page — it is a *discovery page* that lists every experimental
property table the database advertises. We parse it to (a) verify
the URLs the importer fetches are still advertised by CCCBDB and
(b) surface new property tables that the pilot doesn't configure
yet.

* Parser: [`parsers/experimental_index.py`](parsers/experimental_index.py)
  → `ExperimentalIndex.links: list[ExperimentalIndexLink]`.
* Each link carries `section_path`, `label`, `href`,
  `absolute_url`, and a static `target_guess` (`property_kind`
  token) for the well-known data pages.
* The parser does **not** crawl the discovered links — it's
  read-only metadata for maintainer use.

### Running the property-config audit

```python
from pathlib import Path
from app.importers.cccbdb.parsers.experimental_index import parse_experimental_index_page
from app.importers.cccbdb.property_config_audit import audit_property_configs

html = Path("path/to/exp2x.html").read_text(encoding="utf-8")
index = parse_experimental_index_page(html, source_url="https://cccbdb.nist.gov/exp2x.asp")
audit = audit_property_configs(index)

print(audit.matched_targets)                  # configured AND advertised
print(audit.unmatched_configured_targets)     # configured URL has gone stale
print([link.label for link in audit.unconfigured_experimental_links])  # extension candidates
```

The audit is a diagnostic only; it never fails the run.

### Interpreting dry-run health

Each target's per-JSON file now carries a `health` field with one of
four values:

| `health` | meaning |
|---|---|
| `healthy` | parsed rows produced payloads (or zero rows seen) |
| `unhealthy` | `parsed_row_count > 0` AND `payload_count == 0` for a workflow-ready target — almost always a column-config drift |
| `quarantined` | same row/payload shape as unhealthy, but the target's `CrawlTarget.workflow_ready=False` so it's intentionally parsed-only (e.g. `quadrupole_moment`) |
| `skipped` | no cached HTML in the archive (only with `--use-cache-only`) |

The aggregate `summary.json` exposes `unhealthy_count`,
`quarantined_count`, and a `health_summary` map keyed by
`property_kind`. The default pilot should always be
`unhealthy_count == 0` — if it isn't, the CLI logs a WARNING with
the offending targets, but exit code stays `0` so individual
column drifts don't break automated workflows.

`workflow_ready=False` is a deliberate "we parsed it, but we don't
have a safe schema home for it yet" verdict. Reasons today:

* **Tensor properties** with no published scalar (e.g. `quadrupole_moment`).
  The components live in `raw_row`; the builder emits no payload.
* **Future-schema candidates** — properties (rotational constants,
  vibrational frequencies, multi-unit corrections) where the safe
  representation needs new schema work before payload-building can
  be reasoned about.

A `workflow_ready=False` target with `parsed_row_count > 0` and
`payload_count == 0` is the *expected* shape; the gate flags it as
`quarantined`, not `unhealthy`. Only flip `workflow_ready=True`
when the builder's emitted payloads are scientifically correct.

## Phase 6 — session-aware form-page resolver

The flat property-table importer cannot reach CCCBDB's form-only
experimental pages (`ea1x.asp`, `exprot1x.asp`, `expvibs1x.asp`, …).
Those pages POST to ``getformx.asp`` and require session cookies
from the entry-page GET to return data. Phase 6 adds a separate,
conservative resolver to drive that flow programmatically.

### Flat property pages vs form-only pages

| | Flat property table | Form-only experimental page |
|---|---|---|
| Example | `diplistx.asp`, `hf0kx.asp`, `quadlistx.asp` | `ea1x.asp`, `exprot1x.asp`, `expvibs1x.asp` |
| Transport | single GET | GET entry → POST `getformx.asp` (session cookies) |
| Coverage | many species per page | one (or a chosen) species per request |
| Handled by | `property_table_ingest.PropertyTableFetcher` | `form_resolver.run_form_resolver_queue` |
| Throughput | one page = many payloads | one page = one (or zero) payload |

### Why ``getformx.asp`` needs a session-aware resolver

CCCBDB's `<form action="getformx.asp">` carries no `prop=N` argument
in the POST body — the server reads the requested property from the
session state established by the GET on the entry page. A blind
POST against ``getformx.asp`` without an ``ASPSESSIONIDxxx`` cookie
either redirects to a formula-entry page or hits Cloudflare's
rate-limit gate. The resolver uses ``requests.Session`` to propagate
those cookies automatically.

### Creating a tiny form queue

The resolver consumes an explicit JSON queue file — no auto-expansion
from the catalog. Minimal shape:

```json
{
  "records": [
    {
      "species_key": "h2o",
      "formula": "H2O",
      "name": "Water",
      "target_kind": "atomization_energy",
      "entry_url": "https://cccbdb.nist.gov/ea1x.asp"
    }
  ]
}
```

Required fields: `species_key`, `formula`, `target_kind`,
`entry_url`. Optional: `name`, `cas_number`, `inchikey`.

### Running the resolver

```bash
conda run -n tckdb_env python -m scripts.cccbdb_resolve_form_page \
  --queue-file data/external/cccbdb/form_queue.json \
  --output-dir data/external/cccbdb \
  --max-pages 3 \
  --sleep-seconds 15 \
  --save-rejected-html
```

Flags:

| Flag | Default | Purpose |
|---|---|---|
| `--queue-file` | required | JSON queue (records list) |
| `--output-dir` | required | Archive root (raw_html/, parsed/, manifest.json) |
| `--max-pages` | 3 | Hard cap on records resolved per run |
| `--sleep-seconds` | 15 | Polite delay between POSTs |
| `--save-rejected-html` | off | Archive rejected pages under `rejected_html/` |
| `--allow-unknown` | off | Accept pages whose classification is `unknown` (diagnostic use) |
| `--user-agent` | TCKDB UA | Override the HTTP User-Agent |

The CLI exits `2` on bad config / unparseable queue, `0` otherwise.
Per-record verdicts land in `manifest.json` under the same key shape
as the property-snapshot manifest.

### Species-selection (`choosex.asp`) and selection policies

A formula like `C2H6O` matches multiple isomers (ethanol, dimethyl
ether). CCCBDB then redirects from ``getformx.asp`` to ``choosex.asp``
with a list of candidates and a ``<form action="fixchoicex.asp">``
checkbox table.

The resolver supports two policies, selected via
``--selection-policy``:

| Policy | Behavior |
|---|---|
| `reject-ambiguous` (default) | Any ``choosex.asp`` response is rejected without parsing candidates. Manifest records `selection_status="ambiguous_or_no_match"`. |
| `exact-match` | Parses candidate rows. Selects exactly one only if the queue record matches it unambiguously on `formula+name`, `formula+CAS`, or `formula+InChIKey`. Multiple candidates sharing the same `choice` value (e.g. two conformers of one species under the same CAS) count as a single selection. Otherwise rejects as ambiguous. |

Formula-only matching is NEVER allowed. The first candidate is
NEVER auto-picked.

#### Acceptable queue-side identity fields

| Match basis | Required fields |
|---|---|
| `formula+cas` (strongest) | `formula` + `cas_number` |
| `formula+name` | `formula` + `name` |
| `formula+inchikey` | `formula` + `inchikey` *(CCCBDB doesn't surface InChIKey on `choosex.asp` today; supported for forward-compatibility)* |

CAS canonicalization strips hyphens, so `"64-17-5"` and `"64175"`
both match.

#### Why formula alone is forbidden

Selecting by formula alone would always grab the first candidate
when CCCBDB serves multiple isomers — silently picking the wrong
molecule. Phase 7 enforces this at the matcher layer: even with
`--selection-policy exact-match`, a queue record without a name /
CAS / InChIKey rejects on every `choosex.asp` page.

#### Structural vs molecular formula handling

CCCBDB's `choosex.asp` candidate column shows the *structural*
formula (`CH3CH2OH`, `CH3OCH3`), but queue records typically carry
the *molecular* formula (`C2H6O`, the same string the maintainer
POSTed to `getformx.asp`). The matcher derives the Hill-system
molecular formula from each candidate's structural formula
(`CH3CH2OH` → `C2H6O`) and compares both literal and derived forms
on both sides. The maintainer can supply either form.

The derivation handles unparenthesized atomic chains only. For
formulas with parens / charges / dots, the matcher falls back to
literal string comparison.

#### Example queue records

Distinguish ethanol from dimethyl ether by name:

```json
{
  "records": [
    {
      "species_key": "ethanol",
      "formula": "C2H6O",
      "name": "Ethanol",
      "target_kind": "atomization_energy",
      "entry_url": "https://cccbdb.nist.gov/ea1x.asp"
    },
    {
      "species_key": "dimethyl_ether",
      "formula": "C2H6O",
      "name": "Dimethyl ether",
      "target_kind": "atomization_energy",
      "entry_url": "https://cccbdb.nist.gov/ea1x.asp"
    }
  ]
}
```

Or by CAS (preferred when names drift between CCCBDB releases):

```json
{
  "species_key": "ethanol",
  "formula": "C2H6O",
  "cas_number": "64-17-5",
  "target_kind": "atomization_energy",
  "entry_url": "https://cccbdb.nist.gov/ea1x.asp"
}
```

#### Running with exact-match selection

```bash
conda run -n tckdb_env python -m scripts.cccbdb_resolve_form_page \
  --queue-file data/external/cccbdb/form_queue.json \
  --output-dir data/external/cccbdb \
  --selection-policy exact-match \
  --max-pages 3 --sleep-seconds 15
```

The default remains `--selection-policy reject-ambiguous`. Opting
into `exact-match` is an explicit choice the maintainer makes when
they trust the queue's identity fields.

#### Manifest selection metadata

Every record that hit a `choosex.asp` page carries selection
metadata (whether accepted or rejected):

```json
{
  "selection_policy": "exact-match",
  "selection_status": "selected",
  "selection_match_basis": "formula+name",
  "selection_candidate_count": 3,
  "selected_name": "Ethanol",
  "selected_cas_number": "64175",
  "selection_warnings": []
}
```

`selection_status` values:

* `selected` — exact match found, fixchoicex.asp POSTed, follow-up
  page accepted.
* `ambiguous_or_no_match` — zero matches, multiple distinct-CAS
  matches, or `reject-ambiguous` policy.
* `no_candidates` — selection page parsed empty (page-shape drift).

### Why geometry / vibrations / rotational constants are parsed-only

Phase 6 supports exactly one `target_kind`:

```text
atomization_energy
```

The other form-only pages (`exprot1x.asp`, `expvibs1x.asp`,
`expgeom1x.asp`, …) return multi-row, multi-unit data that does NOT
fit cleanly into a single `MolecularPropertyObservation` scalar.
Forcing them would silently produce wrong observations. When their
schema home arrives (tensor channel for vibrations + rotational
constants, geometry sub-payload for structural data), each will get
its own per-target parser in `parsers/form_result.py`.

For now: if the resolver is asked for one of these `target_kind`s
and the page is accepted, the raw HTML is archived but no
`parsed/...json` file is written, and the manifest carries a
`parser_warnings` entry naming the unsupported target.

## Phase 8 — form-result payload builder

The form resolver writes per-target parsed JSON files under
``parsed/form_<target_kind>_<species_key>_<sha>.json``. Phase 8 adds a
builder that converts those parsed files into
``MolecularPropertyObservationCreate`` payloads — closing the loop
from form workflow to the same observation channel the flat
property-table builder feeds.

### Parsed JSON → payload pipeline

```
ea1x.asp form workflow
  → form resolver (Phase 6)
  → parsed/form_atomization_energy_<key>_<sha>.json
  → cccbdb_form_payload_dryrun.py
  → form_payloads_dryrun/atomization_energy.json
      with MolecularPropertyObservationCreate payloads inside.
```

The builder ([form_payload_builder.py](form_payload_builder.py))
exposes one supported target_kind in Phase 8:

* ``atomization_energy`` (CCCBDB ``ea2x.asp``)

Other supported_target_kinds parse fine but the builder skips them
with an "unsupported" warning until each one earns its own per-target
builder.

### Running the form-payload dry-run

```bash
conda run -n tckdb_env python -m scripts.cccbdb_form_payload_dryrun \
  --archive-dir data/external/cccbdb \
  --output-dir data/external/cccbdb/form_payloads_dryrun
```

Output:

```
form_payloads_dryrun/
  summary.json            # aggregate health + counts
  atomization_energy.json # per-target payloads + warnings
```

Health gate:

* ``healthy`` — every supported target either had no parsed files at
  all, or produced at least one workflow-ready payload.
* ``unhealthy`` — at least one target had parsed files but emitted
  zero payloads (silent-empty scenario; almost always a column
  drift or a row missing a 0 K value).

### Why ``atomization_energy`` uses 0 K as ``scalar_value``

CCCBDB's ``ea2x.asp`` reports two values per species: the
atomization energy at 0 K and at 298 K (both in kJ/mol). The
builder treats the 0 K value as the canonical scalar because:

* it matches the 0 K convention used by the flat ``hf0kx.asp`` /
  ``goodlistx.asp`` enthalpy tables;
* the 0 K value is the one CCCBDB reports without thermal
  contributions — purer experimental observation.

The 298 K value is preserved verbatim under
``raw_payload_json["secondary_values"]["298K"]``. If a future
workflow needs both temperatures as first-class observations, it
can derive a second `MolecularPropertyObservationCreate` from the
same row.

### Why ``temperature_k`` is ``None``

The ``MolecularPropertyObservationCreate`` schema validates
``temperature_k > 0``, so we cannot store the literal value
``0.0``. The 0 K condition is encoded via
``property_label="atomization_energy_0k"`` alongside
``temperature_k=None``. The label is the durable hint a downstream
consumer can dispatch on.

### How selection metadata is preserved

When the form resolver hit a ``choosex.asp`` selection page, its
verdict is written into the parsed JSON file's top-level
``selection`` field. The builder copies it verbatim into
``raw_payload_json["selection"]`` so every emitted payload carries
provenance back to the exact ``fixchoicex.asp`` POST that produced
it.

Shape:

```json
{
  "selection": {
    "selection_policy": "exact_match",
    "selection_status": "selected",
    "selection_match_basis": "formula+name",
    "selection_candidate_count": 3,
    "selected_name": "Ethanol",
    "selected_cas_number": "64175",
    "selection_warnings": []
  }
}
```

### Why ``species_entry_id`` remains ``null``

Phase 8 stops short of identity resolution. Every emitted payload
sets ``species_entry_id=None`` and surfaces identity hints inside
``raw_payload_json["identity_hint"]``:

```json
{
  "identity_hint": {
    "formula": "H2O",
    "name": "Water"
  }
}
```

The workflow layer (a separate phase) resolves these hints against
``species_entry`` rows in the database, gated on existing-row
dedup. Doing identity resolution inside the importer would
quietly create or mis-attach species rows — explicitly out of
scope.

### Skip rules

Rows the builder skips (with a warning, never an exception):

| Reason | Warning text |
|---|---|
| No numeric 0 K value | ``no numeric 0 K atomization energy on this row; payload not built`` |
| Missing unit | ``missing unit; payload not built`` |
| Pydantic validation failure | ``pydantic validation failed: ...`` |

Each skipped row contributes a per-target ``warnings`` entry in
``atomization_energy.json``; the dry-run health gate marks the
target ``unhealthy`` if every parsed file produces only skips.

## Phase 9 — DB import workflow

The first CCCBDB phase that touches the database. Reads validated
``MolecularPropertyObservationCreate`` payloads from both dry-run
lanes (flat property-table + form-result) and persists them
idempotently with conservative identity resolution.

### Where the payload JSON files come from

| Lane | Producer | Output |
|---|---|---|
| Flat property-table | `cccbdb_property_payload_dryrun.py` | `payloads_dryrun/<property_kind>.json` |
| Form-result | `cccbdb_form_payload_dryrun.py` | `form_payloads_dryrun/<target_kind>.json` |

Both produce JSON files with the same per-target shape — a
``"payloads"`` list whose entries validate against
``MolecularPropertyObservationCreate``. The import service consumes
either or both directories uniformly.

### Dry-run vs commit

By default the import is a **dry-run**: every payload is validated,
identity resolution runs against the current DB state, and would-be
inserts are counted, but the session rolls back at the end. No rows
are written.

```bash
# Dry-run (default)
conda run -n tckdb_env python -m scripts.cccbdb_import_molecular_property_payloads \
  --flat-payload-dir data/external/cccbdb/payloads_dryrun \
  --form-payload-dir data/external/cccbdb/form_payloads_dryrun
```

Pass ``--commit`` to actually persist:

```bash
conda run -n tckdb_env python -m scripts.cccbdb_import_molecular_property_payloads \
  --flat-payload-dir data/external/cccbdb/payloads_dryrun \
  --form-payload-dir data/external/cccbdb/form_payloads_dryrun \
  --commit
```

Supported flags: ``--flat-payload-dir``, ``--form-payload-dir``,
``--property-kind`` (repeatable), ``--commit``, ``--no-resolve-identity``,
``--created-by``, ``--fail-on-invalid``, ``--limit``, ``--summary-path``.

### Why unresolved ``species_entry_id`` is allowed

The DB schema declares
``molecular_property_observation.species_entry_id`` nullable for
exactly this reason: CCCBDB rows often arrive with at most a
formula and a name, and the catalog enrichment is frequently
ambiguous (isomers). Forcing a non-null FK would push the importer
into fabricating species entries — a worse outcome than carrying an
identity-unresolved observation with its CCCBDB provenance intact.

A row imported with ``species_entry_id=NULL`` is still
self-describing thanks to the ``external_source_*`` columns plus
``raw_payload_json["identity_hint"]``. A future curator (or a
later resolution pass) can UPDATE the FK when an unambiguous
match becomes available.

### What identity resolution is automatic

Only **exact InChIKey** match. The service:

1. Reads ``raw_payload_json["identity_hint"]["inchikey"]``.
2. Looks up the single ``Species`` row with that ``inchi_key``.
3. Finds the single compatible ``SpeciesEntry`` for that species
   (``kind=minimum`` + ``electronic_state_kind=ground``).
4. Sets ``species_entry_id`` only when both lookups return exactly
   one row.

Anything else (no match, multiple species sharing the key,
multiple compatible entries) leaves ``species_entry_id=NULL`` and
records the reason in the disposition's ``warnings``.

### What identity resolution is proposal-only

These fields are surfaced in the dispositions' warnings but never
auto-resolve:

* **formula** — too many species share a formula.
* **formula + name** — name normalization is brittle; high false-match risk.
* **CAS** — TCKDB has no normalized CAS-identity table today.
  When/if such a table arrives, the service can extend the
  ``_resolve_identity`` ``cas_number`` branch.
* **formula + name + …** combinations — same reasoning.

If you've manually curated an identity, populate ``species_entry_id``
on the payload itself before calling the service — the resolver
preserves any non-null value verbatim.

### Idempotency

A repeated import of the same payloads must NOT create duplicate
rows. Idempotency rides on the existing DB-level unique constraint
``mpo_dedupe_key`` (``postgresql_nulls_not_distinct=True``):

| Column | Role |
|---|---|
| species_entry_id | resolved or NULL |
| property_kind | atomization_energy, dipole_moment, … |
| scientific_origin | experimental |
| external_source_name | CCCBDB |
| external_source_release | "22" |
| external_source_url | per-target page URL |
| external_source_record_key | per-row identity |
| reference_label | row "squib" |
| scalar_value | per-row value |
| temperature_k | usually NULL |

The service pre-checks this key with ``IS NOT DISTINCT FROM`` for
each row, so duplicates are reported as
``action="duplicate"`` instead of failing the run. A race-condition
``IntegrityError`` on the unique constraint is caught and
also classified as ``duplicate``.

### How to inspect dispositions

The CLI prints the full result (including per-row dispositions) to
stdout as JSON, and optionally writes the same JSON to
``--summary-path``. A disposition looks like::

    {
      "property_kind": "atomization_energy",
      "property_label": "atomization_energy_0k",
      "external_source_record_key": "h2o",
      "identity_status": "resolved",
      "species_entry_id": 42,
      "action": "inserted",
      "warnings": [],
      "inchikey": "XLYOFNOQVPJJNP-UHFFFAOYSA-N",
      "source_path": "data/external/cccbdb/form_payloads_dryrun/atomization_energy.json"
    }

| ``identity_status`` | meaning |
|---|---|
| ``resolved`` | InChIKey matched exactly one compatible species_entry |
| ``unresolved`` | no InChIKey on the payload |
| ``ambiguous`` | InChIKey matched but >1 compatible species or species_entry |
| ``not_found`` | InChIKey present but no matching species in DB |
| ``skipped`` | invalid payload — not eligible for resolution |

| ``action`` | meaning |
|---|---|
| ``would_insert`` | dry-run: row would be inserted |
| ``inserted`` | commit: row was inserted |
| ``duplicate`` | dedupe key already exists |
| ``invalid`` | pydantic validation failed |
| ``skipped`` | insert failed for another reason; check ``warnings`` |

### How the experimental-index audit guides future allowlisting

The audit module is the single source of truth for "what's the next
page to consider." Its three output buckets answer different
questions:

| Bucket | Question it answers |
|---|---|
| `matched_targets` | "Which configured pages are still advertised?" — staleness check |
| `unmatched_configured_targets` | "Which configured URLs has CCCBDB removed?" — stale-URL alarm |
| `unconfigured_experimental_links` | "What does CCCBDB advertise that we don't yet parse?" — extension menu |
| `form_only_deferred_links` | "Of the unconfigured links, which are POST-only and need a future resolver?" — distinguishes "do later" from "do today" |

When picking the next target, prefer items in
`unconfigured_experimental_links` that are NOT in
`form_only_deferred_links`. Those are the flat single-GET pages
that the property-table importer can ingest with only a config + a
fixture — no new infrastructure.

### Adding a new property table

1. Confirm the URL is a flat single-GET resource (no session state).
2. Append a `PropertyTableConfig(...)` entry to `PROPERTY_CONFIGS` in
   [`parsers/experimental_property_table.py`](parsers/experimental_property_table.py).
3. Append a `CrawlTarget(..., property_kind=<token>, is_validated_url=True)`
   to `EXPERIMENTAL_PROPERTIES_PILOT` in
   [`crawl_plan.py`](crawl_plan.py).
4. If the property fits an existing `MolecularPropertyKind` enum
   value, register the mapping in `_PROPERTY_KIND_MAP` in
   [`builders/molecular_property_payload.py`](builders/molecular_property_payload.py);
   otherwise the row falls through to `MolecularPropertyKind.other`
   with the raw kind preserved as `property_label`.
5. Add a fixture under `fixtures/property_<kind>.html`.
6. Run `pytest backend/tests/importers/cccbdb -q`.

No new parser code. No new builder code. No schema migration unless
the property needs a new `MolecularPropertyKind` enum value.

### Properties intentionally NOT yet allowlisted

`ionization_energy`, `electron_affinity`, `proton_affinity`,
`atomization_energy`, `quadrupole_moment`, full-tensor
`polarizability`, `homo_energy`, `lumo_energy`, `homo_lumo_gap`,
`rotational_constant` — these `MolecularPropertyKind` enum values
exist in `common.py` but no `PropertyTableConfig` or `CrawlTarget` is
registered. The URLs are referenced in CCCBDB navigation but their
exact paths haven't been verified as flat single-fetch resources.
Add them following the recipe above when a maintainer has time to
verify each URL.

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
