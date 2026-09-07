import { z } from "zod"

export const levelOfTheorySchema = z.object({
    method: z.string(),
    basis: z.string().nullable().optional(),
    display: z.string().optional(),
    // Not projected into `display`/`lotLabel`: two rows can render the same
    // string while differing only in dispersion, solvent or spin treatment
    // (backend/app/schemas/reads/scientific_common.py:250-257). Surfaces
    // that treat the level of theory itself as the subject render these
    // explicitly rather than folding them into the compact label.
    level_of_theory_ref: z.string().optional(),
    dispersion: z.string().nullable().optional(),
    solvent: z.string().nullable().optional(),
}).passthrough()

/**
 * The compact "method/basis" (or explicit `display`) label shared by every
 * surface that shows a level of theory inline. Deliberately excludes
 * `dispersion`/`solvent`/`level_of_theory_ref` — see the schema comment
 * above; a caller that needs to distinguish two same-label rows renders
 * those fields itself alongside this label, it does not fold them in here.
 */
export function lotLabel(value: { method: string; basis?: string | null; display?: string }): string {
    return value.display ?? (value.basis ? `${value.method}/${value.basis}` : value.method)
}

export type LevelOfTheory = z.infer<typeof levelOfTheorySchema>

/**
 * A statmech/thermo record's up-to-three levels of theory — geometry
 * (the `opt` role), frequencies (the `freq` role, or the opt's when this
 * record has no dedicated frequency job), and energy (the `sp` role when a
 * single point is linked, else the opt's — an optimisation's final energy
 * is its own single-point energy). Additive on both surfaces: `null` when
 * the server hasn't shipped this yet, in which case
 * `domain/productLevels.ts` derives the same three from the record's own
 * `source_calculations[]` roles instead. `energy_source` names which of
 * the two energy cases applied, plus the two evidence-free classifications
 * (`composite`/`imported`) a record can also declare.
 */
export const productLevelsSchema = z.object({
    geometry: levelOfTheorySchema.nullable().optional(),
    frequency: levelOfTheorySchema.nullable().optional(),
    energy: levelOfTheorySchema.nullable().optional(),
    energy_source: z.string().nullable().optional(),
}).passthrough()

export type ProductLevelsWire = z.infer<typeof productLevelsSchema>

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
    software_release: z.object({ software: z.string() }).passthrough().nullable().optional(),
    workflow_tool_release: z.object({ workflow_tool: z.string() }).passthrough().nullable().optional(),
}).passthrough()
