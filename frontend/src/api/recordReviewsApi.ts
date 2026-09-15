import { throwForFailedResponse } from "./authApi"
import {
    RecordReviewSchema,
    type RecordReview,
    type RecordReviewStatus,
} from "../types/recordReview"

/**
 * Client for `/api/v1/record-reviews` (`backend/app/api/routes/record_reviews.py`).
 *
 * Reads are open to any authenticated user; the PATCH is gated on
 * `require_curator_or_admin`, and the transition policy and
 * self-approval guard live in the service layer behind it. The page
 * checks the role before offering a control, but the server enforces it,
 * and that order matters.
 */

const API_BASE = (import.meta.env.VITE_API_BASE_URL ?? "").replace(/\/$/, "")
const BASE = `${API_BASE}/api/v1/record-reviews`

/**
 * A write that reached the server but whose reply could not be read.
 *
 * Same contract as `CuratorTaskResponseError`: the PATCH returned 2xx, so
 * the transition HAS happened, and only the response body failed to
 * validate. Telling a curator "that did not go through" there would be
 * false, and their natural next move -- do it again -- can then fail as a
 * disallowed transition from the state they just reached.
 */
export class RecordReviewResponseError extends Error {
    constructor() {
        super(
            "The change was saved, but this page could not read the reply. " +
                "Reload to see the record's current review state.",
        )
        this.name = "RecordReviewResponseError"
    }
}

/**
 * A page of review rows.
 *
 * The route answers with a bare array and no total, so `hasMore` is
 * inferred from a full page rather than reported. That is why the page
 * says "there may be more" instead of naming a backlog size: the count
 * would need either a breaking change to this response or a second route
 * (task #257).
 */
export type RecordReviewPage = {
    items: RecordReview[]
    /** Rows the server returned that this build could not read. */
    unreadable: number
    /** True when the server filled the page, so more may exist. */
    full: boolean
}

export async function listRecordReviews(options: {
    status?: RecordReviewStatus
    recordType?: string
    limit: number
    offset?: number
}): Promise<RecordReviewPage> {
    const params = new URLSearchParams()
    if (options.status) params.set("status", options.status)
    if (options.recordType) params.set("record_type", options.recordType)
    params.set("limit", String(options.limit))
    if (options.offset !== undefined) params.set("skip", String(options.offset))

    const response = await fetch(`${BASE}?${params.toString()}`, {
        method: "GET",
        credentials: "include",
        headers: { Accept: "application/json" },
    })
    if (!response.ok) return throwForFailedResponse(response)

    const payload: unknown = await response.json()
    if (!Array.isArray(payload)) throw new RecordReviewResponseError()

    // Row by row: one row this build cannot read costs that row, not the
    // whole queue. A record review list that renders nothing is the worst
    // possible answer to "what still needs looking at".
    const items: RecordReview[] = []
    let unreadable = 0
    for (const row of payload) {
        const parsed = RecordReviewSchema.safeParse(row)
        if (parsed.success) items.push(parsed.data)
        else unreadable += 1
    }
    return { items, unreadable, full: payload.length >= options.limit }
}

/**
 * Transition one record's review status.
 *
 * Addressed by `record_type` + `record_id` because that is what the route
 * takes. The page reads `record_public_ref` and writes the id; see the
 * note in `docs/plans/reviewer-surface-walkthrough.md`.
 *
 * `note` is optional to the backend. The page requires one for EVERY
 * transition, not only the judgements -- a bare "approved" with no
 * reason tells the next reader nothing about why this record is now
 * trusted, and "under review" with no reason tells the next curator
 * nothing about what is being checked or by whom.
 *
 * One consequence worth knowing: the backend overwrites `record_review.note`
 * on each transition that carries one, so the row holds only the latest
 * reason. The full sequence survives in `record_review_event`, which no
 * read surface exposes yet.
 */
export async function setRecordReviewStatus(args: {
    recordType: string
    recordId: number
    status: RecordReviewStatus
    note?: string
}): Promise<RecordReview> {
    const response = await fetch(
        `${BASE}/${encodeURIComponent(args.recordType)}/${args.recordId}`,
        {
            method: "PATCH",
            credentials: "include",
            headers: {
                Accept: "application/json",
                "Content-Type": "application/json",
            },
            body: JSON.stringify({
                status: args.status,
                ...(args.note ? { note: args.note } : {}),
            }),
        },
    )
    if (!response.ok) return throwForFailedResponse(response)

    const parsed = RecordReviewSchema.safeParse(await response.json())
    if (!parsed.success) throw new RecordReviewResponseError()
    return parsed.data
}
