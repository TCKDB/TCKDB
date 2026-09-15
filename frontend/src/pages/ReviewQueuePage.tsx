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
import {
    recordTypeWords,
    resolveRecordLocation,
    type RecordLocation,
} from "../domain/recordRoute"
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
 * Record review: every record, and whether a person has judged it.
 *
 * Named "Record review" on screen, not "Review queue". The owner could
 * not tell this page from the other one at a glance, and he was right
 * that the names were the defect: "Review queue" and "Curator queue"
 * are two arbitrary labels for two things that differ in what they
 * ASSERT, not in who opens them. The pair now says what each is about --
 * this one is about records, the other (`/admin/curator-queue`, shown as
 * "Machine findings") is about what a machine flagged.
 *
 * This is the **authoritative** review axis, and approving is a one-way
 * door for the data even though the review status is not.
 *
 * MEASURED, because an earlier draft of this page's lede said the opposite
 * and it was wrong in the direction that matters. `_ALLOWED_TRANSITIONS`
 * does permit `approved -> under_review` and `approved -> deprecated`, so
 * the STATUS can be reopened -- but `set_record_review_status` stamps
 * `first_approved_at` only when it is null and CLEARS it nowhere (one
 * writer in `app/`, plus a one-off migration backfill), and the deployed
 * `tckdb_record_is_accepted` is exactly
 * `EXISTS (... WHERE record_type = $1 AND record_id = $2 AND
 * first_approved_at IS NOT NULL)`. So the ADR 0003 freeze keys off a
 * column that only ever goes from null to set: reopening the review does
 * not thaw the record, and ADR 0015's repair ledger is the only way back.
 *
 * A reviewer is entitled to know that before they click approve, which is
 * why the lede says it rather than only this comment.
 *
 * Approving here changes what
 * a reader is told to trust, which is exactly what Machine findings does
 * not do -- that one triages a machine's advisory findings and endorses
 * nothing. Two queues, two meanings; the lede says which this is,
 * because "review" alone does not.
 *
 * The route is still `/review-queue`, and deliberately so: a rename of
 * the words a reader sees is not a reason to break every bookmark and
 * every link already sent to a curator. The file, the component and the
 * query keys keep their old names for the same reason -- this was a
 * wording defect, not a modelling one.
 *
 * Unlike Machine findings, nothing has to be run to fill this one. A
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

/**
 * Every link out of this queue opens in a new tab.
 *
 * This is deliberately against the usual advice, which is that a page
 * should not decide how a link opens. It is made here because the queue is
 * **stateful in a way the URL does not capture**: the status filter, the
 * page offset, which rows have an open form, and -- the one that actually
 * costs work -- a half-typed reason in one of them. Every transition here
 * requires a written reason, so navigating away in the same tab can
 * discard several sentences a curator has just composed, and the browser
 * Back button restores the route without restoring any of it.
 *
 * `rel="noopener noreferrer"` is not optional with `target="_blank"`:
 * without `noopener` the opened page gets a live `window.opener` handle
 * back into this one and can navigate it.
 *
 * The note is for screen reader users, who otherwise get no warning that
 * focus is about to leave for a window they did not ask for. It is read
 * aloud and never seen.
 */
function NewTabNote() {
    return <span className="admin-visually-hidden"> (opens in a new tab)</span>
}

/**
 * What a row shows in its Record column: the record, and where to see it.
 *
 * Four outcomes, and the two that cannot be linked are kept apart on
 * purpose -- see `resolveRecordLocation`. Eight identical "cannot be named"
 * lines is what this column used to render for every applied energy
 * correction, and it told a curator nothing about whether they were looking
 * at a bug (a record that has gone missing) or a known gap (a type with no
 * page yet). Those call for different actions, so they get different words.
 */
function RecordCell({ location }: { location: RecordLocation }) {
    switch (location.kind) {
        case "record":
            return (
                <Link
                    to={location.href}
                    className="data"
                    target="_blank"
                    rel="noopener noreferrer"
                >
                    {location.ref}
                    <NewTabNote />
                </Link>
            )
        case "container":
            return (
                <>
                    {/* The record's own name first, when it has one, so the
                        record stays distinguishable from the thing it is
                        shown inside. Without it the row would read as if the
                        container itself were what needs reviewing. */}
                    {location.ref !== null ? (
                        <span className="data">{location.ref}</span>
                    ) : (
                        // The comma is load-bearing, and only here. A ref
                        // followed by "shown on ..." reads as two facts about
                        // one record; "cannot be named shown on ..." with
                        // nothing between them reads as one broken sentence.
                        // Seen on the rendered page, not reasoned about.
                        <>
                            <span className="admin-absent">cannot be named</span>,
                        </>
                    )}{" "}
                    <Link
                        to={location.href}
                        className="review-container-link"
                        target="_blank"
                        rel="noopener noreferrer"
                    >
                        shown on {recordTypeWords(location.containerType)}{" "}
                        <span className="data">{location.containerRef}</span>
                        <NewTabNote />
                    </Link>
                </>
            )
        case "no-page":
            return (
                <>
                    <span className="data">{location.ref}</span>{" "}
                    {/* Two facts, both certainly true here, and no claim
                        about WHICH of them is the operative one -- see
                        `resolveRecordLocation`. The earlier wording, "no
                        page for this record type yet", named the missing
                        page as the sole reason, which is wrong whenever the
                        container was the thing that could not be named. */}
                    <span className="admin-absent">
                        no page for this record type, and nowhere it can be seen
                    </span>
                </>
            )
        case "unnamed":
            // Kept word-for-word as the phrase this column has always used
            // for an unnameable record. What changed is that it is no longer
            // the ONLY thing an unlinkable row can say: "no page for this
            // record type yet" above is a different sentence for a different
            // situation, which is the whole point -- one is a record that has
            // gone missing, the other a page nobody has built.
            return (
                <span className="admin-absent">this record cannot be named</span>
            )
    }
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
                        : "Could not load the record review list.",
            })
        }
    }, [])

    useEffect(() => {
        if (canReview) void refresh()
    }, [canReview, refresh, statusFilter, offset])

    if (state.status === "loading") {
        return (
            <section className="admin-page">
                <h1>Record review</h1>
                <p role="status">Checking your account…</p>
            </section>
        )
    }
    if (state.status === "unreachable") {
        return (
            <section className="admin-page">
                <h1>Record review</h1>
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
                <h1>Record review</h1>
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

    /**
     * How to name a record in a message that may outlive its row.
     *
     * Falls back to the container before giving up. "applied_energy_correction
     * (unnamed)" is what a curator used to be told after a refusal on one of
     * the 164 correction rows, and with several of them on a page it does not
     * identify which. "applied_energy_correction on spc_..." does.
     */
    function labelOf(row: RecordReview): string {
        if (row.record_public_ref) return row.record_public_ref
        if (row.container_ref) return `${row.record_type} on ${row.container_ref}`
        return `${row.record_type} (unnamed)`
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
            <h1>Record review</h1>
            <p className="admin-lede">
                Every record in the archive, and whether a human has vouched for it.
                A review row exists for each one from the moment it is deposited, so
                this list needs nothing run to fill it. Approving here is{" "}
                <strong>authoritative, and permanent in its effect on the data</strong>:
                it changes what every reader is told to trust, and it freezes the
                record against further edits. Moving the review back to under review
                afterwards does not unfreeze it. That is what{" "}
                <Link to="/admin/curator-queue">Machine findings</Link> is not: it
                triages what an automated reviewer raised, and endorses nothing.
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
                                    const location = resolveRecordLocation(
                                        row.record_type,
                                        row.record_public_ref,
                                        row.container_type,
                                        row.container_ref,
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
                                                <RecordCell location={location} />
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
