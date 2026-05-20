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

### Where future builder/upload code goes

Phase 2b/3 may add:

```text
backend/app/importers/cccbdb/builders/
    calculation_payload.py    # computed method/basis -> calculation upload
    property_payload.py       # dipole/IE/EA/PA/... (needs Schema Gap 1)
```

See `backend/docs/specs/cccbdb_importer.md` §9 for the full target
layout, and Schema Gaps §7 for the tables that need to land before
some builders can produce final payloads.
