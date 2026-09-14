import { useCallback, useEffect, useState } from "react"
import { Link, Navigate } from "react-router-dom"
import "../auth.css"
import "../admin.css"
import { AuthApiError } from "../api/authApi"
import {
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
 */

type LoadState =
    | { status: "loading" }
    | { status: "error"; message: string }
    | { status: "ready"; tasks: CuratorTask[]; total: number }

type RowBusy = { taskId: number; what: "start" | "resolve" | "reopen" } | null

const PAGE_LIMIT = 50

export default function CuratorQueuePage() {
    const { state } = useAuth()
    const [filter, setFilter] = useState<CuratorTaskState | "open" | "all">("open")
    const [load, setLoad] = useState<LoadState>({ status: "loading" })
    const [busy, setBusy] = useState<RowBusy>(null)
    const [rowError, setRowError] = useState<{ taskId: number; message: string } | null>(null)
    const [resolving, setResolving] = useState<number | null>(null)
    const [resolutionState, setResolutionState] =
        useState<CuratorTaskState>("resolved_no_action")
    const [note, setNote] = useState("")

    const isAdmin = state.status === "signed-in" && state.user.role === "admin"

    const refresh = useCallback(async () => {
        setLoad({ status: "loading" })
        try {
            // "open" is three states and the list route filters on one, so the
            // page asks for everything and narrows here. At PAGE_LIMIT rows
            // that is cheap; if the queue ever outgrows one page this becomes
            // a backend filter rather than client-side slicing of a partial
            // page, which would silently hide work.
            const page = await listCuratorTasks(
                filter === "open" || filter === "all"
                    ? { limit: PAGE_LIMIT }
                    : { workflowState: filter, limit: PAGE_LIMIT },
            )
            const tasks =
                filter === "open"
                    ? page.items.filter((t) => isOpen(t.workflow_state))
                    : page.items
            setLoad({ status: "ready", tasks, total: page.total })
        } catch (caught) {
            setLoad({
                status: "error",
                message:
                    caught instanceof AuthApiError
                        ? caught.message
                        : "Could not load the curator queue.",
            })
        }
    }, [filter])

    useEffect(() => {
        if (isAdmin) void refresh()
    }, [isAdmin, refresh])

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

    function replaceTask(updated: CuratorTask) {
        setLoad((current) => {
            if (current.status !== "ready") return current
            const tasks = current.tasks.map((t) => (t.id === updated.id ? updated : t))
            return {
                ...current,
                // Dropping a row that no longer matches the filter, rather than
                // leaving it sitting there in a state the filter excludes --
                // which reads as "my change did not take".
                tasks:
                    filter === "open"
                        ? tasks.filter((t) => isOpen(t.workflow_state))
                        : tasks,
            }
        })
    }

    async function run(
        taskId: number,
        what: "start" | "resolve" | "reopen",
        action: () => Promise<CuratorTask>,
    ) {
        setBusy({ taskId, what })
        setRowError(null)
        try {
            replaceTask(await action())
            if (what === "resolve") {
                setResolving(null)
                setNote("")
            }
        } catch (caught) {
            setRowError({
                taskId,
                message:
                    caught instanceof AuthApiError
                        ? caught.message
                        : "That did not go through. Nothing was changed.",
            })
        } finally {
            setBusy(null)
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
                        {load.tasks.length} shown of {load.total} in the queue
                    </p>
                    <div className="table-scroll">
                        <table className="data-table" aria-label="Curator tasks">
                            <thead>
                                <tr>
                                    <th scope="col">Record</th>
                                    <th scope="col">Severity</th>
                                    <th scope="col">Findings</th>
                                    <th scope="col">State</th>
                                    <th scope="col">Submission</th>
                                    <th scope="col">Action</th>
                                </tr>
                            </thead>
                            <tbody>
                                {load.tasks.map((task) => {
                                    const href = recordRoute(task.record_type, task.record_public_ref)
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
                                                <span className={`severity-${task.highest_severity}`}>
                                                    {task.highest_severity}
                                                </span>
                                            </td>
                                            <td>{task.findings_count}</td>
                                            <td>{stateLabel(task.workflow_state)}</td>
                                            <td>{task.submission_id}</td>
                                            <td>
                                                {isOpen(task.workflow_state) ? (
                                                    <>
                                                        {task.workflow_state !== "in_curator_review" && (
                                                            <button
                                                                type="button"
                                                                disabled={busy?.taskId === task.id}
                                                                onClick={() =>
                                                                    void run(task.id, "start", () =>
                                                                        startCuratorTaskReview(task.id),
                                                                    )
                                                                }
                                                            >
                                                                Start review
                                                            </button>
                                                        )}{" "}
                                                        <button
                                                            type="button"
                                                            disabled={busy?.taskId === task.id}
                                                            onClick={() =>
                                                                setResolving(
                                                                    resolving === task.id ? null : task.id,
                                                                )
                                                            }
                                                        >
                                                            Close…
                                                        </button>
                                                    </>
                                                ) : (
                                                    <button
                                                        type="button"
                                                        disabled={busy?.taskId === task.id}
                                                        onClick={() =>
                                                            void run(task.id, "reopen", () =>
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
                                                {resolving === task.id && (
                                                    <form
                                                        className="admin-resolve"
                                                        onSubmit={(e) => {
                                                            e.preventDefault()
                                                            void run(task.id, "resolve", () =>
                                                                resolveCuratorTask(
                                                                    task.id,
                                                                    resolutionState,
                                                                    note,
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
                                                            value={resolutionState}
                                                            onChange={(e) =>
                                                                setResolutionState(
                                                                    e.target.value as CuratorTaskState,
                                                                )
                                                            }
                                                        >
                                                            {TERMINAL_STATES.map((s) => (
                                                                <option key={s} value={s}>
                                                                    {stateLabel(s)}
                                                                </option>
                                                            ))}
                                                        </select>
                                                        <p className="admin-hint">
                                                            {resolutionMeaning(resolutionState)}
                                                        </p>
                                                        <label htmlFor={`note-${task.id}`}>
                                                            Why (required)
                                                        </label>
                                                        <textarea
                                                            id={`note-${task.id}`}
                                                            value={note}
                                                            rows={2}
                                                            onChange={(e) => setNote(e.target.value)}
                                                        />
                                                        <button
                                                            type="submit"
                                                            disabled={
                                                                note.trim().length === 0 ||
                                                                busy?.taskId === task.id
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
