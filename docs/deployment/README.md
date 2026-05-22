# TCKDB deployment guide

TCKDB is **one application** with one schema, one API, and one auth
model. The pages in this directory describe the **scenarios** in which
that one application can be deployed and the **client environments**
that talk to it. They are not separate products, modes, or editions.

---

## One application, many scenarios

```text
TCKDB
  one schema      (backend/app/db/models/, single Alembic chain)
  one API         (backend/app/api/, FastAPI)
  one auth model  (DR-0022: sessions for humans, API keys for clients)

deployment scenarios               client environments
  single-machine private             laptop scripts
  shared private (lab/group)         notebooks
  hosted community                   workflow tools
                                     HPC jobs
                                     future CLI / frontend
```

The deployment scenario is **operational policy** (who can register,
where the API is reachable, how it is backed up). The client
environment is **how a user or job talks to a deployed instance**. All
clients use the same two values: `base_url` + `api_key`. See
[Generic client targeting](../clients/generic-client-targeting.md).

---

## Deployment scenarios

| Scenario | Page | One-line summary |
|---|---|---|
| Single-machine private | [local-v0.md](local-v0.md) | TCKDB privately on one host (laptop, dev workstation, single-user setup). |
| Shared private | [shared-private-deployment.md](shared-private-deployment.md) | The same TCKDB backend deployed on a shared lab/group machine. Closed registration, per-user API keys, backups, reverse proxy. |
| Hosted community | (operator-managed) | Public/community instance with stricter operations and review workflows. Operated separately. |

The shared private scenario is sometimes called a "lab-server"
deployment in the older docs. It is the **same backend** as
single-machine; the difference is operational (closed registration,
networking, backups, access control).

---

## Client environments

Clients talk to a deployed TCKDB instance over HTTP, using a
`base_url` and an `api_key`. The same client code works against any
deployment scenario by changing those two values.

| Environment | Page | Notes |
|---|---|---|
| Any HTTP client (script, notebook, workflow tool, future CLI) | [Generic client targeting](../clients/generic-client-targeting.md) | The canonical client model. |
| HPC compute jobs | [client-access-from-hpc.md](client-access-from-hpc.md) | Special considerations for batch/HPC: networking, login-node uploads, credential handling. **HPC is a client environment, not a deployment.** |

---

## Infrastructure strategies

Bringing up a TCKDB deployment requires PostgreSQL+RDKit, the Python
backend (`tckdb_env`), and S3-compatible artifact storage. There are
several **infrastructure strategies** for provisioning those — they
cut across deployment scenarios and are not scenarios themselves:

| Strategy | Page | When |
|---|---|---|
| Docker Compose quick-start | [local-v0.md §Start the local stack](local-v0.md#1-start-the-local-stack) | Easiest path; reference for both single-machine and shared private deployments. |
| Managed PostgreSQL+RDKit | (no dedicated page) | When the lab or institution already runs a PostgreSQL+RDKit service. Point `DB_HOST` at it; rest of the stack is unchanged. |
| Native install | [native-advanced.md](native-advanced.md) | Advanced fallback when Docker and Apptainer are both unavailable. |
| Apptainer / Singularity | (deferred) | Future packaging; tracked as a future milestone. See [client-access-from-hpc.md §Apptainer/Singularity (deferred packaging)](client-access-from-hpc.md#apptainersingularity-deferred-packaging). |

A backend container image is tracked separately in
[backend-container-packaging-spec.md](../roadmaps/backend-container-packaging-spec.md);
when it lands, Docker Compose becomes a single-command deployment.

---

## Maintainability rule

> TCKDB maintains **one** application and **one** schema.
>
> Deployment documentation may describe multiple environments, but
> these must not create separate code paths, separate schemas, or
> separate feature sets.
>
> A new deployment recipe is acceptable only if it reuses the same
> backend, same migrations, same auth model, and same API. If it
> needs a fork, it isn't a deployment recipe.

This is the rule the rest of this directory follows.

---

## See also

- [Production checklist](production_checklist.md) — the canonical list of env vars and pre-flight checks that must hold before any hosted / shared / public deployment is exposed
- [Generic client targeting](../clients/generic-client-targeting.md) — the canonical `base_url` + `api_key` client model
- [DR-0022 — Auth and Roles v1](../decisions/0022-auth-and-roles-v1.md) — auth model used by every deployment
- [DR-0023 — Local/Offline and Hosted Submission Model](../decisions/0023-local-offline-and-hosted-submission-model.md) — same-schema commitment
- [Implementation plan](../roadmaps/local-offline-and-hosted-submission-implementation-plan.md) — milestone tracking
- [Deployed-DB migration playbook](../../backend/docs/deployment/migrations.md) — operator runbook for `alembic upgrade` on a real database (bootstrap, upgrade, backup, rollback, public-ref backfill)
