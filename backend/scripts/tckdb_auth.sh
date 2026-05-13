#!/usr/bin/env bash
# tckdb_auth.sh — small auth helper for TCKDB deployments.
#
# Wraps the routine cookie-and-API-key dance against any TCKDB instance,
# local or hosted. Unlike scripts/dev_login.sh this script does NOT
# bootstrap or promote an admin — it assumes the user already exists and
# focuses on session login and API-key minting.
#
# Subcommands:
#   me                 GET /auth/me (using API key or cookie file)
#   login              Prompt for credentials, save session cookie
#   create-key         Mint a new API key using the cookie, save to env file
#   login-create-key   Run login, then create-key
#
# Environment:
#   TCKDB_BASE_URL        default: http://127.0.0.1:8010/api/v1
#   TCKDB_COOKIE_FILE     default: .tckdb_cookies.txt
#   TCKDB_AUTH_ENV_FILE   default: .tckdb_auth.env
#   TCKDB_API_KEY         used by `me` if no cookie present
#
# Secrets discipline:
#   - The plaintext API key is written only to TCKDB_AUTH_ENV_FILE
#     (mode 0600); it is never echoed to stdout unless --show-key is passed.
#   - The session cookie file is chmod 0600.
#
# Requires: bash, curl, python3 (for portable JSON parsing). `jq` is used
# for pretty-printing if available, otherwise raw JSON is printed.

set -euo pipefail

DEFAULT_BASE_URL="http://127.0.0.1:8010/api/v1"
DEFAULT_COOKIE_FILE=".tckdb_cookies.txt"
DEFAULT_AUTH_ENV_FILE=".tckdb_auth.env"

BASE_URL="${TCKDB_BASE_URL:-$DEFAULT_BASE_URL}"
COOKIE_FILE="${TCKDB_COOKIE_FILE:-$DEFAULT_COOKIE_FILE}"
AUTH_ENV_FILE="${TCKDB_AUTH_ENV_FILE:-$DEFAULT_AUTH_ENV_FILE}"

usage() {
    cat <<'EOF'
Usage: tckdb_auth.sh <subcommand> [options]

Subcommands:
  me                          Show the currently authenticated user.
  login                       Prompt for username/password, save cookie.
  create-key [--name NAME]    Mint a new API key, save to auth env file.
  login-create-key [--name NAME]
                              login then create-key in one shot.

Options for create-key / login-create-key:
  --name NAME    Human-readable label for the API key (sent as `label`).
  --label NAME   Alias for --name.
  --show-key     Print the plaintext API key to stdout once minted.

Environment:
  TCKDB_BASE_URL          (default: http://127.0.0.1:8010/api/v1)
  TCKDB_COOKIE_FILE       (default: .tckdb_cookies.txt)
  TCKDB_AUTH_ENV_FILE     (default: .tckdb_auth.env)
  TCKDB_API_KEY           Used by `me` when no cookie is present.
EOF
}

die() { echo "error: $*" >&2; exit 1; }

# Pretty-print JSON to stdout, falling back to raw if jq is missing.
print_json() {
    if command -v jq >/dev/null 2>&1; then
        jq .
    else
        cat
    fi
}

# Extract a string field from JSON on stdin, trying several common names.
# Prints the first non-empty value found, or nothing if none match.
extract_api_key() {
    python3 - <<'PY'
import json, sys
try:
    data = json.load(sys.stdin)
except Exception:
    sys.exit(0)
if not isinstance(data, dict):
    sys.exit(0)
for field in ("key", "api_key", "token", "plain_key", "secret"):
    value = data.get(field)
    if isinstance(value, str) and value:
        print(value)
        break
PY
}

# Mask "abcdef…wxyz" — never log the full key.
mask_key() {
    local k="$1"
    local n=${#k}
    if (( n <= 10 )); then
        printf '***'
    else
        printf '%s…%s' "${k:0:6}" "${k: -4}"
    fi
}

require_cookie_file() {
    [[ -s "$COOKIE_FILE" ]] || die "no session cookie at '$COOKIE_FILE'. Run: $0 login"
}

cmd_me() {
    local args=(-sS --fail-with-body "$BASE_URL/auth/me")
    if [[ -n "${TCKDB_API_KEY:-}" ]]; then
        args+=(-H "X-API-Key: $TCKDB_API_KEY")
    elif [[ -s "$COOKIE_FILE" ]]; then
        args+=(-b "$COOKIE_FILE")
    else
        die "no TCKDB_API_KEY set and no cookie at '$COOKIE_FILE'. Run: $0 login"
    fi
    curl "${args[@]}" | print_json
}

cmd_login() {
    local username password
    read -r -p "Username: " username
    [[ -n "$username" ]] || die "username is required"
    # -s: silent (no echo); -r: raw (no backslash mangling).
    read -r -s -p "Password: " password
    echo  # newline after silent prompt
    [[ -n "$password" ]] || die "password is required"

    rm -f "$COOKIE_FILE"
    # --fail-with-body: curl exits nonzero on HTTP errors but still
    # surfaces the response body so the error message is visible.
    local body
    body="$(curl -sS --fail-with-body \
        -X POST "$BASE_URL/auth/login" \
        -H 'Content-Type: application/json' \
        -c "$COOKIE_FILE" \
        --data-binary @- <<JSON
{"username": $(python3 -c 'import json,sys; print(json.dumps(sys.argv[1]))' "$username"),
 "password": $(python3 -c 'import json,sys; print(json.dumps(sys.argv[1]))' "$password")}
JSON
    )" || { echo "login failed:" >&2; echo "$body" >&2; exit 1; }

    chmod 600 "$COOKIE_FILE" 2>/dev/null || true
    # Show identity without showing tokens.
    echo "$body" | print_json
    echo "session cookie saved to $COOKIE_FILE"
}

cmd_create_key() {
    local name=""
    local show_key=0
    while (( $# )); do
        case "$1" in
            --name|--label) name="${2:-}"; shift 2 ;;
            --show-key)     show_key=1; shift ;;
            -h|--help)      usage; return 0 ;;
            *) die "unknown argument to create-key: $1" ;;
        esac
    done

    require_cookie_file

    # Build request body: omit label if not provided (API treats it as optional).
    local body_json
    if [[ -n "$name" ]]; then
        body_json="$(python3 -c 'import json,sys; print(json.dumps({"label": sys.argv[1]}))' "$name")"
    else
        body_json='{}'
    fi

    local response
    response="$(curl -sS --fail-with-body \
        -X POST "$BASE_URL/auth/api-keys" \
        -H 'Content-Type: application/json' \
        -b "$COOKIE_FILE" \
        --data-binary "$body_json")" || {
        echo "create-key failed:" >&2
        echo "$response" >&2
        exit 1
    }

    local api_key
    api_key="$(printf '%s' "$response" | extract_api_key)"
    if [[ -z "$api_key" ]]; then
        echo "error: could not find an API key field in the response." >&2
        echo "tried fields: key, api_key, token, plain_key, secret" >&2
        echo "raw response:" >&2
        echo "$response" >&2
        exit 1
    fi

    umask 077
    cat > "$AUTH_ENV_FILE" <<EOF
# Generated by backend/scripts/tckdb_auth.sh — do not commit.
export TCKDB_BASE_URL='$BASE_URL'
export TCKDB_API_KEY='$api_key'
EOF
    chmod 600 "$AUTH_ENV_FILE"

    local masked
    masked="$(mask_key "$api_key")"
    echo "API key minted (label='${name:-<none>}', key=$masked) -> $AUTH_ENV_FILE"
    echo "to use this key in the current shell:  source $AUTH_ENV_FILE"

    if (( show_key )); then
        echo "TCKDB_API_KEY=$api_key"
    fi
}

main() {
    [[ $# -ge 1 ]] || { usage; exit 2; }
    local sub="$1"; shift
    case "$sub" in
        me)               cmd_me "$@" ;;
        login)            cmd_login "$@" ;;
        create-key)       cmd_create_key "$@" ;;
        login-create-key) cmd_login; cmd_create_key "$@" ;;
        -h|--help|help)   usage ;;
        *) usage; exit 2 ;;
    esac
}

main "$@"
