# Deployed-DB migration playbook

This is the operator playbook for applying TCKDB schema migrations to a real database — local dev, lab-shared, self-hosted, or hosted. It is the runbook side of the contributor-facing policy in `.claude/rules/migration-rules.md`. Read both before touching a deployed DB.

This document supersedes any older guidance that said schema changes must be folded into the single initial migration. That rule applied while the schema was pre-deployment; it no longer does. The current policy is summarized at the top of `migration-rules.md` and assumed below.

---

## Quick reference

```bash
# All commands run from backend/, with the tckdb_env conda env.
conda run -n tckdb_env alembic current               # show current revision
conda run -n tckdb_env alembic history --verbose     # full revision graph
conda run -n tckdb_env alembic heads                 # current head(s)
conda run -n tckdb_env alembic upgrade head          # apply pending revisions
conda run -n tckdb_env alembic downgrade -1          # step back one revision
```

For Docker-Compose-based deployments where Alembic is run via the API image rather than the host conda env, use the compose variant shown later in this document.

Hosted deployments must use the separate migration-owner credentials described
in [`database_roles.md`](database_roles.md). Alembic prefers
`DB_OWNER_USER` / `DB_OWNER_PASSWORD` and refuses to use the API runtime login
when `DEPLOYMENT_MODE` is `shared_private` or `hosted_public`.

---

## Empty DB bootstrap

For a brand-new database (local dev, a fresh shared host, a CI run, a restored backup that landed an empty DB):

```bash
# 1. Confirm the database exists and is reachable.
PGPASSWORD=$DB_PASSWORD psql -h $DB_HOST -U $DB_USER -d $DB_NAME -c '\conninfo'

# 2. Hosted only: provision the role split. This installs RDKit with the
#    bootstrap administrator and transfers application ownership.
conda run -n tckdb_env python scripts/configure_database_roles.py apply

# 3. Apply all migrations.
cd backend
conda run -n tckdb_env alembic upgrade head

# 4. Verify.
conda run -n tckdb_env alembic current
```

`alembic upgrade head` is **always** the source of truth for an empty DB. Whether one revision exists or twenty, the result is identical.

After bootstrap, seed an admin user (`backend/scripts/bootstrap_admin.py`) and continue with the deployment-scenario doc you came from.

---

## Deployed DB upgrade flow

For a database that already holds real data — the lab DB, a self-hosted DB, the hosted community instance — every migration upgrade follows the same sequence.

### 1. Check what is pending

```bash
cd backend
conda run -n tckdb_env alembic current
conda run -n tckdb_env alembic history --verbose
```

Compare `current` against the head of the local branch. The output of `history` is the migration graph; the rows after `current` are pending.

### 2. Back up before touching anything

A migration that fails midway can leave the DB in a partially-upgraded state. **Always back up before running migrations on a deployed DB.**

```bash
# Plain-SQL dump (preferred — survives major Postgres upgrades).
pg_dump "$DATABASE_URL" > "tckdb_backup_$(date +%Y%m%d_%H%M%S).sql"

# Or, if DATABASE_URL is not set, use individual flags:
PGPASSWORD=$DB_PASSWORD pg_dump \
    -h $DB_HOST -p ${DB_PORT:-5432} -U $DB_USER $DB_NAME \
    > "tckdb_backup_$(date +%Y%m%d_%H%M%S).sql"
```

For larger databases, use `pg_dump --format=custom --compress=9` and `pg_restore`. Verify the dump file is non-empty before proceeding.

Artifact storage (MinIO / S3) is a separate concern. If the migration touches artifact-referencing columns, mirror the object store too — see the backup section in [shared-private-deployment.md](../../../docs/deployment/shared-private-deployment.md#backup-and-restore-basics).

### 3. Read the revision docstrings

```bash
conda run -n tckdb_env alembic show <revision_id>
```

Every revision must explain in its docstring:

- what it changes
- whether it requires a backfill
- whether the change is reversible
- any expected runtime cost on a large DB

If the docstring is silent on these and the change is non-trivial, stop and ask the author before applying.

### 4. Apply the migrations

```bash
cd backend
conda run -n tckdb_env alembic upgrade head
```

For step-by-step application (recommended when several revisions are pending and the change set is large):

```bash
conda run -n tckdb_env alembic upgrade +1
```

### 5. Verify

```bash
conda run -n tckdb_env alembic current
# Smoke-test the API.
curl -fsS http://127.0.0.1:8000/health
curl -fsS http://127.0.0.1:8000/api/v1/scientific/species/search?limit=1
```

---

## Docker Compose variant

If Alembic runs inside the API container rather than the host conda env:

```bash
# Bring up the DB (and nothing else).
docker compose up -d db

# Run migrations against it from inside the API image.
docker compose run --rm api \
    conda run -n tckdb_env alembic upgrade head

# Verify.
docker compose run --rm api \
    conda run -n tckdb_env alembic current
```

The exact service names (`db`, `api`) depend on the compose file in use; check `docker-compose.yml` at the repo root.

For `pg_dump` from a containerized DB:

```bash
docker compose exec -T db pg_dump -U $DB_USER $DB_NAME \
    > "tckdb_backup_$(date +%Y%m%d_%H%M%S).sql"
```

---

## Rollback expectations

Migrations have a `downgrade()` method, but it is not a substitute for backup-and-restore:

- **Schema-only downgrades** (add/drop column, add/drop index, add/drop table) are generally safe to run on a database that has not yet seen writes through the new shape.
- **Downgrades after live writes** through the new schema lose data. If a new column was populated by application traffic, `downgrade()` will drop it.
- **Data migrations** (backfills, column copies) generally cannot be reversed cleanly. The downgrade may leave the column empty rather than restoring its previous state.

The expected recovery path for a failed deploy is:

1. Stop the API.
2. Restore the pre-migration `pg_dump`.
3. Re-deploy the previous application version.
4. Investigate.

Use `alembic downgrade` only for narrow, well-understood schema-only changes and only when you accept the data-loss surface.

---

## Public-ref backfill

Adding `PublicRefMixin` to an existing table requires more than the column. The full checklist:

1. **Column addition** — add the `public_ref: Mapped[str] = mapped_column(String(40), unique=True, nullable=False, index=True)` column via a new revision.
2. **Backfill** — populate the column for existing rows inside the same revision (using the prefix and minting helpers from `backend/app/services/public_refs.py`). The column must be NOT NULL by the end of the migration, so the backfill cannot be deferred.
3. **Unique index / constraint** — `PublicRefMixin` declares `unique=True`. Verify the index lands and the constraint name matches `NAMING_CONVENTION` in `backend/app/db/base.py`.
4. **Prefix registration** — add the new entity to the `PREFIXES` map in `backend/app/services/public_refs.py`. The chosen prefix must be unique and short enough that `prefix + "_" + base32` fits inside `String(40)`.
5. **Tests** — add a test that `public_ref` round-trips, that the prefix is correct, and that two independently-minted refs do not collide. Existing tests under `backend/tests/services/` show the pattern.
6. **Length sanity** — `PUBLIC_REF_LEN` (or equivalent constant) in `backend/app/services/public_refs.py` must remain aligned with the DB column width. Currently both are `40`. If you change one, change both, and update every existing migration's column declaration in the same revision.

Public refs currently fit `String(40)`. The longest observed prefix (`nsolve`, 6 chars) plus underscore plus a 26-char base32 body is 33 chars, leaving 7 chars of headroom. Stay under that.

---

## RDKit GiST index migration (d4e5f6a7b8c9)

Revision `d4e5f6a7b8c9_add_species_entry_mol_gist_index` does two things:

1. **Backfills `species_entry.mol`** for any row whose `mol` is NULL but whose parent `species.smiles` is parseable. The backfill uses the cartridge's `mol_from_smiles(sp.smiles)` against the canonical SMILES via a join update; rows where the cartridge cannot parse the SMILES stay NULL and are excluded from structure-search results.
2. **Creates `ix_species_entry_mol_gist`**, a GiST index on `species_entry(mol)`. This is what lets substructure (`@>`) and similarity (`tanimoto_sml(morganbv_fp(...), ...)`) queries scan the index instead of every row.

Operator notes:

- Both steps run inside the Alembic transaction. On a small / self-hosted DB the build is essentially instant. On a larger deployed DB the GiST `CREATE INDEX` and the join-update backfill can take noticeable time — run during a low-traffic window and watch `pg_stat_activity` for long-running queries.
- Downgrade drops only the index. The `mol` column predates this revision (it was created in `d861dfd60891`) and is not dropped on downgrade — application reads keep working against the back-populated column.
- The structure-search service (`app/services/scientific_read/structure_search.py`) reads from `se.mol` directly. After the upgrade, run a quick smoke check:

  ```bash
  curl -fsS "http://127.0.0.1:8000/api/v1/scientific/species/structure-search?query_smiles=CCO&mode=substructure&limit=5" | jq '.pagination.total'
  ```

  A non-zero total on a populated catalog confirms the index path is wired.

---

## Network / PDep exception

While no real production network or PDep data exists, schema work on the `network*` and PDep tables is allowed to be more flexible than the default rule. See the dedicated section in `.claude/rules/migration-rules.md` for what that means; the operator-side implications are:

- A `network*` revision is still a normal Alembic revision and is applied through the steps above.
- It may include changes that would be unacceptable for an already-deployed table (e.g., tightening a nullable column to NOT NULL without a backfill, renaming a column without a column-copy step).
- The exception is **table-scoped**. A revision that touches both a `network*` table and a deployed table must respect the stricter rule for the deployed table.
- The exception ends the moment real network data lands in any long-lived DB. At that point, network tables become "already-deployed" and rejoin the default rule.

If you are reviewing a `network*` migration and unsure whether the exception still applies, check whether any long-lived DB (lab, hosted, operator-managed self-host) has rows in the relevant table.

---

## Self-hosted / Raspberry Pi note

Single-node and Raspberry-Pi deployments follow the same flow as any other deployed DB. Two extra notes:

- **Backup destination matters.** Do not back up to the same physical disk as the DB. For Pi setups, dump to an attached drive, network share, or rsync to a remote host.
- **Migration timing matters.** Schema changes that touch large tables can be slow on lower-end hardware. Run migrations during a quiet window and watch `pg_stat_activity` for long-running queries.

Otherwise the upgrade flow is identical: `pg_dump` → `alembic upgrade head` → smoke test.

---

## Operator checklist

A short, copy-pasteable checklist for each upgrade:

- [ ] Pull the new application version on the host.
- [ ] Confirm the conda env (or container image) matches the deploy target.
- [ ] Run `alembic current` and `alembic history` to confirm what is pending.
- [ ] Read each pending revision's docstring; flag any backfill or data-migration step.
- [ ] Take a `pg_dump` and verify the file is non-empty.
- [ ] If the migration touches artifact-referencing columns, mirror the object store too.
- [ ] Run `alembic upgrade head` (or step `+1` at a time for large change sets).
- [ ] Confirm `alembic current` shows the new head.
- [ ] Smoke-test `/health`, one scientific read, and one authenticated route.
- [ ] Restart the API service (`systemctl restart tckdb-api.service` or compose equivalent).
- [ ] Watch logs for 5–10 minutes for unexpected 500s or `column does not exist` errors.
- [ ] Record the upgrade (timestamp, revision range, who ran it) somewhere the lab can audit.

A backup you have never restored is a hypothesis. Test a restore at least once a quarter.

---

## See also

- `.claude/rules/migration-rules.md` — contributor rules for writing revisions.
- [`docs/deployment/README.md`](../../../docs/deployment/README.md) — overall deployment guide entry point.
- [`docs/deployment/shared-private-deployment.md`](../../../docs/deployment/shared-private-deployment.md) — lab/group deployment, including backup and restore.
- [`docs/deployment/self_hosted_single_node.md`](../../../docs/deployment/self_hosted_single_node.md) — single-node operator guide with concrete commands.
- [`backend/alembic/versions/`](../../alembic/versions/) — the revision graph itself.
