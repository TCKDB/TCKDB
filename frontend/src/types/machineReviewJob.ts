import { z } from "zod"

/**
 * The machine-review job reads.
 *
 * Source of truth: `MachineReviewJobRead`, `MachineReviewJobDetailRead`, `MachineReviewJobEventRead`,
 * `MachineReviewProviderRead`, `MachineReviewSettingsRead` and `SubmissionMachineReviewOpenJobRead` in the backend.
 *
 * The job lifecycle is its own five-value enum and is not the review status (`machineReviewInspection.ts`): a job
 * can be `failed` while no review exists, and a review exists only for a job that `succeeded`. The status stays a
 * strict enum because the page branches on it (open or ended); the failure code, availability, provider key and
 * event kind are display words, so they stay strings and an unfamiliar token is shown as itself.
 *
 * Reads carry no transcript and no failure text, and none is declared here.
 */

export const JOB_STATUSES = ["queued", "running", "succeeded", "failed", "cancelled"] as const
export const JobStatusSchema = z.enum(JOB_STATUSES)
export type JobStatus = z.infer<typeof JobStatusSchema>

/** A job that is still going: queued or running. */
export function isOpenStatus(status: JobStatus): boolean {
    return status === "queued" || status === "running"
}

export const JobEventSchema = z.object({
    seq: z.number().int(),
    kind: z.string(),
    tool_name: z.string().nullable().default(null),
    status_code: z.string().nullable().default(null),
    started_at: z.string().nullable().default(null),
    duration_ms: z.number().int().nullable().default(null),
})
export type JobEvent = z.infer<typeof JobEventSchema>

export const JobSchema = z.object({
    id: z.number().int(),
    submission_id: z.number().int(),
    requested_by: z.number().int(),
    provider_key: z.string(),
    status: JobStatusSchema,
    cancel_requested: z.boolean(),
    failure_code: z.string().nullable().default(null),
    step_count: z.number().int(),
    audit_event_id: z.number().int().nullable().default(null),
    created_at: z.string(),
    started_at: z.string().nullable().default(null),
    finished_at: z.string().nullable().default(null),
})
export type Job = z.infer<typeof JobSchema>

export const JobDetailSchema = JobSchema.extend({ events: z.array(JobEventSchema) })
export type JobDetail = z.infer<typeof JobDetailSchema>

export const JobPageSchema = z.object({ items: z.array(JobSchema) })

export const ProviderSchema = z.object({ provider_key: z.string(), availability: z.string() })
export type Provider = z.infer<typeof ProviderSchema>
export const ProvidersSchema = z.object({ providers: z.array(ProviderSchema) })

export const MachineReviewSettingsSchema = z.object({
    machine_review_enabled: z.boolean(),
    curator_requests_enabled: z.boolean(),
    assistant_mode: z.string(),
    review_allowed: z.boolean(),
    curator_allowed: z.boolean(),
})
export type MachineReviewSettings = z.infer<typeof MachineReviewSettingsSchema>

export const OpenJobSchema = z.object({
    id: z.number().int(),
    status: JobStatusSchema,
    step_count: z.number().int(),
    cancel_requested: z.boolean(),
    created_at: z.string(),
    started_at: z.string().nullable().default(null),
})
export type OpenJob = z.infer<typeof OpenJobSchema>
