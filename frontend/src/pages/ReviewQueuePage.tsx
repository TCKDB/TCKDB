import { useCallback, useEffect, useRef, useState } from "react"
import { Link, Navigate } from "react-router-dom"
import "../auth.css"
import "../admin.css"
import { AuthApiError } from "../api/authApi"
import {
    RecordReviewResponseError,
    listRecordReviews,
    setRecordReviewStatus,
} from "../api/recordReviewsApi"
import { recordRoute } from "../domain/recordRoute"
import { useAuth } from "../hooks/useAuth"
import {
    ALL_STATUSES,
    allowedTransitions,
    statusClass,
    statusLabel,
    statusMeaning,
    type RecordReview,
    type RecordReviewStatus,
} from "../types/recordReview"

/**
 * The review queue: every record, and whether a person has judged it.
 *
 * This is the **authoritative** review axis. Approving here changes what
 * a reader is told to trust, which is exactly what the curator queue
 * (`/admin/curator-queue`) does not do -- that one triages a machine's
 * advisory findings and endorses nothing. Two queues, two meanings; the
 * lede says which this is, because "review" alone does not.
 *
 * Unlike the curator queue, nothing has to be run to fill this one. A
 * `record_review` row is written for every record by the review-policy
 * write that ends every upload, starting at `not_reviewed`. The default
 * filter is therefore the backlog: everything nobody has looked at.
 *
 * ## What this page cannot tell you
 *
 * How big the backlog is. The route answers with a bare array and no
 * total, so a full page means "there may be more" and nothing stronger.
 *
 * Nor is paging a stable walk: the list is newest-first and paging is by
 * offset, so reviewing rows on one page shifts the boundary and the next
 * page skips as many as were moved out of the filter. Nothing is lost --
 * the skipped rows are still on page one -- but "Older until empty" is
 * not a way to be sure you have seen everything.
 * Reporting a real count needs a breaking wire change or a second route
 * (task #257). Saying "50 shown" and implying that is all of it would be
 * the worse failure, so the page says explicitly when it is full.
 */

type LoadState =
    | { status: "loading" }
    | { status: "error"; message: string }
    | { status: "ready"; rows: RecordReview[]; unreadable: number; full: boolean }

/** The open transition form, for the one row it belongs to. */
type Draft = { rowId: number; status: RecordReviewStatus; note: string }

/**
 * A refusal, carrying enough to name its record after the row is gone.
 *
 * The commonest refusal is a state conflict -- somebody else moved the
 * record -- and the re-read that follows drops the row out of the
 * default `not_reviewed` view. Keyed only by row id and rendered only
 * inside the table, the message then vanished together with the row: the
 * curator pressed the button, the row disappeared, and nothing was ever
 * said. Carrying the label lets the message survive its row.
 */
type RowError = { message: string; recordLabel: string }

const PAGE_LIMIT = 50

/** A stable key for a row: the review row's own id. */
function keyOf(row: RecordReview): number {
    return row.id
}

export default function ReviewQueuePage() {
    const { state } = useAuth()
    const [statusFilter, setStatusFilter] = useState<RecordReviewStatus | "all">(
        "not_reviewed",
    )
    const [offset, setOffset] = useState(0)
    const [load, setLoad] = useState<LoadState>({ status: "loading" })
    const [busy, setBusy] = useState<ReadonlySet<number>>(new Set())
    const [rowErrors, setRowErrors] = useState<ReadonlyMap<number, RowError>>(new Map())
    const [draft, setDraft] = useState<Draft | null>(null)

    const role = state.status === "signed-in" ? state.user.role : null
    const canReview = role === "curator" || role === "admin"

    // The filter as of *now*, for code that resumes after an await: a
    // transition can land after the curator has changed the view, and
    // re-reading under the filter captured when the write started would
    // refresh a list nobody is looking at.
    const filterRef = useRef(statusFilter)
    useEffect(() => {
        filterRef.current = statusFilter
    }, [statusFilter])

    const offsetRef = useRef(offset)
    useEffect(() => {
        offsetRef.current = offset
    }, [offset])

    // A refusal is about a row in a view. Change the view and it is no
    // longer about anything the curator is looking at -- left in place it
    // followed them from filter to filter announcing that a row they never
    // saw "is no longer in this view", which is both wrong and unclosable.
    useEffect(() => {
        setRowErrors(new Map())
    }, [statusFilter, offset])

    const requestSeq = useRef(0)

    const refresh = useCallback(async (options?: { keepRows?: boolean }) => {
        if (!options?.keepRows) setLoad({ status: "loading" })
        const seq = (requestSeq.current += 1)
        const current = filterRef.current
        try {
            const page = await listRecordReviews({
                ...(current === "all" ? {} : { status: current }),
                limit: PAGE_LIMIT,
                offset: offsetRef.current,
            })
            if (seq !== requestSeq.current) return
            setLoad({
                status: "ready",
                rows: page.items,
                unreadable: page.unreadable,
                full: page.full,
            })
        } catch (caught) {
            if (seq !== requestSeq.current) return
            setLoad({
                status: "error",
                message:
                    caught instanceof AuthApiError
                        ? caught.message
                        : "Could not load the review queue.",
            })
        }
    }, [])

    useEffect(() => {
        if (canReview) void refresh()
    }, [canReview, refresh, statusFilter, offset])

    if (state.status === "loading") {
        return (
            <section className="admin-page">
                <h1>Review queue</h1>
                <p role="status">Checking your account…</p>
            </section>
        )
    }
    if (state.status === "unreachable") {
        return (
            <section className="admin-page">
                <h1>Review queue</h1>
                <p className="auth-error" role="alert">
                    The archive could not be reached, so your account could not be
                    checked. This is not a sign that you are signed out.
                </p>
            </section>
        )
    }
    if (state.status === "signed-out") return <Navigate to="/login" replace />
    if (!canReview) {
        return (
            <section className="admin-page">
                <h1>Review queue</h1>
                <p className="auth-error" role="alert">
                    This queue is for curators. Reviewing a record changes what every
                    reader is told to trust about it.
                </p>
            </section>
        )
    }

    function setRowError(rowId: number, error: RowError | null) {
        setRowErrors((current) => {
            const next = new Map(current)
            if (error === null) next.delete(rowId)
            else next.set(rowId, error)
            return next
        })
    }

    /** How to name a record in a message that may outlive its row. */
    function labelOf(row: RecordReview): string {
        return row.record_public_ref ?? `${row.record_type} (unnamed)`
    }

    /**
     * The transitions this curator can actually perform on this row.
     *
     * The service refuses to let an actor approve a record they
     * deposited (`record_review.py`'s self-approval guard), and the page
     * holds both facts -- `created_by` on the row and the signed-in
     * user's id. Offering "approved" there is a button that can only
     * fail, which is the very thing mirroring the transition table was
     * for.
     */
    function offeredTransitions(row: RecordReview): readonly RecordReviewStatus[] {
        const all = allowedTransitions(row.status)
        const ownDeposit =
            state.status === "signed-in" &&
            row.created_by !== null &&
            row.created_by === state.user.id
        return ownDeposit ? all.filter((s) => s !== "approved") : all
    }

    /** Open the transition form for one row, or shut it. Always starts blank. */
    function toggleDraft(row: RecordReview) {
        const rowId = keyOf(row)
        const first = offeredTransitions(row)[0]
        setDraft((current) =>
            current?.rowId === rowId || first === undefined
                ? null
                : { rowId, status: first, note: "" },
        )
    }

    async function submit(row: RecordReview, draftNow: Draft) {
        const rowId = keyOf(row)
        setBusy((current) => new Set(current).add(rowId))
        setRowError(rowId, null)
        try {
            await setRecordReviewStatus({
                recordType: row.record_type,
                recordId: row.record_id,
                status: draftNow.status,
                note: draftNow.note.trim() || undefined,
            })
            setDraft((current) => (current?.rowId === rowId ? null : current))
            await refresh({ keepRows: true })
        } catch (caught) {
            const saved = caught instanceof RecordReviewResponseError
            setRowError(rowId, {
                message:
                    saved || caught instanceof AuthApiError
                        ? caught.message
                        : "That did not go through. Nothing was changed.",
                recordLabel: labelOf(row),
            })
            if (saved) {
                setDraft((current) => (current?.rowId === rowId ? null : current))
            }
            // A refusal is usually a disallowed transition or a
            // self-approval block, both of which mean the row on screen
            // may already disagree with the server. Re-read rather than
            // leave a stale status under a live control.
            await refresh({ keepRows: true })
        } finally {
            setBusy((current) => {
                const next = new Set(current)
                next.delete(rowId)
                return next
            })
        }
    }

    return (
        <section className="admin-page">
            <h1>Review queue</h1>
            <p className="admin-lede">
                Every record in the archive, and whether a person has judged it. A
                review row exists for each one from the moment it is deposited, so
                this queue needs nothing run to fill it. Unlike the{" "}
                <Link to="/admin/curator-queue">curator queue</Link>, which triages a
                machine&apos;s advisory findings,{" "}
                <strong>approving here changes what every reader is told to trust</strong>.
            </p>

            <div className="admin-filter">
                <label htmlFor="review-filter">Showing</label>{" "}
                <select
                    id="review-filter"
                    className="admin-role-select"
                    value={statusFilter}
                    onChange={(e) => {
                        // Back to the first page: an offset carried over
                        // from a longer view lands mid-way through a
                        // shorter one, showing a curator page three of a
                        // two-page list and calling it empty.
                        setOffset(0)
                        setStatusFilter(e.target.value as RecordReviewStatus | "all")
                    }}
                >
                    <option value="all">every record</option>
                    {ALL_STATUSES.map((s) => (
                        <option key={s} value={s}>
                            {statusLabel(s)}
                        </option>
                    ))}
                </select>
            </div>

            {load.status === "loading" && <p role="status">Loading the queue…</p>}
            {load.status === "error" && (
                <p className="auth-error" role="alert">
                    {load.message}
                </p>
            )}

            {load.status === "ready" &&
                [...rowErrors.entries()]
                    .filter(([rowId]) => !load.rows.some((r) => keyOf(r) === rowId))
                    .map(([rowId, error]) => (
                        // The row this refusal was about is no longer in
                        // view -- almost always because the re-read that
                        // followed found somebody else had moved it out of
                        // this filter. Saying nothing here is how a curator
                        // presses a button, watches a row vanish, and never
                        // learns why.
                        <p key={rowId} className="auth-error" role="alert">
                            {error.recordLabel}: {error.message} It is no longer
                            in this view.
                        </p>
                    ))}

            {load.status === "ready" && load.unreadable > 0 && (
                <p className="auth-error" role="alert">
                    {load.unreadable} row{load.unreadable === 1 ? "" : "s"} on this
                    page could not be read and {load.unreadable === 1 ? "is" : "are"}{" "}
                    not shown. This page is likely older than the archive it is
                    talking to.
                </p>
            )}

            {load.status === "ready" && load.rows.length === 0 && (
                <p role="status">
                    {offset > 0
                        ? "Nothing older than this. Go back for the newer rows."
                        : statusFilter === "not_reviewed"
                          ? "Nothing is waiting. Every record has been looked at."
                          : "No records match this filter."}
                </p>
            )}

            {load.status === "ready" && load.rows.length > 0 && (
                <>
                    <p className="admin-count" role="status">
                        {load.rows.length} shown
                        {offset > 0 ? `, from ${offset + 1}` : ""}
                        {load.full
                            ? ", a full page — there may be more, and this page cannot say how many"
                            : ""}
                    </p>
                    <div className="table-scroll">
                        <table className="data-table" aria-label="Record reviews">
                            <thead>
                                <tr>
                                    <th scope="col">Record</th>
                                    <th scope="col">Review state</th>
                                    <th scope="col">Reason given</th>
                                    <th scope="col">Action</th>
                                </tr>
                            </thead>
                            <tbody>
                                {load.rows.map((row) => {
                                    const rowId = keyOf(row)
                                    const href = recordRoute(
                                        row.record_type,
                                        row.record_public_ref,
                                    )
                                    const rowBusy = busy.has(rowId)
                                    const options = offeredTransitions(row)
                                    // What the form will actually send. A
                                    // re-read can change a row's status
                                    // under an open form, leaving the held
                                    // choice no longer among the options --
                                    // and a controlled <select> whose value
                                    // matches nothing displays the FIRST
                                    // option. The select then showed one
                                    // state, the hint described it, and
                                    // submitting sent a third. Deriving all
                                    // three from one value removes the
                                    // disagreement rather than papering it.
                                    const chosen =
                                        draft?.rowId === rowId &&
                                        options.includes(draft.status)
                                            ? draft.status
                                            : options[0]
                                    // No status this page can render has an
                                    // empty transition set, but rendering a
                                    // form around `undefined` would submit
                                    // one, so the form is gated on having
                                    // something to submit.
                                    const canSubmit = chosen !== undefined
                                    const cls = statusClass(row.status)
                                    return (
                                        <tr key={rowId}>
                                            <td>
                                                <span className="admin-record-type">
                                                    {row.record_type}
                                                </span>{" "}
                                                {href !== null ? (
                                                    <Link to={href} className="data">
                                                        {row.record_public_ref}
                                                    </Link>
                                                ) : row.record_public_ref !== null ? (
                                                    <span className="data">
                                                        {row.record_public_ref}
                                                    </span>
                                                ) : (
                                                    <span className="admin-absent">
                                                        cannot be named
                                                    </span>
                                                )}
                                            </td>
                                            <td>
                                                <span className={cls ?? undefined}>
                                                    {statusLabel(row.status)}
                                                </span>
                                            </td>
                                            <td>
                                                {row.note ? (
                                                    row.note
                                                ) : (
                                                    <span className="admin-absent">
                                                        none
                                                    </span>
                                                )}
                                            </td>
                                            <td>
                                                {options.length > 0 ? (
                                                    <button
                                                        type="button"
                                                        disabled={rowBusy}
                                                        aria-expanded={draft?.rowId === rowId}
                                                        aria-controls={`review-form-${rowId}`}
                                                        onClick={() => toggleDraft(row)}
                                                    >
                                                        Review…
                                                    </button>
                                                ) : (
                                                    <span className="admin-absent">
                                                        no transition available
                                                    </span>
                                                )}
                                                {rowErrors.has(rowId) && (
                                                    <p
                                                        className="auth-error admin-row-error"
                                                        role="alert"
                                                    >
                                                        {rowErrors.get(rowId)?.message}
                                                    </p>
                                                )}
                                                {draft?.rowId === rowId && canSubmit && (
                                                    <form
                                                        id={`review-form-${rowId}`}
                                                        className="admin-resolve"
                                                        onSubmit={(e) => {
                                                            e.preventDefault()
                                                            void submit(row, {
                                                                ...draft,
                                                                status: chosen,
                                                            })
                                                        }}
                                                    >
                                                        <label htmlFor={`st-${rowId}`}>
                                                            New review state
                                                        </label>
                                                        <select
                                                            id={`st-${rowId}`}
                                                            className="admin-role-select"
                                                            value={chosen}
                                                            onChange={(e) =>
                                                                setDraft({
                                                                    ...draft,
                                                                    status: e.target
                                                                        .value as RecordReviewStatus,
                                                                })
                                                            }
                                                        >
                                                            {options.map((s) => (
                                                                <option key={s} value={s}>
                                                                    {statusLabel(s)}
                                                                </option>
                                                            ))}
                                                        </select>
                                                        <p className="admin-hint">
                                                            {statusMeaning(chosen)}
                                                        </p>
                                                        <label htmlFor={`note-${rowId}`}>
                                                            Why (required)
                                                        </label>
                                                        <textarea
                                                            id={`note-${rowId}`}
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
                                                            Record this judgement
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

            {load.status === "ready" && (load.rows.length > 0 || offset > 0) && (
                <div className="admin-paging">
                    {/* Rendered even when the page came back empty. Inside
                        the rows-present branch, clicking past the end of a
                        list whose length is a multiple of the page size
                        left a curator on "Every record has been looked at"
                        with no control to get back -- the page's own worst
                        failure, one click away. */}
                    <button
                        type="button"
                        disabled={offset === 0}
                        onClick={() => setOffset(Math.max(0, offset - PAGE_LIMIT))}
                    >
                        Newer
                    </button>{" "}
                    <button
                        type="button"
                        disabled={!load.full}
                        onClick={() => setOffset(offset + PAGE_LIMIT)}
                    >
                        Older
                    </button>
                </div>
            )}
        </section>
    )
}
