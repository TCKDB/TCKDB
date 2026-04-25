# Client access from HPC

This document explains how to use TCKDB from **HPC clusters and batch
compute environments**.

> **HPC is a client environment, not a deployment scenario.** HPC jobs
> normally act as TCKDB API clients — they authenticate with a
> user-owned API key and submit results to a reachable TCKDB API,
> typically a [shared private deployment](shared-private-deployment.md)
> on a lab service node. Jobs do **not** run a TCKDB
> database/backend per compute job.

This is a documentation page, not a deployment recipe. If you are
looking for "how do I deploy TCKDB on HPC?", the answer in almost all
cases is "you don't — you deploy it on a lab service node and let HPC
jobs talk to it as clients." See
[the deployment guide overview](README.md) for the wider taxonomy.

Apptainer/Singularity image support is **deferred packaging**, not an
active deployment path; see
[§Apptainer/Singularity (deferred packaging)](#apptainersingularity-deferred-packaging)
below.

---

## Why not Docker on HPC?

Most HPC clusters do **not** allow the Docker daemon on compute or
login nodes. The Docker model assumes root-equivalent privileges and
shared host networking that clusters typically reserve for system
administrators. Even when Docker is technically available it is rarely
the right tool inside batch jobs.

What clusters do allow varies, but commonly:

- **Apptainer / Singularity** — daemonless, rootless, single-image-file
  containers designed for HPC.
- **Module-loaded native software** — Python environments, Conda /
  Miniforge, Spack, etc.
- **Network access** to lab/institutional services from compute nodes
  (often, with caveats — see
  [Network restrictions](#network-restrictions-and-loginservice-node-caveats)).

The Docker Compose quick-start
([local-v0.md §Start the local stack](local-v0.md#1-start-the-local-stack))
is therefore not something an HPC compute node can run. Crucially, it
should not need to.

---

## Recommended pattern: submit to a reachable TCKDB API

The supported pattern is "HPC job as TCKDB client":

```text
HPC compute node                       Reachable TCKDB API
┌────────────────────────────┐         ┌──────────────────────────┐
│ batch job (sbatch / qsub)  │         │ shared private /         │
│   → quantum chemistry run  │         │ hosted /                 │
│   → parses results         │  HTTPS  │ single-machine private   │
│   → POST /api/v1/uploads…  │ ──────► │ TCKDB deployment         │
│      X-API-Key: tck_…      │         │   PostgreSQL+RDKit       │
└────────────────────────────┘         │   MinIO / S3 artifacts   │
                                       └──────────────────────────┘
```

Properties of this pattern:

- **No database, no backend, no MinIO** runs in the compute job. The
  job is *just* a TCKDB client.
- The job needs only `TCKDB_BASE_URL` and `TCKDB_API_KEY`; everything
  else is the same as any other API client. See
  [generic-client-targeting.md](../clients/generic-client-targeting.md).
- The job does not embed scientific resolution, dedup, or moderation
  logic — those live on the API side.
- Failure modes are well-bounded: a job that cannot reach the API can
  stage results to disk and upload later from a login/service node.

Anti-patterns (don't do these):

- ❌ Running a per-job PostgreSQL+RDKit database in the job's scratch
  directory.
- ❌ Bundling the TCKDB backend into a job container so each job has
  its own isolated DB.
- ❌ Sharing a single lab-wide API key across every user's job scripts.
- ❌ Putting API keys in `sbatch` scripts committed to git.

---

## Shared private deployment as the normal HPC target

For most labs the right HPC target is a
**[shared private deployment](shared-private-deployment.md)** running
on a service node the cluster's compute nodes can reach. That host
runs the persistent TCKDB stack; HPC jobs are clients that
authenticate with per-user API keys.

Why a shared private deployment, not the hosted/community instance, by
default:

- Raw computational chemistry results often start out unpublished. A
  private deployment keeps them private.
- Network policies frequently allow HPC → lab subnet traffic but not
  HPC → public internet.
- Per-lab admin policy (registration, key rotation, backups) is owned
  by people who actually know the science.
- Selected records can still be promoted to hosted via the
  [manual local-to-hosted contribution flow](../contribution-bundles/manual-local-to-hosted-v0.md)
  when ready.

If the lab does not (yet) have a shared deployment and the data is
appropriate for the community DB, jobs can target hosted directly using
the hosted base URL + a hosted API key. The targeting model is
identical.

---

## API client configuration from jobs

From the job's perspective, TCKDB is just an HTTP API. The
configuration surface is two values:

```bash
export TCKDB_BASE_URL="https://tckdb.lab.example.org/api/v1"
export TCKDB_API_KEY="tck_replace_me"
```

A minimal smoke-test from inside a job:

```bash
curl -sf "$TCKDB_BASE_URL/auth/me" \
  -H "X-API-Key: $TCKDB_API_KEY"
```

A 200 with the user profile means the key is valid and the API is
reachable. Upload calls work the same way; see
[generic-client-targeting.md §Examples](../clients/generic-client-targeting.md#examples)
and the per-workflow upload docs for payload shapes.

---

## Environment variables for batch jobs

Inject TCKDB credentials into batch jobs via environment, not by
hard-coding them.

### Slurm

```bash
#!/usr/bin/env bash
#SBATCH --job-name=arc-run
#SBATCH --time=12:00:00
#SBATCH --export=NONE,TCKDB_BASE_URL,TCKDB_API_KEY

set -euo pipefail
: "${TCKDB_BASE_URL:?must be set in the submitter's environment}"
: "${TCKDB_API_KEY:?must be set in the submitter's environment}"

# … run quantum chemistry, parse, then upload …
curl -sf -X POST "$TCKDB_BASE_URL/uploads/<workflow>" \
  -H "X-API-Key: $TCKDB_API_KEY" \
  -H "Content-Type: application/json" \
  --data @payload.json
```

The `--export=NONE,TCKDB_BASE_URL,TCKDB_API_KEY` form forwards exactly
these two values from the submitter's shell into the job, so the
secret never has to live in the script file.

### PBS / Torque

```bash
#PBS -v TCKDB_BASE_URL,TCKDB_API_KEY
```

### Sourcing from a per-user env file

If the cluster wraps env passthrough awkwardly, store credentials in a
gitignored file under the user's home directory and source it from the
job:

```bash
# ~/.config/tckdb/env  (chmod 600)
export TCKDB_BASE_URL="https://tckdb.lab.example.org/api/v1"
export TCKDB_API_KEY="tck_replace_me"
```

```bash
# inside the job
. "$HOME/.config/tckdb/env"
```

A starter file is provided at
[`examples/deployment/hpc-client.env.example`](../../examples/deployment/hpc-client.env.example).

---

## Network restrictions and login/service-node caveats

HPC networking varies by site. Common patterns:

- **Compute nodes can reach lab subnets.** Submit directly from the
  job. This is the happy path.
- **Compute nodes are isolated from external networks but can reach
  cluster-internal services.** Run the shared deployment on a host the
  cluster routes to (often called a "service node" or "data node");
  jobs talk to it directly.
- **Compute nodes have no outbound networking at all.** Stage results
  to scratch / shared storage during the job, then submit from a
  **login or service node** after the job finishes.
- **Outbound HTTPS is policy-restricted.** Ask the institution's
  networking team for an allow-list rule for the lab deployment's
  hostname, or use the lab VPN if the cluster supports it.

If you stage-and-upload, design the staged artifact to be replayable:
the upload step should be idempotent on the API side via the existing
identity/dedup model.

Login-node uploads are normal. They are not a workaround — they are a
supported pattern for clusters with restricted egress.

---

## Credential handling on shared clusters

Treat API keys as bearer credentials. On a shared cluster, that
matters more than usual.

- **One API key per user.** Do not share a lab-wide key, even across
  job scripts in one user's home directory.
- Store keys in a per-user file with `chmod 600`, or in a per-job env
  variable forwarded by the scheduler. **Never** commit them to a git
  repo or paste them into a shared notebook.
- Use **distinct keys** for distinct environments
  (`alice-laptop`, `alice-hpc`, `alice-ci`) so you can revoke just one
  if it leaks.
- Rotate proactively. Mint a new key, deploy, then revoke the old.
  Multiple concurrent keys per user are supported.
- Revoke immediately on suspected leak (`DELETE /auth/api-keys/{id}`
  on the *issuing* deployment — each TCKDB deployment issues its own
  keys and they are independent).
- Roles travel with keys. A key minted by a curator has curator
  privileges. Mint keys from the lowest-privilege account that
  satisfies the use case.
- HPC scheduler logs sometimes include the job environment. If your
  cluster does this, prefer sourcing keys from a `chmod 600` file
  inside the job rather than `--export`-ing them.

Full safety surface:
[generic-client-targeting.md §API-key safety](../clients/generic-client-targeting.md#api-key-safety).

---

## Anti-patterns

Calling these out explicitly because they keep coming up:

- ❌ **Per-job database.** Don't bring up a PostgreSQL+RDKit container
  in every compute job and tear it down at job end. You lose all
  cross-job identity, dedup, and provenance, and you turn every job
  into a private fork of TCKDB.
- ❌ **Per-job full backend.** Even with Apptainer, do not run the
  TCKDB API inside each compute job. It is not what TCKDB is for.
- ❌ **Lab-wide shared API key.** Loses attribution; one leak revokes
  for everyone.
- ❌ **Keys in committed scripts.** Treat them like passwords.
- ❌ **Pointing at the hosted instance for raw, unpublished data**
  when you have a shared private deployment available. Hosted is the
  publication endpoint, not a scratch space.

---

## Apptainer/Singularity (deferred packaging)

Apptainer/Singularity is the **likely** container path for any TCKDB
component that needs to run inside an HPC job (for example, a future
CLI uploader bundling its own dependencies). It is **deferred
packaging — not an active deployment path** and not part of the
current deployment story. This section exists so that the question
"what about Apptainer?" has an answer; nothing here is shipped.

Status today:

- **Backend image** — depends on a resolved Python dependency manifest
  for the API/worker, which is tracked separately in
  [backend-container-packaging-spec.md](../roadmaps/backend-container-packaging-spec.md).
  An Apptainer backend image cannot land before that. Even when it
  does, **a per-job backend container is the wrong pattern** — see
  [Anti-patterns](#anti-patterns) above. The realistic Apptainer use
  case for the backend is a long-running service container on a lab
  service node, *not* something a compute job spins up.
- **PostgreSQL + RDKit** — the cartridge image is already
  containerized upstream. On HPC, the right place to run
  PostgreSQL+RDKit is on a lab service node hosting a shared private
  deployment, not inside compute jobs.
- **CLI / uploader images** — when a future CLI client exists, an
  Apptainer image of *just the CLI* would be a reasonable HPC
  packaging artifact. It is not in scope here.

This document does **not** ship `.def` files, image build scripts, or
registry coordinates. When Apptainer support lands it will be a
dedicated milestone with its own spec; in the meantime the recommended
HPC story is "your shared deployment runs the backend; your jobs call
its API."

---

## Native install as fallback

If Docker is unavailable on the lab service node and Apptainer
packaging has not yet shipped, the lab can run TCKDB natively on a
host you control (workstation or shared service node). Native install
is one of several
[infrastructure strategies](README.md#infrastructure-strategies);
it is not a separate deployment scenario. See
[native-advanced.md](native-advanced.md) for required components and
caveats.

A natively-installed deployment still acts as a normal HPC target:
same `base_url` + `api_key` model, same upload endpoints. Only the
operator-side install method differs.

---

## Non-goals

This document does **not** cover:

- Apptainer / Singularity image build files (deferred — see
  [§Apptainer/Singularity (deferred packaging)](#apptainersingularity-deferred-packaging)).
- A backend Dockerfile (tracked in
  [backend-container-packaging-spec.md](../roadmaps/backend-container-packaging-spec.md)).
- Native install automation.
- Cluster-specific deployment recipes (Slurm/PBS/LSF specifics beyond
  the env-variable patterns above).
- Service accounts.
- Direct database access from compute nodes.
- Raw database synchronization between HPC scratch and a lab DB.
- A frontend on HPC.

---

## See also

- [Deployment guide overview](README.md)
- [Shared private deployment](shared-private-deployment.md) — the
  usual HPC target
- [Single-machine private deployment](local-v0.md) — the Docker
  Compose quick-start that the shared scenario reuses
- [Native advanced install](native-advanced.md) — fallback
  infrastructure strategy
- [Generic client targeting](../clients/generic-client-targeting.md)
- [Manual local-to-hosted contribution flow](../contribution-bundles/manual-local-to-hosted-v0.md)
- [DR-0022 — Auth and Roles v1](../decisions/0022-auth-and-roles-v1.md)
- [DR-0023 — Local/Offline and Hosted Submission Model](../decisions/0023-local-offline-and-hosted-submission-model.md)
- [Implementation plan](../roadmaps/local-offline-and-hosted-submission-implementation-plan.md)
