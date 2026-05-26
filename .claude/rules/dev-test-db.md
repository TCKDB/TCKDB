# Dev Test DB Recovery

This rule applies only to local backend test runs.

When backend tests fail with PostgreSQL host connection errors involving `127.0.0.1:5432`, do not give up before running the dev DB port resolver. Trigger on errors that include:

- `psycopg.OperationalError`
- `connection is bad`
- `connection refused`
- `could not connect to server`
- `127.0.0.1:5432`

Run:

```bash
conda run -n tckdb_env python backend/scripts/dev/ensure_test_db_port.py --apply
```

Then follow the script output exactly: start or recreate the local dev `db` service with any printed `docker compose` command, source/load the generated env file, and rerun the failed tests.

The resolver is dev-local only. It must not edit production compose files, Raspberry Pi/self-hosted compose files, `.env.pi`, schemas, migrations, ARC, or `tckdb-client`; it must not kill local Postgres, require sudo, wipe data, or silently change deployed settings.
