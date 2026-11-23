import { z } from "zod"

// Read shape of the reproducibility routes (`backend/app/schemas/entities/reproducibility_assessment.py`). The row carries
// internal ids (`id`, `record_id`, `assessor_user_id`, `source_submission_id`) and a context and evidence payload that hold
// more of them; they are parsed through and never rendered.

export const ReproducibilityCheckSchema = z.object({
    name: z.string(),
    level: z.string(),
    outcome: z.string(),
    evidence: z.record(z.string(), z.unknown()).default({}),
}).passthrough()

export const ReproducibilityWarningSchema = z.object({
    code: z.string(),
    evidence: z.record(z.string(), z.unknown()).default({}),
}).passthrough()

export const ReproducibilityAssessmentReadSchema = z.object({
    id: z.number(),
    assessment_ref: z.string(),
    record_type: z.string(),
    record_id: z.number(),
    grade: z.string(),
    rubric_name: z.string(),
    rubric_version: z.number(),
    context_hash: z.string(),
    context_json: z.record(z.string(), z.unknown()),
    passed: z.array(ReproducibilityCheckSchema),
    missing: z.array(ReproducibilityCheckSchema),
    warnings: z.array(ReproducibilityWarningSchema),
    assessor_kind: z.string(),
    assessor_user_id: z.number().nullable(),
    source_submission_id: z.number().nullable(),
    assessed_at: z.string(),
    created_at: z.string(),
}).passthrough()

export type ReproducibilityAssessment = z.infer<typeof ReproducibilityAssessmentReadSchema>
export type ReproducibilityCheck = z.infer<typeof ReproducibilityCheckSchema>
export type ReproducibilityWarning = z.infer<typeof ReproducibilityWarningSchema>
