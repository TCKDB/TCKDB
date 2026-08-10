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
# HOW THIS SCRIPT'S OWN DEATH BECOMES VISIBLE
#   Every failure of this checker looks, from the outside, exactly like a
#   healthy deployment: silence. The timer never got enabled, the unit failed
#   on a missing EnvironmentFile, ExecStart points at a path the repo has since
#   moved, the host is off -- in every one of those cases nothing is pushed and
#   nothing complains, and the previous version of this script had no way to
#   tell you. Two mechanisms now close that, and they are deliberately in
#   different failure domains:
#
#     1. TCKDB_DEADMAN_URL. Pinged once per COMPLETED run, whatever the
#        verdict. "Degraded" is a completed run: the checker did its job and
#        already pushed about it, so suppressing the ping there would convert a
#        component outage into a spurious "host is gone" page -- the wrong page
#        of the runbook, which is the exact mistake the /status endpoint was
#        shaped to avoid. The ping means "the checker ran", never "the system
#        is well". Conversely the ping is NOT sent when this script aborts
#        before reaching a verdict, because that is a dead checker and the
#        whole point is for the external service to notice the silence.
#
#     2. tckdb-alert-failed.service, wired as OnFailure= on the unit. That
#        catches the cases where this script never gets far enough to ping
#        anything -- but it runs on the same host, so it cannot catch the host
#        dying. Hence both.
#
#   If TCKDB_DEADMAN_URL is unset the checker says so once, on the first run,
#   as a push. An unmonitored monitor is worth exactly one interruption.
#
# SETUP
#   1. Pick a topic name nobody will guess. ntfy topics are public to anyone
#      who knows the name, so treat it like a password:
#        TCKDB_NTFY_TOPIC=tckdb-<something-random>
#   2. Install the ntfy app, subscribe to that topic.
#   3. Copy this script and the .timer/.service units onto the host, set the
#      env vars, enable the timer.
#   4. Create a check on an external dead-man service (healthchecks.io) and set
#      TCKDB_DEADMAN_URL to its ping URL.
#
# ENVIRONMENT
#   TCKDB_STATUS_URL   default https://tckdb.homecalvin.com/api/v1/status
#   TCKDB_NTFY_TOPIC   required -- no default on purpose; a guessable topic
#                      leaks your operational state to anyone who subscribes
#   TCKDB_NTFY_SERVER  default https://ntfy.sh
#   TCKDB_STATE_FILE   default ~/.cache/tckdb-alert-state
#   TCKDB_DEADMAN_URL  optional but strongly recommended -- external URL pinged
#                      after every completed run; its silence is what tells you
#                      this checker died
set -uo pipefail

STATUS_URL="${TCKDB_STATUS_URL:-https://tckdb.homecalvin.com/api/v1/status}"
NTFY_SERVER="${TCKDB_NTFY_SERVER:-https://ntfy.sh}"
STATE_FILE="${TCKDB_STATE_FILE:-$HOME/.cache/tckdb-alert-state}"
DEADMAN_URL="${TCKDB_DEADMAN_URL:-}"

if [[ -z "${TCKDB_NTFY_TOPIC:-}" ]]; then
    echo "TCKDB_NTFY_TOPIC is not set; refusing to run." >&2
    exit 2
fi

mkdir -p "$(dirname "$STATE_FILE")"
previous="$(cat "$STATE_FILE" 2>/dev/null || echo "unknown")"

# Marker for the one-shot "you have no dead man's switch" notice. A separate
# file rather than a second field in the state file, so the state file stays a
# single token that a human can read and edit with `echo`.
DEADMAN_NOTICE_FILE="${STATE_FILE}.deadman-notice"

publish() {
    # publish <title> <priority> <tags> <body>; returns curl's exit status so
    # the caller can decide whether the notification actually happened.
    #
    # --fail, unlike the /status fetch above, because here an HTTP error IS
    # the answer: a 401 from a protected topic or a 5xx from ntfy means the
    # push did not reach anyone, and without --fail curl reports success and
    # the caller records an alert that was never delivered.
    curl -sS --fail --max-time 20 \
        -H "Title: $1" \
        -H "Priority: $2" \
        -H "Tags: $3" \
        -d "$4" \
        "${NTFY_SERVER}/${TCKDB_NTFY_TOPIC}" >/dev/null
}

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
#
# The state file is advanced ONLY once the push has actually gone out. The
# previous version advanced it unconditionally, so a single failed publish --
# ntfy unreachable, DNS down, topic typo -- recorded "you have been told" when
# nobody had been, and the deployment then stayed silently broken until its
# next state change. Leaving the state unadvanced makes the next run retry,
# which is the behaviour a five-minute timer exists to provide.
if [[ "$current" != "$previous" ]]; then
    if [[ "$current" == "ok" && "$previous" == "unknown" ]]; then
        # First ever run and everything is fine: stay quiet, but do record the
        # state so a later break is still an edge.
        printf '%s' "$current" > "$STATE_FILE"
    elif publish "$title" "$priority" "$tags" "$detail"; then
        printf '%s' "$current" > "$STATE_FILE"
    else
        echo "warning: failed to publish to ntfy; state not advanced, will retry" >&2
    fi
fi

# An unmonitored monitor, said once. Without a dead man's switch every failure
# of this script is indistinguishable from a healthy deployment, so the gap is
# worth exactly one interruption -- and not one every five minutes, which is
# how a useful alert becomes one you filter.
if [[ -z "$DEADMAN_URL" && ! -e "$DEADMAN_NOTICE_FILE" ]]; then
    if publish "TCKDB checker has no dead man's switch" "default" "warning" \
        "TCKDB_DEADMAN_URL is unset on $(hostname 2>/dev/null || echo "this host"), so if this health checker stops running -- timer disabled, unit failed, host powered off -- nothing will tell you, and the silence will look exactly like a healthy deployment. Setup: backend/docs/deployment/monitoring.md"; then
        : > "$DEADMAN_NOTICE_FILE"
    fi
fi

# THE DEAD MAN'S PING. Sent for every verdict, including "degraded" and
# "unreachable": it asserts that THIS SCRIPT RAN, which is a different claim
# from "TCKDB is well" and is carried by a different channel on purpose. Tying
# it to a healthy verdict would mean a broken component silences the host
# heartbeat too, and the external service would page "the Pi is gone" while the
# Pi was busy telling you precisely which component had failed.
#
# It is deliberately unreachable from the early `exit 2` above: a checker that
# cannot even start must go quiet, because that silence is the only signal a
# dead checker can send.
if [[ -n "$DEADMAN_URL" ]]; then
    # --fail so a typo'd ping URL (404) is reported rather than counted as a
    # heartbeat that went nowhere.
    curl -sS --fail --max-time 20 -o /dev/null "$DEADMAN_URL" || \
        echo "warning: dead man's switch ping to ${DEADMAN_URL} failed" >&2
fi

echo "$(date -Is) status=${current} (was ${previous})"
[[ "$current" == "ok" ]] && exit 0 || exit 1
