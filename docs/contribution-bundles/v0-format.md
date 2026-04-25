# Contribution Bundle v0 — Format Specification

Status: implemented (schema/format only)
See: [DR-0023 Local/Offline and Hosted Submission Model](../decisions/0023-local-offline-and-hosted-submission-model.md),
[contribution-bundle-v0-format-spec roadmap](../roadmaps/contribution-bundle-v0-format-spec.md)

## What this document covers

A contribution bundle is a portable scientific contribution package. It
carries selected scientific payloads, source-instance metadata, exporter
metadata, submission metadata, bundle-local references, and integrity
metadata in a format that can later be validated and ingested by a hosted
TCKDB instance.

This document defines bundle **v0** (`bundle_version == "0.1"`) and the
schemas implemented in
[`app/schemas/workflows/contribution_bundle.py`](../../app/schemas/workflows/contribution_bundle.py).

## What v0 is

- A schema/format milestone only.
- Re-uses existing thermo and kinetics upload schemas as the bundle's
  scientific payload, so nested validation runs through the same code
  paths as a normal API upload.
- Validatable without database access.

## What v0 is not

- v0 does **not** export records from any database.
- v0 does **not** import into any hosted instance.
- v0 does **not** create submissions, jobs, or moderation events.
- v0 does **not** support raw database synchronization between instances.
- v0 does **not** ship artifact packaging — only the manifest *shape*.
- v0 does **not** support full network bundles, arbitrary graph export, or
  mixed thermo+kinetics bundles.
- v0 does **not** introduce any FastAPI route, service, or DB write.

## Supported v0 bundle families

Allowed `bundle_kind` values:

| Value | Meaning |
|-------|---------|
| `thermo` | Bundle carries thermo upload-equivalent payloads only. |
| `kinetics` | Bundle carries kinetics upload-equivalent payloads only. |

Future families intentionally **not** allowed in v0 (to avoid shipping
bundles no exporter or importer is actually validating yet):

- `statmech`
- `transport`
- `network`
- `mixed`
- `computed_reaction`

## Top-level shape

```json
{
  "bundle_format": "tckdb-contribution-bundle",
  "bundle_version": "0.1",
  "bundle_kind": "thermo",
  "created_at": "2026-04-25T00:00:00Z",
  "source_instance": { "...": "..." },
  "exporter": { "...": "..." },
  "submission": { "...": "..." },
  "records": { "thermo_uploads": [], "kinetics_uploads": [] },
  "local_refs": { "species:ethanol": { "record_type": "species", "label": "ethanol" } },
  "manifest": { "sha256": null, "files": [] }
}
```

### `bundle_format`

Must be the literal string `tckdb-contribution-bundle`.

### `bundle_version`

Must be the literal string `0.1` for v0.

### `bundle_kind`

One of `thermo` | `kinetics`.

### `created_at`

ISO-8601 UTC timestamp (Pydantic `datetime`).

### `source_instance`

Identifies the TCKDB instance that produced the bundle.

| Field | Required | Notes |
|-------|----------|-------|
| `instance_kind` | yes | `local` or `lab_server`. `hosted` is not allowed in v0. |
| `instance_name` | yes | Free-text label, e.g. `calvin-laptop` or `pi-rmgteam-server`. |
| `schema_version` | yes | The Alembic revision the local DB is at, e.g. `d861dfd60891`. |
| `software_version` | no | Optional TCKDB software version label. |
| `created_by_local_user` | no | Optional local username/label. |
| `notes` | no | Free-text. |

### `exporter`

Provenance-only metadata about who produced the bundle. **This is not the
hosted actor identity.** Hosted actor identity comes from hosted
authentication during the future import milestone.

| Field | Required | Notes |
|-------|----------|-------|
| `local_user_label` | yes | Local label such as `calvin`. |
| `orcid` | no | ORCID iD if known. Not validated for format in v0. |
| `affiliation` | no | Free-text. |
| `email` | no | Free-text contact. |
| `notes` | no | Free-text. |

### `submission`

Bundle-level submission metadata. The hosted instance maps this to its own
`submission` row at import time; the bundle does not create submissions.

| Field | Required | Notes |
|-------|----------|-------|
| `title` | yes | Short title for the contribution. |
| `summary` | yes | One-paragraph summary. |
| `source_kind` | yes | Must be `local_bundle` in v0. |

> **Note on `local_bundle` and the database enum.** The
> `submission.source_kind` field on the *bundle* uses a format-level enum
> (`BundleSubmissionSourceKind`). The database enum `SubmissionSourceKind`
> in `app/db/models/common.py` does **not** yet include `local_bundle`.
> Adding it is deferred to the hosted-import milestone — the bundle format
> only states "this submission came from a local bundle", and the hosted
> importer will be responsible for translating that to its own submission
> machinery.

### `records`

Container for the upload-equivalent scientific payloads. Reuses the
existing workflow upload schemas directly:

- `thermo_uploads: list[ThermoUploadRequest]`
- `kinetics_uploads: list[KineticsUploadRequest]`

Family rules for v0:

- A `thermo` bundle **must** contain ≥ 1 `thermo_uploads` entry and
  **must not** contain any `kinetics_uploads`.
- A `kinetics` bundle **must** contain ≥ 1 `kinetics_uploads` entry and
  **must not** contain any `thermo_uploads`.
- Mixed bundles are explicitly rejected.

### `local_refs`

A map from a bundle-local reference key to a small descriptor.

Local refs are **not** hosted IDs and **not** raw DB primary keys. They
exist so that future bundle versions can name records inside the bundle
(e.g. for cross-payload references) without claiming any hosted identity.

#### Local ref key rules

A local ref key has the form `<namespace>:<label>`:

- Namespace: lowercase ASCII, may contain `_`, must start with a letter.
- Label: alphanumeric plus `_`, `-`, `.`. Must start with alphanumeric.
- Purely numeric labels (`species:123`) are **rejected** — they look like
  raw database primary keys and that is exactly what bundles must not
  carry as canonical identity.

Recommended namespaces (informational, validated as `record_type`):

```
species:ethanol
species_entry:ethanol-singlet
reaction:h_abstraction_001
transition_state:ts_h_abstraction_001
calculation:sp_001
thermo:ethanol_nasa_001
kinetics:h_abstraction_rate_001
literature:zhang_2024_jpca
```

Keys are unique by Python dict semantics — duplicate keys in the source
JSON collapse to the last value, so the schema validates the *deduped*
map.

#### Annotation-only in v0

In v0, `local_refs` is an **annotation map**, not a cross-reference graph.
It may describe records, labels, or local identities that help a future
exporter/importer reason about the bundle, but v0 scientific payloads
(`ThermoUploadRequest`, `KineticsUploadRequest`) do not yet cite local
refs.

The v0 schema therefore validates only:

- local-ref key shape (namespaced `<namespace>:<label>`)
- local-ref `record_type`
- rejection of purely numeric (raw-DB-PK shape) labels
- overall map shape

The v0 schema **does not** enforce coverage between `local_refs` and
`records` — having a `thermo:*` ref does not prove it refers to a
specific `ThermoUploadRequest`, because nothing in the upload payload
binds to it yet.

Promoting `local_refs` to a first-class cross-reference is deferred to a
future bundle version, where scientific payloads can explicitly cite
local refs and the hosted importer can define a deterministic local-ref
contract for import preview/diff.

### `manifest`

Integrity metadata for any external artifacts shipped alongside the
bundle. Artifact packaging is **not** implemented in v0; the manifest
just defines the shape so future artifact bundling has a place to land.

| Field | Required | Notes |
|-------|----------|-------|
| `sha256` | no | Optional 64-char lowercase-hex covering hash. |
| `files` | yes (may be `[]`) | List of `BundleManifestFile` entries. |
| `created_by_tool` | no | Free-text. |
| `notes` | no | Free-text. |

#### `manifest.files[*]`

| Field | Required | Notes |
|-------|----------|-------|
| `path` | yes | Path inside the bundle. Must be unique across `files`. |
| `sha256` | yes | 64-char lowercase-hex SHA-256. |
| `size_bytes` | no | Non-negative integer. |
| `content_type` | no | Free-text MIME or label. |
| `role` | no | Free-text role hint (e.g. `output_log`, `input_geometry`). |

A bundle without external artifacts can use:

```json
{ "sha256": null, "files": [] }
```

## Validation rules implemented in v0

1. `bundle_format == "tckdb-contribution-bundle"`.
2. `bundle_version == "0.1"`.
3. `bundle_kind` ∈ `{thermo, kinetics}`.
4. All required top-level metadata is present (`source_instance`,
   `exporter`, `submission`, `records`, `manifest`, `created_at`).
5. The selected bundle kind has at least one record of the matching
   family.
6. Mixed thermo+kinetics record sets are rejected.
7. `manifest.files` paths are unique.
8. `local_refs` keys match the namespaced format rule above.
9. Purely numeric local-ref labels (raw-DB-PK shape) are rejected.
10. `extra="forbid"` on every bundle schema rejects unknown top-level
    fields, so a future field cannot accidentally slip in unnoticed.
11. Nested `ThermoUploadRequest` / `KineticsUploadRequest` payloads run
    their full upload-time validators (e.g. thermo must carry actual
    scientific content, kinetics `a_units` must match molecularity).

DB-backed scientific resolution (species lookup, level-of-theory
resolution, deduplication, etc.) is **not** done in v0.

## Examples

- [Thermo bundle example](../../examples/bundles/thermo-bundle-v0.json)
- [Kinetics bundle example](../../examples/bundles/kinetics-bundle-v0.json)

## Future extension points (not in v0)

- Adding `local_bundle` to the DB `SubmissionSourceKind` enum, when the
  hosted-import milestone needs to persist bundle-origin submissions.
- Wider `bundle_kind` support (`statmech`, `transport`, `network`, etc.).
- Cross-payload references that consume `local_refs` keys directly inside
  scientific upload payloads.
- Real artifact packaging (tarball/zip layout, content-addressed storage).
- Hosted dry-run, hosted import, and hosted submission lifecycle.
- Frontend UX for bundle authoring and review.
