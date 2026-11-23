import { throwForFailedResponse } from "./authApi"
import {
    SubmissionAuditEventSchema,
    SubmissionOverviewPageSchema,
    SubmissionRecordLinkSchema,
    SubmissionSchema,
    type Submission,
    type SubmissionAuditEvent,
    type SubmissionOverviewPage,
    type SubmissionRecordLink,
} from "../types/submission"
import { z } from "zod"

/**
 * Client for the submission reads the inspection page shows: the submission itself, its record links and audit events,
 * and the machine-review overview that lists submissions to pick from.
 *
 * Session-cookie contract as elsewhere: `credentials: "include"` on every call. The list of submissions and these reads
 * are the server's to authorise; the page checks the role first as a courtesy.
 */

const API_BASE = (import.meta.env.VITE_API_BASE_URL ?? "").replace(/\/$/, "")
const BASE = `${API_BASE}/api/v1/submissions`

/** The most a single read of record links or audit events returns. */
export const SUBMISSION_LIST_LIMIT = 200

async function readJson(url: string): Promise<unknown> {
    const response = await fetch(url, {
        method: "GET",
        credentials: "include",
        headers: { Accept: "application/json" },
    })
    if (!response.ok) return throwForFailedResponse(response)
    return response.json()
}

export async function fetchSubmission(id: number): Promise<Submission> {
    return SubmissionSchema.parse(await readJson(`${BASE}/${id}`))
}

export async function listSubmissionRecordLinks(id: number): Promise<SubmissionRecordLink[]> {
    return z
        .array(SubmissionRecordLinkSchema)
        .parse(await readJson(`${BASE}/${id}/record-links?limit=${SUBMISSION_LIST_LIMIT}`))
}

export async function listSubmissionAuditEvents(id: number): Promise<SubmissionAuditEvent[]> {
    return z
        .array(SubmissionAuditEventSchema)
        .parse(await readJson(`${BASE}/${id}/audit-events?limit=${SUBMISSION_LIST_LIMIT}`))
}

export async function listSubmissionMachineReviewOverview(options: {
    offset?: number
    limit?: number
    reviewed?: boolean
    hasOpenTasks?: boolean
    statuses?: string[]
}): Promise<SubmissionOverviewPage> {
    const params = new URLSearchParams()
    if (options.offset !== undefined) params.set("offset", String(options.offset))
    if (options.limit !== undefined) params.set("limit", String(options.limit))
    if (options.reviewed !== undefined) params.set("reviewed", String(options.reviewed))
    if (options.hasOpenTasks !== undefined) params.set("has_open_tasks", String(options.hasOpenTasks))
    for (const status of options.statuses ?? []) params.append("statuses", status)
    const query = params.toString()
    return SubmissionOverviewPageSchema.parse(
        await readJson(`${BASE}/machine-review-overview${query ? `?${query}` : ""}`),
    )
}
