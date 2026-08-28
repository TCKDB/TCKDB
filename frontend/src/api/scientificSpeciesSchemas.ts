import { z } from "zod"
import { recordReviewSchema } from "./scientificSchemas"

export const speciesEntrySummarySchema = z.object({
    species_entry_ref: z.string(),
    species_entry_kind: z.string(),
    electronic_state_kind: z.string(),
    stereo_label: z.string().nullable().optional(),
    electronic_state_label: z.string().nullable().optional(),
    term_symbol: z.string().nullable().optional(),
    isotope_key: z.string().nullable().optional(),
    species_entry_label: z.string().nullable().optional(),
    review: recordReviewSchema,
    availability: z.object({
        has_thermo: z.boolean(),
        has_statmech: z.boolean(),
        has_transport: z.boolean(),
        has_conformers: z.boolean(),
        calculation_count: z.number(),
    }).passthrough(),
}).passthrough()

export const scientificSpeciesRecordSchema = z.object({
    species_ref: z.string(),
    canonical_smiles: z.string(),
    inchi_key: z.string(),
    formula: z.string().nullable(),
    charge: z.number(),
    multiplicity: z.number(),
    stereo_kind: z.string().optional(),
    entries: z.array(speciesEntrySummarySchema),
}).passthrough()

export const scientificSpeciesSearchSchema = z.object({
    records: z.array(scientificSpeciesRecordSchema),
}).passthrough()

export type ScientificSpeciesRecord = z.infer<typeof scientificSpeciesRecordSchema>
export type ScientificSpeciesEntrySummary = z.infer<typeof speciesEntrySummarySchema>
