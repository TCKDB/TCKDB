# Clients on HPC

Cluster jobs use TCKDB as clients. A job authenticates with its owner's API
key and sends results to a TCKDB that the cluster can reach, usually a
[lab server](shared-private-deployment.md). Jobs never run a TCKDB database
or API of their own.

```text
compute node                             TCKDB server
batch job (sbatch / qsub)                lab server or public instance
  runs the calculation                     API
  parses the results          HTTPS        Postgres + RDKit
  POST /api/v1/uploads/...   ------->      object store
  X-API-Key: tck_...
```

## Why not run TCKDB in the job

Most clusters do not allow Docker on login or compute nodes, and a job does
not need it. A job needs only `TCKDB_BASE_URL` and `TCKDB_API_KEY`. Identity
resolution, deduplication and review all happen on the server.

A job that cannot reach the API can write its results to disk and upload them
later from a login node.

## Which server to target

Usually the lab server:

- New results are often unpublished, and a lab server keeps them private.
- Clusters often allow traffic to the lab network but not to the internet.
- The people who run the lab server set its accounts, keys and backups.

Selected records can go to a public instance later through the
[local-to-hosted flow](../contribution-bundles/manual-local-to-hosted.md).
If the lab has no server and the data is meant to be public, a job can
target a public instance directly with that instance's URL and key.

## Configure the job

```bash
export TCKDB_BASE_URL="https://tckdb.lab.example.org/api/v1"
export TCKDB_API_KEY="tck_..."
```

Check the key and the route from inside a job:

```bash
curl -sf "$TCKDB_BASE_URL/auth/me" -H "X-API-Key: $TCKDB_API_KEY"
```

A `200` with your user means the key works and the API is reachable. Upload
calls send the same header; see
[Configuring a client](../clients/generic-client-targeting.md#examples) for
payloads.

### Slurm

Forward the two values from the submitting shell, so the key never sits in
the script:

```bash
#!/usr/bin/env bash
#SBATCH --job-name=arc-run
#SBATCH --time=12:00:00
#SBATCH --export=NONE,TCKDB_BASE_URL,TCKDB_API_KEY

set -euo pipefail
: "${TCKDB_BASE_URL:?must be set in the submitter's environment}"
: "${TCKDB_API_KEY:?must be set in the submitter's environment}"

# ... run the calculation, parse it, then upload ...
curl -sf -X POST "$TCKDB_BASE_URL/uploads/<workflow>" \
  -H "X-API-Key: $TCKDB_API_KEY" \
  -H "Content-Type: application/json" \
  --data @payload.json
```

### PBS / Torque

```bash
#PBS -v TCKDB_BASE_URL,TCKDB_API_KEY
```

### A per-user env file

Some schedulers log the job environment. On those, keep the values in a file
only you can read and source it inside the job:

```bash
# ~/.config/tckdb/env  (chmod 600)
export TCKDB_BASE_URL="https://tckdb.lab.example.org/api/v1"
export TCKDB_API_KEY="tck_..."
```

```bash
. "$HOME/.config/tckdb/env"
```

A starter file is
[`examples/deployment/hpc-client.env.example`](../../examples/deployment/hpc-client.env.example).

## Network restrictions

Networking differs from site to site:

- If compute nodes reach the lab network, upload from the job.
- If they reach only hosts inside the cluster, run the lab server on a host
  the cluster routes to, often called a service or data node.
- If they have no network at all, write results to scratch or shared storage
  during the job and upload from a login node afterwards. Clusters with
  restricted egress use this pattern routinely. Stage the results so they can
  be replayed: the upload step should be idempotent on the server, through
  its identity and deduplication rules.
- If outbound HTTPS is restricted, ask the cluster's network team to allow
  the lab server's hostname, or use the lab VPN.

## Keys on a shared cluster

- One key per person, never one for the whole lab.
- Keep keys in a `chmod 600` file or forward them through the scheduler.
  Never commit them or paste them into a shared notebook.
- Use a separate key for each place (`alice-laptop`, `alice-hpc`, `alice-ci`),
  so a leak means revoking one.
- Revoke a leaked key at once with `DELETE /auth/api-keys/{id}` on the
  instance that issued it. Each instance issues its own keys.
- A key carries its owner's role. Mint keys from the account with the least
  privilege that does the job.

## Don't

- Start a Postgres+RDKit database, or a TCKDB API, inside each job. Every job
  becomes a private copy of TCKDB with no shared identity or provenance.
- Share one key across the lab.
- Put keys in scripts that are committed.
- Send raw, unpublished results to a public instance when a lab server
  exists.

## Containers on clusters

TCKDB publishes no Apptainer or Singularity image. The server belongs on a
service node, not in a job, and a job needs only `curl` or `tckdb-client`. If
the lab cannot run Docker on its service node, use
[Without Docker](native-advanced.md).
