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

## Where future builder/upload code goes

Phase 2 will add:

```text
backend/app/importers/cccbdb/builders/
    thermo_payload.py         # CCCBDBThermoRecord  -> thermo upload payload
    statmech_payload.py       # CCCBDBStatmechRecord -> statmech upload payload
    geometry_payload.py       # CCCBDBGeometryRecord -> conformer / geometry payload
```

Builders consume the Phase 1 intermediate records and emit
TCKDB **workflow-layer upload payloads** — they do not write to ORM
directly. See `backend/docs/specs/cccbdb_importer.md` §9 for the full
target layout, and Schema Gaps §7 for the tables that need to land
before some builders can produce final payloads.
