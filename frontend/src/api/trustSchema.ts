import { z } from "zod"
import { madeUnderSchema } from "../domain/madeUnder"

// Trust fragment (`TrustFragment`). Every token is read as a plain string, so a token this client has not been taught never
// turns a page into a "malformed data" error. `evidence.record_id` and `is_certified` ride on `.passthrough()` and are
// never typed or rendered: the first is an internal database id.
export const trustFragmentSchema = z.object({
    review_status: z.string().optional(),
    trust_status: z.string(),
    evidence: z.object({
        passed_count: z.number(),
        possible_count: z.number(),
        // The rubric that judged the record and its integer version; evaluated at read time, so always the version in force.
        rubric: z.string().optional(),
        rubric_version: z.number().optional(),
        label: z.string().nullable().optional(),
        // Check name -> outcome, in the order the server sends. A warning-kind check appears only when it fires.
        checks: z.record(z.string(), z.string()).optional(),
        hard_fail_reason: z.string().nullable().optional(),
    }).passthrough(),
}).passthrough()

export type TrustFragment = z.infer<typeof trustFragmentSchema>

// `include=assessments`: the reproducibility assessment with the rubric it was made under. The deterministic-trust half
// is left optional and unrendered.
export const reproducibilitySchema = z.object({
    state: z.string(),
    grade: z.string().nullable().optional(),
    rubric: z.string().nullable().optional(),
    rubric_version: z.number().nullable().optional(),
    assessed_at: z.string().nullable().optional(),
    made_under: madeUnderSchema.nullable().optional(),
}).passthrough()

export const assessmentsSchema = z.object({
    reproducibility: reproducibilitySchema.nullable().optional(),
    deterministic_trust: z.unknown().optional(),
}).passthrough()

export type Reproducibility = z.infer<typeof reproducibilitySchema>
export type Assessments = z.infer<typeof assessmentsSchema>
