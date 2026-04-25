# Manual local-to-hosted contribution flow — v0

Status: implemented (documentation milestone)
Spec: [`manual-local-to-hosted-flow-v0-spec.md`](../roadmaps/manual-local-to-hosted-flow-v0-spec.md)
Roadmap: [`local-offline-and-hosted-submission-implementation-plan.md`](../roadmaps/local-offline-and-hosted-submission-implementation-plan.md)

## Overview

This document describes the **complete manual command-line workflow** for
moving selected scientific records from a local/private TCKDB instance to
a hosted/community TCKDB instance.

The flow is file-based and explicit. There is **no live database
synchronization**, no automated push-to-hosted, and no frontend UX. The
user runs three commands in sequence:

1. **Export** a contribution bundle from the local instance to a JSON file.
2. **Dry-run** the bundle against the hosted instance to preview what
   would happen.
3. **Submit** the bundle to the hosted instance, which validates the
   bundle, imports the records through the existing upload workflows,
   and creates a `submission` row marked `pending` / `unreviewed`.

End-to-end:

```text
local DB ──(export script)──> bundle.tckdb.json ──(curl /bundles/submit)──> hosted DB
                                            │
                                            └─(curl /bundles/dry-run)── preview only
```

> Imported records are publicly visible but **unreviewed**. Validation
> means importable, not curator-approved. Use them with appropriate
> caution until a curator reviews the submission.

## Prerequisites

Before starting:

- A working **local TCKDB** install with the records you want to
  contribute. See `CLAUDE.md` and the local deployment docs.
- A **hosted TCKDB account** on the target community instance, with an
  API key issued by **that hosted instance**. Local API keys do not
  authenticate against hosted.
- Network access to the hosted instance from wherever you run `curl`
  (VPN, firewall, proxy permitting).
- The conda environment `tckdb_env` (the local export script imports
  `app.*` modules and reads the local DB).
- A bundle root selected: a single `thermo.id` or one or more
  `kinetics.id` values from the local DB.

## Terminology

| Term | Meaning |
|------|---------|
| **Local instance** | A private/laptop/lab TCKDB deployment that holds the records you want to contribute. |
| **Hosted instance** | The community TCKDB deployment that receives contributions. |
| **Contribution bundle** | A self-contained `.tckdb.json` v0 file produced by `backend/scripts/export_contribution_bundle.py`. |
| **Dry-run** | Authenticated read-only preview against hosted: `POST /bundles/dry-run`. |
| **Submit / import** | Authenticated write call against hosted: `POST /bundles/submit`. |
| **Submission** | A hosted DB row created at submit time that links to imported scientific rows; tracked by status. |
| **`pending`** | The submission's raw moderation status — created, but not yet curated. |
| **`unreviewed`** | The human-facing trust state on the submission and its imported records. |
| **Local exporter metadata** | Identity-style fields written into the bundle (`exporter.local_user_label`, `orcid`, etc.) — provenance only, **not** the hosted actor. |
| **Hosted actor** | The authenticated hosted user whose API key is used at submit time — this is the `created_by` for every imported row. |

## Environment setup

Use placeholder values; never commit real keys.

```bash
# Local — used by the export script (same DB env vars as the rest of the project)
export DB_USER=tckdb
export DB_PASSWORD=tckdb
export DB_NAME=tckdb_dev
export DB_HOST=127.0.0.1
export DB_PORT=5432

# Hosted — used by curl / the optional helper script
export HOSTED_TCKDB_BASE_URL="https://tckdb.example.org/api/v1"
export HOSTED_TCKDB_API_KEY="tck_hosted_replace_me"
```

> The hosted `base_url` already ends in `/api/v1`. The bundle endpoints
> are reached with `$HOSTED_TCKDB_BASE_URL/bundles/dry-run` and
> `$HOSTED_TCKDB_BASE_URL/bundles/submit`. Do **not** add `/api/v1`
> again.

## Step 1: Export a bundle from local

Bundle export is a service + CLI documented in detail in
[`local-export-v0.md`](local-export-v0.md). The CLI converts selected
local rows into a validated `ContributionBundleV0` JSON file. Nothing is
sent anywhere at this stage.

> The export script lives at `backend/scripts/export_contribution_bundle.py`.
> Run the commands below from `backend/` (`cd backend` once); the
> `--output` path is interpreted relative to your shell, so leading
> `./` writes the bundle next to wherever you `cd`'d.

### Thermo bundle

```bash
conda run -n tckdb_env python scripts/export_contribution_bundle.py \
    --kind thermo \
    --thermo-id 1 \
    --output ./thermo-bundle.tckdb.json \
    --title "Thermo contribution from local instance" \
    --summary "Selected local thermo records for hosted review"
```

Multiple thermo records can be combined into one bundle by repeating
`--thermo-id`:

```bash
conda run -n tckdb_env python scripts/export_contribution_bundle.py \
    --kind thermo \
    --thermo-id 1 \
    --thermo-id 2 \
    --thermo-id 5 \
    --output ./thermo-bundle.tckdb.json \
    --title "Thermo contribution from local instance" \
    --summary "Selected local thermo records for hosted review"
```

### Kinetics bundle

```bash
conda run -n tckdb_env python scripts/export_contribution_bundle.py \
    --kind kinetics \
    --kinetics-id 10 \
    --output ./kinetics-bundle.tckdb.json \
    --title "Kinetics contribution from local instance" \
    --summary "Selected local kinetics records for hosted review"
```

Repeat `--kinetics-id` to bundle multiple kinetics records of the same
family.

### Mixing kinds

Mixed thermo + kinetics bundles are **rejected** by
`ContributionBundleV0` validation. Export one bundle per family.

### Optional metadata

Useful optional flags (full list in
[`local-export-v0.md`](local-export-v0.md#cli-options)):

| Flag | Purpose |
|------|---------|
| `--instance-name` | Free-text label for the local source instance (default `local-tckdb`). |
| `--instance-kind {local,lab_server}` | Source instance kind (`hosted` is intentionally not allowed). |
| `--exporter-label` | Local user label (defaults to the current OS user). **Not** a hosted identity. |
| `--orcid`, `--affiliation`, `--email`, `--exporter-notes` | Free-text exporter provenance. |
| `--overwrite` | Replace `--output` if it already exists. |

### Output

On success the script prints something like:

```text
Wrote contribution bundle: ./thermo-bundle.tckdb.json
Bundle kind: thermo
Records exported: 1
```

The file on disk is a validated v0 bundle — the script refuses to write
an invalid file, so a successful exit means the bundle already passes
the structural and family-level validators.

## Step 2: Dry-run the bundle against hosted

Dry-run is the **read-only preview** endpoint — it never mutates the
hosted database. It tells you what a real import would do for each
identity in the bundle. Full reference:
[`hosted-dry-run-v0.md`](hosted-dry-run-v0.md).

### Thermo dry-run

```bash
curl -X POST "$HOSTED_TCKDB_BASE_URL/bundles/dry-run" \
  -H "X-API-Key: $HOSTED_TCKDB_API_KEY" \
  -H "Content-Type: application/json" \
  --data @./thermo-bundle.tckdb.json
```

### Kinetics dry-run

```bash
curl -X POST "$HOSTED_TCKDB_BASE_URL/bundles/dry-run" \
  -H "X-API-Key: $HOSTED_TCKDB_API_KEY" \
  -H "Content-Type: application/json" \
  --data @./kinetics-bundle.tckdb.json
```

### What to look for in the response

The response is a `ContributionBundleDryRunResult`. The most important
fields are:

| Field | Meaning |
|-------|---------|
| `bundle_valid` | Top-level validity. Must be `true` to proceed. |
| `bundle_kind` | Echoes the bundle family. |
| `summary.errors` | Item-level + message-level error count. **Must be `0`** to submit. |
| `summary.unsupported` | Count of unsupported actions. **Must be `0`** to submit. |
| `summary.warnings` | Non-blocking notes. May be `> 0`; submit will still proceed. |
| `summary.would_create` / `would_reuse` / `would_append` | Counts of each action. |
| `items[].action` | Per-record action — see below. |
| `items[].reason` | Human-readable explanation per item. |
| `messages[]` | Bundle-wide notes (info / warning / error). |

### Action meanings

| Action | Meaning |
|--------|---------|
| `would_reuse` | The hosted instance already has a matching identity (same canonical hash, DOI/ISBN, or release tuple). A real import would attach to the existing row. |
| `would_create` | No matching identity on hosted; a real import would create a new identity row. |
| `would_append` | Append-only result rows (`thermo`, `kinetics`). A real import always appends a new result row. |
| `unsupported` | The bundle exercises a feature the hosted dry-run does not yet support. **Submit will be blocked.** |
| `error` | Bundle failed validation/preview at the item or message level. **Submit will be blocked.** |

If the dry-run reports a non-zero `errors` or `unsupported` count, fix
the bundle (or wait for a hosted upgrade) before attempting submit. The
submit endpoint runs the same dry-run service internally as a strict
gate — it will reject the same bundles dry-run flagged.

### What dry-run does *not* do

- Does not create a `submission` row.
- Does not write any scientific rows.
- Does not register an upload job.
- Does not imply curator acceptance.

## Step 3: Submit / import the bundle to hosted

Submit is the **first write path** for contribution bundles. It runs the
dry-run internally as a strict gate, then imports the records through
the existing upload workflows in a single transaction. Full reference:
[`hosted-submit-v0.md`](hosted-submit-v0.md).

### Thermo submit

```bash
curl -X POST "$HOSTED_TCKDB_BASE_URL/bundles/submit" \
  -H "X-API-Key: $HOSTED_TCKDB_API_KEY" \
  -H "Content-Type: application/json" \
  --data @./thermo-bundle.tckdb.json
```

### Kinetics submit

```bash
curl -X POST "$HOSTED_TCKDB_BASE_URL/bundles/submit" \
  -H "X-API-Key: $HOSTED_TCKDB_API_KEY" \
  -H "Content-Type: application/json" \
  --data @./kinetics-bundle.tckdb.json
```

### What to look for in the response

The response is a `ContributionBundleSubmitResult` (HTTP `201`).

| Field | Meaning |
|-------|---------|
| `submission_id` | Hosted DB id of the new `submission` row. Quote this when communicating with curators. |
| `status` | Raw moderation status. Always `pending` in v0. |
| `review_status` | Human-facing trust state. Always `unreviewed` in v0. |
| `bundle_kind` | Bundle family that was imported. |
| `summary.records_imported` | Number of product rows (`thermo` or `kinetics`) appended. |
| `summary.records_linked` | Number of immediate identity parents linked (`species_entry` / `reaction_entry`). |
| `summary.warnings` | Non-blocking warnings carried forward from dry-run. |
| `records[]` | Per-row report — `record_type`, `record_id`, `action` (`imported` or `linked`), `review_status`, `local_ref`. |
| `messages[]` | Bundle-wide notes; includes the `ingestion_succeeded` info message. |

### Transactional behavior

Submit imports the entire bundle in a **single transaction**. Any failure
mid-import rolls back the submission, audit events, record links, and
every scientific row. There are no partial imports. Either all the
records land or none of them do.

## Step 4: Interpret pending / unreviewed status

This is the central policy of submit/import v0.

| State | What it means |
|-------|---------------|
| `submitted` | An authenticated hosted user posted a bundle. |
| `imported` | Records were persisted through the normal upload workflows. |
| **`pending`** | The submission has been created and is awaiting curator review. **Not** approved. |
| **`unreviewed`** | No curator has yet evaluated the submission or its records. |
| `accepted` / `approved` / `curated` | **Not** produced by this milestone. |

Concretely:

- The submit/import endpoint **writes** records.
- Imported records are **publicly visible** through normal read paths
  where applicable (deployments that gate visibility on
  `Submission.is_public` will still hide them, since `is_public` only
  returns `True` for `approved` submissions).
- Imported records are **unreviewed**. `pending` does **not** mean
  curator-approved.
- Validation and successful import do **not** equal scientific
  endorsement.
- Curator review (accept / reject / queue / UI) is a future milestone.

> Imported records are visible but unreviewed. Use them with appropriate
> caution until a curator reviews the submission.

## Troubleshooting

### `401 Unauthorized` on dry-run or submit

- Missing `X-API-Key` header. Add `-H "X-API-Key: $HOSTED_TCKDB_API_KEY"`.
- Wrong key: a key minted on the **local** instance does not
  authenticate against hosted. Use a key issued by the hosted instance.
- Revoked or expired key. Mint a new one on the hosted instance.

### Wrong base URL

- `$HOSTED_TCKDB_BASE_URL` should end in `/api/v1`. The bundle
  endpoints are `${BASE_URL}/bundles/dry-run` and
  `${BASE_URL}/bundles/submit`. Double `/api/v1/api/v1/...` paths return
  `404`.
- Pointing at the local URL (`http://localhost:8000/...`) sends the
  bundle to the local instance and is almost never what you want.

### Dry-run succeeds but submit fails

- Submit applies a **strict** gate: `errors > 0` or `unsupported > 0`
  in the dry-run will block submit even when `bundle_valid` is `true`.
  Re-read the dry-run `items[]` and `messages[]` carefully.
- The submit endpoint may also surface **transactional** failures from
  the underlying upload workflows that the dry-run did not predict
  (e.g. write-time integrity violations). The whole bundle is rolled
  back on any such failure — fix the underlying issue and resubmit.
- Authentication may differ between calls if the API key was rotated
  between dry-run and submit.

### Dry-run reports `unsupported`

- The bundle uses a feature outside the v0 surface (e.g. inline
  calculations on thermo, an unsupported bundle family). Submit will be
  blocked. Either remove the unsupported content from the bundle or
  wait for a later milestone.

### Dry-run reports `error`

- The bundle is structurally invalid for hosted import. The `reason`
  and `messages[]` fields explain why. Common cases: missing identity,
  hash collision against an incompatible existing row, malformed
  provenance.

### Network / VPN / firewall

- `curl: (6) Could not resolve host` — DNS or VPN issue. Confirm you
  can resolve and reach the hosted host.
- `curl: (7) Failed to connect` — firewall or wrong port. Confirm the
  hosted base URL is reachable.
- `curl: (35) SSL` — TLS/cert issue. Do **not** disable cert
  verification (`-k`) silently; investigate the cert chain.

### "`pending` does not mean approved"

- This is by design. `pending` is the initial moderation state. Records
  are visible-but-unreviewed. Quote the `submission_id` when asking a
  curator to review.

## Security notes

- **Do not commit API keys.** Keep them in environment variables or a
  local secret manager. The placeholder values in this doc
  (`tck_hosted_replace_me`) are illustrative only.
- **Do not commit bundle files** that contain unpublished data unless
  you intend to publish them. A `.tckdb.json` bundle is a portable copy
  of selected scientific content.
- **Hosted actor identity comes from hosted authentication.** Local
  exporter metadata in the bundle (`exporter.local_user_label`,
  `orcid`, `affiliation`, `email`) is preserved as **provenance only**.
  It is not the hosted actor and is never trusted as an identity claim.
- **Local primary keys are not hosted identities.** Bundle local-refs
  (`local_refs`) and embedded local IDs are debug/traceability hints;
  hosted resolution always works from scientific content.
- **This is not raw database sync.** The bundle path is a curated,
  validated, authenticated submission — bundles cannot bypass hosted
  validation, dedup, or moderation.
- **Hosted writes attribute everything to the authenticated hosted
  user.** `submission.created_by` and every imported row's `created_by`
  point at the hosted user whose API key submitted the bundle.

## Non-goals

This v0 manual flow does **not** include:

- frontend local-to-hosted UX,
- local push-to-hosted automation,
- a curator review UI / queue,
- new backend routes (only the existing `/bundles/dry-run` and
  `/bundles/submit` are used),
- new bundle schema or families (only `thermo` and `kinetics`),
- raw database synchronization,
- artifact (log/geometry) import,
- network / statmech / transport / computed-reaction bundles,
- service accounts.

If a feature is not listed in steps 1–4 above, it is not part of v0.

## Future UX path

The manual command-line flow is the floor, not the ceiling. Future
milestones on the
[implementation plan](../roadmaps/local-offline-and-hosted-submission-implementation-plan.md)
will layer on:

- a frontend bundle authoring + upload page on the local instance,
- a frontend dry-run / submit / review page on the hosted instance,
- an optional local "push to hosted" UX for one-click contribution,
- a curator review workflow with accept / reject / queue semantics,
- richer record linkage across the bundle's identity/provenance closure,
- artifact packaging and import,
- support for additional bundle families.

The bundle format and the hosted endpoints are stable contracts — both
the manual CLI flow documented here and any future UX share the same
files, the same endpoints, and the same authentication model.

## Optional: helper script

A minimal Python helper that reads `TCKDB_BASE_URL` and `TCKDB_API_KEY`
from the environment, accepts a bundle path, and supports `--dry-run`
or `--submit` modes via the HTTP API only is provided at
[`examples/clients/submit_bundle.py`](../../examples/clients/submit_bundle.py).
It contains no business logic and is functionally equivalent to the
`curl` examples above.

## See also

- [Bundle v0 format](v0-format.md)
- [Local export v0](local-export-v0.md)
- [Hosted dry-run v0](hosted-dry-run-v0.md)
- [Hosted submit v0](hosted-submit-v0.md)
- [Generic client targeting](../clients/generic-client-targeting.md)
- [Deployment guide overview](../deployment/README.md) — TCKDB
  deployment scenarios and client environments
- [Single-machine private deployment](../deployment/local-v0.md) —
  laptop/dev source instance
- [Shared private deployment](../deployment/shared-private-deployment.md) —
  shared lab/group instance as a bundle source
- [Client access from HPC](../deployment/client-access-from-hpc.md) —
  submitting from HPC jobs
- [DR-0023: Local/Offline and Hosted Submission Model](../decisions/0023-local-offline-and-hosted-submission-model.md)
- [Implementation plan](../roadmaps/local-offline-and-hosted-submission-implementation-plan.md)
