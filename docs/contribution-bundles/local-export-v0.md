# Local Contribution Bundle Export — v0

Status: implemented (service + CLI; bundle file output only)
See: [Bundle v0 format](v0-format.md),
[Local bundle export v0 spec](../roadmaps/local-bundle-export-v0-spec.md),
[Local-offline implementation plan](../roadmaps/local-offline-and-hosted-submission-implementation-plan.md)

## What this milestone does

Local export v0 turns selected local database rows into validated
[`ContributionBundleV0`](../../backend/app/schemas/workflows/contribution_bundle.py)
JSON files.

This is a **service + CLI** milestone. The service
([`backend/app/services/contribution_bundle_export.py`](../../backend/app/services/contribution_bundle_export.py))
owns the conversion and validation logic; the CLI wrapper
([`backend/scripts/export_contribution_bundle.py`](../../backend/scripts/export_contribution_bundle.py))
handles argument parsing, database session setup, and writing JSON to
disk.

## What this milestone does **not** do

- It does not import anything into a hosted instance.
- It does not implement hosted dry-run, hosted submit, or hosted import.
- It does not push local bundles to a remote.
- It does not add a public API route for export.
- It does not add any frontend UI.
- It does not synchronize raw database rows between instances.
- It does not package external artifacts (logs, geometries) into the
  bundle — only the manifest *shape* exists in v0.

These remain on the
[implementation plan](../roadmaps/local-offline-and-hosted-submission-implementation-plan.md)
as later milestones.

## Supported export roots

| `--kind` | Selector flag      | Source table |
|----------|--------------------|--------------|
| `thermo` | `--thermo-id`      | `thermo`     |
| `kinetics` | `--kinetics-id`  | `kinetics`   |

Both selectors are repeatable, so a single bundle can carry multiple
records of the same family. Mixed thermo + kinetics bundles are
explicitly rejected by `ContributionBundleV0`; export one of each
family separately.

Future bundle families (`statmech`, `transport`, `network`,
`computed_reaction`) are not yet supported.

## Quick start

> Run from `backend/` (`cd backend` once). The CLI lives at
> `backend/scripts/export_contribution_bundle.py` and is invoked by
> path below.

Thermo:

```bash
conda run -n tckdb_env python scripts/export_contribution_bundle.py \
    --kind thermo \
    --thermo-id 1 \
    --output ./thermo-bundle.tckdb.json \
    --title "Example thermo contribution" \
    --summary "Selected thermo records from local TCKDB"
```

Kinetics:

```bash
conda run -n tckdb_env python scripts/export_contribution_bundle.py \
    --kind kinetics \
    --kinetics-id 10 \
    --output ./kinetics-bundle.tckdb.json \
    --title "Example kinetics contribution" \
    --summary "Selected kinetics records from local TCKDB"
```

The CLI uses the same `DB_*` environment variables as the rest of the
project (see `backend/app/api/config.py`).

## CLI options

### Required

| Flag | Meaning |
|------|---------|
| `--kind {thermo,kinetics}` | Bundle family. Must match the selector flag in use. |
| `--thermo-id INT` *(repeatable)* | Local `thermo.id` to export. Required for `--kind thermo`. |
| `--kinetics-id INT` *(repeatable)* | Local `kinetics.id` to export. Required for `--kind kinetics`. |
| `--output PATH` | Where to write the bundle JSON file. |
| `--title STR` | Submission title written to `submission.title`. |
| `--summary STR` | Submission summary written to `submission.summary`. |

### Source-instance metadata (optional)

| Flag | Default | Meaning |
|------|---------|---------|
| `--instance-name` | `local-tckdb` | Free-text label for the source instance. |
| `--instance-kind` | `local` | One of `local` or `lab_server`. `hosted` is intentionally not allowed in v0. |

### Exporter metadata (optional)

| Flag | Default | Meaning |
|------|---------|---------|
| `--exporter-label` | current OS user | Local label, written to `exporter.local_user_label`. **Not** a hosted identity. |
| `--orcid` | — | ORCID iD if known. |
| `--affiliation` | — | Free-text affiliation. |
| `--email` | — | Free-text contact. |
| `--exporter-notes` | — | Free-text notes. |

### Output behavior

| Flag | Default | Meaning |
|------|---------|---------|
| `--overwrite` | off | Replace `--output` if it already exists. Without this, the CLI exits with an error rather than clobbering an existing file. |

## Dependency closure

Each export root pulls in the minimum supporting data needed to
reconstruct an upload-equivalent payload:

### Thermo (`Thermo` row)

- the `Thermo` row's scalar fields, `tmin_k`/`tmax_k`, and note
- attached `ThermoNASA` (if any) and `ThermoPoint` rows
- target `SpeciesEntry` and its parent `Species` (rebuilt as the upload
  schema's `species_entry` identity payload)
- `Literature` reference if attached
- `SoftwareRelease` and `WorkflowToolRelease` if attached

### Kinetics (`Kinetics` row)

- the `Kinetics` row's scalar fields, `tmin_k`/`tmax_k`, model kind, and note
- canonical `ea_kj_mol` is round-tripped back to the upload schema's
  `(reported_ea, reported_ea_units)` pair using `kj_mol` (the canonical
  storage unit), which is lossless
- `ChemReaction` reversibility and reaction-family metadata
- ordered structured participants from `ReactionEntryStructureParticipant`,
  rebuilt as `species_entry` identity payloads
- `Literature` reference if attached
- `SoftwareRelease` and `WorkflowToolRelease` if attached

### Closure failure

The export service raises `ContributionBundleExportError` (and the CLI
exits non-zero) when:

- the requested `thermo_id` / `kinetics_id` does not exist
- a thermo's species entry or species identity is missing
- a kinetics row's reaction entry, chem reaction, or structured
  participants are missing
- the assembled bundle fails `ContributionBundleV0` validation for any
  reason (including nested upload-schema validation)

The bundle file is **not** written when any of these fail.

## What is *not* reconstructed in v0

These are within the upload schemas but are skipped by v0 export to
keep the surface honest:

- inline supporting `calculations` and `source_calculations` on a
  thermo upload (would require LOT/software resolution and a full
  string-key reconstruction)
- `applied_energy_corrections` on a thermo upload
- `energy_level_of_theory` driven SP source-calc auto-resolution on
  kinetics

These can be added incrementally once the hosted import path knows
what to do with them.

## Local refs

`local_refs` in v0 is **annotation only**. The service emits a small
set of human-readable, traceability-oriented refs covering the root
record and its immediate dependencies:

| Bundle root | Local-ref keys emitted |
|-------------|------------------------|
| Thermo  | `thermo:t<id>`, `species_entry:se<id>`, `species:s<id>` |
| Kinetics | `kinetics:k<id>`, `reaction:r<id>`, `species_entry:se<id>`, `species:s<id>` (one per distinct participant) |

Labels are deliberately prefixed (`t1`, `se42`, `s5`) so they pass the
`ContributionBundleV0` validator's "no purely numeric label" rule, which
exists to discourage shipping raw DB primary keys as portable identity.
The local DB ids are still embedded for **debug/traceability only** —
the bundle does not claim them as hosted identities, and the hosted
importer (when it lands) is responsible for resolving real hosted
identities via scientific content, not via these labels.

The v0 schema does not enforce coverage between `local_refs` and
`records`, so the set above is intentionally minimal.

## Output format

- UTF-8 JSON
- 2-space indentation, key order matches schema field order
- `created_at` is set at export time in UTC
- `manifest.sha256 == null` and `manifest.files == []` (no artifacts in v0)
- the assembled bundle is validated against `ContributionBundleV0`
  *before* the file is written, so a written file is always a valid v0
  bundle

## Validating an exported bundle

The bundle is validated automatically before being written. To
re-validate an existing file with the same schema (e.g. as part of an
external CI step):

```python
import json
from pathlib import Path

from app.schemas.workflows.contribution_bundle import ContributionBundleV0

raw = json.loads(Path("./thermo-bundle.tckdb.json").read_text(encoding="utf-8"))
ContributionBundleV0.model_validate(raw)
```

This re-runs every nested upload validator
(`ThermoUploadRequest`, `KineticsUploadRequest`) plus the bundle-level
family/local-ref/manifest rules.

## Testing

Service + CLI tests live in
[`backend/tests/services/test_contribution_bundle_export.py`](../../backend/tests/services/test_contribution_bundle_export.py)
and cover thermo export, kinetics export, missing-root failures,
incomplete-dependency failures, CLI smoke output, and a credential-leak
guard.

Run them from `backend/`:

```bash
conda run -n tckdb_env pytest tests/services/test_contribution_bundle_export.py -v
```

## Hosted import is not implemented yet

These bundles are not sent anywhere. There is no hosted dry-run, no
hosted submit, no push-to-hosted UX, and no import endpoint. Those
arrive in later milestones on the
[local-offline implementation plan](../roadmaps/local-offline-and-hosted-submission-implementation-plan.md).
