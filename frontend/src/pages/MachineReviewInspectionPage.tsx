import { useState } from "react"
import { Link } from "react-router-dom"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import { fetchMachineReviewInspection } from "../api/machineReviewInspection"
import { AuthApiError } from "../api/authApi"
import {
    buildCuratorTasksForSubmission,
    CuratorTaskResponseError,
} from "../api/curatorTasksApi"
import {
    MachineReviewRunResponseError,
    runMachineReviewForSubmission,
} from "../api/machineReviewRunApi"
import {
    findingsConsidered,
    type CuratorTaskBuildResult,
} from "../types/curatorTask"
import {
    overallHighestSeverity,
    type MachineReviewStatus,
} from "../types/machineReviewInspection"
import {
    knownRunStatus,
    reviewDidNotStart,
    reviewFailed,
    type MachineReviewRunResult,
} from "../types/machineReviewRun"

/**
 * Admin-only Submission Machine-Review Inspection panel.
 *
 * Renders the response of
 * ``GET /api/v1/admin/submissions/{submission_id}/machine-review-inspection``.
 * This is a PRIVATE diagnostic view, not public trust / human review /
 * certification / moderation. Access is enforced by the backend
 * (``require_admin``); this page is expected to be mounted behind the admin
 * shell. See ``backend/docs/specs/machine_review_admin_ui_mock.md``.
 *
 * Future joins (submission status, uploader, human-review status, evidence
 * label/completeness, curator workflow_state) are intentionally NOT shown:
 * the endpoint does not return them and this slice adds no backend joins.
 */

// Status badge text + disclaimer. The disclaimers are deliberately surfaced in
// the UI (legend below the table), never docs-only, so an admin can never read
// a machine status as a human-review verdict.
const STATUS_META: Record<
    MachineReviewStatus,
    { label: string; color: string; disclaimer: string }
> = {
    not_run: {
        label: "not run",
        color: "#6b7280",
        disclaimer: "No mapped machine review for this record.",
    },
    machine_screened_pass: {
        label: "screened: pass",
        color: "#15803d",
        disclaimer: "Machine found no obvious issue; not human-approved.",
    },
    machine_screened_warning: {
        label: "screened: warning",
        color: "#b45309",
        disclaimer: "Advisory warning; not rejected.",
    },
    machine_screened_needs_attention: {
        label: "screened: needs attention",
        color: "#b91c1c",
        disclaimer: "Needs human attention; record is not hidden.",
    },
    machine_review_failed: {
        label: "review failed",
        color: "#6b7280",
        disclaimer: "Reviewer failed; not a record failure.",
    },
}

const OBSERVATION_PROMPTS = [
    "Was the finding useful?",
    "Was the status misleading?",
    "Was this a false positive?",
    "Was mapping correct?",
    "Should this be curator-facing?",
] as const

function StatusBadge({ status }: { status: MachineReviewStatus }) {
    const meta = STATUS_META[status]
    return (
        <span
            title={meta.disclaimer}
            style={{
                background: meta.color,
                color: "white",
                borderRadius: "4px",
                padding: "1px 6px",
                fontSize: "0.8rem",
                whiteSpace: "nowrap",
            }}
        >
            {meta.label}
        </span>
    )
}

function dash(value: unknown): string {
    return value === null || value === undefined || value === "" ? "—" : String(value)
}

/**
 * Accessible names for this page's four live regions.
 *
 * Named constants rather than four literals, because the run pair and the
 * build pair are otherwise distinguished only by wording a later edit could
 * quietly make identical, and two identically named status regions are the
 * same defect as two unnamed ones.
 */
const RUN_ALERT_LABEL = "machine review request problem"
const RUN_STATUS_LABEL = "machine review run outcome"
const BUILD_ALERT_LABEL = "curator task build problem"
const BUILD_STATUS_LABEL = "curator task build result"

/**
 * The react-query key the inspection below is cached under.
 *
 * Shared by the reader and by the run control that invalidates it after a
 * run. Written out twice, the two drift the day the key gains a member, and
 * the failure is silent: the invalidate matches nothing, the table keeps
 * showing the state from before the run, and the page looks like a run that
 * recorded nothing.
 */
function inspectionQueryKey(submissionId: number | null) {
    return ["machine-review-inspection", submissionId] as const
}

/**
 * What the run is doing right now.
 *
 * Four states rather than a pair of booleans, for the reason spelled out on
 * `BuildState` below. `done` here means the REQUEST came back with a result;
 * whether that result is a review that succeeded or one that failed is a
 * property of the result, not of this state.
 */
type RunState =
    | { kind: "idle" }
    | { kind: "running" }
    | { kind: "done"; result: MachineReviewRunResult }
    | { kind: "failed"; message: string }

/**
 * What to tell an admin when the RUN REQUEST did not come back with a result.
 *
 * Three failures with three different answers, mirroring `failureMessage`
 * below, and the distinction matters more here because a machine review
 * costs a provider call: an admin told "it did not run" presses the button
 * again, and if it did run they have now paid for two.
 *
 * Note what is NOT in here. A reply of `status: "machine_review_failed"` is
 * a 200 and never reaches this function: the review RAN, and its failure is
 * an outcome the archive recorded. Routing it here would tell an admin their
 * request broke when what happened is that a reviewer answered.
 */
function runFailureMessage(error: unknown): string {
    if (error instanceof MachineReviewRunResponseError) {
        // 2xx. It ran; only the result was unreadable. The error already
        // carries the whole sentence, including where to go and look.
        return error.message
    }
    if (error instanceof AuthApiError) {
        // The server answered with a refusal, so no review was started.
        return `The review did not run: ${error.message}`
    }
    // No answer: offline, DNS, a dropped connection, a request that never
    // left. The server may or may not have run a review and written it.
    const detail = error instanceof Error ? error.message : String(error)
    return (
        `The run did not report back: ${detail}. A review may or may not have ` +
        "happened; inspect this submission again before pressing it again."
    )
}

/**
 * The one control that produces machine-review findings at all.
 *
 * Nothing else makes them. The machine-review stack "is not wired into
 * uploads or any public read", so before this button a deployment could hold
 * any number of submissions and zero machine reviews, the table above had
 * nothing to project, and the curator-task builder below it had nothing to
 * build from. It sits above that builder because that is the order of the
 * work: produce the findings, read them, then decide whether they deserve a
 * curator.
 *
 * **A result must never outlive its submission**, for exactly the reason
 * given at `CuratorTaskBuilder`, and through exactly the same mechanism:
 * `key={submissionId}` at the mount site. Walking to a submission this
 * session has not fetched, `query.data` goes undefined while it loads and
 * the parent unmounts the whole results block, so this component's state
 * dies with or without a key. Walking BACK to one already visited,
 * react-query answers from cache (`gcTime`, five minutes by default), data
 * never goes undefined, nothing unmounts, and the key is the only thing
 * left that drops the stale result. MEASURED both ways, by the tests that
 * walk 7 -> 9 and 7 -> 9 -> 7.
 */
function MachineReviewRunner({ submissionId }: { submissionId: number }) {
    const [state, setState] = useState<RunState>({ kind: "idle" })
    const queryClient = useQueryClient()

    async function run() {
        setState({ kind: "running" })
        let result: MachineReviewRunResult
        try {
            result = await runMachineReviewForSubmission(submissionId)
        } catch (error) {
            setState({ kind: "failed", message: runFailureMessage(error) })
            return
        }
        setState({ kind: "done", result })

        // The findings table above is a cached query, so without this the
        // admin reads the run's own summary over a table still showing the
        // archive as it was before the run -- most starkly "no records
        // received mapped machine-review findings" directly beneath a line
        // saying the run recorded nine. Invalidated even when the review
        // failed: the request reached the server, and a failed review still
        // records an audit event the projection reads.
        //
        // Outside the try on purpose. In it, a throw from the cache layer
        // would be caught by the branch above and reported as a request
        // failure, turning a run that succeeded into a red alert.
        void queryClient.invalidateQueries({
            queryKey: inspectionQueryKey(submissionId),
        })
    }

    return (
        <section>
            <h3>Machine review</h3>
            <p style={{ color: "#6b7280", marginTop: "-6px" }}>
                A machine review is <strong>advisory</strong>. Nothing starts one
                on upload; this control is the only thing that does. It endorses
                nothing, and it is neither of the two things next to it: human
                review of a record, and moderation of the submission. Neither
                moves when this runs. Running it again reviews this submission
                again and records another result.
            </p>
            <button type="button" onClick={run} disabled={state.kind === "running"}>
                {state.kind === "running"
                    ? "Running machine review…"
                    : "Run machine review for this submission"}
            </button>

            {/* Two permanently mounted, initially empty live regions, for the
                reason given at the build control below. The split is also the
                page's honest line between the two kinds of bad news: a request
                that failed goes in the alert, and a REVIEW that failed is an
                outcome and goes in the result region with every other
                outcome. */}
            <div
                role="alert"
                aria-label={RUN_ALERT_LABEL}
                style={{ color: "#b91c1c" }}
            >
                {state.kind === "failed" ? <p>{state.message}</p> : null}
            </div>
            <div role="status" aria-label={RUN_STATUS_LABEL}>
                {state.kind === "done" ? <RunOutcome result={state.result} /> : null}
            </div>
        </section>
    )
}

/**
 * What one run recorded, in wording an admin can act on.
 *
 * The thing this must not do is read as a request error when the reviewer
 * failed. `machine_review_failed` arrives on a 200: a review ran, the
 * reviewer did not manage to produce findings, and the archive wrote that
 * down. "Failed to run the review" would send an admin looking at the
 * network and the deployment for a fault that is in the reviewer, and the
 * `failure_reason` the server sent is the thing that actually says where.
 */
function RunOutcome({ result }: { result: MachineReviewRunResult }) {
    const failed = reviewFailed(result)
    const known = knownRunStatus(result.status)
    const count = result.findings_count

    const headline = failed
        ? // NOT "the review ran, and the reviewer failed". Several of the
          // failures that land here never reached a reviewer at all -- a
          // missing API key or an unreachable endpoint fails in this app, and
          // telling an admin the reviewer failed would point them at the
          // model when the fault is local configuration. What IS true of
          // every failure on this path is that no usable result came back.
          "The review did not produce a usable result. That is not a finding " +
          "about this submission: no record was judged and nothing about the " +
          "submission changed."
        : reviewDidNotStart(result)
          ? // Zero findings from a reviewer that looked and zero from one
            // that is switched off are the same number and opposite news.
            "No review happened. The machine reviewer is switched off in this " +
            "deployment, so no provider was asked anything and nothing was " +
            "recorded."
          : `The review ran and recorded ${count} finding${count === 1 ? "" : "s"}.`

    return (
        <div
            // Framed, so the outcome reads as the answer to the button above
            // it rather than as one more paragraph of this page's furniture.
            style={{
                borderLeft: "3px solid #6b7280",
                paddingLeft: "12px",
                marginTop: "12px",
            }}
        >
            <p>{headline}</p>
            <ul>
                <li>
                    status:{" "}
                    {known !== null ? (
                        <StatusBadge status={known} />
                    ) : (
                        // Shown as it arrived rather than mapped onto one of
                        // the five this build knows, which would hand it
                        // another status's disclaimer.
                        <code>{result.status}</code>
                    )}
                </li>
                <li>findings recorded: {count}</li>
                <li>model: {dash(result.model)}</li>
                <li>provider: {dash(result.provider)}</li>
                <li>
                    {result.audit_event_recorded
                        ? "written to the audit log, which is what the table above reads"
                        : "not written to the audit log, so the table above will not show it"}
                </li>
            </ul>
            {failed && (
                <p>
                    {/* "Why the run failed", not "reason the reviewer gave".
                        This text can come from the reviewer, but it can just
                        as easily be this app's own ("Machine review could not
                        be configured: ...") or a transport library's. Putting
                        a configuration error in the reviewer's mouth blurs
                        the three axes this page is careful about everywhere
                        else. */}
                    Why the run failed:{" "}
                    {result.failure_reason !== null ? (
                        result.failure_reason
                    ) : (
                        <em>no reason was recorded.</em>
                    )}
                </p>
            )}
            {result.summary !== null && result.summary !== result.failure_reason && (
                <>
                    <h4>reviewer&apos;s summary</h4>
                    {/* The reviewer's own words, advisory like everything else
                        it produced. Quoted plainly so it cannot be mistaken
                        for a statement this page is making. */}
                    <blockquote style={{ margin: 0, color: "#374151" }}>
                        {result.summary}
                    </blockquote>
                </>
            )}
        </div>
    )
}

/**
 * What the build is doing right now.
 *
 * Four states rather than a pair of booleans, because the pair admits
 * combinations that mean nothing -- "running and failed", "idle with a
 * result" -- and every one of those is a way to show an admin a tally
 * belonging to a run that is not the one they are watching.
 */
type BuildState =
    | { kind: "idle" }
    | { kind: "running" }
    | { kind: "done"; result: CuratorTaskBuildResult }
    | { kind: "failed"; message: string }

/**
 * What to tell an admin when the build did not come back with a tally.
 *
 * Three failures, and they do NOT share an answer. Prefixing all of them
 * with "no tasks were built" was wrong in two cases out of three -- worst
 * on the middle one, where the page would assert a thing and its opposite
 * inside one sentence:
 *
 *   "No tasks were built: The build ran, but this page could not read
 *    the tally. Any tasks it made are under Machine findings."
 *
 * Only a refusal that reached the server and came back as an error status
 * supports "nothing was built". A 2xx whose body would not parse means
 * the build DID run. And a request that never got an answer at all means
 * this page does not know, which is the honest thing to say rather than
 * guessing in either direction.
 */
function failureMessage(error: unknown): string {
    if (error instanceof CuratorTaskResponseError) {
        // 2xx. It ran; only the tally was unreadable. The error already
        // carries the whole sentence, including where to go and look.
        return error.message
    }
    if (error instanceof AuthApiError) {
        // The server answered with a refusal, so it wrote nothing.
        return `No tasks were built: ${error.message}`
    }
    // No answer: offline, DNS, a dropped connection, a request that never
    // left. The server may or may not have committed.
    const detail = error instanceof Error ? error.message : String(error)
    return (
        `The build did not report back: ${detail}. It may or may not have ` +
        "run; check Machine findings before pressing it again."
    )
}

/**
 * The one control that puts work into Machine findings.
 *
 * Curator tasks are built by nothing else. The backend route says so in as
 * many words ("Explicit/admin-triggered only -- never runs on upload"), so
 * until this button existed the queue stayed empty however many
 * submissions arrived, and the only way to fill it was a hand-written curl
 * (task #256).
 *
 * It lives here because this page is already showing the exact findings
 * the build reads: an admin who has just looked at a submission's
 * machine-review projection is the person who can judge whether those
 * findings deserve a curator's time.
 *
 * **A tally must never outlive its submission.** Submission 9's findings
 * under submission 7's tally is a false report, and the more convincing
 * for being half true. `key={submissionId}` at the mount site is what
 * prevents it, and it is load-bearing TODAY -- not insurance, as an
 * earlier version of this comment claimed.
 *
 * That earlier claim came from measuring in one direction only. Walking
 * to a submission this session has not seen, `query.data` goes undefined
 * while it loads, the parent unmounts the whole results block, and this
 * component's state goes with it -- so removing the key changes nothing
 * and the test still passed. But react-query caches (`gcTime`, five
 * minutes by default): walk BACK to a submission already visited and its
 * data arrives synchronously, `query.data` never goes undefined, nothing
 * unmounts, and without the key the old tally sits under the new
 * submission's findings. MEASURED, both ways, by the two tests that walk
 * 7 -> 9 and 7 -> 9 -> 7.
 */
function CuratorTaskBuilder({ submissionId }: { submissionId: number }) {
    const [state, setState] = useState<BuildState>({ kind: "idle" })

    async function build() {
        setState({ kind: "running" })
        try {
            const result = await buildCuratorTasksForSubmission(submissionId)
            setState({ kind: "done", result })
        } catch (error) {
            setState({ kind: "failed", message: failureMessage(error) })
        }
    }

    return (
        <section>
            <h3>Curator tasks</h3>
            <p style={{ color: "#6b7280", marginTop: "-6px" }}>
                Tasks are <strong>not</strong> created on upload. This builds them
                from the warning and critical findings above, for this submission
                only, and writes nothing but task rows: the submission&apos;s status
                and every record&apos;s review state are untouched. A task is
                advisory. It asks a person to look, and endorses nothing.
            </p>
            <button type="button" onClick={build} disabled={state.kind === "running"}>
                {state.kind === "running"
                    ? "Building\u2026"
                    : "Build curator tasks for this submission"}
            </button>

            {/* Both live regions are mounted permanently and empty. A
                `role="status"` or `role="alert"` container that is inserted
                into the page ALREADY populated is frequently not announced
                at all -- a screen reader watches regions that exist for
                changes inside them. An unannounced status region is a
                guard that does nothing, which is the shape of defect this
                repo files tasks about. Empty divs take no vertical space,
                so this costs the sighted layout nothing.

                Each region carries an `aria-label` because this page now
                mounts two pairs of them, one for the run above and one for
                the build here. Two unnamed status regions announce as the
                same anonymous thing, so a screen-reader user hearing a
                tally has no way to tell which button it answered. */}
            <div
                role="alert"
                aria-label={BUILD_ALERT_LABEL}
                style={{ color: "#b91c1c" }}
            >
                {state.kind === "failed" ? <p>{state.message}</p> : null}
            </div>
            <div role="status" aria-label={BUILD_STATUS_LABEL}>
                {state.kind === "done" ? <BuildTally result={state.result} /> : null}
            </div>
        </section>
    )
}

/**
 * The tally of one build, in wording an admin can act on.
 *
 * Two things this must not do. It must not add the counts up:
 * `refreshed_count` is a sub-count of `reused_count`, so a total would
 * exceed the findings considered. And it must not print a task id -- the
 * server sends `task_ids`, and `CuratorTaskBuildResultSchema` drops it
 * before it can reach here (DR-0028 Req 2).
 */
function BuildTally({ result }: { result: CuratorTaskBuildResult }) {
    const made = result.created_count
    const considered = findingsConsidered(result)
    const headline =
        made > 0
            ? `${made} new task${made === 1 ? " is" : "s are"} now under Machine findings.`
            : considered > 0
              ? // "already has one" on its own invites the inference that it
                // is therefore waiting in the queue, which is false when the
                // task was closed months ago and the queue's open filter will
                // not show it.
                "No new tasks: every warning or critical finding here already " +
                "has a task, open or closed."
              : result.warnings.length > 0
                ? // Counting zero is not the same as finding nothing. A
                  // finding on a record the builder could not key reaches none
                  // of the six counts and lands in `warnings` instead, so
                  // "nothing here is a warning or critical finding" would
                  // contradict the warning printed directly beneath it.
                  "No tasks were made, and not because there was nothing to " +
                  "make them from. The warnings below say what was skipped."
                : "No tasks: nothing in this submission is a warning or critical " +
                  "finding mapped to a record."

    return (
        <div
            // Framed, so the tally reads as the answer to the button above
            // it rather than as one more paragraph of this page's furniture.
            style={{
                borderLeft: "3px solid #6b7280",
                paddingLeft: "12px",
                marginTop: "12px",
            }}
        >
            <p>{headline}</p>
            <ul>
                <li>new tasks created: {result.created_count}</li>
                <li>
                    findings that already had an open task: {result.reused_count}
                    {result.reused_count > 0
                        ? ` (${result.refreshed_count} of them refreshed with the latest snapshot)`
                        : null}
                </li>
                <li>
                    findings whose task is already closed, left closed:{" "}
                    {result.skipped_terminal_count}
                </li>
                <li>
                    info findings, which never become tasks: {result.skipped_info_count}
                </li>
                <li>
                    unmapped findings, which are about no record:{" "}
                    {result.skipped_unmapped_count}
                </li>
            </ul>
            {result.warnings.length > 0 && (
                <>
                    <h4>build warnings</h4>
                    <ul>
                        {result.warnings.map((w, i) => (
                            <li key={i}>{w}</li>
                        ))}
                    </ul>
                </>
            )}
            <p>
                <Link
                    to="/admin/curator-queue"
                    style={{ color: "#1d4ed8", textDecoration: "underline" }}
                >
                    Open Machine findings
                </Link>
            </p>
        </div>
    )
}

function MachineReviewInspectionPage() {
    const [inputValue, setInputValue] = useState("")
    const [submissionId, setSubmissionId] = useState<number | null>(null)
    const [showRaw, setShowRaw] = useState(false)
    // Maintainer observations are LOCAL ONLY in this slice — not persisted, not
    // sent anywhere. They are a manual-evaluation scratchpad (see spec §9).
    const [checks, setChecks] = useState<Record<string, boolean>>({})
    const [note, setNote] = useState("")

    const query = useQuery({
        queryKey: inspectionQueryKey(submissionId),
        queryFn: () => fetchMachineReviewInspection(submissionId as number),
        enabled: submissionId !== null,
    })

    function onSubmit(e: React.FormEvent) {
        e.preventDefault()
        const parsed = Number(inputValue)
        if (Number.isInteger(parsed) && parsed > 0) {
            setSubmissionId(parsed)
        }
    }

    const data = query.data

    return (
        <div style={{ maxWidth: "1000px" }}>
            <h2>Submission Machine-Review Inspection</h2>
            <p style={{ color: "#6b7280", marginTop: "-8px" }}>
                Admin-only diagnostic view. This is <strong>not</strong> public trust,
                human review, certification, or moderation.
            </p>

            <form onSubmit={onSubmit} style={{ marginBottom: "16px" }}>
                <label>
                    submission_id:{" "}
                    <input
                        type="number"
                        min={1}
                        value={inputValue}
                        onChange={(e) => setInputValue(e.target.value)}
                        placeholder="e.g. 123"
                    />
                </label>{" "}
                <button type="submit">Inspect</button>
            </form>

            {submissionId === null && <p>Enter a submission id to inspect.</p>}
            {query.isLoading && <p>Loading…</p>}
            {query.isError && (
                <p style={{ color: "#b91c1c" }}>
                    Failed to load inspection for submission {submissionId}:{" "}
                    {(query.error as Error).message}
                </p>
            )}

            {data && (
                <>
                    {/* Submission header */}
                    <section>
                        <h3>Submission header</h3>
                        <table>
                            <tbody>
                                <tr>
                                    <td>submission_id</td>
                                    <td>{data.submission_id}</td>
                                </tr>
                                <tr>
                                    <td>status</td>
                                    <td>— (not returned by endpoint)</td>
                                </tr>
                                <tr>
                                    <td>uploaded by</td>
                                    <td>— (not returned by endpoint)</td>
                                </tr>
                                <tr>
                                    <td>submitted at</td>
                                    <td>— (not returned by endpoint)</td>
                                </tr>
                                <tr>
                                    <td>record links / audit events</td>
                                    <td>— (not returned by endpoint)</td>
                                </tr>
                            </tbody>
                        </table>
                    </section>

                    {/* Run summary */}
                    <section>
                        <h3>Machine-review run summary</h3>
                        <p style={{ color: "#6b7280", marginTop: "-6px" }}>
                            Diagnostic only — describes how the projection landed, not a
                            verdict about the submission.
                        </p>
                        <ul>
                            <li>records with mapped findings: {data.record_summaries.length}</li>
                            <li>unmapped findings: {data.unmapped_findings_count}</li>
                            <li>mapping warnings: {data.mapping_warnings.length}</li>
                            <li>parse warnings: {data.parse_warnings.length}</li>
                            <li>
                                source audit events:{" "}
                                {data.source_audit_event_ids.length > 0
                                    ? data.source_audit_event_ids.join(", ")
                                    : "none"}
                            </li>
                            <li>
                                overall highest severity:{" "}
                                {dash(overallHighestSeverity(data))}
                            </li>
                        </ul>
                    </section>

                    {/* Record summaries table */}
                    <section>
                        <h3>Record summaries</h3>
                        {data.record_summaries.length === 0 ? (
                            <p>No records received mapped machine-review findings.</p>
                        ) : (
                            <table border={1} cellPadding={4} style={{ borderCollapse: "collapse" }}>
                                <thead>
                                    <tr>
                                        <th>record_type</th>
                                        <th>record_public_ref</th>
                                        <th>record_id</th>
                                        <th>status</th>
                                        <th>highest_severity</th>
                                        <th>findings_count</th>
                                        <th>model</th>
                                        <th>provider</th>
                                        <th>reviewed_at</th>
                                        <th>all_record_reviews_count</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {data.record_summaries.map((r, i) => (
                                        <tr key={`${r.record_type}:${r.record_public_ref ?? r.record_id ?? i}`}>
                                            <td>{r.record_type}</td>
                                            <td>{dash(r.record_public_ref)}</td>
                                            <td>{dash(r.record_id)}</td>
                                            <td>
                                                <StatusBadge status={r.latest_summary.status} />
                                            </td>
                                            <td>{dash(r.latest_summary.highest_severity)}</td>
                                            <td>{r.latest_summary.findings_count}</td>
                                            <td>{dash(r.latest_summary.model)}</td>
                                            <td>{dash(r.latest_summary.provider)}</td>
                                            <td>{dash(r.latest_summary.reviewed_at)}</td>
                                            <td>{r.all_record_reviews_count}</td>
                                        </tr>
                                    ))}
                                </tbody>
                            </table>
                        )}

                        {/* Badge legend with disclaimers — visible, not docs-only. */}
                        <h4>Status legend</h4>
                        <ul>
                            {(Object.keys(STATUS_META) as MachineReviewStatus[]).map((s) => (
                                <li key={s}>
                                    <StatusBadge status={s} /> <code>{s}</code> —{" "}
                                    {STATUS_META[s].disclaimer}
                                </li>
                            ))}
                        </ul>
                    </section>

                    {/* The run comes first: it is what produces the findings
                        the table above shows and the builder below reads.

                        The key is PREFIXED, and that is not decoration. These
                        are two children of one fragment, so React reconciles
                        them as a keyed list, and giving both of them
                        `key={data.submission_id}` makes two siblings share a
                        key. MEASURED, not feared: with the bare id, walking
                        7 -> 9 -> 7 left submission 9's run outcome mounted
                        beside submission 7's empty one -- two status regions,
                        the stale one still reading "recorded 3 findings"
                        under the wrong submission's findings. That is the
                        exact false report the key was added to prevent. */}
                    <MachineReviewRunner
                        key={`run-${data.submission_id}`}
                        submissionId={data.submission_id}
                    />

                    <CuratorTaskBuilder
                        key={data.submission_id}
                        submissionId={data.submission_id}
                    />

                    {/* Diagnostics */}
                    <section>
                        <h3>Diagnostics</h3>
                        <p style={{ color: "#6b7280", marginTop: "-6px" }}>
                            mapping warnings = projection/mapping problems · parse warnings =
                            provider/payload problems · unmapped findings do not apply to
                            records.
                        </p>
                        <h4>mapping warnings</h4>
                        {data.mapping_warnings.length === 0 ? (
                            <p>No mapping warnings.</p>
                        ) : (
                            <ul>
                                {data.mapping_warnings.map((w, i) => (
                                    <li key={i}>{w}</li>
                                ))}
                            </ul>
                        )}
                        <h4>parse warnings</h4>
                        {data.parse_warnings.length === 0 ? (
                            <p>No parse warnings.</p>
                        ) : (
                            <ul>
                                {data.parse_warnings.map((w, i) => (
                                    <li key={i}>{w}</li>
                                ))}
                            </ul>
                        )}
                        <p>unmapped findings (do not apply to records): {data.unmapped_findings_count}</p>
                        <p>
                            source audit events:{" "}
                            {data.source_audit_event_ids.length > 0
                                ? data.source_audit_event_ids.join(", ")
                                : "none"}
                        </p>
                    </section>

                    {/* Raw JSON drawer */}
                    <section>
                        <h3>Raw admin diagnostic response</h3>
                        <button type="button" onClick={() => setShowRaw((v) => !v)}>
                            {showRaw ? "Hide" : "Show"} raw JSON
                        </button>
                        {showRaw && (
                            <pre
                                data-testid="raw-json"
                                style={{
                                    background: "#f3f4f6",
                                    padding: "8px",
                                    overflowX: "auto",
                                }}
                            >
                                {JSON.stringify(data, null, 2)}
                            </pre>
                        )}
                    </section>

                    {/* Maintainer observations (local, non-persisted) */}
                    <section>
                        <h3>Maintainer observations</h3>
                        <p style={{ color: "#6b7280", marginTop: "-6px" }}>
                            Local scratchpad — <strong>not persisted</strong> and not sent
                            anywhere in this slice.
                        </p>
                        <ul style={{ listStyle: "none", paddingLeft: 0 }}>
                            {OBSERVATION_PROMPTS.map((prompt) => (
                                <li key={prompt}>
                                    <label>
                                        <input
                                            type="checkbox"
                                            checked={checks[prompt] ?? false}
                                            onChange={(e) =>
                                                setChecks((c) => ({
                                                    ...c,
                                                    [prompt]: e.target.checked,
                                                }))
                                            }
                                        />{" "}
                                        {prompt}
                                    </label>
                                </li>
                            ))}
                        </ul>
                        <textarea
                            value={note}
                            onChange={(e) => setNote(e.target.value)}
                            placeholder="Free-text observation (not persisted)…"
                            rows={4}
                            style={{ width: "100%" }}
                        />
                    </section>
                </>
            )}
        </div>
    )
}

export default MachineReviewInspectionPage
