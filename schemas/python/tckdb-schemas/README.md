# tckdb-schemas

Pure Pydantic wire-contract schemas for TCKDB upload payloads.

This package exposes TCKDB's published upload request bodies (plus their
direct dependency closure) so external workflow tools and clients can
validate TCKDB public upload payloads without installing the full backend
(FastAPI, SQLAlchemy, RDKit, etc.).

| Route | Request model |
|---|---|
| `POST /api/v1/uploads/computed-species` | `ComputedSpeciesUploadRequest` |
| `POST /api/v1/uploads/computed-reaction` | `ComputedReactionUploadRequest` |
| `POST /api/v1/uploads/conformers` | `ConformerUploadRequest` |
| `POST /api/v1/uploads/transition-states` | `TransitionStateUploadRequest` |

All four are importable straight from `tckdb_schemas.workflows`.

Stability: the schemas mirror the backend's wire contract. Until the
TCKDB API hits 1.0, expect coordinated bumps with the backend. A contract
that lives here can be pinned, and a breaking change to it forces a
version bump a consumer can act on — which is the reason to put it here
rather than inside the server.

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
  statmech_bits.py         — torsion / source-calculation fragments
  shared/calculation_in.py — base CalculationIn / GeometryIn + adapter
  workflows/
    __init__.py            — the published request models, re-exported
    computed_species_upload.py
    computed_reaction_upload.py
    conformer_upload.py
    transition_state_upload.py
    transport_upload.py    — TransportUploadPayload, nested in the above
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
