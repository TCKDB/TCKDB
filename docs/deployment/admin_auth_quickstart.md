# Admin auth quickstart

Operational recipe for seeding admin/curator accounts, logging in, and
minting the API keys that ARC and other clients need to upload to a
TCKDB deployment. Works against any TCKDB instance — local dev, a
shared lab server, or a public self-hosted deployment at e.g.
`https://tckdb.example.org/api/v1`.

For the full self-hosted single-node setup (compose layout,
reverse-proxy/tunnel, Postgres/MinIO posture) see
[self_hosted_single_node.md](self_hosted_single_node.md). That guide
was tested on a Raspberry Pi but the recipe transfers to any small
Linux server.

> **Never commit any of the files this guide produces.** The repo's
> `.gitignore` already covers `.tckdb_auth.env`, `.tckdb_api_key`,
> `.tckdb_cookies.txt`, `cookies.txt`, and the `backend/`-prefixed
> variants written by the older `dev_login.sh`.

---

## 1. Seed an admin (or curator) account

Public registration is disabled on hosted deployments — admins are
seeded directly. Use [bootstrap_admin.py](../../backend/scripts/bootstrap_admin.py)
(despite the name, it now handles any role):

```bash
# From the backend/ directory — the script's sys.path shim makes
# PYTHONPATH unnecessary.
cd backend

# Password may also come from $TCKDB_BOOTSTRAP_PASSWORD (preferable
# in shared shells — it stays out of history).
conda run -n tckdb_env python scripts/bootstrap_admin.py \
    --username alice \
    --email   alice@example.org \
    --role    admin \
    --password 'correct horse battery staple'
```

The script is idempotent:

- creates the user if they don't exist,
- reactivates them if disabled,
- refuses to change an existing user's role unless you pass
  `--force-role-change` (this is the break-glass override and applies
  to demotions too).

Curators are seeded the same way:

```bash
conda run -n tckdb_env python scripts/bootstrap_admin.py \
    --username bob \
    --email   bob@example.org \
    --role    curator \
    --password 'another long passphrase'
```

If the user already exists with a different role, the script exits
with code 2 and a hint to re-run with `--force-role-change`.

---

## 2. Log in and mint an API key

Use [tckdb_auth.sh](../../backend/scripts/tckdb_auth.sh), the
deployment-agnostic helper:

```bash
# Point at whichever TCKDB you want to talk to.
export TCKDB_BASE_URL="https://tckdb.example.org/api/v1"
# Or for local dev:
# export TCKDB_BASE_URL="http://127.0.0.1:8010/api/v1"

# One-shot: prompts for credentials, saves cookie, mints API key,
# writes both into .tckdb_auth.env (mode 0600).
backend/scripts/tckdb_auth.sh login-create-key --name arc-pi-upload

# Pull the key into the current shell.
source .tckdb_auth.env
```

The script writes three things and prints none of them by default:

| File | Contents | Mode |
|---|---|---|
| `.tckdb_cookies.txt` | Session cookie from `POST /auth/login` | 0600 |
| `.tckdb_auth.env`    | `TCKDB_BASE_URL`, `TCKDB_API_KEY`       | 0600 |

If you want to see the plaintext key once for copy-paste into a
password manager, add `--show-key` — it is otherwise only visible by
reading `.tckdb_auth.env` directly.

Override the paths with `TCKDB_COOKIE_FILE` and `TCKDB_AUTH_ENV_FILE`
when you need to keep multiple deployments separate, e.g. one cookie
jar for local dev and another for the Pi.

### Subcommands

```bash
backend/scripts/tckdb_auth.sh me                            # whoami
backend/scripts/tckdb_auth.sh login                         # cookie only
backend/scripts/tckdb_auth.sh create-key --name arc-pi-upload   # needs cookie
backend/scripts/tckdb_auth.sh login-create-key --name arc-pi-upload
```

`me` prefers `TCKDB_API_KEY` (when set) over the cookie file, so it
also works as a smoke test after `source .tckdb_auth.env`.

---

## 3. Test the API key

```bash
source .tckdb_auth.env

# Should print your user row.
curl -s -H "X-API-Key: $TCKDB_API_KEY" "$TCKDB_BASE_URL/auth/me" | jq

# Or via the helper (same call, just uses the env var).
backend/scripts/tckdb_auth.sh me
```

`/auth/me` accepts either a session cookie or `X-API-Key`; uploads
require the API key:

```text
X-API-Key: $TCKDB_API_KEY
```

---

## 4. Wire ARC against the deployment

ARC's TCKDB adapter reads the same two environment variables:

```bash
export TCKDB_BASE_URL="https://tckdb.example.org/api/v1"
export TCKDB_API_KEY="..."
```

Source `.tckdb_auth.env` (or copy the values into your ARC env file)
before running an ARC job that should upload to TCKDB. The adapter
sends every write as `X-API-Key: $TCKDB_API_KEY` against
`$TCKDB_BASE_URL`; there is no second auth path.

---

## 5. Check a deployment

After spinning up or modifying any compose-based single-node TCKDB
instance:

```bash
# Run from the repo root. Defaults target the self-hosted recipe
# (docker-compose.yml, .env.selfhosted). Override
# COMPOSE_FILE / TCKDB_BASE_URL for other deployments.
backend/scripts/check_selfhosted_deployment.sh

# Example: same script against a local compose dev stack.
COMPOSE_FILE=docker-compose.yml \
COMPOSE_ENV_FILE=.env \
TCKDB_BASE_URL=http://127.0.0.1:8010/api/v1 \
DB_NAME=tckdb_dev \
    backend/scripts/check_selfhosted_deployment.sh

# Example: same script against a public self-hosted instance.
TCKDB_BASE_URL=https://tckdb.example.org/api/v1 \
    backend/scripts/check_selfhosted_deployment.sh
```

What the script verifies:

1. `docker compose ps` works and `db`, `minio` services are running/healthy
2. `psql` inside the `db` container responds
3. The `rdkit` extension is installed in `$DB_NAME`
4. `alembic current` reports a revision (best-effort — needs the
   `tckdb_env` conda env on the host)
5. `GET $TCKDB_BASE_URL/health` returns `{"status": "ok"}`
6. `GET $TCKDB_BASE_URL/scientific/species/search?smiles=O` returns 200

Exit code is non-zero if any check fails; details go to stderr.

---

## What never to commit

The `.gitignore` already covers these, but it's worth knowing the list:

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

If a key leaks: revoke it via `DELETE /api/v1/auth/api-keys/{key_id}`
(or the admin UI when it exists), rotate it, and check git history
with `git log --all --full-history -- <file>` to confirm it never
landed in a commit.
