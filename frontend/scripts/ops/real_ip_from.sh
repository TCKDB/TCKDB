#!/bin/sh
# Write the networks nginx accepts X-Real-IP from: the IPv4 subnets this
# container is attached to. A proxy on the host reaches a published port from
# the bridge gateway, and a proxy container beside this one (the `caddy`
# profile) from its own address; both are inside these subnets. A client
# anywhere else, such as a machine on the lab network, is not, so nginx sets
# X-Real-IP to that client's own address whatever it sent.
#
# Usage: real_ip_from.sh [ROUTE_FILE] [OUTPUT]
# Never fails: if the list cannot be written, the empty one in the image
# stays, and nginx then believes X-Real-IP from nobody.
set -u

route_file="${1:-/proc/net/route}"
out="${2:-/etc/nginx/tckdb/real_ip_from.conf}"

# Little-endian hex, as /proc/net/route prints it, to a dotted quad.
dotted() {
    printf '%d.%d.%d.%d' \
        "0x$(echo "$1" | cut -c7-8)" "0x$(echo "$1" | cut -c5-6)" \
        "0x$(echo "$1" | cut -c3-4)" "0x$(echo "$1" | cut -c1-2)"
}

prefix_length() {
    mask=$((0x$1)) bits=0
    while [ "$mask" -ne 0 ]; do
        bits=$((bits + (mask & 1)))
        mask=$((mask >> 1))
    done
    echo "$bits"
}

lines=""
# Directly connected routes: no gateway, not the default route.
while read -r iface destination gateway _flags _refcnt _use _metric mask _rest; do
    case "$iface" in Iface|lo|"") continue ;; esac
    [ "$gateway" = 00000000 ] && [ "$destination" != 00000000 ] || continue
    lines="${lines}set_real_ip_from $(dotted "$destination")/$(prefix_length "$mask");
"
done <"$route_file"

if printf '%s' "$lines" >"$out" 2>/dev/null; then
    echo "$0: X-Real-IP accepted from: $(printf '%s' "$lines" | sed 's/set_real_ip_from //; s/;//' | tr '\n' ' ')"
else
    echo "$0: warning: could not write $out; X-Real-IP is accepted from no address" >&2
fi
exit 0
