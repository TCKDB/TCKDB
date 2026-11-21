# Admin accounts and API keys

How to create accounts, log in, and mint the API keys that ARC and other
clients use to upload. It works against any TCKDB instance.

None of the files below may be committed. `.gitignore` already covers them.

## 1. Create accounts

Registration is closed on any shared instance, so accounts are created with
[`bootstrap_admin.py`](../../backend/scripts/bootstrap_admin.py). On a
[self-hosted](README.md) stack, `init` has already created the first
administrator from `.env.db-admin`.

A new account needs a password, from `TCKDB_BOOTSTRAP_PASSWORD` or
`--password`. Use the environment variable so the password stays out of your
shell history. `--role` is `user`, `curator` or `admin`, and defaults to
`admin`.

**On a self-hosted stack**, run it in the API container:

```bash
read -rsp "Password: " PW; echo
docker compose --env-file .env.selfhosted --env-file .env.db-admin --profile selfhost \
    exec -e TCKDB_BOOTSTRAP_PASSWORD="$PW" tckdb-api \
    /opt/conda/bin/python scripts/bootstrap_admin.py \
    --username bob --email bob@example.org --role curator
unset PW
```

**From source**, run it from `backend/`:

```bash
TCKDB_BOOTSTRAP_PASSWORD='a long passphrase' \
  conda run -n tckdb_env python scripts/bootstrap_admin.py \
    --username alice --email alice@example.org --role admin
```

The script is idempotent:

- it creates the user if none matches;
- it reactivates a disabled user;
- it refuses to change an existing user's role, promotion or demotion,
  unless you pass `--force-role-change`. Without it the script exits with
  code 2 and says so.

## 2. Log in and mint an API key

Use [`tckdb_auth.sh`](../../backend/scripts/tckdb_auth.sh) with any instance:

```bash
export TCKDB_BASE_URL="https://tckdb.example.org/api/v1"   # or http://127.0.0.1:8010/api/v1
backend/scripts/tckdb_auth.sh login-create-key --name arc-upload
source .tckdb_auth.env
```

It asks for your username and password, saves the session cookie, mints a
key and writes two files. It prints neither the cookie nor the key:

| File | Contents | Mode |
|---|---|---|
| `.tckdb_cookies.txt` | the session cookie | 600 |
| `.tckdb_auth.env` | `TCKDB_BASE_URL`, `TCKDB_API_KEY` | 600 |

Add `--show-key` to print the key once, for a password manager. To keep
several instances apart, set `TCKDB_COOKIE_FILE` and `TCKDB_AUTH_ENV_FILE`
to different paths.

Other subcommands:

```bash
backend/scripts/tckdb_auth.sh me                          # who am I
backend/scripts/tckdb_auth.sh login                       # cookie only
backend/scripts/tckdb_auth.sh create-key --name arc-upload   # needs the cookie
```

`me` uses `TCKDB_API_KEY` when it is set, so after `source .tckdb_auth.env`
it tests the key.

## 3. Test the key

```bash
curl -s -H "X-API-Key: $TCKDB_API_KEY" "$TCKDB_BASE_URL/auth/me" | jq
```

`/auth/me` accepts a session cookie or a key. Uploads need the key, sent as
`X-API-Key`.

## 4. Point ARC at the instance

ARC's TCKDB adapter reads the same two variables:

```bash
export TCKDB_BASE_URL="https://tckdb.example.org/api/v1"
export TCKDB_API_KEY="tck_..."
```

Source `.tckdb_auth.env`, or copy the values into ARC's env file, before an
ARC run that should upload. The adapter sends every write with that key to
that URL. There is no other login path.

## 5. Check an instance

```bash
backend/scripts/check_selfhosted_deployment.sh
```

Run it from the repository root. The defaults fit a self-hosted stack
(`docker-compose.yml`, `.env.selfhosted`, `.env.db-admin`). It checks:

1. no secret is empty or a `change-me` placeholder, unless `DEPLOYMENT_MODE`
   is `local` or unset (the API skips this check in local mode too);
2. `db` and `seaweedfs` are running and healthy, and `tckdb-api` and
   `frontend` too when they run (`STORAGE_SERVICE=minio` on a MinIO stack);
3. the `rdkit` extension is installed in `DB_NAME`;
4. the database is at the latest migration, read inside the `tckdb-api`
   container, or with the host's `tckdb_env` when there is no container;
5. `GET $TCKDB_BASE_URL/health` returns `{"status":"ok"}`;
6. `GET $TCKDB_BASE_URL/scientific/species/search?smiles=O` returns `200`.

It exits non-zero if any check fails and explains on stderr. For a compose
project with another name, pass `-p NAME`. `--help` lists the other inputs.
On a stack started from source, point it at the developer file with
`COMPOSE_ENV_FILE=.env`.

## If a key leaks

Revoke it with `DELETE /api/v1/auth/api-keys/{key_id}` on the instance that
issued it, then mint a new one. Check that it never reached a commit:
`git log --all --full-history -- <file>`.

Files that must never be committed:

```text
.tckdb_auth.env
.tckdb_api_key
.tckdb_cookies.txt
cookies.txt
*.cookies.txt
*.tckdb_cookies.txt
backend/.tckdb_auth.env
backend/.tckdb_api_key
backend/cookies.txt
```
