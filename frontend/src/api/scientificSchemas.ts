import { z } from "zod"

export const levelOfTheorySchema = z.object({
    method: z.string(),
    basis: z.string().nullable().optional(),
    display: z.string().optional(),
}).passthrough()

export const recordReviewSchema = z.object({ status: z.string() }).passthrough()

export const geometrySummarySchema = z.object({
    geometry_ref: z.string(),
    geom_hash: z.string().nullable().optional(),
    natoms: z.number().nullable().optional(),
}).passthrough()

export const calculationSummarySchema = z.object({
    calculation_ref: z.string(),
    type: z.string(),
    quality: z.string().optional(),
    review: recordReviewSchema.optional(),
    level_of_theory: levelOfTheorySchema.nullable().optional(),
    software_release: z.object({ name: z.string() }).passthrough().nullable().optional(),
    workflow_tool_release: z.object({ name: z.string() }).passthrough().nullable().optional(),
}).passthrough()
