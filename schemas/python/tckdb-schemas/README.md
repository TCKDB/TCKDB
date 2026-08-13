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

### 0.25.0 — bundle roots gain SCF stability and thermo provenance; an atomless participant may not carry coordinates

Three additions and one new refusal. Nothing is renamed or removed, so a
0.24.0 payload keeps validating — **unless** it declared a free electron
and hung a geometry off it, which is the refusal below.

**`scf_stability` now reaches the bundle roots.** The field existed only
on `CalculationWithResultsPayload`, so `/uploads/conformers`,
`/transition-states`, `/statmech`, `/thermo` and `/transport` could record
whether an SCF solution had been tested for stability, and
`/uploads/computed-species`, `/uploads/computed-reaction` and
`/networks/pdep` could not. Models are `extra="forbid"`, so a bundle that
tried got a 422 rather than a silent drop.

`SCFStabilityPayload` is now split so the bundles can carry it without
carrying database ids with it. The new `SCFStabilityContent` holds the
finding — `status`, `lowest_eigenvalue`, `instability_count`,
`instability_type`, `reoptimized_wavefunction`, `note` — and
`SCFStabilityPayload` extends it with the two FK fields
(`source_calculation_id`, `source_artifact_id`) the primitive routes
already accept as programmatic chaining. `CalculationInBundle` and the
shared `CalculationIn` take the content class; the primitive routes are
unchanged, so a 0.24.0 payload to any of them still validates.

The FK fields are not merely omitted for tidiness. A bundle names
everything by local key, `source_calculation_id` is already in the
bundle's own `_FORBIDDEN_DB_ID_FIELDS`, and a sideways local key could
not be resolved anyway: the block is persisted with the calculation it
hangs off, before that calculation's siblings exist. A depositor who
needs to cite another row has the primitive routes.

```python
{"key": "h_sp", "type": "sp", ...,
 "scf_stability": {"status": "stabilized", "instability_count": 1,
                   "reoptimized_wavefunction": True}}
```

**`BundleThermoIn` gains the provenance `ThermoInBundle` already had.**
The reaction route's per-species thermo could not record which
calculations produced it. It now takes `source_calculations`,
`literature`, `software_release`, `workflow_tool_release`,
`h298_uncertainty_kj_mol` and `s298_uncertainty_j_mol_k`, and the
workflow persists them — including the `thermo_source_calculation` rows
that route wrote none of. `software_release` / `workflow_tool_release`
override the bundle-level values for that species and fall back to them
when omitted, so existing payloads read exactly as before.

`applied_energy_corrections` is deliberately **not** added: the reaction
bundle already declares those on `BundleSpeciesIn`, against the same
resolved species entry, and a second place to say it would need a rule
for which one counts.

```python
{"key": "h", "species_entry": {...},
 "thermo": {"h298_kj_mol": 218.0,
            "source_calculations": [{"calculation_key": "h_sp", "role": "sp"}]}}
```

**An atomless participant may no longer carry coordinates.** A species
declaring `molecule_kind: electron` is now refused, as a 422 naming the
field, if it carries a conformer, or a `geometry_key`, `input_geometries`
or `output_geometries` on any of its calculations. A conformer geometry
was already refused — later, and as
`species_geometry_composition_mismatch` from mid-transaction — but a
*calculation* geometry reached no composition check on any path and was
accepted, so an electron's coordinates could be stored. Definitional
under ADR 0008: a record of an electron's coordinates cannot be what it
says it is, so this rejects no correct calculation.

`molecule_kind: pseudo` is unaffected and stays that way. A lumped
construct's composition is *unknown*, not empty, and a geometry deposited
under one is an under-described molecule rather than a contradiction.

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
