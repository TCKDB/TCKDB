#!/bin/sh
# Close SeaweedFS's internal ports to the Docker host itself (#548).
#
# WHAT THIS CLOSES
#   docker-compose.yml puts `seaweedfs` on its own `storage` network, which
#   keeps every other container away from its ports (#545, #547). The host's
#   own network namespace is not a container: Docker bridge addresses are
#   routable from it, so any process on the host -- and any container run
#   with `network_mode: host`, such as `cloudflared` -- could still reach the
#   filer, master and volume server, and their gRPC ports, which take no
#   credentials. Over gRPC, `weed shell` deletes objects and whole
#   collections.
#
#   This inserts one rule at the top of the host's OUTPUT chain: new TCP
#   connections from the host to the storage network's subnet are reset,
#   except to the S3 port. The published S3 forward (127.0.0.1:9000 by
#   default) is made from the host namespace by docker-proxy, so that port
#   must stay open, and it is the one port that checks a signature.
#
# WHY OUTPUT AND NOT DOCKER-USER
#   DOCKER-USER is jumped to from FORWARD. Traffic the host originates never
#   passes FORWARD, so a DOCKER-USER rule cannot see it. Traffic between
#   containers on the storage network is bridged and goes through FORWARD,
#   never OUTPUT, so this rule cannot touch the API or the worker.
#
# USAGE
#   tckdb_storage_hostfw.sh [--network NAME] [--allow-ports LIST] [--wait SECONDS]
#   tckdb_storage_hostfw.sh --check  [--network NAME] [--allow-ports LIST]
#   tckdb_storage_hostfw.sh --remove
#
#   The network is, in order: --network, $TCKDB_STORAGE_NETWORK,
#   "${COMPOSE_PROJECT_NAME}_storage", or else the one network Compose
#   labelled `storage`. If Compose made more than one, this refuses to guess.
#
#   --allow-ports (or $TCKDB_STORAGE_ALLOW_PORTS) is the container port(s)
#   left open, comma-separated; default 9000, the S3 gateway.
#
#   --wait gives Docker up to SECONDS to answer and the network to appear,
#   for use at boot. --check changes nothing and exits non-zero unless the
#   rule is in place for the network's current subnet(s). --remove deletes
#   every rule this script made, in both address families, and needs no
#   Docker.
#
#   Applying is idempotent: every rule carrying the tag below is deleted,
#   then one rule per subnet is inserted. So a re-run leaves one rule, and a
#   subnet that changed (the network was recreated) replaces the old one.
#   A missing network fails before anything is deleted, so the last good
#   rule stays in place.
#
#   Linux with iptables (legacy or nft backend) only. Must run as root.
#   See docs/deployment/self_hosted_single_node.md, "Closing the object
#   store to the host".
set -eu

TAG="tckdb-548-storage-hostfw"
ME="tckdb-storage-hostfw"

MODE=apply
NET="${TCKDB_STORAGE_NETWORK:-}"
ALLOW_PORTS="${TCKDB_STORAGE_ALLOW_PORTS:-9000}"
WAIT=0

say() { echo "$ME: $*"; }
die() {
    echo "$ME: $*" >&2
    exit 1
}

usage() {
    sed -n 's/^#   tckdb_storage_hostfw.sh/tckdb_storage_hostfw.sh/p' "$0"
}

while [ $# -gt 0 ]; do
    case "$1" in
        --network)
            [ $# -ge 2 ] || die "--network needs a value"
            NET="$2"
            shift 2
            ;;
        --allow-ports)
            [ $# -ge 2 ] || die "--allow-ports needs a value"
            ALLOW_PORTS="$2"
            shift 2
            ;;
        --wait)
            [ $# -ge 2 ] || die "--wait needs a value"
            WAIT="$2"
            shift 2
            ;;
        --check)
            [ "$MODE" = apply ] || die "--check and --remove cannot be combined"
            MODE=check
            shift
            ;;
        --remove)
            [ "$MODE" = apply ] || die "--check and --remove cannot be combined"
            MODE=remove
            shift
            ;;
        -h | --help)
            usage
            exit 0
            ;;
        *) die "unknown argument: $1 (see --help)" ;;
    esac
done

case "$WAIT" in
    '' | *[!0-9]*) die "--wait must be a whole number of seconds, got '$WAIT'" ;;
esac

# --- the rule --------------------------------------------------------------

# Ports: 1-15 comma-separated numbers (15 is the multiport limit).
case "$ALLOW_PORTS" in
    '' | *[!0-9,]* | ,* | *, | *,,*) die "allowed ports must be comma-separated port numbers, got '$ALLOW_PORTS'" ;;
esac
port_count=0
for p in $(echo "$ALLOW_PORTS" | tr ',' ' '); do
    { [ "$p" -ge 1 ] && [ "$p" -le 65535 ]; } || die "allowed port out of range: $p"
    port_count=$((port_count + 1))
done
[ "$port_count" -le 15 ] || die "at most 15 allowed ports, got $port_count"
if [ "$port_count" -eq 1 ]; then
    PORT_MATCH="-m tcp ! --dport $ALLOW_PORTS"
else
    PORT_MATCH="-m multiport ! --dports $ALLOW_PORTS"
fi

# The rule for one subnet, written in the canonical order `iptables -S`
# prints it, so what is inserted, checked and listed reads the same.
spec() {
    echo "-d $1 -p tcp $PORT_MATCH -m conntrack --ctstate NEW -m comment --comment $TAG -j REJECT --reject-with tcp-reset"
}

# Every OUTPUT rule carrying the tag, as `iptables -S` prints it.
tagged_rules() {
    printf '%s\n' "$1" | grep -E -- "^-A OUTPUT .*--comment \"?$TAG\"?( |\$)" || true
}

# Delete every tagged rule from one family. The listing is read first so a
# failing `-S` stops the script rather than looking like "nothing to delete".
purge() {
    ipt="$1"
    listing=$("$ipt" -S OUTPUT)
    removed=0
    rules=$(tagged_rules "$listing")
    [ -n "$rules" ] || {
        echo 0
        return 0
    }
    # One rule per line, each split into words for `-D`. The tagged rules
    # hold no quoted or spaced arguments, and globbing is off so nothing
    # expands.
    set -f
    while IFS= read -r rule; do
        # shellcheck disable=SC2086 # word splitting is the point
        "$ipt" -D ${rule#-A }
        removed=$((removed + 1))
    done <<EOF
$rules
EOF
    set +f
    echo "$removed"
}

# --- remove: needs no Docker ------------------------------------------------

if [ "$MODE" = remove ]; then
    command -v iptables >/dev/null 2>&1 || die "iptables not found; this rule is Linux iptables-specific"
    n=$(purge iptables)
    say "removed $n iptables rule(s) tagged $TAG"
    if command -v ip6tables >/dev/null 2>&1; then
        n=$(purge ip6tables)
        say "removed $n ip6tables rule(s) tagged $TAG"
    else
        say "ip6tables not found; no IPv6 rules to remove"
    fi
    exit 0
fi

# --- which network, and its subnets ------------------------------------------

command -v docker >/dev/null 2>&1 || die "docker not found"

# Prints the network name, or fails: exit 1 = not there (yet), exit 2 = will
# never resolve on its own (ambiguous).
resolve_network() {
    if [ -n "$NET" ]; then
        echo "$NET"
        return 0
    fi
    if [ -n "${COMPOSE_PROJECT_NAME:-}" ]; then
        echo "${COMPOSE_PROJECT_NAME}_storage"
        return 0
    fi
    found=$(docker network ls --filter label=com.docker.compose.network=storage --format '{{.Name}}' 2>/dev/null) || return 1
    count=$(printf '%s\n' "$found" | grep -c . || true)
    case "$count" in
        0) return 1 ;;
        1)
            echo "$found"
            return 0
            ;;
        *)
            {
                echo "$ME: more than one Compose network is labelled 'storage', refusing to guess:"
                printf '%s\n' "$found" | sed 's/^/    /'
                echo "$ME: name one with --network or TCKDB_STORAGE_NETWORK"
            } >&2
            return 2
            ;;
    esac
}

subnets_of() {
    docker network inspect "$1" --format '{{range .IPAM.Config}}{{println .Subnet}}{{end}}' 2>/dev/null
}

waited=0
while :; do
    rc=0
    name=$(resolve_network) || rc=$?
    [ "$rc" -ne 2 ] || exit 1
    if [ "$rc" -eq 0 ] && SUBNETS=$(subnets_of "$name"); then
        NET="$name"
        break
    fi
    if [ "$waited" -ge "$WAIT" ]; then
        if [ -n "$NET" ] || [ -n "${COMPOSE_PROJECT_NAME:-}" ]; then
            die "network ${name:-$NET} not found (is Docker running, and is this the right name?); nothing was changed"
        fi
        die "no Compose network labelled 'storage' found (is Docker running?); name one with --network or TCKDB_STORAGE_NETWORK; nothing was changed"
    fi
    sleep 1
    waited=$((waited + 1))
done

V4=$(printf '%s\n' "$SUBNETS" | grep -v ':' | grep . || true)
V6=$(printf '%s\n' "$SUBNETS" | grep ':' || true)
[ -n "$V4$V6" ] || die "network $NET has no subnet; nothing was changed"

command -v iptables >/dev/null 2>&1 || die "iptables not found; this rule is Linux iptables-specific (see the deployment docs for other hosts)"
if [ -n "$V6" ]; then
    command -v ip6tables >/dev/null 2>&1 || die "network $NET has an IPv6 subnet but ip6tables is not installed; nothing was changed"
fi

# --- check --------------------------------------------------------------------

# Prints findings; returns non-zero if the family is not as it should be.
check_family() {
    ipt="$1"
    subnets="$2"
    listing=$("$ipt" -S OUTPUT)
    rules=$(tagged_rules "$listing")
    have=$(printf '%s\n' "$rules" | grep -c . || true)
    bad=0
    present=0
    for s in $subnets; do
        # shellcheck disable=SC2046 # word splitting is the point
        if "$ipt" -C OUTPUT $(spec "$s") 2>/dev/null; then
            say "$ipt: present for $NET ($s)"
            present=$((present + 1))
        else
            say "$ipt: MISSING for $NET ($s)"
            bad=1
        fi
    done
    if [ "$have" -gt "$present" ]; then
        say "$ipt: $((have - present)) stale rule(s) tagged $TAG (an old subnet, other ports, or a duplicate):"
        printf '%s\n' "$rules" | sed 's/^/    /'
        bad=1
    fi
    # Our rules must come first: a rule above them that accepts new
    # connections (ufw's output chains do) would let traffic through first.
    if [ "$have" -gt 0 ]; then
        first=$(printf '%s\n' "$listing" | grep '^-A OUTPUT ' | head -n "$have")
        if [ "$(tagged_rules "$first" | grep -c . || true)" -ne "$have" ]; then
            say "$ipt: another OUTPUT rule precedes the tagged rule(s) and may accept the traffic first; re-run this script to move them to the top"
            bad=1
        fi
    fi
    return "$bad"
}

if [ "$MODE" = check ]; then
    status=0
    if [ -n "$V4" ]; then
        check_family iptables "$V4" || status=1
    else
        say "iptables: $NET has no IPv4 subnet"
        check_family iptables "" || status=1
    fi
    if [ -n "$V6" ]; then
        check_family ip6tables "$V6" || status=1
    elif command -v ip6tables >/dev/null 2>&1; then
        say "ip6tables: $NET has no IPv6 subnet, so no rule is expected"
        check_family ip6tables "" || status=1
    else
        say "ip6tables: $NET has no IPv6 subnet and ip6tables is not installed; skipped"
    fi
    if [ "$status" -eq 0 ]; then
        say "OK: host -> $NET tcp (except $ALLOW_PORTS) is rejected"
    else
        say "NOT OK: re-run without --check to apply"
    fi
    exit "$status"
fi

# --- apply ---------------------------------------------------------------------

apply_family() {
    ipt="$1"
    subnets="$2"
    purge "$ipt" >/dev/null
    for s in $subnets; do
        # shellcheck disable=SC2046 # word splitting is the point
        "$ipt" -I OUTPUT 1 $(spec "$s")
        say "host -> $s tcp (except $ALLOW_PORTS) rejected ($ipt)"
    done
}

apply_family iptables "$V4"
if [ -z "$V4" ]; then
    say "$NET has no IPv4 subnet; no iptables rule"
fi
if [ -n "$V6" ]; then
    apply_family ip6tables "$V6"
elif command -v ip6tables >/dev/null 2>&1; then
    # Still purge, so a network recreated without IPv6 leaves no old rule.
    apply_family ip6tables ""
    say "$NET has no IPv6 subnet; no ip6tables rule"
else
    say "$NET has no IPv6 subnet; no ip6tables rule (ip6tables not installed)"
fi
