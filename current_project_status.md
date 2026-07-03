# Project Status Map

**Last updated:** 2026-07-02 (audit pass — see `docs/audits/backend_assessment_2026-07-02.md`)

| Area | Schema | Upload | Read | Tests | Read for demo |
|---|---|---|---|---|---|
| **Species & Entries** | yes | yes | yes | yes | yes |
| **Conformers** | yes | yes | yes | yes | yes |
| **Reactions & Entries** | yes | yes | yes | yes | yes |
| **Calculations** | yes | yes | yes | yes | yes |
| **Kinetics** | yes | yes | yes | yes | yes |
| **Thermo** | yes | yes | yes | yes | yes |
| **Transition States** | yes | yes | yes | yes | yes |
| **Statmech** | yes | yes | yes | partial | yes |
| **Geometries** | yes | yes | yes | yes | yes |
| **Energy Corrections** | yes | yes | yes | partial | yes |
| **Level of Theory** | yes | yes | yes | yes | yes |
| **Software** | yes | yes | yes | yes | yes |
| **Literature** | yes | yes | yes | yes | yes |
| **Transport** | yes | yes | yes | yes | yes |
| **Network** | yes | yes | yes | partial | yes |
| **Network PDep** | yes | yes | yes | partial | yes |
| **Computed Reaction** | yes | yes | n/a | partial | n/a |
| **Lookup (search)** | yes | n/a | yes | yes | yes |
| **Jobs (async)** | yes | yes | yes | partial | maybe |

## Column Definitions

- **Schema**: DB models (`app/db/models/`) and Pydantic schemas (`app/schemas/`) exist
- **Upload**: Upload workflow (`app/workflows/`) and API endpoint (`/uploads/` or embedded in another upload) exist
- **Read**: Read API route registered in `app/api/router.py` with list + get-by-id endpoints
- **Tests**: Test coverage across schema validation, workflow persistence, and API read/write
- **Read for demo**: Sufficient read API coverage to seed data via upload and query it back

## Notes

- **Transport**: Read API at `/transport` with list + get-by-id + 5 filters. Standalone upload workflow now exists (`POST /uploads/transport`, `app/workflows/transport.py`), in addition to creation via conformer and network-pdep uploads.
- **Network / Network PDep**: Upload workflows exist and read routes are registered both on the legacy surface (`/networks`, `app/api/router.py`) and the scientific surface (`app/api/routes/scientific/networks.py`: network list/search, solves, network kinetics). Dedicated end-to-end network read tests remain thinner than other areas ("partial").
- **Computed Reaction**: A bundle upload that creates species, reactions, transition states, kinetics, and calculations. Reads are served through each individual entity's endpoints.
- **Statmech / Energy Corrections**: API read tests exist but only cover empty-list and not-found scenarios; no upload-then-read integration tests yet.
- **Jobs**: Async job queue endpoints exist with worker infrastructure, but test coverage is implicit (tested through upload tests, not dedicated job lifecycle tests).
