# TCKDB deployment modes

## Purpose

TCKDB is one application — one schema, one API, one auth model — that
can be run in several operational scenarios. This document is the
plain-language map of those scenarios, oriented around two questions
operators and contributors actually ask:

1. Do I need a domain or internet connection to use TCKDB?
2. Where does my data live and who can read or write it?

The scenarios described here are deployment **modes**, not separate
products or codepaths. A workflow tool, a notebook, or a CLI talks to
all of them through the same `base_url` + `api_key` client model. See
[Generic client targeting](../clients/generic-client-targeting.md) and
the deployment [README](README.md) for the wider context.

The three modes are:

```text
1. local/offline TCKDB
2. self-hosted online TCKDB
3. hosted/community TCKDB
```

## Mode 1: local/offline TCKDB

A TCKDB instance running on a single machine or private network with
no public exposure.

- No domain required.
- No public internet required.
- Runs on `localhost` or a private/LAN address.
- Suitable for a laptop, workstation, single-user lab server, or an
  air-gapped HPC login node.
- Workflow tools (ARC, RMG, custom scripts, future CLI) point to the
  local/private API URL.
- The hosted-mode read-hardening toggles
  (`LEGACY_READS_REQUIRE_AUTH`, `ALLOW_PUBLIC_INTERNAL_IDS=false`,
  rate limits, hidden OpenAPI) can remain enabled for parity, but
  there is no public attack surface to defend.
- Operator policy is whatever the single user or small team agrees
  on; there is no submission/review queue requirement.

Typical workflow-tool configuration:

```text
TCKDB_BASE_URL=http://127.0.0.1:8010/api/v1
TCKDB_API_KEY=...
```

The reference recipe is
[local-v0.md](local-v0.md). The single-machine private scenario in
the deployment [README](README.md) is the authoritative how-to.

## Mode 2: self-hosted online TCKDB

A TCKDB instance run by a lab, group, or institution that **is**
reachable from outside the host but is operated by a small team for
their own users and downstream workflow tools.

- A domain is **strongly recommended** for stable public access.
- Public reach is provided by a Cloudflare Tunnel or a reverse proxy
  (nginx/Caddy/Traefik) mapping a public hostname to the local API
  port. Router port-forwarding is discouraged.
- Postgres and object storage (MinIO/S3) must never be exposed
  publicly. Only the API is reachable through the tunnel/proxy.
- The canonical self-hosted single-node recipe belongs in this mode
  — see [self_hosted_single_node.md](self_hosted_single_node.md) for
  the worked example (tested on a Raspberry Pi but portable to any
  small Linux server) and the hosted-safe environment-variable
  defaults.
- Quick tunnels (random `*.trycloudflare.com` URLs, ngrok free tier,
  etc.) are acceptable **for testing only** — they rotate, lack a
  managed cert story, and are not appropriate for stable hosting.
- Authenticated uploads and (optionally) anonymous scientific reads;
  the operator chooses whether `/api/v1/scientific/*` is public.
- Backups, log rotation, and TLS are operator responsibilities; the
  Pi guide documents the minimum bar.

The shared-private variant (LAN-only lab/group server, no public
hostname) is described in
[shared-private-deployment.md](shared-private-deployment.md). It uses
the same backend and most of the same hardening toggles; only the
ingress changes.

## Mode 3: hosted/community TCKDB

A community-facing TCKDB instance run by a group or institution as a
shared scientific resource.

- Run by a designated operator team.
- Public scientific reads under `/api/v1/scientific/*`.
- Authenticated uploads and imports only.
- Submission, moderation, and review workflows are exercised in
  earnest — anonymous uploads are not permitted, and contributed
  records pass through the documented review queue (see
  [submission-moderation-schema.md](../submission-moderation-schema.md)
  and
  [species-entry-review.md](../species-entry-review.md)).
- May receive contribution bundles from offline/local instances — the
  cross-instance data-movement path is the export/import flow
  documented in
  [export_import_roadmap.md](../roadmaps/export_import_roadmap.md).
- Operationally indistinguishable from a self-hosted instance from
  the backend's perspective; what changes is the policy posture and
  the operator's commitments to availability, backups, and review SLA.

## Comparison table

| Property | Mode 1 — local/offline | Mode 2 — self-hosted online | Mode 3 — hosted/community |
|---|---|---|---|
| Audience | One user / small team | Lab, group, institution | Public scientific community |
| Network reach | `127.0.0.1` or LAN | Public hostname via tunnel/proxy | Public hostname |
| Domain | Not required | Strongly recommended | Required |
| Internet required at runtime | No | Yes (for tunnel) | Yes |
| TLS | Not required | Required (edge or origin) | Required |
| Anonymous reads | N/A (no public surface) | Operator choice | Yes (throttled) |
| Uploads | Any local user | Seeded accounts | Seeded accounts + review queue |
| Review/moderation workflow | Optional | Optional | Required |
| Backups | Operator choice | Required | Required, with off-box copy |
| Receives contribution bundles | No (origin) | Optional | Yes (primary import target) |
| Reference recipe | [local-v0.md](local-v0.md) | [self_hosted_single_node.md](self_hosted_single_node.md), [shared-private-deployment.md](shared-private-deployment.md) | (operator-managed) |

## Domain and tunnel requirements

```text
Offline/local:
  no domain
  no tunnel
  no public DNS

Testing online tunnel:
  temporary trycloudflare-style URL acceptable
  do NOT distribute the URL to downstream consumers
  rotate or tear down after the test

Stable public Pi/server:
  a domain (or a controlled public hostname under one) is strongly
  recommended
  named Cloudflare Tunnel or a reverse proxy with a managed cert

Institutional:
  institutional domain, institutional reverse proxy, or institutional
  Cloudflare/CDN account
  TLS terminated by the institutional ingress
```

A domain is **never** required to use TCKDB. It is required only when
you want a stable, externally-reachable URL.

## Air-gapped HPC notes

TCKDB is usable on an air-gapped HPC environment as a Mode 1
(local/offline) deployment. The constraints to keep in mind:

- No external auth dependency. TCKDB's auth is fully self-contained
  (sessions for humans, API keys for clients) — no external IdP,
  OAuth provider, or DNS lookup is required at runtime.
- Local API only. The API binds to `127.0.0.1` (or a head-node
  internal address) and is reached over the cluster's internal
  network. Do not assume any hosted TCKDB is reachable from compute
  nodes.
- No Cloudflare. The hosted-Pi tunnel pattern does not apply; there
  is nothing to tunnel to.
- No online docs. The `/docs` Swagger UI can be left enabled
  (`EXPOSE_API_DOCS=true`) on a local-only instance, since there is
  no public surface; consult this repository's `docs/` tree for
  reference material that does not require internet access.
- Export files manually after job completion. When data is to leave
  the air-gapped environment, the export path is the contribution
  bundle, not a database dump — see
  [export_import_roadmap.md](../roadmaps/export_import_roadmap.md).
- Workflow tools should always use the configured `TCKDB_BASE_URL`
  and `TCKDB_API_KEY` rather than hard-coded URLs, so the same code
  works against the air-gapped local instance and against any future
  hosted target.

For the broader HPC client picture (login-node uploads, credential
handling, batch workflows), see
[client-access-from-hpc.md](client-access-from-hpc.md). HPC is a
**client environment**; the deployment mode it talks to is whichever
TCKDB instance it is configured against.

## Security posture by mode

| Concern | Mode 1 — local/offline | Mode 2 — self-hosted online | Mode 3 — hosted/community |
|---|---|---|---|
| Public attack surface | None | Tunnel/proxy ingress only | Public ingress |
| Internal-ID exposure | Tolerated | `ALLOW_PUBLIC_INTERNAL_IDS=false` | `ALLOW_PUBLIC_INTERNAL_IDS=false` |
| Legacy entity routes | Open | `LEGACY_READS_REQUIRE_AUTH=true` | `LEGACY_READS_REQUIRE_AUTH=true` |
| Open registration | Operator choice | Off (`AUTH_ALLOW_OPEN_REGISTRATION=false`) | Off; seeded accounts |
| Rate limiting | Optional | Required | Required |
| OpenAPI / Swagger | Operator choice | Hidden (`EXPOSE_API_DOCS=false`) | Hidden |
| TLS | Not required | Required (edge cert via Cloudflare or origin cert via proxy) | Required |
| Secrets management | `.env` on local disk | `.env.selfhosted` outside repo, restricted perms | Operator-managed (vault/secret store) |

The full hosted-mode threat model and abuse controls are documented in
[security_public_read_abuse_audit.md](../audits/security_public_read_abuse_audit.md)
and
[public_read_abuse_controls.md](../specs/public_read_abuse_controls.md).

## Data movement between modes

There is **no built-in synchronization** between TCKDB instances.
Cross-instance data movement happens through explicit contribution
bundles:

```text
Mode 1 (local/offline)  --export-->  bundle file  --import-->  Mode 2 or 3
Mode 2 (self-hosted)    --export-->  bundle file  --import-->  Mode 3
Mode 3 (community)      --export-->  bundle file  --import-->  any target
```

Bundle export and import:

- preserve scientific identity, provenance, artifact hashes, and
  literature/software references;
- do **not** carry raw database PKs as stable identity — public
  refs and content-derived identities are the carrier;
- support dry-run, validate-only, and submit-for-review modes on
  import.

The full design and phased rollout are in
[export_import_roadmap.md](../roadmaps/export_import_roadmap.md). The
bundle format work in progress is tracked under
[`docs/contribution-bundles/`](../contribution-bundles/) and the
related roadmap specs (`local-bundle-export-v0-spec.md`,
`hosted-bundle-dry-run-import-spec.md`,
`hosted-bundle-submit-v0-spec.md`,
`manual-local-to-hosted-flow-v0-spec.md`).

## Related documentation

- [README.md](README.md) — deployment guide overview, scenarios vs
  client environments, infrastructure strategies.
- [local-v0.md](local-v0.md) — single-machine private (Mode 1)
  reference.
- [shared-private-deployment.md](shared-private-deployment.md) —
  shared LAN-only lab/group instance.
- [self_hosted_single_node.md](self_hosted_single_node.md)
  — hosted Pi (Mode 2) reference.
- [client-access-from-hpc.md](client-access-from-hpc.md) — HPC as a
  client environment.
- [native-advanced.md](native-advanced.md) — advanced native install
  fallback.
- [Generic client targeting](../clients/generic-client-targeting.md)
  — `base_url` + `api_key` model for all clients.
- [public_hosted_querying.md](../guides/public_hosted_querying.md) —
  framing-neutral entry point for the public read surface.
- [workflow_tool_scientific_reads.md](../guides/workflow_tool_scientific_reads.md)
  — workflow-tool integration patterns.
- [export_import_roadmap.md](../roadmaps/export_import_roadmap.md) —
  cross-instance data movement.
- [DR-0023 — Local/Offline and Hosted Submission Model](../decisions/0023-local-offline-and-hosted-submission-model.md).
