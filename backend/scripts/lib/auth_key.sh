#!/usr/bin/env bash
# Shared API-key handling for the scripts that talk to /auth/api-keys.
#
# Sourced, not executed. Defines two functions:
#
#   extract_api_key <json>   print the plaintext key, or nothing
#   mask_key <key>           print a masked form safe to echo
#
# ---------------------------------------------------------------------------
# Why this is shared rather than written twice
# ---------------------------------------------------------------------------
# ``dev_login.sh`` and ``tckdb_auth.sh`` mint a key against the *same*
# endpoint, and they had drifted into two different levels of care about the
# same response. ``tckdb_auth.sh`` parsed the body with python3 and refused
# when no key field was present; ``dev_login.sh`` did
#
#     API_KEY="$(jq -r '.key' <<<"$KEY_RESPONSE")"
#
# and ``jq -r`` prints the four characters ``null`` and **exits 0** for a
# document with no ``key`` field. Under ``set -e`` that is indistinguishable
# from success, so the script wrote ``export TCKDB_API_KEY='null'`` to the auth
# env file, chmod 600'd it, printed a masked value that looked like a real
# credential, and reported ``==> Done.`` The next command to use the key got a
# 401 whose obvious reading is "the server rejected my key" rather than "there
# is no key". One implementation, sourced by both, is the only way the two
# cannot disagree again.
#
# ---------------------------------------------------------------------------
# Why python3 and not jq
# ---------------------------------------------------------------------------
# Two reasons, and the second is the one that bit.
#
# 1. jq is not guaranteed present. python3 is: every path that runs these
#    scripts already has the conda env, and the API cannot be developed
#    without it.
# 2. jq's ``-r`` renders JSON ``null`` and a *missing key* as the literal
#    string ``null`` on stdout with exit status 0. Distinguishing "the field
#    holds null" from "the field is absent" from "the field holds the string
#    'null'" needs ``// empty`` or ``-e``, neither of which anybody remembers
#    to write. The python form below returns a value only when the field is a
#    non-empty string, so absence is empty output and the caller's ``-z`` test
#    is correct by construction.
#
# The document is passed as an *argument*, never on stdin -- see the note in
# tckdb_auth.sh about heredocs and ``conda run`` eating the stream.

# Print the first non-empty string field naming an API key, or nothing.
#
# The candidate list is deliberately broader than the current response schema
# (`key`): these scripts are run against deployments that may be older or
# newer than the checkout, and guessing wrong should degrade to "no key
# found", never to a wrong key.
extract_api_key() {
    python3 -c '
import json, sys
try:
    data = json.loads(sys.argv[1])
except Exception:
    sys.exit(0)
if not isinstance(data, dict):
    sys.exit(0)
for field in ("key", "api_key", "token", "plain_key", "secret"):
    value = data.get(field)
    if isinstance(value, str) and value:
        print(value)
        break
' "$1"
}

# Mask "abcdef...wxyz" -- never log the full key.
#
# ASCII only. The ellipsis here used to be U+2026, which nothing checked
# because scripts/check_runtime_ascii.py only walked *.py. It now walks *.sh
# too, so this line is covered by the same gate as the Python emission sites.
mask_key() {
    local k="$1"
    local n=${#k}
    if (( n <= 10 )); then
        printf '***'
    else
        printf '%s...%s' "${k:0:6}" "${k: -4}"
    fi
}

# Refuse a response that carries no usable key, naming what was actually
# received. Prints the key on stdout on success.
#
# The failure message quotes the raw response because the only way to tell a
# schema change from an auth failure from a proxy returning HTML is to see the
# body. These scripts run against dev and self-hosted deployments only, and the
# body of a *failed* key mint carries no credential.
require_api_key() {
    local response="$1"
    local key
    key="$(extract_api_key "$response")"
    if [[ -z "$key" ]]; then
        echo "error: the API-key response carried no usable key." >&2
        echo "tried fields: key, api_key, token, plain_key, secret" >&2
        echo "raw response:" >&2
        echo "$response" >&2
        return 1
    fi
    printf '%s' "$key"
}
