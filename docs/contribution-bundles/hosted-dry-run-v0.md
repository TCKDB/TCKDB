# Hosted bundle dry-run v0

The hosted dry-run endpoint lets an authenticated user POST a
`ContributionBundleV0` and receive a structured **preview** of what a
real import would do — without creating any scientific records,
submissions, upload jobs, audit events, or record links.

This is a preview-only milestone (milestone 6 of the
[implementation plan](../roadmaps/local-offline-and-hosted-submission-implementation-plan.md)).
There is intentionally no hosted submit/import route yet.

## Endpoint

```text
POST /api/v1/bundles/dry-run
```

- **Auth:** required. Either a session cookie (`tckdb_session`) or an
  API key header (`X-API-Key`) is accepted, matching the existing
  `get_current_user` dependency. Anonymous requests are rejected with
  `401`.
- **Request body:** a `ContributionBundleV0` JSON document. Validated
  by the existing bundle schema; structurally invalid bundles fail with
  the normal `422` validation response.
- **Response:** `ContributionBundleDryRunResult` (HTTP `200`).
- **Side effects:** none. The endpoint runs only `SELECT` queries
  through a non-committing session.

## Supported bundle kinds

Hosted dry-run v0 supports the same families as bundle format v0:

- `thermo`
- `kinetics`

Other families (`network`, `statmech`, `transport`, `computed_reaction`,
mixed) are rejected by `ContributionBundleV0` validation before they
reach this endpoint.

## No-mutation guarantee

The dry-run service performs **only read-only queries**. It never calls
`resolve_or_create_*` and never relies on transaction-rollback safety.
The route binds the read-only `get_db` session, not the committing
`get_write_db` session.

Tests assert that row counts in the following tables are unchanged
across a successful dry-run: `species`, `species_entry`, `chem_reaction`,
`reaction_entry`, `thermo`, `kinetics`, `submission`,
`submission_audit_event`, `submission_record_link`, `upload_job`.

## Conservative preview semantics

For each thermo or kinetics upload in the bundle, dry-run inspects
**identity + provenance** and reports a per-record action:

| Record type | Action |
|---|---|
| `species` | `would_reuse` if a row with the same canonical `inchi_key` exists, else `would_create` |
| `species_entry` | `would_reuse` if a matching entry exists for that species (same `kind`, `stereo_label`, electronic state, isotopologue), else `would_create` |
| `chem_reaction` (kinetics only) | `would_reuse` if a row with the same graph-identity `stoichiometry_hash` exists; `would_create` otherwise (or when any participant species is itself missing) |
| `literature` | `would_reuse` on a DOI or ISBN match (normalized), else `would_create` |
| `software_release` | `would_reuse` on a `(software.name, version, revision, build)` match, else `would_create` |
| `workflow_tool_release` | `would_reuse` on a `(workflow_tool.name, version, git_commit)` match, else `would_create` |
| `thermo` / `kinetics` | always `would_append` — these are append-only result rows |

Provenance items only describe whether the referenced identity already
exists on hosted. They do **not** imply moderation acceptance, curation
status, or that the bundle has been imported.

What is **not** previewed in v0 (deferred to later milestones):

- inline calculations and source-calculation links
- applied energy corrections and their components
- artifacts and manifest contents
- duplicate scientific-product detection (no "best/winner" logic)

## Request example (thermo)

```json
{
  "bundle_format": "tckdb-contribution-bundle",
  "bundle_version": "0.1",
  "bundle_kind": "thermo",
  "created_at": "2026-04-25T00:00:00Z",
  "source_instance": {
    "instance_kind": "local",
    "instance_name": "example-local",
    "schema_version": "d861dfd60891"
  },
  "exporter": {"local_user_label": "example-user"},
  "submission": {
    "title": "Example thermo contribution",
    "summary": "Selected local thermo record for hosted review."
  },
  "records": {
    "thermo_uploads": [
      {
        "species_entry": {"smiles": "O", "charge": 0, "multiplicity": 1},
        "scientific_origin": "computed",
        "h298_kj_mol": -241.8,
        "s298_j_mol_k": 188.8
      }
    ],
    "kinetics_uploads": []
  },
  "manifest": {"files": []}
}
```

## Response example

```json
{
  "bundle_valid": true,
  "bundle_kind": "thermo",
  "summary": {
    "records_seen": 3,
    "would_create": 2,
    "would_reuse": 0,
    "would_append": 1,
    "unsupported": 0,
    "errors": 0,
    "warnings": 0
  },
  "items": [
    {
      "record_type": "species",
      "action": "would_create",
      "reason": "No species with this InChIKey exists yet; one would be created during real import.",
      "local_ref": "thermo_uploads[0].species_entry",
      "target": "O",
      "hosted_identity": {"inchi_key": "XLYOFNOQVPJJNP-UHFFFAOYSA-N"}
    },
    {
      "record_type": "species_entry",
      "action": "would_create",
      "reason": "Species not present on hosted; the corresponding species entry would be created during real import.",
      "local_ref": "thermo_uploads[0].species_entry",
      "target": "O",
      "hosted_identity": {"inchi_key": "XLYOFNOQVPJJNP-UHFFFAOYSA-N"}
    },
    {
      "record_type": "thermo",
      "action": "would_append",
      "reason": "Thermo records are append-only; a real import would append a new thermo row attached to the resolved species entry.",
      "local_ref": "thermo_uploads[0]"
    }
  ],
  "messages": []
}
```

## Difference vs. submit/import

| Aspect | Dry-run (this endpoint) | Real submit/import (future milestone) |
|---|---|---|
| HTTP method/path | `POST /api/v1/bundles/dry-run` | not implemented yet |
| Mutates database | never | yes — creates submission, upload job, scientific rows |
| Creates submission row | no | yes |
| Creates audit/record-link rows | no | yes |
| Returns | preview result with per-record `would_*` actions | submission/upload-job IDs and moderation state |
| Idempotency | trivially idempotent (read-only) | governed by submission/moderation lifecycle |

Dry-run answers *"what would happen?"* without making it happen.
