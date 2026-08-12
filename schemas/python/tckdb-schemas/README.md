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

## Changelog

### 0.24.0 — **BREAKING**: statmech source calculations are named, not numbered

`ConformerUploadRequest.statmech` used to identify a supporting
calculation by its database primary key. It no longer does, and payloads
written against 0.23.0 that used those two fields will be rejected with a
422 rather than silently reinterpreted.

| Before (0.23.0) | After (0.24.0) |
|---|---|
| `statmech.source_calculations[].calculation_id: int` | `statmech.source_calculations[].calculation_key: str` |
| `statmech.torsions[].source_scan_calculation_id: int \| None` | `statmech.torsions[].source_scan_calculation_key: str \| None` |

A key names a calculation *declared in the same request*. To make that
possible, `ConformerUploadRequest.calculation` and
`additional_calculations[]` gained an optional `key`, and the request
refuses a statmech reference to a key nobody declared. This is the shape
the computed-species / computed-reaction bundles have always used; the
conformer path was the odd one out, and a row id is not something a
depositor can know.

Migrating: put a `key` on the calculation you meant, and reference it.

```python
# 0.23.0 — only writable by something that had already queried TCKDB
{"calculation": {...}, "statmech": {"source_calculations": [{"calculation_id": 4711, "role": "freq"}]}}

# 0.24.0
{"calculation": {"key": "h_sp", ...},
 "additional_calculations": [{"key": "h_freq", "type": "freq", ...}],
 "statmech": {"source_calculations": [{"calculation_key": "h_freq", "role": "freq"}]}}
```

Component renames (they affect generated client class names; the JSON
shape of the bundle paths is unchanged):

- `StatmechSourceCalculationCreate`, `StatmechSourceCalcInBundle` and the
  server-side `StatmechSourceCalculationIn` collapse into one component,
  `StatmechSourceCalcIn`. `StatmechSourceCalcInBundle` stays importable as
  an alias — from `tckdb_schemas.statmech_bits` and from both bundle
  modules — and is the same class object, so there is one component, not
  two. `StatmechSourceCalculationCreate` is **removed** from this package.
- `StatmechTorsionCreate` collapses into `StatmechTorsionIn` and is
  **removed** from this package.
- `StatmechTorsionCoordinateCreate` and `StatmechTorsionCoordinateBase`
  are removed; `StatmechTorsionCoordinateIn` is the only spelling of an
  atom quartet. They were field-for-field and validator-for-validator
  identical, so a generator emitted two classes for one concept.
- New: `ConformerCalculationIn` — a `CalculationWithResultsPayload` plus
  the optional `key`.

The two removed `*Create` names still exist inside the TCKDB server, in
`app.schemas.entities.statmech`, where they mean something different: the
row-shaped create payload that speaks `calculation_id`. They are not part
of any wire contract and never were importable from here for that purpose.

Also tightened, matching what the sibling upload paths already enforce.
Each of these used to surface as a 500 rather than a 422:

- `statmech.source_calculations` must be unique by
  `(calculation_key, role)` — it is the row's primary key.
- `statmech.torsions[].torsion_index` must be unique.
- The primary calculation may not be linked twice: once implicitly via
  `statmech.uploaded_calculation_role` and again in
  `source_calculations` under the same role. A *different* role is a
  different row and stays allowed.

### 0.23.0

Published `ConformerUploadRequest` and `TransitionStateUploadRequest`.

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
