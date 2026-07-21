# PostgreSQL role separation

Hosted TCKDB deployments use three distinct PostgreSQL logins:

| Role | Purpose | May own or alter schema? | Loaded by API/worker? |
|---|---|---:|---:|
| `DB_ADMIN_USER` | Cluster bootstrap, extension installation, recovery | Yes; superuser | No |
| `DB_OWNER_USER` | Alembic and application-object ownership | Yes; non-superuser | No |
| `DB_USER` | API and upload worker | No; ordinary DML only | Yes |

This split makes accepted-science triggers meaningful against the application
account. A table owner or superuser can disable triggers; the runtime role
cannot.

## Secret files

Keep runtime and operator credentials separate:

```bash
cp .env.selfhosted.example .env.selfhosted
cp .env.db-admin.example .env.db-admin
chmod 600 .env.selfhosted .env.db-admin
```

The API systemd unit loads only `.env.selfhosted`. Never add
`.env.db-admin` as an API or worker `EnvironmentFile`.

For a new database, start PostgreSQL with both files so the container receives
the bootstrap administrator while application services receive only explicit
runtime variables:

```bash
docker compose \
  --env-file .env.selfhosted \
  --env-file .env.db-admin \
  up -d db minio
```

For an existing database whose current `tckdb` login is the bootstrap
superuser, initially set `DB_ADMIN_USER=tckdb` and its existing password in
`.env.db-admin`. Do not invent `tckdb_admin` until that role has actually been
created.

## Provision or convert a database

Stop API and worker writes, take a database and object-store backup, then load
both operator and runtime settings into the operator shell:

```bash
set -a
source .env.selfhosted
source .env.db-admin
set +a

cd backend
conda run -n tckdb_env python scripts/configure_database_roles.py apply
conda run -n tckdb_env alembic upgrade head
conda run -n tckdb_env python scripts/configure_database_roles.py check
```

`apply` is idempotent. It:

- refuses to continue if surviving approval history exists, unless an operator
  explicitly passes `--allow-existing-approvals` after a curator audit;
- installs the RDKit extension as the administrator when needed;
- creates or hardens the owner and runtime roles as non-superusers;
- transfers non-extension objects in `public` to the migration owner;
- grants the runtime account `SELECT`, `INSERT`, `UPDATE`, and `DELETE`, plus
  sequence access, while withholding ownership, schema creation, temporary
  tables, `TRUNCATE`, and writes to `alembic_version`; and
- establishes equivalent default privileges for objects created by future
  migrations.

In hosted modes Alembic refuses to run without `DB_OWNER_USER` and
`DB_OWNER_PASSWORD`. The API configuration continues to read only `DB_USER`
and `DB_PASSWORD`.

## Verification

The `check` subcommand is read-only and exits nonzero if the owner/runtime
contract is unsafe. It also reports the approval-history preflight counts.

After restarting the API, verify `/api/v1/readyz`, one anonymous scientific
read, and one authenticated write. Keep `.env.db-admin` readable only by the
operator account and use it solely for migrations, role maintenance, and
recovery.
