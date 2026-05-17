# tckdb-schemas

Pure Pydantic wire-contract schemas for TCKDB upload payloads.

This package exposes the computed-species and computed-reaction upload
payload models (plus their direct dependency closure) so external
workflow tools and clients can validate TCKDB public upload payloads
without installing the full backend (FastAPI, SQLAlchemy, RDKit, etc.).

Stability: the schemas mirror the backend's wire contract. Until the
TCKDB API hits 1.0, expect coordinated bumps with the backend.

## Layout

```
tckdb_schemas/
  enums.py                 — wire-contract enum mirror
  common.py                — SchemaBase
  utils.py                 — text/ORCID normalization helpers
  upload_warning.py        — UploadWarning
  reaction_family.py       — canonical RMG family vocabulary
  fragments/               — reusable upload fragments
  literature.py            — LiteratureUploadRequest
  energy_correction.py     — applied energy-correction payloads
  thermo.py                — ThermoPoint / ThermoNASA upload pieces
  statmech_bits.py         — torsion coordinate fragment
  shared/calculation_in.py — base CalculationIn / GeometryIn + adapter
  workflows/
    computed_species_upload.py
    computed_reaction_upload.py
```

## Installation (development)

From the repo root:

```bash
pip install -e schemas/python/tckdb-schemas
```

## Usage

```python
from tckdb_schemas.workflows.computed_species_upload import (
    ComputedSpeciesUploadRequest,
)

payload = ComputedSpeciesUploadRequest.model_validate(data)
wire = payload.model_dump(mode="json", exclude_none=True)
```

## Boundary

`tckdb_schemas` must not import FastAPI, SQLAlchemy, Alembic, RDKit, or
any backend `app.*` module. The package depends only on `pydantic` plus
the standard library. The boundary is enforced by
`tests/test_import_boundaries.py`.
