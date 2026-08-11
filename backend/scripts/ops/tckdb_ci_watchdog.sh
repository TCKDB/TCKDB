#!/usr/bin/env bash
# Watch scheduled GitHub Actions workflows and push to ntfy when they rot.
#
# WHY THIS EXISTS
#   backend-nightly.yml is the only job that runs the third of the test suite
#   the two PR gates never touch. On 2026-08-10 it was found to have failed on
#   every run back to 2026-07-31 -- eleven consecutive nights -- and nobody
#   knew, because a red scheduled workflow produces no email, no push and no
#   red mark anywhere a human passes. A gate whose failure nobody sees is not
#   a gate; it is a job that costs 40 minutes of CI a night and buys nothing.
#
# WHAT IT WATCHES FOR, AND WHY BOTH
#   1. RED. The workflow ran and failed.
#   2. SILENCE. The workflow did not run at all. This is the failure mode that
#      matters more, because it is indistinguishable from success from the
#      outside: no run, no failure, no alert, and the last thing you remember
#      is a green tick. GitHub disables scheduled workflows in a repository
#      with 60 days of no commit activity, and it will happily leave them
#      disabled; a fork, a renamed default branch or a deleted secret get you
#      to the same place. So the absence of a run is treated as a fault in its
#      own right, with a per-workflow expected cadence.
#
# WHAT IT DOES NOT COVER
#   Its own silence. This script runs from a scheduled workflow, so it shares
#   a failure domain with the things it watches: the 60-day disable takes the
#   watchdog down with the nightly, and nothing is left to say so. That is the
#   same shape of hole tckdb_alert_check.sh has on the Pi, and it has the same
#   fix -- an external dead man's switch, TCKDB_DEADMAN_URL, pinged once per
#   completed run so that an outside service alerts on the ping STOPPING.
#   The ping means "this watchdog ran", never "CI is well", and it is
#   deliberately not sent when the script aborts before reaching a verdict.
#   See backend/docs/deployment/monitoring.md.
#
# NOISE BEHAVIOUR, DELIBERATE
#   Eleven identical failures must not become eleven identical pushes. Like
#   the Pi-side checker this is EDGE TRIGGERED: a notification goes out when
#   the verdict CHANGES (green -> red, red -> green, running -> silent), plus
#   one reminder per TCKDB_WATCHDOG_REMINDER_RUNS consecutive failures so a
#   long-running breakage does not fade into silence either.
#
#   The state file is advanced ONLY after ntfy has accepted the push. A failed
#   publish that recorded "you have been told" would mean the next run sees no
#   edge and stays quiet -- an alert lost for a week. That exact bug was fixed
#   in the Pi-side checker and is not being reintroduced here.
#
# ENVIRONMENT
#   TCKDB_NTFY_TOPIC             required. ntfy topics are public to anyone who
#                                knows the name, so it is a password: it must
#                                come from a secret and must never be written
#                                into this repository, a workflow file or a log
#   TCKDB_NTFY_SERVER            default https://ntfy.sh
#   TCKDB_WATCHED_WORKFLOWS      space-separated "<workflow-file>:<max-age-hours>"
#                                default "backend-nightly.yml:30 uptime-check.yml:6"
#   TCKDB_WATCH_REPO             default $GITHUB_REPOSITORY, else TCKDB/TCKDB
#   TCKDB_WATCH_BRANCH           default main
#   TCKDB_GH_API_BASE            default https://api.github.com
#   GH_TOKEN / GITHUB_TOKEN      needs actions:read; without it the API is
#                                rate-limited to 60 requests/hour per IP
#   TCKDB_WATCHDOG_STATE_DIR     default ~/.cache/tckdb-ci-watchdog
#   TCKDB_WATCHDOG_REMINDER_RUNS default 7 (remind every 7th consecutive failure)
#   TCKDB_WATCHDOG_REMINDER_HOURS default 168 (remind weekly while silent)
#   TCKDB_DEADMAN_URL            optional, strongly recommended -- external URL
#                                pinged after every completed run
#
# A RUN THAT CHECKED NOTHING IS NOT A PASS
#   The count of verdicts reached is reported unconditionally and before the
#   result, because it is the number that says whether the run is evidence of
#   anything at all. It exists because it was once possible for every entry in
#   TCKDB_WATCHED_WORKFLOWS to be rejected as malformed -- each one warned
#   about on stderr, none of them watched -- and for the script to then exit 0
#   and send the dead man's ping, so the external monitor vouched for a checker
#   that had checked nothing. Zero verdicts is now the loudest state this
#   script has: no ping, and exit 2.
#
#   There is deliberately no --allow-empty equivalent, unlike
#   verify_artifact_integrity.py, which has one because a fresh deployment can
#   legitimately hold nothing to verify. Watching nothing is never legitimate
#   here: an unset or empty TCKDB_WATCHED_WORKFLOWS falls back to the built-in
#   list, so the only way to reach zero is to have written something the script
#   could not read, and an opt-out for that is an opt-out from the whole point.
#
# EXIT CODES
#   0  every watched workflow is green and running
#   1  something is red, silent, or a push could not be delivered
#   2  something in scope was NOT CHECKED: no topic, no jq, a malformed entry
#      in TCKDB_WATCHED_WORKFLOWS, an unreadable API, an unparseable run list
#      -- or no verdict at all.
#
#   The split is the one backend/scripts/ops/verify_artifact_integrity.py uses,
#   on purpose, so that two scripts in one directory do not need two schemes:
#   1 means something is wrong, 2 means we did not find out, and a runbook that
#   gates on "not zero" catches both. 1 wins when both are true, because a red
#   nightly is the more actionable of the two. Nothing is pinged when no
#   verdict was reached at all: a dead watchdog's only honest signal is its
#   silence.
set -uo pipefail

EXIT_OK=0
EXIT_ALERT=1
EXIT_NOT_CHECKED=2

GH_API_BASE="${TCKDB_GH_API_BASE:-https://api.github.com}"
REPO="${TCKDB_WATCH_REPO:-${GITHUB_REPOSITORY:-TCKDB/TCKDB}}"
BRANCH="${TCKDB_WATCH_BRANCH:-main}"
WATCHED="${TCKDB_WATCHED_WORKFLOWS:-backend-nightly.yml:30 uptime-check.yml:6}"
NTFY_SERVER="${TCKDB_NTFY_SERVER:-https://ntfy.sh}"
STATE_DIR="${TCKDB_WATCHDOG_STATE_DIR:-$HOME/.cache/tckdb-ci-watchdog}"
REMINDER_RUNS="${TCKDB_WATCHDOG_REMINDER_RUNS:-7}"
REMINDER_HOURS="${TCKDB_WATCHDOG_REMINDER_HOURS:-168}"
DEADMAN_URL="${TCKDB_DEADMAN_URL:-}"
TOKEN="${GH_TOKEN:-${GITHUB_TOKEN:-}}"

if [[ -z "${TCKDB_NTFY_TOPIC:-}" ]]; then
    echo "TCKDB_NTFY_TOPIC is not set; refusing to run." >&2
    exit "$EXIT_NOT_CHECKED"
fi

# No grep/sed fallback here, unlike the Pi-side checker. That script parses one
# flat object and a wrong parse is recoverable; this one walks an array of runs
# and decides an edge from it, where a silently wrong parse means "green" -- the
# answer that produces no alert. Refusing to start is the safe direction,
# because a watchdog that exits 2 is a red job, while a watchdog that guesses
# wrong is a green one.
if ! command -v jq >/dev/null 2>&1; then
    echo "jq is required by the CI watchdog; refusing to guess at the run list." >&2
    exit "$EXIT_NOT_CHECKED"
fi

mkdir -p "$STATE_DIR"

CURL_HEADERS=(-H "Accept: application/vnd.github+json" -H "X-GitHub-Api-Version: 2022-11-28")
if [[ -n "$TOKEN" ]]; then
    CURL_HEADERS+=(-H "Authorization: Bearer ${TOKEN}")
fi

# --fail because for the API an HTTP error IS the answer: a 404 (workflow file
# renamed or deleted) and a 401 (token without actions:read) both return a body
# that jq parses to zero runs, which this script would otherwise report as
# "the workflow has never run" -- true-sounding, wrong, and pointing at the
# wrong fix.
api_get() {
    curl -sS --fail --max-time 30 "${CURL_HEADERS[@]}" "${GH_API_BASE}$1"
}

# publish <title> <priority> <tags> <body>; returns curl's exit status so the
# caller can decide whether the notification actually happened.
#
# --fail because a 401 from a protected topic or a 5xx from ntfy means the push
# reached nobody; without it curl reports success and the caller records an
# alert that was never delivered. --retry so one transient 5xx does not cost a
# whole reminder period of silence.
publish() {
    curl -sS --fail --retry 2 --retry-delay 2 --max-time 30 \
        -H "Title: $1" \
        -H "Priority: $2" \
        -H "Tags: $3" \
        -d "$4" \
        "${NTFY_SERVER}/${TCKDB_NTFY_TOPIC}" >/dev/null
}

summary_line() {
    echo "$1"
    if [[ -n "${GITHUB_STEP_SUMMARY:-}" ]]; then
        echo "$1" >> "$GITHUB_STEP_SUMMARY"
    fi
}

annotate() {
    # GitHub log annotations put the verdict on the run's summary page as well
    # as in the log body. Guarded so local runs and the test suite see plain
    # text rather than workflow-command noise.
    if [[ -n "${GITHUB_ACTIONS:-}" ]]; then
        echo "::${1}::${2}"
    fi
}

epoch_of() {
    # ISO-8601 UTC timestamp -> epoch seconds; empty string on anything unparseable.
    [[ -z "$1" ]] && return 0
    date -u -d "$1" +%s 2>/dev/null || true
}

now_epoch="$(date -u +%s)"
alert_rc=0
watchdog_usable=1
notified_any=0

# The three numbers the result is computed from, rather than a single rc that
# cannot tell "nothing was wrong" from "nothing was looked at".
#   watched_count   entries in TCKDB_WATCHED_WORKFLOWS
#   verdicts_count  entries that produced an actual verdict this run
#   unchecked_count entries that did not, for any reason
# shellcheck disable=SC2086
set -- $WATCHED
watched_count=$#
verdicts_count=0
unchecked_count=0

for spec in "$@"; do
    workflow="${spec%%:*}"
    max_age_hours="${spec##*:}"
    if [[ "$workflow" == "$spec" || -z "$max_age_hours" ]]; then
        # An entry that cannot be parsed is a workflow the operator asked for
        # and did not get. Warning about it and carrying on was how every
        # entry could be dropped and the run still called clean, so it now
        # counts as unchecked and takes the heartbeat with it -- the config is
        # wrong until someone fixes it, and nothing here can fix it.
        echo "warning: ignoring malformed TCKDB_WATCHED_WORKFLOWS entry '${spec}' (want file.yml:hours)" >&2
        annotate error "CI watchdog cannot parse the watch-list entry '${spec}'; that workflow is NOT being watched"
        unchecked_count=$((unchecked_count + 1))
        watchdog_usable=0
        continue
    fi

    # Cleared every iteration: `eval` of a jq that produced nothing would
    # otherwise leave the PREVIOUS workflow's run url and sha in scope and
    # report them against this one -- a push pointing at the wrong run is
    # worse than no push.
    unset wf_display_name verdict_list latest_url latest_sha latest_conclusion latest_id latest_started

    runs_json="$(api_get "/repos/${REPO}/actions/workflows/${workflow}/runs?branch=${BRANCH}&per_page=30&exclude_pull_requests=true")"
    api_rc=$?
    if [[ $api_rc -ne 0 || -z "$runs_json" ]]; then
        # Cannot see the run history, so cannot form a verdict. Loud, and no
        # dead man's ping: an unreachable API is the watchdog failing, not the
        # nightly, and forging a heartbeat here would hide it.
        echo "error: could not read run history for ${workflow} from ${REPO}" >&2
        annotate error "CI watchdog could not read run history for ${workflow}"
        watchdog_usable=0
        unchecked_count=$((unchecked_count + 1))
        continue
    fi

    # Freshness is computed over ALL runs including in-progress ones: a nightly
    # that is still running is emphatically not silent, and treating it as such
    # would page every time the watchdog overlapped a slow run.
    latest_any_started="$(jq -r '[.workflow_runs[] | (.run_started_at // .created_at)] | max // ""' <<<"$runs_json")"

    # Verdicts are computed over completed runs with a decisive conclusion.
    # `cancelled`, `skipped` and `action_required` are evidence of neither
    # health nor breakage and must not break a streak in either direction.
    parsed="$(jq -r '
        def decisive:
            (.conclusion // "") as $c
            | (["success", "failure", "timed_out", "startup_failure"] | index($c)) != null;
        [ .workflow_runs[]
          | select(.status == "completed")
          | select(decisive)
        ]
        | sort_by(.run_started_at // .created_at)
        | reverse
        | . as $runs
        | ($runs | map(if .conclusion == "success" then "green" else "red" end)) as $verdicts
        | "wf_display_name=\(($runs[0].name // "") | @sh)",
          "verdict_list=\(($verdicts | join(" ")) | @sh)",
          "latest_url=\(($runs[0].html_url // "") | @sh)",
          "latest_sha=\(($runs[0].head_sha // "") | @sh)",
          "latest_conclusion=\(($runs[0].conclusion // "") | @sh)",
          "latest_id=\(($runs[0].id // "" | tostring) | @sh)",
          "latest_started=\(($runs[0].run_started_at // $runs[0].created_at // "") | @sh)"
    ' <<<"$runs_json")"
    jq_rc=$?

    # A jq that errored leaves `parsed` empty, and an empty parse looks exactly
    # like "no failures" -- the answer that produces no alert. A watchdog is
    # only worth having if its own broken states are loud, so this is treated
    # as no verdict at all rather than as good news.
    if [[ $jq_rc -ne 0 ]]; then
        echo "error: could not parse the run history for ${workflow}; refusing to read it as green" >&2
        annotate error "CI watchdog could not parse the run history for ${workflow}"
        watchdog_usable=0
        unchecked_count=$((unchecked_count + 1))
        continue
    fi
    eval "$parsed"

    wf_display_name="${wf_display_name:-}"
    verdict_list="${verdict_list:-}"
    latest_url="${latest_url:-}"
    latest_sha="${latest_sha:-}"
    latest_conclusion="${latest_conclusion:-}"
    latest_id="${latest_id:-}"
    display_name="${wf_display_name:-$workflow}"
    [[ -z "$display_name" ]] && display_name="$workflow"

    # Consecutive leading failures -- the number that turns "it broke" into
    # "it has been broken for eleven nights", which is a different sentence.
    streak=0
    for v in ${verdict_list:-}; do
        [[ "$v" == "red" ]] || break
        streak=$((streak + 1))
    done

    age_hours=""
    latest_epoch="$(epoch_of "$latest_any_started")"
    if [[ -n "$latest_epoch" ]]; then
        age_hours=$(( (now_epoch - latest_epoch) / 3600 ))
    elif [[ -n "$latest_any_started" ]]; then
        # A timestamp that will not parse means the freshness check silently
        # does not happen, and a freshness check that does not happen reads as
        # "not silent". Say so rather than skip quietly.
        echo "warning: could not parse the last run time for ${workflow}; freshness was not checked" >&2
        annotate warning "CI watchdog could not check whether ${workflow} is still running"
    fi

    # ---------------------------------------------------------------
    # Verdict
    # ---------------------------------------------------------------
    if [[ -z "${verdict_list:-}" && -z "$latest_any_started" ]]; then
        state_key="never"
        title="CI watchdog: ${workflow} has never run"
        priority="high"
        tags="warning"
        detail="No runs of ${workflow} exist on ${BRANCH} in ${REPO}. Either the workflow file has just landed and its schedule has not fired yet, or the schedule is wrong and never will."
    elif [[ -n "$age_hours" && "$age_hours" -gt "$max_age_hours" ]]; then
        # Silence. Bucketed so a workflow that stays disabled reminds weekly
        # rather than daily, without needing a second state field.
        overdue_hours=$((age_hours - max_age_hours))
        bucket=$((overdue_hours / REMINDER_HOURS))
        state_key="silent:${bucket}"
        title="CI watchdog: ${workflow} has not run for ${age_hours}h"
        priority="high"
        tags="warning"
        detail="${display_name} last started ${age_hours}h ago; it is expected at least every ${max_age_hours}h. A scheduled workflow that stops running looks exactly like one that keeps passing. GitHub disables scheduled workflows after 60 days without repository activity -- re-enable it at https://github.com/${REPO}/actions/workflows/${workflow} -- and a deleted secret, a renamed default branch or an edited cron get you here too."
    elif [[ "$streak" -gt 0 ]]; then
        bucket=$(( (streak - 1) / REMINDER_RUNS ))
        state_key="red:${bucket}"
        nights="run"
        [[ "$streak" -gt 1 ]] && nights="consecutive runs"
        title="CI watchdog: ${workflow} failed (${streak} ${nights})"
        priority="high"
        tags="warning"
        detail="$(printf 'Workflow: %s (%s)\nBranch: %s\nVerdict: %s, %s failing %s in a row\nCommit: %s\nRun: %s' \
            "$workflow" "$display_name" "$BRANCH" "${latest_conclusion:-failure}" "$streak" \
            "$([[ "$streak" -gt 1 ]] && echo "runs" || echo "run")" \
            "${latest_sha:0:12}" "${latest_url:-https://github.com/${REPO}/actions/workflows/${workflow}}")"

        # Best-effort enrichment: naming the failed step is the difference
        # between a push you act on and a push you swipe away. A failure here
        # must never suppress the alert, so its exit status is ignored.
        if [[ -n "${latest_id:-}" ]]; then
            jobs_json="$(api_get "/repos/${REPO}/actions/runs/${latest_id}/jobs?per_page=50" 2>/dev/null)"
            if [[ -n "$jobs_json" ]]; then
                failed_steps="$(jq -r '
                    [ .jobs[]?
                      | select(.conclusion == "failure" or .conclusion == "timed_out")
                      | . as $job
                      | ( [ $job.steps[]? | select(.conclusion == "failure" or .conclusion == "timed_out") | .name ]
                          | if length == 0 then [$job.name]
                            else map(if . == $job.name then . else "\($job.name) / \(.)" end)
                            end )
                    ]
                    | flatten | unique | join("; ")
                ' <<<"$jobs_json" 2>/dev/null)"
                [[ -n "$failed_steps" ]] && detail="${detail}"$'\n'"Failed: ${failed_steps}"
            fi
        fi
    else
        state_key="green"
        title="CI watchdog: ${workflow} is green again"
        priority="default"
        tags="white_check_mark"
        detail="$(printf '%s passed on %s.\nCommit: %s\nRun: %s' \
            "$display_name" "$BRANCH" "${latest_sha:0:12}" \
            "${latest_url:-https://github.com/${REPO}/actions/workflows/${workflow}}")"
    fi

    # A verdict, whatever it says. Counted here rather than at the top of the
    # loop because reaching this line is the only thing that makes the run
    # evidence of anything: every `continue` above got here without looking at
    # a workflow, and a run of nothing but those must not be a pass.
    verdicts_count=$((verdicts_count + 1))

    # ---------------------------------------------------------------
    # Edge trigger
    # ---------------------------------------------------------------
    state_file="${STATE_DIR}/$(printf '%s' "$workflow" | tr -c 'A-Za-z0-9._-' '_')"
    previous="$(cat "$state_file" 2>/dev/null || echo "unknown")"

    summary_line "${workflow}: ${state_key} (was ${previous})"
    [[ "$state_key" == "green" ]] || alert_rc=1
    if [[ "$state_key" != "green" ]]; then
        annotate error "${title}"
    fi

    if [[ "$state_key" == "$previous" ]]; then
        continue
    fi

    if [[ "$state_key" == "green" && "$previous" == "unknown" ]]; then
        # First ever look and it is fine: record the state so a later break is
        # still an edge, but do not announce a non-event.
        printf '%s' "$state_key" > "$state_file"
        continue
    fi

    footer="$(printf 'Reported by the CI watchdog (.github/workflows/ci-watchdog.yml), which notifies on a change of verdict and once per %s further failures. All workflows: https://github.com/%s/actions' "$REMINDER_RUNS" "$REPO")"
    if publish "$title" "$priority" "$tags" "${detail}"$'\n\n'"${footer}"; then
        printf '%s' "$state_key" > "$state_file"
        notified_any=1
    else
        # Not advancing the state file is the whole point: the next run sees
        # the same edge and tries again.
        echo "warning: failed to publish ${workflow} alert to ntfy; state not advanced, will retry" >&2
        annotate error "CI watchdog could not deliver the ${workflow} alert to ntfy"
        alert_rc=1
    fi
done

# THE COUNT, FIRST AND UNCONDITIONALLY, before any result line and before the
# heartbeat. Same convention as verify_artifact_integrity.py's `verified=...`:
# the reader should not have to infer from an absence of complaints that
# anything was looked at.
summary_line "watched=${watched_count} verdicts=${verdicts_count} unchecked=${unchecked_count}"

if [[ "$verdicts_count" -eq 0 ]]; then
    # Not a pass, and not a red workflow either: no workflow was looked at.
    # Setting watchdog_usable here is what suppresses the dead man's ping --
    # the existing, documented signal for "this run reached no verdict" --
    # so an external monitor sees the silence instead of a forged heartbeat.
    watchdog_usable=0
    echo "error: NOT CHECKED. No watched workflow produced a verdict, so this run says nothing about CI. The watch list was '${WATCHED}' (entries must read file.yml:hours); the errors above say what happened to each one." >&2
    annotate error "CI watchdog reached no verdict at all: it watched nothing this run"
fi

# 1 before 2, as in verify_artifact_integrity.py: a red workflow is the more
# actionable finding, and "we did not manage to look at the others" is still
# true of the same run.
if [[ "$alert_rc" -ne 0 ]]; then
    overall_rc=$EXIT_ALERT
elif [[ "$verdicts_count" -eq 0 || "$unchecked_count" -gt 0 ]]; then
    overall_rc=$EXIT_NOT_CHECKED
else
    overall_rc=$EXIT_OK
fi

# THE DEAD MAN'S PING. Sent for every completed run whatever the verdict: it
# asserts that THIS WATCHDOG RAN, which is a different claim from "CI is well".
# Tying it to a green verdict would mean a red nightly also silences the
# heartbeat, and the external service would page "the watchdog is gone" while
# the watchdog was busy saying exactly which workflow had failed.
#
# It is skipped whenever any watched entry failed to reach a verdict -- an
# unreachable API, an unparseable run list, an entry this script could not
# read -- and in particular when NONE of them did, because that is a watchdog
# without a verdict, and its silence is the only honest signal it has left.
# The ping asserts that a check happened; a run that checked nothing may not
# make that claim.
if [[ -n "$DEADMAN_URL" ]]; then
    if [[ "$watchdog_usable" -eq 1 ]]; then
        curl -sS --fail --max-time 20 -o /dev/null "$DEADMAN_URL" || \
            echo "warning: dead man's switch ping to the watchdog deadman URL failed" >&2
    else
        echo "warning: no verdict reached, so the dead man's switch was not pinged" >&2
    fi
else
    # Said every run rather than once, because unlike the Pi-side checker this
    # one has nowhere durable to remember that it has already said it, and a
    # line in a log is cheap. It is not a push: an unmonitored watchdog is a
    # real gap but not a 3am one.
    echo "warning: TCKDB_DEADMAN_URL is unset, so nothing will notice if this watchdog itself stops running" >&2
    annotate warning "TCKDB_DEADMAN_URL is unset: nothing will notice if this watchdog stops running. See backend/docs/deployment/monitoring.md"
fi

echo "$(date -Is) watchdog verdicts=${verdicts_count}/${watched_count} rc=${overall_rc} notified=${notified_any}"
exit "$overall_rc"
