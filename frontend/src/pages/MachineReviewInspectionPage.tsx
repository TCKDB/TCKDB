import { useState } from "react"
import { Link } from "react-router-dom"
import { useQuery } from "@tanstack/react-query"
import { fetchMachineReviewInspection } from "../api/machineReviewInspection"
import { AuthApiError } from "../api/authApi"
import {
    buildCuratorTasksForSubmission,
    CuratorTaskResponseError,
} from "../api/curatorTasksApi"
import {
    findingsConsidered,
    type CuratorTaskBuildResult,
} from "../types/curatorTask"
import {
    overallHighestSeverity,
    type MachineReviewStatus,
} from "../types/machineReviewInspection"

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
 *    the tally. Any tasks it made are in the curator queue."
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
        "run; check the curator queue before pressing it again."
    )
}

/**
 * The one control that puts work into the curator queue.
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
                so this costs the sighted layout nothing. */}
            <div role="alert" style={{ color: "#b91c1c" }}>
                {state.kind === "failed" ? <p>{state.message}</p> : null}
            </div>
            <div role="status">
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
            ? `${made} new task${made === 1 ? " is" : "s are"} now in the curator queue.`
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
                    Open the curator queue
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
        queryKey: ["machine-review-inspection", submissionId],
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
