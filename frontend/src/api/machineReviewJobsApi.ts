import { AuthApiError, throwForFailedResponse } from "./authApi"
import {
    JobDetailSchema,
    JobPageSchema,
    JobSchema,
    MachineReviewSettingsSchema,
    ProvidersSchema,
    type Job,
    type JobDetail,
    type MachineReviewSettings,
    type Provider,
} from "../types/machineReviewJob"

/**
 * Client for `/api/v1/machine-review/*` and `/api/v1/admin/machine-review/settings`
 * (`backend/app/api/routes/machine_review_jobs.py`).
 *
 * Session-cookie contract as elsewhere: `credentials: "include"` on every call. The server decides who may request,
 * read or cancel; the page asks for the role first only as a courtesy.
 */

const API_BASE = (import.meta.env.VITE_API_BASE_URL ?? "").replace(/\/$/, "")
const BASE = `${API_BASE}/api/v1/machine-review`
const SETTINGS = `${API_BASE}/api/v1/admin/machine-review/settings`

/** The query key of one job's progress. */
export function jobQueryKey(jobId: number) {
    return ["machine-review-job", jobId] as const
}

/** The query key of a submission's job list. */
export function submissionJobsKey(submissionId: number) {
    return ["machine-review-jobs", submissionId] as const
}

export const JOB_CONFLICT = "machine_review_job_conflict"

async function send(url: string, init: RequestInit): Promise<unknown> {
    const response = await fetch(url, {
        credentials: "include",
        ...init,
        headers: { Accept: "application/json", ...(init.body ? { "Content-Type": "application/json" } : {}) },
    })
    if (!response.ok) return throwForFailedResponse(response)
    return response.json()
}

export async function listProviders(): Promise<Provider[]> {
    return ProvidersSchema.parse(await send(`${BASE}/providers`, { method: "GET" })).providers
}

export async function getJob(jobId: number, afterSeq = 0): Promise<JobDetail> {
    return JobDetailSchema.parse(await send(`${BASE}/jobs/${jobId}?after_seq=${afterSeq}`, { method: "GET" }))
}

/** The submission's jobs, newest first, so an open job is the first item. */
export async function listSubmissionJobs(submissionId: number): Promise<Job[]> {
    return JobPageSchema.parse(await send(`${BASE}/jobs?submission_id=${submissionId}`, { method: "GET" })).items
}

export async function cancelJob(jobId: number): Promise<Job> {
    return JobSchema.parse(await send(`${BASE}/jobs/${jobId}/cancel`, { method: "POST" }))
}

export type RequestOutcome = { kind: "queued"; job: Job } | { kind: "attached"; job: Job }

/**
 * Ask for a review of one submission.
 *
 * A 409 `machine_review_job_conflict` means a job is already queued or running; its read carries no id, so the open
 * job is found through the submission's job list and the page attaches to it. Any other refusal is thrown.
 */
export async function requestMachineReview(submissionId: number, providerKey: string): Promise<RequestOutcome> {
    try {
        const job = JobSchema.parse(
            await send(`${BASE}/jobs`, {
                method: "POST",
                body: JSON.stringify({ submission_id: submissionId, provider_key: providerKey }),
            }),
        )
        return { kind: "queued", job }
    } catch (error) {
        if (!(error instanceof AuthApiError) || error.code !== JOB_CONFLICT) throw error
        const open = (await listSubmissionJobs(submissionId))[0]
        if (open === undefined) throw error
        return { kind: "attached", job: open }
    }
}

export async function getMachineReviewSettings(): Promise<MachineReviewSettings> {
    return MachineReviewSettingsSchema.parse(await send(SETTINGS, { method: "GET" }))
}

/** Change the named switches only; an omitted one stays as it is. */
export async function updateMachineReviewSettings(
    change: Partial<Pick<MachineReviewSettings, "machine_review_enabled" | "curator_requests_enabled">>,
): Promise<MachineReviewSettings> {
    return MachineReviewSettingsSchema.parse(await send(SETTINGS, { method: "PATCH", body: JSON.stringify(change) }))
}
