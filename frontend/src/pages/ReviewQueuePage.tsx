import { useCallback, useEffect, useRef, useState } from "react"
import { Link, Navigate } from "react-router-dom"
import "../auth.css"
import "../admin.css"
import { AuthApiError } from "../api/authApi"
import {
    RecordReviewResponseError,
    listReviewQueue,
    setRecordReviewStatus,
} from "../api/recordReviewsApi"
import { spinWord } from "../domain/chemistryFormat"
import { Formula } from "../components/Formula"
import { ReactionEquation } from "../components/ReactionEquation"
import { facetChips } from "../domain/recordFacets"
import {
    recordRoute,
    recordTypeGroupLabel,
    recordTypeHasPage,
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
    type ReviewQueueSubject,
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
 * ## Task #269: subject grouping
 *
 * The owner rejected the flat, one-row-per-record table outright: two
 * corrections on one species rendered as two lines with nothing telling
 * them apart, a correction's missing public ref leaked as "cannot be
 * named", nothing on screen said what the chemistry WAS, and a bare "50
 * shown" admitted the page could not say how big the backlog is.
 *
 * This page now reads `GET /api/v1/record-reviews/queue`
 * (`listReviewQueue`), which groups every record under the SUBJECT it
 * belongs to -- the species entry or transition-state entry a curator
 * actually judges as one unit, resolved server-side by
 * `app/services/review_queue.py`. Paging counts SUBJECTS, never records,
 * so a page boundary can never fall inside one subject's records. The
 * header reports honest, computed totals (`subject_total`/`record_total`)
 * over the WHOLE filtered backlog, not just the page shown -- the old
 * "there may be more, and this page cannot say how many" hedge is gone
 * because the new route does not need it.
 *
 * Within one response, a subject's records are never split across a page
 * boundary. Across separate requests that is NOT a promise that paging is
 * a stable walk: the server recomputes the subject order fresh each call,
 * with no cursor held between them, so a record leaving the filter (being
 * judged) between reading page 1 and asking for page 2 can shift every
 * later subject's position and skip one at the boundary -- the same
 * offset-pagination cost the flat list this replaces already carried, now
 * over subjects instead of rows. See `list_review_queue`'s own docstring.
 *

 * The review ACTION is unchanged: still per record, still routed through
 * `setRecordReviewStatus(recordType, recordId, ...)`. Grouping is a
 * presentation of the same rows, not a new kind of approval -- a
 * collapsed cluster of same-type records (two corrections on one
 * species) expands so each one is still reached and judged individually.
 * A per-subject bulk approve is a deliberately separate, later change
 * (task #270): it interacts with permanent data freezing and needs its
 * own design, and nothing here offers it.
 */

type LoadState =
    | { status: "loading" }
    | { status: "error"; message: string }
    | {
          status: "ready"
          subjects: ReviewQueueSubject[]
          subjectTotal: number
          recordTotal: number
      }

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

/** A stable key for a subject: type+ref together, since either alone can repeat. */
function subjectKeyOf(subject: ReviewQueueSubject, index: number): string {
    return subject.subject_type && subject.subject_ref
        ? `${subject.subject_type}:${subject.subject_ref}`
        : `orphan:${index}`
}

/**
 * Every link out of this queue opens in a new tab.
 *
 * This is deliberately against the usual advice, which is that a page
 * should not decide how a link opens. It is made here because the queue is
 * **stateful in a way the URL does not capture**: the status filter, the
 * page offset, which rows have an open form, which subject blocks are
 * expanded, and -- the one that actually costs work -- a half-typed reason
 * in one of them. Every transition here requires a written reason, so
 * navigating away in the same tab can discard several sentences a curator
 * has just composed, and the browser Back button restores the route
 * without restoring any of it.
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
 * The subject block's heading: what the chemistry IS, not what it is
 * called. Defect #4 -- nothing on screen said a species entry was a
 * hydrogen atom, C9H8, or a C9H9 radical with stereo label R.
 *
 * Three shapes:
 * - `species_entry`: formula (from `species.smiles`, via the RDKit
 *   cartridge) plus spin word plus the SAME facet chips
 *   (`domain/recordFacets.ts`) every other species surface renders --
 *   reused, not reinvented, per the brief.
 * - `transition_state_entry`: formula from the entry's OWN
 *   `unmapped_smiles` (never the reaction it sits on -- see
 *   `app/services/review_queue.py`'s "naming a transition-state subject")
 *   plus spin word. No facet chips: a transition state carries none of
 *   those axes.
 * - anything else (a conformer group, a reaction entry, an orphaned row):
 *   no formula is available without a second container hop this surface
 *   deliberately does not take (see that module's docstring) -- the
 *   heading says plainly what KIND of record this is instead of guessing.
 */
function SubjectHeading({ subject }: { subject: ReviewQueueSubject }) {
    if (subject.subject_type === null || subject.subject_ref === null) {
        return (
            <p className="review-subject-heading admin-absent">
                No record could be found for this review row. It may have
                been deleted after it was queued.
            </p>
        )
    }

    if (subject.subject_type === "species_entry") {
        const effectiveKind = subject.chemistry.species_entry_kind ?? "minimum"
        // `includeState: false` drops the bare "ground state"/"excited
        // state" phrase -- the same opt-out `SpeciesOverviewPage.tsx`'s
        // `EntryCard` already uses for the identical reason: "singlet"
        // (from `spin` below) already says the electronic state is the
        // unremarkable default for nearly every species in the archive,
        // so restating "ground state" next to it on almost every block
        // was noise review #492 measured, not information. Any REAL
        // extra (an excited-state label, a term symbol) still survives
        // `includeState: false` -- see `facetChips`'s own docstring.
        //
        // `facetChips` always leads with a kind chip ("minimum"/"van der
        // Waals complex") -- there is no flag to omit it, because for a
        // van der Waals complex it is real information a reviewer needs.
        // For the ordinary "minimum" case, which is nearly every species,
        // it is exactly the same kind of noise "ground state" was, so it
        // is dropped here at the call site rather than in the shared
        // helper: the first chip is deterministically the kind chip,
        // dropped only when the kind is the unremarkable default.
        const rawChips = facetChips(
            {
                species_entry_kind: effectiveKind,
                electronic_state_kind: subject.chemistry.electronic_state_kind ?? "ground",
                electronic_state_label: subject.chemistry.electronic_state_label,
                term_symbol: subject.chemistry.term_symbol,
                stereo_label: subject.chemistry.stereo_label,
                isotope_key: subject.chemistry.isotope_key,
            },
            { includeState: false },
        )
        const chips = effectiveKind === "minimum" ? rawChips.slice(1) : rawChips
        const spin = spinWord(subject.chemistry.multiplicity)
        return (
            <>
                <h2 className="review-subject-heading">
                    {subject.chemistry.formula ? (
                        <Formula value={subject.chemistry.formula} />
                    ) : (
                        // An honest "we don't have this" is a CAVEAT, never a
                        // name -- the name slot always holds a name. See the
                        // transition_state_entry branch below, where the
                        // same rule applies to a caveat that is common
                        // rather than a near-unreachable edge case.
                        recordTypeGroupLabel("species_entry", 1)
                    )}
                    {spin && <span className="review-subject-chip">{spin}</span>}
                    {chips.map((chip) => (
                        <span key={chip} className="review-subject-chip">
                            {chip}
                        </span>
                    ))}
                </h2>
                {!subject.chemistry.formula && (
                    <p className="review-subject-caveat admin-absent">
                        formula not available
                    </p>
                )}
            </>
        )
    }

    if (subject.subject_type === "transition_state_entry") {
        const spin = spinWord(subject.chemistry.multiplicity)
        // Two DIFFERENT absences, told apart honestly rather than
        // collapsed into one sentence (review #492 caught the collapse):
        // `unmapped_smiles` is the candidate's OWN saddle-point SMILES,
        // never a "reaction SMILES" -- that word was simply wrong, not
        // just terse -- and it can be present while `formula` is still
        // null, because it can be a reaction-shaped string
        // (`"[CH3].[H]>>C"`) that a single-molecule parser rejects. That
        // is "recorded, but no formula could be derived from it", a
        // different fact from "nothing was ever recorded", and showing
        // the same "not recorded" sentence for both would be false in
        // the first case, not merely unhelpful.
        const smilesRecordedButUnparsed =
            !subject.chemistry.formula && subject.chemistry.unmapped_smiles
        return (
            <>
                <h2 className="review-subject-heading">
                    {/* The name slot always holds a name -- the record
                        type, when there is no formula to show instead --
                        never the caveat. See the species_entry branch
                        above and this component's own module comment for
                        why: an apology standing where every other
                        subject's name stands is the "cannot be named"
                        defect happening again, in a new place. */}
                    {subject.chemistry.formula ? (
                        <Formula value={subject.chemistry.formula} />
                    ) : (
                        "Transition state"
                    )}
                    {spin && <span className="review-subject-chip">{spin}</span>}
                </h2>
                {!subject.chemistry.formula &&
                    (smilesRecordedButUnparsed ? (
                        <p className="review-subject-caveat admin-absent">
                            no formula could be derived from this candidate's
                            own SMILES:{" "}
                            <code className="data">
                                {subject.chemistry.unmapped_smiles}
                            </code>
                        </p>
                    ) : (
                        <p className="review-subject-caveat admin-absent">
                            no SMILES recorded for this candidate
                        </p>
                    ))}
            </>
        )
    }

    if (subject.subject_type === "reaction_entry" && subject.reaction) {
        // A reaction_entry is not one molecule, so it has no formula --
        // but it has the fact a reviewer actually wants, the equation
        // itself. Review #492, finding 6: a kinetics reviewer used to see
        // "Reaction entry rxe_..." and nothing else, the species-only
        // half of this page's own naming problem seen from the other
        // side. `<ReactionEquation>` is the SAME component the reaction
        // entry page renders -- reused, not a second implementation --
        // and `linkParticipants={false}` because every other link in
        // this queue opens in a new tab (see the module docstring on
        // why), and a per-participant in-page navigation link here would
        // be the one exception to that rule.
        //
        // `formulaOnly` -- review #492 caught the component's DEFAULT
        // rendering ("[CH3] (CH3) + [H] (H) ⇌ C (CH4)") printing every
        // participant twice, once as SMILES and once as formula, right
        // below species subject headings that already show formula
        // alone. See `SpeciesFace`'s own docstring (`components/
        // Formula.tsx`) for why this is a scoped, additive prop rather
        // than a fork of the shared component, and for the one case this
        // page's use of it deliberately does NOT reopen (two structurally
        // different participants sharing one formula reading as a
        // species reacting to itself) -- unlikely to matter on a triage
        // surface that already prints the reaction's own ref to click
        // through, but worth someone's eyes if it ever does.
        return (
            <h2 className="review-subject-heading">
                <ReactionEquation
                    reactants={subject.reaction.reactants}
                    products={subject.reaction.products}
                    reversible={subject.reaction.reversible}
                    linkParticipants={false}
                    formulaOnly
                />
            </h2>
        )
    }

    return (
        <h2 className="review-subject-heading">
            {recordTypeGroupLabel(subject.subject_type, 1)}
        </h2>
    )
}

/** The subject's own ref, shown quietly -- for citation, not for scanning. */
function SubjectRefLine({ subject }: { subject: ReviewQueueSubject }) {
    if (subject.subject_type === null || subject.subject_ref === null) return null
    const href = recordRoute(subject.subject_type, subject.subject_ref)
    return (
        <p className="review-subject-ref">
            {href ? (
                <Link
                    to={href}
                    className="data"
                    target="_blank"
                    rel="noopener noreferrer"
                >
                    {subject.subject_ref}
                    <NewTabNote />
                </Link>
            ) : (
                <>
                    <span className="data">{subject.subject_ref}</span>{" "}
                    <span className="admin-absent">
                        no page for this record type yet
                    </span>
                </>
            )}
        </p>
    )
}

/**
 * One record's own link, when its type has a page independent of the
 * subject it is nested under (a calculation, a conformer group nested
 * under a species entry, a reaction entry nested under a reaction). Types
 * with no page of their own render no link here -- the subject block's
 * own link already covers "go and look at this", and repeating "cannot be
 * named" or "shown on ..." for every such row is exactly the noise this
 * redesign removes.
 *
 * The one case that looks identical but is not: a `species_entry` or
 * `transition_state_entry` review row is its OWN subject (see
 * `app/services/review_queue.py`), so it is nested inside the very block
 * whose heading already names it. Rendering this link there repeated the
 * exact ref the block's own header and ref line already show, on nearly
 * every species in the queue --
 *
 *     H2O ...   spe_h2o (opens in a new tab)
 *       Thermochemistry           Review...
 *       Species entry  spe_h2o (opens in a new tab)   Review...
 *
 * -- which is the SAME complaint the subject grouping exists to fix,
 * reappearing one level down. `subject` is passed in so this can compare
 * the row against the block it is already inside, rather than only
 * knowing about the row.
 */
function RecordOwnLink({ row, subject }: { row: RecordReview; subject: ReviewQueueSubject }) {
    const isTheSubjectItself =
        row.record_type === subject.subject_type &&
        row.record_public_ref !== null &&
        row.record_public_ref === subject.subject_ref
    if (isTheSubjectItself) return null
    if (!recordTypeHasPage(row.record_type) || !row.record_public_ref) return null
    const href = recordRoute(row.record_type, row.record_public_ref)
    if (href === null) return null
    return (
        <Link to={href} className="data review-record-own-link" target="_blank" rel="noopener noreferrer">
            {row.record_public_ref}
            <NewTabNote />
        </Link>
    )
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
    const [expanded, setExpanded] = useState<ReadonlySet<string>>(new Set())

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

    const refresh = useCallback(async () => {
        setLoad({ status: "loading" })
        const seq = (requestSeq.current += 1)
        const current = filterRef.current
        try {
            const page = await listReviewQueue({
                ...(current === "all" ? {} : { status: current }),
                limit: PAGE_LIMIT,
                offset: offsetRef.current,
            })
            if (seq !== requestSeq.current) return
            setLoad({
                status: "ready",
                subjects: page.subjects,
                subjectTotal: page.subject_total,
                recordTotal: page.record_total,
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

    /**
     * Whether a record's own status is worth printing next to it.
     *
     * Under a specific filter every record on screen already carries that
     * status -- the server filtered by it -- so repeating the word next to
     * every one of them says nothing "Showing: {filter}" above did not
     * already say, fifteen times on a five-subject page and hundreds
     * across a real backlog. Under "all" a status IS the information, so
     * it always shows there.
     */
    function showStatusFor(status: RecordReviewStatus): boolean {
        return statusFilter === "all" || status !== statusFilter
    }

    function toggleExpanded(groupKey: string) {
        setExpanded((current) => {
            const next = new Set(current)
            if (next.has(groupKey)) next.delete(groupKey)
            else next.add(groupKey)
            return next
        })
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
            await refresh()
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
            await refresh()
        } finally {
            setBusy((current) => {
                const next = new Set(current)
                next.delete(rowId)
                return next
            })
        }
    }

    /** One record's status + action, whether shown alone or inside an expanded group. */
    function renderRecordRow(row: RecordReview, subject: ReviewQueueSubject) {
        const rowId = keyOf(row)
        const rowBusy = busy.has(rowId)
        const options = offeredTransitions(row)
        // What the form will actually send. A re-read can change a row's
        // status under an open form, leaving the held choice no longer
        // among the options -- and a controlled <select> whose value
        // matches nothing displays the FIRST option. The select then
        // showed one state, the hint described it, and submitting sent a
        // third. Deriving all three from one value removes the
        // disagreement rather than papering it.
        const chosen =
            draft?.rowId === rowId && options.includes(draft.status)
                ? draft.status
                : options[0]
        const canSubmit = chosen !== undefined
        const cls = statusClass(row.status)
        return (
            <li key={rowId} className="review-record-row">
                <div className="review-record-row-line">
                    <div className="review-record-row-left">
                        <span className="review-record-type">
                            {recordTypeGroupLabel(row.record_type, 1)}
                        </span>
                        <RecordOwnLink row={row} subject={subject} />
                        {row.note && (
                            <span className="review-record-note">{row.note}</span>
                        )}
                    </div>
                    <div className="review-record-row-right">
                        {/* Dropped when it just repeats the active filter --
                            "not reviewed" fifteen times on a five-subject
                            page under the default filter said nothing a
                            reader did not already know from "Showing: not
                            reviewed" above. Shown in full under "all",
                            where a status IS the information. */}
                        {showStatusFor(row.status) && (
                            <span className={cls ?? undefined}>{statusLabel(row.status)}</span>
                        )}
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
                            <span className="admin-absent">no transition available</span>
                        )}
                    </div>
                </div>
                {rowErrors.has(rowId) && (
                    <p className="auth-error admin-row-error" role="alert">
                        {rowErrors.get(rowId)?.message}
                    </p>
                )}
                {draft?.rowId === rowId && canSubmit && (
                    <form
                        id={`review-form-${rowId}`}
                        className="admin-resolve"
                        onSubmit={(e) => {
                            e.preventDefault()
                            void submit(row, { ...draft, status: chosen })
                        }}
                    >
                        <label htmlFor={`st-${rowId}`}>New review state</label>
                        <select
                            id={`st-${rowId}`}
                            className="admin-role-select"
                            value={chosen}
                            onChange={(e) =>
                                setDraft({
                                    ...draft,
                                    status: e.target.value as RecordReviewStatus,
                                })
                            }
                        >
                            {options.map((s) => (
                                <option key={s} value={s}>
                                    {statusLabel(s)}
                                </option>
                            ))}
                        </select>
                        <p className="admin-hint">{statusMeaning(chosen)}</p>
                        <label htmlFor={`note-${rowId}`}>Why (required)</label>
                        <textarea
                            id={`note-${rowId}`}
                            value={draft.note}
                            rows={2}
                            onChange={(e) =>
                                setDraft({ ...draft, note: e.target.value })
                            }
                        />
                        <button
                            type="submit"
                            disabled={draft.note.trim().length === 0 || rowBusy}
                        >
                            Record this judgement
                        </button>
                    </form>
                )}
            </li>
        )
    }

    /**
     * Records of one type under one subject, collapsed to a count when
     * there is more than one (defect #2: two corrections on one species
     * rendering as an unexplained duplicate). The collapsed line still
     * expands to every individual record's own status and Review action
     * -- a per-subject bulk approve is explicitly NOT this (task #270).
     */
    function renderRecordGroup(
        groupKey: string,
        recordType: string,
        rows: RecordReview[],
        subject: ReviewQueueSubject,
    ) {
        if (rows.length === 1) return renderRecordRow(rows[0], subject)

        const statuses = new Set(rows.map((r) => r.status))
        const uniformStatus = statuses.size === 1 ? [...statuses][0] : null
        const isExpanded = expanded.has(groupKey)
        return (
            <li key={groupKey} className="review-record-group">
                <div className="review-record-group-summary">
                    <span className="review-record-type">
                        {recordTypeGroupLabel(recordType, rows.length)}
                    </span>
                    <span className="review-record-count">{rows.length}</span>
                    {uniformStatus ? (
                        showStatusFor(uniformStatus) && (
                            <span className={statusClass(uniformStatus) ?? undefined}>
                                {statusLabel(uniformStatus)}
                            </span>
                        )
                    ) : (
                        // Mixed only arises under "all" (a specific filter
                        // already narrows every grouped record to one
                        // status server-side), where a status is always
                        // worth showing -- so this is never suppressed.
                        <span className="admin-absent">mixed review state</span>
                    )}
                    <button
                        type="button"
                        aria-expanded={isExpanded}
                        onClick={() => toggleExpanded(groupKey)}
                    >
                        {isExpanded ? "Hide records" : `Show ${rows.length} records`}
                    </button>
                </div>
                {isExpanded && (
                    <ul className="review-record-group-detail">
                        {rows.map((row) => renderRecordRow(row, subject))}
                    </ul>
                )}
            </li>
        )
    }

    function renderSubject(subject: ReviewQueueSubject, index: number) {
        const subjectKey = subjectKeyOf(subject, index)
        // Group this subject's own records by type, preserving the order
        // the server returned them in (newest-first).
        const groups = new Map<string, RecordReview[]>()
        for (const row of subject.records) {
            const existing = groups.get(row.record_type)
            if (existing) existing.push(row)
            else groups.set(row.record_type, [row])
        }
        return (
            <section key={subjectKey} className="card review-subject">
                <SubjectHeading subject={subject} />
                <SubjectRefLine subject={subject} />
                <ul className="review-subject-records">
                    {[...groups.entries()].map(([recordType, rows]) =>
                        renderRecordGroup(`${subjectKey}:${recordType}`, recordType, rows, subject),
                    )}
                </ul>
            </section>
        )
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
                Records are grouped below by the species or transition state they
                belong to; reviewing still happens one record at a time.
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
                    .filter(
                        ([rowId]) =>
                            !load.subjects.some((s) =>
                                s.records.some((r) => keyOf(r) === rowId),
                            ),
                    )
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

            {load.status === "ready" && load.subjects.length === 0 && (
                <p role="status">
                    {offset > 0
                        ? "Nothing older than this. Go back for the newer subjects."
                        : statusFilter === "not_reviewed"
                          ? "Nothing is waiting. Every record has been looked at."
                          : "No records match this filter."}
                </p>
            )}

            {load.status === "ready" && load.subjects.length > 0 && (
                <>
                    <p className="admin-count" role="status">
                        {load.recordTotal} record{load.recordTotal === 1 ? "" : "s"}
                        {statusFilter === "not_reviewed" ? " awaiting review" : ""}
                        {", "}
                        {load.subjectTotal} subject
                        {load.subjectTotal === 1 ? "" : "s"}
                        {offset > 0
                            ? ` (showing subjects ${offset + 1}-${
                                  offset + load.subjects.length
                              })`
                            : ""}
                    </p>
                    {load.subjects.map((subject, index) => renderSubject(subject, index))}
                </>
            )}

            {load.status === "ready" &&
                (load.subjects.length > 0 || offset > 0) && (
                    <div className="admin-paging">
                        <button
                            type="button"
                            disabled={offset === 0}
                            onClick={() => setOffset(Math.max(0, offset - PAGE_LIMIT))}
                        >
                            Newer
                        </button>{" "}
                        <button
                            type="button"
                            disabled={offset + PAGE_LIMIT >= load.subjectTotal}
                            onClick={() => setOffset(offset + PAGE_LIMIT)}
                        >
                            Older
                        </button>
                    </div>
                )}
        </section>
    )
}
