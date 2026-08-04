#!/usr/bin/env bash
# Poll a TCKDB deployment and push to ntfy when something is wrong.
#
# WHAT THIS DOES
#   Fetches /api/v1/status, decides whether the deployment is healthy, and
#   sends a phone push via ntfy.sh only when the answer CHANGES. You are told
#   when it breaks and when it recovers, and not once every five minutes in
#   between -- an alert you learn to ignore is worse than no alert.
#
# WHAT THIS DOES NOT DO
#   It runs *on the host it watches*, so it cannot tell you the host is gone.
#   If the Pi loses power this script dies with it and you hear nothing. That
#   is a failure-domain problem, not a bug, and the fix is a DEAD MAN'S SWITCH:
#   an external service that expects a regular ping and alerts on SILENCE.
#   See the runbook (backend/docs/deployment/monitoring.md) -- it is about ten
#   minutes of setup and it is what covers total death.
#
# SETUP
#   1. Pick a topic name nobody will guess. ntfy topics are public to anyone
#      who knows the name, so treat it like a password:
#        TCKDB_NTFY_TOPIC=tckdb-<something-random>
#   2. Install the ntfy app, subscribe to that topic.
#   3. Copy this script and the .timer/.service units onto the host, set the
#      env vars, enable the timer.
#
# ENVIRONMENT
#   TCKDB_STATUS_URL   default https://tckdb.homecalvin.com/api/v1/status
#   TCKDB_NTFY_TOPIC   required -- no default on purpose; a guessable topic
#                      leaks your operational state to anyone who subscribes
#   TCKDB_NTFY_SERVER  default https://ntfy.sh
#   TCKDB_STATE_FILE   default ~/.cache/tckdb-alert-state
set -uo pipefail

STATUS_URL="${TCKDB_STATUS_URL:-https://tckdb.homecalvin.com/api/v1/status}"
NTFY_SERVER="${TCKDB_NTFY_SERVER:-https://ntfy.sh}"
STATE_FILE="${TCKDB_STATE_FILE:-$HOME/.cache/tckdb-alert-state}"

if [[ -z "${TCKDB_NTFY_TOPIC:-}" ]]; then
    echo "TCKDB_NTFY_TOPIC is not set; refusing to run." >&2
    exit 2
fi

mkdir -p "$(dirname "$STATE_FILE")"
previous="$(cat "$STATE_FILE" 2>/dev/null || echo "unknown")"

# --max-time so a hung server cannot wedge the timer. --fail is deliberately
# NOT used: /status returns 200 while degraded and we want to read the body.
# The HTTP code is captured separately because a 404 body parses as "no status
# field" and would otherwise be reported as a degraded deployment -- true, but
# for the wrong reason and with a misleading message. A 404 here means the
# endpoint is missing (old build deployed), which is a different problem from
# a component being unhealthy.
raw="$(curl -sS --max-time 20 -w '\n%{http_code}' "$STATUS_URL" 2>/dev/null)"
curl_rc=$?
http_code="$(tail -n1 <<<"$raw")"
response="$(sed '$d' <<<"$raw")"

if [[ $curl_rc -ne 0 || -z "$response" ]]; then
    current="unreachable"
    title="TCKDB unreachable"
    detail="No response from ${STATUS_URL} (curl exit ${curl_rc}). The API is down, the host is unreachable, or TLS/proxy is broken."
    priority="urgent"
    tags="rotating_light"
elif [[ "$http_code" != "200" ]]; then
    current="bad_endpoint"
    title="TCKDB status endpoint returned HTTP ${http_code}"
    detail="${STATUS_URL} answered ${http_code}. A 404 usually means a build without /status is deployed; a 5xx means the API is failing before it can report. Either way the deployment is not reporting its own health."
    priority="high"
    tags="warning"
else
    # Prefer jq; fall back to grep so a host without jq still alerts.
    if command -v jq >/dev/null 2>&1; then
        status="$(jq -r '.status // "unparseable"' <<<"$response")"
        degraded="$(jq -r '(.degraded // []) | join(", ")' <<<"$response")"
        reasons="$(jq -r '[.components // {} | to_entries[] | select(.value.healthy == false) | "\(.key): \(.value.reason // "unhealthy")"] | join("; ")' <<<"$response")"
    else
        status="$(grep -o '"status"[[:space:]]*:[[:space:]]*"[^"]*"' <<<"$response" | head -1 | sed 's/.*"\([^"]*\)"$/\1/')"
        degraded="(install jq for detail)"
        reasons="(install jq for detail)"
    fi

    if [[ "$status" == "ok" ]]; then
        current="ok"
        title="TCKDB recovered"
        detail="All components healthy again."
        priority="default"
        tags="white_check_mark"
    else
        current="degraded"
        title="TCKDB degraded"
        detail="Degraded: ${degraded:-unknown}. ${reasons}"
        priority="high"
        tags="warning"
    fi
fi

# Only notify on a state change. Recovery is worth a push too -- otherwise you
# are left wondering whether it is still broken.
if [[ "$current" != "$previous" ]]; then
    if [[ "$current" == "ok" && "$previous" == "unknown" ]]; then
        : # First ever run and everything is fine: stay quiet.
    else
        curl -sS --max-time 20 \
            -H "Title: ${title}" \
            -H "Priority: ${priority}" \
            -H "Tags: ${tags}" \
            -d "${detail}" \
            "${NTFY_SERVER}/${TCKDB_NTFY_TOPIC}" >/dev/null || \
            echo "warning: failed to publish to ntfy" >&2
    fi
    printf '%s' "$current" > "$STATE_FILE"
fi

echo "$(date -Is) status=${current} (was ${previous})"
[[ "$current" == "ok" ]] && exit 0 || exit 1
