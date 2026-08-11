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

## Network / PDep exception (RETIRED)

This exception is retired. Real `network_*` rows now live on the deployed database, so every `network*` and PDep table follows the default already-deployed-table rule: a new revision per change, both `upgrade()` and `downgrade()` implemented, and a backfill design for any non-nullable addition. See `.claude/rules/migration-rules.md`.

---

## Stage 2 PDep re-upload (revision `c1d2e3f4a5b6`)

`c1d2e3f4a5b6` **refuses to run** against a database that still holds pre-v2 pressure-dependent network rows, and says so with a `RuntimeError` before any DDL is applied. This is deliberate, not a bug to work around.

**Why it refuses.** The v2 contract requires two things that v1 rows do not carry and that cannot be reconstructed from what was stored:

- `network_solve_energy_transfer` gains `state_id` and `collider_species_entry_id` (both NOT NULL). A v1 row recorded one unscoped ⟨ΔE⟩down with no record of which well or which collider it described.
- `network_channel` gains `channel_key`, the producer-visible identity that lets parallel mechanistic pathways share the same macroscopic endpoints. v1 channels have no such key.

Guessing either value during ingestion would fabricate scientific provenance. The contract is therefore **refuse and re-upload**, not backfill.

**Detecting the condition ahead of time:**

```bash
psql "$DATABASE_URL" -c "
  SELECT (SELECT count(*) FROM network_solve_energy_transfer) AS energy_transfer_rows,
         (SELECT count(*) FROM network_channel)               AS channel_rows,
         (SELECT count(*) FROM network)                       AS networks;"
```

If both counts are zero, `alembic upgrade head` proceeds normally and nothing below applies.

**If rows exist, in order:**

1. **Back up.** Follow "Back up before touching anything" above. Do not skip this — step 3 deletes rows.
2. **Export what is there, and locate its source.** The stored rows are not sufficient to rebuild the network; the original Arkane / master-equation run directory is. Capture both:
   ```bash
   curl -sH "Authorization: Bearer $TCKDB_API_KEY" \
     "$TCKDB_URL/api/v1/scientific/networks?limit=200" > networks.json
   curl -sH "Authorization: Bearer $TCKDB_API_KEY" \
     "$TCKDB_URL/api/v1/scientific/network-solves/<ref>?include=all" > solve-<ref>.json
   ```
   Confirm you can still reach the run directory each network was built from before continuing.
3. **Delete the legacy network graph**, child-first so no FK is violated:
   ```sql
   BEGIN;
   DELETE FROM network_kinetics_point       WHERE network_kinetics_id IN (SELECT id FROM network_kinetics);
   DELETE FROM network_kinetics_plog        WHERE network_kinetics_id IN (SELECT id FROM network_kinetics);
   DELETE FROM network_kinetics_chebyshev   WHERE network_kinetics_id IN (SELECT id FROM network_kinetics);
   UPDATE kinetics SET network_kinetics_id = NULL WHERE network_kinetics_id IS NOT NULL;
   DELETE FROM network_kinetics;
   DELETE FROM network_solve_energy_transfer;
   DELETE FROM network_solve_bath_gas;
   DELETE FROM network_solve_source_calculation;
   DELETE FROM network_solve;
   DELETE FROM network_channel;
   DELETE FROM network_state_participant;
   DELETE FROM network_state;
   DELETE FROM network_reaction;
   DELETE FROM network_species;
   DELETE FROM network;
   COMMIT;
   ```
   Species, calculations, transition states and statmech rows are **not** deleted — they are shared identity/result rows and the re-upload will resolve to the existing ones.
4. **Migrate.** `alembic upgrade head` now runs to completion.
5. **Re-upload** each network through `POST /api/v1/uploads/networks/pdep` with a v2 payload regenerated from its source run (for Arkane runs, `backend/scripts/pdep_ingestion` emits one). Each payload must carry per-path `channel_barriers`, an `energy_transfer` block, and, for every channel that is one elementary step, `microreaction_paths`. Since `b6e1d3a9c740` the energy-transfer block may take either form: one `scope: "per_well"` entry per `(state_key, collider_species_key)` pair, or — if the source run declared a single `energyTransferModel` for the network, as Arkane and MESS inputs usually do — one `scope: "network_wide"` entry naming neither. Do **not** duplicate a single value across the wells to make it look per-well; that is the fabrication ADR 0009 exists to stop. A chemically-activated **well-skipping** channel has no elementary step to name and instead declares `mechanism: "well_skipping"` with no paths (see `backend/docs/specs/pdep_upload_contract_v2.md`, "Well-skipping channels"). Omitting the paths *without* that declaration is still rejected.
6. **Verify** that the re-uploaded network count matches the export, and that `network_channel.channel_key` is non-null everywhere:
   ```sql
   SELECT count(*) FROM network_channel WHERE channel_key IS NULL;  -- expect 0
   ```
   The per-network **channel** count should match the export too: well-skipping channels are carried across, so a v1 network's phenomenological channels are all representable in v2. A shortfall means the producer dropped chemically-activated pathways rather than declaring them.

**Downgrading** `c1d2e3f4a5b6` refuses symmetrically when either a TS-owned `statmech` row exists (the prior schema has no truthful subject for it) or two channels share `(network_id, source_state_id, sink_state_id)` (the prior schema made that triple unique). Resolve those rows before rolling back.

---

## Energy-transfer declaration scope (revision `b6e1d3a9c740`)

Adds `network_solve_energy_transfer.scope` (`per_well` | `network_wide`) and makes `state_id` / `collider_species_entry_id` nullable so a network-wide ⟨ΔE⟩down is representable. See [ADR 0009](../../../docs/adr/0009-record-what-energy-transfer-was-specified-over.md).

**Upgrading needs no operator action.** Both scope columns were NOT NULL before this revision, so every existing row already resolves a (state, collider) pair; `server_default='per_well'` states that fact rather than guessing it. No row changes meaning, acquires a NULL, or needs a re-upload. Verify with:

```sql
SELECT scope, count(*) FROM network_solve_energy_transfer GROUP BY scope;
-- expect every pre-existing row under 'per_well'
SELECT count(*) FROM network_solve_energy_transfer
 WHERE scope = 'per_well' AND (state_id IS NULL OR collider_species_entry_id IS NULL);
-- expect 0 (also enforced by ck_network_solve_energy_transfer_scope_columns_agree)
```

**Downgrading refuses** while any `network_wide` row exists. Such a row has no (state, collider) to restore — the producer never determined one — so dropping the column would either delete a real declaration or re-present it as per-well data it never was. Export the affected solves (`GET /scientific/network-solves/{ref}?include=energy_transfer`), delete the network-wide rows or the solves carrying them, then re-run the downgrade.

---

## Network-solve origin (revision `c4d8f1b2a9e6`)

Adds `network_solve.kind` (`computed` | `reported`) so that k(T,P) transcribed from a publication can be deposited without the master-equation inputs a solve run here must supply. See [ADR 0010](../../../docs/adr/0010-hold-literature-reported-pressure-dependent-kinetics.md).

**Upgrading needs no operator action.** Before this revision the schema admitted nothing but a master-equation solve — every solve had to carry a state energy per network state, a barrier per saddle-point path and an energy-transfer model, and there was no way to express a transcribed one — so `server_default='computed'` states a fact about every existing row rather than guessing it. No row changes meaning and none needs a re-upload. The new check constraint cannot fail on the backfill, because it constrains only `reported` rows and the backfill creates none. Verify with:

```sql
SELECT kind, count(*) FROM network_solve GROUP BY kind;
-- expect every pre-existing row under 'computed'
SELECT count(*) FROM network_solve WHERE kind = 'reported' AND literature_id IS NULL;
-- expect 0 (also enforced by ck_network_solve_reported_requires_literature)
```

**Depositing reported kinetics.** A `reported` solve supplies `literature` and at least one `channel_kinetics` entry, and may omit `state_energies`, `channel_barriers`, `energy_transfer`, `bath_gas` and `source_calculations`. The network topology is still required — a reported rate has to attach to a channel that exists — and anything the payload *does* supply is validated as strictly as on a computed solve. Every such upload returns the `reported_network_solve` warning; that is expected, not a defect.

**Downgrading refuses** while any `reported` solve exists. The prior schema has no way to record that a set of rates was transcribed rather than derived, so dropping the column would present published numbers as this database's own master-equation output. Export the affected solves (`GET /api/v1/scientific/network-solves/search?kind=reported`), delete them and the network kinetics hanging off them, then re-run the downgrade.

---

## Repairing an accepted record (revision `e2c9a4f7b163`)

Some migrations rewrite a value that carries no scientific content — the letter
case of an element symbol in `geometry_atom.element` (`b4e7c1d20f83`) is the
worked example. On a database holding a non-canonical row under an **accepted**
calculation, such a migration fails with the accepted-science guard's own
`accepted calculation record N is immutable`. That failure is correct: the
decision to rewrite a value inside approved science belongs to the operator, not
to a migration file.

**Do not disable the trigger.** Declare the repair instead, in the same
transaction as the rewrite:

```sql
BEGIN;
INSERT INTO accepted_science_repair
    (target_table, declared_columns, alembic_revision, reason)
VALUES ('geometry_atom', ARRAY['element'], 'b4e7c1d20f83',
        'canonicalise element symbol case; geom_hash, xyz_text and '
        'coordinates are not touched');

UPDATE geometry_atom
   SET element = upper(substr(btrim(element), 1, 1)) || lower(substr(btrim(element), 2))
 WHERE btrim(element) <> upper(substr(btrim(element), 1, 1)) || lower(substr(btrim(element), 2));

-- Read back what was actually changed before committing.
SELECT record_type, record_id, row_identity, before_json, after_json
  FROM accepted_science_repair_change;
COMMIT;
```

The declaration permits an UPDATE to that table **only** for the columns it
names — an UPDATE that touches anything else is refused by name and the whole
transaction rolls back — and every changed row is recorded against the accepted
record it sits under. The declaration is dead the moment the transaction ends,
so there is nothing to close. Run this as the migration owner (`DB_OWNER_USER`);
the runtime account is refused. Full semantics and limits:
[`../specs/accepted_science_immutability.md`](../specs/accepted_science_immutability.md)
and [ADR 0015](../../../docs/adr/0015-a-repair-to-accepted-science-is-declared-before-it-is-made.md).

Two things this cannot do, deliberately. It cannot INSERT or DELETE rows under
an accepted record, and it cannot rewrite a primary key. Both are changes to
*what* was accepted rather than to how it is spelled, and both need a
replacement record and a `scientific_record_supersession` edge.

The repair record is **not** carried by `tckdb.archive.v1` — it is keyed on this
cluster's transaction id. Keep the `pg_dump` from the upgrade if the account of
the repair matters to you.

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
