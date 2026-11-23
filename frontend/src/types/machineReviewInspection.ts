import { z } from "zod"

/**
 * Runtime + static types for the admin-only machine-review inspection response.
 *
 * Source of truth: the backend schema
 * ``AdminSubmissionMachineReviewInspectionResponse`` in
 * ``backend/app/api/routes/admin.py``, served by
 * ``GET /api/v1/admin/submissions/{submission_id}/machine-review-inspection``.
 *
 * This is a PRIVATE/diagnostic shape. It is intentionally NOT the public
 * scientific ``TrustFragment`` and carries no approval/rejection/certification
 * semantics.
 */

export const MACHINE_REVIEW_STATUSES = [
    "not_run",
    "machine_screened_pass",
    "machine_screened_warning",
    "machine_screened_needs_attention",
    "machine_review_failed",
] as const

export const MachineReviewStatusSchema = z.enum(MACHINE_REVIEW_STATUSES)
export type MachineReviewStatus = z.infer<typeof MachineReviewStatusSchema>

export const MachineReviewSeveritySchema = z.enum(["info", "warning", "critical"])
export type MachineReviewSeverity = z.infer<typeof MachineReviewSeveritySchema>

/** One record's latest machine-review summary (MachineReviewRecordSummary). */
export const MachineReviewRecordSummarySchema = z.object({
    status: MachineReviewStatusSchema,
    curator_priority: z.enum(["low", "medium", "high"]).nullable().default(null),
    findings_count: z.number().int().nonnegative().default(0),
    highest_severity: MachineReviewSeveritySchema.nullable().default(null),
    model: z.string().nullable().default(null),
    provider: z.string().nullable().default(null),
    reviewed_at: z.string().nullable().default(null),
    submission_id: z.number().int().nullable().default(null),
})
export type MachineReviewRecordSummary = z.infer<typeof MachineReviewRecordSummarySchema>

/**
 * One finding the reviewer raised. Severity, category and the status words are display-only here, so they stay strings: an
 * unfamiliar token is shown as itself rather than costing the whole page.
 */
export const AdminMachineReviewFindingSchema = z.object({
    severity: z.string(),
    category: z.string(),
    message: z.string(),
    evidence_keys: z.array(z.string()).default([]),
    recommended_action: z.string().nullable().default(null),
    curator_task_id: z.number().int().nullable().default(null),
})
export type AdminMachineReviewFinding = z.infer<typeof AdminMachineReviewFindingSchema>

/** One stored review of one record. */
export const AdminMachineReviewRecordReviewSchema = z.object({
    audit_event_id: z.number().int().nullable().default(null),
    reviewed_at: z.string().nullable().default(null),
    status: z.string(),
    curator_priority: z.string().nullable().default(null),
    model: z.string().nullable().default(null),
    provider: z.string().nullable().default(null),
    is_latest: z.boolean().default(false),
    findings: z.array(AdminMachineReviewFindingSchema).default([]),
})
export type AdminMachineReviewRecordReview = z.infer<typeof AdminMachineReviewRecordReviewSchema>

/** One stored reviewer run, including one whose payload could not be read (`parsed: false`). */
export const AdminMachineReviewRunSchema = z.object({
    audit_event_id: z.number().int().nullable().default(null),
    reviewed_at: z.string().nullable().default(null),
    status: z.string().nullable().default(null),
    curator_priority: z.string().nullable().default(null),
    model: z.string().nullable().default(null),
    provider: z.string().nullable().default(null),
    findings_count: z.number().int().default(0),
    mapped_findings_count: z.number().int().default(0),
    unmapped_findings_count: z.number().int().default(0),
    highest_severity: z.string().nullable().default(null),
    summary: z.string().nullable().default(null),
    parsed: z.boolean(),
    parse_warnings: z.array(z.string()).default([]),
})
export type AdminMachineReviewRun = z.infer<typeof AdminMachineReviewRunSchema>

/** A finding that mapped to no record. `cited_*` are the reviewer's words, kept verbatim. */
export const AdminMachineReviewUnmappedFindingSchema = z.object({
    audit_event_id: z.number().int().nullable().default(null),
    reason: z.string(),
    cited_record_type: z.string().nullable().default(null),
    cited_record_ref: z.string().nullable().default(null),
    severity: z.string(),
    category: z.string(),
    message: z.string(),
    evidence_keys: z.array(z.string()).default([]),
    recommended_action: z.string().nullable().default(null),
})
export type AdminMachineReviewUnmappedFinding = z.infer<typeof AdminMachineReviewUnmappedFindingSchema>

/**
 * One linked record that received >=1 exactly-mapped finding.
 *
 * `record_public_ref` is the identifier the archive is addressed by, `null`
 * when the record cannot be named. `record_id` is the internal row id, which
 * an admin-only surface may carry.
 *
 * There is no `record_ref` field: that name means the *public* ref
 * everywhere else in this API, and the machine-review stack's private
 * matching key must not be shown under it. It is not exposed, because on this
 * surface the matching key is always `String(record_id)` -- publishing it
 * would put a second row-id column next to the first.
 */
export const AdminMachineReviewRecordInspectionSchema = z.object({
    record_type: z.string(),
    record_public_ref: z.string().nullable().default(null),
    record_id: z.number().int().nullable().default(null),
    // Where the record is shown when it has no page of its own; null together.
    container_type: z.string().nullable().default(null),
    container_ref: z.string().nullable().default(null),
    latest_summary: MachineReviewRecordSummarySchema,
    all_record_reviews_count: z.number().int().nonnegative().default(0),
    all_record_reviews: z.array(AdminMachineReviewRecordReviewSchema).default([]),
})
export type AdminMachineReviewRecordInspection = z.infer<
    typeof AdminMachineReviewRecordInspectionSchema
>

/** The full admin inspection response for one submission. */
export const AdminSubmissionMachineReviewInspectionSchema = z.object({
    submission_id: z.number().int(),
    record_summaries: z.array(AdminMachineReviewRecordInspectionSchema).default([]),
    unmapped_findings_count: z.number().int().nonnegative().default(0),
    unmapped_findings: z.array(AdminMachineReviewUnmappedFindingSchema).default([]),
    runs: z.array(AdminMachineReviewRunSchema).default([]),
    mapping_warnings: z.array(z.string()).default([]),
    parse_warnings: z.array(z.string()).default([]),
    source_audit_event_ids: z.array(z.number().int()).default([]),
})
export type AdminSubmissionMachineReviewInspection = z.infer<
    typeof AdminSubmissionMachineReviewInspectionSchema
>
