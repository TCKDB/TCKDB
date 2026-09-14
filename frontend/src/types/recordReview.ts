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
 * Unlike the curator queue's wording, these sentences are about the
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
