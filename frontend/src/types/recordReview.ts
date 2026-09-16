import { z } from "zod"

/**
 * Runtime + static types for the record-review queue.
 *
 * Source of truth: `backend/app/api/routes/record_reviews.py`
 * (`RecordReviewRead`) and `backend/app/services/record_review.py`
 * (the transition policy).
 *
 * A record review is the **authoritative** answer to "has a person
 * endorsed this claim?" -- the third of the review axes and the only one
 * that changes what a reader is told to trust. It is not the machine's
 * advisory screen (`machine_review_status`), and it is not the curator
 * task queue, which tracks whether somebody handled a machine *finding*
 * and endorses nothing (ADR 0016).
 *
 * One row exists per record, written by the review-policy write that
 * every upload ends with, starting at `not_reviewed`. Nothing has to run
 * to populate this queue: every record ever deposited is already in it.
 */

export const RecordReviewStatusSchema = z.enum([
    "not_reviewed",
    "under_review",
    "approved",
    "rejected",
    "deprecated",
])
export type RecordReviewStatus = z.infer<typeof RecordReviewStatusSchema>

export const ALL_STATUSES: readonly RecordReviewStatus[] = [
    "not_reviewed",
    "under_review",
    "approved",
    "rejected",
    "deprecated",
]

/**
 * One review row.
 *
 * `record_public_ref` is the handle the archive is addressed by, added
 * to this read in #484 so a queue can name what it is asking somebody to
 * look at. `record_id` is the internal row id; it is what the PATCH path
 * takes, so the page reads a ref and writes an id -- an asymmetry that
 * is tolerable only while this surface is curator-gated and the id never
 * reaches the screen.
 *
 * `status` stays a strict enum: the page branches on it to decide which
 * transitions to offer, so a value it cannot classify is unusable rather
 * than merely unfamiliar. `record_type` is a plain string, because it is
 * only displayed and passed to `recordRoute`, which already answers
 * "no page" for anything it does not know.
 */
export const RecordReviewSchema = z.object({
    id: z.number().int(),
    record_type: z.string(),
    record_id: z.number().int(),
    record_public_ref: z.string().nullable().default(null),
    // Where the record can be SEEN, as opposed to what it is called. Added
    // in #262: six record types are rendered only inside their parent, so a
    // ref addresses no route and 30% of this queue pointed at nothing.
    //
    // `.default(null)` on both, so a server that predates the field parses
    // rather than costing the row -- `listRecordReviews` drops a row it
    // cannot read, and a queue that renders nothing is the worst possible
    // answer to "what still needs looking at".
    //
    // Loosely typed as strings on purpose, matching `record_type`: they are
    // displayed and passed to `resolveRecordLocation`, which already answers
    // "no page" for anything it does not recognise. A strict enum here would
    // turn a record type this build has not heard of into an unreadable row.
    container_type: z.string().nullable().default(null),
    container_ref: z.string().nullable().default(null),
    status: RecordReviewStatusSchema,
    submission_id: z.number().int().nullable().default(null),
    reviewed_by: z.number().int().nullable().default(null),
    reviewed_at: z.string().nullable().default(null),
    first_approved_at: z.string().nullable().default(null),
    note: z.string().nullable().default(null),
    created_at: z.string(),
    created_by: z.number().int().nullable().default(null),
})
export type RecordReview = z.infer<typeof RecordReviewSchema>

/**
 * Which transitions the backend allows, mirroring `_ALLOWED_TRANSITIONS`
 * in `app/services/record_review.py`.
 *
 * **This table is a convenience, not the rule.** The server decides, and
 * it refuses with a `DomainError` if this copy drifts. It exists so a
 * curator is not offered a button that can only fail -- offering every
 * status and letting the server reject three of them would be honest but
 * unusable. The page still surfaces the server's refusal verbatim, which
 * is what keeps a drifted copy visible rather than silently wrong.
 *
 * The three deliberate omissions are the point of the policy, not an
 * oversight: `approved -> rejected`, `rejected -> approved` and
 * `deprecated -> rejected` all route through `under_review` instead, so
 * that reversing a judgement is recorded as a re-review rather than
 * happening in one silent step.
 */
export const ALLOWED_TRANSITIONS: Readonly<
    Record<RecordReviewStatus, readonly RecordReviewStatus[]>
> = {
    not_reviewed: ["under_review", "approved", "rejected", "deprecated"],
    under_review: ["approved", "rejected", "not_reviewed"],
    approved: ["under_review", "deprecated"],
    rejected: ["under_review", "deprecated"],
    deprecated: ["under_review", "approved"],
}

export function allowedTransitions(
    from: RecordReviewStatus,
): readonly RecordReviewStatus[] {
    return ALLOWED_TRANSITIONS[from] ?? []
}

/** Human wording for a status. */
export function statusLabel(status: RecordReviewStatus): string {
    switch (status) {
        case "not_reviewed":
            return "not reviewed"
        case "under_review":
            return "under review"
        case "approved":
            return "approved"
        case "rejected":
            return "rejected"
        case "deprecated":
            return "deprecated"
    }
}

/**
 * What each status asserts about the science, shown where one is chosen.
 *
 * Unlike Machine findings' wording, these sentences are about the
 * record itself: this is the axis that does change what a reader is told
 * to trust, and a curator should be looking at that fact when they pick.
 */
export function statusMeaning(status: RecordReviewStatus): string {
    switch (status) {
        case "not_reviewed":
            return "Nobody has judged this record. This is where every deposit starts."
        case "under_review":
            return "Somebody is looking at it now. No judgement is recorded yet."
        case "approved":
            return "A person has endorsed this record. Readers are told it is trusted."
        case "rejected":
            return "A person judged this record wrong. It stays in the archive, marked."
        case "deprecated":
            return "Superseded or no longer to be relied on, without being judged wrong."
    }
}

/** The CSS class for a status, or null when the token is unfamiliar. */
export function statusClass(status: string): string | null {
    return (ALL_STATUSES as readonly string[]).includes(status)
        ? `review-status-${status.replace(/_/g, "-")}`
        : null
}

/**
 * The subject-grouped queue (task #269): `GET /api/v1/record-reviews/queue`.
 *
 * Source of truth: `backend/app/schemas/entities/record_review.py`'s
 * `ReviewQueuePageRead`/`ReviewQueueSubjectRead`/
 * `ReviewQueueSubjectChemistry`, built by
 * `backend/app/services/review_queue.py`.
 *
 * `subject_type`/`subject_ref` are null together only for the orphan case
 * (a review row that names nothing -- see that module's `SubjectKey`
 * docstring). Every field of `chemistry` is independently optional: a
 * subject type with no such axis (e.g. a conformer group has no formula)
 * and a value that was genuinely never recorded (a transition-state
 * entry's `unmapped_smiles`) both read as `null` here, and both are
 * honest, not failures.
 */
export const ReviewQueueSubjectChemistrySchema = z.object({
    formula: z.string().nullable().default(null),
    multiplicity: z.number().int().nullable().default(null),
    species_entry_kind: z.string().nullable().default(null),
    electronic_state_kind: z.string().nullable().default(null),
    electronic_state_label: z.string().nullable().default(null),
    term_symbol: z.string().nullable().default(null),
    stereo_label: z.string().nullable().default(null),
    isotope_key: z.string().nullable().default(null),
})
export type ReviewQueueSubjectChemistry = z.infer<
    typeof ReviewQueueSubjectChemistrySchema
>

export const ReviewQueueSubjectSchema = z.object({
    subject_type: z.string().nullable().default(null),
    subject_ref: z.string().nullable().default(null),
    chemistry: ReviewQueueSubjectChemistrySchema,
    records: z.array(RecordReviewSchema),
})
export type ReviewQueueSubject = z.infer<typeof ReviewQueueSubjectSchema>

export const ReviewQueuePageSchema = z.object({
    subjects: z.array(ReviewQueueSubjectSchema),
    // Computed over every row matching the filter, not just this page --
    // see `list_review_queue`'s docstring. Honest counts, never estimates.
    subject_total: z.number().int(),
    record_total: z.number().int(),
    offset: z.number().int(),
    limit: z.number().int(),
})
export type ReviewQueuePage = z.infer<typeof ReviewQueuePageSchema>
