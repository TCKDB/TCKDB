import { useCallback, useEffect, useRef, useState } from "react"
import { Link, Navigate, useSearchParams } from "react-router-dom"
import "../auth.css"
import "../admin.css"
import { listAdminUsers } from "../api/adminApi"
import { AuthApiError } from "../api/authApi"
import {
    assignCuratorTask,
    CuratorTaskResponseError,
    getCuratorTask,
    listCuratorTasks,
    reopenCuratorTask,
    resolveCuratorTask,
    startCuratorTaskReview,
    type CuratorTaskListOptions,
} from "../api/curatorTasksApi"
import { fetchSubmission, listSubmissionMachineReviewOverview } from "../api/submissionsApi"
import { MachineFindingList } from "../components/MachineFindingList"
import { MachineReviewStatusLabel } from "../components/MachineReviewStatusLabel"
import { MadeUnderLine } from "../components/MadeUnderLine"
import { NewTabNote, RecordLocationCell } from "../components/RecordLocationCell"
import { adminDate, adminDateTime, RECORD_TYPE_OPTIONS, wordsFor } from "../domain/machineReviewWords"
import { recordTypeGroupLabel, recordTypeWords, resolveRecordLocation } from "../domain/recordRoute"
import { useAuth } from "../hooks/useAuth"
import { useDocumentTitle } from "../hooks/useDocumentTitle"
import {
    KNOWN_SEVERITIES,
    OPEN_STATES,
    TERMINAL_STATES,
    isOpen,
    resolutionMeaning,
    severityClass,
    stateLabel,
    type CuratorTask,
    type CuratorTaskState,
} from "../types/curatorTask"

/**
 * Machine findings: machine-review findings waiting on a person.
 *
 * Named "Machine findings" on screen, not "Curator queue". The on-screen
 * names describe what each page is ABOUT: findings a machine raised
 * here, records a human judged on `/review-queue` ("Record review").
 *
 * The route is `/admin/curator-queue`, and the file, component and
 * query keys say "curator task" throughout, so the URL stays stable for
 * bookmarks and links. `curator_task` is also the
 * backend's own table name, so the internal spelling still matches the
 * thing it names.
 *
 * Eight backend routes have served this queue since before there was
 * anywhere to see it. This is that place.
 *
 * **What this surface is not.** It is not record review. Nothing here
 * endorses any science, writes `record_review`, or changes what a reader
 * is told to trust. Resolving a task says a person dealt with a *finding*;
 * `resolved_human_reviewed` notes that a review happened elsewhere and
 * does not itself perform one. That separation is the whole design,
 * and it is stated on the page rather than only in a doc, because
 * a queue with a button labelled "resolve" invites exactly the wrong
 * reading.
 *
 * Filtering defaults to open tasks. A curator opening this wants the work,
 * not the history; closed tasks are one click away and each closed state is
 * labelled with what it asserts. Every filter, the page offset and the task
 * to open live in the address, so a view can be linked to. Paging and the
 * count come from the server: the total is exact under the same filters.
 *
 * ## Per-row state, and why none of it is per-page
 *
 * Everything a row owns is keyed by task id: which row's close form is
 * open, the note typed into it, the state chosen in it, which rows have a
 * write in flight, and which row is showing a refusal. Holding the note and
 * the chosen state as single page-level values would silently attach one
 * record's written justification to a different record's audit trail. Open the
 * close form on task A, type why A is fine, change your mind, open task
 * B: the form under B came up pre-filled with A's sentence and already
 * submittable. `resolution_note` exists precisely so the next reader
 * learns why THIS finding stopped mattering.
 *
 * `busy` is a set for the same reason: as a single slot, the first write
 * to come back re-enabled every other row's buttons, including rows still
 * waiting on their own request.
 */

type LoadState =
    | { status: "loading" }
    | { status: "error"; message: string }
    | { status: "ready"; tasks: CuratorTask[]; total: number; unreadable: number; offset: number }

/** The close form's contents, for the one row it is open under. */
type ResolveDraft = { taskId: number; state: CuratorTaskState; note: string }

/** The reopen form's contents, for the one row it is open under. */
type ReopenDraft = { taskId: number; target: CuratorTaskState; clearAssignment: boolean }

type DetailState =
    | { status: "loading" }
    | { status: "error" }
    | { status: "ready"; task: CuratorTask }

const PAGE_LIMIT = 50

type StateFilter = "open" | "closed" | "all" | CuratorTaskState

/** The view in the address. Every field is already validated. */
type Filter = {
    state: StateFilter
    severity: string
    recordType: string
    recordId: number | null
    submissionId: number | null
    assignee: string
    offset: number
}

const STATE_TOKENS: readonly string[] = [...OPEN_STATES, ...TERMINAL_STATES]

function positiveInt(raw: string | null): number | null {
    return raw !== null && /^\d+$/.test(raw) && Number(raw) > 0 ? Number(raw) : null
}

function readFilter(params: URLSearchParams): Filter {
    const state = params.get("state") ?? "open"
    const recordType = params.get("record_type") ?? ""
    const severity = params.get("severity") ?? ""
    const assignee = params.get("assignee") ?? ""
    return {
        state: state === "open" || state === "closed" || state === "all" || STATE_TOKENS.includes(state) ? (state as StateFilter) : "open",
        severity: (KNOWN_SEVERITIES as readonly string[]).includes(severity) ? severity : "",
        recordType: RECORD_TYPE_OPTIONS.some((o) => o.value === recordType) ? recordType : "",
        // A record id means nothing without its type, so it is honoured only beside one.
        recordId: RECORD_TYPE_OPTIONS.some((o) => o.value === recordType) ? positiveInt(params.get("record")) : null,
        submissionId: positiveInt(params.get("submission")),
        assignee: assignee === "me" || positiveInt(assignee) !== null ? assignee : "",
        offset: positiveInt(params.get("offset")) ?? 0,
    }
}

function isDefaultView(f: Filter): boolean {
    return f.state === "open" && f.severity === "" && f.recordType === "" && f.recordId === null && f.submissionId === null && f.assignee === ""
}

export default function CuratorQueuePage() {
    useDocumentTitle("Machine findings")
    const { state } = useAuth()
    const [params, setParams] = useSearchParams()
    const filter = readFilter(params)
    const [load, setLoad] = useState<LoadState>({ status: "loading" })
    const [busy, setBusy] = useState<ReadonlySet<number>>(new Set())
    //: Keyed by task, like everything else a row owns. As a single slot,
    //: acting on row B silently erased the refusal still standing under
    //: row A, and two rows could never show their own refusals at once.
    const [rowErrors, setRowErrors] = useState<ReadonlyMap<number, string>>(new Map())
    const [draft, setDraft] = useState<ResolveDraft | null>(null)
    const [reopenDraft, setReopenDraft] = useState<ReopenDraft | null>(null)
    const [assignDrafts, setAssignDrafts] = useState<ReadonlyMap<number, string>>(new Map())
    const [expanded, setExpanded] = useState<ReadonlySet<number>>(() => {
        const open = positiveInt(params.get("task"))
        return new Set(open === null ? [] : [open])
    })
    const [details, setDetails] = useState<ReadonlyMap<number, DetailState>>(new Map())
    const [admins, setAdmins] = useState<Array<{ id: number; username: string }>>([])
    const [submissions, setSubmissions] = useState<Array<{ id: number; label: string }>>([])
    const [extraSubmission, setExtraSubmission] = useState<{ id: number; label: string } | null>(null)
    // One permanently mounted status region: what the last row action did, in words made from codes and values.
    const [announcement, setAnnouncement] = useState("")
    // Asks for focus to return to a stable element once a row action has finished and the rows are re-read.
    const [focusRequest, setFocusRequest] = useState<{ taskIds: number[]; n: number } | null>(null)

    const isAdmin = state.status === "signed-in" && state.user.role === "admin"
    const myId = state.status === "signed-in" ? state.user.id : null

    // The view as of *now*, for code that resumes after an await. A write can land after the curator has changed a
    // filter, and reading the filter captured by the render that started it would re-read a view nobody is looking at.
    const filterRef = useRef(filter)
    useEffect(() => {
        filterRef.current = filter
    })
    const expandedRef = useRef(expanded)
    useEffect(() => {
        expandedRef.current = expanded
    })

    // Drops a response that a newer request has already superseded, so two quick filter changes cannot leave the
    // slower answer on screen under the faster one's heading.
    const requestSeq = useRef(0)

    function optionsFor(f: Filter): CuratorTaskListOptions {
        return {
            ...(f.state === "open" ? { stateGroup: "open" as const } : {}),
            ...(f.state === "closed" ? { stateGroup: "terminal" as const } : {}),
            ...(STATE_TOKENS.includes(f.state) ? { workflowStates: [f.state as CuratorTaskState] } : {}),
            ...(f.severity ? { highestSeverity: f.severity } : {}),
            ...(f.recordType ? { recordType: f.recordType } : {}),
            ...(f.recordType && f.recordId !== null ? { recordId: f.recordId } : {}),
            ...(f.submissionId !== null ? { submissionId: f.submissionId } : {}),
            ...(f.assignee === "me" && myId !== null ? { assignedTo: myId } : {}),
            ...(f.assignee !== "" && f.assignee !== "me" ? { assignedTo: Number(f.assignee) } : {}),
            limit: PAGE_LIMIT,
            offset: f.offset,
        }
    }

    const refresh = useCallback(async (options?: { keepRows?: boolean }) => {
        if (!options?.keepRows) setLoad({ status: "loading" })
        const seq = (requestSeq.current += 1)
        const current = filterRef.current
        try {
            const page = await listCuratorTasks(optionsFor(current))
            if (seq !== requestSeq.current) return
            setLoad({ status: "ready", tasks: page.items, total: page.total, unreadable: page.unreadable, offset: current.offset })
        } catch (caught) {
            if (seq !== requestSeq.current) return
            setLoad({
                status: "error",
                message: caught instanceof AuthApiError ? caught.message : "Could not load the machine findings.",
            })
        }
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [myId])

    // `keep` re-reads a detail already on screen without unmounting it, so a form inside it keeps its focus.
    const loadDetail = useCallback(async (taskId: number, keep = false) => {
        if (!keep) setDetails((current) => new Map(current).set(taskId, { status: "loading" }))
        try {
            const task = await getCuratorTask(taskId)
            setDetails((current) => new Map(current).set(taskId, { status: "ready", task }))
        } catch {
            if (keep) return
            setDetails((current) => new Map(current).set(taskId, { status: "error" }))
        }
    }, [])

    // The view is the address: one key stands for every filter and the offset.
    const viewKey = JSON.stringify(filter)
    // A page turn keeps the table and the paging buttons mounted while the next page loads, so keyboard focus stays
    // put. Any other change of view starts from the loading state.
    const baseKey = JSON.stringify({ ...filter, offset: 0 })
    const lastBaseKey = useRef(baseKey)
    useEffect(() => {
        const samePage = lastBaseKey.current === baseKey
        lastBaseKey.current = baseKey
        if (isAdmin) void refresh({ keepRows: samePage })
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [isAdmin, refresh, viewKey])

    useEffect(() => {
        if (focusRequest === null) return
        const active = document.activeElement
        if (active !== null && active !== document.body) return
        // The row itself, then the rows around it in reading order, then the heading: a row that has left the view
        // hands focus to its neighbour rather than to the top of the page.
        let target: HTMLElement | null = null
        for (const id of focusRequest.taskIds) {
            target = document.getElementById(`task-details-btn-${id}`)
            if (target !== null) break
        }
        ;(target ?? document.getElementById("queue-heading"))?.focus()
    }, [focusRequest])

    useEffect(() => {
        if (!isAdmin) return
        for (const id of expandedRef.current) void loadDetail(id)
    }, [isAdmin, loadDetail])

    useEffect(() => {
        if (!isAdmin) return
        let live = true
        listAdminUsers({ role: "admin", limit: 200 })
            .then((page) => live && setAdmins(page.items.map((u) => ({ id: u.id, username: u.username }))))
            .catch(() => {})
        listSubmissionMachineReviewOverview({ reviewed: true, limit: 200 })
            .then(
                (page) =>
                    live &&
                    setSubmissions(page.items.map((s) => ({ id: s.id, label: `${s.public_ref} · ${adminDate(s.submitted_at)}` }))),
            )
            .catch(() => {})
        return () => {
            live = false
        }
    }, [isAdmin])

    // A submission named in the address but outside the 200 offered is still selectable, labelled from the submission.
    useEffect(() => {
        const id = filter.submissionId
        if (!isAdmin || id === null || submissions.some((s) => s.id === id)) return
        let live = true
        fetchSubmission(id)
            .then((s) => live && setExtraSubmission({ id, label: `${s.public_ref} · ${adminDate(s.submitted_at)}` }))
            .catch(() => live && setExtraSubmission({ id, label: "this submission" }))
        return () => {
            live = false
        }
    }, [isAdmin, filter.submissionId, submissions])

    if (state.status === "loading") {
        return (
            <section className="admin-page">
                <h1>Machine findings</h1>
                <p role="status">Checking your account…</p>
            </section>
        )
    }
    if (state.status === "unreachable") {
        return (
            <section className="admin-page">
                <h1>Machine findings</h1>
                <p className="auth-error" role="alert">
                    The archive could not be reached, so your account could not be
                    checked. This is not a sign that you are signed out.
                </p>
            </section>
        )
    }
    if (state.status === "signed-out") return <Navigate to="/login" replace />
    if (!isAdmin) {
        return (
            <section className="admin-page">
                <h1>Machine findings</h1>
                <p className="auth-error" role="alert">
                    This queue is for administrators.
                </p>
            </section>
        )
    }

    /** Change filters. The page goes back to the first, and a task opened from a link is let go. */
    function setView(patch: Partial<Record<"state" | "severity" | "record_type" | "record" | "submission" | "assignee" | "offset", string | null>>) {
        const next = new URLSearchParams(params)
        for (const [key, value] of Object.entries(patch)) {
            if (value === null || value === "" || (key === "state" && value === "open") || (key === "offset" && value === "0")) next.delete(key)
            else next.set(key, value)
        }
        if (!("offset" in patch)) next.delete("offset")
        next.delete("task")
        setParams(next, { replace: true })
    }

    /** The address with the record filter dropped, for the chip's link. */
    function withoutRecord(): string {
        const next = new URLSearchParams(params)
        next.delete("record")
        next.delete("record_type")
        next.delete("task")
        next.delete("offset")
        const q = next.toString()
        return q ? `?${q}` : "?"
    }

    /** The address with closed tasks included and no linked task; every other filter stays. */
    function includeClosed(): string {
        const next = new URLSearchParams(params)
        next.set("state", "all")
        next.delete("task")
        next.delete("offset")
        return `?${next.toString()}`
    }

    /** Open the close form under one row, or shut it. Always starts blank. */
    function toggleResolve(taskId: number) {
        setReopenDraft(null)
        setDraft((current) =>
            current?.taskId === taskId ? null : { taskId, state: "resolved_no_action", note: "" },
        )
    }

    function toggleReopen(taskId: number) {
        setDraft(null)
        setReopenDraft((current) =>
            current?.taskId === taskId ? null : { taskId, target: "needs_curator_review", clearAssignment: false },
        )
    }

    function toggleDetails(taskId: number) {
        const isOpenNow = expanded.has(taskId)
        setExpanded((current) => {
            const next = new Set(current)
            if (isOpenNow) next.delete(taskId)
            else next.add(taskId)
            return next
        })
        if (!isOpenNow) void loadDetail(taskId)
    }

    function setRowError(taskId: number, message: string | null) {
        setRowErrors((current) => {
            const next = new Map(current)
            if (message === null) next.delete(taskId)
            else next.set(taskId, message)
            return next
        })
    }

    /** Re-read the list and every expanded detail, so nothing on screen predates the write. */
    async function reread() {
        await refresh({ keepRows: true })
        for (const id of expandedRef.current) void loadDetail(id, true)
    }

    /**
     * Run one row's write, then re-read the list.
     *
     * `closeForm` closes the form that sent the write once it has done its job. Only the form's own write does:
     * clearing the draft after a `start-review` threw away a reason the curator had already typed.
     */
    async function run(
        taskId: number,
        closeForm: (() => void) | null,
        action: () => Promise<unknown>,
        success: string,
    ) {
        if (busy.has(taskId)) return
        const order = (load.status === "ready" ? load.tasks : []).map((t) => t.id)
        setAnnouncement("")
        setBusy((current) => new Set(current).add(taskId))
        setRowError(taskId, null)
        try {
            await action()
            closeForm?.()
            setAnnouncement(success)
            // Re-read rather than patching the row from the reply: the server is the authority on whether the row
            // still belongs in the current view.
            await reread()
        } catch (caught) {
            const saved = caught instanceof CuratorTaskResponseError
            setAnnouncement("")
            setRowError(
                taskId,
                saved || caught instanceof AuthApiError
                    ? caught.message
                    : "That did not go through. Nothing was changed.",
            )
            // The write DID land; only its reply was unreadable. Leaving a filled-in form open beneath it invites the
            // curator to submit the same reason again.
            if (saved) closeForm?.()
            // Re-read on refusal too: a refusal is very often a state conflict, so the row on screen is the one that
            // is now wrong.
            await reread()
        } finally {
            setBusy((current) => {
                const next = new Set(current)
                next.delete(taskId)
                return next
            })
            const at = order.indexOf(taskId)
            const taskIds = [taskId, ...order.slice(at + 1), ...order.slice(0, Math.max(at, 0)).reverse()]
            setFocusRequest((current) => ({ taskIds, n: (current?.n ?? 0) + 1 }))
        }
    }

    const closeResolve = (taskId: number) => () =>
        setDraft((current) => (current?.taskId === taskId ? null : current))
    const closeReopen = (taskId: number) => () =>
        setReopenDraft((current) => (current?.taskId === taskId ? null : current))

    const tasks = load.status === "ready" ? load.tasks : []
    const chipRef = filter.recordType && filter.recordId !== null ? (tasks[0]?.record_public_ref ?? null) : null
    const submissionOptions =
        filter.submissionId !== null && !submissions.some((s) => s.id === filter.submissionId)
            ? [...submissions, extraSubmission?.id === filter.submissionId ? extraSubmission : { id: filter.submissionId, label: "this submission" }]
            : submissions
    const otherAdmins = admins.filter((a) => a.id !== myId)
    const linkedTask = positiveInt(params.get("task"))
    const linkedRecord = filter.recordType !== "" && filter.recordId !== null
    // A link from another page names a task or a record that this view may not hold, for instance a task closed since.
    const linkedMissing =
        load.status === "ready" &&
        ((linkedTask !== null && !load.tasks.some((t) => t.id === linkedTask)) || (linkedRecord && load.tasks.length === 0))
    // Only a view that leaves closed tasks out can be missing a task for that reason.
    const closedHidden = filter.state === "open" || (OPEN_STATES as readonly string[]).includes(filter.state)
    const resultText =
        load.status !== "ready"
            ? ""
            : load.tasks.length > 0
              ? `Showing ${load.offset + 1}–${load.offset + load.tasks.length} of ${load.total} tasks`
              : linkedMissing
                ? linkedTask !== null
                    ? "The linked task is not in this view."
                    : "The linked record has no tasks in this view."
                : filter.offset > 0
                  ? "Nothing further on this page. Go back for the earlier tasks."
                  : isDefaultView(filter)
                    ? "Nothing is waiting. No open curator tasks."
                    : "No tasks match these filters."

    // A record with no ref of its own is named by its kind and where it is shown.
    function taskForName(task: CuratorTask): string {
        if (task.record_public_ref) return task.record_public_ref
        const location = resolveRecordLocation(
            task.record_type,
            task.record_public_ref,
            task.container_type,
            task.container_ref,
        )
        return location.kind === "container"
            ? `${recordTypeWords(task.record_type)} on ${location.containerRef}`
            : recordTypeWords(task.record_type)
    }

    function renderDetail(task: CuratorTask) {
        const detail = details.get(task.id)
        const rowBusy = busy.has(task.id)
        return (
            <tr key={`detail-${task.id}`} className="admin-task-detail">
                <td colSpan={7}>
                    <div id={`task-detail-${task.id}`} role="region" aria-label={`Details for ${taskForName(task)}`}>
                        {(detail === undefined || detail.status === "loading") && <p role="status">Loading the details…</p>}
                        {detail?.status === "error" && (
                            <p className="auth-error" role="alert">Could not load this task&apos;s details.</p>
                        )}
                        {detail?.status === "ready" && (() => {
                            const t = detail.task
                            const match = t.finding_match
                            const matchWords = match === null ? null : wordsFor("findingMatch", match)
                            return (
                                <>
                                    <h3>Finding</h3>
                                    {t.finding !== null ? (
                                        <MachineFindingList findings={[t.finding]} />
                                    ) : (
                                        <p>
                                            {match === "not_found"
                                                ? matchWords?.meaning
                                                : "The finding could not be recovered from a stored review."}
                                        </p>
                                    )}
                                    <dl className="admin-facts">
                                        {matchWords !== null && (
                                            <>
                                                <dt>Matched</dt>
                                                <dd>{matchWords.label}</dd>
                                            </>
                                        )}
                                        <dt>Machine review</dt>
                                        <dd><MachineReviewStatusLabel status={t.machine_review_status} withMeaning /></dd>
                                        <dt>Created</dt>
                                        <dd>{adminDateTime(t.created_at)}</dd>
                                        <dt>Updated</dt>
                                        <dd>{adminDateTime(t.updated_at)}</dd>
                                        <dt>Assigned to</dt>
                                        <dd>{t.assigned_to_username ?? <span className="admin-absent">unassigned</span>}</dd>
                                        {!isOpen(t.workflow_state) && (
                                            <>
                                                <dt>Resolution</dt>
                                                <dd>{stateLabel(t.workflow_state)}</dd>
                                                <dt>Note</dt>
                                                <dd>{t.resolution_note ?? <span className="admin-absent">none was written</span>}</dd>
                                                <dt>Resolved by</dt>
                                                <dd>{t.resolved_by_username ?? <span className="admin-absent">not named</span>}</dd>
                                                <dt>Resolved</dt>
                                                <dd>{adminDateTime(t.resolved_at)}</dd>
                                            </>
                                        )}
                                    </dl>
                                    <p>
                                        <Link
                                            to={`/admin/machine-review-inspection?submission=${t.submission_id}`}
                                            target="_blank"
                                            rel="noopener noreferrer"
                                        >
                                            Open this submission&apos;s machine review
                                            <NewTabNote />
                                        </Link>
                                    </p>
                                    {isOpen(t.workflow_state) && (
                                        <form
                                            className="admin-resolve"
                                            onSubmit={(e) => {
                                                e.preventDefault()
                                                const chosen = assignDrafts.get(task.id) ?? String(t.assigned_to ?? "")
                                                void run(
                                                    task.id,
                                                    () =>
                                                        setAssignDrafts((current) => {
                                                            const next = new Map(current)
                                                            next.delete(task.id)
                                                            return next
                                                        }),
                                                    () => assignCuratorTask(task.id, chosen === "" ? null : Number(chosen)),
                                                    chosen === ""
                                                        ? "Task unassigned."
                                                        : `Task assigned to ${admins.find((a) => String(a.id) === chosen)?.username ?? "the chosen admin"}.`,
                                                )
                                            }}
                                        >
                                            <label htmlFor={`assign-${task.id}`}>Assign this task to</label>
                                            <select
                                                id={`assign-${task.id}`}
                                                className="admin-role-select"
                                                value={assignDrafts.get(task.id) ?? String(t.assigned_to ?? "")}
                                                onChange={(e) =>
                                                    setAssignDrafts((current) => new Map(current).set(task.id, e.target.value))
                                                }
                                            >
                                                <option value="">unassigned</option>
                                                {admins.map((a) => (
                                                    <option key={a.id} value={String(a.id)}>
                                                        {a.username}{a.id === myId ? " (you)" : ""}
                                                    </option>
                                                ))}
                                            </select>
                                            <button type="submit" aria-disabled={rowBusy ? "true" : undefined}>Save assignee</button>
                                        </form>
                                    )}
                                </>
                            )
                        })()}
                    </div>
                </td>
            </tr>
        )
    }

    return (
        <section className="admin-page">
            <h1 id="queue-heading" tabIndex={-1}>Machine findings</h1>
            <p className="admin-lede">
                Concerns an automated reviewer raised against deposited records,
                waiting for a person. These are <strong>advisory</strong>: nothing
                here endorses any science, changes what a reader is told to trust,
                or edits any record. Closing one means somebody dealt with the
                finding, never that the record is good. That judgement is{" "}
                <Link to="/review-queue">Record review</Link>, and it is made
                separately.
            </p>

            <div className="admin-filters">
                <div className="admin-filter">
                    <label htmlFor="queue-filter">State</label>{" "}
                    <select
                        id="queue-filter"
                        className="admin-role-select"
                        value={filter.state}
                        onChange={(e) => setView({ state: e.target.value })}
                    >
                        <option value="open">open tasks</option>
                        <option value="closed">closed tasks</option>
                        <option value="all">every task</option>
                        {OPEN_STATES.map((s) => (
                            <option key={s} value={s}>{stateLabel(s)}</option>
                        ))}
                        {TERMINAL_STATES.map((s) => (
                            <option key={s} value={s}>{stateLabel(s)}</option>
                        ))}
                    </select>
                </div>
                <div className="admin-filter">
                    <label htmlFor="queue-severity">Severity</label>{" "}
                    <select
                        id="queue-severity"
                        className="admin-role-select"
                        value={filter.severity}
                        onChange={(e) => setView({ severity: e.target.value })}
                    >
                        <option value="">any severity</option>
                        {KNOWN_SEVERITIES.map((s) => (
                            <option key={s} value={s}>{wordsFor("severity", s).label}</option>
                        ))}
                    </select>
                </div>
                <div className="admin-filter">
                    <label htmlFor="queue-record-type">Record type</label>{" "}
                    <select
                        id="queue-record-type"
                        className="admin-role-select"
                        value={filter.recordType}
                        onChange={(e) => setView({ record_type: e.target.value, record: null })}
                    >
                        <option value="">any record type</option>
                        {RECORD_TYPE_OPTIONS.map((o) => (
                            <option key={o.value} value={o.value}>{o.label}</option>
                        ))}
                    </select>
                </div>
                <div className="admin-filter">
                    <label htmlFor="queue-submission">Submission</label>{" "}
                    <select
                        id="queue-submission"
                        className="admin-role-select"
                        value={filter.submissionId === null ? "" : String(filter.submissionId)}
                        onChange={(e) => setView({ submission: e.target.value })}
                    >
                        <option value="">any submission</option>
                        {submissionOptions.map((s) => (
                            <option key={s.id} value={String(s.id)}>{s.label}</option>
                        ))}
                    </select>
                </div>
                <div className="admin-filter">
                    <label htmlFor="queue-assignee">Assignee</label>{" "}
                    <select
                        id="queue-assignee"
                        className="admin-role-select"
                        value={filter.assignee}
                        onChange={(e) => setView({ assignee: e.target.value })}
                    >
                        <option value="">anyone</option>
                        <option value="me">me</option>
                        {otherAdmins.map((a) => (
                            <option key={a.id} value={String(a.id)}>{a.username}</option>
                        ))}
                    </select>
                </div>
            </div>

            <p role="status" className="admin-visually-hidden">{announcement}</p>

            {filter.recordType && filter.recordId !== null && (
                <p className="admin-hint">
                    {chipRef !== null ? (
                        <>
                            Only <span className="data">{chipRef}</span>
                        </>
                    ) : (
                        "Only one record"
                    )}{" "}
                    <Link to={withoutRecord()}>Show all records</Link>
                </p>
            )}

            {/* Always mounted, so a count that arrives after a filter change is announced. */}
            <p
                role="status"
                className={!resultText ? "admin-visually-hidden" : load.status === "ready" && load.tasks.length > 0 ? "admin-count" : undefined}
            >
                {resultText}
            </p>
            {load.status === "ready" && load.tasks.length === 0 && linkedMissing && closedHidden && (
                <p className="admin-hint">
                    Closed tasks are hidden unless the state filter includes them.{" "}
                    <Link to={includeClosed()}>Include closed tasks</Link>
                </p>
            )}

            {load.status === "loading" && <p role="status">Loading the queue…</p>}
            {load.status === "error" && (
                <p className="auth-error" role="alert">{load.message}</p>
            )}

            {load.status === "ready" && load.unreadable > 0 && (
                <p className="auth-error" role="alert">
                    {load.unreadable} task{load.unreadable === 1 ? "" : "s"} on this
                    page could not be read and {load.unreadable === 1 ? "is" : "are"}{" "}
                    not shown. This page is likely older than the archive it is
                    talking to.
                </p>
            )}

            {load.status === "ready" && load.tasks.length > 0 && linkedTask !== null && linkedMissing && (
                <p className="admin-hint">
                    The linked task is not in this view.
                    {closedHidden && (
                        <>
                            {" "}
                            <Link to={includeClosed()}>Include closed tasks</Link>
                        </>
                    )}
                </p>
            )}

            {load.status === "ready" && load.tasks.length > 0 && (
                <>
                    <h2 className="admin-visually-hidden">Tasks</h2>
                    <div className="table-scroll admin-task-scroll">
                        <table className="data-table" aria-label="Curator tasks">
                            <thead>
                                <tr>
                                    <th scope="col">Record</th>
                                    <th scope="col">Severity</th>
                                    <th scope="col">Findings</th>
                                    <th scope="col">State</th>
                                    <th scope="col">Assignee</th>
                                    <th scope="col">Updated</th>
                                    <th scope="col">Action</th>
                                </tr>
                            </thead>
                            <tbody>
                                {load.tasks.flatMap((task) => {
                                    const location = resolveRecordLocation(
                                        task.record_type,
                                        task.record_public_ref,
                                        task.container_type,
                                        task.container_ref,
                                    )
                                    const rowBusy = busy.has(task.id)
                                    const severity = severityClass(task.highest_severity)
                                    const isExpanded = expanded.has(task.id)
                                    const severityWords = wordsFor("severity", task.highest_severity)
                                    const forName = taskForName(task)
                                    const forRecord = (
                                        <>{" "}<span className="admin-visually-hidden">{`for ${forName}`}</span></>
                                    )
                                    const row = (
                                        <tr key={task.id}>
                                            <td>
                                                <span className="admin-record-type">{recordTypeGroupLabel(task.record_type, 1)}</span>{" "}
                                                <RecordLocationCell location={location} />
                                            </td>
                                            <td>
                                                <span className={severity ?? undefined}>
                                                    {severityWords.known ? severityWords.label : <code>{task.highest_severity}</code>}
                                                </span>
                                            </td>
                                            <td>
                                                {task.findings_count}
                                                <MadeUnderLine madeUnder={task.made_under} kind="review" />
                                            </td>
                                            <td>{stateLabel(task.workflow_state)}</td>
                                            <td>
                                                {task.assigned_to_username ??
                                                    (task.assigned_to !== null ? (
                                                        <span className="admin-absent">not named</span>
                                                    ) : (
                                                        <span className="admin-absent">unassigned</span>
                                                    ))}
                                            </td>
                                            <td className="admin-date">{adminDate(task.updated_at)}</td>
                                            <td>
                                                <button
                                                    type="button"
                                                    id={`task-details-btn-${task.id}`}
                                                    aria-expanded={isExpanded}
                                                    aria-controls={isExpanded ? `task-detail-${task.id}` : undefined}
                                                    onClick={() => toggleDetails(task.id)}
                                                >
                                                    Details{forRecord}
                                                </button>{" "}
                                                {isOpen(task.workflow_state) ? (
                                                    <>
                                                        {task.workflow_state !== "in_curator_review" && (
                                                            <button
                                                                type="button"
                                                                aria-disabled={rowBusy ? "true" : undefined}
                                                                onClick={() =>
                                                                    void run(
                                                                        task.id,
                                                                        null,
                                                                        () => startCuratorTaskReview(task.id),
                                                                        "Review started.",
                                                                    )
                                                                }
                                                            >
                                                                Start review{forRecord}
                                                            </button>
                                                        )}{" "}
                                                        <button
                                                            type="button"
                                                            aria-disabled={rowBusy ? "true" : undefined}
                                                            aria-expanded={draft?.taskId === task.id}
                                                            aria-controls={`resolve-form-${task.id}`}
                                                            onClick={() => toggleResolve(task.id)}
                                                        >
                                                            Close…{forRecord}
                                                        </button>
                                                    </>
                                                ) : (
                                                    <button
                                                        type="button"
                                                        aria-disabled={rowBusy ? "true" : undefined}
                                                        aria-expanded={reopenDraft?.taskId === task.id}
                                                        aria-controls={`reopen-form-${task.id}`}
                                                        onClick={() => toggleReopen(task.id)}
                                                    >
                                                        Reopen…{forRecord}
                                                    </button>
                                                )}
                                            </td>
                                        </tr>
                                    )
                                    const hasForm =
                                        rowErrors.has(task.id) || draft?.taskId === task.id || reopenDraft?.taskId === task.id
                                    // Forms and refusals take the full table width, so opening one never changes a column's width.
                                    const formRow = hasForm ? (
                                        <tr key={`forms-${task.id}`} className="admin-task-detail">
                                            <td colSpan={7}>
                                                <div>
                                                    {rowErrors.has(task.id) && (
                                                        <p className="auth-error admin-row-error" role="alert">
                                                            {rowErrors.get(task.id)}
                                                        </p>
                                                    )}
                                                    {reopenDraft?.taskId === task.id && (
                                                        <form
                                                            id={`reopen-form-${task.id}`}
                                                            className="admin-resolve"
                                                            onSubmit={(e) => {
                                                                e.preventDefault()
                                                                void run(task.id, closeReopen(task.id), () =>
                                                                    reopenCuratorTask(task.id, {
                                                                        targetState: reopenDraft.target,
                                                                        clearAssignment: reopenDraft.clearAssignment,
                                                                    }),
                                                                    `Task reopened as ${stateLabel(reopenDraft.target)}.`,
                                                                )
                                                            }}
                                                        >
                                                            <label htmlFor={`reopen-as-${task.id}`}>Reopen as</label>
                                                            <select
                                                                id={`reopen-as-${task.id}`}
                                                                className="admin-role-select"
                                                                value={reopenDraft.target}
                                                                onChange={(e) =>
                                                                    setReopenDraft({
                                                                        ...reopenDraft,
                                                                        target: e.target.value as CuratorTaskState,
                                                                    })
                                                                }
                                                            >
                                                                {OPEN_STATES.map((s) => (
                                                                    <option key={s} value={s}>{stateLabel(s)}</option>
                                                                ))}
                                                            </select>
                                                            <label htmlFor={`reopen-clear-${task.id}`}>
                                                                <input
                                                                    id={`reopen-clear-${task.id}`}
                                                                    type="checkbox"
                                                                    checked={reopenDraft.clearAssignment}
                                                                    onChange={(e) =>
                                                                        setReopenDraft({
                                                                            ...reopenDraft,
                                                                            clearAssignment: e.target.checked,
                                                                        })
                                                                    }
                                                                />{" "}
                                                                Also clear the assignee
                                                            </label>
                                                            <button type="submit" aria-disabled={rowBusy ? "true" : undefined}>Reopen task</button>
                                                        </form>
                                                    )}
                                                    {draft?.taskId === task.id && (
                                                        <form
                                                            id={`resolve-form-${task.id}`}
                                                            className="admin-resolve"
                                                            onSubmit={(e) => {
                                                                e.preventDefault()
                                                                void run(task.id, closeResolve(task.id), () =>
                                                                    resolveCuratorTask(
                                                                        task.id,
                                                                        draft.state,
                                                                        draft.note,
                                                                    ),
                                                                    "Task closed.",
                                                                )
                                                            }}
                                                        >
                                                            <label htmlFor={`res-${task.id}`}>
                                                                How it was settled
                                                            </label>
                                                            <select
                                                                id={`res-${task.id}`}
                                                                className="admin-role-select"
                                                                value={draft.state}
                                                                onChange={(e) =>
                                                                    setDraft({
                                                                        ...draft,
                                                                        state: e.target
                                                                            .value as CuratorTaskState,
                                                                    })
                                                                }
                                                            >
                                                                {TERMINAL_STATES.map((s) => (
                                                                    <option key={s} value={s}>
                                                                        {stateLabel(s)}
                                                                    </option>
                                                                ))}
                                                            </select>
                                                            <p className="admin-hint">
                                                                {resolutionMeaning(draft.state)}
                                                            </p>
                                                            <label htmlFor={`note-${task.id}`}>
                                                                Why (required)
                                                            </label>
                                                            <textarea
                                                                id={`note-${task.id}`}
                                                                value={draft.note}
                                                                rows={2}
                                                                onChange={(e) =>
                                                                    setDraft({
                                                                        ...draft,
                                                                        note: e.target.value,
                                                                    })
                                                                }
                                                            />
                                                            <button
                                                                type="submit"
                                                                disabled={draft.note.trim().length === 0}
                                                                aria-disabled={rowBusy ? "true" : undefined}
                                                            >
                                                                Close task
                                                            </button>
                                                        </form>
                                                    )}
                                                </div>
                                            </td>
                                        </tr>
                                    ) : null
                                    return [row, ...(isExpanded ? [renderDetail(task)] : []), ...(formRow ? [formRow] : [])]
                                })}
                            </tbody>
                        </table>
                    </div>
                </>
            )}

            {load.status === "ready" && (load.tasks.length > 0 || filter.offset > 0) && (
                <div className="admin-paging">
                    <button
                        type="button"
                        aria-disabled={filter.offset === 0 ? "true" : undefined}
                        onClick={() =>
                            filter.offset > 0 && setView({ offset: String(Math.max(0, filter.offset - PAGE_LIMIT)) })
                        }
                    >
                        Previous
                    </button>{" "}
                    <button
                        type="button"
                        aria-disabled={filter.offset + PAGE_LIMIT >= load.total ? "true" : undefined}
                        onClick={() =>
                            filter.offset + PAGE_LIMIT < load.total &&
                            setView({ offset: String(filter.offset + PAGE_LIMIT) })
                        }
                    >
                        Next
                    </button>
                </div>
            )}
        </section>
    )
}
