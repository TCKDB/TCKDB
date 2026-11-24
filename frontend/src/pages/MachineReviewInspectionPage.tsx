import { Fragment, useEffect, useRef, useState } from "react"
import { Link, Navigate, useSearchParams } from "react-router-dom"
import { keepPreviousData, useQuery } from "@tanstack/react-query"
import "../auth.css"
import "../admin.css"
import { fetchMachineReviewInspection, InspectionFetchError, inspectionQueryKey } from "../api/machineReviewInspection"
import { listAdminUsers } from "../api/adminApi"
import { AuthApiError } from "../api/authApi"
import {
    fetchSubmission,
    listSubmissionAuditEvents,
    listSubmissionMachineReviewOverview,
    listSubmissionRecordLinks,
    SUBMISSION_LIST_LIMIT,
} from "../api/submissionsApi"
import { Disclosure } from "../components/Disclosure"
import { MachineFindingList } from "../components/MachineFindingList"
import { MachineReviewerText } from "../components/MachineReviewerText"
import { RequestMachineReview } from "../components/RequestMachineReview"
import { MachineReviewStatusLabel } from "../components/MachineReviewStatusLabel"
import { RecordLocationCell } from "../components/RecordLocationCell"
import { adminDate, adminDateTime, recommendationFor, STATUS_TOKENS, wordLabel, wordsFor } from "../domain/machineReviewWords"
import { recordTypeGroupLabel, resolveRecordLocation } from "../domain/recordRoute"
import { useAuth } from "../hooks/useAuth"
import { useDocumentTitle } from "../hooks/useDocumentTitle"
import {
    buildCuratorTasksForSubmission,
    CuratorTaskResponseError,
} from "../api/curatorTasksApi"
import { listSubmissionJobs, submissionJobsKey } from "../api/machineReviewJobsApi"
import type { Job } from "../types/machineReviewJob"
import {
    findingsConsidered,
    severityClass,
    type CuratorTaskBuildResult,
} from "../types/curatorTask"
import type {
    AdminMachineReviewRecordInspection,
    AdminMachineReviewRecordReview,
    AdminMachineReviewRun,
    AdminMachineReviewUnmappedFinding,
    AdminSubmissionMachineReviewInspection,
} from "../types/machineReviewInspection"

/**
 * Machine-review inspection, for curators and admins.
 *
 * Reads the stored machine reviews of one submission: what the reviewer found, record by record, what it said about no
 * record in particular, and every run it made. It also requests a review and shows its progress. This is a diagnostic
 * view, not public trust, human review, certification or moderation. The routes behind it are for curators and admins
 * on the server, and the curator-task routes for admins only; the page asks for the same roles first.
 *
 * A submission is chosen from a list and addressed as `?submission=<id>`. The id lives in the address only; the page
 * names a submission by its public reference.
 *
 * The reviewer's findings, messages and summaries are shown only inside a block labelled as the reviewer's. Anything the depositor typed
 * (the submission title) appears only in a field marked as deposited.
 */

/** Accessible names for the curator-task build's two live regions. */
const BUILD_ALERT_LABEL = "curator task build problem"
const BUILD_STATUS_LABEL = "curator task build result"

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
 * submissions arrived, and the only way to fill it was a hand-written curl.
 *
 * It lives here because this page is already showing the exact findings
 * the build reads: an admin who has just looked at a submission's
 * machine-review projection is the person who can judge whether those
 * findings deserve a curator's time.
 *
 * **A tally must never outlive its submission.** Submission 9's findings
 * under submission 7's tally is a false report, and the more convincing
 * for being half true. `key={submissionId}` at the mount site is what
 * prevents it, and it is load-bearing, not insurance.
 *
 * Walking
 * to a submission this session has not seen, `query.data` goes undefined
 * while it loads, the parent unmounts the whole results block, and this
 * component's state goes with it -- so removing the key changes nothing
 * and the test still passed. But react-query caches (`gcTime`, five
 * minutes by default): walk BACK to a submission already visited and its
 * data arrives synchronously, `query.data` never goes undefined, nothing
 * unmounts, and without the key the old tally sits under the new
 * submission's findings. Covered by the two tests that walk
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
            <h2>Curator tasks</h2>
            <p className="admin-hint">
                Tasks are <strong>not</strong> created on upload. This builds them
                from the warning and critical findings above, for this submission
                only, and writes nothing but task rows: the submission&apos;s status
                and every record&apos;s review state are untouched. A task is
                advisory. It asks a person to look, and endorses nothing.
            </p>
            <button type="button" onClick={build} aria-disabled={state.kind === "running" ? "true" : undefined}>
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
            <div role="alert" aria-label={BUILD_ALERT_LABEL}>
                {state.kind === "failed" ? <p className="auth-error">{state.message}</p> : null}
            </div>
            <div role="status" aria-label={BUILD_STATUS_LABEL}>
                {state.kind === "done" ? <BuildTally result={state.result} submissionId={submissionId} /> : null}
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
 * before it can reach here.
 */
function BuildTally({ result, submissionId }: { result: CuratorTaskBuildResult; submissionId: number }) {
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
        <div className="card">
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
                    <h3>build warnings</h3>
                    <ul>
                        {result.warnings.map((w, i) => (
                            <li key={i}>{w}</li>
                        ))}
                    </ul>
                </>
            )}
            <p>
                <Link to={`/admin/curator-queue?submission=${submissionId}`}>
                    Open Machine findings
                </Link>
            </p>
        </div>
    )
}


function plural(n: number, one: string, many = `${one}s`): string {
    return `${n} ${n === 1 ? one : many}`
}

/** A submission id from `?submission=`: digits only, greater than zero. */
function parseSubmissionParam(raw: string | null): { kind: "none" } | { kind: "bad" } | { kind: "ok"; id: number } {
    if (raw === null || raw === "") return { kind: "none" }
    if (!/^\d+$/.test(raw) || Number(raw) <= 0) return { kind: "bad" }
    return { kind: "ok", id: Number(raw) }
}

const INSPECTION_PATH = "/admin/machine-review-inspection"
const PAGE_LIMIT = 50

type PickerFilter = "all" | "reviewed" | "open"

function SubmissionPicker() {
    // The view lives in the address, so Back and a shared link return to the same page of the same list.
    const [params, setParams] = useSearchParams()
    const showParam = params.get("show")
    const filter: PickerFilter = showParam === "reviewed" || showParam === "open" ? showParam : "all"
    const rawOffset = params.get("offset")
    const offset = rawOffset !== null && /^\d+$/.test(rawOffset) ? Number(rawOffset) : 0
    function setView(next: { show?: PickerFilter; offset?: number }) {
        const merged = new URLSearchParams(params)
        const show = next.show ?? filter
        const at = next.offset ?? 0
        if (show === "all") merged.delete("show")
        else merged.set("show", show)
        if (at === 0) merged.delete("offset")
        else merged.set("offset", String(at))
        setParams(merged, { replace: true })
    }
    const query = useQuery({
        queryKey: ["machine-review-overview", filter, offset],
        queryFn: async () => ({
            ...(await listSubmissionMachineReviewOverview({
                offset,
                limit: PAGE_LIMIT,
                reviewed: filter === "reviewed" ? true : undefined,
                hasOpenTasks: filter === "open" ? true : undefined,
            })),
            shownOffset: offset,
        }),
        // Keeps the table and the paging buttons mounted while the next page loads, so focus stays put.
        placeholderData: keepPreviousData,
    })
    const page = query.data
    const shownOffset = page?.shownOffset ?? offset

    return (
        <section aria-labelledby="picker-heading">
            <h2 id="picker-heading">Choose a submission</h2>
            <div className="admin-filter">
                <label htmlFor="picker-filter">Show</label>{" "}
                <select
                    id="picker-filter"
                    className="admin-role-select"
                    value={filter}
                    onChange={(e) => setView({ show: e.target.value as PickerFilter })}
                >
                    <option value="all">every submission</option>
                    <option value="reviewed">reviewed only</option>
                    <option value="open">with open tasks</option>
                </select>
            </div>

            {query.isLoading && <p role="status">Loading the submissions…</p>}
            {query.isError && <p className="auth-error" role="alert">The submissions could not be loaded.</p>}

            {page && page.items.length === 0 && <p role="status">No submissions match.</p>}

            {page && page.items.length > 0 && (
                <>
                    <p className="admin-count" role="status">
                        Showing {shownOffset + 1}–{shownOffset + page.items.length} of {page.total} submissions
                    </p>
                    <div className="table-scroll">
                        <table className="data-table" aria-label="Submissions">
                            <thead>
                                <tr>
                                    <th scope="col">Submission</th>
                                    <th scope="col">Kind</th>
                                    <th scope="col">Status</th>
                                    <th scope="col">Submitted</th>
                                    <th scope="col">Uploader</th>
                                    <th scope="col">Machine review</th>
                                    <th scope="col">Open tasks</th>
                                    <th scope="col">Action</th>
                                </tr>
                            </thead>
                            <tbody>
                                {page.items.map((row) => {
                                    const run = row.machine_review.latest_run
                                    return (
                                        <tr key={row.id}>
                                            <td><span className="data">{row.public_ref}</span></td>
                                            <td>{wordLabel("submissionKind", row.submission_kind)}</td>
                                            <td>{wordLabel("submissionStatus", row.status)}</td>
                                            <td className="admin-date">{adminDate(row.submitted_at)}</td>
                                            <td>
                                                {row.created_by_username ?? <span className="admin-absent">not named</span>}
                                            </td>
                                            <td>
                                                {row.machine_review.open_job !== null && (
                                                    <>
                                                        <strong>
                                                            {row.machine_review.open_job.status === "queued"
                                                                ? "review queued"
                                                                : `review running (step ${row.machine_review.open_job.step_count})`}
                                                        </strong>
                                                        {row.machine_review.run_count > 0 ? ", " : ""}
                                                    </>
                                                )}
                                                {row.machine_review.run_count === 0 ? (
                                                    row.machine_review.open_job !== null ? null : (
                                                        <span className="admin-absent">never reviewed</span>
                                                    )
                                                ) : (
                                                    <>
                                                        {plural(row.machine_review.run_count, "run")}
                                                        {run && run.status ? (
                                                            <>
                                                                , latest <MachineReviewStatusLabel status={run.status} />
                                                                {run.highest_severity ? (
                                                                    <>
                                                                        , highest{" "}
                                                                        <span className={severityClass(run.highest_severity) ?? undefined}>
                                                                            {wordLabel("severity", run.highest_severity)}
                                                                        </span>
                                                                    </>
                                                                ) : null}
                                                            </>
                                                        ) : null}
                                                    </>
                                                )}
                                            </td>
                                            <td>
                                                {row.machine_review.open_task_count} open of {row.machine_review.task_count}
                                            </td>
                                            <td>
                                                <Link
                                                    to={`${INSPECTION_PATH}?submission=${row.id}`}
                                                    aria-label={`Inspect ${row.public_ref}`}
                                                >
                                                    Inspect
                                                </Link>
                                            </td>
                                        </tr>
                                    )
                                })}
                            </tbody>
                        </table>
                    </div>
                </>
            )}

            {page && (page.items.length > 0 || offset > 0) && (
                <div className="admin-paging">
                    <button
                        type="button"
                        aria-disabled={offset === 0 ? "true" : undefined}
                        onClick={() => offset > 0 && setView({ offset: Math.max(0, offset - PAGE_LIMIT) })}
                    >
                        Previous
                    </button>{" "}
                    <button
                        type="button"
                        aria-disabled={offset + PAGE_LIMIT >= page.total ? "true" : undefined}
                        onClick={() => offset + PAGE_LIMIT < page.total && setView({ offset: offset + PAGE_LIMIT })}
                    >
                        Next
                    </button>
                </div>
            )}
        </section>
    )
}

function isNotFound(error: unknown): boolean {
    return (
        (error instanceof InspectionFetchError || error instanceof AuthApiError) && error.status === 404
    )
}

function SubmissionHeader({ submissionId, isAdmin }: { submissionId: number; isAdmin: boolean }) {
    const submission = useQuery({ queryKey: ["submission", submissionId], queryFn: () => fetchSubmission(submissionId) })
    const links = useQuery({
        queryKey: ["submission-record-links", submissionId],
        queryFn: () => listSubmissionRecordLinks(submissionId),
    })
    const events = useQuery({
        queryKey: ["submission-audit-events", submissionId],
        queryFn: () => listSubmissionAuditEvents(submissionId),
    })
    const users = useQuery({
        queryKey: ["admin-users-for-uploader"],
        queryFn: () => listAdminUsers({ limit: 200 }),
        // The account list is an admin read; a curator is not named the uploader here.
        enabled: isAdmin,
    })

    const s = submission.data
    const uploader = s ? users.data?.items.find((u) => u.id === s.created_by)?.username : undefined

    const counts = new Map<string, number>()
    for (const link of links.data ?? []) counts.set(link.record_type, (counts.get(link.record_type) ?? 0) + 1)

    return (
        <>
            <section aria-labelledby="header-heading">
                <h2 id="header-heading">Submission</h2>
                {submission.isLoading && <p role="status">Loading the submission…</p>}
                {submission.isError && !isNotFound(submission.error) && (
                    <p className="auth-error" role="alert">The submission could not be loaded.</p>
                )}
                {s && (
                    <dl className="admin-facts">
                        <dt>Submission</dt>
                        <dd><span className="data">{s.public_ref}</span></dd>
                        <dt>Kind</dt>
                        <dd>{wordLabel("submissionKind", s.submission_kind)}</dd>
                        <dt>Status</dt>
                        <dd>{wordLabel("submissionStatus", s.status)}</dd>
                        <dt>Submitted</dt>
                        <dd>{adminDateTime(s.submitted_at)}</dd>
                        <dt>Uploader</dt>
                        <dd>{uploader ?? <span className="admin-absent">not named</span>}</dd>
                        <dt>Title</dt>
                        <dd data-testid="as-deposited">
                            {s.title ? (
                                <>
                                    <span className="admin-hint">as deposited: </span>
                                    <q>{s.title}</q>
                                </>
                            ) : (
                                <span className="admin-absent">none was given</span>
                            )}
                        </dd>
                        <dt>Records linked</dt>
                        <dd>
                            {links.isLoading ? (
                                "Loading…"
                            ) : counts.size === 0 ? (
                                <span className="admin-absent">none</span>
                            ) : (
                                <ul className="admin-plain-list admin-tight-list">
                                    {[...counts.entries()].map(([type, n]) => (
                                        <li key={type}>
                                            {recordTypeGroupLabel(type, n)} {n}
                                        </li>
                                    ))}
                                </ul>
                            )}
                            {(links.data?.length ?? 0) >= SUBMISSION_LIST_LIMIT && (
                                <p className="admin-hint">Counted from the first {SUBMISSION_LIST_LIMIT} links.</p>
                            )}
                        </dd>
                    </dl>
                )}
            </section>

            <section aria-label="Audit trail">
                <Disclosure summary="Audit trail" count={events.data?.length}>
                    {events.isLoading && <p role="status">Loading the audit trail…</p>}
                    {events.isError && <p className="auth-error" role="alert">The audit trail could not be loaded.</p>}
                    {events.data && events.data.length === 0 && <p>No events are recorded.</p>}
                    {events.data && events.data.length > 0 && (
                        <ul className="admin-plain-list">
                            {events.data.map((e) => (
                                <li key={e.id}>
                                    {adminDateTime(e.created_at)} · {wordLabel("auditEventKind", e.event_kind)} ·{" "}
                                    {wordLabel("actorKind", e.actor_kind)}
                                    {e.from_status !== null || e.to_status !== null ? (
                                        <>
                                            {" "}· status:{" "}
                                            {e.from_status ? wordLabel("submissionStatus", e.from_status) : "none"} →{" "}
                                            {e.to_status ? wordLabel("submissionStatus", e.to_status) : "none"}
                                        </>
                                    ) : null}
                                </li>
                            ))}
                        </ul>
                    )}
                </Disclosure>
            </section>
        </>
    )
}

function priorityWords(priority: string | null): string {
    return priority === null ? "none" : wordLabel("priority", priority)
}

/** Why a failed run did not finish, in the archive's words, from the job that recorded it. */
function failedRunWords(run: AdminMachineReviewRun, jobs: readonly Job[] | undefined): string {
    const code = jobs?.find((j) => j.audit_event_id !== null && j.audit_event_id === run.audit_event_id)?.failure_code ?? null
    return code === null ? "Did not finish." : `Did not finish: ${wordLabel("jobFailure", code)}.`
}

function RunsSection({ runs, submissionId }: { runs: AdminMachineReviewRun[]; submissionId: number }) {
    const jobs = useQuery({ queryKey: submissionJobsKey(submissionId), queryFn: () => listSubmissionJobs(submissionId) })
    const ordered = [...runs].sort((a, b) => (b.reviewed_at ?? "").localeCompare(a.reviewed_at ?? ""))
    return (
        <section aria-labelledby="runs-heading">
            <h2 id="runs-heading">Reviewer runs</h2>
            {ordered.length === 0 ? (
                <p>The reviewer has not been run on this submission.</p>
            ) : (
                <ul className="admin-plain-list">
                    {ordered.map((run, i) => (
                        <li key={i} className="card">
                            <p>
                                {adminDateTime(run.reviewed_at) || <span className="admin-absent">no date</span>}
                                {run.parsed && run.status ? (
                                    <>
                                        {" "}· <MachineReviewStatusLabel status={run.status} />
                                    </>
                                ) : null}
                            </p>
                            {run.parsed ? (
                                <>
                                    <p>
                                        Priority {priorityWords(run.curator_priority)} · model{" "}
                                        {run.model ?? <span className="admin-absent">not named</span>} · provider{" "}
                                        {run.provider ?? <span className="admin-absent">not named</span>}
                                    </p>
                                    <p>
                                        {plural(run.findings_count, "finding")}: {run.mapped_findings_count} about a record,{" "}
                                        {run.unmapped_findings_count} about none · highest severity{" "}
                                        {run.highest_severity ? (
                                            <span className={severityClass(run.highest_severity) ?? undefined}>
                                                {wordLabel("severity", run.highest_severity)}
                                            </span>
                                        ) : (
                                            <span className="admin-absent">none</span>
                                        )}
                                    </p>
                                    {run.status === "machine_review_failed" ? (
                                        <>
                                            <p>{failedRunWords(run, jobs.data)}</p>
                                            {run.summary && (
                                                <p className="admin-hint">Error the archive recorded: {run.summary}</p>
                                            )}
                                        </>
                                    ) : (
                                        run.summary && (
                                            <MachineReviewerText>
                                                <blockquote>{run.summary}</blockquote>
                                            </MachineReviewerText>
                                        )
                                    )}
                                </>
                            ) : (
                                <p>The stored result could not be read.</p>
                            )}
                            {run.parse_warnings.length > 0 && (
                                <ul>
                                    {run.parse_warnings.map((w, k) => (
                                        <li key={k}>{w}</li>
                                    ))}
                                </ul>
                            )}
                        </li>
                    ))}
                </ul>
            )}
        </section>
    )
}

function ReviewHeadline({ review }: { review: AdminMachineReviewRecordReview }) {
    return (
        <p>
            {adminDate(review.reviewed_at)} · <MachineReviewStatusLabel status={review.status} withMeaning /> · priority{" "}
            {priorityWords(review.curator_priority)}
        </p>
    )
}

function RecordCard({ record, isAdmin }: { record: AdminMachineReviewRecordInspection; isAdmin: boolean }) {
    const location = resolveRecordLocation(
        record.record_type,
        record.record_public_ref,
        record.container_type,
        record.container_ref,
    )
    const typeLabel = recordTypeGroupLabel(record.record_type, 1)
    const reviews = record.all_record_reviews
    const latest = reviews.find((r) => r.is_latest) ?? reviews[0]
    const earlier = reviews.filter((r) => r !== latest)
    const summary = record.latest_summary
    // The curator-task pages are admin-only, so a curator is given no link to them.
    const taskHref = !isAdmin ? undefined : (taskId: number) => {
        const q = new URLSearchParams({ state: "all", record_type: record.record_type })
        if (record.record_id !== null) q.set("record", String(record.record_id))
        q.set("task", String(taskId))
        return `/admin/curator-queue?${q.toString()}`
    }
    return (
        <li className="card">
            <h3>
                <span className="admin-record-type">{typeLabel}</span> <RecordLocationCell location={location} />
            </h3>
            <dl className="admin-facts">
                <dt>Latest review</dt>
                <dd><MachineReviewStatusLabel status={summary.status} withMeaning /></dd>
                <dt>Highest severity</dt>
                <dd>
                    {summary.highest_severity ? (
                        <span className={severityClass(summary.highest_severity) ?? undefined}>
                            {wordLabel("severity", summary.highest_severity)}
                        </span>
                    ) : (
                        <span className="admin-absent">none</span>
                    )}
                </dd>
                <dt>Curator priority</dt>
                <dd>{priorityWords(summary.curator_priority)}</dd>
                <dt>Reviewed</dt>
                <dd>{adminDate(summary.reviewed_at) || <span className="admin-absent">no date</span>}</dd>
                {latest && (
                    <>
                        <dt>Model</dt>
                        <dd>{latest.model ?? <span className="admin-absent">not named</span>}</dd>
                        <dt>Provider</dt>
                        <dd>{latest.provider ?? <span className="admin-absent">not named</span>}</dd>
                    </>
                )}
            </dl>
            {latest && <MachineFindingList findings={latest.findings} taskHref={taskHref} />}
            {earlier.length > 0 && (
                <Disclosure summary="Earlier reviews" count={earlier.length}>
                    <ul className="admin-plain-list">
                        {earlier.map((review, i) => (
                            <li key={i}>
                                <ReviewHeadline review={review} />
                                <MachineFindingList findings={review.findings} taskHref={taskHref} />
                            </li>
                        ))}
                    </ul>
                </Disclosure>
            )}
        </li>
    )
}

function UnmappedSection({ findings }: { findings: AdminMachineReviewUnmappedFinding[] }) {
    return (
        <section aria-labelledby="unmapped-heading">
            <h2 id="unmapped-heading">Findings about no record</h2>
            <p className="admin-hint">
                The reviewer raised these, but they could not be tied to a record in this submission.
            </p>
            {findings.length === 0 ? (
                <p>There are none.</p>
            ) : (
                <ul className="admin-plain-list">
                    {findings.map((f, i) => (
                        <li key={i} className="card">
                            <p>{wordLabel("unmappedReason", f.reason)}</p>
                            {(f.cited_record_type !== null || f.cited_record_ref !== null) && (
                                <MachineReviewerText>
                                    <p>
                                        Cited:{" "}
                                        {f.cited_record_type !== null && <code>{f.cited_record_type}</code>}{" "}
                                        {f.cited_record_ref !== null && <code>{f.cited_record_ref}</code>}
                                    </p>
                                </MachineReviewerText>
                            )}
                            <MachineFindingList findings={[f]} />
                        </li>
                    ))}
                </ul>
            )}
        </section>
    )
}

function Diagnostics({ data }: { data: AdminSubmissionMachineReviewInspection }) {
    return (
        <section aria-labelledby="diagnostics-heading">
            <h2 id="diagnostics-heading">Diagnostics</h2>
            <p className="admin-hint">
                Mapping warnings are problems tying findings to records. Parse warnings are problems reading what the
                reviewer returned.
            </p>
            <h3>Mapping warnings</h3>
            {data.mapping_warnings.length === 0 ? (
                <p>No mapping warnings.</p>
            ) : (
                <MachineReviewerText>
                    <ul>{data.mapping_warnings.map((w, i) => <li key={i}>{w}</li>)}</ul>
                </MachineReviewerText>
            )}
            <h3>Parse warnings</h3>
            {data.parse_warnings.length === 0 ? (
                <p>No parse warnings.</p>
            ) : (
                <ul>{data.parse_warnings.map((w, i) => <li key={i}>{w}</li>)}</ul>
            )}
        </section>
    )
}

function StatusLegend() {
    return (
        <Disclosure summary="What the statuses mean">
            <dl className="admin-facts admin-legend">
                {STATUS_TOKENS.map((token) => (
                    <Fragment key={token}>
                        <dt><MachineReviewStatusLabel status={token} /></dt>
                        <dd>{wordsFor("status", token).meaning}</dd>
                    </Fragment>
                ))}
            </dl>
        </Disclosure>
    )
}

/**
 * The advice of the newest stored review, in the archive's words. It is advice, never approval: a person still decides.
 * A failed review reads with the failure code of the job that recorded it; an unreadable one says so.
 */
function RecommendationLine({ submissionId, runs }: { submissionId: number; runs: AdminMachineReviewRun[] }) {
    const jobs = useQuery({ queryKey: submissionJobsKey(submissionId), queryFn: () => listSubmissionJobs(submissionId) })
    const latest = [...runs].sort((a, b) => (b.reviewed_at ?? "").localeCompare(a.reviewed_at ?? ""))[0]
    if (latest === undefined) return null
    let line: string | null
    if (!latest.parsed || latest.status === null) {
        line = "The latest machine review could not be read."
    } else {
        const code = jobs.data?.find((j) => j.audit_event_id !== null && j.audit_event_id === latest.audit_event_id)?.failure_code ?? null
        line = recommendationFor(latest.status, code)
    }
    if (line === null) return null
    return (
        <p className="machine-review-recommendation" data-testid="recommendation">
            <strong>{line}</strong>
            {latest.reviewed_at && <span className="admin-hint"> · reviewed {adminDateTime(latest.reviewed_at)}</span>}
        </p>
    )
}

function SubmissionInspection({ submissionId, isAdmin }: { submissionId: number; isAdmin: boolean }) {
    const query = useQuery({
        queryKey: inspectionQueryKey(submissionId),
        queryFn: () => fetchMachineReviewInspection(submissionId),
    })
    const data = query.data

    return (
        <>
            <p className="admin-hint"><Link to={INSPECTION_PATH}>All submissions</Link></p>
            {query.isError && (
                <p className="auth-error" role="alert">
                    {isNotFound(query.error)
                        ? "No submission was found for this link."
                        : "The machine-review inspection could not be loaded."}
                </p>
            )}
            {!isNotFound(query.error) && <SubmissionHeader submissionId={submissionId} isAdmin={isAdmin} />}
            {query.isLoading && <p role="status">Loading the inspection…</p>}

            {data && (
                <>
                    <section aria-labelledby="machine-review-heading">
                        <h2 id="machine-review-heading">Machine review</h2>
                        <p className="admin-hint">
                            A machine review is <strong>advisory</strong>. It endorses nothing, and it is neither human review of a
                            record nor moderation of the submission: neither moves when one runs. Requesting another reviews this
                            submission again and records another result.
                        </p>
                        <RecommendationLine submissionId={data.submission_id} runs={data.runs} />
                        {/* Keyed by submission so a request or a running review never follows the viewer to another one. */}
                        <RequestMachineReview key={`run-${data.submission_id}`} submissionId={data.submission_id} />
                    </section>

                    <RunsSection runs={data.runs} submissionId={data.submission_id} />

                    <section aria-labelledby="records-heading">
                        <h2 id="records-heading">Records</h2>
                        {data.record_summaries.length === 0 ? (
                            <p>No record received a mapped finding.</p>
                        ) : (
                            <ul className="admin-plain-list">
                                {data.record_summaries.map((record, i) => (
                                    <RecordCard key={`${record.record_type}:${record.record_public_ref ?? record.record_id ?? i}`} record={record} isAdmin={isAdmin} />
                                ))}
                            </ul>
                        )}
                    </section>

                    <UnmappedSection findings={data.unmapped_findings} />
                    <Diagnostics data={data} />

                    {isAdmin && <CuratorTaskBuilder key={data.submission_id} submissionId={data.submission_id} />}
                    <StatusLegend />
                </>
            )}
        </>
    )
}

function MachineReviewInspectionPage() {
    useDocumentTitle("Machine review inspection")
    const { state } = useAuth()
    const [params] = useSearchParams()
    const chosen = parseSubmissionParam(params.get("submission"))
    // Choosing a submission swaps the picker for the inspection in place, so focus goes to the heading, as a page
    // change would. The first render is left alone: a page opened by address keeps the browser's own focus.
    const chosenId = chosen.kind === "ok" ? chosen.id : null
    const lastChosen = useRef(chosenId)
    useEffect(() => {
        if (lastChosen.current === chosenId) return
        lastChosen.current = chosenId
        if (chosenId !== null) document.getElementById("inspection-heading")?.focus()
    }, [chosenId])

    if (state.status === "loading") {
        return (
            <section className="admin-page">
                <h1>Machine review inspection</h1>
                <p role="status">Checking your account…</p>
            </section>
        )
    }
    if (state.status === "unreachable") {
        return (
            <section className="admin-page">
                <h1>Machine review inspection</h1>
                <p className="auth-error" role="alert">
                    The archive could not be reached, so your account could not be checked. This is not a sign that you
                    are signed out.
                </p>
            </section>
        )
    }
    if (state.status === "signed-out") return <Navigate to="/login" replace />
    if (state.user.role !== "admin" && state.user.role !== "curator") {
        return (
            <section className="admin-page">
                <h1>Machine review inspection</h1>
                <p className="auth-error" role="alert">This page is for curators and administrators.</p>
            </section>
        )
    }
    const isAdmin = state.user.role === "admin"

    return (
        <section className="admin-page">
            <h1 id="inspection-heading" tabIndex={-1}>Machine review inspection</h1>
            <p className="admin-lede">
                What an automated reviewer said about one submission. This is advisory: it is <strong>not</strong> human
                review, certification, or moderation, and nothing here changes what a reader is told to trust.
            </p>
            {chosen.kind === "bad" && <p className="auth-error" role="alert">That link does not name a submission.</p>}
            {chosen.kind === "ok" ? <SubmissionInspection submissionId={chosen.id} isAdmin={isAdmin} /> : <SubmissionPicker />}
        </section>
    )
}

export default MachineReviewInspectionPage
