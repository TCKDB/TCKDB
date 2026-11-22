#!/usr/bin/env bash
# Write .env.selfhosted and .env.db-admin from the repository templates, with
# every secret generated.
#
# Asks who should reach TCKDB, the deployment mode and the first
# administrator's username and email; every question has a flag, and with
# --yes (or no terminal) the defaults are taken. The administrator password is
# generated unless given, and printed once at the end, with the command that
# starts the stack.
#
# Who reaches TCKDB (--access):
#   local       this machine only. Every port stays on 127.0.0.1 (default).
#   lan         the lab network, over plain http. The web UI is published on
#               this machine's lab address; nothing else is.
#   https       the internet, with your domain name. Caddy answers on 80 and
#               443 and gets a Let's Encrypt certificate.
#   cloudflare  the internet, through a Cloudflare Tunnel. No port is opened.
#
# USAGE
#   backend/scripts/ops/tckdb_make_env.sh [options]
#
# OPTIONS
#   --access ACCESS        local (default), lan, https or cloudflare
#   --lan-address ADDR     lan: this machine's IPv4 address on the lab network
#                          (default: the source address of the default route)
#   --domain NAME          https and cloudflare: the domain name, such as
#                          tckdb.example.org
#   --acme-email EMAIL     https: contact address for Let's Encrypt
#                          (default: none)
#   --mode MODE            shared_private, hosted_public or local (default:
#                          hosted_public for https and cloudflare, else
#                          shared_private)
#   --public-url URL       URL the deployment is reached at, without /api/v1
#                          (default: follows --access)
#   --proxy-header NAME    trusted client-IP header (default: X-Real-IP for
#                          lan and https, CF-Connecting-IP for cloudflare,
#                          none for local); pass '' for none
#   --admin-user NAME      first administrator's username (default admin)
#   --admin-email EMAIL    first administrator's email (default: none)
#   --admin-password PW    use this password instead of generating one; the
#                          TCKDB_BOOTSTRAP_PASSWORD environment variable does
#                          the same without showing it in the process list
#   --set KEY=VALUE        set any other variable; repeatable. Written to
#                          whichever template defines KEY, else .env.selfhosted
#   --dir DIR              where to write the files (default: repository root)
#   --force                overwrite existing files. Every password is
#                          regenerated, so use it only before the first start
#                          or with a fresh database volume: the roles keep the
#                          old passwords (see .env.db-admin.example). Keeps
#                          the compose project the old file ran under
#                          (COMPOSE_PROJECT_NAME, else this directory's name)
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
ACCESS="" LAN_ADDRESS="" DOMAIN="" ACME_EMAIL=""
MODE="" PUBLIC_URL="" PROXY_HEADER="" ADMIN_USER="" ADMIN_EMAIL=""
ADMIN_PASSWORD="${TCKDB_BOOTSTRAP_PASSWORD:-}"
ACCESS_SET=0 LAN_SET=0 DOMAIN_SET=0 ACME_SET=0
MODE_SET=0 URL_SET=0 PROXY_SET=0 USER_SET=0 EMAIL_SET=0
FORCE=0 ASSUME_YES=0
EXTRA=()

need_arg() { [[ $# -ge 2 ]] || usage_error "$1 needs a value"; }
while [[ $# -gt 0 ]]; do
    case "$1" in
        --access) need_arg "$@"; ACCESS="$2"; ACCESS_SET=1; shift 2 ;;
        --lan-address) need_arg "$@"; LAN_ADDRESS="$2"; LAN_SET=1; shift 2 ;;
        --domain) need_arg "$@"; DOMAIN="$2"; DOMAIN_SET=1; shift 2 ;;
        --acme-email) need_arg "$@"; ACME_EMAIL="$2"; ACME_SET=1; shift 2 ;;
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
for file in "$RUNTIME_FILE" "$ADMIN_FILE"; do
    [[ -e "$file" ]] || continue
    [[ $FORCE -eq 1 ]] || die "$file already exists; nothing written (pass --force to overwrite it)"
    echo "warning: overwriting $file with new passwords; a database volume provisioned with the old ones will refuse them" >&2
done

# Compose starts every volume name with the project name. Regenerated files
# keep the project the install runs under (its own setting, else the name
# Compose derives from this directory), or the next `up` finds empty volumes.
keep_project=""
if [[ -e "$RUNTIME_FILE" ]]; then
    keep_project="$(sed -n 's/^COMPOSE_PROJECT_NAME=//p' "$RUNTIME_FILE" | tail -n 1)"
    if [[ -z "$keep_project" ]]; then
        keep_project="$(basename "$REPO_ROOT" | tr '[:upper:]' '[:lower:]' | tr -cd 'a-z0-9_-' | sed 's/^[-_]*//')"
    fi
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

if [[ $ACCESS_SET -eq 0 && $interactive -eq 1 ]]; then
    echo "Who should reach TCKDB?"
    echo "  [1] this machine only"
    echo "  [2] my lab network"
    echo "  [3] the internet, with my domain name (HTTPS)"
    echo "  [4] the internet, through Cloudflare Tunnel"
fi
ask ACCESS "Choose 1-4" "1" "$ACCESS_SET"
case "$ACCESS" in
    1|local) ACCESS=local ;;
    2|lan) ACCESS=lan ;;
    3|https) ACCESS=https ;;
    4|cloudflare) ACCESS=cloudflare ;;
    *) usage_error "access must be local, lan, https or cloudflare, got '$ACCESS'" ;;
esac

ipv4_re='^([0-9]{1,3})\.([0-9]{1,3})\.([0-9]{1,3})\.([0-9]{1,3})$'
domain_re='^([A-Za-z0-9]([A-Za-z0-9-]*[A-Za-z0-9])?\.)+[A-Za-z]{2,}$'
case "$ACCESS" in
    lan)
        detected=""
        if command -v ip >/dev/null 2>&1; then
            detected="$(ip -4 route get 192.0.2.1 2>/dev/null | sed -n 's/.* src \([0-9.]*\).*/\1/p' | head -n 1)"
        fi
        ask LAN_ADDRESS "This machine's address on the lab network" "$detected" "$LAN_SET"
        [[ -n "$LAN_ADDRESS" ]] || usage_error "could not find this machine's lab address; pass --lan-address"
        [[ "$LAN_ADDRESS" =~ $ipv4_re ]] || usage_error "lab address must be an IPv4 address, got '$LAN_ADDRESS'"
        for octet in "${BASH_REMATCH[@]:1}"; do
            (( 10#$octet <= 255 )) || usage_error "lab address must be an IPv4 address, got '$LAN_ADDRESS'"
        done
        [[ "$LAN_ADDRESS" != 127.* && "$LAN_ADDRESS" != 0.0.0.0 ]] \
            || usage_error "lab address must be this machine's address on the network, not $LAN_ADDRESS"
        default_url="http://${LAN_ADDRESS}:${web_port}"
        default_proxy="X-Real-IP" default_mode="shared_private" ;;
    https|cloudflare)
        ask DOMAIN "Domain name, such as tckdb.example.org" "" "$DOMAIN_SET"
        [[ -n "$DOMAIN" ]] || usage_error "--access $ACCESS needs a domain name (--domain)"
        [[ "$DOMAIN" =~ $domain_re ]] || usage_error "domain must be a name such as tckdb.example.org, without http:// or a path, got '$DOMAIN'"
        default_url="https://${DOMAIN}"
        default_mode="hosted_public"
        if [[ "$ACCESS" == https ]]; then
            default_proxy="X-Real-IP"
            ask ACME_EMAIL "Contact email for Let's Encrypt (blank for none)" "" "$ACME_SET"
            [[ -z "$ACME_EMAIL" || "$ACME_EMAIL" =~ ^[^[:space:]@]+@[^[:space:]@]+$ ]] || usage_error "'$ACME_EMAIL' is not an email address"
        else
            default_proxy="CF-Connecting-IP"
        fi ;;
    *)
        default_url="http://127.0.0.1:${web_port}"
        default_proxy="" default_mode="shared_private" ;;
esac
[[ $URL_SET -eq 1 ]] || PUBLIC_URL="$default_url"
[[ $PROXY_SET -eq 1 ]] || PROXY_HEADER="$default_proxy"

ask MODE "Deployment mode (shared_private, hosted_public, local)" "$default_mode" "$MODE_SET"
case "$MODE" in
    shared_private|hosted_public|local) ;;
    *) usage_error "deployment mode must be shared_private, hosted_public or local, got '$MODE'" ;;
esac
PUBLIC_URL="${PUBLIC_URL%/}"
PUBLIC_URL="${PUBLIC_URL%/api/v1}"
[[ "$PUBLIC_URL" =~ ^https?://[^[:space:]]+$ ]] || usage_error "public URL must start with http:// or https://, got '$PUBLIC_URL'"
[[ -z "$PROXY_HEADER" || "$PROXY_HEADER" =~ ^[A-Za-z0-9-]+$ ]] || usage_error "proxy header must be a header name, got '$PROXY_HEADER'"
ask ADMIN_USER "Administrator username" "admin" "$USER_SET"
[[ "$ADMIN_USER" =~ ^[A-Za-z0-9_.@-]+$ ]] || usage_error "administrator username may hold letters, digits and _.@- only"
ask ADMIN_EMAIL "Administrator email (blank for none)" "" "$EMAIL_SET"
[[ -z "$ADMIN_EMAIL" || "$ADMIN_EMAIL" =~ ^[^[:space:]@]+@[^[:space:]@]+$ ]] || usage_error "'$ADMIN_EMAIL' is not an email address"

tunnel_token=""
if [[ "$ACCESS" == cloudflare && $interactive -eq 1 ]]; then
    read -r -s -p "Cloudflare Tunnel token (blank to add it later): " tunnel_token
    echo
fi

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

profiles="selfhost"
case "$ACCESS" in
    lan)
        set_key "$runtime" TCKDB_WEB_BIND "$LAN_ADDRESS" ;;
    https)
        profiles="selfhost,caddy"
        set_key "$runtime" TCKDB_DOMAIN "$DOMAIN"
        [[ -n "$ACME_EMAIL" ]] && set_key "$runtime" TCKDB_ACME_EMAIL "$ACME_EMAIL" ;;
    cloudflare)
        profiles="selfhost,cloudflare"
        [[ -n "$tunnel_token" ]] && set_key "$runtime" CLOUDFLARED_TUNNEL_TOKEN "$tunnel_token" ;;
esac
set_key "$runtime" COMPOSE_PROFILES "$profiles"
[[ -n "$keep_project" ]] && set_key "$runtime" COMPOSE_PROJECT_NAME "$keep_project"

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
if [[ -n "$keep_project" && "$keep_project" != tckdb ]] \
        && grep -qx "COMPOSE_PROJECT_NAME=$keep_project" "$RUNTIME_FILE"; then
    echo "note: kept the compose project this install runs under, COMPOSE_PROJECT_NAME=$keep_project, so its volumes stay in use"
fi
echo
echo "administrator: $ADMIN_USER"
if [[ $password_generated -eq 1 ]]; then
    echo "password:      $ADMIN_PASSWORD"
    echo "  (shown once; also in $ADMIN_FILE until you remove TCKDB_BOOTSTRAP_PASSWORD after the first start)"
fi
echo
case "$ACCESS" in
    lan)
        if command -v ip >/dev/null 2>&1 && ! ip -4 -o addr show 2>/dev/null | grep -q " ${LAN_ADDRESS}/"; then
            echo "warning: $LAN_ADDRESS is not an address of this machine; the web UI cannot be published on it"
        fi
        IFS=. read -r o1 o2 _ _ <<<"$LAN_ADDRESS"
        if ! (( o1 == 10 || (o1 == 172 && o2 >= 16 && o2 <= 31) || (o1 == 192 && o2 == 168) \
                || (o1 == 100 && o2 >= 64 && o2 <= 127) || (o1 == 169 && o2 == 254) )); then
            echo "warning: $LAN_ADDRESS is a public address; anyone who can reach it gets the web UI over plain http. Use --access https, or limit it to the lab at the network's firewall"
        fi
        echo "before you start:"
        echo "  - allow TCP port $web_port from the lab network in this machine's firewall, if it has one" ;;
    https)
        echo "before you start:"
        echo "  - point a DNS A (or AAAA) record for $DOMAIN at this server's public address"
        echo "  - let TCP ports 80 and 443 and UDP port 443 reach this server" ;;
    cloudflare)
        echo "before you start:"
        grep -Eq '^CLOUDFLARED_TUNNEL_TOKEN=.' "$RUNTIME_FILE" \
            || echo "  - add CLOUDFLARED_TUNNEL_TOKEN=<token> to $RUNTIME_FILE"
        echo "  - in the tunnel's settings, route the public hostname $DOMAIN to http://127.0.0.1:$web_port" ;;
esac
if [[ "$ACCESS" == lan || "$ACCESS" == https ]]; then
    image_tag="$(sed -n 's/^TCKDB_IMAGE_TAG=//p' "$RUNTIME_FILE")"
    if [[ "$image_tag" == 0.2.0 ]]; then
        echo "  - this access needs an image newer than 0.2.0; set TCKDB_IMAGE_TAG in $RUNTIME_FILE to 0.3.0 or later"
    fi
fi
[[ "$ACCESS" == local ]] || echo
profile_flags="--profile ${profiles//,/ --profile }"
echo "start it with:"
echo "  docker compose --env-file .env.selfhosted --env-file .env.db-admin $profile_flags up -d --wait"
echo "then open $PUBLIC_URL"
