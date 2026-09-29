# Hosted bundle dry-run v0

The hosted dry-run endpoint lets an authenticated user POST a
`ContributionBundleV0` and receive a structured **preview** of what a
real import would do — without creating any scientific records,
submissions, upload jobs, audit events, or record links.

This was milestone 6 of the local-offline-and-hosted-submission
implementation plan. The submit route it anticipated now exists
([`hosted-submit-v0.md`](hosted-submit-v0.md)), and since #577 a dry run
rehearses it.

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
- **Response:** `ContributionBundleDryRunResult` (HTTP `200`), including when
  submit would refuse the bundle. Exceptions: `503 dry_run_contended` when the
  rehearsal gave way to a concurrent deposit (retry), and a 5xx where submit
  itself would fail the same way.
- **Side effects:** nothing is kept. The endpoint rehearses the real
  submit inside a savepoint that is always rolled back, on a session
  that never commits (see [No-mutation guarantee](#no-mutation-guarantee)).

## Supported bundle kinds

Hosted dry-run v0 supports the same families as bundle format v0:

- `thermo`
- `kinetics`

Other families (`network`, `statmech`, `transport`, `computed_reaction`,
mixed) are rejected by `ContributionBundleV0` validation before they
reach this endpoint.

## Will submit accept it? (#577)

A dry run answers two things: the per-record preview below, and whether
`POST /bundles/submit` would accept the same bundle.

The second answer comes from **running submit itself**. After building
the preview, the route calls `submit_contribution_bundle` -- the function
the submit route calls -- inside a savepoint, and rolls the savepoint back
whatever happens. If submit would refuse, the result carries one extra
`messages[]` entry:

```json
{
  "level": "error",
  "code": "enthalpy_declaration_absent",
  "message": "Enthalpy content requires enthalpy_reference_kind. ...",
  "field": "enthalpy_reference_kind"
}
```

`code` and `message` are exactly what submit returns for the same bundle:
the refusal is rendered by the same exception handler. `bundle_valid` is
then `false`, and `summary.errors` counts it. A refusal that would be a
server error on submit (5xx) is a server error here too.

Until #577 the dry run ran only the preview, which is submit's *gate* but
not everything submit checks. Every check inside the thermo and kinetics
upload workflows -- the enthalpy-reference rule, `existing_*_id` and
public-ref resolution, ownership, role/type compatibility, reaction
anchoring, SP resolution for `energy_level_of_theory`, database
constraints -- ran on submit and never on a dry run. Copying those checks
into the preview would give the two routes a second place to disagree, so
the dry run calls submit instead. `backend/tests/api/test_api_bundle_dry_run_submit_parity.py`
holds both routes to identical refusals and requires the dry run to run
every backend function submit runs.

Like submit, the dry run reports the **first** refusal only: submit stops
at the first failing record, and so does its rehearsal.

## No-mutation guarantee

Nothing a dry run does is kept:

- The route binds the non-committing `get_db` session, not the committing
  `get_write_db` session.
- The submit rehearsal runs inside a `SAVEPOINT` that is rolled back
  whether it succeeds or fails (`app/workflows/rehearsal.py`).
- While it runs, these attempts to publish its writes are refused, and
  the request fails with a 500 rather than keeping a write:
  - `Session.commit()`, and releasing the rehearsal's savepoint through
    the ORM;
  - `Connection.commit()` on the SQLAlchemy connection the session uses;
  - SQL sent through that connection that would end or publish the
    transaction: `COMMIT`, `END`, `PREPARE TRANSACTION`, or `RELEASE` of a
    savepoint the rehearsal did not open.

  A refusal below the ORM also discards the physical connection, so the
  server aborts the transaction and no pooled connection can carry the
  writes to the next request.
- **Not guarded:** a raw DBAPI connection or cursor taken from under
  SQLAlchemy, and any other connection, engine or session the rehearsed
  code opens for itself. Nothing in the submit path does either; code that
  starts to would be outside this guarantee.
- Code being rehearsed cannot tell it is: no module under `app/workflows`,
  `app/services` or `app/chemistry` other than the rehearsal itself may
  ask whether it is inside a savepoint (a repo test enforces it), so the
  dry run takes every branch submit takes.

Two things a rollback does not undo, both accepted: sequence values the
rehearsal drew (ids are not contiguous anyway), and the Crossref/ISBN
metadata lookup made for a literature reference not already on the
instance -- the same lookup submit makes. That lookup is made *before* the
rehearsal opens, and cached in-process (by DOI/ISBN, no database ids, 24 h
TTL, 1024 entries), so a repeated dry run -- or the submit that follows it
-- does not fetch again. A lookup that failed is not cached; for that
reference the rehearsal tries again and holds its locks across the
attempt, as submit does.

## Sharing the database with real deposits

A rehearsal's inserts take the locks a real submit's do. Two rules keep a
dry run from being the reason a real deposit fails:

- It waits at most `lock_timeout` for another transaction's lock: 500 ms,
  and never more than half the server's `deadlock_timeout` (default 1 s),
  so it gives up before a real submit waiting on it would run its own
  deadlock check.
- Where the database role may (a superuser, as in the default
  deployment), it runs with `deadlock_timeout` of 10 ms, so when it closes
  a deadlock it finds the cycle, and is aborted, long before the submit
  would. Without that privilege the submit can still lose a deadlock that
  forms within the `lock_timeout` window.

A dry run that gives way answers **`503 dry_run_contended`**, with
`context.reason` `lock_timeout` or `deadlock` and `Retry-After: 1`. It
decided nothing about the bundle; retry it.

What this does not remove: a real submit that needs a row a dry run has
just inserted waits for that dry run, and PostgreSQL keeps such a row
locked until the dry-run *request's* transaction ends, not merely its
savepoint -- which is when the route returns.

Before the rehearsal opens, the route also discards the request's own
unflushed bookkeeping (`api_key.last_used_at`), which would otherwise hold
the API key's row locked for the whole rehearsal and queue a second dry
run on the same key behind the first. A dry-run session never commits, so
that value was never saved.

This reverses the v0 milestone's rule that the dry run "never relies on
transaction-rollback safety". That rule was written before submit
existed, and a preview that refuses to run submit's checks cannot predict
submit; the savepoint plus the guards above are what now carry the
guarantee.

Tests assert that row counts in the following tables are unchanged
across a dry-run, accepted or refused: `species`, `species_entry`,
`chem_reaction`, `reaction_entry`, `thermo`, `kinetics`, `submission`,
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

What is **not** previewed item by item in v0 (deferred to later
milestones) -- though every check submit applies to them is applied by the
rehearsal above:

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

| Aspect | Dry-run (this endpoint) | Submit/import (`hosted-submit-v0.md`) |
|---|---|---|
| HTTP method/path | `POST /api/v1/bundles/dry-run` | `POST /api/v1/bundles/submit` |
| Checks applied | every check submit applies (it runs submit) | all of them |
| Refusal | HTTP `200`, an `error` message with submit's `code` and message | HTTP `4xx` with that `code` and message |
| Contention | HTTP `503 dry_run_contended` (retry; nothing was decided) | waits for the lock, as any write does |
| Mutates database | never -- the rehearsal is rolled back | yes -- creates submission and scientific rows |
| Creates submission row | no | yes |
| Creates audit/record-link rows | no | yes |
| Returns | preview result with per-record `would_*` actions | submission id and moderation state |
| Idempotency | nothing is kept, so repeating it changes nothing | `Idempotency-Key` header |

Dry-run answers *"what would happen?"* without making it happen.
