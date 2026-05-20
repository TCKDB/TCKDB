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

So every ``CrawlTarget`` in [crawl_plan.py](crawl_plan.py) carries an
explicit ``is_validated_url`` flag, defaulting to ``False`` on the
placeholder URLs. The snapshot CLI refuses to live-fetch any
unvalidated URL unless ``--allow-unverified-urls`` is passed,
returning exit code 2 with a precise error listing each offending
target. Tests with injected ``FixtureFetcher`` bypass the guard.

To make the snapshot actually fetch live data, the next person will
need to either:

1. add a session-aware fetcher (POST the formula form, follow cookies
   to the data page), or
2. pivot to cross-species property tables such as
   ``xp1x.asp?prop=1`` (stable GET URLs but a different page kind
   that the current parser does not handle).

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
