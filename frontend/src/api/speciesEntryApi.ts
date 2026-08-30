import { z } from "zod"
import { geometrySummarySchema, levelOfTheorySchema } from "./scientificSchemas"
import {
    scientificSpeciesSearchSchema,
    speciesEntrySummarySchema,
} from "./scientificSpeciesSchemas"
import { parseScientificResponse, requestScientificJson } from "./scientificTransport"

const softwareReleaseSchema = z.object({
    software_release_ref: z.string(),
    software: z.string(),
    version: z.string().nullable().optional(),
}).passthrough()

const workflowToolReleaseSchema = z.object({
    workflow_tool_release_ref: z.string(),
    workflow_tool: z.string(),
    version: z.string().nullable().optional(),
}).passthrough()

// `software_release` / `workflow_tool_release` are measured-present on this
// endpoint's calculation rows (live, `conformers/search?include=calculations`)
// even though nothing here previously typed them -- `.passthrough()` already
// carried them at runtime; these two fields just give the geometry/SP tabs a
// typed way to read what was already on the wire, not a new request shape.
const calculationSchema = z.object({
    calculation_ref: z.string(),
    type: z.string(),
    level_of_theory: levelOfTheorySchema.nullable().optional(),
    software_release: softwareReleaseSchema.nullable().optional(),
    workflow_tool_release: workflowToolReleaseSchema.nullable().optional(),
}).passthrough()
const observationCalculationsSchema = z.array(calculationSchema).nullable().optional()
const observationSchema = z.object({
    conformer_observation: z.object({ conformer_observation_ref: z.string() }).passthrough(),
    calculations: observationCalculationsSchema,
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
        // `role` is measured-present on this endpoint's geometry links
        // (live: "final") but was never typed by the shared
        // `geometrySummarySchema` -- extended locally here, the same way
        // `calculationSchema` above extends the shared calculation shape,
        // rather than widening a schema three other API modules also import.
        geometries: z.array(z.object({
            calculation_ref: z.string(),
            geometry: geometrySummarySchema.extend({ role: z.string().nullable().optional() }),
        }).passthrough()).nullable().optional(),
    }).passthrough()),
}).passthrough()

export type SpeciesEntryProjection = z.infer<typeof speciesEntrySummarySchema> & {
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
    const response = parseScientificResponse(scientificSpeciesSearchSchema, payload, "species entry")
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
