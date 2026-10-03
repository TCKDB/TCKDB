import { z } from "zod"

/**
 * The composite recipe a level of theory is bound to (ADR 0021): refs and
 * names only. `geometry_level_of_theory_ref` is the level the recipe runs
 * its own geometry at, `null` when the recipe states none.
 */
export const compositeSchemeSummarySchema = z.object({
    composite_scheme_ref: z.string(),
    kind: z.string(),
    name: z.string(),
    geometry_level_of_theory_ref: z.string().nullable().optional(),
}).passthrough()

export type CompositeSchemeSummary = z.infer<typeof compositeSchemeSummarySchema>

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
    // ADR 0021 / P7a: stated-or-null facts of the level itself and the
    // recipe it is bound to. Each is `null`/absent when nothing is stated,
    // and renders as absent -- never as a default (`all_electron`, say).
    solvent_model: z.string().nullable().optional(),
    aux_basis: z.string().nullable().optional(),
    cabs_basis: z.string().nullable().optional(),
    core_treatment: z.string().nullable().optional(),
    spin_treatment: z.string().nullable().optional(),
    composite_scheme: compositeSchemeSummarySchema.nullable().optional(),
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
 * How far a composite energy has been checked (ADR 0021, P7a). `state` is
 * kept a plain string on the wire so a state this client has not met never
 * fails to parse; `domain/compositeVerification.ts` maps the five known
 * ones and degrades an unknown one to a neutral "not recognised" line.
 */
export const compositeEnergyVerificationSchema = z.object({
    state: z.string(),
    assembly: z.string().optional(),
    reason: z.string().nullable().optional(),
    difference_hartree: z.number().nullable().optional(),
    tolerance_hartree: z.number().nullable().optional(),
}).passthrough()

export type CompositeEnergyVerification = z.infer<typeof compositeEnergyVerificationSchema>

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
    // ADR 0021 / P7a. `notation` is the SERVER's chemist's shorthand
    // (`energy//geometry`), never composed on the client; `null` when
    // either level is absent. `geometry_source`/`frequency_source` say
    // when a level is what a composite recipe runs internally
    // (`composite_recipe`) rather than a deposited calculation.
    notation: z.string().nullable().optional(),
    geometry_source: z.string().nullable().optional(),
    frequency_source: z.string().nullable().optional(),
    composite_energy_verification: compositeEnergyVerificationSchema.nullable().optional(),
    legacy_composite_shape: z.string().nullable().optional(),
}).passthrough()

export type ProductLevelsWire = z.infer<typeof productLevelsSchema>

/**
 * `note` is the curator's stated reason for the status -- public,
 * reader-facing prose (`backend/app/schemas/reads/scientific_common.py`'s
 * `RecordReviewBadge.note`), not gated on `status`: a note on an
 * `approved` record is as meaningful as one under review. `null`/absent
 * means no reason was recorded and must render as nothing, never an
 * empty box (see `components/ReviewNote.tsx`).
 */
export const recordReviewSchema = z.object({
    status: z.string(),
    note: z.string().nullable().optional(),
}).passthrough()

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
