# TCKDB Stage 0 representative staging-corpus manifest

**Versioned baseline:** source `00546f6`, 2026-07-30.
**Purpose:** a deterministic, no-production-write corpus contract for Stage 0
journey and claim evidence. This manifest points to repository fixtures and
seed assets; it does not assert that they already meet Stage 4 catalog-scale
SLOs.

## Corpus cases

| Case ID | Coverage category | Versioned input / fixture | Reproducible validation command | Supports Stage 0 journey / claim | Boundary |
|---|---|---|---|---|---|
| `api-contract` | Every documented HTTP operation | `backend/tests/api/golden/openapi.json` | `(cd backend && conda run -n tckdb_env pytest tests/api/test_openapi_snapshot.py -q)` | Public scientific query contract; client parity inventory | Source/CI only; production OpenAPI stays disabled. |
| `scientific-smoke` | Public scientific response integration | `backend/tests/smoke/test_scientific_read_api_smoke.py` | `(cd backend && conda run -n tckdb_env pytest tests/smoke/test_scientific_read_api_smoke.py -q)` | Explore scientific species/reaction/provenance records | Test DB fixture, not a scale benchmark. |
| `chemkin-roundtrip` | Species, thermo, kinetics, transport, CHEMKIN import/export | `backend/tests/integration/fixtures/{mech.inp,therm.dat,tran.dat,species_dictionary.txt}` | `(cd backend && conda run -n tckdb_env pytest tests/integration/test_chemkin_round_trip.py -q)` | Thermo/kinetics/transport ingestion and CHEMKIN convenience export | Does not establish public release packaging. |
| `rmg-ammonia-methane` | Multi-file RMG mechanism ingestion | `backend/tests/integration/fixtures/rmg_ammonia_methane/` | `(cd backend && conda run -n tckdb_env pytest tests/integration/test_chemkin_round_trip_real.py -q)` | Chemistry-first input normalization and provenance-bearing records | Bounded fixture, not a representative PDep pathway corpus. |
| `pdep-schema` | Network/PDep payload validation | `backend/tests/schemas/test_network_pdep_schema.py` | `(cd backend && conda run -n tckdb_env pytest tests/schemas/test_network_pdep_schema.py -q)` | Statement that network/PDep representations exist | Does not support microscopic pathway or state-specific reconstructibility claims. |
| `bundle-v0` | Authenticated thermo/kinetics contribution bundle | `docs/contribution-bundles/v0-format.md`; `backend/tests` contribution-bundle tests | `(cd backend && conda run -n tckdb_env pytest tests -k contribution_bundle -q)` | Narrow contribution-bundle journey | Thermo or kinetics only; no mixed or other-family support. |
| `artifact-integrity` | Artifact metadata and byte integrity | `backend/tests/db/test_calculation_artifact_integrity.py` | `(cd backend && conda run -n tckdb_env pytest tests/db/test_calculation_artifact_integrity.py -q)` | Protected artifact journey | Does not make bytes anonymous/public. |
| `archive-restore` | Full scientific state archive/restore behavior | `backend/tests/cli/test_tckdb_archive_script.py`; `backend/docs/specs/tckdb_archive_v1.md` | `(cd backend && conda run -n tckdb_env pytest tests/cli/test_tckdb_archive_script.py -q)` | Operator archive contract | Separate from public DOI release. |

## Production-shaped read-only reference inventory

The release owner supplied a same-day read-only production inventory at the
same source/Alembic baseline: 55 species, 460 calculations, 77 Hessians, 512
calculation artifacts, 1,069 review rows, and zero upload jobs. The isolated
restore evidence in the Stage 0 ledger matches these database counts exactly.
This inventory is a capacity/shape reference only; it is not committed
scientific dataset content and is not used as a test fixture.

## Unsupported claims remain explicit

No current corpus case supports: a catalog-scale performance claim, an
independent computed-rate rerun, atom-resolved isotope claims, independently
identified PDep microscopic pathways, a persisted curated selection, a public
immutable DOI release, or universal competitor superiority. Those claims stay
unsupported in the product/release contract until their roadmap stage exit
criteria pass.

## Regeneration and review rule

This is a versioned manifest, not an auto-discovered test list. When a journey
or claim changes, update its corpus case, command, and boundary in the same
review. Run every listed command appropriate to the changed journey; the
minimum Stage 0 contract command remains the OpenAPI snapshot and parity
generator checks in the stage ledger.
