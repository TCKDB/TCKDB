import { z } from "zod"

// Read shapes of `GET /api/v1/curation/structure-evidence-findings` (`backend/app/schemas/entities/structure_evidence_finding.py`).
// Enum-like fields are plain strings, so a token this build has not been taught words neutrally instead of failing the parse;
// the token lists live in `domain/curationWording.ts` and a parity test checks them against the backend.

export const RemovedAdjudicationSchema = z.object({
    finding_ref: z.string(),
    deleted_calculation_ref: z.string(),
    deleted_at: z.string(),
}).passthrough()

export const StructureEvidenceFindingReadSchema = z.object({
    finding_ref: z.string(),
    scope: z.string(),
    subject_ref: z.string(),
    kind: z.string(),
    role: z.string().nullable(),
    verdict: z.string(),
    authority: z.string(),
    rationale: z.string(),
    source_calculation_ref: z.string().nullable(),
    literature_ref: z.string().nullable(),
    applied_kind: z.string().nullable(),
    applied_role: z.string().nullable(),
    supersedes_finding_ref: z.string().nullable(),
    settled_by_finding_ref: z.string().nullable(),
    in_force: z.boolean(),
    removed_adjudications: z.array(RemovedAdjudicationSchema).default([]),
    created_at: z.string(),
}).passthrough()

export const StructureEvidenceFindingPageReadSchema = z.object({
    subject_ref: z.string(),
    findings: z.array(StructureEvidenceFindingReadSchema),
    offset: z.number(),
    limit: z.number(),
}).passthrough()

export type StructureEvidenceFinding = z.infer<typeof StructureEvidenceFindingReadSchema>
export type StructureEvidenceFindingPage = z.infer<typeof StructureEvidenceFindingPageReadSchema>
