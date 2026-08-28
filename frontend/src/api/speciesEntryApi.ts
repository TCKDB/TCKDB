import { z } from "zod"
import { geometrySummarySchema, levelOfTheorySchema, recordReviewSchema } from "./scientificSchemas"
import { parseScientificResponse, requestScientificJson } from "./scientificTransport"

const calculationSchema = z.object({
    calculation_ref: z.string(),
    type: z.string(),
    level_of_theory: levelOfTheorySchema.nullable().optional(),
}).passthrough()
const observationSchema = z.object({
    conformer_observation: z.object({ conformer_observation_ref: z.string() }).passthrough(),
}).passthrough()

const entrySchema = z.object({
    species_entry_ref: z.string(),
    species_entry_kind: z.string(),
    electronic_state_kind: z.string(),
    review: recordReviewSchema,
    availability: z.object({
        has_thermo: z.boolean(),
        has_statmech: z.boolean(),
        has_transport: z.boolean(),
        has_conformers: z.boolean(),
        calculation_count: z.number(),
    }).passthrough(),
}).passthrough()
const speciesResponseSchema = z.object({
    records: z.array(z.object({
        species_ref: z.string(),
        canonical_smiles: z.string(),
        inchi_key: z.string(),
        formula: z.string().nullable(),
        charge: z.number(),
        multiplicity: z.number(),
        entries: z.array(entrySchema),
    }).passthrough()),
}).passthrough()
const conformerResponseSchema = z.object({
    records: z.array(z.object({
        conformer_group: z.object({
            conformer_group_ref: z.string(),
            label: z.string().nullable(),
        }).passthrough(),
        observations_summary: z.object({ total: z.number() }).passthrough(),
        evidence_summary: z.object({
            calculation_count: z.number(),
            optimization_chain_count: z.number(),
            geometry_count: z.number(),
            evidence_coverage: z.object({
                opt: z.number(),
                freq: z.number(),
                sp: z.number(),
            }).passthrough(),
            levels_of_theory: z.record(z.string(), z.array(levelOfTheorySchema)),
        }).passthrough(),
        observations: z.array(observationSchema).nullable().optional(),
        calculations: z.array(calculationSchema).nullable().optional(),
        geometries: z.array(z.object({
            calculation_ref: z.string(),
            geometry: geometrySummarySchema,
        }).passthrough()).nullable().optional(),
    }).passthrough()),
}).passthrough()

export type SpeciesEntryProjection = z.infer<typeof entrySchema> & {
    speciesRef: string
    canonicalSmiles: string
    inchiKey: string
    formula: string | null
    charge: number
    multiplicity: number
}
export type ConformerProjection = z.infer<typeof conformerResponseSchema>["records"][number]

export async function loadSpeciesEntry(entryRef: string, signal?: AbortSignal): Promise<SpeciesEntryProjection | null> {
    const query = new URLSearchParams({ species_entry_ref: entryRef })
    for (const include of ["thermo", "statmech", "transport", "conformers"]) query.append("include", include)
    const payload = await requestScientificJson(`/api/v1/scientific/species/search?${query}`, signal)
    const response = parseScientificResponse(speciesResponseSchema, payload, "species entry")
    for (const species of response.records) {
        const entry = species.entries.find((candidate) => candidate.species_entry_ref === entryRef)
        if (entry) return {
            ...entry,
            speciesRef: species.species_ref,
            canonicalSmiles: species.canonical_smiles,
            inchiKey: species.inchi_key,
            formula: species.formula,
            charge: species.charge,
            multiplicity: species.multiplicity,
        }
    }
    return null
}

export async function loadEntryConformers(entryRef: string, signal?: AbortSignal): Promise<ConformerProjection[]> {
    const query = new URLSearchParams({ species_entry_ref: entryRef, limit: "50" })
    for (const include of ["observations", "calculations", "geometries"]) query.append("include", include)
    const payload = await requestScientificJson(`/api/v1/scientific/conformers/search?${query}`, signal)
    return parseScientificResponse(conformerResponseSchema, payload, "conformer").records
}
