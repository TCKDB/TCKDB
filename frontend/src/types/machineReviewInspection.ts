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
 * semantics. See ``backend/docs/specs/machine_review_admin_ui_mock.md``.
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

/** One linked record that received >=1 exactly-mapped finding. */
export const AdminMachineReviewRecordInspectionSchema = z.object({
    record_type: z.string(),
    record_ref: z.string().nullable().default(null),
    record_id: z.number().int().nullable().default(null),
    latest_summary: MachineReviewRecordSummarySchema,
    all_record_reviews_count: z.number().int().nonnegative().default(0),
})
export type AdminMachineReviewRecordInspection = z.infer<
    typeof AdminMachineReviewRecordInspectionSchema
>

/** The full admin inspection response for one submission. */
export const AdminSubmissionMachineReviewInspectionSchema = z.object({
    submission_id: z.number().int(),
    record_summaries: z.array(AdminMachineReviewRecordInspectionSchema).default([]),
    unmapped_findings_count: z.number().int().nonnegative().default(0),
    mapping_warnings: z.array(z.string()).default([]),
    parse_warnings: z.array(z.string()).default([]),
    source_audit_event_ids: z.array(z.number().int()).default([]),
})
export type AdminSubmissionMachineReviewInspection = z.infer<
    typeof AdminSubmissionMachineReviewInspectionSchema
>

/**
 * Highest severity across all returned record summaries, computed client-side
 * (the endpoint does not return an aggregate). ``null`` when no summary carries
 * a severity.
 */
const SEVERITY_RANK: Record<MachineReviewSeverity, number> = {
    info: 1,
    warning: 2,
    critical: 3,
}

export function overallHighestSeverity(
    inspection: AdminSubmissionMachineReviewInspection,
): MachineReviewSeverity | null {
    let best: MachineReviewSeverity | null = null
    for (const record of inspection.record_summaries) {
        const sev = record.latest_summary.highest_severity
        if (sev && (best === null || SEVERITY_RANK[sev] > SEVERITY_RANK[best])) {
            best = sev
        }
    }
    return best
}
