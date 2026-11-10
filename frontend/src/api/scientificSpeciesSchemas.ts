import { z } from "zod"
import { recordReviewSchema } from "./scientificSchemas"
import { listRequestEchoSchema } from "./listEnvelope"

export const speciesEntrySummarySchema = z.object({
    species_entry_ref: z.string(),
    species_entry_kind: z.string(),
    electronic_state_kind: z.string(),
    stereo_label: z.string().nullable().optional(),
    // DR-0038: the key (`null` only for an achiral species), how much of it is known, and the
    // elements no data can settle yet. `stereo_label` is only the display text of the key and is
    // NULL while nothing is resolved -- never read that NULL as "achiral".
    stereo_descriptor: z.string().nullable().optional(),
    stereo_resolution: z.enum(["resolved", "partial", "unresolved"]).nullable().optional(),
    stereo_smiles: z.string().nullable().optional(),
    stereo_not_represented: z.array(z.string()).optional(),
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
    request: listRequestEchoSchema.optional(),
}).passthrough()

export type ScientificSpeciesRecord = z.infer<typeof scientificSpeciesRecordSchema>
export type ScientificSpeciesEntrySummary = z.infer<typeof speciesEntrySummarySchema>
