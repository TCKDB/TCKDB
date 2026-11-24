import { z } from "zod"
import { OpenJobSchema } from "./machineReviewJob"

/**
 * The submission reads the machine-review pages use.
 *
 * Source of truth: `SubmissionRead`, `SubmissionAuditEventRead`, `SubmissionRecordLinkRead` and
 * `SubmissionMachineReviewOverviewRead` in the backend. Only the fields a page shows are listed; the status, kind and
 * actor words are display-only, so they stay strings and an unfamiliar one is shown as itself.
 *
 * `title` and `summary` are the depositor's own words: a page shows them only in a field marked as deposited.
 * Audit events carry free text (`reason`, `summary`, `details_json`); it is deliberately not declared, so zod drops it.
 */

export const SubmissionSchema = z.object({
    id: z.number().int(),
    public_ref: z.string(),
    created_by: z.number().int(),
    submission_kind: z.string(),
    status: z.string(),
    submitted_at: z.string(),
    title: z.string().nullable().default(null),
})
export type Submission = z.infer<typeof SubmissionSchema>

export const SubmissionAuditEventSchema = z.object({
    id: z.number().int(),
    created_at: z.string(),
    actor_kind: z.string(),
    event_kind: z.string(),
    from_status: z.string().nullable().default(null),
    to_status: z.string().nullable().default(null),
})
export type SubmissionAuditEvent = z.infer<typeof SubmissionAuditEventSchema>

export const SubmissionRecordLinkSchema = z.object({
    id: z.number().int(),
    record_type: z.string(),
    record_id: z.number().int(),
})
export type SubmissionRecordLink = z.infer<typeof SubmissionRecordLinkSchema>

export const OverviewLatestRunSchema = z.object({
    reviewed_at: z.string().nullable().default(null),
    status: z.string().nullable().default(null),
    highest_severity: z.string().nullable().default(null),
    findings_count: z.number().int().default(0),
})

export const SubmissionOverviewRowSchema = z.object({
    id: z.number().int(),
    public_ref: z.string(),
    status: z.string(),
    submission_kind: z.string(),
    created_by_username: z.string().nullable().default(null),
    submitted_at: z.string(),
    machine_review: z.object({
        run_count: z.number().int(),
        open_task_count: z.number().int(),
        task_count: z.number().int(),
        latest_run: OverviewLatestRunSchema.nullable().default(null),
        open_job: OpenJobSchema.nullable().default(null),
    }),
})
export type SubmissionOverviewRow = z.infer<typeof SubmissionOverviewRowSchema>

export const SubmissionOverviewPageSchema = z.object({
    items: z.array(SubmissionOverviewRowSchema),
    total: z.number().int(),
    skip: z.number().int(),
    limit: z.number().int(),
})
export type SubmissionOverviewPage = z.infer<typeof SubmissionOverviewPageSchema>
