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
#
# WARNINGS ARE A SEPARATE, QUIETER CHANNEL
#   /status carries two verdict fields. `degraded` lists components that are
#   BROKEN, and this script pages on it at Priority: high. `warnings` lists
#   things that are TRUE BUT NOT YET BROKEN -- today, an object store whose
#   remaining room has fallen below the largest artifact TCKDB accepts, which
#   is worth knowing about hours before the first upload fails.
#
#   A warning is pushed at Priority: low, does NOT change the verdict, and
#   does NOT change the exit code. That separation is the whole point: an
#   alert that fires while every upload still succeeds is an alert you learn
#   to swipe away, and you swipe the real ones away with it. Warnings get
#   their own state file so a standing warning is pushed once rather than
#   every five minutes, on the same edge-triggered discipline as the verdict.
#
# EXIT CODES
#   0  the deployment is healthy
#   1  a VERDICT of unhealthy: unreachable, bad endpoint, or degraded. The unit
#      lists this in SuccessExitStatus, because a degraded deployment is
#      information the script delivered successfully, not a failure of the
#      script -- the push has already gone out and OnFailure= must not fire.
#   2  no verdict was reached: no topic, or the script died before deciding.
#      Never in SuccessExitStatus, so systemd marks the unit failed and
#      tckdb-alert-failed.service pushes.
#
# WHY 1 IS NOT ALLOWED TO MEAN BOTH THINGS
#   bash exits 1 when it dies on an unbound variable under `set -u`, and 1 is
#   also this script's "the deployment is unhealthy". SuccessExitStatus=0 1
#   therefore told systemd to call an accidental death a success, so OnFailure=
#   never fired for the entire class of bug that kills the script partway
#   through -- and a dead checker looks exactly like a healthy deployment.
#   The trap below closes that: 1 keeps its meaning only once a verdict exists,
#   and any other early exit is reported as 2.
set -uo pipefail

verdict_reached=0
on_exit() {
    local rc=$?
    if [[ "$rc" -ne 0 && "$verdict_reached" -eq 0 ]]; then
        # Quiet when the code is already 2 -- that is a deliberate refusal
        # further down, which has said its own piece.
        if [[ "$rc" -ne 2 ]]; then
            echo "error: the checker exited ${rc} before reaching a verdict; reporting 2 so systemd does not read it as a completed check." >&2
        fi
        exit 2
    fi
    exit "$rc"
}
trap on_exit EXIT

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

# Warnings are edge-triggered like the verdict, but on their own axis: a store
# can start warning while the deployment stays healthy, and can stop warning
# while it stays degraded. One token here rather than a second field in the
# state file, for the same reason the deadman notice is its own file -- the
# state file stays something a human can read and overwrite with `echo`.
WARNING_STATE_FILE="${STATE_FILE}.warnings"

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

# Set on the 200 path only; declared here because `set -u` is on and the
# unreachable and bad-endpoint paths never reach the parser. Empty means "no
# warnings", which is also the right answer for a build too old to have the
# field -- an absent `warnings` array is not a warning.
warnings=""

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
    # Prefer jq; fall back to grep/sed so a host without jq still alerts.
    #
    # `degraded` is parsed on BOTH paths, because it is now part of the
    # decision and not just of the message. The old fallback put a literal
    # "(install jq for detail)" here, which was harmless while only `status`
    # decided anything and would have been a permanent false alarm now.
    if command -v jq >/dev/null 2>&1; then
        status="$(jq -r '.status // "unparseable"' <<<"$response")"
        degraded="$(jq -r '(.degraded // []) | join(", ")' <<<"$response")"
        reasons="$(jq -r '[.components // {} | to_entries[] | select(.value.healthy == false) | "\(.key): \(.value.reason // "unhealthy")"] | join("; ")' <<<"$response")"
        warnings="$(jq -r '[.warnings // [] | .[] | .summary // .code // "unspecified warning"] | join(" | ")' <<<"$response")"
    else
        status="$(grep -o '"status"[[:space:]]*:[[:space:]]*"[^"]*"' <<<"$response" | head -1 | sed 's/.*"\([^"]*\)"$/\1/')"
        # `degraded` is an ARRAY. A string-shaped grep returns empty for every
        # value including a non-empty one -- i.e. it would report a degraded
        # deployment as clean, which is the failure this whole file exists to
        # prevent. Match the array, as tckdb_deploy.sh does.
        degraded="$(sed -n 's/.*"degraded"[[:space:]]*:[[:space:]]*\[\([^]]*\)\].*/\1/p' <<<"$response" | head -1 | tr -d '" ')"
        reasons="(install jq for per-component reasons)"
        # `warnings` is an array of OBJECTS, so the array-of-strings sed used
        # for `degraded` cannot read it -- and a regex that tried would be a
        # regex nobody could check. Presence is all this path claims: an
        # empty array is silence, a non-empty one is a push whose detail says
        # where to look. Note the three-way test: an ABSENT field is not an
        # empty one, and both are silence, but for different reasons and only
        # one of them means "upgrade the deployment".
        compact="$(tr -d " \t\n" <<<"$response")"
        if [[ "$compact" == *'"warnings":[]'* ]]; then
            warnings=""
        elif [[ "$compact" == *'"warnings":['* ]]; then
            warnings="(install jq for warning detail; open ${STATUS_URL} to read them)"
        else
            warnings=""
        fi
    fi

    # BOTH fields decide, not just `status`.
    #
    # They are derived from the same set of unhealthy components today, so a
    # divergence would be a bug rather than a state -- which is exactly the
    # point. If that derivation ever changes, or a future component reports
    # itself degraded without flipping the summary field, this checker must
    # alert rather than pass on a field that happened to stay "ok". The whole
    # class of incident here is monitoring that vouches against an outage;
    # reading one of two fields is how that happens. The deploy script's
    # verification loop already reads both.
    if [[ "$status" == "ok" && -z "$degraded" ]]; then
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
        if [[ "$status" == "ok" && -n "$degraded" ]]; then
            # Worth its own sentence in the push: the endpoint is contradicting
            # itself, which is a different bug from a component being down.
            detail="/status reported status=ok while listing degraded components (${degraded}). Treat the components as authoritative and fix the endpoint's summary field. ${reasons}"
        fi
    fi
fi

# A verdict exists from here on, so exit 1 now means "unhealthy deployment"
# rather than "died on the way", and the unit may go on treating it as a
# completed run. Everything above this line exits 2.
verdict_reached=1

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

# WARNINGS. Low priority, no effect on the verdict, no effect on the exit
# code. Edge-triggered against their own state file so a store that has been
# warning for a week is one push, not two thousand.
#
# The state is advanced only once the push has gone out, exactly as the
# verdict's is, so a failed publish retries on the next run instead of
# recording "you have been told" when nobody had been.
#
# A warning CLEARING is recorded silently rather than pushed. Recovery from a
# verdict is worth a push because you were left wondering whether it was still
# broken; nothing was broken here, so there is nothing to reassure about, and
# a "good news" push is exactly the kind of traffic that trains you to ignore
# the channel.
previous_warnings="$(cat "$WARNING_STATE_FILE" 2>/dev/null || echo "")"
if [[ "$warnings" != "$previous_warnings" ]]; then
    if [[ -z "$warnings" ]]; then
        printf '%s' "$warnings" > "$WARNING_STATE_FILE"
        echo "$(date -Is) warning cleared"
    elif publish "TCKDB warning" "low" "warning" \
        "${warnings} -- nothing is broken and every upload still works; this is the notice that arrives before one stops working. Source: ${STATUS_URL}"; then
        printf '%s' "$warnings" > "$WARNING_STATE_FILE"
        echo "$(date -Is) warning=${warnings}"
    else
        echo "warning: failed to publish the low-priority warning; state not advanced, will retry" >&2
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
    #
    # THE URL ITSELF IS NEVER LOGGED. A healthchecks.io ping URL is
    # password-equivalent in the same way the ntfy topic is: the secret is the
    # whole path, and anyone holding it can forge this heartbeat and keep the
    # alerts quiet indefinitely -- silencing the one channel that covers the
    # host dying. A failed ping is precisely the moment somebody reads this
    # journal and pastes it into an issue, so the line says only what helps:
    # the host that could not be reached, and curl's exit code, which
    # separates DNS (6) from refused (7) from an HTTP error (22) from a
    # timeout (28). curl's own stderr is dropped for the same reason rather
    # than trusted to be discreet. The CI watchdog has always withheld this;
    # the two scripts used to disagree about a rule one of them already kept.
    curl -sS --fail --max-time 20 -o /dev/null "$DEADMAN_URL" 2>/dev/null
    ping_rc=$?
    if [[ $ping_rc -ne 0 ]]; then
        # scheme, then any user:password@, then the path: what is left is the
        # host (with its port), which is not a secret and is the only part
        # worth naming.
        deadman_host="${DEADMAN_URL#*://}"
        deadman_host="${deadman_host##*@}"
        deadman_host="${deadman_host%%/*}"
        echo "warning: dead man's switch ping to ${deadman_host:-the configured host} failed (curl exit ${ping_rc}); the ping URL is password-equivalent and is not logged" >&2
    fi
fi

echo "$(date -Is) status=${current} (was ${previous})"
[[ "$current" == "ok" ]] && exit 0 || exit 1
