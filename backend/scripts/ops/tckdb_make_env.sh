#!/usr/bin/env bash
# Write .env.selfhosted and .env.db-admin from the repository templates, with
# every secret generated.
#
# Asks for the deployment mode, public URL, trusted proxy header and the first
# administrator's username and email; every question has a flag, and with
# --yes (or no terminal) the defaults are taken. The administrator password is
# generated unless given, and printed once at the end.
#
# USAGE
#   backend/scripts/ops/tckdb_make_env.sh [options]
#
# OPTIONS
#   --mode MODE            shared_private (default), hosted_public or local
#   --public-url URL       URL the deployment is reached at, without /api/v1
#                          (default http://127.0.0.1:<TCKDB_WEB_PORT>)
#   --proxy-header NAME    trusted client-IP header, e.g. CF-Connecting-IP or
#                          X-Real-IP (default: none)
#   --admin-user NAME      first administrator's username (default admin)
#   --admin-email EMAIL    first administrator's email (default: none)
#   --admin-password PW    use this password instead of generating one
#   --set KEY=VALUE        set any other variable; repeatable. Written to
#                          whichever template defines KEY, else .env.selfhosted
#   --dir DIR              where to write the files (default: repository root)
#   --force                overwrite existing files
#   -y, --yes              do not ask; use flags and defaults
#   -h, --help             show this help
#
# Exit codes: 0 written, 1 refused or failed, 2 usage error.
set -euo pipefail

usage() { sed -n '2,/^set -euo/p' "$0" | sed '$d; s/^# \{0,1\}//'; }
die() { echo "error: $*" >&2; exit 1; }
usage_error() { echo "error: $*" >&2; echo "run with --help for usage" >&2; exit 2; }

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
OUT_DIR="$REPO_ROOT"
MODE="" PUBLIC_URL="" PROXY_HEADER="" ADMIN_USER="" ADMIN_EMAIL="" ADMIN_PASSWORD=""
MODE_SET=0 URL_SET=0 PROXY_SET=0 USER_SET=0 EMAIL_SET=0
FORCE=0 ASSUME_YES=0
EXTRA=()

need_arg() { [[ $# -ge 2 ]] || usage_error "$1 needs a value"; }
while [[ $# -gt 0 ]]; do
    case "$1" in
        --mode) need_arg "$@"; MODE="$2"; MODE_SET=1; shift 2 ;;
        --public-url) need_arg "$@"; PUBLIC_URL="$2"; URL_SET=1; shift 2 ;;
        --proxy-header) need_arg "$@"; PROXY_HEADER="$2"; PROXY_SET=1; shift 2 ;;
        --admin-user) need_arg "$@"; ADMIN_USER="$2"; USER_SET=1; shift 2 ;;
        --admin-email) need_arg "$@"; ADMIN_EMAIL="$2"; EMAIL_SET=1; shift 2 ;;
        --admin-password) need_arg "$@"; ADMIN_PASSWORD="$2"; shift 2 ;;
        --set) need_arg "$@"; [[ "$2" =~ ^[A-Za-z_][A-Za-z0-9_]*= ]] || usage_error "--set wants KEY=VALUE, got '$2'"
               EXTRA+=("$2"); shift 2 ;;
        --dir) need_arg "$@"; OUT_DIR="$2"; shift 2 ;;
        --force) FORCE=1; shift ;;
        -y|--yes) ASSUME_YES=1; shift ;;
        -h|--help) usage; exit 0 ;;
        *) usage_error "unknown argument '$1'" ;;
    esac
done

command -v openssl >/dev/null 2>&1 || die "openssl is required to generate secrets"

RUNTIME_TEMPLATE="$REPO_ROOT/.env.selfhosted.example"
ADMIN_TEMPLATE="$REPO_ROOT/.env.db-admin.example"
RUNTIME_FILE="$OUT_DIR/.env.selfhosted"
ADMIN_FILE="$OUT_DIR/.env.db-admin"
for template in "$RUNTIME_TEMPLATE" "$ADMIN_TEMPLATE"; do
    [[ -r "$template" ]] || die "template not found: $template"
done
[[ -d "$OUT_DIR" ]] || die "not a directory: $OUT_DIR"
if [[ $FORCE -eq 0 ]]; then
    for file in "$RUNTIME_FILE" "$ADMIN_FILE"; do
        [[ -e "$file" ]] && die "$file already exists; nothing written (pass --force to overwrite it)"
    done
fi

interactive=0
[[ $ASSUME_YES -eq 0 && -t 0 ]] && interactive=1

# ask VAR "question" default: prompt only when interactive and VAR's flag was not given.
ask() {
    local var="$1" question="$2" default="$3" given="$4" answer=""
    if [[ "$given" -eq 1 ]]; then return; fi
    if [[ $interactive -eq 1 ]]; then
        read -r -p "$question [${default:-none}]: " answer
    fi
    printf -v "$var" '%s' "${answer:-$default}"
}

web_port="8011"
for kv in "${EXTRA[@]}"; do [[ "$kv" == TCKDB_WEB_PORT=* ]] && web_port="${kv#*=}"; done

ask MODE "Deployment mode (shared_private, hosted_public, local)" "shared_private" "$MODE_SET"
case "$MODE" in
    shared_private|hosted_public|local) ;;
    *) usage_error "deployment mode must be shared_private, hosted_public or local, got '$MODE'" ;;
esac
ask PUBLIC_URL "Public URL of this deployment" "http://127.0.0.1:${web_port}" "$URL_SET"
PUBLIC_URL="${PUBLIC_URL%/}"
PUBLIC_URL="${PUBLIC_URL%/api/v1}"
[[ "$PUBLIC_URL" =~ ^https?://[^[:space:]]+$ ]] || usage_error "public URL must start with http:// or https://, got '$PUBLIC_URL'"
ask PROXY_HEADER "Trusted proxy header (blank for none)" "" "$PROXY_SET"
[[ -z "$PROXY_HEADER" || "$PROXY_HEADER" =~ ^[A-Za-z0-9-]+$ ]] || usage_error "proxy header must be a header name, got '$PROXY_HEADER'"
ask ADMIN_USER "Administrator username" "admin" "$USER_SET"
[[ "$ADMIN_USER" =~ ^[A-Za-z0-9_.@-]+$ ]] || usage_error "administrator username may hold letters, digits and _.@- only"
ask ADMIN_EMAIL "Administrator email (blank for none)" "" "$EMAIL_SET"
[[ -z "$ADMIN_EMAIL" || "$ADMIN_EMAIL" =~ ^[^[:space:]@]+@[^[:space:]@]+$ ]] || usage_error "'$ADMIN_EMAIL' is not an email address"

secret() { openssl rand -hex "$1"; }
password_generated=0
if [[ -z "$ADMIN_PASSWORD" ]]; then
    ADMIN_PASSWORD="$(secret 16)"
    password_generated=1
fi

# set_key FILE KEY VALUE: replace `KEY=...`, else the first `# KEY=...`, else append.
set_key() {
    local file="$1" tmp
    tmp="$(mktemp "${file}.XXXXXX")"
    KEY="$2" VALUE="$3" awk '
        BEGIN { key = ENVIRON["KEY"]; line = key "=" ENVIRON["VALUE"] }
        { rows[++n] = $0 }
        !active && $0 ~ "^" key "=" { active = n }
        !commented && $0 ~ "^#[[:space:]]*" key "=" { commented = n }
        END {
            if (active) rows[active] = line
            else if (commented) rows[commented] = line
            for (i = 1; i <= n; i++) print rows[i]
            if (!active && !commented) print line
        }' "$file" >"$tmp"
    cat "$tmp" >"$file"
    rm -f "$tmp"
}

defines_key() { grep -Eq "^#?[[:space:]]*$2=" "$1"; }

umask 077
staging="$(mktemp -d "$OUT_DIR/.tckdb_make_env.XXXXXX")"
trap 'rm -rf "$staging"' EXIT
runtime="$staging/runtime" admin="$staging/admin"
cp "$RUNTIME_TEMPLATE" "$runtime"
cp "$ADMIN_TEMPLATE" "$admin"

set_key "$runtime" DEPLOYMENT_MODE "$MODE"
set_key "$runtime" DB_PASSWORD "$(secret 24)"
set_key "$runtime" S3_ACCESS_KEY "tckdb$(secret 8)"
set_key "$runtime" S3_SECRET_KEY "$(secret 32)"
set_key "$runtime" SEAWEEDFS_JWT_KEY "$(secret 32)"
set_key "$runtime" PAGING_CURSOR_KEY "$(secret 32)"
set_key "$runtime" TCKDB_PUBLIC_BASE_URL "$PUBLIC_URL/api/v1"
[[ -n "$PROXY_HEADER" ]] && set_key "$runtime" TRUSTED_PROXY_HEADER "$PROXY_HEADER"

# Browsers drop a Secure cookie served over plain HTTP from anything but
# localhost, which breaks browser login.
url_host="${PUBLIC_URL#*://}"
url_host="${url_host%%[:/]*}"
cookie_note=""
if [[ "$PUBLIC_URL" == http://* && "$url_host" != localhost && "$url_host" != 127.0.0.1 ]]; then
    case "$MODE" in
        shared_private)
            set_key "$runtime" SESSION_COOKIE_SECURE false
            set_key "$runtime" SESSION_COOKIE_ALLOW_PLAIN_HTTP true
            cookie_note="the public URL is plain http, so session cookies are not marked Secure (SESSION_COOKIE_SECURE=false); use https to keep them encrypted" ;;
        local)
            set_key "$runtime" SESSION_COOKIE_SECURE false
            cookie_note="the public URL is plain http, so session cookies are not marked Secure" ;;
        hosted_public)
            cookie_note="warning: hosted_public requires Secure cookies, so browser login works only once the public URL is served over https" ;;
    esac
fi

set_key "$admin" DB_ADMIN_PASSWORD "$(secret 24)"
set_key "$admin" DB_OWNER_PASSWORD "$(secret 24)"
set_key "$admin" TCKDB_BOOTSTRAP_USERNAME "$ADMIN_USER"
set_key "$admin" TCKDB_BOOTSTRAP_EMAIL "$ADMIN_EMAIL"
set_key "$admin" TCKDB_BOOTSTRAP_PASSWORD "$ADMIN_PASSWORD"

endpoint_set=0
for kv in "${EXTRA[@]}"; do
    key="${kv%%=*}" value="${kv#*=}"
    [[ "$key" == S3_ENDPOINT_URL ]] && endpoint_set=1
    if defines_key "$admin" "$key" && ! defines_key "$runtime" "$key"; then
        set_key "$admin" "$key" "$value"
    else
        set_key "$runtime" "$key" "$value"
    fi
done
# The host-side endpoint follows a moved S3 port unless it was set explicitly.
for kv in "${EXTRA[@]}"; do
    if [[ "$kv" == S3_PORT=* && $endpoint_set -eq 0 ]]; then
        set_key "$runtime" S3_ENDPOINT_URL "http://127.0.0.1:${kv#*=}"
    fi
done

if grep -Eq '^[A-Za-z_][A-Za-z0-9_]*=change-me' "$runtime" "$admin"; then
    die "a change-me placeholder survived generation; nothing written"
fi

install -m 600 "$runtime" "$RUNTIME_FILE"
install -m 600 "$admin" "$ADMIN_FILE"

echo "wrote $RUNTIME_FILE (API runtime settings)"
echo "wrote $ADMIN_FILE (database owner/admin credentials; never give it to the API)"
[[ -n "$cookie_note" ]] && echo "note: $cookie_note"
echo
echo "administrator: $ADMIN_USER"
if [[ $password_generated -eq 1 ]]; then
    echo "password:      $ADMIN_PASSWORD"
    echo "  (shown once; also in $ADMIN_FILE until you remove TCKDB_BOOTSTRAP_PASSWORD after the first start)"
fi
echo
echo "start it with:"
echo "  docker compose --env-file .env.selfhosted --env-file .env.db-admin --profile selfhost up -d"
