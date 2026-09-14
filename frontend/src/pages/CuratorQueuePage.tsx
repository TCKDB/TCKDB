import { useCallback, useEffect, useRef, useState } from "react"
import { Link, Navigate } from "react-router-dom"
import "../auth.css"
import "../admin.css"
import { AuthApiError } from "../api/authApi"
import {
    CuratorTaskResponseError,
    listCuratorTasks,
    reopenCuratorTask,
    resolveCuratorTask,
    startCuratorTaskReview,
} from "../api/curatorTasksApi"
import { recordRoute } from "../domain/recordRoute"
import { useAuth } from "../hooks/useAuth"
import {
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
 * The curator queue: machine-review findings waiting on a person.
 *
 * Eight backend routes have served this queue since before there was
 * anywhere to see it. This is that place.
 *
 * **What this surface is not.** It is not record review. Nothing here
 * endorses any science, writes `record_review`, or changes what a reader
 * is told to trust. Resolving a task says a person dealt with a *finding*;
 * `resolved_human_reviewed` notes that a review happened elsewhere and
 * does not itself perform one. That separation is the whole design (ADR
 * 0016), and it is stated on the page rather than only in a doc, because
 * a queue with a button labelled "resolve" invites exactly the wrong
 * reading.
 *
 * Filtering defaults to open tasks. A curator opening this wants the work,
 * not the history; terminal states are one click away and are labelled
 * with what each of them asserts.
 *
 * ## Per-row state, and why none of it is per-page
 *
 * Everything a row owns is keyed by task id: which row's close form is
 * open, the note typed into it, the state chosen in it, which rows have a
 * write in flight, and which row is showing a refusal. An earlier draft
 * held the note and the chosen state as single page-level values, and
 * that is not a tidiness problem -- it silently attaches one record's
 * written justification to a different record's audit trail. Open the
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
    | { status: "ready"; tasks: CuratorTask[]; total: number; unreadable: number }

/** The close form's contents, for the one row it is open under. */
type ResolveDraft = { taskId: number; state: CuratorTaskState; note: string }

const PAGE_LIMIT = 50

export default function CuratorQueuePage() {
    const { state } = useAuth()
    const [filter, setFilter] = useState<CuratorTaskState | "open" | "all">("open")
    const [load, setLoad] = useState<LoadState>({ status: "loading" })
    const [busy, setBusy] = useState<ReadonlySet<number>>(new Set())
    const [rowError, setRowError] = useState<{ taskId: number; message: string } | null>(null)
    const [draft, setDraft] = useState<ResolveDraft | null>(null)

    const isAdmin = state.status === "signed-in" && state.user.role === "admin"

    // The filter as of *now*, for code that resumes after an await. A write
    // can land after the curator has changed the filter, and reading the
    // filter captured by the render that started it would then re-filter
    // the list by a view nobody is looking at any more.
    const filterRef = useRef(filter)
    useEffect(() => {
        filterRef.current = filter
    }, [filter])

    // Drops a response that a newer request has already superseded, so two
    // quick filter changes cannot leave the slower answer on screen under
    // the faster one's heading.
    const requestSeq = useRef(0)

    const refresh = useCallback(async (options?: { keepRows?: boolean }) => {
        if (!options?.keepRows) setLoad({ status: "loading" })
        const seq = (requestSeq.current += 1)
        const current = filterRef.current
        try {
            // "open" is three states and the list route filters on one, so the
            // page asks for everything and narrows here. At PAGE_LIMIT rows
            // that is cheap; if the queue ever outgrows one page this becomes
            // a backend filter rather than client-side slicing of a partial
            // page, which would silently hide work.
            const page = await listCuratorTasks(
                current === "open" || current === "all"
                    ? { limit: PAGE_LIMIT }
                    : { workflowState: current, limit: PAGE_LIMIT },
            )
            if (seq !== requestSeq.current) return
            const tasks =
                current === "open"
                    ? page.items.filter((t) => isOpen(t.workflow_state))
                    : page.items
            setLoad({
                status: "ready",
                tasks,
                total: page.total,
                unreadable: page.unreadable,
            })
        } catch (caught) {
            if (seq !== requestSeq.current) return
            setLoad({
                status: "error",
                message:
                    caught instanceof AuthApiError
                        ? caught.message
                        : "Could not load the curator queue.",
            })
        }
    }, [])

    useEffect(() => {
        if (isAdmin) void refresh()
    }, [isAdmin, refresh, filter])

    if (state.status === "loading") {
        return (
            <section className="admin-page">
                <h1>Curator queue</h1>
                <p role="status">Checking your account…</p>
            </section>
        )
    }
    if (state.status === "unreachable") {
        return (
            <section className="admin-page">
                <h1>Curator queue</h1>
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
                <h1>Curator queue</h1>
                <p className="auth-error" role="alert">
                    This queue is for administrators.
                </p>
            </section>
        )
    }

    /** Open the close form under one row, or shut it. Always starts blank. */
    function toggleResolve(taskId: number) {
        setDraft((current) =>
            current?.taskId === taskId
                ? null
                : { taskId, state: "resolved_no_action", note: "" },
        )
    }

    async function run(taskId: number, action: () => Promise<unknown>) {
        setBusy((current) => new Set(current).add(taskId))
        setRowError(null)
        try {
            await action()
            setDraft((current) => (current?.taskId === taskId ? null : current))
            // Re-read the list rather than patching the row from the reply.
            // The server is the authority on whether the row still belongs
            // in the current view, and this keeps that judgement in one
            // place instead of duplicating the filter logic at the write
            // site -- where it was, and where it used a stale filter.
            await refresh({ keepRows: true })
        } catch (caught) {
            setRowError({
                taskId,
                message:
                    caught instanceof CuratorTaskResponseError ||
                    caught instanceof AuthApiError
                        ? caught.message
                        : "That did not go through. Nothing was changed.",
            })
        } finally {
            setBusy((current) => {
                const next = new Set(current)
                next.delete(taskId)
                return next
            })
        }
    }

    return (
        <section className="admin-page">
            <h1>Curator queue</h1>
            <p className="admin-lede">
                Findings a machine review raised against deposited records, waiting
                for a person. These are <strong>advisory</strong>: nothing here
                approves science, changes what a reader is told to trust, or edits
                any record. Closing a task records that somebody dealt with the
                finding.
            </p>

            <div className="admin-filter">
                <label htmlFor="queue-filter">Showing</label>{" "}
                <select
                    id="queue-filter"
                    className="admin-role-select"
                    value={filter}
                    onChange={(e) => setFilter(e.target.value as CuratorTaskState | "open" | "all")}
                >
                    <option value="open">open tasks</option>
                    <option value="all">every task</option>
                    {OPEN_STATES.map((s) => (
                        <option key={s} value={s}>{stateLabel(s)}</option>
                    ))}
                    {TERMINAL_STATES.map((s) => (
                        <option key={s} value={s}>{stateLabel(s)}</option>
                    ))}
                </select>
            </div>

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

            {load.status === "ready" && load.tasks.length === 0 && (
                <p role="status">
                    {filter === "open"
                        ? "Nothing is waiting. No open curator tasks."
                        : "No tasks match this filter."}
                </p>
            )}

            {load.status === "ready" && load.tasks.length > 0 && (
                <>
                    <p className="admin-count" role="status">
                        {/* Under "open" the backend counted every task including
                            closed ones, so "N of TOTAL" would compare a filtered
                            count against an unfiltered one and read as though
                            work were missing. Only the exact-state filters ask
                            the backend to count the same thing the table shows. */}
                        {filter === "open" || filter === "all"
                            ? `${load.tasks.length} shown`
                            : `${load.tasks.length} shown of ${load.total} in this state`}
                        {load.tasks.length === PAGE_LIMIT
                            ? `, the most this page loads at once — there may be more`
                            : ""}
                    </p>
                    <div className="table-scroll">
                        <table className="data-table" aria-label="Curator tasks">
                            <thead>
                                <tr>
                                    <th scope="col">Record</th>
                                    <th scope="col">Severity</th>
                                    <th scope="col">Findings</th>
                                    <th scope="col">State</th>
                                    <th scope="col">Action</th>
                                </tr>
                            </thead>
                            <tbody>
                                {load.tasks.map((task) => {
                                    const href = recordRoute(task.record_type, task.record_public_ref)
                                    const rowBusy = busy.has(task.id)
                                    const severity = severityClass(task.highest_severity)
                                    return (
                                        <tr key={task.id}>
                                            <td>
                                                <span className="admin-record-type">{task.record_type}</span>{" "}
                                                {href !== null ? (
                                                    <Link to={href} className="data">
                                                        {task.record_public_ref}
                                                    </Link>
                                                ) : task.record_public_ref !== null ? (
                                                    <span className="data">{task.record_public_ref}</span>
                                                ) : (
                                                    <span className="admin-absent">
                                                        cannot be named
                                                    </span>
                                                )}
                                            </td>
                                            <td>
                                                <span className={severity ?? undefined}>
                                                    {task.highest_severity}
                                                </span>
                                            </td>
                                            <td>{task.findings_count}</td>
                                            <td>{stateLabel(task.workflow_state)}</td>
                                            <td>
                                                {isOpen(task.workflow_state) ? (
                                                    <>
                                                        {task.workflow_state !== "in_curator_review" && (
                                                            <button
                                                                type="button"
                                                                disabled={rowBusy}
                                                                onClick={() =>
                                                                    void run(task.id, () =>
                                                                        startCuratorTaskReview(task.id),
                                                                    )
                                                                }
                                                            >
                                                                Start review
                                                            </button>
                                                        )}{" "}
                                                        <button
                                                            type="button"
                                                            disabled={rowBusy}
                                                            aria-expanded={draft?.taskId === task.id}
                                                            aria-controls={`resolve-form-${task.id}`}
                                                            onClick={() => toggleResolve(task.id)}
                                                        >
                                                            Close…
                                                        </button>
                                                    </>
                                                ) : (
                                                    <button
                                                        type="button"
                                                        disabled={rowBusy}
                                                        onClick={() =>
                                                            void run(task.id, () =>
                                                                reopenCuratorTask(task.id),
                                                            )
                                                        }
                                                    >
                                                        Reopen
                                                    </button>
                                                )}
                                                {rowError?.taskId === task.id && (
                                                    <p className="auth-error admin-row-error" role="alert">
                                                        {rowError.message}
                                                    </p>
                                                )}
                                                {draft?.taskId === task.id && (
                                                    <form
                                                        id={`resolve-form-${task.id}`}
                                                        className="admin-resolve"
                                                        onSubmit={(e) => {
                                                            e.preventDefault()
                                                            void run(task.id, () =>
                                                                resolveCuratorTask(
                                                                    task.id,
                                                                    draft.state,
                                                                    draft.note,
                                                                ),
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
                                                            disabled={
                                                                draft.note.trim().length === 0 ||
                                                                rowBusy
                                                            }
                                                        >
                                                            Close task
                                                        </button>
                                                    </form>
                                                )}
                                            </td>
                                        </tr>
                                    )
                                })}
                            </tbody>
                        </table>
                    </div>
                </>
            )}
        </section>
    )
}
