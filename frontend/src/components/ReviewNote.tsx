import { Disclosure } from "./Disclosure"
import "../review-note.css"

/**
 * The curator's stated reason for a record's review status
 * (`RecordReview.note`, projected as `review.note` on every scientific
 * read schema that carries a `review` badge --
 * `backend/app/schemas/reads/scientific_common.py`'s `RecordReviewBadge`).
 *
 * A status alone ("under_review") is a warning a reader cannot act on;
 * this is what makes it usable. It is public, reader-facing prose of up
 * to a few thousand characters -- too long for a `.value-pill`, so it
 * gets its own disclosure rather than sitting inline next to the status
 * pill (`domain/reviewPillFormat.ts`).
 *
 * **Absence is absence.** `note` is `null`/`undefined` on the vast
 * majority of records (most reviews carry no note at all, and most
 * records carry no review row). This renders nothing at all in that
 * case -- never an empty disclosure, never a heading with no body. Do
 * not gate this on `status`: a note on an `approved` record is exactly
 * as meaningful as one on `under_review` (there is no status check in
 * this component, and there must not be one).
 *
 * Collapsed by default, matching every other `Disclosure` in this app
 * that summarizes rather than headlines its content -- the note explains
 * the status a reader already saw in the pill, it does not need to
 * repeat it inline uncollapsed.
 */
export function ReviewNote({ note, label = "Curator's review note" }: {
    note: string | null | undefined
    label?: string
}) {
    if (!note) return null
    return (
        <Disclosure summary={label}>
            <p className="review-note-text">{note}</p>
        </Disclosure>
    )
}
